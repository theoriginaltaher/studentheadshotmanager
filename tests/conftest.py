import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from student_headshot_manager.database import Database


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "roster.sqlite3")
    yield database
    database.close()


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    from student_headshot_manager.ui.theme import apply_theme
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    return app


@pytest.fixture
def processing_capture(db, tmp_path):
    from pathlib import Path
    from samples.generate_processing_samples import make_sample
    from student_headshot_manager.capture_repository import CaptureRepository
    from student_headshot_manager.folders import Folders
    from student_headshot_manager.ingestion import IngestionEngine
    repo = CaptureRepository(db)
    student = db.save_student("001", "Synthetic Subject", db.create_class("Test class"))
    session = repo.create_session("Synthetic processing test")
    folders = Folders(*(str(tmp_path / name) for name in ("Incoming", "Originals", "Processed", "Export")))
    Path(folders.incoming).mkdir()
    folders.validate()
    repo.set_settings("folders", folders.as_dict())
    incoming = Path(folders.incoming) / "synthetic.jpg"
    make_sample(size=(120, 160)).save(incoming, "JPEG", quality=98, subsampling=0)
    clock = [0.0]
    engine = IngestionEngine(repo, folders, clock=lambda: clock[0])
    engine.discover(incoming, repo.assignment(student, session))
    clock[0] = 3
    engine.tick()
    photo = repo.photos()[0]
    engine.accept(photo["id"])
    return photo["id"], Path(repo.get(photo["id"])["original_copy_path"]), incoming, folders
