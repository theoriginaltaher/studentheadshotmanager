"""Additive capture schema and persistence; roster CRUD remains independent."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import uuid

from .database import clean


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Assignment:
    student_record_id: int | None
    student_id: str
    student_name: str
    session_id: str


def migrate(db):
    """Safe to run on a v1 roster. No student/class table is rebuilt."""
    db.connection.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS capture_sessions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            started_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS captures (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES capture_sessions(id),
            student_record_id INTEGER REFERENCES students(id) ON DELETE SET NULL,
            student_id TEXT NOT NULL,
            student_name TEXT NOT NULL,
            original_path TEXT NOT NULL,
            source_key TEXT NOT NULL,
            source_size INTEGER,
            source_mtime_ns INTEGER,
            received_at TEXT NOT NULL,
            capture_time TEXT,
            capture_time_source TEXT NOT NULL DEFAULT 'file modification time (UTC)',
            content_hash TEXT UNIQUE,
            processing_status TEXT NOT NULL CHECK(processing_status IN
                ('receiving','awaiting_confirmation','copying','imported','discarded','failed','duplicate')),
            error_message TEXT,
            failure_stage TEXT,
            original_copy_path TEXT,
            thumbnail BLOB,
            duplicate_of TEXT REFERENCES captures(id),
            confirmed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS captures_source ON captures(source_key);
        CREATE INDEX IF NOT EXISTS captures_session ON captures(session_id, received_at);
        CREATE TABLE IF NOT EXISTS capture_events (
            id INTEGER PRIMARY KEY,
            capture_id TEXT NOT NULL REFERENCES captures(id),
            event_time TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT NOT NULL
        );
        COMMIT;
    """)
    if db.connection.execute("PRAGMA user_version").fetchone()[0] < 2:
        db.connection.execute("PRAGMA user_version = 2")


class CaptureRepository:
    def __init__(self, db):
        self.db = db

    def settings(self, key, default=None):
        row = self.db.connection.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_settings(self, key, value):
        with self.db.transaction():
            self.db.connection.execute("INSERT INTO app_settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                       (key, json.dumps(value)))

    def create_session(self, name):
        name = clean(name, "Session name")
        session_id = uuid.uuid4().hex
        with self.db.transaction():
            self.db.connection.execute("INSERT INTO capture_sessions VALUES (?, ?, ?)", (session_id, name, utc_now()))
        return session_id

    def sessions(self):
        return self.db.connection.execute("SELECT * FROM capture_sessions ORDER BY started_at DESC, rowid DESC").fetchall()

    def assignment(self, student_record_id, session_id):
        student = self.db.student(student_record_id)
        if not student:
            raise ValueError("Select an existing student.")
        if not self.db.connection.execute("SELECT 1 FROM capture_sessions WHERE id=?", (session_id,)).fetchone():
            raise ValueError("Select a capture session.")
        return Assignment(student_record_id, student["student_id"], student["full_name"], session_id)

    def unassigned(self, session_id):
        if not self.db.connection.execute("SELECT 1 FROM capture_sessions WHERE id=?", (session_id,)).fetchone():
            raise ValueError("Select a capture session.")
        return Assignment(None, "", "Unassigned", session_id)

    def get(self, capture_id):
        row = self.db.connection.execute("SELECT * FROM captures WHERE id=?", (capture_id,)).fetchone()
        if not row:
            raise ValueError("This photograph no longer exists.")
        return row

    def photos(self, session_id=None):
        return self.db.connection.execute(
            "SELECT * FROM captures WHERE (? IS NULL OR session_id=?) ORDER BY received_at DESC, rowid DESC",
            (session_id, session_id),
        ).fetchall()

    def latest_source(self, source_key):
        return self.db.connection.execute("SELECT * FROM captures WHERE source_key=? ORDER BY rowid DESC LIMIT 1", (source_key,)).fetchone()

    def begin_receiving(self, path, source_key, signature, assignment):
        capture_id = uuid.uuid4().hex
        with self.db.transaction():
            # Keep the identity snapshot even if the roster entry was just deleted.
            live = self.db.student(assignment.student_record_id) if assignment.student_record_id is not None else None
            record_id = assignment.student_record_id if live and live["student_id"] == assignment.student_id else None
            self.db.connection.execute("""
                INSERT INTO captures(id,session_id,student_record_id,student_id,student_name,
                    original_path,source_key,source_size,source_mtime_ns,received_at,capture_time,processing_status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,'receiving')
            """, (capture_id, assignment.session_id, record_id, assignment.student_id, assignment.student_name,
                  str(path), source_key, *signature, utc_now(),
                  datetime.fromtimestamp(signature[1] / 1e9, timezone.utc).isoformat() if signature[1] else utc_now()))
            detail = (f"Proposed: {assignment.student_name} ({assignment.student_id})"
                      if record_id is not None else "Imported for batch assignment")
            self.event(capture_id, "detected", detail)
        return capture_id

    def event(self, capture_id, action, detail):
        self.db.connection.execute("INSERT INTO capture_events(capture_id,event_time,action,detail) VALUES (?,?,?,?)",
                                   (capture_id, utc_now(), action, detail))

    def ready(self, capture_id, signature, content_hash, thumbnail, capture_time):
        with self.db.transaction():
            # Obtain a write lock before testing the globally unique content hash.
            self.db.connection.execute("UPDATE captures SET error_message=NULL WHERE id=?", (capture_id,))
            other = self.db.connection.execute("SELECT id,student_id FROM captures WHERE content_hash=? AND id<>?",
                                               (content_hash, capture_id)).fetchone()
            if other:
                self.db.connection.execute("""UPDATE captures SET processing_status='duplicate', duplicate_of=?,
                    error_message=?,source_size=?,source_mtime_ns=?,thumbnail=?,capture_time=? WHERE id=?""",
                    (other["id"], f"Identical bytes already received for student {other['student_id']}. No second import.",
                     *signature, thumbnail, capture_time, capture_id))
                self.event(capture_id, "duplicate", other["id"])
                return False
            self.db.connection.execute("""UPDATE captures SET processing_status='awaiting_confirmation',
                content_hash=?,thumbnail=?,capture_time=?,source_size=?,source_mtime_ns=?,
                error_message=NULL,failure_stage=NULL WHERE id=?""",
                (content_hash, thumbnail, capture_time, *signature, capture_id))
            self.event(capture_id, "ready", "Image verified; operator confirmation required")
        return True

    def fail(self, capture_id, message, stage="ingestion"):
        with self.db.transaction():
            self.db.connection.execute("UPDATE captures SET processing_status='failed',error_message=?,failure_stage=? WHERE id=?",
                                       (str(message), stage, capture_id))
            self.event(capture_id, "failed", str(message))

    def signature(self, capture_id, signature):
        with self.db.transaction():
            self.db.connection.execute("UPDATE captures SET source_size=?,source_mtime_ns=? WHERE id=?", (*signature, capture_id))

    def prepare_retry(self, capture_id):
        with self.db.transaction():
            row = self.get(capture_id)
            if row["processing_status"] != "failed":
                raise ValueError("Only failed imports can be retried.")
            self.db.connection.execute("UPDATE captures SET processing_status='receiving',error_message=NULL WHERE id=?", (capture_id,))
            self.event(capture_id, "retry", "Retry requested by operator")

    def claim_copy(self, capture_id, destination):
        with self.db.transaction():
            row = self.get(capture_id)
            if row["student_record_id"] is None:
                raise ValueError("Assign this photograph to a student before confirming it.")
            cursor = self.db.connection.execute("""UPDATE captures SET processing_status='copying',
                original_copy_path=?,confirmed_at=?,error_message=NULL,failure_stage='copy'
                WHERE id=? AND (processing_status='awaiting_confirmation' OR
                    (processing_status='failed' AND failure_stage='copy'))""", (str(destination), utc_now(), capture_id))
            if not cursor.rowcount:
                raise ValueError("This photo is not awaiting confirmation or is already being copied.")
            self.event(capture_id, "confirmed", str(destination))

    def imported(self, capture_id):
        with self.db.transaction():
            row = self.get(capture_id)
            self.db.connection.execute("UPDATE captures SET processing_status='imported',error_message=NULL,failure_stage=NULL WHERE id=?", (capture_id,))
            self.db.connection.execute("UPDATE students SET status='Captured' WHERE id=? AND status='Not photographed'", (row["student_record_id"],))
            self.event(capture_id, "imported", row["original_copy_path"])

    def reassign(self, capture_id, student_record_id):
        with self.db.transaction():
            row = self.get(capture_id)
            if row["processing_status"] in ("receiving", "copying", "duplicate"):
                raise ValueError("Wait for ingestion to finish, or use the original record for a duplicate.")
            assignment = self.assignment(student_record_id, row["session_id"])
            status = "failed" if not row["content_hash"] else "awaiting_confirmation"
            self.db.connection.execute("""UPDATE captures SET student_record_id=?,student_id=?,student_name=?,
                processing_status=?,confirmed_at=NULL WHERE id=?""",
                (assignment.student_record_id, assignment.student_id, assignment.student_name, status, capture_id))
            self.event(capture_id, "reassigned", f"{row['student_id']} -> {assignment.student_id}; confirm again")

    def discard(self, capture_id):
        with self.db.transaction():
            row = self.get(capture_id)
            if row["processing_status"] in ("receiving", "copying", "duplicate"):
                raise ValueError("Wait for this photograph to finish arriving before discarding its assignment.")
            self.db.connection.execute("UPDATE captures SET processing_status='discarded' WHERE id=?", (capture_id,))
            self.event(capture_id, "discarded", "Assignment discarded; incoming and archived files retained")

    def recover_interrupted(self):
        rows = self.db.connection.execute("SELECT id,processing_status FROM captures WHERE processing_status IN ('receiving','copying')").fetchall()
        for row in rows:
            self.fail(row["id"], "The application stopped during import. Use Retry import to recover.",
                      "copy" if row["processing_status"] == "copying" else "ingestion")
