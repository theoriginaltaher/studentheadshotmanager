from dataclasses import asdict, replace
import io

import numpy as np
from PIL import Image, ImageOps
import pytest

from samples.generate_processing_samples import make_sample
from student_headshot_manager.processing import (
    ProcessingSettings, composite_portrait, make_mask, process_image, suppress_spill,
)
import student_headshot_manager.processing as processing


def clean_settings(**changes):
    return replace(ProcessingSettings(), noise_radius=0, holes_radius=0, edge_shift=0, feather=0,
                   spill_strength=0, canvas_width=120, canvas_height=160, **changes)


def test_full_resolution_cutout_and_grey_composite(tmp_path):
    path = tmp_path / "subject.jpg"
    make_sample(size=(180, 240)).save(path, quality=98, subsampling=0)
    original = path.read_bytes()
    stat = path.stat()
    output = process_image(path, clean_settings())
    assert output.cutout.mode == "RGBA" and output.cutout.size == (180, 240)
    assert output.mask.mode == "L" and output.mask.size == (180, 240)
    assert output.portrait.size == (120, 160)
    assert output.cutout.getpixel((0, 0))[3] == 0
    assert output.cutout.getpixel((90, 70))[3] == 255
    assert output.portrait.getpixel((0, 0)) == (180, 180, 180)
    assert not output.diagnostics["blocking"]
    assert path.read_bytes() == original and path.stat().st_mtime_ns == stat.st_mtime_ns


def test_raw_source_uses_raw_decoder_without_modifying_original(tmp_path, monkeypatch):
    path = tmp_path / "subject.CR3"
    path.write_bytes(b"raw-camera-bytes")
    rgb = np.full((120, 90, 3), (0, 220, 0), dtype=np.uint8)
    rgb[35:95, 30:60] = (180, 100, 80)

    class FakeRaw:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def postprocess(self, **kwargs): return rgb

    class FakeRawpy:
        @staticmethod
        def imread(source):
            assert str(source).endswith("subject.CR3")
            return FakeRaw()

    monkeypatch.setattr(processing, "rawpy_module", lambda: FakeRawpy)
    output = process_image(path, clean_settings())
    assert output.cutout.size == (90, 120)
    assert output.portrait.size == (120, 160)
    assert path.read_bytes() == b"raw-camera-bytes"


@pytest.mark.parametrize("orientation", range(1, 9))
def test_all_exif_orientations_apply_without_resampling(tmp_path, orientation):
    path = tmp_path / "oriented.jpg"
    image = make_sample(size=(90, 120))
    exif = Image.Exif()
    exif[274] = orientation
    image.save(path, exif=exif, quality=98, subsampling=0)
    before = path.read_bytes()
    with Image.open(path) as loaded:
        expected = ImageOps.exif_transpose(loaded).convert("RGB")
    output = process_image(path, clean_settings())
    assert output.cutout.size == expected.size
    np.testing.assert_array_equal(np.asarray(output.cutout)[:, :, :3], np.asarray(expected))
    assert path.read_bytes() == before


@pytest.mark.parametrize("settings", [
    replace(ProcessingSettings(), hue_low=100, hue_high=120),
    replace(ProcessingSettings(), saturation_low=200),
    replace(ProcessingSettings(), saturation_high=80),
    replace(ProcessingSettings(), value_low=200),
    replace(ProcessingSettings(), value_high=100),
])
def test_hue_saturation_and_brightness_thresholds(settings):
    rgb = np.full((24, 24, 3), (80, 160, 80), dtype=np.uint8)
    baseline, _, _ = make_mask(rgb, clean_settings())
    changed, _, _ = make_mask(rgb, replace(settings, noise_radius=0, holes_radius=0, edge_shift=0, feather=0))
    assert not baseline.any()
    assert changed.all()


def test_noise_holes_shift_and_feather_controls():
    rgb = np.full((100, 100, 3), (0, 200, 0), dtype=np.uint8)
    rgb[25:75, 25:75] = (190, 110, 80)
    rgb[10, 10] = (190, 110, 80)
    rgb[50, 50] = (0, 200, 0)
    baseline, _, _ = make_mask(rgb, clean_settings())
    cleaned, _, _ = make_mask(rgb, replace(clean_settings(), noise_radius=1, holes_radius=2))
    assert baseline[10, 10] == 255 and cleaned[10, 10] == 0
    assert baseline[50, 50] == 0 and cleaned[50, 50] == 255
    eroded, _, _ = make_mask(rgb, replace(clean_settings(), edge_shift=-2))
    dilated, _, _ = make_mask(rgb, replace(clean_settings(), edge_shift=2))
    assert np.count_nonzero(eroded) < np.count_nonzero(baseline) < np.count_nonzero(dilated)
    _, feathered, _ = make_mask(rgb, replace(clean_settings(), feather=2))
    assert np.any((feathered > 0) & (feathered < 255))


def test_spill_suppression_is_confined_to_edge_band():
    rgb = np.full((60, 60, 3), (80, 150, 80), dtype=np.uint8)
    binary = np.zeros((60, 60), dtype=np.uint8)
    binary[10:50, 10:50] = 255
    corrected = suppress_spill(rgb, binary, binary, 1.0, 4)
    assert corrected[10, 30, 1] < rgb[10, 30, 1]
    np.testing.assert_array_equal(corrected[30, 30], rgb[30, 30])
    np.testing.assert_array_equal(corrected[0, 0], rgb[0, 0])
    np.testing.assert_array_equal(corrected[:, :, 0], rgb[:, :, 0])
    np.testing.assert_array_equal(suppress_spill(rgb, binary, binary, 0, 4), rgb)


@pytest.mark.parametrize("source_size", [(200, 100), (100, 200)])
def test_portrait_fit_never_stretches_or_crops(source_size):
    cutout = Image.new("RGBA", source_size, (255, 0, 0, 255))
    settings = replace(clean_settings(), canvas_width=180, canvas_height=240, padding_percent=0, grey=170)
    composite = np.asarray(composite_portrait(cutout, settings))
    ys, xs = np.where(composite[:, :, 0] == 255)
    actual_ratio = (xs.max() - xs.min() + 1) / (ys.max() - ys.min() + 1)
    assert actual_ratio == pytest.approx(source_size[0] / source_size[1], abs=.02)
    assert min(xs) >= 0 and min(ys) >= 0
    assert composite.shape[:2] == (240, 180)


@pytest.mark.parametrize("kind", ["no-green", "empty-green"])
def test_bad_separation_is_blocking_not_silently_approved(tmp_path, kind):
    path = tmp_path / "bad.jpg"
    make_sample(kind, (120, 160)).save(path, quality=98, subsampling=0)
    output = process_image(path, clean_settings())
    assert output.diagnostics["blocking"]
    assert output.diagnostics["issues"]


def test_large_enclosed_hole_is_flagged(tmp_path):
    path = tmp_path / "green-clothing.jpg"
    make_sample("green-clothing", (240, 320)).save(path, quality=98, subsampling=0)
    output = process_image(path, clean_settings())
    assert any("transparent hole" in issue for issue in output.diagnostics["issues"])


@pytest.mark.parametrize("changes", [
    {"hue_low": 100, "hue_high": 80}, {"grey": 256}, {"edge_shift": 30},
    {"feather": float("nan")}, {"spill_strength": -1}, {"canvas_width": 10000, "canvas_height": 10000},
    {"canvas_width": True}, {"jpeg_quality": 50},
])
def test_invalid_settings_are_rejected(changes):
    with pytest.raises(ValueError):
        replace(ProcessingSettings(), **changes).validate()


def test_every_setting_participates_in_cache_identity():
    default = ProcessingSettings()
    for name, value in asdict(default).items():
        delta = -1 if name in ("saturation_high", "value_high") else (.05 if name == "spill_strength" else 1)
        changed = replace(default, **{name: value + delta})
        assert changed.digest() != default.digest(), name
    assert replace(default, feather=1).digest() == replace(default, feather=1.0).digest()
