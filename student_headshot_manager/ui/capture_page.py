"""Incoming photographs and explicit operator-controlled student assignment."""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from PySide6.QtCore import QSize, Qt, Signal, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ..capture_repository import CaptureRepository
from ..capture_worker import CaptureController
from .capture_dialogs import FolderDialog, ReassignDialog

STATUS_LABELS = {
    "receiving": "Receiving…", "awaiting_confirmation": "Confirm assignment",
    "copying": "Copying original…", "imported": "Accepted", "discarded": "Assignment discarded",
    "failed": "Import failed", "duplicate": "Duplicate skipped",
}


def text_label(text="", name=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(True)
    if name:
        widget.setObjectName(name)
    return widget


class CapturePage(QWidget):
    roster_changed = Signal()

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.repo = CaptureRepository(db)
        self.controller = CaptureController(db.path, self)
        self.watching = False
        self.busy = False
        self.rows = {}
        self.cache = {}
        self.table_session = None
        self.initialized = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(10)
        header = QHBoxLayout()
        header.addWidget(text_label("Incoming photographs", "title"), 1)
        self.folders_button = QPushButton("Configure folders")
        self.folders_button.clicked.connect(self.configure)
        header.addWidget(self.folders_button)
        self.watch_button = QPushButton("Resume watcher")
        self.watch_button.setObjectName("primary")
        self.watch_button.clicked.connect(self.toggle_watcher)
        header.addWidget(self.watch_button)
        layout.addLayout(header)
        self.connection_status = text_label("Paused · watcher not started", "section")
        layout.addWidget(self.connection_status)
        layout.addWidget(text_label("Folder monitor only · camera connection, shutter and live view are not controlled here", "muted"))
        self.folder_summary = text_label("No Incoming folder configured", "muted")
        layout.addWidget(self.folder_summary)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Session"))
        self.sessions = QComboBox()
        self.sessions.setAccessibleName("Active capture session")
        self.sessions.setMinimumWidth(200)
        self.sessions.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        controls.addWidget(self.sessions, 1)
        create = QPushButton("New session")
        create.clicked.connect(self.new_session)
        controls.addWidget(create)
        controls.addWidget(QLabel("Student"))
        self.students = QComboBox()
        self.students.setAccessibleName("Active student for new incoming photographs")
        self.students.setMinimumWidth(240)
        self.students.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        controls.addWidget(self.students, 2)
        layout.addLayout(controls)
        self.active_label = text_label("No student selected", "section")
        layout.addWidget(self.active_label)
        note = text_label("Each new photo is proposed for the student selected at first detection. "
                          "Check the assignment below before accepting. Starting or resuming also scans existing JPEGs.", "muted")
        layout.addWidget(note)
        bar = QHBoxLayout()
        self.count_label = text_label("0 photographs received")
        bar.addWidget(self.count_label, 1)
        self.only_active = QCheckBox("Only active student")
        self.only_active.toggled.connect(self.refresh_photos)
        bar.addWidget(self.only_active)
        layout.addLayout(bar)
        self.table = QTableWidget(0, 4)
        self.table.setAccessibleName("Incoming photographs and their proposed assignments")
        self.table.setHorizontalHeaderLabels(["Photograph", "Incoming file", "Student assignment", "Status"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setIconSize(QSize(112, 84))
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(96)
        self.table.setColumnWidth(0, 128)
        self.table.setColumnWidth(1, 160)
        self.table.setColumnWidth(3, 160)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self.update_actions)
        layout.addWidget(self.table, 1)
        self.detail = text_label("Select a photograph to inspect and confirm its assignment.")
        layout.addWidget(self.detail)
        actions = QHBoxLayout()
        self.confirm_button = QPushButton("Confirm assignment")
        self.confirm_button.setObjectName("primary")
        self.confirm_button.clicked.connect(lambda: self.photo_command("confirm"))
        self.reassign_button = QPushButton("Reassign image")
        self.reassign_button.clicked.connect(self.reassign)
        self.discard_button = QPushButton("Discard assignment")
        self.discard_button.clicked.connect(self.discard)
        self.retry_button = QPushButton("Retry import")
        self.retry_button.clicked.connect(lambda: self.photo_command("retry"))
        for widget in (self.confirm_button, self.reassign_button, self.discard_button, self.retry_button):
            actions.addWidget(widget)
        actions.addStretch()
        layout.addLayout(actions)
        self.feedback = text_label("Incoming originals are never modified or deleted.", "muted")
        layout.addWidget(self.feedback)
        self.controller.changed.connect(self.on_changed)
        self.controller.status.connect(self.on_status)
        self.controller.result.connect(self.on_result)
        self.sessions.currentIndexChanged.connect(self.selection_changed)
        self.students.currentIndexChanged.connect(self.selection_changed)
        self.refresh_roster()
        self.refresh_sessions()
        self.on_changed()

    def showEvent(self, event):
        super().showEvent(event)
        if not self.initialized and self.db.path != ":memory:":
            self.initialized = True
            self.controller.command("initialize")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Keep the selected thumbnail on screen when shrinking the workstation window.
        QTimer.singleShot(0, self.reveal_selection)

    def reveal_selection(self):
        if self.table.selectedItems():
            self.table.scrollToItem(self.table.item(self.table.currentRow(), 0))

    def refresh_roster(self):
        selected = self.students.currentData()
        self.students.blockSignals(True)
        self.students.clear()
        self.students.addItem("Choose student…", None)
        for row in self.db.students():
            self.students.addItem(f"{row['student_id']} · {row['full_name']} · {row['class_name']}", row["id"])
        self.students.setCurrentIndex(max(0, self.students.findData(selected)))
        self.students.blockSignals(False)
        self.selection_changed()

    def refresh_sessions(self, selected=None):
        selected = selected or self.sessions.currentData()
        self.sessions.blockSignals(True)
        self.sessions.clear()
        self.sessions.addItem("Choose session…", None)
        for row in self.repo.sessions():
            self.sessions.addItem(f"{row['name']} · {row['started_at'][:10]}", row["id"])
        self.sessions.setCurrentIndex(max(0, self.sessions.findData(selected)))
        self.sessions.blockSignals(False)
        self.selection_changed()

    def selection_changed(self):
        student_id, session_id = self.students.currentData(), self.sessions.currentData()
        assignment = None
        if student_id is not None and session_id is not None:
            try:
                assignment = self.repo.assignment(student_id, session_id)
            except ValueError:
                pass
        self.controller.set_assignment(assignment)
        if assignment:
            self.active_label.setText(f"Active student: {assignment.student_name} ({assignment.student_id})")
        else:
            self.active_label.setText("Select a student and session before receiving photographs.")
            if self.watching:
                self.controller.command("pause")
        self.watch_button.setEnabled(not self.busy and (self.watching or bool(assignment)))
        self.refresh_photos()

    def new_session(self):
        name, accepted = QInputDialog.getText(self, "New capture session", "Session name", text=datetime.now().strftime("Photography %Y-%m-%d"))
        if accepted:
            try:
                session_id = self.repo.create_session(name)
                self.refresh_sessions(session_id)
            except (ValueError, sqlite3.Error) as exc:
                self.feedback.setText(str(exc))

    def configure(self):
        if self.watching:
            self.feedback.setText("Pause the watcher before changing folders.")
            return
        FolderDialog(self.controller, self.repo.settings("folders", {}), self).exec()

    def toggle_watcher(self):
        self.busy = True
        self.watch_button.setEnabled(False)
        self.controller.command("pause" if self.watching else "resume")

    def on_status(self, message, watching):
        self.watching = watching
        self.connection_status.setText(message)
        self.watch_button.setText("Pause watcher" if watching else "Resume watcher")
        self.folders_button.setEnabled(not watching and not self.busy)
        self.watch_button.setEnabled(not self.busy and (watching or self.controller.context() is not None or
                                    (self.students.currentData() is not None and self.sessions.currentData() is not None)))

    def on_changed(self):
        folders = self.repo.settings("folders", {})
        self.folder_summary.setText(f"Incoming: {folders['incoming']}" if folders else "No Incoming folder configured")
        self.refresh_photos()

    def on_result(self, action, success, message):
        self.busy = False
        self.on_status(self.connection_status.text(), self.watching)
        messages = {"confirm": "Assignment confirmed. A verified original is saved.",
                    "reassign": "Assignment changed. Check the student and confirm again.",
                    "discard": "Assignment discarded. All original files are retained.",
                    "retry": "Retry requested. Watch the photo status for the result.",
                    "configure": "Folders saved.", "pause": "Watcher paused.", "resume": "Watcher resumed."}
        self.feedback.setText(messages.get(action, "Ready.") if success else message)
        self.refresh_photos()
        if action in ("confirm", "retry"):
            self.roster_changed.emit()

    def selected_photo(self):
        item = self.table.item(self.table.currentRow(), 0)
        return self.rows.get(item.data(Qt.UserRole)) if item and self.table.selectedItems() else None

    def refresh_photos(self):
        selected = self.selected_photo()
        selected_id = selected["id"] if selected else None
        session_id = self.sessions.currentData()
        rows = self.repo.photos(session_id) if session_id else []
        self.rows = {row["id"]: row for row in rows}
        active_id = self.students.currentData()
        student_rows = [row for row in rows if row["student_record_id"] == active_id and row["processing_status"] != "duplicate"] if active_id else []
        accepted = sum(row["processing_status"] == "imported" for row in student_rows)
        failed = sum(row["processing_status"] == "failed" for row in student_rows)
        discarded = sum(row["processing_status"] == "discarded" for row in student_rows)
        self.count_label.setText(f"{len(student_rows)} received for active student · {accepted} accepted · {failed} failed · {discarded} discarded")
        visible = [row for row in rows if not self.only_active.isChecked() or row["student_record_id"] == active_id]
        # Rebuild only when content/selection filter changes, not on every watchdog event.
        cache = {row["id"]: tuple(row) for row in visible}
        if cache != self.cache or session_id != self.table_session:
            self.cache = cache
            self.table_session = session_id
            self.table.blockSignals(True)
            self.table.setRowCount(0)
            self.table.setRowCount(len(visible))
            for i, row in enumerate(visible):
                preview = QTableWidgetItem()
                preview.setData(Qt.UserRole, row["id"])
                if row["thumbnail"]:
                    pixmap = QPixmap()
                    pixmap.loadFromData(row["thumbnail"])
                    preview.setIcon(QIcon(pixmap))
                else:
                    preview.setText("Waiting…" if row["processing_status"] == "receiving" else "No preview")
                self.table.setItem(i, 0, preview)
                values = (Path(row["original_path"]).name,
                          f"{row['student_name']} ({row['student_id']})",
                          STATUS_LABELS[row["processing_status"]])
                for j, value in enumerate(values, 1):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    self.table.setItem(i, j, item)
                if row["id"] == selected_id:
                    self.table.selectRow(i)
            self.table.blockSignals(False)
            self.reveal_selection()
        self.update_actions()

    def update_actions(self):
        row = self.selected_photo()
        status = row["processing_status"] if row else None
        self.confirm_button.setEnabled(not self.busy and status == "awaiting_confirmation")
        self.retry_button.setEnabled(not self.busy and status == "failed")
        self.reassign_button.setEnabled(not self.busy and status in ("awaiting_confirmation", "imported", "discarded", "failed"))
        self.discard_button.setEnabled(not self.busy and status in ("awaiting_confirmation", "imported", "failed"))
        if row:
            prefix = {"imported": "Accepted assignment", "discarded": "Discarded assignment",
                      "duplicate": "Duplicate delivery proposed for"}.get(status, "Proposed assignment")
            captured = (datetime.fromisoformat(row["capture_time"]).astimezone(timezone.utc).strftime("%d %b %Y, %H:%M:%S UTC")
                        if row["capture_time"] else "Waiting for file")
            self.detail.setText(f"{prefix}: {row['student_name']} ({row['student_id']}) · {self.sessions.currentText()}\n"
                                f"Source: {row['original_path']}\n"
                                + (f"Error: {row['error_message']}" if row["error_message"] else
                                   f"Capture time: {captured} · file modification time"))
            self.detail.setToolTip(f"Archived original: {row['original_copy_path'] or 'Not yet accepted'}")
        else:
            self.detail.setText("Select a photograph to inspect and confirm its assignment. "
                                "No assignment is accepted automatically.")

    def photo_command(self, action, *args):
        row = self.selected_photo()
        if row and not self.busy:
            self.busy = True
            self.update_actions()
            self.controller.command(action, row["id"], *args)

    def reassign(self):
        row = self.selected_photo()
        if row:
            dialog = ReassignDialog(self.db, row, self)
            if dialog.exec() == QDialog.Accepted:
                self.photo_command("reassign", dialog.student.currentData())

    def discard(self):
        row = self.selected_photo()
        if row:
            box = QMessageBox(self)
            box.setWindowTitle("Discard assignment")
            box.setTextFormat(Qt.PlainText)
            box.setText(f"Discard the assignment of {Path(row['original_path']).name} to {row['student_name']} ({row['student_id']})?\n\n"
                        "The Incoming original and any archived copy will be retained.")
            discard = box.addButton("Discard assignment", QMessageBox.DestructiveRole)
            cancel = box.addButton(QMessageBox.Cancel)
            box.setDefaultButton(cancel)
            box.exec()
            if box.clickedButton() == discard:
                self.photo_command("discard")
