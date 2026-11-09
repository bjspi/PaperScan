"""Output file names of the form ``<date> Title.pdf``, safe on Windows and macOS."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from enum import StrEnum
from pathlib import Path

#: Maximum file name length including date prefix and extension.
MAX_FILE_NAME_LENGTH = 200
PDF_SUFFIX = ".pdf"
FALLBACK_TITLE = "Scan"
#: Characters Windows or macOS forbid in file names, plus control characters.
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")
#: Names Windows reserves regardless of extension.
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}


def sanitize_title(title: str) -> str:
    """Return ``title`` without characters that are invalid in file names."""
    normalized = unicodedata.normalize("NFC", title)
    cleaned = _WHITESPACE.sub(" ", _FORBIDDEN.sub(" ", normalized)).strip(" .")
    if not cleaned or cleaned.upper() in _RESERVED:
        return FALLBACK_TITLE
    return cleaned


class FileDateStyle(StrEnum):
    """How the date at the start of a file name is written; the value is the strftime pattern."""

    DOTS = "%Y.%m.%d"
    DASHES = "%Y-%m-%d"
    COMPACT = "%Y%m%d"


def pdf_file_name(title: str, document_date: date | None, style: FileDateStyle = FileDateStyle.DOTS) -> str:
    """Return ``<date> Title.pdf`` truncated to :data:`MAX_FILE_NAME_LENGTH` characters."""
    prefix = f"{document_date.strftime(style.value)} " if document_date else ""
    room = MAX_FILE_NAME_LENGTH - len(prefix) - len(PDF_SUFFIX)
    title_part = sanitize_title(title)[:room].rstrip(" .")
    return f"{prefix}{title_part or FALLBACK_TITLE}{PDF_SUFFIX}"


def clean_pdf_name(name: str) -> str:
    """Make a user-entered file name safe: no forbidden characters, ``.pdf`` suffix, length limit."""
    stem = name.strip()
    if stem.lower().endswith(PDF_SUFFIX):
        stem = stem[: -len(PDF_SUFFIX)]
    return f"{sanitize_title(stem)[: MAX_FILE_NAME_LENGTH - len(PDF_SUFFIX)].rstrip(' .')}{PDF_SUFFIX}"


def unique_path(directory: Path, file_name: str) -> Path:
    """Return ``directory / file_name``, adding `` (2)``, `` (3)`` … if that file exists."""
    candidate = directory / file_name
    stem, suffix = candidate.stem, candidate.suffix
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate
