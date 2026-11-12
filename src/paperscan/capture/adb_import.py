"""Import camera photos from an Android phone over ADB.

Lists the newest images in the phone's camera folder and pulls the selected
ones into a local import directory. Photos only leave the phone when the user
explicitly deletes them after an export.

The phone is looked for on USB first. If none is plugged in, the client can
fall back to Wireless debugging (Android 11+): a configured ``IP:port`` or a
phone announced on the local network over mDNS (paired with this computer once).
"""

from __future__ import annotations

import logging
import posixpath
import shlex
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from paperscan.app_paths import partial_path
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
#: Modification time in local copy names (keeps photos with equal names apart).
LOCAL_NAME_TIME_FORMAT = "%Y%m%d-%H%M%S"
#: Extended regular expression (case-insensitive) for the names of photo files on the phone.
PHOTO_NAME_PATTERN = r"\.(" + "|".join(extension.lstrip(".") for extension in PHOTO_EXTENSIONS) + r")$"
#: Device state reported by ``adb devices`` for usable devices.
DEVICE_READY_STATE = "device"
#: Timeout for connecting or pairing over Wi-Fi, in seconds.
ADB_WIRELESS_TIMEOUT = 15.0
#: Port of the classic ``adb tcpip`` mode, used when an address has no port.
LEGACY_TCPIP_PORT = 5555
#: mDNS service of phones with Wireless debugging on (paired computers connect to it).
MDNS_CONNECT_SERVICE = "_adb-tls-connect._tcp"
#: mDNS service of phones in the classic ``adb tcpip`` mode.
MDNS_LEGACY_SERVICE = "_adb._tcp"
#: Replies of ``adb connect`` and ``adb pair`` that mean success (adb also exits with 0 on failure).
CONNECTED_REPLIES = ("connected to", "already connected to")
PAIRED_REPLY = "successfully paired"
NO_PHONE_HINT = "Connect the phone by USB with USB debugging allowed"
WIRELESS_HINT = "or turn on Wireless debugging and pair this computer once (Settings → Phone)"


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

    @property
    def wireless(self) -> bool:
        """Return whether adb reaches the device over the network (``IP:port`` or an mDNS name)."""
        return ":" in self.serial or MDNS_CONNECT_SERVICE in self.serial

    @property
    def label(self) -> str:
        """Return a short human-readable name with the connection type."""
        return f"{self.model or self.serial} ({'Wi-Fi' if self.wireless else 'USB'})"


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

    @property
    def local_name(self) -> str:
        """Return the file name of the local copy, unique per photo.

        Camera apps restart their numbering (after a reset, on another phone), so the
        name alone may stand for different photos; time and size tell them apart.
        """
        stem, suffix = posixpath.splitext(self.name)
        return f"{stem}_{self.modified:{LOCAL_NAME_TIME_FORMAT}}_{self.size}{suffix}"


class AdbClient:
    """Thin wrapper around the adb executable, talking to one phone.

    The phone is chosen on the first command that needs it (see :meth:`connect`)
    and kept for the lifetime of the client.
    """

    def __init__(self, adb_path: str = "", serial: str = "", *, wireless_fallback: bool = False, wireless_address: str = "") -> None:
        """Resolve adb now so a missing tool is reported before any work starts.

        Args:
            adb_path: Configured adb executable; empty finds it automatically.
            serial: Use exactly this device (only needed with several phones).
            wireless_fallback: Look for a phone over Wi-Fi when none is on USB.
            wireless_address: ``IP[:port]`` to connect to over Wi-Fi; empty discovers the phone via mDNS.
        """
        self._adb = str(require_tool(Tool.ADB, adb_path))
        self._serial = serial
        self._wireless_fallback = wireless_fallback
        self._wireless_address = wireless_address.strip()
        self._device: Device | None = None

    def devices(self) -> list[Device]:
        """Return all devices adb knows about."""
        output = self._run(["devices", "-l"], with_device=False)
        return [device for line in output.splitlines()[1:] if (device := _parse_device_line(line))]

    def connect(self) -> Device:
        """Return the phone commands go to, connecting over Wi-Fi if needed.

        Order: the configured serial; else the only ready device, preferring USB;
        else (with the Wi-Fi fallback) the configured address or a phone found via mDNS.
        """
        if self._device is None:
            self._device = self._find_device()
            log.info("Using phone %s", self._device.label)
        return self._device

    def pair(self, address: str, code: str) -> None:
        """Pair this computer with a phone shown under "Pair device with pairing code" (one-time step)."""
        output = self._run(["pair", address.strip(), code.strip()], with_device=False, timeout=ADB_WIRELESS_TIMEOUT)
        if PAIRED_REPLY not in output.lower():
            raise AdbError(output.strip() or f"Pairing with {address} failed")
        log.info("Paired with %s", address)

    def wireless_services(self) -> list[str]:
        """Return the ``IP:port`` addresses of phones that announce adb on the local network."""
        output = self._run(["mdns", "services"], with_device=False)
        return [address for line in output.splitlines() if (address := _parse_mdns_line(line))]

    def list_photos(self, camera_dir: str = DEFAULT_CAMERA_DIR, limit: int = 50) -> list[RemotePhoto]:
        """Return the newest photos in ``camera_dir``, newest first."""
        # Photos are picked out before the list is cut, so new videos cannot crowd them out.
        newest_photos = f"ls -t | grep -iE {shlex.quote(PHOTO_NAME_PATTERN)} | head -n {int(limit)}"
        script = f"cd {shlex.quote(camera_dir)} && {newest_photos} | while read f; do stat -c '%Y %s %n' \"$f\"; done"
        output = self._run(["shell", script])
        photos = [photo for line in output.splitlines() if (photo := _parse_stat_line(line, camera_dir))]
        log.info("Listed %d photo(s) in %s", min(len(photos), limit), camera_dir)
        return photos[:limit]

    def pull(self, photo: RemotePhoto, target_dir: Path) -> Path:
        """Copy one photo to ``target_dir`` (kept if it already exists) and return its local path.

        The photo is downloaded under a temporary name and only renamed once complete, so an
        interrupted copy is never mistaken for an imported photo.
        """
        target_dir.mkdir(parents=True, exist_ok=True)
        local = target_dir / photo.local_name
        if local.is_file() and local.stat().st_size == photo.size:
            return local
        partial = partial_path(local)
        try:
            self._run(["pull", "-a", photo.path, str(partial)], timeout=ADB_PULL_TIMEOUT)
            partial.replace(local)
        finally:
            partial.unlink(missing_ok=True)
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

    # --- Device selection ------------------------------------------------------------------------

    def _find_device(self) -> Device:
        """Choose the phone as described in :meth:`connect`."""
        devices = self.devices()
        if self._serial:
            return _device_with_serial(devices, self._serial)
        ready = [device for device in devices if device.ready]
        usb = [device for device in ready if not device.wireless]
        if len(usb) > 1 or (not usb and len(ready) > 1):
            names = ", ".join(device.label for device in ready)
            raise AdbError(f"Several phones are connected ({names}). Enter the device serial in Settings → Phone.")
        if ready:
            return usb[0] if usb else ready[0]
        if not self._wireless_fallback:
            raise AdbError(f"No phone found. {NO_PHONE_HINT}.")
        return self._connect_wireless()

    def _connect_wireless(self) -> Device:
        """Connect to the configured address, else to the first phone announced over mDNS."""
        candidates = [_with_port(self._wireless_address)] if self._wireless_address else self.wireless_services()
        errors: list[str] = []
        for address in candidates:
            try:
                return self._connect_address(address)
            except AdbError as exc:
                errors.append(str(exc))
        details = f" ({'; '.join(errors)})" if errors else ""
        raise AdbError(f"No phone found over USB or Wi-Fi{details}. {NO_PHONE_HINT}, {WIRELESS_HINT}.")

    def _connect_address(self, address: str) -> Device:
        """Run ``adb connect`` and return the device it added."""
        output = self._run(["connect", address], with_device=False, timeout=ADB_WIRELESS_TIMEOUT)
        if not output.strip().lower().startswith(CONNECTED_REPLIES):
            raise AdbError(output.strip() or f"Cannot connect to {address}")
        log.info("Connected to %s over Wi-Fi", address)
        listed = [device for device in self.devices() if device.serial == address]
        device = listed[0] if listed else Device(serial=address, state=DEVICE_READY_STATE, model="")
        if not device.ready:
            raise AdbError(f"{address} is {device.state}; allow debugging on the phone")
        return device

    # --- Running adb ----------------------------------------------------------------------------

    def _command(self, *, with_device: bool = True) -> list[str]:
        """Return the adb executable plus the selection of the phone (chosen on first use)."""
        if with_device:
            return [self._adb, "-s", self.connect().serial]
        return [self._adb]

    def _run(self, arguments: list[str], *, with_device: bool = True, timeout: float = ADB_QUERY_TIMEOUT) -> str:
        """Run adb and return stdout; wraps failures in :class:`AdbError`."""
        command = self._command(with_device=with_device)
        try:
            return run_tool(command + arguments, timeout=timeout).stdout
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


def _device_with_serial(devices: list[Device], serial: str) -> Device:
    """Return the ready device with ``serial`` or explain why it cannot be used."""
    for device in devices:
        if device.serial != serial:
            continue
        if not device.ready:
            raise AdbError(f"Phone {serial} is {device.state}; allow debugging on the phone")
        return device
    raise AdbError(f"Phone {serial} is not connected. Clear the device serial in Settings → Phone to use any phone.")


def _with_port(address: str) -> str:
    """Return ``address`` with the classic adb port appended when it has none."""
    return address if ":" in address else f"{address}:{LEGACY_TCPIP_PORT}"


def _parse_mdns_line(line: str) -> str | None:
    """Parse ``<name> <service> <IP:port>`` from ``adb mdns services``; pairing services are skipped."""
    parts = line.split()
    if len(parts) < 3 or parts[1].rstrip(".") not in (MDNS_CONNECT_SERVICE, MDNS_LEGACY_SERVICE):
        return None
    return parts[-1]


def _parse_stat_line(line: str, camera_dir: str) -> RemotePhoto | None:
    """Parse ``<mtime> <size> <name>`` from the remote stat output, skipping non-photos."""
    parts = line.split(" ", 2)
    if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    name = parts[2].strip()
    if not name.lower().endswith(PHOTO_EXTENSIONS):
        return None
    return RemotePhoto(path=posixpath.join(camera_dir, name), modified=datetime.fromtimestamp(int(parts[0])), size=int(parts[1]))
