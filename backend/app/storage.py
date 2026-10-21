"""
Temporary storage for uploaded resumes.

Each upload gets its own folder, named by a random "session ID":

    <upload_dir>/
        3f9c...e1/
            original.docx      <- exactly the bytes the user uploaded

`original.docx` is never modified. Later milestones write edited copies
next to it, so the original is always there for comparison and recovery.

There is no database. A session is just a folder, and it is deleted when
the user removes it or when it is older than `session_ttl_minutes`.
"""

import re
import shutil
import time
import uuid
from pathlib import Path

from app.config import settings

ORIGINAL_NAME = "original.docx"

# A session ID is 32 lowercase hex characters (a UUID without dashes).
# We check every ID that comes from outside against this pattern, so a
# request like DELETE /api/resume/../../etc can never point outside our folder.
_SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def is_valid_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID_PATTERN.match(session_id))


def _session_dir(session_id: str) -> Path:
    if not is_valid_session_id(session_id):
        raise ValueError("Invalid session ID")
    return settings.upload_dir / session_id


def create_session(file_bytes: bytes) -> str:
    """Save an uploaded file in a new session folder and return its ID."""
    session_id = uuid.uuid4().hex  # random, so IDs can't be guessed
    folder = _session_dir(session_id)
    folder.mkdir(parents=True, exist_ok=False)
    (folder / ORIGINAL_NAME).write_bytes(file_bytes)
    return session_id


def original_path(session_id: str) -> Path | None:
    """Path to the untouched uploaded file, or None if the session is gone."""
    path = _session_dir(session_id) / ORIGINAL_NAME
    return path if path.is_file() else None


def delete_session(session_id: str) -> bool:
    """Delete a session folder. Returns False if it didn't exist."""
    folder = _session_dir(session_id)
    if not folder.is_dir():
        return False
    shutil.rmtree(folder)
    return True


def cleanup_expired_sessions() -> int:
    """
    Delete every session folder older than the time limit.

    Called on each upload, so old files disappear without needing a
    separate background job. Returns how many folders were removed.
    """
    if not settings.upload_dir.is_dir():
        return 0

    cutoff = time.time() - settings.session_ttl_minutes * 60
    removed = 0
    for folder in settings.upload_dir.iterdir():
        # Skip anything that isn't one of our session folders.
        if not folder.is_dir() or not is_valid_session_id(folder.name):
            continue
        if folder.stat().st_mtime < cutoff:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    return removed
