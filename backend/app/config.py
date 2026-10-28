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
    api_version: str = "0.12.0"

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
    # Keys come ONLY from the environment: backend/.env locally, Render's
    # Environment settings in production. Never commit them.

    # Which AI to use: "claude", "gemini", or "auto" (the default). Auto
    # uses Claude when ANTHROPIC_API_KEY is set, otherwise Gemini, so the
    # site switches to Claude as soon as the key is added on Render.
    ai_provider_setting: str = os.getenv("AI_PROVIDER", "auto").strip().lower()

    # --- Claude (Anthropic API, paid from the company's API credits) ---
    # Variable names start with CLAUDE_API_ on purpose: Claude's own tools
    # set variables like CLAUDE_EFFORT on developer machines, and those
    # must never change how this app behaves.
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    # Haiku is the fastest and cheapest Claude model; ample for this task.
    claude_model: str = os.getenv("CLAUDE_API_MODEL", "claude-haiku-5-5")
    # Tried if the main model is overloaded or over its rate limit.
    # Set to "" to switch off.
    claude_fallback_model: str = os.getenv("CLAUDE_API_FALLBACK_MODEL", "claude-sonnet-5-5")
    # How much work Claude puts in (low, medium, high). Low is fastest and
    # cheapest; our own checks catch the mistakes that matter.
    claude_effort: str = os.getenv("CLAUDE_API_EFFORT", "low").strip().lower()
    # A ceiling on one answer, thinking included. Billing is for tokens
    # actually used, so a generous ceiling costs nothing extra.
    claude_max_tokens: int = int(os.getenv("CLAUDE_API_MAX_TOKENS", "16000"))
    # Only "true" if the company opts in to letting Anthropic train on API
    # data. Shown in the privacy notice, so it must match the real terms.
    claude_trains_on_data: bool = os.getenv("CLAUDE_API_TRAINS_ON_DATA", "false").strip().lower() == "true"

    @property
    def ai_provider(self) -> str:
        if self.ai_provider_setting in ("claude", "gemini"):
            return self.ai_provider_setting
        return "claude" if self.anthropic_api_key else "gemini"

    def problems(self) -> list[str]:
        """Settings that look wrong, logged once at startup."""
        found = []
        if self.ai_provider_setting not in ("auto", "claude", "gemini"):
            found.append(f"AI_PROVIDER={self.ai_provider_setting!r} is not auto, claude or gemini; using auto.")
        if self.claude_effort not in ("low", "medium", "high", "xhigh", "max"):
            found.append(f"CLAUDE_API_EFFORT={self.claude_effort!r} is not a valid effort level.")
        if os.getenv("ANTHROPIC_CUSTOM_HEADERS"):
            found.append("ANTHROPIC_CUSTOM_HEADERS is set; the Anthropic library adds those headers to every request.")
        return found

    # --- Gemini (Google) ---
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")

    # Best model on the Gemini free tier as of October 2026. Change it here
    # (or in .env) to upgrade, e.g. to a paid model, without code changes.
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

    # Used automatically when the main model is busy or over its free
    # limit. Google runs each model separately, so a lighter model often
    # has room when the main one doesn't. Set to "" to switch this off.
    gemini_fallback_model: str = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")

    # Shown to users before they send anything, so it must be accurate.
    # Set to "paid" once you use a paid Gemini key.
    gemini_tier: str = os.getenv("GEMINI_TIER", "free")

    # Seconds the AI library waits for one attempt.
    ai_timeout_seconds: float = float(os.getenv("AI_TIMEOUT_SECONDS", "75"))

    # Our own hard limit for one AI request, retries included. After this,
    # the user gets a clear message instead of an endless wait.
    ai_total_seconds: float = float(os.getenv("AI_TOTAL_SECONDS", "100"))

    # How much Gemini "thinks" first: low, medium or high. Low is much faster.
    gemini_thinking_level: str = os.getenv("GEMINI_THINKING_LEVEL", "low")

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
