"""Console output helpers shared by all CLI commands."""

from __future__ import annotations

import dataclasses
import json
from enum import Enum
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.theme import Theme

from paperscan import __version__

THEME = Theme({"accent": "bold cyan", "muted": "grey62", "ok": "bold green", "warn": "bold yellow", "error": "bold red", "label": "bold"})
stdout = Console(theme=THEME, highlight=False)
stderr = Console(theme=THEME, highlight=False, stderr=True)

BANNER = r"""
   ___                     ___
  | _ \__ _ _ __  ___ _ _ / __| __ __ _ _ _
  |  _/ _` | '_ \/ -_) '_|\__ \/ _/ _` | ' \
  |_| \__,_| .__/\___|_|  |___/\__\__,_|_||_|
           |_|
"""


def banner_text() -> str:
    """Return the ASCII header with the version, styled for rich."""
    return f"[accent]{BANNER}[/accent]\n[muted]  Phone photos → clean, searchable PDFs · v{__version__}[/muted]\n"


def print_banner() -> None:
    """Print the ASCII header to stderr (keeps stdout clean for --json)."""
    if stderr.is_terminal:
        stderr.print(banner_text())


def print_json(value: object) -> None:
    """Print ``value`` as one JSON document (dataclasses, enums and paths are converted)."""
    stdout.print_json(json.dumps(_jsonable(value), ensure_ascii=False, default=str))


def _jsonable(value: Any) -> Any:  # noqa: ANN401 - generic converter
    """Convert dataclasses, enums and paths for JSON output."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {item.name: _jsonable(getattr(value, item.name)) for item in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def facts_table(title: str, rows: list[tuple[str, str]]) -> Table:
    """Return a two-column label/value table."""
    table = Table.grid(padding=(0, 3))
    table.title = title
    table.add_column(style="label")
    table.add_column()
    for label, value in rows:
        table.add_row(label, value)
    return table


def success(message: str) -> None:
    """Print a success line."""
    stderr.print(f"[ok]✔[/ok] {message}")


def warning(message: str) -> None:
    """Print a warning line."""
    stderr.print(f"[warn]![/warn] {message}")


def error(message: str, hint: str = "") -> None:
    """Print an error line with an optional hint."""
    stderr.print(f"[error]✘[/error] {message}")
    if hint:
        stderr.print(f"  [muted]{hint}[/muted]")
