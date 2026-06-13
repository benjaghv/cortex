"""
cortex.tools.pdf
─────────────────
Create formatted PDF documents from markdown-like text.

Uses ReportLab's Platypus (flowables) for real layout: word-wrap, page breaks,
headings, bullets, and **bold**/*italic* inline styling — not raw canvas strings.

ReportLab is pure-Python (ships in core deps). The import is still lazy so a
broken/missing install returns a clear [ERROR] instead of crashing the agent.
"""

from __future__ import annotations

import html
import os
import re
from pathlib import Path

SCHEMA = {
    "type": "function",
    "function": {
        "name": "pdf",
        "description": (
            "Create a formatted PDF document. Use for ANY request to 'make a PDF', "
            "'export to PDF', 'create a .pdf', or a printable report/invoice/letter. "
            "Pass markdown-like content: headings with #/##/###, - bullets, "
            "1. numbered lists, **bold**, *italic*, and blank lines for paragraphs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Output file path ending in .pdf. Example: ~/Desktop/report.pdf",
                },
                "title": {
                    "type": "string",
                    "description": "Document title, rendered large at the top.",
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Body in markdown-like text:\n"
                        "  # Heading 1\n  ## Heading 2\n  ### Heading 3\n"
                        "  - bullet item\n  1. numbered item\n"
                        "  **bold**, *italic*\n  blank line = new paragraph"
                    ),
                },
            },
            "required": ["path", "content"],
        },
    },
}


def _resolve(path: str) -> Path:
    p = Path(os.path.expandvars(path)).expanduser()
    if p.suffix.lower() != ".pdf":
        p = p.with_suffix(".pdf")
    return p.resolve()


def _inline(text: str) -> str:
    """Convert **bold**/*italic*/`code` to ReportLab mini-HTML, escaping the rest."""
    # Escape first so user content can't inject tags, then add our markup.
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"`(.+?)`", r'<font face="Courier">\1</font>', text)
    return text


def execute(path: str, content: str, title: str = "", **_) -> str:
    try:
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import mm
            from reportlab.lib.enums import TA_LEFT
            from reportlab.platypus import (
                SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem,
            )
        except ImportError:
            return ("[ERROR] pdf: ReportLab no está instalado. "
                    "Instálalo con:  pip install reportlab")

        p = _resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        styles = getSampleStyleSheet()
        body = ParagraphStyle("body", parent=styles["BodyText"],
                              fontSize=11, leading=16, alignment=TA_LEFT, spaceAfter=6)
        h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=18, leading=22, spaceBefore=12)
        h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=15, leading=19, spaceBefore=10)
        h3 = ParagraphStyle("h3", parent=styles["Heading3"], fontSize=13, leading=17, spaceBefore=8)
        title_style = ParagraphStyle("title", parent=styles["Title"], fontSize=24, leading=28, spaceAfter=18)

        doc = SimpleDocTemplate(
            str(p), pagesize=A4,
            leftMargin=22 * mm, rightMargin=22 * mm,
            topMargin=22 * mm, bottomMargin=22 * mm,
            title=title or p.stem,
        )

        flow: list = []
        if title:
            flow.append(Paragraph(_inline(title), title_style))

        # Group consecutive bullet / numbered lines into a single list flowable.
        pending: list = []
        pending_kind: "str | None" = None  # 'bullet' | 'number'

        def flush_list():
            nonlocal pending, pending_kind
            if not pending:
                return
            items = [ListItem(Paragraph(_inline(t), body), leftIndent=6) for t in pending]
            bullet = "bullet" if pending_kind == "bullet" else "1"
            flow.append(ListFlowable(items, bulletType=bullet, leftIndent=12))
            flow.append(Spacer(1, 4))
            pending, pending_kind = [], None

        for raw in content.splitlines():
            line = raw.rstrip()
            stripped = line.strip()

            m_bullet = re.match(r"^[-*]\s+(.*)$", stripped)
            m_number = re.match(r"^\d+[.)]\s+(.*)$", stripped)

            if m_bullet:
                if pending_kind not in (None, "bullet"):
                    flush_list()
                pending_kind = "bullet"
                pending.append(m_bullet.group(1))
                continue
            if m_number:
                if pending_kind not in (None, "number"):
                    flush_list()
                pending_kind = "number"
                pending.append(m_number.group(1))
                continue

            flush_list()  # any non-list line ends the current list

            if not stripped:
                flow.append(Spacer(1, 6))
            elif stripped.startswith("### "):
                flow.append(Paragraph(_inline(stripped[4:]), h3))
            elif stripped.startswith("## "):
                flow.append(Paragraph(_inline(stripped[3:]), h2))
            elif stripped.startswith("# "):
                flow.append(Paragraph(_inline(stripped[2:]), h1))
            else:
                flow.append(Paragraph(_inline(stripped), body))

        flush_list()

        if not flow:
            flow.append(Paragraph("(documento vacío)", body))

        doc.build(flow)
        kb = max(1, p.stat().st_size // 1024)
        return f"Created {p}  ({kb} KB)"

    except Exception as e:
        return f"[ERROR] pdf: {type(e).__name__}: {e}"
