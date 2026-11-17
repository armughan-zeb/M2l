# =============================================================================
# TRANSCRIPTION SERVICE - Whisper + ASS Subtitles
# =============================================================================

import hashlib
import os
import re
import subprocess
import time
from difflib import SequenceMatcher
from concurrent.futures import Future, TimeoutError as FutureTimeoutError, as_completed
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from pathlib import Path
import sys
import threading
from .job_context import JobThread, remaining_seconds
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    TEMP_DIR, FONTS_DIR, FONT_NAME_MAP, ANIMATIONS, CAPTION_STYLES
)

# Global state
_whisper_model = None
_available = False
_whisper_backend = ""
_resolved_whisper_model_name = ""
_worker_local = threading.local()
_whisper_init_lock = threading.Lock()
_whisper_start_lock = threading.Lock()
_whisper_start = None


def init_whisper(device: str = "cpu"):
    """Initialize Whisper model"""
    global _whisper_model, _available
    with _whisper_init_lock:
        return _init_whisper_locked(device)


def _init_whisper_locked(device: str = "cpu"):
    global _whisper_model, _available, _whisper_backend, _resolved_whisper_model_name

    if _whisper_model is not None:
        return _available

    try:
        from faster_whisper import WhisperModel
        compute_type = os.environ.get("VIDZEO_FASTER_WHISPER_COMPUTE_TYPE", "int8") or "int8"
        cpu_threads = int(os.environ.get("VIDZEO_WHISPER_BASE_THREADS", str(os.cpu_count() or 4)) or 4)
        last_error = None
        for model_name in _whisper_model_candidates():
            try:
                print(f"   Loading Whisper ({device}) via faster-whisper model={model_name} (threads={cpu_threads})...")
                _whisper_model = WhisperModel(
                    model_name, device=device, compute_type=compute_type,
                    cpu_threads=cpu_threads, num_workers=1
                )
                _resolved_whisper_model_name = model_name
                _whisper_backend = "faster-whisper"
                _available = True
                if model_name != WHISPER_MODEL_NAME:
                    print(f"   Whisper loaded with cached fallback model: {model_name}")
                else:
                    print("   Whisper loaded (faster-whisper)")
                return _available
            except Exception as exc:
                last_error = exc
                print(f"   Whisper model {model_name} unavailable: {exc}")
                _whisper_model = None
        raise last_error or RuntimeError("No Whisper model could be loaded")
    except Exception as e:
        print(f"   faster-whisper failed: {e}")
        _whisper_model = None
        _whisper_backend = ""
        _available = False

    return _available


def _load_worker_model(device: str = "cpu", cpu_threads: int = 2):
    global _resolved_whisper_model_name
    from faster_whisper import WhisperModel
    compute_type = os.environ.get("VIDZEO_FASTER_WHISPER_COMPUTE_TYPE", "int8") or "int8"
    last_error = None
    for model_name in _whisper_model_candidates(prefer_resolved=True):
        try:
            print(f"   Loading Whisper worker ({device}) via faster-whisper model={model_name} (threads={cpu_threads})...")
            model = WhisperModel(
                model_name, device=device, compute_type=compute_type,
                cpu_threads=cpu_threads, num_workers=1
            )
            _resolved_whisper_model_name = model_name
            return model, "faster-whisper"
        except Exception as exc:
            last_error = exc
            print(f"   Whisper worker model {model_name} unavailable: {exc}")
    raise last_error or RuntimeError("No Whisper worker model could be loaded")


def is_available():
    """Check if transcription is available"""
    return _available


def whisper_runtime_status() -> dict:
    """Report real local Whisper readiness without starting a model download."""
    cache_roots = []
    for raw in (
        os.environ.get("HF_HUB_CACHE"),
        (Path(os.environ.get("HF_HOME", "")).expanduser() / "hub") if os.environ.get("HF_HOME") else None,
        Path.home() / ".cache" / "huggingface" / "hub",
    ):
        try:
            if raw:
                root = Path(raw).expanduser()
                if root not in cache_roots:
                    cache_roots.append(root)
        except (TypeError, ValueError):
            continue

    cached = []
    for model_name in _whisper_model_candidates(prefer_resolved=True):
        model_path = Path(model_name).expanduser()
        is_cached = model_path.is_dir() and (model_path / "model.bin").is_file()
        if not is_cached:
            cache_key = "models--Systran--faster-whisper-" + model_name.replace("/", "--")
            for root in cache_roots:
                model_root = root / cache_key / "snapshots"
                try:
                    is_cached = any((snapshot / "model.bin").is_file() for snapshot in model_root.iterdir())
                except (OSError, FileNotFoundError):
                    pass
                if is_cached:
                    break
        if is_cached and model_name not in cached:
            cached.append(model_name)

    try:
        import importlib.util
        installed = importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        installed = False
    return {
        "available": bool(_available or cached),
        "loaded": bool(_available and _whisper_model is not None),
        "installed": installed,
        "configured_model": WHISPER_MODEL_NAME,
        "loaded_model": _resolved_whisper_model_name or None,
        "cached_models": cached,
        "backend": _whisper_backend or None,
    }


def unload_whisper_model():
    """Release Whisper model from memory."""
    global _whisper_model, _available, _resolved_whisper_model_name
    # A caption caller may already have fallen back while the shared model
    # is still loading. Cleanup must never join that same slow load.
    if not _whisper_init_lock.acquire(blocking=False):
        return False
    try:
        previous = _whisper_model
        _whisper_model = None
        _available = False
        _resolved_whisper_model_name = ""
    finally:
        _whisper_init_lock.release()
    if previous is not None:
        import gc
        del previous
        gc.collect()
        print("   [whisper] model released from memory")
    return previous is not None


WHISPER_MODEL_NAME = (os.environ.get("VIDZEO_WHISPER_MODEL", "base") or "base").strip()
WHISPER_FALLBACK_MODELS = tuple(
    item.strip() for item in (os.environ.get("VIDZEO_WHISPER_FALLBACK_MODELS", "small.en,tiny.en") or "small.en,tiny.en").split(",")
    if item.strip()
)
WHISPER_PREWARM_SYNC_SECONDS = 0.0
WHISPER_MIN_PARALLEL_DURATION_SECONDS = float(os.environ.get("VIDZEO_WHISPER_MIN_PARALLEL_SECONDS", "20") or 20)
WHISPER_TARGET_CHUNK_SECONDS = float(os.environ.get("VIDZEO_WHISPER_TARGET_CHUNK_SECONDS", "14") or 14)
WHISPER_CHUNK_OVERLAP_SECONDS = max(
    0.25, min(1.5, float(os.environ.get("VIDZEO_WHISPER_CHUNK_OVERLAP_SECONDS", "0.75") or 0.75))
)


def _whisper_model_candidates(prefer_resolved: bool = False) -> list[str]:
    """Prefer locally cached Whisper models so offline jobs do not stall on misses."""
    names = []
    resolved = str(globals().get("_resolved_whisper_model_name") or "").strip()
    if prefer_resolved and resolved:
        names.append(resolved)
    bundled = Path(__file__).resolve().parents[2] / "tools" / "models" / "whisper-tiny.en"
    if (bundled / "model.bin").is_file():
        names.append(str(bundled))
    names.append(WHISPER_MODEL_NAME)
    names.extend(WHISPER_FALLBACK_MODELS)
    result: list[str] = []
    for name in names:
        if name and name not in result:
            result.append(name)
    cache_roots = []
    explicit_cache = os.environ.get("HF_HUB_CACHE")
    hf_home = os.environ.get("HF_HOME")
    if explicit_cache:
        cache_roots.append(Path(explicit_cache).expanduser())
    if hf_home:
        cache_roots.append(Path(hf_home).expanduser() / "hub")
    cache_roots.append(Path.home() / ".cache" / "huggingface" / "hub")
    cached = []
    for model_name in result:
        model_path = Path(model_name).expanduser()
        found = model_path.is_dir() and (model_path / "model.bin").is_file()
        if not found:
            cache_key = "models--Systran--faster-whisper-" + model_name.replace("/", "--")
            for root in cache_roots:
                snapshots = root / cache_key / "snapshots"
                try:
                    found = any((snapshot / "model.bin").is_file() for snapshot in snapshots.iterdir())
                except (OSError, FileNotFoundError):
                    found = False
                if found:
                    break
        if found:
            cached.append(model_name)
    if cached:
        return cached
    return result

def get_whisper_parallel_policy(chunk_count: int, cpu_count: int, available_memory_gb: float | None = None) -> dict:
    # Whisper workers each own a model instance. Bound model copies by actual
    # free RAM so prewarming does not steal the headroom needed by Remotion.
    if available_memory_gb is None and "PYTEST_CURRENT_TEST" in os.environ:
        available_gb = 16.0
    elif available_memory_gb is not None:
        available_gb = float(available_memory_gb)
    else:
        try:
            import psutil
            available_gb = float(psutil.virtual_memory().available) / (1024 ** 3)
        except Exception:
            available_gb = 8.0
    if available_gb >= 10.0:
        memory_workers = 4
    elif available_gb >= 5.0:
        memory_workers = 3
    elif available_gb >= 2.5:
        memory_workers = 2
    else:
        memory_workers = 1
    cpu_workers = 4 if cpu_count >= 10 else 3
    worker_count = max(1, min(int(chunk_count or 1), memory_workers, cpu_workers))
    threads_per_worker = max(1, min(4, cpu_count // worker_count))
    return {
        "worker_count": worker_count,
        "cpu_tier_workers": cpu_workers,
        "memory_tier_workers": memory_workers,
        "available_memory_gb": round(available_gb, 2),
        "threads_per_worker": threads_per_worker,
    }


def align_whisper_words_to_script(script: str, whisper_words: list, audio_duration: float) -> tuple[list, dict]:
    """Map Whisper timestamps onto the exact TTS script, retaining punctuation.

    ASR insertions are discarded, omitted script tokens receive interpolated
    timestamps between neighboring recognized words, and scene splitting then
    operates on the narration's exact word order rather than raw ASR token count.
    """
    script_tokens = re.findall(r"\S+", str(script or "").strip())
    asr_words = [row for row in (whisper_words or []) if str(row.get("word", "")).strip()]
    if not script_tokens or not asr_words:
        return [], {"status": "unavailable", "script_word_count": len(script_tokens), "asr_word_count": len(asr_words)}

    def normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

    script_norm = [normalize(word) for word in script_tokens]
    asr_norm = [normalize(row.get("word", "")) for row in asr_words]
    n, m = len(script_tokens), len(asr_words)
    delete_cost, insert_cost = 0.92, 0.78
    cost = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0], back[i][0] = i * delete_cost, "script_only"
    for j in range(1, m + 1):
        cost[0][j], back[0][j] = j * insert_cost, "asr_only"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            left, right = script_norm[i - 1], asr_norm[j - 1]
            similarity = SequenceMatcher(None, left, right).ratio() if left and right else 0.0
            if left and left == right:
                match_cost = 0.0
            elif similarity >= 0.55:
                match_cost = 0.45 + (1.0 - similarity) * 0.65
            else:
                match_cost = 1.35
            choices = (
                (cost[i - 1][j - 1] + match_cost, "match"),
                (cost[i - 1][j] + delete_cost, "script_only"),
                (cost[i][j - 1] + insert_cost, "asr_only"),
            )
            cost[i][j], back[i][j] = min(choices, key=lambda entry: entry[0])

    matches = {}
    exact_matches = 0
    i, j = n, m
    while i or j:
        op = back[i][j]
        if op == "match":
            matches[i - 1] = j - 1
            if script_norm[i - 1] and script_norm[i - 1] == asr_norm[j - 1]:
                exact_matches += 1
            i -= 1
            j -= 1
        elif op == "script_only":
            i -= 1
        elif op == "asr_only":
            j -= 1
        else:
            break

    duration = max(0.05, float(audio_duration or 0.05))
    anchors = {}
    for script_index, asr_index in matches.items():
        row = asr_words[asr_index]
        start = max(0.0, min(duration, float(row.get("start", 0.0) or 0.0)))
        end = max(start, min(duration, float(row.get("end", start) or start)))
        anchors[script_index] = {"start": start, "end": end, "asr_word": str(row.get("word") or "")}

    matched_indices = sorted(anchors)
    aligned = []
    for index, token in enumerate(script_tokens):
        anchor = anchors.get(index)
        if anchor:
            start, end = anchor["start"], anchor["end"]
            source = "whisper"
        else:
            before = next((idx for idx in reversed(matched_indices) if idx < index), None)
            after = next((idx for idx in matched_indices if idx > index), None)
            left_time = (
                (anchors[before]["start"] + anchors[before]["end"]) / 2
                if before is not None else 0.0
            )
            right_time = (
                (anchors[after]["start"] + anchors[after]["end"]) / 2
                if after is not None else duration
            )
            missing_count = (after - before - 1) if before is not None and after is not None else 1
            missing_offset = (index - before) if before is not None else index + 1
            center = left_time + (right_time - left_time) * (missing_offset / max(1, missing_count + 1))
            estimated_width = min(0.16, max(0.06, 0.035 * len(normalize(token))))
            start = max(0.0, min(duration - 0.02, center - estimated_width / 2))
            end = min(duration, max(start + 0.02, center + estimated_width / 2))
            source = "interpolated_from_whisper"
        aligned.append({
            "word": token,
            "start": round(start, 3),
            "end": round(end, 3),
            "index": index,
            "timing_source": source,
        })

    # Whisper occasionally assigns near-zero intervals to short words or gives
    # adjacent words the same onset. Keep the acoustic anchors, but smooth
    # these local clusters into non-overlapping, readable word windows. This is
    # important for scene cuts, kinetic text, and sound effects that use words
    # as their timing anchors.
    min_word_seconds = 0.075

    def _word_weight(value: str) -> float:
        return max(1.0, min(10.0, len(normalize(value))))

    raw = [dict(row) for row in aligned]
    repaired = set()
    index = 0
    while index < len(raw):
        start_value = float(raw[index]["start"])
        end_value = float(raw[index]["end"])
        previous_end = float(raw[index - 1]["end"]) if index else 0.0
        is_short = end_value - start_value < min_word_seconds
        overlaps_previous = index > 0 and start_value < previous_end - 0.015
        if not is_short and not overlaps_previous:
            index += 1
            continue

        group_start = index - 1 if overlaps_previous else index
        group_end = index + 1
        group_left = min(float(raw[pos]["start"]) for pos in range(group_start, group_end))
        group_right = max(float(raw[pos]["end"]) for pos in range(group_start, group_end))
        # Include touching neighbors only while the local interval is still
        # too short for its words; avoid smoothing whole sentences together.
        while group_end < len(raw) and group_right - group_left < (group_end - group_start) * min_word_seconds:
            next_start = float(raw[group_end]["start"])
            next_end = float(raw[group_end]["end"])
            if next_start > group_right + 0.025:
                break
            group_left = min(group_left, next_start)
            group_right = max(group_right, next_end)
            group_end += 1

        group_size = group_end - group_start
        if group_size > 1:
            group_right = max(group_right, group_left + min_word_seconds * group_size)
            next_start = float(raw[group_end]["start"]) if group_end < len(raw) else duration
            group_right = min(duration, group_right, max(group_left + min_word_seconds * group_size, next_start))
            weights = [_word_weight(raw[pos]["word"]) for pos in range(group_start, group_end)]
            weight_sum = sum(weights) or float(group_size)
            cursor = group_left
            for offset, pos in enumerate(range(group_start, group_end)):
                share = weights[offset] / weight_sum
                word_end = group_right if offset == group_size - 1 else cursor + (group_right - group_left) * share
                raw[pos]["start"] = cursor
                raw[pos]["end"] = word_end
                raw[pos]["timing_source"] = "whisper_smoothed"
                repaired.add(pos)
                cursor = word_end
            index = group_end
            continue

        # An isolated clipped Whisper token gets a conservative duration based
        # on nearby valid words, without moving its measured onset.
        neighbors = [
            float(raw[pos]["end"]) - float(raw[pos]["start"])
            for pos in range(max(0, index - 2), min(len(raw), index + 3))
            if pos != index
            and float(raw[pos]["end"]) - float(raw[pos]["start"]) >= min_word_seconds
        ]
        estimated = max(min_word_seconds, min(0.42, sorted(neighbors)[len(neighbors) // 2] if neighbors else 0.18))
        next_start = float(raw[index + 1]["start"]) if index + 1 < len(raw) else duration
        proposed_end = min(duration, start_value + estimated)
        if next_start > start_value:
            proposed_end = min(proposed_end, next_start)
        if proposed_end - start_value < min_word_seconds and next_start - start_value >= min_word_seconds:
            proposed_end = min(duration, next_start)
        raw[index]["end"] = max(start_value + min_word_seconds, proposed_end)
        raw[index]["timing_source"] = "whisper_smoothed"
        repaired.add(index)
        index += 1

    # Enforce a monotonic, non-overlapping word timeline while retaining real
    # pauses. Starts are never moved before their Whisper onset.
    previous_end = 0.0
    for index, row in enumerate(raw):
        start_value = max(previous_end, min(duration, float(row["start"])))
        next_start = float(raw[index + 1]["start"]) if index + 1 < len(raw) else duration
        end_value = min(duration, max(start_value + min_word_seconds, float(row["end"])))
        if next_start > start_value:
            end_value = min(end_value, next_start)
        if end_value - start_value < min_word_seconds and duration - start_value >= min_word_seconds:
            end_value = min(duration, start_value + min_word_seconds)
        row["start"] = round(start_value, 3)
        row["end"] = round(max(start_value, end_value), 3)
        previous_end = row["end"]
    aligned = raw
    if aligned:
        # Keep the exact TTS clock at the final boundary. Rounding 43.566984
        # to 43.567 made the immutable scene aligner reject an otherwise valid
        # Whisper timeline as longer than its audio file.
        aligned[-1]["end"] = duration
    exact_ratio = exact_matches / max(1, n)
    return aligned, {
        "status": "aligned",
        "script_word_count": n,
        "asr_word_count": m,
        "exact_match_count": exact_matches,
        "exact_match_ratio": round(exact_ratio, 4),
        "interpolated_word_count": n - len(matches),
        "asr_insertions_dropped": max(0, m - len(matches)),
        "timing_repair_count": len(repaired),
        "minimum_word_seconds": min_word_seconds,
        "alignment_cost": round(cost[n][m], 3),
    }

def _build_chunk_plan(duration: float, target_chunks: int, overlap_seconds: float = WHISPER_CHUNK_OVERLAP_SECONDS) -> list:
    """Build overlapping windows while preserving a non-overlapping core per chunk."""
    chunk_len = duration / target_chunks
    overlap = max(0.0, min(float(overlap_seconds or 0.0), chunk_len * 0.35))
    plan = []
    for i in range(target_chunks):
        core_start = i * chunk_len
        core_end = duration if i == target_chunks - 1 else (i + 1) * chunk_len
        start = max(0.0, core_start - overlap)
        end = min(duration, core_end + overlap)
        plan.append({
            "index": i,
            "start": start,
            "end": end,
            "duration": max(0.01, end - start),
            "core_start": core_start,
            "core_end": core_end,
        })
    return plan


def _normalise_word_token(value: str) -> str:
    import re
    return re.sub(r"[^a-z0-9']+", "", str(value or "").lower())


def _dedupe_overlapping_transcript(words: list, segments: list, duration: float) -> tuple[list, list]:
    """Remove duplicate boundary words created by overlapping Whisper windows."""
    ordered = sorted(words or [], key=lambda row: (float(row.get("start", 0.0) or 0.0), float(row.get("end", 0.0) or 0.0)))
    clean_words = []
    for row in ordered:
        token = _normalise_word_token(row.get("word", ""))
        if not token:
            continue
        start = max(0.0, min(float(duration or 0.0), float(row.get("start", 0.0) or 0.0)))
        end = max(start, min(float(duration or 0.0), float(row.get("end", start) or start)))
        candidate = {**row, "start": round(start, 3), "end": round(end, 3)}
        duplicate = False
        for previous in reversed(clean_words[-4:]):
            previous_token = _normalise_word_token(previous.get("word", ""))
            if previous_token != token:
                continue
            if abs(start - float(previous.get("start", 0.0))) <= 0.22 and abs(end - float(previous.get("end", 0.0))) <= 0.40:
                duplicate = True
                # Keep the wider/earlier boundary estimate for stable scene cuts.
                previous["start"] = round(min(float(previous.get("start", start)), start), 3)
                previous["end"] = round(max(float(previous.get("end", end)), end), 3)
                break
        if not duplicate:
            clean_words.append(candidate)

    clean_segments = []
    for row in sorted(segments or [], key=lambda item: (float(item.get("start", 0.0) or 0.0), float(item.get("end", 0.0) or 0.0))):
        start = max(0.0, min(float(duration or 0.0), float(row.get("start", 0.0) or 0.0)))
        end = max(start, min(float(duration or 0.0), float(row.get("end", start) or start)))
        text = str(row.get("text", "") or "").strip()
        if not text:
            continue
        if clean_segments and abs(start - clean_segments[-1]["start"]) <= 0.22 and text.lower() == clean_segments[-1]["text"].lower():
            clean_segments[-1]["end"] = round(max(clean_segments[-1]["end"], end), 3)
            continue
        clean_segments.append({**row, "start": round(start, 3), "end": round(end, 3), "text": text})
    return clean_words, clean_segments

def start_whisper_job_warmup(device="cpu", preload_workers=True, worker_count=None):
    event = threading.Event()
    cpu_count = os.cpu_count() or 4
    policy = get_whisper_parallel_policy(8, cpu_count)
    if worker_count is None:
        worker_count = policy["worker_count"]
    preload_workers = preload_workers and worker_count > 1 and policy["worker_count"] > 1
    threads_per_worker = policy.get("threads_per_worker", 2)
    state = {
        "base_model_ready": False,
        "ready_workers": 0,
        "target_workers": worker_count if preload_workers else 0,
        "error": None
    }

    handle = {
        "event": event,
        "state": state,
        "executor": None,
        "worker_futures": [],
    }

    def load_base():
        try:
            state["base_model_ready"] = _init_whisper_with_timeout(device=device)
        except Exception as e:
            state["error"] = str(e)
        finally:
            if not preload_workers:
                event.set()

    def load_workers():
        if not preload_workers:
            return
        try:
            pool_size = max(1, int(worker_count or 1))
            executor = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="whisper-prewarm")
            handle["executor"] = executor
            futures = [executor.submit(_get_worker_model, device, threads_per_worker) for _ in range(pool_size)]
            handle["worker_futures"] = futures
            for future in as_completed(futures):
                future.result()
                state["ready_workers"] = int(state.get("ready_workers", 0)) + 1
        except Exception as e:
            state["error"] = str(e)
        finally:
            event.set()

    JobThread(target=load_base, daemon=True).start()
    JobThread(target=load_workers, daemon=True).start()
    return handle

def describe_whisper_job_warmup(handle: dict) -> dict:
    return handle.get("state", {
        "base_model_ready": False,
        "ready_workers": 0,
        "target_workers": 0,
        "error": None
    })

def trigger_whisper_worker_prewarm(handle: dict, worker_count: int = 4):
    return bool(handle and handle.get("executor"))

def shutdown_whisper_job_warmup(handle: dict):
    executor = handle.get("executor") if isinstance(handle, dict) else None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=False)

def prewarm_whisper_workers():
    init_whisper()

def _init_whisper_with_timeout(timeout: float = 30.0, device: str = "cpu") -> bool:
    """Bound the caller's wait and share a single model load across attempts."""
    global _whisper_start
    if _available:
        return True
    budget = max(0.0, min(timeout, remaining_seconds(timeout)))
    if budget <= 0:
        return False
    with _whisper_start_lock:
        if _available:
            return True
        if _whisper_start is None or _whisper_start.done():
            pending = Future()
            _whisper_start = pending

            def load():
                try:
                    pending.set_result(init_whisper(device))
                except Exception as exc:
                    pending.set_exception(exc)

            # The shared model can finish loading even if one job times out.
            threading.Thread(target=load, name="whisper-model-load", daemon=True).start()
        active = _whisper_start
    try:
        return active.result(timeout=budget)
    except FutureTimeoutError:
        print(f"   [whisper] model loading exceeded the {budget:.1f}s wait budget")
        return False

def _transcribe_with_model(model, audio_path: str, backend: str = "", offset: float = 0.0,
                           language: str | None = None) -> tuple:
    words = []
    segments = []

    if backend == "faster-whisper":
        fw_segments, _info = model.transcribe(audio_path, word_timestamps=True, language=language)
        for seg in fw_segments:
            seg_start = float(getattr(seg, "start", 0.0) or 0.0) + offset
            seg_end = float(getattr(seg, "end", 0.0) or 0.0) + offset
            segments.append({
                "text": str(getattr(seg, "text", "") or "").strip(),
                "start": seg_start,
                "end": seg_end,
            })
            for wd in getattr(seg, "words", None) or []:
                words.append({
                    "word": str(getattr(wd, "word", "") or "").strip(),
                    "start": float(getattr(wd, "start", 0.0) or 0.0) + offset,
                    "end": float(getattr(wd, "end", 0.0) or 0.0) + offset,
                })
        return words, segments

    result = model.transcribe(audio_path, word_timestamps=True, language=language)
    if not isinstance(result, dict):
        raise RuntimeError("Whisper returned no transcription result")

    for seg in result.get("segments", []) or []:
        if not isinstance(seg, dict):
            continue
        segments.append({
            "text": str(seg.get("text", "") or "").strip(),
            "start": float(seg.get("start", 0)) + offset,
            "end": float(seg.get("end", 0)) + offset,
        })
        for wd in seg.get("words", []) or []:
            if not isinstance(wd, dict):
                continue
            words.append({
                "word": str(wd.get("word", "") or "").strip(),
                "start": float(wd.get("start", 0)) + offset,
                "end": float(wd.get("end", 0)) + offset,
            })
    return words, segments


def _transcribe_audio_local(audio_path: str, language: str | None = None) -> tuple:
    global _whisper_model, _available, _whisper_backend

    # Keep an owned reference across inference. Another job may release the
    # idle global cache after its own alignment without invalidating this one.
    with _whisper_init_lock:
        model, available, backend = _whisper_model, _available, _whisper_backend
    if not available or model is None:
        return [], []

    print("   Transcribing...")
    words, segments = _transcribe_with_model(
        model, audio_path, backend, language=language
    )
    print(f"   {len(words)} words transcribed")
    return words, segments


def transcribe_audio(audio_path: str, warmup_handle: dict = None,
                     language: str | None = None) -> tuple:
    global _available
    if not _available and not _init_whisper_with_timeout():
        return [], []
    if warmup_handle and warmup_handle.get("state", {}).get("target_workers", 1):
        return transcribe_audio_parallel(audio_path, warmup_handle, language=language)
    return _transcribe_audio_local(audio_path, language=language)

def _get_audio_duration(audio_path: str) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", audio_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return max(0.0, float((result.stdout or "0").strip() or 0))
    except Exception:
        return 0.0


def _extract_audio_chunk(audio_path: str, chunk: dict) -> str:
    seed = f"{audio_path}:{chunk.get('index')}:{time.time()}"
    chunk_path = TEMP_DIR / f"whisper_chunk_{hashlib.sha256(seed.encode()).hexdigest()[:12]}.wav"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", f"{float(chunk['start']):.3f}",
            "-t", f"{float(chunk['duration']):.3f}",
            "-i", audio_path,
            "-ar", "16000",
            "-ac", "1",
            "-vn",
            str(chunk_path),
        ],
        capture_output=True,
        text=True,
        timeout=max(20, int(float(chunk["duration"]) + 20)),
        check=True,
    )
    return str(chunk_path)


def _get_worker_model(device: str = "cpu", cpu_threads: int = 2):
    model = getattr(_worker_local, "model", None)
    backend = getattr(_worker_local, "backend", "")
    if model is not None and backend:
        return model, backend

    model, backend = _load_worker_model(device, cpu_threads=cpu_threads)

    _worker_local.model = model
    _worker_local.backend = backend
    return model, backend


def _transcribe_chunk(
    audio_path: str,
    chunk: dict,
    device: str = "cpu",
    language: str | None = None,
    cpu_threads: int = 2,
) -> tuple:
    chunk_path = None
    offset = float(chunk.get("start") or 0.0)
    try:
        chunk_path = _extract_audio_chunk(audio_path, chunk)
        model, backend = _get_worker_model(device, cpu_threads=cpu_threads)
        words, segments = _transcribe_with_model(
            model,
            chunk_path,
            backend,
            offset=offset,
            language=language,
        )
        return int(chunk["index"]), words, segments
    finally:
        if chunk_path:
            try:
                os.remove(chunk_path)
            except Exception:
                pass


def transcribe_audio_parallel(
    audio_path: str,
    warmup_handle: dict = None,
    language: str | None = None,
) -> tuple:
    duration = _get_audio_duration(audio_path)
    if duration < WHISPER_MIN_PARALLEL_DURATION_SECONDS:
        if not _available:
            _init_whisper_with_timeout()
        return _transcribe_audio_local(audio_path, language=language)

    target_chunks = max(2, min(16, int(round(duration / max(6.0, WHISPER_TARGET_CHUNK_SECONDS)))))
    policy = get_whisper_parallel_policy(target_chunks, os.cpu_count() or 2)
    worker_count = max(1, int(policy.get("worker_count") or 1))
    if worker_count == 1:
        return _transcribe_audio_local(audio_path, language=language)
    threads_per_worker = int(policy.get("threads_per_worker", 2))
    chunks = _build_chunk_plan(duration, target_chunks)
    print(f"   Whisper chunk transcription: {len(chunks)} overlapping chunks x {worker_count} workers (threads/worker={threads_per_worker}, overlap={WHISPER_CHUNK_OVERLAP_SECONDS:.2f}s)")

    words_by_chunk = {}
    segments_by_chunk = {}
    failures = []
    executor = warmup_handle.get("executor") if isinstance(warmup_handle, dict) else None
    owns_executor = executor is None
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="whisper-chunk")
    future_map = {}
    try:
        future_map = {
            executor.submit(_transcribe_chunk, audio_path, chunk, "cpu", language, threads_per_worker): chunk
            for chunk in chunks
        }
        for future in as_completed(future_map, timeout=remaining_seconds(None)):
            chunk = future_map[future]
            try:
                index, words, segments = future.result()
                words_by_chunk[index] = words
                segments_by_chunk[index] = segments
                print(f"   Whisper chunk {index + 1}/{len(chunks)} done ({len(words)} words)")
            except Exception as exc:
                failures.append(f"chunk {int(chunk.get('index', 0)) + 1}: {exc}")
    except FutureTimeoutError:
        print("   [whisper] parallel transcription exhausted its job budget")
    finally:
        for future in future_map:
            future.cancel()
        if owns_executor:
            executor.shutdown(wait=False, cancel_futures=True)

    if not words_by_chunk and remaining_seconds(1) <= 0:
        return [], []
    if failures and not words_by_chunk:
        print(f"   Whisper chunk transcription failed; falling back to full audio: {'; '.join(failures)}")
        return _transcribe_audio_local(audio_path, language=language)
    if failures:
        print(f"   Whisper partial chunk fallback: {'; '.join(failures)}")

    merged_words = []
    merged_segments = []
    for index in range(len(chunks)):
        merged_words.extend(words_by_chunk.get(index, []))
        merged_segments.extend(segments_by_chunk.get(index, []))
    merged_words.sort(key=lambda row: (row.get("start", 0), row.get("end", 0)))
    merged_segments.sort(key=lambda row: (row.get("start", 0), row.get("end", 0)))
    raw_word_count = len(merged_words)
    merged_words, merged_segments = _dedupe_overlapping_transcript(merged_words, merged_segments, duration)
    print(f"   [ok] {len(merged_words)} words transcribed in parallel chunks (deduped {raw_word_count - len(merged_words)} overlap words)")
    return merged_words, merged_segments


def to_ass_time(t: float) -> str:
    """Convert seconds to ASS timestamp format"""
    cs = int(round(t * 100))
    return f"{cs // 360000}:{(cs // 6000) % 60:02d}:{(cs // 100) % 60:02d}.{cs % 100:02d}"


def hex_to_ass(c: str) -> str:
    """Convert hex color to ASS format"""
    c = c.lstrip("#")
    return f"&H00{int(c[4:6], 16):02X}{int(c[2:4], 16):02X}{int(c[0:2], 16):02X}"


def hex_bgr(c: str) -> str:
    """Convert hex to BGR format"""
    c = c.lstrip("#")
    return f"{int(c[4:6], 16):02X}{int(c[2:4], 16):02X}{int(c[0:2], 16):02X}"


def escape_ass(s: str) -> str:
    """Escape special characters for ASS"""
    return s.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def get_font_name(dropdown_value: str) -> str:
    """Extract font family name from dropdown value"""
    if "|" in dropdown_value:
        # Robust split: take last part and strip whitespace
        filename = dropdown_value.split("|")[-1].strip()
    else:
        filename = dropdown_value
    return FONT_NAME_MAP.get(filename, filename)


_CAPTION_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "when", "at", "by",
    "for", "with", "about", "against", "between", "into", "through", "during", "before",
    "after", "above", "below", "to", "from", "up", "down", "in", "out", "on", "off",
    "over", "under", "again", "further", "then", "once", "here", "there", "all", "any",
    "both", "each", "few", "more", "most", "other", "some", "such", "no", "nor", "not",
    "only", "own", "same", "so", "than", "too", "very", "s", "t", "can", "will", "just",
    "don", "should", "now", "is", "are", "was", "were", "be", "been", "being", "have",
    "has", "had", "having", "do", "does", "did", "doing", "i", "me", "my", "myself",
    "we", "our", "ours", "ourselves", "you", "your", "yours", "yourself", "yourselves",
    "he", "him", "his", "himself", "she", "her", "hers", "herself", "it", "its",
    "itself", "they", "them", "their", "theirs", "themselves", "what", "which", "who",
    "whom", "this", "that", "these", "those", "am", "also", "as", "of"
}

_HIGH_IMPACT_HINTS = {
    "world", "cup", "history", "legend", "legendary", "icon", "iconic", "greatest",
    "best", "master", "mastered", "champion", "victory", "pioneer", "pioneered",
    "revolution", "revolutionary", "lethal", "explosive", "unmatched", "monumental",
    "fearless", "terrorized", "discovery", "science", "record", "first", "million",
    "billion", "trillion", "thousand", "hundred", "percent", "gravity", "relativity",
    "physics", "nobel", "prize", "captain", "cricket", "king", "queen", "empire",
    "secret", "insane", "massive", "deadly", "crucial", "extreme", "ultimate"
}


def select_best_highlight_word(block: List[Dict]) -> int:
    """Select the most impactful word index in a chunk of words for caption highlighting.

    Prefers numbers, years, proper nouns, intense actions, and high-impact keywords over stopwords.
    """
    import re
    if not block:
        return 0
    if len(block) == 1:
        return 0

    best_idx = 0
    best_score = -999.0

    for idx, w in enumerate(block):
        raw_word = str(w.get("text", w.get("word", "")) or "").strip()
        clean_word = re.sub(r"[^\w]", "", raw_word).lower()
        if not clean_word:
            continue

        score = 1.0

        # 1. Digits / Numbers / Years / Stats (highest priority)
        if re.search(r"\d", raw_word):
            score += 12.0
        elif clean_word in _HIGH_IMPACT_HINTS:
            score += 9.0

        # 2. Proper Nouns (capitalized in original raw word)
        if raw_word and raw_word[0].isupper() and clean_word not in _CAPTION_STOPWORDS:
            score += 6.0

        # 3. Stopword penalty vs Length bonus
        if clean_word in _CAPTION_STOPWORDS:
            score -= 6.0
        else:
            score += min(len(clean_word) * 0.5, 4.0)

        # 4. Position bias: slightly prefer middle/end of chunk over very first word if scores are close
        if idx > 0:
            score += 0.2

        if score > best_score:
            best_score = score
            best_idx = idx

    return best_idx


_DANGLING_END_WORDS = {
    "to", "of", "in", "for", "with", "at", "by", "from", "on", "into", "onto",
    "the", "a", "an",
    "and", "or", "but", "nor", "so", "as",
    "is", "are", "was", "were", "has", "have", "had", "will", "would", "be", "been",
    "that", "this", "which", "their", "his", "her", "my", "your", "our", "its"
}


def _compute_next_word_group_end(words: List[Dict], start_idx: int, target_size: int) -> int:
    n = len(words)
    if start_idx >= n:
        return n
    limit = min(start_idx + max(1, target_size), n)
    if limit == n:
        return n

    # 1. Sentence punctuation boundary: break after terminal punctuation
    for idx in range(start_idx, limit):
        word_txt = str(words[idx].get("word") or "").strip()
        if word_txt and word_txt[-1] in ".!?":
            return idx + 1

    # 2. Clause boundary: break after comma or colon
    for idx in range(start_idx + 1, limit):
        word_txt = str(words[idx].get("word") or "").strip()
        if word_txt and word_txt[-1] in ",;:-–—":
            return idx + 1

    # 3. Avoid dangling prepositions / conjunctions / auxiliary at group end
    def _is_dangling(idx: int) -> bool:
        if idx < 0 or idx >= n:
            return False
        w = str(words[idx].get("word") or "").strip().lower().strip(".,;:?!\"'")
        return w in _DANGLING_END_WORDS

    if _is_dangling(limit - 1):
        # First preference: look forward up to +2 words to complete the phrase naturally
        for step in (1, 2):
            fwd = limit + step
            if fwd <= n:
                prev_txt = str(words[fwd - 1].get("word") or "").strip()
                if prev_txt and prev_txt[-1] in ".!?":
                    return fwd
                if not _is_dangling(fwd - 1):
                    return fwd

        # Second preference: step backwards to find non-dangling word
        for bwd in range(limit - 1, start_idx, -1):
            if not _is_dangling(bwd - 1):
                return bwd

    return limit


def generate_captions(
    audio_path: str,
    caption_style: Optional[str] = None,
    caption_mode: str = "smart_emphasis",
    words_per_group: int = 3,
    font: str = "Montserrat-Bold",
    fontsize: int = 48,
    bold: bool = True,
    uppercase: bool = True,
    text_color: str = "#FFFFFF",
    highlight_color: str = "#FFE600",
    outline_color: str = "#000000",
    outline_size: int = 4,
    shadow_on: bool = True,
    shadow_color: str = "#000000",
    shadow_depth: int = 2,
    bg_on: bool = False,
    bg_color: str = "#000000",
    bg_opacity: int = 80,
    position: str = "bottom",
    margin_v: int = 50,
    animation: str = "Pop",
    resolution: Optional[Tuple[int, int]] = None,
    precomputed_words: Optional[List[Dict]] = None,
) -> str:
    """
    Generate ASS subtitle file from audio with dynamic highlighting, style presets, and animations.

    Args:
        audio_path: Path to audio file
        caption_style: Preset style name (e.g. 'hormozi', 'mrbeast', 'cyberpunk', 'cinematic_minimal', 'vox_documentary', 'karaoke_box', 'classic')
        caption_mode: 'smart_emphasis' (dynamic best-word highlight), 'karaoke' (word-by-word active highlight), 'karaoke_box' (colored jumping box), 'single' (one word), 'line' (full sentence)
        ... styling parameters ...

    Returns:
        str: Path to generated ASS file
    """
    # If a caption_style preset is specified, apply its defaults
    if caption_style:
        style_key = str(caption_style).strip().lower().replace(" ", "_").replace("-", "_")
        if style_key in CAPTION_STYLES:
            preset = CAPTION_STYLES[style_key]
            caption_mode = preset.get("caption_mode", caption_mode)
            words_per_group = int(preset.get("words_per_group", words_per_group))
            font = preset.get("font", font)
            fontsize = int(preset.get("fontsize", fontsize))
            bold = bool(preset.get("bold", bold))
            uppercase = bool(preset.get("uppercase", uppercase))
            text_color = preset.get("text_color", text_color)
            highlight_color = preset.get("highlight_color", highlight_color)
            outline_color = preset.get("outline_color", outline_color)
            outline_size = int(preset.get("outline_size", outline_size))
            shadow_on = bool(preset.get("shadow_on", shadow_on))
            shadow_color = preset.get("shadow_color", shadow_color)
            shadow_depth = int(preset.get("shadow_depth", shadow_depth))
            bg_on = bool(preset.get("bg_on", bg_on))
            bg_color = preset.get("bg_color", bg_color)
            bg_opacity = int(preset.get("bg_opacity", bg_opacity))
            animation = preset.get("animation", animation)
            print(f"   [captions] Applying Caption Style Preset: '{preset.get('label', style_key)}'")

    # Get font family name
    font_raw = font
    font = get_font_name(font)
    print(f"   [captions] Font Mapping: '{font_raw}' -> '{font}' (mode={caption_mode}, anim={animation})")

    # Measured provider words already contain the timing evidence. Formatting
    # an optional sidecar must not load another model or disable those words
    # when the local transcription runtime is unavailable.
    if not precomputed_words and (not _available or _whisper_model is None):
        print("   [captions] Loading Whisper model...")
        init_whisper()

    if not precomputed_words and not _available:
        print("   [captions] Whisper not available - captions disabled")
        return None

    # Use precomputed words if available, otherwise transcribe
    if precomputed_words:
        words = [{**word, 'word': word.get('word') or word.get('text', '')}
                 for word in precomputed_words]
        segments = []
        # Reconstruct segments from words
        current_seg = None
        for w in words:
            seg_id = w.get("segment", 0)
            if current_seg is None or current_seg["id"] != seg_id:
                if current_seg:
                    segments.append(current_seg)
                current_seg = {"id": seg_id, "start": w["start"], "end": w["end"], "text": w.get("text", w.get("word", ""))}
            else:
                current_seg["end"] = w["end"]
                current_seg["text"] += " " + w.get("text", w.get("word", ""))
        if current_seg:
            segments.append(current_seg)
        print(f"   [captions] Using {len(words)} precomputed words ({len(segments)} segments)")
    else:
        words, segments = transcribe_audio(audio_path)
    if not words:
        print("   [captions] No words transcribed - check audio file")
        return None

    # Position alignment
    align = {"top": 8, "middle": 5, "bottom": 2}.get(position, 2)
    shadow = shadow_depth if shadow_on else 0
    shadow_ass = hex_to_ass(shadow_color).replace("&H00", "&H80")
    border_style = 3 if bg_on else 1
    bg_ass = hex_to_ass(bg_color).replace("&H00", f"&H{255 - int(bg_opacity * 2.55):02X}")
    bold_val = -1 if bold else 0

    # Custom style adjustments for karaoke_box
    if caption_mode == "karaoke_box":
        hl_border_style = 3
        hl_primary = hex_to_ass(text_color)
        hl_back = hex_to_ass(highlight_color).replace("&H00", f"&H{255 - int(bg_opacity * 2.55):02X}")
    else:
        hl_border_style = border_style
        hl_primary = hex_to_ass(highlight_color)
        hl_back = shadow_ass if shadow_on else bg_ass

    # ASS header
    res_x, res_y = (resolution[0], resolution[1]) if resolution else (1920, 1080)

    # Moderate documentary typography: avoid giant captions on 720p/1080p
    eff_fontsize = fontsize
    if fontsize == 48:
        eff_fontsize = 28 if res_y <= 720 else 40
    eff_outline_size = outline_size if outline_size != 4 else 2
    eff_margin_v = margin_v if margin_v != 50 else (36 if res_y <= 720 else 48)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {res_x}
PlayResY: {res_y}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{eff_fontsize},{hex_to_ass(text_color)},{hex_to_ass(text_color)},{hex_to_ass(outline_color)},{shadow_ass if shadow_on else bg_ass},{bold_val},0,0,0,100,100,0,0,{border_style},{eff_outline_size},{shadow},{align},100,100,{eff_margin_v},1
Style: Highlight,{font},{eff_fontsize},{hl_primary},{hl_primary},{hex_to_ass(outline_color)},{hl_back},{bold_val},0,0,0,100,100,0,0,{hl_border_style},{eff_outline_size + 1},{shadow},{align},100,100,{eff_margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = [header]
    anim = ANIMATIONS.get(animation, ANIMATIONS.get("Pop", ANIMATIONS["None"]))
    anim_tag = "{" + anim.get("in", "") + "}" if anim.get("in") else ""

    hl_bgr = hex_bgr(highlight_color)
    txt_bgr = hex_bgr(text_color)

    if caption_mode == "single":
        # One word at a time
        for w in words:
            txt = w["word"].upper() if uppercase else w["word"]
            start, end = w["start"], w["end"]
            if end <= start:
                end = start + 0.25
            lines.append(f"Dialogue: 0,{to_ass_time(start)},{to_ass_time(end)},Highlight,,0,0,0,,{anim_tag}{escape_ass(txt)}\n")

    elif caption_mode == "line":
        # Line mode: Show N words at a time (using words_per_group), clean subtitle
        n = len(words)
        i = 0
        while i < n:
            j = _compute_next_word_group_end(words, i, words_per_group)
            if j <= i:
                j = min(i + max(1, words_per_group), n)
            block = words[i:j]
            if not block:
                i = j
                continue

            start = block[0]["start"]
            end = block[-1]["end"]
            if j < n:
                next_start = words[j]["start"]
                if next_start > end and next_start - end < 0.35:
                    end = next_start
            if end <= start:
                end = start + 0.5

            txt_parts = []
            for w in block:
                txt = w["word"].upper() if uppercase else w["word"]
                txt_parts.append(escape_ass(txt))

            full_txt = " ".join(txt_parts)
            lines.append(f"Dialogue: 0,{to_ass_time(start)},{to_ass_time(end)},Default,,0,0,0,,{{\\q2}}{anim_tag}{full_txt}\n")
            i = j

    elif caption_mode == "smart_emphasis":
        # Smart Emphasis mode: Show group of words with the single highest-impact keyword highlighted
        n = len(words)
        i = 0
        while i < n:
            j = _compute_next_word_group_end(words, i, words_per_group)
            if j <= i:
                j = min(i + max(1, words_per_group), n)
            block = words[i:j]
            if not block:
                i = j
                continue

            start = block[0]["start"]
            end = block[-1]["end"]
            # Smooth gap to next word group
            if j < n:
                next_start = words[j]["start"]
                if next_start > end and next_start - end < 0.35:
                    end = next_start
            if end <= start:
                end = start + 0.3

            best_idx = select_best_highlight_word(block)
            parts = [anim_tag] if anim_tag else []
            for m, w in enumerate(block):
                txt = w["word"].upper() if uppercase else w["word"]
                if m == best_idx:
                    # Highlighted standout keyword with color accent
                    parts.append("{\\rHighlight}" + escape_ass(txt) + "{\\rDefault}")
                else:
                    parts.append(escape_ass(txt))
                if m < len(block) - 1:
                    parts.append(" ")

            lines.append(f"Dialogue: 0,{to_ass_time(start)},{to_ass_time(end)},Default,,0,0,0,,{''.join(parts)}\n")
            i = j

    elif caption_mode in ["karaoke", "karaoke_box"]:
        # Karaoke mode with word grouping: Show N words, highlight active spoken word in real-time
        n = len(words)
        i = 0
        while i < n:
            j = _compute_next_word_group_end(words, i, words_per_group)
            if j <= i:
                j = min(i + max(1, words_per_group), n)
            block = words[i:j]

            for k, active_word in enumerate(block):
                start = active_word["start"]
                end = active_word["end"]

                # Extend end time to next word if gap exists
                if k < len(block) - 1:
                    next_start = block[k + 1]["start"]
                    if next_start > end:
                        end = next_start
                elif j < n:
                    next_start = words[j]["start"]
                    if next_start > end and next_start - end < 0.3:
                        end = next_start

                if end <= start:
                    end = start + 0.2

                parts = [anim_tag] if (k == 0 and anim_tag) else []
                for m, w in enumerate(block):
                    txt = w["word"].upper() if uppercase else w["word"]
                    if m == k:
                        # Spoken active word
                        parts.append("{\\rHighlight}" + escape_ass(txt) + "{\\rDefault}")
                    else:
                        parts.append(escape_ass(txt))
                    if m < len(block) - 1:
                        parts.append(" ")

                lines.append(f"Dialogue: 0,{to_ass_time(start)},{to_ass_time(end)},Default,,0,0,0,,{''.join(parts)}\n")

            i = j

    else:  # "word" mode - grouped words with color switch
        n = len(words)
        i = 0
        while i < n:
            j = _compute_next_word_group_end(words, i, words_per_group)
            if j <= i:
                j = min(i + max(1, words_per_group), n)
            block = words[i:j]
            for k, w in enumerate(block):
                start, end = w["start"], w["end"]
                if end <= start:
                    end = start + 0.2
                parts = [anim_tag] if (k == 0 and anim_tag) else []
                for m, word in enumerate(block):
                    txt = word["word"].upper() if uppercase else word["word"]
                    if m == k:
                        parts.append("{\\rHighlight\\1c&H" + hl_bgr + "&}" + escape_ass(txt))
                    else:
                        parts.append("{\\rDefault\\1c&H" + txt_bgr + "&}" + escape_ass(txt))
                    if m < len(block) - 1:
                        parts.append(" ")
                lines.append(f"Dialogue: 0,{to_ass_time(start)},{to_ass_time(end)},Default,,0,0,0,,{''.join(parts)}\n")
            i = j

    # Save ASS file
    ass_hash = hashlib.sha256(str(time.time()).encode()).hexdigest()[:8]
    ass_path = TEMP_DIR / f"captions_{ass_hash}.ass"

    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write("".join(lines))

    print(f"   [captions] Captions generated: {ass_path.name}")
    return str(ass_path)
