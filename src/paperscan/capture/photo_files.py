"""Local photo files: loading, capture time and folder listing.

Local files and the phone (see ``adb_import``) are equal sources; both end up
as paths handled here.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener
except ImportError:  # optional extra "heic": without it HEIC photos cannot be opened
    pass
else:
    register_heif_opener()

log = logging.getLogger(__name__)

#: File extensions treated as photos (lower case).
PHOTO_EXTENSIONS: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff")
#: EXIF sub-IFD and its "DateTimeOriginal" tag, with the EXIF date format.
EXIF_SUB_IFD = 0x8769
EXIF_DATE_TIME_ORIGINAL = 36867
EXIF_DATE_FORMAT = "%Y:%m:%d %H:%M:%S"
EXIF_ORIENTATION = 0x0112
JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"
#: EXIF orientation value -> rotation that makes the image upright.
_UPRIGHT: dict[int, Image.Transpose] = {3: Image.Transpose.ROTATE_180, 6: Image.Transpose.ROTATE_270, 8: Image.Transpose.ROTATE_90}


def is_photo(path: Path) -> bool:
    """Return whether ``path`` has a photo file extension."""
    return path.suffix.lower() in PHOTO_EXTENSIONS


def load_photo(path: Path) -> np.ndarray:
    """Load a photo as an upright RGB array (EXIF orientation applied)."""
    with Image.open(path) as image:
        return np.asarray(ImageOps.exif_transpose(image).convert("RGB"))


def capture_time(path: Path) -> datetime:
    """Return the EXIF capture time, or the file modification time."""
    try:
        with Image.open(path) as image:
            raw = image.getexif().get_ifd(EXIF_SUB_IFD).get(EXIF_DATE_TIME_ORIGINAL)
        if raw:
            return datetime.strptime(str(raw).strip(), EXIF_DATE_FORMAT)
    except (OSError, ValueError) as exc:
        log.debug("No EXIF date in %s: %s", path, exc)
    return datetime.fromtimestamp(path.stat().st_mtime)


def embedded_thumbnail(head: bytes) -> np.ndarray | None:
    """Return the upright EXIF thumbnail found in the first bytes of a JPEG, or None.

    Phone cameras embed a small JPEG (typically 512 px) in the EXIF block, so the
    first ~128 KB of a photo are enough for a preview.
    """
    try:
        with Image.open(io.BytesIO(head)) as photo:
            orientation = photo.getexif().get(EXIF_ORIENTATION, 1)
    except (OSError, SyntaxError, ValueError):
        return None
    start = head.find(JPEG_START, len(JPEG_START))
    end = head.find(JPEG_END, start)
    if start < 0 or end < 0:
        return None
    try:
        with Image.open(io.BytesIO(head[start : end + len(JPEG_END)])) as thumbnail:
            upright = thumbnail.transpose(_UPRIGHT[orientation]) if orientation in _UPRIGHT else thumbnail.copy()
            return np.asarray(upright.convert("RGB"))
    except (OSError, SyntaxError, ValueError):
        return None


def thumbnail_of_file(path: Path, edge: int) -> np.ndarray:
    """Return an upright preview of a local photo with the longer side at most ``edge``."""
    with Image.open(path) as image:
        upright = ImageOps.exif_transpose(image).convert("RGB")
        upright.thumbnail((edge, edge))
        return np.asarray(upright)


def photos_in_folder(folder: Path) -> list[Path]:
    """Return the photos directly in ``folder``, oldest capture first (page order)."""
    photos = [path for path in folder.iterdir() if path.is_file() and is_photo(path)]
    return sorted(photos, key=capture_time)
