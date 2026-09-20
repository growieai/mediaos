"""Pure rendering tests; no database or external provider is needed."""

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image
from pydantic import ValidationError

from app.models.schemas import CarouselDraft, CarouselSlide, TextBlock
from app.rendering.renderer import render_carousel
from app.rendering.schemas import VisualConfig, VisualPalette


@pytest.fixture
def visual():
    root = Path(__file__).resolve().parents[1]
    fonts = root / "assets" / "fonts"
    regular, bold = fonts / "Inter-Regular.ttf", fonts / "Inter-Bold.ttf"
    assert regular.exists() and bold.exists(), "Install the repository's pinned font bundle"
    return VisualConfig(
        display_name="Creadora de prueba",
        required_disclosure="Contenido creado por una identidad virtual con IA.",
        regular_font_path=str(regular),
        bold_font_path=str(bold),
    )


@pytest.fixture
def draft(visual):
    return CarouselDraft(
        influencer_version_id=UUID("00000000-0000-0000-0000-000000000123"),
        language="es-ES",
        brand_association_level=0,
        slides=[
            CarouselSlide(
                index=1,
                headline=TextBlock(kind="CREATIVE", text="Información para revisar"),
                body=TextBlock(
                    kind="FACT",
                    text="La solicitud estará abierta del 1 al 20 de diciembre.\nÁvila, Málaga y Cádiz.",
                    fact_ids=[UUID("00000000-0000-0000-0000-000000000456")],
                ),
            )
        ],
        caption=TextBlock(
            kind="CREATIVE", text="Consulta los requisitos íntegros en la fuente oficial."
        ),
        cta=TextBlock(kind="CREATIVE", text="Consulta la fuente oficial."),
        disclosure=visual.required_disclosure,
    )


def test_render_dimensions_hashes_and_exact_span_coverage(tmp_path, draft, visual):
    manifest = render_carousel(draft, visual, tmp_path)
    assert manifest.status == "PASS", manifest.findings
    assert len(manifest.slides) == 1
    slide = manifest.slides[0]
    assert slide.filename == "slide-01.png"
    with Image.open(tmp_path / slide.filename) as image:
        assert image.size == (1080, 1350)
        assert image.mode == "RGB"
    assert slide.sha256 == hashlib.sha256((tmp_path / slide.filename).read_bytes()).hexdigest()
    assert manifest.caption.text == draft.caption.text
    assert manifest.caption_coverage.placement == "CAPTION"
    for coverage in slide.text_coverage:
        assert coverage.exact_coverage
        restored = "".join(line.text + ("\n" if line.hard_break else "") for line in coverage.lines)
        assert restored == coverage.text
        for line in coverage.lines:
            assert coverage.text[line.start : line.end] == line.text + (
                "\n" if line.hard_break else ""
            )
    body = next(c for c in slide.text_coverage if c.field_path.endswith(".body"))
    assert body.text == draft.slides[0].body.text
    assert body.fact_ids == [str(draft.slides[0].body.fact_ids[0])]
    assert "Ávila, Málaga y Cádiz." in body.text
    saved = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert saved["slides"][0]["sha256"] == slide.sha256


def test_render_is_deterministic_and_filenames_are_content_independent(tmp_path, draft, visual):
    first = render_carousel(draft, visual, tmp_path / "first")
    second = render_carousel(draft, visual, tmp_path / "second")
    assert first.model_dump() == second.model_dump()
    assert (tmp_path / "first" / "slide-01.png").read_bytes() == (
        tmp_path / "second" / "slide-01.png"
    ).read_bytes()
    draft.slides[0].headline.text = "../../escape.png"
    changed = render_carousel(draft, visual, tmp_path / "third")
    assert changed.slides[0].filename == "slide-01.png"
    assert not (tmp_path / "escape.png").exists()


def test_overflow_requires_revision_without_output_or_smaller_font(tmp_path, draft, visual):
    draft.slides[0].body.text = "Una condición importante. " * 180
    manifest = render_carousel(draft, visual, tmp_path)
    assert manifest.status == "REVISION_REQUIRED"
    assert any(
        f.code == "TEXT_OVERFLOW" and f.field_path.endswith(".body") for f in manifest.findings
    )
    assert manifest.slides[0].overflow
    assert not list(tmp_path.glob("*.png"))
    body = next(c for c in manifest.slides[0].text_coverage if c.field_path.endswith(".body"))
    assert body.text == draft.slides[0].body.text
    assert body.font_size == visual.body_font_size


@pytest.mark.parametrize("disclosure", ["", "Una creadora."])
def test_missing_or_changed_disclosure_blocks_all_images(tmp_path, draft, visual, disclosure):
    draft.disclosure = disclosure
    manifest = render_carousel(draft, visual, tmp_path)
    assert manifest.status == "BLOCKED"
    assert any(f.code == "DISCLOSURE_MISMATCH" for f in manifest.findings)
    assert not list(tmp_path.glob("*.png"))


def test_required_disclosure_and_cta_are_on_every_slide(tmp_path, draft, visual):
    draft.slides.append(draft.slides[0].model_copy(update={"index": 2}, deep=True))
    manifest = render_carousel(draft, visual, tmp_path)
    assert manifest.status == "PASS"
    for slide in manifest.slides:
        fields = {coverage.field_path: coverage for coverage in slide.text_coverage}
        assert fields["disclosure"].text == visual.required_disclosure
        assert fields["disclosure"].font_size >= 26
        assert fields["cta"].text == draft.cta.text


def test_low_contrast_and_unsafe_control_text_fail_closed(tmp_path, draft, visual):
    visual.palette = VisualPalette(background="#FFFFFF", text="#EEEEEE", accent="#EEEEEE")
    draft.slides[0].body.text = "Una condición\u202efalsa"
    manifest = render_carousel(draft, visual, tmp_path)
    assert manifest.status == "BLOCKED"
    assert {f.code for f in manifest.findings} >= {"LOW_CONTRAST", "UNSAFE_TEXT"}
    assert not list(tmp_path.glob("*.png"))


def test_invalid_reference_fails_closed_and_valid_reference_is_hashed(tmp_path, draft, visual):
    invalid = tmp_path / "invalid.dat"
    invalid.write_bytes(b"not an image")
    failed = render_carousel(draft, visual, tmp_path / "failed", invalid)
    assert failed.status == "BLOCKED"
    assert failed.reference_sha256 == hashlib.sha256(invalid.read_bytes()).hexdigest()
    assert any(f.code == "INVALID_REFERENCE" for f in failed.findings)
    reference = tmp_path / "portrait.png"
    Image.new("RGB", (50, 100), "#FF0000").save(reference)
    success = render_carousel(draft, visual, tmp_path / "success", reference)
    assert success.status == "PASS"
    assert success.reference_sha256 == hashlib.sha256(reference.read_bytes()).hexdigest()


def test_oversized_reference_retains_its_hash_in_blocked_manifest(tmp_path, draft, visual):
    reference = tmp_path / "oversized.png"
    reference.write_bytes(b"x" * 20_000_001)
    failed = render_carousel(draft, visual, tmp_path / "failed", reference)
    assert failed.status == "BLOCKED"
    assert failed.reference_sha256 == hashlib.sha256(reference.read_bytes()).hexdigest()
    assert any(f.code == "REFERENCE_TOO_LARGE" for f in failed.findings)
    assert not list((tmp_path / "failed").glob("*.png"))


def test_render_never_overwrites_existing_artifact(tmp_path, draft, visual):
    render_carousel(draft, visual, tmp_path)
    original = (tmp_path / "slide-01.png").read_bytes()
    draft.slides[0].body.text = "Texto nuevo."
    with pytest.raises(FileExistsError):
        render_carousel(draft, visual, tmp_path)
    assert (tmp_path / "slide-01.png").read_bytes() == original


def test_font_sizes_are_bounded_by_readability_policy(visual):
    data = visual.model_dump()
    data["body_font_size"] = 12
    with pytest.raises(ValidationError):
        VisualConfig.model_validate(data)


def test_slide_indexes_cannot_control_paths(tmp_path, draft, visual):
    draft.slides[0].index = 999999
    manifest = render_carousel(draft, visual, tmp_path)
    assert manifest.status == "BLOCKED"
    assert any(f.code == "SLIDE_SEQUENCE" for f in manifest.findings)
    assert not list(tmp_path.glob("*.png"))


def test_font_location_does_not_change_config_identity_or_png(tmp_path, draft, visual):
    original = render_carousel(draft, visual, tmp_path / "original")
    copied = visual.model_copy(deep=True)
    for style in ("regular", "bold"):
        path = tmp_path / f"moved-{style}.ttf"
        path.write_bytes(Path(getattr(visual, f"{style}_font_path")).read_bytes())
        setattr(copied, f"{style}_font_path", str(path))
    relocated = render_carousel(draft, copied, tmp_path / "relocated")
    assert original.visual_config_sha256 == relocated.visual_config_sha256
    assert original.slides[0].sha256 == relocated.slides[0].sha256


def test_caption_and_cta_fact_references_remain_exact(tmp_path, draft, visual):
    fact = draft.slides[0].body.fact_ids[0]
    draft.caption = TextBlock(kind="FACT", text="Importe máximo: 100 €.", fact_ids=[fact])
    draft.cta = TextBlock(kind="FACT", text="Fin de solicitud: 20/12.", fact_ids=[fact])
    manifest = render_carousel(draft, visual, tmp_path)
    assert manifest.status == "PASS"
    assert manifest.caption == draft.caption
    assert manifest.caption_coverage.fact_ids == [str(fact)]
    cta = next(c for c in manifest.slides[0].text_coverage if c.field_path == "cta")
    assert cta.text == draft.cta.text and cta.fact_ids == [str(fact)]


def test_unavailable_glyph_does_not_silently_become_a_box(tmp_path, draft, visual):
    draft.slides[0].body.text = "Texto \U0010ffff"
    manifest = render_carousel(draft, visual, tmp_path)
    assert manifest.status == "BLOCKED"
    assert any(f.code == "MISSING_GLYPH" for f in manifest.findings)
    assert not list(tmp_path.glob("*.png"))
