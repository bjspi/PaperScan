"""Let a vision model pick the best filter variant of a page."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from paperscan.analysis.vision import AnalysisError, ProviderConfig, ask_vision_json

RATING_PROMPT = """\
The {count} images are the same scanned document page, each processed with a different filter.
Image N is variant N.
Pick the version that is best for a long-term archive PDF: clean white paper, crisp and dark text,
no lost information (light print, stamps, signatures, colour marks), no blotches, streaks or noise.
Answer with ONE JSON object and nothing else:
{{"best": <variant number>, "scores": {{"1": <0-10>, "2": <0-10>, ...}}, "reason": "<one sentence>"}}"""


@dataclass(frozen=True)
class VariantRating:
    """The model's choice and per-variant scores (0..10)."""

    best: int
    reason: str
    scores: dict[int, float] = field(default_factory=dict)


def rate_variants(images: Sequence[bytes], config: ProviderConfig) -> VariantRating:
    """Ask the vision model which of ``images`` (variant 1..N) looks best."""
    data = ask_vision_json(RATING_PROMPT.format(count=len(images)), images, config)
    return rating_from_json(data, len(images))


def rating_from_json(data: dict[str, object], count: int) -> VariantRating:
    """Validate the model's answer; raises :class:`AnalysisError` if ``best`` is out of range."""
    try:
        best = int(str(data.get("best")))
    except ValueError as exc:
        raise AnalysisError(f"The model named no valid variant: {data.get('best')!r}") from exc
    if not 1 <= best <= count:
        raise AnalysisError(f"The model chose variant {best}, but only 1..{count} exist.")
    raw_scores = data.get("scores")
    scores = {int(key): float(value) for key, value in raw_scores.items() if str(key).isdigit()} if isinstance(raw_scores, dict) else {}
    return VariantRating(best=best, reason=str(data.get("reason") or "").strip(), scores=scores)
