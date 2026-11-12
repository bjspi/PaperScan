"""Vertical strip of page thumbnails: drag to reorder, right-click for page actions.

With several documents in the session, each document sits on its own tinted,
rounded area (colours alternate) with a header such as "DOCUMENT 2 · 3 PAGES"
above its first page, so the split is visible at a glance without changing
which row is which page.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDropEvent, QFont, QIcon, QPainter, QPaintEvent, QPixmap
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
#: Item data roles: header text above a page ("" = none) and the page's document number (0-based).
HEADER_ROLE = Qt.ItemDataRole.UserRole + 1
DOCUMENT_ROLE = Qt.ItemDataRole.UserRole + 2
HEADER_HEIGHT = 24
HEADER_FONT_POINTS = 8
HEADER_PADDING = 8
#: Hues of the document areas, used in turn; readable in the light and the dark theme.
DOCUMENT_HUES = ("#2f7de1", "#2fa84f", "#d9822b", "#8b5cf6")
#: Opacity (0–255) of a document area and of its header text.
AREA_ALPHA = 38
HEADER_ALPHA = 230
#: Inset of a document area from the strip's edges, padding around its pages, corner radius.
AREA_MARGIN = 2
AREA_PADDING = 4
AREA_RADIUS = 10


def document_header(number: int, page_count: int) -> str:
    """Return the header text of a document in the strip."""
    return f"DOCUMENT {number} · {page_count} PAGE{'S' if page_count != 1 else ''}"


def document_color(document: int, alpha: int) -> QColor:
    """Return the colour of the document at position ``document`` (0-based) with ``alpha``."""
    color = QColor(DOCUMENT_HUES[document % len(DOCUMENT_HUES)])
    color.setAlpha(alpha)
    return color


class _DocumentHeaderDelegate(QStyledItemDelegate):
    """Draws a document header above pages that begin a document."""

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:  # noqa: N802 - Qt API
        """Reserve room for the header."""
        size = super().sizeHint(option, index)
        if index.data(HEADER_ROLE):
            size.setHeight(size.height() + HEADER_HEIGHT)
        return size

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        """Paint the header in the document's colour, then the page below it."""
        header = index.data(HEADER_ROLE)
        if not header:
            super().paint(painter, option, index)
            return
        rect: QRect = option.rect
        painter.save()
        font = QFont(painter.font())
        font.setPointSize(HEADER_FONT_POINTS)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(document_color(int(index.data(DOCUMENT_ROLE)), HEADER_ALPHA))
        band = QRect(rect.left() + HEADER_PADDING, rect.top(), rect.width() - 2 * HEADER_PADDING, HEADER_HEIGHT)
        painter.drawText(band, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, header)
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
        self._documents: list[range] = []

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
        """Show each document as a coloured area with a header (nothing while there is only one)."""
        self._documents = documents if len(documents) > 1 else []
        for number, pages in enumerate(documents):
            for row in pages:
                header = document_header(number + 1, len(pages)) if self._documents and row == pages.start else ""
                self.item(row).setData(HEADER_ROLE, header)
                self.item(row).setData(DOCUMENT_ROLE, number)
        self.doItemsLayout()
        self.viewport().update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        """Paint the document areas behind the pages, then the pages."""
        if self._documents:
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            for number, pages in enumerate(self._documents):
                if pages.stop <= self.count():
                    painter.setBrush(document_color(number, AREA_ALPHA))
                    painter.drawRoundedRect(self._document_area(pages), AREA_RADIUS, AREA_RADIUS)
            painter.end()
        super().paintEvent(event)

    def _document_area(self, pages: range) -> QRect:
        """Return the viewport rectangle around all pages of one document."""
        top = self.visualItemRect(self.item(pages.start)).top() - AREA_PADDING
        bottom = self.visualItemRect(self.item(pages[-1])).bottom() + AREA_PADDING
        return QRect(AREA_MARGIN, top, self.viewport().width() - 2 * AREA_MARGIN, bottom - top)

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
