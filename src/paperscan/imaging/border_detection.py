"""Automatic page border detection.

Two detectors are combined:

* an optional neural corner regressor (a 224x224 TFLite model, run with
  ``ai-edge-litert``), which is robust on cluttered backgrounds, and
* a classic OpenCV contour search, which is pixel-precise when it finds the page.

If the model is confident and both outlines agree closely, the precise contour
wins; otherwise the model's outline is used, then the contour, then the full
image. No model is shipped with this project; a compatible model can be added
with ``paperscan model import``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

try:
    from ai_edge_litert.interpreter import Interpreter
except ImportError:  # optional extra "ml": without it only the OpenCV detector runs
    Interpreter = None

from paperscan.imaging.geometry import CORNER_COUNT, Quad, limit_long_edge, order_corners, quad_iou

log = logging.getLogger(__name__)

#: Square input edge of the corner model.
MODEL_INPUT_EDGE = 224
#: Mean corner confidence above which the model's outline is trusted (app value).
MODEL_MIN_CONFIDENCE = 0.5
#: Overlap above which the precise contour replaces the model outline (app value).
CONTOUR_AGREEMENT_IOU = 0.96
#: Output tensor holding normalised (y, x) corners, shape (1, 1, 4, 2).
MODEL_CORNERS_OUTPUT = "StatefulPartitionedCall:1"
#: Output tensor holding the per-corner confidence, shape (1, 1, 4).
MODEL_SCORES_OUTPUT = "StatefulPartitionedCall:0"

#: Working size of the contour search.
CONTOUR_WORK_EDGE = 640
#: A contour must cover at least this share of the image to count as a page.
CONTOUR_MIN_AREA_SHARE = 0.2
#: Polygon approximation tolerance relative to the contour perimeter.
CONTOUR_APPROX_EPSILON = 0.02
CANNY_LOW_THRESHOLD = 30
CANNY_HIGH_THRESHOLD = 120


class DetectionSource(StrEnum):
    """Which detector produced an outline."""

    MODEL = "model"
    CONTOUR = "contour"
    MODEL_AND_CONTOUR = "model+contour"
    FULL_IMAGE = "full-image"


@dataclass(frozen=True)
class Detection:
    """Result of a border detection."""

    quad: Quad
    source: DetectionSource
    confidence: float


def detect_page(rgb: np.ndarray, model_path: Path | None) -> Detection:
    """Detect the page outline in ``rgb`` (source pixel coordinates)."""
    height, width = rgb.shape[:2]
    contour = detect_by_contour(rgb)
    model = detect_by_model(rgb, model_path) if model_path else None
    if model and model.confidence >= MODEL_MIN_CONFIDENCE:
        if contour and quad_iou(model.quad, contour) >= CONTOUR_AGREEMENT_IOU:
            return Detection(contour, DetectionSource.MODEL_AND_CONTOUR, model.confidence)
        return model
    if contour:
        return Detection(contour, DetectionSource.CONTOUR, 1.0)
    return Detection(Quad.full_image(width, height), DetectionSource.FULL_IMAGE, 0.0)


# --- Neural model -------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _load_interpreter(model_path: Path) -> object:
    """Load and cache the TFLite interpreter."""
    interpreter = Interpreter(model_path=str(model_path))
    interpreter.allocate_tensors()
    return interpreter


def model_available(model_path: Path | None) -> bool:
    """Return whether the model file exists and the TFLite runtime is installed."""
    return Interpreter is not None and model_path is not None and model_path.is_file()


def detect_by_model(rgb: np.ndarray, model_path: Path) -> Detection | None:
    """Run the corner model; returns None if the model or runtime is unavailable."""
    if not model_available(model_path):
        return None
    try:
        interpreter = _load_interpreter(model_path)
        corners, confidence = _run_model(interpreter, rgb)
    except (RuntimeError, ValueError, OSError) as exc:
        log.warning("Border model failed: %s", exc)
        return None
    height, width = rgb.shape[:2]
    quad = Quad.from_array(corners * np.array([width, height], dtype=np.float32)).clamped(width, height)
    return Detection(quad, DetectionSource.MODEL, confidence)


def _run_model(interpreter: object, rgb: np.ndarray) -> tuple[np.ndarray, float]:
    """Feed the 224x224 RGB image (raw 0..255 floats) and read normalised corners and confidence."""
    resized = cv2.resize(rgb, (MODEL_INPUT_EDGE, MODEL_INPUT_EDGE), interpolation=cv2.INTER_LINEAR)
    batch = resized.astype(np.float32)[None, ...]
    interpreter.set_tensor(interpreter.get_input_details()[0]["index"], batch)  # type: ignore[attr-defined]
    interpreter.invoke()  # type: ignore[attr-defined]
    outputs = {detail["name"]: detail["index"] for detail in interpreter.get_output_details()}  # type: ignore[attr-defined]
    y_x = interpreter.get_tensor(outputs[MODEL_CORNERS_OUTPUT]).reshape(CORNER_COUNT, 2)  # type: ignore[attr-defined]
    scores = interpreter.get_tensor(outputs[MODEL_SCORES_OUTPUT]).reshape(CORNER_COUNT)  # type: ignore[attr-defined]
    return y_x[:, ::-1].copy(), float(scores.mean())


# --- Classic contour search ---------------------------------------------------------------------


def detect_by_contour(rgb: np.ndarray) -> Quad | None:
    """Find the largest four-cornered contour; returns None if nothing page-like is found."""
    small = limit_long_edge(rgb, CONTOUR_WORK_EDGE)
    scale = rgb.shape[1] / small.shape[1]
    edges = _edge_image(small)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    min_area = CONTOUR_MIN_AREA_SHARE * small.shape[0] * small.shape[1]
    best = _largest_quadrilateral(contours, min_area)
    if best is None:
        return None
    return Quad.from_array(order_corners(best) * scale)


def _edge_image(rgb: np.ndarray) -> np.ndarray:
    """Return a closed edge map suited for page contour search."""
    gray = cv2.GaussianBlur(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    edges = cv2.Canny(gray, CANNY_LOW_THRESHOLD, CANNY_HIGH_THRESHOLD)
    return cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)


def _largest_quadrilateral(contours: Sequence[np.ndarray], min_area: float) -> np.ndarray | None:
    """Return the largest convex four-point approximation with at least ``min_area``."""
    best: np.ndarray | None = None
    best_area = min_area
    for contour in contours:
        hull = cv2.convexHull(contour)
        approx = cv2.approxPolyDP(hull, CONTOUR_APPROX_EPSILON * cv2.arcLength(hull, True), True)
        area = cv2.contourArea(approx)
        if len(approx) == CORNER_COUNT and area > best_area:
            best, best_area = approx.reshape(CORNER_COUNT, 2).astype(np.float32), area
    return best
