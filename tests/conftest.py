"""Shared fixtures: synthetic document photos, no personal data."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

PAGE_COLOR = (240, 240, 235)
TABLE_COLOR = (120, 90, 60)
INK_COLOR = (25, 25, 25)


def make_document_photo(width: int = 900, height: int = 1200) -> np.ndarray:
    """Return an RGB photo of a white page with text on a dark table, lit unevenly."""
    photo = np.full((height, width, 3), TABLE_COLOR, np.uint8)
    corners = np.array([[150, 140], [760, 170], [740, 1080], [130, 1050]], np.int32)
    cv2.fillPoly(photo, [corners], PAGE_COLOR)
    for line in range(12):
        cv2.putText(photo, f"Invoice line {line} total 12,34 EUR", (190, 260 + line * 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, INK_COLOR, 2)
    shading = np.linspace(0.6, 1.0, width)[None, :, None]
    return (photo * shading).astype(np.uint8)


@pytest.fixture
def document_photo() -> np.ndarray:
    """A synthetic, unevenly lit document photo."""
    return make_document_photo()


@pytest.fixture
def document_photo_file(tmp_path: Path) -> Path:
    """The synthetic photo saved as JPEG."""
    path = tmp_path / "photo.jpg"
    cv2.imwrite(str(path), cv2.cvtColor(make_document_photo(), cv2.COLOR_RGB2BGR))
    return path
