"""Compatibility facade for the compact YouTube pipeline v2.

The previous implementation in this file had grown to ~18k lines and contained
multiple competing resolver/ranker/download paths. The active YouTube clip
pipeline now lives in `youtube_pipeline_v2.py`.
"""

from services.youtube_pipeline_v2 import (  # noqa: F401
    QueryPack,
    SceneRetrievalContract,
    VideoFormat,
    YOUTUBE_ALLOW_SAME_SOURCE_REUSE,
    _SOURCE_CLASS_COMEDY,
    _SCENE_FAMILY_PERSON,
    _SCENE_FAMILY_PLACE,
    _calculate_scene_text_overlap,
    _detect_video_format,
    _generate_queries_mechanical,
    _is_listicle_video,
    _get_authenticated_youtube_session,
    _search_container_pools,
    _search_youtube,
    _scene_subject_terms,
    _subject_is_weak,
    get_youtube_clips,
    parse_script_to_scenes,
    select_best_youtube_candidate,
    select_youtube_scene_matches,
)


__all__ = [
    "QueryPack",
    "SceneRetrievalContract",
    "VideoFormat",
    "YOUTUBE_ALLOW_SAME_SOURCE_REUSE",
    "_SOURCE_CLASS_COMEDY",
    "_SCENE_FAMILY_PERSON",
    "_SCENE_FAMILY_PLACE",
    "_calculate_scene_text_overlap",
    "_detect_video_format",
    "_generate_queries_mechanical",
    "_is_listicle_video",
    "_get_authenticated_youtube_session",
    "_search_container_pools",
    "_search_youtube",
    "_scene_subject_terms",
    "_subject_is_weak",
    "get_youtube_clips",
    "parse_script_to_scenes",
    "select_best_youtube_candidate",
    "select_youtube_scene_matches",
]
