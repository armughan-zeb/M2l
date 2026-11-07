"""Web Image Pipeline — modular, parallel, multi-source.

Usage:
    from services.web_image import resolve_web_images_for_video_fast

    result = resolve_web_images_for_video_fast(
        script=script, title=title, scene_texts=scene_texts,
        scene_durations=durations, scene_queries_override=queries,
    )
    paths = result["image_paths"]
"""
from .constants import (
    LOW_CONFIDENCE_DOMAINS,
    REJECT_TEXT_SIGNALS,
    STOPWORDS,
    SUPPORTED_IMAGE_EXTS,
    VISUAL_ENRICHMENTS,
    WATERMARK_SIGNALS,
    # Backward compat aliases
    _LOW_CONFIDENCE_DOMAINS,
    _REJECT_TEXT_SIGNALS,
    _STOPWORDS,
    _SUPPORTED_IMAGE_EXTS,
    _WATERMARK_SIGNALS,
)
from .query import (
    _classify_scene_type,
    _detect_royal_family_anchor,
    _derive_effective_web_title,
    _ensure_query_diversity,
    _extract_main_subject,
    _generate_queries_mechanical,
    generate_web_image_queries,
)
from .resolver import resolve_web_images_for_video_fast
from .runtime import (
    _resolve_gallery_dl_command,
    reset_web_runtime_state,
    resolve_gallery_dl,
)
from .flickr_api_source import search_flickr_api, search_flickr_api_multi
from .source_broker import web_image_source_catalog, web_image_fallback_layers

__all__ = [
    # Main entry point
    "resolve_web_images_for_video_fast",
    # Query generation
    "generate_web_image_queries",
    "_generate_queries_mechanical",
    "_ensure_query_diversity",
    "_derive_effective_web_title",
    "_detect_royal_family_anchor",
    "_extract_main_subject",
    "_classify_scene_type",
    # Runtime
    "reset_web_runtime_state",
    "_resolve_gallery_dl_command",
    "resolve_gallery_dl",
    # Flickr API
    "search_flickr_api",
    "search_flickr_api_multi",
    # Provider map and layered rescue policy
    "web_image_source_catalog",
    "web_image_fallback_layers",
    # Constants
    "LOW_CONFIDENCE_DOMAINS",
    "REJECT_TEXT_SIGNALS",
    "WATERMARK_SIGNALS",
    "STOPWORDS",
    "SUPPORTED_IMAGE_EXTS",
    "VISUAL_ENRICHMENTS",
    "_LOW_CONFIDENCE_DOMAINS",
    "_REJECT_TEXT_SIGNALS",
    "_WATERMARK_SIGNALS",
    "_STOPWORDS",
    "_SUPPORTED_IMAGE_EXTS",
]
