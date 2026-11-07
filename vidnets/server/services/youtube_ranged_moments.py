"""Bounded temporal evidence for the existing ranged YouTube downloader."""
from __future__ import annotations

import time
import re
from collections.abc import Mapping


def prepare_ranged_moments(rows, assignments, pools, durations, *, session_jar=None,
                          deadline_seconds=20.0, cancellation=None, index=None):
    from .youtube_transcript_index import YouTubeTranscriptIndex
    from .youtube_moment_matcher import match_scene_moments
    started = time.monotonic()
    if cancellation is not None:
        cancellation.raise_if_cancelled()
    # A primary source per scene receives a chance before reserve masters.
    # Do not fetch transcripts for the entire search-result universe.
    selected = {}
    scene_sources = {}
    for scene_index, candidate in enumerate(assignments):
        if not isinstance(candidate, Mapping):
            continue
        video_id = str(candidate.get('id') or candidate.get('video_id') or '')
        if re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            selected.setdefault(video_id, candidate)
            scene_sources[scene_index] = [video_id]
    source_cap = min(32, max(4, len(selected) + 4))
    if len(selected) > source_cap:
        keys = list(selected)
        positions = {round(i * (len(keys) - 1) / (source_cap - 1)) for i in range(source_cap)}
        selected = {key: selected[key] for i, key in enumerate(keys) if i in positions}
        scene_sources = {scene: [key for key in keys if key in selected]
                         for scene, keys in scene_sources.items()}
    for scene_index, row in enumerate(rows):
        candidate = assignments[scene_index] if scene_index < len(assignments) else None
        if not candidate:
            continue
        pool_key = str(row.get('pool_key') or row.get('container_id') or row.get('section_subject') or '')
        pool = pools.get(pool_key) or []
        for reserve in pool:
            video_id = str(reserve.get('id') or reserve.get('video_id') or '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id) or video_id in scene_sources.get(scene_index, []):
                continue
            if video_id not in selected and len(selected) >= source_cap:
                continue
            # The caller's pool already carries ranking, but explicit metadata
            # rejections cannot be revived by a transcript term match.
            if reserve.get('v2_reject_reason'):
                continue
            selected.setdefault(video_id, reserve)
            scene_sources.setdefault(scene_index, []).append(video_id)
            break
    if not selected or deadline_seconds <= 0:
        return {}, {'status': 'skipped', 'sources_requested': 0}
    index = index or YouTubeTranscriptIndex(session_jar=session_jar)
    documents = index.fetch_many(list(selected), workers=3,
                                 deadline_seconds=max(.05, min(25., float(deadline_seconds))))
    if cancellation is not None:
        cancellation.raise_if_cancelled()
    moments = {}
    for scene_index, row in enumerate(rows):
        matches = []
        subject = str(row.get('candidate_subject') or row.get('section_subject') or row.get('scene_subject') or '')
        for video_id in scene_sources.get(scene_index, []):
            document = documents.get(video_id)
            if document is None:
                continue
            matches.extend(match_scene_moments(document, scene_index=scene_index,
                subject=subject, scene_text=str(row.get('scene_text') or ''),
                visual_facet=str(row.get('visual_facet') or row.get('visual_intent') or ''),
                positive_terms=row.get('retrieval_positive_terms') or (),
                negative_terms=row.get('retrieval_negative_terms') or (),
                duration_needed=float(durations[scene_index] if scene_index < len(durations) else 4),
                source_id=video_id, video_id=video_id, limit=3))
        if matches:
            moments[scene_index] = sorted((m.to_dict() for m in matches),
                key=lambda m: (-m['match_score'], m['source_id'], m['start']))[:6]
    return moments, {'status': 'completed', 'sources_requested': len(selected),
        'sources_returned': len(documents),
        'sources_available': sum(document.available for document in documents.values()),
        'scenes_with_moments': len(moments),
        'source_statuses': {source: (documents[source].status if source in documents else 'deadline_or_unavailable')
                            for source in selected},
        'elapsed_seconds': round(time.monotonic() - started, 3)}
