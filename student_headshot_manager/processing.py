"""Local chroma key and compositing. No networking or cloud inference."""

from dataclasses import asdict, dataclass
import hashlib
import io
import json
from pathlib import Path
import warnings

import cv2
import numpy as np
from PIL import Image, ImageOps

PIPELINE_VERSION = "local-chroma-key-2"
MAX_PIXELS = 40_000_000
MAX_BYTES = 250 * 1024 * 1024
RAW_EXTENSIONS = {".cr2", ".cr3", ".nef"}


@dataclass(frozen=True)
class ProcessingSettings:
    hue_low: int = 35
    hue_high: int = 85
    saturation_low: int = 60
    saturation_high: int = 255
    value_low: int = 40
    value_high: int = 255
    noise_radius: int = 1
    holes_radius: int = 2
    edge_shift: int = -1
    feather: float = 1.2
    spill_strength: float = 0.65
    spill_width: int = 8
    grey: int = 180
    background_r: int | None = None
    background_g: int | None = None
    background_b: int | None = None
    canvas_width: int = 1080
    canvas_height: int = 1080
    dpi: int = 300
    padding_percent: float = 4.0
    subject_scale: float = 100.0
    offset_x: int = 0
    offset_y: int = 0
    jpeg_quality: int = 95

    def validate(self):
        integers = {
            "hue_low": (0, 179), "hue_high": (0, 179),
            "saturation_low": (0, 255), "saturation_high": (0, 255),
            "value_low": (0, 255), "value_high": (0, 255),
            "noise_radius": (0, 20), "holes_radius": (0, 20), "edge_shift": (-20, 20),
            "spill_width": (0, 100), "grey": (0, 255),
            "canvas_width": (16, 10000), "canvas_height": (16, 10000),
            "dpi": (72, 1200), "offset_x": (-10000, 10000), "offset_y": (-10000, 10000),
            "jpeg_quality": (85, 100),
        }
        for name, (low, high) in integers.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name.replace('_', ' ').title()} must be an integer from {low} to {high}.")
        for name in ("background_r", "background_g", "background_b"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or not 0 <= value <= 255):
                raise ValueError(f"{name.replace('_', ' ').title()} must be an integer from 0 to 255.")
        for name, low, high in (("feather", 0, 20), ("spill_strength", 0, 1),
                                ("padding_percent", 0, 25), ("subject_scale", 10, 400)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not low <= value <= high:
                raise ValueError(f"{name.replace('_', ' ').title()} must be from {low} to {high}.")
        for prefix in ("hue", "saturation", "value"):
            if getattr(self, prefix + "_low") > getattr(self, prefix + "_high"):
                raise ValueError(f"{prefix.title()} low must not exceed high.")
        if self.canvas_width * self.canvas_height > MAX_PIXELS:
            raise ValueError("The portrait canvas must not exceed 40 megapixels.")
        return self

    def canonical_json(self):
        self.validate()
        values = asdict(self)
        for name in ("feather", "spill_strength", "padding_percent", "subject_scale"):
            values[name] = float(values[name])
        return json.dumps(values, sort_keys=True, separators=(",", ":"))

    def digest(self):
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    def background_rgb(self):
        if None in (self.background_r, self.background_g, self.background_b):
            return (self.grey, self.grey, self.grey)
        return (self.background_r, self.background_g, self.background_b)


def read_original(path):
    path = Path(path)
    before = path.stat()
    if before.st_size > MAX_BYTES:
        raise ValueError("Original exceeds the 250 MB image limit.")
    with path.open("rb") as file:
        data = file.read(MAX_BYTES + 1)
    after = path.stat()
    if len(data) > MAX_BYTES or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Original changed while it was being read. Retry after the file is stable.")
    return data, hashlib.sha256(data).hexdigest()


def rawpy_module():
    try:
        import rawpy
    except ImportError as exc:
        raise ValueError("RAW support requires the rawpy package.") from exc
    return rawpy


def load_raw(path):
    if path is None:
        raise ValueError("RAW processing requires access to the archived original file.")
    rawpy = rawpy_module()
    with rawpy.imread(str(path)) as raw:
        rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
    if rgb.shape[0] * rgb.shape[1] > MAX_PIXELS:
        raise ValueError("Original exceeds 40 megapixels. It was not downscaled or changed.")
    return Image.fromarray(rgb).convert("RGB")


def load_oriented(data, source_path=None):
    """Apply EXIF orientation without resampling; output coordinates are upright."""
    path = Path(source_path) if source_path else None
    if path and path.suffix.lower() in RAW_EXTENSIONS:
        return load_raw(path)
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as source:
            if source.format != "JPEG":
                raise ValueError("The original must be a JPEG.")
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("Original exceeds 40 megapixels. It was not downscaled or changed.")
            source.load()
            upright = ImageOps.exif_transpose(source).convert("RGB")
            upright.info["icc_profile"] = source.info.get("icc_profile")
            return upright


def kernel(radius):
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))


def make_mask(rgb, settings):
    settings.validate()
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    green = cv2.inRange(hsv,
                       (settings.hue_low, settings.saturation_low, settings.value_low),
                       (settings.hue_high, settings.saturation_high, settings.value_high))
    foreground = cv2.bitwise_not(green)
    raw_area = int(np.count_nonzero(foreground))
    green_fraction = float(np.count_nonzero(green) / green.size)
    del hsv, green
    if settings.noise_radius:
        foreground = cv2.morphologyEx(foreground, cv2.MORPH_OPEN, kernel(settings.noise_radius))
    if settings.holes_radius:
        foreground = cv2.morphologyEx(foreground, cv2.MORPH_CLOSE, kernel(settings.holes_radius))
    if settings.edge_shift < 0:
        foreground = cv2.erode(foreground, kernel(-settings.edge_shift))
    elif settings.edge_shift > 0:
        foreground = cv2.dilate(foreground, kernel(settings.edge_shift))
    diagnostics = inspect_mask(foreground, green_fraction, raw_area)
    alpha = (cv2.GaussianBlur(foreground, (0, 0), settings.feather)
             if settings.feather > 0 else foreground.copy())
    return foreground, alpha, diagnostics


def inspect_mask(binary, green_fraction, raw_area):
    """Conservative geometric checks, not a subject/face quality classifier."""
    total = binary.size
    area = int(np.count_nonzero(binary))
    fraction = area / total
    issues = []
    blocking = False
    if green_fraction < 0.005:
        issues.append("Almost no green screen was detected. Adjust HSV thresholds or flag for manual review.")
        blocking = True
    if fraction < 0.005:
        issues.append("The mask retains almost no subject. Do not approve this result.")
        blocking = True
    if fraction > 0.98:
        issues.append("The mask is almost fully opaque; background separation is not established.")
        blocking = True
    if fraction < 0.05 and not blocking:
        issues.append("Very little foreground remains. Check whether parts of the subject were removed.")
    if fraction > 0.9 and not blocking:
        issues.append("Most of the frame remains opaque. Check the background and clothing.")
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    areas = stats[1:, cv2.CC_STAT_AREA]
    largest = int(areas.max()) if len(areas) else 0
    if area and largest / area < 0.9:
        issues.append("The foreground contains disconnected regions. Check for screen noise or missing subject parts.")
    if sum(int(value) > total * 0.01 for value in areas) > 1:
        issues.append("Multiple large foreground regions remain. Inspect the full mask.")
    del labels, stats
    count, labels, stats, _ = cv2.connectedComponentsWithStats(cv2.bitwise_not(binary), connectivity=8)
    height, width = binary.shape
    for x, y, w, h, component_area in stats[1:]:
        if x > 0 and y > 0 and x + w < width and y + h < height and component_area > total * 0.002:
            issues.append("A large transparent hole lies inside the foreground. Check green clothing and subject detail.")
            break
    del labels, stats
    if any(np.count_nonzero(edge) / len(edge) > 0.1 for edge in (binary[0], binary[:, 0], binary[:, -1])):
        issues.append("Foreground touches the top or side of the frame. Check for clipping or unremoved screen.")
    if raw_area and area < raw_area * 0.8:
        issues.append("Cleanup removed more than 20% of the initial foreground. Check fine detail.")
    return {"issues": issues, "blocking": blocking, "foreground_fraction": fraction,
            "green_fraction": green_fraction, "largest_component_fraction": largest / area if area else 0.0}


def suppress_spill(rgb, foreground, alpha, strength, width):
    result = rgb.copy()
    if not strength or not width:
        return result
    distance = cv2.distanceTransform(foreground, cv2.DIST_L2, 3)
    weight = np.clip(1.0 - distance / (width + 1), 0, 1) * strength
    weight[alpha == 0] = 0
    excess = np.maximum(rgb[:, :, 1].astype(np.int16) - np.maximum(rgb[:, :, 0], rgb[:, :, 2]), 0)
    result[:, :, 1] = np.clip(rgb[:, :, 1] - excess * weight, 0, 255).astype(np.uint8)
    return result


def composite_portrait(cutout, settings):
    """Fit the full upright source frame with uniform scaling and grey padding."""
    width, height = settings.canvas_width, settings.canvas_height
    inset = settings.padding_percent / 100
    available = (max(1, round(width * (1 - 2 * inset))), max(1, round(height * (1 - 2 * inset))))
    fitted = ImageOps.contain(cutout, available, Image.Resampling.LANCZOS)
    if settings.subject_scale != 100:
        fitted = fitted.resize(
            (max(1, round(fitted.width * settings.subject_scale / 100)),
             max(1, round(fitted.height * settings.subject_scale / 100))),
            Image.Resampling.LANCZOS,
        )
    background = Image.new("RGBA", (width, height), settings.background_rgb() + (255,))
    position = ((width - fitted.width) // 2 + settings.offset_x,
                (height - fitted.height) // 2 + settings.offset_y)
    background.alpha_composite(fitted, position)
    return background.convert("RGB")


@dataclass
class ProcessingOutput:
    cutout: Image.Image
    mask: Image.Image
    portrait: Image.Image
    diagnostics: dict
    icc_profile: bytes | None


def process_bytes(data, settings, source_path=None):
    settings.validate()
    original = load_oriented(data, source_path)
    rgb = np.asarray(original)
    foreground, alpha, diagnostics = make_mask(rgb, settings)
    corrected = suppress_spill(rgb, foreground, alpha, settings.spill_strength, settings.spill_width)
    cutout = Image.fromarray(corrected).convert("RGBA")
    mask = Image.fromarray(alpha)
    cutout.putalpha(mask)
    portrait = composite_portrait(cutout, settings)
    return ProcessingOutput(cutout, mask, portrait, diagnostics, original.info.get("icc_profile"))


def process_with_mask(data, settings, mask, source_path=None):
    """Rebuild outputs from a manually edited full-resolution alpha mask."""
    settings.validate()
    original = load_oriented(data, source_path)
    mask = mask.convert("L")
    if mask.size != original.size:
        raise ValueError("The edited mask dimensions do not match the upright original.")
    alpha = np.asarray(mask).copy()
    binary = np.where(alpha >= 128, 255, 0).astype(np.uint8)
    diagnostics = inspect_mask(binary, 1.0, int(np.count_nonzero(binary)))
    diagnostics["manual_mask"] = True
    rgb = np.asarray(original)
    corrected = suppress_spill(rgb, binary, alpha, settings.spill_strength, settings.spill_width)
    cutout = Image.fromarray(corrected).convert("RGBA")
    cutout.putalpha(mask)
    portrait = composite_portrait(cutout, settings)
    return ProcessingOutput(cutout, mask, portrait, diagnostics, original.info.get("icc_profile"))


def process_image(path, settings=None):
    data, _ = read_original(path)
    return process_bytes(data, settings or ProcessingSettings(), path)
