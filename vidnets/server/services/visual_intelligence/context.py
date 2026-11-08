"""Per-job visual decision context."""
from __future__ import annotations

import math
import os
import json
import hashlib
import threading
import uuid
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .cache import VisualDecisionCache, decision_cache_key, vision_cache_policy, valid_cached_decision
from .vision_client import (
    DEEP_QWEN_VISION_MODEL,
    DEFAULT_QWEN_VISION_MODEL,
    QwenVisionClient,
    VisionClientError,
)


class VisualBudgetExceeded(VisionClientError):
    pass


VISUAL_PASS = "pass"
VISUAL_HARD_FAIL = "hard_fail"
VISUAL_UNCERTAIN = "uncertain"
VISUAL_UNAVAILABLE = "unavailable"


def default_visual_call_cap(scene_count: int) -> int:
    """One verification per scene plus a bounded retry reserve."""
    return max(8, int(math.ceil(max(0, int(scene_count or 0)) * 1.25)))


def scene_requires_visual_verification(scene: Any) -> bool:
    """Return whether an individual scene must fail closed without Qwen."""
    if hasattr(scene, "to_dict"):
        try:
            scene = scene.to_dict()
        except Exception:
            pass
    row = scene if isinstance(scene, dict) else {}
    policy = row.get("source_policy") or {}
    requirement = row.get("visual_requirement")
    # The semantic requirement is the stronger contract.  Scene builders may
    # leave the legacy job-level flag at False for compatibility, but that
    # flag cannot downgrade a named entity/model/year requirement into a
    # metadata-only decision.
    if isinstance(requirement, Mapping) and bool(requirement.get("strict_identity")):
        return True
    if hasattr(policy, "visual_verification_required"):
        return bool(getattr(policy, "visual_verification_required"))
    if isinstance(policy, dict) and "visual_verification_required" in policy:
        return bool(policy.get("visual_verification_required"))
    # The adapter stamps this policy for jobs that opted into strict factual
    # retrieval.  Treat it as a semantic verification requirement even when
    # a legacy scene contract omitted ``strict_identity``.
    if isinstance(policy, Mapping) and bool(policy.get("strict_real_media")):
        return True
    if bool(row.get("visual_required") or row.get("named_factual") or row.get("strict_factual")):
        return True
    format_name = str(row.get("format") or row.get("format_hint") or "").strip().lower()
    return format_name in {"listicle", "comparison", "mixed_multi_subject", "multi_subject"}


def classify_visual_outcome(
    accepted: bool,
    vision_available: bool,
    reason: str = "",
    *,
    score: float = 0.0,
) -> str:
    """Normalize provider-specific verdicts into orchestration outcomes.

    Availability failures must not masquerade as relevance failures.  The
    resolver can retry an unavailable/uncertain decision with a different
    candidate while permanently blacklisting only a genuine hard rejection.
    """
    if not vision_available:
        return VISUAL_UNAVAILABLE
    if accepted:
        return VISUAL_PASS
    normalized = str(reason or "").strip().lower()
    hard_markers = (
        "replace", "reject", "fatal", "wrong", "mismatch", "unsafe",
        "adult", "suggestive", "synthetic", "watermark", "off-topic",
        "off_topic", "irrelevant",
    )
    if any(marker in normalized for marker in hard_markers):
        return VISUAL_HARD_FAIL
    if float(score or 0.0) <= 0.0 and normalized in {"", "missing_verdict"}:
        return VISUAL_UNAVAILABLE
    return VISUAL_UNCERTAIN


_GLOBAL_SEMAPHORE = threading.BoundedSemaphore(
    max(1, int(os.environ.get("VIDZEO_VIP_GLOBAL_CONCURRENCY", "20") or 20))
)


# One-time live vision health check. Account, provider and model changes
# re-probe. Runs on first real use (backend boots lazily, so the key may not
# exist at process start). A failed probe is authoritative for this process:
# callers must use metadata-only ranking and mark the result unverified until a
# later job/model change causes a fresh probe.
_PROBE_LOCK = threading.Lock()
_PROBE_DONE: Dict[str, Tuple[bool, str]] = {}
_PROBE_LAST_FAILURE_AT: Dict[str, float] = {}


def _probe_is_transient(detail: str) -> bool:
    """Identify transport/rate-limit failures that are safe to retry later."""
    value = str(detail or "").lower()
    return any(marker in value for marker in (
        "connectionerror", "connection aborted", "connection reset",
        "failed to resolve", "name or service not known", "dns",
        "timed out", "timeout", "max retries", "network",
        "temporarily", "provider_unavailable", "rate_limited",
        "http 429", "http 503", " 429", " 503",
    ))


def _maybe_probe_vision(raw_client) -> Tuple[bool, str]:
    if str(os.environ.get("VIDZEO_VISION_PROBE", "1")).strip().lower() not in {"1", "true", "yes", "on"}:
        return True, "probe_disabled"
    model = str(getattr(raw_client, "model", "") or "unknown")
    probe_key = hashlib.sha256(json.dumps({"model": model,
        "provider": getattr(raw_client, "provider", "openrouter"),
        "account": hashlib.sha256(str(getattr(raw_client, "api_key", "")).encode()).hexdigest()}, sort_keys=True).encode()).hexdigest()
    owns_probe = False
    now = time.monotonic()
    with _PROBE_LOCK:
        previous = _PROBE_DONE.get(probe_key)
        if previous is None:
            _PROBE_DONE[probe_key] = (False, "probing")  # reserve so only one thread probes
            owns_probe = True
        elif previous[1] != "probing":
            # DNS outages and provider throttles are transient.  Re-open the
            # probe after a short cooldown so a later scene can use vision in
            # the same job once the network recovers.  Auth/model/schema
            # failures remain sticky and fail closed for this process.
            try:
                retry_seconds = max(
                    5.0,
                    float(os.environ.get("VIDZEO_VISION_PROBE_RETRY_SECONDS", "20") or 20),
                )
            except (TypeError, ValueError):
                retry_seconds = 20.0
            last_failure = _PROBE_LAST_FAILURE_AT.get(probe_key, now)
            if not previous[0] and _probe_is_transient(previous[1]) and now - last_failure >= retry_seconds:
                _PROBE_DONE[probe_key] = (False, "probing")
                owns_probe = True
            else:
                return previous
    if not owns_probe:
        # Another scene worker is probing. Wait briefly for its result so the
        # first request cannot race ahead with a client whose health is unknown.
        deadline = time.monotonic() + 35.0
        while time.monotonic() < deadline:
            with _PROBE_LOCK:
                current = _PROBE_DONE.get(probe_key)
                if current and current[1] != "probing":
                    return current
            time.sleep(0.05)
        return False, "probe_in_progress_timeout"
    probe = getattr(raw_client, "probe", None)
    if not callable(probe):
        # Small test/dummy clients and compatible adapters may not expose the
        # optional live probe. They are still allowed to run; real QwenClient
        # always implements it and failures there remain authoritative.
        with _PROBE_LOCK:
            _PROBE_DONE[probe_key] = (True, "probe_unsupported")
        return True, "probe_unsupported"
    try:
        ok, detail = probe()
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}:{exc}"
    with _PROBE_LOCK:
        _PROBE_DONE[probe_key] = (bool(ok), str(detail or "unknown"))
        if ok:
            _PROBE_LAST_FAILURE_AT.pop(probe_key, None)
        else:
            _PROBE_LAST_FAILURE_AT[probe_key] = time.monotonic()
    if ok:
        print(f"[vision] startup probe OK - {detail}", flush=True)
    else:
        print(
            f"[vision] startup probe FAILED - {detail}. Vision ranking is "
            f"disabled for this job and will use metadata scoring; check the OpenRouter key/model "
            f"(set VIDZEO_VISION_PROBE=0 to silence this check).",
            flush=True,
        )
    return bool(ok), str(detail or "unknown")


class _ContextClient:
    def __init__(self, context: "VisualDecisionContext", client: QwenVisionClient):
        self._context = context
        self._client = client
        self.model = client.model
        self.api_key = client.api_key

    def _run(self, operation: str, paths: List[str], prompt: str, callback, cache_lookup=None):
        cacheable = True
        try:
            policy = vision_cache_policy(self._client)
            cache_key = decision_cache_key(
                operation=operation, model=self.model, prompt=prompt, paths=paths,
                account_scope=policy["account"], policy=policy,
            )
        except (OSError, ValueError, TypeError):
            cacheable = False
            cache_key = "uncached-" + uuid.uuid4().hex

        cached = self._context.cache.get(cache_key)
        if valid_cached_decision(operation, cached):
            self._context._record(operation, 0.0, 0.0, True, cached)
            from ..openrouter_usage import record_cache_hit
            record_cache_hit(self.model, "vision_context_" + operation, kind="vision")
            return cached

        if callable(cache_lookup):
            reused = cache_lookup()
            if valid_cached_decision(operation, reused):
                self._context._record(operation, 0., 0., True, reused)
                if cacheable: self._context.cache.put(cache_key, operation, self.model, reused)
                return reused

        with self._context._lock:
            inflight = self._context._inflight.get(cache_key)
            if inflight is None:
                inflight = threading.Event()
                self._context._inflight[cache_key] = inflight
                owns_request = True
            else:
                owns_request = False
        if not owns_request:
            # Match the owner's configured retry window. The old fixed 35s
            # wait caused duplicate OpenRouter calls when a legitimate vision
            # request was allowed to run longer, then both results competed
            # for the same scene.
            wait_timeout = max(
                35.0,
                float(self._context.call_timeout_seconds)
                * max(1, int(self._context.call_max_attempts))
                + 15.0,
            )
            inflight.wait(timeout=min(wait_timeout, max(0., self._context.deadline_at - time.monotonic())))
            cached = self._context.cache.get(cache_key)
            if valid_cached_decision(operation, cached):
                self._context._record(operation, 0.0, 0.0, True, cached)
                from ..openrouter_usage import record_cache_hit
                record_cache_hit(self.model, "vision_context_" + operation, kind="vision")
                return cached
            # Never recursively launch duplicates after a wait deadline or
            # replay a failed owner through every waiting worker. A fresh
            # explicit request can retry later; transient failures stay uncached.
            raise VisionClientError("Identical visual verification did not complete successfully within its budget")

        try:
            # Reserve only once the request owns an execution slot. This stops
            # queued work from starting after the job-level deadline.
            self._context._acquire_before_deadline(self._context._semaphore)
            global_acquired = False
            try:
                self._context._acquire_before_deadline(_GLOBAL_SEMAPHORE)
                global_acquired = True
                if callable(cache_lookup):
                    reused = cache_lookup()
                    if valid_cached_decision(operation, reused):
                        self._context._record(operation, 0., 0., True, reused)
                        if cacheable: self._context.cache.put(cache_key, operation, self.model, reused)
                        return reused
                self._context._reserve_call()
                started = time.perf_counter()
                result = callback()
            finally:
                if global_acquired:
                    _GLOBAL_SEMAPHORE.release()
                self._context._semaphore.release()
            latency_ms = (time.perf_counter() - started) * 1000.0
            cost = float(getattr(self._client, "last_cost", 0.0) or 0.0)
            inner_cache_hit = bool(getattr(getattr(self._client, "_telemetry", None), "last_cache_hit", False))
            if inner_cache_hit:
                # Another owner may complete after the preflight cache lookup.
                # Refund that logical reservation and record reuse only once;
                # the inner cache already writes the savings ledger event.
                with self._context._lock:
                    self._context.calls = max(0, self._context.calls - 1)
                cost = 0.
            self._context._record(operation, latency_ms, cost, inner_cache_hit, result)
            if cacheable and valid_cached_decision(operation, result):
                try:
                    unchanged_key = decision_cache_key(operation=operation, model=self.model, prompt=prompt, paths=paths,
                        account_scope=policy["account"], policy=policy)
                    if unchanged_key == cache_key:
                        self._context.cache.put(cache_key, operation, self.model, result)
                except (OSError, ValueError, TypeError):
                    pass
            return result
        finally:
            with self._context._lock:
                self._context._inflight.pop(cache_key, None)
                inflight.set()

    def _lookup_raw(self, method_name, *args):
        method = getattr(self._client, method_name, None)
        lookup = getattr(method, "_vision_cache_lookup", None)
        return lookup(self._client, *args) if callable(lookup) else None

    def analyze_image(self, image_path: str, user_prompt: str, system_prompt: str) -> dict:
        return self._run(
            "image",
            [image_path],
            json.dumps({"system_prompt": system_prompt, "user_prompt": user_prompt}, sort_keys=True, ensure_ascii=False),
            lambda: self._client.analyze_image(image_path, user_prompt, system_prompt),
            cache_lookup=lambda: self._lookup_raw("analyze_image", image_path, user_prompt, system_prompt),
        )

    def analyze_clip_frames(self, frame_paths: list, user_prompt: str, system_prompt: str) -> dict:
        return self._run(
            "clip",
            list(frame_paths),
            json.dumps({"system_prompt": system_prompt, "user_prompt": user_prompt}, sort_keys=True, ensure_ascii=False),
            lambda: self._client.analyze_clip_frames(frame_paths, user_prompt, system_prompt),
            cache_lookup=lambda: self._lookup_raw("analyze_clip_frames", frame_paths, user_prompt, system_prompt),
        )

    def compare_images(self, image_paths: list, user_prompt: str) -> dict:
        return self._run(
            "compare_images",
            list(image_paths[:4]),
            user_prompt,
            lambda: self._client.compare_images(image_paths[:4], user_prompt),
            cache_lookup=lambda: self._lookup_raw("compare_images", image_paths[:4], user_prompt),
        )


@dataclass
class VisualDecisionContext:
    enabled: bool
    api_key: str
    model: str
    scene_count: int
    max_calls: int
    budget_usd: float
    deadline_at: float
    visual_required: bool = False
    fail_closed: bool = False
    max_concurrent: int = 4
    fallback_model: str = ""
    fallback_models: tuple[str, ...] = ()
    call_timeout_seconds: float = 20.0
    call_max_attempts: int = 2
    fast_mode: bool = False
    cache: VisualDecisionCache = field(default_factory=VisualDecisionCache)
    calls: int = 0
    cache_hits: int = 0
    cost_usd: float = 0.0
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    degraded_reason: str = ""

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._semaphore = threading.BoundedSemaphore(max(1, self.max_concurrent))
        self._inflight: Dict[str, threading.Event] = {}
        self._client: Optional[_ContextClient] = None
        self._fallback_client: Optional[_ContextClient] = None

    def refresh_deadline(
        self,
        deadline_seconds: Optional[float] = None,
        deadline_at: Optional[float] = None,
    ) -> None:
        """Extend or refresh the context deadline and clear deadline-based degradation."""
        with self._lock:
            now = time.monotonic()
            if deadline_at is not None:
                new_deadline = float(deadline_at)
            elif deadline_seconds is not None:
                if math.isinf(float(deadline_seconds)):
                    new_deadline = float("inf")
                else:
                    new_deadline = now + max(1.0, float(deadline_seconds))
            else:
                new_deadline = now + max(300.0, float(self.scene_count or 1) * 25.0)

            if math.isinf(new_deadline) or new_deadline > self.deadline_at or self.deadline_at <= now:
                self.deadline_at = new_deadline
                if self.degraded_reason == "visual_deadline_exceeded":
                    self.degraded_reason = ""

    @classmethod
    def from_config(cls, config: Dict[str, Any], scene_count: int) -> "VisualDecisionContext":
        from ..provider_selection import normalize_provider_config
        config = normalize_provider_config(config)
        key = str(
            config.get("openrouter_api_key")
            or config.get("openrouterApiKey")
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("VIDZEO_OPENROUTER_API_KEY")
            or ""
        ).strip()
        raw_enabled = config.get("visual_understanding")
        if raw_enabled is None:
            enabled = bool(key) or _as_bool(os.environ.get("VIDZEO_VISUAL_UNDERSTANDING", "1"))
        else:
            enabled = _as_bool(raw_enabled)
        model = str(
            config.get("qwen_vision_model")
            or os.environ.get("VIDZEO_QWEN_VISION_MODEL")
            or DEFAULT_QWEN_VISION_MODEL
        ).strip() or DEFAULT_QWEN_VISION_MODEL
        count = max(1, int(scene_count or 1))
        required_raw = config.get("visual_required")
        if required_raw is None:
            required_raw = os.environ.get("VIDZEO_VISUAL_REQUIRED", "0")
        visual_required = _as_bool(required_raw)
        fail_closed = _as_bool(config.get("visual_fail_closed")) or str(
            config.get("pipeline_v2_mode") or os.environ.get("VIDZEO_PIPELINE_V2_MODE") or ""
        ).strip().lower() in {"shadow", "on", "enabled", "canary", "1", "true", "yes", "v2"}
        # Retrieval branches and the optional final gate consume one job-scoped
        # budget. The proportional value preserves small strict-job behavior;
        # the shared ceiling below bounds production fan-out for long jobs.
        proportional_calls = default_visual_call_cap(count)
        requested_max_calls = int(
            config.get("visual_max_calls")
            or os.environ.get("VIDZEO_VISUAL_MAX_CALLS")
            or proportional_calls
        )
        # All visual consumers in a job share this hard ceiling. Internal
        # branch code can request headroom but cannot silently restore the old
        # one-call-per-scene fan-out.
        v2_budget_policy = (
            str(config.get("visual_budget_policy") or "").strip().lower() == "per_scene"
            or str(config.get("pipeline_v2_mode") or os.environ.get("VIDZEO_PIPELINE_V2_MODE") or "").strip().lower() in {"shadow", "on", "enabled", "canary", "1", "true", "yes", "v2"}
            or bool(config.get("storyboard_v2"))
        )
        configured_shared_cap = config.get("visual_shared_max_calls")
        if configured_shared_cap is not None:
            shared_max_calls = int(configured_shared_cap)
        elif v2_budget_policy:
            # V2 uses its scene-scaled budget. The legacy environment cap was
            # sized for short jobs and must not silently leave most scenes in
            # a long documentary without visual review. A job can still set an
            # explicit visual_shared_max_calls when it needs a hard ceiling.
            shared_max_calls = proportional_calls
        elif os.environ.get("VIDZEO_VISUAL_SHARED_MAX_CALLS") is not None:
            shared_max_calls = int(os.environ["VIDZEO_VISUAL_SHARED_MAX_CALLS"])
        else:
            shared_max_calls = max(6, proportional_calls)
        max_calls = min(max(1, requested_max_calls), max(1, shared_max_calls))
        configured_fallbacks = config.get("qwen_vision_fallback_models")
        if configured_fallbacks is None:
            configured_fallbacks = os.environ.get("VIDZEO_QWEN_VISION_FALLBACK_MODELS", "")
        if isinstance(configured_fallbacks, str):
            configured_fallbacks = [item.strip() for item in configured_fallbacks.replace(";", ",").split(",") if item.strip()]
        fallback_candidates = []
        for candidate in (configured_fallbacks or []):
            candidate = str(candidate or "").strip()
            if candidate and candidate not in fallback_candidates:
                fallback_candidates.append(candidate)
        legacy_fallback = str(
            config.get("qwen_vision_fallback_model")
            or os.environ.get("VIDZEO_QWEN_VISION_FALLBACK_MODEL")
            or DEEP_QWEN_VISION_MODEL
        ).strip()
        if legacy_fallback and legacy_fallback not in fallback_candidates:
            fallback_candidates.insert(0, legacy_fallback)
        fallback_model = next((candidate for candidate in fallback_candidates if candidate != model), model)
        budget = float(
            config.get("visual_budget_usd")
            or os.environ.get("VIDZEO_VISUAL_BUDGET_USD")
            or max(0.25, count * 0.01)
        )
        # V2 creates the verifier before parallel provider retrieval begins.
        # A fixed 75-second absolute deadline therefore expired while a normal
        # authenticated YouTube batch was still downloading, even though the
        # final Qwen check had not started. Scale the default with scene count;
        # explicit config/environment limits remain authoritative.
        web_wait_complete = _as_bool(
            config.get("web_wait_until_complete")
            or os.environ.get("VIDZEO_WEB_WAIT_UNTIL_COMPLETE")
        )
        delivery_budget = None
        try:
            from ..delivery_budget import get_delivery_budget
            delivery_budget = get_delivery_budget(config)
        except Exception:
            delivery_budget = None

        budget_allowance = None
        if delivery_budget is not None:
            try:
                reserve_after = float(
                    config.get("_render_reserve_seconds")
                    or delivery_budget.render_reserve_seconds
                )
            except (TypeError, ValueError):
                reserve_after = float(delivery_budget.render_reserve_seconds)
            budget_allowance = delivery_budget.allowance(
                None,
                reserve_after_seconds=reserve_after,
            )

        if web_wait_complete:
            default_deadline_seconds = float("inf")
        elif v2_budget_policy:
            scaled_proportional = max(450.0, min(1200.0, count * 20.0))
            if budget_allowance is not None and budget_allowance > 0.0:
                default_deadline_seconds = max(scaled_proportional, budget_allowance)
            else:
                default_deadline_seconds = scaled_proportional
        else:
            default_deadline_seconds = 75.0

        raw_req_deadline = (
            config.get("visual_deadline_seconds")
            or os.environ.get("VIDZEO_VISUAL_DEADLINE_SECONDS")
        )
        requested_deadline_seconds = (
            float(raw_req_deadline)
            if raw_req_deadline is not None
            else default_deadline_seconds
        )
        raw_shared_deadline = (
            config.get("visual_shared_deadline_seconds")
            or os.environ.get("VIDZEO_VISUAL_SHARED_DEADLINE_SECONDS")
        )
        shared_deadline_seconds = (
            float(raw_shared_deadline)
            if raw_shared_deadline is not None
            else default_deadline_seconds
        )
        if math.isinf(requested_deadline_seconds) or math.isinf(shared_deadline_seconds):
            if math.isinf(requested_deadline_seconds) and math.isinf(shared_deadline_seconds):
                deadline_seconds = float("inf")
            elif math.isinf(requested_deadline_seconds):
                deadline_seconds = max(1.0, shared_deadline_seconds)
            else:
                deadline_seconds = max(1.0, requested_deadline_seconds)
        else:
            deadline_seconds = min(
                max(1.0, requested_deadline_seconds),
                max(1.0, shared_deadline_seconds),
            )
        max_concurrent = max(1, min(6, int(config.get("visual_concurrency") or 4)))
        if v2_budget_policy:
            call_timeout_seconds = max(
                10.0,
                min(120.0, float(
                    config.get("visual_call_timeout_seconds")
                    or os.environ.get("VIDZEO_VISUAL_CALL_TIMEOUT_SECONDS")
                    or 30.0
                )),
            )
        else:
            call_timeout_seconds = max(
                15.0,
                min(300.0, float(
                    config.get("visual_call_timeout_seconds")
                    or os.environ.get("VIDZEO_VISUAL_CALL_TIMEOUT_SECONDS")
                    or 15.0
                )),
            )
        call_max_attempts = max(
            2,
            min(
                3,
                int(
                    config.get("visual_call_max_attempts")
                    or os.environ.get("VIDZEO_VISUAL_CALL_MAX_ATTEMPTS")
                    or 2
                ),
            ),
        )
        if str(config.get('llm_provider') or '') == 'external_agent' or str(key).startswith('external-agent:'):
            max_concurrent = min(4, max_concurrent)
            call_timeout_seconds = 300.0
            call_max_attempts = 2
        fast_mode = _as_bool(config.get("visual_fast_mode"))
        context = cls(
            enabled=enabled,
            api_key=key,
            model=model,
            scene_count=count,
            max_calls=max(1, max_calls),
            budget_usd=max(0.0, budget),
            deadline_at=(float("inf") if math.isinf(deadline_seconds) else time.monotonic() + deadline_seconds),
            visual_required=visual_required,
            fail_closed=fail_closed,
            max_concurrent=max_concurrent,
            fallback_model=fallback_model,
            fallback_models=tuple(fallback_candidates),
            call_timeout_seconds=call_timeout_seconds,
            call_max_attempts=call_max_attempts,
            fast_mode=fast_mode,
        )
        if enabled and not key:
            context.degraded_reason = "missing_openrouter_key"
        return context

    def get_client(self) -> Optional[_ContextClient]:
        if not self.enabled or not self.api_key or self.degraded_reason:
            return None
        with self._lock:
            if self._client is None:
                # Client construction is followed by a live provider probe.
                # Reject exhausted jobs here so an already-expired visual
                # stage cannot make an unaccounted paid/network request before
                # the normal per-operation reservation guard gets a chance to
                # run.
                if not math.isinf(self.deadline_at) and time.monotonic() >= self.deadline_at:
                    self.degraded_reason = "visual_deadline_exceeded"
                    return None
                if self.calls >= self.max_calls:
                    self.degraded_reason = "visual_call_budget_exceeded"
                    return None
                if self.budget_usd and self.cost_usd >= self.budget_usd:
                    self.degraded_reason = "visual_cost_budget_exceeded"
                    return None
                raw = QwenVisionClient(
                    api_key=self.api_key,
                    model=self.model,
                    fallback_models=getattr(self, "fallback_models", None),
                    max_attempts=self.call_max_attempts,
                    timeout_seconds=self.call_timeout_seconds,
                    fast_mode=self.fast_mode,
                )
                self._client = _ContextClient(self, raw)
            client = self._client
        # Confirm the model+key actually work, once per process (outside the
        # lock so concurrent scene workers don't serialize on the ping).
        if getattr(client._client, 'provider', '') == 'external_agent':
            # The first real candidate validates the connected host; a gray
            # synthetic probe consumes an inbox turn without inspecting media.
            return client
        probe_ok, probe_detail = _maybe_probe_vision(client._client)
        if not probe_ok:
            detail_lower = str(probe_detail or "").lower()
            if _probe_is_transient(detail_lower):
                # Keep the context retryable.  The probe cache applies a
                # cooldown, and clearing the client forces the next caller to
                # construct a fresh transport instead of reusing a dead one.
                with self._lock:
                    if self._client is client:
                        self._client = None
                    self.degraded_reason = ""
                return None
            response_failure = any(token in detail_lower for token in (
                "empty content",
                "non-json",
                "malformed",
                "unexpected_response",
                "unexpected response",
                "response_type",
                "not available",
                "model not",
                "rate_limited",
                "provider_unavailable",
                "credits",
                "402",
                "403",
                "404",
            ))
            fallback_model = str(self.fallback_model or "").strip()
            if (
                response_failure
                and fallback_model
                and fallback_model != self.model
            ):
                fallback_raw = QwenVisionClient(
                    api_key=self.api_key,
                    model=fallback_model,
                    fallback_models=tuple(
                        candidate for candidate in self.fallback_models
                        if candidate != fallback_model
                    ),
                    max_attempts=1,
                    timeout_seconds=self.call_timeout_seconds,
                    fast_mode=self.fast_mode,
                )
                fallback_ok, fallback_detail = _maybe_probe_vision(fallback_raw)
                if fallback_ok:
                    with self._lock:
                        self.model = fallback_model
                        self._client = _ContextClient(self, fallback_raw)
                    print(
                        f"[vision] fallback model enabled - {fallback_model}",
                        flush=True,
                    )
                    return self._client
                probe_detail = (
                    f"primary={probe_detail}; fallback={fallback_detail}"
                )
            # Do not expose an apparently usable client after an auth/model/
            # response failure. Resolver callers use this state to retain the
            # metadata gate and mark winners as unverified.
            with self._lock:
                self.degraded_reason = f"vision_probe_failed:{probe_detail}"
            return None
        # ``probe`` may have promoted an ordered fallback model after the
        # primary route returned 402/404. Keep cache keys and telemetry aligned
        # with the model that will handle subsequent scene decisions.
        resolved_model = str(getattr(client._client, "last_model", "") or "").strip()
        with self._lock:
            if self._client is None and not self.degraded_reason:
                self._client = client
            if resolved_model and resolved_model != self.model:
                self.model = resolved_model
                client.model = resolved_model
        return client

    def get_fallback_client(self) -> Optional[_ContextClient]:
        """Return the configured fallback through the same job guardrails.

        The returned client is another ``_ContextClient`` bound to this exact
        context. Consequently a schema-repair attempt reserves its own call,
        shares the job/global concurrency semaphores and deadline, records its
        cost, and uses a model-specific cache key. It never bypasses budget
        accounting with a raw OpenRouter call.
        """
        if not self.enabled or not self.api_key or self.degraded_reason:
            return None
        model = str(self.fallback_model or DEEP_QWEN_VISION_MODEL).strip()
        if not model or model == str(self.model or "").strip():
            return None
        if self._fallback_client is not None:
            return self._fallback_client
        with self._lock:
            if self._fallback_client is None:
                raw = QwenVisionClient(
                    api_key=self.api_key,
                    model=model,
                    max_attempts=self.call_max_attempts,
                    timeout_seconds=self.call_timeout_seconds,
                    fast_mode=self.fast_mode,
                )
                self._fallback_client = _ContextClient(self, raw)
        return self._fallback_client

    def _reserve_call(self) -> None:
        with self._lock:
            if not math.isinf(self.deadline_at) and time.monotonic() >= self.deadline_at:
                self.degraded_reason = "visual_deadline_exceeded"
                raise VisualBudgetExceeded(self.degraded_reason)
            if self.calls >= self.max_calls:
                self.degraded_reason = "visual_call_budget_exceeded"
                raise VisualBudgetExceeded(self.degraded_reason)
            if self.budget_usd and self.cost_usd >= self.budget_usd:
                self.degraded_reason = "visual_cost_budget_exceeded"
                raise VisualBudgetExceeded(self.degraded_reason)
            self.calls += 1

    def _acquire_before_deadline(self, semaphore: threading.BoundedSemaphore) -> None:
        """Acquire an execution slot without waiting past the shared wall."""
        if math.isinf(self.deadline_at):
            semaphore.acquire()
            return
        remaining = max(0.0, self.deadline_at - time.monotonic())
        if math.isinf(remaining):
            semaphore.acquire()
            return
        if remaining <= 0.0 or not semaphore.acquire(timeout=remaining):
            with self._lock:
                self.degraded_reason = "visual_deadline_exceeded"
            raise VisualBudgetExceeded(self.degraded_reason)

    def _record(
        self,
        operation: str,
        latency_ms: float,
        cost_usd: float,
        cache_hit: bool,
        result: Any,
    ) -> None:
        with self._lock:
            if cache_hit:
                self.cache_hits += 1
            self.cost_usd += max(0.0, float(cost_usd or 0.0))
            decision = {
                "operation": operation,
                "latency_ms": round(latency_ms, 1),
                "cost_usd": round(max(0.0, float(cost_usd or 0.0)), 8),
                "cache_hit": cache_hit,
                "summary": str(result)[:240],
            }
            if isinstance(result, Mapping):
                decision["verdict"] = {
                    key: result.get(key)
                    for key in (
                        "accepted", "status", "final_score", "reason",
                        "verification_attempt", "verification_model",
                        "schema_valid", "schema_errors", "primary_schema_errors",
                        "entity_conflict", "fatal_mismatch",
                        "watermark", "unsafe_content", "suggestive_content",
                        "visible_overlays", "subject_box", "safe_text_regions",
                        "identity_score", "action_score", "context_score", "quality",
                    )
                    if key in result
                }
            self.decisions.append(decision)

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            completed_checks = sum(
                1 for decision in self.decisions
                if not decision.get("cache_hit")
                and decision.get("operation") in {"image", "clip", "compare"}
            )
            budget_exhausted = self.degraded_reason == "visual_call_budget_exceeded"
            return {
                "enabled": self.enabled,
                "visual_required": self.visual_required,
                "fail_closed": self.fail_closed,
                # A completed budget is not the same as an unavailable model:
                # the job may have already received valid checks for all of
                # its finalist candidates. Keep this distinction visible in
                # the exported manifest while retaining fail-closed ranking
                # for any work that arrives after the cap.
                "vision_available": bool(
                    self.enabled and self.api_key and (
                        not self.degraded_reason
                        or (budget_exhausted and completed_checks > 0)
                    )
                ),
                "vision_checks_completed": completed_checks,
                "vision_budget_exhausted": budget_exhausted,
                "model": self.model if self.enabled else "",
                "calls": self.calls,
                "cache_hits": self.cache_hits,
                "cost_usd": round(self.cost_usd, 8),
                "max_calls": self.max_calls,
                "budget_usd": round(self.budget_usd, 6),
                "deadline_remaining_seconds": round(
                    max(0.0, self.deadline_at - time.monotonic()), 1
                ),
                "call_timeout_seconds": round(self.call_timeout_seconds, 1),
                "call_max_attempts": self.call_max_attempts,
                "fast_mode": self.fast_mode,
                "degraded_reason": self.degraded_reason,
                "decisions": list(self.decisions),
            }

    def is_usable(self) -> bool:
        """Return whether a live-probed client is available for ranking.

        This intentionally performs the one-time probe. Callers should use
        this instead of checking only ``enabled``: a configured key can still
        point at an invalid model or return malformed/empty content.
        """
        return self.get_client() is not None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
