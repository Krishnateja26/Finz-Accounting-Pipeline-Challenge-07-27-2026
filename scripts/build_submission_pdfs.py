from __future__ import annotations

import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer


ROOT = Path(__file__).resolve().parent.parent
INPUTS = [
    ROOT / "deliverables" / "final_validation.md",
    ROOT / "deliverables" / "internal_pnl.md",
    ROOT / "deliverables" / "ai_usage_note.md",
    ROOT / "deliverables" / "submission_summary.md",
]
OUTPUT_DIR = ROOT / "output" / "pdf"


def esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def inline_markup(text: str) -> str:
    text = esc(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`(.+?)`", r"<font face='Courier'>\1</font>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<a href='\2'>\1</a>", text)
    return text


def paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(inline_markup(text), style)


def build_story(md_text: str, styles: dict[str, ParagraphStyle]):
    lines = md_text.splitlines()
    story = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        if not stripped:
            story.append(Spacer(1, 0.12 * inch))
            i += 1
            continue

        if stripped.startswith("```"):
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i].rstrip("\n"))
                i += 1
            story.append(Preformatted("\n".join(block), styles["code"]))
            story.append(Spacer(1, 0.12 * inch))
            i += 1
            continue

        if stripped.startswith("# "):
            story.append(paragraph(stripped[2:], styles["h1"]))
            story.append(Spacer(1, 0.12 * inch))
            i += 1
            continue
        if stripped.startswith("## "):
            story.append(paragraph(stripped[3:], styles["h2"]))
            story.append(Spacer(1, 0.09 * inch))
            i += 1
            continue
        if stripped.startswith("### "):
            story.append(paragraph(stripped[4:], styles["h3"]))
            story.append(Spacer(1, 0.07 * inch))
            i += 1
            continue

        if stripped.startswith("|") and "|" in stripped[1:]:
            block = [stripped]
            i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i].rstrip())
                i += 1
            story.append(Preformatted("\n".join(block), styles["table"]))
            story.append(Spacer(1, 0.12 * inch))
            continue

        if stripped.startswith(("- ", "* ")):
            story.append(paragraph("- " + stripped[2:].lstrip(), styles["bullet"]))
            i += 1
            continue

        if stripped[:1] in ("\u2022", "\u25cf"):
            story.append(paragraph("- " + stripped[1:].lstrip(), styles["bullet"]))
            i += 1
            continue

        story.append(paragraph(stripped, styles["body"]))
        i += 1

    return story


def build_pdf(md_path: Path, pdf_path: Path) -> None:
    md_text = md_path.read_text(encoding="utf-8")
    styles_base = getSampleStyleSheet()
    styles = {
        "h1": ParagraphStyle(
            "h1",
            parent=styles_base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            spaceAfter=6,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#111111"),
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=styles_base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            spaceAfter=4,
            textColor=colors.HexColor("#111111"),
        ),
        "h3": ParagraphStyle(
            "h3",
            parent=styles_base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            spaceAfter=3,
            textColor=colors.HexColor("#111111"),
        ),
        "body": ParagraphStyle(
            "body",
            parent=styles_base["BodyText"],
            fontName="Helvetica",
            fontSize=10.2,
            leading=13,
            spaceAfter=2,
            textColor=colors.HexColor("#222222"),
        ),
        "bullet": ParagraphStyle(
            "bullet",
            parent=styles_base["BodyText"],
            fontName="Helvetica",
            fontSize=10.2,
            leading=13,
            leftIndent=14,
            firstLineIndent=-10,
            spaceAfter=1,
            textColor=colors.HexColor("#222222"),
        ),
        "code": ParagraphStyle(
            "code",
            parent=styles_base["Code"],
            fontName="Courier",
            fontSize=8.6,
            leading=10,
            backColor=colors.HexColor("#F6F7F9"),
            borderPadding=6,
            leftIndent=4,
            rightIndent=4,
        ),
        "table": ParagraphStyle(
            "table",
            parent=styles_base["Code"],
            fontName="Courier",
            fontSize=8.4,
            leading=10,
            backColor=colors.HexColor("#FBFBFB"),
            borderPadding=4,
            leftIndent=2,
            rightIndent=2,
        ),
    }

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
    )
    story = build_story(md_text, styles)
    doc.build(story)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for md_path in INPUTS:
        pdf_path = OUTPUT_DIR / f"{md_path.stem}.pdf"
        build_pdf(md_path, pdf_path)
        print(pdf_path)


if __name__ == "__main__":
    main()
