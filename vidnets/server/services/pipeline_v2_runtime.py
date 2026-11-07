"""Production bridge for the immutable Storyboard V2 contracts.

This module deliberately contains no Flask or provider code.  ``app.py`` owns
transport orchestration; this bridge owns the job/run-scoped storyboard and
converts its immutable scenes into the small dictionaries expected by the
legacy render boundary during the V2 rollout.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
from typing import Any, Iterable, Mapping, Sequence

from .storyboard_v2 import (
    EditorialCueV2,
    GenerationRequestV2,
    MediaCandidateV1,
    ScriptPlanV1,
    StoryboardStore,
    StoryboardV2,
    WordTimingV1,
    align_script_plan_to_words,
    build_script_plan,
    stable_id,
)
from .storyboard_v2.store import redact_sensitive


def _initialize_canonical_ffmpeg(config: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve, exercise, and publish one FFmpeg pair for every V2 subsystem."""
    from .media_integrity import resolve_ffmpeg_pair, smoke_test_ffmpeg_pair

    workspace_root = Path(__file__).resolve().parents[2]
    ffmpeg_path, ffprobe_path = resolve_ffmpeg_pair(workspace_root)
    smoke = smoke_test_ffmpeg_pair(
        ffmpeg_path,
        ffprobe_path,
    )
    if not smoke.get("ok"):
        raise RuntimeError(
            f"Canonical FFmpeg startup smoke test failed: {smoke.get('reason') or 'unknown'}"
        )
    os.environ["VIDZEO_FFMPEG_PATH"] = ffmpeg_path
    os.environ["VIDZEO_FFPROBE_PATH"] = ffprobe_path
    ffmpeg_dir = str(Path(ffmpeg_path).parent)
    path_parts = [part for part in str(os.environ.get("PATH") or "").split(os.pathsep) if part]
    if ffmpeg_dir.lower() not in {part.lower() for part in path_parts}:
        os.environ["PATH"] = os.pathsep.join([ffmpeg_dir, *path_parts])
    if isinstance(config, dict):
        config["_ffmpeg_path"] = ffmpeg_path
        config["_ffprobe_path"] = ffprobe_path
        config["_ffmpeg_smoke"] = dict(smoke)
    return dict(smoke)


def pipeline_v2_mode(config: Mapping[str, Any] | None) -> str:
    mode = str((config or {}).get("pipeline_v2_mode") or "on").strip().lower()
    return mode if mode in {"off", "shadow", "on"} else "on"


def pipeline_v2_active(config: Mapping[str, Any] | None) -> bool:
    return pipeline_v2_mode(config) == "on"


def pipeline_v2_visible(config: Mapping[str, Any] | None) -> bool:
    return pipeline_v2_mode(config) in {"shadow", "on"}


def _scene_weights(scenes: Sequence[Any]) -> list[int]:
    return [
        max(1, int(getattr(scene, "token_end", 0)) - int(getattr(scene, "token_start", 0)))
        for scene in scenes
    ]


def estimated_scene_durations(scenes: Sequence[Any], total_duration: float) -> list[float]:
    weights = _scene_weights(scenes)
    denominator = max(1, sum(weights))
    durations = [float(total_duration) * weight / denominator for weight in weights]
    if durations:
        durations[-1] += float(total_duration) - sum(durations)
    return [round(max(0.001, value), 6) for value in durations]


def _word_rows(words: Iterable[Mapping[str, Any] | WordTimingV1]) -> tuple[WordTimingV1, ...]:
    rows = []
    for index, raw in enumerate(words or ()):
        if isinstance(raw, WordTimingV1):
            rows.append(raw)
        else:
            rows.append(WordTimingV1.from_mapping(index, raw))
    return tuple(rows)


def _fingerprint(path: str) -> str:
    candidate = Path(str(path or ""))
    if not candidate.is_file():
        return ""
    digest = hashlib.sha256()
    try:
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def _safe_scope_identifier(value: Any, *, prefix: str) -> str:
    """Return a filesystem-safe, collision-resistant job/run identifier."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("._-")
    if sanitized == raw and len(sanitized) <= 96:
        return sanitized
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:12]
    stem = sanitized[:72].rstrip("._-") or prefix
    return f"{stem}-{digest}"


@dataclass
class V2PipelineRuntime:
    request: GenerationRequestV2
    storyboard: StoryboardV2
    store: StoryboardStore

    @classmethod
    def create(
        cls,
        config: Mapping[str, Any],
        *,
        output_path: str | os.PathLike[str],
    ) -> "V2PipelineRuntime":
        _initialize_canonical_ffmpeg(config)
        request = GenerationRequestV2.from_mapping(config)
        plan = build_script_plan(
            request.script,
            content_title=request.content_title,
            format_hint=request.format_hint,
            language=request.language,
            scene_mode=request.scene_mode,
        )
        configured_job_id = str(
            config.get("_queue_job_id")
            or config.get("queue_job_id")
            or config.get("job_id")
            or ""
        ).strip()
        job_id = _safe_scope_identifier(configured_job_id, prefix="job") or stable_id(
            "job", request.output_name, plan.script_hash, secrets.token_hex(8)
        )
        configured_run_id = str(
            config.get("_queue_run_id")
            or config.get("queue_run_id")
            or config.get("run_id")
            or ""
        ).strip()
        run_id = _safe_scope_identifier(configured_run_id, prefix="run") or stable_id(
            "run", job_id, secrets.token_hex(12)
        )
        store = StoryboardStore.for_output(output_path, job_id=job_id, run_id=run_id)
        # ``create`` starts a new generation, even when the caller reuses an
        # output name or queue identifiers.  Clear only this scoped ledger so
        # a prior attempt cannot make the first event append fail as stale.
        store.reset_event_ledger()
        storyboard = StoryboardV2.new(
            job_id=job_id,
            run_id=run_id,
            request=request,
            script_plan=plan,
        )
        store.save_atomic(storyboard)
        runtime = cls(request=request, storyboard=storyboard, store=store)
        runtime.record(
            "script_plan_created",
            {
                "script_hash": plan.script_hash,
                "detected_format": plan.detected_format,
                "format_confidence": plan.format_confidence,
                "container_count": len(plan.leaf_containers),
                "scene_count": len(plan.scenes),
            },
        )
        return runtime

    @property
    def script_plan(self) -> ScriptPlanV1:
        return self.storyboard.script_plan

    @property
    def snapshot_path(self) -> str:
        return str(self.store.snapshot_path)

    def record(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        scene_id: str | None = None,
        container_id: str | None = None,
    ) -> None:
        self.storyboard = self.store.record_attempt(
            self.storyboard,
            event_type,
            payload,
            scene_id=scene_id,
            container_id=container_id,
        )

    def expected_plan(self, estimated_duration: float) -> dict[str, Any]:
        plan = self.script_plan
        return {
            "scene_texts": [scene.text for scene in plan.scenes],
            "scene_durations": estimated_scene_durations(plan.scenes, estimated_duration),
            "scene_mode": self.request.scene_mode,
            "estimated_duration": float(estimated_duration),
            "script_plan_v2": plan,
            "scene_contracts": list(plan.scenes),
            "containers": list(plan.containers),
            "detected_format": plan.detected_format,
            "format_confidence": plan.format_confidence,
            "storyboard_path": self.snapshot_path,
        }

    def align_audio(
        self,
        whisper_words: Sequence[Mapping[str, Any] | WordTimingV1],
        *,
        audio_duration: float,
    ) -> dict[str, Any]:
        words = _word_rows(whisper_words)
        alignment_summary: dict[str, Any]
        if words:
            timed_plan, alignment = align_script_plan_to_words(
                self.script_plan,
                words,
                audio_duration=float(audio_duration),
            )
            alignment_summary = {
                "matched_ratio": alignment.token_alignment.matched_ratio,
                "confidence": alignment.token_alignment.confidence,
                "exact_scene_count": sum(
                    1 for scene in timed_plan.scenes if scene.timing_quality == "exact"
                ),
            }
            words = alignment.words
        else:
            durations = estimated_scene_durations(self.script_plan.scenes, audio_duration)
            cursor = 0.0
            timed_scenes = []
            for scene, duration in zip(self.script_plan.scenes, durations):
                end = min(float(audio_duration), cursor + duration)
                if scene.order == len(self.script_plan.scenes) - 1:
                    end = float(audio_duration)
                timed_scenes.append(
                    scene.with_timing(
                        word_start_index=None,
                        word_end_index=None,
                        start_seconds=cursor,
                        end_seconds=max(cursor + 0.001, end),
                        timing_quality="estimated",
                        alignment_confidence=0.0,
                    )
                )
                cursor = end
            timed_plan = self.script_plan.with_scenes(timed_scenes)
            alignment_summary = {
                "matched_ratio": 0.0,
                "confidence": 0.0,
                "exact_scene_count": 0,
                "reason": "whisper_words_unavailable",
            }
        self.storyboard = self.storyboard.with_script_plan(timed_plan, word_timings=words)
        self.store.save_atomic(self.storyboard)
        self.record("audio_alignment_completed", alignment_summary)
        return self.audio_plan_fields()

    def audio_plan_fields(self) -> dict[str, Any]:
        scenes = self.script_plan.scenes
        windows = []
        for scene in scenes:
            windows.append(
                {
                    "scene_index": scene.order,
                    "scene_id": scene.scene_id,
                    "container_id": scene.container_id,
                    "contract_hash": scene.contract_hash,
                    "start_seconds": round(float(scene.start_seconds or 0.0), 6),
                    "end_seconds": round(float(scene.end_seconds or 0.0), 6),
                    "duration_needed": round(float(scene.duration or 0.0), 6),
                    "anchor_text": scene.text,
                    "neighboring_text": "",
                    "timing_quality": scene.timing_quality,
                    "word_start_index": scene.word_start_index,
                    "word_end_index": scene.word_end_index,
                }
            )
        return {
            "scene_texts": [scene.text for scene in scenes],
            "scene_durations": [round(float(scene.duration or 0.0), 6) for scene in scenes],
            "scene_timing_windows": windows,
            "scene_contracts": list(scenes),
            "containers": list(self.script_plan.containers),
            "script_plan_v2": self.script_plan,
            "storyboard_path": self.snapshot_path,
        }

    def persist_media_resolutions(self, resolutions: Mapping[int, Any]) -> None:
        candidates = []
        states: dict[str, dict[str, Any]] = {}
        for index, scene in enumerate(self.script_plan.scenes):
            resolution = resolutions.get(index)
            if resolution is None:
                states[scene.scene_id] = {
                    "status": "unresolved",
                    "degraded_reason": "resolver_missing_terminal_state",
                }
                continue
            row = resolution.to_dict() if hasattr(resolution, "to_dict") else dict(resolution)
            selected_path = str(row.get("selected_path") or row.get("path") or "")
            status = str(row.get("status") or "unresolved")
            states[scene.scene_id] = {
                "status": "degraded" if str(row.get("fallback_reason") or "").startswith("degraded:") else status,
                "selected_path": selected_path,
                "selected_source": row.get("provider") or row.get("source") or "",
                "verification_status": row.get("verification_status") or "",
                "fallback_reason": row.get("fallback_reason") or "",
                "resolution_kind": row.get("resolution_kind") or "unique",
                "scene_text": scene.text,
                "query_used": str(row.get("query_used") or ""),
                "requirement_hash": str(row.get("requirement_hash") or ""),
                # Retain rejected/pending alternatives as well as the winner.
                # Otherwise a later investigation sees only the final graphic.
                "attempts": redact_sensitive(row.get("attempts") or []),
                "candidate_ledger": redact_sensitive(row.get("candidate_ledger") or []),
                "alternates": redact_sensitive(row.get("alternates") or []),
                "visual_requirement": redact_sensitive((row.get("final_scene_context") or {}).get("visual_requirement") or {}),
                "gate_outcomes": redact_sensitive(row.get("gate_outcomes") or {}),
                "timings": redact_sensitive(row.get("timings") or {}),
            }
            if selected_path:
                video_start = row.get("video_start", row.get("video_start_seconds"))
                video_end = row.get("video_end", row.get("video_end_seconds"))
                if (video_start is None) != (video_end is None):
                    video_start = video_end = None
                else:
                    # Provider adapters occasionally return a zero-length or
                    # reversed range when a clip was recovered from disk (for
                    # example after a soft timeout).  A malformed range must
                    # never abort the whole production while persisting the
                    # otherwise valid media candidate.  Treat it as an
                    # un-ranged video; the renderer will use the full asset.
                    try:
                        if video_start is not None and video_end is not None:
                            video_start = float(video_start)
                            video_end = float(video_end)
                            if (
                                not math.isfinite(video_start)
                                or not math.isfinite(video_end)
                                or video_start < 0
                                or video_end <= video_start
                            ):
                                video_start = video_end = None
                    except (TypeError, ValueError):
                        video_start = video_end = None
                candidates.append(
                    MediaCandidateV1(
                        candidate_id=stable_id(
                            "med", scene.scene_id, selected_path, row.get("provider") or ""
                        ),
                        scene_id=scene.scene_id,
                        container_id=scene.container_id,
                        provider=str(row.get("provider") or row.get("source") or "unknown"),
                        source=str(row.get("provider") or row.get("source") or "unknown"),
                        origin_url=str(row.get("source_url") or row.get("origin_url") or ""),
                        local_path=selected_path,
                        media_type=str(row.get("media_type") or "image"),
                        query=str(row.get("query_used") or row.get("query") or ""),
                        fingerprint=_fingerprint(selected_path),
                        rights_status=str(row.get("rights_status") or "unknown"),
                        license=str(row.get("license") or ""),
                        attribution=str(row.get("attribution") or row.get("creator") or ""),
                        video_start_seconds=video_start,
                        video_end_seconds=video_end,
                        deterministic_scores={"metadata": float(row.get("metadata_score") or 0.0)},
                        qwen_result=dict(row.get("visual_decision") or {}),
                        gate_outcomes={
                            "verification_status": row.get("verification_status") or "",
                            "resolution_kind": row.get("resolution_kind") or "unique",
                            "license_url": row.get("license_url") or "",
                            "candidate_id": row.get("candidate_id") or "",
                            "asset_fingerprint": row.get("asset_fingerprint") or "",
                            **dict(row.get("gate_outcomes") or {}),
                            "attempts": row.get("attempts") or [],
                        },
                        status="selected" if status in {"accepted", "resolved"} else status,
                        requirement_hash=str(row.get("requirement_hash") or getattr(resolution, "requirement_hash", "") or ""),
                        candidate_evidence=dict(row.get("candidate_evidence") or getattr(resolution, "candidate_evidence", {}) or {}),
                        verification_status=str(row.get("verification_status") or ""),
                    )
                )
        self.storyboard = replace(
            self.storyboard,
            media_candidates=tuple(candidates),
            scene_states=states,
        )
        self.store.save_atomic(self.storyboard)
        self.record(
            "media_resolution_completed",
            {
                "accepted": sum(1 for state in states.values() if state["status"] in {"accepted", "resolved"}),
                "degraded": sum(1 for state in states.values() if state["status"] == "degraded"),
                "unresolved": sum(1 for state in states.values() if state["status"] == "unresolved"),
            },
        )

    def persist_editorial_cues(self, cues: Sequence[EditorialCueV2 | Mapping[str, Any]]) -> None:
        cue_fields = {item.name for item in fields(EditorialCueV2)}
        timing_aliases = {
            "scene_local_start_seconds": "scene_local_start",
            "scene_local_end_seconds": "scene_local_end",
            "absolute_start_seconds": "absolute_start",
            "absolute_end_seconds": "absolute_end",
        }
        normalized_rows = []
        for cue in cues:
            if isinstance(cue, EditorialCueV2):
                normalized_rows.append(cue)
                continue
            row = dict(cue)
            for canonical, alias in timing_aliases.items():
                if canonical not in row and alias in row:
                    row[canonical] = row[alias]
            normalized_rows.append(
                EditorialCueV2.from_dict(
                    {key: value for key, value in row.items() if key in cue_fields}
                )
            )
        normalized = tuple(normalized_rows)
        self.storyboard = replace(self.storyboard, editorial_cues=normalized)
        self.store.save_atomic(self.storyboard)
        self.record("editorial_cues_planned", {"cue_count": len(normalized)})

    def finalize(self, *, video_path: str, component_manifest: Mapping[str, Any]) -> str:
        # Keep the manifest beside the exported video. Gallery/download
        # consumers already resolve sidecar manifests from the output path;
        # writing to a shared temp directory caused production exports and
        # isolated runtime tests to disagree about where the final metadata
        # lived.
        video_file = Path(video_path).resolve()
        video_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_path = video_file.with_suffix(".manifest.json")
        staged_path = manifest_path.with_name(f".{manifest_path.name}.{secrets.token_hex(4)}.part")
        try:
            with staged_path.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    redact_sensitive({
                        **dict(component_manifest),
                        "media_evidence": {
                            "schema_version": 1,
                            "job_id": self.storyboard.job_id,
                            "run_id": self.storyboard.run_id,
                            "scene_states": self.storyboard.scene_states,
                            "attempt_ledger": [entry.to_dict() for entry in self.storyboard.attempt_ledger],
                        },
                    }),
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staged_path, manifest_path)
        finally:
            try:
                staged_path.unlink(missing_ok=True)
            except OSError:
                pass

        self.record(
            "render_completed",
            {
                "video_path": str(video_path),
                "video_fingerprint": _fingerprint(video_path),
                "manifest_path": str(manifest_path),
                "source_accounting": component_manifest.get("source_accounting") or {},
                "duration": component_manifest.get("duration"),
            },
        )
        return str(manifest_path)


def create_runtime_for_config(
    config: Mapping[str, Any],
    *,
    output_dir: str | os.PathLike[str],
    output_name: str,
) -> V2PipelineRuntime:
    output_path = Path(output_dir) / f"{output_name}.mp4"
    return V2PipelineRuntime.create(config, output_path=output_path)


def build_aligned_audio_plan_v2(
    expected_plan: Mapping[str, Any],
    whisper_words: Sequence[Mapping[str, Any] | WordTimingV1],
    *,
    audio_path: str,
    audio_duration: float,
    scene_mode: str,
    runtime: V2PipelineRuntime | None = None,
) -> dict[str, Any]:
    """Complete the final immutable timing snapshot after TTS/Whisper."""

    if runtime is not None:
        aligned = runtime.align_audio(whisper_words or (), audio_duration=audio_duration)
    else:
        provisional_plan = expected_plan.get("script_plan_v2")
        if not isinstance(provisional_plan, ScriptPlanV1):
            raise ValueError("V2 audio alignment requires ScriptPlanV1")
        words = _word_rows(whisper_words or ())
        if words:
            timed_plan, alignment = align_script_plan_to_words(
                provisional_plan,
                words,
                audio_duration=float(audio_duration),
            )
            word_timings = alignment.words
        else:
            durations = estimated_scene_durations(provisional_plan.scenes, audio_duration)
            cursor = 0.0
            timed_scenes = []
            for scene, duration in zip(provisional_plan.scenes, durations):
                end = (
                    float(audio_duration)
                    if scene.order == len(provisional_plan.scenes) - 1
                    else cursor + duration
                )
                timed_scenes.append(
                    scene.with_timing(
                        word_start_index=None,
                        word_end_index=None,
                        start_seconds=cursor,
                        end_seconds=max(cursor + 0.001, end),
                        timing_quality="estimated",
                        alignment_confidence=0.0,
                    )
                )
                cursor = end
            timed_plan = provisional_plan.with_scenes(timed_scenes)
            word_timings = tuple()
        scenes = timed_plan.scenes
        aligned = {
            "scene_texts": [scene.text for scene in scenes],
            "scene_durations": [round(float(scene.duration or 0.0), 6) for scene in scenes],
            "scene_timing_windows": [
                {
                    "scene_index": scene.order,
                    "scene_id": scene.scene_id,
                    "container_id": scene.container_id,
                    "contract_hash": scene.contract_hash,
                    "start_seconds": scene.start_seconds,
                    "end_seconds": scene.end_seconds,
                    "duration_needed": scene.duration,
                    "anchor_text": scene.text,
                    "neighboring_text": "",
                    "timing_quality": scene.timing_quality,
                    "word_start_index": scene.word_start_index,
                    "word_end_index": scene.word_end_index,
                }
                for scene in scenes
            ],
            "scene_contracts": list(scenes),
            "containers": list(timed_plan.containers),
            "script_plan_v2": timed_plan,
            "word_timings_v2": list(word_timings),
        }
    return {
        "audio_path": audio_path,
        "duration": float(audio_duration),
        "whisper_words": list(whisper_words or ()),
        "scene_mode": scene_mode,
        **aligned,
    }
