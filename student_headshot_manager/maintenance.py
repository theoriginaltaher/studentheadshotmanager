"""Local diagnostics, consistent SQLite backups and guarded startup recovery."""
from datetime import datetime, timezone
from contextlib import closing
import shutil
import faulthandler
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sqlite3
import sys
import uuid

VERSION='1.3.0'
_crash_file=None


def data_directory():
    return Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local'/'share'))/'StudentHeadshotManager'


def validate_data_location(path):
    resolved=Path(path).resolve()
    for key in ('ProgramFiles','ProgramFiles(x86)','ProgramW6432'):
        value=os.environ.get(key)
        if value and resolved.is_relative_to(Path(value).resolve()):
            raise ValueError('Choose a data folder outside Program Files. Use AppData, Documents or a local photo drive.')
    return resolved


def setup_logging():
    global _crash_file
    folder=data_directory()/'Logs';folder.mkdir(parents=True,exist_ok=True)
    handler=RotatingFileHandler(folder/'application.log',maxBytes=2_000_000,backupCount=5,encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s'))
    logging.getLogger().setLevel(logging.INFO);logging.getLogger().addHandler(handler)
    _crash_file=(folder/'crash.log').open('a',encoding='utf-8')
    faulthandler.enable(_crash_file)
    logging.info('Starting Student Headshot Manager %s; frozen=%s',VERSION,getattr(sys,'frozen',False))
    return folder


def check_backup(path):
    source=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        if source.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('SQLite integrity check failed.')
        tables={row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'students','classes'}.issubset(tables):raise ValueError('This is not a Student Headshot Manager roster.')
        if source.execute('PRAGMA user_version').fetchone()[0]>6:raise ValueError('This backup requires a newer application.')
    finally:source.close()


def backup_database(database, destination=None):
    path=Path(database).resolve();check_backup(path)
    folder=Path(destination) if destination else path.parent/'Backups'
    folder.mkdir(parents=True,exist_ok=True)
    target=folder/f'roster-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.sqlite3'
    target.touch(exist_ok=False)
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as source, closing(sqlite3.connect(target)) as output:
            source.backup(output)
        check_backup(target)
        logging.info('Database snapshot saved: %s',target)
        return target
    except Exception:
        target.unlink(missing_ok=True)
        raise


def startup_backup(database):
    path=Path(database)
    if not path.exists():return None
    check_backup(path)
    folder=path.parent/'Backups'
    today=datetime.now(timezone.utc).strftime('%Y%m%d')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as source:
        version=source.execute('PRAGMA user_version').fetchone()[0]
    if version<6 or not folder.exists() or not list(folder.glob(f'roster-{today}-*.sqlite3')):
        return backup_database(path)
    return None


def restore_database(backup,database):
    """Caller must own the application lock and have no open database/workers."""
    source=Path(backup).resolve();target=Path(database).resolve()
    if source==target:raise ValueError('Choose a backup different from the live database.')
    check_backup(source);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        try:backup_database(target)
        except (sqlite3.DatabaseError,ValueError):
            preserved=target.with_name(target.name+'.corrupt-'+uuid.uuid4().hex)
            with target.open('rb') as inp,preserved.open('xb') as out:shutil.copyfileobj(inp,out)
            logging.warning('Preserved damaged database at %s before restore',preserved)
    staging=target.with_name(target.name+'.restore-'+uuid.uuid4().hex)
    try:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as inp, closing(sqlite3.connect(staging)) as out:inp.backup(out)
        check_backup(staging)
        # Preserve any remaining old sidecars instead of attaching them to the restored database.
        suffix=uuid.uuid4().hex
        for extension in ('-wal','-shm'):
            sidecar=Path(str(target)+extension)
            if sidecar.exists():sidecar.rename(sidecar.with_name(sidecar.name+'.before-restore-'+suffix))
        os.replace(staging,target)
        logging.info('Restored database from %s; photo files were not changed',source)
    finally:staging.unlink(missing_ok=True)
