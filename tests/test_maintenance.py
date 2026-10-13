from pathlib import Path
import sqlite3
import pytest
from student_headshot_manager.database import Database
from student_headshot_manager.maintenance import backup_database,restore_database,startup_backup,check_backup


def test_program_files_data_locations_are_rejected(tmp_path,monkeypatch):
    from student_headshot_manager.maintenance import validate_data_location
    protected=tmp_path/'Program Files';monkeypatch.setenv('ProgramFiles',str(protected))
    with pytest.raises(ValueError,match='outside Program Files'):
        validate_data_location(protected/'Student Headshot Manager'/'Photos')
    assert validate_data_location(tmp_path/'User Photos')==tmp_path/'User Photos'


def test_backup_includes_wal_and_restores_selection(db,processing_capture,tmp_path):
    from student_headshot_manager.review_repository import ReviewRepository
    capture,*_=processing_capture;repo=ReviewRepository(db);student=repo.students()[0]['id'];repo.select(student,capture)
    backup=backup_database(db.path);restored=tmp_path/'Restored ü'/ 'Roster with spaces.sqlite3';restore_database(backup,restored)
    check_backup(restored);other=Database(restored)
    try:assert ReviewRepository(other).students()[0]['final_capture_id']==capture
    finally:other.close()
    assert startup_backup(db.path) is None


def test_invalid_backup_cannot_replace_live_roster(db,tmp_path):
    db.create_class('Keep');bad=tmp_path/'bad.db';bad.write_bytes(b'not sqlite')
    with pytest.raises(sqlite3.DatabaseError):restore_database(bad,db.path)
    assert db.classes()[0]['name']=='Keep'


def test_recovery_preserves_corrupt_database(tmp_path):
    good=Database(tmp_path/'good.db');good.create_class('Restored');good.close()
    bad=tmp_path/'broken.db';bad.write_bytes(b'corrupted original')
    restore_database(tmp_path/'good.db',bad)
    assert list(tmp_path.glob('broken.db.corrupt-*'))[0].read_bytes()==b'corrupted original'
    check_backup(bad)


def test_backup_destination_error_does_not_change_database(db,tmp_path):
    db.create_class('Keep');not_directory=tmp_path/'file';not_directory.write_text('keep')
    with pytest.raises(OSError):backup_database(db.path,not_directory)
    assert db.classes()[0]['name']=='Keep'


def test_packaged_diagnostic_workflow_in_source(qapp,tmp_path):
    from student_headshot_manager.self_test import run
    import json
    destination=tmp_path/'diagnostic ü'
    assert run(destination,qapp)==0
    assert json.loads((destination/'result.json').read_text(encoding='utf-8'))['result']=='PASS'
    assert run(destination,qapp)==2
