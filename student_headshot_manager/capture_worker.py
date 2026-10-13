"""Watchdog notifications and ingestion commands run outside the GUI thread."""
import logging

from pathlib import Path
import queue
import threading
import time

from PySide6.QtCore import QObject, QLockFile, Signal
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .capture_repository import CaptureRepository
from .database import Database
from .folders import Folders
from .ingestion import IngestionEngine


class IncomingHandler(FileSystemEventHandler):
    def __init__(self, callback):
        self.callback = callback

    def on_created(self, event):
        if not event.is_directory:
            self.callback(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self.callback(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self.callback(event.dest_path)


class CaptureController(QObject):
    changed = Signal()
    status = Signal(str, bool)
    result = Signal(str, bool, str)
    finished = Signal()

    def __init__(self, database_path, parent=None):
        super().__init__(parent)
        self.database_path = database_path
        self.commands = queue.Queue()
        self.events = queue.Queue()
        self.guard = threading.Lock()
        self.assignment = None
        self.accepting_events = False
        self.stopping = threading.Event()
        self.thread = None

    def set_assignment(self, assignment):
        with self.guard:
            self.assignment = assignment

    def context(self):
        with self.guard:
            return self.assignment if self.accepting_events else None

    def on_path(self, path):
        assignment = self.context()
        if assignment is not None:
            self.events.put((path, assignment))

    def command(self, action, *args):
        if self.stopping.is_set():
            return
        if action == "pause":
            with self.guard:
                self.accepting_events = False
        self.commands.put((action, args))
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self.run, name="JPEG ingestion", daemon=True)
            self.thread.start()

    def stop(self):
        with self.guard:
            self.accepting_events = False
        self.stopping.set()

    def is_running(self):
        return self.thread is not None and self.thread.is_alive()

    def run(self):
        db = None
        observer = None
        lock = QLockFile(str(Path(self.database_path).resolve()) + ".capture.lock")
        lock.setStaleLockTime(0)
        try:
            if not lock.tryLock(0):
                raise ValueError("Another application instance owns this capture workspace. Close it before starting ingestion.")
            db = Database(self.database_path)
            repo = CaptureRepository(db)
            repo.recover_interrupted()
            config = repo.settings("folders")
            folders = Folders(**config) if config else None
            engine = IngestionEngine(repo, folders)
            self.changed.emit()
            next_scan = 0.0
            while not self.stopping.is_set():
                try:
                    action, args = self.commands.get(timeout=0.1)
                except queue.Empty:
                    action = None
                if action:
                    try:
                        if action == "configure":
                            if observer:
                                raise ValueError("Pause the watcher before changing folders.")
                            folders = Folders(**args[0]).validate()
                            repo.set_settings("folders", folders.as_dict())
                            engine.folders = folders
                            self.status.emit("Paused · folders configured", False)
                        elif action == "resume":
                            if folders is None:
                                raise ValueError("Configure the four folders before starting the watcher.")
                            if self.assignment is None:
                                raise ValueError("Select a student and capture session first.")
                            folders = folders.validate()
                            engine.folders = folders
                            if not observer:
                                observer = Observer()
                                observer.schedule(IncomingHandler(self.on_path), folders.incoming, recursive=False)
                                observer.start()
                            with self.guard:
                                self.accepting_events = True
                            next_scan = 0
                            self.status.emit("Watching · Incoming folder connected", True)
                        elif action == "pause":
                            if observer:
                                observer.stop()
                                observer.join(timeout=3)
                                observer = None
                            self.status.emit("Paused · already detected files may finish", False)
                        elif action == "confirm":
                            engine.accept(args[0])
                        elif action == "reassign":
                            repo.reassign(*args)
                        elif action == "discard":
                            repo.discard(args[0])
                        elif action == "retry":
                            engine.retry(args[0])
                        self.changed.emit()
                        self.result.emit(action, True, "")
                    except Exception as exc:
                        logging.exception('Background operation failed')
                        self.changed.emit()
                        self.result.emit(action, False, str(exc))
                        if action == "resume":
                            with self.guard:
                                self.accepting_events = False
                            if observer:
                                observer.stop()
                                observer.join(timeout=3)
                                observer = None
                            self.status.emit(f"Disconnected · {exc}", False)
                # A periodic scan catches notifications missed by a device/filesystem.
                if observer and time.monotonic() >= next_scan:
                    try:
                        if not observer.is_alive() or any(not emitter.is_alive() for emitter in observer.emitters):
                            raise OSError("Folder observer stopped. Resume to reconnect.")
                        for path in Path(folders.incoming).iterdir():
                            self.on_path(str(path))
                        next_scan = time.monotonic() + 2
                    except OSError as exc:
                        with self.guard:
                            self.accepting_events = False
                        observer.stop()
                        observer.join(timeout=3)
                        observer = None
                        self.status.emit(f"Disconnected · {exc}", False)
                changed = False
                # Bounded batches leave commands responsive during a large folder scan.
                for _ in range(100):
                    try:
                        path, assignment = self.events.get_nowait()
                    except queue.Empty:
                        break
                    changed = engine.discover(path, assignment) or changed
                changed = engine.tick() or changed
                if changed:
                    self.changed.emit()
        except Exception as exc:
            logging.exception('Background operation failed')
            self.status.emit(f"Disconnected · {exc}", False)
            self.result.emit("worker", False, str(exc))
            # Do not execute stale queued confirmations after a fatal worker error.
            while not self.commands.empty():
                try:
                    self.commands.get_nowait()
                except queue.Empty:
                    break
        finally:
            with self.guard:
                self.accepting_events = False
            if observer:
                observer.stop()
                observer.join(timeout=3)
            if db:
                db.close()
            lock.unlock()
            self.thread = None
            self.finished.emit()
