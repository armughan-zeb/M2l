"""Parallel Pipelined Director Execution Engine.

Implements the Parallel Timeline Zone Architecture:
1. Upfront Directorial Planning: LLM Director analyzes the script immediately,
   producing a structured EditPlan.json before any rendering or media retrieval.
2. Scene Partitioning:
   - Media-Independent Scenes (AnimatedMap, Chart, KineticText, Timeline, ChapterTitle):
     These procedural motion graphics do NOT require external web scrapers.
   - Media-Attached Scenes (SmartImage, SmartVideo, DocumentHighlight):
     Dispatched immediately to parallel retrieval workers.
3. Concurrent Asynchronous Coordination:
   - Media downloads resolve in parallel workers.
   - As each image/video resolves, it attaches to its respective scene.
   - Zero sequential bottleneck: Remotion composition bundles and executes
     without idle waiting.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .editorial_director import (
    EditingDirector,
    EditPlan,
    SceneDecision,
    VisualSceneType,
)
from .native_render_policy import render_semantic_documentary
from .remotion_render_gate import run_with_remotion_render_slot


MEDIA_INDEPENDENT_TYPES = {
    VisualSceneType.ANIMATED_MAP.value,
    VisualSceneType.CHART.value,
    VisualSceneType.KINETIC_TEXT.value,
    VisualSceneType.TIMELINE.value,
    VisualSceneType.CHAPTER_TITLE.value,
    VisualSceneType.DIAGRAM.value,
}


@dataclass
class SceneTask:
    scene_index: int
    scene_id: str
    scene_type: str
    is_media_independent: bool
    status: str = "pending"  # "pending", "retrieving", "ready", "rendered"
    asset_path: Optional[str] = None
    render_duration: float = 0.0


class ParallelDirectorPipeline:
    """Orchestrates parallel media retrieval and Remotion timeline execution."""

    def __init__(
        self,
        director: Optional[EditingDirector] = None,
        max_media_workers: int = 4,
    ):
        self.director = director or EditingDirector()
        self.max_media_workers = max_media_workers

    def plan_timeline(
        self,
        script_text: str,
        scene_texts: Sequence[str],
        scene_durations: Sequence[float],
        topic: str = "",
        category: str = "",
        channel_brand: Optional[Mapping[str, Any]] = None,
    ) -> Tuple[EditPlan, List[SceneTask]]:
        """Direct the entire production upfront, partitioning independent vs media-attached scenes."""
        print("[ParallelPipeline] Directing scene compositions upfront...")
        plan = self.director.plan_documentary(
            script_text=script_text,
            scene_texts=scene_texts,
            scene_durations=scene_durations,
            topic=topic,
            category=category,
            channel_brand=channel_brand,
        )

        tasks: List[SceneTask] = []
        for s in plan.scenes:
            is_ind = s.primary_visual.type in MEDIA_INDEPENDENT_TYPES and not (s.primary_visual.asset and s.primary_visual.asset.get("require_download"))
            task = SceneTask(
                scene_index=s.scene_index,
                scene_id=s.scene_id,
                scene_type=s.primary_visual.type,
                is_media_independent=is_ind,
                status="ready" if is_ind else "retrieving",
                asset_path=s.primary_visual.asset.get("src") if s.primary_visual.asset else None,
            )
            tasks.append(task)

        print(f"[ParallelPipeline] Partitioned {len(tasks)} scenes:")
        for t in tasks:
            print(f"  - Scene {t.scene_index} [{t.scene_type}]: {'Media-Independent (Immediate)' if t.is_media_independent else 'Media-Attached (Parallel Retrieve)'}")

        return plan, tasks

    def resolve_media_parallel(
        self,
        plan: EditPlan,
        tasks: List[SceneTask],
        media_fetcher: Optional[Callable[[int, SceneDecision], Optional[str]]] = None,
    ) -> EditPlan:
        """Resolve all media-dependent scenes concurrently in background threads."""
        media_tasks = [t for t in tasks if not t.is_media_independent]
        if not media_tasks:
            print("[ParallelPipeline] All scenes are media-independent. Skipping retrieval.")
            return plan

        print(f"[ParallelPipeline] Spawning {len(media_tasks)} parallel media workers...")
        start_t = time.monotonic()

        def fetch_worker(t: SceneTask) -> Tuple[int, Optional[str]]:
            scene = plan.scenes[t.scene_index]
            if media_fetcher:
                resolved = media_fetcher(t.scene_index, scene)
            else:
                resolved = t.asset_path
            return t.scene_index, resolved

        with ThreadPoolExecutor(max_workers=min(self.max_media_workers, len(media_tasks))) as executor:
            future_to_task = {
                executor.submit(fetch_worker, task): task
                for task in media_tasks
            }
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    idx, resolved_path = future.result()
                    task.status = "ready"
                    task.asset_path = resolved_path
                    if resolved_path:
                        is_vid = any(resolved_path.lower().endswith(ext) for ext in (".mp4", ".webm", ".mov", ".m4v"))
                        media_type = "video" if is_vid else "image"
                        plan.scenes[idx].primary_visual.asset = {
                            "type": media_type,
                            "src": resolved_path.replace("\\", "/"),
                        }
                        if is_vid and plan.scenes[idx].primary_visual.type == VisualSceneType.SMART_IMAGE.value:
                            plan.scenes[idx].primary_visual.type = VisualSceneType.SMART_VIDEO.value
                    print(f"  [OK] Scene {idx} media resolved in {time.monotonic() - start_t:.2f}s: {resolved_path}")
                except Exception as exc:
                    print(f"  [WARN] Scene {task.scene_index} media fetch warning: {exc}")
                    task.status = "ready"  # Proceed with procedural fallback

        return plan

    def render_timeline(
        self,
        plan: EditPlan,
        output_mp4: str | Path,
        voiceover_path: Optional[str | Path] = None,
        config: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Convert planned composition to Remotion manifest and execute local rendering."""
        print("[ParallelPipeline] Exporting Remotion composition manifest...")
        manifest = plan.to_remotion_manifest()

        if voiceover_path:
            manifest["audio"]["voiceover"] = {
                "src": str(voiceover_path).replace("\\", "/"),
                "volume": 1.0,
            }

        cfg = dict(config or {})
        # Every documentary quality mode uses the same Remotion scene graph.
        # Fast preview may change its own preview settings, but a final export
        # must never silently switch to the older FFmpeg composition.
        cfg["render_backend"] = "remotion"
        cfg["semantic_render_required"] = True
        cfg["allow_legacy_render_fallback"] = False
        cfg.setdefault("scale", 1.0)
        cfg.setdefault("concurrency", 2)

        print(f"[ParallelPipeline] Rendering timeline to {output_mp4} via the selected local renderer...")
        return run_with_remotion_render_slot(
            lambda: render_semantic_documentary(
                manifest=manifest,
                output_path=output_mp4,
                config=cfg,
            ),
        )
