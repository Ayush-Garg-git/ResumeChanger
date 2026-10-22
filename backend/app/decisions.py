"""
The user's decision on each suggestion: accept, reject, edit, or not yet decided.

Rule from the project brief: apply ONLY what the user explicitly approved.
So every suggestion starts as "undecided", and undecided counts as "keep
the original". Milestone 5 will apply only decisions whose effect is
"change" or "remove".

Which actions each kind of suggestion allows:

    kind                       accept means        edit   reject
    shorten/reword/emphasise   use proposed text    yes    keep original
    consider_removing          remove the line      yes    keep original
    review                     (not allowed)        yes    keep original

Decisions are saved in the session folder as decisions.json:
    {"s1": {"action": "accept", "effect": "change", "final_text": "...", ...}}
"""

import re

REWRITE_KINDS = {"shorten", "reword", "emphasise", "add_confirmed"}

# Longest edit we accept, in characters. A resume paragraph is short.
MAX_EDIT_CHARS = 1000


def _tidy(text: str) -> str:
    """Line breaks to spaces, repeated spaces to one, keep tabs, trim ends."""
    text = re.sub(r"[\r\n]+", " ", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip(" ")


class DecisionError(Exception):
    """A decision that can't be saved, explained for the user."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def make_decision(suggestion: dict, action: str, text: str | None) -> dict | None:
    """
    Work out what a decision means. Returns the record to store, or None for
    "undecided" (which simply removes any earlier decision).
    """
    kind = suggestion["kind"]
    original = suggestion["original_text"]
    record = {"paragraph_id": suggestion["paragraph_id"], "action": action}

    if action == "undecided":
        return None

    if action == "reject":
        return {**record, "effect": "keep", "final_text": None}

    if action == "accept":
        if kind in REWRITE_KINDS:
            return {**record, "effect": "change", "final_text": suggestion["proposed_text"]}
        if kind == "consider_removing":
            return {**record, "effect": "remove", "final_text": None}
        raise DecisionError("This suggestion has no proposed change to accept. Keep it or edit it.")

    if action == "edit":
        # One paragraph: line breaks become spaces (they would add a new
        # line in Word). Tabs are kept: many resumes use a tab to push dates
        # to the right edge, and losing it would break that alignment.
        edited = _tidy(text or "")
        if not edited:
            raise DecisionError("Your edit is empty. To remove the line, use Remove line or Keep.")
        if len(edited) > MAX_EDIT_CHARS:
            raise DecisionError(f"Your edit is longer than {MAX_EDIT_CHARS} characters.")
        if _tidy(original) == edited:
            raise DecisionError("Your edit is the same as the original line. Choose Keep instead.")
        if any(ph in edited for ph in ("[email]", "[phone]", "[link]")):
            raise DecisionError("Your edit contains a placeholder like [email]. Please write the real text.")
        # Note: we do NOT run the "invented skill" checks on the user's own
        # words. Those checks guard against the AI. The user is the source
        # of truth about their own experience.
        return {**record, "effect": "change", "final_text": edited}

    raise DecisionError("Unknown action.")


def summarise(analysis: dict, decisions: dict) -> dict:
    """Counts and a list of what will actually change, in resume order."""
    counts = {"change": 0, "remove": 0, "keep": 0, "undecided": 0}
    changes = []

    for s in analysis["suggestions"]:
        decision = decisions.get(s["id"])
        if decision is None:
            counts["undecided"] += 1
            continue
        counts[decision["effect"]] += 1
        if decision["effect"] in ("change", "remove"):
            changes.append(
                {
                    "suggestion_id": s["id"],
                    "paragraph_id": s["paragraph_id"],
                    "effect": decision["effect"],
                    "original_text": s["original_text"],
                    "final_text": decision["final_text"],
                    "edited_by_user": decision["action"] == "edit",
                }
            )

    # p3 before p10 before p21: the order they appear in the resume.
    changes.sort(key=lambda c: int(c["paragraph_id"][1:]))
    return {"counts": counts, "changes": changes}
