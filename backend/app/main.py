"""
Entry point of the backend.

Run it with:
    uvicorn app.main:app --reload

`app.main:app` means: in the package `app`, file `main.py`, use the
variable called `app`.

How a session's files fit together
----------------------------------
    original.docx        the upload, never modified
    analysis.json        AI suggestions (has a random analysis_id)
    decisions.json       {"analysis_id", "version", "items": {s1: ...}}
    refined.docx         approved changes applied to a copy
    apply_report.json    what was applied and the validation result,
                         stamped with the decisions version it was made from
    preview/             page images of the marked copies

The version stamp is what keeps "only approved changes" true even when
clicks overlap: a refined file or preview made from older decisions is
never served.
"""

import logging
import os
import re
import shutil
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from app import renderer, storage
from app.analyzer import AIError, analyse, paragraphs_to_send, person_name_words
from app.answers import ADD_KIND, MAX_ANSWER_CHARS, place_answers
from app.config import settings
from app.decisions import DecisionError, make_decision, summarise
from app.docx_editor import Change, apply_changes, mark_original, neutralise_external_links
from app.docx_reader import read_resume
from app.limits import BodySizeLimitMiddleware, check_analysis_allowed, check_upload_allowed
from app.page_fit import page_fit
from app.schemas import AnalyzeRequest, AnswersRequest, DecisionRequest
from app.suggestion_checks import check_suggestions, clean_evidence
from app.validation import UploadError, validate_docx_upload
from app.validator import validate

# Privacy rule for logs: record what happened (IDs, counts, error types),
# never resume text or file names. File names often contain a person's name.
logger = logging.getLogger("resume_refiner")
logging.basicConfig(level=logging.INFO)

EXPIRED = "Your resume was removed or has expired. Please upload it again."


# ---------------------------------------------------------------------------
# Background cleanup
# ---------------------------------------------------------------------------
def _cleanup_loop(stop: threading.Event) -> None:
    """Delete expired sessions every few minutes, even if nobody uploads."""
    while not stop.wait(settings.cleanup_interval_seconds):
        try:
            removed = storage.cleanup_expired_sessions()
            if removed:
                logger.info("Cleanup removed %d expired session(s)", removed)
        except Exception:
            logger.exception("Cleanup failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop = threading.Event()
    thread = threading.Thread(target=_cleanup_loop, args=(stop,), daemon=True)
    thread.start()
    yield
    stop.set()


app = FastAPI(
    title=settings.app_name,
    version=settings.api_version,
    lifespan=lifespan,
    docs_url="/docs" if settings.enable_api_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.enable_api_docs else None,
)

# Refuse oversized request bodies before anything else reads them.
app.add_middleware(BodySizeLimitMiddleware)


# Browser security headers on every response.
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    # The /docs page loads its scripts from a CDN, so it can't use this policy.
    if not request.url.path.startswith("/docs"):
        response.headers["Content-Security-Policy"] = _CSP
    return response


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    """Anything we didn't plan for: log the type only, show a calm message."""
    logger.error("Unexpected %s on %s", type(exc).__name__, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on our side. Your original file is unchanged. Please try again."},
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
# Every route starts with /api. This keeps backend URLs clearly separate
# from frontend pages, and lets the Vite dev server forward exactly these
# requests to us (see frontend/vite.config.js).
@app.get("/api/health")
def health() -> dict:
    """Answers "I am running", and what this server can do."""
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.api_version,
        # Lets the frontend show an accurate privacy notice before analysis.
        "ai": {
            "configured": bool(settings.gemini_api_key),
            "provider": "Google Gemini",
            "model": settings.gemini_model,
            "tier": settings.gemini_tier,
        },
        "preview_available": renderer.is_available(),
        "session_minutes": settings.session_ttl_minutes,
    }


# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------
def _check_session(session_id: str) -> Path:
    if not storage.is_valid_session_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID.")
    original = storage.original_path(session_id)
    if original is None:
        raise HTTPException(status_code=404, detail=EXPIRED)
    return original


def _load_analysis(session_id: str) -> dict:
    _check_session(session_id)
    analysis = storage.load_json(session_id, "analysis.json")
    if analysis is None:
        raise HTTPException(status_code=404, detail="Please analyse your resume first.")
    return analysis


def _load_decisions(session_id: str, analysis: dict) -> dict:
    """Decisions for THIS analysis. Leftovers from an older analysis are ignored."""
    saved = storage.load_json(session_id, "decisions.json")
    if not saved or saved.get("analysis_id") != analysis["analysis_id"]:
        return {"analysis_id": analysis["analysis_id"], "version": 0, "items": {}}
    return saved


def _planned_changes(analysis: dict, items: dict) -> list[Change]:
    """The approved changes, in resume order, ready for the editor."""
    return [
        Change(
            suggestion_id=c["suggestion_id"],
            paragraph_id=c["paragraph_id"],
            effect=c["effect"],
            original_text=c["original_text"],
            final_text=c["final_text"],
        )
        for c in summarise(analysis, items)["changes"]
    ]


def _current_report(session_id: str) -> tuple[dict, dict, dict]:
    """
    (analysis, decisions, report) only if a validated refined file exists AND
    it was made from the current decisions. Call while holding the session lock.
    """
    analysis = _load_analysis(session_id)
    decisions = _load_decisions(session_id, analysis)
    report = storage.load_json(session_id, "apply_report.json")
    current = (
        report is not None
        and report.get("download_ready")
        and report.get("analysis_id") == analysis["analysis_id"]
        and report.get("decisions_version") == decisions["version"]
        and storage.refined_path(session_id).is_file()
    )
    if not current:
        raise HTTPException(status_code=404, detail="Please create the refined resume first.")
    return analysis, decisions, report


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
# Plain `def`: reading a DOCX takes real CPU time. FastAPI runs plain
# functions in a worker thread, so one slow file can't freeze the server.
# (`async def` would run it on the single event loop and block everyone.)
@app.post("/api/resume")
def upload_resume(request: Request, file: UploadFile = File(...)) -> dict:
    """
    Receive a DOCX, check it, store it temporarily, and return its outline.

    Nothing is sent to any outside service here. The file stays on this server.
    """
    check_upload_allowed(request)
    storage.cleanup_expired_sessions()

    # Read at most one byte more than the limit, to detect oversized files.
    data = file.file.read(settings.max_upload_bytes + 1)

    try:
        validate_docx_upload(file.filename, data)
    except UploadError as err:
        logger.info("Upload rejected: %s", err.message)
        raise HTTPException(status_code=err.status_code, detail=err.message)

    session_id = storage.create_session(data)
    try:
        outline = read_resume(storage.original_path(session_id))
    except Exception:
        # The file looked like a DOCX but couldn't be read. Don't keep it.
        storage.delete_session(session_id)
        logger.warning("Could not read DOCX for session %s", session_id)
        raise HTTPException(
            status_code=400,
            detail="This Word document could not be read. It may be damaged.",
        )

    logger.info(
        "Session %s created: %d paragraphs, %d sections, %d warnings",
        session_id, len(outline.paragraphs), len(outline.sections), len(outline.warnings),
    )
    return {
        "session_id": session_id,
        "expires_in_minutes": settings.session_ttl_minutes,
        **outline.to_dict(),
    }


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
@app.post("/api/resume/{session_id}/analyze")
def analyze_resume(request: Request, session_id: str, body: AnalyzeRequest) -> dict:
    """
    Compare the uploaded resume with a job description using the AI.

    Returns suggestions only. Nothing in the document is changed here.
    """
    original = _check_session(session_id)

    # The user must agree, every time, before their resume leaves the server.
    if not body.consent:
        raise HTTPException(
            status_code=400,
            detail="Please confirm that your resume may be sent to the AI service.",
        )

    job_description = body.job_description.strip()
    if len(job_description) < settings.job_description_min_chars:
        raise HTTPException(
            status_code=400,
            detail="The job description looks too short. Please paste the full posting.",
        )
    if len(job_description) > settings.job_description_max_chars:
        raise HTTPException(
            status_code=400,
            detail=f"The job description is too long (over {settings.job_description_max_chars} characters).",
        )

    outline = read_resume(original)
    sent = paragraphs_to_send(outline)
    if not sent:
        raise HTTPException(status_code=400, detail="We couldn't find resume content to analyse.")

    check_analysis_allowed(request, session_id)
    storage.touch_session(session_id)

    # The AI call is slow, so it happens WITHOUT the lock: the user can
    # still remove their resume meanwhile.
    try:
        analysis = analyse(outline, job_description)
    except AIError as err:
        raise HTTPException(status_code=err.status_code, detail=err.message)

    suggestions, dropped = check_suggestions(analysis, sent, job_description, person_name_words(outline))
    analysis = clean_evidence(analysis, sent)

    result = {
        # A new random ID per analysis. Decisions are tied to it, so choices
        # made on an older analysis can never be applied to a newer one.
        "analysis_id": uuid.uuid4().hex,
        "role_title": analysis.role_title,
        "fit_summary": analysis.fit_summary,
        "requirements": [r.model_dump() for r in analysis.requirements],
        "suggestions": [{"id": f"s{i + 1}", **s.model_dump()} for i, s in enumerate(suggestions)],
        "dropped": [d.__dict__ for d in dropped],
        "questions_for_user": analysis.questions_for_user,
        # IDs so the user can answer each question (Milestone 4b).
        "questions": [{"id": f"q{i + 1}", "text": q} for i, q in enumerate(analysis.questions_for_user)],
        "no_changes_needed": analysis.no_changes_needed and not suggestions,
        "model": settings.gemini_model,
    }

    with storage.session_lock(session_id):
        _check_session(session_id)  # it may have been removed while the AI worked
        storage.save_json(session_id, "analysis.json", result)
        storage.delete_json(session_id, "decisions.json")
        storage.clear_refined(session_id)

    logger.info(
        "Session %s analysed: %d suggestions kept, %d dropped, %d questions",
        session_id, len(suggestions), len(dropped), len(analysis.questions_for_user),
    )
    return result


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------
@app.put("/api/resume/{session_id}/suggestions/{suggestion_id}")
def decide_suggestion(session_id: str, suggestion_id: str, body: DecisionRequest) -> dict:
    """Save the user's decision on one suggestion. Nothing is applied yet."""
    with storage.session_lock(session_id):
        analysis = _load_analysis(session_id)
        if body.analysis_id != analysis["analysis_id"]:
            raise HTTPException(
                status_code=409,
                detail="These suggestions were replaced by a newer analysis. Please refresh the page.",
            )
        suggestion = next((s for s in analysis["suggestions"] if s["id"] == suggestion_id), None)
        if suggestion is None:
            raise HTTPException(status_code=404, detail="That suggestion no longer exists.")

        try:
            decision = make_decision(suggestion, body.action, body.text)
        except DecisionError as err:
            raise HTTPException(status_code=400, detail=err.message)

        decisions = _load_decisions(session_id, analysis)

        # Two changes to the same line can't both be applied: the second
        # would no longer match the line's text. Ask the user to choose.
        if decision is not None and decision["effect"] in ("change", "remove"):
            for other_id, other in decisions["items"].items():
                if (
                    other_id != suggestion_id
                    and other["paragraph_id"] == suggestion["paragraph_id"]
                    and other["effect"] in ("change", "remove")
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=f"You already chose a change for this line ({other_id}). "
                        "Undo that one first, or use Edit here to combine both in your own words.",
                    )

        if decision is None:
            decisions["items"].pop(suggestion_id, None)
        else:
            decisions["items"][suggestion_id] = decision
        decisions["version"] += 1
        storage.save_json(session_id, "decisions.json", decisions)
        # Any refined file or preview made earlier no longer matches.
        storage.clear_refined(session_id)
        storage.touch_session(session_id)

        return {"decision": decision, "summary": summarise(analysis, decisions["items"])}


@app.post("/api/resume/{session_id}/answers")
def answer_questions(request: Request, session_id: str, body: AnswersRequest) -> dict:
    """
    Turn the user's Yes answers into suggestions (Milestone 4b).

    Only Yes answers with the user's own words are sent to the AI. New
    suggestions are added to the analysis as "add_confirmed" cards, which
    the user accepts, edits or rejects like any other. Answering again
    replaces the earlier answer-based cards.
    """
    analysis = _load_analysis(session_id)
    if body.analysis_id != analysis["analysis_id"]:
        raise HTTPException(status_code=409, detail="These questions were replaced by a newer analysis. Please refresh the page.")
    if not body.consent:
        raise HTTPException(status_code=400, detail="Please confirm that your resume and answers may be sent to the AI service.")

    questions = {q["id"]: q["text"] for q in analysis.get("questions", [])}
    confirmed = []
    for a in body.answers:
        details = " ".join(a.details.split())
        if a.question_id not in questions or not a.has_it:
            continue  # "No" answers never leave the server
        if not details:
            raise HTTPException(status_code=400, detail="For each Yes, please say briefly where or how you used it.")
        if len(details) > MAX_ANSWER_CHARS:
            raise HTTPException(status_code=400, detail=f"Please keep each answer under {MAX_ANSWER_CHARS} characters.")
        confirmed.append({"question_id": a.question_id, "question": questions[a.question_id], "details": details})
    if not confirmed:
        raise HTTPException(status_code=400, detail="Answer Yes to at least one question to get suggestions.")

    outline = read_resume(storage.original_path(session_id))
    check_analysis_allowed(request, session_id)
    taken = {s["paragraph_id"] for s in analysis["suggestions"] if s["kind"] != ADD_KIND}
    try:
        kept, dropped, not_placed = place_answers(outline, analysis["role_title"], confirmed, taken)
    except AIError as err:
        raise HTTPException(status_code=err.status_code, detail=err.message)

    with storage.session_lock(session_id):
        current = _load_analysis(session_id)
        if current["analysis_id"] != analysis["analysis_id"]:
            raise HTTPException(status_code=409, detail="These questions were replaced by a newer analysis. Please refresh the page.")

        # Replace earlier answer-based cards, and forget decisions on them.
        old_ids = {s["id"] for s in current["suggestions"] if s["kind"] == ADD_KIND}
        base = [s for s in current["suggestions"] if s["kind"] != ADD_KIND]
        next_number = max([int(s["id"][1:]) for s in current["suggestions"]] or [0]) + 1
        added = [{"id": f"s{next_number + i}", **k} for i, k in enumerate(kept)]
        current["suggestions"] = base + added
        storage.save_json(session_id, "analysis.json", current)

        decisions = _load_decisions(session_id, current)
        if old_ids & set(decisions["items"]):
            for sid in old_ids:
                decisions["items"].pop(sid, None)
            decisions["version"] += 1
            storage.save_json(session_id, "decisions.json", decisions)
            storage.clear_refined(session_id)
        storage.touch_session(session_id)

        logger.info(
            "Session %s answers: %d sent, %d placed, %d dropped, %d not placed",
            session_id, len(confirmed), len(added), len(dropped), len(not_placed),
        )
        return {
            "suggestions": current["suggestions"],
            "added": [s["id"] for s in added],
            "dropped": dropped,
            "not_placed": not_placed,
            "summary": summarise(current, decisions["items"]),
        }


@app.get("/api/resume/{session_id}/decisions")
def get_decisions(session_id: str) -> dict:
    """Everything decided so far, and the list of changes that would be applied."""
    with storage.session_lock(session_id):
        analysis = _load_analysis(session_id)
        decisions = _load_decisions(session_id, analysis)
        return {"decisions": decisions["items"], "summary": summarise(analysis, decisions["items"])}


# ---------------------------------------------------------------------------
# Apply and download
# ---------------------------------------------------------------------------
@app.post("/api/resume/{session_id}/apply")
def apply_decisions(session_id: str) -> dict:
    """
    Write the approved changes into a copy of the original, then validate it.

    Holds the session lock throughout: a decision clicked meanwhile waits,
    then withdraws this file, so a stale file is never offered.
    """
    with storage.session_lock(session_id):
        analysis = _load_analysis(session_id)
        decisions = _load_decisions(session_id, analysis)
        changes = _planned_changes(analysis, decisions["items"])
        if not changes:
            raise HTTPException(
                status_code=400,
                detail="There is nothing to apply. Accept, edit or remove at least one suggestion first.",
            )

        original = storage.original_path(session_id)
        refined = storage.refined_path(session_id)
        storage.clear_refined(session_id)

        try:
            result = apply_changes(original, refined, changes)
        except Exception:
            storage.clear_refined(session_id)
            logger.warning("Applying changes failed for session %s", session_id)
            raise HTTPException(
                status_code=500,
                detail="We couldn't create the refined document. Your original is unchanged.",
            )

        applied_ids = {a["paragraph_id"] for a in result.applied}
        final_texts = {
            c.paragraph_id: c.final_text
            for c in changes
            if c.paragraph_id in applied_ids and c.final_text is not None
        }
        report = validate(original, refined, result.applied, final_texts)

        response = {
            "applied": result.applied,
            "skipped": result.skipped,
            "validation": report.to_dict(),
            "download_ready": report.ok and bool(result.applied),
            "analysis_id": analysis["analysis_id"],
            "decisions_version": decisions["version"],
        }
        storage.save_json(session_id, "apply_report.json", response)
        if not response["download_ready"]:
            refined.unlink(missing_ok=True)  # never offer a file that failed its checks

        logger.info(
            "Session %s applied: %d applied, %d skipped, validation %s",
            session_id, len(result.applied), len(result.skipped), "passed" if report.ok else "FAILED",
        )
        return response


@app.get("/api/resume/{session_id}/download")
def download_refined(session_id: str) -> FileResponse:
    """The validated, refined copy. The frontend gives it the user's file name."""
    with storage.session_lock(session_id):
        try:
            _current_report(session_id)
        except HTTPException as err:
            if err.detail == EXPIRED:
                raise
            raise HTTPException(status_code=404, detail="There is no refined resume to download. Please create it again.")
        # Copy while holding the lock, so a decision clicked right now can't
        # delete the file halfway through sending it.
        fd, name = tempfile.mkstemp(prefix="refined-", suffix=".docx")
        os.close(fd)
        snapshot = Path(name)
        shutil.copyfile(storage.refined_path(session_id), snapshot)
        storage.touch_session(session_id)

    return FileResponse(
        snapshot,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="resume-refined.docx",
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(lambda: snapshot.unlink(missing_ok=True)),
    )


# ---------------------------------------------------------------------------
# Preview and page limit
# ---------------------------------------------------------------------------
# One preview build at a time per session, so two identical requests (e.g.
# React running an effect twice in development) don't render twice.
_preview_locks: dict[str, threading.Lock] = {}
_preview_locks_guard = threading.Lock()


def _preview_lock(session_id: str) -> threading.Lock:
    with _preview_locks_guard:
        return _preview_locks.setdefault(session_id, threading.Lock())


@app.get("/api/resume/{session_id}/preview")
def preview(session_id: str) -> dict:
    """
    Page images of the original and the refined resume, with page counts.

    The images come from marked copies: in "before", lines that change are
    yellow and removed lines red; in "after", changed words are yellow.

    Rendering is slow, so it runs in a private temp folder WITHOUT the
    session lock (the user can keep clicking). The result is only saved if
    the decisions are still the same when it finishes.
    """
    with _preview_lock(session_id):
        with storage.session_lock(session_id):
            analysis, decisions, report = _current_report(session_id)
            cached = storage.load_json(session_id, "preview/preview.json")
            if cached and cached.get("decisions_version") == decisions["version"]:
                return cached
            if not renderer.is_available():
                return {"available": False, "message": "Preview isn't available on this server."}
            changes = _planned_changes(analysis, decisions["items"])
            version = decisions["version"]
            original = storage.original_path(session_id)
            work = Path(tempfile.mkdtemp(prefix="preview-"))
            shutil.copyfile(original, work / "original.docx")

        try:
            marked_before = work / "marked-before.docx"
            marked_after = work / "marked-after.docx"
            mark_original(work / "original.docx", marked_before, report["applied"])
            apply_changes(work / "original.docx", marked_after, changes, highlight=True)
            # Don't let LibreOffice follow links to outside files or URLs.
            neutralise_external_links(marked_before)
            neutralise_external_links(marked_after)
            images_dir = work / "images"
            before = renderer.render(marked_before, images_dir, "before")
            after = renderer.render(marked_after, images_dir, "after")
        except renderer.RenderError as err:
            shutil.rmtree(work, ignore_errors=True)
            raise HTTPException(status_code=503, detail=str(err))
        except Exception:
            shutil.rmtree(work, ignore_errors=True)
            raise

        try:
            with storage.session_lock(session_id):
                analysis, decisions, _ = _current_report(session_id)
                if decisions["version"] != version:
                    raise HTTPException(
                        status_code=409,
                        detail="Your choices changed while the preview was being drawn. Please create the refined resume again.",
                    )
                folder = storage.preview_dir(session_id)
                shutil.rmtree(folder, ignore_errors=True)
                folder.mkdir(parents=True)
                for image in [*before.images, *after.images]:
                    shutil.move(str(image), folder / image.name)

                base = f"/api/resume/{session_id}/preview"
                result = {
                    "available": True,
                    "before": {"pages": before.pages, "images": [f"{base}/{p.name}" for p in before.images]},
                    "after": {"pages": after.pages, "images": [f"{base}/{p.name}" for p in after.images]},
                    "max_preview_pages": renderer.MAX_PREVIEW_PAGES,
                    "decisions_version": version,
                }
                storage.save_json(session_id, "preview/preview.json", result)
        finally:
            shutil.rmtree(work, ignore_errors=True)  # marked copies never outlive the request

        logger.info("Session %s preview: %d -> %d pages", session_id, before.pages, after.pages)
        return result


_PREVIEW_IMAGE = re.compile(r"^(before|after)-\d{1,2}\.png$")


@app.get("/api/resume/{session_id}/preview/{name}")
def preview_image(session_id: str, name: str) -> FileResponse:
    if not storage.is_valid_session_id(session_id) or not _PREVIEW_IMAGE.match(name):
        raise HTTPException(status_code=400, detail="Invalid preview request.")
    path = storage.preview_dir(session_id) / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="This preview has expired. Please create it again.")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/resume/{session_id}/page-fit")
def check_page_fit(session_id: str, max_pages: int) -> dict:
    """Does the refined resume fit `max_pages`? If not, options to choose from."""
    if not 1 <= max_pages <= 10:
        raise HTTPException(status_code=400, detail="Choose between 1 and 10 pages.")
    with storage.session_lock(session_id):
        analysis, decisions, _ = _current_report(session_id)
        shown = storage.load_json(session_id, "preview/preview.json")
        if not shown or not shown.get("available") or shown.get("decisions_version") != decisions["version"]:
            raise HTTPException(status_code=404, detail="Please load the preview first.")
        return page_fit(analysis, decisions["items"], shown["after"]["pages"], max_pages)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
@app.delete("/api/resume/{session_id}", status_code=204)
def delete_resume(session_id: str) -> Response:
    """Delete an uploaded resume right away, at the user's request."""
    if not storage.is_valid_session_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID.")
    if not storage.delete_session(session_id):
        raise HTTPException(status_code=404, detail="This resume was already removed.")
    with _preview_locks_guard:
        _preview_locks.pop(session_id, None)
    logger.info("Session %s deleted by user", session_id)
    return Response(status_code=204)


# --- Serving the frontend in production ------------------------------------
# In development, Vite serves the React app and forwards /api to us.
# In production there is no Vite: `npm run build` turns the React app into
# plain HTML/CSS/JS files, and FastAPI serves those files itself. The whole
# app then runs as one service at one URL.
#
# This block must stay at the bottom: routes defined above (all /api/...)
# are matched first, and anything else falls through to the frontend files.
# If the build folder doesn't exist (normal in development), we skip it.
if settings.static_dir and Path(settings.static_dir).is_dir():
    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="frontend")
