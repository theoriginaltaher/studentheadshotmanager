"""Small record editors with inline validation."""

import sqlite3
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QVBoxLayout,
)
from ..database import STATUSES


class RecordDialog(QDialog):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(24, 24, 24, 24)
        self.layout.setSpacing(16)
        self.form = QFormLayout()
        self.form.setSpacing(12)
        self.layout.addLayout(self.form)
        self.error = QLabel()
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText("Save changes")
        self.buttons.button(QDialogButtonBox.Save).setObjectName("primary")
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        self.layout.addWidget(self.buttons)

    def save(self):
        try:
            self.persist()
        except (ValueError, sqlite3.Error) as exc:
            self.error.setText(str(exc))
        else:
            self.accept()


class ClassDialog(RecordDialog):
    def __init__(self, db, parent=None, record=None):
        super().__init__(parent, "Edit class" if record else "New class")
        self.db, self.record = db, record
        self.saved_id = None
        self.name = QLineEdit(record["name"] if record else "")
        self.name.setMaxLength(200)
        self.form.addRow("Class &name", self.name)
        self.name.setFocus()

    def persist(self):
        if self.record:
            self.db.update_class(self.record["id"], self.name.text())
            self.saved_id = self.record["id"]
        else:
            self.saved_id = self.db.create_class(self.name.text())


class StudentDialog(RecordDialog):
    def __init__(self, db, parent=None, record=None, class_id=None):
        super().__init__(parent, "Edit student" if record else "Add student")
        self.db, self.record = db, record
        self.saved_id = None
        self.student_id = QLineEdit(record["student_id"] if record else "")
        self.student_id.setMaxLength(80)
        self.full_name = QLineEdit(record["full_name"] if record else "")
        self.full_name.setMaxLength(200)
        self.export_filename = QLineEdit(record["export_filename"] if record else "")
        self.export_filename.setMaxLength(200)
        self.class_choice = QComboBox()
        for item in db.classes():
            self.class_choice.addItem(item["name"], item["id"])
        selected = record["class_id"] if record else class_id
        index = self.class_choice.findData(selected)
        if index >= 0:
            self.class_choice.setCurrentIndex(index)
        self.status = QComboBox()
        self.status.addItems(STATUSES)
        if record:
            self.status.setCurrentText(record["status"])
        self.form.addRow("Student &ID", self.student_id)
        self.form.addRow("Full &name", self.full_name)
        self.form.addRow("Export &filename", self.export_filename)
        self.form.addRow("&Class", self.class_choice)
        self.form.addRow("Photography &status", self.status)
        note = QLabel("Status can be edited manually. Accepting an incoming photograph also marks a student as Captured.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        self.form.addRow(note)
        self.student_id.setFocus()

    def persist(self):
        self.saved_id = self.db.save_student(
            self.student_id.text(), self.full_name.text(), self.class_choice.currentData(),
            self.status.currentText(), self.record["id"] if self.record else None,
            self.export_filename.text() or None,
        )
