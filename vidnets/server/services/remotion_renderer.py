"""Backward-compatible import shim for the quality-aware render policy.

Documentary exports use the Remotion composition engine. FFmpeg remains
available for explicit native-only operations and post-render media handling.
"""

from .native_render_policy import (
    assert_semantic_edit_quality,
    native_render_enabled,
    procedural_sound_library,
    render_semantic_documentary,
    semantic_render_enabled,
    semantic_render_required,
    validate_native_backend,
)

__all__ = [
    "assert_semantic_edit_quality",
    "native_render_enabled",
    "procedural_sound_library",
    "render_semantic_documentary",
    "semantic_render_enabled",
    "semantic_render_required",
    "validate_native_backend",
]
