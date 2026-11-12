"""Document workflow: photos in, one searchable and well-named PDF out.

The export runs in two phases so the GUI can let the user review the AI's
suggestion before anything is written:

1. :func:`prepare_export` renders every page (crop, rotate, resize, filter),
   encodes it with jpegli, runs OCR and the optional AI analysis, and returns an
   :class:`ExportDraft`.
2. :func:`write_export` applies the confirmed title and date and writes the PDF.

The CLI simply runs both phases back to back.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path

import numpy as np

from paperscan.analysis.document_analysis import AnalysisRequest, DocumentAnalysis, analyze_document
from paperscan.analysis.vision import PROVIDER_LABELS
from paperscan.capture.photo_files import capture_time, load_photo
from paperscan.export.jpeg_encoding import encode_jpeg
from paperscan.export.naming import FALLBACK_TITLE, FileDateStyle, clean_pdf_name, pdf_file_name, unique_path
from paperscan.export.pdf_builder import PdfMetadata, PdfPage, build_pdf
from paperscan.imaging.border_detection import Detection, detect_page
from paperscan.imaging.filters import FilterSettings, apply_filter
from paperscan.imaging.geometry import Quad, Rotation, limit_long_edge, rotate, warp_perspective
from paperscan.ocr.recognition import OcrPage, recognize
from paperscan.settings import AppSettings

log = logging.getLogger(__name__)

#: (message, fraction done 0..1)
ProgressCallback = Callable[[str, float], None]


class ExportCancelledError(RuntimeError):
    """Raised when the user cancels a running export."""


# --- Pages -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PageEdit:
    """User decisions for one page; ``quad`` is in source pixel coordinates."""

    quad: Quad
    rotation: Rotation = Rotation.NONE
    filter: FilterSettings = field(default_factory=FilterSettings)


@dataclass(frozen=True)
class ScanPage:
    """A source photo and how to turn it into a page."""

    source: Path
    captured_at: datetime
    width: int
    height: int
    edit: PageEdit
    detection: Detection | None = None

    def with_edit(self, edit: PageEdit) -> ScanPage:
        """Return a copy with other user decisions."""
        return replace(self, edit=edit)


def open_page(path: Path, settings: AppSettings, *, detect: bool = True) -> ScanPage:
    """Load a photo, detect its page outline and apply the default filter."""
    rgb = load_photo(path)
    height, width = rgb.shape[:2]
    detection = detect_page(rgb, settings.model_path) if detect else None
    quad = detection.quad if detection else Quad.full_image(width, height)
    edit = PageEdit(quad=quad, filter=settings.default_filter)
    if detection is not None:
        log.info("Opened %s (%dx%d): page found by %s, confidence %.2f", path.name, width, height, detection.source, detection.confidence)
    return ScanPage(source=path, captured_at=capture_time(path), width=width, height=height, edit=edit, detection=detection)


def flatten_page(source_rgb: np.ndarray, edit: PageEdit, max_edge: int | None) -> np.ndarray:
    """Crop, straighten, rotate and downscale one page (no filter)."""
    upright = rotate(warp_perspective(source_rgb, edit.quad), edit.rotation)
    return limit_long_edge(upright, max_edge)


def render_page(source_rgb: np.ndarray, edit: PageEdit, max_edge: int | None) -> np.ndarray:
    """Flatten one page and apply its filter."""
    return apply_filter(flatten_page(source_rgb, edit, max_edge), edit.filter)


# --- Export ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportDraft:
    """Everything needed to write the PDF, plus the AI's suggestions for review."""

    pages: tuple[PdfPage, ...]
    analysis: DocumentAnalysis
    capture_date: date
    #: Small page images kept for a later AI request from the review dialog.
    ai_images: tuple[bytes, ...] = ()


@dataclass(frozen=True)
class ExportChoice:
    """The confirmed title, date, summary and file name for the PDF."""

    title: str
    document_date: date | None
    file_name: str
    summary: str = ""
    keywords: tuple[str, ...] = ()


class _Progress:
    """Reports progress and checks for cancellation between steps."""

    def __init__(self, callback: ProgressCallback | None, cancel: threading.Event | None, total_steps: int) -> None:
        self._callback = callback
        self._cancel = cancel
        self._total = max(1, total_steps)
        self._done = 0

    def step(self, message: str) -> None:
        """Announce the next step; raises :class:`ExportCancelledError` if cancelled."""
        self.check_cancelled()
        if self._callback is not None:
            self._callback(message, self._done / self._total)
        self._done += 1

    def check_cancelled(self) -> None:
        """Raise :class:`ExportCancelledError` if the user cancelled (also after the last step)."""
        if self._cancel is not None and self._cancel.is_set():
            raise ExportCancelledError("Export cancelled")


def prepare_export(
    pages: list[ScanPage], settings: AppSettings, progress: ProgressCallback | None = None, cancel: threading.Event | None = None
) -> ExportDraft:
    """Render, encode and OCR every page, then ask the AI (if configured) for title and date."""
    if not pages:
        raise ValueError("No pages to export")
    tracker = _Progress(progress, cancel, total_steps=len(pages) * 2 + 1)
    pdf_pages: list[PdfPage] = []
    ai_images: list[bytes] = []
    for number, page in enumerate(pages, start=1):
        tracker.step(f"Rendering page {number}/{len(pages)}")
        rendered = render_page(load_photo(page.source), page.edit, settings.max_edge or None)
        jpeg = encode_jpeg(rendered, settings.jpeg)
        ai_images.append(ai_image(rendered, settings))
        tracker.step(f"Recognising text with Tesseract (page {number}/{len(pages)})")
        ocr = recognize(rendered, settings.ocr.languages, settings.ocr.tesseract_path) if settings.ocr.enabled else None
        log.info(
            "Page %d/%d: %dx%d px, %d KB JPEG, %s",
            number,
            len(pages),
            rendered.shape[1],
            rendered.shape[0],
            len(jpeg) // 1024,
            f"{len(ocr.lines)} OCR lines" if ocr else "no OCR",
        )
        pdf_pages.append(PdfPage(jpeg=jpeg, width=rendered.shape[1], height=rendered.shape[0], ocr=ocr))
    capture_date = min(page.captured_at for page in pages).date()
    tracker.step(_analysis_step(settings))
    analysis = _analyse(pdf_pages, ai_images, capture_date, settings)
    tracker.check_cancelled()
    pages_out = tuple(_apply_corrections(pdf_pages, analysis))
    return ExportDraft(pages=pages_out, analysis=analysis, capture_date=capture_date, ai_images=tuple(ai_images))


def prepare_documents(
    documents: list[list[ScanPage]],
    settings: AppSettings,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> list[ExportDraft]:
    """Prepare several documents one after another (see :func:`prepare_export`); progress spans all of them."""
    return [
        prepare_export(pages, settings, _document_progress(progress, number, len(documents)), cancel)
        for number, pages in enumerate(documents, start=1)
    ]


def _document_progress(progress: ProgressCallback | None, number: int, total: int) -> ProgressCallback | None:
    """Map one document's progress onto the whole run and name the document when there are several."""
    if progress is None or total == 1:
        return progress
    report = progress

    def scaled(message: str, fraction: float) -> None:
        report(f"Document {number}/{total} · {message}", (number - 1 + fraction) / total)

    return scaled


def reanalyse(draft: ExportDraft, settings: AppSettings) -> ExportDraft:
    """Ask the configured AI again for an already prepared document (e.g. from the review dialog)."""
    analysis = _analyse(list(draft.pages), list(draft.ai_images), draft.capture_date, settings)
    return replace(draft, pages=tuple(_apply_corrections(list(draft.pages), analysis)), analysis=analysis)


def write_export(draft: ExportDraft, choice: ExportChoice, output_dir: Path) -> Path:
    """Write the PDF under the chosen file name (made unique) and return its path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    target = unique_path(output_dir, clean_pdf_name(choice.file_name))
    metadata = PdfMetadata(
        title=choice.title, subject=choice.summary, keywords=", ".join(choice.keywords), document_date=choice.document_date
    )
    build_pdf(list(draft.pages), metadata, target)
    log.info("Wrote %s (%d pages, %d KB)", target, len(draft.pages), target.stat().st_size // 1024)
    return target


def suggested_choice(draft: ExportDraft, style: FileDateStyle) -> ExportChoice:
    """Return the AI's suggestion, falling back to the capture date and a generic title."""
    analysis = draft.analysis
    return choice_for(
        title=analysis.title or FALLBACK_TITLE,
        document_date=analysis.document_date or draft.capture_date,
        style=style,
        summary=analysis.summary,
        keywords=analysis.keywords,
    )


def choice_for(
    *, title: str, document_date: date | None, style: FileDateStyle, summary: str = "", keywords: tuple[str, ...] = ()
) -> ExportChoice:
    """Build a choice whose file name is derived from title and date."""
    file_name = pdf_file_name(title, document_date, style)
    return ExportChoice(title=title, document_date=document_date, file_name=file_name, summary=summary, keywords=keywords)


def ai_image(rendered: np.ndarray, settings: AppSettings) -> bytes:
    """Return a small JPEG of a rendered page for AI requests."""
    return encode_jpeg(limit_long_edge(rendered, settings.ai.image_edge), replace(settings.jpeg, use_jpegli=False))


def _analysis_step(settings: AppSettings) -> str:
    """Return the progress text of the final step, naming the AI that reads the pages."""
    provider = settings.ai.provider
    if not provider.enabled:
        return "Finishing the document"
    return f"AI vision: {PROVIDER_LABELS.get(provider.provider, 'the AI')} reads the document (title, date, text)"


def _analyse(pdf_pages: list[PdfPage], ai_images: list[bytes], capture_date: date, settings: AppSettings) -> DocumentAnalysis:
    """Run the configured AI analysis."""
    request = AnalysisRequest(
        page_jpegs=tuple(ai_images),
        ocr_pages=tuple(page.ocr for page in pdf_pages),
        fallback_date=capture_date,
        correct_text=settings.ai.correct_ocr_text and settings.ocr.enabled,
        language_hint=settings.ai.language_hint,
    )
    return analyze_document(request, settings.ai.provider)


def _apply_corrections(pdf_pages: list[PdfPage], analysis: DocumentAnalysis) -> list[PdfPage]:
    """Replace OCR line texts with the AI's corrections, keeping all positions."""
    corrected: list[PdfPage] = []
    for index, page in enumerate(pdf_pages):
        lines = analysis.corrections.get(index)
        ocr: OcrPage | None = page.ocr.with_line_texts(lines) if page.ocr and lines else page.ocr
        corrected.append(replace(page, ocr=ocr))
    return corrected
