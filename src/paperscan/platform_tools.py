"""Locate external command-line tools (adb, tesseract, cjpegli, claude, codex) on Windows and macOS.

GUI apps started from the macOS Dock or Finder do not inherit the shell's PATH,
so the common Homebrew prefixes are searched as well. A path configured by the
user always wins.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from enum import StrEnum
from pathlib import Path

from paperscan.app_paths import TOOLS_DIR

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"

#: Directories searched after PATH: tools installed by the setup assistant, the
#: Tesseract installer's default folder on Windows, and Homebrew (Apple Silicon, Intel).
#: Also the default install folders of Claude Code, Codex and npm, because apps started
#: from the Start menu or Dock may see an older or shorter PATH than a terminal.
EXTRA_SEARCH_DIRS: tuple[Path, ...] = (
    TOOLS_DIR / "bin",
    TOOLS_DIR / "platform-tools",
    Path.home() / ".local" / "bin",
    Path.home() / ".claude" / "local",
    Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Programs" / "OpenAI" / "Codex" / "bin",
    Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "npm",
    Path("C:/Program Files/Tesseract-OCR"),
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
)

#: Hide console windows of child processes started from the GUI on Windows.
NO_WINDOW_FLAGS: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Tool(StrEnum):
    """External tools the pipeline can use; the value is the executable name."""

    ADB = "adb"
    TESSERACT = "tesseract"
    CJPEGLI = "cjpegli"
    CLAUDE = "claude"
    CODEX = "codex"


#: Install hints shown when a tool is missing.
INSTALL_HINTS: dict[Tool, str] = {
    Tool.ADB: "Run 'paperscan setup' (downloads Google's platform-tools)",
    Tool.TESSERACT: "Run 'paperscan setup' (winget on Windows, brew on macOS)",
    Tool.CJPEGLI: "Run 'paperscan setup' or macOS: brew install jpeg-xl",
    Tool.CLAUDE: "Install Claude Code: https://docs.claude.com/claude-code",
    Tool.CODEX: "Install the OpenAI Codex CLI: npm install -g @openai/codex",
}


class ToolMissingError(RuntimeError):
    """Raised when a required external tool cannot be found."""

    def __init__(self, tool: Tool) -> None:
        """Build the message with an install hint for ``tool``."""
        super().__init__(f"'{tool.value}' was not found. {INSTALL_HINTS[tool]}")
        self.tool = tool


def find_tool(tool: Tool, configured: str = "") -> Path | None:
    """Return the tool's executable: the configured path, then PATH, then the extra directories."""
    if configured:
        candidate = Path(configured).expanduser()
        return candidate if candidate.is_file() else None
    found = shutil.which(tool.value)
    if found:
        return Path(found)
    for directory in EXTRA_SEARCH_DIRS:
        found = shutil.which(tool.value, path=str(directory))
        if found:
            return Path(found)
    return None


def require_tool(tool: Tool, configured: str = "") -> Path:
    """Return the tool's executable or raise :class:`ToolMissingError`."""
    path = find_tool(tool, configured)
    if path is None:
        raise ToolMissingError(tool)
    return path


def set_windows_app_id(app_id: str) -> None:
    """Give the process its own taskbar identity on Windows, so the app icon is shown instead of Python's."""
    if not IS_WINDOWS:
        return
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)  # type: ignore[attr-defined]


def run_tool_bytes(arguments: list[str], *, timeout: float) -> bytes:
    """Run a tool and return its raw stdout (binary-safe); raises ``CalledProcessError`` on failure."""
    completed = subprocess.run(  # noqa: S603 - arguments are built from resolved tool paths, never a shell
        arguments,
        capture_output=True,
        timeout=timeout,
        check=True,
        creationflags=NO_WINDOW_FLAGS,
    )
    return completed.stdout


def run_tool(arguments: list[str], *, timeout: float, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a tool and capture its text output; raises ``CalledProcessError`` on failure."""
    return subprocess.run(  # noqa: S603 - arguments are built from resolved tool paths, never a shell
        arguments,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=True,
        creationflags=NO_WINDOW_FLAGS,
    )
