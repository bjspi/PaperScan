"""Finding the phone in the GUI: the connection check in the settings and the search dialog.

Both look for the phone the same way the import does (USB first, then Wireless
debugging when the Wi-Fi fallback is on) and offer pairing over Wi-Fi.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum, auto

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from paperscan.capture.adb_import import Device
from paperscan.gui.setting_rows import Card, setting_row
from paperscan.gui.tasks import start_task
from paperscan.gui.theme import set_style_state
from paperscan.settings import AppSettings, PhoneSettings

#: Style property that colours a connection status line (see theme).
CONNECTION_STATE = "connection"
PAIR_INSTRUCTIONS = (
    "On the phone open Developer options → Wireless debugging → <b>Pair device with pairing code</b>. "
    "Enter the IP address:port and the six-digit code shown there. The phone and this computer must be "
    "on the same network. Pairing is needed only once."
)
#: Pause between automatic searches while the search dialog is open, in milliseconds.
SEARCH_RETRY_MS = 3000
SEARCH_BAR_WIDTH = 120
SEARCH_BAR_HEIGHT = 6

#: Looks for the phone (runs in a worker thread).
PhoneSearch = Callable[[], Device]


class SearchOutcome(Enum):
    """How the search dialog was closed."""

    FOUND = auto()
    OPEN_SETTINGS = auto()
    CANCELLED = auto()


def find_phone(phone: PhoneSettings) -> Device:
    """Return the phone the import would use (USB first, then Wi-Fi)."""
    return phone.adb_client().connect()


def pair_and_find(phone: PhoneSettings, address: str, code: str) -> Device:
    """Pair over Wi-Fi, then return the phone the import would use."""
    client = phone.adb_client()
    client.pair(address, code)
    return client.connect()


def show_connection_state(label: QLabel, text: str, state: str) -> None:
    """Show ``text`` in ``label`` coloured by ``state`` ("ok", "error" or "busy")."""
    label.setText(text)
    set_style_state(label, CONNECTION_STATE, state)


class PairDialog(QDialog):
    """Asks for the pairing address and code shown on the phone."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the instructions and both fields."""
        super().__init__(parent)
        self.setWindowTitle("Pair phone over Wi-Fi")
        self.setMinimumWidth(460)
        instructions = QLabel(PAIR_INSTRUCTIONS)
        instructions.setWordWrap(True)
        self._address = QLineEdit()
        self._address.setPlaceholderText("192.168.1.20:37011")
        self._code = QLineEdit()
        self._code.setPlaceholderText("482913")
        form = QFormLayout()
        form.addRow("Address", self._address)
        form.addRow("Pairing code", self._code)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Pair")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(instructions)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def address(self) -> str:
        """Return the entered ``IP:port``."""
        return self._address.text().strip()

    def code(self) -> str:
        """Return the entered pairing code."""
        return self._code.text().strip()


class PhoneConnectionCard(QWidget):
    """Settings row that finds the phone with the entered (unsaved) settings and pairs it over Wi-Fi."""

    def __init__(self, settings_provider: Callable[[], AppSettings], parent: QWidget | None = None) -> None:
        """Build the row; ``settings_provider`` returns the current (possibly unsaved) settings."""
        super().__init__(parent)
        self._settings = settings_provider
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.hide()
        self._pair = QPushButton("Pair …")
        self._pair.setToolTip("Pair this computer with a phone over Wireless debugging (Android 11+).")
        self._pair.clicked.connect(self._ask_pairing)
        self._test = QPushButton("Test")
        self._test.setToolTip("Look for the phone: USB first, then Wi-Fi.")
        self._test.clicked.connect(self._run_test)
        controls = QWidget()
        controls.setObjectName("rowControls")
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)
        controls_layout.addWidget(self._pair)
        controls_layout.addWidget(self._test)
        card = Card()
        card.add_row(setting_row("Test connection", "USB first; with the Wi-Fi fallback, Wireless debugging next.", controls, self._status))
        heading = QLabel("CHECK")
        heading.setObjectName("hint")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(heading)
        layout.addWidget(card)

    def _run_test(self) -> None:
        """Look for the phone in the background."""
        phone = self._settings().phone
        self._start("Looking for the phone …", lambda: find_phone(phone))

    def _ask_pairing(self) -> None:
        """Ask for address and code, pair, then look for the paired phone."""
        dialog = PairDialog(self)
        if not dialog.exec() or not dialog.address() or not dialog.code():
            return
        phone, address, code = self._settings().phone, dialog.address(), dialog.code()
        self._start(f"Pairing with {address} …", lambda: pair_and_find(phone, address, code))

    def _start(self, message: str, search: PhoneSearch) -> None:
        """Run ``search`` and show its result; the buttons wait meanwhile."""
        self._set_busy(True)
        self._status.show()
        show_connection_state(self._status, message, "busy")
        start_task(lambda _progress: search(), self._found, self._failed)

    def _found(self, device: object) -> None:
        """Show the phone that will be used."""
        assert isinstance(device, Device)
        self._set_busy(False)
        show_connection_state(self._status, f"✓  {device.label} · serial {device.serial}", "ok")

    def _failed(self, error: Exception) -> None:
        """Show why no phone was found."""
        self._set_busy(False)
        show_connection_state(self._status, f"✕  {error or type(error).__name__}", "error")

    def _set_busy(self, busy: bool) -> None:
        """Disable both buttons while adb works."""
        self._pair.setEnabled(not busy)
        self._test.setEnabled(not busy)


class PhoneSearchDialog(QDialog):
    """Shown when no phone is found: keeps looking until one appears, or on request.

    The dialog accepts itself as soon as a search succeeds; :meth:`outcome` tells
    the caller whether to continue the import or open the phone settings.
    """

    def __init__(self, reason: str, search: PhoneSearch, parent: QWidget | None = None) -> None:
        """Show ``reason`` and start retrying ``search`` every few seconds."""
        super().__init__(parent)
        self.setWindowTitle("Looking for the phone")
        self.setMinimumWidth(520)
        self._search = search
        self._searching = False
        self._closed = False
        self._outcome = SearchOutcome.CANCELLED
        self._retry = QTimer(self, singleShot=True, interval=SEARCH_RETRY_MS)
        self._retry.timeout.connect(self._search_now)
        title = QLabel("No phone found yet")
        title.setObjectName("dialogTitle")
        hint = QLabel(
            "Plug in the phone with USB debugging allowed, or turn on Wireless debugging on a phone paired with this "
            "computer. PaperScan keeps looking and continues by itself."
        )
        hint.setWordWrap(True)
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._activity = QProgressBar()
        self._activity.setObjectName("statusProgress")
        self._activity.setRange(0, 0)
        self._activity.setTextVisible(False)
        self._activity.setFixedSize(SEARCH_BAR_WIDTH, SEARCH_BAR_HEIGHT)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self._status)
        layout.addLayout(self._build_buttons())
        self._show_failure(reason)

    def outcome(self) -> SearchOutcome:
        """Return why the dialog was closed."""
        return self._outcome

    def done(self, result: int) -> None:
        """Stop searching once the dialog is closed."""
        self._closed = True
        self._retry.stop()
        super().done(result)

    def _build_buttons(self) -> QHBoxLayout:
        """Activity indicator, settings, cancel and search-now buttons."""
        settings = QPushButton("Phone settings …")
        settings.clicked.connect(self._open_settings)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self._search_button = QPushButton("Search now")
        self._search_button.setObjectName("primary")
        self._search_button.clicked.connect(self._search_now)
        row = QHBoxLayout()
        row.addWidget(self._activity)
        row.addStretch(1)
        row.addWidget(settings)
        row.addWidget(cancel)
        row.addWidget(self._search_button)
        return row

    def _search_now(self) -> None:
        """Look for the phone once in the background (ignored while a search runs)."""
        if self._searching or self._closed:
            return
        self._retry.stop()
        self._searching = True
        self._search_button.setEnabled(False)
        self._activity.show()
        show_connection_state(self._status, "Searching USB and Wi-Fi …", "busy")
        start_task(lambda _progress: self._search(), self._found, lambda error: self._show_failure(str(error)))

    def _found(self, device: object) -> None:
        """Close the dialog and let the import continue."""
        assert isinstance(device, Device)
        self._searching = False
        if self._closed:
            return
        self._outcome = SearchOutcome.FOUND
        show_connection_state(self._status, f"✓  Found {device.label}", "ok")
        self.accept()

    def _show_failure(self, reason: str) -> None:
        """Show the latest reason and schedule the next automatic search."""
        self._searching = False
        if self._closed:
            return
        self._search_button.setEnabled(True)
        self._activity.hide()
        show_connection_state(self._status, f"✕  {reason}\nTrying again in {SEARCH_RETRY_MS // 1000} s …", "error")
        self._retry.start()

    def _open_settings(self) -> None:
        """Close and ask the caller to open the phone settings."""
        self._outcome = SearchOutcome.OPEN_SETTINGS
        self.reject()
