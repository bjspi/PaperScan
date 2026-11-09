"""Actions for the page shown in the editor: a centred icon bar above the photo.

The same actions are available as keyboard shortcuts and in the page strip's
right-click menu; all of them are created here so they exist only once.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QFrame, QHBoxLayout, QToolButton, QWidget

from paperscan.gui.line_icons import LineIcon, line_icon

ICON_SIZE = 18
ICON_COLOR = "#4a5160"
REMOVE_COLOR = "#c2410c"


class PageAction(StrEnum):
    """Something done to the current page."""

    ROTATE_LEFT = "rotate_left"
    ROTATE_RIGHT = "rotate_right"
    DETECT_EDGES = "detect_edges"
    WHOLE_PHOTO = "whole_photo"
    REMOVE = "remove"


@dataclass(frozen=True)
class _ActionSpec:
    """Icon, label, shortcut and colour of a page action."""

    icon: LineIcon
    label: str
    shortcut: str
    color: str = ICON_COLOR


ACTIONS: dict[PageAction, _ActionSpec] = {
    PageAction.ROTATE_LEFT: _ActionSpec(LineIcon.ROTATE_LEFT, "Rotate left", "Ctrl+L"),
    PageAction.ROTATE_RIGHT: _ActionSpec(LineIcon.ROTATE_RIGHT, "Rotate right", "Ctrl+R"),
    PageAction.DETECT_EDGES: _ActionSpec(LineIcon.DETECT_EDGES, "Detect edges again", "Ctrl+D"),
    PageAction.WHOLE_PHOTO: _ActionSpec(LineIcon.WHOLE_PHOTO, "Use the whole photo", "Ctrl+Shift+D"),
    PageAction.REMOVE: _ActionSpec(LineIcon.TRASH, "Remove page from the document", "Del", REMOVE_COLOR),
}
#: Actions separated from the others in the bar.
_GROUP_STARTS = {PageAction.DETECT_EDGES, PageAction.REMOVE}


class PageToolbar(QFrame):
    """Centred icon buttons for the current page plus matching window shortcuts."""

    action_requested = Signal(object)  # PageAction

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the buttons and the shortcut actions."""
        super().__init__(parent)
        self.setObjectName("pageToolbar")
        self._buttons: dict[PageAction, QToolButton] = {}
        self.actions_for_window: list[QAction] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)
        layout.addStretch(1)
        for action, spec in ACTIONS.items():
            if action in _GROUP_STARTS:
                layout.addSpacing(14)
            layout.addWidget(self._button(action, spec))
            self.actions_for_window.append(self._shortcut(action, spec))
        layout.addStretch(1)
        self.set_page_actions_enabled(False)

    def set_page_actions_enabled(self, enabled: bool) -> None:
        """Enable buttons and shortcuts when a page is shown."""
        for button in self._buttons.values():
            button.setEnabled(enabled)
        for action in self.actions_for_window:
            action.setEnabled(enabled)

    def _button(self, action: PageAction, spec: _ActionSpec) -> QToolButton:
        """Return an icon button for ``action``."""
        button = QToolButton()
        button.setObjectName("pageAction")
        button.setIcon(line_icon(spec.icon, spec.color, ICON_SIZE))
        button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        button.setToolTip(f"{spec.label} ({spec.shortcut})")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda: self.action_requested.emit(action))
        self._buttons[action] = button
        return button

    def _shortcut(self, action: PageAction, spec: _ActionSpec) -> QAction:
        """Return a window action with the keyboard shortcut of ``action``."""
        window_action = QAction(line_icon(spec.icon, spec.color, ICON_SIZE), spec.label, self)
        window_action.setShortcut(QKeySequence(spec.shortcut))
        window_action.triggered.connect(lambda: self.action_requested.emit(action))
        return window_action
