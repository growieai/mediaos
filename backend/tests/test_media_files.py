"""Private media composition verifies synthetic local fixtures, never provider accounts."""

import hashlib
import io
import json
import shutil
import socket
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from PIL import Image, ImageFont

from app.media import files
from app.media_providers.schemas import Alignment, SpeechResult


@pytest.fixture(scope="session", autouse=True)
def database():
    yield None


@pytest.fixture
def directory(tmp_path):
    return files.media_directory(tmp_path / "private", uuid4(), uuid4())


def digest(data):
    return hashlib.sha256(data).hexdigest()


def aligned(text="Hola"):
    return Alignment(
        characters=list(text),
        character_start_times_seconds=[i * 0.8 / len(text) for i in range(len(text))],
        character_end_times_seconds=[(i + 1) * 0.8 / len(text) for i in range(len(text))],
    )


def test_uuid_paths_and_immutable_atomic_files(directory):
    assert directory.is_dir()
    files._write(directory, "speech.mp3", b"original")
    files._write(directory, "speech.mp3", b"original")
    with pytest.raises(files.MediaFileError, match="INTEGRITY"):
        files._write(directory, "speech.mp3", b"changed")
    assert files.load_blob(directory, "speech.mp3", digest(b"original"), 20) == b"original"
    assert not list(directory.glob("*.partial"))


@pytest.mark.parametrize("name", ["../speech.mp3", "x.mp4", "C:\\secret", "video.mp4/other"])
def test_file_names_are_fixed(directory, name):
    with pytest.raises(files.MediaFileError, match="UNSAFE_MEDIA_PATH"):
        files.load_blob(directory, name, "0" * 64, 100)


def test_limits_and_corruption(directory):
    files._write(directory, "speech.mp3", b"original")
    for limit in (-1, 0, 3, files.LIMIT + 1):
        with pytest.raises(files.MediaFileError):
            files.load_blob(directory, "speech.mp3", digest(b"original"), limit)
    with pytest.raises(files.MediaFileError):
        files.load_blob(directory, "speech.mp3", "0" * 64, 100)


def test_symlinks_are_rejected(tmp_path):
    target, link = tmp_path / "actual", tmp_path / "link"
    target.mkdir()
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows account cannot create symbolic links")
    with pytest.raises(files.MediaFileError, match="UNSAFE_MEDIA_PATH"):
        files.media_directory(link, uuid4(), uuid4())


@pytest.mark.parametrize(
    "url",
    [
        "http://files.heygen.ai/a",
        "https://evil.example/a",
        "https://key:secret@files.heygen.ai/a",
        "https://files.heygen.ai:8443/a",
    ],
)
def test_download_host_and_transport_policy(url):
    requests = []
    transport = httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, content=b"source")
    )
    with pytest.raises(files.MediaFileError, match="MEDIA_DOWNLOAD_FAILED"):
        files._download(url, transport)
    assert not requests


def test_download_redirects_and_limits(monkeypatch):
    requests = []
    transport = httpx.MockTransport(
        lambda request: requests.append(request)
        or httpx.Response(302, headers={"Location": "https://evil.example"})
    )
    with pytest.raises(files.MediaFileError):
        files._download("https://files.heygen.ai/source.mp4", transport)
    assert len(requests) == 1
    monkeypatch.setattr(files, "LIMIT", 100)
    with pytest.raises(files.MediaFileError):
        files._download(
            "https://files.heygen.ai/source.mp4",
            httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 101)),
        )


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
@pytest.mark.parametrize(
    "header,expected", [("0", 0), ("30", 30), ("99999999", 86400), ("-1", None), ("invalid", None)]
)
def test_download_transient_responses_preserve_bounded_retry_metadata(status, header, expected):
    requests = []
    transport = httpx.MockTransport(
        lambda request: requests.append(request)
        or httpx.Response(status, headers={"Retry-After": header}, content=b"private diagnostic")
    )
    with pytest.raises(files.MediaRetrievalError) as error:
        files._download("https://files.heygen.ai/output.mp4?signature=secret", transport)
    assert error.value.retry_after_seconds == expected
    assert str(error.value) == "MEDIA_RETRIEVAL_UNAVAILABLE"
    assert len(requests) == 1


@pytest.mark.parametrize(
    "failure", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError]
)
def test_download_network_failure_is_retryable_without_leaking_url(failure):
    def fail(request):
        raise failure("private provider diagnostics", request=request)

    with pytest.raises(files.MediaRetrievalError) as error:
        files._download("https://files.heygen.ai/a.mp4?signature=secret", httpx.MockTransport(fail))
    assert error.value.retry_after_seconds is None
    assert str(error.value) == "MEDIA_RETRIEVAL_UNAVAILABLE"


def test_download_dns_failure_is_transient(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("private resolver diagnostic")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(files.MediaRetrievalError):
        files._download("https://files.heygen.ai/a.mp4", None)


@pytest.mark.parametrize("status", [301, 302, 400, 401, 403, 404])
def test_download_policy_or_missing_object_is_not_a_transient_failure(status):
    transport = httpx.MockTransport(lambda r: httpx.Response(status))
    with pytest.raises(files.MediaFileError) as error:
        files._download("https://files.heygen.ai/a.mp4", transport)
    assert not isinstance(error.value, files.MediaRetrievalError)


def test_download_integrity_limits_are_not_retryable(monkeypatch):
    monkeypatch.setattr(files, "LIMIT", 5)
    with pytest.raises(files.MediaFileError) as error:
        files._download(
            "https://files.heygen.ai/a.mp4",
            httpx.MockTransport(lambda r: httpx.Response(200, content=b"oversized")),
        )
    assert not isinstance(error.value, files.MediaRetrievalError)


def test_dns_private_addresses_block_before_request(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))],
    )
    with pytest.raises(files.MediaFileError, match="MEDIA_DOWNLOAD_FAILED"):
        files._download("https://files.heygen.ai/source.mp4", None)


def test_dns_address_is_pinned_with_original_tls_identity(monkeypatch):
    requests = []
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))],
    )

    class Capture(httpx.BaseTransport):
        def handle_request(self, request):
            requests.append(request)
            return httpx.Response(200, content=b"source")

    assert files._download("https://files.heygen.ai/a.mp4?signature=opaque", Capture()) == b"source"
    assert requests[0].url.host == "93.184.215.14"
    assert requests[0].headers["Host"] == "files.heygen.ai"
    assert requests[0].extensions["sni_hostname"] == "files.heygen.ai"
    assert "Authorization" not in requests[0].headers


@pytest.fixture
def fonts():
    base = Path(__file__).parents[1] / "assets/fonts"
    return base / "Inter-Regular.ttf", base / "Inter-Bold.ttf"


def test_captions_preserve_every_character_and_literal_filter_text(fonts):
    text = "Texto exacto {\\an8} con acentos: autónomos y pequeñas empresas. " * 4
    font = ImageFont.truetype(str(fonts[1]), 54)
    cues = files._captions(text, aligned(text), font)
    assert len(cues) > 1
    assert "".join(c["text"] for c in cues) == text
    assert all(len(files._lines(c["text"], font)) <= 2 for c in cues)


def test_sql_script_newlines_and_whitespace_preserve_exact_alignment(fonts):
    # media_script joins selected approved blocks and the disclosure with newlines.
    text = "Ayudas para autónomos.\r\nConsulta los requisitos.\nIdentidad\tvirtual creada con IA."
    font = ImageFont.truetype(str(fonts[1]), 54)
    cues = files._captions(text, aligned(text), font)
    assert "".join(cue["text"] for cue in cues) == text
    assert all(len(files._lines(cue["text"], font)) <= 2 for cue in cues)


def fact_script(*blocks):
    disclosure = "Identidad virtual creada con IA."
    return {
        "schema_version": 1,
        "language": "es-ES",
        "blocks": list(blocks),
        "text": "\n".join([b["text"] for b in blocks] + [disclosure]),
        "disclosure": disclosure,
    }


def test_fact_cards_use_only_exact_approved_facts_and_aligned_intervals(fonts):
    fact_id = str(uuid4())
    script = fact_script(
        {"path": "caption", "kind": "CREATIVE", "text": "Consulta la fuente.", "fact_ids": []},
        {
            "path": "slides.0.body",
            "kind": "FACT",
            "text": "La convocatoria admite solicitudes de autónomos.",
            "fact_ids": [fact_id],
        },
    )
    timing = Alignment(
        characters=list(script["text"]),
        character_start_times_seconds=[i * 0.1 for i in range(len(script["text"]))],
        character_end_times_seconds=[(i + 1) * 0.1 for i in range(len(script["text"]))],
    )
    cards = files._cutaways(script, timing, ImageFont.truetype(str(fonts[1]), 72))
    assert len(cards) == 1
    assert cards[0]["text"] == script["blocks"][1]["text"]
    assert cards[0]["fact_ids"] == [fact_id]
    assert cards[0]["block_path"] == "slides.0.body"
    assert cards[0]["start"] == pytest.approx((len(script["blocks"][0]["text"]) + 1) * 0.1)
    assert cards[0]["end"] - cards[0]["start"] == pytest.approx(2.0)


@pytest.mark.parametrize("fact_ids", [[], ["fabricated"], "not-a-list"])
def test_fact_cutaway_without_exact_evidence_identity_fails_closed(fonts, fact_ids):
    script = fact_script(
        {"path": "caption", "kind": "FACT", "text": "Dato aprobado.", "fact_ids": fact_ids}
    )
    with pytest.raises(files.MediaFileError, match="CUTAWAY_PROVENANCE_MISMATCH"):
        files._cutaways(script, aligned(script["text"]), ImageFont.truetype(str(fonts[1]), 72))


def test_cutaways_reject_text_changes_and_skip_overflow_without_truncating(fonts):
    blocks = [
        {
            "path": "caption",
            "kind": "FACT",
            "text": "Dato con evidencia. " * 30,
            "fact_ids": [str(uuid4())],
        },
        {"path": "cta", "kind": "FACT", "text": "Un dato breve.", "fact_ids": [str(uuid4())]},
    ]
    script = fact_script(*blocks)
    cards = files._cutaways(script, aligned(script["text"]), ImageFont.truetype(str(fonts[1]), 72))
    assert [card["text"] for card in cards] == [blocks[1]["text"]]
    assert cards[0]["end"] - cards[0]["start"] < 2.0
    with pytest.raises(files.MediaFileError, match="CUTAWAY_PROVENANCE_MISMATCH"):
        files._fact_intervals(
            {**script, "text": script["text"] + " Invented."}, aligned(script["text"])
        )


@pytest.mark.parametrize("text", ["x" * 300, "Secret\x00", "Invisible\u200b"])
def test_unsupported_caption_layout_fails_closed(fonts, text):
    with pytest.raises(files.MediaFileError):
        files._captions(text, aligned(text), ImageFont.truetype(str(fonts[1]), 54))


@pytest.fixture
def real_inputs(directory):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("Local FFmpeg tools unavailable")
    speech, source = directory / "fixture.mp3", directory / "fixture.mp4"
    commands = [
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:a",
            "libmp3lame",
            "-ar",
            "44100",
            str(speech),
        ],
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=1080x1920:r=24:d=1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
    ]
    for arguments in commands:
        subprocess.run(
            [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-n", *arguments],
            check=True,
            timeout=30,
            capture_output=True,
        )
    return SpeechResult(
        audio=speech.read_bytes(), alignment=aligned(), request_id="fixture-speech"
    ), source.read_bytes()


def test_actual_composition_resume_and_integrity(directory, real_inputs, fonts):
    result, source = real_inputs
    fact_id = str(uuid4())
    script = fact_script(
        {
            "path": "slides.0.body",
            "kind": "FACT",
            "text": "Un apoyo oficial para autónomos.",
            "fact_ids": [fact_id],
        }
    )
    narration = script["text"]
    result = result.model_copy(update={"alignment": aligned(narration)})
    speech = files.store_speech(directory, result, narration)
    assert speech["character_count"] == len(narration)
    assert speech["provider"] == "elevenlabs"
    requests = []
    transport = httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, content=source)
    )
    manifest = files.compose(
        directory,
        "https://files.heygen.ai/fixture.mp4",
        script,
        speech,
        "a" * 64,
        *fonts,
        transport=transport,
    )
    assert manifest["audio_present"]
    assert manifest["video_codec"] == "h264"
    assert manifest["caption_text"] == narration
    assert manifest["disclosure_burned_in"]
    assert manifest["video_sha256"] == digest(files.validate_final(directory, manifest))
    captions_bytes = (directory / "captions.json").read_bytes()
    captions = json.loads(captions_bytes)
    assert captions["script"] == script
    assert captions["cutaways"][0]["fact_ids"] == [fact_id]
    assert captions["cutaways"][0]["text"] == script["blocks"][0]["text"]
    frame_bytes = subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(directory / "video.mp4"),
            "-ss",
            "0.25",
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    with Image.open(io.BytesIO(frame_bytes)) as frame:
        assert frame.size == (1080, 1920)
        # The white source is replaced by a sourced fact card during its narration.
        assert frame.convert("RGB").getpixel((10, 500))[2] < 242
        assert max(frame.convert("RGB").getpixel((10, 1800))) < 35
        assert (
            sum(
                1
                for pixel in frame.convert("RGB").crop((64, 1790, 1000, 1850)).get_flattened_data()
                if min(pixel) > 220
            )
            > 100
        )
    assert (
        files.compose(
            directory,
            "https://files.heygen.ai/fixture.mp4",
            script,
            speech,
            "a" * 64,
            *fonts,
            transport=transport,
        )
        == manifest
    )
    assert len(requests) == 1
    captions["cutaways"][0]["fact_ids"] = [str(uuid4())]
    forged = files._json(captions)
    (directory / "captions.json").write_bytes(forged)
    with pytest.raises(files.MediaFileError, match="CUTAWAY_PROVENANCE_MISMATCH"):
        files.validate_final(directory, {**manifest, "captions_sha256": digest(forged)})
    (directory / "captions.json").write_bytes(captions_bytes)
    with pytest.raises(files.MediaFileError, match="INTEGRITY"):
        files.compose(
            directory,
            "https://files.heygen.ai/fixture.mp4",
            {**script, "disclosure": "Changed disclosure"},
            speech,
            "a" * 64,
            *fonts,
            transport=transport,
        )
    (directory / "alignment.json").write_text("{}")
    with pytest.raises(files.MediaFileError, match="INTEGRITY"):
        files.validate_final(directory, manifest)


def test_speech_duration_and_text_validation(directory, real_inputs):
    result, _ = real_inputs
    with pytest.raises(files.MediaFileError, match="ALIGNMENT_MISMATCH"):
        files.store_speech(directory, result, "Different narration")
    bad = result.model_copy(
        update={
            "alignment": Alignment(
                characters=list("Hola"),
                character_start_times_seconds=[0.0, 0.5, 1.0, 2.0],
                character_end_times_seconds=[0.5, 1.0, 2.0, 3.0],
            )
        }
    )
    with pytest.raises(files.MediaFileError, match="ALIGNMENT_MISMATCH"):
        files.store_speech(directory, bad, "Hola")


def test_no_fake_audio_allowed(directory):
    with pytest.raises(files.MediaFileError, match="INVALID_MEDIA"):
        files.store_speech(
            directory, SpeechResult(audio=b"ID3not-audio", alignment=aligned()), "Hola"
        )


def test_source_duration_mismatch_blocks_composition(directory, real_inputs, fonts, monkeypatch):
    result, source = real_inputs
    speech = files.store_speech(directory, result, "Hola")
    original = files._probe
    monkeypatch.setattr(
        files, "_probe", lambda path, kind: (5.0, {}) if kind == "source" else original(path, kind)
    )
    with pytest.raises(files.MediaFileError, match="VIDEO_AUDIO_DURATION_MISMATCH"):
        files.compose(
            directory,
            "https://files.heygen.ai/fixture.mp4",
            {"text": "Hola", "disclosure": "AI character"},
            speech,
            "a" * 64,
            *fonts,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=source)),
        )
    assert not (directory / "video.mp4").exists()
