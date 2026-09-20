"""Deterministic visual artifacts; publication and tenant authorization live upstream."""

from app.rendering.renderer import render_carousel
from app.rendering.schemas import RenderManifest, VisualConfig

__all__ = ["RenderManifest", "VisualConfig", "render_carousel"]
