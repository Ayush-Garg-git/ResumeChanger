"""
Entry point of the backend.

Run it with:
    uvicorn app.main:app --reload

`app.main:app` means: in the package `app`, file `main.py`, use the
variable called `app`.
"""

import logging
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import storage
from app.analyzer import AIError, analyse, paragraphs_to_send
from app.config import settings
from app.docx_reader import read_resume
from app.decisions import DecisionError, make_decision, summarise
from app.docx_editor import Change, apply_changes
from app.validator import validate
from app.schemas import AnalyzeRequest, DecisionRequest
from app.suggestion_checks import check_suggestions, clean_evidence
from app.validation import UploadError, validate_docx_upload

# Privacy rule for logs: record what happened (IDs, counts, error types),
# never resume text or file names. File names often contain a person's name.
logger = logging.getLogger("resume_refiner")
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title=settings.app_name,
    version=settings.api_version,
)


# Every route starts with /api. This keeps backend URLs clearly separate
# from frontend pages, and lets the Vite dev server forward exactly these
# requests to us (see frontend/vite.config.js).
@app.get("/api/health")
def health() -> dict:
    """
    A tiny endpoint that answers "I am running".

    The frontend calls it on page load to confirm the backend is reachable.
    Later it is also useful for deployment checks.
    """
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
    }


@app.post("/api/resume")
async def upload_resume(file: UploadFile = File(...)) -> dict:
    """
    Receive a DOCX, check it, store it temporarily, and return its outline.

    Nothing is sent to any outside service here. The file stays on this server.
    """
    # Housekeeping: remove sessions older than the time limit.
    storage.cleanup_expired_sessions()

    # Read at most one byte more than the limit. If we got that extra byte,
    # the file is too big, and we never load a huge file fully into memory.
    data = await file.read(settings.max_upload_bytes + 1)

    try:
        validate_docx_upload(file.filename, data)
    except UploadError as err:
        logger.info("Upload rejected: %s", err.message)
        raise HTTPException(status_code=err.status_code, detail=err.message)

    session_id = storage.create_session(data)

    try:
        outline = read_resume(storage.original_path(session_id))
    except Exception:
        # The file looked like a DOCX but python-docx couldn't open it.
        # Don't keep a file we can't use.
        storage.delete_session(session_id)
        logger.exception("Could not read DOCX for session %s", session_id)
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


# A plain `def` (not `async def`): the AI call blocks while waiting, and
# FastAPI runs plain functions in a worker thread so other requests continue.
@app.post("/api/resume/{session_id}/analyze")
def analyze_resume(session_id: str, body: AnalyzeRequest) -> dict:
    """
    Compare the uploaded resume with a job description using the AI.

    Returns suggestions only. Nothing in the document is changed here.
    """
    if not storage.is_valid_session_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID.")
    original = storage.original_path(session_id)
    if original is None:
        raise HTTPException(
            status_code=404,
            detail="Your resume was removed or has expired. Please upload it again.",
        )

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

    storage.touch_session(session_id)
    try:
        analysis = analyse(outline, job_description)
    except AIError as err:
        raise HTTPException(status_code=err.status_code, detail=err.message)

    suggestions, dropped = check_suggestions(analysis, sent, job_description)
    analysis = clean_evidence(analysis, sent)

    # Give each kept suggestion a short ID for the approve/reject step.
    result = {
        "role_title": analysis.role_title,
        "fit_summary": analysis.fit_summary,
        "requirements": [r.model_dump() for r in analysis.requirements],
        "suggestions": [
            {"id": f"s{i + 1}", **s.model_dump()} for i, s in enumerate(suggestions)
        ],
        "dropped": [d.__dict__ for d in dropped],
        "questions_for_user": analysis.questions_for_user,
        "no_changes_needed": analysis.no_changes_needed and not suggestions,
        "model": settings.gemini_model,
    }
    storage.save_json(session_id, "analysis.json", result)
    # A new analysis has new suggestions, so earlier decisions no longer apply.
    storage.delete_json(session_id, "decisions.json")
    storage.clear_refined(session_id)

    logger.info(
        "Session %s analysed: %d suggestions kept, %d dropped, %d questions",
        session_id, len(suggestions), len(dropped), len(analysis.questions_for_user),
    )
    return result


def _load_analysis(session_id: str) -> dict:
    """Shared checks for the decision routes."""
    if not storage.is_valid_session_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID.")
    if storage.original_path(session_id) is None:
        raise HTTPException(
            status_code=404,
            detail="Your resume was removed or has expired. Please upload it again.",
        )
    analysis = storage.load_json(session_id, "analysis.json")
    if analysis is None:
        raise HTTPException(status_code=404, detail="Please analyse your resume first.")
    return analysis


@app.put("/api/resume/{session_id}/suggestions/{suggestion_id}")
def decide_suggestion(session_id: str, suggestion_id: str, body: DecisionRequest) -> dict:
    """Save the user's decision on one suggestion. Nothing is applied yet."""
    analysis = _load_analysis(session_id)
    suggestion = next((s for s in analysis["suggestions"] if s["id"] == suggestion_id), None)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="That suggestion no longer exists.")

    try:
        decision = make_decision(suggestion, body.action, body.text)
    except DecisionError as err:
        raise HTTPException(status_code=400, detail=err.message)

    decisions = storage.load_json(session_id, "decisions.json") or {}
    if decision is None:
        decisions.pop(suggestion_id, None)
    else:
        decisions[suggestion_id] = decision
    storage.save_json(session_id, "decisions.json", decisions)
    # Any refined file made earlier no longer matches the decisions.
    storage.clear_refined(session_id)
    storage.touch_session(session_id)

    return {"decision": decision, "summary": summarise(analysis, decisions)}


@app.get("/api/resume/{session_id}/decisions")
def get_decisions(session_id: str) -> dict:
    """Everything decided so far, and the list of changes that would be applied."""
    analysis = _load_analysis(session_id)
    decisions = storage.load_json(session_id, "decisions.json") or {}
    return {"decisions": decisions, "summary": summarise(analysis, decisions)}


@app.post("/api/resume/{session_id}/apply")
def apply_decisions(session_id: str) -> dict:
    """
    Write the approved changes into a copy of the original, then validate it.

    The original stays untouched. Only "change" and "remove" decisions are
    applied; rejected and undecided suggestions never reach the document.
    """
    analysis = _load_analysis(session_id)
    decisions = storage.load_json(session_id, "decisions.json") or {}
    planned = summarise(analysis, decisions)["changes"]
    if not planned:
        raise HTTPException(
            status_code=400,
            detail="There is nothing to apply. Accept, edit or remove at least one suggestion first.",
        )

    changes = [
        Change(
            suggestion_id=c["suggestion_id"],
            paragraph_id=c["paragraph_id"],
            effect=c["effect"],
            original_text=c["original_text"],
            final_text=c["final_text"],
        )
        for c in planned
    ]

    original = storage.original_path(session_id)
    refined = storage.refined_path(session_id)
    storage.clear_refined(session_id)

    try:
        result = apply_changes(original, refined, changes)
    except Exception:
        storage.clear_refined(session_id)
        logger.exception("Applying changes failed for session %s", session_id)
        raise HTTPException(status_code=500, detail="We couldn't create the refined document. Your original is unchanged.")

    applied_ids = {a["paragraph_id"] for a in result.applied}
    final_texts = {c.paragraph_id: c.final_text for c in changes if c.paragraph_id in applied_ids and c.final_text is not None}
    report = validate(original, refined, result.applied, final_texts)

    response = {
        "applied": result.applied,
        "skipped": result.skipped,
        "validation": report.to_dict(),
        "download_ready": report.ok and bool(result.applied),
    }
    storage.save_json(session_id, "apply_report.json", response)
    if not response["download_ready"]:
        # Never offer a file that failed its checks.
        storage.refined_path(session_id).unlink(missing_ok=True)

    logger.info(
        "Session %s applied: %d applied, %d skipped, validation %s",
        session_id, len(result.applied), len(result.skipped), "passed" if report.ok else "FAILED",
    )
    return response


@app.get("/api/resume/{session_id}/download")
def download_refined(session_id: str) -> FileResponse:
    """The validated, refined copy. The frontend gives it the user's file name."""
    if not storage.is_valid_session_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID.")
    report = storage.load_json(session_id, "apply_report.json") if storage.original_path(session_id) else None
    refined = storage.refined_path(session_id)
    if not report or not report.get("download_ready") or not refined.is_file():
        raise HTTPException(status_code=404, detail="There is no refined resume to download. Please create it again.")
    storage.touch_session(session_id)
    return FileResponse(
        refined,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="resume-refined.docx",
        headers={"Cache-Control": "no-store"},
    )


@app.delete("/api/resume/{session_id}", status_code=204)
def delete_resume(session_id: str) -> Response:
    """Delete an uploaded resume right away, at the user's request."""
    if not storage.is_valid_session_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID.")
    if not storage.delete_session(session_id):
        raise HTTPException(status_code=404, detail="This resume was already removed.")
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
