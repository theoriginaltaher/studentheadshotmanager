from pathlib import Path
import time
import threading

from PIL import Image
from PySide6.QtCore import QTimer

from student_headshot_manager.capture_repository import CaptureRepository
from student_headshot_manager.capture_worker import CaptureController
from student_headshot_manager.folders import Folders


def wait_until(qapp, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    assert predicate(), "Timed out waiting for background ingestion"


def test_real_watchdog_pause_resume_snapshot_and_background_copy(qapp, db, tmp_path, monkeypatch):
    repo = CaptureRepository(db)
    class_id = db.create_class("A")
    first = db.save_student("001", "First", class_id)
    second = db.save_student("002", "Second", class_id)
    session = repo.create_session("Live test")
    folders = Folders(*(str(tmp_path / name) for name in ("in", "originals", "processed", "export")))
    Path(folders.incoming).mkdir()
    folders.validate()
    repo.set_settings("folders", folders.as_dict())
    controller = CaptureController(db.path)
    results, statuses = [], []
    controller.result.connect(lambda *args: results.append(args))
    controller.status.connect(lambda *args: statuses.append(args))
    controller.set_assignment(repo.assignment(first, session))
    try:
        controller.command("resume")
        wait_until(qapp, lambda: any(watching for _, watching in statuses))
        incoming = Path(folders.incoming) / "live.jpg"
        Image.new("RGB", (120, 80), "red").save(incoming)
        wait_until(qapp, lambda: bool(repo.photos()))
        # Change the selection after detection, but before file settling finishes.
        controller.set_assignment(repo.assignment(second, session))
        wait_until(qapp, lambda: repo.photos()[0]["processing_status"] == "awaiting_confirmation")
        photo = repo.photos()[0]
        assert photo["student_record_id"] == first
        import student_headshot_manager.ingestion as ingestion
        original_copy = ingestion.copy_verified
        copy_entered, copy_continue = threading.Event(), threading.Event()

        def deliberately_slow_copy(*args):
            copy_entered.set()
            assert copy_continue.wait(timeout=5), "GUI did not remain responsive during copy"
            return original_copy(*args)

        monkeypatch.setattr(ingestion, "copy_verified", deliberately_slow_copy)
        controller.command("confirm", photo["id"])
        wait_until(qapp, copy_entered.is_set)
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        wait_until(qapp, lambda: bool(heartbeat))
        copy_continue.set()
        wait_until(qapp, lambda: repo.get(photo["id"])["processing_status"] == "imported")
        assert Path(repo.get(photo["id"])["original_copy_path"]).exists()
        controller.command("pause")
        wait_until(qapp, lambda: any(action == "pause" and ok for action, ok, _ in results))
        paused = Path(folders.incoming) / "while-paused.jpeg"
        Image.new("RGB", (120, 80), "blue").save(paused)
        time.sleep(0.2)
        qapp.processEvents()
        assert len(repo.photos()) == 1
        controller.command("resume")
        wait_until(qapp, lambda: len(repo.photos()) == 2)
        wait_until(qapp, lambda: repo.photos()[0]["processing_status"] == "awaiting_confirmation")
        assert repo.photos()[0]["student_record_id"] == second
        assert incoming.exists() and paused.exists()
    finally:
        controller.stop()
        wait_until(qapp, lambda: not controller.is_running())


def test_worker_errors_recover_and_only_one_owner(qapp, db, tmp_path):
    repo = CaptureRepository(db)
    student = db.save_student("1", "One", db.create_class("A"))
    session = repo.create_session("Test")
    first, other = CaptureController(db.path), CaptureController(db.path)
    results, other_results = [], []
    first.result.connect(lambda *args: results.append(args))
    other.result.connect(lambda *args: other_results.append(args))
    first.set_assignment(repo.assignment(student, session))
    try:
        first.command("resume")
        wait_until(qapp, lambda: bool(results))
        assert not results[-1][1] and "Configure" in results[-1][2]
        other.command("initialize")
        wait_until(qapp, lambda: bool(other_results))
        assert not other_results[-1][1]
        assert "Another application instance" in other_results[-1][2]
        paths = {role: str(tmp_path / role) for role in ("incoming", "originals", "processed", "export")}
        Path(paths["incoming"]).mkdir()
        first.command("configure", paths)
        wait_until(qapp, lambda: any(action == "configure" and ok for action, ok, _ in results))
        first.command("resume")
        wait_until(qapp, lambda: any(action == "resume" and ok for action, ok, _ in results))
    finally:
        first.stop()
        other.stop()
        wait_until(qapp, lambda: not first.is_running() and not other.is_running())
