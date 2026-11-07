"""Deterministic scene-to-transcript moment matching for pooled YouTube sources."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .youtube_transcript_index import TranscriptDocument, TranscriptSegment, normalize_text


@dataclass(frozen=True)
class YouTubeMoment:
    source_id: str
    video_id: str
    start: float
    end: float
    transcript_text: str
    match_score: float
    scene_index: int
    match_reason: str
    heuristic: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "video_id": self.video_id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "transcript_text": self.transcript_text,
            "match_score": round(self.match_score, 3),
            "scene_index": self.scene_index,
            "match_reason": self.match_reason,
            "heuristic": self.heuristic,
        }


def _tokens(value: Any) -> set[str]:
    return {
        token for token in re.findall(r"[^\W_][\w'\-]*", normalize_text(value), flags=re.UNICODE)
        if len(token) > 2
    }


def _terms(values: Any) -> List[str]:
    if isinstance(values, str):
        values = re.split(r"[,;|]+", values)
    if not isinstance(values, (list, tuple, set)):
        return []
    return [normalize_text(value) for value in values if normalize_text(value)]


def _contains_phrase(text: str, phrase: str) -> bool:
    return bool(phrase and phrase in text)


def _score_segment(
    segment: TranscriptSegment,
    *,
    subject: str,
    scene_text: str,
    visual_facet: str,
    positive_terms: Sequence[str],
    negative_terms: Sequence[str],
    source_confidence: float,
    duration_needed: float,
) -> tuple[float, str]:
    text = segment.normalized_text
    segment_tokens = _tokens(text)
    subject_text = normalize_text(subject)
    subject_tokens = _tokens(subject_text)
    scene_tokens = _tokens(scene_text)
    facet_tokens = _tokens(visual_facet)
    positive = [term for term in positive_terms if _contains_phrase(text, term) or _tokens(term) & segment_tokens]
    negative = [term for term in negative_terms if _contains_phrase(text, term) or _tokens(term) & segment_tokens]
    exact_subject = 1.0 if subject_text and _contains_phrase(text, subject_text) else 0.0
    subject_overlap = len(subject_tokens & segment_tokens) / max(1, len(subject_tokens))
    scene_overlap = len(scene_tokens & segment_tokens) / max(1, min(len(scene_tokens), 8))
    facet_overlap = len(facet_tokens & segment_tokens) / max(1, len(facet_tokens))
    duration_fit = min(1.0, max(0.0, segment.end - segment.start) / max(1.0, duration_needed))
    confidence = max(0.0, min(1.0, segment.confidence or 0.0))
    score = (
        exact_subject * 45.0
        + subject_overlap * 25.0
        + scene_overlap * 12.0
        + facet_overlap * 12.0
        + min(10.0, len(positive) * 3.0)
        + duration_fit * 4.0
        + confidence * 4.0
        + max(0.0, min(1.0, source_confidence)) * 3.0
        - min(50.0, len(negative) * 18.0)
    )
    reasons = []
    if exact_subject:
        reasons.append("exact_subject")
    if subject_overlap:
        reasons.append("subject_overlap")
    if scene_overlap:
        reasons.append("scene_overlap")
    if facet_overlap:
        reasons.append("visual_facet")
    if positive:
        reasons.append("positive_terms")
    if negative:
        reasons.append("negative_terms")
    return score, "+".join(reasons) or "weak_overlap"


def match_scene_moments(
    document: TranscriptDocument,
    *,
    scene_index: int,
    subject: str,
    scene_text: str,
    visual_facet: str = "",
    positive_terms: Iterable[str] = (),
    negative_terms: Iterable[str] = (),
    duration_needed: float = 4.0,
    source_id: str = "",
    video_id: str = "",
    used_ranges: Iterable[tuple[float, float]] = (),
    limit: int = 8,
    minimum_score: Optional[float] = None,
) -> List[YouTubeMoment]:
    if not document.available:
        return []
    positive = _terms(list(positive_terms))
    negative = _terms(list(negative_terms))
    candidates: List[YouTubeMoment] = []
    segments = list(document.segments or [])
    max_window_span = max(8.0, min(14.0, float(duration_needed or 4.0) + 8.0))
    windows: List[TranscriptSegment] = []
    seen_windows = set()
    for start_index, first in enumerate(segments):
        combined = [first.text]
        window_end = first.end
        for end_index in range(start_index, min(len(segments), start_index + 4)):
            if end_index > start_index:
                next_segment = segments[end_index]
                if next_segment.start - window_end > 3.0:
                    break
                combined.append(next_segment.text)
                window_end = max(window_end, next_segment.end)
            if window_end - first.start > max_window_span:
                break
            text = " ".join(combined).strip()
            key = (round(first.start, 3), round(window_end, 3), text)
            if key in seen_windows:
                continue
            seen_windows.add(key)
            windows.append(TranscriptSegment(
                start=first.start,
                end=window_end,
                text=text,
                normalized_text=normalize_text(text),
                language=first.language,
                source=first.source,
                confidence=max(
                    segment.confidence for segment in segments[start_index:end_index + 1]
                ),
            ))
    if minimum_score is None:
        try:
            minimum_score = float(os.environ.get("VIDZEO_YT_TRANSCRIPT_MIN_SCORE", "35") or 35)
        except (TypeError, ValueError):
            minimum_score = 35.0
    minimum_score = max(0.0, float(minimum_score))
    for segment in windows:
        score, reason = _score_segment(
            segment,
            subject=subject,
            scene_text=scene_text,
            visual_facet=visual_facet,
            positive_terms=positive,
            negative_terms=negative,
            source_confidence=1.0,
            duration_needed=duration_needed,
        )
        # A merely positive lexical score is not evidence for a usable clip.
        if score < minimum_score:
            continue
        start = segment.start
        end = max(segment.end, start + max(1.0, duration_needed))
        if any(abs(start - old_start) < max(18.0, duration_needed + 8.0) for old_start, _old_end in used_ranges):
            continue
        candidates.append(YouTubeMoment(
            source_id=source_id or video_id,
            video_id=video_id,
            start=start,
            end=end,
            transcript_text=segment.text,
            match_score=score,
            scene_index=scene_index,
            match_reason=reason,
        ))
    candidates.sort(key=lambda moment: (-moment.match_score, moment.start, moment.end))
    return candidates[:max(1, int(limit or 8))]


def match_pool_moments(
    documents: Mapping[str, TranscriptDocument],
    scenes: Sequence[Mapping[str, Any]],
    *,
    source_metadata: Optional[Mapping[str, Mapping[str, Any]]] = None,
    used_ranges: Optional[Dict[str, List[tuple[float, float]]]] = None,
    limit_per_scene: int = 8,
) -> Dict[int, List[YouTubeMoment]]:
    metadata = source_metadata or {}
    ranges = used_ranges if used_ranges is not None else {}
    results: Dict[int, List[YouTubeMoment]] = {}
    for scene_index, scene in enumerate(scenes):
        scene_results: List[YouTubeMoment] = []
        scene_pool_keys = {
            normalize_text(value)
            for value in (
                scene.get("pool_key"),
                scene.get("container_id"),
                scene.get("container_key"),
            )
            if normalize_text(value)
        }
        for source_id, document in documents.items():
            source = metadata.get(source_id, {})
            source_pool_keys = {
                normalize_text(value)
                for value in (source.get("pool_keys") or source.get("container_ids") or [])
                if normalize_text(value)
            }
            # Pool/container evidence is authoritative whenever both sides
            # provide it. Legacy metadata without keys remains compatible.
            if scene_pool_keys and source_pool_keys and scene_pool_keys.isdisjoint(source_pool_keys):
                continue
            scene_results.extend(match_scene_moments(
                document,
                scene_index=int(scene.get("scene_index", scene_index)),
                subject=str(scene.get("subject") or scene.get("candidate_subject") or ""),
                scene_text=str(scene.get("scene_text") or scene.get("text") or ""),
                visual_facet=str(scene.get("visual_facet") or ""),
                positive_terms=scene.get("retrieval_positive_terms") or (),
                negative_terms=scene.get("retrieval_negative_terms") or (),
                duration_needed=float(scene.get("duration") or scene.get("duration_needed") or 4.0),
                source_id=source_id,
                video_id=str(source.get("video_id") or source_id),
                used_ranges=ranges.get(source_id, []),
                limit=limit_per_scene,
                minimum_score=scene.get("minimum_transcript_score"),
            ))
        scene_results.sort(key=lambda moment: (-moment.match_score, moment.source_id, moment.start))
        if scene_results:
            winner = scene_results[0]
            ranges.setdefault(winner.source_id, []).append((winner.start, winner.end))
        results[int(scene.get("scene_index", scene_index))] = scene_results[:limit_per_scene]
    return results


def heuristic_moment(
    *,
    source_id: str,
    video_id: str,
    scene_index: int,
    start: float,
    duration: float,
    reason: str = "no_transcript_match",
) -> YouTubeMoment:
    return YouTubeMoment(
        source_id=source_id,
        video_id=video_id,
        start=max(0.0, float(start)),
        end=max(0.0, float(start)) + max(0.1, float(duration)),
        transcript_text="",
        match_score=0.0,
        scene_index=scene_index,
        match_reason=reason,
        heuristic=True,
    )
