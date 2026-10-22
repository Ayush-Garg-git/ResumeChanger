"""
Checks that an uploaded file is a genuine, safe DOCX before we keep it.

A .docx file is really a ZIP archive containing XML files. So "is this a
real DOCX?" means: is it a valid ZIP, and does it contain the part every
Word document has (word/document.xml)?

We never trust the file name or the browser's "content type" on their own:
both are easy to fake. Each check raises UploadError with a message that
is safe to show to the user.
"""

import io
import re
import zipfile

from app.config import settings


class UploadError(Exception):
    """A problem with the uploaded file, explained in plain language."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# A DOCX this large once unzipped is not a resume. This blocks "zip bombs":
# tiny files that expand to gigabytes and crash the server.
MAX_UNZIPPED_BYTES = 20 * 1024 * 1024

# The main text part of a real resume is well under 1 MB, with at most a
# few hundred paragraphs. A tiny file with 100,000 empty paragraphs would
# keep the server busy for minutes, so we refuse it before reading it.
MAX_DOCUMENT_XML_BYTES = 5 * 1024 * 1024
MAX_PARAGRAPHS = 5000
_PARAGRAPH_TAG = re.compile(rb"<(?:[A-Za-z_][\w.-]*:)?p[\s/>]")


def validate_docx_upload(filename: str | None, data: bytes) -> None:
    # 1. Name: only .docx. Older .doc files use a completely different format
    #    that python-docx cannot read.
    name = (filename or "").lower()
    if name.endswith(".doc"):
        raise UploadError(
            "This is an older .doc file. Please open it in Word and save it as .docx."
        )
    if name.endswith(".docm"):
        raise UploadError("Word files with macros (.docm) are not accepted.")
    if not name.endswith(".docx"):
        raise UploadError("Please upload a Word document (.docx).")

    # 2. Size
    if len(data) == 0:
        raise UploadError("The file is empty.")
    if len(data) > settings.max_upload_bytes:
        limit_mb = settings.max_upload_bytes // (1024 * 1024)
        raise UploadError(f"The file is larger than {limit_mb} MB.", status_code=413)

    # 3. Contents: must be a ZIP with Word's main document inside.
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise UploadError(
            "This file is not a valid Word document. It may be damaged or renamed."
        )

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())

        if "word/document.xml" not in names:
            raise UploadError("This file is not a valid Word document.")

        # Macros live in vbaProject.bin. A real .docx should never have one.
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            raise UploadError("Word files with macros are not accepted.")

        unzipped_size = sum(info.file_size for info in archive.infolist())
        if unzipped_size > MAX_UNZIPPED_BYTES:
            raise UploadError("This document is too large to process.")

        if archive.getinfo("word/document.xml").file_size > MAX_DOCUMENT_XML_BYTES:
            raise UploadError("This document is too long to be a resume.")
        body = archive.read("word/document.xml")
        # Count paragraph tags without parsing the XML: <w:p>, <w:p ...> and
        # the short empty form <w:p/>, whatever the namespace prefix. (Not
        # <w:pPr> or <w:pStyle>: the character after "p" must end the name.)
        paragraphs = len(_PARAGRAPH_TAG.findall(body))
        if paragraphs > MAX_PARAGRAPHS:
            raise UploadError("This document is too long to be a resume.")
