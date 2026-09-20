"""Local, deterministic package rehearsal. This module has no network client."""

import hashlib
import io
import zipfile
from datetime import datetime
from typing import cast
from uuid import UUID

from PIL import Image
from pydantic import ValidationError

from app.db.repository import canonical_hash
from app.delivery.schemas import DeliveryPlan, DeliveryReceipt, DeliverySlide
from app.rendering.schemas import RenderManifest

ADAPTER_VERSION = "dry-run-v1"
MAX_PACKAGE_BYTES = 64_000_000


class RetryableDeliveryError(Exception):
    """Explicit local transient failure; the persisted service owns the retry."""


class DeliveryIntegrityError(Exception):
    def __init__(self):
        super().__init__("DELIVERY_PACKAGE_INVALID")


def prepare_delivery(
    manifest: RenderManifest,
    archive: bytes,
    target_id: UUID,
    render_id: UUID,
    manifest_hash: str,
    validated_at: datetime,
) -> tuple[DeliveryPlan, DeliveryReceipt]:
    """Verify exact archive bytes and produce a receipt, without a platform claim."""
    if (
        manifest.status != "PASS"
        or canonical_hash(manifest.model_dump(mode="json")) != manifest_hash
        or not 1 <= len(manifest.slides) <= 20
        or not 0 < len(archive) <= MAX_PACKAGE_BYTES
    ):
        raise DeliveryIntegrityError()
    expected = [f"slide-{index:02d}.png" for index in range(1, len(manifest.slides) + 1)]
    expected.extend(["manifest.json", "caption.txt"])
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            infos = package.infolist()
            if (
                sorted(info.filename for info in infos) != sorted(expected)
                or any(info.file_size > 20_000_000 or info.flag_bits & 1 for info in infos)
                or sum(info.file_size for info in infos) > MAX_PACKAGE_BYTES
            ):
                raise DeliveryIntegrityError()
            archive_manifest = RenderManifest.model_validate_json(
                package.read("manifest.json"), strict=True
            )
            if canonical_hash(archive_manifest.model_dump(mode="json")) != manifest_hash:
                raise DeliveryIntegrityError()
            if package.read("caption.txt") != manifest.caption.text.encode("utf-8"):
                raise DeliveryIntegrityError()
            for index, slide in enumerate(manifest.slides, 1):
                filename = f"slide-{index:02d}.png"
                if slide.index != index or slide.filename != filename or slide.overflow:
                    raise DeliveryIntegrityError()
                content = package.read(filename)
                if hashlib.sha256(content).hexdigest() != slide.sha256:
                    raise DeliveryIntegrityError()
                with Image.open(io.BytesIO(content)) as image:
                    if image.format != "PNG" or image.size != (1080, 1350):
                        raise DeliveryIntegrityError()
                    image.verify()
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, ValidationError):
        raise DeliveryIntegrityError() from None
    payload = DeliveryPlan(
        target_id=target_id,
        render_run_id=render_id,
        manifest_hash=manifest_hash,
        language=manifest.language,
        caption=manifest.caption,
        slides=[
            DeliverySlide(
                index=index, filename=f"slide-{index:02d}.png", sha256=cast(str, slide.sha256)
            )
            for index, slide in enumerate(manifest.slides, 1)
        ],
        package_sha256=hashlib.sha256(archive).hexdigest(),
    )
    receipt = DeliveryReceipt(
        package_sha256=payload.package_sha256,
        payload_sha256=canonical_hash(payload.model_dump(mode="json")),
        manifest_hash=manifest_hash,
        caption_sha256=hashlib.sha256(manifest.caption.text.encode("utf-8")).hexdigest(),
        slide_sha256=[slide.sha256 for slide in payload.slides],
        validated_at=validated_at,
    )
    return payload, receipt
