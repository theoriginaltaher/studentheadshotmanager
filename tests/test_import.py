from pathlib import Path

import pytest

from student_headshot_manager.roster_import import (
    CsvData, import_csv, read_csv, suggest_mapping, validate,
)

MAPPING = {"Student ID": 0, "Full name": 1, "Class": 2}


def data(*rows):
    return CsvData(("student_id", "full_name", "class"), tuple(rows))


def test_sample_import_and_preview_do_not_write(db):
    csv = read_csv(Path(__file__).parents[1] / "samples" / "students.csv")
    mapping = suggest_mapping(csv)
    preview = validate(csv, mapping, db)
    assert len(preview) == 12
    assert all(not row.errors for row in preview)
    assert db.classes() == []
    assert import_csv(csv, mapping, db) == 12
    assert len(db.classes()) == 4
    assert db.students(search="0001")[0]["student_id"] == "0001"
    assert db.students(search="Chen")[0]["full_name"] == "Chen, Lucas"
    assert all(row["status"] == "Not photographed" for row in db.students())


def test_mapping_and_utf8_bom(tmp_path, db):
    file = tmp_path / "roster.csv"
    file.write_text('Group,Person,Number\r\n7A,"Zoë, Test",0009\r\n', encoding="utf-8-sig", newline="")
    csv = read_csv(file)
    assert csv.headers[0] == "Group"
    assert import_csv(csv, {"Student ID": 2, "Full name": 1, "Class": 0}, db) == 1
    assert db.students()[0]["student_id"] == "0009"


def test_duplicate_ids_flag_all_occurrences_and_existing(db):
    class_id = db.create_class("A")
    db.save_student("0001", "Existing", class_id)
    csv = data(("0001", "One", "B"), ("ID2", "Two", "B"), (" id2 ", "Three", "C"))
    rows = validate(csv, MAPPING, db)
    assert all(row.errors for row in rows)
    with pytest.raises(ValueError, match="blocked"):
        import_csv(csv, MAPPING, db)
    assert len(db.students()) == 1
    assert len(db.classes()) == 1


@pytest.mark.parametrize("row", [
    ("", "Name", "A"), ("1", "", "A"), ("1", "Name", ""),
    ("1", "Name"), ("1", "Name", "A", "Extra"), (),
    ("x" * 81, "Name", "A"), ("1", "Bad\nName", "A"),
])
def test_invalid_rows_prevent_entire_import(db, row):
    csv = data(("good", "Valid Student", "New class"), row)
    assert validate(csv, MAPPING, db)[1].errors
    with pytest.raises(ValueError):
        import_csv(csv, MAPPING, db)
    assert db.classes() == []
    assert db.students() == []


@pytest.mark.parametrize("mapping", [{}, {"Student ID": 0, "Full name": 0, "Class": 2},
                                      {"Student ID": -1, "Full name": 1, "Class": 2}])
def test_invalid_mapping(db, mapping):
    with pytest.raises(ValueError):
        validate(data(("1", "Name", "A")), mapping, db)


@pytest.mark.parametrize("content", ["", "id,name,class\n", "id,id,class\n1,A,B", ",name,class\n1,A,B", 'id,name,class\n1,"unterminated,B'])
def test_bad_csv_files(tmp_path, content):
    file = tmp_path / "bad.csv"
    file.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        read_csv(file)


def test_import_rechecks_after_preview(db):
    csv = data(("1", "Name", "A"))
    assert not validate(csv, MAPPING, db)[0].errors
    class_id = db.create_class("A")
    db.save_student("1", "Intervening record", class_id)
    with pytest.raises(ValueError):
        import_csv(csv, MAPPING, db)
    assert len(db.students()) == 1


def test_import_rolls_back_midway_failure(db, monkeypatch):
    csv = data(("1", "One", "A"), ("2", "Two", "B"))
    original = db.save_student

    def fail_second(*args, **kwargs):
        if args[0] == "2":
            raise RuntimeError("Simulated storage failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(db, "save_student", fail_second)
    with pytest.raises(RuntimeError):
        import_csv(csv, MAPPING, db)
    assert db.students() == []
    assert db.classes() == []


def test_existing_classes_reused_with_normalization(db):
    db.create_class("Year A")
    import_csv(data(("1", "One", "year a"), ("2", "Two", " Year A ")), MAPPING, db)
    assert len(db.classes()) == 1
    assert db.classes()[0]["student_count"] == 2


def test_invalid_encoding_and_file_limits(tmp_path, monkeypatch):
    from student_headshot_manager import roster_import
    file = tmp_path / "bad.csv"
    file.write_bytes(b"id,name,class\n1,\xff,A")
    with pytest.raises(ValueError, match="UTF-8"):
        read_csv(file)
    file.write_text("id,name,class\n1,One,A\n2,Two,A", encoding="utf-8")
    monkeypatch.setattr(roster_import, "MAX_ROWS", 1)
    with pytest.raises(ValueError, match="data rows"):
        read_csv(file)
    monkeypatch.setattr(roster_import, "MAX_BYTES", 1)
    with pytest.raises(ValueError, match="10 MB"):
        read_csv(file)
