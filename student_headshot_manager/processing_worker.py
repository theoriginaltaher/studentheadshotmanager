"""Single local processing queue. QImage data may cross threads; widgets never do."""
import logging

import json
from pathlib import Path
import queue
import threading

import cv2
from PIL import Image
from PySide6.QtCore import QObject, QLockFile, Signal
from PySide6.QtGui import QImage

from .database import Database
from .processing import ProcessingSettings, load_oriented
from .processing_service import ProcessingService


def qt_image(image, limit=None):
    image = image.copy()
    if limit:
        image.thumbnail((limit, limit), Image.Resampling.LANCZOS)
    if image.mode == "L":
        channels, format_ = 1, QImage.Format_Grayscale8
    else:
        image = image.convert("RGBA")
        channels, format_ = 4, QImage.Format_RGBA8888
    raw = image.tobytes()
    return QImage(raw, image.width, image.height, image.width * channels, format_).copy()


def preview_payload(row, original_data, cached=False, full=False):
    original = load_oriented(original_data)
    payload = {"job": dict(row) if row else None, "cached": cached,
               "original": qt_image(original, None if full else 2048), "original_size": original.size}
    if row:
        outputs = json.loads(row["outputs_json"])
        for name in ("portrait", "cutout", "mask"):
            with Image.open(outputs[name]["path"]) as image:
                image.load()
                payload[name] = qt_image(image, None if full or name == "mask" else 2048)
    return payload


class ProcessingController(QObject):
    done = Signal(str, int, bool, object)
    progress = Signal(int, str)
    finished = Signal()

    def __init__(self, database_path, parent=None):
        super().__init__(parent)
        self.database_path = database_path
        self.commands = queue.Queue()
        self.stopping = threading.Event()
        self.thread = None

    def command(self, action, token, *args):
        if self.stopping.is_set():
            return
        self.commands.put((action, token, args))
        if not self.is_running():
            self.thread = threading.Thread(target=self.run, name="Local image processing", daemon=True)
            self.thread.start()

    def is_running(self):
        return self.thread is not None and self.thread.is_alive()

    def stop(self):
        self.stopping.set()

    def run(self):
        db = None
        lock = QLockFile(str(Path(self.database_path).resolve()) + ".processing.lock")
        lock.setStaleLockTime(0)
        try:
            if not lock.tryLock(0):
                raise ValueError("Another application instance owns the processing queue. Close it before processing here.")
            db = Database(self.database_path)
            service = ProcessingService(db)
            service.repo.recover_interrupted()
            cv2.setNumThreads(2)
            while not self.stopping.is_set():
                try:
                    action, token, args = self.commands.get(timeout=0.1)
                except queue.Empty:
                    continue
                try:
                    if action in ("preview", "process"):
                        capture_id, values = args
                        settings = ProcessingSettings(**values).validate()
                        if action == "process":
                            row, cached, data = service.process(capture_id, settings,
                                lambda message: self.progress.emit(token, message))
                        else:
                            self.progress.emit(token, "Loading local previews…")
                            row, data = service.preview(capture_id, settings)
                            cached = row is not None
                        payload = preview_payload(row, data, cached)
                    elif action in ("take_preview", "take_full"):
                        capture_id = args[0]
                        latest = service.repo.latest(capture_id)
                        settings = ProcessingSettings(**json.loads(latest['settings_json'])) if latest else ProcessingSettings()
                        row, data = service.preview(capture_id, settings)
                        payload = preview_payload(row, data, row is not None, full=action == "take_full")
                    elif action == "flag":
                        service.repo.flag(*args)
                        payload = {}
                    elif action == "approve":
                        service.approve(*args)
                        payload = {}
                    else:
                        payload = {}
                    self.done.emit(action, token, True, payload)
                except Exception as exc:
                    logging.exception('Background operation failed')
                    self.done.emit(action, token, False, {"error": str(exc)})
        except Exception as exc:
            logging.exception('Background operation failed')
            self.done.emit("worker", -1, False, {"error": str(exc)})
            while not self.commands.empty():
                try:
                    self.commands.get_nowait()
                except queue.Empty:
                    break
        finally:
            if db:
                db.close()
            lock.unlock()
            self.thread = None
            self.finished.emit()
