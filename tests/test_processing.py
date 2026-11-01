from dataclasses import asdict, replace
import io

import numpy as np
from PIL import Image, ImageOps
import pytest

from samples.generate_processing_samples import make_sample
from student_headshot_manager.processing import (
    ProcessingSettings, border_connected, composite_portrait, make_mask, process_image,
    suppress_spill,
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


@pytest.mark.parametrize("extension", ("CR3", "NEF"))
def test_raw_source_uses_raw_decoder_without_modifying_original(tmp_path, monkeypatch, extension):
    path = tmp_path / f"subject.{extension}"
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
            assert str(source).endswith(f"subject.{extension}")
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
    rgb[50, :51] = (0, 200, 0)
    baseline, _, _ = make_mask(rgb, clean_settings())
    cleaned, _, _ = make_mask(rgb, replace(clean_settings(), noise_radius=1, holes_radius=2))
    assert baseline[10, 10] == 255 and cleaned[10, 10] == 0
    assert baseline[50, 50] == 0 and cleaned[50, 50] == 255
    eroded, _, _ = make_mask(rgb, replace(clean_settings(), edge_shift=-2))
    dilated, _, _ = make_mask(rgb, replace(clean_settings(), edge_shift=2))
    assert np.count_nonzero(eroded) < np.count_nonzero(baseline) < np.count_nonzero(dilated)
    _, feathered, _ = make_mask(rgb, replace(clean_settings(), feather=2))
    assert np.any((feathered > 0) & (feathered < 255))


def test_only_edge_connected_green_is_removed_automatically():
    rgb = np.full((100, 100, 3), (0, 200, 0), dtype=np.uint8)
    rgb[20:80, 20:80] = (190, 110, 80)
    rgb[45:55, 45:55] = (0, 200, 0)
    foreground, _, diagnostics = make_mask(rgb, clean_settings())
    assert foreground[0, 0] == 0
    assert foreground[50, 50] == 255
    assert diagnostics["protected_green_fraction"] == pytest.approx(0.01)
    assert any("Enclosed green detail" in issue for issue in diagnostics["issues"])


def test_border_connected_finds_separate_background_regions():
    candidates = np.zeros((20, 20), dtype=np.uint8)
    candidates[:, :5] = 255
    candidates[:, 15:] = 255
    candidates[8:12, 8:12] = 255
    connected = border_connected(candidates)
    assert connected[10, 2] == connected[10, 17] == 255
    assert connected[10, 10] == 0


def test_default_preset_does_not_erode_single_pixel_hair_detail():
    rgb = np.full((80, 80, 3), (0, 200, 0), dtype=np.uint8)
    rgb[25:70, 25:55] = (60, 40, 30)
    rgb[15:26, 38] = (60, 40, 30)
    foreground, _, _ = make_mask(rgb, ProcessingSettings())
    assert foreground[15:26, 38].all()


def person_prior_scene():
    """Green field with a head whose top is green-tinted spill, a hair strand the
    model misses, and a non-green light stand far from the person."""
    green = np.zeros((400, 300), dtype=np.uint8)
    semantic = np.zeros((400, 300), dtype=np.uint8)
    green[:, :] = 255
    green[60:400, 90:210] = 0          # head and body, non-green
    green[40:60, 100:200] = 255        # spill on top of hair, border-connected green
    green[30:60, 205] = 0              # thin hair strand beside the head
    green[0:400, 5:12] = 0             # light stand at the frame edge
    semantic[40:400, 90:210] = 255
    green[300:340, 120:180] = 255      # green visible through the lower body
    return green, semantic


def test_person_prior_rescues_head_spill_and_keeps_missed_hair():
    green, semantic = person_prior_scene()
    foreground = processing.merge_person_prior(green, semantic)
    assert foreground[45, 150] == 255      # spill on hair kept
    assert foreground[35, 205] == 255      # strand the model missed kept
    assert foreground[200, 150] == 255


def test_person_prior_drops_distant_objects_and_lower_body_green():
    green, semantic = person_prior_scene()
    foreground = processing.merge_person_prior(green, semantic)
    assert not foreground[:, 5:12].any()   # light stand removed
    assert foreground[320, 150] == 0       # green below the shoulders still keyed
    assert foreground[0, 0] == 0


def test_person_prior_never_removes_non_green_pixels_near_the_person():
    green, semantic = person_prior_scene()
    foreground = processing.merge_person_prior(green, semantic)
    near_subject = (green == 0) & (np.abs(np.arange(300) - 150)[None, :] < 70)
    assert foreground[near_subject].all()


def test_person_prior_rejects_frames_without_a_person():
    green, _ = person_prior_scene()
    with pytest.raises(ValueError, match="could not find a person"):
        processing.merge_person_prior(green, np.zeros_like(green))


def test_large_frames_use_person_prior_and_small_frames_use_chroma(monkeypatch):
    calls = []

    def prior(rgb):
        calls.append(rgb.shape)
        mask = np.zeros(rgb.shape[:2], dtype=np.uint8)
        mask[20:80, 20:80] = 255
        return mask

    monkeypatch.setattr(processing, "semantic_person_mask", prior)
    rgb = np.full((100, 100, 3), (0, 200, 0), dtype=np.uint8)
    rgb[20:80, 20:80] = (190, 110, 80)
    _, _, diagnostics = make_mask(rgb, clean_settings())
    assert calls and diagnostics["extractor"] == "u2netp-person-plus-chroma"


def test_bundled_portrait_model_runs_locally():
    rgb = np.full((1100, 1000, 3), (0, 200, 0), dtype=np.uint8)
    rgb[250:1100, 300:700] = (190, 130, 100)
    semantic = processing.semantic_person_mask(rgb)
    assert semantic.shape == (1100, 1000) and semantic.dtype == np.uint8
    assert semantic[700, 500] > semantic[50, 50]


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


def test_portrait_canvas_applies_color_scale_and_position():
    cutout = Image.new("RGBA", (40, 80), (255, 0, 0, 255))
    settings = replace(
        clean_settings(), canvas_width=200, canvas_height=200, padding_percent=0,
        background_r=12, background_g=34, background_b=56,
        subject_scale=50, offset_x=25, offset_y=-20,
    )
    composite = np.asarray(composite_portrait(cutout, settings))
    ys, xs = np.where(composite[:, :, 0] == 255)
    assert (xs.min(), xs.max(), ys.min(), ys.max()) == (100, 149, 30, 129)
    np.testing.assert_array_equal(composite[0, 0, :3], (12, 34, 56))


@pytest.mark.parametrize("kind", ["no-green", "empty-green"])
def test_bad_separation_is_blocking_not_silently_approved(tmp_path, kind):
    path = tmp_path / "bad.jpg"
    make_sample(kind, (120, 160)).save(path, quality=98, subsampling=0)
    output = process_image(path, clean_settings())
    assert output.diagnostics["blocking"]
    assert output.diagnostics["issues"]


def test_large_enclosed_green_area_is_preserved_and_flagged(tmp_path):
    path = tmp_path / "green-clothing.jpg"
    make_sample("green-clothing", (240, 320)).save(path, quality=98, subsampling=0)
    output = process_image(path, clean_settings())
    assert output.mask.getpixel((120, 192)) > 0
    assert any("Enclosed green detail" in issue for issue in output.diagnostics["issues"])


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
        changed = replace(default, **{name: 1 if value is None else value + delta})
        assert changed.digest() != default.digest(), name
    assert replace(default, feather=1).digest() == replace(default, feather=1.0).digest()
