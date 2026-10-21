"""Offline card import and fast photograph-to-student batch assignment."""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from PySide6.QtCore import QSize, Qt, Signal, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..capture_repository import CaptureRepository
from ..capture_worker import CaptureController
from ..ingestion import IMAGE_EXTENSIONS
from .capture_dialogs import FolderDialog


STATUS_LABELS = {
    "receiving": "Reading image…", "awaiting_confirmation": "Ready to confirm",
    "copying": "Archiving original…", "imported": "Assigned and archived",
    "discarded": "Discarded", "failed": "Import failed", "duplicate": "Duplicate skipped",
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
        self.busy = False
        self.rows = {}
        self.initialized = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(10)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.addWidget(text_label("Batch assignment", "title"))
        heading.addWidget(text_label(
            "Copy a card or selected files into managed storage, then match photographs to students.", "muted"))
        header.addLayout(heading, 1)
        self.folders_button = QPushButton("Configure storage")
        self.folders_button.clicked.connect(self.configure)
        header.addWidget(self.folders_button)
        self.import_folder_button = QPushButton("Import card folder")
        self.import_folder_button.clicked.connect(self.import_folder)
        header.addWidget(self.import_folder_button)
        self.import_button = QPushButton("Import photos")
        self.import_button.setObjectName("primary")
        self.import_button.clicked.connect(self.import_files)
        header.addWidget(self.import_button)
        layout.addLayout(header)

        session_bar = QHBoxLayout()
        session_bar.addWidget(QLabel("Batch"))
        self.sessions = QComboBox()
        self.sessions.setAccessibleName("Photography batch")
        self.sessions.setMinimumWidth(260)
        self.sessions.currentIndexChanged.connect(self.refresh_photos)
        session_bar.addWidget(self.sessions, 1)
        create = QPushButton("New batch")
        create.clicked.connect(self.new_session)
        session_bar.addWidget(create)
        self.folder_summary = text_label("No managed storage configured", "muted")
        session_bar.addWidget(self.folder_summary, 2)
        layout.addLayout(session_bar)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self.build_photo_panel())
        split.addWidget(self.build_student_panel())
        split.setSizes([610, 430])
        layout.addWidget(split, 1)

        actions = QHBoxLayout()
        self.assign_button = QPushButton("Assign selected photos")
        self.assign_button.setObjectName("primary")
        self.assign_button.clicked.connect(self.assign_selected)
        self.confirm_button = QPushButton("Confirm selected assignments")
        self.confirm_button.clicked.connect(self.confirm_selected)
        self.discard_button = QPushButton("Discard selected")
        self.discard_button.clicked.connect(self.discard_selected)
        self.retry_button = QPushButton("Retry selected")
        self.retry_button.clicked.connect(self.retry_selected)
        for button in (self.assign_button, self.confirm_button, self.discard_button, self.retry_button):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.feedback = text_label(
            "Nothing is accepted automatically. Imported card files are copied into Incoming; archived originals are never modified.",
            "muted")
        layout.addWidget(self.feedback)

        self.controller.changed.connect(self.on_changed)
        self.controller.status.connect(lambda message, watching: self.feedback.setText(message))
        self.controller.result.connect(self.on_result)
        self.refresh_roster()
        self.refresh_sessions()
        self.on_changed()

    def build_photo_panel(self):
        panel = QWidget()
        panel.setMinimumWidth(430)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 10, 0)
        top = QHBoxLayout()
        self.photo_count = text_label("0 photographs", "section")
        top.addWidget(self.photo_count, 1)
        self.photo_filter = QComboBox()
        for label, value in (("All photos", None), ("Unassigned", "unassigned"),
                             ("Ready to confirm", "ready"), ("Assigned", "imported"),
                             ("Needs attention", "attention")):
            self.photo_filter.addItem(label, value)
        self.photo_filter.currentIndexChanged.connect(self.refresh_photos)
        top.addWidget(self.photo_filter)
        layout.addLayout(top)
        self.table = QTableWidget(0, 3)
        self.table.setAccessibleName("Imported photographs")
        self.table.setHorizontalHeaderLabels(["Photograph", "File", "Assignment and status"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setIconSize(QSize(124, 92))
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(104)
        self.table.setColumnWidth(0, 140)
        self.table.setColumnWidth(1, 170)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setWordWrap(True)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self.update_actions)
        layout.addWidget(self.table, 1)
        self.detail = text_label("Select one or more photographs, then choose a student on the right.")
        layout.addWidget(self.detail)
        return panel

    def build_student_panel(self):
        panel = QWidget()
        panel.setMinimumWidth(350)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 0, 0, 0)
        layout.addWidget(text_label("Students", "section"))
        filters = QHBoxLayout()
        self.student_search = QLineEdit()
        self.student_search.setPlaceholderText("Search name, ID, or filename")
        self.student_search.setClearButtonEnabled(True)
        self.student_search.textChanged.connect(self.refresh_students)
        filters.addWidget(self.student_search, 1)
        self.class_filter = QComboBox()
        self.class_filter.currentIndexChanged.connect(self.refresh_students)
        filters.addWidget(self.class_filter)
        layout.addLayout(filters)
        self.student_table = QTableWidget(0, 3)
        self.student_table.setAccessibleName("Students available for assignment")
        self.student_table.setHorizontalHeaderLabels(["Student ID", "Name", "Class"])
        self.student_table.verticalHeader().hide()
        self.student_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.student_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.student_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.student_table.setColumnWidth(0, 105)
        self.student_table.setColumnWidth(2, 100)
        self.student_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.student_table.setAlternatingRowColors(True)
        self.student_table.itemSelectionChanged.connect(self.update_actions)
        self.student_table.itemDoubleClicked.connect(lambda *_: self.assign_selected())
        layout.addWidget(self.student_table, 1)
        self.student_count = text_label("0 students", "muted")
        layout.addWidget(self.student_count)
        return panel

    def showEvent(self, event):
        super().showEvent(event)
        if not self.initialized and self.db.path != ":memory:":
            self.initialized = True
            self.controller.command("initialize")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.reveal_selection)

    def reveal_selection(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if rows:
            self.table.scrollToItem(self.table.item(rows[0].row(), 0))

    def refresh_roster(self):
        selected_class = self.class_filter.currentData() if hasattr(self, "class_filter") else None
        self.class_filter.blockSignals(True)
        self.class_filter.clear()
        self.class_filter.addItem("All classes", None)
        for row in self.db.classes():
            self.class_filter.addItem(row["name"], row["id"])
        self.class_filter.setCurrentIndex(max(0, self.class_filter.findData(selected_class)))
        self.class_filter.blockSignals(False)
        self.refresh_students()

    def refresh_students(self):
        selected = self.selected_student_id()
        rows = self.db.students(self.class_filter.currentData(), self.student_search.text())
        self.student_table.blockSignals(True)
        self.student_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for column, value in enumerate((row["student_id"], row["full_name"], row["class_name"])):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(Qt.UserRole, row["id"])
                self.student_table.setItem(index, column, item)
            if row["id"] == selected:
                self.student_table.selectRow(index)
        self.student_table.blockSignals(False)
        self.student_count.setText(f"{len(rows):,} students shown")
        self.update_actions()

    def refresh_sessions(self, selected=None):
        selected = selected or self.sessions.currentData()
        self.sessions.blockSignals(True)
        self.sessions.clear()
        self.sessions.addItem("Choose batch…", None)
        for row in self.repo.sessions():
            self.sessions.addItem(f"{row['name']} · {row['started_at'][:10]}", row["id"])
        self.sessions.setCurrentIndex(max(0, self.sessions.findData(selected)))
        self.sessions.blockSignals(False)
        self.refresh_photos()

    def new_session(self):
        name, accepted = QInputDialog.getText(
            self, "New photography batch", "Batch name", text=datetime.now().strftime("Card import %Y-%m-%d"))
        if accepted:
            try:
                self.refresh_sessions(self.repo.create_session(name))
            except (ValueError, sqlite3.Error) as exc:
                self.feedback.setText(str(exc))

    def configure(self):
        FolderDialog(self.controller, self.repo.settings("folders", {}), self).exec()

    def import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose photographs from the card", "",
            "Camera images (*.jpg *.jpeg *.cr2 *.cr3 *.nef);;All files (*)")
        self.queue_import(paths)

    def import_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose the card or camera folder")
        if not folder:
            return
        paths = [str(path) for path in Path(folder).rglob("*")
                 if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS]
        self.queue_import(paths)

    def queue_import(self, paths):
        if not paths:
            return
        if self.sessions.currentData() is None:
            self.feedback.setText("Create or choose a batch before importing photographs.")
            return
        if not self.repo.settings("folders"):
            self.feedback.setText("Configure managed storage before importing photographs.")
            return
        self.busy = True
        self.feedback.setText(f"Copying {len(paths):,} photograph(s) from the card…")
        self.controller.command("import_files", paths, self.sessions.currentData())
        self.update_actions()

    def selected_photo_ids(self):
        result = []
        if self.table.selectionModel():
            for index in self.table.selectionModel().selectedRows():
                item = self.table.item(index.row(), 0)
                if item:
                    result.append(item.data(Qt.UserRole))
        return result

    def selected_photos(self):
        return [self.rows[value] for value in self.selected_photo_ids() if value in self.rows]

    def selected_student_id(self):
        row = self.student_table.currentRow() if hasattr(self, "student_table") else -1
        item = self.student_table.item(row, 0) if row >= 0 else None
        return item.data(Qt.UserRole) if item and self.student_table.selectedItems() else None

    def refresh_photos(self):
        if not hasattr(self, "table"):
            return
        selected = set(self.selected_photo_ids())
        session_id = self.sessions.currentData()
        all_rows = self.repo.photos(session_id) if session_id else []
        mode = self.photo_filter.currentData()
        if mode == "unassigned":
            visible = [row for row in all_rows if row["student_record_id"] is None and row["processing_status"] != "duplicate"]
        elif mode == "ready":
            visible = [row for row in all_rows if row["processing_status"] == "awaiting_confirmation"]
        elif mode == "imported":
            visible = [row for row in all_rows if row["processing_status"] == "imported"]
        elif mode == "attention":
            visible = [row for row in all_rows if row["processing_status"] in ("failed", "discarded", "duplicate")]
        else:
            visible = list(all_rows)
        self.rows = {row["id"]: row for row in all_rows}
        unassigned = sum(row["student_record_id"] is None and row["processing_status"] != "duplicate" for row in all_rows)
        ready = sum(row["processing_status"] == "awaiting_confirmation" for row in all_rows)
        accepted = sum(row["processing_status"] == "imported" for row in all_rows)
        self.photo_count.setText(
            f"{len(all_rows):,} photos · {unassigned:,} unassigned · {ready:,} ready · {accepted:,} archived")
        self.table.blockSignals(True)
        self.table.setRowCount(len(visible))
        for index, row in enumerate(visible):
            preview = QTableWidgetItem()
            preview.setData(Qt.UserRole, row["id"])
            if row["thumbnail"]:
                pixmap = QPixmap()
                pixmap.loadFromData(row["thumbnail"])
                preview.setIcon(QIcon(pixmap))
            else:
                preview.setText("Reading…" if row["processing_status"] == "receiving" else "No preview")
            self.table.setItem(index, 0, preview)
            assignment = (f"{row['student_name']} ({row['student_id']})" if row["student_record_id"] is not None
                          else "Unassigned")
            values = (Path(row["original_path"]).name,
                      f"{assignment}\n{STATUS_LABELS[row['processing_status']]}")
            for column, value in enumerate(values, 1):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(index, column, item)
            if row["id"] in selected:
                self.table.selectRow(index)
        self.table.blockSignals(False)
        self.reveal_selection()
        self.update_actions()

    def update_actions(self):
        rows = self.selected_photos()
        student = self.selected_student_id()
        assignable = rows and all(row["processing_status"] not in ("receiving", "copying", "duplicate") for row in rows)
        confirmable = rows and all(row["processing_status"] == "awaiting_confirmation" and
                                   row["student_record_id"] is not None for row in rows)
        discardable = rows and all(row["processing_status"] in ("awaiting_confirmation", "imported", "failed") for row in rows)
        retryable = len(rows) == 1 and rows[0]["processing_status"] == "failed"
        self.assign_button.setEnabled(bool(assignable and student is not None and not self.busy))
        self.confirm_button.setEnabled(bool(confirmable and not self.busy))
        self.discard_button.setEnabled(bool(discardable and not self.busy))
        self.retry_button.setEnabled(bool(retryable and not self.busy))
        self.import_button.setEnabled(not self.busy)
        self.import_folder_button.setEnabled(not self.busy)
        if rows:
            row = rows[0]
            captured = (datetime.fromisoformat(row["capture_time"]).astimezone(timezone.utc).strftime("%d %b %Y, %H:%M:%S UTC")
                        if row["capture_time"] else "Still reading")
            extra = f" · and {len(rows) - 1} more" if len(rows) > 1 else ""
            self.detail.setText(f"{Path(row['original_path']).name}{extra}\n{captured}"
                                + (f"\nError: {row['error_message']}" if row["error_message"] else ""))
        else:
            self.detail.setText("Select one or more photographs, then choose a student on the right.")

    def run_command(self, action, *args):
        if self.busy:
            return
        self.busy = True
        self.controller.command(action, *args)
        self.update_actions()

    def assign_selected(self):
        ids, student = self.selected_photo_ids(), self.selected_student_id()
        if ids and student is not None:
            self.run_command("assign_many", ids, student)

    def confirm_selected(self):
        ids = [row["id"] for row in self.selected_photos()
               if row["processing_status"] == "awaiting_confirmation" and row["student_record_id"] is not None]
        if ids:
            self.run_command("confirm_many", ids)

    def discard_selected(self):
        ids = self.selected_photo_ids()
        if not ids:
            return
        answer = QMessageBox.question(
            self, "Discard selected photographs",
            f"Discard {len(ids)} selected assignment(s)? Card copies and archived originals are retained.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.run_command("discard_many", ids)

    def retry_selected(self):
        rows = self.selected_photos()
        if len(rows) == 1:
            self.run_command("retry", rows[0]["id"])

    def on_changed(self):
        folders = self.repo.settings("folders", {})
        self.folder_summary.setText(
            f"Incoming copies: {folders['incoming']}" if folders else "No managed storage configured")
        self.refresh_photos()

    def on_result(self, action, success, message):
        self.busy = False
        labels = {
            "import_files": "Card copy completed. Images are being validated; assign them when thumbnails appear.",
            "assign_many": "Assignment updated. Confirm the selected photographs to archive the originals.",
            "confirm_many": "Assignments confirmed and originals archived.",
            "discard_many": "Selected assignments discarded.", "retry": "Retry requested.",
            "configure": "Storage folders saved.", "initialize": "Batch workspace ready.",
        }
        self.feedback.setText(labels.get(action, "Ready.") if success else message)
        self.refresh_photos()
        if action in ("confirm_many", "retry"):
            self.roster_changed.emit()
