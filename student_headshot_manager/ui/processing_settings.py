"""Adjustable, persistent processing settings with a single-click starter preset."""

from dataclasses import asdict
import re

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from ..processing import ProcessingSettings


class ProcessingSettingsPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fields = {}
        self.loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 10)
        layout.setSpacing(12)
        preset = QPushButton("Even green screen preset")
        preset.clicked.connect(lambda: self.set_values(ProcessingSettings(), notify=True))
        layout.addWidget(preset)
        hint = QLabel("A starting point for evenly lit green screens. Inspect the mask; this is not automatic subject detection.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        layout.addWidget(hint)
        self.heading(layout, "Green-screen thresholds")
        form = QFormLayout()
        form.setSpacing(8)
        for prefix, caption, maximum in (("hue", "Hue (0–179)", 179),
                                          ("saturation", "Saturation (0–255)", 255),
                                          ("value", "Brightness (0–255)", 255)):
            pair = QHBoxLayout()
            pair.setSpacing(4)
            pair.addWidget(self.spin(prefix + "_low", 0, maximum))
            pair.addWidget(QLabel("to"))
            pair.addWidget(self.spin(prefix + "_high", 0, maximum))
            form.addRow(caption, pair)
        layout.addLayout(form)
        self.heading(layout, "Mask cleanup · source pixels")
        form = QFormLayout()
        form.setSpacing(8)
        for name, caption, low, high, decimal in (
            ("noise_radius", "Noise opening", 0, 20, False),
            ("holes_radius", "Hole closing", 0, 20, False),
            ("edge_shift", "Edge shift − / +", -20, 20, False),
            ("feather", "Feather sigma", 0, 20, True),
            ("spill_strength", "Spill strength (0–1)", 0, 1, True),
            ("spill_width", "Spill edge width", 0, 100, False),
        ):
            form.addRow(caption, self.spin(name, low, high, decimal))
        self.fields["edge_shift"].setToolTip("Negative erodes the subject mask; positive dilates it. Units: full-resolution pixels.")
        self.fields["holes_radius"].setToolTip("Close small holes. Large green areas inside clothing may require manual review.")
        layout.addLayout(form)
        self.heading(layout, "Portrait canvas")
        form = QFormLayout()
        form.setSpacing(8)
        for name, caption, low, high, decimal in (
            ("canvas_width", "Width (px)", 16, 10000, False),
            ("canvas_height", "Height (px)", 16, 10000, False),
            ("dpi", "Resolution (DPI)", 72, 1200, False),
            ("padding_percent", "Padding (%)", 0, 25, True),
            ("subject_scale", "Portrait size (%)", 10, 400, True),
            ("offset_x", "Horizontal position (px)", -10000, 10000, False),
            ("offset_y", "Vertical position (px)", -10000, 10000, False),
            ("jpeg_quality", "JPEG quality", 85, 100, False),
        ):
            form.addRow(caption, self.spin(name, low, high, decimal))
        layout.addLayout(form)
        self.heading(layout, "Background color")
        color_form = QFormLayout()
        color_row = QHBoxLayout()
        for name, caption in (("background_r", "R"), ("background_g", "G"), ("background_b", "B")):
            color_row.addWidget(QLabel(caption))
            color_row.addWidget(self.spin(name, 0, 255))
        color_form.addRow("RGB", color_row)
        self.hex_color = QLineEdit()
        self.hex_color.setPlaceholderText("#B4B4B4")
        self.hex_color.editingFinished.connect(self.apply_hex)
        color_form.addRow("Hex", self.hex_color)
        self.hsl_color = QLineEdit()
        self.hsl_color.setPlaceholderText("0, 0%, 71%")
        self.hsl_color.editingFinished.connect(self.apply_hsl)
        color_form.addRow("HSL", self.hsl_color)
        layout.addLayout(color_form)
        note = QLabel("Default delivery is a square 1080 × 1080 portrait at 300 DPI. The upright source is fitted without stretching; the selected RGB, Hex, or HSL color fills the canvas.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        layout.addStretch()
        self.set_values(ProcessingSettings())

    @staticmethod
    def heading(layout, text):
        heading = QLabel(text)
        heading.setObjectName("section")
        heading.setWordWrap(True)
        layout.addWidget(heading)

    def spin(self, name, low, high, decimal=False):
        field = QDoubleSpinBox() if decimal else QSpinBox()
        field.setRange(low, high)
        if decimal:
            field.setDecimals(2)
            field.setSingleStep(0.05 if name == "spill_strength" else 0.2)
        field.setAccessibleName(name.replace("_", " "))
        field.setKeyboardTracking(False)
        field.valueChanged.connect(self.value_changed)
        self.fields[name] = field
        return field

    def value_changed(self):
        if not self.loading:
            if self.sender() in tuple(self.fields.get(name) for name in ("background_r", "background_g", "background_b")):
                self.update_color_codes()
            self.changed.emit()

    def set_values(self, settings, notify=False):
        self.loading = True
        values = asdict(settings)
        red, green, blue = settings.background_rgb()
        values.update(background_r=red, background_g=green, background_b=blue)
        for name, value in values.items():
            if name in self.fields:
                self.fields[name].setValue(value)
        self.update_color_codes()
        self.loading = False
        if notify:
            self.changed.emit()

    def settings(self):
        return ProcessingSettings(**{name: field.value() for name, field in self.fields.items()}).validate()

    def set_transform(self, scale, offset_x, offset_y, notify=True):
        self.loading = True
        self.fields["subject_scale"].setValue(scale)
        self.fields["offset_x"].setValue(offset_x)
        self.fields["offset_y"].setValue(offset_y)
        self.loading = False
        if notify:
            self.changed.emit()

    def update_color_codes(self):
        if not all(name in self.fields for name in ("background_r", "background_g", "background_b")):
            return
        color = QColor(*(self.fields[name].value() for name in ("background_r", "background_g", "background_b")))
        self.hex_color.setText(color.name().upper())
        hue, saturation, lightness, _ = color.getHsl()
        hue = max(0, hue)
        self.hsl_color.setText(f"{hue}, {round(saturation * 100 / 255)}%, {round(lightness * 100 / 255)}%")

    def set_color(self, color):
        if not color.isValid():
            return False
        self.loading = True
        for name, value in zip(("background_r", "background_g", "background_b"), color.getRgb()[:3]):
            self.fields[name].setValue(value)
        self.update_color_codes()
        self.loading = False
        self.changed.emit()
        return True

    def apply_hex(self):
        value = self.hex_color.text().strip()
        if value and not value.startswith("#"):
            value = "#" + value
        if not self.set_color(QColor(value)):
            self.update_color_codes()

    def apply_hsl(self):
        match = re.fullmatch(r"\s*(\d{1,3})\s*,\s*(\d{1,3})%?\s*,\s*(\d{1,3})%?\s*", self.hsl_color.text())
        if not match:
            self.update_color_codes()
            return
        hue, saturation, lightness = map(int, match.groups())
        if not (0 <= hue <= 359 and 0 <= saturation <= 100 and 0 <= lightness <= 100):
            self.update_color_codes()
            return
        self.set_color(QColor.fromHsl(hue, round(saturation * 255 / 100), round(lightness * 255 / 100)))
