from dataclasses import asdict, replace
import threading
import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QInputDialog

from student_headshot_manager import processing
from student_headshot_manager.processing import ProcessingSettings
from student_headshot_manager.processing_repository import ProcessingRepository
from student_headshot_manager.processing_worker import ProcessingController
from student_headshot_manager.ui.image_view import ImageView
from student_headshot_manager.ui.main_window import MainWindow


def wait_for(qapp, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(.02)
    assert predicate(), "Timed out waiting for local processing"


def test_worker_keeps_gui_responsive_and_returns_native_mask(qapp, db, processing_capture, monkeypatch):
    capture_id, *_ = processing_capture
    controller = ProcessingController(db.path)
    results = []
    controller.done.connect(lambda *args: results.append(args))
    entered, proceed = threading.Event(), threading.Event()
    real_process = processing.process_bytes

    def slow_process(*args):
        entered.set()
        assert proceed.wait(5), "GUI could not release the background worker"
        return real_process(*args)

    monkeypatch.setattr(processing, "process_bytes", slow_process)
    try:
        settings = replace(ProcessingSettings(), canvas_width=120, canvas_height=160)
        controller.command("process", 1, capture_id, asdict(settings))
        wait_for(qapp, entered.is_set)
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        wait_for(qapp, lambda: bool(heartbeat))
        proceed.set()
        wait_for(qapp, lambda: bool(results))
        action, token, success, payload = results[-1]
        assert action == "process" and token == 1 and success
        assert payload["mask"].size().width() == 120
        assert payload["mask"].size().height() == 160
        assert payload["job"]["status"] == "awaiting_review"
        assert not payload["portrait"].isNull()
    finally:
        proceed.set()
        controller.stop()
        wait_for(qapp, lambda: not controller.is_running())


def test_review_controls_mask_zoom_flags_and_dirty_settings(qapp, db, processing_capture, monkeypatch):
    capture_id, *_ = processing_capture
    window = MainWindow(db)
    page = window.review_page
    try:
        assert page.photos.currentData() is None
        assert not page.process_button.isEnabled()
        page.settings_panel.set_values(replace(ProcessingSettings(), canvas_width=120, canvas_height=160))
        page.photos.setCurrentIndex(page.photos.findData(capture_id))
        wait_for(qapp, lambda: not page.loading)
        assert page.before.canvas.has_image
        assert page.process_button.isEnabled()
        assert not page.approve_button.isEnabled()
        page.process_button.click()
        wait_for(qapp, lambda: not page.busy)
        assert page.current_job and page.after.canvas.has_image and page.mask.canvas.has_image
        assert page.approve_button.isEnabled()
        page.mask.canvas.actual_size()
        assert page.mask.canvas.transform().m11() == 1
        page.mask.canvas.zoom(2)
        assert page.mask.canvas.transform().m11() == 2
        monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *args, **kwargs: ("Inspect hair edges", True))
        page.flag_button.click()
        wait_for(qapp, lambda: not page.busy)
        assert ProcessingRepository(db).review(capture_id)["manual_review"] == 1
        assert "manual review" in page.status.text()
        page.settings_panel.fields["grey"].setValue(160)
        assert page.current_job is None
        assert not page.after.canvas.has_image
        assert not page.approve_button.isEnabled()
        assert page.process_button.isEnabled()
        page.settings_panel.set_values(ProcessingSettings(), notify=True)
        assert page.settings_panel.settings() == ProcessingSettings()
    finally:
        page.controller.stop()
        window.capture_page.controller.stop()
        wait_for(qapp, lambda: not page.controller.is_running())
        window.close()


def test_late_result_cannot_replace_preview_for_changed_settings(qapp, db, processing_capture, monkeypatch):
    capture_id, *_ = processing_capture
    window = MainWindow(db)
    page = window.review_page
    entered, proceed = threading.Event(), threading.Event()
    real_process = processing.process_bytes

    def slow_process(*args):
        entered.set()
        assert proceed.wait(5)
        return real_process(*args)

    monkeypatch.setattr(processing, "process_bytes", slow_process)
    try:
        page.settings_panel.set_values(replace(ProcessingSettings(), canvas_width=120, canvas_height=160))
        page.photos.setCurrentIndex(page.photos.findData(capture_id))
        wait_for(qapp, lambda: not page.loading)
        page.process()
        wait_for(qapp, entered.is_set)
        page.settings_panel.fields["grey"].setValue(120)
        proceed.set()
        wait_for(qapp, lambda: not page.busy)
        assert page.current_job is None and not page.after.canvas.has_image
        assert page.settings_panel.settings().grey == 120
        assert not page.approve_button.isEnabled()
        assert ProcessingRepository(db).latest(capture_id)["status"] == "awaiting_review"
    finally:
        proceed.set()
        page.controller.stop()
        wait_for(qapp, lambda: not page.controller.is_running())
        window.close()
