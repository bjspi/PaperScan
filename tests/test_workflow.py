"""Behaviour: several photos become one PDF; the session keeps one shared filter."""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from paperscan import pipeline
from paperscan.capture.adb_import import RemotePhoto
from paperscan.export.naming import FileDateStyle
from paperscan.imaging.filters import FilterMode, FilterSettings
from paperscan.pipeline import ExportCancelledError, choice_for, open_page, prepare_export, suggested_choice, write_export
from paperscan.session import DocumentSession, load_session_page, render_preview
from paperscan.settings import AppSettings, OcrSettings


@pytest.fixture
def offline_settings(tmp_path: Path) -> AppSettings:
    """Settings that need no external tool: no OCR, no AI, Pillow JPEG, no model."""
    settings = AppSettings(output_dir=str(tmp_path / "out"), border_model_path="", ocr=OcrSettings(enabled=False))
    return replace(settings, jpeg=replace(settings.jpeg, use_jpegli=False))


def test_three_photos_become_one_dated_pdf(document_photo_file: Path, offline_settings: AppSettings) -> None:
    pages = [open_page(document_photo_file, offline_settings) for _ in range(3)]
    draft = prepare_export(pages, offline_settings)
    choice = choice_for(title="Heating bill", document_date=draft.capture_date, style=FileDateStyle.DOTS)
    target = write_export(draft, choice, Path(offline_settings.output_dir))
    assert target.name == f"{draft.capture_date:%Y.%m.%d} Heating bill.pdf"
    assert suggested_choice(draft, FileDateStyle.DOTS).title == "Scan"  # no AI configured
    with pymupdf.open(target) as document:
        assert document.page_count == 3


def test_export_can_be_cancelled(document_photo_file: Path, offline_settings: AppSettings) -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ExportCancelledError):
        prepare_export([open_page(document_photo_file, offline_settings)], offline_settings, cancel=cancel)


def test_cancel_during_the_analysis_stops_the_export(
    document_photo_file: Path, offline_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancel = threading.Event()
    real_analyse = pipeline._analyse

    def analyse_then_cancel(*arguments: Any) -> Any:
        cancel.set()  # the user clicks Cancel while the AI is still answering
        return real_analyse(*arguments)

    monkeypatch.setattr(pipeline, "_analyse", analyse_then_cancel)
    with pytest.raises(ExportCancelledError):
        prepare_export([open_page(document_photo_file, offline_settings)], offline_settings, cancel=cancel)


def test_session_shared_and_page_filters(document_photo_file: Path, offline_settings: AppSettings) -> None:
    session = DocumentSession(FilterSettings())
    for _ in range(3):
        session.add(load_session_page(document_photo_file, offline_settings))
    clear = FilterSettings(mode=FilterMode.CLEAR)
    session.set_filter(0, clear, only_this_page=False)
    assert {entry.page.edit.filter.mode for entry in session.pages} == {FilterMode.CLEAR}
    session.set_filter(1, FilterSettings(mode=FilterMode.PHOTO), only_this_page=True)
    session.set_filter(0, FilterSettings(mode=FilterMode.BLACK_WHITE), only_this_page=False)
    assert [entry.page.edit.filter.mode for entry in session.pages] == [FilterMode.BLACK_WHITE, FilterMode.PHOTO, FilterMode.BLACK_WHITE]


def test_session_reorders_and_renders(document_photo_file: Path, offline_settings: AppSettings, tmp_path: Path) -> None:
    session = DocumentSession(FilterSettings())
    first = session.add(load_session_page(document_photo_file, offline_settings))
    second_file = tmp_path / "second.jpg"
    second_file.write_bytes(document_photo_file.read_bytes())
    session.add(load_session_page(second_file, offline_settings))
    session.move(1, 0)
    assert session.page(0).page.source == second_file
    session.rotate(first, clockwise=True)
    preview = render_preview(session.page(first))
    assert preview.shape[0] < preview.shape[1]  # portrait page turned to landscape


def test_only_phone_imports_are_marked_for_deletion(document_photo_file: Path, offline_settings: AppSettings) -> None:
    photo = RemotePhoto("/sdcard/DCIM/Camera/a.jpg", datetime(2026, 1, 1), 1)
    session = DocumentSession(FilterSettings())
    session.add(load_session_page(document_photo_file, offline_settings, photo))
    session.add(load_session_page(document_photo_file, offline_settings, photo))
    session.add(load_session_page(document_photo_file, offline_settings))
    assert session.phone_photos() == [photo]
