"""Read, map and validate CSV data before any database writes."""

import csv
from dataclasses import dataclass
from pathlib import Path

from .database import Database, clean, identity

FIELDS = ("Student ID", "Full name", "Class")
MAX_ROWS = 20000
MAX_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class CsvData:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class PreviewRow:
    line: int
    student_id: str
    full_name: str
    class_name: str
    errors: tuple[str, ...]


def read_csv(path: str | Path) -> CsvData:
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("CSV files must be 10 MB or smaller.")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.reader(file, strict=True)
            headers = next(reader, None)
            if not headers:
                raise ValueError("The CSV is empty or has no header row.")
            headers = tuple(value.strip() for value in headers)
            if any(not header for header in headers) or len(set(identity(h) for h in headers)) != len(headers):
                raise ValueError("Column headers must be non-empty and unique.")
            rows = []
            for row in reader:
                rows.append(tuple(row))
                if len(rows) > MAX_ROWS:
                    raise ValueError(f"CSV files may contain at most {MAX_ROWS:,} data rows.")
    except UnicodeError as exc:
        raise ValueError("Save the CSV using UTF-8 encoding and try again.") from exc
    except csv.Error as exc:
        raise ValueError(f"Unable to read CSV: {exc}") from exc
    if not rows:
        raise ValueError("The CSV contains headers but no students.")
    return CsvData(headers, tuple(rows))


def suggest_mapping(data: CsvData) -> dict[str, int | None]:
    aliases = {
        "Student ID": {"student id", "student_id", "id", "student number"},
        "Full name": {"full name", "full_name", "name", "student name"},
        "Class": {"class", "class name", "class_name", "grade"},
    }
    return {field: next((i for i, h in enumerate(data.headers) if identity(h) in names), None)
            for field, names in aliases.items()}


def validate(data: CsvData, mapping: dict, db: Database) -> list[PreviewRow]:
    indexes = [mapping.get(field) for field in FIELDS]
    if any(type(i) is not int or i < 0 or i >= len(data.headers) for i in indexes):
        raise ValueError("Map a CSV column to each required field.")
    if len(set(indexes)) != len(FIELDS):
        raise ValueError("Use a different CSV column for each field.")
    existing = db.student_keys()
    counts = {}
    for row in data.rows:
        key = identity(row[indexes[0]]) if indexes[0] < len(row) else ""
        counts[key] = counts.get(key, 0) + 1
    result = []
    for line, row in enumerate(data.rows, start=2):
        values = [row[i].strip() if i < len(row) else "" for i in indexes]
        errors = []
        if len(row) != len(data.headers):
            errors.append(f"Expected {len(data.headers)} columns; found {len(row)}.")
        for value, field in zip(values, FIELDS):
            try:
                clean(value, field, 80 if field == "Student ID" else 200)
            except ValueError as exc:
                errors.append(str(exc))
        key = identity(values[0])
        if key in existing:
            errors.append("Student ID already exists in the roster.")
        if key and counts[key] > 1:
            errors.append("Student ID is repeated in this CSV.")
        result.append(PreviewRow(line, *values, tuple(errors)))
    return result


def import_csv(data: CsvData, mapping: dict, db: Database) -> int:
    """Revalidate under a write lock; import all rows or none."""
    with db.transaction():
        # Acquire SQLite's write lock before checking duplicates.
        db.connection.execute("UPDATE classes SET name = name WHERE 0")
        rows = validate(data, mapping, db)
        if not rows or any(row.errors for row in rows):
            raise ValueError("Import blocked. Resolve all validation errors before importing.")
        classes = {row["name_key"]: row["id"] for row in db.classes()}
        for row in rows:
            key = identity(row.class_name)
            if key not in classes:
                classes[key] = db.create_class(row.class_name)
            db.save_student(row.student_id, row.full_name, classes[key])
    return len(rows)
