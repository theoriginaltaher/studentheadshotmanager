"""Folder configuration and explicit reassignment dialogs."""

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)


class FolderDialog(QDialog):
    def __init__(self, controller, values, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Capture folders")
        self.setMinimumWidth(660)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        note = QLabel("Choose four separate folders. Incoming is read only to this application. "
                      "Processed and Export are reserved for later stages.")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        form.setSpacing(12)
        self.fields = {}
        for name in ("incoming", "originals", "processed", "export"):
            row = QHBoxLayout()
            field = QLineEdit(values.get(name, ""))
            field.setAccessibleName(f"{name.title()} folder")
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda checked=False, n=name: self.browse(n))
            row.addWidget(field, 1)
            row.addWidget(browse)
            form.addRow(name.title(), row)
            self.fields[name] = field
        layout.addLayout(form)
        self.error = QLabel()
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText("Save folders")
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.controller.result.connect(self.on_result)
        self.finished.connect(lambda: self.controller.result.disconnect(self.on_result))

    def browse(self, name):
        chosen = QFileDialog.getExistingDirectory(self, f"Choose {name.title()} folder", self.fields[name].text())
        if chosen:
            self.fields[name].setText(chosen)

    def save(self):
        self.buttons.setEnabled(False)
        self.error.setText("Checking folder access…")
        self.controller.command("configure", {name: field.text().strip() for name, field in self.fields.items()})

    def on_result(self, action, success, message):
        if action not in ("configure", "worker"):
            return
        self.buttons.setEnabled(True)
        if success:
            self.accept()
        else:
            self.error.setText(message)


class ReassignDialog(QDialog):
    def __init__(self, db, photo, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reassign photograph")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        note = QLabel("Select the student who is actually in this photograph. "
                      "The capture session is retained and the new assignment must be confirmed.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.student = QComboBox()
        self.student.setAccessibleName("Assign photograph to student")
        self.student.addItem("Choose student…", None)
        for row in db.students():
            self.student.addItem(f"{row['student_id']} · {row['full_name']} · {row['class_name']}", row["id"])
        index = self.student.findData(photo["student_record_id"])
        self.student.setCurrentIndex(max(index, 0))
        layout.addWidget(self.student)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        save = buttons.button(QDialogButtonBox.Save)
        save.setText("Reassign photograph")
        save.setEnabled(self.student.currentData() is not None)
        self.student.currentIndexChanged.connect(lambda: save.setEnabled(self.student.currentData() is not None))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
