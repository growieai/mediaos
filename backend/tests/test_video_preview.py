"""Offline concept previews cannot acquire publication or real-voice authority."""

import io
import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont
from pydantic import ValidationError

from app.video_preview.cli import render_preview, repository_input, validate_media
from app.video_preview.renderer import PREVIEW_LABEL, FrameRenderer
from app.video_preview.schemas import ConceptSpec, PreviewManifest


@pytest.fixture(scope="session", autouse=True)
def database():
    """These tests never initialize, query or reset PostgreSQL."""
    yield None


def concept_data(**changes):
    data = {
        "schema_version": 1,
        "preview_only": True,
        "publishable": False,
        "audio": "NONE",
        "identity_status": "PROPOSED",
        "subject_name": "Creadora de prueba",
        "title": "Un concepto para revisar",
        "disclosure": "Identidad virtual creada con IA.",
        "width": 1080,
        "height": 1920,
        "fps": 24,
        "portrait_path": "portrait.png",
        "regular_font_path": "regular.ttf",
        "bold_font_path": "bold.ttf",
        "scenes": [
            {
                "kind": kind,
                "duration_seconds": 3,
                "kicker": "CONCEPTO",
                "headline": headline,
                "supporting": "Texto creativo para revisar.",
                "caption": "Una propuesta visual, sin voz ni afirmaciones comerciales.",
            }
            for kind, headline in (
                ("PORTRAIT", "Una idea para tu negocio"),
                ("QUESTION", "¿Qué quieres conocer?"),
                ("CLOSING", "Revisemos la propuesta"),
            )
        ],
    }
    return {**data, **changes}


@pytest.fixture
def asset_root(tmp_path):
    fonts = Path(__file__).resolve().parents[1] / "assets" / "fonts"
    shutil.copyfile(fonts / "Inter-Regular.ttf", tmp_path / "regular.ttf")
    shutil.copyfile(fonts / "Inter-Bold.ttf", tmp_path / "bold.ttf")
    portrait = Image.new("RGB", (320, 480), "#275849")
    ImageDraw.Draw(portrait).ellipse((60, 40, 260, 320), fill="#d9aa7d")
    portrait.save(tmp_path / "portrait.png")
    return tmp_path


@pytest.mark.parametrize(
    "field,value",
    [
        ("preview_only", False),
        ("preview_only", 1),
        ("publishable", True),
        ("publishable", 0),
        ("audio", "VOICE"),
        ("identity_status", "APPROVED"),
        ("schema_version", 2),
        ("schema_version", True),
        ("width", 720),
        ("width", "1080"),
        ("height", 1920.0),
        ("fps", 30),
        ("publish_to", "instagram"),
    ],
)
def test_schema_cannot_upgrade_preview_authority_or_coerce_media_contract(field, value):
    with pytest.raises(ValidationError):
        ConceptSpec.model_validate(concept_data(**{field: value}))


@pytest.mark.parametrize("duration", [2, 9, True, "3", 3.0])
def test_scene_duration_is_a_bounded_integer(duration):
    data = concept_data()
    data["scenes"][0]["duration_seconds"] = duration
    with pytest.raises(ValidationError):
        ConceptSpec.model_validate(data)


@pytest.mark.parametrize("count", [0, 2, 9])
def test_scene_count_is_bounded(count):
    data = concept_data()
    data["scenes"] = [deepcopy(data["scenes"][0]) for _ in range(count)]
    with pytest.raises(ValidationError):
        ConceptSpec.model_validate(data)


def test_total_duration_cannot_exceed_thirty_seconds():
    data = concept_data()
    data["scenes"] = [deepcopy(data["scenes"][0]) for _ in range(4)]
    for scene in data["scenes"]:
        scene["duration_seconds"] = 8
    with pytest.raises(ValidationError, match="30 seconds"):
        ConceptSpec.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [("kind", "TALKING_AVATAR"), ("caption", " "), ("headline", "x" * 81), ("voice_id", "v1")],
)
def test_scene_schema_rejects_blank_overlong_or_unimplemented_inputs(field, value):
    data = concept_data()
    data["scenes"][0][field] = value
    with pytest.raises(ValidationError):
        ConceptSpec.model_validate(data)


@pytest.mark.parametrize("field", ["portrait_path", "regular_font_path", "bold_font_path"])
def test_renderer_rejects_inputs_outside_repository(asset_root, field):
    spec = ConceptSpec.model_validate(concept_data(**{field: "../outside.png"}))
    with pytest.raises(ValueError):
        FrameRenderer(spec, asset_root)


def test_repository_input_rejects_external_missing_and_oversized_files(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")
    for value in ("../outside.txt", "missing.txt", "."):
        with pytest.raises(ValueError):
            repository_input(root, value)
    oversized = root / "oversized.bin"
    with oversized.open("wb") as stream:
        stream.truncate(20_000_001)
    with pytest.raises(ValueError, match="20 MB"):
        repository_input(root, oversized.name)


@pytest.mark.parametrize("invalid", ["corrupt", "too-wide"])
def test_invalid_portrait_fails_before_any_frame_is_returned(asset_root, invalid):
    if invalid == "corrupt":
        (asset_root / "portrait.png").write_bytes(b"not a PNG")
    else:
        Image.new("RGB", (4097, 1), "black").save(asset_root / "portrait.png")
    with pytest.raises(ValueError):
        FrameRenderer(ConceptSpec.model_validate(concept_data()), asset_root)


def test_unsupported_glyph_fails_preflight(asset_root):
    data = concept_data()
    data["scenes"][1]["headline"] = "Una idea \U0010ffff"
    with pytest.raises(ValueError):
        FrameRenderer(ConceptSpec.model_validate(data), asset_root)


def test_fixed_preview_marks_reject_missing_glyphs_even_when_configured_copy_is_supported(
    monkeypatch, asset_root
):
    spec = ConceptSpec.model_validate(concept_data())
    configured_text = [spec.subject_name, spec.title, spec.disclosure]
    configured_text.extend(
        text
        for scene in spec.scenes
        for text in (scene.kicker, scene.headline, scene.supporting, scene.caption)
    )
    assert all("V" not in text for text in configured_text)
    assert "V" in PREVIEW_LABEL
    FrameRenderer(spec, asset_root)
    original_mask = ImageFont.FreeTypeFont.getmask

    def missing_fixed_letter(font, text, *args, **kwargs):
        return original_mask(font, "\U0010ffff" if text == "V" else text, *args, **kwargs)

    monkeypatch.setattr(ImageFont.FreeTypeFont, "getmask", missing_fixed_letter)
    with pytest.raises(ValueError, match="unsupported glyphs"):
        FrameRenderer(spec, asset_root)


def test_unbreakable_overflow_fails_instead_of_clipping_or_shrinking(asset_root):
    data = concept_data()
    data["scenes"][1]["headline"] = "W" * 80
    with pytest.raises(ValueError):
        FrameRenderer(ConceptSpec.model_validate(data), asset_root)


def test_frame_bounds_dimensions_determinism_and_persistent_disclosures(asset_root):
    assert "AI" in PREVIEW_LABEL.split(), "AI disclosure cannot depend on configurable copy"
    spec = ConceptSpec.model_validate(concept_data(disclosure="Texto editable de muestra."))
    renderer = FrameRenderer(spec, asset_root)
    assert renderer.frame_count == 9 * 24
    for invalid in (-1, renderer.frame_count):
        with pytest.raises(IndexError):
            renderer.frame(invalid)
    indexes = (0, 3 * 24, renderer.frame_count - 1)
    frames = [renderer.frame(index) for index in indexes]
    assert all(frame.mode == "RGB" and frame.size == (1080, 1920) for frame in frames)
    assert renderer.frame(0).tobytes() == frames[0].tobytes()
    assert len({frame.crop((0, 100, 1080, 1800)).tobytes() for frame in frames}) == 3
    for bounds in ((0, 0, 1080, 88), (0, 1830, 1080, 1920)):
        regions = [frame.crop(bounds) for frame in frames]
        assert all(region.tobytes() == regions[0].tobytes() for region in regions)
        assert any(low != high for low, high in regions[0].getextrema()), "Disclosure band is empty"


def probe_payload():
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1080,
                "height": 1920,
                "pix_fmt": "yuv420p",
                "avg_frame_rate": "24/1",
                "nb_frames": "216",
            }
        ],
        "format": {"duration": "9.000000"},
    }


def stub_probe(monkeypatch, payload):
    def run(args, **kwargs):
        assert args[0] == "fake-ffprobe"
        assert kwargs["check"] and kwargs["timeout"] == 30
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    monkeypatch.setattr("app.video_preview.cli.subprocess.run", run)


def test_encoded_media_must_match_exact_frame_contract(monkeypatch, tmp_path):
    expected = probe_payload()
    stub_probe(monkeypatch, expected)
    result = validate_media(
        tmp_path / "video.mp4", ConceptSpec.model_validate(concept_data()), "fake-ffprobe"
    )
    assert result == expected


@pytest.mark.parametrize(
    "field,value",
    [
        ("codec_type", "audio"),
        ("codec_name", "mpeg4"),
        ("width", 720),
        ("height", 1080),
        ("pix_fmt", "yuv444p"),
        ("avg_frame_rate", "25/1"),
        ("nb_frames", "215"),
    ],
)
def test_encoded_media_mismatch_is_not_completed(monkeypatch, tmp_path, field, value):
    payload = probe_payload()
    payload["streams"][0][field] = value
    stub_probe(monkeypatch, payload)
    with pytest.raises(ValueError):
        validate_media(
            tmp_path / "video.mp4", ConceptSpec.model_validate(concept_data()), "fake-ffprobe"
        )


@pytest.mark.parametrize("case", ["audio-track", "empty", "short-duration"])
def test_audio_extra_streams_and_incomplete_media_fail_closed(monkeypatch, tmp_path, case):
    payload = probe_payload()
    if case == "audio-track":
        payload["streams"].append({"codec_type": "audio", "codec_name": "aac"})
    elif case == "empty":
        payload["streams"] = []
    else:
        payload["format"]["duration"] = "8.0"
    stub_probe(monkeypatch, payload)
    with pytest.raises(ValueError):
        validate_media(
            tmp_path / "video.mp4", ConceptSpec.model_validate(concept_data()), "fake-ffprobe"
        )


def save_spec(root):
    path = root / "concept.json"
    path.write_text(json.dumps(concept_data()), encoding="utf-8")
    return path


def test_missing_local_encoder_refuses_without_creating_outputs(monkeypatch, asset_root):
    spec = save_spec(asset_root)
    monkeypatch.setattr("app.video_preview.cli.shutil.which", lambda _: None)
    with pytest.raises(RuntimeError, match="ffmpeg and ffprobe"):
        render_preview(spec, asset_root)
    assert not (asset_root / ".local").exists()


def test_oversized_spec_is_rejected_before_encoder_lookup(monkeypatch, asset_root):
    spec = save_spec(asset_root)
    data = spec.read_bytes()
    spec.write_bytes(data + b" " * (65_537 - len(data)))

    def unexpected_lookup(_):
        pytest.fail("An oversized specification reached encoder lookup")

    monkeypatch.setattr("app.video_preview.cli.shutil.which", unexpected_lookup)
    with pytest.raises(ValueError, match="[Ss]pec|64"):
        render_preview(spec, asset_root)
    assert not (asset_root / ".local").exists()


def fake_local_tool_version(monkeypatch):
    monkeypatch.setattr("app.video_preview.cli.shutil.which", lambda tool: f"fake-{tool}")

    def run(args, **kwargs):
        assert args == ["fake-ffmpeg", "-version"]
        return subprocess.CompletedProcess(args, 0, "ffmpeg fixture version\n", "")

    monkeypatch.setattr("app.video_preview.cli.subprocess.run", run)


@pytest.mark.parametrize("escape", ["resolved-outside", "symlink"])
def test_output_directory_escape_fails_before_encoder_start(monkeypatch, asset_root, escape):
    spec = save_spec(asset_root)
    fake_local_tool_version(monkeypatch)
    monkeypatch.setattr(
        "app.video_preview.renderer.FrameRenderer",
        lambda *_: SimpleNamespace(frame_count=216),
    )
    parent = asset_root / ".local" / "video-previews"
    original_resolve = Path.resolve
    original_symlink = Path.is_symlink
    if escape == "resolved-outside":
        monkeypatch.setattr(
            Path,
            "resolve",
            lambda path, *args, **kwargs: (
                asset_root.parent / "outside"
                if path == parent
                else original_resolve(path, *args, **kwargs)
            ),
        )
    else:
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda path: path == asset_root / ".local" or original_symlink(path),
        )
    with pytest.raises(ValueError, match="storage must remain inside"):
        render_preview(spec, asset_root)
    assert not parent.exists()


def test_probe_failure_never_creates_completed_video_or_manifest(monkeypatch, asset_root):
    spec = save_spec(asset_root)
    monkeypatch.setattr("app.video_preview.cli.shutil.which", lambda tool: f"fake-{tool}")

    class FakeFrame:
        mode = "RGB"
        size = (1080, 1920)

        def copy(self):
            return self

        def tobytes(self):
            return b""

    class FakeEncoder:
        def __init__(self, args, **kwargs):
            assert args[0] == "fake-ffmpeg" and "-an" in args
            Path(args[-1]).write_bytes(b"intentionally invalid encoded fixture")
            self.stdin = io.BytesIO()
            self.returncode = None

        def wait(self, **kwargs):
            self.returncode = 0
            return 0

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -1

    def run(args, **kwargs):
        if args[0] == "fake-ffprobe":
            raise subprocess.CalledProcessError(1, args)
        assert args == ["fake-ffmpeg", "-version"]
        return subprocess.CompletedProcess(args, 0, "ffmpeg fixture version\n", "")

    monkeypatch.setattr(
        "app.video_preview.renderer.FrameRenderer",
        lambda *_: SimpleNamespace(frame_count=216, frame=lambda _: FakeFrame()),
    )
    monkeypatch.setattr("app.video_preview.cli.subprocess.run", run)
    monkeypatch.setattr("app.video_preview.cli.subprocess.Popen", FakeEncoder)
    with pytest.raises(subprocess.CalledProcessError):
        render_preview(spec, asset_root)
    outputs = asset_root / ".local" / "video-previews"
    assert len(list(outputs.glob("*/video.partial.mp4"))) == 1
    assert not list(outputs.glob("*/video.mp4"))
    assert not list(outputs.glob("*/manifest.json"))


def manifest_data(**changes):
    return {
        "spec": concept_data(),
        "input_sha256": {
            key: "a" * 64
            for key in ("spec", "portrait_path", "regular_font_path", "bold_font_path")
        },
        "ffmpeg_version": "ffmpeg fixture version",
        "pillow_version": "12.3.0",
        "output_sha256": "b" * 64,
        "frame_count": 216,
        "duration_seconds": 9,
        **changes,
    }


def test_manifest_round_trip_retains_local_unpublished_authority():
    manifest = PreviewManifest.model_validate(manifest_data())
    assert PreviewManifest.model_validate_json(manifest.model_dump_json()) == manifest
    assert manifest.publishable is False and manifest.preview_only is True
    assert manifest.provider == "deterministic-local" and manifest.model is None
    assert type(manifest.cost) is int and manifest.cost == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("frame_count", 240),
        ("duration_seconds", 10),
        ("input_sha256", {"spec": "a" * 64}),
        ("input_sha256", {}),
        ("output_sha256", "fabricated"),
        ("cost", False),
        ("cost", 0.0),
        ("publishable", 0),
        ("preview_only", 1),
        ("schema_version", True),
        ("provider", "openai"),
    ],
)
def test_manifest_cannot_misstate_timeline_inputs_cost_or_authority(field, value):
    with pytest.raises(ValidationError):
        PreviewManifest.model_validate(manifest_data(**{field: value}))


def test_manifest_rejects_unexpected_provenance_key():
    data = manifest_data()
    data["input_sha256"]["unrecorded_voice"] = "c" * 64
    with pytest.raises(ValidationError):
        PreviewManifest.model_validate(data)
