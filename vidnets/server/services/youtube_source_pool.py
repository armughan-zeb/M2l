"""Source-level records and deduplication for pool-first YouTube retrieval."""
from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, urlparse
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping


_POOL_PRESENTER_TERMS = (
    "interview", "podcast", "reaction", "reacts", "watch along",
    "talk show", "talkshow", "panel discussion", "studio host",
    "presenter", "news anchor", "commentary", "commentator",
    "analysis", "breakdown", "explained", "what really happened",
    "debate", "livestream", "live stream", "facecam", "face cam",
    "streamer", "press conference", "q&a", "question and answer",
    # Gaming benchmark/review uploads routinely carry an always-on creator
    # camera and giant title strips. They are not raw gameplay captures for a
    # documentary pool, so let the frame gate spend its budget elsewhere.
    "benchmark", "pc benchmark", "gameplay review", "review gameplay",
)

_POOL_NARRATIVE_PACKAGE_TERMS = (
    "biography", "bio of", "life story", "lifestyle", "top facts",
    "facts about", "who is", "story of", "history of",
)

_POOL_FOOTAGE_TERMS = (
    "highlights", "goals", "skills", "match footage", "game footage",
    "training footage", "raw footage", "archive footage", "archival footage",
    "official footage", "full match", "live match", "warm up", "warm-up",
    "b-roll", "b roll", "best moments", "all goals", "compilation",
)

_POOL_REJECT_SOURCE_TERMS = (
    "with subtitles", "with captions", "english subtitles", "english captions",
    "hardcoded subtitles", "hard-coded subtitles", "burned in subtitles",
    "burned-in subtitles", "burnt subtitles", "closed captions",
    "captioned video", "captioned footage", "on screen text", "on-screen text",
    "text overlay", "lyrics video", "lyric video", "karaoke", "slideshow",
    "slide show", "photo slideshow", "image slideshow", "photo montage",
    "picture montage", "still images", "static images", "photo essay",
    "presentation slides", "image compilation", "photo compilation", "text card",
    "text cards", "powerpoint",
    "archive title card", "timeline graphic", "timeline slideshow",
    "chapter card", "chapter title card", "benchmark overlay",
)


def _source_text(candidate: Mapping[str, Any]) -> str:
    return " ".join(
        str(candidate.get(key) or "")
        for key in ("title", "description", "channel", "uploader")
    ).casefold()


def _has_rejected_source_text(candidate: Mapping[str, Any]) -> bool:
    return any(term in _source_text(candidate) for term in _POOL_REJECT_SOURCE_TERMS)


def _is_pool_visual_candidate(candidate: Mapping[str, Any]) -> bool:
    """Keep source-pool downloads biased toward footage, not presenters.

    Pool-first pays the download cost once and then cuts many moments from a
    source. A presenter package that would fail every local overlay screen is
    therefore much more expensive than a normal ranged-candidate miss. Filter
    explicit metadata rejects and obvious presenter packages before download;
    the frame-level overlay policy remains authoritative for the surviving
    footage.
    """
    reason = str(candidate.get("v2_reject_reason") or "").strip().lower()
    hard_reasons = (
        # These metadata labels almost always identify a presenter-led source
        # and are safe to keep out of a shared pool. Less certain labels such
        # as package_without_visual_signal and copyright_high_risk remain
        # probeable: metadata should rank candidates, while frames decide.
        "streamer", "reaction", "talking_head", "talking head",
    )
    if any(token in reason for token in hard_reasons):
        return False
    text = _source_text(candidate)
    # Reject packages whose entire source is likely to carry publisher
    # captions or to be a slideshow. The renderer only sees local cuts after
    # this point, so waiting for frame-level screening would waste the shared
    # download and can leave the scene with a misleading still.
    if _has_rejected_source_text(candidate):
        return False
    # “Benchmark” uploads are commonly presenter-led reviews with a permanent
    # webcam and a large comparison banner (the exact leak the local frame
    # gate is meant to prevent). Keep a source only when its metadata clearly
    # promises a clean capture rather than a review package.
    if "benchmark" in text and not any(term in text for term in (
        "raw gameplay", "no commentary", "without commentary",
        "direct gameplay capture", "gameplay only",
    )):
        return False
    # A biography/documentary title alone cannot establish presenter or
    # caption contamination. Such sources may contain clean archival moments;
    # their sampled frames and individual cuts must decide admission.
    # Raw/official/match footage signals override a generic word such as
    # "commentary" in a description, but not an explicit interview/podcast.
    footage_signal = any(term in text for term in (
        "match footage", "game footage", "gameplay", "highlights",
        "best goals", "goals and skills", "training footage", "raw footage",
        "archive footage", "archival footage", "official footage",
        "full match", "live match", "stadium", "warm up", "warm-up",
    ))
    explicit_presenter = any(term in text for term in _POOL_PRESENTER_TERMS)
    return not explicit_presenter or (footage_signal and not any(
        term in text for term in ("interview", "podcast", "reaction", "talk show", "facecam")
    ))


@dataclass
class YouTubeSource:
    source_id: str
    video_id: str
    url: str
    title: str = ""
    channel: str = ""
    duration: float = 0.0
    metadata_score: float = 0.0
    pool_keys: List[str] = field(default_factory=list)
    transcript_status: str = "pending"

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "video_id": self.video_id,
            "url": self.url,
            "title": self.title,
            "channel": self.channel,
            "duration": self.duration,
            "metadata_score": self.metadata_score,
            "pool_keys": list(self.pool_keys),
            "transcript_status": self.transcript_status,
        }


def normalize_source_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"[A-Za-z0-9_-]{6,}", raw):
        return raw
    try:
        parsed = urlparse(raw)
        host = parsed.netloc.lower()
        if host in {"youtu.be", "www.youtu.be"}:
            return parsed.path.strip("/").split("/")[0]
        if "youtube.com" in host:
            query_id = parse_qs(parsed.query).get("v", [""])[0]
            if query_id:
                return query_id
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
                return parts[1]
    except Exception:
        pass
    return raw


def source_id(candidate: Mapping[str, Any]) -> str:
    for value in (
        candidate.get("id"),
        candidate.get("video_id"),
        candidate.get("webpage_url"),
        candidate.get("url"),
    ):
        normalized = normalize_source_id(value)
        if normalized:
            return normalized
    return ""


def build_sources(
    pools: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    per_pool: int | None = None,
) -> Dict[str, YouTubeSource]:
    # Keep a reserve of metadata candidates available for the renderer's
    # motion/overlay gate. A five-item pool was too shallow: one slideshow,
    # one AI reconstruction, and one presenter package could consume the
    # entire download budget before a real moving-footage candidate appeared.
    if per_pool is None:
        try:
            per_pool = int(os.environ.get("VIDZEO_YT_SOURCE_RECORDS_PER_POOL", "24") or 24)
        except (TypeError, ValueError):
            per_pool = 24
    per_pool = max(1, min(24, int(per_pool)))
    sources: Dict[str, YouTubeSource] = {}
    for pool_key, candidates in pools.items():
        raw_candidates = list(candidates or [])
        eligible = [candidate for candidate in raw_candidates if _is_pool_visual_candidate(candidate)]
        # The source pool is the probe universe, not the final editorial
        # decision. Keep metadata-ambiguous candidates available for the cheap
        # low-resolution probe. Only explicit burned-in-text/slideshow markers
        # are hard filtered; presenter/package and copyright heuristics are
        # useful ranking signals but are not reliable enough to discard footage
        # before a frame is inspected.
        if len(eligible) < min(len(raw_candidates), 8):
            eligible = [candidate for candidate in raw_candidates if not _has_rejected_source_text(candidate)]
        # Sparse metadata can still be screened at the frame level, but an
        # explicit subtitle/slideshow marker is never allowed back through
        # this compatibility path. If every candidate has such a marker, keep
        # the pool empty and let the caller record a hard source shortfall.
        ranked = sorted(
            eligible,
            key=lambda candidate: float(candidate.get("v2_score") or candidate.get("score") or 0.0),
            reverse=True,
        )
        for candidate in ranked[:per_pool]:
            sid = source_id(candidate)
            if not sid:
                continue
            existing = sources.get(sid)
            if existing is None:
                existing = YouTubeSource(
                    source_id=sid,
                    video_id=normalize_source_id(candidate.get("id") or candidate.get("video_id") or sid),
                    url=str(candidate.get("webpage_url") or candidate.get("url") or ""),
                    title=str(candidate.get("title") or ""),
                    channel=str(candidate.get("channel") or candidate.get("uploader") or ""),
                    duration=float(candidate.get("duration") or 0.0),
                    metadata_score=float(candidate.get("v2_score") or candidate.get("score") or 0.0),
                )
                sources[sid] = existing
            if pool_key not in existing.pool_keys:
                existing.pool_keys.append(str(pool_key))
    return sources
