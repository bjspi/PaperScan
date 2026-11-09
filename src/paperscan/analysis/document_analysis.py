"""AI document analysis: title, document date, summary and optional OCR correction.

One request per document sends downscaled page images plus Tesseract's
numbered lines to a vision model (see ``vision``) and expects one JSON object
back. Line corrections only replace text; positions always stay Tesseract's.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from paperscan.analysis.vision import ProviderConfig, ask_vision_json, extract_json
from paperscan.ocr.recognition import OcrPage

log = logging.getLogger(__name__)

#: Upper bound for AI-suggested titles before file name truncation.
MAX_TITLE_LENGTH = 150


@dataclass(frozen=True)
class AnalysisRequest:
    """Input for one document analysis.

    Attributes:
        page_jpegs: Downscaled page images, in page order.
        ocr_pages: OCR result per page (None where OCR was skipped).
        fallback_date: Capture date used when the document shows no date.
        correct_text: Also ask for corrected OCR line texts.
        language_hint: Preferred language for title and summary (e.g. "German").
    """

    page_jpegs: tuple[bytes, ...]
    ocr_pages: tuple[OcrPage | None, ...]
    fallback_date: date | None
    correct_text: bool
    language_hint: str


@dataclass(frozen=True)
class DocumentAnalysis:
    """What the model found out about the document."""

    title: str
    document_date: date | None
    summary: str = ""
    keywords: tuple[str, ...] = ()
    #: Corrected line texts per page index: {page: {line: text}}.
    corrections: dict[int, dict[int, str]] = field(default_factory=dict)


def analyze_document(request: AnalysisRequest, config: ProviderConfig) -> DocumentAnalysis:
    """Run the analysis with the configured backend; ``NONE`` returns a neutral result."""
    if not config.enabled:
        return DocumentAnalysis(title="", document_date=request.fallback_date)
    data = ask_vision_json(build_prompt(request), request.page_jpegs, config)
    return analysis_from_json(data, request.fallback_date)


# --- Prompt -------------------------------------------------------------------------------------


PROMPT_TEMPLATE = """\
You analyse a scanned document. Look at the page images and the OCR lines below.
{images}Answer with ONE JSON object and nothing else, with these keys:
- "title": a specific, descriptive document title in {language} for use as a file name \
(sender + document type + subject, at most {max_title} characters, no date, no file extension).
- "document_date": the date printed on the document (invoice, letter or issue date) as "YYYY-MM-DD", or null if none is visible.
- "summary": two or three sentences in {language} on what the document is about.
- "keywords": up to 8 short keywords.
{correction_rule}
The photo was taken on {capture_date}.

{ocr_lines}"""

CORRECTION_RULE = """\
- "corrections": an object keyed by page number (from 1, as a string); each value maps an OCR line number (as a string) \
to the corrected text of exactly that line, read from the image. Keep line boundaries, never merge or split lines, \
omit lines that are already right. Example: {"1": {"4": "Total amount 128.40 EUR"}}."""

NO_CORRECTION_RULE = '- "corrections": always an empty object {}.'


def build_prompt(request: AnalysisRequest, image_names: list[str] | None = None) -> str:
    """Return the instruction text, including the numbered OCR lines of every page."""
    return PROMPT_TEMPLATE.format(
        images=f"The page images are the files {', '.join(image_names)} (in page order).\n" if image_names else "",
        language=request.language_hint,
        max_title=MAX_TITLE_LENGTH,
        correction_rule=CORRECTION_RULE if request.correct_text else NO_CORRECTION_RULE,
        capture_date=f"{request.fallback_date:%Y-%m-%d}" if request.fallback_date else "unknown",
        ocr_lines=_ocr_section(request),
    )


def _ocr_section(request: AnalysisRequest) -> str:
    """Return the numbered OCR lines of all pages."""
    parts = []
    for number, page in enumerate(request.ocr_pages, start=1):
        text = page.numbered_text() if page else "(no OCR)"
        parts.append(f"=== OCR page {number} ===\n{text}")
    return "\n\n".join(parts)


# --- Answer parsing ------------------------------------------------------------------------------


def parse_answer(answer: str, fallback_date: date | None) -> DocumentAnalysis:
    """Parse the model's raw text answer."""
    return analysis_from_json(extract_json(answer), fallback_date)


def analysis_from_json(data: dict[str, Any], fallback_date: date | None) -> DocumentAnalysis:
    """Build the result from the model's JSON object; missing or invalid fields get defaults."""
    return DocumentAnalysis(
        title=str(data.get("title") or "")[:MAX_TITLE_LENGTH].strip(),
        document_date=_parse_date(data.get("document_date")) or fallback_date,
        summary=str(data.get("summary") or "").strip(),
        keywords=tuple(str(keyword) for keyword in data.get("keywords") or [] if str(keyword).strip()),
        corrections=_parse_corrections(data.get("corrections")),
    )


def _parse_date(value: object) -> date | None:
    """Parse an ISO date; anything else becomes None."""
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _parse_corrections(value: object) -> dict[int, dict[int, str]]:
    """Convert {"1": {"0": "text"}} (1-based pages) into {0: {0: "text"}}."""
    if not isinstance(value, dict):
        return {}
    corrections: dict[int, dict[int, str]] = {}
    for page_key, lines in value.items():
        if not str(page_key).isdigit() or not isinstance(lines, dict):
            continue
        page_lines = {int(key): str(text) for key, text in lines.items() if str(key).isdigit() and isinstance(text, str)}
        corrections[int(page_key) - 1] = page_lines
    return corrections
