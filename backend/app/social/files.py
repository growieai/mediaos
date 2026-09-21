"""Exact reviewed JPEGs and short-lived, purpose-bound public media capabilities."""

import base64
import hashlib
import hmac
import io
import json
import os
import time
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image

from app.db.repository import canonical_hash
from app.media.files import media_directory
from app.rendering.schemas import RenderManifest
from app.rendering.service import validate_files
from app.social.schemas import PublishPlan, PublishSlide


def sign(key: str, purpose: str, payload: dict) -> str:
    if len(key) < 32:
        raise ValueError("A dedicated strong signing key is required")
    encoded = (
        base64.urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(
        key.encode(), (purpose + ":" + encoded).encode(), hashlib.sha256
    ).hexdigest()
    return encoded + "." + signature


def verify(key: str, purpose: str, token: str) -> dict:
    try:
        if len(token) > 2048:
            raise ValueError
        encoded, signature = token.split(".")
        expected = hmac.new(
            key.encode(), (purpose + ":" + encoded).encode(), hashlib.sha256
        ).hexdigest()
        if len(key) < 32 or not hmac.compare_digest(signature, expected):
            raise ValueError
        result = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if not isinstance(result, dict) or type(result.get("expires")) is not int:
            raise ValueError
        if not time.time() < result["expires"] <= time.time() + 3601:
            raise ValueError
        return result
    except (ValueError, TypeError, UnicodeError):
        raise PermissionError("Invalid or expired capability") from None


def prepare(directory: Path, manifest: RenderManifest, connection: UUID, render: UUID):
    originals = validate_files(directory, manifest)
    images = {}
    slides = []
    for slide in manifest.slides:
        if slide.sha256 is None:
            raise ValueError("Completed source image required")
        with Image.open(io.BytesIO(originals[f"slide-{slide.index:02d}.png"])) as source:
            target = io.BytesIO()
            source.convert("RGB").save(target, format="JPEG", quality=95, subsampling=0)
        raw = target.getvalue()
        if len(raw) > 8_000_000:
            raise ValueError("Instagram JPEG size exceeded")
        images[slide.index] = raw
        slides.append(
            PublishSlide(
                index=slide.index,
                sha256=hashlib.sha256(raw).hexdigest(),
                source_sha256=slide.sha256,
                width=1080,
                height=1350,
            )
        )
    plan = PublishPlan(
        connection_id=connection,
        render_run_id=render,
        manifest_hash=canonical_hash(manifest.model_dump(mode="json")),
        caption=manifest.caption.text,
        slides=slides,
    )
    return plan, images


def directory(base: Path, tenant: UUID, run: UUID):
    return media_directory(base, tenant, run)


def save_images(base: Path, tenant: UUID, run: UUID, images: dict[int, bytes]):
    folder = directory(base, tenant, run)
    for index, raw in images.items():
        path = folder / f"image-{index:02d}.jpg"
        if path.exists():
            if path.is_symlink() or path.read_bytes() != raw:
                raise ValueError("Immutable social image mismatch")
            continue
        temporary = folder / f"{uuid4()}.partial"
        try:
            with temporary.open("xb") as handle:
                if os.name != "nt":
                    temporary.chmod(0o600)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def image_bytes(base: Path, tenant: UUID, run: UUID, plan: PublishPlan, index: int):
    if not 1 <= index <= len(plan.slides):
        raise LookupError("Slide not found")
    path = directory(base, tenant, run) / f"image-{index:02d}.jpg"
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 8_000_000:
        raise ValueError("Invalid social image")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != plan.slides[index - 1].sha256:
        raise ValueError("Social image checksum mismatch")
    with Image.open(io.BytesIO(raw)) as result:
        if result.format != "JPEG" or result.size != (1080, 1350):
            raise ValueError("Invalid social image format")
        result.verify()
    return raw
