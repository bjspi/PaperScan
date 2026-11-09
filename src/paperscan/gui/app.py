"""GUI entry point."""

from __future__ import annotations

import sys
from importlib.resources import files

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from paperscan import __version__
from paperscan.app_paths import APP_NAME
from paperscan.gui.main_window import MainWindow
from paperscan.gui.theme import apply_theme
from paperscan.platform_tools import set_windows_app_id
from paperscan.settings import SettingsStore

SPLASH_ICON_SIZE = 256
QT_STYLE = "Fusion"
#: Application icon shipped as package data.
APP_ICON = files("paperscan.gui") / "resources" / "app_icon.png"
#: All sizes from 16 to 256 px, so title bars and the taskbar get a sharp small icon.
APP_ICON_SIZES = files("paperscan.gui") / "resources" / "app_icon.ico"


def _splash_screen() -> QSplashScreen:
    """Show the app icon with its name while the main window loads."""
    pixmap = QPixmap(str(APP_ICON)).scaled(
        SPLASH_ICON_SIZE, SPLASH_ICON_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    splash = QSplashScreen(pixmap, Qt.WindowType.WindowStaysOnTopHint)
    splash.showMessage(f"{APP_NAME} {__version__}", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, Qt.GlobalColor.white)
    splash.show()
    QApplication.processEvents()
    return splash


def _app_icon() -> QIcon:
    """Return the application icon with every size available to Qt."""
    icon = QIcon(str(APP_ICON_SIZES))
    icon.addFile(str(APP_ICON))
    return icon


def run_gui() -> int:
    """Start the Qt application and return its exit code."""
    set_windows_app_id(APP_NAME)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    # Fusion draws every widget (including dropdown popups) through the style sheet on all platforms;
    # the native Windows 11 style paints combo box popups itself, black in light mode.
    QApplication.setStyle(QT_STYLE)
    QApplication.setWindowIcon(_app_icon())
    splash = _splash_screen()
    store = SettingsStore()
    settings = store.load()
    window = MainWindow(store, settings)
    apply_theme(window, settings.color_theme)
    window.show()
    splash.finish(window)
    QTimer.singleShot(0, window.offer_setup_if_needed)
    return app.exec()
