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
- "tailor"/"reword" keep every number the line had: a result
  (40%, 20+ members) is never quietly dropped.                (keep achievements)
- "remove_items" only deletes whole items from a list line,
  e.g. "Tools: Git, WordPress, Excel" -> "Tools: Git, Excel". (nothing else changes)
- Section tips don't suggest adding things the resume
  doesn't show.                                               (rule 1: never invent)
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

REWRITE_KINDS = {"tailor", "reword", "shorten", "emphasise", "remove_items"}
# Kinds that must keep every number (result) the original line had.
KEEP_NUMBER_KINDS = {"tailor", "reword", "emphasise", "remove_items"}


@dataclass
class DroppedSuggestion:
    paragraph_id: str
    kind: str
    why: str


def _squash(text: str) -> str:
    """Lowercase and collapse spaces, for forgiving comparisons."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def quotes_start_of(quote: str, original: str) -> bool:
    """
    The AI copies only the opening words of the line it means (writing
    whole paragraphs back made answers slow). Check those words really
    are how this line starts: at least 3 words, or the whole line if
    it's shorter. A full copy of the line passes too.
    """
    quote_words = _squash(quote).split()
    original_words = _squash(original).split()
    needed = min(3, len(original_words))
    if len(quote_words) < needed:
        return False
    return original_words[: len(quote_words)] == quote_words


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


# Irregular past forms, so "built" in a resume covers "build" in a job ad.
_IRREGULAR = {
    "built": "build", "wrote": "write", "written": "write", "ran": "run", "made": "make",
    "taught": "teach", "brought": "bring", "sold": "sell", "bought": "buy", "kept": "keep",
    "gave": "give", "given": "give", "took": "take", "taken": "take", "held": "hold",
    "met": "meet", "thought": "think", "spent": "spend", "sent": "send", "found": "find",
    "grew": "grow", "grown": "grow", "began": "begin", "begun": "begin", "chose": "choose",
    "chosen": "choose", "drew": "draw", "drawn": "draw", "won": "win", "shown": "show",
    "knew": "know", "known": "know", "spoke": "speak", "spoken": "speak", "understood": "understand",
}


def word_form(word: str) -> tuple[str, str]:
    """
    ("verb", root) for -ed / -ing / irregular past forms, ("noun", root)
    for everything else, with plurals reduced: "dashboards" -> ("noun",
    "dashboard"), "built" and "building" -> ("verb", "build").

    The KIND matters. A verb form never matches a plain word, so "excelled"
    in a resume can't vouch for the tool "Excel", "reacted" for "React",
    "marketing" for "market", or a date's "Present" for "presented".
    Only for deciding whether the resume already uses a word.
    """
    w = word.casefold()
    if w in _IRREGULAR:
        return "verb", _IRREGULAR[w].rstrip("e")  # same shape as "writing" -> "writ"
    for suffix in ("ing", "ed"):
        root = w[: -len(suffix)]
        if w.endswith(suffix) and len(root) >= 3 and not w.endswith("eed"):
            if root[-1] == root[-2] and root[-1] not in "aeiouls":
                root = root[:-1]  # "debugging" -> "debug", "planned" -> "plan"
            return "verb", root.rstrip("e")
    if w.endswith("ies") and len(w) >= 5:
        return "noun", w[:-3] + "y"
    if w.endswith(("ses", "xes", "ches", "shes", "zes")) and len(w) >= 5:
        return "noun", w[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us", "sis")) and len(w) >= 4:
        return "noun", w[:-1]
    return "noun", w


def _root(word: str) -> str:
    """Root regardless of kind: for the job-description side, where being
    generous means catching MORE words, which is the safe direction."""
    return word_form(word)[1]


def _new_words(proposed: str, resume_tokens: set[str]) -> set[str]:
    """Words in the proposal that the resume doesn't already use in any form of the same kind."""
    resume_forms = {word_form(t) for t in resume_tokens}
    return {t for t in _tokens(proposed) - resume_tokens if word_form(t) not in resume_forms}


def invented_terms(proposed: str, resume_tokens: set[str], job_description: str) -> list[str]:
    """
    Words the proposal adds that the resume never mentions, and that look
    like a tool, skill or qualification. Two ways to catch them:
    - the word is a tool-like term in the job description, or
    - the word looks tool-like in the proposal itself (capitalised
      mid-sentence, acronym, digits/symbols), e.g. "...with Python and Tableau".
    """
    new_words = _new_words(proposed, resume_tokens)
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
    "led", "lead", "leads", "leading", "spearhead", "spearheads", "spearheaded", "spearheading",
    "headed", "heads", "heading", "direct", "directs", "directed", "directing",
    "own", "owns", "owned", "owning", "architected", "architecting", "pioneered", "pioneering",
    "championed", "championing", "orchestrated", "orchestrating", "drove", "drives", "driving",
    "founded", "founding", "manage", "manages", "managed", "managing", "oversee", "oversees",
    "oversaw", "overseeing", "supervise", "supervises", "supervised", "supervising",
    "expert", "experts", "mastered", "mastering",
}

# Words that say someone else shared or led the work. Dropping one would
# change who did the work ("Assisted engineers in designing" -> "Designed").
SHARED_CREDIT_WORDS = {
    "assist", "assists", "assisted", "assisting", "helped", "helping", "supported", "supporting",
    "contributed", "contributing", "participated", "participating", "involved", "shadowed",
    "shadowing", "supervision", "guidance", "co-founded", "co-authored", "jointly",
}


def dropped_shared_credit(original: str, proposed: str) -> list[str]:
    """Shared-credit words the original line had and the proposal drops."""
    words = lambda t: {w.casefold() for w in re.findall(r"[A-Za-z][A-Za-z-]*", t)}  # noqa: E731
    return sorted((words(original) & SHARED_CREDIT_WORDS) - words(proposed))


def invented_words(proposed: str, resume_tokens: set[str], job_description: str) -> list[str]:
    """
    Meaningful words (3+ letters, not plain words) that the proposal adds,
    that appear in the job description, and that the resume never uses,
    whatever their capitalisation. E.g. "machine", "learning".
    """
    new_words = _new_words(proposed, resume_tokens)
    jd_roots = {_root(t) for t in _tokens(job_description)}
    return sorted(
        w for w in new_words if _root(w) in jd_roots and len(w) >= 3 and w not in _PLAIN_WORDS
    )


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


def _split_top_level(text: str) -> list[str]:
    """Split on , ; | but not inside brackets: "Linux (CLI, config)" is one item."""
    items, depth, current = [], 0, []
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if ch in ",;|" and depth == 0:
            items.append("".join(current))
            current = []
        else:
            current.append(ch)
    items.append("".join(current))
    return [i.strip() for i in items if i.strip()]


def _label_and_items(text: str) -> tuple[str, list[str]]:
    """"Tools: Git, GitHub, VS Code" -> ("tools", ["git", "github", "vs code"])."""
    label, rest = "", text
    colon = text.find(":")
    if 0 < colon <= 40:
        label, rest = text[:colon], text[colon + 1:]
    items = _split_top_level(rest.strip().rstrip("."))
    return _squash(label), [_squash(i) for i in items]


MAX_WORDS_PER_ITEM = 6
# A "list item" starting with one of these is really part of a sentence:
# "Wrote tests in Python, under supervision, for the billing team".
_CLAUSE_STARTERS = {
    "under", "for", "with", "in", "to", "by", "at", "of", "on", "from", "including", "which",
    "that", "while", "and", "but", "so", "reducing", "cutting", "improving", "increasing",
}


def removal_problem(original: str, proposed: str) -> str | None:
    """
    Why a remove_items proposal is not a pure removal of list items, or None.
    Only plain lists qualify: one label at most, short items. A sentence
    with commas ("Wrote tests in Python, under supervision") or a line with
    two labels ("Proficient: Python | Beginner: Java") is not a list, and
    removing a piece of it could change its meaning.
    """
    label, items = _label_and_items(original)
    new_label, new_items = _label_and_items(proposed)
    if (
        len(items) < 2
        or any(":" in item for item in items)
        or any(len(item.split()) > MAX_WORDS_PER_ITEM for item in items)
        or any(item.split()[0] in _CLAUSE_STARTERS for item in items[1:])
    ):
        return "It was marked 'remove items' but the line isn't a simple list."
    if new_label != label:
        return "It was marked 'remove items' but changed the start of the line."
    if not new_items:
        return "It would have removed every item; use 'consider removing' for that."
    remaining = iter(items)  # kept items must appear in the original order
    if not all(item in remaining for item in new_items):
        return "It was marked 'remove items' but changed or added words."
    if len(new_items) >= len(items):
        return "It was marked 'remove items' but removed nothing."
    removed = list(items)
    for item in new_items:
        removed.remove(item)
    if any(re.search(r"\d", item) for item in removed):
        return "It would have removed an item with a number in it, which may be a result."
    return None


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
        if not quotes_start_of(s.original_text, original):
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
            if s.kind == "remove_items":
                problem = (
                    "Removing items is only offered for skills lists."
                    if para.section != "Skills"
                    else removal_problem(original, proposed)
                )
                if problem:
                    drop(s, problem)
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
            shared = dropped_shared_credit(original, proposed)
            if shared:
                drop(s, f"It removed '{shared[0]}', which would change who did the work.")
                continue
            inflated = inflated_words(original, proposed)
            if inflated:
                drop(s, f"It changed the wording to '{inflated[0]}', which claims more than the original line says.")
                continue
            if s.kind == "shorten" and len(proposed) >= len(original):
                drop(s, "It was marked 'shorten' but wasn't shorter.")
                continue
            lost_numbers = _numbers(original) - _numbers(proposed)
            if s.kind in KEEP_NUMBER_KINDS and lost_numbers:
                drop(s, f"It dropped a result from the line ({', '.join(sorted(lost_numbers))}).")
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


def check_section_reviews(
    analysis: ResumeAnalysis,
    sent_paragraphs: list[ResumeParagraph],
    job_description: str,
    name_words: list[str] | None = None,
) -> list[dict]:
    """
    Keep one review per real section, in resume order. Tips are advice
    only (never applied), but a tip that suggests adding something the
    resume doesn't show is still dropped: that belongs in a question.
    """
    from app.schemas import MAX_TIPS_PER_SECTION

    order = []
    for p in sent_paragraphs:
        if p.section not in order:
            order.append(p.section)
    by_name = {_squash(name): name for name in order}
    resume_tokens = _tokens(" ".join(redact(p.text, name_words) for p in sent_paragraphs))

    reviews: dict[str, dict] = {}
    for r in analysis.section_reviews:
        name = by_name.get(_squash(r.section))
        if name is None or name in reviews:
            continue
        tips = []
        for tip in r.tips:
            tip = tidy_proposal(tip)
            if not tip or any(ph in tip for ph in PLACEHOLDERS):
                continue
            if invented_terms(tip, resume_tokens, job_description):
                continue
            if invented_words(tip, resume_tokens, job_description):
                continue
            tips.append(tip)
        reviews[name] = {
            "section": name,
            "relevance": r.relevance,
            "verdict": tidy_proposal(r.verdict),
            "tips": tips[:MAX_TIPS_PER_SECTION],
        }
    return [reviews[name] for name in order if name in reviews]


def clean_evidence(analysis: ResumeAnalysis, sent_paragraphs: list[ResumeParagraph]) -> ResumeAnalysis:
    """Remove evidence IDs that don't exist, so the UI never links to nothing."""
    valid = {p.id for p in sent_paragraphs}
    requirements = [
        r.model_copy(update={"evidence": [e for e in r.evidence if e in valid]})
        for r in analysis.requirements
    ]
    return analysis.model_copy(update={"requirements": requirements})
