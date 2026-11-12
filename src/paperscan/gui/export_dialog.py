"""Review title, date, summary and file name before the PDFs are written; optionally ask the AI.

One dialog serves one or several documents: with several, a list on the left
shows every document and the form on the right edits the selected one. Folder,
"open after saving" and phone deletion apply to all of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, QSize, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
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
SINGLE_SIZE = QSize(720, 560)
BATCH_SIZE = QSize(1040, 620)
DOCUMENT_LIST_WIDTH = 270
DOCUMENT_ICON_SIZE = QSize(54, 72)


@dataclass(frozen=True)
class DocumentReview:
    """One document as offered for review.

    Attributes:
        suggestion: Prefilled title, date, summary and file name.
        page_count: Number of pages, shown in the document list.
        preview_jpeg: Small JPEG of the first page for the document list.
        analysed_by: Backend that already produced ``suggestion`` while processing, if any.
    """

    suggestion: ExportChoice
    page_count: int
    preview_jpeg: bytes = b""
    analysed_by: AnalysisProvider | None = None


def _ai_hint(providers: list[AnalysisProvider], analysed_by: AnalysisProvider | None) -> str:
    """Return the AI card's explanation for the current situation."""
    if analysed_by is not None:
        return f"✓ Suggested by {PROVIDER_LABELS.get(analysed_by, 'the AI')} while processing. Ask again for a new suggestion."
    if providers:
        return "Let a vision model read the pages and suggest title, date and summary."
    return NO_AI_HINT


class DocumentForm(QWidget):
    """AI suggestion card plus title, date, file name and summary of one document."""

    ai_requested = Signal(object)  # AnalysisProvider
    title_changed = Signal(str)

    def __init__(
        self,
        review: DocumentReview,
        *,
        style: FileDateStyle,
        providers: list[AnalysisProvider],
        preferred: AnalysisProvider,
        parent: QWidget | None = None,
    ) -> None:
        """Prefill the form with the document's suggestion."""
        super().__init__(parent)
        self._style = style
        self._keywords = review.suggestion.keywords
        self._file_name_edited = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._build_ai_card(providers, preferred, review.analysed_by))
        layout.addWidget(self._build_form(review.suggestion))
        layout.addStretch(1)

    def choice(self) -> ExportChoice:
        """Return the confirmed values."""
        return ExportChoice(
            title=self._title.text().strip(),
            document_date=self._chosen_date(),
            file_name=self._file_name.text().strip(),
            summary=self._summary.toPlainText().strip(),
            keywords=self._keywords,
        )

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

    def _build_form(self, suggestion: ExportChoice) -> QWidget:
        """Title, date, file name and summary."""
        self._title = QLineEdit(suggestion.title)
        self._title.setMaxLength(MAX_FILE_NAME_LENGTH)
        self._title.textChanged.connect(self._sync_file_name)
        self._title.textChanged.connect(self.title_changed)
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
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(10)
        form.addRow("Title", self._title)
        form.addRow("Date", self._date)
        form.addRow("File name", self._file_name)
        form.addRow("Summary", self._summary)
        return form_widget

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


class ExportDialog(QDialog):
    """Lets the user confirm or edit every document's metadata and file name, then save them all."""

    ai_requested = Signal(int, object)  # document index, AnalysisProvider

    def __init__(
        self,
        documents: list[DocumentReview],
        output_dir: Path,
        *,
        open_after: bool,
        style: FileDateStyle,
        providers: list[AnalysisProvider],
        preferred: AnalysisProvider,
        phone_photo_count: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        """Build one form per document; ``providers`` are the AI backends usable here."""
        super().__init__(parent)
        count = len(documents)
        self.setWindowTitle("Save PDF" if count == 1 else f"Save {count} PDFs")
        self.resize(SINGLE_SIZE if count == 1 else BATCH_SIZE)
        self._forms = [DocumentForm(review, style=style, providers=providers, preferred=preferred) for review in documents]
        for index, form in enumerate(self._forms):
            form.ai_requested.connect(lambda provider, index=index: self.ai_requested.emit(index, provider))
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.addLayout(self._build_documents(documents), 1)
        layout.addLayout(self._build_folder_row(output_dir))
        self._open_after = ToggleSwitch("Open the PDF after saving" if count == 1 else "Open the PDFs after saving")
        self._open_after.setChecked(open_after)
        layout.addWidget(self._open_after)
        self._delete_from_phone = ToggleSwitch(f"Delete the {phone_photo_count} photo(s) from the phone after saving")
        self._delete_from_phone.setToolTip("Asks again before deleting. Local copies in the import folder are kept.")
        self._delete_from_phone.setVisible(phone_photo_count > 0)
        layout.addWidget(self._delete_from_phone)
        layout.addLayout(self._build_buttons(count))

    # --- Public API -----------------------------------------------------------------------------

    def choices(self) -> list[ExportChoice]:
        """Return the confirmed values of every document, in document order."""
        return [form.choice() for form in self._forms]

    def output_dir(self) -> Path:
        """Return the chosen target folder."""
        return Path(self._folder.text().strip()).expanduser()

    def delete_from_phone(self) -> bool:
        """Return whether the imported phone photos should be deleted after saving."""
        return self._delete_from_phone.isVisible() and self._delete_from_phone.isChecked()

    def open_after(self) -> bool:
        """Return whether the PDFs should be opened after saving."""
        return self._open_after.isChecked()

    def apply_suggestion(self, index: int, suggestion: ExportChoice) -> None:
        """Fill in the AI's answer for document ``index``."""
        self._forms[index].apply_suggestion(suggestion)

    def ai_failed(self, index: int, message: str) -> None:
        """Report a failed AI request for document ``index``."""
        self._forms[index].ai_failed(message)

    # --- Construction ---------------------------------------------------------------------------

    def _build_documents(self, documents: list[DocumentReview]) -> QHBoxLayout:
        """Lay out the single form, or the document list next to a stack of forms."""
        row = QHBoxLayout()
        row.setSpacing(16)
        if len(self._forms) == 1:
            row.addWidget(self._forms[0])
            return row
        stack = QStackedWidget()
        document_list = QListWidget()
        document_list.setFixedWidth(DOCUMENT_LIST_WIDTH)
        document_list.setIconSize(DOCUMENT_ICON_SIZE)
        for number, (review, form) in enumerate(zip(documents, self._forms, strict=True), start=1):
            stack.addWidget(form)
            item = QListWidgetItem(_document_icon(review.preview_jpeg), "")
            document_list.addItem(item)
            form.title_changed.connect(lambda title, item=item, number=number, pages=review.page_count: _label(item, number, title, pages))
            _label(item, number, review.suggestion.title, review.page_count)
        document_list.currentRowChanged.connect(stack.setCurrentIndex)
        document_list.setCurrentRow(0)
        row.addWidget(document_list)
        row.addWidget(stack, 1)
        return row

    def _build_folder_row(self, output_dir: Path) -> QHBoxLayout:
        """Target folder shared by all documents."""
        self._folder = QLineEdit(str(output_dir))
        browse = QPushButton("Browse …")
        browse.setFixedWidth(BROWSE_WIDTH)
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(QLabel("Folder"))
        row.addWidget(self._folder, 1)
        row.addWidget(browse)
        return row

    def _build_buttons(self, count: int) -> QHBoxLayout:
        """Create the cancel and save buttons."""
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save PDF" if count == 1 else f"Save {count} PDFs")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(save)
        return row

    def _browse(self) -> None:
        """Pick another target folder."""
        folder = QFileDialog.getExistingDirectory(self, "Target folder", self._folder.text())
        if folder:
            self._folder.setText(folder)


def _document_icon(jpeg: bytes) -> QIcon:
    """Return the first-page preview as a list icon (empty if there is none)."""
    pixmap = QPixmap()
    pixmap.loadFromData(jpeg)
    return QIcon(pixmap)


def _label(item: QListWidgetItem, number: int, title: str, page_count: int) -> None:
    """Show the document number, its title and page count in the list."""
    pages = f"{page_count} page{'s' if page_count != 1 else ''}"
    item.setText(f"Document {number} · {pages}\n{title.strip() or '—'}")
