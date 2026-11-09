from __future__ import annotations

import numpy as np
import pytest

from paperscan.imaging.border_detection import DetectionSource, detect_by_contour, detect_page
from paperscan.imaging.filters import (
    FilterMode,
    FilterSettings,
    apply_filter,
    block_edge,
    cosine_curve,
    enhance_photo,
)
from paperscan.imaging.geometry import (
    Point,
    Quad,
    Rotation,
    limit_long_edge,
    order_corners,
    output_size,
    quad_iou,
    rotate,
    warp_perspective,
)


def test_order_corners_sorts_any_order() -> None:
    shuffled = np.array([[100, 100], [0, 0], [0, 100], [100, 0]], np.float32)
    assert order_corners(shuffled).tolist() == [[0, 0], [100, 0], [100, 100], [0, 100]]


def test_quad_iou_identical_and_disjoint() -> None:
    square = Quad.full_image(100, 100)
    far = Quad(Point(200, 200), Point(300, 200), Point(300, 300), Point(200, 300))
    assert quad_iou(square, square) == pytest.approx(1.0)
    assert quad_iou(square, far) == 0.0


def test_warp_uses_longest_edges() -> None:
    quad = Quad(Point(0, 0), Point(200, 10), Point(190, 300), Point(5, 290))
    width, height = output_size(quad)
    assert warp_perspective(np.zeros((400, 400, 3), np.uint8), quad).shape == (height, width, 3)


@pytest.mark.parametrize(("max_edge", "expected"), [(None, (300, 200)), (150, (150, 100)), (1000, (300, 200))])
def test_limit_long_edge_never_upscales(max_edge: int | None, expected: tuple[int, int]) -> None:
    assert limit_long_edge(np.zeros((300, 200, 3), np.uint8), max_edge).shape[:2] == expected


def test_rotation_turns() -> None:
    image = np.zeros((10, 20, 3), np.uint8)
    assert rotate(image, Rotation.CW_90).shape[:2] == (20, 10)
    assert Rotation.CW_270.turned_clockwise() is Rotation.NONE
    assert Rotation.NONE.turned_counterclockwise() is Rotation.CW_270


def test_contour_detection_finds_the_page(document_photo: np.ndarray) -> None:
    quad = detect_by_contour(document_photo)
    assert quad is not None
    assert quad.top_left.x == pytest.approx(150, abs=15)
    assert quad.bottom_right.y == pytest.approx(1080, abs=15)


def test_detect_page_without_model_uses_contour(document_photo: np.ndarray) -> None:
    assert detect_page(document_photo, None).source is DetectionSource.CONTOUR


def test_detect_page_falls_back_to_full_image() -> None:
    blank = np.full((300, 200, 3), 128, np.uint8)
    detection = detect_page(blank, None)
    assert detection.source is DetectionSource.FULL_IMAGE
    assert detection.quad == Quad.full_image(200, 300)


def test_cosine_curve_endpoints() -> None:
    curve = cosine_curve(255, 0.8)
    assert curve[0] == 0
    assert curve[-1] == 255
    assert np.all(np.diff(curve) >= 0)


def test_block_edge_has_a_minimum() -> None:
    assert block_edge(100, 100) == 15
    assert block_edge(4000, 3000) == int((4000 * 3000 // 8000) ** 0.5) + 1


@pytest.mark.parametrize("mode", [FilterMode.DOCUMENT, FilterMode.CLEAR])
def test_document_filters_flatten_shading(document_photo: np.ndarray, mode: FilterMode) -> None:
    page = document_photo[200:1000, 200:700]
    result = apply_filter(page, FilterSettings(mode=mode))
    paper = result[result.mean(axis=2) > 128]
    assert paper.mean() > 245  # the shaded paper became white everywhere
    assert result.min() < 80  # ink stays dark


def test_black_white_is_binary(document_photo: np.ndarray) -> None:
    result = apply_filter(document_photo[200:1000, 200:700], FilterSettings(mode=FilterMode.BLACK_WHITE))
    assert set(np.unique(result)) <= {0, 255}


def test_grayscale_keeps_three_channels(document_photo: np.ndarray) -> None:
    result = apply_filter(document_photo, FilterSettings(mode=FilterMode.ORIGINAL, grayscale=True))
    assert result.shape == document_photo.shape
    assert np.array_equal(result[..., 0], result[..., 1])


def test_photo_filter_stretches_levels() -> None:
    ramp = np.tile(np.arange(50, 200, dtype=np.uint8), (10, 1))[..., None].repeat(3, axis=2)
    result = enhance_photo(ramp, black_point=50, white_point=199)
    assert result.min() == 0
    assert result.max() == 255
