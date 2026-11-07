"""Retired template animation cache.

Production templates are rendered by native Pillow/FFmpeg code. This module is
kept as a no-op compatibility surface for old design scripts and never starts
Node or a browser renderer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
FRAMES_DIR = ROOT / "public" / "template-animations"


class TemplateAnimationCache:
    """Manages pre-rendered template frame sequences."""

    def __init__(self, scale: float = 1.0):
        self.scale = max(0.5, min(1.0, scale))
        FRAMES_DIR.mkdir(parents=True, exist_ok=True)

    def ensure(
        self,
        template_id: str,
        props: Dict[str, Any],
        duration: float,
        variant: int = 0,
        theme: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Return empty: native rendering replaces animated frame caching."""
        return ""

    def preload_common(self, template_ids: list[str], duration: float = 2.0, variants: int = 3):
        """Pre-render a batch of templates with default props."""
        from .template_preview import TEMPLATE_PREVIEWS

        for tid in template_ids:
            if tid not in TEMPLATE_PREVIEWS:
                continue
            props = TEMPLATE_PREVIEWS.get(tid, {})
            for v in range(variants):
                self.ensure(tid, props, duration, variant=v)


# Singleton
_cache: Optional[TemplateAnimationCache] = None


def get_template_cache(scale: float = 1.0) -> TemplateAnimationCache:
    global _cache
    if _cache is None:
        _cache = TemplateAnimationCache(scale=scale)
    return _cache
