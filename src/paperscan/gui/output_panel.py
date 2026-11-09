"""Quick output settings in the main window: longest page edge and JPEG quality."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from paperscan.gui.number_field import NumberField

MAX_EDGE_LIMIT = 20000
NUMBER_WIDTH = 96


class OutputPanel(QGroupBox):
    """Edits the two output values needed most often; changes are published immediately."""

    changed = Signal(int, int)  # max edge (0 = full resolution), JPEG quality

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the two number fields."""
        super().__init__("OUTPUT", parent)
        self._max_edge = NumberField(0, MAX_EDGE_LIMIT)
        self._max_edge.setToolTip("Longest edge of every page in pixels; 0 keeps the full resolution.")
        self._quality = NumberField(1, 100)
        self._quality.setToolTip("JPEG quality; 68–96 recommended, jpegli stays sharp at lower values.")
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addLayout(_row("Longest page edge", self._max_edge, "px"))
        layout.addLayout(_row("JPEG quality", self._quality, "%"))
        for field in (self._max_edge, self._quality):
            field.valueChanged.connect(self._emit)

    def show_values(self, max_edge: int, quality: int) -> None:
        """Load values without emitting :attr:`changed`."""
        self._max_edge.setValue(max_edge)
        self._quality.setValue(quality)

    def _emit(self) -> None:
        """Publish the current values."""
        self.changed.emit(self._max_edge.value(), self._quality.value())


def _row(title: str, field: NumberField, unit: str) -> QHBoxLayout:
    """Label left, number and unit right."""
    field.setFixedWidth(NUMBER_WIDTH)
    unit_label = QLabel(unit)
    unit_label.setObjectName("hint")
    row = QHBoxLayout()
    row.addWidget(QLabel(title))
    row.addStretch(1)
    row.addWidget(field)
    row.addWidget(unit_label)
    return row
