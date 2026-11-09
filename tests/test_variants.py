"""Filter variants: presets, sweeps, text overrides, overview sheet and AI rating parsing."""

from __future__ import annotations

import numpy as np
import pytest

from paperscan.analysis.variant_rating import rating_from_json
from paperscan.analysis.vision import AnalysisError, extract_json
from paperscan.comparison import overview, render_variants
from paperscan.imaging.filter_variants import (
    MAX_VARIANTS,
    VariantError,
    filter_values,
    overview_sheet,
    preset_variants,
    sweep_variants,
    with_filter_values,
)
from paperscan.imaging.filters import FilterMode, FilterSettings


def test_presets_are_numbered_from_one() -> None:
    variants = preset_variants()
    assert [variant.number for variant in variants] == list(range(1, len(variants) + 1))


def test_sweep_keeps_the_base_and_varies_one_field() -> None:
    base = FilterSettings(mode=FilterMode.BLACK_WHITE)
    variants = sweep_variants(base, "bw_threshold", ["120", "160"])
    assert [variant.settings.bw_threshold for variant in variants] == [120, 160]
    assert all(variant.settings.mode is FilterMode.BLACK_WHITE for variant in variants)


def test_sweep_is_limited() -> None:
    with pytest.raises(VariantError):
        sweep_variants(FilterSettings(), "bw_threshold", [str(value) for value in range(MAX_VARIANTS + 1)])


@pytest.mark.parametrize(
    ("values", "check"),
    [
        ({"mode": "clear"}, lambda s: s.mode is FilterMode.CLEAR),
        ({"grayscale": "yes"}, lambda s: s.grayscale),
        ({"hard_contrast": "0.5"}, lambda s: s.hard_contrast == 0.5),
        ({"black_point": "auto"}, lambda s: s.black_point is None),
        ({"black_point": "12"}, lambda s: s.black_point == 12),
    ],
)
def test_text_values_are_converted(values: dict[str, str], check: object) -> None:
    assert check(with_filter_values(FilterSettings(black_point=5), values))  # type: ignore[operator]


@pytest.mark.parametrize("values", [{"nope": "1"}, {"bw_threshold": "dark"}, {"mode": "sepia"}])
def test_invalid_text_values_are_rejected(values: dict[str, str]) -> None:
    with pytest.raises(VariantError):
        with_filter_values(FilterSettings(), values)


def test_text_values_reproduce_every_preset() -> None:
    for variant in preset_variants():
        assert with_filter_values(FilterSettings(), filter_values(variant.settings)) == variant.settings


def test_overview_has_one_tile_per_variant(document_photo: np.ndarray) -> None:
    renders = render_variants(document_photo[200:1000, 200:700], preset_variants()[:3])
    sheet = overview(renders)
    assert sheet.shape[1] > sheet.shape[0]  # three tiles side by side
    assert overview_sheet([render.rgb for render in renders], [1, 2, 3]).shape == sheet.shape


def test_rating_is_validated() -> None:
    rating = rating_from_json(extract_json('Sure! {"best": 2, "scores": {"1": 6, "2": 9}, "reason": "cleanest"}'), 3)
    assert (rating.best, rating.scores, rating.reason) == (2, {1: 6.0, 2: 9.0}, "cleanest")
    with pytest.raises(AnalysisError):
        rating_from_json({"best": 7}, 3)
