"""Side-by-side comparison of filter variants for the current page.

The dialog opens at once with placeholder tiles and a busy indicator; the
renderings are filled in when the background task delivers them.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from paperscan.analysis.variant_rating import VariantRating
from paperscan.comparison import VariantRender
from paperscan.gui.image_view import ImageView
from paperscan.gui.images import to_pixmap
from paperscan.imaging.filter_variants import FilterVariant

TILE_ICON = QSize(150, 200)
GRID_COLUMNS = 4
BEST_MARK = "★ "
PLACEHOLDER_COLOR = QColor("#dfe3e9")
BUSY_BAR_WIDTH = 220


def _button_text(text: str) -> str:
    """Escape '&', which Qt would otherwise treat as a shortcut marker."""
    return text.replace("&", "&&")


class CompareDialog(QDialog):
    """Shows numbered variants; the chosen one is applied to the document."""

    rating_requested = Signal()

    def __init__(self, variants: list[FilterVariant], ai_available: bool, parent: QWidget | None = None) -> None:
        """Show placeholders for ``variants`` until :meth:`show_renders` delivers the images."""
        super().__init__(parent)
        self.setWindowTitle("Compare filters")
        self.resize(1400, 900)
        self._variants = variants
        self._renders: list[VariantRender] = []
        self._ai_available = ai_available
        self._group = QButtonGroup(self)
        self._buttons: list[QToolButton] = []
        self._large = ImageView("Rendering …")
        self._busy = QProgressBar()
        self._busy.setRange(0, 0)
        self._busy.setFixedWidth(BUSY_BAR_WIDTH)
        self._status = QLabel(f"Rendering {len(variants)} variants of this page …")
        self._status.setObjectName("hint")
        self._status.setWordWrap(True)
        body = QHBoxLayout()
        body.addLayout(self._build_grid(), 0)
        body.addWidget(self._large, 1)
        status_row = QHBoxLayout()
        status_row.addWidget(self._busy)
        status_row.addWidget(self._status, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addLayout(status_row)
        layout.addLayout(self._build_buttons())
        self._group.idClicked.connect(self._show_variant)

    # --- Public API -----------------------------------------------------------------------------

    def show_renders(self, renders: list[VariantRender]) -> None:
        """Fill the tiles with the finished renderings and enable the actions."""
        self._renders = renders
        for button, render in zip(self._buttons, renders, strict=True):
            button.setIcon(QIcon(to_pixmap(render.rgb)))
            button.setEnabled(True)
        self._busy.hide()
        self._status.setText("Click a variant to see it large.")
        self._apply.setEnabled(True)
        self._ask_ai.setEnabled(self._ai_available)
        self._buttons[0].setChecked(True)
        self._show_variant(0)

    def render_failed(self, error: Exception) -> None:
        """Report that the variants could not be rendered."""
        self._busy.hide()
        self._status.setText(f"Rendering failed: {error}")

    def renders(self) -> list[VariantRender]:
        """Return the renderings (empty while they are still being computed)."""
        return self._renders

    def selected_variant(self) -> FilterVariant:
        """Return the variant the user picked."""
        return self._variants[max(0, self._group.checkedId())]

    def show_rating(self, rating: VariantRating) -> None:
        """Mark and select the AI's choice and show its scores."""
        self._ask_ai.setEnabled(True)
        self._busy.hide()
        for index, variant in enumerate(self._variants):
            score = rating.scores.get(variant.number)
            mark = BEST_MARK if variant.number == rating.best else ""
            suffix = f"  ({score:.0f}/10)" if score is not None else ""
            self._buttons[index].setText(_button_text(f"{mark}{variant.number}  {variant.name}{suffix}"))
        best_index = rating.best - 1
        self._buttons[best_index].setChecked(True)
        self._show_variant(best_index)
        self._status.setText(f"AI recommends variant {rating.best}: {rating.reason}")

    def rating_failed(self, error: Exception) -> None:
        """Re-enable the AI button after an error."""
        self._ask_ai.setEnabled(True)
        self._busy.hide()
        self._status.setText(f"AI rating failed: {error}")

    # --- Construction ---------------------------------------------------------------------------

    def _build_grid(self) -> QGridLayout:
        """Create one checkable tile per variant, showing a placeholder until rendered."""
        placeholder = QPixmap(TILE_ICON)
        placeholder.fill(PLACEHOLDER_COLOR)
        grid = QGridLayout()
        for index, variant in enumerate(self._variants):
            button = QToolButton()
            button.setCheckable(True)
            button.setEnabled(False)
            button.setIcon(QIcon(placeholder))
            button.setIconSize(TILE_ICON)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setText(_button_text(f"{variant.number}  {variant.name}"))
            self._group.addButton(button, index)
            self._buttons.append(button)
            grid.addWidget(button, index // GRID_COLUMNS, index % GRID_COLUMNS)
        return grid

    def _build_buttons(self) -> QHBoxLayout:
        """Build the AI rating, cancel and apply buttons."""
        self._ask_ai = QPushButton("Let AI choose")
        self._ask_ai.setEnabled(False)
        tooltip = "Uses the AI provider from the settings." if self._ai_available else "Select an AI provider in the settings first."
        self._ask_ai.setToolTip(tooltip)
        self._ask_ai.clicked.connect(self._request_rating)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self._apply = QPushButton("Apply to document")
        self._apply.setObjectName("primary")
        self._apply.setEnabled(False)
        self._apply.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addWidget(self._ask_ai)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(self._apply)
        return row

    def _request_rating(self) -> None:
        """Ask the main window to run the AI rating."""
        self._ask_ai.setEnabled(False)
        self._busy.show()
        self._status.setText("AI is rating the variants …")
        self.rating_requested.emit()

    def _show_variant(self, index: int) -> None:
        """Show variant ``index`` large."""
        if index < len(self._renders):
            self._large.set_image(to_pixmap(self._renders[index].rgb))
