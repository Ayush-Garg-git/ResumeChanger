"""
Temporary storage for uploaded resumes.

Each upload gets its own folder, named by a random "session ID":

    <upload_dir>/
        3f9c...e1/
            original.docx      <- exactly the bytes the user uploaded

`original.docx` is never modified. Later milestones write edited copies
next to it, so the original is always there for comparison and recovery.

There is no database. A session is just a folder, and it is deleted when
the user removes it or when it has been unused for `session_ttl_minutes`.

Two requests for the same session can arrive at the same moment (FastAPI
runs them in parallel threads). Anything that reads a session's files,
changes them and writes them back must hold `session_lock(session_id)`,
or one request's changes can silently overwrite the other's.
"""

import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
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


# One lock per session. RLock: the same request may take it again inside
# a helper without blocking itself.
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _lock_for(session_id: str) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault(session_id, threading.RLock())


@contextmanager
def session_lock(session_id: str):
    """Hold this while reading and then changing a session's files."""
    lock = _lock_for(session_id)
    with lock:
        yield


def _forget_lock(session_id: str) -> None:
    with _locks_guard:
        _locks.pop(session_id, None)


def save_json(session_id: str, name: str, data: dict) -> None:
    """
    Save a small JSON file next to the original, atomically: write a temp
    file and swap it in, so a reader never sees a half-written file.
    """
    target = _session_dir(session_id) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_json(session_id: str, name: str) -> dict | None:
    path = _session_dir(session_id) / name
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def delete_json(session_id: str, name: str) -> None:
    (_session_dir(session_id) / name).unlink(missing_ok=True)


REFINED_NAME = "refined.docx"


def refined_path(session_id: str) -> Path:
    """Where the edited copy goes. It may not exist yet."""
    return _session_dir(session_id) / REFINED_NAME


def preview_dir(session_id: str) -> Path:
    """Preview images and their marked .docx copies live here."""
    return _session_dir(session_id) / "preview"


def clear_refined(session_id: str) -> None:
    """Forget the edited copy and its preview, e.g. when a decision changes."""
    refined_path(session_id).unlink(missing_ok=True)
    delete_json(session_id, "apply_report.json")
    shutil.rmtree(preview_dir(session_id), ignore_errors=True)


def touch_session(session_id: str) -> None:
    """Mark a session as recently used, so it isn't cleaned up mid-work."""
    _session_dir(session_id).touch()


def delete_session(session_id: str) -> bool:
    """Delete a session folder. Returns False if it didn't exist."""
    folder = _session_dir(session_id)
    with session_lock(session_id):
        if not folder.is_dir():
            return False
        shutil.rmtree(folder)
    _forget_lock(session_id)
    return True


def cleanup_expired_sessions() -> int:
    """
    Delete every session folder unused for longer than the time limit.

    Runs every few minutes in the background (see main.py) and on each
    upload. Sessions busy with a request are skipped this round.
    Returns how many folders were removed.
    """
    if not settings.upload_dir.is_dir():
        return 0

    cutoff = time.time() - settings.session_ttl_minutes * 60
    removed = 0
    for folder in settings.upload_dir.iterdir():
        # Skip anything that isn't one of our session folders.
        if not folder.is_dir() or not is_valid_session_id(folder.name):
            continue
        if folder.stat().st_mtime >= cutoff:
            continue
        lock = _lock_for(folder.name)
        if not lock.acquire(blocking=False):
            continue  # in use right now; try again next round
        try:
            shutil.rmtree(folder, ignore_errors=True)
        finally:
            lock.release()
        _forget_lock(folder.name)
        removed += 1
    return removed
