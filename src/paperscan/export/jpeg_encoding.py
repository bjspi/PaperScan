"""JPEG encoding with jpegli (like XnConvert) and a Pillow fallback.

Pages are written from raw pixels, so the output never carries EXIF, GPS or
other metadata from the camera photo.
"""

from __future__ import annotations

import io
import logging
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from paperscan.platform_tools import Tool, find_tool, run_tool

log = logging.getLogger(__name__)

#: Timeout for one cjpegli run, in seconds.
CJPEGLI_TIMEOUT = 120.0


class ChromaSubsampling(StrEnum):
    """JPEG chroma subsampling; 4:4:4 keeps coloured text and stamps sharp."""

    FULL = "444"
    HALF = "422"
    QUARTER = "420"


@dataclass(frozen=True)
class JpegOptions:
    """Encoder settings.

    Attributes:
        quality: libjpeg-style quality 1..100 (jpegli maps it to a butteraugli distance).
        subsampling: Chroma subsampling for colour pages.
        use_jpegli: Prefer cjpegli; falls back to Pillow if it is not installed.
        cjpegli_path: Explicit cjpegli path; empty means search PATH.
    """

    quality: int = 82
    subsampling: ChromaSubsampling = ChromaSubsampling.FULL
    use_jpegli: bool = True
    cjpegli_path: str = ""


def encode_jpeg(rgb: np.ndarray, options: JpegOptions) -> bytes:
    """Encode an RGB image to JPEG bytes without any metadata."""
    cjpegli = find_tool(Tool.CJPEGLI, options.cjpegli_path) if options.use_jpegli else None
    if cjpegli is not None:
        return _encode_with_cjpegli(rgb, options, cjpegli)
    if options.use_jpegli:
        log.info("cjpegli not found, encoding with Pillow")
    return _encode_with_pillow(rgb, options)


def jpegli_available(options: JpegOptions) -> bool:
    """Return whether cjpegli would be used for ``options``."""
    return options.use_jpegli and find_tool(Tool.CJPEGLI, options.cjpegli_path) is not None


def _encode_with_cjpegli(rgb: np.ndarray, options: JpegOptions, cjpegli: Path) -> bytes:
    """Encode through a lossless PNG handed to cjpegli."""
    with tempfile.TemporaryDirectory(prefix="csocr-") as tmp:
        source, target = Path(tmp) / "page.png", Path(tmp) / "page.jpg"
        cv2.imwrite(str(source), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        command = [
            str(cjpegli),
            str(source),
            str(target),
            f"--quality={options.quality}",
            f"--chroma_subsampling={options.subsampling.value}",
        ]
        run_tool(command, timeout=CJPEGLI_TIMEOUT)
        return target.read_bytes()


_PILLOW_SUBSAMPLING: dict[ChromaSubsampling, int] = {
    ChromaSubsampling.FULL: 0,
    ChromaSubsampling.HALF: 1,
    ChromaSubsampling.QUARTER: 2,
}


def _encode_with_pillow(rgb: np.ndarray, options: JpegOptions) -> bytes:
    """Encode with Pillow's libjpeg as optimised progressive JPEG."""
    buffer = io.BytesIO()
    Image.fromarray(rgb).save(
        buffer,
        format="JPEG",
        quality=options.quality,
        subsampling=_PILLOW_SUBSAMPLING[options.subsampling],
        optimize=True,
        progressive=True,
    )
    return buffer.getvalue()
