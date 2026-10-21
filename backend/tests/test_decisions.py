"""
Tests for accepting, rejecting and editing suggestions (Milestone 4).

The AI is replaced with a stand-in that returns one suggestion of each kind.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import analyzer, storage
from app.config import settings
from app.main import app

client = TestClient(app)

JOB = (
    "Data Analyst. Build Power BI dashboards, write SQL and present weekly sales "
    "insights to regional managers. Python automation is a plus."
)
P7 = "Tools: Excel, Power BI, Git"
P10 = "Built a Power BI dashboard used by regional managers to track weekly sales."
P11 = "Automated a manual Excel report with Python, cutting preparation time from a full day to under an hour."
P13 = "Inventory Forecast: Forecasted monthly stock using past sales in Python."
P14 = "Portfolio Website: Personal site built with HTML and CSS."


def _s(pid, kind, original, proposed):
    return {"paragraph_id": pid, "kind": kind, "original_text": original,
            "proposed_text": proposed, "reason": "Because.", "requirement": None}


ANSWER = {
    "role_title": "Data Analyst",
    "fit_summary": "Good match.",
    "requirements": [],
    "suggestions": [
        _s("p7", "emphasise", P7, "Tools: Power BI, Excel, Git"),                       # s1
        _s("p10", "reword", P10, "Built a Power BI dashboard that regional managers used to track weekly sales."),  # s2
        _s("p11", "shorten", P11, "Automated a manual Excel report with Python, cutting preparation from a day to under an hour."),  # s3
        _s("p13", "review", P13, None),                                                 # s4
        _s("p14", "consider_removing", P14, None),                                      # s5
    ],
    "questions_for_user": [],
    "no_changes_needed": False,
}


@pytest.fixture
def session_id(sample_resume, monkeypatch):
    """An uploaded, analysed resume, ready for decisions."""
    monkeypatch.setattr(analyzer, "call_model", lambda prompt, schema: json.dumps(ANSWER))
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    files = {"file": ("resume.docx", sample_resume.read_bytes(), "application/octet-stream")}
    sid = client.post("/api/resume", files=files).json()["session_id"]
    analysed = client.post(f"/api/resume/{sid}/analyze", json={"job_description": JOB, "consent": True})
    assert analysed.status_code == 200, analysed.text
    assert len(analysed.json()["suggestions"]) == 5
    return sid


def _decide(sid, suggestion_id, action, text=None):
    return client.put(f"/api/resume/{sid}/suggestions/{suggestion_id}",
                      json={"action": action, "text": text})


# --- Starting state ---------------------------------------------------------

def test_everything_starts_undecided(session_id):
    summary = client.get(f"/api/resume/{session_id}/decisions").json()["summary"]
    assert summary["counts"] == {"change": 0, "remove": 0, "keep": 0, "undecided": 5}
    assert summary["changes"] == []  # undecided means nothing will change


# --- Each action ------------------------------------------------------------

def test_accept_a_rewrite(session_id):
    body = _decide(session_id, "s2", "accept").json()
    assert body["decision"]["effect"] == "change"
    assert body["decision"]["final_text"].startswith("Built a Power BI dashboard that")
    assert body["summary"]["counts"]["change"] == 1


def test_reject_keeps_the_original(session_id):
    body = _decide(session_id, "s2", "reject").json()
    assert body["decision"]["effect"] == "keep"
    assert body["summary"]["changes"] == []


def test_accept_removal(session_id):
    body = _decide(session_id, "s5", "accept").json()
    assert body["decision"]["effect"] == "remove"
    assert body["summary"]["changes"][0]["paragraph_id"] == "p14"


def test_review_cannot_be_accepted(session_id):
    response = _decide(session_id, "s4", "accept")
    assert response.status_code == 400
    assert "no proposed change" in response.json()["detail"]


def test_user_edit_is_used_as_written(session_id):
    # The user may add things the AI may not: they know their own experience.
    text = "Forecasted monthly stock from past sales in Python and Tableau."
    body = _decide(session_id, "s4", "edit", text).json()
    assert body["decision"]["final_text"] == text
    assert body["summary"]["changes"][0]["edited_by_user"] is True


def test_edit_cleans_up_line_breaks(session_id):
    body = _decide(session_id, "s2", "edit", "Built a Power BI\n dashboard   for managers.").json()
    assert body["decision"]["final_text"] == "Built a Power BI dashboard for managers."


@pytest.mark.parametrize("text, message", [
    ("", "empty"),
    ("   ", "empty"),
    (P10, "same as the original"),
    ("Contact [email] for details", "placeholder"),
    ("x" * 1001, "longer than"),
])
def test_bad_edits_are_refused(session_id, text, message):
    response = _decide(session_id, "s2", "edit", text)
    assert response.status_code == 400
    assert message in response.json()["detail"]


def test_undecided_clears_a_decision(session_id):
    _decide(session_id, "s2", "accept")
    body = _decide(session_id, "s2", "undecided").json()
    assert body["decision"] is None
    assert body["summary"]["counts"]["undecided"] == 5


def test_changing_your_mind_replaces_the_decision(session_id):
    _decide(session_id, "s2", "accept")
    body = _decide(session_id, "s2", "reject").json()
    assert body["summary"]["counts"] == {"change": 0, "remove": 0, "keep": 1, "undecided": 4}


# --- Summary and storage ----------------------------------------------------

def test_changes_are_listed_in_resume_order(session_id):
    _decide(session_id, "s5", "accept")   # p14
    _decide(session_id, "s1", "accept")   # p7
    _decide(session_id, "s3", "accept")   # p11
    summary = client.get(f"/api/resume/{session_id}/decisions").json()["summary"]
    assert [c["paragraph_id"] for c in summary["changes"]] == ["p7", "p11", "p14"]


def test_decisions_are_saved_for_milestone_5(session_id):
    _decide(session_id, "s2", "accept")
    saved = storage.load_json(session_id, "decisions.json")
    assert saved["s2"]["paragraph_id"] == "p10"
    assert saved["s2"]["effect"] == "change"


def test_new_analysis_resets_decisions(session_id):
    _decide(session_id, "s2", "accept")
    client.post(f"/api/resume/{session_id}/analyze", json={"job_description": JOB, "consent": True})
    assert storage.load_json(session_id, "decisions.json") is None


def test_decisions_never_touch_the_original_file(session_id):
    before = storage.original_path(session_id).read_bytes()
    _decide(session_id, "s2", "accept")
    _decide(session_id, "s5", "accept")
    assert storage.original_path(session_id).read_bytes() == before


# --- Errors -----------------------------------------------------------------

def test_unknown_suggestion(session_id):
    assert _decide(session_id, "s99", "accept").status_code == 404


def test_decisions_before_analysis(sample_resume):
    files = {"file": ("resume.docx", sample_resume.read_bytes(), "application/octet-stream")}
    sid = client.post("/api/resume", files=files).json()["session_id"]
    response = _decide(sid, "s1", "accept")
    assert response.status_code == 404
    assert "analyse your resume first" in response.json()["detail"]


def test_unknown_action_is_rejected(session_id):
    assert _decide(session_id, "s1", "approve-everything").status_code == 422
