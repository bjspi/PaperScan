"""Filter controls: presets, mode, colour/gray, fine-tuning sliders and the page scope.

Presets are the same numbered variants the comparison uses, so "pick a preset,
then fine-tune" and "compare, then apply" lead to the same settings.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from paperscan.gui.toggle_switch import ToggleSwitch
from paperscan.imaging.filter_variants import FilterVariant, preset_variants
from paperscan.imaging.filters import FilterMode, FilterSettings

MODE_LABELS: dict[FilterMode, str] = {
    FilterMode.ORIGINAL: "Original",
    FilterMode.PHOTO: "Photo",
    FilterMode.DOCUMENT: "Document",
    FilterMode.CLEAR: "Clear",
    FilterMode.BLACK_WHITE: "B/W",
}
CUSTOM_PRESET = "Custom"
#: Value label of black/white points left on automatic.
AUTO_TEXT = "auto"
SECTION_SPACING = 14
ROW_SPACING = 10


@dataclass(frozen=True)
class SliderSpec:
    """A slider bound to one numeric :class:`FilterSettings` field.

    Floats are mapped to integer slider steps with ``scale``; ``decimals`` controls the value label.
    """

    field: str
    label: str
    minimum: float
    maximum: float
    scale: int
    decimals: int
    modes: frozenset[FilterMode]
    tooltip: str


_DOCUMENT_MODES = frozenset({FilterMode.DOCUMENT, FilterMode.CLEAR, FilterMode.BLACK_WHITE})
_PERCENT = 100
_RESET_HINT = " Double-click to reset."

SLIDERS: tuple[SliderSpec, ...] = (
    SliderSpec(
        "soft_contrast", "Soft contrast", 0.3, 1.5, _PERCENT, 2, frozenset({FilterMode.DOCUMENT}), "Curve for flat areas (default 0.80)."
    ),
    SliderSpec(
        "hard_contrast", "Hard contrast", 0.3, 1.5, _PERCENT, 2, _DOCUMENT_MODES, "Text and edge curve; lower = stronger (default 0.62)."
    ),
    SliderSpec("ink_darkness", "Ink darkness", 0.3, 1.5, _PERCENT, 2, _DOCUMENT_MODES, "Lower = darker ink (default 0.70)."),
    SliderSpec(
        "edge_sharpening", "Edge sharpening", 5, 150, 1, 0, frozenset({FilterMode.DOCUMENT}), "Lower = crisper text edges (default 50)."
    ),
    SliderSpec("paper_min_brightness", "Paper brightness", 20, 200, 1, 0, _DOCUMENT_MODES, "Minimum paper brightness (default 80)."),
    SliderSpec("paper_max_tint", "Paper tint", 10, 150, 1, 0, _DOCUMENT_MODES, "Maximum colour tint still counted as paper (default 60)."),
    SliderSpec(
        "bw_threshold", "B/W threshold", 50, 250, 1, 0, frozenset({FilterMode.BLACK_WHITE}), "Gray level separating black from white."
    ),
    SliderSpec(
        "black_point", "Black point", 0, 128, 1, 0, frozenset({FilterMode.PHOTO}), "Input level that becomes black (auto by default)."
    ),
    SliderSpec(
        "white_point", "White point", 128, 255, 1, 0, frozenset({FilterMode.PHOTO}), "Input level that becomes white (auto by default)."
    ),
)

_FLOAT_FIELDS = {item.name for item in fields(FilterSettings) if item.type in ("float", float)}


class ResettableSlider(QSlider):
    """A horizontal slider that asks for a reset on double-click."""

    reset_requested = Signal()

    def __init__(self) -> None:
        """Create the slider."""
        super().__init__(Qt.Orientation.Horizontal)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        """Reset instead of jumping to the click position."""
        self.reset_requested.emit()
        event.accept()


@dataclass(frozen=True)
class _SliderRow:
    """Widgets of one fine-tuning slider."""

    spec: SliderSpec
    container: QWidget
    slider: ResettableSlider
    value: QLabel


class FilterPanel(QGroupBox):
    """Edits a :class:`FilterSettings` value and emits every change."""

    settings_changed = Signal(object, bool)  # FilterSettings, only_this_page
    compare_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the controls with default settings."""
        super().__init__("FILTER", parent)
        self._settings = FilterSettings()
        self._loading = False
        self._presets = preset_variants()
        self._mode_group = QButtonGroup(self)
        self._rows: dict[str, _SliderRow] = {}
        layout = QVBoxLayout(self)
        layout.setSpacing(SECTION_SPACING)
        layout.addLayout(self._build_preset_row())
        layout.addLayout(self._build_mode_buttons())
        self._gray = ToggleSwitch("Grayscale")
        self._gray.toggled.connect(self._emit)
        layout.addWidget(self._gray)
        layout.addLayout(self._build_fine_tuning_header())
        sliders = QVBoxLayout()
        sliders.setSpacing(ROW_SPACING)
        for spec in SLIDERS:
            sliders.addWidget(self._build_slider_row(spec))
        layout.addLayout(sliders)
        layout.addStretch(1)
        self._only_this_page = ToggleSwitch("Only this page")
        self._only_this_page.setToolTip("By default every page of the document uses the same filter.")
        self._only_this_page.toggled.connect(self._emit)
        layout.addWidget(self._only_this_page)

    def show_settings(self, settings: FilterSettings, own_filter: bool) -> None:
        """Load ``settings`` into the controls without emitting a change."""
        self._loading = True
        self._settings = settings
        self._mode_group.button(list(FilterMode).index(settings.mode)).setChecked(True)
        self._gray.setChecked(settings.grayscale)
        self._only_this_page.setChecked(own_filter)
        for row in self._rows.values():
            self._show_slider_value(row, getattr(settings, row.spec.field))
        self._update_visibility(settings.mode)
        self._show_matching_preset(settings)
        self._loading = False

    # --- Construction ---------------------------------------------------------------------------

    def _build_preset_row(self) -> QHBoxLayout:
        """Preset dropdown plus the comparison shortcut."""
        self._preset = QComboBox()
        self._preset.addItem(CUSTOM_PRESET, None)
        for variant in self._presets:
            self._preset.addItem(f"{variant.number}  {variant.name}", variant)
        self._preset.setToolTip("Start from a preset, then fine-tune below.")
        self._preset.activated.connect(self._preset_chosen)
        compare = QPushButton("Compare …")
        compare.setToolTip("Show all presets side by side for the current page (Ctrl+K).")
        compare.clicked.connect(self.compare_requested)
        row = QHBoxLayout()
        row.addWidget(self._preset, 1)
        row.addWidget(compare)
        return row

    def _build_mode_buttons(self) -> QHBoxLayout:
        """Create one checkable button per filter mode."""
        row = QHBoxLayout()
        row.setSpacing(4)
        for index, mode in enumerate(FilterMode):
            button = QPushButton(MODE_LABELS[mode])
            button.setObjectName("mode")
            button.setCheckable(True)
            self._mode_group.addButton(button, index)
            row.addWidget(button)
        self._mode_group.idToggled.connect(self._mode_toggled)
        return row

    def _build_fine_tuning_header(self) -> QHBoxLayout:
        """Section label with a reset-all button."""
        title = QLabel("FINE-TUNING")
        title.setObjectName("hint")
        reset = QPushButton("Reset all")
        reset.setObjectName("link")
        reset.setToolTip("Restore the defaults for this mode.")
        reset.clicked.connect(self._reset_all)
        row = QHBoxLayout()
        row.addWidget(title)
        row.addStretch(1)
        row.addWidget(reset)
        return row

    def _build_slider_row(self, spec: SliderSpec) -> QWidget:
        """Title and value on one line, the slider underneath."""
        container = QWidget()
        container.setObjectName("sliderRow")
        container.setToolTip(spec.tooltip + _RESET_HINT)
        title = QLabel(spec.label)
        value = QLabel()
        value.setObjectName("sliderValue")
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        slider = ResettableSlider()
        slider.setRange(round(spec.minimum * spec.scale), round(spec.maximum * spec.scale))
        slider.setToolTip(spec.tooltip + _RESET_HINT)
        slider.sliderReleased.connect(self._emit)
        slider.valueChanged.connect(lambda _value, field=spec.field: self._slider_moved(field))
        slider.reset_requested.connect(lambda field=spec.field: self._reset_field(field))
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(value)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(header)
        layout.addWidget(slider)
        self._rows[spec.field] = _SliderRow(spec, container, slider, value)
        return container

    # --- Reactions ------------------------------------------------------------------------------

    def _preset_chosen(self, index: int) -> None:
        """Load the chosen preset and apply it."""
        variant: FilterVariant | None = self._preset.itemData(index)
        if variant is None:
            return
        self.show_settings(variant.settings, self._only_this_page.isChecked())
        self._emit()

    def _slider_moved(self, field: str) -> None:
        """Update the value label; keyboard changes (no drag) are applied immediately."""
        row = self._rows[field]
        row.value.setText(_format(row.spec, _slider_value(row.spec, row.slider)))
        if not row.slider.isSliderDown():
            self._emit()

    def _mode_toggled(self, button_id: int, checked: bool) -> None:
        """Switch the mode when a mode button gets checked."""
        if checked:
            self._update_visibility(list(FilterMode)[button_id])
            self._emit()

    def _reset_field(self, field: str) -> None:
        """Restore one slider to its default."""
        self._show_slider_value(self._rows[field], getattr(FilterSettings(), field))
        self._settings = replace(self._settings, **{field: getattr(FilterSettings(), field)})
        self._emit()

    def _reset_all(self) -> None:
        """Restore all sliders to their defaults, keeping mode and gray."""
        current = self._current()
        self.show_settings(replace(FilterSettings(), mode=current.mode, grayscale=current.grayscale), self._only_this_page.isChecked())
        self._emit()

    def _update_visibility(self, mode: FilterMode) -> None:
        """Show only the sliders that affect ``mode``."""
        for row in self._rows.values():
            row.container.setVisible(mode in row.spec.modes)
        self._gray.setVisible(mode is not FilterMode.BLACK_WHITE)

    def _show_slider_value(self, row: _SliderRow, value: float | None) -> None:
        """Move a slider without emitting and update its label ("auto" for unset levels)."""
        shown = value if value is not None else (row.spec.minimum if row.spec.field == "black_point" else row.spec.maximum)
        row.slider.blockSignals(True)
        row.slider.setValue(round(shown * row.spec.scale))
        row.slider.blockSignals(False)
        row.value.setText(_format(row.spec, value))

    def _show_matching_preset(self, settings: FilterSettings) -> None:
        """Select the preset equal to ``settings``, or "Custom"."""
        index = next((position + 1 for position, variant in enumerate(self._presets) if variant.settings == settings), 0)
        self._preset.setCurrentIndex(index)

    def _current(self) -> FilterSettings:
        """Read the settings from the controls; untouched automatic levels stay automatic."""
        values: dict[str, object] = {"mode": list(FilterMode)[max(0, self._mode_group.checkedId())], "grayscale": self._gray.isChecked()}
        for field, row in self._rows.items():
            if getattr(self._settings, field) is None and row.value.text() == AUTO_TEXT:
                continue
            values[field] = _slider_value(row.spec, row.slider)
        return replace(self._settings, **values)  # type: ignore[arg-type]

    def _emit(self) -> None:
        """Publish the current settings."""
        if self._loading:
            return
        self._settings = self._current()
        self._show_matching_preset(self._settings)
        self.settings_changed.emit(self._settings, self._only_this_page.isChecked())


def _slider_value(spec: SliderSpec, slider: QSlider) -> float | int:
    """Convert the slider position back to the field's type."""
    if spec.field in _FLOAT_FIELDS:
        return slider.value() / spec.scale
    return slider.value()


def _format(spec: SliderSpec, value: float | None) -> str:
    """Return the value label text."""
    if value is None:
        return AUTO_TEXT
    return f"{value:.{spec.decimals}f}"
