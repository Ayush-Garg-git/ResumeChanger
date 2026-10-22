"""
Limits that protect a small public server and the free AI quota.

1. Request size: refuse any request body larger than the upload limit
   (plus a little room for form overhead). Without this, the web
   framework would happily save a 2 GB upload to disk before our code
   ever saw it.

2. Rate limits: count recent actions per visitor (IP address), per
   session, and for the whole site per day. Counts live in memory, so
   they reset when the server restarts. That's fine for this purpose:
   the goal is to stop runaway scripts, not to bill anyone.
"""

import threading
import time
from collections import deque

from fastapi import HTTPException, Request

from app.config import settings

# ---------------------------------------------------------------------------
# 1. Request size
# ---------------------------------------------------------------------------
# Room for the multipart "envelope" around the file, and for JSON bodies.
_OVERHEAD_BYTES = 1024 * 1024


def max_body_bytes() -> int:
    return settings.max_upload_bytes + _OVERHEAD_BYTES


class BodySizeLimitMiddleware:
    """
    Plain ASGI middleware: it sits in front of the app and watches the
    request body as it streams in.

    - If the declared Content-Length is too big, answer 413 at once.
    - Otherwise count bytes as they arrive and stop as soon as the limit is
      passed (this also catches uploads that don't declare a length).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        limit = max_body_bytes()
        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            return await _send_413(send)

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # An HTTPException raised while the body is being read
                    # is passed through by FastAPI as a normal 413 response.
                    raise HTTPException(status_code=413, detail=_TOO_LARGE)
            return message

        await self.app(scope, limited_receive, send)


_TOO_LARGE = "This request is too large."


async def _send_413(send):
    body = b'{"detail":"' + _TOO_LARGE.encode() + b'"}'
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
    })
    await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------------------
# 2. Rate limits
# ---------------------------------------------------------------------------
class RateLimiter:
    """Counts events per key in a sliding time window."""

    def __init__(self):
        self._events: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        """Record one event for `key` if it's under the limit. True if allowed."""
        now = time.monotonic()
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and events[0] <= now - window_seconds:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


limiter = RateLimiter()

HOUR = 60 * 60
DAY = 24 * HOUR


def client_ip(request: Request) -> str:
    """
    The visitor's IP address. Behind Render's proxy the real address is in
    X-Forwarded-For. We take the LAST entry: that's the one Render's proxy
    added. Earlier entries are whatever the client claimed and can be faked.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def check_upload_allowed(request: Request) -> None:
    if not limiter.allow(f"upload:{client_ip(request)}", settings.uploads_per_ip_per_hour, HOUR):
        raise HTTPException(status_code=429, detail="Too many uploads from your network. Please try again later.")


def check_analysis_allowed(request: Request, session_id: str) -> None:
    """Three limits, checked from narrowest to widest."""
    if not limiter.allow(f"analyse-session:{session_id}", settings.analyses_per_session, DAY):
        raise HTTPException(
            status_code=429,
            detail=f"You've analysed this resume {settings.analyses_per_session} times. "
            "Upload it again to start a new session.",
        )
    if not limiter.allow(f"analyse-ip:{client_ip(request)}", settings.analyses_per_ip_per_hour, HOUR):
        raise HTTPException(status_code=429, detail="Too many analyses from your network. Please try again in an hour.")
    if not limiter.allow("analyse-site", settings.analyses_per_day, DAY):
        raise HTTPException(
            status_code=429,
            detail="The site has reached its daily AI limit. Please try again tomorrow.",
        )
