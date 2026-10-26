"""
Tests for Milestone 4b: answering the AI's questions to add confirmed skills.

The AI is replaced with a stand-in. The first call returns an analysis with
questions; the second returns placements for the user's answers. We control
both, including placements that break the rules, to check they're dropped.
"""

import io
import json

import pytest
from docx import Document
from fastapi.testclient import TestClient

from tests.helpers import ai_post

from app import analyzer, storage
from app.config import settings
from app.main import app

client = TestClient(app)

JOB = (
    "Data Analyst. Build Power BI dashboards, write SQL and present weekly sales "
    "insights to regional managers. Tableau and pandas are a plus."
)
P7 = "Tools: Excel, Power BI, Git"
P10 = "Built a Power BI dashboard used by regional managers to track weekly sales."
P13 = "Inventory Forecast: Forecasted monthly stock using past sales in Python."

ANALYSIS = {
    "role_title": "Data Analyst", "fit_summary": "ok", "requirements": [], "no_changes_needed": False,
    "suggestions": [
        {"paragraph_id": "p10", "kind": "reword", "original_text": P10,
         "proposed_text": "Built a Power BI dashboard that regional managers used to track weekly sales.",
         "reason": "r", "requirement": None},
    ],
    "questions_for_user": [
        "The job lists Tableau as a plus. Have you used it?",
        "The job mentions pandas. Have you used it?",
        "Have you designed A/B tests?",
    ],
}


def placement(qid, pid, original, proposed):
    return {"question_id": qid, "paragraph_id": pid, "original_text": original,
            "proposed_text": proposed, "reason": "Fits your tools line."}


class FakeAI:
    """First call: the analysis. Later calls: whatever `placements` holds."""

    def __init__(self):
        self.prompts = []
        self.placements = {"placements": [], "not_placed": []}

    def __call__(self, prompt, schema):
        self.prompts.append(prompt)
        if "suggestions" in schema["properties"]:
            return json.dumps(ANALYSIS)
        return json.dumps(self.placements)


@pytest.fixture
def ai(monkeypatch):
    fake = FakeAI()
    monkeypatch.setattr(analyzer, "call_model", fake)
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    return fake


@pytest.fixture
def sid(sample_resume, ai):
    files = {"file": ("r.docx", sample_resume.read_bytes(), "application/octet-stream")}
    session_id = client.post("/api/resume", files=files).json()["session_id"]
    body = ai_post(client, f"/api/resume/{session_id}/analyze", json={"job_description": JOB, "consent": True}).json()
    assert [q["id"] for q in body["questions"]] == ["q1", "q2", "q3"]
    return session_id


def _aid(session_id):
    return storage.load_json(session_id, "analysis.json")["analysis_id"]


def _answer(session_id, answers, consent=True):
    return ai_post(client, 
        f"/api/resume/{session_id}/answers",
        json={"analysis_id": _aid(session_id), "answers": answers, "consent": consent},
    )


YES_TABLEAU = {"question_id": "q1", "has_it": True,
               "details": "Built two sales dashboards in Tableau during my internship"}
NO_PANDAS = {"question_id": "q2", "has_it": False, "details": ""}


# --- What is sent ------------------------------------------------------------

def test_only_yes_answers_are_sent(sid, ai):
    ai.placements = {"placements": [placement("q1", "p7", P7, "Tools: Excel, Power BI, Tableau, Git")], "not_placed": []}
    _answer(sid, [YES_TABLEAU, NO_PANDAS,
                  {"question_id": "q3", "has_it": False, "details": "I secretly know A/B testing"}])
    prompt = ai.prompts[-1]
    assert "two sales dashboards in Tableau" in prompt
    assert "pandas" not in prompt.split("<confirmed_answers>")[1]
    assert "secretly" not in prompt


def test_answers_are_masked_like_the_resume(sid, ai):
    _answer(sid, [{"question_id": "q1", "has_it": True,
                   "details": "Used Tableau at work, mail me at riya.mehta@example.com"}])
    assert "riya.mehta@example.com" not in ai.prompts[-1]
    assert "Riya" not in ai.prompts[-1]


def test_consent_is_required_every_time(sid, ai):
    response = _answer(sid, [YES_TABLEAU], consent=False)
    assert response.status_code == 400
    assert len(ai.prompts) == 1  # only the original analysis was ever sent


def test_yes_needs_your_own_words(sid, ai):
    response = _answer(sid, [{"question_id": "q1", "has_it": True, "details": "   "}])
    assert response.status_code == 400


def test_all_no_means_nothing_is_sent(sid, ai):
    response = _answer(sid, [NO_PANDAS])
    assert response.status_code == 400
    assert len(ai.prompts) == 1


# --- The checks: only your words get in ------------------------------------

def test_good_placement_becomes_a_card(sid, ai):
    ai.placements = {"placements": [placement("q1", "p7", P7, "Tools: Excel, Power BI, Tableau, Git")], "not_placed": []}
    body = _answer(sid, [YES_TABLEAU]).json()
    added = [s for s in body["suggestions"] if s["id"] in body["added"]]
    assert len(added) == 1
    card = added[0]
    assert card["kind"] == "add_confirmed"
    assert card["proposed_text"] == "Tools: Excel, Power BI, Tableau, Git"
    assert card["from_answer"] == YES_TABLEAU["details"]
    assert card["id"] == "s2"  # after the analysis's own s1


def test_words_not_in_your_answer_are_dropped(sid, ai):
    # You said Tableau. The AI also slipped in "Looker".
    ai.placements = {"placements": [placement("q1", "p7", P7, "Tools: Excel, Power BI, Tableau, Looker, Git")], "not_placed": []}
    body = _answer(sid, [YES_TABLEAU]).json()
    assert body["added"] == []
    assert "looker" in body["dropped"][0]["why"]
    assert body["not_placed"][0]["question_id"] == "q1"  # reported, not lost


def test_numbers_you_didnt_give_are_dropped(sid, ai):
    ai.placements = {"placements": [placement(
        "q1", "p13", P13, "Inventory Forecast: Forecasted monthly stock using past sales in Python, with 12 Tableau dashboards.")],
        "not_placed": []}
    body = _answer(sid, [YES_TABLEAU]).json()
    assert body["added"] == [] and "12" in body["dropped"][0]["why"]


def test_seniority_you_didnt_claim_is_dropped(sid, ai):
    ai.placements = {"placements": [placement(
        "q1", "p10", P10, "Led a Power BI and Tableau dashboard used by regional managers to track weekly sales.")],
        "not_placed": []}
    body = _answer(sid, [YES_TABLEAU]).json()
    assert body["added"] == [] and "'led'" in body["dropped"][0]["why"]


def test_change_must_add_what_you_confirmed(sid, ai):
    # A placement that rewords the line but never adds Tableau.
    ai.placements = {"placements": [placement("q1", "p7", P7, "Tools: Power BI, Excel, Git")], "not_placed": []}
    body = _answer(sid, [YES_TABLEAU]).json()
    assert body["added"] == [] and "without adding" in body["dropped"][0]["why"]


def test_placement_for_a_no_answer_is_dropped(sid, ai):
    ai.placements = {"placements": [placement("q2", "p7", P7, "Tools: Excel, Power BI, pandas, Git")], "not_placed": []}
    body = _answer(sid, [YES_TABLEAU, NO_PANDAS]).json()
    assert body["added"] == []
    assert "didn't answer Yes" in body["dropped"][0]["why"]


def test_headings_and_header_lines_are_never_targets(sid, ai):
    ai.placements = {"placements": [placement("q1", "p5", "SKILLS", "SKILLS (Tableau)")], "not_placed": []}
    assert _answer(sid, [YES_TABLEAU]).json()["added"] == []


def test_not_placed_answers_are_reported(sid, ai):
    ai.placements = {"placements": [], "not_placed": [{"question_id": "q1", "reason": "No tools line fits."}]}
    body = _answer(sid, [YES_TABLEAU]).json()
    assert body["not_placed"] == [{"question_id": "q1", "reason": "No tools line fits."}]


# --- Deciding and applying --------------------------------------------------

def _decide(session_id, suggestion_id, action, text=None):
    return client.put(f"/api/resume/{session_id}/suggestions/{suggestion_id}",
                      json={"action": action, "text": text, "analysis_id": _aid(session_id)})


def test_accept_and_apply_an_answer_card(sid, ai):
    ai.placements = {"placements": [placement("q1", "p7", P7, "Tools: Excel, Power BI, Tableau, Git")], "not_placed": []}
    card = _answer(sid, [YES_TABLEAU]).json()["added"][0]
    assert _decide(sid, card, "accept").status_code == 200
    body = client.post(f"/api/resume/{sid}/apply").json()
    assert body["download_ready"] and body["validation"]["ok"]
    texts = [p.text for p in Document(io.BytesIO(client.get(f"/api/resume/{sid}/download").content)).paragraphs]
    assert "Tools: Excel, Power BI, Tableau, Git" in texts


def test_two_changes_to_one_line_are_refused(sid, ai):
    # s1 (analysis) and the answer card both target p10.
    ai.placements = {"placements": [placement(
        "q1", "p10", P10, "Built a Power BI dashboard, later rebuilt in Tableau, used by regional managers to track weekly sales.")],
        "not_placed": []}
    card = _answer(sid, [{"question_id": "q1", "has_it": True,
                          "details": "later rebuilt the sales dashboard in Tableau"}]).json()["added"][0]
    assert _decide(sid, "s1", "accept").status_code == 200
    second = _decide(sid, card, "accept")
    assert second.status_code == 409
    assert "Undo that one first" in second.json()["detail"]
    # Rejecting is always allowed.
    assert _decide(sid, card, "reject").status_code == 200


def test_answering_again_replaces_earlier_cards_and_their_decisions(sid, ai):
    ai.placements = {"placements": [placement("q1", "p7", P7, "Tools: Excel, Power BI, Tableau, Git")], "not_placed": []}
    first = _answer(sid, [YES_TABLEAU]).json()["added"][0]
    _decide(sid, first, "accept")
    client.post(f"/api/resume/{sid}/apply")

    ai.placements = {"placements": [], "not_placed": [{"question_id": "q1", "reason": "x"}]}
    body = _answer(sid, [YES_TABLEAU]).json()
    assert first not in [s["id"] for s in body["suggestions"]]
    assert first not in storage.load_json(sid, "decisions.json")["items"]
    # The refined file built from the old card is withdrawn.
    assert client.get(f"/api/resume/{sid}/download").status_code == 404


def test_answers_for_an_older_analysis_are_refused(sid, ai):
    old = _aid(sid)
    ai_post(client, f"/api/resume/{sid}/analyze", json={"job_description": JOB, "consent": True})
    response = ai_post(client, f"/api/resume/{sid}/answers",
                           json={"analysis_id": old, "answers": [YES_TABLEAU], "consent": True})
    assert response.status_code == 409


def test_seniority_word_from_elsewhere_in_resume_is_still_dropped():
    # "Led" appears in ANOTHER line of the resume, so it isn't a brand-new
    # word. Adding it to this line still claims more than the user said.
    from app.answers import AnswerPlacements, Placement, check_placements
    from app.docx_reader import ResumeParagraph

    def para(pid, text):
        return ResumeParagraph(id=pid, text=text, section="Experience", is_heading=False,
                               is_list_item=True, in_table=False, style="List Bullet")

    sent = [para("p1", "Led the college coding club."), para("p2", "Built a sales dashboard in Power BI.")]
    result = AnswerPlacements(
        placements=[Placement(question_id="q1", paragraph_id="p2", original_text="Built a sales dashboard in Power BI.",
                              proposed_text="Led a sales dashboard in Power BI and Tableau.", reason="r")],
        not_placed=[],
    )
    answers = [{"question_id": "q1", "question": "Tableau?", "details": "I also used Tableau for it"}]
    kept, dropped, _ = check_placements(result, sent, answers, [], set())
    assert not kept and "'led'" in dropped[0]["why"]


def test_edit_combines_and_replaces_the_other_change_for_the_line(sid, ai):
    # The refusal message says "use Edit here to combine both". That must work.
    ai.placements = {"placements": [placement(
        "q1", "p10", P10, "Built a Power BI dashboard, later rebuilt in Tableau, used by regional managers to track weekly sales.")],
        "not_placed": []}
    card = _answer(sid, [{"question_id": "q1", "has_it": True,
                          "details": "later rebuilt the sales dashboard in Tableau"}]).json()["added"][0]
    assert _decide(sid, "s1", "accept").status_code == 200
    combined = "Built a Power BI dashboard, later rebuilt in Tableau, that regional managers used to track weekly sales."
    response = _decide(sid, card, "edit", text=combined)
    assert response.status_code == 200
    body = response.json()
    assert body["changed"]["s1"]["effect"] == "keep"
    items = storage.load_json(sid, "decisions.json")["items"]
    assert items["s1"]["effect"] == "keep" and items[card]["final_text"] == combined
    # Exactly one change for the line, so creating the file works.
    applied = client.post(f"/api/resume/{sid}/apply").json()
    assert applied["download_ready"] and len(applied["applied"]) == 1
