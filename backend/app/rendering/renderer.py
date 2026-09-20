"""Render exact approved text with bounded, deterministic typography.

This module makes no claims about evidence validity or approval. The caller must
enforce those gates and bind the returned manifest to immutable input revisions.
Caption text is preserved as publication metadata, not silently appended to slides.
"""

import hashlib
import io
import json
import math
import unicodedata
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, ImageOps
from PIL import __version__ as pillow_version

from app.models.schemas import CarouselDraft
from app.rendering.schemas import (
    RenderedSlide,
    RenderFinding,
    RenderManifest,
    TextCoverage,
    TextLine,
    VisualConfig,
)


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _contrast(first: str, second: str) -> float:
    def luminance(color):
        channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[TextLine]:
    """Retain every code point, including spaces and explicit line breaks."""
    lines: list[TextLine] = []
    start = 0
    while start < len(text):
        end = start
        last_space = None
        while end < len(text) and text[end] != "\n":
            candidate = text[start : end + 1]
            bounds = font.getbbox(candidate)
            if bounds[2] - bounds[0] > width:
                break
            if text[end].isspace():
                last_space = end + 1
            end += 1
        if end == start and text[start] != "\n":
            raise ValueError("A single character is wider than its text region")
        if end < len(text) and text[end] != "\n" and last_space is not None:
            end = last_space
        hard_break = end < len(text) and text[end] == "\n"
        stop = end + int(hard_break)
        lines.append(TextLine(start=start, end=stop, text=text[start:end], hard_break=hard_break))
        start = stop
    if text.endswith("\n"):
        lines.append(TextLine(start=len(text), end=len(text), text=""))
    return lines


def _reconstruct(lines: list[TextLine]) -> str:
    return "".join(line.text + ("\n" if line.hard_break else "") for line in lines)


def _line_height(font: ImageFont.FreeTypeFont, lines: list[TextLine]) -> int:
    # Combining marks can exceed nominal ascent/descent. Include actual ink so
    # unusual text cannot overlap a following line or escape its fixed region.
    ink_height = max((font.getbbox(line.text, anchor="lt")[3] for line in lines), default=0)
    return math.ceil(max(sum(font.getmetrics()), ink_height)) + 4


def _unsafe_characters(text: str) -> bool:
    return any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} and c != "\n" for c in text)


def _missing_glyph(text: str, font: ImageFont.FreeTypeFont) -> bool:
    # FreeType's missing-glyph bitmap is stable within each exact font/size.
    # Whitespace has no ink and is handled by layout, not glyph comparison.
    missing = bytes(font.getmask("\U0010ffff"))
    return bool(missing) and any(
        not c.isspace() and bytes(font.getmask(c)) == missing for c in set(text)
    )


def render_carousel(
    draft: CarouselDraft,
    config: VisualConfig,
    output_dir: str | Path,
    reference_image_path: str | Path | None = None,
) -> RenderManifest:
    """Preflight all slides, then write numbered PNGs and manifest.json.

    On BLOCKED/REVISION_REQUIRED only the manifest is written. Font or filesystem
    errors raise rather than substituting a font or claiming a successful render.
    Existing managed files are never overwritten; callers supply a fresh directory.
    """
    draft = CarouselDraft.model_validate(draft.model_dump())
    config = VisualConfig.model_validate(config.model_dump())
    output = Path(output_dir)
    findings: list[RenderFinding] = []

    def finding(code, severity, path, message, index=None):
        findings.append(
            RenderFinding(
                code=code, severity=severity, field_path=path, message=message, slide_index=index
            )
        )

    font_bytes = {
        "regular": Path(config.regular_font_path).read_bytes(),
        "bold": Path(config.bold_font_path).read_bytes(),
    }
    font_hashes = {name: _hash(data) for name, data in font_bytes.items()}
    visual_data = config.model_dump(mode="json")
    # Absolute deployment paths do not change a visual configuration's identity.
    for name in font_bytes:
        visual_data[f"{name}_font_path"] = font_hashes[name]
    fonts: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    def font_for(style, size):
        key = (style, size)
        if key not in fonts:
            fonts[key] = ImageFont.truetype(
                io.BytesIO(font_bytes[style]), size=size, layout_engine=ImageFont.Layout.BASIC
            )
        return fonts[key]

    if draft.disclosure != config.required_disclosure:
        finding(
            "DISCLOSURE_MISMATCH",
            "BLOCKED",
            "disclosure",
            "The exact required AI disclosure is missing.",
        )
    if [slide.index for slide in draft.slides] != list(range(1, len(draft.slides) + 1)):
        finding(
            "SLIDE_SEQUENCE",
            "BLOCKED",
            "slides",
            "Slide indexes must be contiguous and start at one.",
        )
    for color in (config.palette.text, config.palette.accent):
        if _contrast(color, config.palette.background) < config.minimum_contrast_ratio:
            finding(
                "LOW_CONTRAST",
                "BLOCKED",
                "palette",
                "Text contrast is below the configured minimum.",
            )

    reference = None
    reference_hash = None
    if reference_image_path is not None:
        ref_bytes = Path(reference_image_path).read_bytes()
        # Pin the attempted input even when visual QA rejects its size or format.
        # Otherwise the persisted BLOCKED result cannot prove which reference failed.
        reference_hash = _hash(ref_bytes)
        if len(ref_bytes) > 20_000_000:
            finding("REFERENCE_TOO_LARGE", "BLOCKED", "reference", "Reference image exceeds 20 MB.")
        else:
            try:
                with Image.open(io.BytesIO(ref_bytes)) as source:
                    if source.width * source.height > 20_000_000:
                        raise ValueError("Reference image exceeds 20 million pixels")
                    source.load()
                    reference = ImageOps.contain(
                        ImageOps.exif_transpose(source).convert("RGB"), (144, 144)
                    )
            except (OSError, ValueError, Image.DecompressionBombError):
                finding(
                    "INVALID_REFERENCE",
                    "BLOCKED",
                    "reference",
                    "The supplied raster reference cannot be safely decoded.",
                )

    caption = TextCoverage(
        field_path="caption",
        text=draft.caption.text,
        text_sha256=_hash(draft.caption.text.encode()),
        fact_ids=[str(value) for value in draft.caption.fact_ids],
        placement="CAPTION",
        exact_coverage=True,
    )
    if _unsafe_characters(draft.caption.text):
        finding(
            "UNSAFE_TEXT",
            "BLOCKED",
            "caption",
            "Control or direction-changing characters are unsupported.",
        )
    rendered: list[RenderedSlide] = []
    planned: list[list[tuple[TextCoverage, str]]] = []
    for slide in draft.slides:
        margin = config.margin
        right = config.width - margin
        # Each region has a fixed maximum size. Font sizes never shrink to hide overflow.
        fields = [
            (
                "display_name",
                config.display_name,
                [],
                "bold",
                config.identity_font_size,
                (margin, 72, right - 176, 168),
            ),
            (
                f"slides[{slide.index - 1}].headline",
                slide.headline.text,
                slide.headline.fact_ids,
                "bold",
                config.headline_font_size,
                (margin, 238, right, 426),
            ),
            (
                f"slides[{slide.index - 1}].body",
                slide.body.text,
                slide.body.fact_ids,
                "regular",
                config.body_font_size,
                (margin, 458, right, 1000),
            ),
            (
                "cta",
                draft.cta.text,
                draft.cta.fact_ids,
                "bold",
                config.cta_font_size,
                (margin, 1042, right, 1142),
            ),
            (
                "disclosure",
                draft.disclosure,
                [],
                "regular",
                config.disclosure_font_size,
                (margin, 1198, right, 1296),
            ),
        ]
        coverage = []
        drawing = []
        overflow = False
        for path, content, fact_ids, style, size, bounds in fields:
            font = font_for(style, size)
            if _unsafe_characters(content):
                finding(
                    "UNSAFE_TEXT",
                    "BLOCKED",
                    path,
                    "Control or direction-changing characters are unsupported.",
                    slide.index,
                )
            if _missing_glyph(content, font):
                finding(
                    "MISSING_GLYPH",
                    "BLOCKED",
                    path,
                    "The configured font cannot render every character.",
                    slide.index,
                )
            try:
                lines = _wrap(content, font, bounds[2] - bounds[0])
            except ValueError:
                lines = []
            exact = _reconstruct(lines) == content
            line_height = _line_height(font, lines)
            if len(lines) * line_height > bounds[3] - bounds[1] or not exact:
                overflow = True
                finding(
                    "TEXT_OVERFLOW",
                    "REVISION_REQUIRED",
                    path,
                    "Text does not fit at the configured safe font size; revise the source asset.",
                    slide.index,
                )
            record = TextCoverage(
                field_path=path,
                text=content,
                text_sha256=_hash(content.encode()),
                fact_ids=[str(value) for value in fact_ids],
                lines=lines,
                exact_coverage=exact,
                font_size=size,
                bounds=bounds,
            )
            coverage.append(record)
            drawing.append((record, style))
        rendered.append(
            RenderedSlide(
                index=slide.index,
                filename=None,
                sha256=None,
                text_coverage=coverage,
                overflow=overflow,
            )
        )
        planned.append(drawing)

    status: Literal["PASS", "REVISION_REQUIRED", "BLOCKED"] = (
        "BLOCKED"
        if any(f.severity == "BLOCKED" for f in findings)
        else "REVISION_REQUIRED"
        if findings
        else "PASS"
    )
    manifest = RenderManifest(
        pillow_version=pillow_version,
        status=status,
        draft_sha256=_hash(_canonical(draft.model_dump(mode="json"))),
        visual_config_sha256=_hash(_canonical(visual_data)),
        font_sha256=font_hashes,
        reference_sha256=reference_hash,
        influencer_version_id=str(draft.influencer_version_id),
        language=draft.language,
        caption=draft.caption,
        caption_coverage=caption,
        slides=rendered,
        findings=findings,
    )
    encoded: list[tuple[str, bytes]] = []
    if status == "PASS":
        for rendered_slide, drawing in zip(rendered, planned, strict=True):
            image = Image.new("RGB", (config.width, config.height), config.palette.background)
            canvas = ImageDraw.Draw(image)
            canvas.rectangle(
                (config.margin, 202, config.width - config.margin, 208), fill=config.palette.accent
            )
            if reference is not None:
                image.paste(reference, (config.width - config.margin - 144, 56))
            for record, style in drawing:
                assert record.bounds is not None and record.font_size is not None
                font = font_for(style, record.font_size)
                x, top, _, _ = record.bounds
                line_height = _line_height(font, record.lines)
                for line_number, line in enumerate(record.lines):
                    # Account for glyph overhangs rather than clipping an initial character.
                    box = font.getbbox(line.text, anchor="lt")
                    canvas.text(
                        (x - box[0], top + line_number * line_height),
                        line.text,
                        font=font,
                        anchor="lt",
                        fill=config.palette.text,
                    )
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", optimize=False, compress_level=9)
            filename = f"slide-{rendered_slide.index:02d}.png"
            png_content = buffer.getvalue()
            rendered_slide.filename, rendered_slide.sha256 = filename, _hash(png_content)
            encoded.append((filename, png_content))
    encoded.append(("manifest.json", _canonical(manifest.model_dump(mode="json"))))
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() or (output / name).is_symlink() for name, _ in encoded):
        raise FileExistsError(
            "Render output already exists; use a new immutable artifact directory"
        )
    for filename, data in encoded:
        with (output / filename).open("xb") as file:
            file.write(data)
    return manifest
