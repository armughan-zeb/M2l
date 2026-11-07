"""Persistent, content-addressed cache for visual decisions."""
from __future__ import annotations

import hashlib
import copy
import functools
import inspect
import math
import weakref
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


SCHEMA_VERSION = "visual-decision-v3-account-policy"
_MAX_AGE_SECONDS = 7 * 86400
_REQUEST_LOCKS = weakref.WeakValueDictionary()
_REQUEST_GUARD = threading.Lock()
_INIT_LOCK = threading.Lock()
_INITIALIZED: set[str] = set()


class _VisionFlight:
    """Share one owner's exception with its existing waiters, never later calls."""
    def __init__(self):
        self.lock = threading.Lock()
        self.generation = 0
        self.failure = None


def _default_path() -> Path:
    root = Path(os.environ.get("VIDZEO_ROOT") or Path(__file__).resolve().parents[3])
    path = root / "temp" / "visual_intelligence" / "visual_cache.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path



def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decision_cache_key(
    *,
    operation: str,
    model: str,
    prompt: str,
    paths: Iterable[str],
    schema_version: str = SCHEMA_VERSION,
    account_scope: str = "",
    policy: Optional[dict] = None,
) -> str:
    digest = hashlib.sha256()
    for value in (schema_version, operation, model, prompt, account_scope, json.dumps(policy or {}, sort_keys=True, default=str)):
        digest.update(str(value or "").encode("utf-8", "replace"))
        digest.update(b"\0")
    for path in paths:
        try:
            digest.update(file_sha256(path).encode("ascii"))
        except Exception:
            # A missing file cannot bind a cached approval to actual pixels.
            raise ValueError("Visual cache requires readable source content")
        digest.update(b"\0")
    return digest.hexdigest()


class VisualDecisionCache:
    def __init__(self, path: Optional[str] = None):
        self.path = str(Path(path) if path else _default_path())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=2)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _ensure_schema(self) -> None:
        if self.path in _INITIALIZED and Path(self.path).is_file():
            return
        with _INIT_LOCK:
            if self.path in _INITIALIZED and Path(self.path).is_file():
                return
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS visual_decisions (
                        cache_key TEXT PRIMARY KEY,
                        operation TEXT NOT NULL,
                        model TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        last_used_at REAL NOT NULL
                    )
                    """
                )
            _INITIALIZED.add(self.path)

    def get(self, cache_key: str) -> Optional[Dict[str, Any]]:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload_json, created_at FROM visual_decisions WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone()
                if not row or not 0 <= time.time() - float(row[1]) <= _MAX_AGE_SECONDS:
                    return None
                connection.execute(
                    "UPDATE visual_decisions SET last_used_at = ? WHERE cache_key = ?",
                    (time.time(), cache_key),
                )
            value = json.loads(row[0])
            return value if isinstance(value, dict) else None
        except Exception:
            return None

    def put(self, cache_key: str, operation: str, model: str, payload: Dict[str, Any]) -> None:
        try:
            now = time.time()
            encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO visual_decisions
                        (cache_key, operation, model, payload_json, created_at, last_used_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        payload_json = excluded.payload_json,
                        created_at = excluded.created_at,
                        last_used_at = excluded.last_used_at
                    """,
                    (cache_key, operation, model, encoded, now, now),
                )
        except Exception:
            pass



def vision_cache_policy(client):
    """Exact account and effective model/probe/prompt policy, never raw secrets."""
    from ..job_context import current_job
    context = current_job()
    secret = str(getattr(client, "api_key", ""))
    return {"account": hashlib.sha256(secret.encode()).hexdigest(),
            "provider": getattr(client, "provider", "openrouter"),
            "model": getattr(client, "model", ""),
            "fallback_models": list(getattr(client, "fallback_models", ()) or ()),
            "cost_profile": context.get("cost_profile"),
            "fast_mode": bool(getattr(client, "fast_mode", False)),
            "thinking": os.environ.get("VIDZEO_QWEN_VISION_THINKING", "0"),
            "source_contract": file_sha256(str(Path(__file__).with_name("vision_client.py"))),
            "cache_contract": file_sha256(__file__),
            "endpoint": getattr(inspect.getmodule(type(client)), "OPENROUTER_API_URL", ""),
            "probe_settings": {key: os.environ.get(key, "") for key in (
                "VIDZEO_VISION_IMAGE_PROBE_EDGE", "VIDZEO_VISION_FRAME_PROBE_EDGE",
                "VIDZEO_QWEN_VISION_MAX_IMAGE_BYTES", "VIDZEO_QWEN_VISION_MAX_CLIP_PAYLOAD_BYTES")}}


def valid_cached_decision(operation, result):
    """Only successful, complete verdicts; legitimate rejection is reusable."""
    if not isinstance(result, dict) or not result or result.get("error") or result.get("_schema_errors") or result.get("status") in ("unavailable", "error", "timeout"):
        return False
    if operation in {"image", "clip", "analyze_image", "analyze_clip_frames"}:
        if "_schema_present" in result:
            required = {"candidate_id", "entity_conflict", "identity_score", "action_score", "context_score", "quality", "visible_overlays", "subject_box", "safe_text_regions"}
            return (required.issubset(set(result.get("_schema_present") or []))
                    and all(isinstance(result.get(key), (int, float)) and not isinstance(result.get(key), bool)
                            and math.isfinite(result[key]) and 0 <= result[key] <= 100
                            for key in ("identity_score", "action_score", "context_score", "quality"))
                    and isinstance(result.get("entity_conflict"), bool)
                    and isinstance(result.get("visible_overlays"), list)
                    and isinstance(result.get("subject_box"), dict)
                    and isinstance(result.get("safe_text_regions"), list))
        # Legacy/custom verifiers remain supported, but empty/default dicts and
        # unavailable placeholders are not useful successful cache entries.
        return bool(result.get("reason") and result.get("scene_description")) and isinstance(result.get("quality"), (int, float))
    if operation == "review_editorial_frames":
        return (result.get("status") in ("pass", "needs_revision")
                and isinstance(result.get("confidence"), (int, float)) and not isinstance(result.get("confidence"), bool)
                and math.isfinite(result["confidence"]) and 0 <= result["confidence"] <= 100
                and isinstance(result.get("issues"), list)
                and result.get("repair_kind") in ("none", "layout", "design", "media")
                and (result.get("patch") is None or isinstance(result.get("patch"), dict)))
    if operation in {"compare_images", "compare"}:
        required = {"selected_index", "acceptable", "reason", "confidence"}
        present = result.get("_schema_present")
        return (isinstance(present, list) and all(isinstance(item, str) for item in present)
                and required.issubset(set(present))
                and isinstance(result.get("selected_index"), int) and not isinstance(result.get("selected_index"), bool)
                and isinstance(result.get("_candidate_count"), int)
                and 0 <= result["selected_index"] < result["_candidate_count"]
                and isinstance(result.get("acceptable"), bool)
                and isinstance(result.get("reason"), str) and bool(result["reason"].strip())
                and isinstance(result.get("confidence"), (int, float)) and not isinstance(result.get("confidence"), bool)
                and math.isfinite(result["confidence"]) and 0 <= result["confidence"] <= 100)
    return False


def _vision_cache_entry(client, function, signature, args, kwargs):
    from ..job_context import check_job_active, current_job
    if getattr(client, "provider", "openrouter") != "openrouter" or getattr(client._telemetry, "cache_bypass", False):
        return None
    bound = signature.bind(client, *args, **kwargs); bound.apply_defaults()
    values = dict(bound.arguments); values.pop("self", None)
    paths = values.pop("frame_paths", values.pop("image_paths", []))
    if "image_path" in values: paths = [values.pop("image_path")]
    paths = list(paths or [])
    if function.__name__ == "compare_images": paths = paths[:4]
    if not paths: return None
    check_job_active()
    try:
        policy = vision_cache_policy(client)
        key = decision_cache_key(operation=function.__name__, model=client.model,
            prompt=json.dumps(values, sort_keys=True, ensure_ascii=False, default=str), paths=paths,
            account_scope=policy["account"], policy=policy)
        directory = current_job().get("llm_cache_dir")
        store = VisualDecisionCache(str(Path(directory) / "vision-decisions.sqlite3")) if directory else VisualDecisionCache()
        return key, store, policy, values, paths
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return None


def _reuse_vision_response(client, operation, cached):
    client.last_usage = {}; client.last_cost = 0.; client.last_latency_ms = 0.
    client._telemetry.last_cache_hit = True
    from ..openrouter_usage import record_cache_hit
    record_cache_hit(client.model, "vision_" + operation, kind="vision")
    return copy.deepcopy(cached)


def cache_vision_response(function):
    """Coalesce exact multimodal work across clients; never bypass live probes."""
    signature = inspect.signature(function)
    def lookup(client, *args, **kwargs):
        entry = _vision_cache_entry(client, function, signature, args, kwargs)
        if entry is None: return None
        key, store, *_ = entry
        cached = store.get(key)
        return _reuse_vision_response(client, function.__name__, cached) if valid_cached_decision(function.__name__, cached) else None
    @functools.wraps(function)
    def wrapped(self, *args, **kwargs):
        from ..job_context import check_job_active, remaining_seconds
        self._telemetry.last_cache_hit = False
        entry = _vision_cache_entry(self, function, signature, args, kwargs)
        if entry is None: return function(self, *args, **kwargs)
        key, store, policy, values, paths = entry
        with _REQUEST_GUARD:
            lock_key = (store.path, key)
            flight = _REQUEST_LOCKS.get(lock_key)
            if flight is None:
                flight = _VisionFlight(); _REQUEST_LOCKS[lock_key] = flight
            observed_generation = flight.generation
        # A timed-out waiter never launches another paid request while its
        # exact owner is still live. Provider failures are never cached.
        wait_seconds = max(1., float(getattr(self, "timeout_seconds", 120)) * max(1, int(getattr(self, "max_attempts", 2))) + 15.)
        remaining = remaining_seconds(wait_seconds)
        deadline = time.monotonic() + min(wait_seconds, remaining if remaining is not None else wait_seconds)
        while not flight.lock.acquire(timeout=.1):
            check_job_active()
            if time.monotonic() >= deadline:
                raise TimeoutError("An identical visual verification is still pending; no duplicate request was started.")
        try:
            check_job_active()
            if flight.generation != observed_generation and flight.failure is not None:
                error_type, arguments = flight.failure
                raise error_type(*arguments)
            cached = store.get(key)
            if valid_cached_decision(function.__name__, cached):
                return _reuse_vision_response(self, function.__name__, cached)
            try:
                result = function(self, *args, **kwargs)
                check_job_active()
            except BaseException as failure:
                with _REQUEST_GUARD:
                    # Do not retain a traceback containing encoded footage
                    # while failed requests wait for garbage collection.
                    flight.failure = (type(failure), failure.args)
                    flight.generation += 1
                raise
            if valid_cached_decision(function.__name__, result):
                try:
                    unchanged_key = decision_cache_key(operation=function.__name__, model=self.model,
                        prompt=json.dumps(values, sort_keys=True, ensure_ascii=False, default=str), paths=paths,
                        account_scope=policy["account"], policy=policy)
                    if unchanged_key == key: store.put(key, function.__name__, self.model, result)
                except (OSError, ValueError, TypeError):
                    pass
            return result
        finally:
            flight.lock.release()
    wrapped._vision_cache_lookup = lookup
    return wrapped
