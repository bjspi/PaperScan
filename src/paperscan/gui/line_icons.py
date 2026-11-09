"""Crisp line icons drawn from small inline SVGs, tinted at runtime.

Text glyphs such as ↺ depend on the installed fonts and look different on every
system; these icons render the same everywhere and scale for high-DPI screens.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

#: Pixel ratio the icons are rendered at (sharp on 200 % displays, downscaled elsewhere).
RENDER_SCALE = 2
STROKE_WIDTH = 2


class LineIcon(StrEnum):
    """Available icons; the value is the SVG body on a 24x24 grid."""

    ROTATE_LEFT = '<path d="M4 4v5h5"/><path d="M4.6 15a8 8 0 1 0 1.8-8.4L4 9"/>'
    ROTATE_RIGHT = '<path d="M20 4v5h-5"/><path d="M19.4 15a8 8 0 1 1-1.8-8.4L20 9"/>'
    DETECT_EDGES = (
        '<path d="M4 8V4h4"/><path d="M16 4h4v4"/><path d="M20 16v4h-4"/><path d="M8 20H4v-4"/>'
        '<rect x="8.5" y="8.5" width="7" height="7" rx="1"/>'
    )
    WHOLE_PHOTO = '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 16l5-5 4 4 3-3 6 6"/><circle cx="16.5" cy="9.5" r="1.5"/>'
    PHONE = '<rect x="6.5" y="2.5" width="11" height="19" rx="2"/><path d="M11 18.5h2"/>'
    ADD_PHOTOS = (
        '<path d="M13 5H5a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6"/>'
        '<path d="M3 16l4.5-4.5 4 4 2-2 5.5 5.5"/><path d="M19 2v6"/><path d="M16 5h6"/>'
    )
    ADD_FOLDER = (
        '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 10.5v5"/><path d="M9.5 13h5"/>'
    )
    COMPARE = '<rect x="3" y="4" width="7.5" height="16" rx="1.5"/><rect x="13.5" y="4" width="7.5" height="16" rx="1.5"/>'
    NEW_DOCUMENT = (
        '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'
        '<path d="M12 11.5v6"/><path d="M9 14.5h6"/>'
    )
    SETTINGS = (
        '<circle cx="12" cy="12" r="3"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8'
        'M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8"/><circle cx="12" cy="12" r="6.5"/>'
    )
    TRASH = '<path d="M4 7h16"/><path d="M9 7V4.5h6V7"/><path d="M6.5 7l1 12.5h9l1-12.5"/><path d="M10.5 11v5"/><path d="M13.5 11v5"/>'


def _svg(icon: LineIcon, color: str) -> bytes:
    """Return the complete SVG document for ``icon`` in ``color``."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="{STROKE_WIDTH}" stroke-linecap="round" stroke-linejoin="round">{icon.value}</svg>'
    ).encode()


@lru_cache(maxsize=64)
def line_icon(icon: LineIcon, color: str, size: int = 18) -> QIcon:
    """Return ``icon`` drawn in ``color`` at ``size`` logical pixels."""
    renderer = QSvgRenderer(QByteArray(_svg(icon, color)))
    pixels = size * RENDER_SCALE
    pixmap = QPixmap(QSize(pixels, pixels))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, pixels, pixels))
    painter.end()
    pixmap.setDevicePixelRatio(RENDER_SCALE)
    return QIcon(pixmap)
