"""Conversion between numpy RGB arrays and Qt images."""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage, QPixmap


def to_qimage(rgb: np.ndarray) -> QImage:
    """Return a deep-copied QImage of an RGB uint8 array (safe to use after ``rgb`` is gone)."""
    contiguous = np.ascontiguousarray(rgb)
    height, width = contiguous.shape[:2]
    image = QImage(contiguous.data, width, height, contiguous.strides[0], QImage.Format.Format_RGB888)
    return image.copy()


def to_pixmap(rgb: np.ndarray) -> QPixmap:
    """Return a QPixmap of an RGB uint8 array (main thread only)."""
    return QPixmap.fromImage(to_qimage(rgb))


def image_to_pixmap(image: QImage) -> QPixmap:
    """Convert a QImage made in a worker thread into a QPixmap (main thread only)."""
    return QPixmap.fromImage(image)
