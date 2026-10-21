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
from fastapi.staticfiles import StaticFiles

from app import storage
from app.analyzer import AIError, analyse, paragraphs_to_send
from app.config import settings
from app.docx_reader import read_resume
from app.schemas import AnalyzeRequest
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

    logger.info(
        "Session %s analysed: %d suggestions kept, %d dropped, %d questions",
        session_id, len(suggestions), len(dropped), len(analysis.questions_for_user),
    )
    return result


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
