"""Local file outputs, content-addressed reuse, and explicit result approval."""

import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import shutil

from PIL import Image

from .capture_repository import CaptureRepository
from .ingestion import file_hash
from . import processing
from .processing_repository import ProcessingRepository


def process_source_bytes(data, settings, source):
    parameters = inspect.signature(processing.process_bytes).parameters
    if len(parameters) >= 3:
        return processing.process_bytes(data, settings, source)
    return processing.process_bytes(data, settings)


class ProcessingService:
    def __init__(self, db):
        self.db = db
        self.captures = CaptureRepository(db)
        self.repo = ProcessingRepository(db)

    def source(self, capture_id):
        capture = self.captures.get(capture_id)
        if capture["processing_status"] != "imported" or not capture["original_copy_path"]:
            raise ValueError("Confirm this photograph's assignment in Capture before processing it.")
        return capture, Path(capture["original_copy_path"])

    @staticmethod
    def cache_key(source_hash, settings):
        return hashlib.sha256(f"{processing.PIPELINE_VERSION}:{source_hash}:{settings.digest()}".encode()).hexdigest()

    @staticmethod
    def outputs_valid(row):
        try:
            outputs = json.loads(row["outputs_json"])
            return all(Path(outputs[name]["path"]).is_file() and
                       file_hash(outputs[name]["path"]) == outputs[name]["sha256"]
                       for name in ("cutout", "portrait", "mask"))
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def matching(self, capture_id, source_hash, settings):
        for row in self.repo.cached(capture_id, self.cache_key(source_hash, settings)):
            if self.outputs_valid(row):
                return row
            self.repo.fail(row["id"], "Cached output is missing or modified. Run Process locally to rebuild it.")
        return None

    def output_root(self, source):
        config = self.captures.settings("folders")
        if not config:
            raise ValueError("Configure folders in Capture before processing photographs.")
        root = Path(config["processed"]).resolve()
        if not Path(config["processed"]).is_absolute():
            raise ValueError("Processed must be an absolute local folder path.")
        for name in ("incoming", "originals", "export"):
            other = Path(config[name]).resolve()
            if root == other or root in other.parents or other in root.parents:
                raise ValueError("Processed must be separate from Incoming, Originals, and Export.")
        if root == source.resolve() or root in source.resolve().parents:
            raise ValueError("Processed cannot contain the original image.")
        root.mkdir(parents=True, exist_ok=True)
        return root

    def save_output(self, job_id, capture_id, source, digest, settings, output, key):
        capture_root = self.output_root(source) / capture_id
        capture_root.mkdir(parents=True, exist_ok=True)
        staging = capture_root / f".pending-{job_id}"
        try:
            staging.mkdir()
            exif = Image.Exif()
            exif[274] = 1
            dpi = (settings.dpi, settings.dpi)
            specs = {
                "cutout": (output.cutout, "cutout.png", "PNG",
                           {"exif": exif.tobytes(), "icc_profile": output.icc_profile, "dpi": dpi}),
                "mask": (output.mask, "mask.png", "PNG", {"dpi": dpi}),
                "portrait": (output.portrait, "portrait.jpg", "JPEG",
                             {"quality": settings.jpeg_quality, "subsampling": 0, "exif": exif.tobytes(),
                              "icc_profile": output.icc_profile, "dpi": dpi}),
            }
            for image, filename, file_format, options in specs.values():
                with (staging / filename).open("xb") as file:
                    image.save(file, format=file_format, **options)
                    file.flush()
                    os.fsync(file.fileno())
            if file_hash(source) != digest:
                raise ValueError("Original changed during processing. The result was not accepted; retry with a stable original.")
            destination = capture_root / f"{key[:16]}-{job_id}"
            staging.rename(destination)
            outputs = {name: {"path": str(destination / spec[1]), "sha256": file_hash(destination / spec[1])}
                       for name, spec in specs.items()}
            self.repo.complete(job_id, output.diagnostics, outputs)
            return self.repo.get(job_id)
        finally:
            if staging.exists() and staging.parent.resolve() == capture_root.resolve() and staging.name == f".pending-{job_id}":
                shutil.rmtree(staging)

    def process(self, capture_id, settings, phase=lambda message: None):
        settings.validate()
        job_id = None
        staging = None
        capture_root = None
        source = None
        try:
            phase("Reading original and checking cache…")
            capture, source = self.source(capture_id)
            data, digest = processing.read_original(source)
            cached = self.matching(capture_id, digest, settings)
            if cached:
                return cached, True, (data, source)
            key = self.cache_key(digest, settings)
            job_id = self.repo.create(capture_id, settings, processing.PIPELINE_VERSION, str(source), digest, key)
            phase("Building the full-resolution mask and portrait…")
            output = process_source_bytes(data, settings, source)
            output.diagnostics["source_size"] = list(output.cutout.size)
            output.diagnostics["portrait_size"] = list(output.portrait.size)
            if capture["content_hash"] != digest:
                output.diagnostics["issues"].append("The archived original differs from the accepted capture. Verify the original and its assignment.")
                output.diagnostics["blocking"] = True
            phase("Saving and verifying local output files…")
            row = self.save_output(job_id, capture_id, source, digest, settings, output, key)
            staging = None
            return row, False, (data, source)
        except Exception as exc:
            if job_id is None:
                job_id = self.repo.create(capture_id, settings, processing.PIPELINE_VERSION, str(source) if source else None)
            self.repo.fail(job_id, str(exc))
            raise
        finally:
            if staging and staging.exists():
                # Only remove this job's owned staging directory within Processed.
                if staging.parent.resolve() == capture_root.resolve() and staging.name == f".pending-{job_id}":
                    shutil.rmtree(staging)

    def apply_mask(self, job_id, mask_data, phase=lambda message: None):
        previous = self.repo.get(job_id)
        capture_id = previous["capture_id"]
        capture, source = self.source(capture_id)
        settings = processing.ProcessingSettings(**json.loads(previous["settings_json"])).validate()
        data, digest = processing.read_original(source)
        if digest != previous["source_hash"]:
            raise ValueError("The archived original changed. Reprocess before editing its mask.")
        try:
            with Image.open(io.BytesIO(mask_data)) as opened:
                opened.load()
                mask = opened.convert("L")
        except (OSError, ValueError) as exc:
            raise ValueError("The edited mask could not be read.") from exc
        phase("Rebuilding the cutout and square portrait from the edited mask…")
        output = processing.process_with_mask(data, settings, mask, source)
        output.diagnostics["source_size"] = list(output.cutout.size)
        output.diagnostics["portrait_size"] = list(output.portrait.size)
        mask_hash = hashlib.sha256(mask_data).hexdigest()
        key = hashlib.sha256(f"{self.cache_key(digest, settings)}:manual:{mask_hash}".encode()).hexdigest()
        new_job = self.repo.create(capture_id, settings, processing.PIPELINE_VERSION, str(source), digest, key)
        try:
            return self.save_output(new_job, capture_id, source, digest, settings, output, key), (data, source)
        except Exception as exc:
            self.repo.fail(new_job, str(exc))
            raise

    def preview(self, capture_id, settings):
        _, source = self.source(capture_id)
        data, digest = processing.read_original(source)
        return self.matching(capture_id, digest, settings), data, source

    def approve(self, job_id, acknowledged=False):
        row = self.repo.get(job_id)
        if row["status"] not in ("awaiting_review", "needs_review", "approved") or not row["diagnostics_json"]:
            raise ValueError("Process a valid result before approving it.")
        diagnostics = json.loads(row["diagnostics_json"])
        if diagnostics["blocking"]:
            raise ValueError("Separation could not be established. Adjust settings and reprocess, or flag for manual review.")
        review = self.repo.review(row["capture_id"])
        if (diagnostics["issues"] or (review and review["manual_review"])) and not acknowledged:
            raise ValueError("Inspect the warnings and explicitly acknowledge them before approving.")
        _, source = self.source(row["capture_id"])
        if file_hash(source) != row["source_hash"] or not self.outputs_valid(row):
            self.repo.fail(job_id, "Original or output files changed. Reprocess before approval.")
            raise ValueError("Original or output files changed. Reprocess before approval.")
        self.repo.approve(job_id)
