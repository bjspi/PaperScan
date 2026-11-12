"""Choose the newest camera photos on the phone for one document, with thumbnails."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from paperscan.capture.adb_import import RemotePhoto
from paperscan.capture.photo_files import SERIES_GAP
from paperscan.gui.images import image_to_pixmap, to_qimage
from paperscan.gui.tasks import start_task

BYTES_PER_MB = 1_048_576
THUMBNAIL_SIZE = QSize(150, 200)
GRID_SIZE = QSize(176, 258)
PLACEHOLDER_COLOR = QColor("#dfe3e9")
LOADING_BAR_WIDTH = 140
LOADING_BAR_HEIGHT = 6

#: Loads the preview of one phone photo (runs in a worker thread).
ThumbnailLoader = Callable[[RemotePhoto], np.ndarray]


def latest_series(photos: list[RemotePhoto]) -> list[RemotePhoto]:
    """Return the newest photos taken in one go (newest first input, gaps under :data:`SERIES_GAP`)."""
    series: list[RemotePhoto] = []
    for photo in photos:
        if series and series[-1].modified - photo.modified > SERIES_GAP:
            break
        series.append(photo)
    return series


class PhoneDialog(QDialog):
    """A grid of the phone's newest photos with the latest series preselected."""

    def __init__(self, photos: list[RemotePhoto], load_thumbnail: ThumbnailLoader, parent: QWidget | None = None) -> None:
        """Show ``photos`` (newest first); thumbnails are loaded one after another in the background."""
        super().__init__(parent)
        self.setWindowTitle("Photos on the phone")
        self.resize(980, 720)
        self._photos = photos
        self._load_thumbnail = load_thumbnail
        self._next_thumbnail = 0
        self._closed = False
        self._list = self._build_grid()
        self._count = QLabel()
        self._loading = QProgressBar()
        self._loading.setObjectName("statusProgress")
        self._loading.setRange(0, 0)
        self._loading.setTextVisible(False)
        self._loading.setFixedSize(LOADING_BAR_WIDTH, LOADING_BAR_HEIGHT)
        self._loading_text = QLabel()
        self._loading_text.setObjectName("hint")
        self._count.setObjectName("hint")
        self._import = QPushButton()
        self._import.setObjectName("primary")
        self._import.clicked.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addLayout(self._build_header())
        layout.addWidget(self._list, 1)
        layout.addLayout(self._build_footer())
        self._select(latest_series(photos))
        self._list.itemChanged.connect(self._update_count)
        self._load_next_thumbnail()

    def done(self, result: int) -> None:
        """Stop loading thumbnails once the dialog is closed."""
        self._closed = True
        super().done(result)

    def selected_photos(self) -> list[RemotePhoto]:
        """Return the ticked photos oldest first, which is the page order."""
        chosen = [item.data(Qt.ItemDataRole.UserRole) for item in self._items() if item.checkState() == Qt.CheckState.Checked]
        return sorted(chosen, key=lambda photo: photo.modified)

    # --- Construction ---------------------------------------------------------------------------

    def _build_header(self) -> QHBoxLayout:
        """Hint plus quick selection buttons."""
        hint = QLabel("Click the photos that belong to this document. They stay on the phone.")
        hint.setObjectName("hint")
        series = QPushButton("Latest series")
        series.setToolTip("Select the newest photos taken within a few minutes of each other.")
        series.clicked.connect(lambda: self._select(latest_series(self._photos)))
        none = QPushButton("None")
        none.clicked.connect(lambda: self._select([]))
        row = QHBoxLayout()
        row.addWidget(hint, 1)
        row.addWidget(series)
        row.addWidget(none)
        return row

    def _build_grid(self) -> QListWidget:
        """One checkable tile per photo with a placeholder until its thumbnail arrives."""
        grid = QListWidget()
        grid.setViewMode(QListView.ViewMode.IconMode)
        grid.setResizeMode(QListView.ResizeMode.Adjust)
        grid.setMovement(QListView.Movement.Static)
        grid.setIconSize(THUMBNAIL_SIZE)
        grid.setGridSize(GRID_SIZE)
        grid.setUniformItemSizes(True)
        grid.setWordWrap(True)
        placeholder = QPixmap(THUMBNAIL_SIZE)
        placeholder.fill(PLACEHOLDER_COLOR)
        for photo in self._photos:
            item = QListWidgetItem(QIcon(placeholder), f"{photo.modified:%d.%m.%Y %H:%M}\n{photo.size / BYTES_PER_MB:.1f} MB")
            item.setData(Qt.ItemDataRole.UserRole, photo)
            item.setToolTip(photo.name)
            # Not user-checkable: the box only shows the state, a click anywhere on the tile toggles it
            # once (in _toggle); with Qt toggling the box as well, a click on it would cancel itself.
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            grid.addItem(item)
        grid.itemClicked.connect(self._toggle)
        # The space bar ticks the current tile, as it did while Qt handled the box itself.
        space = QShortcut(QKeySequence(Qt.Key.Key_Space), grid, context=Qt.ShortcutContext.WidgetShortcut)
        space.activated.connect(self._toggle_current)
        return grid

    def _build_footer(self) -> QHBoxLayout:
        """Build the selection count, cancel and import row."""
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self._count, 1)
        row.addWidget(self._loading_text)
        row.addWidget(self._loading)
        row.addSpacing(12)
        row.addWidget(cancel)
        row.addWidget(self._import)
        return row

    # --- Selection ------------------------------------------------------------------------------

    def _items(self) -> list[QListWidgetItem]:
        """Return all tiles."""
        return [self._list.item(row) for row in range(self._list.count())]

    def _select(self, photos: list[RemotePhoto]) -> None:
        """Tick exactly ``photos``."""
        wanted = {photo.path for photo in photos}
        self._list.blockSignals(True)
        for item in self._items():
            checked = item.data(Qt.ItemDataRole.UserRole).path in wanted
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            item.setSelected(checked)
        self._list.blockSignals(False)
        self._update_count()

    def _toggle(self, item: QListWidgetItem) -> None:
        """Clicking a tile ticks or unticks it."""
        checked = item.checkState() != Qt.CheckState.Checked
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        item.setSelected(checked)

    def _toggle_current(self) -> None:
        """Tick or untick the tile with the keyboard focus."""
        item = self._list.currentItem()
        if item is not None:
            self._toggle(item)

    def _update_count(self) -> None:
        """Show how many photos are ticked and enable the import button."""
        count = len(self.selected_photos())
        self._count.setText(f"{count} of {len(self._photos)} selected · page order = capture time")
        self._import.setText(f"Import {count} photo{'s' if count != 1 else ''}")
        self._import.setEnabled(count > 0)

    # --- Thumbnails -----------------------------------------------------------------------------

    def _load_next_thumbnail(self) -> None:
        """Load thumbnails one at a time so adb is not flooded and the newest appear first."""
        if self._closed:
            return
        if self._next_thumbnail >= len(self._photos):
            self._loading.hide()
            self._loading_text.hide()
            return
        self._loading_text.setText(f"Loading previews {self._next_thumbnail + 1}/{len(self._photos)}")
        index = self._next_thumbnail
        self._next_thumbnail += 1
        photo = self._photos[index]
        start_task(
            lambda _progress: to_qimage(self._load_thumbnail(photo)),
            lambda image: self._thumbnail_ready(index, image),
            lambda _error: self._load_next_thumbnail(),
        )

    def _thumbnail_ready(self, index: int, image: object) -> None:
        """Show a loaded thumbnail and continue with the next one."""
        if self._closed:
            return
        self._list.item(index).setIcon(QIcon(image_to_pixmap(image)))  # type: ignore[arg-type]
        self._load_next_thumbnail()
