import hashlib
import io

import pytest
from PIL import Image

from app.media.service import _portrait
from app.services.workflows import ConflictError


@pytest.fixture(scope="session", autouse=True)
def database():
    yield None


@pytest.mark.parametrize("format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_portrait_mime_comes_from_verified_bytes_not_extension(tmp_path, format, mime):
    data = io.BytesIO()
    Image.new("RGB", (256, 256), "white").save(data, format=format)
    path = tmp_path / "misleading-extension.webp"
    path.write_bytes(data.getvalue())
    assert _portrait(path, hashlib.sha256(data.getvalue()).hexdigest()) == (data.getvalue(), mime)


def test_webp_portrait_rejected_before_paid_speech(tmp_path):
    path = tmp_path / "portrait.jpg"
    Image.new("RGB", (256, 256), "white").save(path, format="WEBP")
    with pytest.raises(ConflictError, match="PNG or JPEG"):
        _portrait(path, hashlib.sha256(path.read_bytes()).hexdigest())


def test_portrait_changed_bytes_fail_closed(tmp_path):
    path = tmp_path / "portrait.png"
    Image.new("RGB", (256, 256), "white").save(path)
    with pytest.raises(ConflictError):
        _portrait(path, "0" * 64)
