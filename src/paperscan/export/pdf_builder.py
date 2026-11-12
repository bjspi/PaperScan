"""Build a searchable PDF: one JPEG per page plus an invisible, selectable text layer.

JPEG streams are embedded as-is (no re-encoding). Each OCR line is written in
render mode 3 (invisible) and stretched horizontally to its box, so selecting
text in a PDF viewer highlights the matching area of the scan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path

import pymupdf

from paperscan.ocr.recognition import OcrPage

#: PDF page long edge in points (A4 long edge), so pages print at a natural size.
PAGE_LONG_EDGE_PT = 841.89
#: Invisible text render mode (PDF spec, "neither fill nor stroke").
INVISIBLE_TEXT = 3
#: Fonts of the text layer, by PDF resource name, in order of preference (PyMuPDF built-ins).
#: They are embedded (and subset on save), so every recognised character stays searchable:
#: Helvetica covers Latin, Greek and Cyrillic, Droid Sans Fallback adds Chinese, Japanese and Korean.
TEXT_FONTS = {"PSText": "helv", "PSTextCJK": "cjk"}
#: Font size relative to the line box height; glyph boxes include ascenders and descenders.
FONT_SIZE_PER_LINE_HEIGHT = 0.85
#: Baseline position relative to the line box (from the top).
BASELINE_SHARE = 0.8
#: PyMuPDF save options: drop unused objects and compress streams.
SAVE_OPTIONS = {"garbage": 3, "deflate": True}
PRODUCER = "PaperScan"


@dataclass(frozen=True)
class PdfPage:
    """One page to write: encoded JPEG, its pixel size and optional OCR text."""

    jpeg: bytes
    width: int
    height: int
    ocr: OcrPage | None


@dataclass(frozen=True)
class PdfMetadata:
    """Document information written to the PDF."""

    title: str
    subject: str = ""
    keywords: str = ""
    document_date: date | None = None


def build_pdf(pages: list[PdfPage], metadata: PdfMetadata, target: Path) -> None:
    """Write ``pages`` as one PDF to ``target`` (parent directories are created).

    The PDF is built in memory and written by Python, so a failing disk raises
    :class:`OSError` (PyMuPDF reports its own errors) and leaves no broken file behind.
    """
    data = _pdf_bytes(pages, metadata)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_bytes(data)
    except OSError:
        target.unlink(missing_ok=True)
        raise


def _pdf_bytes(pages: list[PdfPage], metadata: PdfMetadata) -> bytes:
    """Return the finished PDF."""
    with pymupdf.open() as document:
        for page in pages:
            _add_page(document, page)
        document.subset_fonts()
        document.set_metadata(_metadata_dict(metadata))
        return document.tobytes(**SAVE_OPTIONS)


def _add_page(document: pymupdf.Document, page: PdfPage) -> None:
    """Append one page with its image and invisible text."""
    scale = PAGE_LONG_EDGE_PT / max(page.width, page.height)
    pdf_page = document.new_page(width=page.width * scale, height=page.height * scale)
    pdf_page.insert_image(pdf_page.rect, stream=page.jpeg)
    if page.ocr is not None:
        _add_text_layer(pdf_page, page.ocr, page.width / page.ocr.width * scale)


def _add_text_layer(pdf_page: pymupdf.Page, ocr: OcrPage, scale: float) -> None:
    """Write every OCR line invisibly, horizontally stretched to the line's box."""
    page_fonts: set[str] = set()
    for line in ocr.lines:
        font_name = _font_for(line.text)
        font_size = max(1.0, line.height * scale * FONT_SIZE_PER_LINE_HEIGHT)
        natural_width = _font(font_name).text_length(line.text, fontsize=font_size)
        if natural_width <= 0:
            continue
        if font_name not in page_fonts:
            pdf_page.insert_font(fontname=font_name, fontbuffer=_font(font_name).buffer)
            page_fonts.add(font_name)
        origin = pymupdf.Point(line.left * scale, (line.top + line.height * BASELINE_SHARE) * scale)
        stretch = pymupdf.Matrix(line.width * scale / natural_width, 1)
        pdf_page.insert_text(origin, line.text, fontname=font_name, fontsize=font_size, render_mode=INVISIBLE_TEXT, morph=(origin, stretch))


def _font_for(text: str) -> str:
    """Return the first text layer font that has a glyph for every character of ``text``."""
    for font_name in TEXT_FONTS:
        font = _font(font_name)
        if all(font.has_glyph(ord(char)) for char in text if not char.isspace()):
            return font_name
    return next(iter(TEXT_FONTS))


@cache
def _font(font_name: str) -> pymupdf.Font:
    """Return the loaded font behind the resource name ``font_name`` (loaded once)."""
    return pymupdf.Font(TEXT_FONTS[font_name])


def _metadata_dict(metadata: PdfMetadata) -> dict[str, str]:
    """Return PyMuPDF's metadata mapping; dates use the PDF ``D:YYYYMMDD`` format."""
    pdf_date = f"D:{metadata.document_date:%Y%m%d}000000" if metadata.document_date else ""
    return {
        "title": metadata.title,
        "subject": metadata.subject,
        "keywords": metadata.keywords,
        "creator": PRODUCER,
        "producer": PRODUCER,
        "creationDate": pdf_date,
        "modDate": pdf_date,
    }
