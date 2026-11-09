"""The final step in the side panel: what the export will do, and the Create PDF button."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QGroupBox, QLabel, QPushButton, QVBoxLayout, QWidget

from paperscan.gui.theme import set_style_state

CREATE_TEXT = "✓  Create PDF"
#: Style property that colours a capability line (see theme).
CAPABILITY_STATE = "capability"


@dataclass(frozen=True)
class Capability:
    """Whether an export step will run, with a short explanation."""

    ready: bool
    text: str


class WorkflowPanel(QGroupBox):
    """Shows page count, OCR and AI status, and starts the export."""

    create_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the status lines and the button."""
        super().__init__("WORKFLOW", parent)
        self._pages = QLabel()
        self._pages.setObjectName("hint")
        self._ocr = QLabel()
        self._ai = QLabel()
        for label in (self._ocr, self._ai):
            label.setWordWrap(True)
        status = QGridLayout()
        status.setHorizontalSpacing(10)
        status.addWidget(QLabel("OCR"), 0, 0)
        status.addWidget(self._ocr, 0, 1)
        status.addWidget(QLabel("AI"), 1, 0)
        status.addWidget(self._ai, 1, 1)
        status.setColumnStretch(1, 1)
        self._create = QPushButton(CREATE_TEXT)
        self._create.setObjectName("create")
        self._create.setToolTip("Process all pages, review title and date, then save the PDF (Ctrl+S).")
        self._create.clicked.connect(self.create_requested)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addLayout(status)
        layout.addWidget(self._pages)
        layout.addWidget(self._create)
        self.show_state(page_count=0, busy=False)

    def show_capabilities(self, ocr: Capability, ai: Capability) -> None:
        """Show whether OCR and AI naming will run."""
        for label, capability in ((self._ocr, ocr), (self._ai, ai)):
            label.setText(f"{'✓' if capability.ready else '–'}  {capability.text}")
            set_style_state(label, CAPABILITY_STATE, "ok" if capability.ready else "off")

    def show_state(self, *, page_count: int, busy: bool) -> None:
        """Update the page summary and whether the button can be used."""
        if busy:
            summary = "Working …"
        elif page_count:
            summary = f"{page_count} page{'s' if page_count != 1 else ''} ready."
        else:
            summary = "Add photos to start a document."
        self._pages.setText(summary)
        self._create.setEnabled(page_count > 0 and not busy)
