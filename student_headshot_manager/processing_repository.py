"""Processing jobs, versioned cache manifests and explicit human review."""

import json
import uuid

from .capture_repository import utc_now


def migrate(db):
    db.connection.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS processing_jobs (
            id TEXT PRIMARY KEY,
            capture_id TEXT NOT NULL REFERENCES captures(id),
            source_path TEXT,
            source_hash TEXT,
            settings_hash TEXT NOT NULL,
            settings_json TEXT NOT NULL,
            pipeline_version TEXT NOT NULL,
            cache_key TEXT,
            status TEXT NOT NULL CHECK(status IN ('running','awaiting_review','needs_review','approved','failed')),
            error_message TEXT,
            diagnostics_json TEXT,
            outputs_json TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS processing_cache ON processing_jobs(capture_id,cache_key);
        CREATE TABLE IF NOT EXISTS processing_reviews (
            capture_id TEXT PRIMARY KEY REFERENCES captures(id),
            manual_review INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            approved_job_id TEXT REFERENCES processing_jobs(id),
            updated_at TEXT NOT NULL
        );

        COMMIT;
    """)

    if db.connection.execute("PRAGMA user_version").fetchone()[0] < 3:
        db.connection.execute("PRAGMA user_version = 3")


class ProcessingRepository:
    def __init__(self, db):
        self.db = db

    def photos(self):
        return self.db.connection.execute("""SELECT c.*, s.name AS session_name
            FROM captures c JOIN capture_sessions s ON s.id=c.session_id
            WHERE c.processing_status='imported' AND c.original_copy_path IS NOT NULL
            ORDER BY c.received_at DESC,c.rowid DESC""").fetchall()

    def latest(self, capture_id):
        return self.db.connection.execute("SELECT * FROM processing_jobs WHERE capture_id=? ORDER BY rowid DESC LIMIT 1", (capture_id,)).fetchone()

    def get(self, job_id):
        row = self.db.connection.execute("SELECT * FROM processing_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise ValueError("This processing result no longer exists.")
        return row

    def cached(self, capture_id, key):
        return self.db.connection.execute("""SELECT * FROM processing_jobs WHERE capture_id=? AND cache_key=?
            AND status IN ('awaiting_review','needs_review','approved') ORDER BY rowid DESC""", (capture_id, key)).fetchall()

    def create(self, capture_id, settings, version, source_path=None, source_hash=None, key=None):
        job_id = uuid.uuid4().hex
        with self.db.transaction():
            self.db.connection.execute("""INSERT INTO processing_jobs
                (id,capture_id,source_path,source_hash,settings_hash,settings_json,pipeline_version,cache_key,status,started_at)
                VALUES (?,?,?,?,?,?,?,?,'running',?)""",
                (job_id, capture_id, source_path, source_hash, settings.digest(), settings.canonical_json(), version, key, utc_now()))
        return job_id

    def complete(self, job_id, diagnostics, outputs):
        with self.db.transaction():
            review = self.review(self.get(job_id)["capture_id"])
            status = "needs_review" if diagnostics["issues"] or (review and review["manual_review"]) else "awaiting_review"
            self.db.connection.execute("""UPDATE processing_jobs SET status=?,diagnostics_json=?,outputs_json=?,
                error_message=NULL,finished_at=? WHERE id=?""",
                (status, json.dumps(diagnostics), json.dumps(outputs), utc_now(), job_id))

    def fail(self, job_id, message):
        with self.db.transaction():
            self.db.connection.execute("UPDATE processing_jobs SET status='failed',error_message=?,finished_at=? WHERE id=?",
                                       (str(message), utc_now(), job_id))
            self.db.connection.execute("UPDATE processing_reviews SET approved_job_id=NULL WHERE approved_job_id=?", (job_id,))

    def review(self, capture_id):
        return self.db.connection.execute("SELECT * FROM processing_reviews WHERE capture_id=?", (capture_id,)).fetchone()

    def flag(self, capture_id, note):
        with self.db.transaction():
            self.db.connection.execute("UPDATE processing_jobs SET status='needs_review' WHERE capture_id=? AND status IN ('approved','awaiting_review')", (capture_id,))
            self.db.connection.execute("""INSERT INTO processing_reviews(capture_id,manual_review,note,updated_at)
                VALUES (?,1,?,?) ON CONFLICT(capture_id) DO UPDATE SET manual_review=1,note=excluded.note,
                approved_job_id=NULL,updated_at=excluded.updated_at""", (capture_id, note.strip() or "Operator requested manual review.", utc_now()))

    def approve(self, job_id):
        row = self.get(job_id)
        with self.db.transaction():
            self.db.connection.execute("UPDATE processing_jobs SET status='approved' WHERE id=?", (job_id,))
            self.db.connection.execute("""INSERT INTO processing_reviews(capture_id,manual_review,note,approved_job_id,updated_at)
                VALUES (?,0,'',?,?) ON CONFLICT(capture_id) DO UPDATE SET manual_review=0,note='',
                approved_job_id=excluded.approved_job_id,updated_at=excluded.updated_at""", (row["capture_id"], job_id, utc_now()))

    def recover_interrupted(self):
        with self.db.transaction():
            self.db.connection.execute("""UPDATE processing_jobs SET status='failed',
                error_message='Processing was interrupted. Run Process locally to retry.',finished_at=? WHERE status='running'""", (utc_now(),))
