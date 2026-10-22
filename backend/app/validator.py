"""
Checks the refined document against the original before the user can download it.

The editor says what it did. The validator doesn't take its word for it:
it reopens both files from disk and compares them independently.

Checks
------
1. The refined file opens.
2. Text: every paragraph is exactly what we expect. Approved changes have
   their new text, removed lines are gone, and every other paragraph is
   identical, in the same order. Nothing disappeared, nothing extra appeared.
3. Formatting of untouched lines: each unchanged paragraph's underlying
   Word XML is identical to the original (style, spacing, bold, links...).
4. Formatting of changed lines: the paragraph settings (style, bullets,
   indent, alignment) are identical; only the runs' text differs.
5. Structure: same number of tables, images, sections, and the same set
   of internal files (headers, footers, styles, images).
"""

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

from app.docx_reader import iter_paragraphs


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ValidationReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "checks": [c.__dict__ for c in self.checks]}


def _xml(element) -> bytes:
    return etree.tostring(element, method="c14n")


def _ppr_xml(paragraph) -> bytes:
    ppr = paragraph._p.pPr
    return b"" if ppr is None else _xml(ppr)


def _structure(doc) -> dict:
    body = doc.element.body
    return {
        "tables": len(body.findall(".//" + qn("w:tbl"))),
        "images": len(body.findall(".//" + qn("w:drawing"))) + len(body.findall(".//" + qn("w:pict"))),
        "sections": len(doc.sections),
    }


def validate(original: Path, refined: Path, applied: list[dict], final_texts: dict[str, str]) -> ValidationReport:
    """
    `applied`: the editor's applied records (paragraph_id, how).
    `final_texts`: paragraph_id -> the approved new text, for changed lines.
    """
    report = ValidationReport()

    # 1. Opens
    try:
        new_doc = Document(str(refined))
        with zipfile.ZipFile(refined) as z:
            if z.testzip() is not None:
                raise ValueError("damaged zip entry")
    except Exception:
        report.checks.append(Check("The refined file opens", False, "The new file could not be opened."))
        return report
    report.checks.append(Check("The refined file opens", True))

    old_doc = Document(str(original))
    how = {a["paragraph_id"]: a["how"] for a in applied}

    # What we expect, paragraph by paragraph, in order.
    expected = []  # (paragraph_id, expected_text, original_paragraph)
    for pid, para, _ in iter_paragraphs(old_doc):
        action = how.get(pid)
        if action == "removed":
            continue
        if action == "emptied":
            expected.append((pid, "", para))
        elif action == "changed":
            expected.append((pid, final_texts[pid], para))
        else:
            expected.append((pid, para.text, para))

    actual = [para for _, para, _ in iter_paragraphs(new_doc)]

    # 2. Text
    text_problems = []
    if len(actual) != len(expected):
        text_problems.append(
            f"Expected {len(expected)} paragraphs but found {len(actual)}."
        )
    for (pid, want, _), got in zip(expected, actual):
        if got.text != want:
            text_problems.append(f"{pid} has unexpected text.")
    report.checks.append(
        Check(
            "Only approved text changed, nothing went missing",
            not text_problems,
            "; ".join(text_problems[:5]),
        )
    )

    # 3 and 4. Formatting
    untouched_problems, changed_problems = [], []
    for (pid, _, old_para), new_para in zip(expected, actual):
        if pid in how:
            if _ppr_xml(old_para) != _ppr_xml(new_para):
                changed_problems.append(pid)
        elif _xml(old_para._p) != _xml(new_para._p):
            untouched_problems.append(pid)
    report.checks.append(
        Check(
            "Untouched lines are identical, formatting included",
            not untouched_problems,
            ("Differences in " + ", ".join(untouched_problems[:5])) if untouched_problems else "",
        )
    )
    report.checks.append(
        Check(
            "Edited lines keep their style, bullets and spacing",
            not changed_problems,
            ("Paragraph settings changed in " + ", ".join(changed_problems[:5])) if changed_problems else "",
        )
    )

    # 5. Structure
    old_s, new_s = _structure(old_doc), _structure(new_doc)
    with zipfile.ZipFile(original) as a, zipfile.ZipFile(refined) as b:
        same_parts = set(a.namelist()) == set(b.namelist())
    structure_ok = old_s == new_s and same_parts
    detail = ""
    if old_s != new_s:
        detail = f"Before {old_s}, after {new_s}."
    elif not same_parts:
        detail = "The set of internal files (headers, styles, images) changed."
    report.checks.append(
        Check("Tables, images, sections and headers are all still there", structure_ok, detail)
    )

    return report
