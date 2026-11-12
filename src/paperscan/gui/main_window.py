"""Main window: one document at a time, from photos to a saved PDF.

The window wires widgets to the :class:`DocumentSession` and runs every slow
step (loading, detection, previews, phone access, export) as a background task.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from paperscan.analysis.vision import AnalysisProvider, available_providers
from paperscan.app_paths import APP_NAME, LOG_FILE
from paperscan.capture.adb_import import AdbError, RemotePhoto
from paperscan.capture.photo_files import SERIES_GAP, is_photo, photos_in_folder
from paperscan.comparison import rate_renders, render_page_variants
from paperscan.gui.compare_dialog import CompareDialog
from paperscan.gui.corner_editor import CornerEditor
from paperscan.gui.export_dialog import PROVIDER_LABELS, DocumentReview, ExportDialog
from paperscan.gui.filter_panel import FilterPanel
from paperscan.gui.image_view import ImageView
from paperscan.gui.images import image_to_pixmap, to_qimage
from paperscan.gui.line_icons import LineIcon, line_icon
from paperscan.gui.output_panel import OutputPanel
from paperscan.gui.page_strip import STRIP_WIDTH, PageStrip
from paperscan.gui.page_toolbar import PageAction, PageToolbar
from paperscan.gui.phone_connection import PhoneSearchDialog, SearchOutcome, find_phone
from paperscan.gui.phone_dialog import PhoneDialog
from paperscan.gui.settings_window import SettingsWindow
from paperscan.gui.setup_panel import SetupPanel
from paperscan.gui.tasks import start_task
from paperscan.gui.theme import set_style_state
from paperscan.gui.workflow_panel import Capability, WorkflowPanel
from paperscan.imaging.filter_variants import preset_variants
from paperscan.imaging.filters import FilterSettings
from paperscan.imaging.geometry import Quad
from paperscan.pipeline import (
    ExportCancelledError,
    ExportChoice,
    ExportDraft,
    prepare_documents,
    reanalyse,
    suggested_choice,
    write_export,
)
from paperscan.platform_tools import Tool, find_tool
from paperscan.session import DocumentSession, SessionPage, full_photo, load_session_page, render_final, render_preview
from paperscan.settings import AppSettings, SettingsStore, with_setting

log = logging.getLogger(__name__)

#: Delay before re-rendering the preview after an edit, in milliseconds.
PREVIEW_DEBOUNCE_MS = 120
TOOLBAR_ICON_SIZE = 18
TOOLBAR_ICON_COLOR = "#3a4049"
BRAND_ICON_SIZE = 22
BRAND_MARGINS = (6, 0, 10, 0)
#: Gap around the work area, so it reads as a surface of its own below the header.
WORKSPACE_MARGINS = (12, 12, 12, 10)
#: Settings edited directly in the output panel.
OUTPUT_PANEL_PATHS = ("max_edge", "jpeg.quality")
#: Settings remembered from the save dialog.
EXPORT_DIALOG_PATHS = ("output_dir", "open_after_export")
#: Longest edge of page strip thumbnails.
THUMBNAIL_EDGE = 320
#: Longest edge of phone photo previews when no EXIF thumbnail is embedded.
PHONE_THUMBNAIL_EDGE = 400
FILTER_PANEL_WIDTH = 380
#: Initial editor/preview split (only the ratio matters): the finished page gets more room.
EDITOR_SHARE = 2
PREVIEW_SHARE = 3
#: Pixels per share for the initial splitter sizes (Qt ignores tiny values before the window is shown).
SPLIT_UNIT = 300
PROGRESS_STEPS = 1000
PROGRESS_BAR_WIDTH = 180
PROGRESS_BAR_HEIGHT = 6
PHOTO_FILE_FILTER = "Photos (*.jpg *.jpeg *.png *.heic *.webp *.tif *.tiff)"


@dataclass(frozen=True)
class ToolbarEntry:
    """One action of the main toolbar."""

    key: str
    text: str
    icon: LineIcon
    handler: Callable[[], object]
    shortcut: str


@dataclass(frozen=True)
class _PreviewResult:
    """A rendered preview tagged with the request it answers."""

    generation: int
    index: int
    image: Any  # QImage, created in the worker


class MainWindow(QMainWindow):
    """Collects pages, lets the user edit them and exports one PDF."""

    def __init__(self, store: SettingsStore, settings: AppSettings) -> None:
        """Build the window for an empty document."""
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 860)
        self.setAcceptDrops(True)
        self._store = store
        self._settings = settings
        self._session = DocumentSession(settings.default_filter)
        self._current = -1
        self._preview_generation = 0
        #: Generation whose final-quality preview is on screen (quick previews must not replace it).
        self._final_shown = -1
        self._busy_jobs = 0
        self._cancel_export = threading.Event()
        self._settings_window: SettingsWindow | None = None
        self._preview_timer = QTimer(self, singleShot=True, interval=PREVIEW_DEBOUNCE_MS)
        self._preview_timer.timeout.connect(self._render_current_preview)
        self._build_ui()
        self._update_actions()

    # --- Construction ---------------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Toolbar, page strip, editor, preview, filter panel and status bar."""
        self.addToolBar(self._build_toolbar())
        self._strip = PageStrip()
        self._strip.setFixedWidth(STRIP_WIDTH)
        self._strip.context_requested.connect(self._show_page_menu)
        self._page_toolbar = PageToolbar()
        self._page_toolbar.action_requested.connect(self._page_action)
        self.addActions(self._page_toolbar.actions_for_window)
        self._strip.page_selected.connect(self._select_page)
        self._strip.page_moved.connect(self._move_page)
        self._editor = CornerEditor()
        self._editor.quad_changed.connect(self._quad_edited)
        self._preview = ImageView("Add photos from the phone or a folder to start.")
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(*WORKSPACE_MARGINS)
        layout.addWidget(self._strip)
        layout.addWidget(self._build_views(), 1)
        layout.addLayout(self._build_side_panel())
        self.setCentralWidget(central)
        self._build_status_bar()
        self._refresh_capabilities()

    def _build_views(self) -> QSplitter:
        """Corner editor (with the page actions above it) next to the finished-page preview."""
        views = QSplitter()
        views.setChildrenCollapsible(False)
        editor_column = QWidget()
        editor_layout = QVBoxLayout(editor_column)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(4)
        editor_layout.addWidget(self._page_toolbar)
        editor_layout.addWidget(self._editor, 1)
        views.addWidget(editor_column)
        views.addWidget(self._preview)
        views.setStretchFactor(0, EDITOR_SHARE)
        views.setStretchFactor(1, PREVIEW_SHARE)
        views.setSizes([EDITOR_SHARE * SPLIT_UNIT, PREVIEW_SHARE * SPLIT_UNIT])
        return views

    def _build_side_panel(self) -> QVBoxLayout:
        """Filter, output and workflow panels stacked on the right."""
        self._filters = FilterPanel()
        self._filters.setFixedWidth(FILTER_PANEL_WIDTH)
        self._filters.settings_changed.connect(self._filter_edited)
        self._filters.compare_requested.connect(self._compare_filters)
        self._filters.show_settings(self._session.shared_filter, own_filter=False)
        self._output = OutputPanel()
        self._output.setFixedWidth(FILTER_PANEL_WIDTH)
        self._output.show_values(self._settings.max_edge, self._settings.jpeg.quality)
        self._output.changed.connect(self._output_edited)
        self._workflow = WorkflowPanel()
        self._workflow.setFixedWidth(FILTER_PANEL_WIDTH)
        self._workflow.create_requested.connect(self._export)
        side = QVBoxLayout()
        side.addWidget(self._filters, 1)
        side.addWidget(self._output)
        side.addWidget(self._workflow)
        return side

    def _build_toolbar(self) -> QToolBar:
        """Create the app header: brand, adding pages, comparing, and app-wide actions on the right.

        Page actions live above the editor, the export in the workflow panel, so the header
        only holds what starts or frames the work.
        """
        toolbar = QToolBar("Main")
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.setIconSize(QSize(TOOLBAR_ICON_SIZE, TOOLBAR_ICON_SIZE))
        self._actions: dict[str, QAction] = {}
        add_pages = [
            ToolbarEntry("phone", "From phone", LineIcon.PHONE, self._import_from_phone, "Ctrl+P"),
            ToolbarEntry("files", "Add photos", LineIcon.ADD_PHOTOS, self._add_photos, "Ctrl+O"),
            ToolbarEntry("folder", "Add folder", LineIcon.ADD_FOLDER, self._add_folder, "Ctrl+Shift+O"),
        ]
        review = [ToolbarEntry("compare", "Compare filters", LineIcon.COMPARE, self._compare_filters, "Ctrl+K")]
        app_wide = [
            ToolbarEntry("new", "New document", LineIcon.NEW_DOCUMENT, self._new_document, "Ctrl+N"),
            ToolbarEntry("settings", "Settings", LineIcon.SETTINGS, self._open_settings, "Ctrl+,"),
        ]
        toolbar.addWidget(_toolbar_brand())
        for group in (add_pages, review):
            toolbar.addSeparator()
            self._add_toolbar_entries(toolbar, group)
        toolbar.addWidget(_toolbar_spacer())
        self._add_toolbar_entries(toolbar, app_wide)
        export = QAction("Create PDF", self)
        export.setShortcut(QKeySequence("Ctrl+S"))
        export.triggered.connect(self._export)
        self.addAction(export)
        self._actions["export"] = export
        return toolbar

    def _add_toolbar_entries(self, toolbar: QToolBar, entries: list[ToolbarEntry]) -> None:
        """Add one action per entry and remember it under its key."""
        for entry in entries:
            action = QAction(line_icon(entry.icon, TOOLBAR_ICON_COLOR, TOOLBAR_ICON_SIZE), entry.text, self)
            action.triggered.connect(entry.handler)
            action.setShortcut(QKeySequence(entry.shortcut))
            action.setToolTip(f"{entry.text} ({entry.shortcut})")
            toolbar.addAction(action)
            self._actions[entry.key] = action

    def _build_status_bar(self) -> None:
        """Status text, progress bar and cancel button."""
        self._status = QLabel("Ready")
        self._status.setObjectName("statusText")
        self._progress = QProgressBar()
        self._progress.setObjectName("statusProgress")
        self._progress.setRange(0, PROGRESS_STEPS)
        self._progress.setTextVisible(False)
        self._progress.setFixedSize(PROGRESS_BAR_WIDTH, PROGRESS_BAR_HEIGHT)
        self._progress.hide()
        self._cancel = QPushButton("Cancel")
        self._cancel.setObjectName("link")
        self._cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel.clicked.connect(self._cancel_export.set)
        self._cancel.hide()
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        bar.addWidget(self._status, 1)
        bar.addPermanentWidget(self._progress)
        bar.addPermanentWidget(self._cancel)

    # --- First start ----------------------------------------------------------------------------

    def offer_setup_if_needed(self) -> None:
        """Offer the setup assistant when required tools are missing."""
        missing = SetupPanel(lambda: self._settings).missing_required()
        if not missing:
            return
        names = ", ".join(tool.value for tool in missing)
        answer = QMessageBox.question(self, "Set up tools", f"Not installed yet: {names}.\n\nOpen the setup assistant to install them?")
        if answer == QMessageBox.StandardButton.Yes:
            self._open_settings().open_tools_page()

    # --- Adding pages ---------------------------------------------------------------------------

    def _add_photos(self) -> None:
        """Pick photo files."""
        files, _ = QFileDialog.getOpenFileNames(self, "Add photos", "", PHOTO_FILE_FILTER)
        self._load_paths([Path(file) for file in files])

    def _add_folder(self) -> None:
        """Add every photo of a folder, oldest first."""
        folder = QFileDialog.getExistingDirectory(self, "Add folder")
        if folder:
            self._load_paths(photos_in_folder(Path(folder)))

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt API
        """Accept dropped files and folders."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt API
        """Add dropped photos (folders are expanded)."""
        paths: list[Path] = []
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            paths.extend(photos_in_folder(path) if path.is_dir() else [path] if is_photo(path) else [])
        self._load_paths(paths)

    def _import_from_phone(self) -> None:
        """List the phone's newest photos in the background, then let the user pick."""
        settings = self._settings

        def work(_progress: Callable[[str, float], None]) -> list[RemotePhoto]:
            client = settings.phone.adb_client()
            return client.list_photos(settings.phone.camera_dir, settings.phone.list_count)

        self._run("Reading the phone …", work, self._choose_phone_photos, self._phone_unavailable)

    def _phone_unavailable(self, error: Exception) -> None:
        """Keep looking for the phone in a dialog; continue the import once it appears."""
        if not isinstance(error, AdbError):
            self._task_failed(error)
            return
        self._set_status("No phone found")
        dialog = PhoneSearchDialog(str(error), lambda: find_phone(self._settings.phone), self)
        dialog.exec()
        match dialog.outcome():
            case SearchOutcome.FOUND:
                self._import_from_phone()
            case SearchOutcome.OPEN_SETTINGS:
                self._open_settings().open_phone_page()
            case SearchOutcome.CANCELLED:
                self._set_status("Ready")

    def _choose_phone_photos(self, photos: list[RemotePhoto]) -> None:
        """Show the phone dialog and pull the chosen photos."""
        if not photos:
            QMessageBox.information(self, "Phone", "No photos found in the camera folder.")
            return
        settings = self._settings
        client = settings.phone.adb_client()
        import_dir = Path(settings.import_dir).expanduser()
        dialog = PhoneDialog(photos, lambda photo: client.thumbnail(photo, import_dir, PHONE_THUMBNAIL_EDGE), self)
        if not dialog.exec() or not dialog.selected_photos():
            return
        chosen = dialog.selected_photos()

        def work(progress: Callable[[str, float], None]) -> list[tuple[Path, RemotePhoto]]:
            pulled = []
            for number, photo in enumerate(chosen, start=1):
                progress(f"Copying {photo.name}", number / len(chosen))
                pulled.append((client.pull(photo, import_dir), photo))
            return pulled

        self._run("Copying photos …", work, lambda pulled: self._load_paths([path for path, _ in pulled], dict(pulled)))

    def _load_paths(self, paths: list[Path], remotes: dict[Path, RemotePhoto] | None = None) -> None:
        """Load photos, detect their pages and append them (in the given order); ``remotes`` maps phone imports."""
        if not paths:
            return
        settings = self._settings

        def work(progress: Callable[[str, float], None]) -> list[SessionPage]:
            pages = []
            for number, path in enumerate(paths, start=1):
                progress(f"Detecting page in {path.name}", number / len(paths))
                pages.append(load_session_page(path, settings, (remotes or {}).get(path)))
            return pages

        self._run("Loading photos …", work, self._pages_loaded)

    def _pages_loaded(self, pages: list[SessionPage]) -> None:
        """Append loaded pages and show the first new one."""
        first_new = len(self._session)
        for page in pages:
            self._session.add(page)
        self._refresh_strip()
        for index in range(first_new, len(self._session)):
            self._render_thumbnail(index)
        self._select_page(first_new)

    # --- Editing --------------------------------------------------------------------------------

    def _select_page(self, index: int) -> None:
        """Show page ``index`` in the editor, filter panel and preview."""
        if not 0 <= index < len(self._session):
            self._current = -1
            self._editor.clear_page()
            self._preview.clear_image()
            self._update_actions()
            return
        self._current = index
        entry = self._session.page(index)
        self._strip.select(index)
        self._editor.show_page(entry.preview_rgb, entry.page.edit.quad.scaled(entry.preview_scale))
        self._filters.show_settings(entry.page.edit.filter, entry.own_filter)
        self._update_actions()
        self._schedule_preview()
        start_task(lambda _progress: full_photo(entry), lambda photo: self._detail_ready(index, photo), self._task_failed)

    def _detail_ready(self, index: int, photo: object) -> None:
        """Give the loupe the full-resolution photo if the page is still shown."""
        if index == self._current:
            entry = self._session.page(index)
            self._editor.set_detail_image(photo, 1 / entry.preview_scale)  # type: ignore[arg-type]

    def _quad_edited(self, preview_quad: Quad) -> None:
        """Store the dragged outline in source coordinates."""
        if self._current < 0:
            return
        entry = self._session.page(self._current)
        self._session.set_quad(self._current, preview_quad.scaled(1 / entry.preview_scale))
        self._schedule_preview()

    def _filter_edited(self, settings: FilterSettings, only_this_page: bool) -> None:
        """Apply a filter change to this page or the whole document."""
        if self._current >= 0 and not only_this_page and self._session.page(self._current).own_filter:
            self._session.follow_shared_filter(self._current)
        self._session.set_filter(self._current if self._current >= 0 else None, settings, only_this_page=only_this_page)
        self._schedule_preview()
        if not only_this_page:
            for index in range(len(self._session)):
                self._render_thumbnail(index)

    def _page_action(self, action: PageAction) -> None:
        """Run an action on the current page."""
        handlers: dict[PageAction, Callable[[], None]] = {
            PageAction.ROTATE_LEFT: lambda: self._rotate(clockwise=False),
            PageAction.ROTATE_RIGHT: lambda: self._rotate(clockwise=True),
            PageAction.DETECT_EDGES: self._detect_again,
            PageAction.WHOLE_PHOTO: self._use_full_photo,
            PageAction.REMOVE: self._remove_page,
        }
        handlers[action]()

    def _show_page_menu(self, position: QPoint) -> None:
        """Right-click menu on a page thumbnail (the clicked page is already selected)."""
        index = self._current
        in_first_document = index in self._session.document_ranges()[0]
        menu = QMenu(self)
        menu.addActions(self._page_toolbar.actions_for_window)
        menu.addSeparator()
        entries = [
            ("New document starting with this page", self._split_document, index > 0 and not self._session.starts_document(index)),
            ("Join with the previous document", self._join_document, not in_first_document),
            ("Split at pauses between photos", self._split_at_pauses, len(self._session) > 1),
        ]
        for text, handler, enabled in entries:
            action = menu.addAction(text)
            action.triggered.connect(handler)
            action.setEnabled(enabled)
        menu.exec(position)

    def _split_document(self) -> None:
        """Make the current page (and the pages after it in its document) a new document."""
        self._session.split_before(self._current)
        self._documents_changed()

    def _join_document(self) -> None:
        """Merge the current page's document into the one before it."""
        self._session.join_with_previous(self._current)
        self._documents_changed()

    def _split_at_pauses(self) -> None:
        """Start a new document wherever photos were taken minutes apart."""
        count = self._session.split_at_pauses(SERIES_GAP)
        minutes = int(SERIES_GAP.total_seconds() // 60)
        self._documents_changed()
        self._set_status(f"{count} document(s) · split where photos are more than {minutes} minutes apart")

    def _documents_changed(self) -> None:
        """Show the new split in the strip and the workflow panel."""
        self._refresh_strip()
        count = len(self._session.document_ranges())
        self._set_status(f"{count} documents · each becomes its own PDF" if count > 1 else "One document")

    def _rotate(self, *, clockwise: bool) -> None:
        """Turn the current page."""
        if self._current >= 0:
            self._session.rotate(self._current, clockwise)
            self._schedule_preview()

    def _detect_again(self) -> None:
        """Re-run border detection on the current page."""
        if self._current >= 0:
            self._session.reset_quad(self._current, self._settings)
            self._select_page(self._current)

    def _use_full_photo(self) -> None:
        """Use the whole photo as the page (no cropping)."""
        if self._current >= 0:
            self._session.full_quad(self._current)
            self._select_page(self._current)

    def _remove_page(self) -> None:
        """Remove the current page."""
        if self._current < 0:
            return
        self._session.remove(self._current)
        self._refresh_strip()
        for index in range(len(self._session)):
            self._render_thumbnail(index)
        self._select_page(min(self._current, len(self._session) - 1))

    def _move_page(self, source: int, target: int) -> None:
        """Reorder pages after a drag in the strip."""
        self._session.move(source, target)
        self._refresh_strip()
        for index in range(len(self._session)):
            self._render_thumbnail(index)
        self._select_page(target)

    def _new_document(self) -> None:
        """Start over with an empty document."""
        if (
            len(self._session)
            and QMessageBox.question(self, "New document", "Discard the current pages?") != QMessageBox.StandardButton.Yes
        ):
            return
        self._session.clear()
        self._refresh_strip()
        self._select_page(-1)

    def _compare_filters(self) -> None:
        """Open the comparison at once and render the preset variants of the current page in the background."""
        if self._current < 0:
            return
        page = self._session.page(self._current).page
        settings = self._settings
        variants = preset_variants()
        dialog = CompareDialog(variants, settings.ai.provider.enabled, self)
        start_task(
            lambda _progress: render_page_variants(page, variants),
            dialog.show_renders,
            lambda error: dialog.render_failed(str(error)),
        )
        dialog.rating_requested.connect(
            lambda: start_task(
                lambda _progress: rate_renders(dialog.renders(), settings),
                dialog.show_rating,
                lambda error: dialog.rating_failed(str(error)),
            )
        )
        if not dialog.exec():
            return
        chosen = dialog.selected_variant()
        self._session.set_filter(self._current, chosen.settings, only_this_page=False)
        self._filters.show_settings(chosen.settings, own_filter=False)
        self._set_status(f"Filter variant {chosen.number} ({chosen.name}) applied to all pages")
        self._schedule_preview()
        for index in range(len(self._session)):
            self._render_thumbnail(index)

    # --- Previews -------------------------------------------------------------------------------

    def _schedule_preview(self) -> None:
        """Re-render the current preview shortly (coalesces fast edits)."""
        self._preview_timer.start()

    def _render_current_preview(self) -> None:
        """Render a quick preview, then the final-quality one; stale results are dropped."""
        if self._current < 0:
            return
        self._preview_generation += 1
        generation, index, entry = self._preview_generation, self._current, self._session.page(self._current)
        max_edge = self._settings.max_edge or None
        start_task(
            lambda _progress: _PreviewResult(generation, index, to_qimage(render_preview(entry))),
            self._quick_preview_ready,
            self._task_failed,
        )
        start_task(
            lambda _progress: _PreviewResult(generation, index, to_qimage(render_final(entry, max_edge))),
            self._final_preview_ready,
            self._task_failed,
        )

    def _quick_preview_ready(self, result: _PreviewResult) -> None:
        """Show the fast preview unless the final one (or a newer request) already arrived."""
        if result.generation != self._preview_generation or self._final_shown == result.generation:
            return
        pixmap = image_to_pixmap(result.image)
        self._preview.set_image(pixmap)
        self._strip.set_thumbnail(result.index, pixmap)

    def _final_preview_ready(self, result: _PreviewResult) -> None:
        """Replace the quick preview with the export-quality rendering."""
        if result.generation != self._preview_generation:
            return
        self._final_shown = result.generation
        self._preview.set_image(image_to_pixmap(result.image))

    def _render_thumbnail(self, index: int) -> None:
        """Render a page thumbnail in the background."""
        entry = self._session.page(index)
        start_task(
            lambda _progress: (index, entry.page.source, to_qimage(render_preview(entry, max_edge=THUMBNAIL_EDGE))),
            self._thumbnail_ready,
            self._task_failed,
        )

    def _thumbnail_ready(self, result: tuple[int, Path, Any]) -> None:
        """Show a thumbnail if the page is still at that position."""
        index, source, image = result
        if index < len(self._session) and self._session.page(index).page.source == source:
            self._strip.set_thumbnail(index, image_to_pixmap(image))

    def _refresh_strip(self) -> None:
        """Match the strip to the session's pages and documents."""
        self._strip.set_page_count(len(self._session))
        self._strip.set_documents(self._session.document_ranges())
        self._update_actions()

    # --- Export ---------------------------------------------------------------------------------

    def _export(self) -> None:
        """Process every document in the background, then let the user review them together."""
        if not len(self._session):
            return
        documents, settings = self._session.document_pages(), self._settings
        self._cancel_export.clear()
        self._cancel.show()
        message = "Creating PDF …" if len(documents) == 1 else f"Creating {len(documents)} PDFs …"
        self._run(message, lambda progress: prepare_documents(documents, settings, progress, self._cancel_export), self._review_export)

    def _review_export(self, drafts: list[ExportDraft]) -> None:
        """Show every document's suggestion (AI on request) and write the PDFs once confirmed."""
        self._cancel.hide()
        settings = self._settings
        providers = available_providers(settings.ai.provider)
        preferred = settings.ai.provider.provider if settings.ai.provider.enabled else next(iter(providers), AnalysisProvider.NONE)
        dialog = ExportDialog(
            [self._document_review(draft) for draft in drafts],
            Path(settings.output_dir).expanduser(),
            open_after=settings.open_after_export,
            style=settings.file_date_style,
            providers=providers,
            preferred=preferred,
            phone_photo_count=len(self._session.phone_photos()),
            parent=self,
        )
        current = list(drafts)

        def ask_ai(index: int, provider: AnalysisProvider) -> None:
            ai_settings = with_setting(settings, "ai.provider.provider", provider)

            def reanalysed(new_draft: ExportDraft) -> None:
                current[index] = new_draft
                dialog.apply_suggestion(index, suggested_choice(new_draft, settings.file_date_style))

            start_task(
                lambda _progress: reanalyse(current[index], ai_settings),
                reanalysed,
                lambda error: dialog.ai_failed(index, str(error)),
            )

        dialog.ai_requested.connect(ask_ai)
        if not dialog.exec():
            self._set_status("Export cancelled")
            return
        saved = self._write_documents(current, dialog.choices(), dialog.output_dir(), open_after=dialog.open_after())
        self._remember_export_choices(dialog.output_dir(), open_after=dialog.open_after())
        if dialog.delete_from_phone():
            ranges = self._session.document_ranges()
            self._delete_phone_photos(self._session.phone_photos(page for index in saved for page in ranges[index]))

    def _document_review(self, draft: ExportDraft) -> DocumentReview:
        """Return what the save dialog shows for one prepared document."""
        settings = self._settings
        analysed = settings.ai.provider.enabled and bool(draft.analysis.title)
        return DocumentReview(
            suggestion=suggested_choice(draft, settings.file_date_style),
            page_count=len(draft.pages),
            preview_jpeg=draft.ai_images[0] if draft.ai_images else b"",
            analysed_by=settings.ai.provider.provider if analysed else None,
        )

    def _write_documents(self, drafts: list[ExportDraft], choices: list[ExportChoice], output_dir: Path, *, open_after: bool) -> list[int]:
        """Write each confirmed document and return the indices of those saved (stops at the first failure)."""
        saved: list[int] = []
        targets: list[Path] = []
        for index, (draft, choice) in enumerate(zip(drafts, choices, strict=True)):
            try:
                targets.append(write_export(draft, choice, output_dir))
            except OSError as exc:
                self._show_error(f"Saving {choice.file_name} failed: {exc}")
                break
            saved.append(index)
        if targets:
            self._set_status(f"Saved: {targets[0]}" if len(targets) == 1 else f"Saved {len(targets)} PDFs in {output_dir}")
        if open_after:
            for target in targets:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        return saved

    def _remember_export_choices(self, output_dir: Path, *, open_after: bool) -> None:
        """Keep the folder and "open after saving" chosen in the save dialog for next time."""
        settings = with_setting(with_setting(self._settings, "output_dir", str(output_dir)), "open_after_export", open_after)
        if settings == self._settings:
            return
        self._settings_saved(settings)
        if self._settings_window is not None:
            self._settings_window.sync_fields(settings, EXPORT_DIALOG_PATHS)

    def _delete_phone_photos(self, photos: list[RemotePhoto]) -> None:
        """Delete the imported photos from the phone after one more confirmation."""
        names = "\n".join(photo.name for photo in photos)
        answer = QMessageBox.question(
            self,
            "Delete from phone",
            f"Permanently delete these {len(photos)} photo(s) from the phone?\n\n{names}\n\n"
            "They do not go to the gallery's trash. Local copies in the import folder are kept.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        settings = self._settings

        def work(progress: Callable[[str, float], None]) -> int:
            client = settings.phone.adb_client()
            for number, photo in enumerate(photos, start=1):
                progress(f"Deleting {photo.name} from the phone", number / len(photos))
                client.delete(photo)
            return len(photos)

        self._run("Deleting photos from the phone …", work, lambda count: self._set_status(f"Deleted {count} photo(s) from the phone"))

    # --- Settings -------------------------------------------------------------------------------

    def _open_settings(self) -> SettingsWindow:
        """Show the settings window (one instance)."""
        if self._settings_window is None:
            self._settings_window = SettingsWindow(self._settings)
            self._settings_window.saved.connect(self._settings_saved)
        self._settings_window.load(self._settings)
        self._settings_window.show()
        self._settings_window.raise_()
        return self._settings_window

    def _settings_saved(self, settings: AppSettings) -> None:
        """Persist new settings."""
        try:
            self._store.save(settings)
        except OSError as exc:
            self._show_error(f"Settings could not be saved: {exc}")
            return
        self._settings = settings
        log.info("Settings saved")
        self._output.show_values(settings.max_edge, settings.jpeg.quality)
        self._refresh_capabilities()
        self._set_status("Settings saved")

    def _refresh_capabilities(self) -> None:
        """Show in the workflow panel whether OCR and AI naming will run on export."""
        settings = self._settings
        tesseract = find_tool(Tool.TESSERACT, settings.ocr.tesseract_path)
        if not settings.ocr.enabled:
            ocr = Capability(ready=False, text="off (Settings → Text recognition)")
        elif tesseract is None:
            ocr = Capability(ready=False, text="Tesseract missing (Settings → Tools)")
        else:
            ocr = Capability(ready=True, text=f"Tesseract · {settings.ocr.languages}")
        providers = available_providers(settings.ai.provider)
        if settings.ai.provider.enabled:
            ai = Capability(ready=True, text=f"{PROVIDER_LABELS.get(settings.ai.provider.provider, '')} names the PDF")
        elif providers:
            names = ", ".join(PROVIDER_LABELS[provider] for provider in providers)
            ai = Capability(ready=False, text=f"off · available: {names} (ask in the save dialog)")
        else:
            ai = Capability(ready=False, text="off · no AI found")
        self._workflow.show_capabilities(ocr, ai)

    def _output_edited(self, max_edge: int, quality: int) -> None:
        """Save the quick output settings right away."""
        settings = with_setting(with_setting(self._settings, "max_edge", max_edge), "jpeg.quality", quality)
        self._settings_saved(settings)
        if self._settings_window is not None:
            self._settings_window.sync_fields(settings, OUTPUT_PANEL_PATHS)

    # --- Background jobs ------------------------------------------------------------------------

    def _run(
        self,
        message: str,
        work: Callable[[Callable[[str, float], None]], Any],
        on_success: Callable[[Any], None],
        on_failure: Callable[[Exception], None] | None = None,
    ) -> None:
        """Run ``work`` in the background with status, progress and error handling.

        ``on_failure`` replaces the default error dialog for failures the caller can recover from.
        """
        self._busy_jobs += 1
        self._set_status(message)
        # Busy animation until the task reports a percentage (some steps, like reading the phone, never do).
        self._progress.setRange(0, 0)
        self._progress.show()
        self._update_actions()

        def finished(result: object) -> None:
            self._job_done()
            self._set_status("Ready")
            on_success(result)

        def failed(error: Exception) -> None:
            self._job_done()
            self._cancel.hide()
            if isinstance(error, ExportCancelledError):
                self._set_status("Export cancelled")
                return
            (on_failure or self._task_failed)(error)

        start_task(work, finished, failed, self._show_progress)

    def _job_done(self) -> None:
        """Hide progress once no job runs."""
        self._busy_jobs -= 1
        if self._busy_jobs == 0:
            self._progress.hide()
        self._update_actions()

    def _show_progress(self, message: str, fraction: float) -> None:
        """Update the status bar; the percentage goes into the text, the bar stays slim."""
        self._set_status(f"{message} · {fraction:.0%}")
        if self._progress.maximum() == 0:
            self._progress.setRange(0, PROGRESS_STEPS)
        self._progress.setValue(round(fraction * PROGRESS_STEPS))

    def _task_failed(self, error: Exception) -> None:
        """Report a failed background task (the traceback is in the log file)."""
        self._show_error(str(error) or type(error).__name__)

    def _show_error(self, message: str) -> None:
        """Show the failure in the status bar and a dialog, pointing to the log file."""
        first_line = message.strip().splitlines()[0] if message.strip() else "Unknown error"
        self._set_status(f"Error: {first_line}", error=True)
        self._status.setToolTip(message)
        QMessageBox.warning(self, APP_NAME, f"{message}\n\nDetails are in the log file:\n{LOG_FILE}")

    def _set_status(self, text: str, *, error: bool = False) -> None:
        """Show ``text`` in the status bar, highlighted for errors."""
        self._status.setText(text)
        if not error:
            self._status.setToolTip("")
        set_style_state(self._status, "status", "error" if error else "normal")

    def _update_actions(self) -> None:
        """Enable actions that make sense right now."""
        has_page = self._current >= 0
        idle = self._busy_jobs == 0
        self._actions["compare"].setEnabled(has_page)
        self._page_toolbar.set_page_actions_enabled(has_page)
        self._actions["export"].setEnabled(idle and len(self._session) > 0)
        self._workflow.show_state(page_count=len(self._session), document_count=len(self._session.document_ranges()), busy=not idle)
        for key in ("phone", "files", "folder", "new"):
            self._actions[key].setEnabled(idle)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        """Stop a running export before closing."""
        self._cancel_export.set()
        super().closeEvent(event)


def _toolbar_brand() -> QWidget:
    """Return the app icon and name that open the header."""
    brand = QWidget()
    brand.setObjectName("toolbarBrand")
    layout = QHBoxLayout(brand)
    layout.setContentsMargins(*BRAND_MARGINS)
    layout.setSpacing(8)
    icon = QLabel()
    icon.setPixmap(QApplication.windowIcon().pixmap(BRAND_ICON_SIZE, BRAND_ICON_SIZE))
    name = QLabel(APP_NAME)
    name.setObjectName("brandName")
    layout.addWidget(icon)
    layout.addWidget(name)
    return brand


def _toolbar_spacer() -> QWidget:
    """Return a stretching gap that pushes the following actions to the right edge."""
    spacer = QWidget()
    spacer.setObjectName("toolbarSpacer")
    spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return spacer
