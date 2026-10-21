"""
Builds a FAKE sample resume for testing. The person and details are invented.

It deliberately mixes the ways real resumes are made, so tests cover them:
- some headings use Word's Heading style, others are just bold text
- bullets use Word's list style
- a line with mixed formatting ("Languages:" bold, the rest normal)
- education inside a table

Run from the backend/ folder to (re)create samples/sample_resume.docx:
    python -m samples.make_sample_resume
"""

from pathlib import Path

from docx import Document
from docx.shared import Pt


def _bold_heading(doc, text):
    """A heading made with bold text only (no Heading style), as many people do."""
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = True
    run.font.size = Pt(13)
    return p


def _label_line(doc, label, rest):
    """A line like 'Languages: Python, SQL' with only the label in bold."""
    p = doc.add_paragraph()
    p.add_run(label).bold = True
    p.add_run(rest)
    return p


def build_sample_resume(path: Path) -> Path:
    doc = Document()

    # Top: name and contact (no heading above it)
    name = doc.add_paragraph()
    name_run = name.add_run("Riya Mehta")
    name_run.bold = True
    name_run.font.size = Pt(20)
    doc.add_paragraph("riya.mehta@example.com | +91 90000 00000 | Pune")
    doc.add_paragraph("")  # an empty line, common in real resumes

    doc.add_heading("Professional Summary", level=1)
    doc.add_paragraph(
        "Analyst with two years of experience turning sales and operations data "
        "into weekly reports for business teams."
    )

    _bold_heading(doc, "SKILLS")
    _label_line(doc, "Languages: ", "Python, SQL")
    _label_line(doc, "Tools: ", "Excel, Power BI, Git")

    doc.add_heading("Work Experience", level=1)
    doc.add_paragraph("Data Analyst, Example Retail Pvt Ltd (2023 to present)")
    doc.add_paragraph(
        "Built a Power BI dashboard used by regional managers to track weekly sales.",
        style="List Bullet",
    )
    doc.add_paragraph(
        "Automated a manual Excel report with Python, cutting preparation time "
        "from a full day to under an hour.",
        style="List Bullet",
    )

    _bold_heading(doc, "Projects")
    _label_line(doc, "Inventory Forecast: ", "Forecasted monthly stock using past sales in Python.")
    _label_line(doc, "Portfolio Website: ", "Personal site built with HTML and CSS.")

    doc.add_heading("Education", level=1)
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "B.Com, Example University"
    table.cell(0, 1).text = "2019 to 2022"
    table.cell(1, 0).text = "Class XII, Example School"
    table.cell(1, 1).text = "2019"

    _bold_heading(doc, "Certifications:")
    doc.add_paragraph("Google Data Analytics Certificate", style="List Bullet")

    path = Path(path)
    doc.save(str(path))
    return path


if __name__ == "__main__":
    out = Path(__file__).parent / "sample_resume.docx"
    build_sample_resume(out)
    print(f"Wrote {out}")
