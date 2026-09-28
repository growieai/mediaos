"""Versioned Instagram 4:5 geometry. Decorative marks never invent content.

Text regions are mirrored by migration 0019's database manifest guard. Changing
these constants requires a new template version and matching guarded migration.
"""

from PIL import Image, ImageDraw, ImageOps

from app.rendering.schemas import VisualConfig

Bounds = tuple[int, int, int, int]


def social_regions(config: VisualConfig, index: int, total: int) -> list[Bounds]:
    margin, right = config.margin, config.width - config.margin
    cover = index == 1
    closing = index == total and not cover
    return [
        (margin, 76, right - 176, 152),
        (margin, 208, 688, 524) if cover else (margin, 228, right, 432),
        (margin, 584, right, 1008)
        if cover
        else (margin + 32, 494, right - 32, 982 if closing else 1016),
        (margin + (32 if closing else 0), 1070, right - 88, 1174),
        (margin, 1222, right, 1304),
    ]


def _portrait(image: Image.Image, reference: Image.Image, bounds: Bounds, radius: int):
    x, y, right, bottom = bounds
    size = (right - x, bottom - y)
    # Crop from the upper third, preserving the face in the canonical portrait.
    fitted = ImageOps.fit(reference, size, Image.Resampling.LANCZOS, centering=(0.5, 0.3))
    mask = Image.new("L", size)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    image.paste(fitted, (x, y), mask)


def paint_social_surface(
    image: Image.Image,
    config: VisualConfig,
    index: int,
    total: int,
    reference: Image.Image | None,
) -> list[str]:
    """Return five text colors corresponding to the five immutable text fields."""
    canvas = ImageDraw.Draw(image)
    margin, right = config.margin, config.width - config.margin
    paper, ink = config.palette.background, config.palette.text
    cover, closing = index == 1, index == total and index != 1
    lime, coral = "#DDF49B", "#F3AD90"  # Decorative, never used as factual status indicators.
    background = ink if cover or closing else paper
    foreground = paper if cover or closing else ink
    canvas.rectangle((0, 0, config.width, config.height), fill=background)
    # A small editorial folio and progress segments communicate sequence without
    # inventing a category, verification label, statistic, or predicted virality.
    canvas.rounded_rectangle((right - 80, 72, right, 88), radius=8, fill=lime if cover else ink)
    segment_width = (right - margin - (total - 1) * 8) / total
    for position in range(total):
        start = round(margin + position * (segment_width + 8))
        end = round(start + segment_width)
        canvas.rounded_rectangle(
            (start, 1322, end, 1328),
            radius=3,
            fill=foreground if position == index - 1 else config.palette.accent,
        )
    if cover:
        portrait_bounds = (736, 184, right, 524)
        canvas.rounded_rectangle(portrait_bounds, radius=38, fill=lime)
        if reference is not None:
            _portrait(image, reference, portrait_bounds, 38)
        else:
            # Generic creators need no generated or borrowed face. An intentional
            # abstract identity tile is safe until an owned reference is supplied.
            canvas.ellipse((766, 216, 926, 376), fill=coral)
            canvas.rounded_rectangle((786, 354, right + 56, 566), radius=96, fill=paper)
            canvas.rectangle((736, 524, config.width, 570), fill=ink)
        canvas.rounded_rectangle((margin - 24, 550, right + 24, 1036), radius=30, fill=paper)
        colors = [paper, paper, ink, paper, paper]
    else:
        # Evidence occupies the largest quiet area; all exact source text is
        # printed at the configured minimum size, with no ellipsis or rewriting.
        canvas.rounded_rectangle(
            (margin, 454, right, 1020 if closing else 1050),
            radius=30,
            fill=paper if closing else ink,
        )
        canvas.rounded_rectangle((margin, 174, margin + 94, 186), radius=6, fill=coral)
        colors = [foreground, foreground, ink if closing else paper, foreground, foreground]
        if closing:
            canvas.rounded_rectangle((margin, 1046, right, 1196), radius=28, fill=paper)
            colors[3] = ink
    # A purely geometric forward arrow stays outside the CTA's measured region.
    arrow_color = ink if closing else foreground
    y = 1100
    canvas.line((right - 58, y, right - 18, y), fill=arrow_color, width=4)
    canvas.line((right - 32, y - 14, right - 18, y, right - 32, y + 14), fill=arrow_color, width=4)
    return colors
