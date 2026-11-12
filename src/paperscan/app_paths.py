"""Per-user application directories (identical layout on Windows and macOS)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

APP_NAME = "PaperScan"
APP_DATA_DIR = Path.home() / f".{APP_NAME}"
CONFIG_FILE = APP_DATA_DIR / "config.json"
#: Tools downloaded by the setup assistant (cjpegli, adb).
TOOLS_DIR = APP_DATA_DIR / "tools"
#: Tesseract language data downloaded by the setup assistant.
TESSDATA_DIR = APP_DATA_DIR / "tessdata"
#: Optional page corner model imported with ``model import``.
MODEL_PATH = APP_DATA_DIR / "models" / "page_corners.tflite"
IMPORT_DIR = APP_DATA_DIR / "imports"
DEFAULT_OUTPUT_DIR = Path.home() / "Documents" / APP_NAME
#: Empty marker file that tells backup tools honouring it to skip a folder of reproducible data.
BACKUP_EXCLUSION_MARKER = ".nobackup"
#: Daily rotating log file.
LOG_FILE = APP_DATA_DIR / "logs" / f"{APP_NAME}.log"
#: Suffix of a file while it is being downloaded; it gets its final name only when complete.
PARTIAL_SUFFIX = ".part"


def make_reproducible_dir(path: Path) -> Path:
    """Create a folder for downloaded or imported data and mark it as excluded from backups.

    An existing marker is never touched.
    """
    path.mkdir(parents=True, exist_ok=True)
    marker = path / BACKUP_EXCLUSION_MARKER
    if not marker.exists():
        marker.touch()
    return path


def partial_path(target: Path) -> Path:
    """Return where ``target`` is written until it is complete (see :data:`PARTIAL_SUFFIX`).

    The name is unique per call, so two transfers of the same file never share it.
    """
    return target.with_name(f"{target.name}.{uuid4().hex}{PARTIAL_SUFFIX}")
