"""Encode an isolated local concept preview without database or provider access."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import threading
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

from PIL import Image
from PIL import __version__ as pillow_version

from app.video_preview.schemas import ConceptSpec, PreviewManifest

REPO_ROOT = Path(__file__).resolve().parents[3]
ENCODE_TIMEOUT_SECONDS = 600


def repository_input(root: Path, value: str | Path) -> Path:
    root = root.resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Preview inputs must be existing files inside the repository")
    if path.stat().st_size > 20_000_000:
        raise ValueError("Preview inputs must not exceed 20 MB")
    return path


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_media(path: Path, spec: ConceptSpec, ffprobe: str) -> dict:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    expected_frames = sum(scene.duration_seconds for scene in spec.scenes) * spec.fps
    if len(streams) != 1:
        raise ValueError("A preview must have exactly one video stream and no audio")
    stream = streams[0]
    if (
        stream.get("codec_type") != "video"
        or stream.get("codec_name") != "h264"
        or stream.get("width") != spec.width
        or stream.get("height") != spec.height
        or stream.get("pix_fmt") != "yuv420p"
        or Fraction(stream.get("avg_frame_rate", "0")) != spec.fps
        or int(stream.get("nb_frames", 0)) != expected_frames
        or abs(float(data.get("format", {}).get("duration", 0)) - expected_frames / spec.fps) > 0.05
    ):
        raise ValueError("Encoded preview does not match its declared media contract")
    return data


def render_preview(spec_path: Path, repo_root: Path = REPO_ROOT) -> Path:
    from app.video_preview.renderer import FrameRenderer

    root = repo_root.resolve()
    spec_file = repository_input(root, spec_path)
    if spec_file.stat().st_size > 65_536:
        raise ValueError("Concept specification must not exceed 64 KiB")
    with spec_file.open("rb") as stream:
        spec_bytes = stream.read(65_537)
    if len(spec_bytes) > 65_536:
        raise ValueError("Concept specification must not exceed 64 KiB")
    spec = ConceptSpec.model_validate_json(spec_bytes)
    inputs = {"spec": spec_file}
    for field in ("portrait_path", "regular_font_path", "bold_font_path"):
        inputs[field] = repository_input(root, getattr(spec, field))
    hashes = {key: file_hash(path) for key, path in inputs.items()}
    if hashes["spec"] != hashlib.sha256(spec_bytes).hexdigest():
        raise ValueError("The concept specification changed during validation")
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("Local ffmpeg and ffprobe are required on PATH; no provider is called")
    version = subprocess.run(
        [ffmpeg, "-version"], capture_output=True, text=True, check=True, timeout=10
    ).stdout.splitlines()[0]
    renderer = FrameRenderer(spec, root)
    expected = sum(scene.duration_seconds for scene in spec.scenes) * spec.fps
    if renderer.frame_count != expected:
        raise ValueError("Renderer frame count differs from the strict specification")
    parent = root / ".local" / "video-previews"
    if not parent.resolve().is_relative_to(root) or any(
        part.is_symlink() for part in (root / ".local", parent)
    ):
        raise ValueError("Preview output storage must remain inside the repository")
    parent.mkdir(parents=True, exist_ok=True)
    output = parent / str(uuid4())
    output.mkdir(exist_ok=False)
    temporary = output / "video.partial.mp4"
    args = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-n",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{spec.width}x{spec.height}",
        "-r",
        str(spec.fps),
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "20",
        "-movflags",
        "+faststart",
        str(temporary),
    ]
    selected = {spec.fps + round(i * (expected - 1 - spec.fps) / 5) for i in range(6)}
    samples: list[Image.Image] = []
    timed_out = threading.Event()
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors
        )

        def stop_encoder():
            timed_out.set()
            process.kill()

        timer = threading.Timer(ENCODE_TIMEOUT_SECONDS, stop_encoder)
        timer.daemon = True
        timer.start()
        try:
            assert process.stdin is not None
            for index in range(expected):
                frame = renderer.frame(index)
                if frame.mode != "RGB" or frame.size != (spec.width, spec.height):
                    raise ValueError("Renderer emitted an invalid frame")
                if index in selected:
                    samples.append(frame.copy())
                process.stdin.write(frame.tobytes())
            process.stdin.close()
            process.wait(timeout=ENCODE_TIMEOUT_SECONDS)
            if process.returncode or timed_out.is_set():
                raise RuntimeError("Local video encoding failed or exceeded its time limit")
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
    validate_media(temporary, spec, ffprobe)
    if hashes != {key: file_hash(repository_input(root, path)) for key, path in inputs.items()}:
        raise ValueError("A preview input changed during rendering; output cannot be completed")
    manifest = PreviewManifest(
        spec=spec,
        pillow_version=pillow_version,
        input_sha256=hashes,
        ffmpeg_version=version,
        output_sha256=file_hash(temporary),
        frame_count=expected,
        duration_seconds=expected // spec.fps,
    )
    samples[0].save(output / "poster.png")
    contact = Image.new("RGB", (1080, 1280), "#101816")
    for index, frame in enumerate(samples):
        frame.thumbnail((360, 640), Image.Resampling.LANCZOS)
        contact.paste(frame, ((index % 3) * 360, (index // 3) * 640))
    contact.save(output / "contact-sheet.png")
    (output / "spec.json").write_bytes(spec_bytes)
    temporary.rename(output / "video.mp4")
    (output / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render a silent, unpublished local concept preview"
    )
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    print(render_preview(args.spec))


if __name__ == "__main__":
    main()
