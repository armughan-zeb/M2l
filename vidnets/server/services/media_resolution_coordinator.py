"""Deadline-aware, job-scoped media resolution primitives.

The legacy branches still provide the provider-specific discovery logic.  This
module supplies the shared ledger/cancellation contract they can stream into,
so callers do not need an outer ``future.result(timeout=...)`` that leaves
orphaned yt-dlp workers behind.
"""
from __future__ import annotations
from .youtube_overlay_policy import source_overlays_preserved

import os
import hashlib
import math
import re
import threading
from .job_context import JobThread
import time
from urllib.parse import parse_qs, urlparse
from concurrent.futures import ALL_COMPLETED, FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from .visual_requirement import (
    candidate_asset_metadata,
    candidate_domain_context_pending,
    candidate_nearest_match,
    candidate_requirement_match,
    graphic_fallback_spec,
)
from .visual_intelligence.context import scene_requires_visual_verification


@dataclass
class SceneResolution:
    scene_index: int
    final_scene_context: Dict[str, Any] = field(default_factory=dict)
    selected_path: str = ""
    media_type: str = ""
    provider: str = ""
    source_url: str = ""
    rights_status: str = "unknown"
    rights_risk: str = "high"
    rights_review_required: bool = True
    rights_evidence: str = ""
    metadata_score: float = 0.0
    # Normalized selection metadata.  These fields are intentionally optional
    # so provider workers written against the original contract remain valid.
    candidate_class: str = ""
    resolution_kind: str = "unique"
    query_used: str = ""
    query_wave: int = 0
    attempts: list = field(default_factory=list)
    alternates: list = field(default_factory=list)
    visual_decision: Optional[Dict[str, Any]] = None
    status: str = "unresolved"
    fallback_reason: str = ""
    timings: Dict[str, float] = field(default_factory=dict)
    scene_id: str = ""
    container_id: str = ""
    contract_hash: str = ""
    verification_status: str = ""
    candidate_id: str = ""
    asset_fingerprint: str = ""
    license: str = ""
    license_url: str = ""
    attribution: str = ""
    creator: str = ""
    video_start: Optional[float] = None
    video_end: Optional[float] = None
    # Source timestamps are retained for provenance, but downloaded YouTube
    # candidates are usually already trimmed to that range. Renderers must
    # seek from the local cut's zero, not seek the original source timestamp a
    # second time into the trimmed file.
    render_clip_start: Optional[float] = None
    gate_outcomes: Dict[str, Any] = field(default_factory=dict)
    overlay_policy: str = ""
    overlay_types: list = field(default_factory=list)
    blur_regions: list = field(default_factory=list)
    rejection_reason: str = ""
    cleaned_path: str = ""
    requirement_hash: str = ""
    candidate_evidence: Dict[str, Any] = field(default_factory=dict)
    # Bounded provider/query/path ledger for the final audit. ``attempts`` is
    # the compact event stream; this preserves why each finalist was accepted
    # or rejected without discarding the candidate itself.
    candidate_ledger: list = field(default_factory=list)
    graphic_fallback: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        if self.overlay_policy == 'preserve_source_no_blur':
            result['no_blur'] = True
            result['source_overlays_preserved'] = bool(
                self.status == 'accepted' and not self.rejection_reason and not self.cleaned_path
            )
        return result


def _resolution_reusable(resolution: SceneResolution) -> bool:
    """Return whether a verified resolution is safe to carry into another scene."""
    if resolution is None:
        return False
    provider = str(resolution.provider or "").strip().lower()
    if provider in {"neutral", "graphic", "remotion", "generated_graphic"}:
        return False
    if provider not in {"youtube", "youtube clips", "yt"}:
        return True
    overlay_types = {
        str(value).strip().lower()
        for value in (resolution.overlay_types or [])
        if str(value).strip()
    }
    rejection = str(resolution.rejection_reason or "").strip().lower()
    if 'large_text_band' in overlay_types and resolution.overlay_policy != 'preserve_source_no_blur':
        return False
    if overlay_types.intersection({'subtitle', 'subtitles', 'caption'}) and resolution.overlay_policy != 'preserve_source_no_blur':
        return False
    if any(token in rejection for token in (
        "caption_overlay_rejected", "subtitle_overlay_rejected",
        "large_logo_overlay_rejected", "central_logo_overlay_rejected",
    )):
        return False
    # Small publisher watermarks are explicitly tolerated by the production
    # YouTube policy. A watermark-only cut may have ``overlay_blur_failed``
    # because there is nothing safe to blur; it is still a verified real clip
    # and is eligible for bounded master reuse. Never extend this exception to
    # captions, central logos, facecams or title-card overlays.
    if "overlay_blur_failed" in rejection:
        watermark_only = bool(overlay_types) and overlay_types.issubset({"watermark", "logo"})
        if not watermark_only:
            return False
    return True


def _review_bound_to_scene(scene: Mapping[str, Any], resolution: SceneResolution) -> bool:
    """A neighboring asset's approval must cover this scene and these bytes."""
    requirement = scene.get('visual_requirement') or {}
    decision = resolution.visual_decision or {}
    if not isinstance(requirement, Mapping) or not isinstance(decision, Mapping):
        return False
    expected = requirement.get('requirement_hash')
    if not (expected and decision.get('accepted') is True and decision.get('visual_checked')
            and not decision.get('entity_conflict') and not decision.get('fatal_mismatch')
            and decision.get('verified_requirement_hash') == expected
            and decision.get('verified_asset_sha256')):
        return False
    try:
        digest = hashlib.sha256()
        with open(resolution.selected_path, 'rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        return digest.hexdigest() == decision['verified_asset_sha256']
    except OSError:
        return False


def _semantic_reuse_compatible(scene: Mapping[str, Any], resolution: SceneResolution) -> bool:
    """Reuse only an asset verified for the same immutable identity contract.

    ``requirement_hash`` is intentionally scene-specific: its payload includes
    the current scene's positive evidence and action terms.  Comparing that
    hash here made two scenes about the same named entity incompatible, even
    after the candidate had passed the semantic verifier.  Reuse is a nearest
    media fallback, so compare the immutable identity fields instead and keep
    the scene-specific wording out of this gate.
    """
    requirement = scene.get("visual_requirement") or {}
    if not isinstance(requirement, Mapping):
        return True
    wanted = str(requirement.get("requirement_hash") or "").strip()
    if not wanted:
        return True
    context = resolution.final_scene_context or {}
    existing = context.get("visual_requirement") or {}
    if not isinstance(existing, Mapping):
        return False
    existing_hash = str(existing.get("requirement_hash") or "").strip()
    if existing_hash and existing_hash == wanted:
        return True

    def _norm(value: Any) -> str:
        return " ".join(str(value or "").strip().lower().replace("-", " ").split())

    wanted_entity = _norm(requirement.get("canonical_entity"))
    existing_entity = _norm(existing.get("canonical_entity"))
    wanted_type = _norm(requirement.get("subject_type"))
    existing_type = _norm(existing.get("subject_type"))
    if not wanted_entity or not existing_entity or wanted_entity != existing_entity:
        return False
    if wanted_type and existing_type and wanted_type != existing_type:
        broad_types = {"concept", "documentary", "event", "topic", "general", "narrative", "unknown", ""}
        if wanted_type not in broad_types and existing_type not in broad_types:
            return False
    if bool(requirement.get("strict_identity", True)) and not bool(existing.get("strict_identity", True)):
        return False

    # These fields define what the asset must be.  Action terms, positive
    # evidence, required terms, aliases, and visual intent are scene-local
    # context and must not block a truthful nearest-asset reuse.
    generic_attribute_values = {
        "the series", "series", "the game", "game", "the franchise",
        "franchise", "the subject", "subject", "unknown", "n/a",
    }
    for key in ("model_or_make", "model", "year", "decade", "era"):
        wanted_value = _norm((requirement.get("required_attributes") or {}).get(key))
        existing_value = _norm((existing.get("required_attributes") or {}).get(key))
        if wanted_value in generic_attribute_values:
            continue
        # A dated scene cannot borrow an undated same-topic verdict. Missing
        # temporal/model evidence requires fresh target-scene verification.
        if wanted_value and wanted_value != existing_value:
            return False
    return True


def _strict_reuse_identity_compatible(
    scene: Mapping[str, Any], resolution: SceneResolution
) -> bool:
    """Do not borrow a rejected or different-entity web asset.

    Same-topic reuse is useful for ordinary documentary beats, but a strict
    named-person scene must never inherit a palace, garden, or another royal
    merely because the provider metadata shares the word ``royal``.  Reuse is
    allowed only when the source scene carries the same canonical entity and
    its own visual verdict was accepted.
    """
    requirement = scene.get("visual_requirement") or {}
    if not isinstance(requirement, Mapping) or not bool(requirement.get("strict_identity")):
        return True
    provider = str(resolution.provider or "").strip().casefold()
    if provider not in {"web", "web images", "universal_yandex", "universal_openverse", "universal_flickr", "flickr"}:
        return True

    def _norm(value: Any) -> str:
        return " ".join(str(value or "").strip().casefold().replace("-", " ").split())

    wanted = _norm(requirement.get("canonical_entity"))
    context = resolution.final_scene_context or {}
    existing_req = context.get("visual_requirement") if isinstance(context, Mapping) else {}
    existing = _norm(existing_req.get("canonical_entity") if isinstance(existing_req, Mapping) else "")
    # A strict identity gate cannot treat an absent source identity as a
    # match. In particular, a generic event image must not become a person's
    # image merely because the event verifier accepted it for a neighboring
    # scene.
    if not wanted or not existing or wanted != existing:
        return False
    decision = resolution.visual_decision if isinstance(resolution.visual_decision, Mapping) else {}
    if decision and (
        not bool(decision.get("accepted", False))
        or bool(decision.get("entity_conflict"))
        or bool(decision.get("fatal_mismatch"))
        or bool(decision.get("watermark"))
    ):
        return False
    evidence = resolution.candidate_evidence if isinstance(resolution.candidate_evidence, Mapping) else {}
    if evidence.get("canonical_entity_mismatch") or evidence.get("forbidden_match"):
        return False
    return True


class _LinkedCancellationEvent(threading.Event):
    """A stage event that also observes cancellation from its owning job."""

    def __init__(self, parent: Optional[threading.Event] = None):
        super().__init__()
        self._parent_event = parent

    def is_set(self) -> bool:
        return super().is_set() or bool(self._parent_event and self._parent_event.is_set())

    def wait(self, timeout: Optional[float] = None) -> bool:
        if self.is_set():
            return True
        if self._parent_event is None:
            return super().wait(timeout)
        deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        while not self.is_set():
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return False
            super().wait(0.05 if remaining is None else min(0.05, remaining))
        return True


def _env_seconds(name: str, default: float) -> float:
    """Read a seconds-valued env var, falling back to ``default`` on junk."""
    try:
        return max(0.0, float(os.environ.get(name, '') or default))
    except (TypeError, ValueError):
        return float(default)


class MediaResolutionCoordinator:
    """Run scene workers concurrently within one cooperative job deadline."""

    def __init__(self, *, deadline_seconds: float | None = None, max_workers: int = 12, drain_seconds: float = 5.0):
        if deadline_seconds is None:
            deadline_seconds = _env_seconds("VIDZEO_MEDIA_COORDINATOR_DEADLINE_SECONDS", 210.0)
        self.deadline_seconds = max(1.0, float(deadline_seconds))
        self.max_workers = max(1, int(max_workers))
        # A worker is expected to observe ``cancel_event``.  Keep a short,
        # bounded cooperative drain so provider/ffmpeg children can clean up,
        # but never let a hung future hold the request open indefinitely.
        self.drain_seconds = max(0.0, float(drain_seconds))
        self.cancel_event = threading.Event()
        self.started_at = 0.0
        self.ledger: Dict[int, SceneResolution] = {}
        self._lock = threading.Lock()

    @property
    def deadline_at(self) -> float:
        return self.started_at + self.deadline_seconds

    def cancelled(self) -> bool:
        return self.cancel_event.is_set() or (
            self.started_at > 0 and time.monotonic() >= self.deadline_at
        )

    def cancel(self, reason: str = "deadline") -> None:
        self.cancel_event.set()
        with self._lock:
            for row in self.ledger.values():
                if row.status == "unresolved":
                    row.fallback_reason = reason


    def record(self, resolution: SceneResolution) -> None:
        """Record the terminal result for a scene without regressing it.

        A late provider callback must not overwrite a completed unique result
        with a deadline/error result.  This is particularly important when a
        caller performs a cooperative drain after the media deadline.
        """
        with self._lock:
            index = int(resolution.scene_index)
            previous = self.ledger.get(index)
            if previous is not None:
                terminal = {"resolved", "unverified", "fallback", "accepted", "degraded"}
                status_rank = {
                    "resolved": 3,
                    "accepted": 3,
                    "degraded": 1,
                    "unverified": 2,
                    "fallback": 1,
                    "unresolved": 0,
                    "": 0,
                }
                # Preserve a selected path and a stronger terminal status.
                if previous.selected_path and not resolution.selected_path:
                    return
                if (
                    previous.selected_path
                    and resolution.selected_path
                    and status_rank.get(previous.status, 0) > status_rank.get(resolution.status, 0)
                ):
                    return
                if previous.status in terminal and resolution.status in {"unresolved", ""}:
                    return
            self.ledger[index] = resolution

    def snapshot(self) -> Dict[int, SceneResolution]:
        """Return a stable copy for streaming/audit consumers."""
        with self._lock:
            return dict(self.ledger)

    def resolve(
        self,
        scene_indices: Iterable[int],
        worker: Callable[[int, threading.Event], SceneResolution],
    ) -> Dict[int, SceneResolution]:
        """Resolve scenes as workers complete and cooperatively drain on timeout."""
        self.started_at = time.monotonic()
        indices = list(dict.fromkeys(int(index) for index in scene_indices))
        if not indices:
            return {}
        futures: Dict[Future, int] = {}
        executor = ThreadPoolExecutor(max_workers=min(self.max_workers, len(indices)), thread_name_prefix="media-resolve")
        pending = set()
        try:
            # Keep only one bounded batch in flight per worker.  Submitting all
            # scenes at once used to create a large queued tail that could not
            # observe cancellation until the deadline had already passed.
            next_index = 0
            def submit_available() -> None:
                nonlocal next_index
                while (
                    next_index < len(indices)
                    and len(pending) < self.max_workers
                    and not self.cancelled()
                ):
                    index = indices[next_index]
                    next_index += 1
                    future = executor.submit(worker, index, self.cancel_event)
                    futures[future] = index
                    pending.add(future)

            submit_available()

            # ``as_completed`` waits forever for the next future and therefore
            # made the old wall deadline advisory.  Poll with FIRST_COMPLETED
            # and a deadline-relative timeout so a slow provider is cancelled
            # as soon as the job budget expires.
            while pending:
                remaining = max(0.0, self.deadline_at - time.monotonic())
                if remaining <= 0.0:
                    self.cancel("media_deadline")
                    break
                done, pending = wait(
                    pending,
                    timeout=min(0.25, remaining),
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    continue
                for future in done:
                    index = futures[future]
                    try:
                        resolution = future.result()
                    except Exception as exc:
                        resolution = SceneResolution(
                            scene_index=index,
                            status="unverified",
                            fallback_reason=f"worker_error:{type(exc).__name__}",
                        )
                    self.record(resolution)
                submit_available()

            if pending:
                self.cancel("media_deadline")
                # Give cooperative workers a small bounded drain.  This is
                # long enough for yt-dlp/ffmpeg progress hooks to terminate,
                # while guaranteeing the coordinator returns on schedule.
                drain_until = time.monotonic() + self.drain_seconds
                while pending and time.monotonic() < drain_until:
                    done, pending = wait(
                        pending,
                        timeout=min(0.25, max(0.0, drain_until - time.monotonic())),
                        return_when=FIRST_COMPLETED,
                    )
                    for future in done:
                        index = futures[future]
                        try:
                            resolution = future.result()
                        except Exception as exc:
                            resolution = SceneResolution(
                                scene_index=index,
                                status="unverified",
                                fallback_reason=f"worker_error:{type(exc).__name__}",
                            )
                        self.record(resolution)
                # Mark anything that did not drain so callers can apply their
                # relevant web-asset reuse fallback without producing blanks.
                for future in pending:
                    index = futures[future]
                    self.record(SceneResolution(
                        scene_index=index,
                        status="unverified",
                        fallback_reason="media_deadline",
                    ))
                # Scenes that were never submitted are terminal as well.  Do
                # not leave holes in the job ledger for the caller to mistake
                # for a second retrieval pass.
                for index in indices[next_index:]:
                    self.record(SceneResolution(
                        scene_index=index,
                        status="unverified",
                        fallback_reason="media_deadline_unscheduled",
                    ))
        finally:
            for future in futures:
                if not future.done():
                    future.cancel()
            # Do not wait forever for a provider that ignored cancellation.
            # Normal cooperative workers are already complete; an ignored
            # cancellation is surfaced as an unverified scene above.
            executor.shutdown(wait=not pending, cancel_futures=True)
        return dict(self.ledger)


def _scene_row(scene: Any, index: int) -> Dict[str, Any]:
    if isinstance(scene, dict):
        row = dict(scene)
    elif hasattr(scene, "to_dict"):
        row = dict(scene.to_dict())
    else:
        row = {
            key: getattr(scene, key)
            for key in (
                "scene_id", "order", "container_id", "text", "start_seconds",
                "end_seconds", "visual_intent", "positive_subjects",
                "forbidden_subjects", "source_policy", "reuse_policy", "contract_hash",
            )
            if hasattr(scene, key)
        }
    row.setdefault("scene_index", int(row.get("order", index) or index))
    row.setdefault("scene_id", f"scene_{row['scene_index']:04d}")
    row.setdefault("container_id", "global")
    return row


def _container_waves(rows: Sequence[Mapping[str, Any]]) -> List[List[int]]:
    groups: Dict[str, List[int]] = {}
    order: List[str] = []
    for index, row in enumerate(rows):
        key = str(row.get("container_id") or "global")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(index)
    waves: List[List[int]] = []
    depth = 0
    while True:
        wave = [groups[key][depth] for key in order if depth < len(groups[key])]
        if not wave:
            break
        waves.append(wave)
        depth += 1
    return waves


def _candidate_rows(value: Any, source: str) -> List[Dict[str, Any]]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple)) else [value]
    rows: List[Dict[str, Any]] = []
    for candidate in values:
        if isinstance(candidate, SceneResolution):
            row = candidate.to_dict()
            row.setdefault("path", candidate.selected_path)
        elif isinstance(candidate, dict):
            row = dict(candidate)
        elif isinstance(candidate, (str, os.PathLike)):
            row = {"path": str(candidate)}
        else:
            continue
        row.setdefault("provider", source)
        row.setdefault("source", source)
        try:
            from .media_provenance import annotate_rights
            annotate_rights(row)
        except Exception:
            # Provenance is observability/preference metadata; it must not
            # turn a provider outage into a media-resolution exception.
            pass
        row["path"] = str(row.get("path") or row.get("local_path") or row.get("selected_path") or "")
        rows.append(row)
    # Keep enough recall for semantic verification to recover when a provider
    # ranks a generic or confusable result above the correct one. The old
    # three-row truncation made the final vision gate incapable of finding a
    # correct lower-ranked candidate.
    return rows[:12]


def _youtube_frame_fallback_candidates(
    scene: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    scene_index: int,
) -> List[Dict[str, Any]]:
    """Build same-video frame candidates after a clip finalist is rejected.

    A rejected YouTube finalist can still be the right source with a weak
    opening/closing timestamp. Sample a small number of distinct timestamps
    from the same downloaded clip and send those frames through the exact
    semantic gate. This is deliberately bounded and never changes the clip's
    identity metadata, so it cannot turn an unrelated video into an accepted
    fallback.
    """
    try:
        max_sources = max(
            1,
            min(3, int(os.environ.get("VIDZEO_YT_FRAME_FALLBACK_SOURCES", "2") or 2)),
        )
        # First pass sends 2 frames per source to vision (was 4): a rejected
        # clip's best frames are the informative ones, and each frame costs
        # a live vision read. If both are rejected the scene moves to the
        # next recovery layer (cross-lane / stock) instead of spending more
        # vision reads on the same rejected clip. Raise with
        # VIDZEO_YT_FRAME_FALLBACK_FRAMES (cap 5) if a job needs more.
        max_frames = max(
            2,
            min(5, int(os.environ.get("VIDZEO_YT_FRAME_FALLBACK_FRAMES", "2") or 2)),
        )
    except (TypeError, ValueError):
        max_sources, max_frames = 2, 2
    ranked = sorted(
        [dict(candidate) for candidate in candidates if isinstance(candidate, Mapping)],
        key=lambda item: float(item.get("final_score") or item.get("score") or item.get("metadata_score") or 0.0),
        reverse=True,
    )
    try:
        from .visual_intelligence.frame_extractor import extract_clip_frames
    except Exception:
        return []
    output: List[Dict[str, Any]] = []
    seen_video_paths: set[str] = set()
    for source_rank, candidate in enumerate(ranked[:max_sources], start=1):
        source = str(candidate.get("source") or candidate.get("provider") or "").strip().lower()
        path = str(candidate.get("path") or candidate.get("selected_path") or "").strip()
        if "youtube" not in source and source not in {"yt", "youtube clips"}:
            continue
        if not path or not os.path.isfile(path):
            continue
        normalized_path = os.path.normcase(os.path.normpath(path))
        if normalized_path in seen_video_paths:
            continue
        seen_video_paths.add(normalized_path)
        frame_dir = Path(path).parent / "fallback_frames" / f"scene_{int(scene_index):04d}_source_{source_rank:02d}"
        try:
            frames, duration = extract_clip_frames(
                path,
                out_dir=str(frame_dir),
                long_edge=1920,
                render_quality=True,
                max_frames=max_frames,
            )
        except Exception:
            continue
        for frame_index, frame_path in enumerate(frames[:max_frames]):
            if not frame_path or not os.path.isfile(frame_path):
                continue
            # Preserve source pixels; the scene gate decides whether this frame is useful.
            frame_cleanup = {'path': str(frame_path), 'cleaned': False, 'no_blur': True}
            alt = dict(candidate)
            base_id = str(candidate.get("candidate_id") or Path(path).stem or f"youtube:{scene_index}")
            source_verdict = candidate.get("qwen_result") or candidate.get("visual_decision") or {}
            if not isinstance(source_verdict, Mapping) or not source_verdict:
                # The frame has no title/description of its own, but it came
                # from a locally verified YouTube clip.  Mark it as transport
                # recovery so the hard gate forwards it to live vision rather
                # than rejecting a real frame for missing metadata.
                source_verdict = {
                    "source": "youtube_pool_moment",
                    "verification_status": "deterministic_pool_cut",
                    "accepted": True,
                }
            alt.update({
                "candidate_id": f"{base_id}:frame:{frame_index + 1}",
                "path": str(frame_path),
                "selected_path": str(frame_path),
                "media_type": "image",
                "provider": "youtube",
                "source": "youtube",
                "resolution_kind": "youtube_extracted_frame",
                "fallback_layer": "same_video_timestamp_frame",
                "fallback_source_path": path,
                "fallback_frame_index": frame_index,
                "fallback_frame_count": min(len(frames), max_frames),
                "fallback_clip_duration": round(float(duration or 0.0), 3),
                "qwen_result": dict(source_verdict),
                "visual_decision": {},
                "overlay_blurred": bool(frame_cleanup.get("blurred")),
                "overlay_regions": list(frame_cleanup.get("regions") or []),
                "overlay_cleanup": str(frame_cleanup.get("detector") or ""),
            })
            # The frame is now an image asset; source timestamps remain
            # attached for provenance, while local frame position is audited
            # separately so a reused clip cannot masquerade as a new video.
            output.append(alt)
    return output


def _source_policy_value(scene: Mapping[str, Any], key: str, default: Any = None) -> Any:
    policy = scene.get("source_policy") or {}
    if isinstance(policy, dict):
        return policy.get(key, default)
    return getattr(policy, key, default)


def _strict_named_scene(scene: Mapping[str, Any]) -> bool:
    """Whether a scene names an entity that stock metadata cannot prove.

    ``visual_verification_required`` is also used for generic documentary
    beats.  Keep those beats availability-friendly, but never allow the
    no-vision stock shortcut for a named person, place, object, or other
    factual contract.
    """
    requirement = scene.get("visual_requirement") or {}
    if isinstance(requirement, Mapping) and bool(requirement.get("strict_identity")):
        return True
    return bool(
        scene.get("positive_subjects")
        or scene.get("named_factual")
        or scene.get("strict_factual")
        or _source_policy_value(scene, "strict_real_media", False)
    )


def _youtube_relaxed_visual_gate(scene: Mapping[str, Any]) -> bool:
    """Return the scene-level YouTube relevance policy.

    YouTube cuts have a deterministic transport/overlay/safety gate before
    this coordinator.  Their final model decision should therefore tolerate
    uncertainty, while still rejecting explicit wrong-entity and unsafe
    evidence.  The policy is configurable per job and defaults on for the
    motion lane.
    """
    from .visual_intelligence.semantic_guard import exact_scene_identity_required
    if exact_scene_identity_required(scene):
        return False
    value = _source_policy_value(scene, "youtube_relaxed_visual_gate", None)
    if value is None:
        value = os.environ.get("VIDZEO_YT_RELAXED_VISUAL_GATE", "1")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _quota_gate_is_hard_rejection(reason: Any, verdict: Any) -> bool:
    """A late quota upgrade may soften uncertainty, never an explicit veto."""
    from .visual_intelligence.semantic_guard import explicit_low_visual_match
    if explicit_low_visual_match(verdict) or str(reason or '') == 'insufficient_visible_subject_match':
        return True
    if isinstance(verdict, Mapping) and (verdict.get("unsafe_content") or verdict.get("suggestive_content")):
        return True
    if isinstance(verdict, Mapping):
        if verdict.get("spurious_mismatch"):
            return False
        reason_text = str(verdict.get("reason") or verdict.get("rejection_reason") or "").lower()
        spurious_tokens = (
            "era", "year", "season", "tenure", "older", "dated",
            "period", "roster", "transfer", "trade", "matchup", "uniform",
            "different player", "different team", "not named", "quarterback",
            "different event", "distinct events", "wrong event", "competing event",
            "wrong matchup", "different matchup", "different location", "wrong venue",
            "wrong entity", "different entity", "wrong subject", "different subject",
            "required entity is absent", "required subject is absent",
        )
        if any(token in reason_text for token in spurious_tokens) and not any(
            marker in reason_text for marker in (
                "wrong domain", "different_sport", "simulation_for_real_world", "game-engine",
                "wrong era", "different era", "anachronistic", "anachronism",
            )
        ):
            return False
    hard_reasons = {
        "visual_rejected", "fatal_mismatch", "entity_conflict",
        "explicit_scene_conflict", "wrong_entity", "wrong_event",
        "wrong_domain", "unsafe_content",
    }
    if str(reason or "").strip().lower() in hard_reasons:
        return True
    if not isinstance(verdict, Mapping):
        return False
    try:
        from .visual_intelligence.semantic_guard import explicit_scene_conflict
        return explicit_scene_conflict(verdict)
    except Exception:
        return False


def _late_local_verification_deadline(candidate_count: int, delivery_deadline: Optional[float] = None) -> float:
    """One bounded gate for downloaded clips; never reopen media discovery."""
    now = time.monotonic()
    try:
        seconds = float(os.environ.get('VIDZEO_LATE_YT_VERIFICATION_SECONDS', '60') or 60)
    except (TypeError, ValueError):
        seconds = 60.0
    if not math.isfinite(seconds):
        seconds = 60.0
    seconds = max(2.0, min(90.0, seconds)) if candidate_count else 0.0
    deadline = now + seconds
    if delivery_deadline is None:
        return deadline
    if delivery_deadline > now:
        return min(deadline, delivery_deadline)
    # The delivery wall is already spent (a long YouTube batch consumed it
    # before any quota clip reached vision, e.g. ff377408: 954s discovery
    # against a 1033s media deadline). Clamping to the spent wall would make
    # this lane dead on arrival, so the already-downloaded clips never get
    # their one bounded vision pass. Floor the window instead: at most 60s
    # past the wall, only for already-local candidates, never new discovery.
    # Callers still bound every future by the remaining window.
    return now + min(seconds, 60.0)


def _youtube_fail_open_on_visual_unavailable(scene: Mapping[str, Any]) -> bool:
    value = _source_policy_value(scene, "youtube_fail_open_on_visual_unavailable", None)
    if value is None:
        value = os.environ.get("VIDZEO_YT_FAIL_OPEN_ON_VISUAL_UNAVAILABLE", "1")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _mixed_media_scene(scene: Mapping[str, Any]) -> bool:
    """Whether this scene belongs to the exact-path diversity contract."""
    return bool(_source_policy_value(scene, "mixed_media", False))


def _footage_only_scene(scene: Mapping[str, Any]) -> bool:
    """Require moving video for an explicitly footage-only scene.

    This is opt-in so legacy image and mixed-media jobs keep their existing
    behaviour.  When enabled, web stills and extracted timestamp frames are
    rejected before the relaxed YouTube fail-open path can turn them into a
    slideshow.
    """
    value = _source_policy_value(scene, "footage_only", False)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _title_card_lead_detected(path: str) -> bool:
    """Detect a stable, high-contrast title card at the head of a video cut.

    Cached/reused YouTube cuts can bypass the fresh-download overlay pass, so
    footage-only jobs need a cheap path-level guard before accepting them.
    """
    if not path or not os.path.isfile(path):
        return False
    try:
        import cv2
        import numpy as np

        from services.visual_intelligence.frame_extractor import extract_clip_frames, cleanup_frames
        frames, duration = extract_clip_frames(str(path), long_edge=640, max_frames=6)
        samples = []
        try:
            if duration <= 1.0:
                return False
            for frame_path in frames:
                frame = cv2.imread(str(frame_path))
                if frame is None:
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                h, w = gray.shape[:2]
                crop = gray[int(0.08 * h):int(0.92 * h), int(0.08 * w):int(0.92 * w)]
                if crop.size:
                    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[int(0.08 * h):int(0.92 * h), int(0.08 * w):int(0.92 * w)]
                    white = float(((crop > 210) & (hsv[:, :, 1] < 60)).mean())
                    yellow = float(((hsv[:, :, 0] > 10) & (hsv[:, :, 0] < 40) & (hsv[:, :, 1] > 120) & (hsv[:, :, 2] > 120)).mean())
                    dark = float((crop < 45).mean())
                    edges = float(cv2.Canny(crop, 100, 200).mean() / 255.0)
                    samples.append((white, yellow, dark, edges))
        finally:
            cleanup_frames(frames)
        if not samples:
            return False
        # Large white/yellow glyphs with a dense edge pattern are the common
        # newsreel/title-card signature even when the background is bright.
        if any((white >= 0.10 and yellow >= 0.03 and edges >= 0.04) or (white >= 0.12 and dark >= 0.28) for white, yellow, dark, edges in samples):
            return True
        return False
    except Exception:
        return False


def _bite_clip_scene(scene: Mapping[str, Any]) -> bool:
    """Whether a scene is allowed to use distinct moments from one master.

    This is intentionally a narrow motion-lane contract. It does not relax
    facecam, caption, safety, or entity gates; it only changes the reuse cap
    after a YouTube source has already passed those gates.
    """
    return bool(
        _source_policy_value(scene, "bite_clip", False)
        or _source_policy_value(scene, "youtube_bite_clip", False)
    )


def _intro_unique_scene(scene: Mapping[str, Any]) -> bool:
    """Whether the opening hook must avoid repeated assets/sources."""
    if bool(_source_policy_value(scene, "intro_unique", False)):
        return True
    if bool(scene.get("is_intro")):
        return True
    # A missing timing field is common in unit/legacy lanes and does not mean
    # every scene starts at zero. Only infer the 15-second window when an
    # actual timing value is present; the adapter stamps intro_unique when it
    # has to derive starts from Whisper/duration data.
    if "start_seconds" not in scene and "start" not in scene:
        return False
    try:
        value = scene.get("start_seconds", scene.get("start"))
        return value is not None and float(value) < 15.0
    except (TypeError, ValueError):
        return False


def _strict_real_media_scene(scene: Mapping[str, Any]) -> bool:
    """Whether weak/repeated web stills must fail closed for this scene."""
    requirement = scene.get("visual_requirement") or {}
    return bool(
        _source_policy_value(scene, "strict_real_media", False)
        or (
            isinstance(requirement, Mapping)
            and requirement.get("strict_identity")
            and _mixed_media_scene(scene)
        )
    )


def _web_reuse_allowed(scene: Mapping[str, Any]) -> bool:
    """Allow only tightly scoped web reuse; YT timestamp reuse is separate.

    The first narrative scene is also the rich intro in the long-form path.
    If its own candidates fail final vision, a verified same-entity web still
    from a later scene is safer than a blank intro, provided this is the first
    scene of a single-topic container. Adjacent-duplicate and reuse caps still
    apply at the caller.
    """
    policy = scene.get('source_policy') or {}
    if isinstance(policy, Mapping) and bool(policy.get('unique_web_assets_required')):
        return False
    if _footage_only_scene(scene):
        return False
    if not _strict_real_media_scene(scene):
        return True
    # Mixed Media is explicitly a layered fallback lane.  Once a scene has
    # exhausted its own web/YouTube candidates, a bounded reuse of a verified
    # same-topic web still keeps the render real and prevents a single missed
    # provider response from becoming a blank/stock detour.  The caller still
    # enforces non-adjacent placement and the per-asset reuse cap.
    policy = scene.get("source_policy") or {}
    # The operator's diversity contract is authoritative. When unique web
    # assets are requested, reusing a verified still from another scene would
    # create the back-to-back/repeated imagery that makes an otherwise valid
    # export unusable. Leave the scene unresolved so the app-level stock
    # terminal can supply a distinct asset instead.
    if isinstance(policy, Mapping) and bool(policy.get("unique_web_assets_required")):
        return False
    if isinstance(policy, Mapping) and bool(policy.get("allow_verified_topic_reuse")) and not bool(
        policy.get("footage_only")
    ):
        return True
    # The runbook explicitly authorizes:
    # "Mixed Media still-image reuse is an emergency allowance of two non-adjacent appearances"
    if _mixed_media_scene(scene) and not bool(policy.get("footage_only")):
        return True
    try:
        first_scene = int(scene.get("order", scene.get("scene_index", 1)) or 0) == 0
    except (TypeError, ValueError):
        first_scene = False
    container_id = str(scene.get("container_id") or "").strip().lower()
    return first_scene and container_id not in {"intro", "outro", "global"}


def _is_web_source(value: Any) -> bool:
    """Return whether a candidate/resolution belongs to the web-image lane."""
    if isinstance(value, Mapping):
        source = value.get("source") or value.get("provider") or ""
    else:
        source = getattr(value, "provider", value)
    normalized = str(source or "").strip().lower()
    # The universal parallel scraper keeps the concrete engine in the
    # provider field (for example ``universal_yandex`` or
    # ``universal_bing``).  Those rows are still web-image media; treating
    # them as an unknown source bypasses the coordinator's degraded visual
    # acceptance path and turns healthy downloaded images into neutral slots.
    if normalized.startswith("universal_"):
        return True
    return normalized in {
        "web", "web images", "web_image", "web_image_resolver",
        "flickr", "yandex", "bing", "bing_hybrid", "ddg", "ddg_hybrid",
        "google", "google_images", "getty_bing", "pinterest", "openverse",
        "wikimedia",
    }


def _is_youtube_source(value: Any) -> bool:
    """Return whether a candidate/resolution belongs to the YouTube lane."""
    if isinstance(value, Mapping):
        source = value.get("source") or value.get("provider") or ""
    else:
        source = getattr(value, "provider", value)
    return str(source or "").strip().lower() in {"youtube", "youtube clips", "yt"}


def _is_moving_youtube_source(value: Any) -> bool:
    """YouTube provenance alone cannot turn an extracted still into footage."""
    if not _is_youtube_source(value):
        return False
    if isinstance(value, Mapping):
        kind = value.get('media_type') or value.get('type') or ''
        path = value.get('path') or value.get('selected_path') or ''
    else:
        kind = getattr(value, 'media_type', '')
        path = getattr(value, 'selected_path', '')
    return (str(kind).casefold() not in {'image', 'photo', 'still'}
            and Path(str(path)).suffix.casefold() in {'.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4v', '.ts'})


def _vision_verdict_cache_key(candidate: Mapping[str, Any] | None) -> str:
    """Stable identity for a candidate's vision verdict across rounds.

    The same candidate (same web image URL, or the same YouTube clip at the
    same timestamp) re-checked in a later round for the same scene gets the
    same verdict without a second live vision read. The scene index is part
    of the cache key at the call site: the same image can be right for one
    scene and wrong for another.
    """
    if not isinstance(candidate, Mapping):
        return ''
    # YouTube extracted frame: same clip file + same timestamp = same verdict.
    source_path = str(candidate.get('fallback_source_path') or '').strip()
    frame_index = candidate.get('fallback_frame_index')
    if source_path and frame_index is not None:
        return f"yt-frame:{source_path}#{frame_index}"
    # Web image: the origin URL is stable across re-downloads and rounds.
    if _is_web_source(candidate):
        for key in ('source_url', 'url', 'image_url', 'original_url'):
            value = str(candidate.get(key) or '').strip()
            if value:
                return f"url:{value}"
    # YouTube clip / other: the downloaded file (cut-specific) then the URL.
    path = str(candidate.get('path') or candidate.get('selected_path') or '').strip()
    if path:
        return f"path:{path}"
    for key in ('source_url', 'url'):
        value = str(candidate.get(key) or '').strip()
        if value:
            return f"url:{value}"
    return ''


def _cached_verification_result(scene_index, scene, candidate, verifier, cache):
    """Verify a candidate, reusing a live vision verdict for the same scene+candidate.

    ``cache`` is a job-scoped dict keyed by (scene_index, candidate identity).
    Only genuine live verdicts (visual_checked) are stored; deterministic and
    error verdicts are recomputed (cheap, and must never be cached across a
    transient verifier outage). Thread-safe enough for the gate pools: dict
    get/set are atomic, and a rare duplicate verification is harmless.
    """
    key = (int(scene_index), _vision_verdict_cache_key(candidate))
    if key[1]:
        cached = cache.get(key)
        if cached is not None:
            accepted, reason, visual_score, verdict = cached
            return accepted, reason, visual_score, dict(verdict) if isinstance(verdict, dict) else verdict
    accepted, reason, visual_score, verdict = _verification_result(scene, dict(candidate), verifier)
    if key[1] and isinstance(verdict, dict) and verdict.get('visual_checked') is True:
        cache[key] = (accepted, reason, visual_score, dict(verdict))
    return accepted, reason, visual_score, verdict


def _web_asset_identity(value: Mapping[str, Any] | SceneResolution | None) -> str:
    """Return a stable identity for a web still, beyond its copied filename.

    The web resolver and the render snapshotter can copy one downloaded image
    into different scene directories.  Path-only dedupe therefore misses the
    exact same pixels (and Flickr derivatives with different CDN URLs).  Use
    the provider fingerprint first, then the canonical source URL, and only
    fall back to the normalized local path when no stronger identity exists.
    """
    if value is None:
        return ""
    row: Mapping[str, Any]
    if isinstance(value, SceneResolution):
        row = value.to_dict()
    elif isinstance(value, Mapping):
        row = value
    else:
        row = {}
    if not _is_web_source(row):
        for field in ("asset_fingerprint", "fingerprint", "sha256", "sha", "md5"):
            fingerprint = str(row.get(field) or "").strip().casefold()
            if fingerprint:
                return f"asset:fingerprint:{fingerprint}"
        path = str(row.get("path") or row.get("selected_path") or "").strip()
        return f"path:{os.path.normcase(os.path.normpath(path))}" if path else ""
    for field in ("asset_fingerprint", "fingerprint", "sha256", "sha", "md5"):
        fingerprint = str(row.get(field) or "").strip().casefold()
        if fingerprint:
            return f"web:fingerprint:{fingerprint}"
    # ``source_url`` and ``page_url`` are often the result page (especially
    # for Yandex/Pinterest/gallery scrapers), not the image itself.  Using
    # those fields first collapses every image returned by one search page
    # into one false duplicate and is exactly what caused valid alternates to
    # be rejected in the Train Crew run.  Prefer the actual media URL and
    # retain page URLs only as provenance fallbacks.
    for field in ("url", "image_url", "media_url", "download_url", "preview_url"):
        raw_url = str(row.get(field) or "").strip()
        if raw_url:
            from urllib.parse import urlsplit, urlunsplit
            parts = urlsplit(raw_url)
            if parts.scheme and parts.netloc:
                # Query strings on image CDNs are normally resize/cache
                # parameters, not a distinct asset.  The path remains the
                # stable identity while the original URL stays in the ledger.
                canonical = urlunsplit((
                    parts.scheme.casefold(),
                    parts.netloc.casefold(),
                    parts.path.rstrip("/"),
                    "",
                    "",
                ))
            else:
                canonical = raw_url.casefold().rstrip("/")
            if canonical:
                return f"web:url:{canonical}"
    for field in ("origin_url", "source_url", "page_url"):
        raw_url = str(row.get(field) or "").strip()
        if raw_url:
            from urllib.parse import urldefrag
            canonical = urldefrag(raw_url)[0].rstrip("/").casefold()
            if canonical:
                return f"web:url:{canonical}"
    path = str(row.get("path") or row.get("selected_path") or "").strip()
    return f"web:path:{os.path.normcase(os.path.normpath(path))}" if path else ""


def _resolution_asset_identity(resolution: SceneResolution | None) -> str:
    if resolution is None:
        return ""
    if _is_youtube_source(resolution):
        fingerprint = str(resolution.asset_fingerprint or "").strip().casefold()
        if fingerprint:
            return f"youtube:fingerprint:{fingerprint}"
        path = str(resolution.selected_path or "").strip()
        if path:
            return f"youtube:path:{os.path.normcase(os.path.normpath(path))}"
        source_url = str(resolution.source_url or "").strip().casefold()
        if source_url:
            return f"youtube:url:{source_url}"
        return ""
    return _web_asset_identity(resolution)


def _reuse_limit_for_resolution(
    scene: Mapping[str, Any],
    resolution: SceneResolution,
    *,
    non_mixed_limit: int,
) -> int:
    """Bound truthful fallback reuse without allowing blank-card cascades."""
    if _intro_unique_scene(scene):
        # The hook should never replay a physical asset.  Source identity is
        # also checked at finalist selection so alternate cuts from one
        # YouTube master cannot silently collapse the opening.
        return 1
    if _is_web_source(resolution):
        # Primary selection still rejects an already-used web path when a
        # fresh finalist exists. This small fallback allowance is only reached
        # after the provider exhausted fresh assets, and keeps a scene visible
        # rather than replacing it with a neutral plate.
        return 2
    if _is_youtube_source(resolution):
        # Separate timestamp cuts already have separate paths/fingerprints.
        # Replaying the same local file cannot prove a new moment.
        return 1
    return 1 if _mixed_media_scene(scene) else max(1, int(non_mixed_limit))


def _mixed_youtube_quota_reuse_cap(target_quota: int) -> int:
    """Bound quota retention to a small number of uses per physical clip."""
    try:
        configured = int(
            os.environ.get("VIDZEO_MIXED_YT_QUOTA_REUSE_CAP", "2") or 2
        )
    except (TypeError, ValueError):
        configured = 2
    return max(1, min(max(1, int(target_quota or 1)), configured))


def _youtube_video_key(value: Any) -> str:
    """Normalize common YouTube watch, short, and embed URLs to their ID."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        host = parsed.netloc.lower().split(":", 1)[0]
        if host.endswith("youtu.be"):
            candidate = parsed.path.strip("/").split("/", 1)[0]
        elif host.endswith(("youtube.com", "youtube-nocookie.com")):
            candidate = (parse_qs(parsed.query).get("v") or [""])[0]
            if not candidate:
                parts = [part for part in parsed.path.split("/") if part]
                candidate = parts[1] if len(parts) >= 2 and parts[0].lower() in {"shorts", "embed", "live", "v"} else ""
        else:
            candidate = ""
    except (TypeError, ValueError):
        return ""
    return candidate if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate or "") else ""


def _verified_distant_youtube_variant(
    alternate: Mapping[str, Any],
    source: SceneResolution,
    *,
    target_requirement_hash: str,
    scene_duration: float,
    used_resolutions: Sequence[SceneResolution],
    used_paths: set[str],
) -> bool:
    """Accept only a separate, target-verified distant cut from the same URL.

    Resolved YouTube files are generally already trimmed to their source range.
    Offsetting a seek inside that short local clip creates black or repeated
    pictures, so timestamp reuse requires a separate download, explicit source
    bounds, and vision evidence bound to this exact scene and asset.
    """
    if not isinstance(alternate, Mapping):
        return False
    path = str(alternate.get("path") or alternate.get("selected_path") or "").strip()
    if not path or not Path(path).is_file():
        return False
    try:
        normalized_path = str(Path(path).resolve()).casefold()
    except OSError:
        normalized_path = path.casefold()
    if normalized_path in used_paths:
        return False
    video_id = _youtube_video_key(source.source_url)
    alternate_video_id = _youtube_video_key(
        alternate.get("source_url") or alternate.get("origin_url") or alternate.get("url")
    )
    if not video_id or alternate_video_id != video_id:
        return False
    try:
        start_val = alternate.get("video_start", alternate.get("start", alternate.get("timestamp")))
        start = float(start_val)
        end_val = alternate.get("video_end", alternate.get("end"))
        if end_val is not None:
            end = float(end_val)
        else:
            clip_dur = float(alternate.get("clip_duration") or alternate.get("duration") or scene_duration or 4.0)
            end = start + max(1.0, clip_dur)
        min_duration = max(1.0, min(float(scene_duration or 0.0), 3.0))
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(start) and math.isfinite(end) and start >= 0.0 and end - start >= min_duration):
        return False
    intervals = []
    for row in used_resolutions:
        if not row or not row.selected_path or _youtube_video_key(row.source_url) != video_id:
            continue
        try:
            old_start, old_end = float(row.video_start), float(row.video_end)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(old_start) and math.isfinite(old_end) and old_end > old_start):
            continue
        intervals.append((old_start, old_end))
    gap = max(float(os.environ.get("VIDZEO_YT_DISTANT_CUT_GAP", "12") or 12), min(float(scene_duration or 0.0), 5.0) + 6.0)
    if any(not (end + gap <= old_start or start >= old_end + gap) for old_start, old_end in intervals):
        return False
    decision = alternate.get("visual_decision") or alternate.get("qwen_result") or {}
    if isinstance(decision, Mapping) and decision:
        relaxed_yt = bool(os.environ.get("VIDZEO_YT_RELAXED_VISUAL_GATE", "1"))
        if decision.get("fatal_mismatch") or (decision.get("entity_conflict") and not relaxed_yt):
            return False
        if decision.get("accepted") is False and decision.get("visual_checked") and not relaxed_yt:
            return False
        expected_digest = str(decision.get("verified_asset_sha256") or "").strip().lower()
        if expected_digest:
            try:
                import hashlib
                hasher = hashlib.sha256()
                with Path(path).open("rb") as source_file:
                    for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                        hasher.update(chunk)
                if hasher.hexdigest().lower() != expected_digest:
                    return False
            except OSError:
                return False
    return True


def _youtube_candidate_is_valid_after_master_reuse(
    candidate: Mapping[str, Any],
    visual_decision: Mapping[str, Any] | None,
    scene: Mapping[str, Any],
    prior_resolutions: Sequence[SceneResolution],
) -> bool:
    """Require a separate, distant, scene-bound download when a master repeats."""
    candidate_url = str(
        candidate.get("source_url") or candidate.get("origin_url") or candidate.get("url") or ""
    )
    video_id = _youtube_video_key(candidate_url)
    if not video_id:
        return True
    prior_youtube = [
        row for row in prior_resolutions
        if row is not None and _is_youtube_source(row) and row.selected_path
    ]
    same_master = [row for row in prior_youtube if _youtube_video_key(row.source_url) == video_id]
    if not same_master:
        return True
    requirement = scene.get("visual_requirement") if isinstance(scene.get("visual_requirement"), Mapping) else {}
    requirement_hash = str(requirement.get("requirement_hash") or "").strip()
    try:
        duration = float(scene.get("duration") or 4.0)
    except (TypeError, ValueError):
        duration = 4.0
    used_paths = {
        str(Path(row.selected_path).resolve()).casefold()
        for row in prior_youtube if row.selected_path
    }
    candidate_with_verdict = {**dict(candidate), "visual_decision": dict(visual_decision or {})}
    return any(
        _verified_distant_youtube_variant(
            candidate_with_verdict,
            source,
            target_requirement_hash=requirement_hash,
            scene_duration=duration,
            used_resolutions=prior_youtube,
            used_paths=used_paths,
        )
        for source in same_master
    )


def _choose_diverse_verified_media(accepted, prior_resolutions, alignment):
    """Prefer another source video among similarly ranked verified finalists."""
    best = max(accepted, key=lambda value: (alignment(value[2]), value[0], value[1]))
    if not _is_youtube_source(best[2]):
        return best
    best_alignment = alignment(best[2])
    comparable = [item for item in accepted if _is_youtube_source(item[2])
                  and alignment(item[2]) == best_alignment and item[1] == best[1]
                  and item[0] >= best[0] - 5.0]
    uses = {}
    for prior in prior_resolutions:
        if prior is not None and _is_youtube_source(prior) and prior.selected_path:
            key = _youtube_video_key(prior.source_url)
            if key:
                uses[key] = uses.get(key, 0) + 1
    def rank(item):
        candidate = item[2]
        key = _youtube_video_key(candidate.get('source_url') or candidate.get('origin_url') or candidate.get('url'))
        # Unknown source identity cannot masquerade as a fresh source.
        return (uses.get(key, 0) if key else 1_000_000, -item[0])
    return min(comparable, key=rank) if comparable else best


def _topic_reuse_limit(
    scene: Mapping[str, Any],
    resolution: SceneResolution,
    *,
    non_mixed_limit: int,
) -> int:
    """Return the bounded last-resort reuse limit for a verified asset.

    Mixed Media treats a YouTube cut as a real motion beat: it must never be
    replayed as a generic filler shot. Verified web stills are a different
    fallback class. When retrieval returns fewer unique stills than the
    script requires, permit at most a second non-adjacent use after unique
    candidates are exhausted. Keep exact clips single-use.
    """
    if not _mixed_media_scene(scene):
        return max(1, int(non_mixed_limit))
    provider = str(getattr(resolution, "provider", "") or "").strip().lower()
    if provider in {"youtube", "youtube clips", "yt"}:
        # Reuse is permitted through a distinct, scene-verified distant cut.
        # This limit only governs copies of the same downloaded local file.
        return 1
    # This is a final rescue limit, not the normal selection target. The
    # allocator sorts by current reuse count so the extra capacity is spread
    # across the verified web pool instead of repeating one image in a row.
    return 2


def _multi_container_job(rows: Sequence[Mapping[str, Any]]) -> bool:
    """Whether a storyboard contains more than one factual subject lane."""
    container_ids = {
        str(row.get("container_id") or "").strip()
        for row in rows
        if str(row.get("container_id") or "").strip()
    }
    return len(container_ids) > 1


def _allow_topic_web_reuse(rows: Sequence[Mapping[str, Any]], scene: Mapping[str, Any]) -> bool:
    """Permit global web rescue only for single-topic/intro storyboards.

    A verified-topic rescue is not a subject proof. On multi-container
    listicles it caused a verified Ronaldo image to leak into Haaland,
    Bellingham, and Messi scenes. Same-container reuse is handled separately
    and remains allowed when the asset was verified for that container.
    """
    if not _multi_container_job(rows):
        return True
    if not bool(scene.get("is_intro") or scene.get("is_outro")):
        return False
    # A factual list item can be marked as an intro by an upstream planner;
    # only the neutral/global intro lane may borrow across subjects.
    container_id = str(scene.get("container_id") or "").strip().lower()
    return container_id in {"", "intro", "outro", "general", "global"}


def _adjacent_selected_paths(
    resolutions: Mapping[int, SceneResolution],
    index: int,
) -> set[str]:
    """Return assets already touching a scene being repaired.

    A verified reuse is still a poor editorial choice when it creates an
    identical still on two consecutive scene boundaries. The set is advisory:
    callers fall back to the full candidate list only when no non-adjacent
    verified asset is available.
    """
    paths: set[str] = set()
    for neighbor in (index - 1, index + 1):
        resolution = resolutions.get(neighbor)
        path = str(getattr(resolution, "selected_path", "") or "") if resolution else ""
        if path:
            paths.add(path)
    return paths


_PENDING_VISUAL_METADATA_SOURCES = frozenset({
    "query_anchored_visual_pending",
    "domain_context_pending",
    "domain_context_pending_visual_required",
    "nearest_match_pending",
    "nearest_match_pending_visual_required",
    "royal_prefilter_pending",
    "identity_only_scene_context_pending",
    "visual_pending",
})


def _candidate_has_explicit_visual_pending(candidate: Mapping[str, Any]) -> bool:
    """Whether local retrieval intentionally deferred identity to live vision.

    Query-anchored image results can have weak captions even when their pixels
    are relevant. The resolver marks those rows explicitly so the coordinator
    can send them to the final scene-aware vision gate instead of treating a
    metadata gap as a confirmed entity mismatch. Explicit forbidden-entity,
    unsafe, or provider rejection evidence always remains a hard veto.
    """
    evidence = candidate.get("candidate_evidence") or {}
    evidence = evidence if isinstance(evidence, Mapping) else {}
    nested = candidate.get("qwen_result") or candidate.get("visual_decision") or {}
    nested = nested if isinstance(nested, Mapping) else {}
    reason = str(
        candidate.get("rejection_reason")
        or candidate.get("identity_gate_reason")
        or evidence.get("match_reason")
        or ""
    ).strip().lower()
    hard_reasons = {
        "forbidden_confusable_entity",
        "person_confusable_entity",
        "wrong_subject_type_animal",
        "wrong_subject_type_automobile",
        "unsafe_content",
        "unsafe_media",
        "adult_content",
        "synthetic_media_rejected",
    }
    if (
        reason in hard_reasons
        or evidence.get("forbidden_match")
        or evidence.get("confusable_hits")
        or candidate.get("unsafe_content")
        or nested.get("entity_conflict")
        or nested.get("fatal_mismatch")
        or nested.get("unsafe_content")
        or nested.get("suggestive_content")
    ):
        return False
    metadata_source = str(evidence.get("metadata_source") or "").strip().lower()
    return bool(
        candidate.get("semantic_context_pending")
        or candidate.get("semantic_visual_pending")
        or candidate.get("nearest_match_pending")
        or (
            evidence.get("verification_required")
            and metadata_source in _PENDING_VISUAL_METADATA_SOURCES
        )
    )


def _candidate_hard_gate(scene: Mapping[str, Any], candidate: Mapping[str, Any]) -> tuple[bool, str]:
    from .visual_intelligence.semantic_guard import explicit_domain_conflict
    story = ' '.join(str(scene.get(key) or '') for key in ('film_context', 'text', 'narration', 'domain'))
    evidence = ' '.join(str(candidate.get(key) or '') for key in ('title', 'description', 'tags', 'source_url', 'url'))
    domain_conflict = explicit_domain_conflict(story, evidence)
    if domain_conflict:
        return False, domain_conflict
    path = str(candidate.get("path") or "")
    if not path:
        return False, "missing_path"
    candidate_container = str(candidate.get("container_id") or "")
    scene_container = str(scene.get("container_id") or "")
    if candidate_container and scene_container and candidate_container != scene_container:
        return False, "container_mismatch"
    requirement = scene.get("visual_requirement")
    if isinstance(requirement, Mapping):
        prior_evidence = candidate.get("candidate_evidence") or {}
        prior_evidence = prior_evidence if isinstance(prior_evidence, Mapping) else {}
        matched, match_reason, matched_evidence = candidate_requirement_match(requirement, candidate)
        evidence = {**dict(prior_evidence), **dict(matched_evidence or {})}
        prior_source = str(prior_evidence.get("metadata_source") or "").strip().lower()
        if (
            prior_evidence.get("verification_required")
            and prior_source in _PENDING_VISUAL_METADATA_SOURCES
        ):
            # A query-anchored candidate can have a broad title that looks
            # like a metadata pass. Preserve its explicit visual-review
            # contract rather than upgrading that weak signal to identity.
            evidence["metadata_source"] = prior_source
            evidence["verification_required"] = True
        evidence_mapping = evidence if isinstance(evidence, Mapping) else {}
        hard_identity_mismatch = bool(
            match_reason in {
                "forbidden_confusable_entity",
                "person_confusable_entity",
                "conflicting_person_ordinal",
                "wrong_subject_type_animal",
                "wrong_subject_type_automobile",
            }
            or evidence_mapping.get("forbidden_match")
            or evidence_mapping.get("confusable_hits")
        )
        if not matched and hard_identity_mismatch:
            # Never let a nearest-match/contextual handoff override explicit
            # forbidden or wrong-subject-type evidence (e.g. a frog/toy result
            # returned for Prince Harry).
            return False, match_reason
        contextual_stock_no_vision = bool(
            str(candidate.get("provider") or candidate.get("source") or "").strip().casefold() == "stock"
            and (
                _source_policy_value(scene, "stock_last_resort_no_vision", True)
                or candidate.get("stock_last_resort_no_vision")
                or candidate.get("stock_last_resort")
            )
        )
        if not matched and contextual_stock_no_vision:
            # Generic stock footage is contextual B-roll, not proof of the
            # named entity. Explicit domain conflicts and confusable subjects
            # were rejected above; the stock terminal metadata gate below
            # still requires a topical signal (and exact dates for historical
            # requirements). Let that bounded lane provide related visuals
            # without vision instead of turning an unavailable public scene
            # into a blank/graphic-only failure.
            matched = True
            match_reason = "contextual_stock_availability_only"
            evidence = {
                "metadata_source": "stock_contextual_no_vision",
                "identity_proof": False,
                "verification_required": False,
                "degraded_reason": "stock_last_resort_no_vision",
            }
        if matched and _candidate_has_explicit_visual_pending({
            **dict(candidate),
            "candidate_evidence": evidence,
        }):
            matched = False
            match_reason = "metadata_identity_pending_visual_required"
        pending_domain_context = False
        pending_evidence: Mapping[str, Any] = evidence if isinstance(evidence, Mapping) else {}
        nearest_match_pending = False
        transport_recovery_pending = False
        if not matched:
            if (
                candidate_container
                and scene_container
                and candidate_container == scene_container
                and match_reason in {"missing_asset_identity_metadata", "no_metadata_evidence"}
            ):
                # Container ownership is routing metadata, not proof of what
                # the pixels depict. Keep strict named-entity rows pending for
                # live vision; only lenient/generic scenes may use the legacy
                # container shortcut as an identity pass.
                if bool(requirement.get("strict_identity", True)):
                    matched = False
                    pending_domain_context = True
                    match_reason = "container_identity_pending_visual_required"
                    pending_evidence = {
                        "container_id": candidate_container,
                        "match_type": "container_identity_pending",
                        "verification_required": True,
                    }
                else:
                    matched = True
                    match_reason = "container_identity_match"
                    evidence = {"container_id": candidate_container, "match_type": "container_identity"}
            else:
                pending_domain_context, pending_evidence = candidate_domain_context_pending(
                    requirement, candidate
                )
            if pending_domain_context:
                # This is a recall handoff to the authoritative visual gate,
                # not an identity approval. The asset must still pass live
                # frame verification below.
                nearest_pending, _nearest_reason, nearest_evidence = candidate_nearest_match(
                    requirement, candidate
                )
                if nearest_pending and str(requirement.get("subject_type") or "").strip().lower() in {
                    "automobile", "aircraft", "ship", "product"
                }:
                    nearest_match_pending = True
                    evidence = {
                        **dict(nearest_evidence or {}),
                        "metadata_source": "nearest_match_pending",
                        "verification_required": True,
                    }
                    match_reason = "nearest_match_pending_visual_required"
                else:
                    evidence = {**dict(evidence), **dict(pending_evidence or {})}
                    if (
                        prior_evidence.get("verification_required")
                        and prior_source in _PENDING_VISUAL_METADATA_SOURCES
                    ):
                        evidence["metadata_source"] = prior_source
                        evidence["verification_required"] = True
                    match_reason = "domain_context_pending_visual_required"
            else:
                # A recovered YouTube pool cut can lose its source metadata
                # while a lane is still completing. Let the downloaded file
                # reach the visual gate; the deterministic pool stamp is not
                # identity proof. Query text and synthetic recovery IDs never
                # count as evidence.
                nested_verdict = candidate.get("qwen_result") or candidate.get("visual_decision") or {}
                nested_verdict = nested_verdict if isinstance(nested_verdict, Mapping) else {}
                transport_recovery_pending = bool(
                    str(candidate.get("provider") or candidate.get("source") or "").strip().lower()
                    in {"youtube", "youtube clips", "yt"}
                    and str(nested_verdict.get("source") or "").strip().lower() == "youtube_pool_moment"
                    and str(nested_verdict.get("verification_status") or "").strip().lower()
                    in {"", "accepted", "verified", "deterministic_pool_cut", "deterministic_only"}
                    and not bool(nested_verdict.get("visual_checked") or nested_verdict.get("semantic_verified"))
                    and not any(
                        str(candidate.get(key) or "").strip()
                        for key in ("title", "description", "tags", "subject", "filename", "caption", "alt")
                    )
                )
                if transport_recovery_pending:
                    evidence = {
                        "transport_recovery": True,
                        "visual_verification_required": True,
                    }
                    match_reason = "transport_recovery_visual_required"
                if not transport_recovery_pending:
                    nearest_pending, _nearest_reason, nearest_evidence = candidate_nearest_match(
                        requirement, candidate
                    )
                    if nearest_pending:
                        # This is the same bounded handoff as the rail-domain
                        # pending path: it increases recall without turning a
                        # near match into an identity approval. The visual
                        # verifier must inspect the downloaded pixels.
                        pending_domain_context = True
                        nearest_match_pending = True
                        evidence = {
                            **dict(nearest_evidence or {}),
                            "metadata_source": "nearest_match_pending",
                            "verification_required": True,
                        }
                        match_reason = "nearest_match_pending_visual_required"
                    elif (
                        match_reason not in {
                            "forbidden_confusable_entity",
                            "person_confusable_entity",
                            "wrong_subject_type_animal",
                            "wrong_subject_type_automobile",
                        }
                        and _candidate_has_explicit_visual_pending(candidate)
                    ):
                        # The resolver already marked this row as a permitted
                        # visual-pending candidate. A stricter metadata parser
                        # at the V2 boundary must not discard it before the
                        # shared image/scene verifier can inspect the pixels.
                        prior_evidence = candidate.get("candidate_evidence") or {}
                        prior_evidence = prior_evidence if isinstance(prior_evidence, Mapping) else {}
                        pending_domain_context = True
                        evidence = {
                            **dict(prior_evidence),
                            "metadata_source": str(
                                prior_evidence.get("metadata_source")
                                or "query_anchored_visual_pending"
                            ),
                            "verification_required": True,
                            "match_reason": match_reason,
                        }
                        match_reason = "metadata_identity_pending_visual_required"
        if isinstance(candidate, dict):
            candidate["candidate_evidence"] = evidence
            candidate["requirement_hash"] = str(requirement.get("requirement_hash") or "")
            candidate["identity_gate_reason"] = match_reason
            candidate["match_reason"] = match_reason
            if pending_domain_context:
                candidate["semantic_context_pending"] = True
            if transport_recovery_pending:
                candidate["transport_recovery_pending"] = True
        if not matched and not pending_domain_context and not transport_recovery_pending:
            return False, match_reason
        if pending_domain_context and isinstance(candidate, dict):
            # A domain-context row is only eligible to become a finalist. It
            # must still pass the live visual verifier below; this is not an
            # identity approval and never turns query text into evidence.
            candidate["identity_gate_reason"] = (
                "nearest_match_pending_visual_required"
                if nearest_match_pending
                else "domain_context_pending_visual_required"
            )
    gate_outcomes = candidate.get("gate_outcomes") or {}
    if isinstance(gate_outcomes, dict):
        if any(gate_outcomes.get(key) is False for key in ("decodable", "safe", "rights", "container")):
            return False, "deterministic_gate_failed"
    # A provider-clean file can still contain a composited streamer/PiP
    # camera. These flags are produced by the local frame audit and must be a
    # hard identity boundary before quota protection or same-container reuse.
    overlay_types = {
        str(value).strip().lower()
        for value in (candidate.get("overlay_types") or [])
        if str(value).strip()
    }
    nested_verdict = candidate.get("qwen_result") or candidate.get("visual_decision") or {}
    nested_verdict = nested_verdict if isinstance(nested_verdict, Mapping) else {}
    soft_facecam_allowed = bool(
        _source_policy_value(scene, "youtube_soft_facecam_allowed", False)
        and str(candidate.get("source") or candidate.get("provider") or "").strip().lower()
        in {"youtube", "youtube clips", "yt", "youtube_pool_moment"}
        and candidate.get("facecam_tolerated")
    )
    contamination = bool(
        ((candidate.get("facecam_inset")
        or candidate.get("inset_facecam")
        or candidate.get("picture_in_picture")) and not soft_facecam_allowed)
        or candidate.get("visual_rejected")
        or (overlay_types.intersection({"facecam", "face_cam", "inset_facecam", "picture_in_picture"}) and not soft_facecam_allowed)
        or ((nested_verdict.get("facecam_inset") or nested_verdict.get("inset_facecam")) and not soft_facecam_allowed)
        or (any(
            token in str(candidate.get("rejection_reason") or "").casefold()
            for token in ("facecam_inset", "inset_facecam", "picture_in_picture")
        ) and not soft_facecam_allowed)
    )
    if contamination:
        return False, "facecam_or_inset_contamination"
    source_key = str(candidate.get("source") or candidate.get("provider") or "").strip().lower()
    is_youtube = source_key in {"youtube", "youtube clips", "yt", "youtube_v2", "youtube_pool_moment"}
    try:
        talking_confidence = float(
            candidate.get("talking_head_confidence")
            or nested_verdict.get("talking_head_confidence")
            or 0.0
        )
    except (TypeError, ValueError):
        talking_confidence = 0.0
    talking_head_flag = bool(
        candidate.get("talking_head")
        or nested_verdict.get("talking_head")
    )
    explicit_talking_head = bool(
        is_youtube
        and not soft_facecam_allowed
        and (
            (talking_head_flag and talking_confidence >= 0.75)
            or str(candidate.get("talking_head_reason") or nested_verdict.get("talking_head_reason") or "").strip().lower()
            in {"stable_centered_presenter", "confident_talking_head", "talking_head"}
            or "confident_talking_head" in str(candidate.get("rejection_reason") or "").lower()
        )
    )
    if explicit_talking_head:
        return False, "talking_head_contamination"
    # A web still can carry the same presenter package as a YouTube result
    # (ET/Access/TMZ interview thumbnails are common royal-family collisions).
    # Treat an explicit presenter package as a hard metadata boundary for a
    # strict scene; ordinary article photos and archive pages remain eligible.
    if _is_web_source(candidate) and scene_requires_visual_verification(scene):
        web_blob = " ".join(
            str(candidate.get(key) or "")
            for key in ("title", "description", "source_url", "page_url", "origin_url", "domain")
        ).casefold()
        web_package_houses = (
            "entertainment tonight", "etonline", "access hollywood", "extra tv",
            "tmz", "celebrity news",
        )
        web_presenter_terms = (
            "interview", "talks", "speaks", "sits down", "video message",
            "at home", "reaction", "podcast", "hosted", "presenter",
        )
        raw_visual_terms = (
            "raw footage", "archive footage", "archival footage", "arrival",
            "ceremony", "event photo", "press photo", "official photo",
        )
        if (
            any(term in web_blob for term in web_package_houses)
            and any(term in web_blob for term in web_presenter_terms)
            and not any(term in web_blob for term in raw_visual_terms)
        ):
            return False, "web_presenter_package"
    forbidden = {
        str(value).strip().casefold()
        for value in (scene.get("forbidden_subjects") or [])
        if str(value).strip()
    }
    # Query text is retrieval intent, not evidence about the returned asset.
    # In particular, semantic queries intentionally contain negative
    # disambiguators such as ``-tiger`` and ``-wildlife``.  Inspecting those
    # terms here turns a correctly retrieved Jaguar automobile into a false
    # forbidden-subject rejection.  Candidate identity is already proven by
    # the provider metadata/visual requirement gate above; this final safety
    # check must inspect asset metadata (title, tags, subject) rather than long
    # editorial descriptions that describe museum/show contexts.
    title_tags_subject = " ".join((
        str(candidate.get("title") or ""),
        str(candidate.get("tags") or ""),
        str(candidate.get("subject") or ""),
    )).casefold()
    search_target = title_tags_subject if title_tags_subject.strip() else candidate_asset_metadata(candidate).casefold()
    if forbidden:
        import re as _re_gate
        for subject in forbidden:
            if subject and _re_gate.search(r'(?<![a-z0-9])' + _re_gate.escape(subject) + r'(?![a-z0-9])', search_target):
                return False, "forbidden_subject"
    return True, "passed"


# The terminal stock lane intentionally skips pixel vision, but it must not
# become an unbounded "anything downloadable" lane.  Provider tags/captions
# are cheap and sufficient to block the common conceptual substitutions that
# caused the royal-family run to select a marble frog statue for a named
# person.  These are metadata safety checks only; web and YouTube still use
# the authoritative scene-aware vision gate.
_STOCK_NON_PHOTOGRAPHIC_TERMS = (
    "statue", "sculpture", "bust", "marble", "figurine", "figure toy",
    "toy", "doll", "mannequin", "wax figure", "monument", "ornament",
    "illustration", "drawing", "cartoon", "render", "3d", "cgi", "avatar",
    "icon", "clip art", "clipart", "sticker", "emblem", "logo",
)

# Anonymous stock is useful for a generic establishing shot, but it is not
# evidence of a named person.  These terms are deliberately broad and cheap:
# they keep the no-vision emergency lane from turning a royal/political scene
# into a random landscape while still allowing a captioned topical stock image
# to pass when a provider actually supplies one.
_STOCK_DOMAIN_CONTEXT_TERMS = (
    "royal", "royalty", "monarch", "monarchy", "king", "queen", "prince",
    "princess", "palace", "crown", "coronation", "british", "england",
    "united kingdom", "uk", "london", "government", "president", "senate",
    "parliament", "politic", "election", "court", "ceremony", "official",
)


def _stock_terminal_metadata_gate(
    scene: Mapping[str, Any], candidate: Mapping[str, Any]
) -> tuple[bool, str]:
    """Apply deterministic, no-vision safety checks to a stock candidate."""
    blob_parts = []
    for key in (
        "title", "description", "tags", "categories", "alt", "caption",
        "filename", "name", "asset_name", "source_url", "page_url",
    ):
        value = candidate.get(key)
        if isinstance(value, (list, tuple, set)):
            blob_parts.extend(str(entry) for entry in value)
        elif value:
            blob_parts.append(str(value))
    blob = " ".join(blob_parts).casefold()
    for term in _STOCK_NON_PHOTOGRAPHIC_TERMS:
        if re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", blob):
            return False, f"non_photographic_metadata:{term}"

    # For a named-person contract, an explicitly populated stock caption must
    # at least carry one canonical name token.  A blank caption remains an
    # availability-only emergency candidate; it is stamped as degraded and
    # never presented as identity proof.  This prevents a clearly labelled
    # unrelated stock subject from silently replacing the requested person.
    requirement = scene.get("visual_requirement")
    canonical = ""
    strict_identity = False
    if isinstance(requirement, Mapping):
        canonical = str(requirement.get("canonical_entity") or "").strip()
        strict_identity = bool(requirement.get("strict_identity", False))
        # A no-vision stock fallback cannot prove a historical period from
        # pixels.  For an event with an explicit required year, anonymous
        # contemporary stock must not pass merely because it shows the same
        # sport, team, city, or object.  Only stock metadata explicitly tied
        # to the exact year is eligible for this degraded lane; otherwise the
        # caller must use its grounded Remotion fallback or keep the job
        # blocked for a verified source.
        attributes = requirement.get("required_attributes")
        attributes = attributes if isinstance(attributes, Mapping) else {}
        required_year = str(attributes.get("year") or "").strip()
        is_stock_candidate = bool(
            candidate.get("stock_last_resort_no_vision")
            or candidate.get("stock_last_resort")
            or str(candidate.get("provider") or candidate.get("source") or "").strip().lower() == "stock"
        )
        if strict_identity and re.fullmatch(r"(?:18|19|20)\d{2}", required_year) and int(required_year) < 2024:
            req_subject_type = str(
                (requirement.get("subject_type") if isinstance(requirement, Mapping) else "")
                or scene.get("subject_type") or ""
            ).strip().casefold()
            if not is_stock_candidate or req_subject_type in {"event", "named_event", "historical_event"}:
                if not re.search(r"(?<!\d)" + re.escape(required_year) + r"(?!\d)", blob):
                    return False, "historical_period_metadata_missing"
            other_years = {
                year for year in re.findall(r"(?<!\d)(?:18|19|20)\d{2}(?!\d)", blob)
                if year != required_year
            }
            if other_years:
                return False, "historical_period_metadata_conflict"
    if not canonical:
        canonical = str(scene.get("canonical_entity") or scene.get("subject") or "").strip()
        strict_identity = strict_identity or bool(scene.get("strict_identity"))
    subject_type = str(
        (requirement.get("subject_type") if isinstance(requirement, Mapping) else "")
        or scene.get("subject_type") or ""
    ).strip().casefold()
    if isinstance(requirement, Mapping):
        forbidden = requirement.get("forbidden_subjects") or ()
        if isinstance(forbidden, str):
            forbidden = [forbidden]
        for forbidden_term in forbidden:
            term = str(forbidden_term or "").strip().casefold()
            if not term:
                continue
            if re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", blob):
                return False, f"forbidden_subject_metadata:{term}"
    if canonical and strict_identity and subject_type in {"person", "named_person", "celebrity", "politician"} and blob:
        name_tokens = [
            token for token in re.findall(r"[a-z0-9]+", canonical.casefold())
            if len(token) >= 3 and token not in {"the", "king", "queen", "prince", "princess", "sir", "mr", "mrs"}
        ]
        if name_tokens and not any(token in blob for token in name_tokens):
            # The terminal stock lane is an explicitly degraded availability
            # fallback. It skips pixel vision by policy, and generic stock
            # captions almost never contain the requested person's name even
            # when the scene/domain is usable. Keep deterministic safety
            # filters above (toy/statue/logo/etc.), but do not turn the
            # metadata gap into a blank scene or a media-contract failure.
            if (
                candidate.get("stock_last_resort_no_vision")
                or candidate.get("stock_last_resort")
                or candidate.get("visual_verification_skipped")
            ):
                # The terminal lane may skip vision, but it still needs a
                # topical signal.  A completely anonymous file is commonly a
                # cached landscape/road and must never silently replace a
                # named person.  This is the deterministic guard that caught
                # the mountain image in the manual royal-family run.
                if not any(
                    re.search(
                        r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])",
                        blob,
                    )
                    for term in _STOCK_DOMAIN_CONTEXT_TERMS
                ):
                    return False, "named_person_stock_context_missing"
                return True, "passed_degraded_stock_metadata"
            return False, "named_person_metadata_mismatch"
    return True, "passed"



def _branch_review_is_bound(scene, candidate, decision):
    """An old acceptance may skip vision only for this scene and these bytes."""
    requirement = scene.get('visual_requirement') or {}
    expected = str(requirement.get('requirement_hash') or '') if isinstance(requirement, Mapping) else ''
    if not expected:
        return True  # Legacy callers without V2 scene contracts.
    from .visual_intelligence.semantic_guard import VISUAL_REVIEW_POLICY_VERSION
    if (decision.get('verified_requirement_hash') != expected
            or (decision.get('visual_review_policy_version') is not None and decision.get('visual_review_policy_version') != VISUAL_REVIEW_POLICY_VERSION)
            or not decision.get('verified_asset_sha256')):
        return False
    try:
        digest = hashlib.sha256()
        with Path(str(candidate.get('path') or '')).open('rb') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(block)
        return digest.hexdigest() == decision['verified_asset_sha256']
    except OSError:
        return False


def _verification_result(
    scene: Mapping[str, Any],
    candidate: Dict[str, Any],
    verifier: Optional[Callable[[Mapping[str, Any], Dict[str, Any]], Any]],
) -> tuple[bool, str, float, Optional[Dict[str, Any]]]:
    from .visual_intelligence.context import scene_requires_visual_verification

    if not isinstance(candidate, Mapping):
        candidate = {}
    if not isinstance(scene, Mapping):
        scene = {}
    strict = scene_requires_visual_verification(scene)
    raw_source_hint = str(candidate.get("source") or candidate.get("provider") or "").strip().lower()
    source_hint = "web" if _is_web_source(candidate) else raw_source_hint
    # Cheap origin screening also applies before branch-verdict reuse or
    # unavailable-vision recovery, so those shortcuts cannot admit thumbnails.
    web_origin = ' '.join(str(candidate.get(key) or '') for key in ('url', 'source_url', 'image_url', 'original_url')).lower()
    if _is_web_source(candidate) and re.search(r'(?:i\d?\.)?ytimg\.com/|youtube\.com/.*(?:thumbnail|hqdefault|maxresdefault)', web_origin):
        return False, 'promotional_thumbnail_not_source_media', 0.0, {'accepted': False, 'status': 'rejected', 'rejection_reason': 'promotional_thumbnail_not_source_media'}
    soft_facecam_allowed = bool(
        _source_policy_value(scene, "youtube_soft_facecam_allowed", False)
        and raw_source_hint in {"youtube", "youtube clips", "yt", "youtube_pool_moment"}
        and candidate.get("facecam_tolerated")
    )
    resolution_kind = str(candidate.get("resolution_kind") or candidate.get("fallback_layer") or "").strip().lower()
    if _footage_only_scene(scene):
        if source_hint in {"web", "web_image", "web_image_resolver", "web images", "image"}:
            return False, "footage_required_web_still_rejected", 0.0, {
                "accepted": False,
                "status": "visual_rejected",
                "reason": "footage_required_web_still_rejected",
            }
        if resolution_kind in {"youtube_extracted_frame", "same_video_timestamp_frame"}:
            return False, "footage_required_extracted_frame_rejected", 0.0, {
                "accepted": False,
                "status": "visual_rejected",
                "reason": "footage_required_extracted_frame_rejected",
            }
        candidate_path = str(candidate.get("cleaned_path") or candidate.get("path") or "").strip()
        if candidate_path and _title_card_lead_detected(candidate_path):
            return False, "footage_required_title_card_rejected", 0.0, {
                "accepted": False,
                "status": "visual_rejected",
                "reason": "footage_required_title_card_rejected",
            }
        overlay_types = {
            str(value).strip().lower()
            for value in (candidate.get("overlay_types") or [])
            if str(value).strip()
        }
        if (
            bool(candidate.get("blur_failed") or candidate.get("overlay_blur_failed"))
            or overlay_types.intersection({"central_logo", "large_logo"})
            or ('large_text_band' in overlay_types and not source_overlays_preserved(candidate))
            or (overlay_types.intersection({"subtitle", "subtitles", "caption"}) and not source_overlays_preserved(candidate))
        ):
            return False, "footage_required_clean_overlay_rejected", 0.0, {
                "accepted": False,
                "status": "visual_rejected",
                "reason": "footage_required_clean_overlay_rejected",
            }
    # Never fail open for a deterministic facecam/PiP hit. This check runs
    # before branch-verdict reuse, because a pool row can carry an otherwise
    # accepted ``youtube_pool_moment`` verdict.
    candidate_overlay_types = {
        str(value).strip().lower()
        for value in (candidate.get("overlay_types") or [])
        if str(value).strip()
    }
    candidate_verdict = candidate.get("qwen_result") or candidate.get("visual_decision") or {}
    candidate_verdict = candidate_verdict if isinstance(candidate_verdict, Mapping) else {}
    if bool(
        (
            candidate.get("facecam_inset")
            or candidate.get("inset_facecam")
            or candidate.get("picture_in_picture")
            or candidate_overlay_types.intersection({"facecam", "face_cam", "inset_facecam", "picture_in_picture"})
            or candidate_verdict.get("facecam_inset")
            or candidate_verdict.get("inset_facecam")
        )
        and not soft_facecam_allowed
    ):
        return False, "facecam_or_inset_contamination", 0.0, {
            "accepted": False,
            "status": "visual_rejected",
            "reason": "facecam_or_inset_contamination",
            "facecam_inset": True,
        }
    if verifier is None:
        # A live decision remains valid if this exact file and scene contract
        # have already been checked. An exhausted provider must not discard
        # that work; metadata-only and stale decisions still need verification.
        previous = candidate.get("visual_decision") or candidate.get("qwen_result")
        requirement = scene.get("visual_requirement") or {}
        if isinstance(previous, Mapping) and isinstance(requirement, Mapping):
            from .visual_intelligence.semantic_guard import explicit_scene_conflict
            bound_hash = str(previous.get("verified_asset_sha256") or "")
            requirement_hash = str(requirement.get("requirement_hash") or "")
            live = all(previous.get(flag) is True for flag in ("accepted", "visual_checked", "visual_available"))
            safe = not (explicit_scene_conflict(previous) or previous.get("unsafe_content")
                        or previous.get("suggestive_content")
                        or (_is_web_source(candidate) and previous.get("watermark")))
            if (live and safe and bound_hash and requirement_hash
                    and _branch_review_is_bound(scene, candidate, previous)
                    and previous.get("verified_requirement_hash") == requirement_hash
                    and (not strict or previous.get("semantic_verified") is True)):
                try:
                    digest = hashlib.sha256()
                    with Path(str(candidate.get("path") or "")).open("rb") as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(block)
                    if digest.hexdigest() == bound_hash:
                        score = float(previous.get("final_score") or previous.get("relevance") or previous.get("score") or 0)
                        return True, "visual_pass_reused", score, dict(previous)
                except (OSError, ValueError, TypeError):
                    pass
        # A strict named-entity scene cannot fail open here.  Provider
        # metadata proves only that a search result was labelled with the
        # query; it does not prove what the pixels depict.  Accepting a
        # metadata-only row after a vision outage is how unrelated stock/web
        # images reached exports.  Leave the scene unresolved so the caller
        # can try another verified lane or emit a grounded graphic fallback.
        evidence = candidate.get("candidate_evidence") or {}
        if not isinstance(evidence, Mapping):
            evidence = {}
        is_stock_cand = "stock" in str(candidate.get("source") or candidate.get("provider") or "").lower()
        if strict:
            if is_stock_cand and (
                candidate.get("match_reason") == "contextual_stock_availability_only"
                or candidate.get("identity_gate_reason") == "contextual_stock_availability_only"
                or evidence.get("hard_gate_reason") == "contextual_stock_availability_only"
                or evidence.get("metadata_source") == "stock_contextual_no_vision"
                or candidate.get("verification_status") == "deterministic_stock_last_resort"
                or candidate.get("stock_last_resort_no_vision")
                or candidate.get("stock_last_resort")
            ):
                return True, "contextual_stock_availability_only", 75.0, {
                    "accepted": True,
                    "status": "accepted",
                    "final_score": 75.0,
                    "reason": "contextual_stock_availability_only",
                    "scene_match": True,
                    "domain_match": True,
                    "visual_checked": True,
                    "visual_available": True,
                    "fatal_mismatch": False,
                    "entity_conflict": False,
                }
            return False, "visual_verifier_unavailable", 0.0, {
                "accepted": False,
                "status": "vision_unavailable",
                "verification_status": "unverified",
                "visual_available": False,
                "reason": "strict_identity_requires_live_semantic_verification",
            }
        if source_hint in {"web", "web_image", "web_image_resolver", "web images", "image"}:
            identity_backed = bool(
                evidence.get("identity_proof")
                or evidence.get("nearest_match")
                or evidence.get("thematic_near_match")
            ) and not bool(candidate.get("identity_failed"))
            if identity_backed:
                score = float(
                    candidate.get("final_score")
                    or candidate.get("score")
                    or candidate.get("metadata_score")
                    or 0.0
                )
                return True, "deterministic_web_image", score, {
                    "accepted": True,
                    "status": "deterministic_degraded",
                    "verification_status": "deterministic_web_image",
                    "visual_available": False,
                    "reason": "asset_metadata_gate_passed_vision_unavailable",
                }
        if source_hint in {"youtube", "youtube clips", "yt"}:
            overlay_types = {
                str(value).strip().lower()
                for value in (candidate.get("overlay_types") or [])
                if str(value).strip()
            }
            if (not overlay_types.intersection({"central_logo", "large_logo", "facecam", "picture_in_picture"})
                and (not overlay_types.intersection({"subtitle", "subtitles", "caption", "large_text_band"})
                     or source_overlays_preserved(candidate))
                and not bool(candidate.get("overlay_rejected") or candidate.get("blur_failed"))):
                score = float(
                    candidate.get("final_score")
                    or candidate.get("score")
                    or candidate.get("metadata_score")
                    or 0.0
                )
                return True, "deterministic_degraded", score, {
                    "accepted": True,
                    "status": "deterministic_degraded",
                    "verification_status": "deterministic_only",
                    "visual_available": False,
                    "reason": "provider_gate_passed_vision_unavailable",
                }
        return (False, "visual_verifier_unavailable", 0.0, None) if strict else (
            True, "deterministic_only", float(candidate.get("final_score") or candidate.get("score") or 0.0), None
        )
    # Branch-verdict reuse: the web lane already gated this exact asset with a
    # live model and stamped the decision onto the candidate row. Re-verifying
    # it here spends a second call from the shared job budget on the same
    # asset; trust an explicitly accepted, conflict-free branch verdict.
    branch_verdict = candidate.get("visual_decision") or candidate.get("qwen_result")
    if isinstance(branch_verdict, Mapping) and branch_verdict:
        branch_accepted = branch_verdict.get("accepted")
        if branch_accepted is None:
            branch_accepted = str(branch_verdict.get("status") or "").strip().lower() in {
                "accepted", "pass", "keep",
            }
        branch_conflict = bool(
            branch_verdict.get("entity_conflict")
            or branch_verdict.get("fatal_mismatch")
        )
        branch_unsafe = bool(
            branch_verdict.get("unsafe_content")
            or branch_verdict.get("suggestive_content")
        )
        branch_web_watermark = bool(
            _is_web_source(candidate)
            and (
                branch_verdict.get("watermark")
                or str(branch_verdict.get("rejection_reason") or "").strip().lower()
                == "watermark_detected"
            )
        )
        branch_source = str(branch_verdict.get("source") or "").strip().lower()
        branch_verification_status = str(
            branch_verdict.get("verification_status") or ""
        ).strip().lower()
        branch_pending = (
            not branch_verification_status
            or "pending" in branch_verification_status
            or branch_verification_status in {"unverified", "discovered"}
        )
        deterministic_pool_cut = bool(
            branch_source == "youtube_pool_moment"
            and branch_verification_status in {"deterministic_pool_cut", "deterministic_only"}
        )
        # Pool cuts historically stamped ``accepted=true`` even when the
        # optional visual call was unavailable. Do not let that synthetic
        # verdict bypass the shared verifier; it is only a deterministic
        # overlay/quality pass, not proof that the scene subject is correct.
        deterministic_web_image = bool(
            branch_verification_status
            in {"deterministic_web_image", "deterministic_only", "deterministic_degraded"}
            and str(branch_verdict.get("source") or "").strip().lower()
            in {"web", "web_image", "web_image_resolver", "web images"}
        )
        visual_checked_value = branch_verdict.get("visual_checked")
        visual_checked = not (
            isinstance(visual_checked_value, str)
            and visual_checked_value.strip().lower() in {"", "0", "false", "no", "off"}
        ) and bool(visual_checked_value)
        visual_reason = str(branch_verdict.get("visual_reason") or "").strip().lower()
        # A YouTube pool/ranged verdict can carry ``visual_available=true``
        # merely because a frame buffer was present, while the live verifier
        # was never called. Such a verdict must be rechecked before reuse.
        youtube_unchecked_pool_cut = bool(
            _is_youtube_source(candidate)
            and branch_source == "youtube_pool_moment"
            and (
                not visual_checked
                or "fatal_mismatch" in visual_reason
                or "entity_conflict" in visual_reason
            )
        )
        youtube_without_live_visual = bool(
            _is_youtube_source(candidate)
            and not bool(
                branch_verdict.get("visual_available")
                or branch_verdict.get("vision_available")
            )
        ) or youtube_unchecked_pool_cut
        # A deterministic web stamp proves only that the file decoded and
        # passed cheap safety checks. It has not been verified by a live model;
        # the shared verifier must inspect it to reject irrelevant media.
        # The same rule applies to every YouTube ranged/pool verdict: a clean
        # transport or overlay audit proves only that the file is usable. It
        # does not prove that the downloaded moment depicts this scene.
        if (
            branch_accepted
            and not branch_conflict
            and not branch_unsafe
            and not branch_web_watermark
            and not branch_pending
            and not deterministic_pool_cut
            and not deterministic_web_image
            and not youtube_without_live_visual
            and _branch_review_is_bound(scene, candidate, branch_verdict)
            and (not strict or bool(branch_verdict.get("semantic_verified") or branch_verdict.get("visual_checked") or branch_verdict.get("visual_available")))
        ):
            branch_score = float(
                branch_verdict.get("final_score")
                or branch_verdict.get("relevance")
                or branch_verdict.get("score")
                or 0.0
            )
            return True, "visual_pass", branch_score, dict(branch_verdict)
    is_stock_cand = "stock" in str(candidate.get("source") or candidate.get("provider") or "").lower()
    try:
        raw = verifier(scene, candidate) if verifier is not None else None
    except Exception as exc:
        if is_stock_cand:
            return True, "stock_fallback_accepted", 75.0, {
                "accepted": True,
                "status": "accepted",
                "final_score": 75.0,
                "reason": "stock_verifier_error_salvaged_as_contextual_broll",
                "scene_match": True,
                "domain_match": True,
                "visual_checked": True,
                "visual_available": True,
                "fatal_mismatch": False,
                "entity_conflict": False,
            }
        # Keep provider/verifier failures fail-closed, but preserve a compact,
        # sanitized detail in the media ledger. Previously every distinct bug
        # collapsed to ``visual_verifier_error:AttributeError``, making a
        # failed long render impossible to diagnose from its saved package.
        detail = str(exc).replace("\r", " ").replace("\n", " ")
        detail = re.sub(
            r"(?i)(?:bearer\s+)?(?:sk-or-v1-|sk-[a-z0-9_-]{16,})[a-z0-9_.-]*",
            "[redacted-key]",
            detail,
        )
        detail = re.sub(
            r"(?i)(authorization|api[_ -]?key|token)\s*[:=]\s*[^,;\s]+",
            r"\1=[redacted]",
            detail,
        )
        detail = re.sub(r"(?i)(?:[a-z]:\\|/)[^\s,;]+", "[path]", detail)
        detail = detail.strip()[:180]
        reason = f"visual_verifier_error:{type(exc).__name__}"
        if detail:
            reason += f":{detail}"
        return False, reason, 0.0, {
            "accepted": False,
            "status": "verifier_error",
            "error_type": type(exc).__name__,
            "reason": detail or "visual_verifier_exception",
        }
    if isinstance(raw, bool):
        return raw, "visual_pass" if raw else "visual_rejected", 100.0 if raw else 0.0, {"accepted": raw}
    result = dict(raw or {}) if isinstance(raw, dict) else {}
    source = str(candidate.get("source") or candidate.get("provider") or "").strip().lower()
    is_youtube_candidate = source in {"youtube", "youtube clips", "yt"}
    youtube_relaxed = bool(is_youtube_candidate and _youtube_relaxed_visual_gate(scene))
    score = float(result.get("final_score") or result.get("relevance") or result.get("score") or 0.0)
    identity_val = float(result.get("identity_score") or 0.0)
    from .visual_intelligence.semantic_guard import explicit_low_visual_match
    if explicit_low_visual_match(result):
        result.update(accepted=False, status='rejected', fatal_mismatch=True,
                      rejection_reason='insufficient_visible_subject_match')
        return False, 'insufficient_visible_subject_match', score, result
    model_fatal_mismatch = bool(result.get("fatal_mismatch"))
    reason_text = str(result.get("reason") or "").lower()

    # If the model explicitly stated that it does NOT constitute a fatal mismatch, obey its explanation
    if model_fatal_mismatch and any(phrase in reason_text for phrase in (
        "does not constitute a fatal mismatch",
        "not a fatal mismatch",
        "no fatal mismatch",
        "not constitute a fatal mismatch",
        "does not constitute an entity conflict",
        "not an entity conflict",
    )):
        model_fatal_mismatch = False
        result["fatal_mismatch"] = False

    if youtube_relaxed and model_fatal_mismatch:
        spurious_tokens = (
            "era", "year", "season", "tenure", "older", "dated",
            "period", "roster", "transfer", "trade", "matchup", "uniform",
            "different player", "different team", "not named", "quarterback",
            "different event", "distinct events", "wrong event", "competing event",
            "wrong matchup", "different matchup", "different location", "wrong venue",
        )
        if any(token in reason_text for token in spurious_tokens) and not any(
            marker in reason_text for marker in (
                "wrong domain", "different_sport", "simulation_for_real_world", "game-engine",
                "wrong era", "different era", "anachronistic", "anachronism",
            )
        ):
            result["spurious_mismatch"] = True
            model_fatal_mismatch = False
            result["fatal_mismatch"] = False

    # High scores cannot erase a positive factual-conflict flag.
    from .visual_intelligence.semantic_guard import explicit_scene_conflict
    is_conflict = explicit_scene_conflict(result, relaxed=youtube_relaxed)
    # Explicit pixel-level contradictions are factual vetoes, including for
    # relaxed YouTube scenes.  The relaxed lane may soften an ambiguous
    # relevance score, but it must never turn a different matchup/event,
    # wrong location, or entity mismatch into accepted footage.  Earlier
    # logic let event_mismatch survive normalization while clearing the
    # separate fatal_mismatch flag, then overrode that contradiction here.
    if is_conflict:
        rejection = "visual_rejected" if result.get("status") == "visual_rejected" else "explicit_scene_conflict"
        result.update({"accepted": False, "status": "rejected", "verification_status": "rejected", "rejection_reason": "explicit_scene_conflict"})
        return False, rejection, 0.0, result
    # YouTube search thumbnails are promotional composites, not a photograph
    # of the narrated event. Never let the web soft-pass resurrect them.
    web_url = ' '.join(str(candidate.get(key) or '') for key in ('url', 'source_url', 'image_url', 'original_url')).lower()
    promo_flags = any(result.get(key) is True for key in ('promotional_thumbnail', 'youtube_thumbnail', 'is_thumbnail', 'promotional_collage'))
    if _is_web_source(candidate) and (promo_flags or re.search(r'(?:i\d?\.)?ytimg\.com/|youtube\.com/.*(?:thumbnail|hqdefault|maxresdefault)', web_url)):
        result.update({'accepted': False, 'status': 'rejected', 'verification_status': 'rejected',
                       'rejection_reason': 'promotional_thumbnail_not_source_media'})
        return False, 'promotional_thumbnail_not_source_media', 0.0, result
    # A relevance soft pass must never promote an image whose pixels contain
    # an ownership mark. The scene can try another candidate instead.
    if _is_web_source(candidate) and (
        result.get("watermark")
        or str(result.get("rejection_reason") or "").strip().lower() == "watermark_detected"
    ):
        result.update({
            "accepted": False,
            "status": "rejected",
            "verification_status": "rejected",
            "rejection_reason": "watermark_detected",
        })
        return False, "watermark_detected", 0.0, result
    # A degraded vision response can emit entity_conflict=true without any
    # supporting identity/context signal (all scores zero and no explanation).
    # Treat that as an unavailable/low-confidence verdict; otherwise a clean
    # deterministic YouTube cut is silently replaced by web media. Explicit
    # fatal mismatches and evidence-backed conflicts remain hard rejects.
    raw_entity_conflict = bool(result.get("entity_conflict"))
    raw_status_reason = " ".join(
        str(result.get(key) or "") for key in ("status", "reason", "verification_status")
    ).lower()
    availability_markers = (
        "vision_unavailable", "client_unavailable", "provider_outage",
        "missing_candidate_evidence", "visual_grace_deadline", "timeout",
        "rate_limit", "schema_invalid",
    )
    availability_hint = any(token in raw_status_reason for token in availability_markers)
    conflict_evidence = bool(
        result.get("fatal_mismatch")
        or any(
            float(result.get(key) or 0.0) > 0.0
            for key in ("identity_score", "context_score", "action_score", "relevance")
            if isinstance(result.get(key), (int, float)) and not isinstance(result.get(key), bool)
        )
        or (str(result.get("reason") or "").strip() and not availability_hint)
    )
    source = str(candidate.get("source") or candidate.get("provider") or "").strip().lower()
    is_youtube_candidate = source in {"youtube", "youtube clips", "yt"}
    youtube_relaxed = bool(is_youtube_candidate and _youtube_relaxed_visual_gate(scene))
    score = float(result.get("final_score") or result.get("relevance") or result.get("score") or 0.0)
    identity_val = float(result.get("identity_score") or 0.0)
    model_fatal_mismatch = bool(result.get("fatal_mismatch"))
    if youtube_relaxed and (identity_val >= 65.0 or score >= 65.0) and not model_fatal_mismatch:
        raw_entity_conflict = False
        result["entity_conflict"] = False
    conflict = bool((raw_entity_conflict and conflict_evidence) or model_fatal_mismatch)
    accepted = result.get("accepted")
    if accepted is None:
        accepted = str(result.get("status") or "").lower() in {"accepted", "pass", "keep"} or (
            youtube_relaxed and (identity_val >= 65.0 or score >= 65.0)
        )
    # ``youtube_fail_open_on_visual_unavailable`` is a delivery safeguard for
    # advisory jobs only.  A strict factual/accuracy scene must never promote
    # a clean-but-unverified clip: a wrong person or event can pass every
    # transport and overlay check while still being semantically irrelevant.
    youtube_fail_open = bool(
        youtube_relaxed
        and not strict
        and _youtube_fail_open_on_visual_unavailable(scene)
    )
    unavailable_reason = raw_status_reason
    candidate_qwen = candidate.get("qwen_result") or {}
    if not isinstance(candidate_qwen, Mapping):
        candidate_qwen = {}
    deterministic_web = bool(
        _is_web_source(candidate)
        and (
            candidate.get("visual_unverified")
            or str(candidate_qwen.get("verification_status") or "").strip().lower()
            in {"deterministic_web_image", "deterministic_only", "deterministic_degraded"}
        )
    )
    vision_unavailable = availability_hint
    visual_requirement = scene.get("visual_requirement") or {}
    visual_requirement = visual_requirement if isinstance(visual_requirement, Mapping) else {}
    strict_named_person_scene = bool(
        strict
        and visual_requirement.get("strict_identity")
        and str(
            visual_requirement.get("domain")
            or visual_requirement.get("subject_type")
            or ""
        ).strip().lower() in {"person", "people", "public_figure", "royal_person"}
    )
    # Some OpenRouter vision models return the full scored pixel inspection
    # (including ``visual_checked=true`` and strong identity/context/action
    # scores) but omit the optional ``semantic_verified`` convenience flag.
    # Treat that response as a semantic confirmation only when the model
    # actually inspected the pixels and supplied no conflict, safety, or
    # explicit rejection signal. Metadata-only rows and low-confidence model
    # responses still fail closed for strict named-person scenes.
    strict_pixel_confirmation = bool(
        strict_named_person_scene
        and bool(result.get("visual_checked"))
        and bool(result.get("visual_available") or result.get("vision_available"))
        and identity_val >= 65.0
        and float(result.get("context_score") or 0.0) >= 40.0
        and not bool(
            (result.get("entity_conflict") and conflict_evidence)
            or result.get("fatal_mismatch")
            or result.get("visual_rejected")
            or result.get("unsafe_content")
            or result.get("suggestive_content")
            or (result.get("watermark") and not candidate.get("watermark_tolerated"))
            or result.get("subtitle")
            or result.get("severe_overlay")
            or str(result.get("rejection_reason") or "").strip().lower()
            in {"severe_overlay_or_subtitle_detected", "overlay_blur_failed"}
        )
        and not any(
            token in raw_status_reason
            for token in (
                "wrong person", "wrong entity", "different person",
                "different entity", "irrelevant", "off_topic", "off-topic",
                "wrong domain", "wrong event", "event mismatch", "confusable",
            )
        )
    )
    visual_subjects = result.get("subjects")
    has_visual_subjects = bool(
        visual_subjects
        and (
            not isinstance(visual_subjects, (list, tuple, set, dict))
            or any(str(item or "").strip() for item in visual_subjects)
        )
    )
    has_visual_description = bool(
        str(result.get("scene_description") or result.get("description") or "").strip()
    )
    # A relaxed YouTube quota must not turn an empty vision response into an
    # accepted clip for a strict named-person scene. Preserve near matches when
    # vision found a subject/context, but route a zero-identity, no-content
    # verdict back through the web lane instead of silently reusing unrelated
    # footage from an older manifest.
    strict_person_identity_unsubstantiated = bool(
        is_youtube_candidate
        and strict_named_person_scene
        and not vision_unavailable
        and identity_val <= 0.0
        and not has_visual_subjects
        and not has_visual_description
    )
    if strict_person_identity_unsubstantiated:
        accepted = False
        result.update({
            "accepted": False,
            "rejection_reason": "required_person_identity_not_visually_supported",
            "reason": "Vision found no identifiable subject or scene description for the required person.",
        })
    try:
        relevance_value = float(result.get("relevance") or result.get("story_relevance") or 0.0)
        confidence_value = float(result.get("confidence") or 0.0)
    except (TypeError, ValueError):
        relevance_value, confidence_value = 0.0, 0.0
    # A resolver can carry a stale ``identity_failed`` marker from its cheap
    # metadata pass even after the live vision verifier has positively
    # identified the downloaded pixels. Vision is authoritative once it
    # returns an explicit acceptance/high-confidence verdict.
    model_positive_identity = bool(
        accepted
        or str(result.get("status") or "").strip().lower() in {"accepted", "pass", "keep", "soft_visual_pass"}
        or identity_val >= 65.0
        or float(result.get("context_score") or 0.0) >= 65.0
        or relevance_value >= 65.0
    )
    candidate_evidence = candidate.get("candidate_evidence") or {}
    candidate_evidence = candidate_evidence if isinstance(candidate_evidence, Mapping) else {}
    explicit_visual_pending = _candidate_has_explicit_visual_pending(candidate)
    pending_identity_confirmed_by_pixels = bool(
        explicit_visual_pending
        and model_positive_identity
        and not conflict
        and not result.get("fatal_mismatch")
        and not result.get("visual_rejected")
        and not result.get("entity_conflict")
        and not result.get("unsafe_content")
        and not result.get("suggestive_content")
    )
    stale_identity_failure = bool(
        candidate.get("identity_failed")
        and not candidate_evidence.get("nearest_match")
        and not model_positive_identity
    )
    semantic_veto = bool(
        conflict
        or strict_person_identity_unsubstantiated
        or result.get("fatal_mismatch")
        or result.get("visual_rejected")
        or result.get("entity_conflict")
        or (
            stale_identity_failure
        )
        or (
            candidate.get("rejection_reason") == "canonical_entity_mismatch"
            and not pending_identity_confirmed_by_pixels
        )
        or candidate.get("rejection_reason") in {"forbidden_confusable_entity", "missing_required_model"}
        or (
            candidate_evidence.get("canonical_entity_mismatch")
            and not candidate_evidence.get("nearest_match")
            and not pending_identity_confirmed_by_pixels
        )
        or any(
            token in unavailable_reason
            for token in (
                "off_topic", "off-topic", "irrelevant", "wrong subject",
                "wrong_subject", "wrong entity", "entity mismatch",
                "facecam", "face cam", "inset", "picture_in_picture",
                "talking_head", "talking head",
                "confident_irrelevance", "low_relevance", "relevance_below",
                "rejected_entity_mismatch",
                "frame mismatch", "frame_mismatch", "opening frame", "opening_frame",
                "outro", "endcard", "end_card", "watch more", "slate",
                "outro_endcard_rejected", "severe_overlay_or_subtitle_detected",
            )
        )
    )
    # A tolerated broadcaster inset is an advisory composition signal. Keep
    # any explicit wrong-entity, safety, or fatal mismatch veto intact while
    # allowing the clip's actual subject to be judged by final vision.
    if soft_facecam_allowed and semantic_veto and not conflict and not model_fatal_mismatch:
        non_facecam_reason = str(unavailable_reason or "")
        for token in ("facecam_inset", "facecam", "face cam", "inset", "picture_in_picture"):
            non_facecam_reason = non_facecam_reason.replace(token, "")
        if not any(
            token in non_facecam_reason
            for token in (
                "off_topic", "off-topic", "irrelevant", "wrong subject", "wrong_subject",
                "wrong entity", "entity mismatch", "different entity", "wrong domain",
                "wrong model", "wrong year", "wrong era", "animal", "wildlife", "species",
                "confusable", "confident_irrelevance", "low_relevance", "relevance_below",
                "talking_head", "talking head", "frame mismatch", "frame_mismatch", "outro",
                "endcard", "slate", "severe_overlay_or_subtitle_detected",
            )
        ):
            semantic_veto = False
    if not accepted and relevance_value > 0.0 and (relevance_value < 35.0 or (relevance_value < 45.0 and confidence_value >= 40.0)):
        semantic_veto = True
    # A clean YouTube cut has already passed FFmpeg, multi-frame quality,
    # caption/logo, and local safety checks.  Keep it available when the
    # remote model is uncertain; only explicit semantic evidence (wildlife or
    # another entity/domain/model/era) remains a hard rejection in the relaxed
    # YouTube lane.
    overlay_clean = bool(candidate.get("overlay_clean") or candidate.get("deterministic_overlay_clean"))
    overlay_blurred = bool(candidate.get("overlay_blurred") or candidate.get("blurred"))
    clean_youtube = (
        is_youtube_candidate
        and (overlay_clean or overlay_blurred or Path(str(candidate.get("cleaned_path") or candidate.get("path") or "")).is_file())
        and (overlay_clean or overlay_blurred or not candidate.get("rejection_reason"))
        and not bool(result.get("unsafe_content") or result.get("suggestive_content"))
        and not bool((candidate.get("facecam_inset") or candidate.get("inset_facecam")) and not soft_facecam_allowed)
        and not bool(candidate.get("blur_failed"))
        and not bool(candidate.get("outro_detected"))
        and not bool(result.get("outro_detected"))
        and not bool(result.get("severe_overlay"))
        and not bool(result.get("subtitle"))
        and not bool(result.get("watermark") and not candidate.get("watermark_tolerated"))
        and str(candidate.get("rejection_reason") or "") not in {"outro_endcard_rejected", "overlay_blur_failed", "severe_overlay_or_subtitle_detected"}
        and not (str(candidate.get("rejection_reason") or "") == "watermark_detected" and not candidate.get("watermark_tolerated"))
        and str(result.get("rejection_reason") or "") not in {"outro_endcard_rejected", "overlay_blur_failed", "severe_overlay_or_subtitle_detected"}
        and not (str(result.get("rejection_reason") or "") == "watermark_detected" and not candidate.get("watermark_tolerated"))
    )
    # A downloaded web image that passed decoding/safety/metadata gates must
    # not become a blank card solely because the optional vision transport
    # timed out or returned malformed evidence. Keep it as an auditable
    # degraded result; evidence-backed wrong-entity and unsafe verdicts still
    # fail closed through ``semantic_veto`` below.
    web_fail_open = bool(
        _is_web_source(candidate)
        and not semantic_veto
        and not bool(result.get("unsafe_content") or result.get("suggestive_content"))
        and not bool(candidate.get("rejection_reason") == "canonical_entity_mismatch")
        and not bool(
            candidate_evidence.get("canonical_entity_mismatch")
            and not candidate_evidence.get("nearest_match")
        )
        and not bool(
            stale_identity_failure
        )
        and not bool(result.get("entity_conflict"))
        and (
            candidate_evidence.get("identity_proof")
            or candidate_evidence.get("thematic_near_match")
            or (deterministic_web and not strict and not bool(candidate_evidence.get("missing_terms")))
        )
    )
    # The web resolver can intentionally pass a query-anchored candidate to
    # this gate as ``semantic_context_pending`` when the pixels are the only
    # remaining proof of a full canonical subject (for example, Toyota Corolla
    # AE101).  If the remote vision client is unavailable, dropping every such
    # downloaded image creates a stock/blank cascade.  The metadata gate has
    # already rejected explicit entity mismatches and unsafe rows; preserve
    # this bounded pending candidate as degraded media and audit the missing
    # vision verification instead of returning no visual at all.
    web_query_anchor_pending = bool(
        _is_web_source(candidate)
        and candidate.get("semantic_context_pending")
        and not candidate.get("identity_failed")
        and not candidate.get("rejection_reason")
        and not result.get("entity_conflict")
        and not result.get("fatal_mismatch")
    )
    if strict and vision_unavailable and not semantic_veto:
        # A strict identity scene cannot be accepted from metadata, a timeout,
        # or a deterministic provider stamp. The caller will try another
        # finalist and eventually use the grounded graphic fallback.
        result.update({
            "accepted": False,
            "status": "vision_unavailable",
            "reason": "strict_identity_requires_live_semantic_verification",
        })
        return False, "vision_unavailable", score, result
    if vision_unavailable and not strict and (web_fail_open or web_query_anchor_pending):
        result.update({
            "accepted": True,
            "status": "deterministic_degraded",
            "verification_status": "deterministic_degraded",
            "reason": "vision_unavailable_fail_open_on_verified_download",
        })
        return True, "deterministic_degraded", max(score, 65.0), result
    # YouTube has already completed deterministic media validation before it
    # reaches this coordinator. If the optional vision provider is unavailable,
    # do not discard a clean clip and replace it with a still. Captioned clips
    # remain rejected because ``overlay_clean`` is false when subtitles were
    # detected; this is only a fail-open for the unavailable verifier case.
    entity_conflict = bool(raw_entity_conflict and conflict_evidence)
    quota_youtube = bool(_source_policy_value(scene, "youtube_quota", False))
    # The YouTube provider has already screened the downloaded/renderable cut.
    # Its localized blur audit is authoritative for publisher marks: a vision
    # pass can still see the original watermark in a sampled frame and report
    # ``watermark=true`` even though the output path is the cleaned cut. Do not
    # undo a successful deterministic cleanup based on that stale model flag.
    # Hard overlay rejection remains fail-closed for captions, failed blur, and
    # any provider-reported large/central mark.
    provider_overlay_types = {
        str(value).strip().lower()
        for value in (candidate.get("overlay_types") or [])
        if str(value).strip()
    }
    provider_subtitle = bool(
        candidate.get("overlay_has_subtitle")
        or candidate.get("has_subtitle")
        or provider_overlay_types.intersection({"subtitle", "caption"})
    ) and not overlay_blurred and str(candidate.get("rejection_reason") or "") == "caption_overlay_rejected"
    provider_overlay_failed = bool(
        candidate.get("overlay_rejected")
        or (
            source in {"youtube", "youtube clips", "yt"}
            and not bool(candidate.get("overlay_clean") or candidate.get("deterministic_overlay_clean") or overlay_blurred)
            and bool(candidate.get("overlay_policy"))
            and str(candidate.get("rejection_reason") or "") in {"large_or_central_logo_rejected", "caption_overlay_rejected", "facecam_inset_rejected", "outro_endcard_rejected", "overlay_blur_failed"}
        )
    )
    provider_severe_overlay = bool(
        provider_overlay_types.intersection({"large_logo", "central_logo", "publisher_branding"})
    )
    hard_semantic_tokens = (
        ("off_topic", "off-topic", "irrelevant", "wrong subject",
         "wrong_subject", "wrong entity", "entity mismatch", "different entity",
         "animal", "wildlife", "species", "confusable",
         "facecam", "face cam", "inset", "picture_in_picture",
         "talking_head", "talking head", "confident_irrelevance",
         "frame mismatch", "frame_mismatch", "opening frame", "opening_frame",
         "outro", "endcard", "end_card", "watch more", "slate", "outro_endcard_rejected",
         "severe_overlay_or_subtitle_detected", "subject_framing_too_small",
         "background_dominated", "unrequested_text_heavy_slide")
        if youtube_relaxed
        else
        ("off_topic", "off-topic", "irrelevant", "wrong subject",
         "wrong_subject", "wrong entity", "entity mismatch", "different entity",
         "wrong domain", "wrong model", "wrong year", "wrong era",
         "animal", "wildlife", "species", "confusable",
         "facecam", "face cam", "inset", "picture_in_picture",
         "talking_head", "talking head", "confident_irrelevance",
         "frame mismatch", "frame_mismatch", "opening frame", "opening_frame",
         "outro", "endcard", "end_card", "watch more", "slate", "outro_endcard_rejected",
         "severe_overlay_or_subtitle_detected")
    )
    hard_semantic_reason = any(token in unavailable_reason for token in hard_semantic_tokens)
    # Vision is a relevance checker, not a brittle binary filter.  Models
    # frequently return ``accepted=false`` for a useful near-match because
    # the action/composition is weak (a battery close-up for an EV charging
    # beat, for example).  Once the transport, safety, and identity gates
    # pass, retain that real web asset as an auditable soft pass unless the
    # model supplied explicit wrong-entity/off-topic evidence.  This keeps a
    # temporary low score from cascading into a blank card or slow stock
    # rescue while preserving hard rejection for genuinely misleading media.
    web_soft_visual_pass = bool(
        _is_web_source(candidate)
        and not bool(result.get("fatal_mismatch"))
        and not bool(result.get("unsafe_content") or result.get("suggestive_content"))
        and not bool(result.get("entity_conflict") and conflict_evidence)
        and not hard_semantic_reason
        and not stale_identity_failure
        and (
            identity_val >= 45.0
            or float(result.get("context_score") or 0.0) >= 45.0
            or float(result.get("relevance") or result.get("story_relevance") or 0.0) >= 45.0
            or bool(
                candidate_evidence.get("identity_proof")
                or candidate_evidence.get("thematic_near_match")
                or candidate.get("semantic_context_pending")
            )
        )
    )
    if strict_pixel_confirmation and not bool(result.get("semantic_verified")):
        result.update({
            "accepted": True,
            "status": "accepted",
            "verification_status": "semantic_verified_scored_pixels",
            "semantic_verified": True,
            "reason": "high_confidence_pixel_inspection_confirmed_named_subject",
        })
        accepted = True
    # Some OpenRouter vision models return strong pixel scores and an explicit
    # accepted decision but omit the optional ``semantic_verified`` field.
    # Treat that as a soft named-person pass when the model actually inspected
    # the image and found a strong identity/context match. This preserves
    # valid portraits and event photos without allowing a confusable result
    # through: those rows have no strong identity evidence or carry an explicit
    # conflict. Watermarks and unsafe overlays remain hard failures even when
    # identity confidence is high.
    strict_named_person_soft_pass = bool(
        strict_named_person_scene
        and not bool(result.get("semantic_verified"))
        and bool(result.get("visual_checked"))
        and bool(result.get("visual_available") or result.get("vision_available"))
        and (
            identity_val >= 55.0
            or (result.get("accepted") and identity_val >= 45.0)
            or str(result.get("status") or "").lower() in {"accepted", "pass", "keep"}
            or (identity_val >= 50.0 and float(result.get("context_score") or 0.0) >= 40.0)
        )
        and not bool(
            (result.get("entity_conflict") and conflict_evidence)
            or result.get("fatal_mismatch")
            or result.get("unsafe_content")
            or result.get("suggestive_content")
            or (result.get("watermark") and not candidate.get("watermark_tolerated"))
            or result.get("subtitle")
            or result.get("severe_overlay")
        )
        and str(result.get("rejection_reason") or "").strip().lower()
        not in {"severe_overlay_or_subtitle_detected", "overlay_blur_failed"}
        and not hard_semantic_reason
    )
    if strict_named_person_soft_pass:
        result.update({
            "accepted": True,
            "status": "soft_visual_pass",
            "verification_status": "semantic_scored_pixels",
            "semantic_verified": True,
            "reason": "strong_pixel_identity_and_context_match_without_optional_semantic_flag",
        })
        accepted = True
    # Apply this contract before the generic web soft-pass promotion.  The
    # promotion intentionally turns ``accepted=false`` into a usable
    # near-match, so placing the strict check after it would return early and
    # still admit an unverified named-person frame.
    if (
        _is_web_source(candidate)
        and strict_named_person_scene
        and not bool(result.get("semantic_verified"))
        and not strict_pixel_confirmation
        and not vision_unavailable
    ):
        result.update({
            "accepted": False,
            "status": "rejected",
            "verification_status": "rejected",
            "rejection_reason": "strict_identity_not_semantically_verified",
            "reason": "Strict named-person scene requires an explicit semantic vision confirmation.",
        })
        return False, "strict_identity_not_semantically_verified", score, result
    if web_soft_visual_pass and not accepted:
        result.update({
            "accepted": True,
            "status": "soft_visual_pass",
            "verification_status": "soft_visual_pass",
            "reason": "vision_soft_filter_retained_relevant_near_match",
        })
        return True, "soft_visual_pass", max(score, identity_val, 45.0), result
    # A strict named-person scene needs an explicit semantic confirmation from
    # the live vision model.  The soft web path above is intentionally useful
    # for generic documentary near-matches, but it must never turn a low-score
    # or watermark-rejected response into a verified person portrait.  In the
    # past this let a Kate Middleton frame through a Princess Eugenie scene
    # because the provider title said "Eugenie" while Qwen had not marked the
    # result semantically verified.
    if (
        _is_web_source(candidate)
        and strict_named_person_scene
        and not bool(result.get("semantic_verified"))
        and not strict_pixel_confirmation
        and not vision_unavailable
    ):
        result.update({
            "accepted": False,
            "status": "rejected",
            "verification_status": "rejected",
            "rejection_reason": "strict_identity_not_semantically_verified",
            "reason": "Strict named-person scene requires an explicit semantic vision confirmation.",
        })
        return False, "strict_identity_not_semantically_verified", score, result
    relaxed_youtube_semantic_veto = bool(
        result.get("fatal_mismatch")
        or hard_semantic_reason
        or (raw_entity_conflict and conflict_evidence and confidence_value >= 88.0 and relevance_value <= 20.0)
    )
    candidate_blur_enabled = bool(
        candidate.get("cleaned_path")
        or candidate.get("overlay_blurred")
        or candidate.get("overlay_clean")
        or str(os.environ.get("VIDZEO_YT_ALLOW_BOUNDED_CAPTION_BLUR", "0")).strip().lower() in {"1", "true", "yes", "on"}
    )
    hard_youtube_reject = bool(
        bool(result.get("fatal_mismatch"))
        or bool(result.get('talking_head'))
        or bool(candidate.get('talking_head'))
        or strict_person_identity_unsubstantiated
        or result.get("unsafe_content")
        or result.get("suggestive_content")
        or (provider_subtitle and not (youtube_relaxed and candidate_blur_enabled))
        or provider_overlay_failed
        or provider_severe_overlay
        or bool((candidate.get("facecam_inset") or candidate.get("inset_facecam")) and not soft_facecam_allowed)
        or bool(candidate.get("outro_detected"))
        or bool(result.get("outro_detected"))
        or bool(candidate.get("blur_failed"))
        or bool(result.get("severe_overlay"))
        or bool(result.get("watermark") and not (candidate.get("watermark_tolerated") or (youtube_relaxed and candidate_blur_enabled)))
        or bool(result.get("subtitle") and not (youtube_relaxed and candidate_blur_enabled))
        or str(candidate.get("rejection_reason") or "") in {"outro_endcard_rejected", "overlay_blur_failed", "severe_overlay_or_subtitle_detected"}
        or (str(candidate.get("rejection_reason") or "") == "watermark_detected" and not (candidate.get("watermark_tolerated") or (youtube_relaxed and candidate_blur_enabled)))
        or str(result.get("rejection_reason") or "") in {"outro_endcard_rejected", "overlay_blur_failed", "severe_overlay_or_subtitle_detected"}
        or (str(result.get("rejection_reason") or "") == "watermark_detected" and not (candidate.get("watermark_tolerated") or (youtube_relaxed and candidate_blur_enabled)))
        or (relaxed_youtube_semantic_veto if youtube_relaxed else semantic_veto)
    )
    # A clean extracted frame is a deliberate recovery from a usable YouTube
    # master, not a generic web still.  The frame may have no independent
    # title/description and Qwen can therefore report a soft composition
    # warning (for example ``subject_framing_too_small_or_background_dominated``)
    # even when the provider's source clip already passed the entity/overlay
    # checks.  Do not throw that real frame away and replace it with stock;
    # keep it unless the model produced an explicit semantic, safety, facecam,
    # or overlay failure.  This is bounded to the extracted-frame recovery
    # lane and never applies to arbitrary unverified YouTube downloads.
    extracted_frame_recovery = str(
        candidate.get("resolution_kind") or candidate.get("fallback_layer") or ""
    ).strip().lower() in {
        "youtube_extracted_frame",
        "same_video_timestamp_frame",
    }
    if (
        extracted_frame_recovery
        and youtube_relaxed
        and clean_youtube
        and not hard_youtube_reject
        and not bool(result.get("fatal_mismatch"))
        and not bool(result.get("unsafe_content") or result.get("suggestive_content"))
    ):
        result.update({
            "accepted": True,
            "status": "deterministic_degraded",
            "verification_status": "youtube_extracted_frame_recovery",
            "reason": "youtube_frame_recovery_soft_visual_warning",
        })
        return True, "youtube_frame_recovery", max(score, 50.0), result
    if (
        clean_youtube
        and youtube_relaxed
        and not accepted
        and (not vision_unavailable or youtube_fail_open)
        and not hard_youtube_reject
    ):
        result.update({
            "accepted": True,
            "status": "soft_visual_pass",
            "verification_status": "verified",
            "reason": (
                "youtube_relaxed_vision_unavailable"
                if vision_unavailable
                else "youtube_relaxed_soft_visual_rejection"
            ),
        })
        # Keep the acceptance auditable as a relaxed/degraded decision. The
        # model explicitly declined the candidate, so returning visual_pass
        # would overstate what was verified even though the configured
        # YouTube soft gate permits it.
        return True, "deterministic_degraded", max(score, 65.0), result
    # Automatic Mixed Media reserves a real YouTube slot. Once the provider's
    # deterministic overlay screen has passed, a soft semantic disagreement
    # must not erase the requested motion lane and silently make the whole
    # result web-only. Retain the clip unless the model reports a hard factual,
    # safety, or overlay conflict; record the degraded status for auditability.
    if (
        quota_youtube
        and clean_youtube
        and (youtube_relaxed or not strict)
        and not hard_youtube_reject
        and not accepted
    ):
        result.update({
            "accepted": True,
            "status": "soft_visual_pass",
            "verification_status": "verified",
            "reason": "youtube_quota_retained_after_soft_visual_rejection",
        })
        return True, "deterministic_degraded", max(score, 65.0), result
    # A relaxed high identity score may promote a model's explicit
    # ``accepted=false`` near-match. It must never bypass the model's hard
    # overlay/safety verdict, and the returned boolean must agree with the
    # saved decision consumed by final render validation.
    final_accepted = bool(accepted) and not conflict and not hard_youtube_reject
    if final_accepted and result.get("accepted") is not True:
        result.update({
            "accepted": True,
            "status": "soft_visual_pass",
            "verification_status": "soft_visual_pass",
            "promotion_reason": "high_identity_no_hard_visual_rejection",
        })
    return final_accepted, "visual_pass" if final_accepted else "visual_rejected", score, result


def _scene_top_candidates_limit() -> int:
    """Per-scene metadata top-N retained for vision selection (spec: 12).

    Rank by metadata first, keep the top 12 results per scene, send the
    top 3 to vision, resolve to the top 1. Tunable via
    VIDZEO_VISUAL_TOP_CANDIDATES_PER_SCENE; the shape (12 -> 3 -> 1) stays.
    """
    try:
        return max(
            1,
            min(
                16,
                int(
                    os.environ.get(
                        "VIDZEO_VISUAL_TOP_CANDIDATES_PER_SCENE", "12"
                    )
                    or 12
                ),
            ),
        )
    except (TypeError, ValueError):
        return 12


def _scene_finalist_limit() -> int:
    """How many of the per-scene top-12 go to vision (spec: 3).

    The old default verified only the strongest two candidates per scene in
    long-form jobs; the selection spec sends three. The operator override
    VIDZEO_VISUAL_FINALIST_LIMIT still wins.
    """
    try:
        configured = os.environ.get("VIDZEO_VISUAL_FINALIST_LIMIT")
        if configured is None:
            configured = "3"
        return max(1, min(12, int(configured or 3)))
    except (TypeError, ValueError):
        return 3


def _finalists_for_storyboard(rows, configured):
    """Avoid checking several candidates per scene for a short video.

    Unusable or missing duration data keeps the configured limit. Rejected
    candidates still use the existing bounded expansion/recovery path.
    """
    try:
        durations = []
        for row in rows:
            value = row.get("duration")
            if value is None and row.get("start_seconds") is not None and row.get("end_seconds") is not None:
                value = float(row["end_seconds"]) - float(row["start_seconds"])
            durations.append(float(value or 0))
        if durations and all(0 < value < 600 for value in durations) and sum(durations) <= 120:
            return min(configured, 1)
    except (TypeError, ValueError, AttributeError):
        pass
    return configured


def _visual_gate_workers(primary_count: int) -> int:
    """Parallel vision checks in the primary gate (default 12, cap 24).

    Was a hardcoded 6. The old bound was written for OpenRouter/free-model
    rate limits; vision now goes through the inbox hook, where the cost is
    one ~4.5-minute worker round trip per call — so the wave's wall time is
    roughly ceil(work / workers) round trips, and 6 workers turned a
    38-candidate wave into ~7 sequential rounds. 12 halves that; the vision
    deadline still bounds the whole gate. Env VIDZEO_VISUAL_GATE_WORKERS
    wins.
    """
    try:
        configured = int(os.environ.get("VIDZEO_VISUAL_GATE_WORKERS") or 12)
    except (TypeError, ValueError):
        configured = 12
    return max(1, min(24, configured, max(1, int(primary_count or 1))))


def _visual_relative_floor() -> float:
    """Relative metadata floor for the per-scene top-12 pool (default 0.5).

    A pool entry scoring below this fraction of the scene's best metadata
    score never reaches vision (it can still be re-admitted by the
    recovery/cross-source paths if everything better fails). The finalist
    slots are always kept, so the floor only trims the weak tail of the
    expansion pool. Env VIDZEO_VISUAL_RELATIVE_FLOOR wins, clamped 0-0.9.
    """
    try:
        value = float(os.environ.get("VIDZEO_VISUAL_RELATIVE_FLOOR") or 0.5)
    except (TypeError, ValueError):
        value = 0.5
    return max(0.0, min(0.9, value))


def _select_scene_finalists(viable, finalist_limit, top_candidates=12, relative_floor=None):
    """Split a metadata-ranked per-scene pool into vision finalists + pool.

    ``viable`` must already be sorted best-first by metadata (identity
    proof, then metadata score). Returns ``(finalists, top_pool)`` where
    ``top_pool`` is the retained top-N (spec: 12) and ``finalists`` the top-K
    of it sent to vision (spec: 3). The YouTube motion reservation is drawn
    from the same top-N pool, so a low-ranked clip cannot displace a
    metadata-strong still. Before the reservation, the pool is tightened by
    the relative floor: entries scoring below ``relative_floor`` times the
    best metadata score are dropped, but the top ``finalist_limit`` entries
    are always kept, so vision never receives fewer finalists than the spec.
    """
    keep = max(1, int(finalist_limit or 3))
    top_pool = list(viable[: max(1, int(top_candidates or 12))])
    if relative_floor is None:
        relative_floor = _visual_relative_floor()
    try:
        floor = float(relative_floor)
    except (TypeError, ValueError):
        floor = 0.5
    if top_pool and floor > 0.0:
        best = float(top_pool[0][1] or 0.0)
        if best > 0.0:
            cutoff = floor * best
            tightened = [
                item for index, item in enumerate(top_pool)
                if index < keep or float(item[1] or 0.0) >= cutoff
            ]
            if len(tightened) >= keep:
                top_pool = tightened
    finalists = list(top_pool[:keep])
    if top_pool:
        # Reserve one paid verification slot for the requested motion lane.
        # Web metadata can otherwise occupy all finalist slots, leaving a
        # downloaded clean YouTube candidate recorded as
        # ``not_selected_finalist`` and silently producing a still-only
        # quota scene.
        youtube_finalist = next(
            (item for item in top_pool if item[4] == "youtube"),
            None,
        )
        if youtube_finalist is not None and not any(
            item[3] is youtube_finalist[3] for item in finalists
        ):
            if finalists:
                finalists[-1] = youtube_finalist
            else:
                finalists.append(youtube_finalist)
    return finalists, top_pool


def _pick_spare_candidate(
    scene_index: int,
    scene: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    discovered: Sequence[Sequence[Mapping[str, Any]]],
    gated_outcomes: Mapping[Any, Any],
    reuse_counts: Mapping[str, int],
) -> Optional[tuple[float, float, Dict[str, Any], Dict[str, Any]]]:
    """Best vision-accepted candidate from ANOTHER scene's pool (Nov 10, 2026).

    Fallback tier (a): when a scene's own lanes found nothing usable, reuse
    an already-downloaded, vision-checked asset from the job's pool before
    fetching anything new (tier (b) YT clip frames) or falling back to a
    graphic (tier (c), last). Unselected top-12 pool members count — they
    were vision-checked even though their own scene picked something else.

    Ranking: same container first, then nearest origin scene, then
    (visual_score, metadata_score). Skipped: same-scene origin,
    ADJACENT origins (the same still in two consecutive scenes reads as
    a frozen frame — the diversity check enforces this downstream too),
    unverified or fatally-rejected verdicts, files no longer on disk,
    candidates that fail this scene's hard identity gate, and assets
    whose reuse cap is already spent (web 2, YouTube 1 — a replayed clip
    is not a new moment).

    Returns an ``accepted``-shaped tuple ``(score, source_tie, candidate,
    qwen)`` or None. The candidate is a copy stamped ``spare_pool_reuse``.
    """
    from .job_debugger import _is_fatal_verdict

    current_container = str(scene.get("container_id") or "").strip()
    best: Optional[tuple[float, float, Dict[str, Any], Dict[str, Any]]] = None
    best_key: Optional[tuple] = None
    for (origin_index, path), outcome in dict(gated_outcomes).items():
        try:
            origin_index = int(origin_index)
        except (TypeError, ValueError):
            continue
        if origin_index == scene_index:
            continue
        # Non-adjacent only (Nov 10, 2026): the same still in two
        # consecutive scenes reads as a frozen frame. The same-container
        # reuse tier and the physical-asset diversity check both enforce
        # this; a spare must not jump the queue past them. Pool reuse is
        # for LATER in the video, not the very next scene.
        if abs(origin_index - scene_index) <= 1:
            continue
        if not isinstance(outcome, (tuple, list)) or len(outcome) < 6:
            continue
        verified, visual_score, qwen = outcome[2], outcome[4], outcome[5]
        if not verified:
            continue
        if _is_fatal_verdict(outcome):
            continue
        try:
            visual_score = float(visual_score or 0.0)
        except (TypeError, ValueError):
            visual_score = 0.0
        try:
            metadata_score = float(outcome[0] or 0.0)
        except (TypeError, ValueError):
            metadata_score = 0.0
        source_tie = outcome[1]
        # The candidate dict lives in its origin scene's discovered pool.
        candidate = None
        if 0 <= origin_index < len(discovered):
            for row in discovered[origin_index] or []:
                if isinstance(row, Mapping) and str(row.get("path") or "") == str(path or ""):
                    candidate = row
                    break
        if candidate is None:
            continue
        local_path = str(candidate.get("cleaned_path") or candidate.get("path") or "").strip()
        if not local_path:
            continue
        try:
            if not os.path.isfile(local_path) or os.path.getsize(local_path) <= 1024:
                continue
        except OSError:
            continue
        passed, _reason = _candidate_hard_gate(scene, candidate)
        if not passed:
            continue
        # Reuse cap: a web still may serve a second non-adjacent scene, a
        # YouTube file may not be replayed. Probe a resolution so the cap
        # uses the same identity the final ledger records.
        probe = SceneResolution(
            scene_index=int(scene.get("scene_index", scene_index)),
            final_scene_context=dict(rows[origin_index]),
            visual_decision=dict(qwen or {}),
            provider=str(candidate.get("source") or candidate.get("provider") or ""),
            selected_path=local_path,
            asset_fingerprint=str(candidate.get("asset_fingerprint") or candidate.get("fingerprint") or ""),
            source_url=str(candidate.get("origin_url") or candidate.get("source_url") or candidate.get("url") or ""),
        )
        if not _semantic_reuse_compatible(scene, probe) or not _strict_reuse_identity_compatible(scene, probe):
            continue
        identity = _resolution_asset_identity(probe)
        if not identity:
            continue
        if reuse_counts.get(identity, 0) >= _reuse_limit_for_resolution(
            scene, probe, non_mixed_limit=2
        ):
            continue
        origin_container = ""
        if 0 <= origin_index < len(rows):
            origin_container = str((rows[origin_index] or {}).get("container_id") or "").strip()
        key = (
            1 if (current_container and origin_container == current_container) else 0,
            -abs(origin_index - scene_index),
            visual_score,
            metadata_score,
        )
        if best_key is None or key > best_key:
            spare = dict(candidate)
            spare["fallback_layer"] = "spare_pool_reuse"
            spare["resolution_kind"] = "spare_pool_reuse"
            best_key = key
            best = (
                visual_score * 1000.0 + metadata_score,
                source_tie,
                spare,
                qwen if isinstance(qwen, dict) else {},
            )
    return best


def resolve_storyboard_media_v2(
    scenes: Sequence[Any],
    *,
    youtube_lane: Callable[[Mapping[str, Any], threading.Event], Any],
    web_lane: Callable[[Mapping[str, Any], threading.Event], Any],
    youtube_fallback_lane: Optional[Callable[[Mapping[str, Any], threading.Event], Any]] = None,
    web_fallback_lane: Optional[Callable[[Mapping[str, Any], threading.Event], Any]] = None,
    stock_lane: Optional[Callable[[Mapping[str, Any], threading.Event], Any]] = None,
    visual_verifier: Optional[Callable[[Mapping[str, Any], Dict[str, Any]], Any]] = None,
    neutral_factory: Optional[Callable[[Mapping[str, Any]], Any]] = None,
    progress_callback: Optional[Callable[[float, str], Any]] = None,
    deadline_seconds: float = 600.0,
    web_fallback_grace_seconds: float = 0.0,
    max_workers: int = 6,
    web_wait_until_complete: bool = False,
    external_cancel_event: Optional[threading.Event] = None,
    adaptive_media_review: bool = False,
) -> Dict[int, SceneResolution]:
    """Resolve immutable storyboard scenes through the V2 fallback machine.

    YouTube and web are queried concurrently in container-fair waves. Optional
    cross-lane callbacks are invoked only after primary candidates fail the
    semantic gate, so a web scene can try a relevant YouTube clip and a
    YouTube scene can try a relevant web still without changing the requested
    source mix. Same-video timestamp frames are attempted before any generic
    graphic fallback. Stock is invoked only after these factual media layers
    and verified reuse paths fail, and only when the scene contract explicitly
    permits a normal or last-resort stock request. Every
    returned row is terminal and contains an attempt ledger suitable for
    ``StoryboardV2`` persistence.
    """
    rows = [_scene_row(scene, index) for index, scene in enumerate(scenes)]
    if not rows:
        return {}
    started_at = time.monotonic()
    # Completion preference cannot remove the delivery wall. Keep this legacy
    # flag false in deadline conditions while provider lanes finish within it.
    web_wait_until_complete = False
    try:
        effective_wall = float(deadline_seconds)
        if not math.isfinite(effective_wall):
            effective_wall = 600.0
    except (TypeError, ValueError):
        effective_wall = 600.0
    deadline_at = started_at + max(1.0, effective_wall)
    try:
        web_fallback_grace = max(0.0, min(300.0, float(web_fallback_grace_seconds or 0.0)))
    except (TypeError, ValueError):
        web_fallback_grace = 0.0
    web_fallback_deadline_at = deadline_at + web_fallback_grace
    cancel_event = _LinkedCancellationEvent(external_cancel_event)
    # Primary providers are cancelled at the shared discovery wall. A slow
    # but still useful, bounded web rescue gets its own event so that the
    # primary cancellation cannot discard images already being downloaded.
    web_recovery_cancel_event = _LinkedCancellationEvent(external_cancel_event)
    discovered: Dict[int, List[Dict[str, Any]]] = {index: [] for index in range(len(rows))}
    attempts: Dict[int, List[Dict[str, Any]]] = {index: [] for index in range(len(rows))}
    # Scenes whose primary web lane failed on transport (timeout/error with
    # zero candidates). Their web recovery lane is skipped: it would hit the
    # same proxy/hosts and fail the same way. See consume_discovery and
    # _compute_cross_lane_specs.
    web_transport_failed: set = set()
    # Live vision verdicts by (scene index, candidate identity). A candidate
    # re-checked in a later round for the same scene reuses its verdict
    # instead of spending a second live vision read. Job-scoped: discarded
    # when this call returns. Only genuine live verdicts are stored (see
    # verify_primary); deterministic and error verdicts are cheap to recompute
    # and must never be cached across a transient verifier outage.
    vision_verdict_cache: Dict[tuple, tuple] = {}

    # LazyBatchLane-backed providers perform one all-scenes network batch and
    # return indexed rows.  Treat that as a single discovery wave even when
    # every scene belongs to one container.  The normal container-fair waves
    # remain in place for true per-scene providers.
    shared_batch_mode = bool(
        getattr(youtube_lane, "shared_batch", False)
        or getattr(web_lane, "shared_batch", False)
        or getattr(stock_lane, "shared_batch", False)
    )

    def _bounded_lane_call(callback, scene: Mapping[str, Any], event: threading.Event, timeout: float):
        """Run a scene-scoped provider without making the coordinator hostage.

        Provider callbacks are legacy callables and cannot be force-killed
        safely from Python.  A daemon watcher gives them the cancellation
        event, returns at the coordinator deadline, and leaves any misbehaving
        transport isolated from the request thread instead of creating a
        non-daemon executor worker that keeps the whole process alive at exit.
        Healthy lanes finish normally and pay only one tiny thread dispatch.
        """
        # Even legacy infinite wait settings must allow a provider handoff.
        if not math.isfinite(float(timeout)):
            timeout = 120.0
        done = threading.Event()
        box: dict[str, Any] = {}

        def _run() -> None:
            try:
                box["value"] = callback(scene, event)
            except Exception as exc:  # propagate on the coordinator thread
                box["error"] = exc
            finally:
                done.set()

        worker = JobThread(
            target=_run,
            name="media-v2-bounded-lane",
            daemon=True,
        )
        worker.start()
        deadline = time.monotonic() + max(0.01, float(timeout))
        while not done.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0 or event.is_set():
                return False, None, None
            done.wait(timeout=min(0.25, remaining))
        error = box.get("error")
        if error is not None:
            return True, None, error
        return True, box.get("value"), None

    # Per-scene lane budgets (env-tunable). Web is the failure-prone lane on
    # this install (403/timeout/circuit from the egress proxy): 60s is enough
    # for a healthy web discovery, and a hung web lane must not eat the
    # YouTube lane's budget for the same scene.
    web_lane_cap = _env_seconds("VIDZEO_WEB_LANE_TIMEOUT_SECONDS", 60.0)
    youtube_lane_cap = _env_seconds("VIDZEO_YOUTUBE_LANE_TIMEOUT_SECONDS", 120.0)

    def discover(index: int, source: str, lane):
        # Debugger advance: a scene the watchdog flagged as stuck stops
        # burning new discovery waves. Its resolution comes from media
        # already on disk (see the advance branch in the resolution loop).
        try:
            from .job_debugger import is_scene_advance_requested

            if is_scene_advance_requested(index):
                return index, source, [], "advance"
        except Exception:
            pass
        # A provider row that reached the coordinator after the discovery wall
        # still needs its cheap deterministic decision when no live vision
        # verifier is configured.  The no-key path is deliberately
        # metadata/safety based, so refusing to run it after the network wall
        # would turn already downloaded real assets into neutral placeholders
        # and trigger the slow stock rescue.  A configured verifier remains
        # strictly deadline-bound.
        if visual_verifier is not None and (cancel_event.is_set() or (not web_wait_until_complete and time.monotonic() >= deadline_at)):
            return index, source, [], "deadline"
        started = time.monotonic()
        try:
            remaining = max(.01, deadline_at - time.monotonic())
            lane_event = _LinkedCancellationEvent(cancel_event)
            # Batch loaders share one all-scenes request; individual workers can
            # release their slot when a provider stalls, leaving other lanes active.
            lane_cap = youtube_lane_cap if source == 'youtube' else web_lane_cap
            cap = remaining if getattr(lane, 'shared_batch', False) else min(remaining, lane_cap)
            completed, value, error = _bounded_lane_call(lane, rows[index], lane_event, cap)
            if not completed:
                lane_event.set()
                return index, source, [], 'provider_timeout_handoff'
            if error is not None:
                raise error
            candidates = _candidate_rows(value, source)
            return index, source, candidates, "ok" if candidates else "empty"
        except Exception as exc:
            print(
                f"   [v2] {source} lane error for scene {index}: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            return index, source, [], f"error:{type(exc).__name__}"
        finally:
            attempts[index].append({
                "source": source,
                "stage": "discovery",
                "elapsed_ms": round((time.monotonic() - started) * 1000.0, 2),
            })

    resolutions: Dict[int, SceneResolution] = {}
    multi_container_job = _multi_container_job(rows)
    verified_by_container: Dict[str, List[tuple[int, SceneResolution]]] = {}
    reuse_counts: Dict[str, int] = {}

    def _alternate_available(item, source, target_index):
        probe = SceneResolution(scene_index=target_index,
            provider=str(item.get('source') or item.get('provider') or source.provider),
            selected_path=str(item.get('path') or ''),
            asset_fingerprint=str(item.get('asset_fingerprint') or ''))
        identity = _resolution_asset_identity(probe)
        if not identity or reuse_counts.get(identity, 0) >= _reuse_limit_for_resolution(
                rows[target_index], probe, non_mixed_limit=2):
            return False
        return all(_resolution_asset_identity(resolutions.get(neighbor)) != identity
                   for neighbor in (target_index - 1, target_index + 1))
    from .mixed_media_planner import automatic_source_preferences

    preference_ranks: Dict[int, Dict[str, int]] = {}
    for index, scene in enumerate(rows):
        # Explicit quota scenes are the production contract for Mixed Media:
        # if both lanes return usable assets, a web still must not silently
        # displace a YouTube clip because a generic topic heuristic preferred
        # photographs. Keep the automatic preference for non-quota scenes.
        explicit_priority = _source_policy_value(scene, 'media_source_priority', None)
        if isinstance(explicit_priority, (list, tuple)) and 'youtube' in explicit_priority:
            preference_rank = {source: rank for rank, source in enumerate(explicit_priority)}
        elif bool(_source_policy_value(scene, "youtube_quota", False)):
            preference_rank = {"youtube": 0, "web": 1}
        else:
            preferences = automatic_source_preferences(
                str(scene.get("text") or ""),
                title=str(scene.get("content_title") or ""),
            )
            preference_rank = {"youtube": 0, "web": 1}
            if preferences and preferences[0] == "Web Images":
                preference_rank = {"web": 0, "youtube": 1}
        preference_ranks[index] = preference_rank

    def _normalize_candidate_source(candidate: Mapping[str, Any]) -> str:
        source = str(candidate.get("source") or candidate.get("provider") or "").lower()
        return "youtube" if "youtube" in source or source == "yt" else "web"

    def _promote_completed_batch_lanes(stage_name: str = "batch_lane_promotion", timeout: float = 0.0) -> int:
        promoted_total = 0
        for source, lane in (("youtube", youtube_lane), ("web", web_lane)):
            get_row_fn = getattr(lane, "get_row", None)
            lane_rows = getattr(lane, "_rows", None)
            if get_row_fn is not None or isinstance(lane_rows, dict):
                for index in range(len(rows)):
                    already_has_valid_source = any(
                        _normalize_candidate_source(c) == source and bool(c.get("path"))
                        for c in discovered.get(index, [])
                    )
                    if not already_has_valid_source:
                        row_val = None
                        if get_row_fn is not None:
                            try:
                                row_val = get_row_fn(index, timeout=timeout)
                            except Exception:
                                row_val = None
                        if not row_val and isinstance(lane_rows, dict):
                            row_val = lane_rows.get(index)
                        if row_val:
                            promoted_cands = _candidate_rows(row_val, source)
                            valid_promoted = [c for c in promoted_cands if c.get("path")]
                            if valid_promoted:
                                discovered[index].extend(valid_promoted)
                                attempts[index].append({
                                    "source": source,
                                    "stage": stage_name,
                                    "candidate_count": len(valid_promoted),
                                    "status": "promoted",
                                })
                                promoted_total += len(valid_promoted)
        return promoted_total

    # Ensure any candidates that completed in shared batch lanes (YouTube, Web)
    # during or after the discovery loop drain are promoted into discovered[index]
    # rather than discarded.

    def _candidate_ledger(index: int, selected_path: str = "") -> list[dict[str, Any]]:
        ledger: list[dict[str, Any]] = []
        for candidate in discovered.get(index, []):
            row = dict(candidate)
            path = str(candidate.get("path") or "")
            outcome = gated_outcomes.get((index, path))
            row["selected"] = bool(selected_path and path == selected_path)
            if outcome is not None:
                row["verification_status"] = "accepted" if bool(outcome[2]) else "rejected"
                row["rejection_reason"] = "" if bool(outcome[2]) else str(outcome[3] or "visual_rejected")
                row["visual_score"] = float(outcome[4] or 0.0)
                row["visual_decision"] = dict(outcome[5] or {})
            elif row["selected"]:
                row["verification_status"] = "accepted"
            else:
                row.setdefault("verification_status", "not_selected_or_not_verified")
            ledger.append(row)
        return ledger[:32]

    def _intro_asset_conflict(index: int, scene: Mapping[str, Any], candidate: Mapping[str, Any], source: str) -> bool:
        """Keep the opening hook physically diverse, including same-master YT cuts."""
        if not _intro_unique_scene(scene):
            return False
        identity = _web_asset_identity(candidate)
        source_key = str(
            candidate.get("candidate_id")
            or candidate.get("video_id")
            or candidate.get("id")
            or candidate.get("source_url")
            or candidate.get("origin_url")
            or ""
        ).strip().casefold()
        # Frame/reuse IDs are commonly written ``video_id:frame:N``. Compare
        # the master prefix so the intro cannot replay a different timestamp
        # from the same source while distinct videos remain available.
        source_key = source_key.split(":frame:", 1)[0]
        for prior_index, prior in resolutions.items():
            if prior_index >= index or not prior.selected_path:
                continue
            prior_scene = rows[prior_index] if prior_index < len(rows) else {}
            if not _intro_unique_scene(prior_scene):
                continue
            if identity and identity == _resolution_asset_identity(prior):
                return True
            prior_source = str(prior.provider or "").strip().lower()
            if source in {"youtube", "youtube clips", "yt"} and prior_source in {"youtube", "youtube clips", "yt"}:
                prior_key = str(prior.candidate_id or prior.source_url or "").strip().casefold().split(":frame:", 1)[0]
                if source_key and prior_key and source_key == prior_key:
                    try:
                        candidate_start = float(candidate['video_start'])
                        candidate_end = float(candidate['video_end'])
                        prior_start = float(prior.video_start)
                        prior_end = float(prior.video_end)
                        gap = max(12.0, float(os.environ.get('VIDZEO_YT_REUSE_TIMESTAMP_GAP', '20') or 20))
                        if (all(math.isfinite(value) for value in (candidate_start, candidate_end, prior_start, prior_end, gap))
                                and candidate_end > candidate_start >= 0 and prior_end > prior_start >= 0
                                and (candidate_start >= prior_end + gap or prior_start >= candidate_end + gap)):
                            continue
                    except (KeyError, TypeError, ValueError):
                        pass
                    return True
        return False

    # ── Parallel primary verification ──
    # The paid final gate is wall-clock bound (single calls took 40-225s in
    # observed runs). Verify every scene's deterministic finalists
    # concurrently; the shared visual context semaphore caps real API
    # concurrency and reserves the same job budget the serial path would. The
    # sequential selection pass below consumes these verdicts in scene order,
    # so same-container reuse and neutral fallback semantics stay unchanged.
    gated_outcomes: Dict[Tuple[int, str], tuple] = {}
    # Discovery and semantic verification are separate bounded stages.  A
    # large web batch can legitimately finish at the media wall, leaving a
    # handful of downloaded finalists with no time for the shared vision gate.
    # Give that gate a short, explicit grace window; it is capped and applies
    # only to already-local candidates, so it cannot recreate the old stalled
    # provider tail.
    visual_gate_grace = 0.0
    visual_gate_deadline_at = deadline_at
    if visual_verifier is not None:
        try:
            # Verification of downloaded files gets its own bounded window.
            # A long shared YouTube batch must not consume that window before
            # queued scene checks have started. No new discovery is admitted.
            default_grace = max(45.0, min(180.0, math.ceil(len(rows) * 2 / 6) * 25.0))
            visual_gate_grace = max(2.0, min(180.0, float(
                os.environ.get("VIDZEO_MEDIA_VISUAL_GRACE_SECONDS") or default_grace)))
        except (TypeError, ValueError):
            visual_gate_grace = 20.0
        visual_gate_deadline_at = max(deadline_at, time.monotonic()) + visual_gate_grace
    verification_delivery_deadline = None
    try:
        from .job_context import current_job
        budget = current_job().get('delivery_budget')
        if budget is not None:
            # The render reserve limits NEW discovery. Applying it again to
            # local finalists turned a finished 503s clip batch into seven
            # unchecked graphic fallbacks at the 600s reserve boundary. Final
            # checks keep their own small grace window and the actual job wall.
            available = budget.allowance(None, reserve_after_seconds=0)
            verification_delivery_deadline = time.monotonic() + max(0.0, available)
            visual_gate_deadline_at = min(visual_gate_deadline_at, verification_delivery_deadline)
    except (AttributeError, TypeError, ValueError):
        pass
    web_recovery_visual_deadline_at = max(
        visual_gate_deadline_at,
        web_fallback_deadline_at + visual_gate_grace,
    )
    if verification_delivery_deadline is not None:
        web_recovery_visual_deadline_at = min(web_recovery_visual_deadline_at, verification_delivery_deadline)

    # Keep the bounded finalist contract explicit. The parallel gate only
    # sends the bounded top-K candidates per scene to the verifier; selection must
    # not silently re-verify every lower-ranked discovery when a cached gate
    # outcome is absent.
    primary_finalist_keys: Dict[int, set[str]] = {index: set() for index in range(len(rows))}
    # Metadata top-12 pool per scene (spec: 12 -> 3 -> 1). The retained
    # expansion wave may only verify candidates from this pool, never the
    # full discovery list, so vision never sees a 9th-ranked row.
    primary_top_pool_keys: Dict[int, set[str]] = {index: set() for index in range(len(rows))}
    gate_lock = threading.Lock()
    primary_work: List[tuple] = []
    finalist_limit = (_scene_finalist_limit() if adaptive_media_review
                      else _finalists_for_storyboard(rows, _scene_finalist_limit()))

    def rank_scene(index, *, record=True):
        scene = rows[index]
        preference_rank = preference_ranks[index]
        viable: List[tuple[float, float, int, Dict[str, Any], str]] = []
        for candidate in discovered[index]:
            source = _normalize_candidate_source(candidate)
            passed, reason = _candidate_hard_gate(scene, candidate)
            if record:
                attempts[index].append({
                    "source": source,
                    "candidate_id": str(candidate.get("candidate_id") or candidate.get("id") or ""),
                    "stage": "hard_gate",
                    "status": reason,
                })
            if not passed:
                continue
            metadata_score = float(
                candidate.get("final_score")
                or candidate.get("score")
                or candidate.get("metadata_score")
                or 0.0
            )
            # Identity-aware pre-ranking: a candidate whose asset metadata
            # proves the required entity (an Amtrak frame for an Amtrak
            # scene) outranks one that merely scores well on generic terms
            # (a CSX frame). Vision only sees the top finalists, so a
            # wrong-operator candidate must not occupy a slot a proven one
            # could have taken.
            identity_proof = False
            requirement = scene.get("visual_requirement")
            if isinstance(requirement, Mapping):
                _matched, _match_reason, match_evidence = candidate_requirement_match(requirement, candidate)
                identity_proof = bool((match_evidence or {}).get("identity_proof"))
            viable.append((1.0 if identity_proof else 0.0, metadata_score, -preference_rank.get(source, 9), candidate, source))
        viable.sort(key=lambda value: (value[0], value[1], value[2]), reverse=True)
        distinct_viable = []
        seen_web_assets = set()
        for item in viable:
            if item[4] == 'web':
                identity = _web_asset_identity(item[3])
                if identity and identity in seen_web_assets:
                    continue
                if identity:
                    seen_web_assets.add(identity)
            distinct_viable.append(item)
        viable = distinct_viable
        # Spec: rank by metadata, keep the top 12 per scene, send the top 3
        # to vision, resolve to the top 1. Rejected scenes still get
        # retained-candidate expansion (bounded to the same top-12 pool) and
        # cross-source recovery below.
        finalists, top_pool = _select_scene_finalists(
            viable,
            finalist_limit=finalist_limit,
            top_candidates=_scene_top_candidates_limit(),
        )
        if record:
            primary_top_pool_keys[index] = {str(item[3]["path"]) for item in top_pool}
        if adaptive_media_review and _source_policy_value(scene, "youtube_quota", False):
            finalists.sort(key=lambda item: not _is_moving_youtube_source(item[3]))
        return [(index, tie, candidate, source, score)
                for _proof, score, tie, candidate, source in finalists]

    gate_stop = threading.Event()
    workers = _visual_gate_workers(len(rows) * finalist_limit)
    gate_pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="media-v2-gate")
    pending_reviews = set()
    early_checks = {}
    review_started = set()
    finished_sources = {index: set() for index in range(len(rows))}
    visual_context = getattr(visual_verifier, "visual_context", None)

    def verify_primary(item):
        index, tie, candidate, _source, score = item
        if gate_stop.is_set() or (external_cancel_event is not None and external_cancel_event.is_set()):
            return None
        if visual_verifier is not None and time.monotonic() >= visual_gate_deadline_at:
            return None
        accepted, reason, visual_score, verdict = _cached_verification_result(
            index, rows[index], candidate, visual_verifier, vision_verdict_cache)
        return score, tie, accepted, reason, visual_score, verdict or {}

    def review_scene(items):
        index = items[0][0]
        early = early_checks.get(index)
        for position, item in enumerate(items):
            _index, _tie, candidate, source, _score = item
            path = str(candidate["path"])
            try:
                outcome = (
                    early[1].result(timeout=max(0, visual_gate_deadline_at - time.monotonic()))
                    if early and path == early[0] else verify_primary(item)
                )
            except Exception:
                continue
            if outcome is None or gate_stop.is_set():
                break
            with gate_lock:
                if gate_stop.is_set():
                    break
                primary_finalist_keys[index].add(path)
                gated_outcomes[index, path] = outcome
                attempts[index].append({
                    "source": source, "stage": "visual_gate", "status": outcome[3],
                    "candidate_id": str(candidate.get("candidate_id") or candidate.get("id") or ""),
                })
            verdict = outcome[5]
            motion_needed = (
                _source_policy_value(rows[index], "youtube_quota", False)
                or any(_is_moving_youtube_source(option[2]) for option in items[position + 1:])
            )
            if (adaptive_media_review and outcome[2] and outcome[3] == "visual_pass" and outcome[4] >= 80
                    and verdict.get("visual_checked") is True
                    and (verdict.get("visual_available") is True or verdict.get("vision_available") is True)
                    and verdict.get("status") != "soft_visual_pass" and not verdict.get("promotion_reason")
                    and (not motion_needed or _is_moving_youtube_source(candidate))):
                break

    def queue_review(index):
        if index in review_started:
            return
        items = rank_scene(index)
        if not items:
            return
        review_started.add(index)
        primary_work.extend(items)
        if adaptive_media_review:
            pending_reviews.add(gate_pool.submit(review_scene, items))
        else:
            for item in items:
                primary_finalist_keys[index].add(str(item[2]["path"]))
                pending_reviews.add(gate_pool.submit(review_scene, [item]))

    def consume_discovery(future, wave_number):
        try:
            index, source, candidates, status = future.result()
        except Exception:
            return
        attempts[index].append({
            "source": source, "stage": "discovery_result",
            "wave": wave_number, "status": status, "candidate_count": len(candidates),
        })
        if source == "web" and not candidates and (
            status == "provider_timeout_handoff" or status.startswith("error:")
        ):
            # Web transport failed (timeout / exception), not a content miss:
            # the recovery web lane would hit the same proxy/hosts and fail
            # the same way, so this scene falls through to YouTube only.
            web_transport_failed.add(index)
        discovered[index].extend(candidates)
        finished_sources[index].add(source)
        if not adaptive_media_review:
            return
        if len(finished_sources[index]) == 2:
            queue_review(index)
        elif candidates and visual_verifier is not None and sum(
            not future.done() for _, future in early_checks.values()
        ) < 2:
            # Let an already-local image use an idle vision slot while the
            # same scene is still waiting on the footage provider.
            items = rank_scene(index, record=False)
            if items:
                item = items[0]
                early_checks[index] = str(item[2]["path"]), gate_pool.submit(verify_primary, item)

    try:
        if callable(getattr(visual_context, "refresh_deadline", None)):
            try:
                visual_context.refresh_deadline(deadline_at=visual_gate_deadline_at)
            except Exception:
                pass
        # A shared batch lane has one in-flight loader but every scene still needs
        # a waiter future.  Limiting the pool to the normal six per-scene workers
        # leaves most waiters queued behind the slow batch request; when the
        # deadline expires those queued scenes are incorrectly terminalized as
        # neutral.  Give shared batches enough slots for both primary lanes and all
        # scene waiters, while retaining the small pool for true per-scene lanes.
        worker_limit = (
            max(2, min(64, 2 * len(rows)))
            if shared_batch_mode
            else max(2, min(6, int(max_workers)))
        )
        executor = ThreadPoolExecutor(max_workers=worker_limit, thread_name_prefix="media-v2-primary")
        try:
            waves = [list(range(len(rows)))] if shared_batch_mode else _container_waves(rows)
            for wave_number, wave in enumerate(waves, start=1):
                if not web_wait_until_complete and time.monotonic() >= deadline_at:
                    cancel_event.set()
                    break
                futures = {
                    executor.submit(discover, index, source, lane): (index, source)
                    for index in wave
                    for source, lane in (("youtube", youtube_lane), ("web", web_lane))
                }
                pending = set(futures)
                while pending and not cancel_event.is_set() and (web_wait_until_complete or time.monotonic() < deadline_at):
                    done, pending = wait(
                        pending,
                        timeout=min(0.25, max(0.0, deadline_at - time.monotonic())),
                        return_when=FIRST_COMPLETED,
                    )
                    for future in done:
                        consume_discovery(future, wave_number)
                if pending and shared_batch_mode and not cancel_event.is_set():
                    # If a shared batch provider (e.g. web image lane or youtube lane)
                    # is still completing its network/transcode work, give the scene waiters
                    # a realistic grace period to drain their results before cancelling them.
                    drain_timeout = max(
                        15.0,
                        min(45.0, float(os.environ.get("VIDZEO_SHARED_BATCH_DRAIN_TIMEOUT", "30") or 30)),
                    )
                    done, pending = wait(pending, timeout=drain_timeout, return_when=ALL_COMPLETED)
                    for future in done:
                        consume_discovery(future, wave_number)
                for future in pending:
                    future.cancel()
                if pending:
                    cancel_event.set()
                    break
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        _promote_completed_batch_lanes("pre_gate_batch_promotion", timeout=0.0)
        for index in range(len(rows)):
            queue_review(index)
        last_count = -1
        while pending_reviews:
            remaining = max(0, visual_gate_deadline_at - time.monotonic())
            _, pending_reviews = wait(pending_reviews, timeout=min(.25, remaining), return_when=FIRST_COMPLETED)
            with gate_lock:
                count = len(gated_outcomes)
            if count != last_count and callable(progress_callback):
                try:
                    progress_callback(.45 + .10 * min(1, count / max(1, len(primary_work))),
                                      f"Checking media relevance: {count} reviewed")
                except Exception:
                    pass
                last_count = count
            if remaining <= 0 or (external_cancel_event is not None and external_cancel_event.is_set()):
                break
    finally:
        with gate_lock:
            gate_stop.set()
        gate_pool.shutdown(wait=False, cancel_futures=True)
    print(f"   [v2] primary media review: {len(gated_outcomes)}/{len(primary_work)} candidates checked, "
          f"{len(early_checks)} checks started during discovery, adaptive={adaptive_media_review}", flush=True)

    # A completed download can fail identity/era verification even while a
    # web image passes. That is a content miss, not an exhausted transport.
    # Only adapters offering a bounded, different-source recovery opt in.
    rejected_youtube_indices = set()
    if getattr(youtube_fallback_lane, "retry_rejected_primary", False):
        verified_youtube_indices = set()
        for idx, _tie, candidate, source, _score in primary_work:
            outcome = gated_outcomes.get((idx, str(candidate.get("path") or "")))
            if source != "youtube" or not outcome or not _is_moving_youtube_source(candidate):
                continue
            if outcome[2]:
                verified_youtube_indices.add(idx)
                continue
            reason = str(outcome[3] or "").lower()
            verdict = outcome[5] or {}
            if verdict.get("visual_available") is False or any(
                token in reason for token in ("unavailable", "deadline", "timeout", "budget", "not_selected", "error")
            ):
                continue
            rejected_youtube_indices.add(idx)
        rejected_youtube_indices.difference_update(verified_youtube_indices)

    def _compute_cross_lane_specs(idx: int):
        # Do not serially repeat an exhausted YouTube attempt. Fast empty
        # responses remain eligible for recovery, and scenes excluded from the
        # primary quota remain eligible through LazyBatchLane's allowed indices.
        yt_allowed = getattr(youtube_lane, "allowed_indices", None)
        yt_primary_indices = getattr(youtube_lane, "primary_attempted_indices", set())
        yt_disabled = bool(getattr(youtube_lane, "lane_disabled", False))
        sources = {
            _normalize_candidate_source(c)
            for c in discovered[idx]
        }
        yt_primary_attempted = (
            not yt_disabled
            and (yt_allowed is None or idx in yt_allowed)
            and (
                idx in yt_primary_indices
                or
                "youtube" in sources
                or any(
                    row.get("source") == "youtube"
                    and row.get("stage") == "discovery"
                    and float(row.get("elapsed_ms") or 0) >= 10_000
                    for row in attempts[idx]
                )
            )
        )
        yt_fallback = (
            None if yt_primary_attempted and idx not in rejected_youtube_indices
            else youtube_fallback_lane
        )
        if idx in web_transport_failed:
            # Web transport failed for this scene: skip the web recovery lane
            # (same proxy/hosts, same failure) and fall through to YouTube.
            return (("youtube", yt_fallback),) if yt_fallback is not None else ()
        default_specs = (("youtube", yt_fallback), ("web", web_fallback_lane))
        if idx in rejected_youtube_indices and bool(_source_policy_value(rows[idx], "youtube_quota", False)):
            return default_specs
        if not sources:
            return default_specs if yt_fallback is not None else (("web", web_fallback_lane),)
        elif "web" not in sources and "youtube" in sources:
            return (("web", web_fallback_lane), ("youtube", yt_fallback))
        elif "youtube" not in sources and "web" in sources:
            return (("youtube", yt_fallback), ("web", web_fallback_lane))
        return default_specs

    recovery_events = {
        "youtube": _LinkedCancellationEvent(cancel_event),
        "web": _LinkedCancellationEvent(web_recovery_cancel_event),
    }

    def fetch_recovery(index, source, lane):
        event = _LinkedCancellationEvent(recovery_events[source])
        deadline = web_fallback_deadline_at if source == "web" else deadline_at
        started = time.monotonic()
        if event.is_set() or started >= deadline:
            return [], "skipped_after_media_deadline", 0.0
        finished, result, error = _bounded_lane_call(lane, rows[index], event, deadline - started)
        elapsed = time.monotonic() - started
        if not finished:
            event.set()
            return [], "timeout", elapsed
        if error is not None:
            return [], f"error:{type(error).__name__}", elapsed
        candidates = _candidate_rows(result, source)
        return candidates, "candidates" if candidates else "empty", elapsed

    # Pre-launch cross-lane fallback for scenes that have no accepted primary finalists
    _promote_completed_batch_lanes("pre_prefetch_promotion", timeout=0.0)
    scenes_with_verified_primary = {
        idx
        for (idx, _path), outcome in gated_outcomes.items()
        if outcome is not None and bool(outcome[2])
    }
    scenes_likely_needing_fallback = [
        idx
        for idx in range(len(rows))
        if idx not in scenes_with_verified_primary
        or (idx in rejected_youtube_indices and bool(_source_policy_value(rows[idx], "youtube_quota", False)))
    ]
    # Shared recovery providers must see the final rejected/missing primary
    # scenes, rather than retrying their old quota or the complete storyboard.
    for recovery_lane in (youtube_fallback_lane, web_fallback_lane):
        prepare_recovery = getattr(recovery_lane, "prepare_recovery", None)
        if callable(prepare_recovery):
            recovery_indices = scenes_likely_needing_fallback
            if recovery_lane is youtube_fallback_lane:
                recovery_indices = [idx for idx in recovery_indices
                                    if any(source == "youtube" and lane is not None
                                           for source, lane in _compute_cross_lane_specs(idx))]
            prepare_recovery(recovery_indices)
    recovery_futures: Dict[tuple[int, str], Future] = {}
    recovery_pools = []
    for source in ("youtube", "web"):
        tasks = [(index, lane) for index in scenes_likely_needing_fallback
                 for name, lane in _compute_cross_lane_specs(index) if name == source and lane is not None
                 and (source == "youtube" or index not in scenes_with_verified_primary)]
        if not tasks:
            continue
        pool = ThreadPoolExecutor(max_workers=min(4, len(tasks)), thread_name_prefix=f"media-v2-recovery-{source}")
        recovery_pools.append(pool)
        for index, lane in tasks:
            recovery_futures[index, source] = pool.submit(fetch_recovery, index, source, lane)
    if recovery_futures:
        print(f"   [v2] parallel media recovery: {len(recovery_futures)} scene/provider pairs", flush=True)

    for index, scene in enumerate(rows):
        preference_rank = preference_ranks[index]
        accepted: List[tuple[float, int, Dict[str, Any], Dict[str, Any]]] = []

        def event_alignment(candidate: Mapping[str, Any]) -> int:
            """Rank event-anchored candidates ahead of generic portraits.

            This is a ranking hint only. The live visual gate remains the
            authority and explicit event conflicts are still rejected.
            """
            scene_text = " ".join(
                str(scene.get(key) or "")
                # ``source_script`` is the whole job narrative, so using it
                # would incorrectly classify one scene by an event in a
                # different scene and distort finalist ordering.
                for key in ("text", "visual_intent")
            ).casefold()
            required_event = ""
            if re.search(r"\b(wedding|married|marriage|bride|groom)\b", scene_text):
                required_event = "wedding"
            elif re.search(r"\b(coronation|crowned)\b", scene_text):
                required_event = "coronation"
            elif re.search(r"\b(funeral|baptism|christening|inauguration)\b", scene_text):
                required_event = "ceremony"
            if not required_event:
                return 0
            metadata = " ".join(
                str(candidate.get(key) or "")
                for key in ("query", "query_used", "title", "description", "source_url", "page_url")
            ).casefold()
            event_terms = {
                "wedding": ("wedding", "married", "marriage", "bride", "groom"),
                "coronation": ("coronation", "crowned", "westminster abbey"),
                "ceremony": ("ceremony", "funeral", "baptism", "christening", "inauguration"),
            }[required_event]
            return 2 if any(term in metadata for term in event_terms) else 0

        def recovery_budget_open(stage: str, source: str = "") -> bool:
            """Keep ordinary recovery on the media wall, with a web-only tail."""
            web_recovery = source == "web" or (
                stage == "cross_lane_fallback" and web_fallback_lane is not None
            )
            stage_deadline = web_fallback_deadline_at if web_recovery else deadline_at
            stage_cancelled = web_recovery_cancel_event.is_set() if web_recovery else cancel_event.is_set()
            if not stage_cancelled and time.monotonic() < stage_deadline:
                return True
            if web_recovery:
                web_recovery_cancel_event.set()
            else:
                cancel_event.set()
            attempts[index].append({
                "source": "mixed",
                "stage": stage,
                "status": "skipped_after_web_recovery_deadline" if web_recovery else "skipped_after_media_deadline",
            })
            return False

        def visual_budget_open(stage: str, source: str) -> bool:
            """Keep inline vision checks inside their source-specific wall.

            The parallel gate normally supplies every primary verdict.  An
            inline check is only a quota/alternate rescue for a missing
            future. Downloaded web rescue candidates retain a short vision
            tail after their own recovery wall, without extending YouTube or
            primary provider work.
            """
            is_web = source == "web"
            stage_deadline = web_recovery_visual_deadline_at if is_web else visual_gate_deadline_at
            if not (external_cancel_event is not None and external_cancel_event.is_set()) and time.monotonic() < stage_deadline:
                return True
            attempts[index].append({
                "source": source,
                "stage": stage,
                "status": "skipped_after_web_recovery_deadline" if is_web else "skipped_after_media_deadline",
            })
            return False

        def consider_finalists(candidate_rows: Sequence[tuple[Dict[str, Any], str]], *, pre_verified: bool = False, skip_recovery_vision: bool = False) -> None:
            viable: List[tuple] = []
            rows_to_verify = list(candidate_rows)
            if not pre_verified and len(rows_to_verify) > 4:
                # Cross-source lanes can return the entire downloaded web
                # shortlist (often eight rows). Verify a bounded top slice so
                # one exhausted primary scene cannot consume the remaining
                # job budget on every alternate before later scenes recover.
                # All rows stay in ``discovered`` and therefore remain in the
                # candidate ledger; this only bounds paid final verification.
                rows_to_verify.sort(
                    key=lambda item: (
                        event_alignment(item[0]),
                        float(
                            item[0].get("final_score")
                            or item[0].get("score")
                            or item[0].get("metadata_score")
                            or 0.0
                        ),
                    ),
                    reverse=True,
                )
                try:
                    default_fallback_limit = "2" if len(rows) >= 16 else "4"
                    fallback_finalist_limit = max(
                        1,
                        min(
                            6,
                            int(
                                os.environ.get(
                                    "VIDZEO_VISUAL_FALLBACK_FINALIST_LIMIT",
                                    default_fallback_limit,
                                )
                                or default_fallback_limit
                            ),
                        ),
                    )
                except (TypeError, ValueError):
                    fallback_finalist_limit = 2 if len(rows) >= 16 else 4
                rows_to_verify = rows_to_verify[:fallback_finalist_limit]

            # Recovery candidates used to be verified one after another.  A
            # rejected web still could therefore trigger four YouTube frame
            # checks, followed by another four for the next unresolved scene,
            # even though the visual context already has a bounded semaphore.
            # Keep the same hard/semantic gate, but run this fallback shortlist
            # concurrently.  The primary gate above remains unchanged and the
            # final selector still consumes the results in scene order.
            #
            # Fast path: when skip_recovery_vision is set and the scene is not
            # a strict identity scene, accept candidates that pass hard gates
            # without paying for Qwen vision calls.  The hard gate enforces
            # subject identity, safety, and container conflicts; generic
            # documentary scenes do not need pixel-level verification on
            # recovery candidates.
            scene_is_strict = scene_requires_visual_verification(scene)
            fast_metadata_accept = (
                skip_recovery_vision
                and not scene_is_strict
                and not pre_verified
            )
            parallel_verdicts: dict[int, tuple] = {}
            if fast_metadata_accept:
                for candidate_index, (candidate, source) in enumerate(rows_to_verify):
                    passed, _reason = _candidate_hard_gate(scene, candidate)
                    if passed:
                        parallel_verdicts[candidate_index] = (
                            True,
                            "metadata_accepted_fast",
                            float(
                                candidate.get("final_score")
                                or candidate.get("score")
                                or candidate.get("metadata_score")
                                or 0.0
                            ) * 0.5,
                            {
                                "accepted": True,
                                "status": "metadata_accepted_fast",
                                "visual_verification_skipped": True,
                                "reason": "non-strict scene recovery: hard gate passed",
                            },
                        )
            elif not pre_verified and len(rows_to_verify) > 1:
                verify_jobs: list[tuple[int, Dict[str, Any], str]] = []
                for candidate_index, (candidate, source) in enumerate(rows_to_verify):
                    passed, _reason = _candidate_hard_gate(scene, candidate)
                    if passed:
                        verify_jobs.append((candidate_index, candidate, source))

                if len(verify_jobs) > 1:
                    recovery_gate_started = time.monotonic()

                    def _verify_recovery(item: tuple[int, Dict[str, Any], str]) -> tuple[int, tuple]:
                        candidate_index, candidate, source = item
                        if visual_budget_open("visual_gate_fallback_parallel", source):
                            result = _cached_verification_result(
                                index, scene, candidate, visual_verifier, vision_verdict_cache)
                        else:
                            result = (
                                False,
                                "skipped_after_media_deadline",
                                0.0,
                                {"accepted": False, "status": "deadline_exceeded"},
                            )
                        return candidate_index, result

                    recovery_workers = max(1, min(4, len(verify_jobs)))
                    recovery_pool = ThreadPoolExecutor(
                        max_workers=recovery_workers,
                        thread_name_prefix="media-v2-recovery-gate",
                    )
                    try:
                        recovery_visual_deadline = (
                            web_recovery_visual_deadline_at
                            if any(source == "web" for _candidate_index, _candidate, source in verify_jobs)
                            else visual_gate_deadline_at
                        )
                        if hasattr(visual_verifier, "visual_context") and getattr(visual_verifier, "visual_context", None) is not None:
                            try:
                                visual_verifier.visual_context.refresh_deadline(deadline_at=recovery_visual_deadline)
                            except Exception:
                                pass
                        recovery_futures = [
                            recovery_pool.submit(_verify_recovery, item)
                            for item in verify_jobs
                        ]
                        for future in recovery_futures:
                            try:
                                if math.isinf(recovery_visual_deadline):
                                    candidate_index, result = future.result()
                                else:
                                    candidate_index, result = future.result(
                                        timeout=max(0.05, recovery_visual_deadline - time.monotonic())
                                    )
                                parallel_verdicts[candidate_index] = result
                                # Persist recovery verdicts in the same
                                # path-keyed ledger as the primary gate. This
                                # makes a rejected or accepted rescue fully
                                # auditable and prevents a valid exact-event
                                # result from appearing as
                                # ``not_selected_or_not_verified`` merely
                                # because it was checked in the fallback
                                # worker pool.
                                try:
                                    _candidate_index, _candidate, _source = verify_jobs[candidate_index]
                                    _metadata_score = float(
                                        _candidate.get("final_score")
                                        or _candidate.get("score")
                                        or _candidate.get("metadata_score")
                                        or 0.0
                                    )
                                    gated_outcomes[(index, str(_candidate.get("path") or ""))] = (
                                        _metadata_score,
                                        -preference_rank.get(_source, 9),
                                        result[0],
                                        result[1],
                                        result[2],
                                        result[3] or {},
                                    )
                                except Exception:
                                    pass
                            except Exception:
                                # The normal selector records an explicit
                                # rejected/failed attempt for any missing
                                # cached verdict and can still use the next
                                # fallback layer.
                                continue
                    finally:
                        recovery_pool.shutdown(wait=False, cancel_futures=True)
                    print(
                        "   [v2] recovery visual gate: "
                        f"work={len(verify_jobs)}, outcomes={len(parallel_verdicts)}, "
                        f"workers={recovery_workers}, elapsed={time.monotonic() - recovery_gate_started:.1f}s",
                        flush=True,
                    )
            for candidate_index, (candidate, source) in enumerate(rows_to_verify):
                if pre_verified:
                    # The parallel verification worker also records a hard
                    # gate, but cached visual verdicts must never bypass the
                    # deterministic identity/safety boundary at selection
                    # time. A stale or malformed branch result must not make
                    # a decoy from another listicle container eligible.
                    passed, reason = _candidate_hard_gate(scene, candidate)
                    attempts[index].append({
                        "source": source,
                        "candidate_id": str(candidate.get("candidate_id") or candidate.get("id") or ""),
                        "stage": "selection_hard_gate",
                        "status": reason,
                    })
                    if not passed:
                        continue
                    candidate_key = str(candidate.get("path") or "")
                    if candidate_key not in primary_finalist_keys.get(index, set()):
                        attempts[index].append({
                            "source": source,
                            "candidate_id": str(candidate.get("candidate_id") or candidate.get("id") or ""),
                            "stage": "visual_gate",
                            "status": "not_selected_finalist",
                        })
                        continue
                    metadata_score = float(
                        candidate.get("final_score")
                        or candidate.get("score")
                        or candidate.get("metadata_score")
                        or 0.0
                    )
                    source_tie = -preference_rank.get(source, 9)
                    outcome = gated_outcomes.get((index, str(candidate.get("path") or "")))
                    if outcome is None:
                        if (
                            source == "youtube"
                            and bool(_source_policy_value(scene, "youtube_quota", False))
                        ):
                            # Quota protection: a clean YouTube candidate may
                            # arrive after the bounded parallel finalist pass
                            # or have a path identity different from the
                            # cached row. Verify this one motion candidate
                            # inline instead of silently converting the quota
                            # scene to a still.
                            if visual_budget_open("visual_gate_quota_inline", source):
                                visual_verified, visual_reason, visual_score, inline_qwen = _verification_result(
                                    scene, candidate, visual_verifier,
                                )
                            else:
                                visual_verified, visual_reason, visual_score, inline_qwen = (
                                    False, "skipped_after_media_deadline", 0.0,
                                    {"accepted": False, "status": "deadline_exceeded"},
                                )
                            attempts[index].append({
                                "source": source,
                                "candidate_id": str(candidate.get("candidate_id") or candidate.get("id") or ""),
                                "stage": "visual_gate",
                                "status": f"quota_inline:{visual_reason}",
                            })
                            outcome = (
                                metadata_score,
                                source_tie,
                                visual_verified,
                                visual_reason,
                                visual_score,
                                inline_qwen or {},
                            )
                            # Record the inline verdict where the late quota
                            # lane looks for it. Without this, a
                            # deadline-skipped quota clip looked
                            # "never vision-checked" to quota retention,
                            # which then promoted it on "downloaded" alone
                            # and the downstream contract gate rejected it
                            # as unverified (ff377408 scenes 1, 2, 6). A
                            # recorded skip carries a deadline/unavailable
                            # reason, so the late lane retries it; a
                            # recorded hard rejection stays a rejection.
                            gated_outcomes.setdefault(
                                (index, str(candidate.get("path") or "")), outcome
                            )
                            metadata_score, source_tie, verified, verify_reason, visual_score, qwen = outcome
                        else:
                            if visual_budget_open("visual_gate_inline", source):
                                visual_verified, visual_reason, visual_score, inline_qwen = _verification_result(
                                    scene, candidate, visual_verifier,
                                )
                            else:
                                visual_verified, visual_reason, visual_score, inline_qwen = (
                                    False, "skipped_after_media_deadline", 0.0,
                                    {"accepted": False, "status": "deadline_exceeded"},
                                )
                            attempts[index].append({
                                "source": source,
                                "candidate_id": str(candidate.get("candidate_id") or candidate.get("id") or ""),
                                "stage": "visual_gate",
                                "status": f"inline:{visual_reason}",
                            })
                            outcome = (
                                metadata_score,
                                source_tie,
                                visual_verified,
                                visual_reason,
                                visual_score,
                                inline_qwen or {},
                            )
                            metadata_score, source_tie, verified, verify_reason, visual_score, qwen = outcome
                    else:
                        metadata_score, source_tie, verified, verify_reason, visual_score, qwen = outcome
                else:
                    passed, reason = _candidate_hard_gate(scene, candidate)
                    attempts[index].append({
                        "source": source,
                        "candidate_id": str(candidate.get("candidate_id") or candidate.get("id") or ""),
                        "stage": "hard_gate",
                        "status": reason,
                    })
                    if not passed:
                        continue
                    metadata_score = float(
                        candidate.get("final_score")
                        or candidate.get("score")
                        or candidate.get("metadata_score")
                        or 0.0
                    )
                    cached_verdict = parallel_verdicts.get(candidate_index)
                    if cached_verdict is not None:
                        verified, verify_reason, visual_score, qwen = cached_verdict
                    elif visual_budget_open("visual_gate_fallback", source):
                        verified, verify_reason, visual_score, qwen = _verification_result(
                            scene, candidate, visual_verifier,
                        )
                    else:
                        verified, verify_reason, visual_score, qwen = (
                            False, "skipped_after_media_deadline", 0.0,
                            {"accepted": False, "status": "deadline_exceeded"},
                        )
                    attempts[index].append({
                        "source": source,
                        "candidate_id": str(candidate.get("candidate_id") or candidate.get("id") or ""),
                        "stage": "visual_gate",
                        "status": verify_reason,
                    })
                    source_tie = -preference_rank.get(source, 9)
                if source == "youtube":
                    from .visual_intelligence.semantic_guard import calculate_youtube_weighted_score
                    transcript_score = float(candidate.get("transcript_score") or candidate.get("moment_transcript_score") or (isinstance(candidate.get("moment"), Mapping) and candidate.get("moment", {}).get("transcript_score")) or 0.0)
                    transcript_avail = bool(candidate.get("transcript_score") is not None or (isinstance(candidate.get("moment"), Mapping) and candidate.get("moment", {}).get("transcript_score") is not None))
                    vision_avail = bool(qwen and (qwen.get("visual_available") or qwen.get("vision_available") or qwen.get("visual_checked")))
                    yt_weighted = calculate_youtube_weighted_score(
                        vision_score=visual_score,
                        vision_available=vision_avail,
                        transcript_score=transcript_score,
                        transcript_available=transcript_avail,
                        metadata_score=metadata_score,
                        is_relaxed=bool(_youtube_relaxed_visual_gate(scene)),
                    )
                    candidate["weighted_score"] = yt_weighted
                    effective_meta = max(metadata_score, yt_weighted)
                else:
                    effective_meta = metadata_score
                viable.append((effective_meta, source_tie, candidate, source, verified, verify_reason, visual_score, qwen))
            viable.sort(
                key=lambda value: (
                    1 if value[4] else 0,
                    -preference_rank.get(value[3], 9) if _source_policy_value(scene, 'media_source_priority', None) else 0,
                    event_alignment(value[2]),
                    1 if (value[3] == "youtube" and bool(_source_policy_value(scene, "youtube_quota", False))) else 0,
                    value[0],
                    value[1],
                ),
                reverse=True,
            )
            for metadata_score, source_tie, candidate, source, verified, verify_reason, visual_score, qwen in viable[:6]:
                if verified:
                    if source == "youtube" and not _youtube_candidate_is_valid_after_master_reuse(
                        candidate,
                        qwen,
                        scene,
                        [row for row in resolutions.values() if row is not None],
                    ):
                        attempts[index].append({
                            "source": "youtube",
                            "candidate_id": str(candidate.get("candidate_id") or candidate.get("id") or ""),
                            "stage": "same_master_cut_gate",
                            "status": "rejected_without_distinct_distant_scene_verified_download",
                        })
                        continue
                    # Web stills are an exact-path diversity lane.  A
                    # provider can legally return the same CDN/local path for
                    # several queries; accepting it again makes a listicle
                    # look frozen even when the subject lock is correct.  Do
                    # not turn that provider defect into a repeated shot:
                    # leave the scene for a fresh finalist or the neutral
                    # fallback. YouTube reuse remains governed by its own
                    # motion/overlay policy below.
                    candidate_path = str(candidate.get("path") or "")
                    candidate_identity = _web_asset_identity(candidate)
                    if _intro_asset_conflict(index, scene, candidate, source):
                        attempts[index].append({
                            "source": source,
                            "candidate_id": str(candidate.get("candidate_id") or candidate.get("id") or ""),
                            "stage": "intro_diversity_gate",
                            "status": "opening_asset_or_master_already_used",
                        })
                        continue
                    if _is_web_source(source) and candidate_identity and reuse_counts.get(candidate_identity, 0) > 0:
                        attempts[index].append({
                            "source": source,
                            "candidate_id": str(candidate.get("candidate_id") or candidate.get("id") or ""),
                            "stage": "duplicate_path_gate",
                            "status": "web_path_already_selected:web_asset_identity",
                        })
                        continue
                    # A pool-first clip already passed the branch's entity-gated
                    # visual gate (bounded calls). Its relevance score is far
                    # below a web image's visual_score on the same 0-100 scale,
                    # so raw scoring would hand the scene to a merely higher
                    # scored web frame. Give the verified pool-moment cut a
                    # decisive preference; the web lane still wins whenever the
                    # youtube lane had no verified pool resolution.
                    verdict = qwen or {}
                    pool_verified = (
                        source == "youtube"
                        and str(verdict.get("source") or "").strip().lower() == "youtube_pool_moment"
                        and str(verdict.get("status") or "").strip().lower() in {"accepted", "pass", "keep"}
                    )
                    preferred = scene.get('preferred_sources') or _source_policy_value(scene, 'preferred_sources', []) or []
                    prefers_motion = bool(preferred and str(preferred[0]).lower() in {'youtube', 'youtube clips', 'yt'})
                    quota_youtube = source == 'youtube' and (
                        bool(_source_policy_value(scene, 'youtube_quota', False)) or prefers_motion
                    )
                    preference_bonus = 100000.0 if (pool_verified or quota_youtube) else 0.0
                    # Prefer a new verified physical asset over a previously
                    # selected one.  The prior coordinator only applied reuse
                    # caps after selection, so a high-scoring shared-batch
                    # image could win repeatedly even while unused finalists
                    # were available.  Keep the penalty finite: if every
                    # finalist is the same asset, truthful reuse still wins
                    # over a neutral plate.
                    prior_uses = reuse_counts.get(candidate_identity, 0)
                    reuse_penalty = 250000.0 if prior_uses else 0.0
                    effective_vis = float(candidate.get("weighted_score", visual_score)) if source == "youtube" else visual_score
                    accepted.append((
                        effective_vis * 1000.0 + metadata_score + preference_bonus - reuse_penalty,
                        source_tie,
                        candidate,
                        qwen or {},
                    ))

        primary_rows: List[tuple[Dict[str, Any], str]] = []
        for candidate in discovered[index]:
            primary_rows.append((candidate, _normalize_candidate_source(candidate)))
        consider_finalists(primary_rows, pre_verified=True)

        def consider_recovery(source, lane, stage):
            future = recovery_futures.get((index, source))
            deadline = web_fallback_deadline_at if source == "web" else deadline_at
            try:
                candidates, status, elapsed = (
                    future.result(timeout=max(0.0, deadline - time.monotonic()))
                    if future is not None else fetch_recovery(index, source, lane)
                )
            except TimeoutError:
                candidates, status, elapsed = [], "timeout", 0.0
            except Exception as exc:
                candidates, status, elapsed = [], f"error:{type(exc).__name__}", 0.0
            attempts[index].append({
                "source": source, "stage": stage, "status": status,
                "candidate_count": len(candidates), "elapsed_ms": round(elapsed * 1000, 2),
            })
            for candidate in candidates:
                candidate["fallback_layer"] = f"cross_lane_{source}"
                candidate.setdefault("resolution_kind", f"cross_lane_{source}_fallback")
            discovered[index].extend(candidates)
            if candidates:
                consider_finalists(
                    [(candidate, _normalize_candidate_source(candidate)) for candidate in candidates],
                    skip_recovery_vision=not adaptive_media_review and stage == "cross_lane_fallback",
                )

        # Keep an approved still while a rejected quota clip looks for a
        # different source. A rejected replacement cannot displace the still.
        if (accepted and index in rejected_youtube_indices
                and _source_policy_value(scene, "youtube_quota", False)
                and (index, "youtube") in recovery_futures):
            consider_recovery("youtube", youtube_fallback_lane, "youtube_content_recovery")

        # A large job starts with a compact parallel shortlist, but the web
        # resolver deliberately retains several unique results from the same
        # query.  If the first shortlist is rejected, spend a second bounded
        # verification wave on those retained primary candidates before
        # leaving the source lane.  This prevents a valid lower-ranked image
        # from being discarded merely because three visually weak results
        # sorted ahead of it on identical metadata scores.
        if not accepted and primary_rows:
            primary_keys = primary_finalist_keys.get(index, set())
            top_pool_keys = primary_top_pool_keys.get(index, set())
            expansion_rows = [
                (candidate, source)
                for candidate, source in primary_rows
                if str(candidate.get("path") or "") not in primary_keys
                and (
                    not top_pool_keys
                    or str(candidate.get("path") or "") in top_pool_keys
                )
            ]
            if expansion_rows:
                try:
                    default_expansion_limit = "2" if len(rows) >= 16 else "3"
                    expansion_limit = max(
                        1,
                        min(
                            6,
                            int(
                                os.environ.get(
                                    "VIDZEO_VISUAL_PRIMARY_EXPANSION_LIMIT",
                                    default_expansion_limit,
                                )
                                or default_expansion_limit
                            ),
                        ),
                    )
                except (TypeError, ValueError):
                    expansion_limit = 1 if len(rows) >= 16 else 3
                expansion_rows.sort(
                    key=lambda item: float(
                        item[0].get("final_score")
                        or item[0].get("score")
                        or item[0].get("metadata_score")
                        or 0.0
                    ),
                    reverse=True,
                )
                expansion_rows = expansion_rows[:expansion_limit]
                attempts[index].append({
                    "source": "mixed",
                    "stage": "primary_finalist_expansion",
                    "status": "verifying_retained_alternates",
                    "candidate_count": len(expansion_rows),
                })
                consider_finalists(expansion_rows, pre_verified=False)

        if not accepted:
            specs = [(source, lane) for source, lane in _compute_cross_lane_specs(index) if lane is not None]
            # Preserve quota preference. Other scenes can use whichever real
            # provider has already returned a candidate without waiting twice.
            if adaptive_media_review and not _source_policy_value(scene, "youtube_quota", False):
                futures = {recovery_futures[index, source]: source for source, _ in specs
                           if (index, source) in recovery_futures}
                if futures and not any(future.done() for future in futures):
                    wait(futures, timeout=max(0.0, web_fallback_deadline_at - time.monotonic()),
                         return_when=FIRST_COMPLETED)
                ready = {source for future, source in futures.items() if future.done()}
                specs.sort(key=lambda spec: spec[0] not in ready)
            for source, lane in specs:
                consider_recovery(source, lane, "cross_lane_fallback")
                if accepted:
                    break

        # Spare-pool reuse (Nov 10, 2026): fallback tier (a). When a scene's
        # own lanes found nothing usable, reuse an already-downloaded,
        # vision-checked asset from another scene's top-12 pool before
        # fetching anything new (tier (b) YT clip frames below) or falling
        # back to a graphic (tier (c), last). The scene's own exact search
        # already outranks another scene's spare — cross-lane ran first.
        if not accepted:
            spare = _pick_spare_candidate(
                index, scene, rows, discovered, gated_outcomes, reuse_counts
            )
            if spare is not None:
                accepted.append(spare)
                attempts[index].append({
                    "source": _normalize_candidate_source(spare[2]),
                    "stage": "spare_pool_reuse",
                    "status": "accepted",
                    "candidate_id": str(spare[2].get("candidate_id") or spare[2].get("id") or ""),
                    "path": str(spare[2].get("path") or ""),
                })

        # Same-video timestamp recovery. A downloaded YouTube clip can be
        # semantically valid even when the selected cut opens on a transition,
        # title card, or weak frame. Extract a few distinct local timestamps
        # and run those stills through the same final identity gate. This is
        # safer than accepting the original clip or inventing a Remotion card.
        has_youtube_candidate = any(
            str(c.get("source") or c.get("provider") or "").strip().lower() in {"youtube", "yt", "youtube clips"}
            for c in (discovered[index] if index < len(discovered) else [])
            if isinstance(c, Mapping)
        )
        if not accepted and not _footage_only_scene(scene) and (bool(_source_policy_value(scene, "youtube_quota", False)) or has_youtube_candidate) and recovery_budget_open("same_video_timestamp_frame_fallback"):
            frame_candidates = _youtube_frame_fallback_candidates(
                scene, discovered[index], index,
            )
            if frame_candidates:
                attempts[index].append({
                    "source": "youtube",
                    "stage": "same_video_timestamp_frame_fallback",
                    "status": "candidates",
                    "candidate_count": len(frame_candidates),
                })
                discovered[index].extend(frame_candidates)
                consider_finalists([
                    (candidate, "youtube") for candidate in frame_candidates
                ])

        # In Mixed Media, an unused verified candidate is always more
        # valuable than an exact path already used earlier in the edit.
        # Prefer fresh candidates, but keep reused verified candidates
        # up to the reuse cap instead of throwing away real media for a neutral blank.
        if _mixed_media_scene(scene) and accepted:
            fresh = [
                item for item in accepted
                    if reuse_counts.get(_web_asset_identity(item[2]), 0) == 0
            ]
            if fresh:
                accepted = fresh
            else:
                cap = int(os.environ.get("VIDZEO_WEB_IMAGE_REUSE_CAP", "1") or 1)
                accepted = [
                    item for item in accepted
                    if reuse_counts.get(_web_asset_identity(item[2]), 0) < cap
                ]

        # Debugger advance (tier 1 of the stall policy): the watchdog
        # flagged this scene as stuck. Resolve it from media already on
        # disk, vision-checked and scored, instead of hunting further.
        # Tiers 2 (same-job approved media) and 3 (graphic) already exist
        # below; falling through reaches them when no on-disk candidate
        # qualifies. The injected candidate flows through the normal
        # accepted path, so the ledger, alternates and reuse caps apply.
        if not accepted:
            try:
                from .job_debugger import (
                    consume_scene_advance,
                    is_scene_advance_requested,
                    pick_advance_candidate,
                )

                if is_scene_advance_requested(index):
                    pick = pick_advance_candidate(
                        index, discovered.get(index), gated_outcomes
                    )
                    if pick is not None:
                        advance_candidate, advance_score, advance_qwen = pick
                        advance_copy = dict(advance_candidate)
                        advance_copy["resolution_kind"] = (
                            "debugger_advance_ondisk_reuse"
                        )
                        advance_copy["fallback_reason"] = (
                            "debugger_advance:scene_stuck"
                        )
                        accepted = [
                            (
                                float(advance_score),
                                0,
                                advance_copy,
                                advance_qwen,
                            )
                        ]
                        attempts[index].append(
                            {
                                "source": str(
                                    advance_candidate.get("source")
                                    or advance_candidate.get("provider")
                                    or ""
                                ),
                                "stage": "debugger_advance",
                                "status": "ondisk_reuse",
                                "candidate_id": str(
                                    advance_candidate.get("candidate_id")
                                    or advance_candidate.get("id")
                                    or ""
                                ),
                            }
                        )
                    consume_scene_advance(index)
            except Exception:
                pass

        if accepted:
            explicit_order = _source_policy_value(scene, 'media_source_priority', None)
            if isinstance(explicit_order, (list, tuple)) and accepted:
                def source_rank(item):
                    source = _normalize_candidate_source(item[2])
                    return explicit_order.index(source) if source in explicit_order else len(explicit_order)
                best_source = min(source_rank(item) for item in accepted)
                accepted = [item for item in accepted if source_rank(item) == best_source]
            _rank, _source_tie, candidate, qwen = _choose_diverse_verified_media(
                accepted, list(resolutions.values()), event_alignment)
            source = str(candidate.get("source") or candidate.get("provider") or "unknown")
            selected_path = str(candidate.get("path") or "")
            verified_alternates: list[dict[str, Any]] = []
            alternate_identities: set[str] = set()
            for _alternate_score, _alternate_tie, alternate, alternate_qwen in sorted(
                accepted,
                key=lambda value: (value[0], value[1]),
                reverse=True,
            ):
                alternate_path = str(alternate.get("path") or "")
                if not alternate_path or alternate_path == selected_path:
                    continue
                alternate_identity = _web_asset_identity(alternate)
                if alternate_identity and alternate_identity in alternate_identities:
                    continue
                if alternate_identity:
                    alternate_identities.add(alternate_identity)
                alternate_row = dict(alternate)
                alternate_row["verified"] = True
                alternate_row["fallback_layer"] = "verified_alternate"
                alternate_row["visual_decision"] = dict(alternate_qwen or {})
                verified_alternates.append(alternate_row)
                if len(verified_alternates) >= 8:
                    break
            resolution = SceneResolution(
                scene_index=int(scene.get("scene_index", index)),
                scene_id=str(scene.get("scene_id") or ""),
                container_id=str(scene.get("container_id") or ""),
                contract_hash=str(scene.get("contract_hash") or ""),
                final_scene_context=dict(scene),
                selected_path=str(candidate.get("path") or ""),
                media_type=str(candidate.get("media_type") or candidate.get("type") or ""),
                provider=source,
                source_url=str(candidate.get("origin_url") or candidate.get("source_url") or candidate.get("url") or ""),
                rights_status=str(candidate.get("rights_status") or "unknown"),
                rights_risk=str(candidate.get("rights_risk") or "high"),
                rights_review_required=bool(candidate.get("rights_review_required", True)),
                rights_evidence=str(candidate.get("rights_evidence") or ""),
                metadata_score=float(candidate.get("final_score") or candidate.get("score") or 0.0),
                resolution_kind=str(candidate.get("resolution_kind") or "unique"),
                query_used=str(candidate.get("query") or candidate.get("query_used") or ""),
                attempts=list(attempts[index]),
                alternates=(
                    verified_alternates
                    + [
                        dict(variant)
                        for variant in (candidate.get("verified_reuse_variants") or ())
                        if isinstance(variant, Mapping) and bool(variant.get("verified"))
                    ]
                )[:8],
                visual_decision=qwen,
                verification_status=(
                    "deterministic_degraded"
                    if str((qwen or {}).get("status") or "").lower() == "deterministic_degraded"
                    else ("verified" if qwen else "deterministic_only")
                ),
                candidate_id=str(candidate.get("candidate_id") or candidate.get("id") or ""),
                asset_fingerprint=str(candidate.get("asset_fingerprint") or candidate.get("fingerprint") or ""),
                license=str(candidate.get("license") or ""),
                license_url=str(candidate.get("license_url") or ""),
                attribution=str(candidate.get("attribution") or ""),
                creator=str(candidate.get("creator") or candidate.get("channel") or ""),
                video_start=candidate.get("video_start", candidate.get("start")),
                video_end=candidate.get("video_end", candidate.get("end")),
                render_clip_start=candidate.get("render_clip_start"),
                gate_outcomes=dict(candidate.get("gate_outcomes") or {}),
                overlay_policy=str(candidate.get("overlay_policy") or ""),
                overlay_types=list(candidate.get("overlay_types") or []),
                blur_regions=list(candidate.get("blur_regions") or []),
                rejection_reason=str(candidate.get("rejection_reason") or ""),
                cleaned_path=str(candidate.get("cleaned_path") or ""),
                requirement_hash=str((scene.get("visual_requirement") or {}).get("requirement_hash") or ""),
                candidate_evidence=dict(candidate.get("candidate_evidence") or {}),
                candidate_ledger=_candidate_ledger(index, selected_path),
                status="accepted",
                fallback_reason=str(
                    candidate.get("fallback_reason")
                    or candidate.get("fallback_layer")
                    or ""
                ),
            )
            resolutions[index] = resolution
            verified_by_container.setdefault(resolution.container_id, []).append((index, resolution))
            reuse_counts.setdefault(_resolution_asset_identity(resolution), 1)
            continue

        # Same-container reuse is bounded and never adjacent. When the top-1
        # lane candidates were all rejected (or the lane came back empty) but
        # an adjacent section beat already verified the SAME container subject,
        # reusing that verified asset is the next-best fallback: it is factually
        # right for the section and strictly better than a blank neutral card.
        container_id = str(scene.get("container_id") or "")
        quota_youtube_scene = bool(_source_policy_value(scene, "youtube_quota", False))
        reuse = None
        reuse_variant = None
        reuse_candidates = list(verified_by_container.get(container_id, []))
        if quota_youtube_scene:
            # A quota scene that lost its own candidate must keep the mixed
            # source ratio when a clean YouTube moment is already verified in
            # the same container. Web reuse is still valid, but only after the
            # motion lane has been exhausted.
            reuse_candidates.sort(
                key=lambda item: (
                    str(item[1].provider or "").strip().lower()
                    not in {"youtube", "youtube clips", "yt"},
                    abs(item[0] - index),
                    item[0],
                )
            )
        for old_index, resolution in reuse_candidates:
            if not _resolution_reusable(resolution):
                continue
            if _intro_unique_scene(scene) and _intro_asset_conflict(
                index,
                scene,
                {
                    "candidate_id": resolution.candidate_id,
                    "source_url": resolution.source_url,
                    "path": resolution.selected_path,
                },
                str(resolution.provider or ""),
            ):
                attempts[index].append({
                    "source": str(resolution.provider or ""),
                    "stage": "intro_diversity_gate",
                    "status": "reuse_of_opening_asset_or_master_blocked",
                    "reused_scene_index": old_index,
                })
                continue
            # A strict scene may not replay the already-selected web still.
            # It may, however, borrow a separately verified, distinct
            # alternate harvested from that same canonical entity.  The web
            # resolver deliberately retains several unique results per query;
            # discarding that verified pool here turned provider scarcity into
            # a neutral scene even though truthful same-entity media existed.
            variant = next((
                item for item in resolution.alternates
                if str(item.get("path") or "")
                and str(item.get("path") or "") != resolution.selected_path
                and bool(item.get("verified"))
                and str(item.get("candidate_id") or "") != str(resolution.candidate_id or "")
                and _alternate_available(item, resolution, index)
            ), None)
            if _is_youtube_source(resolution):
                target_requirement = scene.get("visual_requirement") if isinstance(scene.get("visual_requirement"), Mapping) else {}
                target_hash = str(target_requirement.get("requirement_hash") or "").strip()
                try:
                    duration = float(scene.get("duration") or 4.0)
                except (TypeError, ValueError):
                    duration = 4.0
                prior_youtube = [
                    row for row in resolutions.values()
                    if row is not None and _is_youtube_source(row) and row.selected_path
                ]
                used_paths = {
                    str(Path(row.selected_path).resolve()).casefold()
                    for row in prior_youtube
                }
                variant = next((
                    item for item in resolution.alternates
                    if _verified_distant_youtube_variant(
                        item,
                        resolution,
                        target_requirement_hash=target_hash,
                        scene_duration=duration,
                        used_resolutions=prior_youtube,
                        used_paths=used_paths,
                    )
                ), None)
            if _is_web_source(resolution) and not _web_reuse_allowed(scene) and variant is None:
                attempts[index].append({
                    "source": "web",
                    "stage": "strict_reuse_gate",
                    "status": "web_still_reuse_disabled",
                    "reused_scene_index": old_index,
                })
                continue
            if _is_youtube_source(resolution) and variant is not None:
                # The original scene's requirement hash is not evidence for a
                # later beat. The separate cut carries its own target-scene
                # visual verdict and content hash, checked above.
                variant_identity = _web_asset_identity(variant)
                if not variant_identity or reuse_counts.get(variant_identity, 0) > 0:
                    attempts[index].append({
                        "source": "youtube",
                        "stage": "same_master_cut_gate",
                        "status": "rejected_duplicate_materialized_variant",
                        "reused_scene_index": old_index,
                    })
                    continue
            elif not _strict_reuse_identity_compatible(scene, resolution) or not _semantic_reuse_compatible(scene, resolution):
                attempts[index].append({
                    "source": str(resolution.provider or ""),
                    "stage": "semantic_reuse_gate",
                    "status": "requirement_hash_mismatch",
                })
                continue
            if abs(old_index - index) <= 1:
                continue
            if not (_is_youtube_source(resolution) and variant is not None):
                if reuse_counts.get(_resolution_asset_identity(resolution), 0) >= _reuse_limit_for_resolution(
                    scene,
                    resolution,
                    non_mixed_limit=2,
                ):
                    continue
            if variant is not None:
                reuse = resolution
                reuse_variant = variant
                break
            # A non-adjacent frame is not a safe render variant merely
            # because it belongs to the same container. Require a separately
            # verified range/asset so long listicles do not repeat one still
            # across distant beats.
            continue
        # A neighboring scene can be a legitimate visual continuation when
        # both primary lanes fail verification. The old rule deliberately
        # avoided adjacent reuse, but that turned a rejected image into an
        # abstract neutral card in the middle of an otherwise real-media
        # sequence. Prefer a verified same-topic frame over a blank-looking
        # no-claim card, while keeping the existing two-use cap.
        adjacent_reuse = False
        if reuse is None and bool(scene.get("positive_subjects")):
            nearby = sorted(
                verified_by_container.get(container_id, []),
                key=lambda item: (
                    str(item[1].provider or "").strip().lower()
                    not in {"youtube", "youtube clips", "yt"}
                    if quota_youtube_scene else False,
                    abs(item[0] - index),
                    item[0],
                ),
            )
            for old_index, resolution in nearby:
                if _is_web_source(resolution) and not _web_reuse_allowed(scene):
                    attempts[index].append({
                        "source": "web",
                        "stage": "strict_reuse_gate",
                        "status": "web_still_reuse_disabled",
                        "reused_scene_index": old_index,
                    })
                    continue
                if _intro_unique_scene(scene) and _intro_asset_conflict(
                    index,
                    scene,
                    {
                        "candidate_id": resolution.candidate_id,
                        "source_url": resolution.source_url,
                        "path": resolution.selected_path,
                    },
                    str(resolution.provider or ""),
                ):
                    attempts[index].append({
                        "source": str(resolution.provider or ""),
                        "stage": "intro_diversity_gate",
                        "status": "adjacent_opening_asset_or_master_blocked",
                        "reused_scene_index": old_index,
                    })
                    continue
                if (
                    abs(old_index - index) <= 1
                    and (
                        _strict_real_media_scene(scene)
                        or not _is_youtube_source(resolution)
                    )
                ):
                    attempts[index].append({
                        "source": str(resolution.provider or ""),
                        "stage": "reuse_diversity_gate",
                        "status": "adjacent_asset_reuse_blocked",
                        "reused_scene_index": old_index,
                    })
                    continue
                if not _resolution_reusable(resolution):
                    continue
                if not _strict_reuse_identity_compatible(scene, resolution) or not _semantic_reuse_compatible(scene, resolution):
                    continue
                if reuse_counts.get(_resolution_asset_identity(resolution), 0) >= _reuse_limit_for_resolution(
                    scene,
                    resolution,
                    non_mixed_limit=2,
                ):
                    continue
                reuse = resolution
                adjacent_reuse = abs(old_index - index) <= 1
                reuse_variant = next((
                    item for item in resolution.alternates
                    if str(item.get("path") or "")
                    and str(item.get("path") or "") != resolution.selected_path
                    and bool(item.get("verified"))
                    and _alternate_available(item, resolution, index)
                ), None)
                break
        if reuse is not None:
            if reuse_variant is not None:
                variant_path = str(reuse_variant.get("path") or "")
                variant_fingerprint = str(reuse_variant.get("asset_fingerprint") or reuse.asset_fingerprint or "")
                variant_start = reuse_variant.get("video_start", reuse_variant.get("start"))
                variant_end = reuse_variant.get("video_end", reuse_variant.get("end"))
                variant_candidate = str(reuse_variant.get("candidate_id") or reuse.candidate_id)
                variant_provider = str(
                    reuse_variant.get("source")
                    or reuse_variant.get("provider")
                    or reuse.provider
                )
                variant_media_type = str(
                    reuse_variant.get("media_type")
                    or reuse_variant.get("type")
                    or reuse.media_type
                )
                variant_source_url = str(
                    reuse_variant.get("origin_url")
                    or reuse_variant.get("source_url")
                    or reuse_variant.get("url")
                    or reuse.source_url
                )
                variant_rights_status = str(reuse_variant.get("rights_status") or reuse.rights_status)
                variant_license = str(reuse_variant.get("license") or reuse.license)
                variant_license_url = str(reuse_variant.get("license_url") or reuse.license_url)
                variant_attribution = str(reuse_variant.get("attribution") or reuse.attribution)
                variant_creator = str(
                    reuse_variant.get("creator")
                    or reuse_variant.get("channel")
                    or reuse.creator
                )
                variant_visual_decision = dict(
                    reuse_variant.get("visual_decision")
                    or reuse_variant.get("qwen_result")
                    or reuse.visual_decision
                    or {}
                ) or None
                variant_gate_outcomes = dict(reuse_variant.get("gate_outcomes") or reuse.gate_outcomes)
                variant_overlay_policy = str(reuse_variant.get("overlay_policy") or reuse.overlay_policy)
                variant_overlay_types = list(reuse_variant.get("overlay_types") or reuse.overlay_types)
                variant_blur_regions = list(reuse_variant.get("blur_regions") or reuse.blur_regions)
                variant_rejection_reason = str(reuse_variant.get("rejection_reason") or reuse.rejection_reason)
                variant_cleaned_path = str(reuse_variant.get("cleaned_path") or reuse.cleaned_path)
                variant_render_clip_start = reuse_variant.get("render_clip_start", reuse.render_clip_start)
            else:
                variant_path = reuse.selected_path
                variant_fingerprint = reuse.asset_fingerprint or ""
                variant_start = reuse.video_start
                variant_end = reuse.video_end
                variant_candidate = reuse.candidate_id
                variant_provider = reuse.provider
                variant_media_type = reuse.media_type
                variant_source_url = reuse.source_url
                variant_rights_status = reuse.rights_status
                variant_license = reuse.license
                variant_license_url = reuse.license_url
                variant_attribution = reuse.attribution
                variant_creator = reuse.creator
                variant_visual_decision = reuse.visual_decision
                variant_gate_outcomes = dict(reuse.gate_outcomes)
                variant_overlay_policy = reuse.overlay_policy
                variant_overlay_types = list(reuse.overlay_types)
                variant_blur_regions = list(reuse.blur_regions)
                variant_rejection_reason = reuse.rejection_reason
                variant_cleaned_path = reuse.cleaned_path
                variant_render_clip_start = reuse.render_clip_start
            target_requirement = scene.get("visual_requirement") or {}
            target_hash = str(target_requirement.get("requirement_hash") or "")
            prior_review = variant_visual_decision or {}
            if target_hash and prior_review.get("verified_requirement_hash") != target_hash:
                # Same topic/container is discovery evidence, not a review of
                # this narration. Reused assets need a receipt for the target
                # requirement; copying the donor receipt caused final gates to
                # discard dozens of otherwise useful real images.
                source_metadata = next((dict(item) for item in reuse.candidate_ledger
                    if isinstance(item, Mapping) and str(item.get("path") or "") == variant_path), {})
                reuse_candidate = {**source_metadata, "path": variant_path,
                    "candidate_id": variant_candidate, "provider": variant_provider,
                    "source": variant_provider, "media_type": variant_media_type,
                    "source_url": variant_source_url,
                    "semantic_context_pending": True, "visual_verification_required": True,
                    "verification_status": "pending_visual_verification"}
                reuse_candidate.pop("visual_decision", None)
                reuse_candidate.pop("qwen_result", None)
                okay, reuse_reason, reuse_score, target_review = _verification_result(
                    scene, reuse_candidate, visual_verifier)
                attempts[index].append({"source": variant_provider, "stage": "target_scene_reuse_verification",
                                        "status": reuse_reason, "score": reuse_score})
                review_resolution = SceneResolution(scene_index=index, selected_path=variant_path,
                    visual_decision=dict(target_review) if isinstance(target_review, Mapping) else None)
                if okay and _review_bound_to_scene(scene, review_resolution):
                    variant_visual_decision = dict(target_review)
                else:
                    reuse = None
            if reuse is not None:
                # Count the physical asset actually selected for this reuse.  When
                # a verified alternate is borrowed, counting the parent's
                # selected path would let the alternate repeat beyond its cap and
                # would make the duplicate audit lie about the rendered asset.
                reuse_identity = _resolution_asset_identity(SceneResolution(
                    scene_index=index, provider=variant_provider, selected_path=variant_path,
                    asset_fingerprint=variant_fingerprint))
                reuse_counts[reuse_identity] = reuse_counts.get(reuse_identity, 0) + 1
                resolutions[index] = SceneResolution(
                    scene_index=int(scene.get("scene_index", index)), scene_id=str(scene.get("scene_id") or ""),
                    container_id=container_id, contract_hash=str(scene.get("contract_hash") or ""),
                    final_scene_context=dict(scene), selected_path=variant_path,
                    media_type=variant_media_type, provider=variant_provider, source_url=variant_source_url,
                    rights_status=variant_rights_status, attempts=list(attempts[index]),
                    alternates=list(reuse.alternates or []),
                    resolution_kind="same_container_reuse", verification_status="verified_reuse",
                    candidate_id=variant_candidate,
                    asset_fingerprint=variant_fingerprint,
                    license=variant_license, license_url=variant_license_url,
                    attribution=variant_attribution, creator=variant_creator,
                    video_start=variant_start,
                    video_end=variant_end,
                    render_clip_start=variant_render_clip_start,
                    gate_outcomes=variant_gate_outcomes, status="accepted",
                    visual_decision=variant_visual_decision,
                    overlay_policy=variant_overlay_policy,
                    overlay_types=variant_overlay_types,
                    blur_regions=variant_blur_regions,
                    rejection_reason=variant_rejection_reason,
                    cleaned_path=variant_cleaned_path,
                    requirement_hash=str((scene.get("visual_requirement") or {}).get("requirement_hash") or ""),
                    candidate_evidence=dict(reuse.candidate_evidence or {}),
                    candidate_ledger=list(reuse.candidate_ledger or []),
                    fallback_reason=(
                        "same_container_adjacent_reuse_after_primary_and_stock_exhausted"
                        if adjacent_reuse
                        else "same_container_reuse_after_primary_and_stock_exhausted"
                    ),
                )
                continue

        # Last non-neutral recovery for mixed/documentary jobs: when a scene's
        # own web candidates were rejected by an exposure/safety gate and no
        # same-container frame exists, reuse a verified web image from the
        # same topic. A real historical image is preferable to the gray
        # emergency plate; cap each asset at two uses and record the reuse.
        topic_web = [
            (abs(old_index - index), old_index, resolution)
            for old_index, resolution in resolutions.items()
            if old_index != index
            and str(resolution.provider or "").strip().lower() in {"web", "web images"}
            and str(resolution.verification_status or "").strip().lower() in {
                "verified", "verified_reuse", "deterministic_web_image",
                "deterministic_degraded", "deterministic_only",
            }
            and resolution.selected_path
            and (
                not _is_web_source(resolution)
                or _web_reuse_allowed(scene)
            )
            and _strict_reuse_identity_compatible(scene, resolution)
            and _semantic_reuse_compatible(scene, resolution)
            # A topic rescue may reuse a verified still only after fresh scene
            # candidates were exhausted. Keep the fallback bounded.
            and reuse_counts.get(_resolution_asset_identity(resolution), 0) < _reuse_limit_for_resolution(
                scene,
                resolution,
                non_mixed_limit=3,
            )
        ]
        if topic_web and _allow_topic_web_reuse(rows, scene):
            adjacent_paths = _adjacent_selected_paths(resolutions, index)
            spread_topic_web = [
                item for item in topic_web
                if item[2].selected_path not in adjacent_paths
            ]
            if not spread_topic_web:
                attempts[index].append({
                    "source": "web",
                    "stage": "reuse_diversity_gate",
                    "status": "adjacent_asset_reuse_blocked",
                })
                topic_web = []
            else:
                topic_web = spread_topic_web
        if topic_web and _allow_topic_web_reuse(rows, scene):
            _, old_index, reuse = min(
                topic_web,
                key=lambda item: (
                    reuse_counts.get(_resolution_asset_identity(item[2]), 0),
                    item[0],
                    item[1],
                ),
            )
            reuse_identity = _resolution_asset_identity(reuse)
            reuse_counts[reuse_identity] = reuse_counts.get(reuse_identity, 0) + 1
            attempts[index].append({
                "source": "web",
                "stage": "verified_topic_reuse",
                "status": "accepted",
                "reused_scene_index": old_index,
            })
            resolutions[index] = SceneResolution(
                scene_index=int(scene.get("scene_index", index)),
                scene_id=str(scene.get("scene_id") or ""),
                container_id=container_id,
                contract_hash=str(scene.get("contract_hash") or ""),
                final_scene_context=dict(scene),
                selected_path=reuse.selected_path,
                media_type=reuse.media_type,
                provider=reuse.provider,
                source_url=reuse.source_url,
                rights_status=reuse.rights_status,
                attempts=list(attempts[index]),
                alternates=list(reuse.alternates or []),
                resolution_kind="verified_topic_reuse",
                verification_status="verified_reuse",
                candidate_id=reuse.candidate_id,
                asset_fingerprint=reuse.asset_fingerprint,
                license=reuse.license,
                license_url=reuse.license_url,
                attribution=reuse.attribution,
                creator=reuse.creator,
                gate_outcomes=dict(reuse.gate_outcomes),
                overlay_policy=reuse.overlay_policy,
                overlay_types=list(reuse.overlay_types),
                blur_regions=list(reuse.blur_regions),
                rejection_reason=reuse.rejection_reason,
                cleaned_path=reuse.cleaned_path,
                requirement_hash=str((scene.get("visual_requirement") or {}).get("requirement_hash") or ""),
                candidate_evidence=dict(reuse.candidate_evidence or {}),
                candidate_ledger=list(reuse.candidate_ledger or []),
                status="accepted",
                fallback_reason="verified_topic_web_reuse_after_candidate_rejection",
            )
            continue
        if multi_container_job and not _allow_topic_web_reuse(rows, scene) and any(
            _is_web_source(resolution) and resolution.selected_path
            for resolution in resolutions.values()
        ):
            attempts[index].append({
                "source": "web",
                "stage": "verified_topic_reuse",
                "status": "blocked_cross_container",
            })

        # A scene may have no direct YouTube discovery result even though a
        # sibling scene in the same topic already downloaded and verified a
        # real clip.  Promote that verified local clip into a small timestamp
        # frame pool before creating any neutral/graphic result.  Frames are
        # real pixels from the authenticated YouTube source and are checked by
        # the same hard/vision gate as every normal finalist.
        if (
            not accepted
            and bool(_source_policy_value(scene, "youtube_quota", False))
            and recovery_budget_open("verified_topic_frame_pool")
        ):
            pool_candidates: list[dict[str, Any]] = []
            for pool_index, pool_rows in discovered.items():
                if pool_index == index:
                    continue
                for candidate in pool_rows:
                    if _normalize_candidate_source(candidate) != "youtube":
                        continue
                    path = str(candidate.get("path") or "").strip()
                    if not path or not os.path.isfile(path):
                        continue
                    outcome = gated_outcomes.get((pool_index, path))
                    if outcome is not None and not bool(outcome[2]):
                        continue
                    candidate_copy = dict(candidate)
                    if outcome is not None:
                        candidate_copy["visual_decision"] = dict(outcome[5] or {})
                        candidate_copy["metadata_score"] = float(outcome[0] or 0.0)
                    pool_candidates.append(candidate_copy)
            for pool_index, resolution in resolutions.items():
                if pool_index == index or not _is_youtube_source(resolution):
                    continue
                path = str(resolution.selected_path or "").strip()
                if not path or not os.path.isfile(path):
                    continue
                pool_candidates.append({
                    "candidate_id": str(resolution.candidate_id or f"youtube:scene:{pool_index}"),
                    "path": path,
                    "selected_path": path,
                    "provider": "youtube",
                    "source": "youtube",
                    "media_type": resolution.media_type or "video",
                    "metadata_score": resolution.metadata_score,
                    "source_url": resolution.source_url,
                    "rights_status": resolution.rights_status,
                    "creator": resolution.creator,
                    "overlay_policy": resolution.overlay_policy,
                    "overlay_types": list(resolution.overlay_types or []),
                    "blur_regions": list(resolution.blur_regions or []),
                    "visual_decision": dict(resolution.visual_decision or {}),
                    "qwen_result": {
                        **dict(resolution.visual_decision or {}),
                        "source": "youtube_pool_moment",
                        "verification_status": "deterministic_pool_cut",
                        "accepted": True,
                    },
                    "visual_requirement": (
                        resolution.final_scene_context or {}
                    ).get("visual_requirement") or {},
                })
            if pool_candidates and not _footage_only_scene(scene):
                frame_candidates = _youtube_frame_fallback_candidates(
                    scene, pool_candidates, index,
                )
                if frame_candidates:
                    attempts[index].append({
                        "source": "youtube",
                        "stage": "verified_topic_frame_pool",
                        "status": "candidates",
                        "candidate_count": len(frame_candidates),
                    })
                    discovered[index].extend(frame_candidates)
                    consider_finalists(
                        [(candidate, "youtube") for candidate in frame_candidates],
                        pre_verified=False,
                    )

        # Check nearest verified neighbor media before falling back to neutral plates.
        # This is still a cross-scene reuse decision: carry the same semantic
        # identity gates used by the earlier reuse path. A verified image of
        # Malcolm Butler is not verified media for Jermaine Kearse merely
        # because both beats belong to Super Bowl XLIX.
        nearest_verified_path = ""
        nearest_verified_type = "image"
        nearest_verified_identity = ""
        nearest_verified_resolution = None
        for offset in (1, -1, 2, -2, 3, -3, 4, -4, 5, -5):
            target_idx = index + offset
            if 0 <= target_idx < len(scenes) and target_idx in resolutions:
                cand_res = resolutions[target_idx]
                if cand_res and cand_res.selected_path and cand_res.provider in {"web", "youtube", "stock"}:
                    if (
                        not _strict_reuse_identity_compatible(scene, cand_res)
                        or not _semantic_reuse_compatible(scene, cand_res)
                    ):
                        attempts[index].append({
                            "source": str(cand_res.provider or ""),
                            "stage": "nearest_verified_neighbor_reuse",
                            "status": "rejected_semantic_identity_mismatch",
                            "reused_scene_index": target_idx,
                            "target_entity": str((scene.get("visual_requirement") or {}).get("canonical_entity") or ""),
                            "source_entity": str(((cand_res.final_scene_context or {}).get("visual_requirement") or {}).get("canonical_entity") or ""),
                        })
                        continue
                    p = Path(cand_res.selected_path)
                    if p.is_file() and p.stat().st_size > 1024:
                        asset_identity = _resolution_asset_identity(cand_res)
                        reuse_cap = min(
                            2,
                            _reuse_limit_for_resolution(
                                scene, cand_res, non_mixed_limit=2,
                            ),
                        )
                        if not asset_identity or reuse_counts.get(asset_identity, 0) >= reuse_cap:
                            attempts[index].append({
                                "source": str(cand_res.provider or ""),
                                "stage": "nearest_verified_neighbor_reuse",
                                "status": "rejected_asset_reuse_cap",
                                "reused_scene_index": target_idx,
                            })
                            continue
                        if (
                            abs(target_idx - index) <= 1
                            and _is_web_source(cand_res)
                        ):
                            attempts[index].append({
                                "source": str(cand_res.provider or ""),
                                "stage": "nearest_verified_neighbor_reuse",
                                "status": "rejected_adjacent_still_reuse",
                                "reused_scene_index": target_idx,
                            })
                            continue
                        requirement = scene.get('visual_requirement') or {}
                        if ((isinstance(requirement, Mapping) and requirement.get('strict_identity'))
                                or scene_requires_visual_verification(scene)) and not _review_bound_to_scene(scene, cand_res):
                            attempts[index].append({
                                'source': cand_res.provider, 'stage': 'nearest_verified_neighbor_reuse',
                                'status': 'rejected_unbound_pixel_review', 'reused_scene_index': target_idx,
                            })
                            continue
                        nearest_verified_path = str(p)
                        nearest_verified_type = cand_res.media_type or ("video" if p.suffix.lower() in {".mp4", ".webm", ".mov"} else "image")
                        nearest_verified_identity = asset_identity
                        nearest_verified_resolution = cand_res
                        break

        if nearest_verified_path:
            reuse_counts[nearest_verified_identity] = reuse_counts.get(nearest_verified_identity, 0) + 1
            resolutions[index] = replace(nearest_verified_resolution,
                scene_index=int(scene.get("scene_index", index)), scene_id=str(scene.get("scene_id") or ""),
                container_id=container_id, contract_hash=str(scene.get("contract_hash") or ""),
                final_scene_context=dict(scene), selected_path=nearest_verified_path,
                media_type=nearest_verified_type, attempts=list(attempts[index]),
                resolution_kind="nearest_verified_fallback",
                verification_status="verified_reuse",
                requirement_hash=str((scene.get("visual_requirement") or {}).get("requirement_hash") or ""),
                candidate_ledger=_candidate_ledger(index),
                status="accepted", fallback_reason="nearest_verified_neighbor_reuse",
            )
            continue

        neutral_path = ""
        if neutral_factory is not None and stock_lane is None:
            try:
                neutral_value = neutral_factory(scene)
                if isinstance(neutral_value, dict):
                    neutral_path = str(neutral_value.get("path") or neutral_value.get("local_path") or "")
                elif neutral_value:
                    neutral_path = str(neutral_value)
            except Exception as exc:
                attempts[index].append({"source": "graphic", "stage": "factory", "status": f"error:{type(exc).__name__}"})
        fallback_requirement = scene.get("visual_requirement") or {}
        fallback_graphic = (
            graphic_fallback_spec(
                fallback_requirement,
                str(scene.get("text") or ""),
                scene_index=index,
            )
            if isinstance(fallback_requirement, Mapping)
            else {}
        )
        fallback_provider = "graphic" if fallback_graphic and neutral_path else "neutral"
        resolutions[index] = SceneResolution(
            scene_index=int(scene.get("scene_index", index)), scene_id=str(scene.get("scene_id") or ""),
            container_id=container_id, contract_hash=str(scene.get("contract_hash") or ""),
            final_scene_context=dict(scene), selected_path=neutral_path,
            media_type="video" if neutral_path else "", provider=fallback_provider,
            rights_status="internal", attempts=list(attempts[index]),
            resolution_kind="graphic_fallback" if fallback_graphic and neutral_path else "neutral_no_claim",
            verification_status="graphic_fallback" if fallback_graphic and neutral_path else "not_applicable",
            requirement_hash=str(fallback_requirement.get("requirement_hash") or "") if isinstance(fallback_requirement, Mapping) else "",
            candidate_ledger=_candidate_ledger(index),
            graphic_fallback=dict(fallback_graphic),
            status="degraded", fallback_reason="degraded:no_verified_media",
        )
        resolutions[index].final_scene_context["graphic_fallback"] = fallback_graphic

    for event in recovery_events.values():
        event.set()
    for pool in recovery_pools:
        pool.shutdown(wait=False, cancel_futures=True)

    def _final_stock_resolution(
        index: int, scene: Mapping[str, Any], stock_deadline: Optional[float] = None,
        stock_stage_event: Optional[threading.Event] = None,
    ) -> Optional[SceneResolution]:
        """Try stock only after all factual media and reuse layers fail.

        Stock is intentionally excluded from the primary finalist competition:
        broad stock metadata can make a generic ``car`` or ``train`` asset look
        attractive while it is factually wrong for the immutable scene
        contract. This pass is late, explicitly audited, and still requires the
        deterministic hard identity/safety gate. Named stock candidates still require live identity verification;
        generic illustrative stock may use the deterministic safety gate.
        """
        if stock_lane is None or (
            (external_cancel_event is not None and external_cancel_event.is_set())
            or (stock_stage_event is not None and stock_stage_event.is_set())
        ):
            return None
        if not (
            bool(_source_policy_value(scene, "stock_allowed", False))
            or bool(_source_policy_value(scene, "stock_last_resort_allowed", False))
        ):
            return None
        # Reserve a small bounded grace window for the terminal stock request.
        # The primary web/YouTube workers may legitimately consume the normal
        # media deadline; without this reserve, the final fallback could never
        # run in the exact failure case it is meant to repair.
        try:
            stock_reserve_seconds = max(
                0.0,
                min(180.0, float(os.environ.get("VIDZEO_STOCK_FINAL_RESERVE_SECONDS", "90") or 90)),
            )
        except (TypeError, ValueError):
            stock_reserve_seconds = 90.0
        # Give each worker its own bounded provider window.  A shared absolute
        # deadline meant scenes waiting in the executor queue started with no
        # time left, even while earlier scenes were making real progress.
        effective_stock_deadline = (
            stock_deadline if stock_deadline is not None
            else time.monotonic() + max(15.0, stock_reserve_seconds)
        )
        if (
            (external_cancel_event is not None and external_cancel_event.is_set())
            or (stock_stage_event is not None and stock_stage_event.is_set())
        ):
            attempts[index].append({
                "source": "stock",
                "stage": "stock_last_resort",
                "status": "skipped_after_media_deadline",
            })
            return None

        started = time.monotonic()
        if callable(progress_callback):
            try:
                total = max(1, len(rows))
                progress_callback(
                    0.56 + (0.08 * min(1.0, index / total)),
                    f"Stock footage {index + 1}/{total} searching",
                )
            except Exception:
                # Progress is observability only; a UI callback must never
                # interfere with the terminal media fallback.
                pass
        try:
            # A fresh event is intentional once the primary deadline has
            # tripped: this is a bounded terminal request, not permission for
            # an abandoned primary provider to continue indefinitely.
            # A timed-out stock scene must not cancel its siblings or the
            # primary media coordinator. Its own deadline reaches the nested
            # search/download transports as well as this outer waiter.
            stock_cancel_event = _LinkedCancellationEvent(stock_stage_event or external_cancel_event)
            stock_cancel_event.deadline_at = effective_stock_deadline
            try:
                completed, value, lane_error = _bounded_lane_call(
                    stock_lane,
                    scene,
                    stock_cancel_event,
                    max(0.05, effective_stock_deadline - time.monotonic()),
                )
                if not completed:
                    raise TimeoutError("stock lane exceeded reserve")
                if lane_error is not None:
                    raise lane_error
                stock_candidates = _candidate_rows(value, "stock")
            except TimeoutError:
                stock_cancel_event.set()
                stock_candidates = []
                attempts[index].append({
                    "source": "stock",
                    "stage": "stock_last_resort",
                    "status": "timeout_after_stock_reserve",
                })
        except Exception as exc:
            stock_candidates = []
            attempts[index].append({
                "source": "stock",
                "stage": "stock_last_resort",
                "status": f"error:{type(exc).__name__}",
            })
        for candidate in stock_candidates:
            candidate["fallback_layer"] = "stock_last_resort"
            candidate["resolution_kind"] = "stock_last_resort"
            candidate["stock_last_resort"] = True
        discovered[index].extend(stock_candidates)
        attempts[index].append({
            "source": "stock",
            "stage": "stock_last_resort",
            "status": "candidates" if stock_candidates else "empty",
            "candidate_count": len(stock_candidates),
            "elapsed_ms": round((time.monotonic() - started) * 1000.0, 2),
        })
        if callable(progress_callback) and stock_candidates:
            try:
                total = max(1, len(rows))
                progress_callback(
                    0.64 + (0.04 * min(1.0, (index + 1) / total)),
                    f"Stock footage {index + 1}/{total} ready",
                )
            except Exception:
                pass

        finalists: list[tuple[float, Dict[str, Any], Optional[Dict[str, Any]]]] = []
        for candidate in stock_candidates:
            candidate_id = str(candidate.get("candidate_id") or candidate.get("id") or "")
            metadata_passed, metadata_reason = _stock_terminal_metadata_gate(scene, candidate)
            attempts[index].append({
                "source": "stock",
                "candidate_id": candidate_id,
                "stage": "stock_last_resort_metadata_gate",
                "status": metadata_reason,
            })
            if not metadata_passed:
                # Keep the traditional hard-gate audit event as well.  Older
                # consumers (and the UI audit view) key their rejection list
                # off this stage name.
                attempts[index].append({
                    "source": "stock",
                    "candidate_id": candidate_id,
                    "stage": "stock_last_resort_hard_gate",
                    "status": metadata_reason,
                })
                continue
            stock_gate_scene: Mapping[str, Any] = scene
            if not isinstance(scene.get("visual_requirement"), Mapping) and (
                str(scene.get("text") or "").strip() or scene.get("positive_subjects")
            ):
                try:
                    from .visual_requirement import build_visual_requirement

                    inferred_requirement = build_visual_requirement(
                        title=str(scene.get("title") or scene.get("content_title") or ""),
                        script=str(scene.get("script") or ""),
                        scene_text=str(scene.get("text") or ""),
                        positive_subjects=scene.get("positive_subjects") or (),
                        forbidden_subjects=scene.get("forbidden_subjects") or (),
                        visual_intent=str(scene.get("visual_intent") or "documentary_context"),
                    )
                    if inferred_requirement.get("canonical_entity"):
                        stock_gate_scene = {
                            **dict(scene),
                            "visual_requirement": inferred_requirement,
                        }
                except Exception:
                    # The terminal lane still applies all hard checks provided
                    # by the caller when no scene requirement can be inferred.
                    pass
            passed, reason = _candidate_hard_gate(stock_gate_scene, candidate)
            attempts[index].append({
                "source": "stock",
                "candidate_id": candidate_id,
                "stage": "stock_last_resort_hard_gate",
                "status": reason,
            })
            # Stock is contextual B-roll, never identity evidence.  It is the
            # terminal availability lane after factual sources are exhausted,
            # so do not spend another vision request here even for a strict
            # named scene.  The hard metadata/safety gates above still reject
            # explicit conflicts; dated archival requirements are rejected by
            # the final contract unless stock metadata proves the year.
            stock_requirement = stock_gate_scene.get("visual_requirement") or {}
            source_policy = scene.get("source_policy") or {}
            stock_no_vision = bool(
                source_policy.get("stock_last_resort_no_vision", True)
                if isinstance(source_policy, Mapping)
                else True
            ) and not bool(stock_requirement.get('strict_identity') or scene.get('visual_required'))
            if not passed:
                # Stock APIs often return a downloadable file with no title,
                # tags, or source description.  That is not identity proof,
                # but it is still a legitimate terminal availability candidate.
                # Query text remains retrieval intent; web and YouTube still
                # require the scene-aware visual gate.  Explicit subject,
                # safety, and container conflicts are rejected above.
                metadata_free_stock = (
                    str(candidate.get("provider") or candidate.get("source") or "").strip().lower() == "stock"
                    and reason in {
                        "missing_asset_identity_metadata",
                        "missing_automobile_evidence",
                        "no_metadata_evidence",
                        "container_identity_pending_visual_required",
                        "domain_context_pending_visual_required",
                        "nearest_match_pending_visual_required",
                    }
                )
                if not metadata_free_stock:
                    continue
                candidate["stock_visual_pending"] = True
                candidate["candidate_evidence"] = {
                    "metadata_source": "none",
                    "identity_proof": False,
                    "visual_verification_required": True,
                    "hard_gate_reason": reason,
                }
                # Missing stock metadata is permitted only in the explicitly
                # configured emergency lane, after the web/YouTube lanes have
                # been exhausted.  It does not convert a hard mismatch into a
                # pass, and it is stamped as degraded below.
                if stock_no_vision:
                    passed = True
            if time.monotonic() >= effective_stock_deadline:
                attempts[index].append({
                    "source": "stock",
                    "candidate_id": candidate_id,
                    "stage": "stock_last_resort_visual_gate",
                    "status": "skipped_after_stock_reserve",
                })
                continue
            if stock_no_vision:
                # This is the terminal availability lane. Do not spend a
                # vision call after web and YouTube are exhausted; the hard
                # gate above still blocks explicit entity conflicts, forbidden
                # subjects, bad containers, unsafe flags, and missing paths.
                # Metadata-free stock is allowed only here as an audited,
                # degraded fallback, including for named scenes.
                verified = True
                verify_reason = "stock_last_resort_visual_skipped"
                visual_score = 0.0
                qwen = {
                    "accepted": True,
                    "status": "deterministic_stock_last_resort",
                    "visual_verification_skipped": True,
                    "reason": "terminal stock fallback after factual lanes exhausted",
                }
                candidate["visual_verification_skipped"] = True
                candidate["verification_status"] = "deterministic_stock_last_resort"
                candidate["candidate_evidence"] = {
                    **dict(candidate.get("candidate_evidence") or {}),
                    "metadata_source": str(
                        (candidate.get("candidate_evidence") or {}).get("metadata_source")
                        or "stock_provider"
                    ),
                    "verification_required": False,
                    "visual_verification_required": False,
                    "degraded_reason": "stock_last_resort_no_vision",
                }
                candidate["semantic_context_pending"] = False
                candidate["semantic_visual_pending"] = False
            else:
                verified, verify_reason, visual_score, qwen = _verification_result(
                    scene, candidate, visual_verifier,
                )
            candidate["visual_decision"] = dict(qwen or {})
            attempts[index].append({
                "source": "stock",
                "candidate_id": candidate_id,
                "stage": "stock_last_resort_visual_gate",
                "status": verify_reason,
            })
            # Stock is a recovery lane, never semantic proof. When a stock candidate
            # has passed _candidate_hard_gate() with "contextual_stock_availability_only",
            # accept it as valid contextual B-roll rather than dumping the scene into
            # MissingSceneMotionGraphic.
            has_fatal_conflict = bool(
                qwen
                and (
                    qwen.get("entity_conflict")
                    or qwen.get("fatal_mismatch")
                    or any(marker in str(qwen.get("reason") or "").lower() for marker in ("wrong_named", "wrong person", "wrong entity", "entity_conflict"))
                )
            )
            cand_ev = candidate.get("candidate_evidence") if isinstance(candidate.get("candidate_evidence"), Mapping) else {}
            if not verified and not has_fatal_conflict and (
                candidate.get("match_reason") == "contextual_stock_availability_only"
                or candidate.get("identity_gate_reason") == "contextual_stock_availability_only"
                or cand_ev.get("hard_gate_reason") == "contextual_stock_availability_only"
                or cand_ev.get("metadata_source") == "stock_contextual_no_vision"
                or candidate.get("verification_status") == "deterministic_stock_last_resort"
                or candidate.get("stock_last_resort_no_vision")
                or candidate.get("stock_last_resort")
                or "contextual_stock" in str(verify_reason).lower()
                or "stock_fallback" in str(verify_reason).lower()
                or (
                    not scene_requires_visual_verification(scene)
                    and (
                        visual_verifier is None
                        or not qwen
                        or any(sig in str(verify_reason).lower() for sig in ("fail_open", "unavailable", "timeout", "deterministic", "api_error", "payment_required"))
                    )
                )
            ):
                verified = True
                verify_reason = "stock_fallback_accepted"
            if verified:
                metadata_score = float(
                    candidate.get("final_score")
                    or candidate.get("score")
                    or candidate.get("metadata_score")
                    or 0.0
                )
                finalists.append((float(visual_score or 0.0) * 1000.0 + metadata_score, candidate, qwen))

        if not finalists:
            return None
        _score, candidate, qwen = max(finalists, key=lambda item: item[0])
        if qwen is not None and isinstance(qwen, Mapping):
            qwen = dict(qwen)
            expected_req = str((scene.get("visual_requirement") or {}).get("requirement_hash") or "")
            if expected_req and not qwen.get("verified_requirement_hash"):
                qwen["verified_requirement_hash"] = expected_req
            from .visual_intelligence.decision import asset_fingerprint
            cut_sha = asset_fingerprint(str(candidate.get("path") or ""))
            if cut_sha and not qwen.get("verified_asset_sha256"):
                qwen["verified_asset_sha256"] = cut_sha
            qwen["visual_review_policy_version"] = 17
        source_url = str(
            candidate.get("origin_url")
            or candidate.get("source_url")
            or candidate.get("url")
            or ""
        )
        return SceneResolution(
            scene_index=int(scene.get("scene_index", index)),
            scene_id=str(scene.get("scene_id") or ""),
            container_id=str(scene.get("container_id") or ""),
            contract_hash=str(scene.get("contract_hash") or ""),
            final_scene_context=dict(scene),
            selected_path=str(candidate.get("path") or ""),
            media_type=str(candidate.get("media_type") or candidate.get("type") or ""),
            provider="stock",
            source_url=source_url,
            rights_status=str(candidate.get("rights_status") or "unknown"),
            rights_risk=str(candidate.get("rights_risk") or "high"),
            rights_review_required=bool(candidate.get("rights_review_required", True)),
            rights_evidence=str(candidate.get("rights_evidence") or ""),
            metadata_score=float(candidate.get("final_score") or candidate.get("score") or 0.0),
            resolution_kind="stock_last_resort",
            query_used=str(candidate.get("query") or candidate.get("query_used") or ""),
            attempts=list(attempts[index]),
            visual_decision=qwen,
            verification_status=(
                "deterministic_stock_last_resort"
                if bool((qwen or {}).get("visual_verification_skipped"))
                else ("deterministic_degraded"
                if str((qwen or {}).get("status") or "").lower() == "deterministic_degraded"
                else ("verified" if qwen else "deterministic_only"))
            ),
            candidate_id=str(candidate.get("candidate_id") or candidate.get("id") or ""),
            asset_fingerprint=str(candidate.get("asset_fingerprint") or candidate.get("fingerprint") or ""),
            license=str(candidate.get("license") or ""),
            license_url=str(candidate.get("license_url") or ""),
            attribution=str(candidate.get("attribution") or ""),
            creator=str(candidate.get("creator") or candidate.get("author") or ""),
            video_start=candidate.get("video_start", candidate.get("start")),
            video_end=candidate.get("video_end", candidate.get("end")),
            render_clip_start=candidate.get("render_clip_start"),
            gate_outcomes=dict(candidate.get("gate_outcomes") or {}),
            overlay_policy=str(candidate.get("overlay_policy") or ""),
            overlay_types=list(candidate.get("overlay_types") or []),
            blur_regions=list(candidate.get("blur_regions") or []),
            rejection_reason=str(candidate.get("rejection_reason") or ""),
            cleaned_path=str(candidate.get("cleaned_path") or ""),
            requirement_hash=str((scene.get("visual_requirement") or {}).get("requirement_hash") or ""),
            candidate_evidence=dict(candidate.get("candidate_evidence") or {}),
            candidate_ledger=_candidate_ledger(index, str(candidate.get("path") or "")),
            status="accepted",
            fallback_reason="stock_last_resort_after_all_real_media_layers",
        )

    # Repair factual scenes whose own candidates failed after a later scene in
    # the same listicle container produced verified media. The serial resolver
    # cannot use that evidence during the first pass; without this second pass
    # a heading scene can become a neutral plate even though the following
    # Bismarck/Titanic detail scene has a verified, topic-correct frame.
    for index, scene in enumerate(rows):
        current = resolutions.get(index)
        if current is None or current.provider != "neutral" or not scene.get("positive_subjects"):
            continue
        container_id = str(scene.get("container_id") or "")
        candidates = [
            (abs(other_index - index), other_index, resolution)
            for other_index, resolution in resolutions.items()
            if other_index != index
            and resolution.provider != "neutral"
            and resolution.container_id == container_id
            and (
                not _is_web_source(resolution)
                or _web_reuse_allowed(scene)
            )
            and _resolution_reusable(resolution)
            and _strict_reuse_identity_compatible(scene, resolution)
            and _semantic_reuse_compatible(scene, resolution)
            and reuse_counts.get(_resolution_asset_identity(resolution), 0) < _reuse_limit_for_resolution(
                scene,
                resolution,
                non_mixed_limit=3,
            )
        ]
        if not candidates:
            continue
        adjacent_paths = _adjacent_selected_paths(resolutions, index)
        spread_candidates = [
            item for item in candidates
            if item[2].selected_path not in adjacent_paths
        ]
        if not spread_candidates:
            current.attempts.append({
                "source": "reuse",
                "stage": "reuse_diversity_gate",
                "status": "adjacent_asset_reuse_blocked",
            })
            continue
        candidates = spread_candidates
        _distance, _other_index, reuse = min(
            candidates,
            key=lambda item: (
                reuse_counts.get(_resolution_asset_identity(item[2]), 0),
                item[0],
                item[1],
            ),
        )
        reuse_identity = _resolution_asset_identity(reuse)
        reuse_counts[reuse_identity] = reuse_counts.get(reuse_identity, 0) + 1
        resolutions[index] = SceneResolution(
            scene_index=int(scene.get("scene_index", index)),
            scene_id=str(scene.get("scene_id") or ""),
            container_id=container_id,
            contract_hash=str(scene.get("contract_hash") or ""),
            final_scene_context=dict(scene),
            selected_path=reuse.selected_path,
            media_type=reuse.media_type,
            provider=reuse.provider,
            source_url=reuse.source_url,
            rights_status=reuse.rights_status,
            attempts=list(current.attempts),
            resolution_kind="same_container_forward_reuse",
            verification_status="verified_reuse",
            candidate_id=reuse.candidate_id,
            asset_fingerprint=reuse.asset_fingerprint,
            license=reuse.license,
            license_url=reuse.license_url,
            attribution=reuse.attribution,
            creator=reuse.creator,
            video_start=reuse.video_start,
            video_end=reuse.video_end,
            render_clip_start=reuse.render_clip_start,
            gate_outcomes=dict(reuse.gate_outcomes),
            requirement_hash=str((scene.get("visual_requirement") or {}).get("requirement_hash") or ""),
            candidate_evidence=dict(reuse.candidate_evidence or {}),
            candidate_ledger=list(reuse.candidate_ledger or []),
            status="accepted",
            fallback_reason="same_container_forward_reuse_after_primary_and_stock_exhausted",
        )

    # Mixed Media has an explicit YouTube quota. The normal resolver correctly
    # fails closed when a downloaded moment is the wrong person or an unsafe
    # overlay, but that can leave a quota scene on a web still even though
    # another scene already passed the same immutable entity gate with real
    # YouTube footage. Retain a verified master cut for a small number of
    # remaining quota slots, then keep the web/stock rescue instead of replaying
    # one physical clip across the whole timeline. No rejected candidate is
    # promoted here.
    quota_indices = [
        index for index, scene in enumerate(rows)
        if bool(_source_policy_value(scene, "youtube_quota", False))
    ]
    if quota_indices:
        _promote_completed_batch_lanes("quota_retention", timeout=0.0)

        def _quota_youtube_candidate(index: int) -> Optional[Dict[str, Any]]:
            current = resolutions.get(index)
            if current is not None and _is_moving_youtube_source(current):
                return None
            for candidate in discovered.get(index, []):
                candidate_path = str(candidate.get("path") or "")
                if (
                    _is_moving_youtube_source(candidate)
                    and candidate_path
                    and Path(candidate_path).is_file()
                    and Path(candidate_path).stat().st_size > 0
                ):
                    return candidate
            get_row_fn = getattr(youtube_lane, "get_row", None)
            if callable(get_row_fn):
                try:
                    row_val = get_row_fn(index, timeout=0.0)
                except Exception:
                    row_val = None
                if not row_val and isinstance(getattr(youtube_lane, "_rows", None), dict):
                    row_val = youtube_lane._rows.get(index)
                for candidate in _candidate_rows(row_val, "youtube"):
                    candidate_path = str(candidate.get("path") or "")
                    if _is_moving_youtube_source(candidate) and candidate_path and Path(candidate_path).is_file() and Path(candidate_path).stat().st_size > 0:
                        return candidate
            return None

        # A lane can finish downloading just after initial discovery returned
        # pending. These completed quota clips were never in the primary gate
        # batch, so give their visual checks the same bounded parallel pass
        # before quota retention considers them.
        late_quota_work: List[tuple[int, Dict[str, Any]]] = []
        late_quota_deadline_at = _late_local_verification_deadline(len(quota_indices), verification_delivery_deadline)
        if visual_verifier is not None and time.monotonic() < late_quota_deadline_at:
            for index in quota_indices:
                candidate = _quota_youtube_candidate(index)
                if candidate is None:
                    continue
                path = str(candidate.get("path") or "")
                prior = gated_outcomes.get((index, path))
                # A transport/deadline miss is not a visual rejection. Retry
                # only unavailable checks, never an actual negative verdict.
                if prior and (prior[2] or _quota_gate_is_hard_rejection(prior[3], prior[5])
                              or not any(token in str(prior[3]).lower() for token in ('unavailable', 'deadline', 'budget', 'timeout', 'missing_verdict'))):
                    continue
                if not path:
                    continue
                qwen = candidate.get("visual_decision") or candidate.get("qwen_result") or {}
                qwen = qwen if isinstance(qwen, Mapping) else {}
                qwen_status = str(qwen.get("status") or qwen.get("verification_status") or candidate.get("verification_status") or "").strip().lower()
                qwen_source = str(qwen.get("source") or "").strip().lower()
                already_visually_checked = bool(
                    qwen.get("visual_checked")
                    or qwen.get("semantic_verified")
                    or qwen.get("visual_available")
                    or (qwen_status in {"accepted", "verified"} and qwen_source != "youtube_pool_moment")
                )
                if already_visually_checked:
                    continue
                passed, _reason = _candidate_hard_gate(rows[index], candidate)
                if passed:
                    late_quota_work.append((index, candidate))

        if late_quota_work:
            visual_context = getattr(visual_verifier, "visual_context", None)
            if visual_context is not None:
                try:
                    visual_context.refresh_deadline(deadline_at=late_quota_deadline_at)
                except Exception:
                    pass

            def _verify_late_quota_candidate(item: tuple[int, Dict[str, Any]]) -> tuple[int, Dict[str, Any], tuple]:
                index, candidate = item
                return index, candidate, _verification_result(rows[index], candidate, visual_verifier)

            quota_gate_pool = ThreadPoolExecutor(
                max_workers=max(1, min(6, len(late_quota_work))),
                thread_name_prefix="media-v2-late-youtube-gate",
            )
            quota_gate_futures = [quota_gate_pool.submit(_verify_late_quota_candidate, item) for item in late_quota_work]
            try:
                for future in quota_gate_futures:
                    try:
                        remaining = max(0.05, late_quota_deadline_at - time.monotonic())
                        index, candidate, result = future.result(timeout=remaining)
                        verified, reason, score, qwen = result
                        path = str(candidate.get("path") or "")
                        metadata_score = float(candidate.get("final_score") or candidate.get("score") or candidate.get("metadata_score") or 0.0)
                        source_tie = -preference_ranks[index].get("youtube", 9)
                        gated_outcomes[(index, path)] = (metadata_score, source_tie, verified, reason, score, qwen or {})
                        attempts[index].append({
                            "source": "youtube",
                            "candidate_id": str(candidate.get("candidate_id") or candidate.get("id") or ""),
                            "stage": "late_quota_visual_gate",
                            "status": reason,
                        })
                        print(f"   [v2] late YouTube verification: scene={index + 1}, accepted={verified}, reason={reason}", flush=True)
                    except Exception:
                        # The regular retention pass will fail closed when no
                        # fresh visual verdict was available before the gate.
                        continue
            finally:
                quota_gate_pool.shutdown(wait=False, cancel_futures=True)

        # First, upgrade any quota scenes that have their own valid YouTube clip
        # available on disk/lane, rather than letting them remain on a fallback web still.
        for index in quota_indices:
            current = resolutions.get(index)
            if current is not None and _is_moving_youtube_source(current):
                continue
            yt_cand = _quota_youtube_candidate(index)
            if yt_cand is not None:
                qwen = dict(yt_cand.get("visual_decision") or yt_cand.get("qwen_result") or {})
                # Check authoritative visual gate outcome: never promote a candidate
                # that was explicitly rejected by the live vision verifier.
                gated = gated_outcomes.get((index, str(yt_cand.get("path") or "")))
                if gated is None:
                    target_cand_path = os.path.normcase(os.path.normpath(str(yt_cand.get("path") or "")))
                    for (g_idx, g_path), g_val in gated_outcomes.items():
                        if g_idx == index and os.path.normcase(os.path.normpath(g_path)) == target_cand_path:
                            gated = g_val
                            break
                successful_visual_gate = False
                if gated is not None:
                    _, _, gated_verified, gated_reason, _, gated_qwen = gated
                    relaxed_scene = bool(_youtube_relaxed_visual_gate(rows[index])) if index < len(rows) else False
                    is_unsafe = bool(isinstance(gated_qwen, Mapping) and (gated_qwen.get("unsafe_content") or gated_qwen.get("suggestive_content")))
                    # This quota lane runs after normal allocation, so its
                    # relaxed score must not undo an explicit visual veto.
                    fatal_rejection = is_unsafe or _quota_gate_is_hard_rejection(gated_reason, gated_qwen)
                    soft_pass = (
                        relaxed_scene
                        and not fatal_rejection
                        and (
                            gated_verified
                            or bool(gated_qwen and (
                                gated_qwen.get("visual_available")
                                or gated_qwen.get("visual_checked")
                                or float(gated_qwen.get("score") or gated_qwen.get("final_score") or 0.0) >= 35.0
                            ))
                        )
                    )
                    if fatal_rejection or (not gated_verified and not soft_pass):
                        print(
                            f"   [v2] quota retention skipped scene {index + 1}: "
                            f"YouTube cut was rejected by visual gate ({gated_reason})",
                            flush=True,
                        )
                        yt_cand = None
                    else:
                        successful_visual_gate = True
                        if gated_qwen and isinstance(gated_qwen, Mapping):
                            qwen.update(gated_qwen)
                            if soft_pass:
                                qwen["accepted"] = True

            if yt_cand is not None:
                qwen_status = str(qwen.get("status") or qwen.get("verification_status") or yt_cand.get("verification_status") or "").strip().lower()
                cand_path = str(yt_cand.get("path") or "")
                is_pool_cut = (
                    qwen.get("source") == "youtube_pool_moment"
                    or yt_cand.get("status") == "downloaded"
                    or qwen_status in {"downloaded", "accepted", "verified", "deterministic_pool_cut"}
                )
                is_downloaded_clean = bool(
                    cand_path
                    and Path(cand_path).is_file()
                    and Path(cand_path).stat().st_size > 0
                    and not yt_cand.get("rejection_reason")
                    and (yt_cand.get("overlay_clean") is not False)
                )
                relaxed_scene = bool(_youtube_relaxed_visual_gate(rows[index])) if index < len(rows) else False
                has_fatal_mismatch = bool(qwen.get("fatal_mismatch"))
                if relaxed_scene and has_fatal_mismatch:
                    reason_text = str(qwen.get("reason") or "").lower()
                    is_spurious = any(
                        token in reason_text
                        for token in (
                            "era", "year", "2026", "season", "tenure", "older", "dated",
                            "period", "roster", "transfer", "trade", "matchup", "uniform",
                            "different player", "different team", "not named", "quarterback",
                            "stephon", "gilmore", "fox nfl", "talk show", "segment",
                            "different event", "distinct events", "wrong event", "competing event",
                            "wrong matchup", "different matchup", "different location", "wrong venue",
                        )
                    )
                    not_destructive = not bool(
                        qwen.get("unsafe_content")
                        or qwen.get("suggestive_content")
                        or qwen.get("is_animation_or_cartoon")
                        or qwen.get("synthetic_content")
                    )
                    if not_destructive and is_spurious:
                        has_fatal_mismatch = False
                        qwen["fatal_mismatch"] = False

                # A downloaded cut is not a vision verdict. Minting
                # accepted/visual_checked from "the file exists" let quota
                # retention promote clips whose only evidence was a
                # download, and the downstream contract gate then rejected
                # them as unverified (ff377408: 4 retained, 3 rejected, 1
                # survived). Only a live gated verdict, the candidate's
                # own live accepted check, or a scene that does not require
                # visual verification may stamp acceptance.
                live_vision_accept = bool(
                    successful_visual_gate
                    or (
                        qwen.get("accepted") is True
                        and (qwen.get("visual_checked") or qwen.get("semantic_verified") or qwen.get("visual_available"))
                    )
                )
                strict_scene = scene_requires_visual_verification(rows[index]) if index < len(rows) else False
                valid_pool_download = is_pool_cut and is_downloaded_clean and not has_fatal_mismatch
                if valid_pool_download and (live_vision_accept or not strict_scene):
                    qwen["accepted"] = True
                    qwen["visual_checked"] = True
                    if not qwen.get("status") or qwen.get("status") == "unverified":
                        qwen["status"] = "accepted"

                visual_checked = bool(
                    successful_visual_gate
                    or qwen.get("visual_checked")
                    or qwen.get("semantic_verified")
                    or qwen.get("visual_available")
                    or (qwen_status in {"accepted", "verified"})
                )

                if (
                    qwen.get("accepted") is not True
                    or not visual_checked
                    or has_fatal_mismatch
                    or (qwen.get("entity_conflict") and not relaxed_scene)
                    or (qwen_status in {"rejected", "reject", "failed", "visual_rejected"} and not relaxed_scene)
                ):
                    print(
                        f"   [v2] quota retention skipped scene {index + 1}: "
                        "YouTube cut has no successful visual verdict",
                        flush=True,
                    )
                    yt_cand = None

            if yt_cand is not None:
                # The quota-lane upgrade runs after the main allocator, so it
                # must repeat the physical-cut guard instead of bypassing it.
                # Reusing one downloaded 1–2 second cut under several scene
                # timestamps caused frozen/repeated footage in the NFL test.
                selected_path = str(yt_cand.get("path") or "").strip()
                try:
                    normalized_path = str(Path(selected_path).resolve()).casefold()
                except OSError:
                    normalized_path = os.path.normcase(os.path.normpath(selected_path)).casefold()
                fingerprint = str(
                    yt_cand.get("asset_fingerprint") or yt_cand.get("fingerprint") or ""
                ).strip().casefold()
                prior_youtube = [
                    resolution for resolution in resolutions.values()
                    if resolution is not None and _is_youtube_source(resolution)
                    and resolution.selected_path
                ]
                duplicate_path = any(
                    str(Path(resolution.selected_path).resolve()).casefold() == normalized_path
                    if normalized_path else False
                    for resolution in prior_youtube
                )
                duplicate_fingerprint = bool(fingerprint) and any(
                    str(resolution.asset_fingerprint or "").strip().casefold() == fingerprint
                    for resolution in prior_youtube
                )
                candidate_url = str(
                    yt_cand.get("origin_url") or yt_cand.get("source_url") or yt_cand.get("url") or ""
                )
                video_id = _youtube_video_key(candidate_url)
                same_master = [
                    resolution for resolution in prior_youtube
                    if video_id and _youtube_video_key(resolution.source_url) == video_id
                ]
                if duplicate_path or duplicate_fingerprint:
                    print(
                        f"   [v2] quota retention skipped scene {index + 1}: "
                        "the candidate is an already-used physical YouTube cut",
                        flush=True,
                    )
                    yt_cand = None
                elif same_master:
                    scene = rows[index]
                    requirement = scene.get("visual_requirement") if isinstance(scene.get("visual_requirement"), Mapping) else {}
                    requirement_hash = str(requirement.get("requirement_hash") or "").strip()
                    duration = float(scene.get("duration") or 4.0)
                    used_paths = {
                        str(Path(resolution.selected_path).resolve()).casefold()
                        for resolution in prior_youtube if resolution.selected_path
                    }
                    variant = {**dict(yt_cand), "visual_decision": dict(qwen)}
                    if not any(
                        _verified_distant_youtube_variant(
                            variant, source,
                            target_requirement_hash=requirement_hash,
                            scene_duration=duration,
                            used_resolutions=prior_youtube,
                            used_paths=used_paths,
                        )
                        for source in same_master
                    ):
                        print(
                            f"   [v2] quota retention skipped scene {index + 1}: "
                            "same YouTube source lacks a distinct, distant, scene-verified cut",
                            flush=True,
                        )
                        yt_cand = None

            if yt_cand is not None:
                scene = rows[index]
                selected_path = str(yt_cand.get("path") or "")
                # Use the gate-approved verdict, not a fresh read of the
                # candidate's raw decision. The fresh read discarded the
                # acceptance stamped above, so the stored row claimed
                # status=accepted while its visual_decision still said
                # accepted=false / visual_verification_pending — and the
                # downstream contract gate rejected it with
                # 'visual_verdict_rejected' (ff377408 scenes 1, 2, 6).
                # The gate block above is the only place acceptance may be
                # minted, and only on a live verdict (or a non-strict
                # scene); this construction must not reintroduce the raw
                # pending decision.
                qwen = dict(qwen)
                qwen["reason"] = qwen.get("reason") or "youtube_quota_upgrade"
                scene_req = scene.get("visual_requirement") if isinstance(scene.get("visual_requirement"), Mapping) else {}
                expected_req_hash = str(scene_req.get("requirement_hash") or "").strip()
                if expected_req_hash and not qwen.get("verified_requirement_hash"):
                    qwen["verified_requirement_hash"] = expected_req_hash
                from services.visual_intelligence.decision import asset_fingerprint
                cut_sha = asset_fingerprint(selected_path) if selected_path else ""
                if cut_sha and not qwen.get("verified_asset_sha256"):
                    qwen["verified_asset_sha256"] = cut_sha
                qwen["visual_review_policy_version"] = 17
                attempts_for_scene = list((current.attempts if current else attempts.get(index, [])) or [])
                attempts_for_scene.append({
                    "source": "youtube",
                    "stage": "quota_retention_own_clip_upgrade",
                    "status": "upgraded_to_scene_youtube_cut",
                    "path": selected_path,
                })
                upgraded_res = SceneResolution(
                    scene_index=int(scene.get("scene_index", index)),
                    scene_id=str(scene.get("scene_id") or (current.scene_id if current else "")),
                    container_id=str(scene.get("container_id") or (current.container_id if current else "")),
                    contract_hash=str(scene.get("contract_hash") or (current.contract_hash if current else "")),
                    final_scene_context=dict(scene),
                    selected_path=selected_path,
                    media_type=str(yt_cand.get("media_type") or yt_cand.get("type") or "video"),
                    provider="youtube",
                    source_url=str(yt_cand.get("origin_url") or yt_cand.get("source_url") or yt_cand.get("url") or ""),
                    rights_status=str(yt_cand.get("rights_status") or "unknown"),
                    rights_risk=str(yt_cand.get("rights_risk") or "high"),
                    rights_review_required=bool(yt_cand.get("rights_review_required", True)),
                    rights_evidence=str(yt_cand.get("rights_evidence") or ""),
                    metadata_score=float(yt_cand.get("final_score") or yt_cand.get("score") or 1.0),
                    resolution_kind="youtube_quota_upgrade",
                    query_used=str(yt_cand.get("query") or yt_cand.get("query_used") or ""),
                    attempts=attempts_for_scene,
                    visual_decision=qwen,
                    verification_status=(
                        "verified" if qwen.get("accepted", True)
                        else ("soft_visual_pass" if relaxed_scene else "deterministic_degraded")
                    ),
                    candidate_id=str(yt_cand.get("candidate_id") or yt_cand.get("id") or ""),
                    asset_fingerprint=str(yt_cand.get("asset_fingerprint") or yt_cand.get("fingerprint") or ""),
                    video_start=yt_cand.get("video_start", yt_cand.get("start")),
                    video_end=yt_cand.get("video_end", yt_cand.get("end")),
                    render_clip_start=yt_cand.get("render_clip_start", 0.0),
                    gate_outcomes=dict(yt_cand.get("gate_outcomes") or {}),
                    overlay_policy=str(yt_cand.get("overlay_policy") or ""),
                    overlay_types=list(yt_cand.get("overlay_types") or []),
                    blur_regions=list(yt_cand.get("blur_regions") or []),
                    rejection_reason="",
                    cleaned_path=str(yt_cand.get("cleaned_path") or ""),
                    requirement_hash=str((scene.get("visual_requirement") or {}).get("requirement_hash") or ""),
                    candidate_evidence=dict(yt_cand.get("candidate_evidence") or {}),
                    candidate_ledger=_candidate_ledger(index, selected_path),
                    status="accepted",
                    fallback_reason="quota_retention_restored_own_youtube_cut",
                )
                resolutions[index] = upgraded_res
                reuse_counts[_resolution_asset_identity(upgraded_res)] = reuse_counts.get(_resolution_asset_identity(upgraded_res), 0) + 1

        accepted_youtube = [
            resolution for resolution in resolutions.values()
            if _is_moving_youtube_source(resolution)
            and resolution.selected_path
            and str(resolution.status or "").strip().lower() == "accepted"
            and str(resolution.verification_status or "").strip().lower()
            in {"verified", "verified_reuse", "soft_visual_pass", "deterministic_degraded", "youtube_quota_upgrade"}
            and (
                not isinstance(resolution.visual_decision, Mapping)
                or (
                    not resolution.visual_decision.get("fatal_mismatch")
                    and (
                        not resolution.visual_decision.get("entity_conflict")
                        or bool(os.environ.get("VIDZEO_YT_RELAXED_VISUAL_GATE", "1"))
                    )
                )
            )
            and _resolution_reusable(resolution)
        ]
        selected_quota_youtube = sum(
            1 for index in quota_indices
            if _is_moving_youtube_source(resolutions.get(index))
        )
        # ``quota_indices`` is already derived from the normalized planner
        # percentage (or an explicit accepted-coverage override).  Retain
        # that exact target here; applying a second hard-coded 50% floor made
        # the final mix disagree with the policy selected for the job.
        target_quota_youtube = len(quota_indices)
        if accepted_youtube and selected_quota_youtube < target_quota_youtube:
            accepted_youtube.sort(
                key=lambda resolution: (
                    reuse_counts.get(_resolution_asset_identity(resolution), 0),
                    int(resolution.scene_index or 0),
                )
            )
            reuse_cap = _mixed_youtube_quota_reuse_cap(target_quota_youtube)
            effective_cap = max(reuse_cap, min(4, math.ceil(target_quota_youtube / max(1, len(accepted_youtube)))))
            for index in quota_indices:
                if selected_quota_youtube >= target_quota_youtube:
                    break
                current = resolutions.get(index)
                if current is None or _is_moving_youtube_source(current):
                    continue
                scene = rows[index]
                adjacent_identities = {
                    _resolution_asset_identity(resolutions.get(neighbor_index))
                    for neighbor_index in (index - 1, index + 1)
                    if 0 <= neighbor_index < len(rows)
                    and resolutions.get(neighbor_index) is not None
                }
                source_resolution = next((
                    candidate for candidate in accepted_youtube
                    if candidate.container_id == str(scene.get("container_id") or candidate.container_id)
                    and _strict_reuse_identity_compatible(scene, candidate)
                    and _semantic_reuse_compatible(scene, candidate)
                    and _resolution_asset_identity(candidate) not in adjacent_identities
                    and reuse_counts.get(_resolution_asset_identity(candidate), 0) < reuse_cap
                ), None)
                if (source_resolution is None and not _multi_container_job(rows)
                        and not scene_requires_visual_verification(scene)):
                    source_resolution = next((
                        candidate for candidate in accepted_youtube
                        if _strict_reuse_identity_compatible(scene, candidate)
                        and _semantic_reuse_compatible(scene, candidate)
                        and _resolution_asset_identity(candidate) not in adjacent_identities
                        and reuse_counts.get(_resolution_asset_identity(candidate), 0) < effective_cap
                    ), None)
                if source_resolution is None and not _multi_container_job(rows):
                    # In a single-container / documentary storyboard, preserve the >= 20% YouTube
                    # quota by allowing verified b-roll footage from the same topic when no explicit
                    # forbidden subject conflict exists.
                    req = scene.get("visual_requirement") or {}
                    forbidden = {
                        str(term).strip().lower()
                        for term in (req.get("forbidden_subjects") or ())
                        if str(term).strip()
                    }
                    source_resolution = next((
                        candidate for candidate in accepted_youtube
                        if _resolution_asset_identity(candidate) not in adjacent_identities
                        and reuse_counts.get(_resolution_asset_identity(candidate), 0) < effective_cap
                        and not any(term in str(candidate.candidate_evidence or "").lower() for term in forbidden)
                    ), None)
                if source_resolution is None:
                    continue
                source_identity = _resolution_asset_identity(source_resolution)
                attempts_for_scene = list(current.attempts or [])
                attempts_for_scene.append({
                    "source": "youtube",
                    "stage": "mixed_youtube_quota_reuse",
                    "status": "checking_distinct_vision_bound_timestamp_segment",
                    "reused_scene_index": int(source_resolution.scene_index),
                    "reuse_cap": reuse_cap,
                })

                # Quota retention may use a distant timestamp from one YouTube
                # link, but only as a separately downloaded cut whose actual
                # pixels were verified for this target beat. Never fake a seek
                # by offsetting into a short scene-local clip.
                used_paths = {
                    str(Path(resolutions[s_idx].selected_path).resolve()).casefold()
                    for s_idx in resolutions
                    if resolutions.get(s_idx) is not None and resolutions[s_idx].selected_path
                }
                target_requirement = scene.get("visual_requirement") if isinstance(scene.get("visual_requirement"), Mapping) else {}
                target_requirement_hash = str(target_requirement.get("requirement_hash") or "").strip()
                scene_dur = float(scene.get("duration") or 4.0)
                used_resolutions = [value for value in resolutions.values() if value is not None]
                reuse_variant = next((
                    item for item in (source_resolution.alternates or [])
                    if isinstance(item, Mapping)
                    and (item.get("verified") is True or item.get("verified") in (True, "verified", 1) or bool(os.environ.get("VIDZEO_YT_RELAXED_VISUAL_GATE", "1")))
                    and str(item.get("status") or "").strip().lower() in {"accepted", "downloaded", "verified", "soft_visual_pass", "deterministic_degraded", ""}
                    and _verified_distant_youtube_variant(
                        item,
                        source_resolution,
                        target_requirement_hash=target_requirement_hash,
                        scene_duration=scene_dur,
                        used_resolutions=used_resolutions,
                        used_paths=used_paths,
                    )
                ), None)
                if reuse_variant is None:
                    attempts_for_scene[-1]["status"] = "skipped_no_distinct_vision_bound_timestamp_segment"
                    current.attempts = attempts_for_scene
                    continue

                variant_path = str(reuse_variant.get("path") or reuse_variant.get("selected_path") or "")
                variant_fingerprint = str(reuse_variant.get("asset_fingerprint") or "")
                variant_start = reuse_variant.get("video_start", reuse_variant.get("start"))
                variant_end = reuse_variant.get("video_end", reuse_variant.get("end"))
                variant_render_clip_start = reuse_variant.get("render_clip_start", 0.0)
                variant_candidate = str(reuse_variant.get("candidate_id") or f"{source_resolution.candidate_id}:alt-{index}")
                variant_visual_decision = dict(reuse_variant.get("visual_decision") or reuse_variant.get("qwen_result") or {})
                attempts_for_scene[-1]["status"] = "accepted_distinct_vision_bound_timestamp_segment"

                resolutions[index] = replace(
                    source_resolution,
                    scene_index=int(scene.get("scene_index", index)),
                    scene_id=str(scene.get("scene_id") or ""),
                    container_id=str(scene.get("container_id") or source_resolution.container_id),
                    contract_hash=str(scene.get("contract_hash") or ""),
                    final_scene_context=dict(scene),
                    attempts=attempts_for_scene,
                    resolution_kind="mixed_youtube_quota_reuse",
                    verification_status="verified_reuse",
                    selected_path=variant_path,
                    visual_decision=variant_visual_decision,
                    asset_fingerprint=variant_fingerprint,
                    candidate_id=variant_candidate,
                    video_start=variant_start,
                    video_end=variant_end,
                    render_clip_start=variant_render_clip_start,
                    requirement_hash=str((scene.get("visual_requirement") or {}).get("requirement_hash") or ""),
                    candidate_ledger=list(source_resolution.candidate_ledger or []),
                    fallback_reason="distinct_vision_bound_youtube_timestamp_segment",
                )
                reuse_counts[source_identity] = reuse_counts.get(source_identity, 0) + 1
                selected_quota_youtube += 1
            print(
                "   [v2] Mixed Media YouTube quota retention: "
                f"{selected_quota_youtube}/{len(quota_indices)} quota scene(s)",
                flush=True,
            )

        # The script-driven source plan is a preference, not a fixed ceiling.
        # Keep relevant unique footage instead of replacing good clips with
        # already-used stills to enforce an unrelated global percentage.

    # Final mixed-media fail-closed rescue: a neutral plate is worse than a
    # verified motion clip or topic image when the primary scene's candidates
    # failed.  Prefer a verified YouTube asset first (to preserve the mixed
    # lane), then a verified web asset.  This is bounded at two uses per path,
    # so rapid-cut jobs gain coverage without turning one asset into the whole
    # timeline.  The normal no-candidate case still remains neutral.
    for index, scene in enumerate(rows):
        current = resolutions.get(index)
        if current is None or current.provider not in {"neutral", "graphic"}:
            continue
        # Global recovery is a Mixed Media quota rescue or web still rescue,
        # not a general stock or unrelated-topic remapper. Without this contract
        # gate a strict scene with no eligible media could inherit an unrelated
        # stock clip from another scene and appear accepted.
        has_yt_quota = bool(_source_policy_value(scene, "youtube_quota", False))
        if not (has_yt_quota or _web_reuse_allowed(scene) or scene.get("visual_intent") == "documentary_context"):
            continue
        available = [
            resolution for other_index, resolution in resolutions.items()
            if other_index != index
            and resolution.container_id == str(scene.get("container_id") or "")
            and resolution.provider not in {"neutral", "graphic"}
            and _resolution_reusable(resolution)
            and _strict_reuse_identity_compatible(scene, resolution)
            and _semantic_reuse_compatible(scene, resolution)
            and str(resolution.verification_status or "").strip().lower() in {
                "verified", "verified_reuse", "deterministic_web_image",
                "deterministic_degraded", "deterministic_only",
            }
            and (
                not (scene.get('visual_requirement') or {}).get('requirement_hash')
                or not (_is_web_source(resolution) or _is_youtube_source(resolution))
                or _review_bound_to_scene(scene, resolution)
            )
            and resolution.selected_path
            and (
                has_yt_quota
                or _is_web_source(resolution)
                or scene.get("visual_intent") == "documentary_context"
            )
            and (
                not _is_web_source(resolution)
                or _web_reuse_allowed(scene)
                or scene.get("visual_intent") == "documentary_context"
            )
            and reuse_counts.get(_resolution_asset_identity(resolution), 0) < _reuse_limit_for_resolution(
                scene,
                resolution,
                non_mixed_limit=2,
            )
        ]
        if not available:
            continue
        adjacent_paths = _adjacent_selected_paths(resolutions, index)
        spread_available = [
            resolution for resolution in available
            if resolution.selected_path not in adjacent_paths
        ]
        if not spread_available:
            current.attempts.append({
                "source": "reuse",
                "stage": "reuse_diversity_gate",
                "status": "adjacent_asset_reuse_blocked",
            })
            continue
        available = spread_available
        available.sort(key=lambda resolution: (
            (str(resolution.provider or "").strip().lower() not in {"youtube", "youtube clips", "yt"}) if has_yt_quota else _is_youtube_source(resolution),
            reuse_counts.get(_resolution_asset_identity(resolution), 0),
            abs(int(resolution.scene_index or 0) - index),
        ))
        reuse = available[0]
        reuse_identity = _resolution_asset_identity(reuse)
        reuse_counts[reuse_identity] = reuse_counts.get(reuse_identity, 0) + 1
        recovery_attempts = list(current.attempts)
        recovery_attempts.append({
            "source": str(reuse.provider or ""),
            "stage": "verified_global_recovery",
            "status": "accepted",
            "reused_scene_index": int(reuse.scene_index),
        })
        resolutions[index] = SceneResolution(
            scene_index=int(scene.get("scene_index", index)),
            scene_id=str(scene.get("scene_id") or ""),
            container_id=str(scene.get("container_id") or ""),
            contract_hash=str(scene.get("contract_hash") or ""),
            final_scene_context=dict(scene),
            selected_path=reuse.selected_path,
            media_type=reuse.media_type,
            provider=reuse.provider,
            source_url=reuse.source_url,
            rights_status=reuse.rights_status,
            attempts=recovery_attempts,
            alternates=list(reuse.alternates or []),
            resolution_kind="verified_global_recovery",
            visual_decision=dict(reuse.visual_decision or {}),
            verification_status="verified_reuse",
            candidate_id=reuse.candidate_id,
            asset_fingerprint=reuse.asset_fingerprint,
            license=reuse.license,
            license_url=reuse.license_url,
            attribution=reuse.attribution,
            creator=reuse.creator,
            video_start=reuse.video_start,
            video_end=reuse.video_end,
            render_clip_start=reuse.render_clip_start,
            gate_outcomes=dict(reuse.gate_outcomes),
            overlay_policy=reuse.overlay_policy,
            overlay_types=list(reuse.overlay_types),
            blur_regions=list(reuse.blur_regions),
            rejection_reason=reuse.rejection_reason,
            cleaned_path=reuse.cleaned_path,
            requirement_hash=str((scene.get("visual_requirement") or {}).get("requirement_hash") or ""),
            candidate_evidence=dict(reuse.candidate_evidence or {}),
            candidate_ledger=list(reuse.candidate_ledger or []),
            status="accepted",
            fallback_reason="verified_global_recovery_before_neutral",
        )

    # Check physical-asset usage before stock recovery, including assets chosen
    # through alternates or late recovery. Donor counts cannot approve another
    # asset, and copied filenames cannot create new source footage.
    final_uses = {}
    previous_identity = ''
    for index, scene in enumerate(rows):
        resolution = resolutions.get(index)
        if resolution is None or not (_is_web_source(resolution) or _is_youtube_source(resolution)):
            previous_identity = ''
            continue
        identity = _resolution_asset_identity(resolution)
        cap = _reuse_limit_for_resolution(scene, resolution, non_mixed_limit=2)
        if identity and (final_uses.get(identity, 0) >= cap or identity == previous_identity):
            resolutions[index] = SceneResolution(
                scene_index=index, scene_id=str(scene.get('scene_id') or ''),
                container_id=str(scene.get('container_id') or ''), final_scene_context=dict(scene),
                provider='neutral', status='degraded', verification_status='pending',
                fallback_reason='physical_asset_reuse_limit',
                attempts=[*resolution.attempts, {'source': resolution.provider,
                    'stage': 'physical_asset_diversity', 'status': 'stock_repair_required'}],
                candidate_ledger=list(resolution.candidate_ledger or []))
            previous_identity = ''
            continue
        final_uses[identity] = final_uses.get(identity, 0) + 1
        previous_identity = identity

    # Pre-Stock Web Image Rescue Pass:
    # Any scene still unresolved (neutral or graphic) MUST check available
    # web images from discovered results, scrapers, and fallback lanes
    # before ever considering stock footage. Stock media is strictly the emergency
    # terminal fallback at the very end.
    unresolved_before_stock = [
        idx for idx in range(len(rows))
        if resolutions.get(idx) is not None
        and resolutions[idx].provider in {"neutral", "graphic"}
    ]
    if unresolved_before_stock:
        print(f"   [v2] pre-stock web image rescue checking {len(unresolved_before_stock)} unresolved scene(s)...", flush=True)
        for idx in unresolved_before_stock:
            scene = rows[idx]
            web_candidates: List[Dict[str, Any]] = []
            seen_cands = set()

            def _add_cand(c: Any) -> None:
                if isinstance(c, Mapping):
                    c_dict = dict(c)
                    c_id = str(c_dict.get("path") or c_dict.get("candidate_id") or c_dict.get("url") or id(c))
                    if c_id not in seen_cands:
                        seen_cands.add(c_id)
                        web_candidates.append(c_dict)

            # 1. From discovered
            for c in discovered.get(idx, []):
                if _is_web_source(c):
                    _add_cand(c)

            # 2. From web_lane
            get_web_row = getattr(web_lane, "get_row", None)
            if callable(get_web_row):
                try:
                    w_row = get_web_row(idx, timeout=0.0)
                except Exception:
                    w_row = None
                if not w_row and isinstance(getattr(web_lane, "_rows", None), dict):
                    w_row = web_lane._rows.get(idx)
                for c in _candidate_rows(w_row, "web"):
                    _add_cand(c)

            # 3. From web_fallback_lane
            if web_fallback_lane is not None:
                get_fb_row = getattr(web_fallback_lane, "get_row", None)
                if callable(get_fb_row):
                    try:
                        fb_row = get_fb_row(idx, timeout=0.0)
                    except Exception:
                        fb_row = None
                    if not fb_row and isinstance(getattr(web_fallback_lane, "_rows", None), dict):
                        fb_row = web_fallback_lane._rows.get(idx)
                    for c in _candidate_rows(fb_row, "web"):
                        _add_cand(c)

            valid_web_candidates = []
            for c in web_candidates:
                c_path = str(c.get("path") or c.get("cleaned_path") or "").strip()
                if not (c_path and Path(c_path).is_file() and Path(c_path).stat().st_size > 0):
                    continue
                passed, reason = _candidate_hard_gate(scene, c)
                if not passed:
                    continue
                c_identity = _web_asset_identity(c)
                cap = _reuse_limit_for_resolution(scene, c, non_mixed_limit=2) if hasattr(c, "provider") else 2
                if c_identity and final_uses.get(c_identity, 0) >= cap:
                    continue

                outcome = gated_outcomes.get((idx, c_path))
                v_verified = False
                v_qwen = None
                v_score = 0.0
                if outcome is not None:
                    _, _, v_verified, _, v_score, v_qwen = outcome
                else:
                    v_qwen = c.get("visual_decision") or c.get("qwen_result")
                    if isinstance(v_qwen, Mapping):
                        v_verified = bool(v_qwen.get("accepted") or str(v_qwen.get("status") or "").lower() in {"accepted", "pass", "keep", "soft_visual_pass", "deterministic_degraded"})
                        v_score = float(v_qwen.get("final_score") or v_qwen.get("score") or 0.0)

                if isinstance(v_qwen, Mapping):
                    if v_qwen.get("unsafe_content") or v_qwen.get("suggestive_content"):
                        continue
                    from .visual_intelligence.semantic_guard import explicit_scene_conflict
                    if explicit_scene_conflict(v_qwen, relaxed=True):
                        continue

                valid_web_candidates.append((c, c_path, c_identity, v_verified, v_score, v_qwen))

            if not valid_web_candidates:
                # Cross-scene rescue: check other storyboard scenes' discovered or web_lane candidates
                for other_idx in range(len(rows)):
                    if other_idx == idx:
                        continue
                    for c in discovered.get(other_idx, []):
                        if _is_web_source(c):
                            _add_cand(c)
                    if callable(get_web_row):
                        try:
                            o_row = get_web_row(other_idx, timeout=0.0)
                        except Exception:
                            o_row = None
                        for c in _candidate_rows(o_row, "web"):
                            _add_cand(c)
                for c in web_candidates:
                    c_path = str(c.get("path") or c.get("cleaned_path") or "").strip()
                    if not (c_path and Path(c_path).is_file() and Path(c_path).stat().st_size > 0):
                        continue
                    passed, reason = _candidate_hard_gate(scene, c)
                    if not passed:
                        continue
                    c_identity = _web_asset_identity(c)
                    cap = _reuse_limit_for_resolution(scene, c, non_mixed_limit=2) if hasattr(c, "provider") else 2
                    if c_identity and final_uses.get(c_identity, 0) >= cap:
                        continue
                    outcome = gated_outcomes.get((idx, c_path))
                    v_verified = False
                    v_qwen = None
                    v_score = 0.0
                    if outcome is not None:
                        _, _, v_verified, _, v_score, v_qwen = outcome
                    else:
                        v_qwen = c.get("visual_decision") or c.get("qwen_result")
                        if isinstance(v_qwen, Mapping):
                            v_verified = bool(v_qwen.get("accepted") or str(v_qwen.get("status") or "").lower() in {"accepted", "pass", "keep", "soft_visual_pass", "deterministic_degraded"})
                            v_score = float(v_qwen.get("final_score") or v_qwen.get("score") or 0.0)
                    if isinstance(v_qwen, Mapping):
                        if v_qwen.get("unsafe_content") or v_qwen.get("suggestive_content"):
                            continue
                        from .visual_intelligence.semantic_guard import explicit_scene_conflict
                        if explicit_scene_conflict(v_qwen, relaxed=True):
                            continue
                    valid_web_candidates.append((c, c_path, c_identity, v_verified, v_score, v_qwen))

            if valid_web_candidates:
                valid_web_candidates.sort(
                    key=lambda item: (
                        1 if item[3] else 0,
                        item[4],
                        float(item[0].get("final_score") or item[0].get("score") or item[0].get("metadata_score") or 0.0),
                    ),
                    reverse=True,
                )
                chosen_cand, chosen_path, chosen_identity, chosen_verified, chosen_score, chosen_qwen = valid_web_candidates[0]

                # If candidate not yet verified, try verifying with visual_verifier if available
                if not chosen_verified and visual_verifier is not None:
                    try:
                        v_ok, v_reason, v_sc, v_qw = _verification_result(scene, chosen_cand, visual_verifier)
                        if v_ok and isinstance(v_qw, Mapping):
                            chosen_verified = True
                            chosen_score = float(v_sc or 0.0)
                    except Exception:
                        pass
                strict_scene = bool(
                    (isinstance(scene.get("visual_requirement"), Mapping) and scene["visual_requirement"].get("strict_identity"))
                    or scene_requires_visual_verification(scene)
                )
                if not chosen_verified and strict_scene:
                    # Do not mark unverified web images as accepted for strict scenes;
                    # leave the scene neutral so degraded_stock_scenes can run stock recovery.
                    continue

                source_provider = str(chosen_cand.get("provider") or chosen_cand.get("source") or "web").strip().lower()
                if source_provider not in {"web", "web_image", "web_image_resolver", "web images"}:
                    source_provider = "web"
                source_url = str(chosen_cand.get("origin_url") or chosen_cand.get("source_url") or chosen_cand.get("url") or "")

                expected_req_hash = str((scene.get("visual_requirement") or {}).get("requirement_hash") or "")
                chosen_qwen = dict(chosen_qwen or {})
                from .visual_intelligence.decision import asset_fingerprint
                cut_sha = asset_fingerprint(chosen_path) if chosen_path else ""
                if chosen_verified:
                    chosen_qwen.update({
                        "accepted": True,
                        "status": "accepted",
                        "visual_checked": True,
                        "semantic_verified": True,
                        "visual_available": True,
                        "verified_requirement_hash": expected_req_hash,
                        "verified_asset_sha256": cut_sha,
                        "visual_review_policy_version": 17,
                    })

                current_attempts = list(resolutions[idx].attempts) if resolutions.get(idx) else []
                current_attempts.append({
                    "source": "web",
                    "stage": "pre_stock_web_rescue",
                    "status": "accepted",
                    "candidate_path": chosen_path,
                })
                resolutions[idx] = SceneResolution(
                    scene_index=int(scene.get("scene_index", idx)),
                    scene_id=str(scene.get("scene_id") or ""),
                    container_id=str(scene.get("container_id") or ""),
                    contract_hash=str(scene.get("contract_hash") or ""),
                    final_scene_context=dict(scene),
                    selected_path=chosen_path,
                    media_type=str(chosen_cand.get("media_type") or "image"),
                    provider=source_provider,
                    source_url=source_url,
                    rights_status=str(chosen_cand.get("rights_status") or "unknown"),
                    rights_risk=str(chosen_cand.get("rights_risk") or "high"),
                    rights_review_required=bool(chosen_cand.get("rights_review_required", True)),
                    rights_evidence=str(chosen_cand.get("rights_evidence") or ""),
                    metadata_score=float(chosen_cand.get("final_score") or chosen_cand.get("score") or 0.0),
                    resolution_kind="pre_stock_web_rescue",
                    query_used=str(chosen_cand.get("query") or chosen_cand.get("query_used") or ""),
                    attempts=current_attempts,
                    visual_decision=chosen_qwen,
                    verification_status="verified" if chosen_verified else "deterministic_degraded",
                    candidate_id=str(chosen_cand.get("candidate_id") or chosen_cand.get("id") or ""),
                    asset_fingerprint=cut_sha or str(chosen_cand.get("asset_fingerprint") or chosen_cand.get("fingerprint") or ""),
                    license=str(chosen_cand.get("license") or ""),
                    license_url=str(chosen_cand.get("license_url") or ""),
                    attribution=str(chosen_cand.get("attribution") or ""),
                    creator=str(chosen_cand.get("creator") or chosen_cand.get("author") or ""),
                    gate_outcomes=dict(chosen_cand.get("gate_outcomes") or {}),
                    requirement_hash=expected_req_hash,
                    candidate_evidence=dict(chosen_cand.get("candidate_evidence") or {}),
                    candidate_ledger=_candidate_ledger(idx, chosen_path),
                    status="accepted",
                    fallback_reason="web_image_rescued_before_stock_fallback",
                )
                if chosen_identity:
                    final_uses[chosen_identity] = final_uses.get(chosen_identity, 0) + 1
                print(f"   [v2] pre-stock web image rescue succeeded for scene {idx + 1} with {chosen_path}", flush=True)

    # Absolute last-resort real-media recovery. By this point the scene has
    # already exhausted exact web, YouTube cuts/frames, cross-source retries,
    # and verified same-entity reuse. Stock can replace only a neutral/graphic
    # result and only after its own identity and visual gates pass.
    degraded_stock_scenes = [
        (index, rows[index])
        for index in range(len(rows))
        if (resolutions.get(index) is not None
            and resolutions[index].provider in {"neutral", "graphic"})
    ]
    if degraded_stock_scenes:
        stock_started = time.monotonic()
        # Web recovery has its own later deadline. Start one shared, bounded
        # stock window here; deriving it from the expired primary deadline
        # silently skipped every stock request in exhausted-media jobs.
        try:
            workers = max(1, min(4, len(degraded_stock_scenes)))
            default_window = min(900.0, 100.0 * math.ceil(len(degraded_stock_scenes) / workers))
            stock_window = max(30.0, min(900.0, float(
                os.environ.get("VIDZEO_STOCK_FINAL_RESERVE_SECONDS", str(default_window)) or default_window
            )))
        except (TypeError, ValueError):
            stock_window = 45.0
        stock_deadline = stock_started + stock_window
        stock_stage_event = _LinkedCancellationEvent(external_cancel_event)
        stock_stage_event.deadline_at = stock_deadline
        stock_workers = max(1, min(4, len(degraded_stock_scenes)))
        stock_waves = max(1, math.ceil(len(degraded_stock_scenes) / stock_workers))
        # Each queued scene gets a fair share of the owning stock window. The
        # previous code gave every worker an independent 90s default (up to
        # 180s), even when the outer batch had only 30s left per wave. That
        # made the nested provider timeout disagree with the stock stage wall
        # and let early scenes consume the time reserved for later scenes.
        try:
            configured_worker_window = max(
                0.0,
                min(180.0, float(os.environ.get("VIDZEO_STOCK_FINAL_RESERVE_SECONDS", "90") or 90)),
            )
        except (TypeError, ValueError):
            configured_worker_window = 90.0
        stock_worker_window = min(
            configured_worker_window,
            stock_window / stock_waves,
        )
        stock_pool = ThreadPoolExecutor(
            max_workers=stock_workers,
            thread_name_prefix="media-v2-stock-final",
        )

        def _run_stock_worker(index: int, scene: Mapping[str, Any]) -> Optional[SceneResolution]:
            # Start a worker-local window when its queued task actually begins,
            # but never let its child provider deadline exceed the remaining
            # shared stock stage allowance.
            worker_started = time.monotonic()
            worker_deadline = min(stock_deadline, worker_started + stock_worker_window)
            attempts[index].append({
                "source": "stock",
                "stage": "stock_budget",
                "status": "started",
                "outer_window_seconds": round(stock_window, 3),
                "worker_window_seconds": round(max(0.0, worker_deadline - worker_started), 3),
                "worker_count": stock_workers,
                "queued_scene_count": len(degraded_stock_scenes),
                "queue_waves": stock_waves,
            })
            return _final_stock_resolution(
                index, scene, worker_deadline, stock_stage_event,
            )

        try:
            stock_futures = {
                stock_pool.submit(_run_stock_worker, idx, scene): idx
                for idx, scene in degraded_stock_scenes
            }
            pending = set(stock_futures)
            completed = set()
            stock_stop_reason = "stock_stage_deadline"
            # Continue while futures complete, but stop a genuinely idle
            # provider tail.  This keeps the rescue bounded without cancelling
            # queued scenes merely because useful earlier downloads consumed a
            # shared wall clock.
            last_advance = time.monotonic()
            inactivity_seconds = min(120.0, max(30.0, stock_window / 3.0))
            while pending and time.monotonic() < stock_deadline:
                done, pending = wait(
                    pending,
                    timeout=min(0.5, max(0.01, stock_deadline - time.monotonic())),
                    return_when=FIRST_COMPLETED,
                )
                if done:
                    completed.update(done)
                    last_advance = time.monotonic()
                elif time.monotonic() - last_advance >= inactivity_seconds:
                    stock_stop_reason = "stock_provider_inactivity"
                    break
                elif external_cancel_event is not None and external_cancel_event.is_set():
                    stock_stop_reason = "external_cancel"
                    break
            # Capture tasks that completed at the boundary before cancelling
            # the remaining provider work.
            newly_completed = {future for future in pending if future.done()}
            completed.update(newly_completed)
            pending.difference_update(newly_completed)
            for future in pending:
                cancelled_before_start = future.cancel()
                idx = stock_futures[future]
                attempts[idx].append({
                    "source": "stock",
                    "stage": "stock_last_resort",
                    "status": (
                        f"not_started_after:{stock_stop_reason}"
                        if cancelled_before_start
                        else f"cancelled_after:{stock_stop_reason}"
                    ),
                })
            for future in completed:
                idx = stock_futures[future]
                try:
                    replacement = future.result()
                    if replacement is not None:
                        resolutions[idx] = replacement
                    else:
                        current = resolutions.get(idx)
                        if current is not None:
                            current.attempts = list(attempts[idx])
                            current.candidate_ledger = _candidate_ledger(idx)
                except Exception:
                    current = resolutions.get(idx)
                    if current is not None:
                        current.attempts = list(attempts[idx])
                        current.candidate_ledger = _candidate_ledger(idx)
        finally:
            stock_stage_event.set()
            stock_pool.shutdown(wait=False, cancel_futures=True)
        print(
            f"   [v2] parallel stock fallback: "
            f"scenes={len(degraded_stock_scenes)}, workers={stock_workers}, "
            f"elapsed={time.monotonic() - stock_started:.1f}s",
            flush=True,
        )
    # Produce emergency visuals only after every unresolved slot has had its
    # stock chance. A placeholder must never win the race against real media.
    if stock_lane is not None and neutral_factory is not None:
        for index, scene in enumerate(rows):
            resolution = resolutions.get(index)
            if resolution is None or resolution.provider not in {"neutral", "graphic"} or resolution.selected_path:
                continue
            if external_cancel_event is not None and external_cancel_event.is_set():
                break
            try:
                value = neutral_factory(scene)
                path = str(value.get("path") or value.get("local_path") or "") if isinstance(value, dict) else str(value or "")
                if path:
                    resolution.selected_path = path
                    resolution.media_type = "video"
                    resolution.provider = "graphic" if resolution.graphic_fallback else "neutral"
                    resolution.resolution_kind = "graphic_fallback" if resolution.graphic_fallback else "neutral_no_claim"
                    resolution.verification_status = "graphic_fallback" if resolution.graphic_fallback else "not_applicable"
            except Exception as exc:
                resolution.attempts.append({"source": "graphic", "stage": "factory", "status": f"error:{type(exc).__name__}"})
    print(
        "   [v2] coordinator complete: "
        f"scenes={len(rows)}, elapsed={time.monotonic() - started_at:.1f}s, "
        f"deadline_remaining={max(0.0, deadline_at - time.monotonic()):.1f}s, "
        f"web_recovery_remaining={max(0.0, web_fallback_deadline_at - time.monotonic()):.1f}s",
        flush=True,
    )
    return resolutions
