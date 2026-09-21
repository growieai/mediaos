"""Private bounded media files; no database or provider credentials enter composition."""

import hashlib
import ipaddress
import json
import math
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import unicodedata
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from PIL import Image, ImageDraw, ImageFont

from app.media_providers.schemas import Alignment, SpeechResult, https_url

LIMIT = 200_000_000
NAMES = {"speech.mp3", "alignment.json", "source.mp4", "video.mp4", "captions.json", "compose.json"}
FINAL_CONTRACT = dict(
    schema_version=1,
    width=1080,
    height=1920,
    fps=24,
    audio_present=True,
    video_codec="h264",
    audio_codec="aac",
    sample_rate=48000,
    disclosure_burned_in=True,
)


class MediaFileError(ValueError):
    """Safe fixed-category errors; never includes URLs, credentials or process stderr."""


class MediaRetrievalError(MediaFileError):
    """A safe GET may be retried later without repeating provider generation."""

    def __init__(self, retry_after_seconds: int | None = None):
        self.retry_after_seconds = retry_after_seconds
        super().__init__("MEDIA_RETRIEVAL_UNAVAILABLE")


def _check(path: Path) -> Path:
    absolute = path.absolute()
    if any(p.is_symlink() or p.is_junction() for p in (absolute, *absolute.parents)):
        raise MediaFileError("UNSAFE_MEDIA_PATH")
    return absolute.resolve()


def media_directory(base: Path, tenant: UUID, run: UUID) -> Path:
    root = _check(base)
    path = _check(root / str(UUID(str(tenant))) / str(UUID(str(run))))
    if not path.is_relative_to(root):
        raise MediaFileError("UNSAFE_MEDIA_PATH")
    path.mkdir(parents=True, exist_ok=True)
    return _check(path)


def _file(directory: Path, name: str) -> Path:
    if name not in NAMES:
        raise MediaFileError("UNSAFE_MEDIA_PATH")
    return _check(_check(directory) / name)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def load_blob(directory: Path, filename: str, digest: str, maxbytes: int) -> bytes:
    try:
        if not 0 < maxbytes <= LIMIT:
            raise MediaFileError("MEDIA_INTEGRITY_FAILED")
        path = _file(directory, filename)
        if not path.is_file() or path.stat().st_size > maxbytes:
            raise MediaFileError("MEDIA_INTEGRITY_FAILED")
        with path.open("rb") as stream:
            data = stream.read(maxbytes + 1)
        if len(data) > maxbytes or _hash(data) != digest:
            raise MediaFileError("MEDIA_INTEGRITY_FAILED")
        return data
    except OSError:
        raise MediaFileError("MEDIA_INTEGRITY_FAILED") from None


def _write(directory: Path, name: str, data: bytes) -> None:
    path = _file(directory, name)
    if path.exists():
        load_blob(directory, name, _hash(data), len(data))
        return
    temporary = path.with_name(f"{uuid4()}.partial")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)  # Atomic create; never replaces a different committed file.
        except FileExistsError:
            load_blob(directory, name, _hash(data), len(data))
    finally:
        temporary.unlink(missing_ok=True)


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise MediaFileError("FFMPEG_REQUIRED")
    return path


def _probe(path: Path, kind: str) -> tuple[float, dict]:
    try:
        result = subprocess.run(
            [
                _tool("ffprobe"),
                *"-v error -protocol_whitelist file,pipe -show_streams -show_format -of json -f".split(),
                "mp3" if kind == "speech" else "mov",
                str(_check(path)),
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        if len(result.stdout) > 100_000:
            raise ValueError()
        metadata = json.loads(result.stdout)
        duration = float(metadata["format"]["duration"])
        if not math.isfinite(duration) or not 0 < duration <= 300:
            raise ValueError()
        streams = metadata["streams"]
        video = [s for s in streams if s["codec_type"] == "video"]
        audio = [s for s in streams if s["codec_type"] == "audio"]
        if kind == "speech":
            if (
                len(streams) != 1
                or len(audio) != 1
                or audio[0]["codec_name"] != "mp3"
                or not 16000 <= int(audio[0]["sample_rate"]) <= 48000
                or audio[0]["channels"] not in (1, 2)
            ):
                raise ValueError()
        else:
            if (
                len(video) != 1
                or len(audio) > 1
                or len(streams) != 1 + len(audio)
                or (video[0]["width"], video[0]["height"]) != (1080, 1920)
            ):
                raise ValueError()
            if (
                any(s.get("rotation", 0) != 0 for s in video[0].get("side_data_list", []))
                or video[0].get("tags", {}).get("rotate", "0") != "0"
            ):
                raise ValueError()
            if kind == "final" and (
                video[0]["codec_name"] != "h264"
                or video[0]["pix_fmt"] != "yuv420p"
                or Fraction(video[0]["avg_frame_rate"]) != 24
                or len(audio) != 1
                or audio[0]["codec_name"] != "aac"
                or int(audio[0]["sample_rate"]) != 48000
            ):
                raise ValueError()
        return duration, metadata
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        raise MediaFileError("INVALID_MEDIA") from None


def store_speech(
    directory: Path, result: SpeechResult, narration: str, model: str = "eleven_multilingual_v2"
) -> dict:
    if "".join(result.alignment.characters) != narration:
        raise MediaFileError("ALIGNMENT_MISMATCH")
    _write(directory, "speech.mp3", result.audio)
    duration, _ = _probe(_file(directory, "speech.mp3"), "speech")
    if result.alignment.character_end_times_seconds[-1] > duration + 0.25:
        raise MediaFileError("ALIGNMENT_MISMATCH")
    alignment = _json(result.alignment.model_dump())
    _write(directory, "alignment.json", alignment)
    return dict(
        schema_version=1,
        provider="elevenlabs",
        model=model,
        audio_sha256=_hash(result.audio),
        audio_size_bytes=len(result.audio),
        alignment_sha256=_hash(alignment),
        narration_sha256=_hash(narration.encode()),
        duration_seconds=duration,
        request_id=result.request_id,
        character_count=len(narration),
    )


def _download(url: str, transport: httpx.BaseTransport | None) -> bytes:
    try:
        parsed = urlsplit(https_url(url))
        if parsed.hostname != "files.heygen.ai":
            raise ValueError()
        request_url, headers, extensions = httpx.URL(url), {"Accept-Encoding": "identity"}, {}
        if not isinstance(transport, httpx.MockTransport):
            addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError()
            # Pin the validated address while preserving TLS identity; no second DNS lookup.
            request_url = request_url.copy_with(host=addresses[0][4][0])
            headers["Host"] = parsed.hostname
            extensions = {"sni_hostname": parsed.hostname}
        started, body = time.monotonic(), bytearray()
        with httpx.Client(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(60, connect=10),
        ) as client:
            with client.stream(
                "GET", request_url, headers=headers, extensions=extensions
            ) as response:
                if response.status_code == 429 or 500 <= response.status_code <= 599:
                    raw_delay = response.headers.get("Retry-After", "")
                    delay = (
                        min(int(raw_delay), 86400)
                        if re.fullmatch(r"[0-9]{1,8}", raw_delay)
                        else None
                    )
                    raise MediaRetrievalError(delay)
                if (
                    response.status_code != 200
                    or response.headers.get("content-encoding", "identity").lower() != "identity"
                ):
                    raise ValueError()
                # Check small network chunks too, rather than waiting for a
                # 64 KiB buffer while an expiring CDN response trickles data.
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > LIMIT:
                        raise ValueError()
                    if time.monotonic() - started > 180:
                        raise MediaRetrievalError()
        return bytes(body)
    except MediaRetrievalError:
        raise
    except (OSError, httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
        raise MediaRetrievalError() from None
    except (ValueError, httpx.HTTPError, httpx.InvalidURL):
        raise MediaFileError("MEDIA_DOWNLOAD_FAILED") from None


def _lines(text: str, font: ImageFont.FreeTypeFont) -> list[str]:
    missing = bytes(font.getmask("\U0010ffff"))
    if any(
        (unicodedata.category(c) in {"Cc", "Cf", "Cs"} and c not in "\n\r\t")
        or (not c.isspace() and bytes(font.getmask(c)) == missing)
        for c in set(text)
    ):
        raise MediaFileError("UNSUPPORTED_CAPTION_GLYPH")
    lines, line = [], ""
    for word in text.split():
        if font.getlength(word) > 952:
            raise MediaFileError("CAPTION_TOO_WIDE")
        candidate = f"{line} {word}" if line else word
        if font.getlength(candidate) > 952:
            lines.append(line)
            line = word
        else:
            line = candidate
    return [*lines, line]


def _captions(text: str, alignment: Alignment, font: ImageFont.FreeTypeFont) -> list[dict]:
    cues: list[dict[str, Any]] = []
    start, end = 0, 0
    words = list(re.finditer(r"\S+", text))
    for word in words:
        if len(_lines(text[start : word.end()], font)) > 2:
            cues.append(
                dict(
                    text=text[start:end],
                    start=alignment.character_start_times_seconds[start],
                    end=alignment.character_end_times_seconds[end - 1],
                )
            )
            start = end
        end = word.end()
    if end:
        cues.append(
            dict(
                text=text[start:],
                start=alignment.character_start_times_seconds[start],
                end=alignment.character_end_times_seconds[-1],
            )
        )
    if "".join(c["text"] for c in cues) != text or any(
        len(_lines(c["text"], font)) > 2 for c in cues
    ):
        raise MediaFileError("CAPTION_MISMATCH")
    return cues


def _fact_intervals(script: dict, alignment: Alignment) -> list[dict]:
    """Map exact approved block characters to their own audio interval."""
    blocks = script.get("blocks", [])
    if not isinstance(blocks, list) or len(blocks) > 10:
        raise MediaFileError("CUTAWAY_PROVENANCE_MISMATCH")
    if not blocks:
        return []
    try:
        if (
            "\n".join([block["text"] for block in blocks] + [script["disclosure"]])
            != script["text"]
            or "".join(alignment.characters) != script["text"]
        ):
            raise ValueError()
        offset, facts = 0, []
        for block in blocks:
            text = block["text"]
            if not text or not isinstance(block["path"], str):
                raise ValueError()
            if block["kind"] == "FACT":
                ids = block["fact_ids"]
                if not isinstance(ids, list) or not ids or [str(UUID(v)) for v in ids] != ids:
                    raise ValueError()
                start = alignment.character_start_times_seconds[offset]
                end = alignment.character_end_times_seconds[offset + len(text) - 1]
                if end > start:
                    facts.append(
                        dict(
                            template_version="fact-card-v1",
                            block_path=block["path"],
                            text=text,
                            fact_ids=ids,
                            start=start,
                            end=min(end, start + 2.0),
                        )
                    )
            offset += len(text) + 1
        return facts
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise MediaFileError("CUTAWAY_PROVENANCE_MISMATCH") from None


def _cutaways(script: dict, alignment: Alignment, font: ImageFont.FreeTypeFont) -> list[dict]:
    cards = []
    for fact in _fact_intervals(script, alignment):
        try:
            lines = _lines(fact["text"], font)
        except MediaFileError as exc:
            if str(exc) == "CAPTION_TOO_WIDE":
                continue
            raise
        if len(lines) <= 6:
            cards.append(fact)
    return cards


def _encode(
    directory: Path,
    duration: float,
    cues: list[dict],
    cutaways: list[dict],
    disclosure: str,
    regular: Path,
    bold: Path,
) -> bytes:
    font = ImageFont.truetype(str(_check(bold)), 54)
    disclosure_font = ImageFont.truetype(str(_check(regular)), 32)
    disclosure_lines = _lines(disclosure, disclosure_font)
    if len(disclosure_lines) > 2 or not disclosure.strip():
        raise MediaFileError("DISCLOSURE_TOO_WIDE")
    overlays = []
    for cue in cues:
        overlay = Image.new("RGBA", (1080, 190))
        draw = ImageDraw.Draw(overlay)
        draw.rounded_rectangle((40, 10, 1040, 180), radius=20, fill=(12, 24, 22, 230))
        for i, line in enumerate(_lines(cue["text"], font)):
            draw.text((64, 30 + i * 65), line, font=font, fill="white")
        overlays.append(overlay)
    disclosure_overlay = Image.new("RGBA", (1080, 150))
    draw = ImageDraw.Draw(disclosure_overlay)
    draw.rectangle((0, 0, 1080, 150), fill=(12, 24, 22, 255))
    for i, line in enumerate(disclosure_lines):
        draw.text((64, 20 + i * 42), line, font=disclosure_font, fill="white")
    card_font = ImageFont.truetype(str(_check(bold)), 72)
    cards = []
    for cutaway in cutaways:
        card = Image.new("RGBA", (1080, 1920), (248, 245, 235, 255))
        draw = ImageDraw.Draw(card)
        draw.rounded_rectangle((464, 340, 616, 352), radius=6, fill=(21, 90, 69, 255))
        lines = _lines(cutaway["text"], card_font)
        for i, line in enumerate(lines):
            draw.text(
                (540, 780 + (i - (len(lines) - 1) / 2) * 92),
                line,
                font=card_font,
                anchor="mm",
                fill=(20, 50, 42, 255),
            )
        cards.append(card)
    ffmpeg = _tool("ffmpeg")
    temporary = _check(directory) / f"{uuid4()}.partial.mp4"
    common = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "2"]
    decoder = subprocess.Popen(
        [
            *common,
            *"-protocol_whitelist file,pipe -noautorotate -f mov -i".split(),
            str(_file(directory, "source.mp4")),
            *"-an -vf".split(),
            "fps=24,tpad=stop_mode=clone:stop_duration=0.5",
            *"-pix_fmt rgb24 -f rawvideo pipe:1".split(),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        encoder = subprocess.Popen(
            [
                *common,
                *"-n -f rawvideo -pix_fmt rgb24 -s 1080x1920 -r 24 -i pipe:0 -protocol_whitelist file,pipe -f mp3 -i".split(),
                str(_file(directory, "speech.mp3")),
                *"-map 0:v:0 -map 1:a:0 -c:v libx264 -preset veryfast -crf 20 -threads 2 -pix_fmt yuv420p -c:a aac -ar 48000 -movflags +faststart -t".split(),
                str(duration),
                str(temporary),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        decoder.kill()
        decoder.wait(timeout=10)
        raise MediaFileError("VIDEO_ENCODING_FAILED") from None

    def stop_processes():
        for process in (decoder, encoder):
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass

    timer = threading.Timer(600, stop_processes)
    timer.daemon = True
    timer.start()
    try:
        assert decoder.stdout and encoder.stdin
        for index in range(math.ceil(duration * 24)):
            raw = decoder.stdout.read(1080 * 1920 * 3)
            if len(raw) != 1080 * 1920 * 3:
                raise MediaFileError("VIDEO_FRAME_MISSING")
            frame = Image.frombytes("RGB", (1080, 1920), raw).convert("RGBA")
            for cutaway, card in zip(cutaways, cards, strict=True):
                if cutaway["start"] <= index / 24 < cutaway["end"]:
                    frame = card.copy()
                    break
            for cue, overlay in zip(cues, overlays, strict=True):
                if cue["start"] <= index / 24 <= cue["end"]:
                    frame.alpha_composite(overlay, (0, 1420))
                    break
            frame.alpha_composite(disclosure_overlay, (0, 1770))
            encoder.stdin.write(frame.convert("RGB").tobytes())
        encoder.stdin.close()
        encoder.wait(timeout=30)
        if encoder.returncode or temporary.stat().st_size > LIMIT:
            raise MediaFileError("VIDEO_ENCODING_FAILED")
        _probe(temporary, "final")
        return temporary.read_bytes()
    except (OSError, subprocess.SubprocessError):
        raise MediaFileError("VIDEO_ENCODING_FAILED") from None
    finally:
        timer.cancel()
        for process in (decoder, encoder):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        temporary.unlink(missing_ok=True)


def compose(
    directory: Path,
    output_url: str,
    script: dict,
    speech: dict,
    portrait_sha256: str,
    regular_font: Path,
    bold_font: Path,
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    identity = dict(
        script_hash=_hash(_json(script)),
        narration_sha256=_hash(script["text"].encode()),
        portrait_sha256=portrait_sha256,
        speech_sha256=speech["audio_sha256"],
        alignment_sha256=speech["alignment_sha256"],
    )
    if identity["narration_sha256"] != speech["narration_sha256"]:
        raise MediaFileError("NARRATION_MISMATCH")
    if _file(directory, "compose.json").exists():
        path = _file(directory, "compose.json")
        if path.stat().st_size > 1_000_000:
            raise MediaFileError("MEDIA_INTEGRITY_FAILED")
        cached = json.loads(path.read_bytes())
        if any(cached.get(k) != v for k, v in identity.items()):
            raise MediaFileError("MEDIA_INTEGRITY_FAILED")
        validate_final(directory, cached)
        return cached
    load_blob(directory, "speech.mp3", speech["audio_sha256"], 12_000_000)
    alignment = Alignment.model_validate_json(
        load_blob(directory, "alignment.json", speech["alignment_sha256"], 1_000_000)
    )
    if "".join(alignment.characters) != script["text"]:
        raise MediaFileError("ALIGNMENT_MISMATCH")
    source = _download(output_url, transport)
    _write(directory, "source.mp4", source)
    duration, _ = _probe(_file(directory, "source.mp4"), "source")
    if abs(duration - speech["duration_seconds"]) > 0.5:
        raise MediaFileError("VIDEO_AUDIO_DURATION_MISMATCH")
    cues = _captions(script["text"], alignment, ImageFont.truetype(str(_check(bold_font)), 54))
    cutaways = _cutaways(script, alignment, ImageFont.truetype(str(_check(bold_font)), 72))
    captions = _json(
        dict(schema_version=1, script=script, text=script["text"], cues=cues, cutaways=cutaways)
    )
    _write(directory, "captions.json", captions)
    video = _encode(
        directory,
        speech["duration_seconds"],
        cues,
        cutaways,
        script["disclosure"],
        regular_font,
        bold_font,
    )
    _write(directory, "video.mp4", video)
    final_duration, _ = _probe(_file(directory, "video.mp4"), "final")
    manifest = dict(
        **FINAL_CONTRACT,
        **identity,
        source_video_sha256=_hash(source),
        video_sha256=_hash(video),
        video_size_bytes=len(video),
        captions_sha256=_hash(captions),
        caption_text=script["text"],
        disclosure=script["disclosure"],
        duration_seconds=final_duration,
    )
    _write(directory, "compose.json", _json(manifest))
    return manifest


def validate_final(directory: Path, manifest: dict) -> bytes:
    if any(manifest.get(k) != v for k, v in FINAL_CONTRACT.items()):
        raise MediaFileError("MEDIA_INTEGRITY_FAILED")
    for name, key, cap in (
        ("speech.mp3", "speech_sha256", 12_000_000),
        ("alignment.json", "alignment_sha256", 1_000_000),
        ("captions.json", "captions_sha256", 1_000_000),
        ("source.mp4", "source_video_sha256", LIMIT),
    ):
        load_blob(directory, name, manifest[key], cap)
    alignment = Alignment.model_validate_json(
        load_blob(directory, "alignment.json", manifest["alignment_sha256"], 1_000_000)
    )
    captions = json.loads(
        load_blob(directory, "captions.json", manifest["captions_sha256"], 1_000_000)
    )
    if (
        "".join(alignment.characters) != manifest["caption_text"]
        or captions["text"] != manifest["caption_text"]
        or "".join(c["text"] for c in captions["cues"]) != manifest["caption_text"]
        or _hash(manifest["caption_text"].encode()) != manifest["narration_sha256"]
        or not isinstance(captions.get("script"), dict)
        or _hash(_json(captions["script"])) != manifest["script_hash"]
        or captions["script"].get("text") != manifest["caption_text"]
        or captions["script"].get("disclosure") != manifest["disclosure"]
    ):
        raise MediaFileError("MEDIA_INTEGRITY_FAILED")
    candidates = _fact_intervals(captions["script"], alignment)
    cutaways = captions.get("cutaways")
    if not isinstance(cutaways, list) or cutaways != [c for c in candidates if c in cutaways]:
        raise MediaFileError("CUTAWAY_PROVENANCE_MISMATCH")
    video = load_blob(directory, "video.mp4", manifest["video_sha256"], LIMIT)
    duration, _ = _probe(_file(directory, "video.mp4"), "final")
    if (
        len(video) != manifest["video_size_bytes"]
        or abs(duration - manifest["duration_seconds"]) > 0.01
    ):
        raise MediaFileError("MEDIA_INTEGRITY_FAILED")
    return video
