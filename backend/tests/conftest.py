"""
Shared test setup. pytest loads this file automatically.

`fixtures` are small helpers that tests ask for by name in their arguments.
"""

import pytest

from app.config import settings
from samples.make_sample_resume import (
    build_sample_resume,
    build_two_column_header_resume,
)


@pytest.fixture(autouse=True)
def temp_upload_dir(tmp_path, monkeypatch):
    """
    Every test gets its own empty upload folder, so tests never touch real
    files and never affect each other. `autouse` applies it to all tests.
    """
    folder = tmp_path / "uploads"
    monkeypatch.setattr(settings, "upload_dir", folder)
    return folder


@pytest.fixture(autouse=True)
def gemini_unless_a_test_says_otherwise(monkeypatch):
    """
    A developer's backend/.env may hold a real ANTHROPIC_API_KEY. Tests must
    never use it (or the network), so every test starts on the Gemini path
    with no Claude key. Claude tests switch provider explicitly.
    """
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "ai_provider_setting", "auto")
    # The shipped defaults, whatever this machine's environment says.
    monkeypatch.setattr(settings, "claude_model", "claude-haiku-5-5")
    monkeypatch.setattr(settings, "claude_fallback_model", "claude-sonnet-5-5")
    monkeypatch.setattr(settings, "claude_effort", "low")
    monkeypatch.setattr(settings, "claude_max_tokens", 16000)
    monkeypatch.setattr(settings, "claude_trains_on_data", False)
    for name in ("ANTHROPIC_LOG", "ANTHROPIC_CUSTOM_HEADERS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """Each test starts with empty rate-limit counters."""
    from app.limits import limiter
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def sample_resume(tmp_path):
    """A freshly built fake resume (see samples/make_sample_resume.py)."""
    return build_sample_resume(tmp_path / "sample_resume.docx")


@pytest.fixture
def two_column_resume(tmp_path):
    """A fake resume with a two-column header and a heading stored before the name."""
    return build_two_column_header_resume(tmp_path / "two_column.docx")
