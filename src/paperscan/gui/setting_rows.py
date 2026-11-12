"""Building blocks of the settings pages: cards with divided rows.

A row shows a bold title and a muted description on the left and its control
right-aligned in a fixed-width column, so every page lines up.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

#: Width of the right-hand control column.
CONTROL_COLUMN_WIDTH = 360
ROW_MARGINS = (18, 12, 18, 12)
CARD_SPACING = 14


class ElidedLabel(QLabel):
    """A single-line label that shortens long text (e.g. paths) in the middle."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        """Create the label; the full text is also the tooltip."""
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._full_text = ""
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        """Change the text."""
        self._full_text = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        """Re-elide when the width changes."""
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        """Fit the text into the current width."""
        self.setText(self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideMiddle, max(0, self.width())))


class Card(QFrame):
    """A rounded panel holding rows separated by thin dividers."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty card."""
        super().__init__(parent)
        self.setObjectName("card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

    def add_row(self, row: QWidget) -> None:
        """Append ``row``, with a divider before it if it is not the first."""
        if self._layout.count():
            divider = QFrame()
            divider.setObjectName("rowDivider")
            divider.setFixedHeight(1)
            self._layout.addWidget(divider)
        self._layout.addWidget(row)


def setting_row(title: str, description: str, control: QWidget | None, detail: QWidget | None = None) -> QWidget:
    """Return a row: title, description and optional detail on the left, ``control`` on the right."""
    row = QWidget()
    row.setObjectName("settingRow")
    layout = QHBoxLayout(row)
    layout.setContentsMargins(*ROW_MARGINS)
    layout.setSpacing(24)
    texts = QVBoxLayout()
    texts.setSpacing(2)
    title_label = QLabel(title)
    title_label.setProperty("rowTitle", True)
    texts.addWidget(title_label)
    if description:
        description_label = QLabel(description)
        description_label.setObjectName("rowDescription")
        description_label.setWordWrap(True)
        texts.addWidget(description_label)
    if detail is not None:
        texts.addWidget(detail)
    layout.addLayout(texts, 1)
    if control is not None:
        layout.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return row


def page_body(*cards: QWidget) -> QWidget:
    """Stack cards with spacing; free space stays below them instead of inside."""
    body = QWidget()
    body.setObjectName("pageBody")
    layout = QVBoxLayout(body)
    layout.setContentsMargins(0, 4, 0, 4)
    layout.setSpacing(CARD_SPACING)
    for card in cards:
        layout.addWidget(card)
    layout.addStretch(1)
    return body
