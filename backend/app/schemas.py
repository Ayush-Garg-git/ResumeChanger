"""
The shapes of data that cross boundaries: what the AI must return, and
what the frontend sends us.

These are Pydantic models. A Pydantic model does two jobs here:
1. Its JSON schema is sent to the AI (Claude or Gemini), which is then
   forced to answer in exactly this shape ("structured output").
2. We validate the AI's answer against the same model, so if anything is
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
# Whole-resume tailoring: enough for a few changes in every section.
MAX_SUGGESTIONS = 16
MAX_QUESTIONS = 5
MAX_SECTION_REVIEWS = 12
MAX_TIPS_PER_SECTION = 2


class Requirement(BaseModel):
    requirement: str = Field(
        description="One requirement from the job description, in a few words."
    )
    importance: Literal["must_have", "nice_to_have"] = Field(
        description="must_have if the job lists it under required skills, must-haves, "
        "eligibility or core responsibilities; nice_to_have only if the job marks it as "
        "a plus, preferred or nice to have."
    )
    evidence: list[str] = Field(
        description="IDs of resume paragraphs that support this requirement, "
        "e.g. ['p10', 'p13']. Empty list if the resume does not support it."
    )
    coverage: Literal["clear", "partial", "missing"] = Field(
        description="How well the resume, as written, shows this requirement. A tool the "
        "resume shows that is a well-known form of the required one counts as evidence "
        "(e.g. Next.js work shows React)."
    )


class Suggestion(BaseModel):
    paragraph_id: str = Field(description="ID of the ONE paragraph this is about, e.g. 'p10'.")
    kind: Literal[
        "tailor", "reword", "shorten", "emphasise", "remove_items", "consider_removing", "review"
    ] = Field(
        description="tailor: rewrite the line so the responsibilities, results and transferable "
        "skills that matter for this job come first, using only facts already in the resume. "
        "reword: clearer or more direct wording, same facts. "
        "shorten: same meaning, fewer words. "
        "emphasise: same words, reordered so the relevant items come first. "
        "remove_items: delete some items from a list line (e.g. skills that add little for this "
        "job); proposed_text is the same line with those items left out and nothing else changed. "
        "consider_removing: the whole line is clearly unrelated to this role. "
        "review: the user should look at this line; no rewrite offered."
    )
    # Only the opening words: enough for us to check the AI means the right
    # line, without making it copy whole paragraphs back (slow to write).
    original_text: str = Field(
        description="The FIRST FIVE WORDS of the paragraph, copied exactly. Not the whole paragraph."
    )
    proposed_text: Optional[str] = Field(
        description="The full replacement text for tailor, reword, shorten, emphasise and "
        "remove_items. null for consider_removing and review."
    )
    reason: str = Field(description="One short plain sentence: why this helps for this job.")
    requirement: Optional[str] = Field(
        description="The job requirement this relates to, or null."
    )


class SectionReview(BaseModel):
    section: str = Field(
        description="The section name exactly as given in square brackets in the resume, e.g. 'Experience'."
    )
    relevance: Literal["high", "medium", "low"] = Field(
        description="How much this section, as written, matters for this job."
    )
    verdict: str = Field(description="One short plain sentence on this section for this job.")
    tips: list[str] = Field(
        description="Up to 2 short, specific tips for this section that are NOT line edits, e.g. "
        "'Move this section above Community & Leadership'. Never suggest adding skills, tools, "
        "numbers or experience the resume doesn't show; ask in questions_for_user instead. "
        "Empty list if none.",
        json_schema_extra={"maxItems": MAX_TIPS_PER_SECTION},
    )


class ResumeAnalysis(BaseModel):
    role_title: str = Field(description="The job title from the job description.")
    # default_factory: older saved answers and test stand-ins may lack it.
    # The schema sent to the AI still requires it (analyzer.require_all).
    section_reviews: list[SectionReview] = Field(
        default_factory=list,
        description="One review for EVERY section of the resume, in resume order.",
        json_schema_extra={"maxItems": MAX_SECTION_REVIEWS},
    )
    fit_summary: str = Field(
        description="Two short neutral sentences on how the resume matches the job."
    )
    # maxItems: Gemini enforces it while writing (shorter, faster answers).
    # Claude doesn't accept it, so it's removed for Claude (claude_schema)
    # and the limits are in the descriptions; analyse() always trims.
    requirements: list[Requirement] = Field(
        description=f"The job's most important requirements, at most {MAX_REQUIREMENTS}.",
        json_schema_extra={"maxItems": MAX_REQUIREMENTS},
    )
    suggestions: list[Suggestion] = Field(
        description=f"Changes worth making across ALL sections, at most {MAX_SUGGESTIONS}, "
        "in resume order. An empty list is a valid answer.",
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
    # The AI named in the notice the user agreed to (see AnalyzeRequest).
    provider: Optional[str] = Field(default=None, max_length=50)


class AnalyzeRequest(BaseModel):
    job_description: str = Field(max_length=50_000)
    # The user ticked "I understand my resume will be sent to the AI provider".
    consent: bool = False
    # The AI named in the notice the user agreed to, e.g. "Anthropic Claude".
    # If the site has switched AI since the page loaded, the consent was for
    # a different provider, so the request is refused.
    provider: Optional[str] = Field(default=None, max_length=50)
