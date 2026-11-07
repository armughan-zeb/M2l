"""Data models for the Visual Intelligence Pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class AssetType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


@dataclass
class ImageAnalysis:
    """Qwen Vision analysis result for a single image candidate."""
    path: str
    scene_index: int = -1
    source_hint: str = ""
    # Layer 1 — cheap CPU pre-filter
    width: int = 0
    height: int = 0
    phash: Optional[int] = None
    prefilter_rejected: bool = False
    prefilter_reason: str = ""
    # Layer 2 — Qwen vision scores (0-100 each)
    relevance: float = 0.0
    quality: float = 0.0
    historical_match: float = 0.0
    composition: float = 0.0
    watermark: bool = False
    has_text: bool = False
    unsafe_content: bool = False
    suggestive_content: bool = False
    synthetic_content: bool = False
    overlay_type: str = "none"
    overlay_regions: List[Dict[str, Any]] = field(default_factory=list)
    fatal_mismatch: bool = False
    confidence: float = 0.0
    # Layer 3 — derived
    final_score: float = 0.0
    reason: str = ""
    scene_description: str = ""
    motion_effect: str = ""
    text_safe_area: str = ""
    vision_available: bool = False
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "scene_index": self.scene_index,
            "source_hint": self.source_hint,
            "width": self.width,
            "height": self.height,
            "prefilter_rejected": self.prefilter_rejected,
            "prefilter_reason": self.prefilter_reason,
            "relevance": round(self.relevance, 2),
            "quality": round(self.quality, 2),
            "historical_match": round(self.historical_match, 2),
            "composition": round(self.composition, 2),
            "watermark": self.watermark,
            "has_text": self.has_text,
            "unsafe_content": self.unsafe_content,
            "suggestive_content": self.suggestive_content,
            "synthetic_content": self.synthetic_content,
            "overlay_type": self.overlay_type,
            "overlay_regions": list(self.overlay_regions or []),
            "fatal_mismatch": self.fatal_mismatch,
            "confidence": round(self.confidence, 2),
            "final_score": round(self.final_score, 2),
            "reason": self.reason,
            "scene_description": self.scene_description,
            "motion_effect": self.motion_effect,
            "text_safe_area": self.text_safe_area,
            "vision_available": self.vision_available,
            "error": self.error,
        }


@dataclass
class ClipAnalysis:
    """Qwen Vision analysis result for a single video clip (sequence of frames)."""
    path: str
    scene_index: int = -1
    source_hint: str = ""
    # Layer 1 — cheap CPU pre-filter
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    phash: Optional[int] = None
    prefilter_rejected: bool = False
    prefilter_reason: str = ""
    # Layer 2 — Qwen vision scores (0-100 each)
    visual_quality: float = 0.0
    documentary_value: float = 0.0
    emotional_score: float = 0.0
    cinematic_score: float = 0.0
    story_relevance: float = 0.0
    watermark: bool = False
    subtitle: bool = False
    has_text_overlay: bool = False
    talking_head: bool = False
    overlay_type: str = "none"
    overlay_regions: List[Dict[str, Any]] = field(default_factory=list)
    fatal_mismatch: bool = False
    confidence: float = 0.0
    # Layer 3 — advanced intelligence
    scene_description: str = ""
    category: str = ""
    subjects: List[str] = field(default_factory=list)
    shot_type: str = ""
    best_usage: str = ""
    recommended_duration: str = ""
    transition: str = ""
    caption_position: str = ""
    music: str = ""
    camera_movement: str = ""
    # Derived
    final_score: float = 0.0
    vision_available: bool = False
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "scene_index": self.scene_index,
            "source_hint": self.source_hint,
            "duration": round(self.duration, 3),
            "width": self.width,
            "height": self.height,
            "fps": round(self.fps, 2),
            "prefilter_rejected": self.prefilter_rejected,
            "prefilter_reason": self.prefilter_reason,
            "visual_quality": round(self.visual_quality, 2),
            "documentary_value": round(self.documentary_value, 2),
            "emotional_score": round(self.emotional_score, 2),
            "cinematic_score": round(self.cinematic_score, 2),
            "story_relevance": round(self.story_relevance, 2),
            "watermark": self.watermark,
            "subtitle": self.subtitle,
            "has_text_overlay": self.has_text_overlay,
            "talking_head": self.talking_head,
            "overlay_type": self.overlay_type,
            "overlay_regions": list(self.overlay_regions or []),
            "fatal_mismatch": self.fatal_mismatch,
            "confidence": round(self.confidence, 2),
            "scene_description": self.scene_description,
            "category": self.category,
            "subjects": list(self.subjects),
            "shot_type": self.shot_type,
            "best_usage": self.best_usage,
            "recommended_duration": self.recommended_duration,
            "transition": self.transition,
            "caption_position": self.caption_position,
            "music": self.music,
            "camera_movement": self.camera_movement,
            "final_score": round(self.final_score, 2),
            "vision_available": self.vision_available,
            "error": self.error,
        }


@dataclass
class RankedAsset:
    """A media asset ranked by the universal ranking engine."""
    asset_id: str
    type: AssetType
    path: str
    scene_index: int
    topic: str
    score: float
    subjects: List[str] = field(default_factory=list)
    usage: str = ""
    analysis: Optional[Any] = None  # ImageAnalysis | ClipAnalysis

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "type": self.type.value,
            "path": self.path,
            "scene_index": self.scene_index,
            "topic": self.topic,
            "score": round(self.score, 2),
            "subjects": list(self.subjects),
            "usage": self.usage,
            "analysis": self.analysis.to_dict() if self.analysis and hasattr(self.analysis, "to_dict") else None,
        }
