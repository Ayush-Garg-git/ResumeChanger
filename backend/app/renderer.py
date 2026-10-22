"""
Turns a DOCX into page images and a page count, for the preview.

A DOCX file doesn't store how many pages it has: pages only exist once
something lays the text out. We use LibreOffice for that:

    DOCX --(LibreOffice, no window)--> PDF --(pdfinfo)--> page count
                                           --(pdftoppm)--> page-1.png, page-2.png ...

Everything runs on our own server. Nothing is sent anywhere.

Honest limits
-------------
- Word and LibreOffice lay out text slightly differently, especially when
  the resume uses fonts we don't have. The page count is a close estimate,
  and the UI says so.
- LibreOffice needs about 200 MB of memory. Render's free plan has 512 MB,
  so only ONE render runs at a time (the lock below). Others wait.
"""

import os
import shutil
import signal
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

# Render at most this many pages as images. A resume rarely has more,
# and it bounds the work if someone uploads a long document.
MAX_PREVIEW_PAGES = 5

# Image resolution. 70 dots per inch gives about 600 x 780 pixels per page:
# readable on screen, small to send.
PREVIEW_DPI = 70

# LibreOffice on 0.1 CPU can be slow to start the first time.
RENDER_TIMEOUT_SECONDS = 150
LOCK_WAIT_SECONDS = 180

_lock = threading.Lock()

# LibreOffice keeps settings in a "profile" folder. Reusing one profile makes
# later renders faster. It must be somewhere we're allowed to write.
_PROFILE_DIR = Path(tempfile.gettempdir()) / "resume-refiner-lo-profile"


class RenderError(Exception):
    """Preview couldn't be made. The message is for the user."""


@dataclass
class Rendered:
    pages: int
    images: list[Path]


def is_available() -> bool:
    """True if LibreOffice and the PDF tools are installed on this server."""
    return all(shutil.which(tool) for tool in ("soffice", "pdfinfo", "pdftoppm"))


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    """
    Run a program in its own process group. On timeout, stop the whole
    group: `soffice` starts a helper (soffice.bin) that would otherwise
    keep running, holding ~200 MB and blocking later previews.
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _to_pdf(docx: Path, work_dir: Path) -> Path:
    result = _run(
        [
            "soffice",
            f"-env:UserInstallation={_PROFILE_DIR.as_uri()}",
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(work_dir),
            str(docx),
        ],
        timeout=RENDER_TIMEOUT_SECONDS,
    )
    pdf = work_dir / (docx.stem + ".pdf")
    if result.returncode != 0 or not pdf.is_file():
        raise RenderError("The preview couldn't be created for this document.")
    return pdf


def _page_count(pdf: Path) -> int:
    result = _run(["pdfinfo", str(pdf)], timeout=30)
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    raise RenderError("The preview couldn't count the pages.")


def render(docx: Path, out_dir: Path, prefix: str) -> Rendered:
    """
    Render `docx` into `out_dir` as `<prefix>-1.png`, `<prefix>-2.png` ...
    Returns the page count and the image paths.
    """
    if not is_available():
        raise RenderError("Preview isn't available on this server (LibreOffice is not installed).")

    if not _lock.acquire(timeout=LOCK_WAIT_SECONDS):
        raise RenderError("The preview service is busy. Please try again in a minute.")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            # Copy to a neutral name: LibreOffice names the PDF after the file.
            source = work / f"{prefix}.docx"
            shutil.copyfile(docx, source)
            try:
                pdf = _to_pdf(source, work)
                pages = _page_count(pdf)
                _run(
                    [
                        "pdftoppm", "-png", "-r", str(PREVIEW_DPI),
                        "-f", "1", "-l", str(MAX_PREVIEW_PAGES),
                        str(pdf), str(out_dir / prefix),
                    ],
                    timeout=60,
                )
            except subprocess.TimeoutExpired:
                raise RenderError("The preview took too long. Please try again.")

        # pdftoppm pads page numbers when there are 10+ pages (page-01.png),
        # so collect whatever it wrote, in page order.
        images = sorted(
            out_dir.glob(f"{prefix}-*.png"),
            key=lambda p: int(p.stem.rsplit("-", 1)[1]),
        )
        if not images:
            raise RenderError("The preview couldn't draw the pages.")
        return Rendered(pages=pages, images=images)
    finally:
        _lock.release()
