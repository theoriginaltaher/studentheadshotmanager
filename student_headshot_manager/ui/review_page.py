"""Before/after and full-resolution alpha-mask review, entirely on the local PC."""

from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton,
    QScrollArea, QSplitter, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from ..capture_repository import CaptureRepository
from ..processing import ProcessingSettings
from ..processing_repository import ProcessingRepository
from ..processing_worker import ProcessingController
from .image_view import ImageView, MaskEditor, PortraitLayoutCanvas
from .processing_settings import ProcessingSettingsPanel


class ReviewPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.repo = ProcessingRepository(db)
        self.controller = ProcessingController(db.path, self)
        self.token = 0
        self.busy = False
        self.loading = False
        self.current_job = None
        self.mask_dirty = False
        self.layout_dirty = False
        self.initialized = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(10)
        title = QLabel("Green-screen review")
        title.setObjectName("title")
        layout.addWidget(title)
        subtitle = QLabel("Local processing only · originals stay unchanged · every result needs an operator's review")
        subtitle.setWordWrap(True)
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)
        chooser = QHBoxLayout()
        chooser.addWidget(QLabel("Photograph"))
        self.photos = QComboBox()
        self.photos.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.photos.setAccessibleName("Accepted photograph to process")
        chooser.addWidget(self.photos, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(lambda: self.refresh_photos(reload=True))
        chooser.addWidget(refresh)
        self.batch_button = QPushButton("Process all assigned")
        self.batch_button.setObjectName("primary")
        self.batch_button.clicked.connect(self.process_all)
        chooser.addWidget(self.batch_button)
        layout.addLayout(chooser)
        self.status = QLabel("Assign and confirm a photograph, then select it here.")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.workspace_tabs = QTabWidget()
        preview_area = QWidget()
        preview_layout = QVBoxLayout(preview_area)
        preview_layout.setContentsMargins(8, 8, 8, 8)
        preview_layout.setSpacing(8)
        self.tabs = QTabWidget()
        comparison = QSplitter(Qt.Horizontal)
        self.before = ImageView("Original · preview up to 2048 px")
        self.after = ImageView("Grey portrait · preview up to 2048 px")
        comparison.addWidget(self.before)
        comparison.addWidget(self.after)
        comparison.setChildrenCollapsible(False)
        self.tabs.addTab(comparison, "Before / after")
        self.cutout = ImageView("Transparent cutout · preview up to 2048 px", checker=True)
        self.tabs.addTab(self.cutout, "Cutout")
        preview_layout.addWidget(self.tabs, 1)
        self.diagnostics = QTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setAccessibleName("Mask checks and manual review notes")
        self.diagnostics.setMaximumHeight(105)
        self.diagnostics.setMinimumHeight(65)
        self.diagnostics.setPlainText("Mask checks cannot certify hair, green clothing, or lighting quality. Inspect edges and the full mask before approving.")
        preview_layout.addWidget(self.diagnostics)
        self.workspace_tabs.addTab(preview_area, "Results")
        self.mask = MaskEditor()
        self.mask.changed.connect(self.mask_changed)
        self.workspace_tabs.addTab(self.mask, "Edit mask")
        layout_page = QWidget()
        layout_page_layout = QVBoxLayout(layout_page)
        layout_note = QLabel(
            "Drag the portrait to move it. Drag the white square at the lower-right corner to resize it. "
            "Use the mouse wheel to zoom the editing view; the 1080 × 1080 frame remains unchanged.")
        layout_note.setWordWrap(True)
        layout_page_layout.addWidget(layout_note)
        self.layout_editor = PortraitLayoutCanvas()
        self.layout_editor.transform_changed.connect(self.transform_changed)
        layout_page_layout.addWidget(self.layout_editor, 1)
        layout_controls = QHBoxLayout()
        fit_layout = QPushButton("Fit canvas")
        fit_layout.clicked.connect(self.layout_editor.fit_canvas)
        actual_layout = QPushButton("100%")
        actual_layout.clicked.connect(self.layout_editor.actual_size)
        layout_controls.addWidget(fit_layout)
        layout_controls.addWidget(actual_layout)
        layout_controls.addStretch()
        layout_page_layout.addLayout(layout_controls)
        self.workspace_tabs.addTab(layout_page, "Position and size")
        self.settings_panel = ProcessingSettingsPanel()
        defaults = CaptureRepository(db).settings("processing_defaults")
        if defaults:
            try:
                self.settings_panel.set_values(ProcessingSettings(**defaults).validate())
            except (TypeError, ValueError):
                pass
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.settings_panel)
        self.workspace_tabs.addTab(scroll, "Processing settings")
        layout.addWidget(self.workspace_tabs, 1)
        actions = QHBoxLayout()
        self.process_button = QPushButton("Process locally")
        self.process_button.setObjectName("primary")
        self.process_button.clicked.connect(self.process)
        self.approve_button = QPushButton("Approve result")
        self.approve_button.clicked.connect(self.approve)
        self.save_mask_button = QPushButton("Apply mask edits")
        self.save_mask_button.clicked.connect(self.apply_mask)
        self.flag_button = QPushButton("Flag manual review")
        self.flag_button.clicked.connect(self.flag)
        self.open_button = QPushButton("Open output folder")
        self.open_button.clicked.connect(self.open_outputs)
        for button in (self.process_button, self.save_mask_button, self.approve_button, self.flag_button, self.open_button):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.photos.currentIndexChanged.connect(self.photo_changed)
        self.settings_panel.changed.connect(self.settings_changed)
        self.controller.done.connect(self.on_done)
        self.controller.progress.connect(self.on_progress)
        self.refresh_photos()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_photos()
        if not self.initialized and self.db.path != ":memory:":
            self.initialized = True
            self.controller.command("initialize", self.token)

    def refresh_photos(self, reload=False):
        current = self.photos.currentData()
        self.photos.blockSignals(True)
        self.photos.clear()
        self.photos.addItem("Choose an accepted photograph…", None)
        for photo in self.repo.photos():
            self.photos.addItem(f"{photo['student_name']} ({photo['student_id']}) · {Path(photo['original_path']).name} · {photo['session_name']}", photo["id"])
        self.photos.setCurrentIndex(max(0, self.photos.findData(current)))
        self.photos.blockSignals(False)
        if current != self.photos.currentData() or (reload and current is not None and not self.busy):
            self.photo_changed()
        self.update_actions()

    def clear_outputs(self):
        self.current_job = None
        self.mask_dirty = False
        self.layout_dirty = False
        self.diagnostics.setPlainText("Inspect the updated result and full-resolution mask before approving. Mask checks cannot certify separation quality.")
        for view in (self.after, self.cutout):
            view.set_image(None)
        self.mask.set_images(None, None)
        try:
            settings = self.settings_panel.settings()
        except ValueError:
            settings = ProcessingSettings()
        self.layout_editor.set_portrait(None, settings)

    def photo_changed(self):
        self.token += 1
        self.loading = False
        self.before.set_image(None)
        self.clear_outputs()
        capture_id = self.photos.currentData()
        if capture_id is None:
            self.status.setText("Assign and confirm a photograph, then select it here.")
            self.update_actions()
            return
        latest = self.repo.latest(capture_id)
        if latest:
            try:
                self.settings_panel.set_values(ProcessingSettings(**json.loads(latest["settings_json"])).validate())
            except (ValueError, TypeError):
                pass
        try:
            settings = self.settings_panel.settings()
        except ValueError as exc:
            self.status.setText(str(exc))
            self.update_actions()
            return
        self.loading = True
        self.controller.command("preview", self.token, capture_id, asdict(settings))
        self.update_actions()

    def settings_changed(self):
        self.token += 1  # Ignore any preview/result queued for older settings.
        self.loading = False
        self.clear_outputs()
        try:
            self.settings_panel.settings()
            self.status.setText("Settings changed. Click Process locally to update the previews.")
        except ValueError as exc:
            self.status.setText(str(exc))
        self.update_actions()

    def process(self):
        if self.photos.currentData() is None or self.busy:
            return
        try:
            settings = self.settings_panel.settings()
            CaptureRepository(self.db).set_settings("processing_defaults", asdict(settings))
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(str(exc))
            return
        self.token += 1
        self.busy = True
        self.loading = False
        self.clear_outputs()
        self.status.setText("Queued for local processing…")
        self.controller.command("process", self.token, self.photos.currentData(), asdict(settings))
        self.update_actions()

    def process_all(self):
        if self.busy:
            return
        capture_ids = [row["id"] for row in self.repo.photos()]
        if not capture_ids:
            self.status.setText("Assign and confirm photographs before batch processing.")
            return
        try:
            settings = self.settings_panel.settings()
            CaptureRepository(self.db).set_settings("processing_defaults", asdict(settings))
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(str(exc))
            return
        self.token += 1
        self.busy = True
        self.loading = False
        self.status.setText(f"Queued {len(capture_ids):,} assigned photograph(s) for local processing…")
        self.controller.command("process_batch", self.token, capture_ids, asdict(settings))
        self.update_actions()

    def mask_changed(self, dirty):
        self.mask_dirty = bool(dirty)
        if dirty:
            self.status.setText("Mask edited. Apply the edits to rebuild the cutout and portrait.")
        self.update_actions()

    def transform_changed(self, scale, offset_x, offset_y):
        self.settings_panel.set_transform(scale, offset_x, offset_y, notify=False)
        self.layout_dirty = True
        self.current_job = None
        self.status.setText("Portrait position changed. Process locally to rebuild the 1080 × 1080 output.")
        self.update_actions()

    def apply_mask(self):
        if not self.current_job or not self.mask_dirty or self.busy:
            return
        try:
            data = self.mask.mask_png()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.token += 1
        self.busy = True
        self.status.setText("Applying the edited mask…")
        self.controller.command("apply_mask", self.token, self.current_job["id"], data)
        self.update_actions()

    def on_progress(self, token, message):
        if token == self.token:
            self.status.setText(message)

    def on_done(self, action, token, success, payload):
        if action in ("process", "process_batch", "apply_mask", "approve", "flag", "worker"):
            self.busy = False
        if action == "worker":
            self.loading = False
            self.status.setText(payload["error"])
        elif token == self.token:
            self.loading = False
            if not success:
                self.status.setText(f"Unable to {action}: {payload['error']}")
                if action in ("process", "approve"):
                    self.clear_outputs()
            elif action in ("preview", "process", "apply_mask", "process_batch"):
                if action == "process_batch" and not payload.get("job"):
                    failures = payload.get("failures", [])
                    self.status.setText(f"Batch finished with {len(failures):,} failure(s) and no completed outputs.")
                    self.diagnostics.setPlainText("\n".join(item["error"] for item in failures[:20]))
                    self.update_actions()
                    return
                self.before.set_image(payload["original"])
                self.current_job = payload["job"]
                for name, view in (("portrait", self.after), ("cutout", self.cutout)):
                    view.set_image(payload.get(name))
                self.mask.set_images(payload.get("original"), payload.get("mask"))
                self.layout_editor.set_portrait(payload.get("cutout"), self.settings_panel.settings())
                self.mask_dirty = False
                self.layout_dirty = False
                if action == "process_batch":
                    failures = payload.get("failures", [])
                    completed = payload.get("completed", [])
                    self.refresh_photos()
                    self.photos.setCurrentIndex(self.photos.findData(self.current_job["capture_id"]))
                    self.status.setText(
                        f"Batch complete: {len(completed):,} processed, {len(failures):,} failed. Review each result before approval.")
                    if failures:
                        self.diagnostics.setPlainText("\n".join(item["error"] for item in failures[:20]))
                else:
                    self.describe_result(payload.get("cached", False))
            elif action in ("approve", "flag"):
                if self.current_job:
                    self.current_job = dict(self.repo.get(self.current_job["id"]))
                self.describe_result()
        self.update_actions()

    def describe_result(self, cached=False):
        capture_id = self.photos.currentData()
        review = self.repo.review(capture_id) if capture_id else None
        job = self.current_job
        if review and review["manual_review"]:
            self.status.setText("Flagged for manual review.")
        elif job:
            diagnostics = json.loads(job["diagnostics_json"])
            if diagnostics["blocking"]:
                self.status.setText("Separation check failed. Adjust settings or flag for manual review; approval is blocked.")
            elif job["status"] == "approved":
                self.status.setText("Operator-approved result" + (" · cached files reused" if cached else ""))
            else:
                self.status.setText(("Cached result reused" if cached else "Local outputs saved") + " · inspect and approve, or flag manual review")
        else:
            latest = self.repo.latest(capture_id) if capture_id else None
            self.status.setText("Last processing attempt failed: " + latest["error_message"]
                                if latest and latest["status"] == "failed" else "Original loaded. Adjust settings and click Process locally.")
        lines = []
        if review and review["manual_review"]:
            lines.append("Operator flag: " + review["note"])
        if job:
            diagnostics = json.loads(job["diagnostics_json"])
            lines.extend(diagnostics["issues"])
            lines.append(f"Foreground: {diagnostics['foreground_fraction']:.1%}. These checks cannot certify separation quality.")
        lines.append("Inspect hair, clothing, edge halos, and holes at 100% in the Mask tab. Green clothing can be removed by chroma keying.")
        self.diagnostics.setPlainText("\n".join(lines))

    def update_actions(self):
        valid = True
        try:
            self.settings_panel.settings()
        except ValueError:
            valid = False
        selected = self.photos.currentData() is not None
        self.process_button.setEnabled(selected and valid and not self.busy and not self.loading)
        self.batch_button.setEnabled(bool(self.repo.photos()) and valid and not self.busy and not self.loading)
        self.save_mask_button.setEnabled(bool(self.current_job and self.mask_dirty and not self.busy and not self.loading))
        self.flag_button.setEnabled(selected and not self.busy)
        job = self.current_job
        can_approve = bool(job and job["status"] in ("awaiting_review", "needs_review", "approved") and
                           not json.loads(job["diagnostics_json"])["blocking"])
        self.approve_button.setEnabled(can_approve and not self.layout_dirty and not self.busy and not self.loading)
        self.open_button.setEnabled(bool(job and job["outputs_json"]))

    def approve(self):
        if not self.current_job or self.busy:
            return
        diagnostics = json.loads(self.current_job["diagnostics_json"])
        review = self.repo.review(self.photos.currentData())
        acknowledged = False
        if diagnostics["issues"] or (review and review["manual_review"]):
            box = QMessageBox(self)
            box.setWindowTitle("Approve inspected result")
            box.setTextFormat(Qt.PlainText)
            box.setText("This photograph has warnings or a manual-review flag. Approve only after inspecting the subject and mask. Approval clears the manual flag for this photograph.")
            approve = box.addButton("Approve inspected result", QMessageBox.AcceptRole)
            cancel = box.addButton(QMessageBox.Cancel)
            box.setDefaultButton(cancel)
            box.exec()
            if box.clickedButton() != approve:
                return
            acknowledged = True
        self.busy = True
        self.controller.command("approve", self.token, self.current_job["id"], acknowledged)
        self.update_actions()

    def flag(self):
        capture_id = self.photos.currentData()
        if capture_id is None or self.busy:
            return
        note, accepted = QInputDialog.getMultiLineText(self, "Flag for manual review", "What needs attention?", "Check subject separation and edges.")
        if accepted:
            self.busy = True
            self.controller.command("flag", self.token, capture_id, note)
            self.update_actions()

    def open_outputs(self):
        if self.current_job:
            path = Path(json.loads(self.current_job["outputs_json"])["cutout"]["path"]).parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
