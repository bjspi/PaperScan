"""Filter variants for side-by-side comparison.

A variant is a numbered :class:`FilterSettings`. Variants come either from a
fixed set of presets or from sweeping one numeric parameter over several
values ("simulate thresholds"). The overview sheet puts all renderings with
their numbers on one image, for people and for vision models.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from dataclasses import dataclass, replace

import cv2
import numpy as np

from paperscan.imaging.filters import FilterMode, FilterSettings
from paperscan.imaging.geometry import limit_long_edge

#: Long edge of one tile in the overview sheet.
SHEET_TILE_EDGE = 600
SHEET_COLUMNS = 4
SHEET_GAP = 16
SHEET_BACKGROUND = (128, 128, 128)
LABEL_COLOR = (220, 30, 30)
LABEL_SCALE = 2.0
LABEL_THICKNESS = 4
LABEL_MARGIN = 12
#: Upper bound so a typo like ``--vary bw_threshold=0..255`` cannot create hundreds of renders.
MAX_VARIANTS = 16


class VariantError(ValueError):
    """Raised for unknown fields or values that do not fit a field."""


@dataclass(frozen=True)
class FilterVariant:
    """A numbered filter setting (numbers start at 1)."""

    number: int
    name: str
    settings: FilterSettings


_PRESETS: tuple[tuple[str, FilterSettings], ...] = (
    ("Document", FilterSettings(mode=FilterMode.DOCUMENT)),
    ("Document soft", FilterSettings(mode=FilterMode.DOCUMENT, soft_contrast=1.0, ink_darkness=0.85)),
    ("Clear", FilterSettings(mode=FilterMode.CLEAR)),
    ("Clear dark ink", FilterSettings(mode=FilterMode.CLEAR, hard_contrast=0.5, ink_darkness=0.55)),
    ("Clear gray", FilterSettings(mode=FilterMode.CLEAR, grayscale=True)),
    ("Black & white light", FilterSettings(mode=FilterMode.BLACK_WHITE, bw_threshold=130)),
    ("Black & white dark", FilterSettings(mode=FilterMode.BLACK_WHITE, bw_threshold=170)),
    ("Photo", FilterSettings(mode=FilterMode.PHOTO)),
)


def preset_variants() -> list[FilterVariant]:
    """Return the built-in comparison set."""
    return [FilterVariant(number, name, settings) for number, (name, settings) in enumerate(_PRESETS, start=1)]


def sweep_variants(base: FilterSettings, field: str, values: list[str]) -> list[FilterVariant]:
    """Return one variant of ``base`` per value of ``field`` (values given as text)."""
    if len(values) > MAX_VARIANTS:
        raise VariantError(f"At most {MAX_VARIANTS} values per sweep.")
    variants = []
    for number, value in enumerate(values, start=1):
        settings = with_filter_values(base, {field: value})
        variants.append(FilterVariant(number, f"{field}={value}", settings))
    return variants


def filter_fields() -> list[str]:
    """Return the names of all adjustable filter fields."""
    return [item.name for item in dataclasses.fields(FilterSettings)]


def with_filter_values(base: FilterSettings, values: dict[str, str]) -> FilterSettings:
    """Return ``base`` with fields set from text values, converted to each field's type.

    ``"auto"`` or ``"none"`` reset optional fields (black/white point) to automatic.
    """
    hints = typing.get_type_hints(FilterSettings)
    changes: dict[str, object] = {}
    for field, text in values.items():
        if field not in hints:
            raise VariantError(f"Unknown filter field '{field}'. Known: {', '.join(filter_fields())}")
        changes[field] = _convert(field, hints[field], text.strip())
    return replace(base, **changes)  # type: ignore[arg-type]


def filter_values(settings: FilterSettings) -> dict[str, str]:
    """Return ``settings`` as text values: the mode plus every field that differs from the defaults.

    Inverse of :func:`with_filter_values` applied to the default settings.
    """
    defaults = FilterSettings()
    values: dict[str, str] = {}
    for field in filter_fields():
        value = getattr(settings, field)
        if value != getattr(defaults, field) or field == "mode":
            values[field] = _to_text(value)
    return values


def _to_text(value: object) -> str:
    """Return the text form understood by :func:`with_filter_values`."""
    if value is None:
        return "auto"
    if isinstance(value, FilterMode):
        return value.value
    return str(value).lower()


def _convert(field: str, hint: object, text: str) -> object:
    """Convert ``text`` to the type of ``field``."""
    optional = isinstance(hint, types.UnionType) and type(None) in typing.get_args(hint)
    if optional and text.lower() in {"auto", "none"}:
        return None
    target = next(arg for arg in typing.get_args(hint) if arg is not type(None)) if optional else hint
    try:
        if target is FilterMode:
            return FilterMode(text)
        if target is bool:
            return _parse_bool(text)
        if target is int:
            return int(text)
        if target is float:
            return float(text)
    except ValueError as exc:
        raise VariantError(f"'{text}' is not a valid value for {field}") from exc
    raise VariantError(f"Field '{field}' cannot be set from text")


def _parse_bool(text: str) -> bool:
    """Parse yes/no style text."""
    lowered = text.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValueError(text)


def overview_sheet(images: list[np.ndarray], numbers: list[int]) -> np.ndarray:
    """Return one RGB image with all renderings in a grid, each labelled with its number."""
    tiles = [_labelled_tile(image, number) for image, number in zip(images, numbers, strict=True)]
    tile_height = max(tile.shape[0] for tile in tiles)
    tile_width = max(tile.shape[1] for tile in tiles)
    columns = min(SHEET_COLUMNS, len(tiles))
    rows = -(-len(tiles) // columns)
    sheet = np.full(
        (rows * (tile_height + SHEET_GAP) + SHEET_GAP, columns * (tile_width + SHEET_GAP) + SHEET_GAP, 3), SHEET_BACKGROUND, np.uint8
    )
    for index, tile in enumerate(tiles):
        top = SHEET_GAP + (index // columns) * (tile_height + SHEET_GAP)
        left = SHEET_GAP + (index % columns) * (tile_width + SHEET_GAP)
        sheet[top : top + tile.shape[0], left : left + tile.shape[1]] = tile
    return sheet


def _labelled_tile(image: np.ndarray, number: int) -> np.ndarray:
    """Return a downscaled copy of ``image`` with its number in the top-left corner."""
    tile = limit_long_edge(image, SHEET_TILE_EDGE).copy()
    origin = (LABEL_MARGIN, LABEL_MARGIN + int(30 * LABEL_SCALE))
    cv2.putText(tile, str(number), origin, cv2.FONT_HERSHEY_SIMPLEX, LABEL_SCALE, (255, 255, 255), LABEL_THICKNESS * 3)
    cv2.putText(tile, str(number), origin, cv2.FONT_HERSHEY_SIMPLEX, LABEL_SCALE, LABEL_COLOR, LABEL_THICKNESS)
    return tile
