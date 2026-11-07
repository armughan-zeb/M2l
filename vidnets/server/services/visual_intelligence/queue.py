"""VisualDecisionQueue — centralized concurrency controller for Qwen Vision.

All Qwen Vision API calls across the codebase (safety override, web image
scoring, clip quality gate, frame rescue, orchestrator) are funneled through
a single shared semaphore. This prevents thread-pool callers from
overwhelming the OpenRouter rate limit and consolidates the 4+ separate
client singletons into one shared instance.

Usage::

    from services.visual_intelligence.queue import get_visual_queue
    queue = get_visual_queue()
    client = queue.get_client()      # semaphored QwenVisionClient proxy
    if client is not None:
        result = client.analyze_image(...)   # goes through semaphore
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Optional

from .vision_client import QwenVisionClient, VisionClientError


class _SemaphoredClient:
    """Thin proxy that wraps QwenVisionClient with a shared semaphore."""

    def __init__(self, client: QwenVisionClient, semaphore: threading.Semaphore):
        self._client = client
        self._semaphore = semaphore

    # ---- proxied public API ------------------------------------------------

    def analyze_image(self, image_path: str, user_prompt: str, system_prompt: str) -> dict:
        with self._semaphore:
            return self._client.analyze_image(image_path, user_prompt, system_prompt)

    def analyze_clip_frames(self, frame_paths: list, user_prompt: str, system_prompt: str) -> dict:
        with self._semaphore:
            return self._client.analyze_clip_frames(frame_paths, user_prompt, system_prompt)

    def _post(
        self,
        content: list,
        *,
        system: str = "",
        json_mode: bool = False,
        max_tokens: int = 900,
    ) -> str:
        with self._semaphore:
            return self._client._post(
                content, system=system, json_mode=json_mode, max_tokens=max_tokens,
            )

    def _parse_json(self, raw: str) -> Any:
        return self._client._parse_json(raw)

    # ---- passthrough properties --------------------------------------------

    @property
    def api_key(self) -> str:
        return self._client.api_key

    @property
    def model(self) -> str:
        return self._client.model

    @property
    def max_attempts(self) -> int:
        return self._client.max_attempts

    @property
    def timeout_seconds(self) -> float:
        return self._client.timeout_seconds

    def is_available(self) -> bool:
        return self._client.is_available()


class VisualDecisionQueue:
    """Centralized concurrency controller for all Qwen Vision operations.

    Provides a single shared ``QwenVisionClient`` proxy that serializes all
    API calls through a semaphore (default: 3 concurrent). Eliminates the 4+
    separate client singletons scattered across the codebase.

    Also tracks per-decision telemetry for Phase 4-5 reporting.
    """

    def __init__(self, max_concurrent: Optional[int] = None, timeout: float = 120.0):
        if max_concurrent is None:
            max_concurrent = int(os.environ.get("VIDZEO_VISION_MAX_CONCURRENT", "6") or 6)
        self._max_concurrent = max(1, min(12, int(max_concurrent)))
        self._timeout = timeout
        self._semaphore = threading.Semaphore(self._max_concurrent)
        self._raw_client: Optional[QwenVisionClient] = None
        self._proxied_client: Optional[_SemaphoredClient] = None
        self._decisions: List[Dict[str, Any]] = []
        self._decisions_lock = threading.Lock()

    # ---- client management -------------------------------------------------

    def _ensure_raw_client(self) -> Optional[QwenVisionClient]:
        if self._raw_client is not None:
            return self._raw_client
        api_key = (
            os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("VIDZEO_OPENROUTER_API_KEY")
            or ""
        ).strip()
        from ..job_context import current_job
        from ..provider_selection import ACCOUNT_PREFIX
        context = current_job()
        if context.get('llm_provider') == 'external_agent':
            from ..agent_bridge import AGENT_HANDLE
            api_key = AGENT_HANDLE + str(context.get('agent_owner_id') or '')
        elif context.get('llm_provider') == 'chatgpt':
            api_key = ACCOUNT_PREFIX + str(context.get('chatgpt_account_id') or '')
        elif context.get('llm_key'):
            api_key = context['llm_key']
        model = (
            os.environ.get("VIDZEO_QWEN_VISION_MODEL")
            or "qwen/qwen3.5-flash-02-23"
        ).strip() or "qwen/qwen3.5-flash-02-23"
        if not api_key:
            return None
        try:
            from .vision_client import _default_attempts, _default_timeout
            self._raw_client = QwenVisionClient(
                api_key=api_key,
                model=model,
                max_attempts=max(1, min(3, int(os.environ.get("VIDZEO_QWEN_VISION_MAX_ATTEMPTS", "3") or 3))),
                timeout_seconds=max(90.0, min(300.0, float(os.environ.get("VIDZEO_QWEN_VISION_TIMEOUT_SECONDS") or self._timeout or _default_timeout()))),
            )
            return self._raw_client
        except Exception:
            return None

    def get_client(self) -> Optional[_SemaphoredClient]:
        """Return a semaphore-wrapped ``QwenVisionClient`` proxy.

        All API calls through this proxy are serialized by the shared
        semaphore, preventing rate-limit overload when multiple thread
        pools (web resolver, clip gate, orchestrator) call simultaneously.

        Returns ``None`` when no OpenRouter API key is configured.
        """
        if self._proxied_client is not None:
            return self._proxied_client
        raw = self._ensure_raw_client()
        if raw is None:
            return None
        self._proxied_client = _SemaphoredClient(raw, self._semaphore)
        return self._proxied_client

    # ---- telemetry ---------------------------------------------------------

    def log_decision(
        self,
        operation: str,
        scene_index: int,
        input_path: str,
        result: Any,
        latency_ms: float,
    ) -> None:
        """Record a single Qwen Vision decision for telemetry."""
        with self._decisions_lock:
            self._decisions.append({
                "ts": time.time(),
                "op": operation,
                "scene": scene_index,
                "path": str(input_path)[:200] if input_path else "",
                "summary": str(result)[:300] if result else "",
                "ms": round(latency_ms, 1),
            })

    def get_decisions(self) -> List[Dict[str, Any]]:
        """Return all recorded decisions (for manifest export)."""
        with self._decisions_lock:
            return list(self._decisions)

    def reset_decisions(self) -> None:
        """Clear the decision log (call at the start of each job)."""
        with self._decisions_lock:
            self._decisions.clear()

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_VISUAL_QUEUE: Optional[VisualDecisionQueue] = None
_QUEUE_INIT_LOCK = threading.Lock()
_PROJECT_QUEUES = {}


def get_visual_queue() -> VisualDecisionQueue:
    """Return (and lazily create) the global ``VisualDecisionQueue``.

    Thread-safe. The singleton is created on first access using env vars:
      - ``VIDZEO_VIP_MAX_CONCURRENT`` — max parallel API calls (default: 3)
      - ``VIDZEO_VIP_TIMEOUT``        — per-call timeout in seconds (default: 30)
    """
    global _VISUAL_QUEUE
    from ..job_context import current_job
    context = current_job()
    identity = context.get('attempt_id') or context.get('job_id')
    if identity:
        # A cached client from another account/provider must never be inherited
        # by the next queued film. Existing workers retain their own queue.
        with _QUEUE_INIT_LOCK:
            if identity not in _PROJECT_QUEUES:
                _PROJECT_QUEUES[identity] = VisualDecisionQueue(
                    max_concurrent=int(os.environ.get('VIDZEO_VIP_MAX_CONCURRENT', '3') or 3),
                    timeout=float(os.environ.get('VIDZEO_VIP_TIMEOUT', '30') or 30))
                while len(_PROJECT_QUEUES) > 16:
                    _PROJECT_QUEUES.pop(next(iter(_PROJECT_QUEUES)))
            return _PROJECT_QUEUES[identity]
    if _VISUAL_QUEUE is not None:
        return _VISUAL_QUEUE
    with _QUEUE_INIT_LOCK:
        if _VISUAL_QUEUE is not None:
            return _VISUAL_QUEUE
        max_concurrent = int(os.environ.get("VIDZEO_VIP_MAX_CONCURRENT", "3") or 3)
        timeout = float(os.environ.get("VIDZEO_VIP_TIMEOUT", "30") or 30)
        _VISUAL_QUEUE = VisualDecisionQueue(max_concurrent=max_concurrent, timeout=timeout)
        return _VISUAL_QUEUE
