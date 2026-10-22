from pathlib import Path
import time

from PIL import Image
from PySide6.QtCore import Qt

from student_headshot_manager.capture_repository import CaptureRepository
from student_headshot_manager.folders import Folders
from student_headshot_manager.ingestion import IngestionEngine
from student_headshot_manager.ui.main_window import MainWindow


def wait_for(qapp, predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    assert predicate()


def test_capture_selection_thumbnail_counts_and_confirm(qapp, db, tmp_path):
    repo = CaptureRepository(db)
    class_id = db.create_class("A")
    first = db.save_student("001", "First Student", class_id)
    second = db.save_student("002", "Second Student", class_id)
    session = repo.create_session("Morning")
    folders = Folders(*(str(tmp_path / name) for name in ("in", "orig", "processed", "export")))
    Path(folders.incoming).mkdir()
    folders.validate()
    repo.set_settings("folders", folders.as_dict())
    clock = [0.0]
    engine = IngestionEngine(repo, folders, clock=lambda: clock[0])
    window = MainWindow(db)
    panel = window.capture_page
    try:
        panel.refresh_sessions(session)
        for index, color in enumerate(("red", "blue"), start=1):
            path = Path(folders.incoming) / f"{index}.jpg"
            Image.new("RGB", (100, 70), color).save(path)
            engine.discover(path, repo.unassigned(session))
        clock[0] = 3
        engine.tick()
        panel.refresh_photos()
        assert panel.table.rowCount() == 2
        assert not panel.table.item(0, 0).icon().isNull()
        window.refresh_classes()
        assert "2 unassigned" in panel.photo_count.text()
        panel.table.selectRow(0)
        student_row = next(row for row in range(panel.student_table.rowCount())
                           if panel.student_table.item(row, 0).data(Qt.UserRole) == first)
        panel.student_table.selectRow(student_row)
        assert panel.assign_button.isEnabled()
        photo_id = panel.selected_photo_ids()[0]
        panel.assign_selected()
        wait_for(qapp, lambda: repo.get(photo_id)["student_record_id"] == first and not panel.busy)
        panel.table.selectRow(next(row for row in range(panel.table.rowCount())
                                   if panel.table.item(row, 0).data(Qt.UserRole) == photo_id))
        assert panel.confirm_button.isEnabled()
        panel.confirm_selected()
        assert not panel.confirm_button.isEnabled()
        wait_for(qapp, lambda: repo.get(photo_id)["processing_status"] == "imported" and not panel.busy)
        assert "1 archived" in panel.photo_count.text()
        assert not panel.confirm_button.isEnabled()
        assert panel.discard_button.isEnabled()
        assert not panel.retry_button.isEnabled()
        db.delete_student(first)
        window.refresh_classes()
        assert panel.selected_student_id() != first
    finally:
        panel.controller.stop()
        wait_for(qapp, lambda: not panel.controller.is_running())
        window.close()


def test_capture_failure_is_visible_with_retry_control(qapp, db, tmp_path):
    repo = CaptureRepository(db)
    student = db.save_student("A1", "Test Student", db.create_class("A"))
    session = repo.create_session("Test")
    incoming = tmp_path / "invalid.jpg"
    incoming.write_bytes(b"incomplete")
    photo = repo.begin_receiving(incoming, str(incoming), (10, incoming.stat().st_mtime_ns), repo.assignment(student, session))
    repo.fail(photo, "Image is incomplete. Retry when transfer has finished.")
    window = MainWindow(db)
    panel = window.capture_page
    panel.refresh_sessions(session)
    panel.table.selectRow(0)
    assert "incomplete" in panel.detail.text()
    assert panel.retry_button.isEnabled()
    assert not panel.confirm_button.isEnabled()
    assert "1 of 1 photos shown" in panel.photo_count.text()
    window.close()


def test_photo_search_class_filter_and_sort(qapp, db, tmp_path):
    repo = CaptureRepository(db)
    class_a = db.create_class("A class")
    class_b = db.create_class("B class")
    first = db.save_student("A-01", "Zara Student", class_a)
    second = db.save_student("B-01", "Adam Student", class_b)
    session = repo.create_session("Sorting")
    for index, (filename, student) in enumerate((("z-last.jpg", first), ("a-first.jpg", second)), 1):
        path = tmp_path / filename
        path.write_bytes(b"pending")
        repo.begin_receiving(path, str(path), (index, index), repo.assignment(student, session))
    window = MainWindow(db)
    panel = window.capture_page
    try:
        panel.refresh_sessions(session)
        assert panel.table.rowCount() == 2

        panel.photo_class_filter.setCurrentIndex(panel.photo_class_filter.findData(class_b))
        assert panel.table.rowCount() == 1
        assert "1 of 2 photos shown" in panel.photo_count.text()
        assert panel.table.item(0, 1).text() == "a-first.jpg"

        panel.photo_class_filter.setCurrentIndex(0)
        panel.photo_search.setText("Zara")
        assert panel.table.rowCount() == 1
        assert panel.table.item(0, 1).text() == "z-last.jpg"

        panel.photo_search.clear()
        panel.photo_sort.setCurrentIndex(panel.photo_sort.findData("file"))
        assert [panel.table.item(row, 1).text() for row in range(2)] == ["a-first.jpg", "z-last.jpg"]

        panel.photo_sort.setCurrentIndex(panel.photo_sort.findData("class"))
        assert [panel.table.item(row, 1).text() for row in range(2)] == ["z-last.jpg", "a-first.jpg"]
    finally:
        panel.controller.stop()
        wait_for(qapp, lambda: not panel.controller.is_running())
        window.close()
