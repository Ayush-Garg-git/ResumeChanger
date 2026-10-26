"""
Tests for background AI jobs.

The problem they solve: a phone dropping a long-open connection lost the
analysis ("Could not reach the server"). Now the work runs on the server
and the page checks in every couple of seconds, so a dropped check loses
nothing.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app import analyzer
from app.config import settings
from app.main import app
from tests.test_analyze import GOOD_ANSWER, JOB

client = TestClient(app)


@pytest.fixture
def slow_ai(monkeypatch):
    """An AI that takes a moment, like the real one."""
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")

    def slow(prompt, schema, model=None):
        time.sleep(0.6)
        return json.dumps(GOOD_ANSWER)

    monkeypatch.setattr(analyzer, "call_model", slow)


def _upload(sample_resume):
    files = {"file": ("r.docx", sample_resume.read_bytes(), "application/octet-stream")}
    return client.post("/api/resume", files=files).json()["session_id"]


def _start(sid, consent=True):
    return client.post(f"/api/resume/{sid}/analyze", json={"job_description": JOB, "consent": consent})


def _status(sid, job_id):
    return client.get(f"/api/resume/{sid}/jobs/{job_id}")


def _wait(sid, job_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = _status(sid, job_id).json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_start_answers_at_once_even_when_the_ai_is_slow(sample_resume, slow_ai):
    sid = _upload(sample_resume)
    started = time.monotonic()
    response = _start(sid)
    assert time.monotonic() - started < 0.4  # didn't wait for the 0.6s AI
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert _status(sid, job_id).json()["status"] == "running"
    assert _wait(sid, job_id)["status"] == "done"


def test_result_survives_missed_checks(sample_resume, slow_ai):
    # The phone locks: no checks for a while. When it comes back, the
    # result is still there, as many times as it asks.
    sid = _upload(sample_resume)
    job_id = _start(sid).json()["job_id"]
    time.sleep(1.0)  # "screen locked": nobody is checking
    first = _status(sid, job_id).json()
    second = _status(sid, job_id).json()
    assert first["status"] == "done" and second["status"] == "done"
    assert first["result"]["role_title"] == "Data Analyst"
    assert first["result"] == second["result"]


def test_quick_problems_are_still_reported_at_once(sample_resume, slow_ai):
    sid = _upload(sample_resume)
    response = _start(sid, consent=False)
    assert response.status_code == 400  # not a job: refused straight away


def test_a_failed_job_says_why(sample_resume, monkeypatch):
    class RateLimited(Exception):
        status_code = 429

    def limited(prompt, schema, model=None):
        raise RateLimited("quota")

    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(analyzer, "call_model", limited)
    sid = _upload(sample_resume)
    body = _wait(sid, _start(sid).json()["job_id"])
    assert body["status"] == "failed"
    assert body["status_code"] == 429
    assert "limit" in body["detail"]


def test_unknown_or_lost_jobs_are_explained(sample_resume):
    sid = _upload(sample_resume)
    response = _status(sid, "0" * 32)
    assert response.status_code == 404
    assert "no longer running" in response.json()["detail"]


def test_jobs_belong_to_their_session(sample_resume, slow_ai):
    mine = _upload(sample_resume)
    theirs = _upload(sample_resume)
    job_id = _start(mine).json()["job_id"]
    assert _status(theirs, job_id).status_code == 404


def test_job_ids_are_checked(sample_resume):
    sid = _upload(sample_resume)
    assert _status(sid, "not-an-id").status_code == 400


def test_removing_the_resume_ends_its_jobs(sample_resume, slow_ai):
    sid = _upload(sample_resume)
    job_id = _start(sid).json()["job_id"]
    client.delete(f"/api/resume/{sid}")
    response = _status(sid, job_id)
    assert response.status_code == 404
    assert "upload it again" in response.json()["detail"]
