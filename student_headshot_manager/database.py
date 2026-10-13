"""SQLite persistence and validation, independent of Qt."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import unicodedata

STATUSES = ("Not photographed", "Captured", "Needs review", "Complete")


def clean(value: str, label: str, limit: int = 200) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{label} is required.")
    if len(value) > limit:
        raise ValueError(f"{label} must be at most {limit} characters.")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError(f"{label} cannot contain control characters.")
    return value


def identity(value: str) -> str:
    return unicodedata.normalize("NFKC", value.strip()).casefold()


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version > 4:
            self.close()
            raise ValueError("This database was created by a newer application version.")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS classes (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 200),
                name_key TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY,
                student_id TEXT NOT NULL CHECK(length(trim(student_id)) BETWEEN 1 AND 80),
                student_key TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL CHECK(length(trim(full_name)) BETWEEN 1 AND 200),
                class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'Not photographed'
                    CHECK(status IN ('Not photographed', 'Captured', 'Needs review', 'Complete')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS students_class ON students(class_id);
        """)
        from .capture_repository import migrate
        migrate(self)
        from .processing_repository import migrate as migrate_processing
        migrate_processing(self)
        from .review_repository import migrate as migrate_review
        migrate_review(self)

    @contextmanager
    def transaction(self):
        """Savepoints allow CRUD calls inside one atomic import transaction."""
        self.connection.execute("SAVEPOINT roster_operation")
        try:
            yield
            self.connection.execute("RELEASE SAVEPOINT roster_operation")
        except Exception:
            self.connection.execute("ROLLBACK TO SAVEPOINT roster_operation")
            self.connection.execute("RELEASE SAVEPOINT roster_operation")
            raise

    def close(self):
        self.connection.close()

    def classes(self):
        return self.connection.execute("""
            SELECT c.*, COUNT(s.id) AS student_count FROM classes c
            LEFT JOIN students s ON s.class_id = c.id
            GROUP BY c.id ORDER BY c.name_key
        """).fetchall()

    def create_class(self, name: str) -> int:
        name = clean(name, "Class name")
        try:
            with self.transaction():
                return self.connection.execute(
                    "INSERT INTO classes(name, name_key) VALUES (?, ?)",
                    (name, identity(name)),
                ).lastrowid
        except sqlite3.IntegrityError as exc:
            raise ValueError("A class with this name already exists.") from exc

    def update_class(self, class_id: int, name: str):
        name = clean(name, "Class name")
        try:
            with self.transaction():
                cursor = self.connection.execute(
                    "UPDATE classes SET name = ?, name_key = ? WHERE id = ?",
                    (name, identity(name), class_id),
                )
                self._require_row(cursor)
        except sqlite3.IntegrityError as exc:
            raise ValueError("A class with this name already exists.") from exc

    def delete_class(self, class_id: int):
        with self.transaction():
            self._require_row(self.connection.execute("DELETE FROM classes WHERE id = ?", (class_id,)))

    def students(self, class_id=None, search="", status=None):
        sql = "SELECT s.*, c.name AS class_name FROM students s JOIN classes c ON c.id = s.class_id WHERE 1=1"
        args = []
        if class_id is not None:
            sql += " AND s.class_id = ?"
            args.append(class_id)
        if status is not None:
            sql += " AND s.status = ?"
            args.append(status)
        rows = self.connection.execute(sql + " ORDER BY s.full_name COLLATE NOCASE, s.student_key", args).fetchall()
        needle = identity(search)
        return [row for row in rows if not needle or needle in identity(row["full_name"]) or needle in identity(row["student_id"])]

    def student(self, record_id):
        return self.connection.execute("SELECT * FROM students WHERE id = ?", (record_id,)).fetchone()

    def student_keys(self):
        return {row[0] for row in self.connection.execute("SELECT student_key FROM students")}

    def save_student(self, student_id, full_name, class_id, status=STATUSES[0], record_id=None):
        student_id = clean(student_id, "Student ID", 80)
        full_name = clean(full_name, "Full name")
        if status not in STATUSES:
            raise ValueError("Choose a valid photography status.")
        try:
            with self.transaction():
                if not self.connection.execute("SELECT 1 FROM classes WHERE id = ?", (class_id,)).fetchone():
                    raise ValueError("Choose an existing class.")
                values = (student_id, identity(student_id), full_name, class_id, status)
                if record_id is None:
                    return self.connection.execute("""
                        INSERT INTO students(student_id, student_key, full_name, class_id, status)
                        VALUES (?, ?, ?, ?, ?)
                    """, values).lastrowid
                cursor = self.connection.execute("""
                    UPDATE students SET student_id=?, student_key=?, full_name=?, class_id=?, status=? WHERE id=?
                """, (*values, record_id))
                self._require_row(cursor)
                return record_id
        except sqlite3.IntegrityError as exc:
            raise ValueError("This student ID already exists. Student IDs must be unique across all classes.") from exc

    def delete_student(self, record_id):
        with self.transaction():
            self._require_row(self.connection.execute("DELETE FROM students WHERE id = ?", (record_id,)))

    @staticmethod
    def _require_row(cursor):
        if not cursor.rowcount:
            raise ValueError("This record no longer exists. Refresh the roster and try again.")
