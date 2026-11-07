"""Shared scene-level visual decision contract.

Retrieval and final alignment use the same normalized representation.  Keeping
coercion here prevents small prompt/provider differences from changing gate
semantics and makes decisions safe to persist in media-plan metadata.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

SCENE_DECISION_VERSION = "scene-visual-v1"


_WORD_SCORES = {
    "high": 80.0, "medium": 55.0, "low": 25.0, "none": 0.0,
    "strong": 85.0, "moderate": 60.0, "weak": 20.0,
    "excellent": 95.0, "good": 82.0, "fair": 55.0, "poor": 20.0,
    "very high": 95.0, "very low": 10.0, "not applicable": 0.0,
}


def _bool(value: Any) -> bool:
    return value is True or str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        num = float(value)
        # Model verdicts sometimes come back on a 0..1 fractional scale while
        # the gate semantics are 0..100. Auto-scale fractions so "relevance":
        # 1.0 / confidence 0.98 are not mistaken for 1% / 0.98%.
        if 0.0 <= num <= 1.0:
            num *= 100.0
        return max(0.0, min(100.0, num))
    except Exception:
        label = str(value or "").strip().lower()
        if label in _WORD_SCORES:
            return _WORD_SCORES[label]
        return default


def _area(box: Sequence[float]) -> float:
    if len(box) != 4:
        return 0.0
    x1, y1, x2, y2 = box
    return max(0.0, min(1.0, x2 - x1)) * max(0.0, min(1.0, y2 - y1))


def normalize_bbox(value: Any) -> Optional[List[float]]:
    if isinstance(value, dict):
        value = [value.get(k) for k in ("x1", "y1", "x2", "y2")]
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        values = [max(0.0, min(1.0, float(v))) for v in value]
    except Exception:
        return None
    x1, y1, x2, y2 = values
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


@dataclass
class OverlayRegion:
    type: str = "other"
    bbox: List[float] = field(default_factory=list)
    frame_indices: List[int] = field(default_factory=list)
    area_ratio: float = 0.0
    persistence: float = 0.0
    confidence: float = 0.0
    moving: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SceneVisualDecision:
    version: str = SCENE_DECISION_VERSION
    scene_index: int = -1
    scene_context_hash: str = ""
    asset_fingerprint: str = ""
    media_type: str = "image"
    status: str = "unverified"
    relevance: float = 0.0
    quality: float = 0.0
    confidence: float = 0.0
    fatal_mismatch: bool = False
    rejection_reasons: List[str] = field(default_factory=list)
    overlay_regions: List[OverlayRegion] = field(default_factory=list)
    model: str = ""
    vision_call_id: str = ""
    cache_hit: bool = False
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    source: str = ""
    operation: str = ""
    degraded_reason: str = ""
    candidate_rank: int = -1
    provider_metadata_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["overlay_regions"] = [row.to_dict() for row in self.overlay_regions]
        data["accepted"] = self.status == "accepted"
        data["visual_checked"] = self.status in {"accepted", "rejected", "needs_cleanup"} or self.confidence > 0.0
        data["semantic_verified"] = self.status == "accepted"
        if self.asset_fingerprint and not data.get("verified_asset_sha256"):
            data["verified_asset_sha256"] = self.asset_fingerprint
        if self.scene_context_hash and not data.get("verified_requirement_hash"):
            data["verified_requirement_hash"] = self.scene_context_hash
        return data


def normalize_overlay_regions(payload: Any, frame_count: int = 3) -> List[OverlayRegion]:
    if not isinstance(payload, list):
        return []
    result: List[OverlayRegion] = []
    for raw in payload[:6]:
        if not isinstance(raw, dict):
            continue
        bbox = normalize_bbox(raw.get("bbox") or raw.get("box") or raw.get("region"))
        if not bbox:
            continue
        frames = raw.get("frame_indices") or raw.get("frames") or []
        if not isinstance(frames, list):
            frames = []
        try:
            frames = sorted({max(0, min(max(0, frame_count - 1), int(v))) for v in frames})
        except Exception:
            frames = []
        persistence = _number(raw.get("persistence"), 0.0) / 100.0
        if persistence <= 0 and frames and frame_count:
            persistence = min(1.0, len(frames) / float(frame_count))
        result.append(OverlayRegion(
            type=str(raw.get("type") or raw.get("overlay_type") or "other").strip().lower(),
            bbox=bbox,
            frame_indices=frames,
            area_ratio=round(_area(bbox), 6),
            persistence=round(max(0.0, min(1.0, persistence)), 4),
            confidence=_number(raw.get("confidence")),
            moving=_bool(raw.get("moving")),
        ))
    return result


def normalize_scene_decision(
    payload: Dict[str, Any],
    *,
    scene_index: int = -1,
    media_type: str = "image",
    source: str = "",
    model: str = "",
    asset_fingerprint_value: str = "",
    scene_context_hash_value: str = "",
    cache_hit: bool = False,
    latency_ms: float = 0.0,
    cost_usd: float = 0.0,
    operation: str = "",
    degraded_reason: str = "",
    candidate_rank: int = -1,
    provider_metadata_score: float = 0.0,
) -> SceneVisualDecision:
    payload = payload if isinstance(payload, dict) else {}
    clip = media_type == "video"
    relevance = _number(payload.get("story_relevance" if clip else "relevance"))
    quality = _number(payload.get("visual_quality" if clip else "quality"))
    confidence = _number(payload.get("confidence"))
    fatal = _bool(payload.get("fatal_mismatch"))
    hard_overlay = [
        row for row in normalize_overlay_regions(payload.get("overlay_regions"), frame_count=3)
        if row.type in {"creator_logo", "ownership_watermark", "title_card", "ticker"}
    ]
    watermark = any(_bool(payload.get(key)) for key in (
        "watermark", "ownership_watermark", "watermark_detected", "has_watermark",
    ))
    adult_unsafe = any(_bool(payload.get(key)) for key in (
        "adult", "adult_content", "sexual_content", "nsfw", "unsafe", "is_adult",
    ))
    suggestive = any(_bool(payload.get(key)) for key in (
        "suggestive", "suggestive_content", "sexualized",
    ))
    synthetic = any(_bool(payload.get(key)) for key in (
        "synthetic", "synthetic_content", "is_synthetic", "cgi", "rendered",
    ))
    talking_head = False
    if clip and _bool(payload.get("talking_head")) and str(
        os.environ.get("VIDZEO_YT_REJECT_TALKING_HEAD", "1") or "1"
    ).strip().lower() in {"1", "true", "yes", "on"}:
        try:
            from services.scene_understanding.analyzers import hard_talking_head_signal
            talking_head = hard_talking_head_signal(
                True,
                payload.get("talking_head_confidence", payload.get("confidence", 0.0)),
                payload.get("talking_head_reason", ""),
            )
        except Exception:
            talking_head = _number(payload.get("confidence")) >= 82.0
    text = _bool(payload.get("has_text_overlay" if clip else "has_text")) or _bool(payload.get("subtitle"))
    disqualifying_text = _bool(payload.get("disqualifying_text"))
    reasons: List[str] = []
    if fatal:
        reasons.append("fatal_mismatch")
    if watermark:
        reasons.append("watermark")
    if adult_unsafe:
        reasons.append("adult_content")
        fatal = True
    if suggestive:
        reasons.append("suggestive_content")
        fatal = True
    if synthetic:
        reasons.append("synthetic_content")
        fatal = True
    if talking_head:
        reasons.append("talking_head")
    if text and (clip or disqualifying_text):
        reasons.append("text_overlay")
    if hard_overlay and "overlay" not in reasons:
        reasons.append("overlay")
    if clip:
        min_relevance = float(os.environ.get("VIDZEO_YT_MIN_RELEVANCE", "55") or 55)
        min_quality = float(os.environ.get("VIDZEO_YT_VISUAL_MIN_QUALITY", "30") or 30)
        min_combined = float(os.environ.get("VIDZEO_YT_VISUAL_MIN_SCORE", "45") or 45)
    else:
        min_relevance = float(os.environ.get("VIDZEO_WEB_MIN_RELEVANCE", "55") or 55)
        min_quality = 0.0
        min_combined = 0.0
    combined = (0.70 * relevance + 0.30 * quality) if clip else relevance
    if relevance < min_relevance:
        reasons.append("low_relevance")
    if clip and quality < min_quality:
        reasons.append("low_quality")
    confident_reject = (
        (relevance < 45.0 and confidence >= 60.0)
        or (relevance < 40.0 and confidence >= 40.0)
        or (relevance < 25.0 and confidence >= 20.0)
        or (clip and combined < min_combined and confidence >= 60.0)
    )
    if talking_head or fatal or confident_reject:
        status = "rejected"
    elif reasons and (watermark or hard_overlay or (text and (clip or disqualifying_text))):
        status = "needs_cleanup" if clip else "rejected"
    elif relevance >= min_relevance and confidence >= 50.0 and not reasons:
        status = "accepted"
    else:
        status = "unverified"
    return SceneVisualDecision(
        scene_index=scene_index,
        scene_context_hash=scene_context_hash_value,
        asset_fingerprint=asset_fingerprint_value,
        media_type=media_type,
        status=status,
        relevance=relevance,
        quality=quality,
        confidence=confidence,
        fatal_mismatch=fatal,
        rejection_reasons=sorted(set(reasons)),
        overlay_regions=normalize_overlay_regions(payload.get("overlay_regions"), frame_count=3),
        model=model,
        cache_hit=cache_hit,
        latency_ms=max(0.0, float(latency_ms or 0.0)),
        cost_usd=max(0.0, float(cost_usd or 0.0)),
        source=source,
        operation=operation,
        degraded_reason=degraded_reason,
        candidate_rank=int(candidate_rank if candidate_rank is not None else -1),
        provider_metadata_score=_number(provider_metadata_score),
    )


def asset_fingerprint(path: str) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return hashlib.sha256(str(path or "").encode("utf-8", "replace")).hexdigest()


def scene_context_hash(*, narration: str, subject: str, topic: str, media_type: str, negatives: Iterable[str] = ()) -> str:
    payload = {
        "narration": str(narration or "").strip(),
        "subject": str(subject or "").strip(),
        "topic": str(topic or "").strip(),
        "media_type": str(media_type or "").strip(),
        "negatives": sorted(str(value or "").strip().lower() for value in negatives if str(value or "").strip()),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
