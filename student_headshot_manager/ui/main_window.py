"""Main workstation window and roster interactions."""

from pathlib import Path
import sys
import sqlite3

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QKeySequence, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSplitter, QStackedWidget,
    QStyle, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from ..database import STATUSES
from ..roster_import import read_csv
from .dialogs import ClassDialog, StudentDialog
from .import_dialog import ImportDialog
from .capture_page import CapturePage
from .review_page import ReviewPage
from .review_workspace import ReviewWorkspace
from .export_page import ExportPage
from ..maintenance import VERSION


AUTHOR = "Taher Hussain"
BYTHC_URL = "https://www.linkedin.com/company/taherhussaincreations/"


def label(text, name=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    if name:
        widget.setObjectName(name)
    return widget


def button(text, callback, primary=False):
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(callback)
    return widget


def icon_button(parent, icon, tooltip, callback):
    widget = QToolButton()
    widget.setIcon(parent.style().standardIcon(icon))
    widget.setToolTip(tooltip)
    widget.setAccessibleName(tooltip)
    widget.clicked.connect(callback)
    return widget


class MainWindow(QMainWindow):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.setWindowTitle("Student Headshot Manager")
        self.resize(1240, 780)
        self.setMinimumSize(1000, 640)
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.setCentralWidget(root)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(190)
        nav_layout = QVBoxLayout(sidebar)
        nav_layout.setContentsMargins(16, 28, 16, 22)
        nav_layout.setSpacing(8)
        nav_layout.addWidget(label("Student\nHeadshot Manager", "section"))
        nav_layout.addSpacing(28)
        self.pages = QStackedWidget()
        self.nav_group = QButtonGroup(self)
        for index, title in enumerate(("Classes", "Assign", "Review", "Export")):
            nav = QPushButton(title)
            nav.setObjectName("nav")
            nav.setCheckable(True)
            self.nav_group.addButton(nav, index)
            nav_layout.addWidget(nav)
        self.nav_group.button(0).setChecked(True)
        self.nav_group.idClicked.connect(self.pages.setCurrentIndex)
        nav_layout.addStretch()
        nav_layout.addWidget(label("Roster & headshots\nLocal workspace", "muted"))
        outer.addWidget(sidebar)
        outer.addWidget(self.pages, 1)
        self.pages.addWidget(self.build_roster())
        self.capture_page = CapturePage(db, self)
        self.capture_page.roster_changed.connect(self.refresh_classes)
        self.pages.addWidget(self.capture_page)
        self.review_page = ReviewPage(db, self)
        self.capture_page.roster_changed.connect(self.review_page.refresh_photos)
        self.review_workspace = ReviewWorkspace(db, self.review_page, self)
        self.capture_page.roster_changed.connect(self.review_workspace.browser.refresh)
        self.pages.addWidget(self.review_workspace)
        self.export_page = ExportPage(db, self)
        self.pages.addWidget(self.export_page)
        self.make_shortcuts()
        support=self.menuBar().addMenu("Data & help")
        backup=QAction("Back up database…",self);backup.triggered.connect(self.backup_roster);support.addAction(backup)
        from ..maintenance import data_directory
        for title,folder in (("Open data folder",Path(db.path).parent),("Open logs",data_directory()/"Logs")):
            action=QAction(title,self);action.triggered.connect(lambda checked=False,p=folder:QDesktopServices.openUrl(QUrl.fromLocalFile(str(p))))
            support.addAction(action)
        about=QAction("About Student Headshot Manager",self);about.triggered.connect(self.show_about);support.addAction(about)
        self.statusBar().showMessage("Ready · All changes are saved automatically")
        location = label("Local SQLite storage", "muted")
        location.setToolTip(str(Path(db.path).absolute()))
        self.statusBar().addPermanentWidget(location)
        self.refresh_classes()

    def show_about(self):
        dialog=QDialog(self)
        dialog.setWindowTitle("About Student Headshot Manager")
        layout=QVBoxLayout(dialog)
        layout.setContentsMargins(24,24,24,18)
        layout.setSpacing(14)
        assets=Path(getattr(sys,"_MEIPASS",Path(__file__).resolve().parents[2]))/"assets"
        wordmark=QPixmap(str(assets/"brand-wordmark.png"))
        if not wordmark.isNull():
            image=QLabel()
            image.setPixmap(wordmark.scaled(520,180,Qt.KeepAspectRatio,Qt.SmoothTransformation))
            layout.addWidget(image)
        title=QLabel(f"<h2>Student Headshot Manager</h2><p>Version {VERSION}</p>")
        title.setTextFormat(Qt.RichText)
        layout.addWidget(title)
        details=QLabel(
            "Local batch headshot assignment and processing workstation<br><br>"
            f"Author: {AUTHOR}<br>"
            f"<a href=\"{BYTHC_URL}\">#ByTHC</a><br><br>"
            "Import JPEG or supported RAW photographs from an SD card or local folder. "
            "See CAMERA_SETUP and the user guide. Logs and backups stay in your user data folder."
        )
        details.setTextFormat(Qt.RichText)
        details.setTextInteractionFlags(Qt.TextBrowserInteraction)
        details.setOpenExternalLinks(True)
        details.setWordWrap(True)
        layout.addWidget(details)
        buttons=QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(560,360)
        dialog.exec()

    def backup_roster(self):
        folder=QFileDialog.getExistingDirectory(self,"Choose database backup folder",str(Path(self.db.path).parent))
        if not folder:return
        try:
            from ..maintenance import backup_database
            saved=backup_database(self.db.path,folder)
            QMessageBox.information(self,"Database backed up",f"Saved: {saved}\n\nBack up Originals and Processed separately; photographs are not embedded in SQLite.")
        except (OSError,ValueError,sqlite3.Error) as exc:
            QMessageBox.critical(self,"Backup failed",str(exc))

    def build_roster(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 28, 28, 20)
        layout.setSpacing(18)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.addWidget(label("Class rosters", "title"))
        heading.addWidget(label("Prepare your students for photography day.", "muted"))
        header.addLayout(heading)
        header.addStretch()
        header.addWidget(button("Import CSV", self.choose_import))
        self.add_button = button("Add student", self.add_student, True)
        header.addWidget(self.add_button)
        layout.addLayout(header)
        split = QSplitter(Qt.Horizontal)
        class_panel = QWidget()
        class_panel.setMinimumWidth(205)
        class_layout = QVBoxLayout(class_panel)
        class_layout.setContentsMargins(0, 0, 14, 0)
        class_layout.setSpacing(10)
        class_layout.addWidget(label("Classes", "section"))
        class_layout.addWidget(button("New class", self.add_class))
        self.class_list = QListWidget()
        self.class_list.setAccessibleName("Classes, select to filter roster")
        self.class_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.class_list.currentItemChanged.connect(self.refresh_students)
        self.class_list.itemSelectionChanged.connect(self.update_class_actions)
        class_layout.addWidget(self.class_list, 1)
        order_actions = QHBoxLayout()
        order_actions.addWidget(label("Order", "muted"))
        order_actions.addStretch()
        self.move_class_up_button = icon_button(
            self, QStyle.SP_ArrowUp, "Move selected classes up", lambda: self.move_classes(-1)
        )
        self.move_class_down_button = icon_button(
            self, QStyle.SP_ArrowDown, "Move selected classes down", lambda: self.move_classes(1)
        )
        order_actions.addWidget(self.move_class_up_button)
        order_actions.addWidget(self.move_class_down_button)
        class_layout.addLayout(order_actions)
        class_actions = QHBoxLayout()
        self.edit_class_button = button("Rename", self.edit_class)
        self.delete_class_button = button("Delete", self.delete_class)
        class_actions.addWidget(self.edit_class_button)
        class_actions.addWidget(self.delete_class_button)
        class_layout.addLayout(class_actions)
        split.addWidget(class_panel)
        roster = QWidget()
        roster.setMinimumWidth(500)
        roster_layout = QVBoxLayout(roster)
        roster_layout.setContentsMargins(14, 0, 0, 0)
        roster_layout.setSpacing(12)
        self.roster_title = label("All students", "section")
        roster_layout.addWidget(self.roster_title)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name, student ID, or filename")
        self.search.setAccessibleName("Search students by name, ID, or filename")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh_students)
        self.status_filter = QComboBox()
        self.status_filter.setAccessibleName("Filter photography status")
        self.status_filter.addItem("All statuses", None)
        for status in STATUSES:
            self.status_filter.addItem(status, status)
        self.status_filter.currentIndexChanged.connect(self.refresh_students)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.status_filter)
        roster_layout.addLayout(filters)
        self.counts = label("", "muted")
        self.counts.setWordWrap(True)
        roster_layout.addWidget(self.counts)
        self.table = QTableWidget(0, 5)
        self.table.setAccessibleName("Student roster")
        self.table.setHorizontalHeaderLabels(["Student ID", "Full name", "Class", "Filename", "Photography status"])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setColumnWidth(0, 120)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 260)
        self.table.setColumnWidth(4, 160)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.sortItems(1, Qt.AscendingOrder)
        self.table.itemSelectionChanged.connect(self.update_student_actions)
        self.table.itemDoubleClicked.connect(self.edit_student)
        roster_layout.addWidget(self.table, 1)
        self.empty = label("")
        self.empty.setWordWrap(True)
        roster_layout.addWidget(self.empty)
        footer = QHBoxLayout()
        self.shown = label("", "muted")
        footer.addWidget(self.shown)
        footer.addStretch()
        self.edit_button = button("Edit student", self.edit_student)
        self.delete_button = button("Delete", self.delete_student)
        footer.addWidget(self.edit_button)
        footer.addWidget(self.delete_button)
        roster_layout.addLayout(footer)
        split.addWidget(roster)
        split.setChildrenCollapsible(False)
        split.setSizes([235, 740])
        layout.addWidget(split, 1)
        return page

    def make_shortcuts(self):
        menu = self.menuBar().addMenu("&Roster")
        for text, key, callback in (
            ("New class", "Ctrl+Shift+N", self.add_class),
            ("Add student", "Ctrl+N", self.add_student),
            ("Import CSV", "Ctrl+I", self.choose_import),
            ("Search students", "Ctrl+F", self.focus_search),
            ("Edit selected student", "F2", self.edit_student),
            ("Delete selected student", "Ctrl+Delete", self.delete_student),
            ("Refresh roster", "F5", self.refresh_classes),
            ("Quit", "Ctrl+Q", self.close),
        ):
            action = QAction(text, self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(callback)
            menu.addAction(action)
        for index in range(4):
            action = QAction(self)
            action.setShortcut(QKeySequence(f"Alt+{index + 1}"))
            action.triggered.connect(lambda checked=False, i=index: self.navigate(i))
            self.addAction(action)

    def navigate(self, index):
        self.pages.setCurrentIndex(index)
        self.nav_group.button(index).setChecked(True)

    def focus_search(self):
        self.navigate(0)
        self.search.setFocus()
        self.search.selectAll()

    def current_class_id(self):
        item = self.class_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def selected_class_ids(self):
        return [item.data(Qt.UserRole) for item in self.class_list.selectedItems()
                if item.data(Qt.UserRole) is not None]

    def selected_student_id(self):
        selected = self.selected_student_ids()
        return selected[0] if len(selected) == 1 else None

    def selected_student_ids(self):
        ids = []
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 0)
            if item:
                ids.append(item.data(Qt.UserRole))
        return ids

    def refresh_classes(self, checked=False, select_id=None, select_ids=None):
        selected = select_id if select_id is not None else self.current_class_id()
        selected_ids = set(select_ids if select_ids is not None else self.selected_class_ids())
        if select_id is not None:
            selected_ids = {select_id}
        self.class_list.blockSignals(True)
        self.class_list.clear()
        classes = self.db.classes()
        all_item = QListWidgetItem(f"All students  ({sum(row['student_count'] for row in classes)})")
        self.class_list.addItem(all_item)
        selected_item = all_item
        for row in classes:
            item = QListWidgetItem(f"{row['name']}  ({row['student_count']})")
            item.setData(Qt.UserRole, row["id"])
            item.setToolTip(row["name"])
            self.class_list.addItem(item)
            if row["id"] == selected:
                selected_item = item
        self.class_list.clearSelection()
        self.class_list.setCurrentItem(selected_item)
        for index in range(1, self.class_list.count()):
            item = self.class_list.item(index)
            if item.data(Qt.UserRole) in selected_ids:
                item.setSelected(True)
        self.class_list.blockSignals(False)
        self.add_button.setEnabled(bool(classes))
        self.update_class_actions()
        self.refresh_students()
        if hasattr(self, "capture_page"):
            self.capture_page.refresh_roster()

    def refresh_students(self, *args):
        selected = set(self.selected_student_ids())
        class_id = self.current_class_id()
        classes = {row["id"]: row for row in self.db.classes()}
        self.roster_title.setText(classes[class_id]["name"] if class_id in classes else "All students")
        all_rows = self.db.students(class_id)
        rows = self.db.students(class_id, self.search.text(), self.status_filter.currentData())
        self.counts.setText("  ·  ".join(f"{sum(row['status'] == status for row in all_rows)} {status.lower()}" for status in STATUSES))
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, field in enumerate(("student_id", "full_name", "class_name", "export_filename", "status")):
                item = QTableWidgetItem(row[field])
                item.setToolTip(row[field])
                if j == 0:
                    item.setData(Qt.UserRole, row["id"])
                self.table.setItem(i, j, item)
        self.table.setSortingEnabled(True)
        if selected:
            for i in range(self.table.rowCount()):
                if self.table.item(i, 0).data(Qt.UserRole) in selected:
                    index = self.table.model().index(i, 0)
                    self.table.selectionModel().select(
                        index, QItemSelectionModel.Select | QItemSelectionModel.Rows
                    )
        self.shown.setText(f"{len(rows):,} of {len(all_rows):,} students")
        if not classes:
            self.empty.setText("Start with New class or Import CSV to build your first roster.")
        elif not all_rows:
            self.empty.setText("This roster is empty. Add a student or import a CSV.")
        else:
            self.empty.setText("No students match. Clear the search or choose All statuses.")
        self.empty.setVisible(not rows)
        self.update_student_actions()

    def update_student_actions(self):
        count = len(self.selected_student_ids())
        self.edit_button.setEnabled(count == 1)
        self.delete_button.setEnabled(count > 0)
        self.delete_button.setText(f"Delete {count} students" if count > 1 else "Delete")

    def update_class_actions(self):
        selected = set(self.selected_class_ids())
        ordered = [row["id"] for row in self.db.classes()]
        positions = [ordered.index(class_id) for class_id in selected if class_id in ordered]
        self.edit_class_button.setEnabled(len(selected) == 1)
        self.delete_class_button.setEnabled(bool(selected))
        self.delete_class_button.setText(f"Delete {len(selected)}" if len(selected) > 1 else "Delete")
        self.move_class_up_button.setEnabled(bool(positions) and min(positions) > 0)
        self.move_class_down_button.setEnabled(bool(positions) and max(positions) < len(ordered) - 1)

    def move_classes(self, direction):
        selected = set(self.selected_class_ids())
        order = [row["id"] for row in self.db.classes()]
        if not selected or direction not in (-1, 1):
            return
        indexes = range(1, len(order)) if direction < 0 else range(len(order) - 2, -1, -1)
        for index in indexes:
            neighbor = index - 1 if direction < 0 else index + 1
            if order[index] in selected and order[neighbor] not in selected:
                order[index], order[neighbor] = order[neighbor], order[index]
        try:
            self.db.reorder_classes(order)
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Unable to reorder classes", str(exc))
        else:
            self.refresh_classes(select_ids=selected)
            self.statusBar().showMessage("Class order saved.", 5000)

    def add_class(self):
        self.navigate(0)
        dialog = ClassDialog(self.db, self)
        if dialog.exec() == QDialog.Accepted:
            self.refresh_classes(select_id=dialog.saved_id)
            self.statusBar().showMessage("Class created and saved.", 5000)

    def edit_class(self):
        class_id = self.current_class_id()
        record = next((row for row in self.db.classes() if row["id"] == class_id), None)
        if record and ClassDialog(self.db, self, record).exec() == QDialog.Accepted:
            self.refresh_classes()

    def confirm_delete(self, title, message):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Warning)
        box.setTextFormat(Qt.PlainText)
        box.setText(message)
        delete = box.addButton(title, QMessageBox.DestructiveRole)
        cancel = box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() == delete

    def delete_class(self):
        selected = set(self.selected_class_ids())
        records = [row for row in self.db.classes() if row["id"] in selected]
        if not records:
            return
        students = sum(row["student_count"] for row in records)
        if len(records) == 1:
            title = "Delete class"
            message = f"Delete {records[0]['name']} and its {students} student(s)? This cannot be undone."
        else:
            title = "Delete classes"
            message = f"Delete {len(records)} selected classes and their {students} student(s)? This cannot be undone."
        if self.confirm_delete(title, message):
            self.mutate(lambda: self.db.delete_classes([row["id"] for row in records]),
                        f"Deleted {len(records)} class(es) and {students} student(s).")

    def add_student(self):
        self.navigate(0)
        if not self.db.classes():
            QMessageBox.information(self, "Create a class first", "Use New class or Import CSV before adding students.")
            return
        dialog = StudentDialog(self.db, self, class_id=self.current_class_id())
        if dialog.exec() == QDialog.Accepted:
            self.refresh_classes()
            self.statusBar().showMessage("Student saved. Active filters may hide this student.", 5000)

    def edit_student(self, *args):
        if self.pages.currentIndex() != 0:
            return
        record_id = self.selected_student_id()
        record = self.db.student(record_id) if record_id is not None else None
        if record and StudentDialog(self.db, self, record).exec() == QDialog.Accepted:
            self.refresh_classes()
            self.statusBar().showMessage("Student changes saved.", 5000)

    def delete_student(self):
        if self.pages.currentIndex() != 0:
            return
        record_ids = self.selected_student_ids()
        records = [self.db.student(record_id) for record_id in record_ids]
        records = [record for record in records if record]
        if not records:
            return
        if len(records) == 1:
            title = "Delete student"
            message = f"Delete {records[0]['full_name']} ({records[0]['student_id']})? This cannot be undone."
        else:
            title = "Delete students"
            message = f"Delete {len(records)} selected students? Their roster records will be removed. This cannot be undone."
        if self.confirm_delete(title, message):
            self.mutate(lambda: self.db.delete_students([record["id"] for record in records]),
                        f"Deleted {len(records)} student(s).")

    def mutate(self, operation, message):
        try:
            operation()
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Unable to save changes", str(exc))
        else:
            self.refresh_classes()
            self.statusBar().showMessage(message, 5000)

    def choose_import(self):
        self.navigate(0)
        filename, _ = QFileDialog.getOpenFileName(self, "Choose roster CSV", "", "CSV files (*.csv);;All files (*)")
        if not filename:
            return
        try:
            data = read_csv(filename)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Unable to read CSV", str(exc))
            return
        dialog = ImportDialog(self.db, data, Path(filename).name, self)
        if dialog.exec() == QDialog.Accepted:
            self.refresh_classes()
            self.statusBar().showMessage(f"Imported and saved {dialog.imported_count:,} students.", 8000)

    def closeEvent(self, event):
        controllers = (self.capture_page.controller, self.review_page.controller, self.export_page.controller)
        if any(controller.is_running() for controller in controllers):
            for controller in controllers:
                controller.stop()
            self.statusBar().showMessage("Finishing background file work before closing…")
            event.ignore()
            QTimer.singleShot(100, self.close)
        else:
            event.accept()
