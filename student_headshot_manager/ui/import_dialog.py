"""CSV mapping, row-by-row preview and atomic import."""

import sqlite3
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)
from ..roster_import import FIELDS, import_csv, suggest_mapping, validate
from ..database import identity


class ImportDialog(QDialog):
    def __init__(self, db, data, filename, parent=None):
        super().__init__(parent)
        self.db, self.data = db, data
        self.imported_count = 0
        self.setWindowTitle("Import class roster")
        self.resize(1060, 660)
        self.setMinimumSize(800, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        title = QLabel("Preview your roster")
        title.setObjectName("title")
        layout.addWidget(title)
        source = QLabel(f"{filename} · {len(data.rows):,} data rows")
        source.setTextFormat(Qt.PlainText)
        source.setObjectName("muted")
        layout.addWidget(source)
        note = QLabel("Map the four required fields. New classes will be created automatically. "
                      "Existing students are never overwritten. All rows must pass validation.")
        note.setWordWrap(True)
        layout.addWidget(note)
        mapping_layout = QHBoxLayout()
        self.choices = {}
        suggested = suggest_mapping(data)
        for field in FIELDS:
            column = QVBoxLayout()
            label = QLabel(field)
            choice = QComboBox()
            choice.setAccessibleName(f"CSV column for {field}")
            choice.addItem("Select column…", None)
            for i, header in enumerate(data.headers):
                choice.addItem(header, i)
            if suggested[field] is not None:
                choice.setCurrentIndex(suggested[field] + 1)
            label.setBuddy(choice)
            column.addWidget(label)
            column.addWidget(choice)
            mapping_layout.addLayout(column)
            self.choices[field] = choice
        layout.addLayout(mapping_layout)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["CSV row", "Student ID", "Full name", "Class", "Filename", "Validation"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().hide()
        self.table.setWordWrap(False)
        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 180)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 260)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.errors_only = QPushButton("Show only errors")
        self.errors_only.setCheckable(True)
        self.errors_only.toggled.connect(self.refresh)
        actions.addWidget(self.errors_only)
        actions.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.import_button = QPushButton("Import students")
        self.import_button.setObjectName("primary")
        self.import_button.clicked.connect(self.run_import)
        actions.addWidget(self.import_button)
        layout.addLayout(actions)
        for choice in self.choices.values():
            choice.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def mapping(self):
        return {field: choice.currentData() for field, choice in self.choices.items()}

    def refresh(self):
        self.import_button.setEnabled(False)
        self.table.setRowCount(0)
        try:
            rows = validate(self.data, self.mapping(), self.db)
        except (ValueError, sqlite3.Error) as exc:
            self.summary.setText(str(exc))
            return
        errors = sum(bool(row.errors) for row in rows)
        existing = {row["name_key"] for row in self.db.classes()}
        new_classes = len({identity(row.class_name) for row in rows if not row.errors} - existing)
        self.summary.setText(f"{len(rows) - errors:,} valid · {errors:,} with errors · {new_classes:,} new classes. "
                             + ("Fix the CSV and reopen it to resolve errors." if errors else "Ready to import."))
        self.import_button.setText(f"Import {len(rows):,} students")
        self.import_button.setEnabled(bool(rows) and not errors)
        visible = [row for row in rows if row.errors or not self.errors_only.isChecked()]
        self.table.setRowCount(len(visible))
        for i, row in enumerate(visible):
            for j, value in enumerate((str(row.line), row.student_id, row.full_name, row.class_name, row.export_filename,
                                       " ".join(row.errors) if row.errors else "Ready")):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(i, j, item)

    def run_import(self):
        try:
            self.imported_count = import_csv(self.data, self.mapping(), self.db)
        except (ValueError, sqlite3.Error) as exc:
            self.refresh()
            self.summary.setText(str(exc))
        else:
            self.accept()
