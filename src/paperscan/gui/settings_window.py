"""Settings window: sidebar navigation, page header, cards of setting rows.

Every row is declared once in :data:`PAGES` as a :class:`SettingField` bound to a
dotted settings path; widgets are created, loaded and read back generically.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from paperscan import __version__
from paperscan.analysis.vision import AnalysisProvider
from paperscan.app_paths import APP_NAME
from paperscan.export.jpeg_encoding import ChromaSubsampling
from paperscan.export.naming import FileDateStyle
from paperscan.gui.number_field import NumberField
from paperscan.gui.setting_rows import CONTROL_COLUMN_WIDTH, Card, page_body, setting_row
from paperscan.gui.setup_panel import SetupPanel
from paperscan.gui.toggle_switch import ToggleSwitch
from paperscan.platform_tools import Tool, find_tool
from paperscan.settings import AppSettings, ColorTheme, normalized, setting_value, with_setting

SIDEBAR_WIDTH = 220
NUMBER_WIDTH = 120
BROWSE_WIDTH = 84
AUTO_PLACEHOLDER = "Automatic"


class FieldKind(StrEnum):
    """Widget type of a setting row."""

    SWITCH = "switch"
    NUMBER = "number"
    TEXT = "text"
    SECRET = "secret"  # noqa: S105 - widget kind name, not a password
    FOLDER = "folder"
    FILE = "file"
    CHOICE = "choice"


#: Human-readable labels for enum values shown in dropdowns.
CHOICE_LABELS: dict[Enum, str] = {
    FileDateStyle.DOTS: "2026.11.09 Title.pdf",
    FileDateStyle.DASHES: "2026-11-09 Title.pdf",
    FileDateStyle.COMPACT: "20261109 Title.pdf",
    ColorTheme.SYSTEM: "Follow the system",
    ColorTheme.LIGHT: "Light",
    ColorTheme.DARK: "Dark",
    ChromaSubsampling.FULL: "4:4:4 — sharpest colour",
    ChromaSubsampling.HALF: "4:2:2 — balanced",
    ChromaSubsampling.QUARTER: "4:2:0 — smallest files",
    AnalysisProvider.NONE: "Off",
    AnalysisProvider.OPENAI: "OpenAI API",
    AnalysisProvider.CLAUDE_CLI: "Claude Code (local CLI)",
    AnalysisProvider.CODEX_CLI: "Codex (local CLI)",
}


@dataclass(frozen=True)
class SettingField:
    """One row: label and description on the left, a control bound to ``path`` on the right."""

    path: str
    kind: FieldKind
    title: str
    description: str = ""
    minimum: int = 0
    maximum: int = 0
    choices: type[Enum] | None = None
    #: For FILE fields that point to a tool: shows the automatically found path as placeholder.
    tool: Tool | None = None


@dataclass(frozen=True)
class SettingsSection:
    """A titled card of rows."""

    title: str
    fields: tuple[SettingField, ...]


@dataclass(frozen=True)
class SettingsPage:
    """A sidebar entry with its sections."""

    title: str
    subtitle: str
    sections: tuple[SettingsSection, ...]


PAGES: tuple[SettingsPage, ...] = (
    SettingsPage(
        "General",
        "Where PDFs go and how they look",
        (
            SettingsSection(
                "OUTPUT",
                (
                    SettingField("output_dir", FieldKind.FOLDER, "Output folder", "Finished PDFs are saved here."),
                    SettingField(
                        "file_date_style", FieldKind.CHOICE, "Date in file names", "Start of every PDF name.", choices=FileDateStyle
                    ),
                    SettingField("open_after_export", FieldKind.SWITCH, "Open PDF after saving", "Shows the PDF in your default viewer."),
                ),
            ),
            SettingsSection(
                "APPEARANCE",
                (SettingField("color_theme", FieldKind.CHOICE, "Theme", "Applies after a restart.", choices=ColorTheme),),
            ),
        ),
    ),
    SettingsPage(
        "Phone",
        "Import photos over USB debugging (ADB)",
        (
            SettingsSection(
                "CAMERA",
                (
                    SettingField("phone.camera_dir", FieldKind.TEXT, "Camera folder", "Folder on the phone."),
                    SettingField(
                        "phone.list_count", FieldKind.NUMBER, "Photos to list", "How many of the newest photos to offer.", 5, 1000
                    ),
                    SettingField("import_dir", FieldKind.FOLDER, "Import folder", "Local copies of phone photos."),
                ),
            ),
            SettingsSection(
                "CONNECTION",
                (
                    SettingField("phone.serial", FieldKind.TEXT, "Device serial", "Only needed with several devices connected."),
                    SettingField("phone.adb_path", FieldKind.FILE, "adb", "Leave empty to find it automatically.", tool=Tool.ADB),
                ),
            ),
        ),
    ),
    SettingsPage(
        "Image & JPEG",
        "Size and compression of the pages inside the PDF",
        (
            SettingsSection(
                "PAGES",
                (
                    SettingField("max_edge", FieldKind.NUMBER, "Longest page edge", "Pixels; 0 keeps the full resolution.", 0, 20000),
                    SettingField(
                        "jpeg.quality", FieldKind.NUMBER, "JPEG quality", "68–96 recommended; jpegli stays sharp at lower values.", 1, 100
                    ),
                    SettingField(
                        "jpeg.subsampling",
                        FieldKind.CHOICE,
                        "Chroma subsampling",
                        "Colour resolution of the JPEG.",
                        choices=ChromaSubsampling,
                    ),
                ),
            ),
            SettingsSection(
                "ENCODER",
                (
                    SettingField("jpeg.use_jpegli", FieldKind.SWITCH, "Use jpegli", "Falls back to standard JPEG if cjpegli is missing."),
                    SettingField(
                        "jpeg.cjpegli_path", FieldKind.FILE, "cjpegli", "Leave empty to find it automatically.", tool=Tool.CJPEGLI
                    ),
                ),
            ),
        ),
    ),
    SettingsPage(
        "Text recognition",
        "Searchable, selectable text in the PDF",
        (
            SettingsSection(
                "OCR",
                (
                    SettingField(
                        "ocr.enabled",
                        FieldKind.SWITCH,
                        "Add text layer",
                        "Invisible text at the right positions, selectable in any viewer.",
                    ),
                    SettingField("ocr.languages", FieldKind.TEXT, "Languages", "Tesseract codes joined with +, e.g. deu+eng."),
                    SettingField(
                        "ocr.tesseract_path", FieldKind.FILE, "Tesseract", "Leave empty to find it automatically.", tool=Tool.TESSERACT
                    ),
                ),
            ),
        ),
    ),
    SettingsPage(
        "AI",
        "Title, document date, summary and OCR correction",
        (
            SettingsSection(
                "ANALYSIS",
                (
                    SettingField(
                        "ai.provider.provider",
                        FieldKind.CHOICE,
                        "Provider",
                        "Which vision model reads the pages.",
                        choices=AnalysisProvider,
                    ),
                    SettingField(
                        "ai.correct_ocr_text",
                        FieldKind.SWITCH,
                        "Correct OCR text",
                        "The model fixes recognition errors; positions stay exact.",
                    ),
                    SettingField("ai.language_hint", FieldKind.TEXT, "Language", "Language of titles and summaries."),
                    SettingField(
                        "ai.image_edge", FieldKind.NUMBER, "Image size for AI", "Longest edge of the page images sent.", 512, 4096
                    ),
                ),
            ),
            SettingsSection(
                "OPENAI API",
                (
                    SettingField("ai.provider.openai_model", FieldKind.TEXT, "Model"),
                    SettingField(
                        "ai.provider.openai_api_key",
                        FieldKind.SECRET,
                        "API key",
                        "Stored only in your settings file; empty = OPENAI_API_KEY.",
                    ),
                ),
            ),
            SettingsSection(
                "LOCAL CLIS",
                (
                    SettingField(
                        "ai.provider.claude_path", FieldKind.FILE, "Claude Code", "Leave empty to find it automatically.", tool=Tool.CLAUDE
                    ),
                    SettingField(
                        "ai.provider.codex_path", FieldKind.FILE, "Codex", "Leave empty to find it automatically.", tool=Tool.CODEX
                    ),
                ),
            ),
        ),
    ),
)
TOOLS_PAGE = ("Tools", "External programs and the border model")


class SettingsWindow(QWidget):
    """Edits a copy of the settings; :attr:`saved` publishes the result."""

    saved = Signal(object)  # AppSettings

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        """Build all pages and load ``settings``."""
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(f"{APP_NAME} settings")
        self.resize(980, 680)
        self._settings = settings
        self._readers: dict[str, Callable[[], Any]] = {}
        self._writers: dict[str, Callable[[Any], None]] = {}
        self._tool_fields: dict[Tool, QLineEdit] = {}
        self._nav = QListWidget()
        self._nav.setObjectName("nav")
        self._nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._stack = QStackedWidget()
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        root.addLayout(self._build_content(), 1)
        for page in PAGES:
            cards = [self._build_section(section) for section in page.sections]
            self._add_page(page.title, page.subtitle, page_body(*cards))
        self._setup_panel = SetupPanel(self.current_settings)
        self._add_page(*TOOLS_PAGE, self._setup_panel)
        self._nav.currentRowChanged.connect(self._show_page)
        self._nav.setCurrentRow(0)
        self.load(settings)

    def load(self, settings: AppSettings) -> None:
        """Show ``settings`` in all controls."""
        self._settings = settings
        for path, write in self._writers.items():
            write(setting_value(settings, path))
        self._refresh_tool_placeholders()
        self._setup_panel.refresh()

    def sync_fields(self, settings: AppSettings, paths: tuple[str, ...]) -> None:
        """Take over ``paths`` from ``settings`` (changed elsewhere) without touching other unsaved edits."""
        for path in paths:
            self._settings = with_setting(self._settings, path, setting_value(settings, path))
            self._writers[path](setting_value(settings, path))

    def current_settings(self) -> AppSettings:
        """Return the settings as currently entered (not yet saved)."""
        settings = self._settings
        for path, read in self._readers.items():
            settings = with_setting(settings, path, read())
        return normalized(settings)

    def open_tools_page(self) -> None:
        """Jump to the tools page (used by the first-start check)."""
        self._nav.setCurrentRow(len(PAGES))

    # --- Layout ---------------------------------------------------------------------------------

    def _build_sidebar(self) -> QFrame:
        """Brand, version and navigation."""
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 18, 14, 14)
        brand = QLabel(APP_NAME)
        brand.setObjectName("brand")
        version = QLabel(f"Version {__version__}")
        version.setObjectName("brandVersion")
        layout.addWidget(brand)
        layout.addWidget(version)
        layout.addSpacing(18)
        layout.addWidget(self._nav, 1)
        return sidebar

    def _build_content(self) -> QVBoxLayout:
        """Page header, page stack and footer with the save button."""
        header = QVBoxLayout()
        header.setContentsMargins(28, 22, 28, 6)
        header.setSpacing(2)
        self._page_title = QLabel()
        self._page_title.setObjectName("pageTitle")
        self._page_subtitle = QLabel()
        self._page_subtitle.setObjectName("pageSubtitle")
        header.addWidget(self._page_title)
        header.addWidget(self._page_subtitle)
        stack_box = QVBoxLayout()
        stack_box.setContentsMargins(28, 0, 28, 16)
        stack_box.addWidget(self._stack)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addLayout(stack_box, 1)
        layout.addWidget(self._build_footer())
        return layout

    def _build_footer(self) -> QFrame:
        """Close and save buttons on a separated footer."""
        footer = QFrame()
        footer.setObjectName("footer")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(28, 12, 28, 12)
        layout.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        save = QPushButton("Save")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        layout.addWidget(close)
        layout.addWidget(save)
        return footer

    def _add_page(self, title: str, subtitle: str, body: QWidget) -> None:
        """Register a page in the sidebar and the stack."""
        self._nav.addItem(title)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        scroll.setProperty("pageTitle", title)
        scroll.setProperty("pageSubtitle", subtitle)
        self._stack.addWidget(scroll)

    def _show_page(self, index: int) -> None:
        """Switch the visible page and its header."""
        self._stack.setCurrentIndex(index)
        page = self._stack.currentWidget()
        if page is None:
            return
        self._page_title.setText(page.property("pageTitle"))
        self._page_subtitle.setText(page.property("pageSubtitle"))

    def _build_section(self, section: SettingsSection) -> QWidget:
        """Return a small heading plus a card with one row per field."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        heading = QLabel(section.title)
        heading.setObjectName("hint")
        card = Card()
        for spec in section.fields:
            card.add_row(setting_row(spec.title, spec.description, self._build_control(spec)))
        layout.addWidget(heading)
        layout.addWidget(card)
        return container

    # --- Controls -------------------------------------------------------------------------------

    def _build_control(self, spec: SettingField) -> QWidget:
        """Create the control for ``spec`` and bind it."""
        match spec.kind:
            case FieldKind.SWITCH:
                return self._switch(spec)
            case FieldKind.NUMBER:
                return self._number(spec)
            case FieldKind.CHOICE:
                return self._choice(spec)
            case FieldKind.FOLDER | FieldKind.FILE:
                return self._path(spec)
            case FieldKind.TEXT | FieldKind.SECRET:
                return self._text(spec)

    def _bind(self, path: str, read: Callable[[], Any], write: Callable[[Any], None]) -> None:
        """Register how a control is read and written."""
        self._readers[path] = read
        self._writers[path] = write

    def _switch(self, spec: SettingField) -> ToggleSwitch:
        switch = ToggleSwitch()
        self._bind(spec.path, switch.isChecked, lambda value: switch.setChecked(bool(value)))
        return switch

    def _number(self, spec: SettingField) -> NumberField:
        field = NumberField(spec.minimum, spec.maximum)
        field.setFixedWidth(NUMBER_WIDTH)
        self._bind(spec.path, field.value, lambda value: field.setValue(int(value)))
        return field

    def _choice(self, spec: SettingField) -> QComboBox:
        combo = QComboBox()
        combo.setFixedWidth(CONTROL_COLUMN_WIDTH)
        assert spec.choices is not None  # declared for every CHOICE field
        for member in spec.choices:
            combo.addItem(CHOICE_LABELS.get(member, str(member.value)), member)
        choices = spec.choices
        # Qt hands enum members back as plain strings; convert them to the enum again.
        self._bind(spec.path, lambda: choices(combo.currentData()), lambda value: combo.setCurrentIndex(max(0, combo.findData(value))))
        return combo

    def _text(self, spec: SettingField) -> QLineEdit:
        edit = QLineEdit()
        edit.setFixedWidth(CONTROL_COLUMN_WIDTH)
        if spec.kind is FieldKind.SECRET:
            edit.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self._bind(spec.path, lambda: edit.text().strip(), lambda value: edit.setText(str(value)))
        return edit

    def _path(self, spec: SettingField) -> QWidget:
        container = QWidget()
        container.setFixedWidth(CONTROL_COLUMN_WIDTH)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        edit = QLineEdit()
        browse = QPushButton("Browse …")
        browse.setFixedWidth(BROWSE_WIDTH)
        browse.clicked.connect(lambda: self._browse(edit, spec.kind))
        layout.addWidget(edit, 1)
        layout.addWidget(browse)
        if spec.tool is not None:
            self._tool_fields[spec.tool] = edit
        self._bind(spec.path, lambda: edit.text().strip(), lambda value: edit.setText(str(value)))
        return container

    def _refresh_tool_placeholders(self) -> None:
        """Show what automatic detection finds in empty tool path fields."""
        for tool, edit in self._tool_fields.items():
            found = find_tool(tool)
            edit.setPlaceholderText(f"{AUTO_PLACEHOLDER}: {found}" if found else f"{AUTO_PLACEHOLDER}: not found")
            edit.setToolTip(str(found) if found else "")

    def _browse(self, edit: QLineEdit, kind: FieldKind) -> None:
        """Pick a folder or file for a path field."""
        if kind is FieldKind.FOLDER:
            chosen = QFileDialog.getExistingDirectory(self, "Choose folder", edit.text())
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, "Choose program", edit.text())
        if chosen:
            edit.setText(chosen)

    def _save(self) -> None:
        """Publish the edited settings."""
        self._settings = self.current_settings()
        self.saved.emit(self._settings)
        self._setup_panel.refresh()
