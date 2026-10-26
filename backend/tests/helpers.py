"""Shared test helpers."""

import time


class JobResult:
    """Looks like a response, so tests can use .status_code and .json()."""

    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body

    @property
    def text(self):
        return str(self._body)


def ai_post(client, url, timeout=30, **kwargs):
    """
    Start an AI step (analyze or answers) and wait for its background job,
    the way the page does: start it, then check /jobs/{id} until it's done.

    Returns the final outcome as a response-like object:
      - 200 with the result when the job finished,
      - the job's own status code and {"detail": ...} when it failed,
      - the original response when the step was refused before starting.
    """
    response = client.post(url, **kwargs)
    if response.status_code != 202:
        return response
    session_id = url.split("/")[3]
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/api/resume/{session_id}/jobs/{job_id}")
        if status.status_code != 200:
            return status
        body = status.json()
        if body["status"] == "done":
            return JobResult(200, body["result"])
        if body["status"] == "failed":
            return JobResult(body["status_code"], {"detail": body["detail"]})
        time.sleep(0.02)
    raise AssertionError("job did not finish in time")
