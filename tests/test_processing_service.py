from dataclasses import replace
import json
from pathlib import Path
import socket

from PIL import Image
import pytest

from samples.generate_processing_samples import make_sample
from student_headshot_manager import processing
from student_headshot_manager.capture_repository import CaptureRepository
from student_headshot_manager.database import Database
from student_headshot_manager.processing import ProcessingSettings
from student_headshot_manager.processing_service import ProcessingService


def settings():
    return replace(ProcessingSettings(), canvas_width=120, canvas_height=160)


def test_saves_png_jpeg_mask_and_never_uses_network(db, processing_capture, monkeypatch):
    capture_id, original, incoming, folders = processing_capture
    before, incoming_before = original.read_bytes(), incoming.read_bytes()
    modified = original.stat().st_mtime_ns

    def no_network(*args, **kwargs):
        raise AssertionError("Processing must not use the network")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    service = ProcessingService(db)
    row, cached, _ = service.process(capture_id, settings())
    assert not cached
    assert row["status"] == "awaiting_review"
    outputs = json.loads(row["outputs_json"])
    with Image.open(outputs["cutout"]["path"]) as cutout:
        assert cutout.mode == "RGBA" and cutout.size == (120, 160)
        assert cutout.getexif().get(274) == 1
    with Image.open(outputs["portrait"]["path"]) as portrait:
        assert portrait.format == "JPEG" and portrait.size == (120, 160)
        assert portrait.getpixel((0, 0)) == (180, 180, 180)
    with Image.open(outputs["mask"]["path"]) as mask:
        assert mask.mode == "L" and mask.size == (120, 160)
    assert original.read_bytes() == before and incoming.read_bytes() == incoming_before
    assert original.stat().st_mtime_ns == modified


def test_cache_hits_without_rerunning_pipeline_and_survives_restart(db, processing_capture, monkeypatch):
    capture_id, *_ = processing_capture
    service = ProcessingService(db)
    first, _, _ = service.process(capture_id, settings())

    def forbidden(*args):
        raise AssertionError("Pipeline reran for unchanged original and settings")

    monkeypatch.setattr(processing, "process_bytes", forbidden)
    reopened = Database(db.path)
    try:
        cached, hit, _ = ProcessingService(reopened).process(capture_id, settings())
        assert hit and cached["id"] == first["id"]
    finally:
        reopened.close()


def test_settings_source_and_missing_output_invalidate_cache(db, processing_capture):
    capture_id, original, *_ = processing_capture
    service = ProcessingService(db)
    one, _, _ = service.process(capture_id, settings())
    two, hit, _ = service.process(capture_id, replace(settings(), grey=155))
    assert not hit and one["id"] != two["id"]
    Path(json.loads(two["outputs_json"])["mask"]["path"]).unlink()
    three, hit, _ = service.process(capture_id, replace(settings(), grey=155))
    assert not hit and three["id"] != two["id"]
    assert service.repo.get(two["id"])["status"] == "failed"
    make_sample("green-clothing", (120, 160)).save(original, quality=98, subsampling=0)
    four, hit, _ = service.process(capture_id, settings())
    assert not hit and four["source_hash"] != one["source_hash"]
    assert json.loads(four["diagnostics_json"])["blocking"]  # Archive changed externally.


def test_storage_failure_records_error_and_can_retry(db, processing_capture, monkeypatch):
    capture_id, original, *_ = processing_capture
    service = ProcessingService(db)
    original_root = service.output_root

    def fail(*args):
        raise PermissionError("Processed drive is unavailable")

    monkeypatch.setattr(service, "output_root", fail)
    with pytest.raises(PermissionError):
        service.process(capture_id, settings())
    failed = service.repo.latest(capture_id)
    assert failed["status"] == "failed" and "unavailable" in failed["error_message"]
    monkeypatch.setattr(service, "output_root", original_root)
    row, cached, _ = service.process(capture_id, settings())
    assert not cached and row["status"] == "awaiting_review"
    assert original.exists()


def test_manual_review_persists_until_explicit_approval(db, processing_capture):
    capture_id, *_ = processing_capture
    service = ProcessingService(db)
    service.repo.flag(capture_id, "Inspect hair manually")
    row, _, _ = service.process(capture_id, settings())
    assert service.repo.review(capture_id)["manual_review"] == 1
    assert row["status"] == "needs_review"
    with pytest.raises(ValueError, match="acknowledge"):
        service.approve(row["id"])
    service.approve(row["id"], acknowledged=True)
    assert service.repo.get(row["id"])["status"] == "approved"
    assert service.repo.review(capture_id)["manual_review"] == 0
    assert service.repo.review(capture_id)["approved_job_id"] == row["id"]
    service.repo.flag(capture_id, "Inspect again")
    assert service.repo.get(row["id"])["status"] == "needs_review"
    assert service.repo.review(capture_id)["approved_job_id"] is None


def test_blocking_mask_cannot_be_approved_even_with_acknowledgment(db, processing_capture):
    capture_id, original, *_ = processing_capture
    make_sample("empty-green", (120, 160)).save(original, quality=98, subsampling=0)
    service = ProcessingService(db)
    row, _, _ = service.process(capture_id, settings())
    assert row["status"] == "needs_review"
    with pytest.raises(ValueError, match="Separation"):
        service.approve(row["id"], acknowledged=True)
    service.repo.flag(capture_id, "Recapture needed")
    assert service.repo.review(capture_id)["manual_review"]


def test_changes_after_preview_block_approval(db, processing_capture):
    capture_id, _, *_ = processing_capture
    service = ProcessingService(db)
    row, _, _ = service.process(capture_id, settings())
    Path(json.loads(row["outputs_json"])["portrait"]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        service.approve(row["id"])
    assert service.repo.get(row["id"])["status"] == "failed"


def test_interrupted_processing_and_missing_original_have_recoverable_errors(db, processing_capture):
    capture_id, original, *_ = processing_capture
    service = ProcessingService(db)
    interrupted = service.repo.create(capture_id, settings(), processing.PIPELINE_VERSION)
    service.repo.recover_interrupted()
    assert service.repo.get(interrupted)["status"] == "failed"
    original.unlink()
    with pytest.raises(FileNotFoundError):
        service.process(capture_id, settings())
    assert service.repo.latest(capture_id)["error_message"]


def test_source_change_during_processing_does_not_publish_result(db, processing_capture, monkeypatch):
    capture_id, original, _, folders = processing_capture
    service = ProcessingService(db)
    run = processing.process_bytes

    def mutate(data, config):
        result = run(data, config)
        make_sample("no-green", (120, 160)).save(original)
        return result

    monkeypatch.setattr(processing, "process_bytes", mutate)
    with pytest.raises(ValueError, match="changed during"):
        service.process(capture_id, settings())
    assert service.repo.latest(capture_id)["status"] == "failed"
    assert not list(Path(folders.processed).rglob("cutout.png"))
    assert not list(Path(folders.processed).rglob(".pending-*"))


def test_reassignment_requires_reconfirmation_before_processing(db, processing_capture):
    capture_id, *_ = processing_capture
    capture = CaptureRepository(db)
    photo = capture.get(capture_id)
    capture.reassign(capture_id, photo["student_record_id"])
    with pytest.raises(ValueError, match="Confirm"):
        ProcessingService(db).process(capture_id, settings())


def test_v2_upgrade_preserves_existing_capture_and_roster(db, processing_capture):
    capture_id, original, *_ = processing_capture
    db.connection.executescript("DROP TABLE processing_reviews; DROP TABLE processing_jobs; PRAGMA user_version=2;")
    upgraded = Database(db.path)
    try:
        assert upgraded.connection.execute("PRAGMA user_version").fetchone()[0] == 4
        row = CaptureRepository(upgraded).get(capture_id)
        assert row["processing_status"] == "imported"
        assert row["original_copy_path"] == str(original)
        assert upgraded.students()[0]["student_id"] == "001"
        assert ProcessingService(upgraded).repo.latest(capture_id) is None
    finally:
        upgraded.close()
