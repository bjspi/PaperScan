"""Compare filter variants on one page and optionally let a vision model choose.

Used by the GUI's comparison dialog and by ``paperscan compare``: the
page is flattened once, every variant's filter is applied to that same image,
and the renderings (plus an overview sheet) can be rated by the configured AI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from paperscan.analysis.variant_rating import VariantRating, rate_variants
from paperscan.capture.photo_files import load_photo
from paperscan.imaging.filter_variants import FilterVariant, overview_sheet
from paperscan.imaging.filters import apply_filter
from paperscan.pipeline import ScanPage, ai_image, flatten_page
from paperscan.settings import AppSettings

#: Long edge of comparison renderings (enough to judge text, fast to compute).
COMPARE_EDGE = 1600
OVERVIEW_FILE = "overview.jpg"


@dataclass(frozen=True)
class VariantRender:
    """One variant applied to the page."""

    variant: FilterVariant
    rgb: np.ndarray


def render_variants(page_rgb: np.ndarray, variants: list[FilterVariant]) -> list[VariantRender]:
    """Apply every variant to the same flattened page image."""
    return [VariantRender(variant, apply_filter(page_rgb, variant.settings)) for variant in variants]


def render_page_variants(page: ScanPage, variants: list[FilterVariant], edge: int = COMPARE_EDGE) -> list[VariantRender]:
    """Load, flatten and render ``page`` once per variant."""
    flattened = flatten_page(load_photo(page.source), page.edit, edge)
    return render_variants(flattened, variants)


def rate_renders(renders: list[VariantRender], settings: AppSettings) -> VariantRating:
    """Ask the configured vision model which rendering is best."""
    return rate_variants([ai_image(render.rgb, settings) for render in renders], settings.ai.provider)


def overview(renders: list[VariantRender]) -> np.ndarray:
    """Return the numbered overview sheet of ``renders``."""
    return overview_sheet([render.rgb for render in renders], [render.variant.number for render in renders])


def write_renders(renders: list[VariantRender], folder: Path) -> dict[int, Path]:
    """Write ``variant-NN.jpg`` files and the overview sheet; return the file per variant number."""
    folder.mkdir(parents=True, exist_ok=True)
    files: dict[int, Path] = {}
    for render in renders:
        path = folder / f"variant-{render.variant.number:02d}.jpg"
        _write_rgb(path, render.rgb)
        files[render.variant.number] = path
    _write_rgb(folder / OVERVIEW_FILE, overview(renders))
    return files


def _write_rgb(path: Path, rgb: np.ndarray) -> None:
    """Save an RGB image as JPEG."""
    if not cv2.imwrite(str(path), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)):
        raise OSError(f"Could not write {path}")
