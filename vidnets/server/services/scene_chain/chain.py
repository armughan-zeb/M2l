"""Per-scene media continuity-chain engine (opt-in, VIDZEO_SCENE_CHAIN=1).

Design goal (Phase 2): resolve every scene in parallel; each scene walks an
ordered chain of source *lanes* and accepts the first candidate that passes
INLINE ranking, otherwise falls through to the next lane — while remembering
the best-effort candidate so a scene is never left empty.

    lane order (default):  web-authentic images  ->  youtube clip  ->
                           stock video/image     ->  extracted frames
                           ->  best-effort (highest inline score seen)

This module is additive and does not modify the existing batch pipeline. It
reuses tested primitives:
  * web-authentic lane  -> web_image.resolver._resolve_scene (already an
    inline-ranked per-scene chain across all image APIs + reddit)
  * inline ranking      -> scene_chain.inline_rank.rank_candidate
  * frame extraction    -> visual_intelligence.frame_extractor

Video lanes (youtube / stock-video) are pluggable via the Lane protocol; the
youtube lane is wired once youtube_pipeline_v2 exposes a standalone per-scene
clip resolver (tracked as the next increment).
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from .inline_rank import RankResult, build_pipelines, is_video


# ── shared, thread-safe dedup state passed to every scene/lane ───────────────
@dataclass
class ChainState:
    global_phashes: set = field(default_factory=set)
    global_md5s: set = field(default_factory=set)
    used_files: set = field(default_factory=set)
    used_subject_signatures: set = field(default_factory=set)
    phash_lock: threading.Lock = field(default_factory=threading.Lock)
    md5_lock: threading.Lock = field(default_factory=threading.Lock)
    file_lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class Scene:
    index: int
    text: str
    subject: str
    duration: float
    queries: List[str] = field(default_factory=list)
    prefers_video: bool = False


@dataclass
class LaneResult:
    path: str
    label: str
    source: str
    # If the lane already ranked/accepted internally, set self_ranked=True and
    # the chain trusts it. Otherwise the chain ranks it inline.
    self_ranked: bool = False
    rank: Optional[RankResult] = None


class Lane(Protocol):
    name: str
    def resolve(self, scene: Scene, state: ChainState) -> Optional[LaneResult]:
        ...


def _accept_threshold() -> float:
    try:
        return float(os.environ.get("VIDZEO_CHAIN_ACCEPT_SCORE", "55") or 55)
    except Exception:
        return 55.0


class SceneChainResolver:
    """Runs the per-scene continuity chain across all scenes in parallel."""

    def __init__(
        self,
        lanes: List[Lane],
        visual_context=None,
        max_workers: int = 6,
    ):
        self.lanes = lanes
        self.visual_context = visual_context
        self.max_workers = max(1, int(max_workers or 6))
        self.image_pipeline, self.clip_pipeline = build_pipelines(visual_context)

    def _rank(self, scene: Scene, path: str) -> RankResult:
        return rank_candidate_safe(
            path, scene.index, scene.subject or scene.text, scene.text, scene.subject,
            image_pipeline=self.image_pipeline, clip_pipeline=self.clip_pipeline,
        )

    def _ordered_lanes(self, scene: Scene) -> List[Lane]:
        """Lane walk order for one scene. A scene that wants motion tries the
        video lanes first (youtube/stock-video) before falling to still images,
        so continuity-critical action scenes prefer footage; everyone else keeps
        the default authentic-image-first order."""
        if not scene.prefers_video:
            return self.lanes
        video = [ln for ln in self.lanes if getattr(ln, "produces_video", False)]
        other = [ln for ln in self.lanes if not getattr(ln, "produces_video", False)]
        return video + other if video else self.lanes

    def _resolve_one(self, scene: Scene, state: ChainState) -> Optional[LaneResult]:
        """Walk the lane chain for one scene; return the accepted or best result."""
        accept = _accept_threshold()
        best: Optional[LaneResult] = None
        best_score = -1.0
        for lane in self._ordered_lanes(scene):
            try:
                res = lane.resolve(scene, state)
            except Exception:
                res = None
            if res is None or not res.path:
                continue
            # A lane that ranked internally (e.g. the web resolver) is trusted.
            if res.self_ranked:
                return res
            rank = res.rank or self._rank(scene, res.path)
            res.rank = rank
            if rank.accepted and rank.score >= accept:
                return res
            if rank.score > best_score:
                best_score = rank.score
                best = res
        return best

    def resolve(self, scenes: List[Scene]) -> Dict[int, LaneResult]:
        state = ChainState()
        out: Dict[int, LaneResult] = {}
        workers = min(self.max_workers, max(1, len(scenes)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="scene-chain") as pool:
            futs = {pool.submit(self._resolve_one, sc, state): sc for sc in scenes}
            for fut in as_completed(futs):
                sc = futs[fut]
                try:
                    res = fut.result()
                except Exception:
                    res = None
                if res is not None:
                    out[sc.index] = res
        return out


def rank_candidate_safe(path, scene_index, topic, narration, subject, *, image_pipeline, clip_pipeline):
    from .inline_rank import rank_candidate
    return rank_candidate(
        path, scene_index, topic, narration, subject,
        image_pipeline=image_pipeline, clip_pipeline=clip_pipeline,
    )


# ── Web-authentic lane ───────────────────────────────────────────────────────
class WebAuthenticLane:
    """Per-scene image chain across Wikimedia/LoC/Flickr/Openverse/Pexels/
    Pixabay/Reddit. Reuses the tested resolver._resolve_scene, which already
    ranks inline and returns None when nothing acceptable is found."""

    name = "web_authentic"
    produces_video = False

    def __init__(self, output_dir: str, visual_context=None, source_telemetry=None):
        self.output_dir = Path(output_dir)
        self.visual_context = visual_context
        self.source_telemetry = source_telemetry

    def resolve(self, scene: Scene, state: ChainState) -> Optional[LaneResult]:
        from services.web_image.resolver import _resolve_scene
        from services.web_image.source_broker import SourceTelemetry
        telemetry = self.source_telemetry or SourceTelemetry()
        queries = scene.queries or [scene.subject or scene.text]
        cand = _resolve_scene(
            scene.index, scene.text, list(queries), None, self.output_dir,
            scene.subject, state.global_phashes, state.global_md5s, state.used_files,
            state.used_subject_signatures,
            state.phash_lock, state.md5_lock, state.file_lock,
            fast_mode=False,
            source_telemetry=telemetry,
        )
        if not cand or not cand.get("path"):
            return None
        return LaneResult(
            path=str(cand["path"]),
            label="Web Images",
            source=str(cand.get("domain") or "web"),
            self_ranked=False,  # chain inline-ranks it so the visual gate still applies
        )


# ── Extracted-frames lane ─────────────────────────────────────────────────────
class FramesLane:
    """Last-resort visual: pull a representative still frame from a video that
    was already downloaded for this or a nearby scene, so a scene that no source
    could fill still shows relevant motion-able footage rather than a blank."""

    name = "frames"
    produces_video = False

    def __init__(self, output_dir: str, source_video_provider: Optional[Callable[[Scene], Optional[str]]] = None):
        self.output_dir = Path(output_dir)
        self.source_video_provider = source_video_provider

    def resolve(self, scene: Scene, state: ChainState) -> Optional[LaneResult]:
        if self.source_video_provider is None:
            return None
        src = self.source_video_provider(scene)
        if not src or not os.path.exists(src):
            return None
        try:
            from services.visual_intelligence.frame_extractor import extract_clip_frames
            frames, _dur = extract_clip_frames(src, max_frames=1, long_edge=1280)
        except Exception:
            frames = []
        if not frames:
            return None
        # Source screenshots retain their original sharp pixels.
        frame_path = str(frames[0])
        from services.scene_understanding.analyzers import screen_extracted_frame
        if screen_extracted_frame(frame_path).get("accepted") is False:
            return None
        return LaneResult(path=frame_path, label="Extracted Frame", source="frames")


# ── YouTube-clip lane ─────────────────────────────────────────────────────────
class YouTubeLane:
    """Per-scene authentic footage from YouTube. Reuses the standalone resolver
    factored out of youtube_pipeline_v2 (build_clip_pools does the metadata
    search once; resolve_clip_for_scene downloads + visual-gates one scene).

    A clip returned here has already cleared the Qwen relevance/quality/overlay
    gate, so it is ``self_ranked`` — the chain trusts it and stops walking. If no
    candidate passes the gate the lane returns None and the chain falls through.
    """

    name = "youtube"
    produces_video = True

    def __init__(self, pools, state, visual_context=None):
        # pools: youtube_pipeline_v2.ClipPools ; state: ClipResolveState
        self.pools = pools
        self.state = state
        self.visual_context = visual_context

    @classmethod
    def build(
        cls,
        title: str,
        scene_texts: List[str],
        scene_durations: Optional[List[float]] = None,
        scene_query_overrides: Optional[List[dict]] = None,
        visual_context=None,
    ) -> Optional["YouTubeLane"]:
        """Run the one-time metadata search and return a ready lane, or None if
        the search yielded no usable pools (so the caller can skip the lane)."""
        try:
            from services.youtube_pipeline_v2 import build_clip_pools, make_resolve_state
        except Exception:
            return None
        try:
            pools = build_clip_pools(
                title, list(scene_texts),
                scene_query_overrides=scene_query_overrides,
                scene_durations=list(scene_durations or []),
            )
        except Exception:
            return None
        if not any(pools.assignments):
            return None
        state = make_resolve_state(pools)
        return cls(pools, state, visual_context=visual_context)

    def resolve(self, scene: Scene, state: ChainState) -> Optional[LaneResult]:
        from services.youtube_pipeline_v2 import resolve_clip_for_scene
        path = resolve_clip_for_scene(
            scene.index, self.pools, self.state, self.visual_context,
        )
        if not path or not os.path.exists(path):
            return None
        with state.file_lock:
            if path in state.used_files:
                return None
            state.used_files.add(path)
        return LaneResult(
            path=str(path), label="YouTube Clips", source="youtube", self_ranked=True,
        )


# ── Stock-video lane ──────────────────────────────────────────────────────────
class StockVideoLane:
    """Per-scene Pexels/Pixabay VIDEO search. Downloads the top-scoring clip and
    hands it to the chain for INLINE ranking (not self-ranked) — stock footage
    has no upstream visual gate, so the chain verifies relevance itself."""

    name = "stock_video"
    produces_video = True

    def __init__(self, pexels_key: str = "", pixabay_key: str = "", cache_dir: Optional[str] = None):
        self._manager = None
        self._pexels_key = pexels_key
        self._pixabay_key = pixabay_key
        self._cache_dir = cache_dir
        self._init_lock = threading.Lock()

    @classmethod
    def build(cls, output_dir: str) -> Optional["StockVideoLane"]:
        pexels = os.environ.get("PEXELS_API_KEY", "") or ""
        pixabay = os.environ.get("PIXABAY_API_KEY", "") or ""
        if not pexels and not pixabay:
            return None  # no keys -> lane is a no-op, skip it entirely
        cache = str(Path(output_dir) / "stock_video_cache")
        return cls(pexels_key=pexels, pixabay_key=pixabay, cache_dir=cache)

    def _get_manager(self):
        if self._manager is None:
            with self._init_lock:
                if self._manager is None:
                    from services.real_media_service import create_media_manager, MediaConfig
                    mgr = create_media_manager(self._pexels_key, self._pixabay_key)
                    if self._cache_dir:
                        try:
                            os.makedirs(self._cache_dir, exist_ok=True)
                            mgr.config.cache_dir = self._cache_dir
                            mgr.downloader.config.cache_dir = self._cache_dir
                        except Exception:
                            pass
                    self._manager = mgr
        return self._manager

    def resolve(self, scene: Scene, state: ChainState) -> Optional[LaneResult]:
        manager = self._get_manager()
        if manager is None:
            return None
        query = (scene.queries[0] if scene.queries else "") or scene.subject or scene.text
        query = str(query).strip()
        if not query:
            return None
        try:
            items = manager.search_engine.search_all_sources(query, media_type="video") or []
        except Exception:
            items = []
        if not items:
            return None
        for item in items:
            url = getattr(item, "download_url", "")
            if not url:
                continue
            with state.md5_lock:
                if url in state.global_md5s:
                    continue
            try:
                ok = manager.downloader.download_item(item)
            except Exception:
                ok = False
            local = getattr(item, "local_path", None)
            if not ok or not local or not os.path.exists(local):
                continue
            with state.file_lock:
                if local in state.used_files:
                    continue
                state.used_files.add(local)
            with state.md5_lock:
                state.global_md5s.add(url)
            return LaneResult(path=str(local), label="Stock Media", source=str(getattr(item, "source", "stock")))
        return None


__all__ = [
    "Scene", "LaneResult", "Lane", "ChainState",
    "SceneChainResolver", "WebAuthenticLane", "FramesLane",
    "YouTubeLane", "StockVideoLane",
]
