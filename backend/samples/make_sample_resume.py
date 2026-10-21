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


def build_two_column_header_resume(path: Path) -> Path:
    """
    A FAKE resume shaped like many PDF-to-Word conversions:
    - empty lines at the top
    - the "SUMMARY" label stored BEFORE the name in the file
    - the name in Heading 1 and a long tagline in Heading 2
    - the top block in two columns, ended by a section break
    """
    from docx.enum.section import WD_SECTION
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    doc = Document()
    for _ in range(3):
        doc.add_paragraph("")

    label = doc.add_paragraph()
    label.add_run("SUMMARY").bold = True

    doc.add_heading("ARJUN RAO", level=1)
    doc.add_heading(
        "Computer Science Undergraduate and Full-Stack Developer", level=2
    )
    doc.add_paragraph("Bengaluru | arjun.rao@example.com | github.com/example")

    # Make the first Word section two columns, then start a new continuous
    # section for the rest of the resume.
    cols = OxmlElement("w:cols")
    cols.set(qn("w:num"), "2")
    doc.sections[0]._sectPr.append(cols)
    doc.add_section(WD_SECTION.CONTINUOUS)

    doc.add_paragraph(
        "Developer who builds web products end to end, from database design "
        "to deployment."
    )
    doc.add_heading("EDUCATION", level=2)
    doc.add_paragraph("B.Tech Computer Science, Example Institute")
    doc.add_heading("COMMUNITY & LEADERSHIP", level=2)
    doc.add_paragraph("Coding Club | Lead")

    path = Path(path)
    doc.save(str(path))
    return path


if __name__ == "__main__":
    out = Path(__file__).parent / "sample_resume.docx"
    build_sample_resume(out)
    print(f"Wrote {out}")
