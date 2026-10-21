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
from app.config import settings
from app.docx_reader import read_resume
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
