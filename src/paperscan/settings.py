"""Persistent application settings.

Settings live in ``~/.PaperScan/config.json`` on every OS. Loading is
forgiving: unknown keys are ignored, values of the wrong type fall back to the
default, and an unreadable file is renamed aside instead of being overwritten.
Saving is atomic (write a temp file, then replace).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import time
import types
import typing
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any

from paperscan.analysis.vision import AnalysisProvider, ProviderConfig
from paperscan.app_paths import CONFIG_FILE, DEFAULT_OUTPUT_DIR, IMPORT_DIR, MODEL_PATH
from paperscan.capture.adb_import import DEFAULT_CAMERA_DIR, AdbClient
from paperscan.export.jpeg_encoding import JpegOptions
from paperscan.export.naming import FileDateStyle
from paperscan.imaging.filters import FilterSettings
from paperscan.platform_tools import Tool

log = logging.getLogger(__name__)

#: Attempts and pause for the atomic replace (virus scanners briefly lock files on Windows).
SAVE_RETRIES = 10
SAVE_RETRY_PAUSE = 0.1


class ColorTheme(StrEnum):
    """Light, dark, or following the operating system."""

    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True)
class PhoneSettings:
    """ADB import options."""

    adb_path: str = ""
    serial: str = ""
    camera_dir: str = DEFAULT_CAMERA_DIR
    list_count: int = 30
    #: Look for the phone over Wireless debugging when none is connected by USB.
    wireless_fallback: bool = True
    #: ``IP[:port]`` for the Wi-Fi fallback; empty discovers a paired phone on the network.
    wireless_address: str = ""

    def adb_client(self) -> AdbClient:
        """Return an adb client for the configured phone (raises if adb is missing)."""
        return AdbClient(self.adb_path, self.serial, wireless_fallback=self.wireless_fallback, wireless_address=self.wireless_address)


@dataclass(frozen=True)
class OcrSettings:
    """Text recognition options."""

    enabled: bool = True
    languages: str = "deu+eng"
    tesseract_path: str = ""


@dataclass(frozen=True)
class AiSettings:
    """Document analysis options."""

    provider: ProviderConfig = field(default_factory=lambda: ProviderConfig(AnalysisProvider.NONE))
    correct_ocr_text: bool = False
    language_hint: str = "German"
    #: Long edge of the page images sent to the model.
    image_edge: int = 1600


@dataclass(frozen=True)
class AppSettings:
    """All persisted settings."""

    output_dir: str = str(DEFAULT_OUTPUT_DIR)
    import_dir: str = str(IMPORT_DIR)
    border_model_path: str = str(MODEL_PATH)
    #: Longest page edge in pixels; 0 keeps the full resolution.
    max_edge: int = 2000
    open_after_export: bool = True
    #: Date at the start of PDF file names, e.g. 2026.11.09.
    file_date_style: FileDateStyle = FileDateStyle.DOTS
    color_theme: ColorTheme = ColorTheme.SYSTEM
    default_filter: FilterSettings = field(default_factory=FilterSettings)
    jpeg: JpegOptions = field(default_factory=JpegOptions)
    ocr: OcrSettings = field(default_factory=OcrSettings)
    ai: AiSettings = field(default_factory=AiSettings)
    phone: PhoneSettings = field(default_factory=PhoneSettings)

    @property
    def model_path(self) -> Path | None:
        """Return the border model path, or None if not configured."""
        return Path(self.border_model_path).expanduser() if self.border_model_path else None

    def tool_path(self, tool: Tool) -> str:
        """Return the user-configured path of ``tool`` ("" = search automatically)."""
        configured = {
            Tool.ADB: self.phone.adb_path,
            Tool.TESSERACT: self.ocr.tesseract_path,
            Tool.CJPEGLI: self.jpeg.cjpegli_path,
            Tool.CLAUDE: self.ai.provider.claude_path,
            Tool.CODEX: self.ai.provider.codex_path,
        }
        return configured[tool]


class SettingsStore:
    """Loads and saves :class:`AppSettings` as JSON."""

    def __init__(self, path: Path = CONFIG_FILE) -> None:
        """Remember the config file location; nothing is read yet."""
        self.path = path

    def load(self) -> AppSettings:
        """Return the stored settings, or defaults if the file is missing or unreadable."""
        if not self.path.is_file():
            return AppSettings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:  # e.g. saved as UTF-16 by an editor
            log.warning("Unreadable settings %s (%s); using defaults", self.path, exc)
            self._set_aside_corrupt_file()
            return AppSettings()
        return from_jsonable(AppSettings, data)

    def save(self, settings: AppSettings) -> None:
        """Write the settings atomically."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(to_jsonable(settings), indent=4, ensure_ascii=False), encoding="utf-8")
        for attempt in range(SAVE_RETRIES):
            try:
                os.replace(temporary, self.path)
                return
            except PermissionError:
                if attempt == SAVE_RETRIES - 1:
                    raise
                time.sleep(SAVE_RETRY_PAUSE)

    def _set_aside_corrupt_file(self) -> None:
        """Rename the unreadable file so the user's data is not lost on the next save."""
        aside = self.path.with_name(f"{self.path.name}.corrupt-{datetime.now():%Y%m%d-%H%M%S}")
        try:
            self.path.rename(aside)
        except OSError as exc:
            log.warning("Could not move the unreadable settings aside: %s", exc)


# --- Dataclass <-> JSON ----------------------------------------------------------------------------


def to_jsonable(value: Any) -> Any:  # noqa: ANN401 - generic serializer
    """Convert dataclasses and enums into JSON-compatible values."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {item.name: to_jsonable(getattr(value, item.name)) for item in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    return value


def from_jsonable[T](cls: type[T], data: object) -> T:
    """Build ``cls`` from JSON data, keeping the default for every missing or invalid field."""
    if not isinstance(data, dict):
        return cls()
    hints = typing.get_type_hints(cls)
    values: dict[str, Any] = {}
    for item in dataclasses.fields(cls):  # type: ignore[arg-type]
        if item.name not in data:
            continue
        converted = _convert(hints[item.name], data[item.name])
        if converted is not _INVALID:
            values[item.name] = converted
    return cls(**values)


_INVALID = object()


def _convert(hint: Any, value: object) -> Any:  # noqa: ANN401, PLR0911 - one branch per supported type
    """Convert one JSON value to ``hint``; returns ``_INVALID`` if it does not fit."""
    if isinstance(hint, types.UnionType):
        if value is None and type(None) in hint.__args__:
            return None
        hint = next(arg for arg in hint.__args__ if arg is not type(None))
    if isinstance(hint, type) and dataclasses.is_dataclass(hint):
        return from_jsonable(hint, value)
    if isinstance(hint, type) and issubclass(hint, Enum):
        try:
            return hint(value)
        except ValueError:
            return _INVALID
    if hint is float and isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if hint is int and isinstance(value, int) and not isinstance(value, bool):
        return value
    if hint in (bool, str) and isinstance(value, hint):
        return value
    return _INVALID


def normalized(settings: AppSettings) -> AppSettings:
    """Return ``settings`` with every value converted to its declared type, exactly as after loading the file.

    Guards against values of the wrong type (e.g. plain strings for enums coming from widgets).
    """
    return from_jsonable(AppSettings, to_jsonable(settings))


# --- Dotted access -------------------------------------------------------------------------------


def setting_value(settings: AppSettings, path: str) -> Any:  # noqa: ANN401 - values of any settings field
    """Return the value at a dotted path such as ``"jpeg.quality"``."""
    value: Any = settings
    for name in path.split("."):
        value = getattr(value, name)
    return value


def with_setting(settings: Any, path: str, value: Any) -> Any:  # noqa: ANN401 - works on any nested settings dataclass
    """Return a copy of ``settings`` with the dotted ``path`` set to ``value``."""
    head, _, rest = path.partition(".")
    if not rest:
        return dataclasses.replace(settings, **{head: value})
    return dataclasses.replace(settings, **{head: with_setting(getattr(settings, head), rest, value)})
