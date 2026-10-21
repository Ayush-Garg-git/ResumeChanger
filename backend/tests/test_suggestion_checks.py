"""
Tests for the safety net that filters AI suggestions.

Each test builds an AI answer by hand, including deliberately bad ones,
and checks that the bad ones are dropped for the right reason.
"""

from app.analyzer import paragraphs_to_send
from app.docx_reader import read_resume
from app.schemas import ResumeAnalysis, Suggestion
from app.suggestion_checks import check_suggestions, job_terms

JOB = (
    "We are hiring a Data Analyst. You will build dashboards in Power BI and "
    "write SQL queries. Experience with Tableau and AWS is a plus."
)


def _analysis(*suggestions):
    return ResumeAnalysis(
        role_title="Data Analyst",
        fit_summary="Good match.",
        requirements=[],
        suggestions=list(suggestions),
        questions_for_user=[],
        no_changes_needed=False,
    )


def _suggest(pid, original, proposed, kind="reword"):
    return Suggestion(
        paragraph_id=pid, kind=kind, original_text=original,
        proposed_text=proposed, reason="Relevant.", requirement=None,
    )


def _run(sample_resume, *suggestions, job=JOB):
    sent = paragraphs_to_send(read_resume(sample_resume))
    return check_suggestions(_analysis(*suggestions), sent, job)


# Paragraph p10 in the sample resume:
P10 = "Built a Power BI dashboard used by regional managers to track weekly sales."


def test_good_reword_is_kept(sample_resume):
    kept, dropped = _run(
        sample_resume,
        _suggest("p10", P10, "Built a Power BI dashboard that regional managers used to track weekly sales."),
    )
    assert len(kept) == 1 and not dropped


def test_unknown_paragraph_is_dropped(sample_resume):
    kept, dropped = _run(sample_resume, _suggest("p999", "x", "y"))
    assert not kept
    assert "isn't in your resume" in dropped[0].why


def test_header_paragraph_is_never_a_target(sample_resume):
    # p0 is the name: it is never sent to the AI, so it's "unknown".
    kept, dropped = _run(sample_resume, _suggest("p0", "Riya Mehta", "Riya M."))
    assert not kept


def test_heading_is_dropped(sample_resume):
    kept, dropped = _run(sample_resume, _suggest("p8", "Work Experience", "Experience"))
    assert "heading" in dropped[0].why


def test_misquoted_original_is_dropped(sample_resume):
    kept, dropped = _run(
        sample_resume, _suggest("p10", "Built a Tableau dashboard.", "Built dashboards.")
    )
    assert "quoted the original line incorrectly" in dropped[0].why


def test_invented_tool_from_job_description_is_dropped(sample_resume):
    # Tableau is in the job description but nowhere in the resume.
    kept, dropped = _run(
        sample_resume,
        _suggest("p10", P10, "Built Power BI and Tableau dashboards used by regional managers to track weekly sales."),
    )
    assert not kept
    assert "tableau" in dropped[0].why


def test_tool_already_in_resume_is_allowed(sample_resume):
    # SQL is in the job description AND in the resume (Skills line).
    kept, dropped = _run(
        sample_resume,
        _suggest("p11", "Automated a manual Excel report with Python, cutting preparation time from a full day to under an hour.",
                 "Automated a manual Excel report with Python and SQL, cutting preparation time from a full day to under an hour."),
    )
    assert len(kept) == 1, dropped


def test_new_number_is_dropped(sample_resume):
    kept, dropped = _run(
        sample_resume,
        _suggest("p10", P10, "Built a Power BI dashboard used by 40 regional managers to track weekly sales."),
    )
    assert "added a number" in dropped[0].why


def test_shorten_must_be_shorter(sample_resume):
    kept, dropped = _run(
        sample_resume,
        _suggest("p10", P10, P10 + " Every week.", kind="shorten"),
    )
    assert not kept


def test_unchanged_text_is_dropped(sample_resume):
    kept, dropped = _run(sample_resume, _suggest("p10", P10, P10))
    assert "same as the original" in dropped[0].why


def test_only_one_suggestion_per_paragraph(sample_resume):
    first = _suggest("p10", P10, "Built a Power BI dashboard for regional managers to track weekly sales.", kind="shorten")
    second = _suggest("p10", P10, "Built a Power BI sales dashboard used by regional managers weekly.")
    kept, dropped = _run(sample_resume, first, second)
    assert len(kept) == 1 and kept[0].kind == "shorten"
    assert "already a suggestion" in dropped[0].why


def test_consider_removing_never_carries_new_text(sample_resume):
    portfolio = "Portfolio Website: Personal site built with HTML and CSS."
    kept, _ = _run(sample_resume, _suggest("p14", portfolio, "something", kind="consider_removing"))
    assert kept[0].proposed_text is None


def test_original_text_is_taken_from_the_document(sample_resume):
    # The AI's copy may differ in spacing or case; we display the real text.
    kept, _ = _run(
        sample_resume,
        _suggest("p10", P10.upper(), "Built a Power BI dashboard that regional managers used to track weekly sales."),
    )
    assert kept[0].original_text == P10


def test_job_terms_ignore_sentence_starting_capitals():
    terms = job_terms("Strong communication. Experience with Tableau, AWS and C++.")
    assert {"tableau", "aws", "c++"} <= terms
    assert "strong" not in terms
    assert "experience" not in terms


def test_invented_tool_at_sentence_start_in_job_is_still_caught(sample_resume):
    # "Tableau" starts a sentence in the job description, so it doesn't look
    # special there. It is still caught because it looks like a tool name in
    # the proposal and the resume never mentions it.
    job = "We need a Data Analyst who knows SQL. Tableau is a plus."
    kept, dropped = _run(
        sample_resume,
        _suggest("p10", P10, "Built a Power BI dashboard in Tableau used by regional managers to track weekly sales."),
        job=job,
    )
    assert not kept
    assert "tableau" in dropped[0].why


def test_ordinary_new_words_are_fine(sample_resume):
    # Small rewording with plain new words ("that", "used") is allowed.
    kept, dropped = _run(
        sample_resume,
        _suggest("p10", P10, "Built a Power BI dashboard that regional managers used each week to track sales."),
    )
    assert len(kept) == 1, dropped
