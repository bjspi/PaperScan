"""Install an optional page corner model into the user's data directory.

No model is shipped with this project. A compatible TFLite model takes a
224x224 RGB image and returns four normalised corners plus per-corner
confidences; :mod:`paperscan.imaging.border_detection` describes the tensors.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from paperscan.app_paths import make_reproducible_dir

TFLITE_SUFFIX = ".tflite"


class ModelImportError(RuntimeError):
    """Raised when the source is not a TFLite model file."""


def import_model(source: Path, target: Path) -> Path:
    """Copy a ``.tflite`` model to ``target`` and return ``target``."""
    if source.suffix.lower() != TFLITE_SUFFIX:
        raise ModelImportError(f"Expected a {TFLITE_SUFFIX} file, got {source.name}")
    make_reproducible_dir(target.parent)
    shutil.copyfile(source, target)
    return target
