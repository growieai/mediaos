import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from pydantic import ValidationError

from app.db.repository import canonical_hash
from app.delivery.adapter import DeliveryIntegrityError, prepare_delivery
from app.delivery.schemas import DeliveryInput, DeliveryPlan, DeliveryReceipt
from app.delivery.service import database_now_utc
from app.models.schemas import TextBlock
from app.rendering.schemas import RenderedSlide, RenderManifest, TextCoverage


@pytest.fixture
def delivery_package():
    picture = io.BytesIO()
    Image.new("RGB", (1080, 1350), "white").save(picture, format="PNG")
    png = picture.getvalue()
    caption = TextBlock(kind="CREATIVE", text="Consulta la fuente. Creadora virtual con IA.")
    manifest = RenderManifest(
        pillow_version=Image.__version__,
        status="PASS",
        draft_sha256="1" * 64,
        visual_config_sha256="2" * 64,
        font_sha256={"regular": "3" * 64, "bold": "4" * 64},
        influencer_version_id=str(uuid4()),
        language="es-ES",
        caption=caption,
        caption_coverage=TextCoverage(
            field_path="caption",
            text=caption.text,
            text_sha256=hashlib.sha256(caption.text.encode()).hexdigest(),
            placement="CAPTION",
            exact_coverage=True,
        ),
        slides=[
            RenderedSlide(
                index=1,
                filename="slide-01.png",
                sha256=hashlib.sha256(png).hexdigest(),
                text_coverage=[],
                overflow=False,
            )
        ],
        findings=[],
    )
    return manifest, {
        "slide-01.png": png,
        "manifest.json": manifest.model_dump_json().encode(),
        "caption.txt": caption.text.encode(),
    }


def archive_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), content)
    return buffer.getvalue()


def prepare(package):
    manifest, files = package
    return prepare_delivery(
        manifest,
        archive_bytes(files),
        uuid4(),
        uuid4(),
        canonical_hash(manifest.model_dump(mode="json")),
        datetime.now(UTC),
    )


def test_rehearsal_preserves_exact_approved_package_and_never_invents_post(delivery_package):
    manifest, files = delivery_package
    payload, receipt = prepare(delivery_package)
    assert payload.caption == manifest.caption
    assert payload.slides[0].sha256 == hashlib.sha256(files["slide-01.png"]).hexdigest()
    assert payload.mode == receipt.mode == "DRY_RUN"
    assert receipt.status == "DRY_RUN_COMPLETE"
    assert receipt.network_performed is False
    assert payload.post_id is receipt.post_id is receipt.published_at is None
    assert receipt.payload_sha256 == canonical_hash(payload.model_dump(mode="json"))
    assert receipt.caption_sha256 == hashlib.sha256(manifest.caption.text.encode()).hexdigest()
    assert receipt.package_sha256 == hashlib.sha256(archive_bytes(files)).hexdigest()


@pytest.mark.parametrize("mutation", ["caption", "image", "missing", "extra", "manifest", "path"])
def test_changed_or_unexpected_package_bytes_fail_closed(delivery_package, mutation):
    _, files = delivery_package
    if mutation == "caption":
        files["caption.txt"] += b" invented claim"
    elif mutation == "image":
        files["slide-01.png"] += b"tampered"
    elif mutation == "missing":
        del files["slide-01.png"]
    elif mutation == "extra":
        files["unused.png"] = b"extra"
    elif mutation == "manifest":
        data = json.loads(files["manifest.json"])
        data["caption"]["text"] = "Changed caption"
        files["manifest.json"] = json.dumps(data).encode()
    else:
        files["../slide-01.png"] = files.pop("slide-01.png")
    with pytest.raises(DeliveryIntegrityError):
        prepare(delivery_package)


def test_non_png_image_with_matching_hash_is_rejected(delivery_package):
    manifest, files = delivery_package
    image = io.BytesIO()
    Image.new("RGB", (1080, 1350), "white").save(image, format="JPEG")
    files["slide-01.png"] = image.getvalue()
    manifest.slides[0].sha256 = hashlib.sha256(image.getvalue()).hexdigest()
    files["manifest.json"] = manifest.model_dump_json().encode()
    with pytest.raises(DeliveryIntegrityError):
        prepare(delivery_package)


@pytest.mark.parametrize("status", ["BLOCKED", "REVISION_REQUIRED"])
def test_nonpassing_manifest_cannot_be_rehearsed(delivery_package, status):
    manifest, files = delivery_package
    manifest.status = status
    files["manifest.json"] = manifest.model_dump_json().encode()
    with pytest.raises(DeliveryIntegrityError):
        prepare(delivery_package)


def test_unordered_slide_identifiers_are_rejected(delivery_package):
    manifest, files = delivery_package
    manifest.slides[0].index = 2
    files["manifest.json"] = manifest.model_dump_json().encode()
    with pytest.raises(DeliveryIntegrityError):
        prepare(delivery_package)


def test_live_mode_and_fabricated_post_are_not_schema_valid(delivery_package):
    with pytest.raises(ValidationError):
        DeliveryInput(target_id=uuid4(), idempotency_key="test", mode="LIVE")
    payload, receipt = prepare(delivery_package)
    data = payload.model_dump(mode="json")
    data["post_id"] = "invented-social-post"
    with pytest.raises(ValidationError):
        DeliveryPlan.model_validate_json(json.dumps(data), strict=True)
    result = receipt.model_dump(mode="json")
    result["network_performed"] = True
    with pytest.raises(ValidationError):
        DeliveryReceipt.model_validate_json(json.dumps(result), strict=True)


def test_receipt_requires_utc_validation_timestamp(delivery_package):
    payload, receipt = prepare(delivery_package)
    data = receipt.model_dump(mode="json")
    data["validated_at"] = "2026-01-01T12:00:00"
    with pytest.raises(ValidationError):
        DeliveryReceipt.model_validate_json(json.dumps(data), strict=True)


def test_non_utc_database_session_preserves_instant_in_receipt(delivery_package):
    local_time = datetime(2026, 9, 21, 12, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    connection = SimpleNamespace(
        execute=lambda statement: SimpleNamespace(scalar_one=lambda: local_time)
    )
    normalized = database_now_utc(connection)
    manifest, files = delivery_package
    _, receipt = prepare_delivery(
        manifest,
        archive_bytes(files),
        uuid4(),
        uuid4(),
        canonical_hash(manifest.model_dump(mode="json")),
        normalized,
    )
    assert receipt.validated_at == local_time
    assert receipt.validated_at.utcoffset() == timedelta(0)
    assert receipt.model_dump(mode="json")["validated_at"] == "2026-09-21T07:00:00Z"
