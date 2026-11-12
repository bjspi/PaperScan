"""Tool status and one-click installation; used in the settings window and on first start."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from paperscan.gui.setting_rows import Card, ElidedLabel, page_body, setting_row
from paperscan.gui.tasks import start_task
from paperscan.gui.theme import set_style_state
from paperscan.imaging.border_detection import model_available
from paperscan.imaging.model_import import import_model
from paperscan.platform_tools import Tool, find_tool
from paperscan.settings import AppSettings
from paperscan.tool_setup import INSTALLABLE_TOOLS, SetupMethod, install, install_tesseract_languages, setup_plan

#: Display name and purpose of each tool.
TOOL_TEXTS: dict[Tool, tuple[str, str]] = {
    Tool.TESSERACT: ("Tesseract", "Text recognition for the searchable PDF layer."),
    Tool.CJPEGLI: ("cjpegli", "Small, high-quality JPEGs (jpegli, as in XnConvert)."),
    Tool.ADB: ("adb", "Imports photos from an Android phone over USB or Wi-Fi."),
    Tool.CLAUDE: ("Claude Code", "Optional AI backend for titles, dates and OCR correction."),
    Tool.CODEX: ("Codex", "Optional AI backend for titles, dates and OCR correction."),
}
STATUS_READY = "Installed"
STATUS_MISSING = "Missing"
STATUS_OPTIONAL = "Not found"
#: Style property that colours the status pill (see theme).
BADGE_STATE = "badge"


@dataclass(frozen=True)
class _ToolRow:
    """Widgets of one tool row."""

    badge: QLabel
    path: ElidedLabel
    button: QPushButton | None


class SetupPanel(QWidget):
    """Shows which tools are available and installs missing ones in the background."""

    model_imported = Signal(Path)

    def __init__(self, settings_provider: Callable[[], AppSettings], parent: QWidget | None = None) -> None:
        """Build the rows; ``settings_provider`` returns the current (possibly unsaved) settings."""
        super().__init__(parent)
        self._settings = settings_provider
        self._rows: dict[Tool, _ToolRow] = {}
        tools_card = Card()
        for tool in TOOL_TEXTS:
            tools_card.add_row(self._tool_row(tool))
        self._model_badge = QLabel()
        self._model_path = ElidedLabel()
        self._model_path.setObjectName("rowDescription")
        import_button = QPushButton("Import …")
        import_button.setToolTip("Add a compatible TFLite page corner model.")
        import_button.clicked.connect(self._import_model)
        model_card = Card()
        model_card.add_row(
            setting_row(
                "Border model",
                "Neural page detection. Without it, OpenCV contour detection is used.",
                _controls(self._model_badge, import_button),
                self._model_path,
            )
        )
        self._progress = QLabel()
        self._progress.setObjectName("rowDescription")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(page_body(tools_card, model_card, self._progress))
        self.refresh()

    def missing_required(self) -> list[Tool]:
        """Return installable tools that are not available yet."""
        return [tool for tool in INSTALLABLE_TOOLS if self._find(tool) is None]

    def refresh(self) -> None:
        """Update every badge, path and button."""
        for tool, row in self._rows.items():
            path = self._find(tool)
            plan = setup_plan(tool)
            missing_text = STATUS_MISSING if tool in INSTALLABLE_TOOLS else STATUS_OPTIONAL
            _set_badge(row.badge, ready=path is not None, missing_text=missing_text)
            row.path.set_full_text(str(path) if path else plan.description)
            if row.button is not None:
                row.button.setVisible(path is None and plan.method is not SetupMethod.MANUAL)
        model_path = self._settings().model_path
        _set_badge(self._model_badge, ready=model_available(model_path), missing_text=STATUS_MISSING)
        self._model_path.set_full_text(str(model_path) if model_path else "")

    def _tool_row(self, tool: Tool) -> QWidget:
        """Return the row of one tool and remember its widgets."""
        title, description = TOOL_TEXTS[tool]
        badge = QLabel()
        path = ElidedLabel()
        path.setObjectName("rowDescription")
        button = None
        if tool in INSTALLABLE_TOOLS:
            button = QPushButton("Install")
            button.clicked.connect(lambda: self._install(tool))
        self._rows[tool] = _ToolRow(badge, path, button)
        return setting_row(title, description, _controls(badge, button), path)

    def _find(self, tool: Tool) -> Path | None:
        """Find ``tool`` honouring the configured path."""
        return find_tool(tool, self._settings().tool_path(tool))

    def _install(self, tool: Tool) -> None:
        """Install ``tool`` (and Tesseract's languages) in the background."""
        languages = self._settings().ocr.languages

        def work(progress: Callable[[str, float], None]) -> Path:
            path = install(tool, progress)
            if tool is Tool.TESSERACT:
                install_tesseract_languages(languages, progress)
            return path

        self._set_busy(True)
        start_task(work, self._installed, self._failed, lambda message, _fraction: self._progress.setText(message))

    def _installed(self, path: object) -> None:
        """Report success."""
        self._set_busy(False)
        self._progress.setText(f"Installed: {path}")

    def _failed(self, error: Exception) -> None:
        """Report a failed installation."""
        self._set_busy(False)
        self._progress.setText("")
        QMessageBox.warning(self, "Installation failed", str(error))

    def _set_busy(self, busy: bool) -> None:
        """Disable install buttons while an installation runs."""
        for row in self._rows.values():
            if row.button is not None:
                row.button.setEnabled(not busy)
        if not busy:
            self.refresh()

    def _import_model(self) -> None:
        """Ask for a .tflite file and install it as the page corner model."""
        source, _ = QFileDialog.getOpenFileName(self, "Page corner model", "", "TFLite model (*.tflite)")
        target = self._settings().model_path
        if not source or target is None:
            return
        try:
            import_model(Path(source), target)
        except (OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        self.refresh()
        self.model_imported.emit(target)


def _controls(badge: QLabel, button: QPushButton | None) -> QWidget:
    """Badge and optional button side by side."""
    container = QWidget()
    container.setObjectName("rowControls")
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    layout.addWidget(badge)
    if button is not None:
        layout.addWidget(button)
    return container


def _set_badge(badge: QLabel, *, ready: bool, missing_text: str) -> None:
    """Show a green or amber status pill."""
    badge.setText(STATUS_READY if ready else missing_text)
    set_style_state(badge, BADGE_STATE, "ok" if ready else "missing")
