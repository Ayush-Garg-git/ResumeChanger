"""
The shapes of data that cross boundaries: what the AI must return, and
what the frontend sends us.

These are Pydantic models. A Pydantic model does two jobs here:
1. Its JSON schema is sent to Gemini, which is then forced to answer in
   exactly this shape ("structured output").
2. We validate Gemini's answer against the same model, so if anything is
   missing or the wrong type, we find out immediately instead of crashing later.

The `description` texts are read by the AI, so they double as instructions.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# What the AI returns
# --------------------------------------------------------------------------
# Caps on how much the AI writes. A screen of suggestions is plenty, and
# every extra item adds seconds to the wait.
MAX_REQUIREMENTS = 10
MAX_SUGGESTIONS = 8
MAX_QUESTIONS = 5


class Requirement(BaseModel):
    requirement: str = Field(
        description="One requirement from the job description, in a few words."
    )
    importance: Literal["must_have", "nice_to_have"]
    evidence: list[str] = Field(
        description="IDs of resume paragraphs that support this requirement, "
        "e.g. ['p10', 'p13']. Empty list if the resume does not support it."
    )
    coverage: Literal["clear", "partial", "missing"] = Field(
        description="How well the resume, as written, shows this requirement."
    )


class Suggestion(BaseModel):
    paragraph_id: str = Field(description="ID of the ONE paragraph this is about, e.g. 'p10'.")
    kind: Literal["shorten", "reword", "emphasise", "consider_removing", "review"] = Field(
        description="shorten: same meaning, fewer words. "
        "reword: small wording change to make existing relevant content clearer. "
        "emphasise: reorder words in the line so relevant existing items come first. "
        "consider_removing: the line may be less relevant to this role. "
        "review: the user should look at this line; no rewrite offered."
    )
    # Only the opening words: enough for us to check the AI means the right
    # line, without making it copy whole paragraphs back (slow to write).
    original_text: str = Field(
        description="The FIRST FIVE WORDS of the paragraph, copied exactly. Not the whole paragraph."
    )
    proposed_text: Optional[str] = Field(
        description="The full replacement text for shorten, reword and emphasise. "
        "null for consider_removing and review."
    )
    reason: str = Field(description="One short plain sentence: why this helps for this job.")
    requirement: Optional[str] = Field(
        description="The job requirement this relates to, or null."
    )


class ResumeAnalysis(BaseModel):
    role_title: str = Field(description="The job title from the job description.")
    fit_summary: str = Field(
        description="Two short neutral sentences on how the resume matches the job."
    )
    # maxItems limits are enforced by Gemini while it writes, which keeps
    # answers short (and fast). We also trim in code, in case.
    requirements: list[Requirement] = Field(
        description=f"The job's most important requirements, at most {MAX_REQUIREMENTS}.",
        json_schema_extra={"maxItems": MAX_REQUIREMENTS},
    )
    suggestions: list[Suggestion] = Field(
        description=f"Only changes worth making, at most {MAX_SUGGESTIONS}. An empty list is a valid answer.",
        json_schema_extra={"maxItems": MAX_SUGGESTIONS},
    )
    questions_for_user: list[str] = Field(
        description="Short questions about job requirements the resume does not show, "
        "e.g. 'The job asks for Tableau. Have you used it?'. Never add such "
        f"things to the resume yourself. At most {MAX_QUESTIONS}.",
        json_schema_extra={"maxItems": MAX_QUESTIONS},
    )
    no_changes_needed: bool = Field(
        description="true if the resume already fits the job well as it is."
    )


# --------------------------------------------------------------------------
# What the frontend sends
# --------------------------------------------------------------------------
# max_length values are hard caps against abuse. The friendlier limits
# (and their messages) are checked in the routes.
class DecisionRequest(BaseModel):
    action: Literal["accept", "reject", "edit", "undecided"]
    # Which analysis these suggestions came from. If the user re-analysed in
    # another tab, an old page can't change decisions for the new analysis.
    analysis_id: str = Field(max_length=64)
    # Only for "edit": the user's own version of the line.
    text: Optional[str] = Field(default=None, max_length=5000)


class Answer(BaseModel):
    question_id: str = Field(max_length=10)
    # True = "Yes, I have this". Only Yes answers with details are used.
    has_it: bool
    # The user's own words: where and how they used it.
    details: str = Field(default="", max_length=2000)


class AnswersRequest(BaseModel):
    analysis_id: str = Field(max_length=64)
    answers: list[Answer] = Field(max_length=20)
    # Sending answers means sending the resume to the AI again.
    consent: bool = False


class AnalyzeRequest(BaseModel):
    job_description: str = Field(max_length=50_000)
    # The user ticked "I understand my resume will be sent to Google Gemini".
    consent: bool = False
