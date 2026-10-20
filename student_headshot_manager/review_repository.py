"""Persistent final-take choices and per-image operator review flags."""
from .capture_repository import utc_now

FILTERS = ('All', 'Not photographed', 'Needs review', 'Selected', 'Retake required')
FLAGS = ('', 'Retake required', 'Processing adjustment', 'Manual review')


def migrate(db):
    db.connection.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS portrait_flags (
            capture_id TEXT PRIMARY KEY REFERENCES captures(id) ON DELETE CASCADE,
            flag TEXT NOT NULL CHECK(flag IN ('','Retake required','Processing adjustment','Manual review')),
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS final_portraits (
            student_id INTEGER PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
            capture_id TEXT NOT NULL UNIQUE REFERENCES captures(id) ON DELETE CASCADE,
            selected_at TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS invalidate_final_assignment
        AFTER UPDATE OF student_record_id,processing_status ON captures
        WHEN NEW.processing_status != 'imported' OR NEW.student_record_id IS NOT OLD.student_record_id
        BEGIN DELETE FROM final_portraits WHERE capture_id=NEW.id; END;
        COMMIT;
    """)
    if db.connection.execute("PRAGMA user_version").fetchone()[0] < 4:
        db.connection.execute("PRAGMA user_version = 4")


class ReviewRepository:
    def __init__(self, db):
        self.db = db

    def takes(self, student_id):
        return self.db.connection.execute("""SELECT c.*, COALESCE(f.flag,'') AS review_flag,
            CASE WHEN p.capture_id IS NULL THEN 0 ELSE 1 END AS selected,
            s.name AS session_name
            FROM captures c JOIN capture_sessions s ON s.id=c.session_id
            LEFT JOIN portrait_flags f ON f.capture_id=c.id
            LEFT JOIN final_portraits p ON p.capture_id=c.id AND p.student_id=c.student_record_id
            WHERE c.student_record_id=? AND c.processing_status NOT IN ('duplicate','discarded')
            ORDER BY c.received_at,c.rowid""", (student_id,)).fetchall()

    def students(self, class_id=None, filter_name='All'):
        rows = self.db.connection.execute("""SELECT s.*,cl.name AS class_name,
            COUNT(c.id) AS take_count,
            MAX(CASE WHEN f.flag='Retake required' THEN 1 ELSE 0 END) AS retake,
            MAX(CASE WHEN f.flag != '' THEN 1 ELSE 0 END) AS flagged,
            MAX(p.capture_id) AS final_capture_id
            FROM students s JOIN classes cl ON cl.id=s.class_id
            LEFT JOIN captures c ON c.student_record_id=s.id AND c.processing_status NOT IN ('duplicate','discarded')
            LEFT JOIN portrait_flags f ON f.capture_id=c.id
            LEFT JOIN final_portraits p ON p.student_id=s.id
            WHERE (? IS NULL OR s.class_id=?) GROUP BY s.id
            ORDER BY s.full_name COLLATE NOCASE,s.student_key""", (class_id,class_id)).fetchall()
        result = []
        for row in rows:
            row = dict(row)
            row['review_status'] = ('Retake required' if row['retake'] else 'Selected' if row['final_capture_id']
                                    else 'Needs review' if row['take_count'] else 'Not photographed')
            matches = (filter_name == 'All' or
                       (filter_name == 'Selected' and row['final_capture_id']) or
                       (filter_name == 'Needs review' and row['take_count'] and (not row['final_capture_id'] or row['flagged'])) or
                       row['review_status'] == filter_name)
            if matches:
                result.append(row)
        return result

    def select(self, student_id, capture_id):
        with self.db.transaction():
            photo = self.db.connection.execute("""SELECT c.*,COALESCE(f.flag,'') AS flag FROM captures c
                LEFT JOIN portrait_flags f ON f.capture_id=c.id WHERE c.id=?""", (capture_id,)).fetchone()
            if not photo or photo['student_record_id'] != student_id or photo['processing_status'] != 'imported':
                raise ValueError('Confirm this photograph’s assignment to this student in Capture first.')
            if photo['flag']:
                raise ValueError('Resolve and clear this take’s review flag before selecting it.')
            self.db.connection.execute("""INSERT INTO final_portraits VALUES (?,?,?)
                ON CONFLICT(student_id) DO UPDATE SET capture_id=excluded.capture_id,selected_at=excluded.selected_at""",
                (student_id,capture_id,utc_now()))

    def clear_selection(self, student_id):
        with self.db.transaction():
            self.db.connection.execute('DELETE FROM final_portraits WHERE student_id=?',(student_id,))

    def flag(self, capture_id, flag):
        if flag not in FLAGS:
            raise ValueError('Choose a valid review flag.')
        with self.db.transaction():
            self.db.connection.execute("""INSERT INTO portrait_flags VALUES (?,?,?)
                ON CONFLICT(capture_id) DO UPDATE SET flag=excluded.flag,updated_at=excluded.updated_at""",
                (capture_id,flag,utc_now()))
            if flag:
                self.db.connection.execute('DELETE FROM final_portraits WHERE capture_id=?',(capture_id,))
