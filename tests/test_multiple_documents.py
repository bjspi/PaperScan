"""Behaviour: one import split into several documents, each exported as its own PDF."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from paperscan.analysis.vision import AnalysisProvider
from paperscan.capture.adb_import import RemotePhoto
from paperscan.capture.photo_files import SERIES_GAP
from paperscan.export.naming import FileDateStyle
from paperscan.gui.export_dialog import DocumentReview, ExportDialog
from paperscan.gui.page_strip import HEADER_ROLE, PageStrip, document_header
from paperscan.imaging.filters import FilterSettings
from paperscan.imaging.geometry import Quad
from paperscan.pipeline import PageEdit, ScanPage, choice_for, open_page, prepare_documents
from paperscan.session import DocumentSession, SessionPage
from paperscan.settings import AppSettings, OcrSettings

START = datetime(2026, 11, 12, 9, 0)
SIDE = 100


def _page(name: str, minutes: float = 0, remote: bool = False) -> SessionPage:
    """Return a tiny page photographed ``minutes`` after :data:`START`."""
    taken = START + timedelta(minutes=minutes)
    edit = PageEdit(quad=Quad.full_image(SIDE, SIDE))
    scan = ScanPage(source=Path(f"/photos/{name}.jpg"), captured_at=taken, width=SIDE, height=SIDE, edit=edit)
    photo = RemotePhoto(path=f"/sdcard/DCIM/Camera/{name}.jpg", modified=taken, size=1) if remote else None
    return SessionPage(page=scan, preview_rgb=np.zeros((10, 10, 3), np.uint8), remote=photo)


def _session(*names: str) -> DocumentSession:
    """Return a session with one page per name, all in one document."""
    session = DocumentSession(FilterSettings())
    for name in names:
        session.add(_page(name))
    return session


def _names(session: DocumentSession) -> list[list[str]]:
    """Return the page names of each document."""
    return [[page.source.stem for page in pages] for pages in session.document_pages()]


def test_new_pages_form_one_document() -> None:
    assert _names(_session("a", "b", "c")) == [["a", "b", "c"]]


def test_split_and_join() -> None:
    session = _session("a", "b", "c", "d")
    session.split_before(2)
    assert _names(session) == [["a", "b"], ["c", "d"]]
    assert session.starts_document(2)
    session.split_before(3)
    assert _names(session) == [["a", "b"], ["c"], ["d"]]
    session.join_with_previous(3)
    assert _names(session) == [["a", "b"], ["c", "d"]]
    session.join_with_previous(0)
    assert _names(session) == [["a", "b"], ["c", "d"]]


def test_moved_page_joins_the_document_it_is_dropped_on() -> None:
    session = _session("a", "b", "c")
    session.split_before(2)
    session.move(2, 0)
    assert _names(session) == [["c", "a", "b"]]
    session.split_before(1)
    assert _names(session) == [["c"], ["a", "b"]]
    session.move(0, 2)  # dragged down onto "b": lands after it, in its document
    assert _names(session) == [["a", "b", "c"]]


def test_removing_the_only_page_of_a_document_drops_it() -> None:
    session = _session("a", "b")
    session.split_before(1)
    session.remove(1)
    assert _names(session) == [["a"]]


def test_imported_pages_join_the_last_document() -> None:
    session = _session("a", "b")
    session.split_before(1)
    session.add(_page("c"))
    assert _names(session) == [["a"], ["b", "c"]]


def test_split_at_pauses_between_photos() -> None:
    session = DocumentSession(FilterSettings())
    for name, minutes in (("a", 0), ("b", 1), ("c", 40), ("d", 41)):
        session.add(_page(name, minutes))
    assert session.split_at_pauses(SERIES_GAP) == 2
    assert _names(session) == [["a", "b"], ["c", "d"]]


def test_phone_photos_of_selected_pages() -> None:
    session = DocumentSession(FilterSettings())
    for name in ("a", "b"):
        session.add(_page(name, remote=True))
    session.split_before(1)
    first = session.document_ranges()[0]
    assert [photo.name for photo in session.phone_photos(first)] == ["a.jpg"]


def test_two_documents_become_two_drafts(document_photo_file: Path, tmp_path: Path) -> None:
    settings = AppSettings(output_dir=str(tmp_path), border_model_path="", ocr=OcrSettings(enabled=False))
    settings = replace(settings, jpeg=replace(settings.jpeg, use_jpegli=False))
    pages = [open_page(document_photo_file, settings) for _ in range(3)]
    messages: list[tuple[str, float]] = []
    drafts = prepare_documents([pages[:2], pages[2:]], settings, lambda message, fraction: messages.append((message, fraction)))
    assert [len(draft.pages) for draft in drafts] == [2, 1]
    assert messages[0][0].startswith("Document 1/2")
    assert messages[-1][0].startswith("Document 2/2")
    assert all(0 <= fraction <= 1 for _message, fraction in messages)


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


def test_strip_shows_headers_only_with_several_documents(qt_app: QApplication) -> None:
    strip = PageStrip()
    strip.set_page_count(3)
    strip.set_documents([range(0, 3)])
    assert [strip.item(row).data(HEADER_ROLE) for row in range(3)] == ["", "", ""]
    strip.set_documents([range(0, 2), range(2, 3)])
    assert [strip.item(row).data(HEADER_ROLE) for row in range(3)] == [document_header(1, 2), "", document_header(2, 1)]
    strip.deleteLater()


def test_save_dialog_returns_one_choice_per_document(qt_app: QApplication, tmp_path: Path) -> None:
    reviews = [
        DocumentReview(choice_for(title=title, document_date=START.date(), style=FileDateStyle.DASHES), page_count=1)
        for title in ("Invoice", "Letter")
    ]
    dialog = ExportDialog(reviews, tmp_path, open_after=False, style=FileDateStyle.DASHES, providers=[], preferred=AnalysisProvider.NONE)
    assert [choice.file_name for choice in dialog.choices()] == ["2026-11-12 Invoice.pdf", "2026-11-12 Letter.pdf"]
    assert dialog.windowTitle() == "Save 2 PDFs"
    dialog.deleteLater()
