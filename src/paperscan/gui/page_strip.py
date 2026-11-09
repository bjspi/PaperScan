"""Vertical strip of page thumbnails: drag to reorder, right-click for page actions."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QDropEvent, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QListWidget,
    QListWidgetItem,
    QWidget,
)

THUMBNAIL_SIZE = QSize(110, 150)
STRIP_WIDTH = 176


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
