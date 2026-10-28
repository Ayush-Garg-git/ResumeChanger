"""
Tests for the Claude (Anthropic API) provider.

Most tests here use the REAL Anthropic library, pointed at a small local
stand-in for Anthropic's server. That checks what we actually send (model,
schema, effort, no hidden retries) and how real error responses are
handled, without a key, credits or network access.
"""

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from tests.helpers import ai_post

from app import analyzer
from app.analyzer import AIError, claude_schema, provider_info
from app.answers import AnswerPlacements
from app.config import settings
from app.main import app
from app.schemas import ResumeAnalysis

client = TestClient(app)

JOB = (
    "Data Analyst at Example Corp. You will build Power BI dashboards, write SQL, "
    "and present weekly sales insights to regional managers. Tableau is a plus."
)
P10 = "Built a Power BI dashboard used by regional managers to track weekly sales."
GOOD_ANSWER = {
    "role_title": "Data Analyst",
    "fit_summary": "Good match on Power BI and SQL.",
    "requirements": [
        {"requirement": "Power BI", "importance": "must_have", "evidence": ["p10"], "coverage": "clear"},
    ],
    "suggestions": [
        {"paragraph_id": "p10", "kind": "reword", "original_text": "Built a Power BI dashboard",
         "proposed_text": "Built a Power BI dashboard that regional managers used to track weekly sales.",
         "reason": "Puts the users first.", "requirement": "Power BI"},
    ],
    "questions_for_user": ["Have you used Tableau?"],
    "no_changes_needed": False,
}


# --------------------------------------------------------------------------
# A local stand-in for Anthropic's API
# --------------------------------------------------------------------------
def _message(text, stop_reason="end_turn", model="claude-haiku-5-5"):
    return 200, {
        "id": "msg_test", "type": "message", "role": "assistant", "model": model,
        # Claude may think first; our code must pick the text block.
        "content": [
            {"type": "thinking", "thinking": "Checking the rules.", "signature": "sig"},
            {"type": "text", "text": text},
        ],
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 1200, "output_tokens": 300},
    }


def _error(status, kind, message, details=None):
    body = {"type": "error", "error": {"type": kind, "message": message}}
    if details:
        body["error"]["details"] = details
    return status, body


class FakeAnthropic:
    """Answers each POST with the next reply in `replies` and records requests."""

    def __init__(self):
        self.replies = []
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                fake.requests.append({"path": self.path, "body": body, "headers": {k.lower(): v for k, v in self.headers.items()}})
                status, reply = fake.replies.pop(0) if fake.replies else _message(json.dumps(GOOD_ANSWER))
                if status == "sleep":  # ("sleep", seconds): answer too late
                    time.sleep(reply)
                    status, reply = _message(json.dumps(GOOD_ANSWER))
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def models_used(self):
        return [r["body"]["model"] for r in self.requests]


@pytest.fixture
def claude(monkeypatch):
    """Switch the app to Claude, talking to the local stand-in."""
    import anthropic
    fake = FakeAnthropic()
    real_client = anthropic.Anthropic

    def local_client(**kwargs):
        fake.client_kwargs = dict(kwargs)
        kwargs["base_url"] = fake.url  # the only change: our stand-in instead of Anthropic
        return real_client(**kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", local_client)
    monkeypatch.setattr(settings, "anthropic_api_key", "test-claude-key")
    monkeypatch.setattr(settings, "claude_model", "claude-haiku-5-5")
    monkeypatch.setattr(settings, "claude_fallback_model", "claude-sonnet-5-5")
    monkeypatch.setattr(analyzer, "RETRY_DELAYS", (0, 0))
    yield fake
    fake.server.shutdown()


def _upload(sample_resume):
    files = {"file": ("resume.docx", sample_resume.read_bytes(), "application/octet-stream")}
    return client.post("/api/resume", files=files).json()["session_id"]


def _analyse(sample_resume):
    sid = _upload(sample_resume)
    return ai_post(client, f"/api/resume/{sid}/analyze", json={"job_description": JOB, "consent": True})


# --------------------------------------------------------------------------
# Choosing the provider
# --------------------------------------------------------------------------
def test_gemini_is_used_when_there_is_no_claude_key(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "g")
    assert settings.ai_provider == "gemini"


def test_adding_a_claude_key_switches_to_claude(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "k")
    assert settings.ai_provider == "claude"


def test_ai_provider_setting_overrides_the_automatic_choice(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "k")
    monkeypatch.setattr(settings, "ai_provider_setting", "gemini")
    assert settings.ai_provider == "gemini"


def test_health_describes_claude_for_the_privacy_notice(claude):
    ai = client.get("/api/health").json()["ai"]
    assert ai == {
        "configured": True,
        "provider": "Anthropic Claude",
        "model": "claude-haiku-5-5",
        "fallback_model": "claude-sonnet-5-5",
        "trains_on_data": False,
    }


def test_health_says_the_gemini_free_tier_may_train_on_data(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "g")
    monkeypatch.setattr(settings, "gemini_tier", "free")
    ai = client.get("/api/health").json()["ai"]
    assert ai["provider"] == "Google Gemini" and ai["trains_on_data"] is True


def test_missing_claude_key_names_the_right_variable(monkeypatch, sample_resume):
    monkeypatch.setattr(settings, "ai_provider_setting", "claude")
    response = _analyse(sample_resume)
    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


# --------------------------------------------------------------------------
# The schema Claude receives
# --------------------------------------------------------------------------
UNSUPPORTED = {"maxItems", "minLength", "maxLength", "minimum", "maximum", "multipleOf"}


def _check(node, path="$", unions=None):
    """Walk a schema; field-name maps (properties, $defs) are stepped through."""
    unions = [] if unions is None else unions
    if isinstance(node, list):
        for i, item in enumerate(node):
            _check(item, f"{path}[{i}]", unions)
        return unions
    if not isinstance(node, dict):
        return unions
    bad = UNSUPPORTED & set(node)
    assert not bad, f"{path} still has {bad}"
    if node.get("type") == "object":
        assert node.get("additionalProperties") is False, path
        # Every field required: Claude allows only 24 optional ones, and
        # we want every field anyway.
        assert set(node.get("required", [])) == set(node.get("properties", {})), path
    if "anyOf" in node or isinstance(node.get("type"), list):
        unions.append(path)
    for key, value in node.items():
        if key in ("properties", "$defs"):
            for name, sub in value.items():
                _check(sub, f"{path}.{name}", unions)
        else:
            _check(value, f"{path}.{key}", unions)
    return unions


@pytest.mark.parametrize("shape", [ResumeAnalysis, AnswerPlacements])
def test_schema_has_nothing_claude_rejects(shape):
    unions = _check(claude_schema(shape.model_json_schema()))
    assert len(unions) <= 16  # Claude's limit on union types


def test_schema_keeps_field_names_and_descriptions():
    original = ResumeAnalysis.model_json_schema()
    converted = claude_schema(original)
    assert set(converted["properties"]) == set(original["properties"])
    assert "at most" in converted["properties"]["suggestions"]["description"]
    # A field that happens to be called "title" must survive (only the
    # schema keyword "title" is dropped).
    weird = {"type": "object", "title": "X", "properties": {"title": {"type": "string", "title": "T"}},
             "required": ["title"]}
    out = claude_schema(weird)
    assert out["properties"] == {"title": {"type": "string"}} and "title" not in {k for k in out if k != "properties"}


# --------------------------------------------------------------------------
# What we send, through the real Anthropic library
# --------------------------------------------------------------------------
def test_full_analysis_through_claude(claude, sample_resume):
    response = _analyse(sample_resume)
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["model"] == "claude-haiku-5-5"
    assert len(body["suggestions"]) == 1
    assert body["suggestions"][0]["original_text"] == P10  # real full line shown

    assert len(claude.requests) == 1
    sent = claude.requests[0]
    assert sent["path"] == "/v1/messages"
    # The Anthropic library (2.x) sends the key as a Bearer token.
    assert sent["headers"]["authorization"] == "Bearer test-claude-key"
    request = sent["body"]
    assert request["model"] == "claude-haiku-5-5"
    assert request["max_tokens"] == settings.claude_max_tokens
    assert request["output_config"]["effort"] == "low"
    fmt = request["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    # Privacy rules apply to Claude exactly as to Gemini.
    prompt = request["messages"][0]["content"]
    assert "Riya" not in prompt and "@" not in prompt


def test_library_retries_are_switched_off(claude, sample_resume):
    claude.replies = [_error(529, "overloaded_error", "Overloaded")] * 3
    _analyse(sample_resume)
    assert claude.client_kwargs["max_retries"] == 0
    # Always Anthropic's real address, never a machine-wide override.
    assert claude.client_kwargs["base_url"] == "https://api.anthropic.com"
    assert claude.client_kwargs["timeout"] == settings.ai_timeout_seconds
    assert len(claude.requests) == 3  # ours only: first try + two retries


def test_overloaded_main_model_switches_to_the_backup(claude, sample_resume):
    claude.replies = [_error(529, "overloaded_error", "Overloaded")]
    response = _analyse(sample_resume)
    assert response.status_code == 200
    assert claude.models_used() == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert response.json()["model"] == "claude-sonnet-5-5"


def test_rate_limited_main_model_switches_to_the_backup(claude, sample_resume):
    claude.replies = [_error(429, "rate_limit_error", "Number of request tokens has exceeded your rate limit")]
    response = _analyse(sample_resume)
    assert response.status_code == 200
    assert claude.models_used() == ["claude-haiku-5-5", "claude-sonnet-5-5"]


def test_still_overloaded_says_claude_is_busy(claude, sample_resume):
    claude.replies = [_error(529, "overloaded_error", "Overloaded")] * 3
    response = _analyse(sample_resume)
    assert response.status_code == 503
    assert "Anthropic Claude is busy" in response.json()["detail"]


# Every way Anthropic says "no money left". None is retried or sent to the
# backup model (same account), and the user gets one clear message.
OUT_OF_CREDIT = [
    _error(400, "invalid_request_error",
           "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."),
    _error(400, "invalid_request_error",
           "You have reached your specified workspace API usage limits. You will regain access on 2026-12-01 at 00:00 UTC."),
    _error(400, "invalid_request_error",
           "You have reached your specified API usage limits. You will regain access on 2026-12-01 at 00:00 UTC."),
    _error(429, "rate_limit_error",
           "You have reached your API usage limits: your organization has crossed its monthly API usage threshold.",
           details={"error_code": "enforced_spend_limit_reached"}),
]


@pytest.mark.parametrize("reply", OUT_OF_CREDIT, ids=["credit", "workspace-limit", "org-limit", "tier-cap"])
def test_out_of_credit_is_clear_and_not_retried(claude, sample_resume, reply, caplog):
    claude.replies = [reply]
    response = _analyse(sample_resume)
    assert response.status_code == 503
    assert "used up its AI allowance" in response.json()["detail"]
    assert len(claude.requests) == 1
    assert "OUT OF CREDIT" in caplog.text  # so the owner spots it in Render logs


def test_bad_key_names_the_right_variable(claude, sample_resume):
    claude.replies = [_error(401, "authentication_error", "invalid x-api-key")]
    response = _analyse(sample_resume)
    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]
    assert len(claude.requests) == 1


def test_a_refusal_is_reported_and_not_retried(claude, sample_resume):
    claude.replies = [_message("I can't help with that.", stop_reason="refusal")]
    response = _analyse(sample_resume)
    assert response.status_code == 502
    assert "declined" in response.json()["detail"]
    assert len(claude.requests) == 1


def test_a_cut_off_answer_is_reported(claude, sample_resume):
    claude.replies = [_message('{"role_title": "Data', stop_reason="max_tokens")]
    response = _analyse(sample_resume)
    assert response.status_code == 502
    assert "too long" in response.json()["detail"]


def test_answers_step_also_works_on_claude(claude, sample_resume):
    sid = _upload(sample_resume)
    analysis = ai_post(client, f"/api/resume/{sid}/analyze",
                       json={"job_description": JOB, "consent": True}).json()
    claude.replies = [_message(json.dumps({
        "placements": [{"question_id": "q1", "paragraph_id": "p7",
                        "original_text": "Tools: Excel, Power BI, Git",
                        "proposed_text": "Tools: Excel, Power BI, Tableau, Git",
                        "reason": "Your tools line."}],
        "not_placed": [],
    }))]
    response = ai_post(client, f"/api/resume/{sid}/answers", json={
        "analysis_id": analysis["analysis_id"], "consent": True,
        "answers": [{"question_id": "q1", "has_it": True, "details": "Built two sales dashboards in Tableau"}],
    })
    assert response.status_code == 200, response.json()
    assert any(s.get("from_answer") for s in response.json()["suggestions"])
    placement_schema = claude.requests[-1]["body"]["output_config"]["format"]["schema"]
    assert "placements" in placement_schema["properties"]


# --------------------------------------------------------------------------
# Review follow-ups: more failure modes
# --------------------------------------------------------------------------
def test_an_answer_with_no_text_is_reported(claude, sample_resume):
    claude.replies = [(200, {
        "id": "m", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
        "content": [{"type": "thinking", "thinking": "hmm", "signature": "s"}],
        "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
    })]
    response = _analyse(sample_resume)
    assert response.status_code == 502 and "empty" in response.json()["detail"]
    assert len(claude.requests) == 1


def test_a_slow_answer_times_out_cleanly(claude, sample_resume, monkeypatch):
    monkeypatch.setattr(settings, "ai_timeout_seconds", 0.5)
    claude.replies = [("sleep", 2.0)]
    started = time.monotonic()
    response = _analyse(sample_resume)
    assert response.status_code == 504 and "too long" in response.json()["detail"]
    assert time.monotonic() - started < 5


def test_unreachable_api_is_retried_then_reported(sample_resume, monkeypatch):
    import anthropic
    attempts = []
    real_client = anthropic.Anthropic

    def nowhere(**kwargs):
        attempts.append(kwargs)
        kwargs["base_url"] = "http://127.0.0.1:9"  # nothing listens here
        return real_client(**kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", nowhere)
    monkeypatch.setattr(settings, "anthropic_api_key", "k")
    monkeypatch.setattr(analyzer, "RETRY_DELAYS", (0, 0))
    response = _analyse(sample_resume)
    assert response.status_code == 503 and "busy" in response.json()["detail"]
    assert len(attempts) == 3


def test_rate_limit_without_a_backup_says_limit_not_out_of_credit(claude, sample_resume, monkeypatch):
    monkeypatch.setattr(settings, "claude_fallback_model", "")
    claude.replies = [_error(429, "rate_limit_error", "Number of request tokens has exceeded your rate limit")]
    response = _analyse(sample_resume)
    assert response.status_code == 429
    assert "usage limit" in response.json()["detail"] and "allowance" not in response.json()["detail"]
    assert len(claude.requests) == 1


def test_library_debug_logs_never_contain_resume_text(claude, sample_resume, caplog):
    # As if ANTHROPIC_LOG=debug were set on the server.
    logging.getLogger("anthropic").setLevel(logging.DEBUG)
    logging.getLogger("httpx").setLevel(logging.DEBUG)
    with caplog.at_level(logging.DEBUG):
        response = _analyse(sample_resume)
    assert response.status_code == 200
    assert "regional managers" not in caplog.text
    assert "Power BI" not in caplog.text


def test_map_fields_are_refused_not_silently_broken():
    with pytest.raises(ValueError):
        claude_schema({"type": "object", "additionalProperties": {"type": "string"}})
    with pytest.raises(ValueError):
        claude_schema({"type": "object"})


def test_unknown_provider_setting_falls_back_to_auto_with_a_warning(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider_setting", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "k")
    assert settings.ai_provider == "claude"
    assert any("AI_PROVIDER" in p for p in settings.problems())


def test_bad_effort_and_custom_headers_are_flagged(monkeypatch):
    monkeypatch.setattr(settings, "claude_effort", "fast")
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "X-Test: 1")
    problems = " ".join(settings.problems())
    assert "CLAUDE_API_EFFORT" in problems and "ANTHROPIC_CUSTOM_HEADERS" in problems


def test_opting_in_to_training_changes_the_notice(claude, monkeypatch):
    monkeypatch.setattr(settings, "claude_trains_on_data", True)
    assert client.get("/api/health").json()["ai"]["trains_on_data"] is True


# --- Consent is for the provider named in the notice ------------------------

def test_consent_for_another_provider_is_refused(claude, sample_resume):
    sid = _upload(sample_resume)
    response = client.post(f"/api/resume/{sid}/analyze",
                           json={"job_description": JOB, "consent": True, "provider": "Google Gemini"})
    assert response.status_code == 409
    assert "reload the page" in response.json()["detail"]
    assert claude.requests == []  # nothing was sent


def test_consent_for_the_current_provider_is_accepted(claude, sample_resume):
    sid = _upload(sample_resume)
    response = ai_post(client, f"/api/resume/{sid}/analyze",
                       json={"job_description": JOB, "consent": True, "provider": "Anthropic Claude"})
    assert response.status_code == 200


def test_answers_with_consent_for_another_provider_are_refused(claude, sample_resume):
    sid = _upload(sample_resume)
    analysis = ai_post(client, f"/api/resume/{sid}/analyze",
                       json={"job_description": JOB, "consent": True}).json()
    sent_before = len(claude.requests)
    response = client.post(f"/api/resume/{sid}/answers", json={
        "analysis_id": analysis["analysis_id"], "consent": True, "provider": "Google Gemini",
        "answers": [{"question_id": "q1", "has_it": True, "details": "Built two dashboards in Tableau"}],
    })
    assert response.status_code == 409
    assert len(claude.requests) == sent_before
