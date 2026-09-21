"""Local art-direction study. Never produces an approved or publishable asset."""

import io
import math
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.video_preview.schemas import ConceptSpec

PREVIEW_LABEL = "CONCEPT PREVIEW / SILENT / AI CHARACTER"
FOOTER_LABEL = "Proposed identity / No voice or lip-sync / Not for publication"
SCRIPT_LABEL = "SCRIPT PREVIEW / NO VOICE"
INK = "#142B27"
CREAM = "#F2F0E7"
LIME = "#D6FA75"
MUTED = "#BDCCC3"


def safe_input(root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or ":" in value:
        raise ValueError("Assets must be repository-relative local files")
    path = (root.resolve() / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Missing asset or asset outside repository")
    return path


def _ease(value: float) -> float:
    return 1 - (1 - min(1.0, max(0.0, value))) ** 3


class FrameRenderer:
    def __init__(self, spec: ConceptSpec, repo_root: Path):
        self.spec = ConceptSpec.model_validate_json(spec.model_dump_json())
        self.frame_count = sum(scene.duration_seconds for scene in spec.scenes) * spec.fps
        self.fonts: dict[tuple[int, str], ImageFont.FreeTypeFont] = {}
        self.font_bytes = {
            "regular": safe_input(repo_root, spec.regular_font_path).read_bytes(),
            "bold": safe_input(repo_root, spec.bold_font_path).read_bytes(),
        }
        image_bytes = safe_input(repo_root, spec.portrait_path).read_bytes()
        if len(image_bytes) > 30_000_000:
            raise ValueError("Portrait exceeds the local preview input limit")
        try:
            with Image.open(io.BytesIO(image_bytes)) as source:
                if max(source.size) > 4096 or min(source.size) < 256:
                    raise ValueError("Portrait dimensions must be between 256 and 4096")
                if source.width * source.height > 16_000_000 or getattr(source, "n_frames", 1) != 1:
                    raise ValueError("Portrait must be a single bounded image")
                source.load()
                self.portrait = ImageOps.exif_transpose(source).convert("RGB")
        except (OSError, Image.DecompressionBombError) as exc:
            raise ValueError("Portrait is not a valid bounded image") from exc
        self.layouts = []
        for scene in spec.scenes:
            self.layouts.append(
                {
                    "headline": self._lines(
                        scene.headline, 112, 932, 3 if scene.kind == "QUESTION" else 2, "bold"
                    ),
                    "caption": self._lines(scene.caption, 43, 880, 3, "bold"),
                    "supporting": self._lines(scene.supporting, 43, 932, 2, "regular"),
                    "kicker": self._lines(scene.kicker, 30, 900, 1, "bold"),
                }
            )
        self._lines(spec.subject_name, 35, 780, 1, "bold")
        self.disclosure_lines = self._lines(spec.disclosure, 28, 950, 1, "regular")
        self._lines(PREVIEW_LABEL, 25, 968, 1, "bold")
        self._lines(FOOTER_LABEL, 22, 968, 1, "regular")
        self._lines(SCRIPT_LABEL, 22, 932, 1, "bold")
        self.marks = Image.new("RGBA", (1080, 1920))
        draw = ImageDraw.Draw(self.marks)
        draw.rectangle((0, 0, 1080, 88), fill=INK)
        draw.text((56, 29), PREVIEW_LABEL, font=self.font(25, "bold"), fill=LIME)
        draw.rectangle((0, 1828, 1080, 1920), fill=INK)
        draw.text((56, 1840), self.disclosure_lines[0], font=self.font(28), fill=CREAM)
        draw.text(
            (56, 1882),
            FOOTER_LABEL,
            font=self.font(22),
            fill=MUTED,
        )

    def font(self, size: int, style: str = "regular") -> ImageFont.FreeTypeFont:
        key = (size, style)
        if key not in self.fonts:
            self.fonts[key] = ImageFont.truetype(
                io.BytesIO(self.font_bytes[style]), size, layout_engine=ImageFont.Layout.BASIC
            )
        return self.fonts[key]

    def _lines(self, value: str, size: int, width: int, count: int, style: str) -> list[str]:
        font = self.font(size, style)
        missing = bytes(font.getmask("\U0010ffff"))
        if any(
            (unicodedata.category(char) in {"Cc", "Cf", "Cs"} and char != "\n")
            or (not char.isspace() and bytes(font.getmask(char)) == missing)
            for char in set(value)
        ):
            raise ValueError("Text contains unsupported glyphs or control characters")
        lines = []
        for paragraph in value.split("\n"):
            line = ""
            for word in paragraph.split(" "):
                if font.getlength(word) > width:
                    raise ValueError("Text word exceeds preview region")
                candidate = f"{line} {word}" if line else word
                if font.getlength(candidate) > width:
                    lines.append(line)
                    line = word
                else:
                    line = candidate
            lines.append(line)
        if len(lines) > count:
            raise ValueError("Text exceeds preview region; revise copy")
        return lines

    def _portrait(self, progress: float) -> Image.Image:
        scale = 1 + progress * 0.035
        width, height = round(1080 * scale), round(1520 * scale)
        photo = ImageOps.fit(self.portrait, (width, height), Image.Resampling.LANCZOS)
        photo = photo.crop(((width - 1080) // 2, 0, (width - 1080) // 2 + 1080, 1520))
        canvas = Image.new("RGB", (1080, 1920), INK)
        canvas.paste(photo, (0, 88))
        shade = Image.new("RGBA", canvas.size)
        draw = ImageDraw.Draw(shade)
        for y in range(880, 1670):
            alpha = round(255 * min(1, (y - 880) / 600))
            draw.line((0, y, 1080, y), fill=(20, 43, 39, alpha))
        return Image.alpha_composite(canvas.convert("RGBA"), shade)

    def _cutaway(self, index: int, seconds: float, progress: float) -> Image.Image:
        canvas = Image.new("RGBA", (1080, 1920), INK)
        draw = ImageDraw.Draw(canvas)
        draw.ellipse((680, 100, 1290, 710), outline="#385049", width=2)
        draw.ellipse((730, 150, 1240, 660), outline="#385049", width=2)
        draw.text((74, 153), f"0{index}", font=self.font(130, "bold"), fill=LIME)
        offset = round(12 * math.sin(seconds * 1.3))
        card = Image.new("RGBA", (932, 570))
        cd = ImageDraw.Draw(card)
        cd.rounded_rectangle((0, 0, 931, 569), radius=32, fill=CREAM)
        cd.rounded_rectangle((0, 0, 931, 74), radius=32, fill="#DADFD4")
        cd.rectangle((0, 38, 931, 74), fill="#DADFD4")
        for x in (36, 64, 92):
            cd.ellipse((x, 28, x + 12, 40), fill="#8A998C")
        if index % 3 == 1:
            cd.rounded_rectangle((62, 126, 535, 484), radius=16, outline=INK, width=4)
            for row in range(5):
                end = 475 if row < 4 else 350
                cd.line((103, 176 + row * 55, end, 176 + row * 55), fill="#9BA898", width=12)
            cd.ellipse((553, 165, 807, 419), fill=LIME, outline=INK, width=5)
            cd.ellipse((610, 220, 744, 354), outline=INK, width=12)
            cd.line((725, 338, 780, 397), fill=INK, width=14)
        elif index % 3 == 2:
            cd.rounded_rectangle((153, 130, 780, 500), radius=20, outline=INK, width=5)
            cd.line((156, 210, 775, 210), fill=INK, width=5)
            for row in range(3):
                for column in range(5):
                    x, y = 195 + column * 110, 250 + row * 76
                    color = LIME if (row, column) == (1, 3) else "#DADFD4"
                    cd.rounded_rectangle((x, y, x + 73, y + 48), radius=7, fill=color)
            cd.arc(
                (470, 280, 710, 520), 205, 205 + round(300 * _ease(progress * 2)), fill=INK, width=5
            )
        else:
            for row in range(3):
                y = 132 + row * 125
                cd.rounded_rectangle((73, y, 151, y + 78), radius=16, fill=LIME)
                cd.text((96, y + 8), "?", font=self.font(50, "bold"), fill=INK)
                cd.line((206, y + 24, 825 - row * 60, y + 24), fill=INK, width=10)
                cd.line((206, y + 57, 630, y + 57), fill="#9BA898", width=9)
        canvas.alpha_composite(card, (74, 790 + offset))
        return canvas

    def frame(self, index: int) -> Image.Image:
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < self.frame_count
        ):
            raise IndexError("Frame outside concept timeline")
        relative = index
        scene_index = 0
        for candidate, scene in enumerate(self.spec.scenes):
            scene_index = candidate
            length = scene.duration_seconds * self.spec.fps
            if relative < length:
                break
            relative -= length
        scene = self.spec.scenes[scene_index]
        seconds = relative / self.spec.fps
        progress = relative / max(1, scene.duration_seconds * self.spec.fps - 1)
        portrait = scene.kind in {"PORTRAIT", "CLOSING"}
        canvas = (
            self._portrait(progress) if portrait else self._cutaway(scene_index, seconds, progress)
        )
        draw = ImageDraw.Draw(canvas)
        draw.text(
            (74, 120),
            self.spec.subject_name,
            font=self.font(35, "bold"),
            fill=INK if portrait else CREAM,
        )
        text_layer = Image.new("RGBA", canvas.size)
        td = ImageDraw.Draw(text_layer)
        entrance = _ease(seconds / 0.45)
        y = (1145 if portrait else 375) + round((1 - entrance) * 38)
        layout = self.layouts[scene_index]
        td.text((74, y - 66), layout["kicker"][0], font=self.font(30, "bold"), fill=LIME)
        for line in layout["headline"]:
            td.text((68, y), line, font=self.font(112, "bold"), fill=CREAM, anchor="lt")
            y += 127
        for line_index, line in enumerate(layout["supporting"]):
            td.text((74, 1480 + line_index * 55), line, font=self.font(43), fill=LIME)
        text_layer.putalpha(text_layer.getchannel("A").point(lambda a: round(a * entrance)))
        canvas.alpha_composite(text_layer)
        draw = ImageDraw.Draw(canvas)
        draw.text((74, 1590), SCRIPT_LABEL, font=self.font(22, "bold"), fill=MUTED)
        for line_index, line in enumerate(layout["caption"]):
            draw.text(
                (74, 1634 + line_index * 56),
                line,
                font=self.font(43, "bold"),
                fill=CREAM,
                anchor="lt",
            )
        for beat in range(len(self.spec.scenes)):
            x = 74 + beat * (932 / len(self.spec.scenes))
            end = x + (932 / len(self.spec.scenes)) - 12
            draw.line((x, 1798, end, 1798), fill="#385049", width=5)
            fill = 1 if beat < scene_index else progress if beat == scene_index else 0
            if fill:
                draw.line((x, 1798, x + (end - x) * fill, 1798), fill=LIME, width=5)
        canvas.alpha_composite(self.marks)
        return canvas.convert("RGB")
