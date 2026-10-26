"""
Background jobs for the slow AI steps (analysis and answers).

Why: an AI answer can take up to 100 seconds. If the page waits on one
open connection that long, anything that drops the connection (a phone
locking its screen, switching apps, moving from Wi-Fi to mobile data)
loses the result, and the user sees "Could not reach the server".

Instead:
    1. The page asks the server to start the work and gets a job ID back
       straight away.
    2. The server does the work in a background thread.
    3. The page checks the job every 2 seconds. A dropped check doesn't
       matter: the next one picks up the same job, and the result waits.

Jobs live in memory only, and finished ones are forgotten after an hour.
If the server restarts, running jobs are lost, and the page says so.
"""

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException

logger = logging.getLogger("resume_refiner")

# A few at a time is plenty for a small server; others wait their turn.
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="job")
_jobs: dict[str, dict] = {}
_lock = threading.Lock()

KEEP_SECONDS = 60 * 60

GENERIC_FAILURE = "Something went wrong on our side. Your original file is unchanged. Please try again."


def _prune() -> None:
    cutoff = time.time() - KEEP_SECONDS
    for job_id in [j for j, rec in _jobs.items() if rec["started"] < cutoff]:
        del _jobs[job_id]


def start(session_id: str, kind: str, work) -> str:
    """Run `work()` in the background. Returns the new job's ID."""
    job_id = uuid.uuid4().hex
    with _lock:
        _prune()
        _jobs[job_id] = {
            "session_id": session_id,
            "kind": kind,
            "status": "running",
            "started": time.time(),
        }

    def run():
        try:
            result = work()
            update = {"status": "done", "result": result}
        except HTTPException as err:
            # Expected failures (AI busy, quota, expired session...) carry
            # a message written for the user.
            update = {"status": "failed", "status_code": err.status_code, "detail": err.detail}
        except Exception as err:
            # Type only in the log: never resume text.
            logger.error("Job %s (%s) failed: %s", job_id, kind, type(err).__name__)
            update = {"status": "failed", "status_code": 500, "detail": GENERIC_FAILURE}
        with _lock:
            if job_id in _jobs:
                _jobs[job_id].update(update)

    _executor.submit(run)
    return job_id


def get(job_id: str, session_id: str) -> dict | None:
    """A copy of the job's record, or None if unknown or not this session's."""
    with _lock:
        record = _jobs.get(job_id)
        if record is None or record["session_id"] != session_id:
            return None
        return dict(record)


def forget_session(session_id: str) -> None:
    """Drop all jobs of a session (when the user removes their resume)."""
    with _lock:
        for job_id in [j for j, rec in _jobs.items() if rec["session_id"] == session_id]:
            del _jobs[job_id]
