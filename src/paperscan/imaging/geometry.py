"""Page geometry: corner quadrilaterals, perspective correction, rotation and scaling.

Corners are always stored in source-image pixel coordinates in the fixed order
top-left, top-right, bottom-right, bottom-left, so every module (detection, GUI
editor, pipeline) can exchange them without re-sorting.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import cv2
import numpy as np

CORNER_COUNT = 4


class Rotation(IntEnum):
    """Clockwise page rotation applied after perspective correction."""

    NONE = 0
    CW_90 = 90
    CW_180 = 180
    CW_270 = 270

    def turned_clockwise(self) -> Rotation:
        """Return the rotation after one more clockwise quarter turn."""
        return Rotation((self.value + 90) % 360)

    def turned_counterclockwise(self) -> Rotation:
        """Return the rotation after one counter-clockwise quarter turn."""
        return Rotation((self.value + 270) % 360)


@dataclass(frozen=True)
class Point:
    """A point in image pixel coordinates."""

    x: float
    y: float


@dataclass(frozen=True)
class Quad:
    """Page outline as four corners: top-left, top-right, bottom-right, bottom-left."""

    top_left: Point
    top_right: Point
    bottom_right: Point
    bottom_left: Point

    @classmethod
    def full_image(cls, width: int, height: int) -> Quad:
        """Return the quad covering the whole image."""
        return cls(Point(0, 0), Point(width, 0), Point(width, height), Point(0, height))

    @classmethod
    def from_array(cls, corners: np.ndarray) -> Quad:
        """Build a quad from a (4, 2) array in TL, TR, BR, BL order."""
        points = [Point(float(x), float(y)) for x, y in corners.reshape(CORNER_COUNT, 2)]
        return cls(*points)

    def to_array(self) -> np.ndarray:
        """Return the corners as a float32 (4, 2) array in TL, TR, BR, BL order."""
        return np.array([[p.x, p.y] for p in self.points], dtype=np.float32)

    @property
    def points(self) -> tuple[Point, Point, Point, Point]:
        """Return the corners in canonical order."""
        return (self.top_left, self.top_right, self.bottom_right, self.bottom_left)

    def scaled(self, factor: float) -> Quad:
        """Return the quad with all coordinates multiplied by ``factor``."""
        return Quad.from_array(self.to_array() * factor)

    def clamped(self, width: int, height: int) -> Quad:
        """Return the quad with every corner moved inside the image bounds."""
        corners = self.to_array()
        corners[:, 0] = np.clip(corners[:, 0], 0, width)
        corners[:, 1] = np.clip(corners[:, 1], 0, height)
        return Quad.from_array(corners)

    def area(self) -> float:
        """Return the polygon area in square pixels."""
        return float(cv2.contourArea(self.to_array()))


def order_corners(corners: np.ndarray) -> np.ndarray:
    """Sort four points into TL, TR, BR, BL order.

    The top-left corner has the smallest x+y sum and the bottom-right the
    largest; top-right has the smallest y-x difference, bottom-left the largest.
    """
    points = corners.reshape(CORNER_COUNT, 2).astype(np.float32)
    sums = points.sum(axis=1)
    diffs = np.diff(points, axis=1).ravel()
    return np.array(
        [points[np.argmin(sums)], points[np.argmin(diffs)], points[np.argmax(sums)], points[np.argmax(diffs)]],
        dtype=np.float32,
    )


def quad_iou(first: Quad, second: Quad) -> float:
    """Return intersection over union of two convex quads."""
    intersection, _ = cv2.intersectConvexConvex(first.to_array(), second.to_array())
    union = first.area() + second.area() - intersection
    if union <= 0:
        return 0.0
    return float(intersection / union)


def output_size(quad: Quad) -> tuple[int, int]:
    """Return the (width, height) of the flattened page, from its longest opposite edges."""
    corners = quad.to_array()
    top = np.linalg.norm(corners[1] - corners[0])
    bottom = np.linalg.norm(corners[2] - corners[3])
    left = np.linalg.norm(corners[3] - corners[0])
    right = np.linalg.norm(corners[2] - corners[1])
    return max(1, round(max(top, bottom))), max(1, round(max(left, right)))


def warp_perspective(image: np.ndarray, quad: Quad) -> np.ndarray:
    """Cut the page out of ``image`` and flatten it to a rectangle."""
    width, height = output_size(quad)
    target = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(quad.to_array(), target)
    return cv2.warpPerspective(image, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


_CV_ROTATIONS: dict[Rotation, int] = {
    Rotation.CW_90: cv2.ROTATE_90_CLOCKWISE,
    Rotation.CW_180: cv2.ROTATE_180,
    Rotation.CW_270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


def rotate(image: np.ndarray, rotation: Rotation) -> np.ndarray:
    """Rotate ``image`` clockwise by ``rotation``."""
    if rotation is Rotation.NONE:
        return image
    return cv2.rotate(image, _CV_ROTATIONS[rotation])


def scale_factor_for_max_edge(width: int, height: int, max_edge: int | None) -> float:
    """Return the downscale factor so the longer edge fits ``max_edge`` (never upscales)."""
    if not max_edge:
        return 1.0
    return min(1.0, max_edge / max(width, height))


def limit_long_edge(image: np.ndarray, max_edge: int | None) -> np.ndarray:
    """Downscale ``image`` so its longer edge is at most ``max_edge`` pixels."""
    height, width = image.shape[:2]
    factor = scale_factor_for_max_edge(width, height, max_edge)
    if factor >= 1.0:
        return image
    size = (max(1, round(width * factor)), max(1, round(height * factor)))
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA)
