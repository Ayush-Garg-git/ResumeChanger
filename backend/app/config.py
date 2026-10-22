"""
Application settings.

All settings come from environment variables so that secrets (like the
LLM API key we add in Milestone 3) never get written into the code.

For local development, put them in a file called `.env` inside the
`backend/` folder. That file is listed in .gitignore, so it is never
committed. `.env.example` shows which variables exist.
"""

import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

# Read backend/.env if it exists. Real environment variables win over the file.
load_dotenv()


class Settings:
    # Name shown in API docs and the health response.
    app_name: str = "AI Resume Refiner"

    # Version of the API. Bump it when the API changes in a way the
    # frontend needs to know about.
    api_version: str = "0.8.0"

    # Where the frontend runs in development. Only used if someone calls the
    # backend directly from the browser instead of through the Vite proxy.
    frontend_origin: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")

    # Folder containing the built frontend (output of `npm run build`).
    # Set only in production (see Dockerfile). Empty in development.
    static_dir: str = os.getenv("STATIC_DIR", "")

    # --- Uploads (Milestone 2) ---
    # Where uploaded resumes are kept while someone is working on them.
    # Defaults to the operating system's temp folder, which is the right
    # place for short-lived files.
    upload_dir: Path = Path(
        os.getenv("UPLOAD_DIR", Path(tempfile.gettempdir()) / "resume-refiner")
    )

    # Largest file we accept. Real resumes are usually well under 1 MB.
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_MB", "5")) * 1024 * 1024

    # How long an uploaded resume is kept before automatic deletion.
    session_ttl_minutes: int = int(os.getenv("SESSION_TTL_MINUTES", "60"))

    # --- AI analysis (Milestone 3) ---
    # The key comes ONLY from the environment: backend/.env locally,
    # Render's Environment settings in production. Never commit it.
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")

    # Best model on the Gemini free tier as of October 2026. Change it here
    # (or in .env) to upgrade, e.g. to a paid model, without code changes.
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

    # Shown to users before they send anything, so it must be accurate.
    # Set to "paid" once you use a paid Gemini key.
    gemini_tier: str = os.getenv("GEMINI_TIER", "free")

    # Seconds to wait for the AI before giving up.
    ai_timeout_seconds: float = float(os.getenv("AI_TIMEOUT_SECONDS", "90"))

    # Job description length limits (characters).
    job_description_min_chars: int = 80
    job_description_max_chars: int = 15000

    # --- Protection for a small public server (Milestone 7) ---
    # Rate limits. The defaults suit a free AI key; raise them for a paid one.
    uploads_per_ip_per_hour: int = int(os.getenv("UPLOADS_PER_IP_PER_HOUR", "30"))
    analyses_per_session: int = int(os.getenv("ANALYSES_PER_SESSION", "5"))
    analyses_per_ip_per_hour: int = int(os.getenv("ANALYSES_PER_IP_PER_HOUR", "10"))
    analyses_per_day: int = int(os.getenv("ANALYSES_PER_DAY", "200"))

    # How often the background job deletes expired sessions (seconds).
    cleanup_interval_seconds: int = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "300"))

    # The interactive API pages (/docs). Useful while developing; switched
    # off by default in production, where STATIC_DIR is set.
    enable_api_docs: bool = os.getenv(
        "ENABLE_API_DOCS", "false" if os.getenv("STATIC_DIR") else "true"
    ).lower() == "true"


settings = Settings()
