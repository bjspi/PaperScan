"""Command line interface; without a command it opens the GUI.

Every pipeline feature is scriptable: pull photos from the phone, turn photos
into one PDF, import the border model and inspect the setup.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, NoReturn

import rich_click as click
from rich.logging import RichHandler
from rich.table import Table

from paperscan import __version__
from paperscan.analysis.variant_rating import VariantRating
from paperscan.analysis.vision import AnalysisError, AnalysisProvider
from paperscan.app_logging import setup_logging
from paperscan.app_paths import CONFIG_FILE, LOG_FILE, MODEL_PATH
from paperscan.capture.adb_import import AdbClient, AdbError, RemotePhoto
from paperscan.capture.photo_files import photos_in_folder
from paperscan.cli import output
from paperscan.comparison import OVERVIEW_FILE, VariantRender, rate_renders, render_page_variants, write_renders
from paperscan.export.jpeg_encoding import jpegli_available
from paperscan.gui.app import run_gui
from paperscan.imaging.border_detection import model_available
from paperscan.imaging.filter_variants import (
    FilterVariant,
    VariantError,
    filter_values,
    preset_variants,
    sweep_variants,
    with_filter_values,
)
from paperscan.imaging.filters import FilterMode, FilterSettings
from paperscan.imaging.model_import import ModelImportError, import_model
from paperscan.pipeline import (
    ExportCancelledError,
    ExportChoice,
    choice_for,
    open_page,
    prepare_export,
    suggested_choice,
    write_export,
)
from paperscan.platform_tools import Tool, ToolMissingError, find_tool
from paperscan.settings import AppSettings, SettingsStore, to_jsonable, with_setting
from paperscan.tool_setup import INSTALLABLE_TOOLS, SetupError, SetupMethod, install, install_tesseract_languages, setup_plan

PROGRAM = "paperscan"
EXIT_FAILED = 1

click.rich_click.TEXT_MARKUP = "rich"
click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.HEADER_TEXT = output.banner_text()
click.rich_click.FOOTER_TEXT = "Every command takes --help. Settings: ~/.PaperScan/config.json (shared with the GUI)."
click.rich_click.COMMAND_GROUPS = {
    PROGRAM: [
        {"name": "Documents", "commands": ["scan", "compare", "phone"]},
        {"name": "Application", "commands": ["gui", "setup", "doctor", "model", "config"]},
    ]
}

CONTEXT = {"help_option_names": ["-h", "--help"]}
PHONE_HINT = "Check the connection with: paperscan phone connect"


def _fail(message: str, hint: str = "") -> NoReturn:
    """Print an error and exit with :data:`EXIT_FAILED`."""
    output.error(message, hint)
    sys.exit(EXIT_FAILED)


def _configure_logging(verbosity: int) -> None:
    """Console: warnings by default, ``-v`` info, ``-vv`` debug; the log file always gets INFO and above."""
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
    setup_logging(RichHandler(console=output.stderr, show_path=False, markup=False), level)


@click.group(invoke_without_command=True, context_settings=CONTEXT)
@click.option("-v", "--verbose", count=True, help="More log output on stderr (-v info, -vv debug).")
@click.version_option(__version__, "-V", "--version", prog_name="PaperScan")
@click.pass_context
def cli(ctx: click.Context, verbose: int) -> None:
    """Turn phone photos of documents into clean, searchable, well-named PDFs.

    Without a command the graphical app opens.
    """
    _configure_logging(verbose)
    ctx.obj = SettingsStore()
    if ctx.invoked_subcommand is None:
        _start_gui()


@cli.command(context_settings=CONTEXT)
def gui() -> None:
    """Open the graphical app."""
    _start_gui()


def _start_gui() -> None:
    """Run the GUI and exit with its code."""
    sys.exit(run_gui())


# --- scan ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ScanOverrides:
    """Command line options that override the saved settings for one run (never saved)."""

    filter_mode: str | None = None
    grayscale: bool | None = None
    max_edge: int | None = None
    quality: int | None = None
    ocr: bool | None = None
    ai_provider: str | None = None
    #: Preset number from ``compare`` (applied before the other filter options).
    variant: int | None = None
    #: ``field=value`` filter assignments from ``--set`` (applied last).
    filter_values: tuple[str, ...] = ()

    def apply(self, settings: AppSettings) -> AppSettings:
        """Return ``settings`` with every given option applied."""
        page_filter = settings.default_filter
        if self.variant is not None:
            page_filter = _preset(self.variant).settings
        if self.filter_mode:
            page_filter = page_filter.with_mode(FilterMode(self.filter_mode))
        if self.grayscale is not None:
            page_filter = replace(page_filter, grayscale=self.grayscale)
        page_filter = with_filter_values(page_filter, _parse_assignments(self.filter_values))
        settings = with_setting(settings, "default_filter", page_filter)
        if self.max_edge is not None:
            settings = with_setting(settings, "max_edge", self.max_edge)
        if self.quality is not None:
            settings = with_setting(settings, "jpeg.quality", self.quality)
        if self.ocr is not None:
            settings = with_setting(settings, "ocr.enabled", self.ocr)
        if self.ai_provider:
            settings = with_setting(settings, "ai.provider.provider", AnalysisProvider(self.ai_provider))
        return settings


def _preset(number: int) -> FilterVariant:
    """Return preset ``number`` or fail with the valid range."""
    presets = preset_variants()
    if not 1 <= number <= len(presets):
        _fail(f"There is no preset variant {number}.", f"Valid: 1..{len(presets)} (see 'paperscan compare').")
    return presets[number - 1]


def _parse_assignments(assignments: tuple[str, ...]) -> dict[str, str]:
    """Parse ``field=value`` pairs."""
    values: dict[str, str] = {}
    for assignment in assignments:
        field, separator, value = assignment.partition("=")
        if not separator:
            raise click.BadParameter(f"'{assignment}' is not FIELD=VALUE", param_hint="--set")
        values[field.strip()] = value
    return values


def _scan_arguments(settings: FilterSettings) -> list[str]:
    """Return the ``--set`` arguments that reproduce ``settings``."""
    arguments: list[str] = []
    for field, value in filter_values(settings).items():
        arguments += ["--set", f"{field}={value}"]
    return arguments


@dataclass(frozen=True)
class ScanResult:
    """One written PDF."""

    pdf: Path
    pages: int
    choice: ExportChoice


@cli.command(context_settings=CONTEXT)
@click.argument("photos", nargs=-1, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option_panel("Sources", options=["--folder", "--phone", "--each", "--delete-from-phone", "--yes"])
@click.option(
    "--folder", "folders", multiple=True, type=click.Path(exists=True, file_okay=False, path_type=Path), help="Add every photo of a folder."
)
@click.option("--phone", "phone_count", type=int, default=0, help="Add the newest N photos from the phone.")
@click.option("--each", is_flag=True, help="One PDF per photo instead of one document.")
@click.option("--delete-from-phone", is_flag=True, help="After all PDFs are written, delete the --phone photos on the phone (asks first).")
@click.option("--yes", "-y", "assume_yes", is_flag=True, help="Do not ask before deleting from the phone.")
@click.option_panel(
    "Processing", options=["--filter", "--gray", "--variant", "--set", "--auto-filter", "--max-edge", "--quality", "--ocr", "--ai"]
)
@click.option("--filter", "filter_mode", type=click.Choice([mode.value for mode in FilterMode]), help="Filter for all pages.")
@click.option("--gray/--color", "grayscale", default=None, help="Gray or colour output.")
@click.option("--variant", type=int, help="Use preset variant N (numbers as shown by 'compare').")
@click.option(
    "--set", "filter_values", multiple=True, metavar="FIELD=VALUE", help="Set a filter parameter, e.g. bw_threshold=140. Repeatable."
)
@click.option("--auto-filter", is_flag=True, help="Let the AI pick the best preset on the first page and use it for all pages.")
@click.option("--max-edge", type=int, help="Longest page edge in pixels (0 = full resolution).")
@click.option("--quality", type=click.IntRange(1, 100), help="JPEG quality.")
@click.option("--ocr/--no-ocr", default=None, help="Add a searchable text layer.")
@click.option(
    "--ai",
    "ai_provider",
    type=click.Choice([provider.value for provider in AnalysisProvider]),
    help="AI backend for title, date and --auto-filter.",
)
@click.option_panel("Output", options=["--title", "--date", "--output-dir", "--json"])
@click.option("--title", help="Document title (skips the AI title).")
@click.option("--date", "document_date", type=click.DateTime(["%Y-%m-%d"]), help="Document date YYYY-MM-DD.")
@click.option("-o", "--output-dir", type=click.Path(file_okay=False, path_type=Path), help="Target folder.")
@click.option("--json", "as_json", is_flag=True, help="Print the result as JSON.")
@click.pass_obj
def scan(store: SettingsStore, photos: tuple[Path, ...], **options: Any) -> None:  # noqa: ANN401 - click passes typed options
    """Turn photos into one searchable PDF (or one PDF per photo with --each).

    Sources can be combined: PHOTOS in the given order, then --folder photos by capture time, then --phone photos.

    [dim]Example:[/] paperscan scan invoice.jpg

    [dim]Example:[/] paperscan scan p1.jpg p2.jpg p3.jpg --filter clear --ai claude_cli

    [dim]Example:[/] paperscan scan --folder ~/Scans/heating-bill --gray

    [dim]Example:[/] paperscan scan --phone 5 --each --json

    [dim]Example:[/] paperscan scan p1.jpg p2.jpg --set mode=black_white --set bw_threshold=140
    """
    output.print_banner()
    override_names = ("filter_mode", "grayscale", "max_edge", "quality", "ocr", "ai_provider", "variant", "filter_values")
    try:
        settings = ScanOverrides(**{name: options[name] for name in override_names}).apply(store.load())
    except VariantError as exc:
        raise click.BadParameter(str(exc), param_hint="--set") from exc
    sources, phone_photos = _collect_sources(settings, photos, options["folders"], options["phone_count"])
    if options["auto_filter"]:
        settings = _auto_filter(settings, sources[0])
    documents = [[path] for path in sources] if options["each"] else [sources]
    output_dir = options["output_dir"] or Path(settings.output_dir).expanduser()
    document_date = options["document_date"].date() if options["document_date"] else None
    results = [_scan_document(paths, settings, options["title"], document_date, output_dir) for paths in documents]
    if options["delete_from_phone"] and phone_photos:
        _delete_from_phone(settings, phone_photos, assume_yes=options["assume_yes"])
    if options["as_json"]:
        output.print_json(results)
        return
    for result in results:
        output.success(f"{result.pages} page(s) → [accent]{result.pdf}[/accent]")


def _auto_filter(settings: AppSettings, first_photo: Path) -> AppSettings:
    """Let the AI choose the best preset on the first page and make it the default filter."""
    if not settings.ai.provider.enabled:
        _fail("--auto-filter needs an AI provider.", "Pass --ai claude_cli|codex_cli|openai or set one in the settings.")
    try:
        with output.stderr.status("[accent]Comparing filter variants …[/accent]"):
            renders = render_page_variants(open_page(first_photo, settings), preset_variants())
            rating = rate_renders(renders, settings)
    except (AnalysisError, ToolMissingError, OSError) as exc:
        _fail(str(exc))
    chosen = renders[rating.best - 1].variant
    output.success(f"AI chose variant {chosen.number} ({chosen.name}): {rating.reason}")
    return with_setting(settings, "default_filter", chosen.settings)


def _collect_sources(
    settings: AppSettings, photos: tuple[Path, ...], folders: tuple[Path, ...], phone_count: int
) -> tuple[list[Path], list[RemotePhoto]]:
    """Return all photos in page order plus the phone photos among them; fails if there are none."""
    sources = list(photos)
    for folder in folders:
        sources.extend(photos_in_folder(folder))
    pulled = _pull_from_phone(settings, phone_count) if phone_count else []
    sources.extend(path for path, _ in pulled)
    if not sources:
        _fail("No photos given.", "Pass photo files, --folder DIR or --phone N.")
    return sources, [photo for _, photo in pulled]


def _delete_from_phone(settings: AppSettings, photos: list[RemotePhoto], *, assume_yes: bool) -> None:
    """Delete the imported photos on the phone after the PDFs were written."""
    names = ", ".join(photo.name for photo in photos)
    question = f"Permanently delete {len(photos)} photo(s) from the phone ({names})? They do not go to the gallery's trash."
    if not assume_yes and not click.confirm(question, default=False, err=True):
        output.warning("Photos kept on the phone.")
        return
    client = _adb(settings)
    try:
        for photo in photos:
            client.delete(photo)
    except AdbError as exc:
        _fail(f"Deleting from the phone failed: {exc}")
    output.success(f"Deleted {len(photos)} photo(s) from the phone (local copies kept).")


def _scan_document(paths: list[Path], settings: AppSettings, title: str | None, document_date: date | None, output_dir: Path) -> ScanResult:
    """Process ``paths`` as one document and write its PDF."""
    try:
        with output.stderr.status("[accent]Detecting pages …[/accent]"):
            pages = [open_page(path, settings) for path in paths]
        with output.stderr.status("[accent]Processing …[/accent]") as status:
            draft = prepare_export(pages, settings, progress=lambda message, _fraction: status.update(f"[accent]{message} …[/accent]"))
    except (ToolMissingError, AnalysisError, ExportCancelledError, OSError) as exc:
        _fail(str(exc))
    suggestion = suggested_choice(draft, settings.file_date_style)
    choice = choice_for(
        title=title or suggestion.title,
        document_date=document_date or suggestion.document_date,
        style=settings.file_date_style,
        summary=suggestion.summary,
        keywords=suggestion.keywords,
    )
    return ScanResult(pdf=write_export(draft, choice, output_dir), pages=len(pages), choice=choice)


# --- compare -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class VariantReport:
    """One rendered variant as reported to scripts and agents."""

    number: int
    name: str
    file: Path
    scan_arguments: list[str]


@dataclass(frozen=True)
class CompareReport:
    """Result of ``compare``."""

    folder: Path
    overview: Path
    variants: list[VariantReport]
    rating: VariantRating | None


@cli.command(context_settings=CONTEXT)
@click.argument("photo", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--vary", metavar="FIELD=V1,V2,...", help="Sweep one filter parameter instead of the presets, e.g. bw_threshold=120,140,160.")
@click.option("--set", "filter_values", multiple=True, metavar="FIELD=VALUE", help="Base filter parameter for --vary. Repeatable.")
@click.option("--rate/--no-rate", default=False, help="Let the AI rate the variants and pick the best.")
@click.option("--ai", "ai_provider", type=click.Choice([provider.value for provider in AnalysisProvider]), help="AI backend for --rate.")
@click.option("-o", "--output-dir", type=click.Path(file_okay=False, path_type=Path), help="Folder for the variant images.")
@click.option("--json", "as_json", is_flag=True, help="Print the result as JSON.")
@click.pass_obj
def compare(store: SettingsStore, photo: Path, **options: Any) -> None:  # noqa: ANN401 - click passes typed options
    """Render filter variants of one photo side by side (for people and agents).

    Writes variant-NN.jpg files plus overview.jpg. Each variant lists the exact
    'scan' arguments that reproduce it, so a script or agent can look at the
    images, decide, and run the batch.

    [dim]Example:[/] paperscan compare page1.jpg --rate --ai claude_cli

    [dim]Example:[/] paperscan compare page1.jpg --set mode=black_white --vary bw_threshold=120,140,160,180 --json
    """
    output.print_banner()
    settings = ScanOverrides(ai_provider=options["ai_provider"]).apply(store.load())
    try:
        base = with_filter_values(settings.default_filter, _parse_assignments(options["filter_values"]))
        variants = _sweep(base, options["vary"]) if options["vary"] else preset_variants()
    except VariantError as exc:
        raise click.BadParameter(str(exc)) from exc
    folder = options["output_dir"] or Path(settings.output_dir).expanduser() / f"compare-{photo.stem}"
    try:
        with output.stderr.status("[accent]Rendering variants …[/accent]"):
            renders = render_page_variants(open_page(photo, settings), variants)
            files = write_renders(renders, folder)
        rating = _rate(renders, settings) if options["rate"] else None
    except (AnalysisError, ToolMissingError, OSError) as exc:
        _fail(str(exc))
    reports = [
        VariantReport(variant.number, variant.name, files[variant.number], _scan_arguments(variant.settings)) for variant in variants
    ]
    _report_compare(CompareReport(folder, folder / OVERVIEW_FILE, reports, rating), options["as_json"])


def _sweep(base: FilterSettings, vary: str) -> list[FilterVariant]:
    """Build sweep variants from ``FIELD=V1,V2,...``."""
    field, separator, values = vary.partition("=")
    if not separator or not values:
        raise VariantError(f"'{vary}' is not FIELD=V1,V2,...")
    return sweep_variants(base, field.strip(), [value for value in values.split(",") if value.strip()])


def _rate(renders: list[VariantRender], settings: AppSettings) -> VariantRating:
    """Rate renders with the configured AI."""
    if not settings.ai.provider.enabled:
        _fail("--rate needs an AI provider.", "Pass --ai claude_cli|codex_cli|openai or set one in the settings.")
    with output.stderr.status("[accent]AI is rating the variants …[/accent]"):
        return rate_renders(renders, settings)


def _report_compare(report: CompareReport, as_json: bool) -> None:
    """Print the comparison as a table or JSON."""
    if as_json:
        output.print_json(report)
        return
    table = Table("#", "Variant", "Score", "scan arguments")
    scores = report.rating.scores if report.rating else {}
    for variant in report.variants:
        marker = " *" if report.rating and report.rating.best == variant.number else ""
        score = f"{scores[variant.number]:.0f}" if variant.number in scores else ""
        table.add_row(f"{variant.number}{marker}", variant.name, score, " ".join(variant.scan_arguments))
    output.stdout.print(table)
    if report.rating:
        output.success(f"Best: variant {report.rating.best} - {report.rating.reason}")
    output.success(f"Overview: [accent]{report.overview}[/accent]")


# --- phone ---------------------------------------------------------------------------------------


@cli.group(context_settings=CONTEXT)
def phone() -> None:
    """Find the phone (USB or Wi-Fi), list and pull camera photos over ADB."""


@phone.command("list", context_settings=CONTEXT)
@click.option("-n", "--count", type=int, default=10, show_default=True, help="How many of the newest photos.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
@click.pass_obj
def phone_list(store: SettingsStore, count: int, as_json: bool) -> None:
    """Show the newest photos on the phone."""
    settings = store.load()
    try:
        photos = _adb(settings).list_photos(settings.phone.camera_dir, count)
    except AdbError as exc:
        _fail(str(exc), PHONE_HINT)
    if as_json:
        output.print_json(photos)
        return
    table = Table("Taken", "File", "Size")
    for photo in photos:
        table.add_row(f"{photo.modified:%Y-%m-%d %H:%M}", photo.name, f"{photo.size / 1_048_576:.1f} MB")
    output.stdout.print(table)


@phone.command("pull", context_settings=CONTEXT)
@click.option("-n", "--count", type=int, default=1, show_default=True, help="How many of the newest photos.")
@click.option("-d", "--dest", type=click.Path(file_okay=False, path_type=Path), help="Target folder (default: import folder).")
@click.pass_obj
def phone_pull(store: SettingsStore, count: int, dest: Path | None) -> None:
    """Copy the newest photos from the phone (they stay on the phone)."""
    for path, _photo in _pull_from_phone(store.load(), count, dest):
        output.stdout.print(str(path))


@phone.command("connect", context_settings=CONTEXT)
@click.pass_obj
def phone_connect(store: SettingsStore) -> None:
    """Find the phone: USB first, then Wireless debugging (if the Wi-Fi fallback is on)."""
    try:
        device = _adb(store.load()).connect()
    except AdbError as exc:
        _fail(str(exc))
    output.success(f"Phone ready: {device.label} · serial {device.serial}")


@phone.command("pair", context_settings=CONTEXT)
@click.argument("address")
@click.argument("code")
@click.pass_obj
def phone_pair(store: SettingsStore, address: str, code: str) -> None:
    """Pair with a phone over Wi-Fi (one-time step).

    On the phone open Developer options → Wireless debugging → Pair device with pairing code,
    then pass the IP address:port and the six-digit code shown there.

    [dim]Example:[/] paperscan phone pair 192.168.1.20:37011 482913
    """
    client = _adb(store.load())
    try:
        client.pair(address, code)
        device = client.connect()
    except AdbError as exc:
        _fail(str(exc))
    output.success(f"Paired. Phone ready: {device.label}")


def _adb(settings: AppSettings) -> AdbClient:
    """Return an adb client for the configured phone."""
    try:
        return settings.phone.adb_client()
    except ToolMissingError as exc:
        _fail(str(exc))


def _pull_from_phone(settings: AppSettings, count: int, dest: Path | None = None) -> list[tuple[Path, RemotePhoto]]:
    """Pull the newest ``count`` photos and return (local path, phone photo) oldest first (page order)."""
    client = _adb(settings)
    target = dest or Path(settings.import_dir).expanduser()
    try:
        photos = client.list_photos(settings.phone.camera_dir, count)
        return [(client.pull(photo, target), photo) for photo in reversed(photos)]
    except AdbError as exc:
        _fail(str(exc), PHONE_HINT)


# --- model, config, doctor -----------------------------------------------------------------------


@cli.group(context_settings=CONTEXT)
def model() -> None:
    """Manage the optional neural border detection model."""


@model.command("import", context_settings=CONTEXT)
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_obj
def model_import(store: SettingsStore, source: Path) -> None:
    """Import an optional page corner model (.tflite).

    [dim]Example:[/] paperscan model import page_corners.tflite
    """
    settings = store.load()
    target = settings.model_path or MODEL_PATH
    try:
        import_model(source, target)
    except (ModelImportError, OSError) as exc:
        _fail(str(exc))
    output.success(f"Model installed: {target}")


@cli.group(context_settings=CONTEXT)
def config() -> None:
    """Show the settings shared by GUI and CLI."""


@config.command("show", context_settings=CONTEXT)
@click.pass_obj
def config_show(store: SettingsStore) -> None:
    """Print the current settings as JSON (API keys masked)."""
    data = to_jsonable(store.load())
    key = data["ai"]["provider"].get("openai_api_key", "")
    if key:
        data["ai"]["provider"]["openai_api_key"] = f"{key[:6]}…{key[-4:]}"
    output.print_json(data)


@config.command("path", context_settings=CONTEXT)
def config_path() -> None:
    """Print the settings file and log file locations."""
    output.stdout.print(str(CONFIG_FILE))
    output.stdout.print(str(LOG_FILE))


@cli.command(context_settings=CONTEXT)
@click.option("--yes", "-y", is_flag=True, help="Install without asking.")
@click.pass_obj
def setup(store: SettingsStore, yes: bool) -> None:
    """Install missing tools (Tesseract with languages, cjpegli, adb).

    Downloads come from the official sources into ~/.PaperScan/tools;
    Tesseract is installed with winget (Windows) or Homebrew (macOS).
    """
    output.print_banner()
    settings = store.load()
    missing = [tool for tool in INSTALLABLE_TOOLS if find_tool(tool, settings.tool_path(tool)) is None]
    for tool in missing:
        step = setup_plan(tool)
        if step.method is SetupMethod.MANUAL:
            output.warning(f"{tool.value}: {step.description}")
            continue
        if not yes and not click.confirm(f"Install {tool.value}? ({step.description})", default=True):
            continue
        _install_tool(tool)
    try:
        with output.stderr.status("[accent]Checking Tesseract languages …[/accent]") as status:
            install_tesseract_languages(settings.ocr.languages, lambda message, _fraction: status.update(f"[accent]{message} …[/accent]"))
    except SetupError as exc:
        _fail(str(exc))
    output.success("Setup complete. Run 'paperscan doctor' to check.")


def _install_tool(tool: Tool) -> None:
    """Install one tool with a status spinner."""
    try:
        with output.stderr.status(f"[accent]Installing {tool.value} …[/accent]") as status:
            path = install(tool, lambda message, _fraction: status.update(f"[accent]{message} …[/accent]"))
    except SetupError as exc:
        _fail(str(exc))
    output.success(f"{tool.value}: {path}")


@cli.command(context_settings=CONTEXT)
@click.pass_obj
def doctor(store: SettingsStore) -> None:
    """Check which tools and optional features are available."""
    output.print_banner()
    settings = store.load()
    rows = [(tool.value, _status(find_tool(tool, settings.tool_path(tool)))) for tool in Tool]
    rows.append(
        (
            "border model",
            "[ok]ready[/ok]" if model_available(settings.model_path) else "[warn]missing (paperscan model import)[/warn]",
        )
    )
    rows.append(("jpegli", "[ok]in use[/ok]" if jpegli_available(settings.jpeg) else "[warn]Pillow fallback[/warn]"))
    rows.append(("phone", _phone_status(settings)))
    output.stdout.print(output.facts_table("Setup", rows))


def _phone_status(settings: AppSettings) -> str:
    """Return the phone PaperScan would use (USB, else Wi-Fi), or why there is none."""
    try:
        device = settings.phone.adb_client().connect()
    except (ToolMissingError, AdbError) as exc:
        return f"[warn]{exc}[/warn]"
    return f"[ok]{device.label}[/ok]"


def _status(path: Path | None) -> str:
    """Return a styled found/missing label."""
    return f"[ok]{path}[/ok]" if path else "[warn]not found[/warn]"


def main() -> None:
    """Console entry point."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    cli(prog_name=PROGRAM)


if __name__ == "__main__":
    main()
