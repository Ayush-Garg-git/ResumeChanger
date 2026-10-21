"""Tests for the upload and delete routes, and for file checks."""

import io
import os
import time
import zipfile

from fastapi.testclient import TestClient

from app import storage
from app.main import app

client = TestClient(app)

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _upload(name, data):
    return client.post("/api/resume", files={"file": (name, data, DOCX_TYPE)})


# --- Happy path -------------------------------------------------------------

def test_valid_resume_is_accepted_and_outlined(sample_resume):
    response = _upload("resume.docx", sample_resume.read_bytes())

    assert response.status_code == 200
    body = response.json()
    assert len(body["session_id"]) == 32
    assert "Skills" in body["sections"]
    assert body["paragraphs"][0]["text"] == "Riya Mehta"


def test_stored_original_is_byte_for_byte_identical(sample_resume):
    data = sample_resume.read_bytes()
    session_id = _upload("resume.docx", data).json()["session_id"]

    assert storage.original_path(session_id).read_bytes() == data


def test_uppercase_extension_is_fine(sample_resume):
    assert _upload("RESUME.DOCX", sample_resume.read_bytes()).status_code == 200


# --- Rejections -------------------------------------------------------------

def test_wrong_extension_is_rejected():
    response = _upload("resume.pdf", b"%PDF-1.4 fake")
    assert response.status_code == 400
    assert ".docx" in response.json()["detail"]


def test_old_doc_format_gets_a_helpful_message():
    response = _upload("resume.doc", b"old word file")
    assert response.status_code == 400
    assert "save it as .docx" in response.json()["detail"]


def test_renamed_file_is_rejected():
    response = _upload("resume.docx", b"this is really a text file")
    assert response.status_code == 400
    assert "not a valid Word document" in response.json()["detail"]


def test_zip_without_word_content_is_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("hello.txt", "not word")
    response = _upload("resume.docx", buffer.getvalue())
    assert response.status_code == 400


def test_docx_with_macros_is_rejected(sample_resume):
    buffer = io.BytesIO(sample_resume.read_bytes())
    with zipfile.ZipFile(buffer, "a") as z:
        z.writestr("word/vbaProject.bin", b"fake macro")
    response = _upload("resume.docx", buffer.getvalue())
    assert response.status_code == 400
    assert "macros" in response.json()["detail"]


def test_empty_file_is_rejected():
    assert _upload("resume.docx", b"").status_code == 400


def test_too_large_file_is_rejected(monkeypatch, sample_resume):
    from app.config import settings
    monkeypatch.setattr(settings, "max_upload_bytes", 1000)
    response = _upload("resume.docx", sample_resume.read_bytes())
    assert response.status_code == 413


def test_rejected_files_are_not_stored(temp_upload_dir):
    _upload("resume.docx", b"not a docx")
    assert not temp_upload_dir.exists() or not any(temp_upload_dir.iterdir())


# --- Deleting and expiry ----------------------------------------------------

def test_delete_removes_the_file(sample_resume):
    session_id = _upload("resume.docx", sample_resume.read_bytes()).json()["session_id"]

    assert client.delete(f"/api/resume/{session_id}").status_code == 204
    assert storage.original_path(session_id) is None
    assert client.delete(f"/api/resume/{session_id}").status_code == 404


def test_delete_rejects_odd_ids():
    assert client.delete("/api/resume/not-a-real-id").status_code == 400
    assert client.delete("/api/resume/..%2F..%2Fetc").status_code in (400, 404)


def test_old_sessions_are_cleaned_up(sample_resume, temp_upload_dir):
    old_id = _upload("resume.docx", sample_resume.read_bytes()).json()["session_id"]

    # Pretend this session was created two hours ago.
    two_hours_ago = time.time() - 2 * 60 * 60
    os.utime(temp_upload_dir / old_id, (two_hours_ago, two_hours_ago))

    # The next upload triggers cleanup.
    new_id = _upload("resume.docx", sample_resume.read_bytes()).json()["session_id"]

    assert storage.original_path(old_id) is None
    assert storage.original_path(new_id) is not None
