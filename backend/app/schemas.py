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
    original_text: str = Field(description="The paragraph's text, copied exactly.")
    proposed_text: Optional[str] = Field(
        description="The full replacement text for shorten, reword and emphasise. "
        "null for consider_removing and review."
    )
    reason: str = Field(description="One or two plain sentences: why this helps for this job.")
    requirement: Optional[str] = Field(
        description="The job requirement this relates to, or null."
    )


class ResumeAnalysis(BaseModel):
    role_title: str = Field(description="The job title from the job description.")
    fit_summary: str = Field(
        description="Two or three neutral sentences on how the resume matches the job."
    )
    requirements: list[Requirement]
    suggestions: list[Suggestion] = Field(
        description="Only changes worth making. An empty list is a valid answer."
    )
    questions_for_user: list[str] = Field(
        description="Questions about job requirements the resume does not show, "
        "e.g. 'The job asks for Tableau. Have you used it?'. Never add such "
        "things to the resume yourself."
    )
    no_changes_needed: bool = Field(
        description="true if the resume already fits the job well as it is."
    )


# --------------------------------------------------------------------------
# What the frontend sends
# --------------------------------------------------------------------------
class DecisionRequest(BaseModel):
    action: Literal["accept", "reject", "edit", "undecided"]
    # Only for "edit": the user's own version of the line.
    text: Optional[str] = None


class AnalyzeRequest(BaseModel):
    job_description: str
    # The user ticked "I understand my resume will be sent to Google Gemini".
    consent: bool = False
