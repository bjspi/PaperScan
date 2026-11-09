"""OCR parsing, AI answer parsing, naming, PDF text layer and settings."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pymupdf
import pytest

from paperscan.analysis.document_analysis import parse_answer
from paperscan.analysis.vision import AnalysisError, AnalysisProvider
from paperscan.app_paths import BACKUP_EXCLUSION_MARKER, make_reproducible_dir
from paperscan.capture.adb_import import RemotePhoto, _parse_device_line, _parse_stat_line
from paperscan.export.naming import MAX_FILE_NAME_LENGTH, FileDateStyle, clean_pdf_name, pdf_file_name, sanitize_title, unique_path
from paperscan.export.pdf_builder import PdfMetadata, PdfPage, build_pdf
from paperscan.gui.phone_dialog import latest_series
from paperscan.ocr.recognition import OcrError, OcrLine, OcrPage, group_lines, parse_tsv
from paperscan.settings import AppSettings, SettingsStore, setting_value, with_setting

TSV = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    "5\t1\t1\t1\t1\t1\t10\t20\t50\t15\t95.0\tHello\n"
    "5\t1\t1\t1\t1\t2\t70\t22\t60\t15\t91.0\tWorld\n"
    "5\t1\t1\t1\t2\t1\t10\t50\t40\t15\t5.0\tnoise\n"
    "5\t1\t1\t1\t2\t2\t60\t50\t5\t15\t90.0\t|\n"
    "5\t1\t1\t1\t3\t1\t10\t80\t40\t15\t88.0\tNext\n"
)


def test_tsv_words_are_grouped_into_lines() -> None:
    lines = group_lines(parse_tsv(TSV))
    assert [line.text for line in lines] == ["Hello World", "Next"]
    assert (lines[0].left, lines[0].top, lines[0].width, lines[0].height) == (10, 20, 120, 17)


def test_line_corrections_keep_positions() -> None:
    page = OcrPage(100, 100, (OcrLine("Helo", 1, 2, 3, 4), OcrLine("ok", 5, 6, 7, 8)))
    corrected = page.with_line_texts({0: "Hello", 1: "  "})
    assert corrected.lines[0] == OcrLine("Hello", 1, 2, 3, 4)
    assert corrected.lines[1].text == "ok"
    assert page.numbered_text() == "0: Helo\n1: ok"


def test_parse_answer_tolerates_fences_and_converts_pages() -> None:
    payload = {"title": "Invoice ACME", "document_date": "2026-03-01", "summary": "S", "keywords": ["a"], "corrections": {"1": {"0": "x"}}}
    answer = f"```json\n{json.dumps(payload)}\n```"
    analysis = parse_answer(answer, date(2026, 1, 1))
    assert analysis.title == "Invoice ACME"
    assert analysis.document_date == date(2026, 3, 1)
    assert analysis.corrections == {0: {0: "x"}}


def test_parse_answer_falls_back_to_capture_date() -> None:
    assert parse_answer('{"title": "T", "document_date": "unknown"}', date(2026, 1, 1)).document_date == date(2026, 1, 1)
    with pytest.raises(AnalysisError):
        parse_answer("no json here", None)


@pytest.mark.parametrize(
    ("title", "expected"),
    [("Rechnung: Strom/Gas?", "Rechnung Strom Gas"), ("  ..  ", "Scan"), ("CON", "Scan"), ("Arztbrief Müller", "Arztbrief Müller")],
)
def test_sanitize_title(title: str, expected: str) -> None:
    assert sanitize_title(title) == expected


@pytest.mark.parametrize(
    ("style", "prefix"),
    [(FileDateStyle.DOTS, "2026.09.04 "), (FileDateStyle.DASHES, "2026-09-04 "), (FileDateStyle.COMPACT, "20260904 ")],
)
def test_file_name_is_dated_and_limited(style: FileDateStyle, prefix: str) -> None:
    name = pdf_file_name("x" * 500, date(2026, 9, 4), style)
    assert name.startswith(prefix)
    assert name.endswith(".pdf")
    assert len(name) == MAX_FILE_NAME_LENGTH


@pytest.mark.parametrize(
    ("typed", "expected"),
    [("2026.11.09 Strom: Abschlag", "2026.11.09 Strom Abschlag.pdf"), ("invoice.PDF", "invoice.pdf"), ("  ", "Scan.pdf")],
)
def test_typed_file_names_are_cleaned(typed: str, expected: str) -> None:
    assert clean_pdf_name(typed) == expected


def test_non_tsv_tesseract_output_is_an_error() -> None:
    with pytest.raises(OcrError):
        parse_tsv("Plain text instead of a TSV table")


def test_reproducible_dirs_get_a_backup_marker_once(tmp_path: Path) -> None:
    folder = make_reproducible_dir(tmp_path / "models")
    marker = folder / BACKUP_EXCLUSION_MARKER
    marker.write_text("kept", encoding="utf-8")
    make_reproducible_dir(folder)
    assert marker.read_text(encoding="utf-8") == "kept"


def test_unique_path_counts_up(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").touch()
    assert unique_path(tmp_path, "a.pdf").name == "a (2).pdf"


def test_pdf_has_selectable_text_at_the_right_place(tmp_path: Path, document_photo_file: Path) -> None:
    jpeg = document_photo_file.read_bytes()
    ocr = OcrPage(900, 1200, (OcrLine("Invoice total", 180, 240, 450, 30),))
    target = tmp_path / "out.pdf"
    build_pdf([PdfPage(jpeg, 900, 1200, ocr)], PdfMetadata(title="Test", document_date=date(2026, 9, 4)), target)
    with pymupdf.open(target) as document:
        words = document[0].get_text("words")
        assert [word[4] for word in words] == ["Invoice", "total"]
        scale = document[0].rect.width / 900
        assert words[0][0] == pytest.approx(180 * scale, abs=2)
        assert document.metadata["title"] == "Test"


def test_settings_roundtrip_and_defaults_for_invalid_values(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "config.json")
    changed = with_setting(AppSettings(), "ai.provider.provider", AnalysisProvider.CLAUDE_CLI)
    changed = with_setting(changed, "jpeg.quality", 77)
    store.save(changed)
    assert store.load() == changed
    data = json.loads(store.path.read_text(encoding="utf-8"))
    data["max_edge"] = "big"
    data["unknown"] = 1
    store.path.write_text(json.dumps(data), encoding="utf-8")
    loaded = store.load()
    assert loaded.max_edge == AppSettings().max_edge
    assert setting_value(loaded, "jpeg.quality") == 77


def test_corrupt_settings_are_set_aside(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "config.json")
    store.path.write_text("{broken", encoding="utf-8")
    assert store.load() == AppSettings()
    assert any(path.name.startswith("config.json.corrupt-") for path in tmp_path.iterdir())


def test_adb_output_parsing() -> None:
    device = _parse_device_line("0123456789ABCDEF device product:x model:Pixel_8 device:shiba")
    assert device is not None and device.ready and device.model == "Pixel 8"
    photo = _parse_stat_line("1762675200 3500085 20261109_083517.jpg", "/sdcard/DCIM/Camera")
    assert photo is not None and photo.path == "/sdcard/DCIM/Camera/20261109_083517.jpg"
    assert _parse_stat_line("1762675200 12 notes.txt", "/sdcard") is None


def test_latest_series_stops_at_a_gap() -> None:
    def photo(minute: int) -> RemotePhoto:
        return RemotePhoto(f"/p/{minute}.jpg", datetime(2026, 1, 1, 10, minute), 1)

    newest_first = [photo(59), photo(57), photo(55), photo(20)]
    assert [item.path for item in latest_series(newest_first)] == ["/p/59.jpg", "/p/57.jpg", "/p/55.jpg"]


def test_settings_are_immutable_values() -> None:
    settings = AppSettings()
    assert replace(settings, max_edge=1) != settings
