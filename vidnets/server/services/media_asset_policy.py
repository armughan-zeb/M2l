"""Shared policy for rejecting synthetic media at the render boundary.

The retrieval pipeline may create a neutral/emergency plate while it is
recovering from a provider failure. Those files are useful for diagnostics,
but they are not editorial media and must never silently become a scene in a
production export. A separately labelled ``Grounded Graphic Fallback`` is an
intentional, nonblank last resort for Mixed/global jobs and is therefore not
classified as an accidental placeholder. Keeping the predicate here makes the
legacy, V2 and native renderers agree on what is a placeholder.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def excess_media_reuse_indices(media_plan):
    """Find copied stills/cuts exceeding production diversity, using real bytes."""
    import hashlib
    paths = list(media_plan.get('media_paths') or [])
    rows = list((media_plan.get('metadata') or {}).get('media_resolution_v2') or [])
    cache, uses, rejected = {}, {}, []
    previous = ''
    for index, path in enumerate(paths):
        row = rows[index] if index < len(rows) and isinstance(rows[index], dict) else {}
        provider = str(row.get('provider') or row.get('source') or '').casefold()
        youtube = provider in {'youtube', 'youtube clips', 'yt'}
        web = provider in {'web', 'web images', 'borrowed'} or provider.startswith('universal_')
        if not (youtube or web) or not path or is_synthetic_media(path):
            previous = ''
            continue
        key = os.path.normcase(os.path.abspath(path))
        try:
            if key not in cache:
                digest = hashlib.sha256()
                with open(path, 'rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(block)
                cache[key] = digest.hexdigest()
        except OSError:
            previous = ''
            continue
        identity = cache[key]
        cap = 1 if youtube and row.get('media_type') != 'image' else 2
        if uses.get(identity, 0) >= cap or identity == previous:
            rejected.append(index)
            previous = ''
            continue
        uses[identity] = uses.get(identity, 0) + 1
        previous = identity
    return rejected


_SYNTHETIC_LABEL_MARKERS = (
    "emergency visual",
    "neutral no-claim",
    "generated visual",
    "synthetic visual",
    "placeholder visual",
    "remotion graphic fallback",
)
_SYNTHETIC_PATH_MARKERS = (
    "/render_media/emergency/",
    "\\render_media\\emergency\\",
    "/placeholders/",
    "\\placeholders\\",
    "/placeholder/",
    "\\placeholder\\",
)


def is_synthetic_media(path: Any, source_label: Any = "") -> bool:
    """Return ``True`` for generated emergency/placeholder scene assets.

    A normal file named ``text_card`` is intentionally *not* rejected: real
    editorial graphic cards can be part of a timeline.  We only reject the
    known generated locations/names and explicit neutral/emergency labels.
    """

    raw_path = str(path or "").strip()
    label = str(source_label or "").strip().casefold()
    if any(marker in label for marker in _SYNTHETIC_LABEL_MARKERS):
        return True
    if not raw_path:
        return False

    normalized = raw_path.replace("\\", "/").casefold()
    if any(marker.replace("\\", "/") in normalized for marker in _SYNTHETIC_PATH_MARKERS):
        return True

    name = Path(normalized).name
    # These prefixes are emitted only by the native fallback code.  Restrict
    # bg_ to PNG because user-provided photographs commonly use bg_*.jpg.
    if name.startswith("emergency_render_") or name.startswith("placeholder_") or name.startswith("graphic_fallback_"):
        return True
    if name.startswith("bg_") and name.endswith(".png"):
        return True
    return False


def grounded_graphics_allowed(config: Any = None) -> bool:
    """Intentional scene-bound graphics are independent of placeholder permission."""
    config = config if isinstance(config, dict) else {}
    def enabled(key, default):
        value = config.get(key, default)
        if value is None:
            return bool(default)
        return value if isinstance(value, bool) else str(value).strip().lower() in {'1', 'true', 'yes', 'on'}
    return (enabled('global_media_guarantee', True)
            and config.get('media_fallback_policy', 'grounded_graphic') == 'grounded_graphic'
            and (enabled('allow_grounded_graphic_fallback', True)
                 or enabled('allow_grounded_graphic_fallback_strict', True)))


def emergency_media_allowed(config: Any = None, *, default: bool = True) -> bool:
    """Read the explicit escape hatch for diagnostic placeholder renders.

    Production jobs set ``allow_emergency_media`` to ``False``.  The default
    remains configurable for backwards-compatible unit/integration callers;
    no user-facing pipeline relies on the implicit default.
    """

    if isinstance(config, dict) and "allow_emergency_media" in config:
        value = config.get("allow_emergency_media")
    else:
        value = os.environ.get("VIDZEO_ALLOW_EMERGENCY_MEDIA")
        if value is None:
            return bool(default)
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}
