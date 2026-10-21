"""
Entry point of the backend.

Run it with:
    uvicorn app.main:app --reload

`app.main:app` means: in the package `app`, file `main.py`, use the
variable called `app`.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

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
