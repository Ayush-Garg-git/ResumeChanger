"""
Application settings.

All settings come from environment variables so that secrets (like the
LLM API key we add in Milestone 3) never get written into the code.

For local development, put them in a file called `.env` inside the
`backend/` folder. That file is listed in .gitignore, so it is never
committed. `.env.example` shows which variables exist.
"""

import os

from dotenv import load_dotenv

# Read backend/.env if it exists. Real environment variables win over the file.
load_dotenv()


class Settings:
    # Name shown in API docs and the health response.
    app_name: str = "AI Resume Refiner"

    # Version of the API. Bump it when the API changes in a way the
    # frontend needs to know about.
    api_version: str = "0.1.0"

    # Where the frontend runs in development. Only used if someone calls the
    # backend directly from the browser instead of through the Vite proxy.
    frontend_origin: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")

    # Folder containing the built frontend (output of `npm run build`).
    # Set only in production (see Dockerfile). Empty in development.
    static_dir: str = os.getenv("STATIC_DIR", "")


settings = Settings()
