"""A compact integer input without spin arrows.

QSpinBox loses its text under style sheets when its buttons are hidden, so this
is a validated line edit with the small part of the spin box API the app needs.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QLineEdit, QWidget


class NumberField(QLineEdit):
    """Integer field clamped to a range; emits :attr:`valueChanged` when editing is finished."""

    valueChanged = Signal(int)  # noqa: N815 - mirrors QSpinBox

    def __init__(self, minimum: int, maximum: int, parent: QWidget | None = None) -> None:
        """Create the field for values in ``minimum..maximum``."""
        super().__init__(parent)
        self._minimum, self._maximum = minimum, maximum
        self._value = minimum
        self.setValidator(QIntValidator(minimum, maximum, self))
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.editingFinished.connect(self._commit)
        self.setText(str(minimum))

    def value(self) -> int:
        """Return the current value."""
        return self._value

    def setValue(self, value: int) -> None:  # noqa: N802 - mirrors QSpinBox
        """Show ``value`` (clamped) without emitting :attr:`valueChanged`."""
        self._value = min(self._maximum, max(self._minimum, int(value)))
        self.setText(str(self._value))

    def _commit(self) -> None:
        """Take over the typed value; invalid or empty input restores the previous value."""
        text = self.text().strip()
        if not text.lstrip("-").isdigit():
            self.setText(str(self._value))
            return
        previous = self._value
        self.setValue(int(text))
        if self._value != previous:
            self.valueChanged.emit(self._value)
