"""Behaviour: the setup assistant never takes a broken download for an installed file."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from paperscan import tool_setup
from paperscan.tool_setup import SetupError


class _BrokenResponse(io.BytesIO):
    """A response whose connection drops after the first block."""

    def read(self, size: int | None = -1) -> bytes:
        if self.tell():
            raise ConnectionResetError("connection reset")
        return super().read(size)


def test_interrupted_language_download_is_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tool_setup, "TESSDATA_DIR", tmp_path)
    monkeypatch.setattr(tool_setup.urllib.request, "urlopen", lambda _url, timeout: _BrokenResponse(b"partial model"))
    with pytest.raises(SetupError):
        tool_setup.install_tesseract_languages("deu", lambda _message, _fraction: None)
    assert not (tmp_path / "deu.traineddata").exists()
    assert list(tmp_path.glob("*.part")) == []

    monkeypatch.setattr(tool_setup.urllib.request, "urlopen", lambda _url, timeout: io.BytesIO(b"complete model"))
    tool_setup.install_tesseract_languages("deu", lambda _message, _fraction: None)
    assert (tmp_path / "deu.traineddata").read_bytes() == b"complete model"
