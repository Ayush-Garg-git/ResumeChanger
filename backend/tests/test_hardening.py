"""
Milestone 7: one test (or more) per finding from the independent review.

Each test reproduces the problem the reviewer found and checks it can no
longer happen. The finding number is in each test's name.
"""

import io
import json
import threading
import time
import zipfile

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from fastapi.testclient import TestClient

from tests.helpers import ai_post

from app import analyzer, main, renderer, storage
from app.analyzer import build_prompt, redact
from app.config import settings
from app.docx_editor import Change, apply_changes, neutralise_external_links
from app.docx_reader import read_resume
from app.main import app
from app.renderer import Rendered

client = TestClient(app)

JOB = (
    "Data Analyst. Build Power BI dashboards, write SQL and present weekly sales "
    "insights to regional managers. Python automation is a plus."
)
P10 = "Built a Power BI dashboard used by regional managers to track weekly sales."
P11 = "Automated a manual Excel report with Python, cutting preparation time from a full day to under an hour."
P14 = "Portfolio Website: Personal site built with HTML and CSS."


def _s(pid, kind, original, proposed):
    return {"paragraph_id": pid, "kind": kind, "original_text": original,
            "proposed_text": proposed, "reason": "r", "requirement": None}


ANSWER = {
    "role_title": "Data Analyst", "fit_summary": "ok", "requirements": [],
    "questions_for_user": [], "no_changes_needed": False,
    "suggestions": [
        _s("p10", "reword", P10, "Built a Power BI dashboard that regional managers used to track weekly sales."),
        _s("p11", "shorten", P11, "Automated a manual Excel report with Python, cutting preparation from a day to under an hour."),
        _s("p14", "consider_removing", P14, None),
    ],
}


@pytest.fixture
def ai(monkeypatch):
    monkeypatch.setattr(analyzer, "call_model", lambda prompt, schema: json.dumps(ANSWER))
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")


def _upload(path):
    files = {"file": ("r.docx", path.read_bytes(), "application/octet-stream")}
    return client.post("/api/resume", files=files)


@pytest.fixture
def sid(sample_resume, ai):
    session_id = _upload(sample_resume).json()["session_id"]
    assert ai_post(client, f"/api/resume/{session_id}/analyze", json={"job_description": JOB, "consent": True}).status_code == 200
    return session_id


def _aid(session_id):
    return storage.load_json(session_id, "analysis.json")["analysis_id"]


def _decide(session_id, suggestion_id, action, analysis_id=None):
    return client.put(
        f"/api/resume/{session_id}/suggestions/{suggestion_id}",
        json={"action": action, "analysis_id": analysis_id or _aid(session_id)},
    )


def _items(session_id):
    return storage.load_json(session_id, "decisions.json")["items"]


# --- 1. Overlapping decision clicks must not lose a decision -----------------

def test_1_overlapping_decisions_are_both_kept(sid, monkeypatch):
    _decide(sid, "s1", "accept")
    real_load = storage.load_json

    def slow_load(session_id, name):
        result = real_load(session_id, name)
        if name == "decisions.json":
            time.sleep(0.3)  # widen the window the reviewer exploited
        return result

    monkeypatch.setattr(storage, "load_json", slow_load)
    threads = [
        threading.Thread(target=_decide, args=(sid, "s1", "reject")),
        threading.Thread(target=_decide, args=(sid, "s3", "accept")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    monkeypatch.setattr(storage, "load_json", real_load)

    items = _items(sid)
    assert items["s1"]["effect"] == "keep"        # the reject was not lost
    assert items["s3"]["effect"] == "remove"


def test_1_decisions_from_an_older_analysis_are_refused(sid):
    old = _aid(sid)
    ai_post(client, f"/api/resume/{sid}/analyze", json={"job_description": JOB, "consent": True})
    response = _decide(sid, "s1", "accept", analysis_id=old)
    assert response.status_code == 409


def test_1_json_writes_are_atomic(sid):
    # No half-written temp files are left behind in the session folder.
    _decide(sid, "s1", "accept")
    folder = storage.original_path(sid).parent
    assert not list(folder.glob(".tmp-*"))


# --- 2. A decision during "Create" must withdraw the file -------------------

def test_2_decision_during_apply_withdraws_the_file(sid, monkeypatch):
    _decide(sid, "s1", "accept")
    real_apply = main.apply_changes

    def slow_apply(*args, **kwargs):
        time.sleep(0.5)
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(main, "apply_changes", slow_apply)
    worker = threading.Thread(target=client.post, args=(f"/api/resume/{sid}/apply",))
    worker.start()
    time.sleep(0.1)
    _decide(sid, "s1", "reject")   # waits for apply, then withdraws its file
    worker.join()

    assert client.get(f"/api/resume/{sid}/download").status_code == 404


def test_2_report_from_old_decisions_is_not_served(sid):
    _decide(sid, "s1", "accept")
    assert client.post(f"/api/resume/{sid}/apply").json()["download_ready"]
    # Simulate a stale report left on disk with an old version stamp.
    report = storage.load_json(sid, "apply_report.json")
    report["decisions_version"] -= 1
    storage.save_json(sid, "apply_report.json", report)
    assert client.get(f"/api/resume/{sid}/download").status_code == 404


# --- 3. A tiny file with huge numbers of paragraphs is refused ---------------

def test_3_paragraph_bomb_is_refused(tmp_path):
    doc = Document()
    body = doc.element.body
    for _ in range(6000):
        body.append(OxmlElement("w:p"))
    path = tmp_path / "bomb.docx"
    doc.save(str(path))
    assert path.stat().st_size < 100_000  # tiny on disk

    start = time.monotonic()
    response = _upload(path)
    assert response.status_code == 400
    assert "too long" in response.json()["detail"]
    assert time.monotonic() - start < 5   # refused quickly, before reading


def test_3_upload_route_runs_off_the_event_loop():
    import inspect
    assert not inspect.iscoroutinefunction(main.upload_resume)


# --- 4. Request bodies are limited ------------------------------------------

def test_4_oversized_upload_is_refused_early():
    big = b"x" * (settings.max_upload_bytes + 2 * 1024 * 1024)
    response = client.post("/api/resume", files={"file": ("r.docx", big, "application/octet-stream")})
    assert response.status_code == 413
    # Refused by the size limit in front of the app, not by the file check
    # that runs only after the whole body has been saved.
    assert response.json()["detail"] == "This request is too large."


def test_4_oversized_json_body_is_refused(sid):
    response = client.put(
        f"/api/resume/{sid}/suggestions/s1",
        content=b'{"action":"edit","text":"' + b"x" * (settings.max_upload_bytes + 2 * 1024 * 1024) + b'"}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413


def test_4_body_without_declared_length_is_cut_off():
    import asyncio
    from fastapi import HTTPException
    from app.limits import BodySizeLimitMiddleware, max_body_bytes

    consumed = {"bytes": 0}

    async def app_reads_everything(scope, receive, send):
        while True:
            message = await receive()
            consumed["bytes"] += len(message.get("body", b""))
            if not message.get("more_body"):
                break

    chunk = b"x" * (1024 * 1024)
    chunks = iter(range(100))  # 100 MB offered, no Content-Length header

    async def receive():
        next(chunks)
        return {"type": "http.request", "body": chunk, "more_body": True}

    middleware = BodySizeLimitMiddleware(app_reads_everything)
    with pytest.raises(HTTPException) as err:
        asyncio.run(middleware({"type": "http", "headers": []}, receive, None))
    assert err.value.status_code == 413
    assert consumed["bytes"] <= max_body_bytes()  # stopped near the limit, not at 100 MB


def test_4_huge_job_description_is_refused(sid):
    response = ai_post(client, f"/api/resume/{sid}/analyze", json={"job_description": "x" * 60_000, "consent": True})
    assert response.status_code == 422


# --- 5. AI use is rate limited ----------------------------------------------

def test_5_analyses_per_session_are_limited(sid, monkeypatch):
    monkeypatch.setattr(settings, "analyses_per_session", 2)
    # The fixture already used one analysis.
    ok = ai_post(client, f"/api/resume/{sid}/analyze", json={"job_description": JOB, "consent": True})
    blocked = ai_post(client, f"/api/resume/{sid}/analyze", json={"job_description": JOB, "consent": True})
    assert ok.status_code == 200
    assert blocked.status_code == 429


def test_5_analyses_per_ip_use_the_proxy_address(sample_resume, ai, monkeypatch):
    monkeypatch.setattr(settings, "analyses_per_ip_per_hour", 1)
    a = _upload(sample_resume).json()["session_id"]
    b = _upload(sample_resume).json()["session_id"]
    first = ai_post(client, f"/api/resume/{a}/analyze", json={"job_description": JOB, "consent": True},
                        headers={"X-Forwarded-For": "1.1.1.1, 203.0.113.9"})
    # Faking the first entry doesn't help: the proxy's (last) entry counts.
    second = ai_post(client, f"/api/resume/{b}/analyze", json={"job_description": JOB, "consent": True},
                         headers={"X-Forwarded-For": "8.8.8.8, 203.0.113.9"})
    assert first.status_code == 200 and second.status_code == 429


# --- 6 and 11. Privacy: what is sent to the AI -----------------------------

@pytest.fixture
def indian_resume(tmp_path):
    doc = Document()
    doc.add_paragraph("Riya Mehta")
    doc.add_paragraph("riya.mehta@example.com | +91 90000 00000")
    doc.add_heading("Experience", level=1)
    doc.add_paragraph("Data Analyst, Infosys 2019 - 2023")
    doc.add_paragraph("Riya led weekly reviews with store managers.")
    doc.add_heading("Personal Details", level=1)
    doc.add_paragraph("Father's name: Suresh Mehta")
    doc.add_paragraph("Address: 12 Example Road, Pune 411001")
    doc.add_heading("Declaration", level=1)
    doc.add_paragraph("I hereby declare the above is true. Riya Mehta, Pune")
    path = tmp_path / "in.docx"
    doc.save(str(path))
    return path


def test_6_personal_sections_are_never_sent(indian_resume):
    prompt = build_prompt(read_resume(indian_resume), JOB)
    assert "Suresh" not in prompt
    assert "411001" not in prompt
    assert "hereby declare" not in prompt


def test_6_name_is_masked_everywhere(indian_resume):
    prompt = build_prompt(read_resume(indian_resume), JOB)
    assert "Riya" not in prompt and "Mehta" not in prompt
    assert "[name] led weekly reviews" in prompt


def test_6_id_numbers_and_birth_dates_are_masked():
    assert redact("PAN: ABCDE1234F") == "PAN: [id]"
    assert redact("Passport J1234567") == "Passport [id]"
    assert redact("Date of Birth: 01/02/2000") == "[personal details]"
    assert redact("DOB 01-02-2000") == "[personal details]"
    assert "[phone]" in redact("Aadhaar 1234 5678 9012")


def test_11_date_ranges_are_not_phone_numbers(indian_resume):
    assert redact("Infosys 2019 - 2023") == "Infosys 2019 - 2023"
    assert redact("Jun 2023 - Nov 2024") == "Jun 2023 - Nov 2024"
    assert "Infosys 2019 - 2023" in build_prompt(read_resume(indian_resume), JOB)


def test_6_lines_with_a_masked_name_can_still_get_suggestions(indian_resume, monkeypatch):
    # The safety net must compare against the same masked text the AI saw.
    from app.suggestion_checks import check_suggestions
    from app.schemas import ResumeAnalysis, Suggestion
    outline = read_resume(indian_resume)
    sent = analyzer.paragraphs_to_send(outline)
    pid = next(p.id for p in sent if p.text.startswith("Riya led"))
    s = Suggestion(paragraph_id=pid, kind="review", original_text="[name] led weekly reviews with store managers.",
                   proposed_text=None, reason="r", requirement=None)
    analysis = ResumeAnalysis(role_title="x", fit_summary="x", requirements=[], suggestions=[s],
                              questions_for_user=[], no_changes_needed=False)
    kept, dropped = check_suggestions(analysis, sent, JOB, analyzer.person_name_words(outline))
    # Kept, but as a review only: lines with placeholders never get rewrites.
    assert not kept and "private details" in dropped[0].why


# --- 7. The safety net catches lowercase inventions and inflation ------------

def _check(original_pid, original, proposed, job):
    from app.suggestion_checks import check_suggestions
    from app.schemas import ResumeAnalysis, Suggestion
    import tempfile
    from pathlib import Path
    from samples.make_sample_resume import build_sample_resume
    path = build_sample_resume(Path(tempfile.mkdtemp()) / "s.docx")
    sent = analyzer.paragraphs_to_send(read_resume(path))
    s = Suggestion(paragraph_id=original_pid, kind="reword", original_text=original,
                   proposed_text=proposed, reason="r", requirement=None)
    analysis = ResumeAnalysis(role_title="x", fit_summary="x", requirements=[], suggestions=[s],
                              questions_for_user=[], no_changes_needed=False)
    return check_suggestions(analysis, sent, job)


def test_7_lowercase_invented_skill_is_dropped():
    kept, dropped = _check("p10", P10, "Built a Power BI dashboard with machine learning forecasts for regional managers.",
                           "Data Analyst with machine learning experience.")
    assert not kept and "machine" in dropped[0].why


def test_7_inflated_wording_is_dropped():
    kept, dropped = _check("p10", P10, "Led a Power BI dashboard used by regional managers to track weekly sales.", JOB)
    assert not kept and "'led'" in dropped[0].why


def test_7_plain_rewording_still_passes():
    kept, dropped = _check("p10", P10, "Built a Power BI dashboard that regional managers used to track weekly sales.", JOB)
    assert kept, dropped


# --- 8. Expired sessions are cleaned up without new uploads ------------------

def test_8_cleanup_skips_a_session_in_use(sample_resume, monkeypatch):
    session_id = _upload(sample_resume).json()["session_id"]
    monkeypatch.setattr(settings, "session_ttl_minutes", -1)  # everything is "expired"
    with storage.session_lock(session_id):
        result = {}
        t = threading.Thread(target=lambda: result.setdefault("n", storage.cleanup_expired_sessions()))
        t.start()
        t.join()
        assert storage.original_path(session_id) is not None  # busy: skipped
    assert storage.cleanup_expired_sessions() == 1
    assert storage.original_path(session_id) is None


def test_8_background_cleanup_runs_on_a_timer(sample_resume, monkeypatch):
    session_id = _upload(sample_resume).json()["session_id"]
    monkeypatch.setattr(settings, "session_ttl_minutes", -1)
    monkeypatch.setattr(settings, "cleanup_interval_seconds", 0.05)
    stop = threading.Event()
    thread = threading.Thread(target=main._cleanup_loop, args=(stop,), daemon=True)
    thread.start()
    time.sleep(0.3)
    stop.set()
    thread.join()
    assert storage.original_path(session_id) is None


# --- 9. Rendering can't fetch outside resources ----------------------------

def test_9_external_image_links_are_neutralised(tmp_path):
    doc = Document()
    doc.add_paragraph("Hello")
    image_type = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
    link_type = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
    doc.part.relate_to("http://attacker.example/pixel.png", image_type, is_external=True)
    doc.part.relate_to("https://example.com", link_type, is_external=True)
    path = tmp_path / "ext.docx"
    doc.save(str(path))

    neutralise_external_links(path)
    with zipfile.ZipFile(path) as z:
        rels = z.read("word/_rels/document.xml.rels").decode()
    assert "attacker.example" not in rels
    assert "https://example.com" in rels  # clickable links are untouched


def test_9_timeout_stops_the_whole_process_group():
    import subprocess
    with pytest.raises(subprocess.TimeoutExpired):
        renderer._run(["bash", "-c", "sleep 30 & sleep 30"], timeout=0.5)


# --- 10. Removal never drops hidden content silently -----------------------

def test_10_line_with_a_field_is_not_removed(tmp_path):
    doc = Document()
    p = doc.add_paragraph("Page ")
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    p._p.append(field)
    path = tmp_path / "field.docx"
    doc.save(str(path))
    result = apply_changes(path, tmp_path / "out.docx", [Change("s1", "p0", "remove", "Page ", None)])
    assert not result.applied and "field" in result.skipped[0]["why"]


# --- 12. Security headers and API docs ---------------------------------------

def test_12_security_headers_are_set():
    headers = client.get("/api/health").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_12_unexpected_errors_hide_details(sid, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret resume text here")
    monkeypatch.setattr(main, "summarise", boom)
    response = TestClient(app, raise_server_exceptions=False).get(f"/api/resume/{sid}/decisions")
    assert response.status_code == 500
    assert "secret" not in response.text


# --- 13. Preview: concurrent and stale requests ---------------------------

def test_13_preview_from_changed_decisions_is_not_saved(sid, monkeypatch, tmp_path):
    _decide(sid, "s1", "accept")
    client.post(f"/api/resume/{sid}/apply")

    def change_and_recreate():
        _decide(sid, "s2", "accept")
        client.post(f"/api/resume/{sid}/apply")  # a NEW valid file exists again

    def fake_render(docx, out_dir, prefix):
        if prefix == "before":
            # While the old pages are drawn, the user changes a decision AND
            # creates the file again. Only the version stamp tells the old
            # preview apart from the new file.
            worker = threading.Thread(target=change_and_recreate)
            worker.start()
            worker.join()
        out_dir.mkdir(parents=True, exist_ok=True)
        image = out_dir / f"{prefix}-1.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        return Rendered(pages=1, images=[image])

    monkeypatch.setattr(renderer, "is_available", lambda: True)
    monkeypatch.setattr(renderer, "render", fake_render)
    response = client.get(f"/api/resume/{sid}/preview")
    assert response.status_code == 409          # stale result is not served
    assert not (storage.preview_dir(sid) / "preview.json").exists()


@pytest.mark.skipif(not renderer.is_available(), reason="LibreOffice not installed")
def test_13_two_previews_at_once_both_succeed(sid):
    _decide(sid, "s1", "accept")
    client.post(f"/api/resume/{sid}/apply")
    results = []
    threads = [threading.Thread(target=lambda: results.append(client.get(f"/api/resume/{sid}/preview")))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json() == results[1].json()


# --- 15. Line breaks from the AI don't reach the document -------------------

def test_15_ai_line_breaks_become_spaces():
    kept, _ = _check("p10", P10, "Built a Power BI dashboard that regional\nmanagers used to track weekly sales.", JOB)
    assert "\n" not in kept[0].proposed_text


def test_15_user_edits_keep_tabs(sid):
    response = client.put(
        f"/api/resume/{sid}/suggestions/s1",
        json={"action": "edit", "text": "Built a Power BI dashboard\t2023", "analysis_id": _aid(sid)},
    )
    assert response.json()["decision"]["final_text"] == "Built a Power BI dashboard\t2023"
