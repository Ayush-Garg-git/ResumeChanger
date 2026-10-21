"""
Entry point of the backend.

Run it with:
    uvicorn app.main:app --reload

`app.main:app` means: in the package `app`, file `main.py`, use the
variable called `app`.
"""

from fastapi import FastAPI

from app.config import settings

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
