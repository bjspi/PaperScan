"""Colour filters for photographed document pages.

The document filters estimate the paper colour block by block, build a smooth
background (shading) map from it, then map every pixel's distance to that
background through cosine tone curves, so paper turns white regardless of
uneven light. The photo filter is a linear black/white point stretch. Black &
white adds a global threshold on top of the "clear" result.

All images are RGB ``uint8`` arrays. Gray output is returned as RGB too so the
rest of the pipeline only ever handles one layout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum

import cv2
import numpy as np

#: Intensity range of 8-bit channels.
MAX_LEVEL = 255
#: Resolution of the ratio tone curve.
RATIO_STEPS = 2000
#: Aim for roughly this many pixels per background block.
PIXELS_PER_BLOCK = 8000
#: Minimum background block edge length (one is added to it).
MIN_BLOCK_EDGE = 14
#: Kernel of the Gaussian blur that smooths the block background map.
BACKGROUND_BLUR_KERNEL = (9, 9)
#: Percentiles used to estimate the photo filter's black and white points.
AUTO_BLACK_PERCENTILE = 1.0
AUTO_WHITE_PERCENTILE = 99.0


class FilterMode(StrEnum):
    """Available page filters."""

    ORIGINAL = "original"
    PHOTO = "photo"
    DOCUMENT = "document"
    CLEAR = "clear"
    BLACK_WHITE = "black_white"


@dataclass(frozen=True)
class FilterSettings:
    """User-adjustable filter parameters with tuned defaults.

    Attributes:
        mode: Which filter to apply.
        grayscale: Convert the result to gray (ignored for black & white).
        soft_contrast: Exponent of the "document" difference curve (default 0.8).
        hard_contrast: Exponent of the "clear" difference curve (default 0.625).
        ink_darkness: Exponent of the ink ratio curve; lower = darker ink (default 0.7).
        edge_sharpening: Laplacian value at which "document" switches fully to the hard
            curve; lower = crisper text edges (default 50).
        paper_min_brightness: Minimum channel value for a block to count as paper (default 80).
        paper_max_tint: Maximum channel spread for a block to count as paper (default 60).
        black_point: Photo filter input level mapped to black, or None for automatic.
        white_point: Photo filter input level mapped to white, or None for automatic.
        bw_threshold: Gray level separating black from white in black & white mode.
    """

    mode: FilterMode = FilterMode.DOCUMENT
    grayscale: bool = False
    soft_contrast: float = 0.8
    hard_contrast: float = 0.625
    ink_darkness: float = 0.7
    edge_sharpening: float = 50.0
    paper_min_brightness: int = 80
    paper_max_tint: int = 60
    black_point: int | None = None
    white_point: int | None = None
    bw_threshold: int = 150

    def with_mode(self, mode: FilterMode) -> FilterSettings:
        """Return a copy with another filter mode."""
        return replace(self, mode=mode)


def apply_filter(rgb: np.ndarray, settings: FilterSettings) -> np.ndarray:
    """Apply the filter described by ``settings`` to an RGB image."""
    match settings.mode:
        case FilterMode.ORIGINAL:
            result = rgb
        case FilterMode.PHOTO:
            result = enhance_photo(rgb, settings.black_point, settings.white_point)
        case FilterMode.DOCUMENT | FilterMode.CLEAR:
            result = enhance_document(rgb, settings)
        case FilterMode.BLACK_WHITE:
            return black_and_white(rgb, settings)
    if settings.grayscale:
        return to_gray_rgb(result)
    return result


def to_gray_rgb(rgb: np.ndarray) -> np.ndarray:
    """Convert to gray but keep three channels."""
    return cv2.cvtColor(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), cv2.COLOR_GRAY2RGB)


# --- Photo filter -----------------------------------------------------------------------------


def estimate_levels(rgb: np.ndarray) -> tuple[int, int]:
    """Estimate black and white points from the luminance histogram."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    black, white = np.percentile(gray, [AUTO_BLACK_PERCENTILE, AUTO_WHITE_PERCENTILE])
    return int(black), int(white)


def enhance_photo(rgb: np.ndarray, black_point: int | None, white_point: int | None) -> np.ndarray:
    """Stretch levels linearly so ``black_point`` becomes 0 and ``white_point`` 255."""
    auto_black, auto_white = estimate_levels(rgb)
    black = auto_black if black_point is None else black_point
    white = auto_white if white_point is None else white_point
    if white <= black:
        return rgb
    levels = np.arange(MAX_LEVEL + 1, dtype=np.float32)
    lut = np.clip((levels - black) / (white - black) * MAX_LEVEL + 0.5, 0, MAX_LEVEL).astype(np.uint8)
    return cv2.LUT(rgb, lut)


# --- Black & white ----------------------------------------------------------------------------


def black_and_white(rgb: np.ndarray, settings: FilterSettings) -> np.ndarray:
    """Clean the page with the clear filter, then threshold it to pure black and white."""
    cleaned = enhance_document(rgb, settings.with_mode(FilterMode.CLEAR))
    gray = cv2.cvtColor(cleaned, cv2.COLOR_RGB2GRAY)
    _, binary = cv2.threshold(gray, settings.bw_threshold, MAX_LEVEL, cv2.THRESH_BINARY)
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2RGB)


# --- Document filters ---------------------------------------------------------------------------


def cosine_curve(steps: int, exponent: float) -> np.ndarray:
    """Return the S-curve ``(1 - cos(pi * (i/steps)^exponent)) / 2 * steps`` for i in 0..steps."""
    x = np.arange(steps + 1, dtype=np.float64) / steps
    return ((1.0 - np.cos(np.pi * x**exponent)) * 0.5 * steps).astype(np.int32)


def block_edge(height: int, width: int) -> int:
    """Return the edge length of the square blocks used to sample the paper colour."""
    return max(MIN_BLOCK_EDGE, int(math.sqrt((height * width) // PIXELS_PER_BLOCK))) + 1


def block_modes(rgb: np.ndarray, edge: int) -> np.ndarray:
    """Return the most frequent value per channel for each block, shape (rows, cols, 3)."""
    height, width, _ = rgb.shape
    rows, cols = height // edge + 1, width // edge + 1
    block_ids = (np.arange(height) // edge)[:, None] * cols + (np.arange(width) // edge)[None, :]
    modes = np.empty((rows * cols, 3), dtype=np.int32)
    for channel in range(3):
        keys = block_ids.ravel() * (MAX_LEVEL + 1) + rgb[..., channel].ravel()
        histogram = np.bincount(keys, minlength=rows * cols * (MAX_LEVEL + 1)).reshape(rows * cols, MAX_LEVEL + 1)
        modes[:, channel] = np.argmax(histogram, axis=1)
    occupied = np.bincount(block_ids.ravel(), minlength=rows * cols) > 0
    modes[~occupied] = -1
    return modes.reshape(rows, cols, 3)


def paper_mask(colors: np.ndarray, settings: FilterSettings) -> np.ndarray:
    """Return which colours (…, 3) look like paper: bright enough and nearly neutral."""
    red, green, blue = (colors[..., channel] for channel in range(3))
    bright = (red >= settings.paper_min_brightness) & (green >= settings.paper_min_brightness) & (blue >= settings.paper_min_brightness)
    spread = colors.max(axis=-1) - colors.min(axis=-1)
    return bright & (spread <= settings.paper_max_tint) & (colors.min(axis=-1) >= 0)


def keep_main_paper_region(modes: np.ndarray, settings: FilterSettings) -> np.ndarray:
    """Invalidate (set to -1) every block outside the largest connected paper region.

    Ink-covered or off-page blocks must not count as background, so only the
    main connected area of paper-like blocks is kept.
    """
    paper = paper_mask(modes, settings).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(paper, connectivity=4)
    result = modes.copy()
    if count <= 1:
        result[paper == 0] = -1
        return result
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    result[labels != largest] = -1
    return result


def fill_invalid_blocks(modes: np.ndarray) -> np.ndarray:
    """Replace invalid blocks with the mean of the valid blocks on the nearest surrounding ring.

    Without any valid block the page is assumed to be white.
    """
    valid = modes[..., 0] >= 0
    if not valid.any():
        return np.full_like(modes, MAX_LEVEL)
    filled = modes.copy()
    rows, cols = valid.shape
    for row, col in zip(*np.nonzero(~valid), strict=True):
        filled[row, col] = _nearest_ring_mean(modes, valid, row, col, max(rows, cols))
    return filled


def _nearest_ring_mean(modes: np.ndarray, valid: np.ndarray, row: int, col: int, max_radius: int) -> np.ndarray:
    """Return the mean colour of the valid blocks on the smallest square ring around (row, col)."""
    for radius in range(1, max_radius + 1):
        top, bottom = max(0, row - radius), min(valid.shape[0], row + radius + 1)
        left, right = max(0, col - radius), min(valid.shape[1], col + radius + 1)
        window = valid[top:bottom, left:right].copy()
        inner_top, inner_left = row - radius + 1 - top, col - radius + 1 - left
        window[max(0, inner_top) : max(0, inner_top + 2 * radius - 1), max(0, inner_left) : max(0, inner_left + 2 * radius - 1)] = False
        if window.any():
            return modes[top:bottom, left:right][window].mean(axis=0).astype(np.int32)
    return np.full(3, MAX_LEVEL, dtype=np.int32)


def paper_white_level(modes: np.ndarray, settings: FilterSettings) -> np.ndarray:
    """Return the target paper colour: white, unless the average paper is clearly coloured."""
    valid = modes[..., 0] >= 0
    if not valid.any():
        return np.full(3, MAX_LEVEL, dtype=np.int32)
    average = modes[valid].mean(axis=0).astype(np.int32)
    if paper_mask(average, settings):
        return np.full(3, MAX_LEVEL, dtype=np.int32)
    return average


def background_map(rgb: np.ndarray, settings: FilterSettings) -> tuple[np.ndarray, np.ndarray]:
    """Return the full-size smooth background image and the target paper colour."""
    height, width, _ = rgb.shape
    modes = block_modes(rgb, block_edge(height, width))
    paper_blocks = keep_main_paper_region(modes, settings)
    white = paper_white_level(paper_blocks, settings)
    filled = fill_invalid_blocks(paper_blocks).astype(np.uint8)
    smooth = cv2.GaussianBlur(filled, BACKGROUND_BLUR_KERNEL, 0)
    background = cv2.resize(smooth, (width, height), interpolation=cv2.INTER_CUBIC)
    return background.astype(np.int32), white


def enhance_document(rgb: np.ndarray, settings: FilterSettings) -> np.ndarray:
    """Flatten the paper to white and darken ink relative to the local background.

    "document" blends a soft and a hard difference curve depending on local
    edge strength; "clear" always uses the hard curve.
    """
    background, white = background_map(rgb, settings)
    difference = rgb.astype(np.int32) - background
    hard = _signed_curve(difference, cosine_curve(MAX_LEVEL, settings.hard_contrast))
    if settings.mode is FilterMode.DOCUMENT:
        soft = _signed_curve(difference, cosine_curve(MAX_LEVEL, settings.soft_contrast))
        weight = _edge_weight(rgb, settings.edge_sharpening)
        curved = (soft + (hard - soft) * weight).astype(np.int32)
    else:
        curved = hard
    return _map_to_output(curved, background, white, cosine_curve(RATIO_STEPS, settings.ink_darkness))


def _signed_curve(difference: np.ndarray, curve: np.ndarray) -> np.ndarray:
    """Apply a magnitude curve while keeping the sign of the difference."""
    return np.sign(difference) * curve[np.abs(difference)]


def _edge_weight(rgb: np.ndarray, full_weight_at: float) -> np.ndarray:
    """Return a (h, w, 1) weight in 0..1 that grows with the Laplacian edge strength."""
    gray = cv2.medianBlur(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), 3)
    laplacian = cv2.Laplacian(gray, cv2.CV_8U, ksize=3)
    return np.minimum(1.0, laplacian / max(full_weight_at, 1.0))[..., None]


def _map_to_output(curved: np.ndarray, background: np.ndarray, white: np.ndarray, ratio_curve: np.ndarray) -> np.ndarray:
    """Map curved differences to output levels around the paper colour ``white``.

    Pixels darker than the background are scaled by how far they are from it
    relative to the background level; brighter pixels move towards 255.
    """
    darker_ratio = np.minimum(RATIO_STEPS, -curved * RATIO_STEPS // np.maximum(background, 1))
    lighter_ratio = np.minimum(RATIO_STEPS, curved * RATIO_STEPS // np.maximum(MAX_LEVEL - background, 1))
    darker = white - ratio_curve[np.clip(darker_ratio, 0, RATIO_STEPS)] * white // RATIO_STEPS
    lighter = white + ratio_curve[np.clip(lighter_ratio, 0, RATIO_STEPS)] * (MAX_LEVEL - white) // RATIO_STEPS
    output = np.where(curved < 0, darker, np.where(curved > 0, lighter, white))
    return np.clip(output, 0, MAX_LEVEL).astype(np.uint8)
