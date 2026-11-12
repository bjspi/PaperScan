"""An on/off switch drawn with QPainter, used instead of checkboxes."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QAbstractButton, QWidget

TRACK_WIDTH = 38
TRACK_HEIGHT = 22
KNOB_MARGIN = 3
TEXT_GAP = 10


class ToggleSwitch(QAbstractButton):
    """A checkable switch with an optional label to its right; API-compatible with QCheckBox."""

    #: Track colours shared by all switches; set by the theme.
    on_color = QColor("#2f7de1")
    off_color = QColor("#c5ccd6")
    knob_color = QColor("#ffffff")

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        """Create an unchecked switch."""
        super().__init__(parent)
        self.setCheckable(True)
        self.setText(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    @classmethod
    def use_theme_colors(cls, on: str, off: str) -> None:
        """Use the theme's accent and inactive track colours for all switches."""
        cls.on_color, cls.off_color = QColor(on), QColor(off)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        """Track plus label."""
        text_width = self.fontMetrics().horizontalAdvance(self.text()) + TEXT_GAP if self.text() else 0
        return QSize(TRACK_WIDTH + text_width, max(TRACK_HEIGHT, self.fontMetrics().height()) + 2)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        """Draw the rounded track, the knob and the label."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        top = (self.height() - TRACK_HEIGHT) / 2
        track = QRectF(0, top, TRACK_WIDTH, TRACK_HEIGHT)
        color = QColor(self.on_color if self.isChecked() else self.off_color)
        if not self.isEnabled():
            color.setAlpha(110)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(track, TRACK_HEIGHT / 2, TRACK_HEIGHT / 2)
        knob_size = TRACK_HEIGHT - 2 * KNOB_MARGIN
        knob_left = TRACK_WIDTH - KNOB_MARGIN - knob_size if self.isChecked() else KNOB_MARGIN
        painter.setBrush(self.knob_color)
        painter.drawEllipse(QRectF(knob_left, top + KNOB_MARGIN, knob_size, knob_size))
        if self.text():
            painter.setPen(self.palette().color(self.foregroundRole()))
            text_rect = self.rect().adjusted(TRACK_WIDTH + TEXT_GAP, 0, 0, 0)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        painter.end()
