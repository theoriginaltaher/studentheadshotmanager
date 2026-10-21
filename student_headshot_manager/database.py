"""SQLite persistence and validation, independent of Qt."""

from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3
import unicodedata

STATUSES = ("Not photographed", "Captured", "Needs review", "Complete")
INVALID_FILENAME = re.compile(r'[<>:"/\\|?*]')
RESERVED_FILENAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                      *(f"LPT{i}" for i in range(1, 10))}


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


def clean_filename(value: str) -> str:
    value = clean(value, "Filename", 200)
    if INVALID_FILENAME.search(value) or value.endswith((" ", ".")) or value in (".", ".."):
        raise ValueError('Filename cannot contain < > : " / \\ | ? *, or end with a space or period.')
    if value.split(".")[0].upper() in RESERVED_FILENAMES:
        raise ValueError("Filename uses a name reserved by Windows.")
    return value


def default_filename(student_id: str, full_name: str) -> str:
    value = INVALID_FILENAME.sub("_", f"{student_id.strip()} - {full_name.strip()}").strip(" .")
    if not value or value.split(".")[0].upper() in RESERVED_FILENAMES:
        value = "student_" + re.sub(r"\W+", "_", student_id, flags=re.UNICODE).strip("_")
    return value[:200].rstrip(" .")


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
        if version > 6:
            self.close()
            raise ValueError("This database was created by a newer application version.")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS classes (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 200),
                name_key TEXT NOT NULL UNIQUE,
                sort_order INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY,
                student_id TEXT NOT NULL CHECK(length(trim(student_id)) BETWEEN 1 AND 80),
                student_key TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL CHECK(length(trim(full_name)) BETWEEN 1 AND 200),
                export_filename TEXT NOT NULL CHECK(length(trim(export_filename)) BETWEEN 1 AND 200),
                filename_key TEXT NOT NULL UNIQUE,
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
        self._migrate_student_filenames()
        self._migrate_class_order()

    def _migrate_student_filenames(self):
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(students)")}
        if "export_filename" not in columns:
            with self.transaction():
                self.connection.execute("ALTER TABLE students ADD COLUMN export_filename TEXT")
                self.connection.execute("ALTER TABLE students ADD COLUMN filename_key TEXT")
                used = set()
                for row in self.connection.execute("SELECT id,student_id,full_name FROM students").fetchall():
                    base = default_filename(row["student_id"], row["full_name"])
                    filename = base
                    if identity(filename) in used:
                        suffix = f"-{row['id']}"
                        filename = base[:200 - len(suffix)].rstrip(" .") + suffix
                    used.add(identity(filename))
                    self.connection.execute(
                        "UPDATE students SET export_filename=?,filename_key=? WHERE id=?",
                        (filename, identity(filename), row["id"]),
                    )
                self.connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS students_filename ON students(filename_key)"
                )
        else:
            self.connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS students_filename ON students(filename_key)"
            )
        if self.connection.execute("PRAGMA user_version").fetchone()[0] < 5:
            self.connection.execute("PRAGMA user_version = 5")

    def _migrate_class_order(self):
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(classes)")}
        if "sort_order" not in columns:
            with self.transaction():
                self.connection.execute("ALTER TABLE classes ADD COLUMN sort_order INTEGER")
                rows = self.connection.execute("SELECT id FROM classes ORDER BY name_key,id").fetchall()
                for position, row in enumerate(rows):
                    self.connection.execute("UPDATE classes SET sort_order=? WHERE id=?", (position, row["id"]))
        if self.connection.execute("PRAGMA user_version").fetchone()[0] < 6:
            self.connection.execute("PRAGMA user_version = 6")

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
            GROUP BY c.id ORDER BY c.sort_order, c.name_key
        """).fetchall()

    def create_class(self, name: str) -> int:
        name = clean(name, "Class name")
        try:
            with self.transaction():
                return self.connection.execute(
                    "INSERT INTO classes(name,name_key,sort_order) VALUES (?,?,COALESCE((SELECT MAX(sort_order)+1 FROM classes),0))",
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
        self.delete_classes([class_id])

    def delete_classes(self, class_ids):
        class_ids = list(dict.fromkeys(class_ids))
        if not class_ids:
            raise ValueError("Select at least one class.")
        placeholders = ",".join("?" for _ in class_ids)
        with self.transaction():
            found = self.connection.execute(
                f"SELECT COUNT(*) FROM classes WHERE id IN ({placeholders})", class_ids
            ).fetchone()[0]
            if found != len(class_ids):
                raise ValueError("One or more classes no longer exist. Refresh and try again.")
            self.connection.execute(f"DELETE FROM classes WHERE id IN ({placeholders})", class_ids)

    def reorder_classes(self, class_ids):
        class_ids = list(class_ids)
        current = [row["id"] for row in self.classes()]
        if len(class_ids) != len(set(class_ids)) or set(class_ids) != set(current):
            raise ValueError("Class order is stale. Refresh and try again.")
        with self.transaction():
            for position, class_id in enumerate(class_ids):
                self.connection.execute("UPDATE classes SET sort_order=? WHERE id=?", (position, class_id))

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
        return [row for row in rows if not needle or needle in identity(row["full_name"])
                or needle in identity(row["student_id"]) or needle in identity(row["export_filename"])]

    def student(self, record_id):
        return self.connection.execute("SELECT * FROM students WHERE id = ?", (record_id,)).fetchone()

    def student_keys(self):
        return {row[0] for row in self.connection.execute("SELECT student_key FROM students")}

    def filename_keys(self):
        return {row[0] for row in self.connection.execute("SELECT filename_key FROM students")}

    def save_student(self, student_id, full_name, class_id, status=STATUSES[0], record_id=None,
                     export_filename=None):
        student_id = clean(student_id, "Student ID", 80)
        full_name = clean(full_name, "Full name")
        if export_filename is None and record_id is not None:
            current = self.student(record_id)
            export_filename = current["export_filename"] if current else None
        export_filename = clean_filename(export_filename or default_filename(student_id, full_name))
        if status not in STATUSES:
            raise ValueError("Choose a valid photography status.")
        try:
            with self.transaction():
                if not self.connection.execute("SELECT 1 FROM classes WHERE id = ?", (class_id,)).fetchone():
                    raise ValueError("Choose an existing class.")
                values = (student_id, identity(student_id), full_name, export_filename,
                          identity(export_filename), class_id, status)
                if record_id is None:
                    return self.connection.execute("""
                        INSERT INTO students(student_id,student_key,full_name,export_filename,filename_key,class_id,status)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, values).lastrowid
                cursor = self.connection.execute("""
                    UPDATE students SET student_id=?,student_key=?,full_name=?,export_filename=?,filename_key=?,
                        class_id=?,status=? WHERE id=?
                """, (*values, record_id))
                self._require_row(cursor)
                return record_id
        except sqlite3.IntegrityError as exc:
            message = str(exc).casefold()
            if "filename" in message:
                raise ValueError("This filename already exists. Filenames must be unique across all students.") from exc
            raise ValueError("This student ID already exists. Student IDs must be unique across all classes.") from exc

    def delete_student(self, record_id):
        self.delete_students([record_id])

    def delete_students(self, record_ids):
        record_ids = list(dict.fromkeys(record_ids))
        if not record_ids:
            raise ValueError("Select at least one student.")
        placeholders = ",".join("?" for _ in record_ids)
        with self.transaction():
            found = self.connection.execute(
                f"SELECT COUNT(*) FROM students WHERE id IN ({placeholders})", record_ids
            ).fetchone()[0]
            if found != len(record_ids):
                raise ValueError("One or more students no longer exist. Refresh and try again.")
            self.connection.execute(f"DELETE FROM students WHERE id IN ({placeholders})", record_ids)

    @staticmethod
    def _require_row(cursor):
        if not cursor.rowcount:
            raise ValueError("This record no longer exists. Refresh the roster and try again.")
