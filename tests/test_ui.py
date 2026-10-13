from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from student_headshot_manager.ui.dialogs import ClassDialog, StudentDialog
from student_headshot_manager.ui.import_dialog import ImportDialog
from student_headshot_manager.ui.main_window import MainWindow
from student_headshot_manager.roster_import import CsvData


def test_window_empty_state_navigation_and_shortcut(qapp, db):
    window = MainWindow(db)
    window.show()
    qapp.processEvents()
    assert window.table.rowCount() == 0
    assert not window.add_button.isEnabled()
    assert not window.edit_button.isEnabled()
    assert window.empty.isVisible()
    for index in range(4):
        QTest.mouseClick(window.nav_group.button(index), Qt.LeftButton)
        assert window.pages.currentIndex() == index
    window.activateWindow()
    QTest.keyClick(window, Qt.Key_F, Qt.ControlModifier)
    qapp.processEvents()
    assert window.pages.currentIndex() == 0
    assert window.search.hasFocus()
    window.close()


def test_editors_validate_and_persist(qapp, db):
    editor = ClassDialog(db)
    editor.save()
    assert editor.error.text()
    editor.name.setText("7A")
    editor.save()
    assert editor.result() == QDialog.Accepted
    class_id = editor.saved_id
    student = StudentDialog(db, class_id=class_id)
    student.student_id.setText("0001")
    student.full_name.setText("Test Student")
    student.save()
    assert student.result() == QDialog.Accepted
    edit = StudentDialog(db, record=db.student(student.saved_id))
    edit.status.setCurrentText("Needs review")
    edit.full_name.setText("Changed Name")
    edit.save()
    assert db.student(student.saved_id)["status"] == "Needs review"
    assert db.student(student.saved_id)["full_name"] == "Changed Name"


def test_roster_filters_sort_selection_and_delete(qapp, db, monkeypatch):
    a, b = db.create_class("A"), db.create_class("B")
    first = db.save_student("1", "Zoe", a, "Complete")
    db.save_student("2", "Adam", b)
    window = MainWindow(db)
    window.show()
    assert window.table.rowCount() == 2
    window.table.sortItems(1, Qt.DescendingOrder)
    window.table.selectRow(0)
    assert window.selected_student_id() == first
    assert window.edit_button.isEnabled()
    window.search.setText("adam")
    assert window.table.rowCount() == 1
    window.search.clear()
    window.status_filter.setCurrentText("Complete")
    assert window.table.rowCount() == 1
    window.table.selectRow(0)
    monkeypatch.setattr(window, "confirm_delete", lambda *args: False)
    window.delete_student()
    assert db.student(first)
    monkeypatch.setattr(window, "confirm_delete", lambda *args: True)
    window.delete_student()
    assert db.student(first) is None
    window.status_filter.setCurrentIndex(0)
    window.refresh_classes(select_id=b)
    assert window.table.rowCount() == 1
    window.delete_class()
    assert db.students() == []
    window.close()


def test_import_dialog_blocks_invalid_and_imports_valid(qapp, db):
    data = CsvData(("Student ID", "Full name", "Class"), (("1", "Name", "7A"),))
    dialog = ImportDialog(db, data, "test.csv")
    assert dialog.table.rowCount() == 1
    assert dialog.import_button.isEnabled()
    dialog.choices["Class"].setCurrentIndex(0)
    assert not dialog.import_button.isEnabled()
    dialog.choices["Class"].setCurrentIndex(3)
    dialog.run_import()
    assert dialog.result() == QDialog.Accepted
    assert dialog.imported_count == 1
    duplicate = ImportDialog(db, data, "duplicate.csv")
    assert not duplicate.import_button.isEnabled()
    assert "already exists" in duplicate.table.item(0, 4).text()
    duplicate.reject()
    assert len(db.students()) == 1
