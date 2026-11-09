"""Review title, date, summary and file name before the PDF is written; optionally ask the AI."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from paperscan.analysis.vision import AnalysisProvider
from paperscan.export.naming import MAX_FILE_NAME_LENGTH, FileDateStyle, pdf_file_name
from paperscan.gui.setting_rows import Card
from paperscan.gui.toggle_switch import ToggleSwitch
from paperscan.pipeline import ExportChoice

#: Provider names shown in the AI dropdown.
PROVIDER_LABELS: dict[AnalysisProvider, str] = {
    AnalysisProvider.CLAUDE_CLI: "Claude Code",
    AnalysisProvider.CODEX_CLI: "Codex",
    AnalysisProvider.OPENAI: "OpenAI API",
}
ASK_AGAIN_TEXT = "✨  Ask again"
NO_AI_HINT = "No AI available — install Claude Code or Codex, or add an OpenAI key under Settings → AI."
SUMMARY_HEIGHT = 84
BROWSE_WIDTH = 84


def _ai_hint(providers: list[AnalysisProvider], analysed_by: AnalysisProvider | None) -> str:
    """Return the AI card's explanation for the current situation."""
    if analysed_by is not None:
        return f"✓ Suggested by {PROVIDER_LABELS.get(analysed_by, 'the AI')} while processing. Ask again for a new suggestion."
    if providers:
        return "Let a vision model read the pages and suggest title, date and summary."
    return NO_AI_HINT


class ExportDialog(QDialog):
    """Lets the user confirm or edit the PDF's metadata and file name."""

    ai_requested = Signal(object)  # AnalysisProvider

    def __init__(
        self,
        suggestion: ExportChoice,
        output_dir: Path,
        *,
        open_after: bool,
        style: FileDateStyle,
        providers: list[AnalysisProvider],
        preferred: AnalysisProvider,
        phone_photo_count: int = 0,
        analysed_by: AnalysisProvider | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Prefill the form with ``suggestion``; ``providers`` are the AI backends usable here.

        ``analysed_by`` names the backend that already produced ``suggestion`` during processing.
        """
        super().__init__(parent)
        self.setWindowTitle("Save PDF")
        self.resize(720, 560)
        self._style = style
        self._keywords = suggestion.keywords
        self._file_name_edited = False
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.addWidget(self._build_ai_card(providers, preferred, analysed_by))
        layout.addWidget(self._build_form(suggestion, output_dir))
        self._open_after = ToggleSwitch("Open the PDF after saving")
        self._open_after.setChecked(open_after)
        layout.addWidget(self._open_after)
        self._delete_from_phone = ToggleSwitch(f"Delete the {phone_photo_count} photo(s) from the phone after saving")
        self._delete_from_phone.setToolTip("Asks again before deleting. Local copies in the import folder are kept.")
        self._delete_from_phone.setVisible(phone_photo_count > 0)
        layout.addWidget(self._delete_from_phone)
        layout.addStretch(1)
        layout.addLayout(self._build_buttons())

    # --- Public API -----------------------------------------------------------------------------

    def choice(self) -> ExportChoice:
        """Return the confirmed values."""
        return ExportChoice(
            title=self._title.text().strip(),
            document_date=self._chosen_date(),
            file_name=self._file_name.text().strip(),
            summary=self._summary.toPlainText().strip(),
            keywords=self._keywords,
        )

    def output_dir(self) -> Path:
        """Return the chosen target folder."""
        return Path(self._folder.text().strip()).expanduser()

    def delete_from_phone(self) -> bool:
        """Return whether the imported phone photos should be deleted after saving."""
        return self._delete_from_phone.isVisible() and self._delete_from_phone.isChecked()

    def open_after(self) -> bool:
        """Return whether the PDF should be opened after saving."""
        return self._open_after.isChecked()

    def apply_suggestion(self, suggestion: ExportChoice) -> None:
        """Fill in the AI's answer (the file name only if the user has not edited it)."""
        self._title.setText(suggestion.title)
        if suggestion.document_date:
            self._set_date(suggestion.document_date)
        self._summary.setPlainText(suggestion.summary)
        self._keywords = suggestion.keywords
        self._ai_status.setText("✓ AI suggestion applied — review and adjust as you like.")
        self._ask_ai.setText(ASK_AGAIN_TEXT)
        self._ask_ai.setEnabled(True)

    def ai_failed(self, message: str) -> None:
        """Report a failed AI request."""
        self._ai_status.setText(f"AI request failed: {message}")
        self._ask_ai.setEnabled(True)

    # --- Construction ---------------------------------------------------------------------------

    def _build_ai_card(self, providers: list[AnalysisProvider], preferred: AnalysisProvider, analysed_by: AnalysisProvider | None) -> Card:
        """Build the provider choice and the button that asks the AI for title, date and summary."""
        self._provider = QComboBox()
        for provider in providers:
            self._provider.addItem(PROVIDER_LABELS[provider], provider)
        self._provider.setCurrentIndex(max(0, self._provider.findData(preferred)))
        self._ask_ai = QPushButton("✨  Ask AI")
        self._ask_ai.setObjectName("primary")
        self._ask_ai.clicked.connect(self._request_ai)
        self._ai_status = QLabel(_ai_hint(providers, analysed_by))
        if analysed_by is not None:
            self._ask_ai.setText(ASK_AGAIN_TEXT)
        self._ai_status.setObjectName("rowDescription")
        self._ai_status.setWordWrap(True)
        for widget in (self._provider, self._ask_ai):
            widget.setEnabled(bool(providers))
        controls = QHBoxLayout()
        controls.addWidget(QLabel("AI suggestion"))
        controls.addStretch(1)
        controls.addWidget(self._provider)
        controls.addWidget(self._ask_ai)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 12, 16, 12)
        body_layout.addLayout(controls)
        body_layout.addWidget(self._ai_status)
        card = Card()
        card.add_row(body)
        return card

    def _build_form(self, suggestion: ExportChoice, output_dir: Path) -> QWidget:
        """Title, date, file name, summary and folder."""
        self._title = QLineEdit(suggestion.title)
        self._title.setMaxLength(MAX_FILE_NAME_LENGTH)
        self._title.textChanged.connect(self._sync_file_name)
        self._date = QDateEdit()
        self._date.setCalendarPopup(True)
        self._date.setDisplayFormat("yyyy-MM-dd")
        self._set_date(suggestion.document_date or date.today())
        self._date.dateChanged.connect(self._sync_file_name)
        self._file_name = QLineEdit(suggestion.file_name)
        self._file_name.setMaxLength(MAX_FILE_NAME_LENGTH)
        self._file_name.setToolTip("Follows title and date until you edit it.")
        self._file_name.textEdited.connect(self._file_name_changed_by_user)
        self._summary = QPlainTextEdit(suggestion.summary)
        self._summary.setFixedHeight(SUMMARY_HEIGHT)
        self._folder = QLineEdit(str(output_dir))
        browse = QPushButton("Browse …")
        browse.setFixedWidth(BROWSE_WIDTH)
        browse.clicked.connect(self._browse)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self._folder, 1)
        folder_row.addWidget(browse)
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(10)
        form.addRow("Title", self._title)
        form.addRow("Date", self._date)
        form.addRow("File name", self._file_name)
        form.addRow("Summary", self._summary)
        form.addRow("Folder", folder_row)
        return form_widget

    def _build_buttons(self) -> QHBoxLayout:
        """Create the cancel and save buttons."""
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save PDF")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(save)
        return row

    # --- Reactions ------------------------------------------------------------------------------

    def _request_ai(self) -> None:
        """Ask the main window to run the AI with the chosen provider."""
        self._ask_ai.setEnabled(False)
        self._ai_status.setText(f"{self._provider.currentText()} is reading the pages …")
        # Qt hands enum members back as plain strings.
        self.ai_requested.emit(AnalysisProvider(self._provider.currentData()))

    def _file_name_changed_by_user(self) -> None:
        """Stop following title and date once the user typed a file name."""
        self._file_name_edited = True

    def _sync_file_name(self) -> None:
        """Derive the file name from title and date unless the user edited it."""
        if not self._file_name_edited:
            self._file_name.setText(pdf_file_name(self._title.text(), self._chosen_date(), self._style))

    def _chosen_date(self) -> date:
        """Return the date picker's value."""
        chosen = self._date.date()
        return date(chosen.year(), chosen.month(), chosen.day())

    def _set_date(self, value: date) -> None:
        """Show ``value`` in the date picker."""
        self._date.setDate(QDate(value.year, value.month, value.day))

    def _browse(self) -> None:
        """Pick another target folder."""
        folder = QFileDialog.getExistingDirectory(self, "Target folder", self._folder.text())
        if folder:
            self._folder.setText(folder)
