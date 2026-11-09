"""Text recognition with word positions, used for the searchable PDF text layer.

Tesseract runs locally and reports every word with its bounding box, which is
exactly what an invisible, selectable PDF text layer needs. Vision LLMs read
text better but cannot return reliable pixel coordinates, so they may correct
the text of Tesseract's numbered lines (see ``analysis``) while the line boxes
stay Tesseract's.
"""

from __future__ import annotations

import csv
import io
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from paperscan.app_paths import TESSDATA_DIR
from paperscan.platform_tools import Tool, require_tool, run_tool

log = logging.getLogger(__name__)

#: Tesseract timeout per page, in seconds.
OCR_TIMEOUT = 300.0
#: First column name of Tesseract's TSV header.
TSV_HEADER_START = "level\t"
#: Tesseract TSV level of word rows.
TSV_WORD_LEVEL = 5
#: Words below this confidence (0..100) are dropped as noise.
MIN_WORD_CONFIDENCE = 30.0
#: Page segmentation mode "fully automatic page segmentation" (Tesseract default).
PAGE_SEGMENTATION_MODE = "3"


@dataclass(frozen=True)
class OcrWord:
    """One recognised word in page pixel coordinates."""

    text: str
    left: int
    top: int
    width: int
    height: int
    confidence: float
    line_key: tuple[int, int, int]  # (block, paragraph, line) as reported by Tesseract


@dataclass(frozen=True)
class OcrLine:
    """One text line: the union box of its words and its text (possibly AI-corrected)."""

    text: str
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class OcrPage:
    """All text lines recognised on one page image, in reading order."""

    width: int
    height: int
    lines: tuple[OcrLine, ...]

    def numbered_text(self) -> str:
        """Return the lines prefixed with their index, as handed to the AI corrector."""
        return "\n".join(f"{index}: {line.text}" for index, line in enumerate(self.lines))

    def with_line_texts(self, texts: dict[int, str]) -> OcrPage:
        """Return a copy where line ``index`` gets ``texts[index]``; missing or blank entries keep the original."""
        lines = [self._replace_text(line, texts.get(index, "")) for index, line in enumerate(self.lines)]
        return OcrPage(self.width, self.height, tuple(lines))

    @staticmethod
    def _replace_text(line: OcrLine, text: str) -> OcrLine:
        """Return ``line`` with ``text`` unless ``text`` is blank."""
        if not text.strip():
            return line
        return OcrLine(text.strip(), line.left, line.top, line.width, line.height)


def group_lines(words: list[OcrWord]) -> list[OcrLine]:
    """Merge the words Tesseract assigned to the same line into one line box."""
    grouped: dict[tuple[int, int, int], list[OcrWord]] = {}
    for word in words:
        grouped.setdefault(word.line_key, []).append(word)
    return [_line_from_words(line_words) for line_words in grouped.values()]


def _line_from_words(words: list[OcrWord]) -> OcrLine:
    """Return the union box of ``words`` with their texts joined by spaces."""
    left = min(word.left for word in words)
    top = min(word.top for word in words)
    right = max(word.left + word.width for word in words)
    bottom = max(word.top + word.height for word in words)
    return OcrLine(" ".join(word.text for word in words), left, top, right - left, bottom - top)


def recognize(rgb: np.ndarray, languages: str, tesseract_path: str = "") -> OcrPage:
    """Run Tesseract on an RGB image and return the positioned text lines."""
    tesseract = require_tool(Tool.TESSERACT, tesseract_path)
    height, width = rgb.shape[:2]
    with tempfile.TemporaryDirectory(prefix="csocr-") as tmp:
        image_path = Path(tmp) / "page.png"
        cv2.imwrite(str(image_path), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        command = [
            str(tesseract),
            str(image_path),
            "stdout",
            *_tessdata_arguments(languages),
            "-l",
            languages,
            "--psm",
            PAGE_SEGMENTATION_MODE,
            # Request TSV directly: the "tsv" config file is missing from downloaded tessdata folders.
            "-c",
            "tessedit_create_tsv=1",
        ]
        output = run_tool(command, timeout=OCR_TIMEOUT).stdout
    return OcrPage(width=width, height=height, lines=tuple(group_lines(parse_tsv(output))))


def _tessdata_arguments(languages: str) -> list[str]:
    """Use the setup assistant's language folder if it holds every requested language."""
    codes = [code for code in languages.split("+") if code]
    if codes and all((TESSDATA_DIR / f"{code}.traineddata").is_file() for code in codes):
        return ["--tessdata-dir", str(TESSDATA_DIR)]
    return []


class OcrError(RuntimeError):
    """Raised when Tesseract does not return TSV output."""


def parse_tsv(tsv: str) -> list[OcrWord]:
    """Parse Tesseract TSV output into words, skipping empty and low-confidence entries."""
    if not tsv.startswith(TSV_HEADER_START):
        raise OcrError(f"Tesseract returned no TSV table (got {tsv[:80]!r}). Check the Tesseract installation and languages.")
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    words: list[OcrWord] = []
    for row in reader:
        word = _word_from_row(row)
        if word is not None:
            words.append(word)
    return words


def _word_from_row(row: dict[str, str]) -> OcrWord | None:
    """Convert one TSV row; returns None for non-word levels, blanks and noise."""
    if row.get("level") != str(TSV_WORD_LEVEL):
        return None
    text = (row.get("text") or "").strip()
    confidence = float(row.get("conf") or -1)
    if confidence < MIN_WORD_CONFIDENCE or not any(character.isalnum() for character in text):
        return None
    return OcrWord(
        text=text,
        left=int(row["left"]),
        top=int(row["top"]),
        width=int(row["width"]),
        height=int(row["height"]),
        confidence=confidence,
        line_key=(int(row["block_num"]), int(row["par_num"]), int(row["line_num"])),
    )
