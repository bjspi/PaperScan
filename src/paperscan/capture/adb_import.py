"""Import camera photos from an Android phone over ADB.

Lists the newest images in the phone's camera folder and pulls the selected
ones into a local import directory. Photos are never deleted on the phone.
"""

from __future__ import annotations

import logging
import posixpath
import shlex
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from paperscan.capture.photo_files import PHOTO_EXTENSIONS, embedded_thumbnail, thumbnail_of_file
from paperscan.platform_tools import Tool, require_tool, run_tool, run_tool_bytes

log = logging.getLogger(__name__)

#: Default camera folder on Android phones.
DEFAULT_CAMERA_DIR = "/sdcard/DCIM/Camera"
#: Timeout for listing and short adb queries, in seconds.
ADB_QUERY_TIMEOUT = 20.0
#: Timeout for pulling one photo, in seconds.
ADB_PULL_TIMEOUT = 120.0
#: MediaStore table of images; deleting a row there makes galleries forget the photo.
MEDIA_STORE_IMAGES = "content://media/external/images/media"
#: Bytes read from the start of a photo for its EXIF thumbnail (phone thumbnails are 20–60 KB).
PHOTO_HEAD_BYTES = 131072
#: Device state reported by ``adb devices`` for usable devices.
DEVICE_READY_STATE = "device"


class AdbError(RuntimeError):
    """Raised when adb reports an error or no device is connected."""


@dataclass(frozen=True)
class Device:
    """A connected Android device."""

    serial: str
    state: str
    model: str

    @property
    def ready(self) -> bool:
        """Return whether the device accepts commands (authorised and online)."""
        return self.state == DEVICE_READY_STATE


@dataclass(frozen=True)
class RemotePhoto:
    """A photo on the phone."""

    path: str
    modified: datetime
    size: int

    @property
    def name(self) -> str:
        """Return the file name."""
        return posixpath.basename(self.path)


class AdbClient:
    """Thin wrapper around the adb executable for one (optional) device serial."""

    def __init__(self, adb_path: str = "", serial: str = "") -> None:
        """Resolve adb now so a missing tool is reported before any work starts."""
        self._adb = str(require_tool(Tool.ADB, adb_path))
        self._serial = serial

    def devices(self) -> list[Device]:
        """Return all devices adb knows about."""
        output = self._run(["devices", "-l"], with_serial=False)
        return [device for line in output.splitlines()[1:] if (device := _parse_device_line(line))]

    def list_photos(self, camera_dir: str = DEFAULT_CAMERA_DIR, limit: int = 50) -> list[RemotePhoto]:
        """Return the newest photos in ``camera_dir``, newest first."""
        script = f"cd {shlex.quote(camera_dir)} && ls -t | head -n {int(limit) * 2} | while read f; do stat -c '%Y %s %n' \"$f\"; done"
        output = self._run(["shell", script])
        photos = [photo for line in output.splitlines() if (photo := _parse_stat_line(line, camera_dir))]
        log.info("Listed %d photo(s) in %s", min(len(photos), limit), camera_dir)
        return photos[:limit]

    def pull(self, photo: RemotePhoto, target_dir: Path) -> Path:
        """Copy one photo to ``target_dir`` (kept if it already exists) and return its local path."""
        target_dir.mkdir(parents=True, exist_ok=True)
        local = target_dir / photo.name
        if local.is_file() and local.stat().st_size == photo.size:
            return local
        self._run(["pull", "-a", photo.path, str(local)], timeout=ADB_PULL_TIMEOUT)
        log.info("Copied %s from the phone to %s", photo.name, local)
        return local

    def read_head(self, photo: RemotePhoto, size: int = PHOTO_HEAD_BYTES) -> bytes:
        """Return the first ``size`` bytes of a photo (enough for its EXIF data and embedded thumbnail)."""
        script = f"dd if={shlex.quote(photo.path)} bs={int(size)} count=1 2>/dev/null"
        try:
            return run_tool_bytes([*self._command(), "exec-out", script], timeout=ADB_QUERY_TIMEOUT)
        except Exception as exc:
            raise AdbError(str(exc)) from exc

    def delete(self, photo: RemotePhoto) -> None:
        """Delete a photo on the phone permanently (it does not go to the gallery's trash).

        The file is removed and its MediaStore entry dropped so galleries stop showing it.
        """
        quoted = shlex.quote(photo.path)
        self._run(["shell", f"rm -f {quoted} && [ ! -e {quoted} ]"])
        log.info("Deleted %s on the phone", photo.path)
        where = "_data='" + photo.path.replace("'", "''") + "'"
        try:
            self._run(["shell", "content", "delete", "--uri", MEDIA_STORE_IMAGES, "--where", shlex.quote(where)])
        except AdbError as exc:  # the file is gone; a stale gallery entry is only cosmetic
            log.info("MediaStore entry of %s not removed: %s", photo.path, exc)

    def thumbnail(self, photo: RemotePhoto, fallback_dir: Path, edge: int) -> np.ndarray:
        """Return a small upright preview: the embedded EXIF thumbnail, else from the pulled photo.

        The fallback pulls the photo into ``fallback_dir`` (the import folder), so a later
        import of the same photo needs no second download.
        """
        embedded = embedded_thumbnail(self.read_head(photo))
        if embedded is not None:
            return embedded
        return thumbnail_of_file(self.pull(photo, fallback_dir), edge)

    def _command(self, *, with_serial: bool = True) -> list[str]:
        """Return the adb executable plus the device selection."""
        if with_serial and self._serial:
            return [self._adb, "-s", self._serial]
        return [self._adb]

    def _run(self, arguments: list[str], *, with_serial: bool = True, timeout: float = ADB_QUERY_TIMEOUT) -> str:
        """Run adb and return stdout; wraps failures in :class:`AdbError`."""
        try:
            return run_tool(self._command(with_serial=with_serial) + arguments, timeout=timeout).stdout
        except Exception as exc:
            stderr = getattr(exc, "stderr", "") or ""
            raise AdbError(stderr.strip() or str(exc)) from exc


def _parse_device_line(line: str) -> Device | None:
    """Parse one ``adb devices -l`` line such as ``0123456789ABCDEF device product:x model:Pixel_8``."""
    parts = line.split()
    if len(parts) < 2:
        return None
    details = dict(part.split(":", 1) for part in parts[2:] if ":" in part)
    return Device(serial=parts[0], state=parts[1], model=details.get("model", "").replace("_", " "))


def _parse_stat_line(line: str, camera_dir: str) -> RemotePhoto | None:
    """Parse ``<mtime> <size> <name>`` from the remote stat output, skipping non-photos."""
    parts = line.split(" ", 2)
    if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    name = parts[2].strip()
    if not name.lower().endswith(PHOTO_EXTENSIONS):
        return None
    return RemotePhoto(path=posixpath.join(camera_dir, name), modified=datetime.fromtimestamp(int(parts[0])), size=int(parts[1]))
