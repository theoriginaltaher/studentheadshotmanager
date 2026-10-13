"""Adjustable, persistent processing settings with a single-click starter preset."""

from dataclasses import asdict

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

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
            ("padding_percent", "Padding (%)", 0, 25, True),
            ("grey", "Neutral grey (0–255)", 0, 255, False),
            ("jpeg_quality", "JPEG quality", 85, 100, False),
        ):
            form.addRow(caption, self.spin(name, low, high, decimal))
        layout.addLayout(form)
        note = QLabel("The upright source frame is fitted with one scale factor. Grey padding fills the remaining canvas; the subject is never stretched.")
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
            self.changed.emit()

    def set_values(self, settings, notify=False):
        self.loading = True
        for name, value in asdict(settings).items():
            self.fields[name].setValue(value)
        self.loading = False
        if notify:
            self.changed.emit()

    def settings(self):
        return ProcessingSettings(**{name: field.value() for name, field in self.fields.items()}).validate()
