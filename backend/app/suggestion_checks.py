"""
The backend's safety net for AI suggestions.

The AI is told not to invent things, but being told isn't proof. Every
suggestion passes through these checks, and any that fails is dropped,
with a plain reason the user can see.

What we check (each maps to a rule in the project brief):
- It points at a real paragraph we actually sent.            (no made-up targets)
- It quotes that paragraph correctly.                         (it's about what we think)
- It doesn't add a number that wasn't in the line.            (rule 1: never invent)
- It doesn't add a tool/skill term from the job description
  that appears nowhere in the resume.                         (rule 1: never invent)
- It doesn't add ANY meaningful word that comes from the job
  description but appears nowhere in the resume, e.g.
  "machine learning".                                         (rule 1: never invent)
- It doesn't upgrade the wording to sound more senior,
  e.g. "Helped" -> "Led".                                     (rule 2: no inflating)
- "shorten" really is shorter.                                (rule 3: smallest change)
- It doesn't add line breaks that would change the layout.    (rule 8)

These are word-based checks, so they catch common cases, not every
possible one. That's why the user still approves every change.
- It doesn't contain our privacy placeholders.                (would insert "[email]")
- One suggestion per paragraph, and it actually changes something.
"""

import re
from collections import Counter
from dataclasses import dataclass

from app.analyzer import PLACEHOLDERS, redact
from app.docx_reader import ResumeParagraph
from app.schemas import ResumeAnalysis, Suggestion

REWRITE_KINDS = {"shorten", "reword", "emphasise"}


@dataclass
class DroppedSuggestion:
    paragraph_id: str
    kind: str
    why: str


def _squash(text: str) -> str:
    """Lowercase and collapse spaces, for forgiving comparisons."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", text))


# Words like "Python", "C++", "Node.js", "AWS", "Power BI" pieces.
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#.]*[A-Za-z0-9+#]|[A-Za-z]")


def _tokens(text: str) -> set[str]:
    return {t.casefold() for t in _TOKEN.findall(text)}


def special_terms(text: str) -> set[str]:
    """
    Words that look like names of tools, skills or qualifications: written
    with a capital letter mid-sentence ("experience with Tableau"), in
    capitals ("AWS"), or with digits/symbols ("C++").
    A capital at the start of a sentence doesn't count ("Strong skills").
    """
    terms = set()
    for match in _TOKEN.finditer(text):
        word = match.group()
        before = text[: match.start()].rstrip()
        sentence_start = not before or before[-1] in ".!?:;\n•*-–(\"'"
        looks_special = (
            any(c.isdigit() or c in "+#." for c in word)
            or (len(word) >= 2 and word.isupper())
            or (word[0].isupper() and not sentence_start)
        )
        if looks_special:
            terms.add(word.casefold().rstrip("."))
    return terms


# Kept as a name of its own because the tests read better with it.
job_terms = special_terms


def invented_terms(proposed: str, resume_tokens: set[str], job_description: str) -> list[str]:
    """
    Words the proposal adds that the resume never mentions, and that look
    like a tool, skill or qualification. Two ways to catch them:
    - the word is a tool-like term in the job description, or
    - the word looks tool-like in the proposal itself (capitalised
      mid-sentence, acronym, digits/symbols), e.g. "...with Python and Tableau".
    """
    new_words = _tokens(proposed) - resume_tokens
    suspicious = (new_words & special_terms(job_description)) | (new_words & special_terms(proposed))
    return sorted(suspicious)


# Common words that carry no claim. A proposal may use these even if the
# resume doesn't, e.g. "that", "used", "weekly".
_PLAIN_WORDS = set("""
a about above across after again against all also an and any are as at be been before being below
between both but by can could did do does doing during each either every few for from further had
has have having here how if in into is it its itself just less more most much new no nor not now of
off on once only or other our out over own per same should since so some such than that the their them
then there these they this those through to too under until up upon use used uses using very via was
we well were what when where which while who whom why will with within without would yet you your
day days week weekly month monthly year yearly daily time times first second one two three several
many each work worked working help helped helping make made making get got take took keep kept
across into onto team teams people user users
""".split())

# Verbs that claim more seniority or ownership than plainer wording.
# A proposal may keep one the original line already used, but not add one.
INFLATION_WORDS = {
    "led", "lead", "leading", "spearheaded", "spearheading", "headed", "directed", "owned",
    "architected", "pioneered", "championed", "orchestrated", "drove", "founded",
    "managed", "oversaw", "supervised", "expert", "mastered",
}


def invented_words(proposed: str, resume_tokens: set[str], job_description: str) -> list[str]:
    """
    Meaningful words (3+ letters, not plain words) that the proposal adds,
    that appear in the job description, and that the resume never uses,
    whatever their capitalisation. E.g. "machine", "learning".
    """
    new_words = _tokens(proposed) - resume_tokens
    jd_words = _tokens(job_description)
    return sorted(w for w in new_words & jd_words if len(w) >= 3 and w not in _PLAIN_WORDS)


def inflated_words(original: str, proposed: str) -> list[str]:
    return sorted((_tokens(proposed) & INFLATION_WORDS) - _tokens(original))


def tidy_proposal(text: str) -> str:
    """Line breaks to spaces and repeated spaces to one. Tabs are kept."""
    text = re.sub(r"[\r\n]+", " ", text)
    return re.sub(r" {2,}", " ", text).strip(" ")


def same_words(a: str, b: str) -> bool:
    """True if both texts use exactly the same words, in any order."""
    words = lambda t: Counter(re.findall(r"[\w+#.]+", t.casefold()))  # noqa: E731
    return words(a) == words(b)


def check_suggestions(
    analysis: ResumeAnalysis,
    sent_paragraphs: list[ResumeParagraph],
    job_description: str,
    name_words: list[str] | None = None,
) -> tuple[list[Suggestion], list[DroppedSuggestion]]:
    """`name_words` must match what analyzer.build_prompt masked."""
    by_id = {p.id: p for p in sent_paragraphs}
    resume_tokens = _tokens(" ".join(redact(p.text, name_words) for p in sent_paragraphs))

    kept: list[Suggestion] = []
    dropped: list[DroppedSuggestion] = []
    seen_ids: set[str] = set()

    def drop(s: Suggestion, why: str):
        dropped.append(DroppedSuggestion(s.paragraph_id, s.kind, why))

    for s in analysis.suggestions:
        para = by_id.get(s.paragraph_id)
        if para is None:
            drop(s, "It pointed at a paragraph that isn't in your resume.")
            continue
        if para.is_heading:
            drop(s, "It tried to change a section heading.")
            continue
        if s.paragraph_id in seen_ids:
            drop(s, "There was already a suggestion for this paragraph.")
            continue

        original = redact(para.text, name_words)
        if _squash(s.original_text) != _squash(original):
            drop(s, "It quoted the original line incorrectly.")
            continue
        if any(ph in original for ph in PLACEHOLDERS):
            drop(s, "The line contains private details we don't send to the AI.")
            continue

        if s.kind not in REWRITE_KINDS:
            # consider_removing / review never carry new text.
            s = s.model_copy(update={"proposed_text": None})
        else:
            proposed = tidy_proposal(s.proposed_text or "")
            if not proposed:
                drop(s, "It suggested a change but gave no new text.")
                continue
            if _squash(proposed) == _squash(original):
                drop(s, "The proposed text was the same as the original.")
                continue
            if any(ph in proposed for ph in PLACEHOLDERS):
                drop(s, "It would have inserted a placeholder into your resume.")
                continue
            new_numbers = _numbers(proposed) - _numbers(original)
            if new_numbers:
                drop(s, f"It added a number that isn't in the original line ({', '.join(sorted(new_numbers))}).")
                continue
            invented = invented_terms(proposed, resume_tokens, job_description)
            if invented:
                where = (
                    "is in the job description but nowhere in your resume"
                    if invented[0] in _tokens(job_description)
                    else "isn't anywhere in your resume"
                )
                drop(s, f"It added '{invented[0]}', which {where}.")
                continue
            added = invented_words(proposed, resume_tokens, job_description)
            if added:
                drop(
                    s,
                    f"It added '{' '.join(added[:2])}', which is in the job description "
                    "but nowhere in your resume.",
                )
                continue
            inflated = inflated_words(original, proposed)
            if inflated:
                drop(s, f"It changed the wording to '{inflated[0]}', which claims more than the original line says.")
                continue
            if s.kind == "shorten" and len(proposed) >= len(original):
                drop(s, "It was marked 'shorten' but wasn't shorter.")
                continue
            if s.kind == "emphasise" and not same_words(original, proposed):
                # "Emphasise" promises the same words in a new order. If words
                # changed, show it honestly as a reword instead.
                s = s.model_copy(update={"kind": "reword"})
            s = s.model_copy(update={"proposed_text": proposed})

        # Keep the real paragraph text (not the AI's copy) for display.
        s = s.model_copy(update={"original_text": para.text})
        seen_ids.add(s.paragraph_id)
        kept.append(s)

    return kept, dropped


def clean_evidence(analysis: ResumeAnalysis, sent_paragraphs: list[ResumeParagraph]) -> ResumeAnalysis:
    """Remove evidence IDs that don't exist, so the UI never links to nothing."""
    valid = {p.id for p in sent_paragraphs}
    requirements = [
        r.model_copy(update={"evidence": [e for e in r.evidence if e in valid]})
        for r in analysis.requirements
    ]
    return analysis.model_copy(update={"requirements": requirements})
