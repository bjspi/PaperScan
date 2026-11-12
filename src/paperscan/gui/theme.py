"""Light and dark theme: palette and component styling."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

from paperscan.gui.toggle_switch import ToggleSwitch
from paperscan.settings import ColorTheme

LIGHT: dict[str, str] = {
    "bg": "#f3f5f8",
    "panel": "#ffffff",
    "panel2": "#f1f4f8",
    "text": "#1f2329",
    "heading": "#15191e",
    "muted": "#6b727c",
    "border": "#e3e7ed",
    "divider": "#eef1f4",
    "field": "#ffffff",
    "field_border": "#cfd5dd",
    "accent": "#2f7de1",
    "accent_hover": "#2a72cf",
    "accent_press": "#2565b8",
    "on_accent": "#ffffff",
    "hover": "#edf0f4",
    "sidebar": "#eaedf2",
    "sidebar_border": "#dfe3e9",
    "nav_text": "#3a4049",
    "nav_hover": "#e0e5ec",
    "nav_selected": "#1f5fb8",
    "selection": "#bcd5f5",
    "switch_off": "#c5ccd6",
    "canvas": "#dfe3e9",
    "ok_strong": "#2fa84f",
    "ok_strong_hover": "#28924a",
    "ok_bg": "#e3f4e8",
    "ok_text": "#1d7a3e",
    "warn_bg": "#fdf0e3",
    "warn_text": "#a4561d",
}

DARK: dict[str, str] = {
    "bg": "#1b1d21",
    "panel": "#25282d",
    "panel2": "#2c3036",
    "text": "#e6e8eb",
    "heading": "#f2f4f7",
    "muted": "#9aa1ab",
    "border": "#33373e",
    "divider": "#2e3238",
    "field": "#1f2226",
    "field_border": "#40454d",
    "accent": "#4c94ef",
    "accent_hover": "#5ea0f2",
    "accent_press": "#3d84de",
    "on_accent": "#ffffff",
    "hover": "#30343a",
    "sidebar": "#202327",
    "sidebar_border": "#2b2f35",
    "nav_text": "#c9ced6",
    "nav_hover": "#2a2e34",
    "nav_selected": "#7fb2f5",
    "selection": "#2f5a91",
    "switch_off": "#4a5059",
    "canvas": "#141619",
    "ok_strong": "#2f9e55",
    "ok_strong_hover": "#37b062",
    "ok_bg": "#1e3a29",
    "ok_text": "#7fd69c",
    "warn_bg": "#3d2c1c",
    "warn_text": "#f0b37a",
}

_QSS = """
QWidget { background-color: %(bg)s; color: %(text)s; font-size: 10pt; }
QLabel, QSlider, QWidget#settingRow, QWidget#rowControls, QWidget#sliderRow, QWidget#pageBody,
QFrame#card QLabel, QFrame#card QWidget#settingRow, QGroupBox QLabel { background: transparent; }
QToolTip { background: %(panel)s; color: %(text)s; border: 1px solid %(border)s; padding: 6px; }

/* Toolbar and buttons */
QToolBar { background: %(panel)s; border: none; border-bottom: 1px solid %(border)s; padding: 4px; spacing: 4px; }
QToolBar#mainToolbar { background: %(sidebar)s; border-bottom: 1px solid %(sidebar_border)s; padding: 7px 12px; spacing: 6px; }
QToolBar#mainToolbar::separator { background: %(sidebar_border)s; width: 1px; margin: 6px 10px; }
QToolBar#mainToolbar QToolButton:hover { background: %(nav_hover)s; border-color: %(sidebar_border)s; }
QWidget#toolbarBrand, QWidget#toolbarSpacer { background: transparent; }
QLabel#brandName { font-size: 11pt; font-weight: 600; color: %(heading)s; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 6px 10px; }
QToolButton:hover { background: %(hover)s; border-color: %(border)s; }
QToolButton:checked { background: %(selection)s; border-color: %(accent)s; }
QToolButton:disabled { color: %(muted)s; }
QPushButton { background: %(panel)s; color: %(text)s; border: 1px solid %(field_border)s; border-radius: 8px; padding: 6px 14px; }
QPushButton:hover { background: %(hover)s; }
QPushButton:pressed { background: %(panel2)s; }
QPushButton:disabled { color: %(muted)s; }
QPushButton#mode { padding: 6px 2px; }
QPushButton#link { background: transparent; border: none; color: %(accent)s; padding: 2px 4px; }
QPushButton#link:hover { color: %(accent_hover)s; text-decoration: underline; }
QLabel#sliderValue { color: %(muted)s; font-size: 9pt; min-width: 40px; }
QPushButton:checked { background: %(accent)s; color: %(on_accent)s; border-color: %(accent)s; }
QPushButton#primary { background: %(accent)s; color: %(on_accent)s; border-color: %(accent)s; font-weight: 600; min-width: 96px; }
QPushButton#primary:hover { background: %(accent_hover)s; }
QPushButton#primary:pressed { background: %(accent_press)s; }
QPushButton#primary:disabled { background: %(panel2)s; color: %(muted)s; border-color: %(border)s; }
QPushButton#create { background: %(ok_strong)s; color: #ffffff; border-color: %(ok_strong)s;
    font-weight: 700; font-size: 11pt; padding: 10px; }
QPushButton#create:hover { background: %(ok_strong_hover)s; }
QPushButton#create:disabled { background: %(panel2)s; color: %(muted)s; border-color: %(border)s; }
QToolButton#pageAction { border: 1px solid %(field_border)s; border-radius: 8px; padding: 4px; font-size: 12pt; background: %(panel)s; }
QToolButton#pageAction:hover { background: %(hover)s; }
QLabel[capability="ok"], QLabel[connection="ok"] { color: %(ok_text)s; }
QLabel[connection="error"] { color: %(warn_text)s; }
QLabel[connection="busy"] { color: %(muted)s; }
QLabel#dialogTitle { font-size: 12pt; font-weight: 600; color: %(heading)s; }
QLabel[capability="off"] { color: %(muted)s; }

/* Input fields */
QLineEdit, QSpinBox, QDateEdit, QComboBox, QPlainTextEdit, QTextEdit {
    background: %(field)s; color: %(text)s; border: 1px solid %(field_border)s; border-radius: 8px; padding: 6px 10px;
    selection-background-color: %(selection)s; selection-color: %(text)s;
}
QLineEdit:focus, QSpinBox:focus, QDateEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border-color: %(accent)s; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled { color: %(muted)s; background: %(panel2)s; }
QComboBox { padding-right: 26px; }
QComboBox::drop-down, QDateEdit::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView {
    background: %(panel)s; color: %(text)s; border: 1px solid %(border)s; border-radius: 8px; padding: 4px; outline: 0;
    selection-background-color: %(selection)s; selection-color: %(text)s;
}
QComboBox QAbstractItemView::item { min-height: 28px; padding: 0 8px; border-radius: 6px; }

/* Cards and setting rows */
QFrame#card { background: %(panel)s; border: 1px solid %(border)s; border-radius: 12px; }
QFrame#rowDivider { background: %(divider)s; border: none; margin: 0 18px; }
QLabel[rowTitle="true"] { font-weight: 600; color: %(text)s; }
QLabel#rowDescription, QLabel#hint { color: %(muted)s; font-size: 9pt; }
QFrame#card QLabel[badge="ok"] { background: %(ok_bg)s; color: %(ok_text)s;
    border-radius: 10px; padding: 3px 10px; font-size: 9pt; font-weight: 600; }
QFrame#card QLabel[badge="missing"] { background: %(warn_bg)s; color: %(warn_text)s;
    border-radius: 10px; padding: 3px 10px; font-size: 9pt; font-weight: 600; }
QGroupBox { background: %(panel)s; border: 1px solid %(border)s; border-radius: 12px; margin-top: 22px; padding: 14px 12px 12px 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 4px; padding: 0 0 6px 0;
    color: %(muted)s; font-weight: 600; font-size: 8pt; background: transparent; }

/* Settings window chrome */
QFrame#sidebar { background: %(sidebar)s; border: none; border-right: 1px solid %(sidebar_border)s; }
QLabel#brand { font-size: 12pt; font-weight: 600; color: %(heading)s; }
QLabel#brandVersion { color: %(muted)s; font-size: 8pt; }
QListWidget#nav { background: transparent; border: none; padding: 0; outline: 0; }
QListWidget#nav::item { padding: 9px 10px; margin: 1px 0; border-radius: 8px; color: %(nav_text)s; border: 1px solid transparent; }
QListWidget#nav::item:hover { background: %(nav_hover)s; }
QListWidget#nav::item:selected { background: %(panel)s; color: %(nav_selected)s; border-color: %(border)s; font-weight: 600; }
QLabel#pageTitle { font-size: 17pt; font-weight: 600; color: %(heading)s; }
QLabel#pageSubtitle { color: %(muted)s; font-size: 9pt; padding-bottom: 8px; }
QFrame#footer { background: %(panel)s; border: none; border-top: 1px solid %(border)s; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }

/* Lists, views, status */
QListWidget { background: %(panel)s; border: 1px solid %(border)s; border-radius: 10px; padding: 4px; outline: 0; }
QListWidget::item { border-radius: 8px; padding: 4px; }
QListWidget::item:selected { background: %(selection)s; color: %(text)s; }
QGraphicsView, QLabel#canvas { background: %(canvas)s; border: 1px solid %(border)s; border-radius: 10px; }
QSlider::groove:horizontal { height: 4px; background: %(field_border)s; border-radius: 2px; }
QSlider::handle:horizontal { background: %(accent)s; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
QProgressBar { background: %(field)s; border: 1px solid %(field_border)s; border-radius: 6px; text-align: center; max-height: 14px; }
QProgressBar::chunk { background: %(accent)s; border-radius: 6px; }
QStatusBar { background: %(panel)s; border-top: 1px solid %(border)s; padding: 2px 10px; }
QStatusBar::item { border: none; }
QLabel#statusText { padding: 4px 0; }
QProgressBar#statusProgress { background: %(divider)s; border: none; border-radius: 3px; margin: 0 6px; }
QProgressBar#statusProgress::chunk { background: %(accent)s; border-radius: 3px; }
QLabel[status="error"] { color: %(warn_text)s; font-weight: 600; }
QScrollBar:vertical { width: 8px; margin: 2px; background: transparent; }
QScrollBar::handle:vertical { background: %(field_border)s; border-radius: 3px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QMenu { background: %(panel)s; border: 1px solid %(border)s; padding: 4px; }
QMenu::item { padding: 6px 24px 6px 14px; border-radius: 6px; }
QMenu::item:selected { background: %(hover)s; color: %(text)s; }
"""


def palette(dark: bool) -> dict[str, str]:
    """Return the colour palette for the given mode."""
    return DARK if dark else LIGHT


def resolve_dark(theme: str) -> bool:
    """Return whether the dark palette applies for ``theme`` ("system" follows the OS)."""
    if theme == ColorTheme.DARK:
        return True
    if theme == ColorTheme.LIGHT:
        return False
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def apply_theme(widget: QWidget, theme: str) -> None:
    """Style ``widget`` and its children, including switches; dialogs inherit it through the application."""
    colors = palette(resolve_dark(theme))
    stylesheet = _QSS % colors
    application = QApplication.instance()
    if isinstance(application, QApplication):
        application.setStyleSheet(stylesheet)
    else:
        widget.setStyleSheet(stylesheet)
    ToggleSwitch.use_theme_colors(colors["accent"], colors["switch_off"])


def set_style_state(widget: QWidget, name: str, value: str) -> None:
    """Set a style property used by ``[name="value"]`` selectors and re-apply the style."""
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
