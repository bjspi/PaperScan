"""Vertical strip of page thumbnails: drag to reorder, right-click for page actions.

With several documents in the session, the first page of each document gets a
small header ("DOCUMENT 2 · 3 PAGES") drawn above it, so the split is visible
without changing which row is which page.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDropEvent, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QListWidget,
    QListWidgetItem,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

THUMBNAIL_SIZE = QSize(110, 150)
STRIP_WIDTH = 176
#: Item data role holding the document header shown above a page ("" = none).
HEADER_ROLE = Qt.ItemDataRole.UserRole + 1
HEADER_HEIGHT = 26
HEADER_FONT_POINTS = 8
HEADER_PADDING = 6
#: Opacity of the header text and its divider line, on the text colour (0–255).
HEADER_TEXT_ALPHA = 170
HEADER_LINE_ALPHA = 60


def document_header(number: int, page_count: int) -> str:
    """Return the header text of a document in the strip."""
    return f"DOCUMENT {number} · {page_count} PAGE{'S' if page_count != 1 else ''}"


class _DocumentHeaderDelegate(QStyledItemDelegate):
    """Draws a document header above pages that begin a document."""

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:  # noqa: N802 - Qt API
        """Reserve room for the header."""
        size = super().sizeHint(option, index)
        if index.data(HEADER_ROLE):
            size.setHeight(size.height() + HEADER_HEIGHT)
        return size

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        """Paint the header band, then the page below it."""
        header = index.data(HEADER_ROLE)
        if not header:
            super().paint(painter, option, index)
            return
        rect: QRect = option.rect
        band = QRect(rect.left(), rect.top(), rect.width(), HEADER_HEIGHT)
        text_color = QColor(option.palette.text().color())
        painter.save()
        font = QFont(painter.font())
        font.setPointSize(HEADER_FONT_POINTS)
        font.setBold(True)
        painter.setFont(font)
        text_color.setAlpha(HEADER_TEXT_ALPHA)
        painter.setPen(text_color)
        text_rect = band.adjusted(HEADER_PADDING, 0, -HEADER_PADDING, -HEADER_PADDING)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, header)
        text_color.setAlpha(HEADER_LINE_ALPHA)
        painter.setPen(text_color)
        painter.drawLine(band.left() + HEADER_PADDING, band.bottom(), band.right() - HEADER_PADDING, band.bottom())
        painter.restore()
        page_option = QStyleOptionViewItem(option)
        page_option.rect = rect.adjusted(0, HEADER_HEIGHT, 0, 0)
        super().paint(painter, page_option, index)


class PageStrip(QListWidget):
    """Shows one thumbnail per page and reports selection, reordering and context menus."""

    page_selected = Signal(int)
    page_moved = Signal(int, int)  # from index, to index
    context_requested = Signal(QPoint)  # global position

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty strip."""
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setIconSize(THUMBNAIL_SIZE)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSpacing(6)
        self.setItemDelegate(_DocumentHeaderDelegate(self))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.currentRowChanged.connect(self._row_changed)
        self._drag_source = -1

    def set_page_count(self, count: int) -> None:
        """Ensure there are exactly ``count`` items, labelled by page number."""
        self.blockSignals(True)
        while self.count() > count:
            self.takeItem(self.count() - 1)
        while self.count() < count:
            self.addItem(QListWidgetItem())
        for row in range(count):
            self.item(row).setText(str(row + 1))
        self.blockSignals(False)

    def set_documents(self, documents: list[range]) -> None:
        """Show a header above the first page of each document (none while there is only one)."""
        headers = {pages.start: document_header(number, len(pages)) for number, pages in enumerate(documents, start=1)}
        show = len(documents) > 1
        for row in range(self.count()):
            self.item(row).setData(HEADER_ROLE, headers.get(row, "") if show else "")
        self.doItemsLayout()

    def set_thumbnail(self, index: int, pixmap: QPixmap) -> None:
        """Show ``pixmap`` for page ``index``."""
        if 0 <= index < self.count():
            scaled = pixmap.scaled(THUMBNAIL_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.item(index).setIcon(QIcon(scaled))

    def select(self, index: int) -> None:
        """Select page ``index`` without emitting :attr:`page_selected`."""
        self.blockSignals(True)
        self.setCurrentRow(index)
        self.blockSignals(False)

    def startDrag(self, actions: Qt.DropAction) -> None:  # noqa: N802 - Qt API
        """Remember which page is dragged."""
        self._drag_source = self.currentRow()
        super().startDrag(actions)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt API
        """Report the move instead of letting Qt reorder items on its own."""
        target = self.indexAt(event.position().toPoint()).row()
        source = self._drag_source
        event.ignore()
        if source < 0:
            return
        if target < 0:
            target = self.count() - 1
        if target != source:
            self.page_moved.emit(source, target)

    def _row_changed(self, row: int) -> None:
        """Forward user selection."""
        if row >= 0:
            self.page_selected.emit(row)

    def _context_menu(self, position: QPoint) -> None:
        """Select the page under the cursor and ask for its menu."""
        row = self.indexAt(position).row()
        if row < 0:
            return
        self.setCurrentRow(row)
        self.context_requested.emit(self.viewport().mapToGlobal(position))
