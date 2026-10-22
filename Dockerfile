# Production image: one container that serves both the API and the frontend.
#
# A Dockerfile is a recipe for building a self-contained box with everything
# the app needs. Any hosting service that runs Docker (Render, Railway,
# Fly.io, a VPS) can run this same box, so we aren't tied to one provider.
#
# It has two stages. Stage 1 uses Node to build the React app into static
# files. Stage 2 is a small Python image that copies in only those built
# files plus the backend. Node and node_modules are left behind in stage 1,
# which keeps the final image small.

# ---------- Stage 1: build the frontend ----------
FROM node:22-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: run the backend ----------
# Pinned to a named Debian release (trixie) so the system packages below
# can't change under us when the default base image moves on.
FROM python:3.12-slim-trixie
WORKDIR /app

# Don't write .pyc files; send logs straight to the console.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STATIC_DIR=/app/static

# For the before/after preview and page count (Milestone 6):
# - libreoffice-writer-nogui: lays out DOCX pages and converts them to PDF,
#   without any of the desktop/window parts
# - poppler-utils: pdfinfo (page count) and pdftoppm (page images)
# - fonts with the same letter widths as common Word fonts, so page counts
#   come out close to Word's:
#     Carlito = Calibri, Caladea = Cambria,
#     Liberation = Arial, Times New Roman, Courier New
# This adds a few hundred MB to the image. It's installed before the app
# code is copied, so Docker can reuse this layer when only code changes.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui \
        poppler-utils \
        fonts-crosextra-carlito \
        fonts-crosextra-caladea \
        fonts-liberation \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend /frontend/dist ./static

# Run as a normal user, not root: limits damage if something goes wrong.
RUN useradd --create-home appuser
USER appuser

# Hosting services tell the app which port to use through $PORT.
EXPOSE 8000
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
