"""A label that shows an image scaled to fit, keeping the aspect ratio."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QResizeEvent
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

#: Smallest size so the splitter cannot collapse the view to nothing.
MIN_VIEW_SIZE = 200


class ImageView(QLabel):
    """Displays a pixmap scaled into the available space."""

    def __init__(self, placeholder: str, parent: QWidget | None = None) -> None:
        """Create the view showing ``placeholder`` until an image is set."""
        super().__init__(placeholder, parent)
        self.setObjectName("canvas")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(MIN_VIEW_SIZE, MIN_VIEW_SIZE)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._placeholder = placeholder
        self._pixmap = QPixmap()

    def set_image(self, pixmap: QPixmap) -> None:
        """Show ``pixmap``."""
        self._pixmap = pixmap
        self._rescale()

    def clear_image(self) -> None:
        """Show the placeholder text again."""
        self._pixmap = QPixmap()
        self.setPixmap(QPixmap())
        self.setText(self._placeholder)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        """Rescale with the widget."""
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        """Fit the stored pixmap into the current size."""
        if self._pixmap.isNull():
            return
        self.setPixmap(self._pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
