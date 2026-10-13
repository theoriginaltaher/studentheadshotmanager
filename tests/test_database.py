import sqlite3

import pytest

from student_headshot_manager.database import Database, STATUSES


def test_schema_initialization_is_repeatable_and_persistent(tmp_path):
    path = tmp_path / "nested" / "roster.sqlite3"
    db = Database(path)
    class_id = db.create_class("Year 7")
    student_id = db.save_student("0001", "Ada Lovelace", class_id, "Captured")
    db.close()
    reopened = Database(path)
    assert reopened.student(student_id)["student_id"] == "0001"
    assert reopened.student(student_id)["status"] == "Captured"
    assert reopened.classes()[0]["student_count"] == 1
    assert reopened.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert reopened.connection.execute("PRAGMA user_version").fetchone()[0] == 4
    reopened.close()


def test_class_crud_and_cascade_only_selected_class(db):
    first = db.create_class(" A ")
    second = db.create_class("B")
    db.save_student("1", "First", first)
    survivor = db.save_student("2", "Second", second)
    db.update_class(first, "Renamed")
    assert db.students(first)[0]["class_name"] == "Renamed"
    db.delete_class(first)
    assert [row["id"] for row in db.students()] == [survivor]
    assert len(db.classes()) == 1


def test_student_crud_move_and_all_statuses(db):
    first = db.create_class("A")
    second = db.create_class("B")
    record = db.save_student(" 001 ", " First Student ", first)
    assert db.student(record)["status"] == "Not photographed"
    for status in STATUSES:
        db.save_student("002", "Renamed Student", second, status, record)
        assert db.student(record)["status"] == status
    assert db.students(first) == []
    assert len(db.students(second)) == 1
    db.delete_student(record)
    assert db.students() == []


def test_unicode_and_case_normalized_duplicates(db):
    class_id = db.create_class("Café")
    with pytest.raises(ValueError, match="already exists"):
        db.create_class(" CAFE\u0301 ")
    db.save_student("Ａ01", "One", class_id)
    with pytest.raises(ValueError, match="already exists"):
        db.save_student("a01", "Two", class_id)
    other = db.create_class("Other")
    with pytest.raises(ValueError):
        db.update_class(other, "Café")
    assert len(db.classes()) == 2


@pytest.mark.parametrize("student_id,name,status", [
    ("", "Name", STATUSES[0]), ("1", "  ", STATUSES[0]),
    ("1", "Name", "unknown"), ("x" * 81, "Name", STATUSES[0]),
    ("1", "x" * 201, STATUSES[0]), ("1\n2", "Name", STATUSES[0]),
])
def test_student_validation(db, student_id, name, status):
    class_id = db.create_class("A")
    with pytest.raises(ValueError):
        db.save_student(student_id, name, class_id, status)
    assert db.students() == []


@pytest.mark.parametrize("name", ["", "  ", "a" * 201, "A\tB"])
def test_invalid_classes(db, name):
    with pytest.raises(ValueError):
        db.create_class(name)


def test_foreign_key_and_status_constraints(db):
    with pytest.raises(ValueError, match="existing class"):
        db.save_student("1", "Name", 999)
    class_id = db.create_class("A")
    for foreign, status in [(999, "Captured"), (class_id, "invalid")]:
        with pytest.raises(sqlite3.IntegrityError), db.transaction():
            db.connection.execute(
                "INSERT INTO students(student_id, student_key, full_name, class_id, status) VALUES (?, ?, ?, ?, ?)",
                ("1", "1", "Name", foreign, status),
            )


def test_filters_and_literal_search(db):
    a, b = db.create_class("A"), db.create_class("B")
    db.save_student("0001", "Mía Jones", a, "Complete")
    db.save_student("0002", "Percent % Student", b)
    assert len(db.students(search="MÍA")) == 1
    assert len(db.students(search="000")) == 2
    assert len(db.students(search="%")) == 1
    assert len(db.students(a, "jones", "Complete")) == 1
    assert db.students(a, status="Captured") == []


def test_missing_records_fail(db):
    with pytest.raises(ValueError):
        db.delete_student(999)
    with pytest.raises(ValueError):
        db.delete_class(999)
    with pytest.raises(ValueError):
        db.update_class(999, "Name")


def test_nested_transaction_rolls_back(db):
    with pytest.raises(RuntimeError):
        with db.transaction():
            class_id = db.create_class("A")
            db.save_student("1", "Name", class_id)
            raise RuntimeError("Failure")
    assert db.classes() == []
    assert db.students() == []
