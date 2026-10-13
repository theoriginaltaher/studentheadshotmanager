"""Testable, Qt-independent JPEG settling, verification and original archiving."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import time
import uuid
import warnings

from PIL import Image, ImageOps

JPEG_EXTENSIONS = {".jpg", ".jpeg"}
MAX_IMAGE_BYTES = 100 * 1024 * 1024


def file_signature(path):
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_jpeg(path, expected_signature):
    """Decode all pixels and check the file again after reading, not just its header."""
    if expected_signature[0] > MAX_IMAGE_BYTES:
        raise ValueError("JPEG exceeds the 100 MB per-image limit.")
    before = file_signature(path)
    with Path(path).open("rb") as source:
        data = source.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("JPEG exceeds the 100 MB per-image limit.")
    if before != expected_signature or file_signature(path) != before:
        raise ValueError("File is still changing.")
    if not data.startswith(b"\xff\xd8") or not data.rstrip(b"\x00\r\n ").endswith(b"\xff\xd9"):
        raise ValueError("JPEG is incomplete or has no end-of-image marker.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "JPEG":
                raise ValueError("Only JPEG images are supported.")
            image.load()  # Pillow rejects truncated image data by default.
            thumb = ImageOps.exif_transpose(image)
            thumb.thumbnail((192, 144))
            thumb = thumb.convert("RGB")
            output = io.BytesIO()
            thumb.save(output, "JPEG", quality=82)
    if file_signature(path) != before:
        raise ValueError("File changed during JPEG validation.")
    captured = datetime.fromtimestamp(before[1] / 1e9, timezone.utc).isoformat()
    return hashlib.sha256(data).hexdigest(), output.getvalue(), captured


@dataclass
class Pending:
    capture_id: str
    path: Path
    signature: tuple
    last_change: float
    last_attempt: float = float("-inf")


class IngestionEngine:
    def __init__(self, repository, folders, *, settle_seconds=2.0, incomplete_timeout=30.0, clock=time.monotonic):
        self.repo = repository
        self.folders = folders
        self.settle_seconds = settle_seconds
        self.incomplete_timeout = incomplete_timeout
        self.clock = clock
        self.pending = {}

    def discover(self, path, assignment):
        if assignment is None or self.folders is None:
            return False
        path = Path(path)
        if path.suffix.lower() not in JPEG_EXTENSIONS or path.is_symlink() or path.is_dir():
            return False
        path = path.resolve()
        if path.parent != Path(self.folders.incoming).resolve():
            return False
        source_key = os.path.normcase(str(path))
        if source_key in self.pending:
            return False  # First detection owns the proposed student/session.
        try:
            signature = file_signature(path)
        except FileNotFoundError:
            return False
        except OSError:
            signature = (None, None)
        previous = self.repo.latest_source(source_key)
        if previous:
            if (previous["source_size"], previous["source_mtime_ns"]) == signature:
                return False
            if previous["processing_status"] == "failed" and previous["failure_stage"] == "ingestion":
                return False  # A visible failed row is recovered explicitly with Retry.
        capture_id = self.repo.begin_receiving(path, source_key, signature, assignment)
        self.pending[source_key] = Pending(capture_id, path, signature, self.clock())
        return True

    def tick(self):
        changed = False
        now = self.clock()
        for key, pending in list(self.pending.items()):
            try:
                signature = file_signature(pending.path)
                if signature != pending.signature:
                    pending.signature = signature
                    pending.last_change = now
                    self.repo.signature(pending.capture_id, signature)
                    continue
                if now - pending.last_change < self.settle_seconds or now - pending.last_attempt < 1:
                    continue
                pending.last_attempt = now
                digest, thumbnail, captured = inspect_jpeg(pending.path, signature)
            except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
                if now - pending.last_change < self.incomplete_timeout:
                    continue
                self.repo.fail(pending.capture_id, f"Could not read a complete JPEG: {exc}")
            else:
                self.repo.ready(pending.capture_id, signature, digest, thumbnail, captured)
            del self.pending[key]
            changed = True
        return changed

    def retry(self, capture_id):
        row = self.repo.get(capture_id)
        if row["processing_status"] != "failed":
            raise ValueError("Only failed imports can be retried.")
        if row["failure_stage"] == "copy":
            self.accept(capture_id)
            return
        self.repo.prepare_retry(capture_id)
        path = Path(row["original_path"])
        try:
            signature = file_signature(path)
        except OSError:
            signature = (None, None)
        self.pending[row["source_key"]] = Pending(capture_id, path, signature, self.clock())

    def accept(self, capture_id):
        row = self.repo.get(capture_id)
        if not row["content_hash"]:
            raise ValueError("Wait for a complete JPEG before confirming assignment.")
        if self.folders is None:
            raise ValueError("Configure the Originals folder first.")
        if row["processing_status"] not in ("awaiting_confirmation", "failed"):
            raise ValueError("This photograph is not awaiting confirmation.")
        claimed = False
        try:
            # An interrupted copy or a reassignment can retain a verified original.
            previous = Path(row["original_copy_path"]) if row["original_copy_path"] else None
            if previous and previous.is_file() and file_hash(previous) == row["content_hash"]:
                self.repo.claim_copy(capture_id, previous)
                claimed = True
                self.repo.imported(capture_id)
                return
            safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", row["student_id"])[:40]
            folder = (Path(self.folders.originals) / row["received_at"][:10] /
                      row["session_id"] / f"student-{safe_id}-{row['student_record_id'] or 'archived'}")
            folder.mkdir(parents=True, exist_ok=True)
            destination = folder / f"{capture_id}-{uuid.uuid4().hex}.jpg"
            self.repo.claim_copy(capture_id, destination)
            claimed = True
            copy_verified(Path(row["original_path"]), destination, row["content_hash"])
            self.repo.imported(capture_id)
        except (OSError, ValueError) as exc:
            # Storage failures before claim are retryable too; invalid state is not.
            if claimed or self.repo.get(capture_id)["processing_status"] in ("awaiting_confirmation", "failed"):
                self.repo.fail(capture_id, f"Original copy failed: {exc}", "copy")
            raise


def copy_verified(source, destination, expected_hash):
    """Never open source for writing. Publish without overwriting another file."""
    temporary = destination.with_suffix(".part")
    before = file_signature(source)
    created_destination = False
    created_temporary = False
    try:
        digest = hashlib.sha256()
        with source.open("rb") as incoming, temporary.open("xb") as outgoing:
            created_temporary = True
            for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                outgoing.write(chunk)
                digest.update(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if digest.hexdigest() != expected_hash or file_signature(source) != before:
            raise ValueError("Incoming file changed after preview. It was not accepted. Review the new incoming version.")
        # Exclusive creation works on Windows, network volumes and non-NTFS disks.
        # The database stays 'copying' until the published file is fully durable.
        with temporary.open("rb") as staged, destination.open("xb") as published:
            created_destination = True
            shutil.copyfileobj(staged, published, 1024 * 1024)
            published.flush()
            os.fsync(published.fileno())
        if file_hash(destination) != expected_hash:
            raise OSError("Archived bytes failed verification.")
    except Exception:
        if created_destination:
            destination.unlink(missing_ok=True)
        raise
    finally:
        # Only our staging file is removed. Incoming files are always untouched.
        if created_temporary:
            temporary.unlink(missing_ok=True)
