"""Render a Markdown report into a .docx file (bytes) for Feishu attachment.

Supports the subset the agent emits: headings (#/##/###), paragraphs with
**bold**, bullet lists, blockquotes, horizontal rules and pipe tables.
"""

from __future__ import annotations

import io
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

FONT = "Microsoft JhengHei"
_BOLD_RE = re.compile(r"(\*\*[^*]+\*\*)")
_HEADING_SIZES = {0: 18, 1: 15, 2: 13}


def _set_font(run, size: float | None = None, bold: bool | None = None,
              color: RGBColor | None = None) -> None:
    run.font.name = FONT
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), FONT)
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if color is not None:
        run.font.color.rgb = color


def _add_inline(par, text: str, size: float = 11, italic: bool = False,
                color: RGBColor | None = None) -> None:
    text = text.replace("`", "")
    for piece in _BOLD_RE.split(text):
        if not piece:
            continue
        bold = piece.startswith("**") and piece.endswith("**")
        run = par.add_run(piece[2:-2] if bold else piece)
        _set_font(run, size=size, bold=bold, color=color)
        run.italic = italic


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _add_table(doc, rows: list[list[str]]) -> None:
    ncols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=ncols)
    table.style = "Table Grid"
    for i, row in enumerate(rows):
        for j in range(ncols):
            cell = table.cell(i, j)
            cell.text = ""
            par = cell.paragraphs[0]
            text = row[j] if j < len(row) else ""
            if i == 0:
                text = f"**{text.strip('*')}**" if text else text
            _add_inline(par, text, size=10)
    doc.add_paragraph()


def markdown_to_docx(md: str) -> bytes:
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(11)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)

    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if stripped.startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = lines[i].strip()
                if not re.fullmatch(r"\|[\s:\-|]+\|?", row):
                    block.append(_cells(row))
                i += 1
            if block:
                _add_table(doc, block)
            continue
        i += 1
        if not stripped or re.fullmatch(r"-{3,}|\*{3,}", stripped):
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = min(len(m.group(1)) - 1, 2)
            h = doc.add_heading(level=level)
            if level == 0:
                h.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = h.add_run(m.group(2).replace("**", ""))
            _set_font(run, size=_HEADING_SIZES[level], bold=True,
                      color=RGBColor(0, 0, 0))
            continue
        if re.match(r"^[-*]\s+", stripped):
            par = doc.add_paragraph(style="List Bullet")
            _add_inline(par, re.sub(r"^[-*]\s+", "", stripped))
            continue
        if stripped.startswith(">"):
            par = doc.add_paragraph()
            _add_inline(par, stripped.lstrip("> "), size=10, italic=True,
                        color=RGBColor(0x59, 0x59, 0x59))
            continue
        par = doc.add_paragraph()
        par.paragraph_format.space_after = Pt(6)
        _add_inline(par, stripped)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
