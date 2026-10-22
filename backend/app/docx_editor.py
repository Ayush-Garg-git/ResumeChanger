"""
Applies the user's approved changes to a COPY of the original DOCX.

The original file is never modified. We open it, change only the approved
paragraphs, and save the result as a new file.

How a change is applied without losing formatting
-------------------------------------------------
A paragraph is a row of "runs", each with its own formatting:

    run 0 "Languages: " (bold)   run 1 "Python, SQL" (normal)

Setting the whole paragraph's text would merge everything into one plain
run and lose the bold (the formatting experiment in Milestone 2 proved
this). Instead we:

1. Split old and new text into words, spaces and punctuation.
2. Work out the smallest set of differences ("keep these words, replace
   this one, insert that one").
3. Apply each difference only inside the run(s) where it happens. New
   words take the formatting of the text they replace or sit next to.

Runs that the change doesn't touch are left exactly as they were.

When we refuse to edit (and say why)
------------------------------------
- The paragraph's text in the file isn't what the user approved a change
  for (the file and the suggestion don't match).
- The paragraph has text we can't see, e.g. tracked changes or fields.
- The change would touch a run that also holds an image, a page break,
  a field code or similar. Rewriting that run could delete it.
- Removing the line would remove a Word section break (page layout).
"""

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from app.docx_reader import iter_paragraphs

# Children a run may have for us to rewrite it safely. Anything else
# (w:drawing, w:fldChar, w:instrText, page breaks, footnote marks...) means
# we leave the run alone.
_SAFE_RUN_CHILDREN = {qn("w:rPr"), qn("w:t"), qn("w:tab"), qn("w:cr"), qn("w:lastRenderedPageBreak")}

# Words, runs of spaces/tabs, and single punctuation marks.
_TOKEN = re.compile(r"\w+|\s+|[^\w\s]", re.UNICODE)


@dataclass
class Change:
    """One approved change, as stored in decisions (see decisions.summarise)."""

    suggestion_id: str
    paragraph_id: str
    effect: str  # "change" or "remove"
    original_text: str
    final_text: str | None


@dataclass
class ApplyResult:
    applied: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)


class EditRefused(Exception):
    """This one change can't be applied safely. The message is for the user."""


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------
def _text_runs(paragraph: Paragraph) -> list[Run]:
    """The runs whose text makes up paragraph.text, in order (hyperlinks included)."""
    return [Run(r, paragraph) for r in paragraph._p.xpath("./w:r | ./w:hyperlink/w:r")]


def _run_is_safe(run: Run) -> bool:
    for child in run._r:
        if child.tag == qn("w:br"):
            if child.get(qn("w:type")) not in (None, "textWrapping"):
                return False  # page or column break
        elif child.tag not in _SAFE_RUN_CHILDREN:
            return False
    return True


def _check_paragraph_is_editable(paragraph: Paragraph) -> list[Run]:
    runs = _text_runs(paragraph)
    if "".join(r.text for r in runs) != paragraph.text:
        raise EditRefused("This line has a structure we can't edit safely yet.")
    # Text inside tracked changes, fields or content controls isn't in
    # paragraph.text. If any exists, our picture of the line is incomplete.
    # Keep the elements themselves in the set, not id() numbers: lxml makes
    # a new Python wrapper each time it's asked, so id() values don't match.
    visible = {t for r in runs for t in r._r.iter(qn("w:t"))}
    if any(t not in visible for t in paragraph._p.iter(qn("w:t"))):
        raise EditRefused("This line contains tracked changes or fields that we can't edit safely.")
    return runs


# --------------------------------------------------------------------------
# Changing text inside runs
# --------------------------------------------------------------------------
def _char_ops(old: str, new: str) -> list[tuple[int, int, str]]:
    """
    The differences between old and new, as (start, end, replacement) on
    character positions in `old`. Pure insertions have start == end.
    """
    a = _TOKEN.findall(old)
    b = _TOKEN.findall(new)
    # Character offset where each token of `a` starts (plus the end).
    offsets = [0]
    for tok in a:
        offsets.append(offsets[-1] + len(tok))

    ops = []
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        start, end, text = offsets[i1], offsets[i2], "".join(b[j1:j2])
        if start == end:
            # An insertion like " SQL," placed just before an existing space
            # is the same change as "SQL, " placed just after it. Prefer the
            # second form: it starts with a word, so it takes the formatting
            # of the word that follows (plain "C++,"), not the label before
            # it (bold "Languages:").
            while text and start < len(old) and text[0].isspace() and old[start] == text[0]:
                text = text[1:] + text[0]
                start = end = start + 1
        ops.append((start, end, text))
    return ops


def _rewrite_runs(runs: list[Run], old: str, new: str, highlight: bool = False) -> None:
    texts = [r.text for r in runs]
    if not runs:
        raise EditRefused("This line has no editable text.")

    # Where each run starts in the paragraph text.
    def run_starts():
        starts, pos = [], 0
        for t in texts:
            starts.append(pos)
            pos += len(t)
        return starts

    def run_at(pos: int) -> int | None:
        """Index of the run containing character `pos`, or None."""
        for i, start in enumerate(run_starts()):
            if start <= pos < start + len(texts[i]):
                return i
        return None

    touched: set[int] = set()

    # Apply from the end backwards, so earlier positions stay valid.
    for start, end, replacement in reversed(_char_ops(old, new)):
        # 1. Choose the run that receives the replacement text.
        if start < end:
            target = run_at(start)  # formatting of the text being replaced
        else:
            before, after = run_at(start - 1) if start > 0 else None, run_at(start)
            if before is None or after is None:
                target = before if after is None else after
            elif before == after:
                target = before
            else:
                # At a boundary between two runs: text starting with a space
                # continues the previous run ("Python| and SQL"); otherwise it
                # belongs with the next word ("Tools: |Power BI, Excel").
                target = before if replacement[:1].isspace() else after
        if target is None:
            target = len(runs) - 1
        insert_at = start - run_starts()[target]

        # 2. Delete [start, end) from every run it overlaps.
        if start < end:
            for i, run_start in enumerate(run_starts()):
                lo = max(start, run_start) - run_start
                hi = min(end, run_start + len(texts[i])) - run_start
                if lo < hi:
                    texts[i] = texts[i][:lo] + texts[i][hi:]
                    touched.add(i)

        # 3. Insert the replacement in the target run.
        if replacement:
            texts[target] = texts[target][:insert_at] + replacement + texts[target][insert_at:]
            touched.add(target)

    if "".join(texts) != new:
        raise EditRefused("The change could not be matched to the line's text.")

    for i in touched:
        if not _run_is_safe(runs[i]):
            raise EditRefused(
                "The change touches part of the line that also holds an image, field or "
                "page break, so it was skipped to avoid damaging it."
            )

    for i in sorted(touched):
        if runs[i].text != texts[i]:
            runs[i].text = texts[i]  # \t becomes a tab, \n a line break
        if highlight and texts[i].strip():
            # Only for the preview copy, never the file the user downloads.
            runs[i].font.highlight_color = WD_COLOR_INDEX.YELLOW


# --------------------------------------------------------------------------
# Removing a paragraph
# --------------------------------------------------------------------------
def _remove_paragraph(paragraph: Paragraph) -> str:
    """Remove the line. Returns how it was removed, for the report."""
    p = paragraph._p
    ppr = p.pPr
    if ppr is not None and ppr.find(qn("w:sectPr")) is not None:
        raise EditRefused("This line ends a page-layout section in Word, so it can't be removed safely.")
    if p.find(".//" + qn("w:drawing")) is not None or p.find(".//" + qn("w:pict")) is not None:
        raise EditRefused("This line holds an image or icon, so it wasn't removed. You can delete it in Word.")

    parent = p.getparent()
    # A table cell must always contain at least one paragraph.
    if parent.tag == qn("w:tc") and len(parent.findall(qn("w:p"))) == 1:
        runs = _check_paragraph_is_editable(paragraph)
        for run in runs:
            if not _run_is_safe(run):
                raise EditRefused("This line holds an image or field, so it can't be emptied safely.")
        for run in runs:
            run.text = ""
        return "emptied"  # the cell stays, now blank

    parent.remove(p)
    return "removed"


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------
def apply_changes(
    original: Path, output: Path, changes: list[Change], highlight: bool = False
) -> ApplyResult:
    """
    Write `output` = `original` with the given changes. `original` is untouched.

    highlight=True marks the changed words in yellow. That is only used for
    the "After" preview image, never for the downloadable file.
    """
    doc = Document(str(original))
    result = ApplyResult()

    # Find every target paragraph BEFORE changing anything: removing a
    # paragraph would shift the numbering of everything after it.
    by_id = {pid: para for pid, para, _ in iter_paragraphs(doc)}

    for change in changes:
        record = {
            "suggestion_id": change.suggestion_id,
            "paragraph_id": change.paragraph_id,
            "effect": change.effect,
        }
        paragraph = by_id.get(change.paragraph_id)
        try:
            if paragraph is None:
                raise EditRefused("This line no longer exists in the document.")
            if paragraph.text != change.original_text:
                raise EditRefused("The line in your file doesn't match the suggestion, so it was left alone.")

            if change.effect == "change":
                runs = _check_paragraph_is_editable(paragraph)
                _rewrite_runs(runs, change.original_text, change.final_text or "", highlight)
                record["how"] = "changed"
            elif change.effect == "remove":
                record["how"] = _remove_paragraph(paragraph)
            else:
                raise EditRefused("Unknown change.")
            result.applied.append(record)
        except EditRefused as err:
            result.skipped.append({**record, "why": str(err)})

    doc.save(str(output))
    return result


def mark_original(original: Path, output: Path, applied: list[dict]) -> None:
    """
    A copy of the original for the "Before" preview: lines that change are
    highlighted yellow, lines that will be removed red. Text is not changed.
    """
    doc = Document(str(original))
    by_id = {pid: para for pid, para, _ in iter_paragraphs(doc)}
    for record in applied:
        paragraph = by_id.get(record["paragraph_id"])
        if paragraph is None:
            continue
        colour = WD_COLOR_INDEX.YELLOW if record["how"] == "changed" else WD_COLOR_INDEX.RED
        for run in _text_runs(paragraph):
            if run.text:  # spaces too, so the line reads as one solid mark
                run.font.highlight_color = colour
    doc.save(str(output))
