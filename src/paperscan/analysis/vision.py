"""Vision model access: send a prompt plus images, get one JSON object back.

Three backends, all called from this machine:

* the OpenAI API (``OPENAI_API_KEY`` or the key from the settings),
* the local Claude Code CLI (``claude -p``), which reads the image files itself,
* the local Codex CLI (``codex exec --image``).

For the CLI backends the images are written to a temporary folder and their
paths are listed before the prompt, in order.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

try:
    import openai
except ImportError:  # optional extra "openai"; the CLI backends need no package
    openai = None  # type: ignore[assignment]

from paperscan.platform_tools import Tool, find_tool, require_tool, run_tool

log = logging.getLogger(__name__)

#: Timeout for one request, in seconds (CLI agents can be slow to start).
VISION_TIMEOUT = 600.0
OPENAI_KEY_ENV = "OPENAI_API_KEY"
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class AnalysisProvider(StrEnum):
    """Vision backends."""

    NONE = "none"
    OPENAI = "openai"
    CLAUDE_CLI = "claude_cli"
    CODEX_CLI = "codex_cli"


class AnalysisError(RuntimeError):
    """Raised when the backend fails or returns no usable JSON."""


@dataclass(frozen=True)
class ProviderConfig:
    """How to reach the selected backend."""

    provider: AnalysisProvider
    openai_model: str = "gpt-5-mini"
    openai_api_key: str = ""
    claude_path: str = ""
    codex_path: str = ""

    @property
    def enabled(self) -> bool:
        """Return whether a backend is selected."""
        return self.provider is not AnalysisProvider.NONE


def available_providers(config: ProviderConfig) -> list[AnalysisProvider]:
    """Return the backends usable on this computer (CLI found, or OpenAI package plus key)."""
    available = []
    if find_tool(Tool.CLAUDE, config.claude_path):
        available.append(AnalysisProvider.CLAUDE_CLI)
    if find_tool(Tool.CODEX, config.codex_path):
        available.append(AnalysisProvider.CODEX_CLI)
    if openai is not None and (config.openai_api_key or os.environ.get(OPENAI_KEY_ENV)):
        available.append(AnalysisProvider.OPENAI)
    return available


def ask_vision_json(prompt: str, images: Sequence[bytes], config: ProviderConfig) -> dict[str, Any]:
    """Send ``prompt`` with JPEG ``images`` and return the JSON object of the answer."""
    log.info("Asking %s with %d image(s)", config.provider, len(images))
    started = time.perf_counter()
    match config.provider:
        case AnalysisProvider.NONE:
            raise AnalysisError("No AI provider selected.")
        case AnalysisProvider.OPENAI:
            answer = _ask_openai(prompt, images, config)
        case AnalysisProvider.CLAUDE_CLI:
            answer = _ask_claude_cli(prompt, images, config)
        case AnalysisProvider.CODEX_CLI:
            answer = _ask_codex_cli(prompt, images, config)
    log.info("%s answered in %.1f s", config.provider, time.perf_counter() - started)
    return extract_json(answer)


def extract_json(answer: str) -> dict[str, Any]:
    """Return the JSON object in ``answer``; tolerates code fences and surrounding prose."""
    match = _JSON_OBJECT.search(answer)
    if match is None:
        raise AnalysisError(f"No JSON object in the answer: {answer[:200]!r}")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise AnalysisError(f"Invalid JSON in the answer: {exc}") from exc
    if not isinstance(data, dict):
        raise AnalysisError("The answer is not a JSON object.")
    return data


def _ask_openai(prompt: str, images: Sequence[bytes], config: ProviderConfig) -> str:
    """Send images and prompt to the OpenAI Chat Completions API in JSON mode."""
    if openai is None:
        raise AnalysisError("The OpenAI backend needs the 'openai' package: pip install paperscan[openai]")
    api_key = config.openai_api_key or os.environ.get(OPENAI_KEY_ENV, "")
    if not api_key:
        raise AnalysisError(f"No OpenAI API key: set it in the settings or the {OPENAI_KEY_ENV} environment variable.")
    content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
    for jpeg in images:
        url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": url}})
    try:
        response = openai.OpenAI(api_key=api_key, timeout=VISION_TIMEOUT).chat.completions.create(  # type: ignore[call-overload]
            model=config.openai_model,
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
        )
    except openai.OpenAIError as exc:
        raise AnalysisError(f"OpenAI request failed: {exc}") from exc
    return response.choices[0].message.content or ""


def _ask_claude_cli(prompt: str, images: Sequence[bytes], config: ProviderConfig) -> str:
    """Let the local Claude Code CLI read the images and answer in print mode."""
    claude = require_tool(Tool.CLAUDE, config.claude_path)
    with tempfile.TemporaryDirectory(prefix="csocr-ai-") as tmp:
        paths = _write_images(images, Path(tmp))
        command = [str(claude), "-p", "--output-format", "json", "--allowedTools", "Read", "--add-dir", tmp]
        result = _run_backend(command, _with_image_list(prompt, paths))
    try:
        return str(json.loads(result).get("result", ""))
    except json.JSONDecodeError:
        return result


def _ask_codex_cli(prompt: str, images: Sequence[bytes], config: ProviderConfig) -> str:
    """Attach the images to a non-interactive, read-only Codex run."""
    codex = require_tool(Tool.CODEX, config.codex_path)
    with tempfile.TemporaryDirectory(prefix="csocr-ai-") as tmp:
        folder = Path(tmp)
        paths = _write_images(images, folder)
        answer_file = folder / "answer.txt"
        command = [str(codex), "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--cd", tmp]
        command += ["--output-last-message", str(answer_file)]
        for path in paths:
            command += ["--image", str(path)]
        command.append("-")
        _run_backend(command, _with_image_list(prompt, paths))
        return answer_file.read_text(encoding="utf-8") if answer_file.is_file() else ""


def _with_image_list(prompt: str, paths: list[Path]) -> str:
    """Prefix the prompt with the image files in order."""
    listing = "\n".join(f"Image {number}: {path}" for number, path in enumerate(paths, start=1))
    return f"{listing}\n\n{prompt}"


def _write_images(images: Sequence[bytes], folder: Path) -> list[Path]:
    """Write ``image-N.jpg`` files and return their paths."""
    paths = []
    for number, jpeg in enumerate(images, start=1):
        path = folder / f"image-{number}.jpg"
        path.write_bytes(jpeg)
        paths.append(path)
    return paths


def _run_backend(command: list[str], prompt: str) -> str:
    """Run a CLI backend with the prompt on stdin and return its stdout."""
    try:
        return run_tool(command, timeout=VISION_TIMEOUT, input_text=prompt).stdout
    except Exception as exc:
        detail = (getattr(exc, "stderr", "") or "").strip() or str(exc)
        raise AnalysisError(f"{Path(command[0]).stem} failed: {detail[:500]}") from exc
