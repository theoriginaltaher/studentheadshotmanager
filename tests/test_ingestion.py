import hashlib
import io
import os
from pathlib import Path
import sqlite3

from PIL import Image
import pytest

from student_headshot_manager.capture_repository import CaptureRepository
from student_headshot_manager.database import Database
from student_headshot_manager.folders import Folders
from student_headshot_manager.ingestion import IngestionEngine, copy_verified, file_hash


def jpeg_bytes(color="red"):
    output = io.BytesIO()
    Image.new("RGB", (96, 64), color).save(output, "JPEG")
    return output.getvalue()


class Clock:
    now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds=3):
        self.now += seconds


@pytest.fixture
def capture_setup(db, tmp_path):
    repo = CaptureRepository(db)
    class_id = db.create_class("7A")
    student = db.save_student("0001", "First Student", class_id)
    other = db.save_student("0002", "Second Student", class_id)
    session = repo.create_session("Morning")
    folders = Folders(*(str(tmp_path / name) for name in ("Incoming", "Originals", "Processed", "Export")))
    Path(folders.incoming).mkdir()
    folders = folders.validate()
    clock = Clock()
    engine = IngestionEngine(repo, folders, clock=clock, settle_seconds=2, incomplete_timeout=10)
    return repo, engine, clock, folders, repo.assignment(student, session), repo.assignment(other, session)


def receive(setup, name="photo.jpg", data=None, assignment=None):
    repo, engine, clock, folders, first, _ = setup
    path = Path(folders.incoming) / name
    path.write_bytes(data if data is not None else jpeg_bytes())
    assert engine.discover(path, assignment or first)
    clock.advance()
    engine.tick()
    return path, repo.photos()[0]


def test_incomplete_and_growing_files_wait_for_complete_stable_jpeg(capture_setup):
    repo, engine, clock, folders, first, second = capture_setup
    path = Path(folders.incoming) / "partial.JPG"
    complete = jpeg_bytes()
    path.write_bytes(complete[:200])
    assert engine.discover(path, first)
    clock.advance()
    engine.tick()
    assert repo.photos()[0]["processing_status"] == "receiving"
    assert repo.photos()[0]["thumbnail"] is None
    path.write_bytes(complete)
    # A second event or a change of active student cannot steal this assignment.
    assert not engine.discover(path, second)
    engine.tick()
    assert repo.photos()[0]["processing_status"] == "receiving"
    clock.advance(1)
    engine.tick()
    assert repo.photos()[0]["processing_status"] == "receiving"
    clock.advance(2)
    engine.tick()
    row = repo.photos()[0]
    assert row["processing_status"] == "awaiting_confirmation"
    assert row["student_id"] == first.student_id
    assert row["session_id"] == first.session_id
    assert row["thumbnail"] and row["capture_time"]
    assert path.read_bytes() == complete
    assert not list(Path(folders.originals).rglob("*.jpg"))


def test_truncated_file_failure_and_explicit_retry(capture_setup):
    repo, engine, clock, *_ = capture_setup
    path, row = receive(capture_setup, data=jpeg_bytes()[:-10])
    assert row["processing_status"] == "receiving"
    clock.advance(10)
    engine.tick()
    assert repo.get(row["id"])["processing_status"] == "failed"
    assert repo.get(row["id"])["error_message"]
    path.write_bytes(jpeg_bytes())
    assert not engine.discover(path, capture_setup[4])
    engine.retry(row["id"])
    clock.advance()
    engine.tick()
    assert repo.get(row["id"])["processing_status"] == "awaiting_confirmation"
    assert repo.get(row["id"])["error_message"] is None
    assert len(repo.photos()) == 1


def test_identical_bytes_cannot_be_imported_twice(capture_setup):
    repo, engine, clock, folders, first, second = capture_setup
    path, row = receive(capture_setup)
    engine.accept(row["id"])
    assert not engine.discover(path, second)
    duplicate, other = receive(capture_setup, "same-photo.jpeg", assignment=second)
    assert other["processing_status"] == "duplicate"
    assert other["duplicate_of"] == row["id"]
    assert "0001" in other["error_message"]
    with pytest.raises(ValueError):
        engine.accept(other["id"])
    assert len(list(Path(folders.originals).rglob("*.jpg"))) == 1
    assert path.exists() and duplicate.exists()


def test_duplicate_detection_survives_restart(capture_setup):
    repo, engine, clock, folders, first, _ = capture_setup
    path, row = receive(capture_setup)
    second_connection = Database(repo.db.path)
    try:
        fresh_repo = CaptureRepository(second_connection)
        fresh = IngestionEngine(fresh_repo, folders, clock=clock)
        assert not fresh.discover(path, first)
        duplicate = path.with_name("renamed.jpg")
        duplicate.write_bytes(path.read_bytes())
        assert fresh.discover(duplicate, first)
        clock.advance()
        fresh.tick()
        assert fresh_repo.photos()[0]["processing_status"] == "duplicate"
    finally:
        second_connection.close()


def test_reassign_before_and_after_acceptance(capture_setup):
    repo, engine, _, folders, first, second = capture_setup
    path, row = receive(capture_setup)
    repo.reassign(row["id"], second.student_record_id)
    assert repo.get(row["id"])["student_id"] == "0002"
    engine.accept(row["id"])
    accepted = repo.get(row["id"])
    assert accepted["processing_status"] == "imported"
    assert "student-0002" in accepted["original_copy_path"]
    repo.reassign(row["id"], first.student_record_id)
    assert repo.get(row["id"])["processing_status"] == "awaiting_confirmation"
    assert repo.get(row["id"])["confirmed_at"] is None
    engine.accept(row["id"])
    assert repo.get(row["id"])["original_copy_path"] == accepted["original_copy_path"]
    assert len(list(Path(folders.originals).rglob("*.jpg"))) == 1
    assert path.exists()
    events = repo.db.connection.execute("SELECT action FROM capture_events WHERE capture_id=?", (row["id"],)).fetchall()
    assert sum(event[0] == "reassigned" for event in events) == 2


def test_discard_retains_all_files_and_can_be_reassigned(capture_setup):
    repo, engine, _, _, _, second = capture_setup
    path, row = receive(capture_setup)
    engine.accept(row["id"])
    archive = Path(repo.get(row["id"])["original_copy_path"])
    original_bytes = path.read_bytes()
    repo.discard(row["id"])
    assert repo.get(row["id"])["processing_status"] == "discarded"
    assert path.read_bytes() == archive.read_bytes() == original_bytes
    assert not engine.discover(path, second)
    repo.reassign(row["id"], second.student_record_id)
    assert repo.get(row["id"])["processing_status"] == "awaiting_confirmation"


def test_failed_copy_can_retry_without_duplicate_record(capture_setup, monkeypatch):
    import student_headshot_manager.ingestion as ingestion
    repo, engine, *_ = capture_setup
    path, row = receive(capture_setup)
    original = ingestion.copy_verified

    def fail(*args):
        raise PermissionError("Simulated drive unavailable")

    monkeypatch.setattr(ingestion, "copy_verified", fail)
    with pytest.raises(PermissionError):
        engine.accept(row["id"])
    failed = repo.get(row["id"])
    assert failed["processing_status"] == "failed"
    assert failed["failure_stage"] == "copy"
    assert "drive unavailable" in failed["error_message"]
    assert path.exists()
    monkeypatch.setattr(ingestion, "copy_verified", original)
    engine.retry(row["id"])
    assert repo.get(row["id"])["processing_status"] == "imported"
    assert repo.get(row["id"])["error_message"] is None
    assert len(repo.photos()) == 1


def test_discarded_incomplete_photo_can_be_reassigned_and_retried(capture_setup):
    repo, engine, clock, _, _, second = capture_setup
    path, row = receive(capture_setup, data=b"incomplete")
    clock.advance(10)
    engine.tick()
    repo.discard(row["id"])
    repo.reassign(row["id"], second.student_record_id)
    assert repo.get(row["id"])["processing_status"] == "failed"
    path.write_bytes(jpeg_bytes())
    engine.retry(row["id"])
    clock.advance()
    engine.tick()
    assert repo.get(row["id"])["processing_status"] == "awaiting_confirmation"
    engine.accept(row["id"])
    assert repo.get(row["id"])["student_id"] == second.student_id
    assert repo.get(row["id"])["processing_status"] == "imported"


def test_source_changed_after_preview_is_not_accepted(capture_setup):
    repo, engine, _, folders, *_ = capture_setup
    path, row = receive(capture_setup)
    path.write_bytes(jpeg_bytes("blue"))
    with pytest.raises(ValueError, match="changed"):
        engine.accept(row["id"])
    assert repo.get(row["id"])["processing_status"] == "failed"
    assert not list(Path(folders.originals).rglob("*.jpg"))
    assert not list(Path(folders.originals).rglob("*.part"))


def test_exclusive_copy_does_not_overwrite_destination_or_staging(tmp_path):
    source = tmp_path / "incoming.jpg"
    source.write_bytes(jpeg_bytes())
    dest = tmp_path / "archive.jpg"
    dest.write_bytes(b"existing archive")
    with pytest.raises(FileExistsError):
        copy_verified(source, dest, file_hash(source))
    assert dest.read_bytes() == b"existing archive"
    assert not dest.with_suffix(".part").exists()
    stage = dest.with_suffix(".part")
    stage.write_bytes(b"existing staging file")
    with pytest.raises(FileExistsError):
        copy_verified(source, dest, file_hash(source))
    assert stage.read_bytes() == b"existing staging file"


def test_accepted_copy_is_identical_and_source_mtime_unchanged(capture_setup):
    repo, engine, *_ = capture_setup
    path, row = receive(capture_setup)
    stat = path.stat()
    engine.accept(row["id"])
    archived = Path(repo.get(row["id"])["original_copy_path"])
    assert archived.read_bytes() == path.read_bytes()
    assert path.stat().st_mtime_ns == stat.st_mtime_ns
    assert repo.db.student(row["student_record_id"])["status"] == "Captured"


def test_reused_incoming_filename_with_new_content(capture_setup):
    repo, engine, clock, _, first, second = capture_setup
    path, row = receive(capture_setup)
    engine.accept(row["id"])
    archive = Path(repo.get(row["id"])["original_copy_path"])
    path.write_bytes(jpeg_bytes("blue"))
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10000))
    assert engine.discover(path, second)
    clock.advance()
    engine.tick()
    new = repo.photos()[0]
    engine.accept(new["id"])
    assert new["id"] != row["id"]
    assert archive.read_bytes() == jpeg_bytes()
    assert Path(repo.get(new["id"])["original_copy_path"]).read_bytes() == jpeg_bytes("blue")


def test_interrupted_copy_recovers_verified_published_file(capture_setup):
    repo, engine, _, folders, *_ = capture_setup
    path, row = receive(capture_setup)
    destination = Path(folders.originals) / "completed-before-crash.jpg"
    repo.claim_copy(row["id"], destination)
    destination.write_bytes(path.read_bytes())
    repo.recover_interrupted()
    assert repo.get(row["id"])["processing_status"] == "failed"
    engine.retry(row["id"])
    assert repo.get(row["id"])["processing_status"] == "imported"
    assert repo.get(row["id"])["original_copy_path"] == str(destination)


def test_deleted_roster_student_keeps_photo_identity(capture_setup):
    repo, engine, _, _, first, _ = capture_setup
    path, row = receive(capture_setup)
    repo.db.delete_student(first.student_record_id)
    stored = repo.get(row["id"])
    assert stored["student_record_id"] is None
    assert stored["student_id"] == first.student_id
    assert stored["student_name"] == first.student_name
    assert stored["session_id"] == first.session_id
    assert path.exists()


def test_no_implicit_student_and_non_jpeg_ignored(capture_setup):
    repo, engine, _, folders, first, _ = capture_setup
    path = Path(folders.incoming) / "image.jpg"
    path.write_bytes(jpeg_bytes())
    assert not engine.discover(path, None)
    other = path.with_suffix(".png")
    other.write_bytes(jpeg_bytes())
    assert not engine.discover(other, first)
    directory = Path(folders.incoming) / "directory.jpg"
    directory.mkdir()
    assert not engine.discover(directory, first)
    assert repo.photos() == []


def test_folders_persist_and_overlap_is_rejected(capture_setup):
    repo, _, _, folders, *_ = capture_setup
    repo.set_settings("folders", folders.as_dict())
    assert repo.settings("folders") == folders.as_dict()
    with pytest.raises(ValueError, match="separate"):
        Folders(folders.incoming, str(Path(folders.incoming) / "copies"), folders.processed, folders.export).validate()
    with pytest.raises(ValueError, match="absolute"):
        Folders("relative", folders.originals, folders.processed, folders.export).validate()


def test_v1_migration_preserves_roster(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE classes(id INTEGER PRIMARY KEY,name TEXT NOT NULL,name_key TEXT NOT NULL UNIQUE);
        CREATE TABLE students(id INTEGER PRIMARY KEY,student_id TEXT NOT NULL,student_key TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'Not photographed',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO classes VALUES (1,'Legacy','legacy');
        INSERT INTO students(student_id,student_key,full_name,class_id,status) VALUES ('001','001','Existing Student',1,'Complete');
        PRAGMA user_version=1;
    """)
    connection.close()
    upgraded = Database(path)
    try:
        assert upgraded.students()[0]["student_id"] == "001"
        assert upgraded.students()[0]["status"] == "Complete"
        assert upgraded.classes()[0]["name"] == "Legacy"
        assert upgraded.connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert CaptureRepository(upgraded).photos() == []
    finally:
        upgraded.close()
