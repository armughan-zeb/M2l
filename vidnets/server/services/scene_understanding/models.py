"""Data models for the LAVIP scene-understanding pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ClipScore:
    """Weighted ranking score for a single clip."""
    quality: float = 0.0          # 0-100, blur+brightness+black-frame
    cleanliness: float = 0.0     # 0-100, absence of watermarks/subtitles
    uniqueness: float = 0.0      # 0-100, inverse similarity to other clips
    motion: float = 0.0          # 0-100, inter-frame motion energy
    final_score: float = 0.0     # weighted combination 0-100
    rejected: bool = False
    reject_reason: str = ""


@dataclass
class ClipAnalysis:
    """Full analysis record for one harvested clip."""
    path: str
    scene_id: int = 0
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    blur_score: float = 0.0          # Laplacian variance (higher = sharper)
    brightness: float = 0.0           # 0-255 average
    black_frame_ratio: float = 0.0   # 0-1
    phash: str = ""                  # perceptual hash hex
    watermark_prob: float = 0.0      # 0-1, max corner stability
    watermark_region: str = ""       # corner name if watermark detected
    has_subtitle: bool = False       # True if subtitle text detected
    subtitle_confidence: float = 0.0 # 0-1, fraction of frames with subtitles
    has_watermark: bool = False
    overlay_regions: List[Dict[str, Any]] = field(default_factory=list)
    is_duplicate: bool = False
    duplicate_of: Optional[str] = None
    # Face analysis
    face_count: int = 0              # max faces in any frame
    face_coverage: float = 0.0       # avg fraction of frame covered by faces
    has_faces: bool = False
    faces_stable: bool = False       # same face appears in 60%+ frames
    # Object detection
    object_count: int = 0            # total objects detected
    dominant_classes: List[str] = field(default_factory=list)
    scene_type: str = "unknown"      # guessed from object classes
    yolo_available: bool = False     # True if YOLOv8 loaded successfully
    # Cleaning
    cleaned: bool = False            # True if clip was cleaned (watermark/subtitle blur applied)
    cleaned_path: Optional[str] = None  # path to cleaned version
    score: ClipScore = field(default_factory=ClipScore)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_rejected(self) -> bool:
        return self.score.rejected

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "scene_id": self.scene_id,
            "duration": round(self.duration, 3),
            "width": self.width,
            "height": self.height,
            "fps": round(self.fps, 2),
            "blur_score": round(self.blur_score, 2),
            "brightness": round(self.brightness, 2),
            "black_frame_ratio": round(self.black_frame_ratio, 4),
            "phash": self.phash,
            "watermark_prob": round(self.watermark_prob, 4),
            "watermark_region": self.watermark_region,
            "has_subtitle": self.has_subtitle,
            "subtitle_confidence": round(self.subtitle_confidence, 3),
            "has_watermark": self.has_watermark,
            "overlay_regions": list(self.overlay_regions or []),
            "is_duplicate": self.is_duplicate,
            "duplicate_of": self.duplicate_of,
            "face_count": self.face_count,
            "face_coverage": round(self.face_coverage, 4),
            "has_faces": self.has_faces,
            "faces_stable": self.faces_stable,
            "object_count": self.object_count,
            "dominant_classes": self.dominant_classes,
            "scene_type": self.scene_type,
            "yolo_available": self.yolo_available,
            "cleaned": self.cleaned,
            "cleaned_path": self.cleaned_path,
            "quality_score": round(self.score.quality, 2),
            "cleanliness_score": round(self.score.cleanliness, 2),
            "uniqueness_score": round(self.score.uniqueness, 2),
            "motion_score": round(self.score.motion, 2),
            "final_score": round(self.score.final_score, 2),
            "rejected": self.score.rejected,
            "reject_reason": self.score.reject_reason,
            "metadata": self.metadata,
        }
