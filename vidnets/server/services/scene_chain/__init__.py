"""Per-scene media continuity chain (opt-in Phase 2 architecture).

Instead of resolving media in per-source batches and ranking at the end, this
resolves every scene in parallel, and each scene walks an ordered chain of
source lanes — accepting the first candidate that passes INLINE ranking and
falling through to the next lane otherwise, while remembering the best-effort
candidate so a scene is never left empty.

Enabled with ``VIDZEO_SCENE_CHAIN=1``. See ``chain.resolve_scene_chain``.
"""
from .inline_rank import RankResult, rank_candidate, build_pipelines, is_video
from .chain import (
    Scene, LaneResult, Lane, ChainState, SceneChainResolver,
    WebAuthenticLane, FramesLane, YouTubeLane, StockVideoLane,
)

__all__ = [
    "RankResult", "rank_candidate", "build_pipelines", "is_video",
    "Scene", "LaneResult", "Lane", "ChainState", "SceneChainResolver",
    "WebAuthenticLane", "FramesLane", "YouTubeLane", "StockVideoLane",
]
