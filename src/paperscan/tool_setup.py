"""Setup assistant: install the external tools the pipeline needs.

Binaries are never part of the repository. On request this module downloads
them from their official sources into ``~/.PaperScan/tools`` or runs the
platform package manager:

* cjpegli — libjxl release archive (Windows) or ``brew install jpeg-xl`` (macOS)
* adb — Google's platform-tools archive (Windows and macOS)
* Tesseract — ``winget`` (Windows) or ``brew`` (macOS); language data is
  downloaded into ``~/.PaperScan/tessdata``
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from paperscan.app_paths import TESSDATA_DIR, TOOLS_DIR, make_reproducible_dir, partial_path
from paperscan.platform_tools import IS_MACOS, IS_WINDOWS, Tool, find_tool, run_tool

log = logging.getLogger(__name__)

#: Download and package manager timeouts, in seconds.
DOWNLOAD_TIMEOUT = 120.0
PACKAGE_MANAGER_TIMEOUT = 1800.0
DOWNLOAD_CHUNK = 1 << 16

CJPEGLI_WINDOWS_URL = "https://github.com/libjxl/libjxl/releases/download/v0.11.1/jxl-x64-windows-static.zip"
PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-{platform}.zip"
TESSDATA_URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{language}.traineddata"
EXE_SUFFIX = ".exe" if IS_WINDOWS else ""

#: Tools the assistant can install (the AI CLIs are installed by their vendors).
INSTALLABLE_TOOLS: tuple[Tool, ...] = (Tool.TESSERACT, Tool.CJPEGLI, Tool.ADB)

#: (message, fraction done 0..1)
SetupProgress = Callable[[str, float], None]


class SetupMethod(StrEnum):
    """How a tool gets installed on this platform."""

    DOWNLOAD = "download"
    PACKAGE_MANAGER = "package_manager"
    MANUAL = "manual"


@dataclass(frozen=True)
class SetupStep:
    """What the assistant would do for one tool on this platform."""

    tool: Tool
    method: SetupMethod
    description: str
    command: tuple[str, ...] = ()


class SetupError(RuntimeError):
    """Raised when an installation step fails."""


def setup_plan(tool: Tool) -> SetupStep:
    """Return how ``tool`` can be installed on this platform."""
    match tool:
        case Tool.CJPEGLI if IS_WINDOWS:
            return SetupStep(tool, SetupMethod.DOWNLOAD, "Download cjpegli from the libjxl release on GitHub")
        case Tool.CJPEGLI if IS_MACOS:
            return SetupStep(tool, SetupMethod.PACKAGE_MANAGER, "brew install jpeg-xl", ("brew", "install", "jpeg-xl"))
        case Tool.ADB if IS_WINDOWS or IS_MACOS:
            return SetupStep(tool, SetupMethod.DOWNLOAD, "Download Google's Android platform-tools")
        case Tool.TESSERACT if IS_WINDOWS:
            command = (
                "winget",
                "install",
                "--id",
                "UB-Mannheim.TesseractOCR",
                "-e",
                "--accept-package-agreements",
                "--accept-source-agreements",
            )
            return SetupStep(tool, SetupMethod.PACKAGE_MANAGER, "winget install UB-Mannheim.TesseractOCR", command)
        case Tool.TESSERACT if IS_MACOS:
            return SetupStep(tool, SetupMethod.PACKAGE_MANAGER, "brew install tesseract", ("brew", "install", "tesseract"))
    return SetupStep(tool, SetupMethod.MANUAL, "Install it yourself and set its path in the settings")


def install(tool: Tool, progress: SetupProgress) -> Path:
    """Install ``tool`` and return its executable path."""
    step = setup_plan(tool)
    match step.method:
        case SetupMethod.DOWNLOAD if tool is Tool.CJPEGLI:
            _install_cjpegli(progress)
        case SetupMethod.DOWNLOAD if tool is Tool.ADB:
            _install_platform_tools(progress)
        case SetupMethod.PACKAGE_MANAGER:
            _run_package_manager(step, progress)
        case _:
            raise SetupError(f"{tool.value}: {step.description}")
    path = find_tool(tool)
    if path is None:
        raise SetupError(f"{tool.value} was installed but cannot be found; restart the app or set its path in the settings.")
    return path


def install_tesseract_languages(languages: str, progress: SetupProgress) -> Path:
    """Download the ``+``-separated Tesseract languages into the app's tessdata folder."""
    codes = [code for code in languages.split("+") if code]
    make_reproducible_dir(TESSDATA_DIR)
    for index, code in enumerate(codes):
        target = TESSDATA_DIR / f"{code}.traineddata"
        if not target.is_file():
            progress(f"Downloading Tesseract language '{code}'", index / len(codes))
            _download(TESSDATA_URL.format(language=code), target)
    return TESSDATA_DIR


def _install_cjpegli(progress: SetupProgress) -> None:
    """Extract cjpegli from the libjxl Windows release into the tools folder."""
    with tempfile.TemporaryDirectory(prefix="csocr-setup-") as tmp:
        archive = Path(tmp) / "libjxl.zip"
        progress("Downloading libjxl (cjpegli)", 0.1)
        _download(CJPEGLI_WINDOWS_URL, archive)
        progress("Extracting cjpegli", 0.8)
        target = TOOLS_DIR / "bin"
        make_reproducible_dir(TOOLS_DIR)
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as bundle:
            (target / f"cjpegli{EXE_SUFFIX}").write_bytes(bundle.read(f"cjpegli{EXE_SUFFIX}"))


def _install_platform_tools(progress: SetupProgress) -> None:
    """Extract Google's platform-tools (adb) into the tools folder."""
    platform = "windows" if IS_WINDOWS else "darwin"
    with tempfile.TemporaryDirectory(prefix="csocr-setup-") as tmp:
        archive = Path(tmp) / "platform-tools.zip"
        progress("Downloading Android platform-tools (adb)", 0.1)
        _download(PLATFORM_TOOLS_URL.format(platform=platform), archive)
        progress("Extracting adb", 0.8)
        make_reproducible_dir(TOOLS_DIR)
        shutil.rmtree(TOOLS_DIR / "platform-tools", ignore_errors=True)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(TOOLS_DIR)
    adb = TOOLS_DIR / "platform-tools" / f"adb{EXE_SUFFIX}"
    if not IS_WINDOWS:
        adb.chmod(0o755)


def _run_package_manager(step: SetupStep, progress: SetupProgress) -> None:
    """Run the package manager command of ``step``."""
    manager = shutil.which(step.command[0])
    if manager is None:
        raise SetupError(f"'{step.command[0]}' is not available. Run manually: {step.description}")
    progress(f"Running: {step.description}", 0.1)
    try:
        run_tool([manager, *step.command[1:]], timeout=PACKAGE_MANAGER_TIMEOUT)
    except Exception as exc:
        detail = (getattr(exc, "stderr", "") or "").strip() or str(exc)
        raise SetupError(f"{step.description} failed: {detail[:400]}") from exc


def _download(url: str, target: Path) -> None:
    """Download ``url`` (HTTPS only) to ``target``.

    The data goes to a temporary file that replaces ``target`` only when complete, so an
    interrupted download never leaves a broken file that later runs would take as installed.
    """
    if not url.startswith("https://"):
        raise SetupError(f"Refusing non-HTTPS download: {url}")
    log.info("Downloading %s", url)
    partial = partial_path(target)
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response, partial.open("wb") as file:  # noqa: S310 - HTTPS checked above
            shutil.copyfileobj(response, file, DOWNLOAD_CHUNK)
        partial.replace(target)
    except OSError as exc:
        raise SetupError(f"Download failed ({url}): {exc}") from exc
    finally:
        partial.unlink(missing_ok=True)
