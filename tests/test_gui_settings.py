"""The settings window must hand back correctly typed settings (regression: enums came back as str)."""

from __future__ import annotations

import os
from enum import Enum

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from paperscan.export.jpeg_encoding import ChromaSubsampling, encode_jpeg
from paperscan.gui.settings_window import PAGES, FieldKind, SettingsWindow
from paperscan.settings import AppSettings, normalized, setting_value, with_setting


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


def test_choice_fields_come_back_as_enums(qt_app: QApplication) -> None:
    window = SettingsWindow(AppSettings())
    settings = window.current_settings()
    choice_fields = [field for page in PAGES for section in page.sections for field in section.fields if field.kind is FieldKind.CHOICE]
    assert choice_fields
    for field in choice_fields:
        value = setting_value(settings, field.path)
        assert isinstance(value, Enum), field.path
    jpeg_options = settings.jpeg
    assert encode_jpeg(np.zeros((8, 8, 3), np.uint8), jpeg_options.__class__(use_jpegli=False, subsampling=jpeg_options.subsampling))
    window.deleteLater()


def test_normalized_restores_declared_types() -> None:
    raw = with_setting(AppSettings(), "jpeg.subsampling", "420")
    assert normalized(raw).jpeg.subsampling is ChromaSubsampling.QUARTER
