"""Application setup and persistent data location."""
import argparse
import logging
from pathlib import Path
import sys
from PySide6.QtCore import QLockFile
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox
from .database import Database
from .maintenance import VERSION,data_directory,setup_logging,startup_backup,restore_database,validate_data_location
from .ui.main_window import MainWindow
from .ui.theme import apply_theme


def default_database_path():
    return data_directory()/'roster.sqlite3'


def main():
    parser=argparse.ArgumentParser(description='Student Headshot Manager')
    parser.add_argument('--database',type=Path,help='Use an alternate SQLite roster')
    parser.add_argument('--restore-backup',type=Path,help='Restore a database snapshot after confirmation')
    parser.add_argument('--self-test',type=Path,help='Run isolated synthetic diagnostics in a NEW directory')
    args=parser.parse_args()
    app=QApplication(sys.argv[:1]);app.setApplicationName('Student Headshot Manager');app.setApplicationVersion(VERSION)
    app.setOrganizationName('StudentHeadshotManager');apply_theme(app)
    assets=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent.parent))/'assets'
    app.setWindowIcon(QIcon(str(assets/'app.ico')))
    try:logs=setup_logging()
    except OSError as exc:
        QMessageBox.critical(None,'Cannot write application logs',f'Check your AppData permissions and free space.\n\n{exc}');return 1
    def exception_hook(kind,value,traceback):
        logging.error('Unhandled application error',exc_info=(kind,value,traceback))
        QMessageBox.critical(None,'Application error',f'{value}\n\nDetails were saved in {logs}. Close and reopen the app if needed.')
    sys.excepthook=exception_hook
    if args.self_test:
        from .self_test import run
        return run(args.self_test,app)
    database=(args.database or default_database_path()).resolve()
    if getattr(sys,'frozen',False) and database.is_relative_to(Path(sys.executable).resolve().parent):
        QMessageBox.critical(None,'Choose a writable data location','The live database must be outside the application installation directory.');return 1
    try:
        validate_data_location(database)
        database.parent.mkdir(parents=True,exist_ok=True)
    except (OSError,ValueError) as exc:
        logging.exception('Database location is not writable')
        QMessageBox.critical(None,'Unable to open roster',str(exc));return 1
    lock=QLockFile(str(database)+'.app.lock');lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.warning(None,'Roster already open','Another application instance is using this roster. Close it before opening or restoring the same database.');return 1
    db=None
    try:
        if args.restore_backup:
            answer=QMessageBox.question(None,'Restore roster backup','Replace this roster with the selected backup? A snapshot of the current valid roster will be kept. Photograph files remain unchanged.',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
            if answer!=QMessageBox.Yes:return 0
            restore_database(args.restore_backup,database)
        startup_backup(database)
        db=Database(database)
        from .capture_repository import CaptureRepository
        from .folders import Folders
        repo=CaptureRepository(db)
        if not repo.settings('folders'):
            base=data_directory()/'Photos'
            folders=Folders(*(str(base/name) for name in ('Incoming','Originals','Processed','Export')))
            Path(folders.incoming).mkdir(parents=True,exist_ok=True);folders.validate();repo.set_settings('folders',folders.as_dict())
        window=MainWindow(db);window.show();return app.exec()
    except Exception as exc:
        logging.exception('Application startup failed')
        QMessageBox.critical(None,'Unable to open roster',f'{exc}\n\nYour roster was not reset. Check permissions/free space or restore a known-good backup. Logs: {logs}');return 1
    finally:
        if db:db.close()
        lock.unlock()
