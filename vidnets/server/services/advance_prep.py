"""Advance preparation: pre-generate scripts and pre-fetch + verify media
while the VM is idle, so a dispatched job reuses them instead of doing
that work live.

Wirings (the point of this module — it plugs into the real pipeline):
- Scripts: the prep script store (``prep_root()/scripts/<key>/``) uses the
  exact key the Node script adapter looks up before running ScriptIT live
  (``lib/prepScriptStore.js``; ``adapters/script.js:generateScript`` logs
  "prep script hit" / "prep script miss").
- Scene texts: derived with ``services.scene_splitter.fallback_split_by_text``
  — the SAME deterministic splitter the live audio-first pipeline uses when
  Whisper is unavailable (``services/audio_first_pipeline.py``).  When the
  live job gets Whisper scene texts, the narration hash differs and the
  media cache misses gracefully; the live job then fetches as before.
- Queries: derived with ``services.mechanical_query_bundle.
  build_mechanical_query_bundle`` — the SAME function the live job uses
  (``vidnets/server/app.py:_generate_mechanical_shared_scene_bundle``).
  Never reimplemented here.
- Web images: verified winners are persisted with the EXISTING historical
  asset cache (``services.web_image.resolver._persist_web_asset_cache``),
  stamped with a ``prep_key``; the live resolver checks that cache before
  fanning out scene workers (see ``_prep_cache_hits_for_scenes`` in the
  resolver).  A cached image still passes through the live visual pass.
- YouTube clips: verified cuts are stored with the EXISTING verified-cut
  cache (``services.youtube_media_cache.put_verified_cut``); the live
  pool renderer / pipeline already check it before downloading
  (``youtube_pool_renderer.py`` / ``youtube_pipeline_v2.py``).
- Pre-scaling: ``services.media_prescale.ensure_scaled``; scaled paths are
  recorded in the per-title manifest.

All cache writes are atomic (temp file + rename) and every prep step is
fail-soft: a prep failure is recorded in the manifest and never breaks a
live job, which simply redoes the work live.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Roots / paths
# ---------------------------------------------------------------------------

_DEFAULT_RUNTIME_TEMP = Path.home() / "workspace" / "vidnets-runtime" / "temp"
_DEFAULT_LIVE_REPO = Path.home() / "workspace" / "M2l"


def runtime_temp_dir() -> Path:
    """The live runtime temp dir (VIDZEO_TEMP_DIR; same as start-daemon.sh)."""
    return Path(os.environ.get("VIDZEO_TEMP_DIR") or _DEFAULT_RUNTIME_TEMP)


def live_repo_root() -> Path:
    """The live (running) repo checkout — used only to resolve LIVE cache
    roots.  Defaults to ~/workspace/M2l; override with VIDZEO_LIVE_REPO_ROOT."""
    return Path(os.environ.get("VIDZEO_LIVE_REPO_ROOT") or _DEFAULT_LIVE_REPO)


def prep_root() -> Path:
    """Root for all prep state. Override with VIDZEO_PREP_ROOT."""
    return Path(os.environ.get("VIDZEO_PREP_ROOT") or (runtime_temp_dir() / "prep"))


def prep_queue_path() -> Path:
    """Local queue file prep watches (no CMS peek endpoint exists — see
    docs/ADVANCE_PREP.md). Override with VIDZEO_PREP_QUEUE."""
    if os.environ.get("VIDZEO_PREP_QUEUE"):
        return Path(os.environ["VIDZEO_PREP_QUEUE"])
    return live_repo_root() / "work" / "prep-queue.json"


def queue_json_path() -> Path:
    return Path(
        os.environ.get("VIDZEO_QUEUE_JSON")
        or (live_repo_root().parent / "vidnets-runtime" / "server" / "queue.json")
    )


def pool_status_path() -> Path:
    return Path(
        os.environ.get("VIDZEO_POOL_STATUS_JSON")
        or (runtime_temp_dir() / "inference-spool" / "pool-status.json")
    )


def active_job_path() -> Path:
    return Path(
        os.environ.get("VIDZEO_ACTIVE_JOB_JSON")
        or (live_repo_root() / "work" / "active-job.json")
    )


def youtube_cut_cache_dir() -> Path:
    """The LIVE verified-cut cache dir.  The live code resolves it from
    VIDZEO_YT_CUT_CACHE_DIR, else VIDZEO_ROOT, else <repo>/vidnets/temp;
    mirror that here against the LIVE repo root (this worktree's own
    vidnets/temp is NOT the live cache)."""
    if os.environ.get("VIDZEO_YT_CUT_CACHE_DIR"):
        return Path(os.environ["VIDZEO_YT_CUT_CACHE_DIR"])
    if os.environ.get("VIDZEO_ROOT"):
        return Path(os.environ["VIDZEO_ROOT"]) / "temp" / "youtube" / "verified_cut_cache"
    return live_repo_root() / "vidnets" / "temp" / "youtube" / "verified_cut_cache"


# ---------------------------------------------------------------------------
# Normalization + cache keys
# ---------------------------------------------------------------------------

def normalize_title(title: str) -> str:
    text = re.sub(r"\s+", " ", str(title or "")).strip().casefold()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def narration_hash(narration: str) -> str:
    return hashlib.sha256(normalize_text(narration).encode("utf-8")).hexdigest()


def prep_cache_key(title: str, narration: str, query: str) -> str:
    """Cache key for one prepped media entry.

    (normalized title, scene narration hash, query string).  The live web
    resolver and the prep runner both call THIS function — one
    implementation, so a key computed in prep equals the key computed live
    for the same triple.
    """
    material = {
        "title": normalize_title(title),
        "narration_sha256": narration_hash(narration),
        "query": normalize_text(query).casefold(),
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def title_key(title: str, duration_minutes: float) -> str:
    """Stable key for one title+duration (script store + manifest dir)."""
    material = {"title": normalize_title(title), "duration_minutes": float(duration_minutes or 0)}
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Atomic JSON helpers
# ---------------------------------------------------------------------------

def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{int(time.time() * 1000)}.tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return True
    except Exception:
        return False


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Idle gate
# ---------------------------------------------------------------------------

# Queue item statuses that mean "a job is live" (services/queue_manager.py:
# JobStatus).  Everything else (completed / cancelled / error) is idle.
_BUSY_QUEUE_STATUSES = {
    "queued", "processing", "cancellation_requested", "paused", "shutdown_timeout",
}

# A render only happens inside a job (daemon job or server queue job), so
# "no render in progress" is covered by the queue + active-job checks plus
# a narrow live-process check for a render wrapper that outlived its job.
_RENDER_PROCESS_PATTERNS = ("render-vidnets-linux.js", "remotion")


def _read_pool_pending(path: Path) -> Tuple[Optional[int], Optional[str]]:
    """Return (pending, error). pending None means the file is unusable."""
    try:
        data = _read_json(path)
    except Exception:
        return None, "unreadable"
    if data is None:
        return None, "unreadable"
    try:
        pending = int(data.get("pending"))
    except (TypeError, ValueError):
        return None, "no pending field"
    updated_at = data.get("updatedAt")
    if updated_at:
        try:
            age = time.time() - (float(updated_at) / 1000.0)
        except (TypeError, ValueError):
            age = None
        if age is not None and age > 600:
            return None, f"stale ({age:.0f}s old)"
    return pending, None


def _render_process_running(process_list: Optional[List[str]] = None) -> Optional[str]:
    """Best-effort render detection.  ``process_list`` (cmdlines) is
    injectable for tests; when None, ps is consulted."""
    if process_list is None:
        try:
            out = subprocess.run(
                ["ps", "-eo", "args"], capture_output=True, text=True, timeout=10
            ).stdout.splitlines()
        except Exception:
            return None
        process_list = out
    live_root = str(live_repo_root())
    for line in process_list:
        text = str(line or "")
        if "render-vidnets-linux.js" in text and live_root in text:
            return text.strip()[:200]
        if "remotion" in text and "render" in text and live_root in text:
            return text.strip()[:200]
    return None


def is_idle(
    *,
    queue_path: Optional[Path] = None,
    pool_status_file: Optional[Path] = None,
    active_job_file: Optional[Path] = None,
    process_list: Optional[List[str]] = None,
    check_processes: bool = True,
) -> Tuple[bool, List[str]]:
    """True when no job is active and prep may run.

    Gates (all must pass; fail closed — an unreadable signal means busy):
    - server queue (queue.json): no item in a busy status;
    - daemon active job (active-job.json): absent;
    - inference pool (pool-status.json): pending == 0 and fresh;
    - no render wrapper process running.
    Returns (idle, reasons).  ``reasons`` is empty when idle.
    """
    reasons: List[str] = []
    qpath = Path(queue_path) if queue_path is not None else queue_json_path()
    try:
        queue = json.loads(qpath.read_text(encoding="utf-8"))
        items = queue.get("items") if isinstance(queue, dict) else None
        if not isinstance(items, list):
            reasons.append(f"queue file {qpath} has no items list")
        else:
            busy = [
                str(item.get("id") or "?") + ":" + str(item.get("status") or "?")
                for item in items
                if isinstance(item, dict) and str(item.get("status") or "") in _BUSY_QUEUE_STATUSES
            ]
            if busy:
                reasons.append(f"queue busy: {', '.join(busy[:5])}")
    except FileNotFoundError:
        reasons.append(f"queue file missing: {qpath}")
    except Exception as exc:
        reasons.append(f"queue file unreadable: {type(exc).__name__}")

    apath = Path(active_job_file) if active_job_file is not None else active_job_path()
    try:
        if apath.is_file():
            data = _read_json(apath)
            if data and data.get("jobId"):
                reasons.append(f"daemon job active: {data.get('jobId')}")
            elif data:
                reasons.append(f"daemon active-job file present: {apath}")
    except Exception as exc:
        reasons.append(f"active-job check failed: {type(exc).__name__}")

    ppath = Path(pool_status_file) if pool_status_file is not None else pool_status_path()
    pending, error = _read_pool_pending(ppath)
    if error is not None:
        reasons.append(f"pool status {error}: {ppath}")
    elif pending and pending > 0:
        reasons.append(f"inference pool busy: pending={pending}")

    if check_processes:
        hit = _render_process_running(process_list)
        if hit:
            reasons.append(f"render process running: {hit}")

    return (not reasons), reasons


# ---------------------------------------------------------------------------
# Script store (shared layout with lib/prepScriptStore.js — keep in sync)
# ---------------------------------------------------------------------------

def script_store_dir(title: str, duration_minutes: float) -> Path:
    return prep_root() / "scripts" / title_key(title, duration_minutes)


def find_prepped_script(title: str, duration_minutes: float) -> Optional[Dict[str, Any]]:
    """Return {script_text, meta, path} for a pre-generated script, or None."""
    directory = script_store_dir(title, duration_minutes)
    script_path = directory / "script.txt"
    try:
        text = script_path.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if not text:
        return None
    meta = _read_json(directory / "meta.json") or {}
    stored_title = normalize_title(str(meta.get("title") or ""))
    if stored_title and stored_title != normalize_title(title):
        return None
    if meta.get("duration_minutes") is not None:
        try:
            if float(meta["duration_minutes"]) != float(duration_minutes or 0):
                return None
        except (TypeError, ValueError):
            return None
    return {"script_text": text, "meta": meta, "path": str(script_path)}


def save_prepped_script(
    title: str, duration_minutes: float, script_text: str, meta: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """Atomically store a pre-generated script. Returns the path or None."""
    text = str(script_text or "").strip()
    if not text:
        return None
    directory = script_store_dir(title, duration_minutes)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        script_path = directory / "script.txt"
        tmp = script_path.with_name(f".script.txt.{os.getpid()}.tmp")
        try:
            tmp.write_text(text + "\n", encoding="utf-8")
            os.replace(tmp, script_path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        payload = dict(meta or {})
        payload.update({
            "title": str(title or ""),
            "duration_minutes": float(duration_minutes or 0),
            "words": len(text.split()),
            "engine": str((meta or {}).get("engine") or "scriptit"),
            "prepped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        if not _write_json_atomic(directory / "meta.json", payload):
            return None
        return str(script_path)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Scene texts + query derivation (SAME functions the live job uses)
# ---------------------------------------------------------------------------

def derive_scene_texts(script: str, duration_seconds: float) -> List[str]:
    """Split a script into scene texts the way the live pipeline does when
    Whisper is unavailable (services/audio_first_pipeline.py calls
    fallback_split_by_text with mode "Every Beat"). Deterministic on the
    script text, so prep and a Whisper-less live job derive identical
    scene texts."""
    from services.scene_splitter import fallback_split_by_text

    scenes = fallback_split_by_text(
        str(script or ""), "Every Beat", float(duration_seconds or 0), emit_log=False
    )
    return [str(scene.text or "").strip() for scene in (scenes or []) if str(getattr(scene, "text", "") or "").strip()]


def derive_query_bundle(title: str, scene_texts: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Build web/yt/stock query rows with the SAME derivation the live job
    uses (vidnets/server/app.py:_generate_mechanical_shared_scene_bundle):
    section subjects via detect_list_sections + canonicalize_sections, then
    services.mechanical_query_bundle.build_mechanical_query_bundle."""
    scene_texts = [str(text or "") for text in (scene_texts or [])]
    try:
        from services.semantic_query_agent import detect_list_sections

        sections = detect_list_sections(list(scene_texts), str(title or ""))
    except Exception:
        sections = []
    try:
        from services.container_guard import canonicalize_sections

        sections = canonicalize_sections(sections, scene_texts=list(scene_texts), topic=str(title or ""))
    except Exception:
        pass
    subjects: List[str] = []
    for idx, _text in enumerate(scene_texts):
        row = sections[idx] if idx < len(sections) and isinstance(sections[idx], dict) else {}
        subject = "" if row.get("is_intro") else str(row.get("container_subject") or "").strip()
        if str(row.get("container_id") or "") == "outro":
            subject = ""
        subjects.append(subject)
    from services.mechanical_query_bundle import build_mechanical_query_bundle

    return build_mechanical_query_bundle(
        title=str(title or ""),
        scene_texts=scene_texts,
        section_subjects=subjects,
        sections=sections,
    )


# ---------------------------------------------------------------------------
# Web image prep cache (wraps the existing historical asset cache)
# ---------------------------------------------------------------------------

def _resolver():
    from services.web_image import resolver

    return resolver


def _asset_cache_key(row: Dict[str, Any]) -> str:
    """Dedupe key the historical asset cache uses (mirrors
    ``resolver._persist_web_asset_cache``): one row per source URL/path.
    Prep records one row per query variant against the same asset, so the
    surviving row must carry ALL variant keys (see
    ``record_prepped_web_asset``)."""
    return str(row.get("source_url") or row.get("path") or "").casefold()


def find_prepped_web_asset(
    title: str, narration: str, queries: Sequence[str]
) -> Optional[Dict[str, Any]]:
    """Return a cached verified web asset for (title, narration, query),
    trying each query in order.  The live resolver calls this with its own
    (possibly repaired) query list for the scene.

    Exact pass first: the (title, narration, query) triple keyed at prep
    time.  Run-6 proof (job 453e5590) showed the exact pass can miss every
    scene even with 48 banked entries: the live job titles the branch
    "Grand Theft Auto VI" while prep recorded "Grand Theft Auto VI: Return
    to Vice City", and the live 13-scene segmentation merges prep's
    24-scene narrations, so neither the title nor the narration hash ever
    matches.  The fuzzy pass below then matches on the title family plus
    discriminating narration/query tokens, so a banked verified image is
    reachable again.  A fuzzy hit is flagged (``prep_fuzzy_match``) and
    still flows through the live visual pass; it can only return an asset
    verified for this title family.
    """
    resolver = _resolver()
    try:
        rows = resolver._load_historical_web_assets()
    except Exception:
        return None
    wanted = {prep_cache_key(title, narration, query) for query in (queries or []) if str(query or "").strip()}
    if not wanted and not str(narration or "").strip():
        return None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        # One row per asset carries every variant key it was recorded under
        # (``prep_keys``); older rows carry only the single ``prep_key``.
        stored = {str(row.get("prep_key") or "")}
        stored |= {str(key) for key in (row.get("prep_keys") or []) if key}
        if not (stored & wanted):
            continue
        path = Path(str(row.get("path") or ""))
        if path.is_file():
            return dict(row)
    fuzzy = _find_prepped_web_asset_fuzzy(title, narration, queries, rows or [])
    if fuzzy is not None:
        return fuzzy
    # Legacy rows recorded before prep_queries existed carry only the
    # generic last variant as prep_query, so the cache-row fuzzy pass
    # scores 0 on them; the per-title manifest still holds each entry's
    # discriminating query and scene narration.
    return _find_prepped_web_manifest_hit(title, narration, queries, rows or [])


_PREP_FUZZY_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "after", "before",
    "when", "then", "into", "over", "under", "about", "which", "their",
    "there", "here", "have", "been", "were", "will", "would", "could",
    "also", "more", "most", "some", "such", "than", "them", "they",
    "photo", "photograph", "image", "picture", "screenshot", "documentary",
    "official", "gameplay",
})


def _prep_fuzzy_tokens(text: str) -> set:
    """Content tokens for fuzzy prep matching (lowercase, len>=3, no stops)."""
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(text or "").casefold())
        if len(token) >= 3 and token not in _PREP_FUZZY_STOPWORDS
    }


def _prep_titles_compatible(stored_title: str, live_title: str) -> bool:
    """Same title family: equal, one a prefix of the other (a subtitle was
    dropped, e.g. "Grand Theft Auto VI" vs "...: Return to Vice City"),
    or the same leading three tokens."""
    a = normalize_title(stored_title)
    b = normalize_title(live_title)
    if not a or not b:
        return False
    if a == b or a.startswith(b) or b.startswith(a):
        return True
    a_tokens = a.split()
    b_tokens = b.split()
    return (
        len(a_tokens) >= 3
        and len(b_tokens) >= 3
        and a_tokens[:3] == b_tokens[:3]
    )


def _find_prepped_web_asset_fuzzy(
    title: str, narration: str, queries: Sequence[str], rows: Sequence[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Fallback prep match when the exact (title, narration, query) key
    misses: same title family plus discriminating token overlap between
    the live narration/queries and the stored prep query.

    Requires at least two discriminating shared tokens (title tokens
    excluded), so a bare title match or a generic-word overlap can never
    wrong-hit another scene's image.  Rows recorded before this change
    carry no narration text, so they are scored on the stored prep query
    alone; newer rows also get a narration-overlap bonus.
    """
    live_title_tokens = _prep_fuzzy_tokens(title)
    narration_tokens = _prep_fuzzy_tokens(narration) - live_title_tokens
    live_query_tokens: set = set()
    for query in queries or []:
        live_query_tokens |= _prep_fuzzy_tokens(query)
    live_query_tokens -= live_title_tokens
    if not narration_tokens and not live_query_tokens:
        return None
    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        stored_title = str(row.get("prep_title") or row.get("title") or "")
        if not _prep_titles_compatible(stored_title, title):
            continue
        path = Path(str(row.get("path") or ""))
        if not path.is_file():
            continue
        stored_title_tokens = _prep_fuzzy_tokens(stored_title)
        stored_queries = [
            str(query_text)
            for query_text in (row.get("prep_queries") or [])
            if str(query_text or "").strip()
        ]
        if not stored_queries and row.get("prep_query"):
            stored_queries = [str(row.get("prep_query"))]
        prep_query_tokens: set = set()
        for stored_query in stored_queries:
            prep_query_tokens |= _prep_fuzzy_tokens(stored_query)
        prep_query_tokens -= stored_title_tokens
        if not prep_query_tokens:
            continue
        narration_hit = narration_tokens & prep_query_tokens
        query_hit = live_query_tokens & prep_query_tokens
        score = float(len(narration_hit)) + 0.5 * float(len(query_hit))
        stored_narration = str(row.get("prep_narration") or "").strip()
        if stored_narration:
            stored_narration_tokens = (
                _prep_fuzzy_tokens(stored_narration) - stored_title_tokens
            )
            union = narration_tokens | stored_narration_tokens
            if union:
                score += 3.0 * (
                    len(narration_tokens & stored_narration_tokens) / len(union)
                )
        if score >= 2.0 and score > best_score:
            best = row
            best_score = score
    if best is None:
        return None
    hit = dict(best)
    hit["prep_fuzzy_match"] = True
    hit["prep_fuzzy_score"] = round(best_score, 2)
    return hit


def _load_manifest_for_title_family(title: str) -> Dict[str, Any]:
    """Prep manifest whose title is in the same family as ``title``
    (see :func:`_prep_titles_compatible`), ignoring duration.

    The exact-title scan (:func:`load_manifest_for_title`) misses when
    the live job shortens the title ("Grand Theft Auto VI" vs the prepped
    "Grand Theft Auto VI: Return to Vice City").  Zero or ambiguous (>1)
    matches => ``{}``: an ambiguous match must never silently pick
    another title's images.  Never raises.
    """
    try:
        titles_dir = prep_root() / "titles"
        if not titles_dir.is_dir():
            return {}
        matches = []
        for manifest_file in sorted(titles_dir.glob("*/manifest.json")):
            manifest = _read_json(manifest_file)
            if not manifest:
                continue
            if _prep_titles_compatible(
                str(manifest.get("title") or ""), title
            ):
                matches.append(manifest)
        if len(matches) == 1:
            return matches[0]
        return {}
    except Exception:
        return {}


def _find_prepped_web_manifest_hit(
    title: str, narration: str, queries: Sequence[str],
    rows: Optional[Sequence[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Fallback prep match against the per-title prep MANIFEST (not the
    historical asset cache).

    Run-6 proof: the 24 banked GTA cache rows all carry the generic last
    variant as ``prep_query`` ("Grand Theft Auto VI official image"), so
    the cache-row fuzzy pass scores 0 on every live scene even though the
    manifest still records each entry's discriminating query and scene
    narration.  Score each manifest web entry on narration overlap with
    its prep scene text plus query-token overlap with its recorded query;
    require at least two discriminating shared tokens (or a narration
    Jaccard >= 0.5 on its own).  Only entries whose file still exists
    can hit.  A hit is flagged like any fuzzy hit and still flows
    through the live visual pass.

    Test-11 repair: a manifest entry's recorded path is the prep run's
    disposable temp file, which is cleaned after the job — all 48
    entries of the GTA manifest point at deleted files while the
    verified bytes still live in the stable asset cache (every winner
    is copied there by ``_persist_web_asset_cache``).  An entry whose
    own path is gone therefore resolves through the cache by its
    ``prep_key`` (``rows``, the already-loaded cache rows, keyed on
    ``prep_key``/``prep_keys``); only an entry with neither a live file
    nor a live cache copy is skipped.  A cache-resolved hit is flagged
    (``prep_cache_resolved``) and returns the cache row, so the caller
    gets bytes that exist.
    """
    manifest = _load_manifest_for_title_family(title)
    if not manifest:
        return None
    if rows is None:
        try:
            rows = _resolver()._load_historical_web_assets()
        except Exception:
            rows = []
    cache_by_key: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for key in [row.get("prep_key")] + list(row.get("prep_keys") or []):
            if key:
                cache_by_key.setdefault(str(key), row)
    live_title_tokens = _prep_fuzzy_tokens(title)
    narration_tokens = _prep_fuzzy_tokens(narration) - live_title_tokens
    live_query_tokens: set = set()
    for query in queries or []:
        live_query_tokens |= _prep_fuzzy_tokens(query)
    live_query_tokens -= live_title_tokens
    if not narration_tokens and not live_query_tokens:
        return None
    scenes = {
        int(scene.get("index") or 0): str(scene.get("text") or "")
        for scene in (manifest.get("scenes") or [])
        if isinstance(scene, dict)
    }
    stored_title_tokens = _prep_fuzzy_tokens(
        str(manifest.get("title") or "")
    )
    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    best_cache_row: Optional[Dict[str, Any]] = None
    best_path = ""
    for entry in (manifest.get("web") or {}).get("entries") or []:
        if not isinstance(entry, dict):
            continue
        entry_path = Path(str(entry.get("path") or ""))
        cache_row: Optional[Dict[str, Any]] = None
        if entry_path.is_file():
            resolved_path = entry_path
        else:
            # Disposable prep-run path is gone: resolve the verified
            # bytes through the stable asset cache by prep_key.
            cache_row = cache_by_key.get(str(entry.get("prep_key") or ""))
            if cache_row is None:
                continue
            cached_path = Path(str(cache_row.get("path") or ""))
            if not cached_path.is_file():
                continue
            resolved_path = cached_path
        entry_query_tokens = (
            _prep_fuzzy_tokens(entry.get("query") or "") - stored_title_tokens
        )
        scene_text = scenes.get(int(entry.get("scene") or 0), "")
        scene_tokens = (
            _prep_fuzzy_tokens(scene_text) - stored_title_tokens
            if scene_text
            else set()
        )
        score = 0.0
        if scene_tokens:
            union = narration_tokens | scene_tokens
            if union:
                jaccard = len(narration_tokens & scene_tokens) / len(union)
                score += 3.0 * jaccard
        score += float(len(narration_tokens & entry_query_tokens))
        score += 0.5 * float(len(live_query_tokens & entry_query_tokens))
        qualifying = score >= 2.0 or (
            scene_tokens
            and narration_tokens
            and len(narration_tokens & scene_tokens)
            / len(narration_tokens | scene_tokens)
            >= 0.5
        )
        if qualifying and score > best_score:
            best = entry
            best_score = score
            best_cache_row = cache_row
            best_path = str(resolved_path)
    if best is None:
        return None
    if best_cache_row is not None:
        hit = dict(best_cache_row)
        hit["path"] = best_path
        hit["prep_query"] = normalize_text(
            str(best.get("query") or hit.get("prep_query") or "")
        )
        hit["prep_fuzzy_match"] = True
        hit["prep_fuzzy_score"] = round(best_score, 2)
        hit["prep_manifest_hit"] = True
        hit["prep_cache_resolved"] = True
        return hit
    return {
        "path": best_path,
        "source_url": str(best.get("path") or ""),
        "title": str(manifest.get("title") or title),
        "prep_title": normalize_title(str(manifest.get("title") or title)),
        "prep_query": normalize_text(str(best.get("query") or "")),
        "prep_key": str(best.get("prep_key") or ""),
        "prep_fuzzy_match": True,
        "prep_fuzzy_score": round(best_score, 2),
        "prep_manifest_hit": True,
    }


def record_prepped_web_asset(
    title: str, narration: str, query: str, asset: Dict[str, Any]
) -> bool:
    """Persist a verified web winner under its prep key, using the existing
    historical asset cache (same file the live resolver reads).  The cache
    dedupes rows by source URL, so recording one entry per query variant
    would otherwise keep only the LAST variant's key; the surviving row
    therefore accumulates every variant key in ``prep_keys``."""
    resolver = _resolver()
    row = dict(asset or {})
    new_key = prep_cache_key(title, narration, query)
    row["prep_key"] = new_key
    row["prep_title"] = normalize_title(title)
    row["prep_narration_sha256"] = narration_hash(narration)
    # Narration text (not just its hash) so the fuzzy fallback in
    # find_prepped_web_asset can score narration overlap when the exact
    # key misses (live re-segmentation changes the hash outright).
    row["prep_narration"] = normalize_text(narration)
    row["prep_query"] = normalize_text(query)
    # All variant query texts (not just the last one recorded): the fuzzy
    # fallback scores on these tokens.  Run-6 proof: every one of the 24
    # banked GTA rows carried prep_query "Grand Theft Auto VI official
    # image" — the last variant overwrote the discriminating query, so
    # the fuzzy pass saw only title tokens and missed 13/13 live scenes.
    try:
        kept_queries = {normalize_text(query)}
        for existing in resolver._load_historical_web_assets() or []:
            if not isinstance(existing, dict):
                continue
            if _asset_cache_key(existing) != _asset_cache_key(row):
                continue
            for stored_query in (existing.get("prep_queries") or []):
                if stored_query:
                    kept_queries.add(normalize_text(str(stored_query)))
            if existing.get("prep_query"):
                kept_queries.add(normalize_text(str(existing["prep_query"])))
        if len(kept_queries) > 1:
            row["prep_queries"] = sorted(kept_queries)
    except Exception:
        pass
    try:
        kept = {new_key}
        for existing in resolver._load_historical_web_assets() or []:
            if not isinstance(existing, dict):
                continue
            if _asset_cache_key(existing) != _asset_cache_key(row):
                continue
            if existing.get("prep_key"):
                kept.add(str(existing["prep_key"]))
            for key in (existing.get("prep_keys") or []):
                if key:
                    kept.add(str(key))
        if len(kept) > 1:
            row["prep_keys"] = sorted(kept)
    except Exception:
        pass
    try:
        subjects = list(row.get("subjects") or [])
        resolver._persist_web_asset_cache(
            [row], subjects, str(row.get("active_subject") or "")
        )
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# YouTube prep (verified-cut cache)
# ---------------------------------------------------------------------------

def find_verified_cut(video_id: str, start: float, duration: float) -> Optional[Dict[str, Any]]:
    from services.youtube_media_cache import get_verified_cut

    try:
        return get_verified_cut(str(video_id or ""), float(start or 0.0), float(duration or 0.0))
    except Exception:
        return None


def record_verified_cut(
    video_id: str, start: float, duration: float, source_path: str, metadata: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    from services.youtube_media_cache import put_verified_cut

    try:
        return put_verified_cut(
            str(video_id or ""), float(start or 0.0), float(duration or 0.0),
            str(source_path or ""), metadata=metadata or {},
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def manifest_path(title: str, duration_minutes: float) -> Path:
    return prep_root() / "titles" / title_key(title, duration_minutes) / "manifest.json"


def load_manifest(title: str, duration_minutes: float) -> Dict[str, Any]:
    return _read_json(manifest_path(title, duration_minutes)) or {}


def load_manifest_for_title(title: str) -> Dict[str, Any]:
    """Find a prep manifest by normalized title, ignoring duration.

    The live YouTube lane knows the job title but not the
    ``duration_minutes`` the prep run was keyed on, so it cannot use
    :func:`title_key`.  Scan ``titles/*/manifest.json`` and match on the
    normalized title (same precedent as :func:`note_reuse_by_title`).
    Zero or ambiguous (>1) matches => ``{}``: an ambiguous match must
    never silently pick another duration's cuts.  Never raises.
    """
    try:
        titles_dir = prep_root() / "titles"
        if not titles_dir.is_dir():
            return {}
        wanted = normalize_title(title)
        if not wanted:
            return {}
        matches = []
        for manifest_file in sorted(titles_dir.glob("*/manifest.json")):
            manifest = _read_json(manifest_file)
            if not manifest:
                continue
            if normalize_title(str(manifest.get("title") or "")) != wanted:
                continue
            matches.append(manifest)
        if len(matches) == 1:
            return matches[0]
        return {}
    except Exception:
        return {}


def save_manifest(title: str, duration_minutes: float, manifest: Dict[str, Any]) -> bool:
    return _write_json_atomic(manifest_path(title, duration_minutes), manifest)


def new_manifest(title: str, duration_minutes: float, channel: str = "") -> Dict[str, Any]:
    return {
        "schema": "vidzeo_prep_manifest_v1",
        "title": str(title or ""),
        "title_key": title_key(title, duration_minutes),
        "duration_minutes": float(duration_minutes or 0),
        "channel": str(channel or ""),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "in_progress",
        "script": None,
        "scenes": [],
        "web": {"attempted": 0, "verified": 0, "entries": []},
        "youtube": {"attempted": 0, "verified": 0, "entries": []},
        "scaled": [],
        "reuse_log": [],
        "errors": [],
    }


def note_reuse(title: str, duration_minutes: float, kind: str, detail: Dict[str, Any]) -> None:
    """Best-effort: record that a live job reused a prepped entry. Never
    raises — the live job must not fail on manifest bookkeeping."""
    try:
        manifest = load_manifest(title, duration_minutes)
        if not manifest:
            return
        manifest.setdefault("reuse_log", []).append({
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "kind": str(kind or ""),
            **(detail or {}),
        })
        manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        save_manifest(title, duration_minutes, manifest)
    except Exception:
        pass


def note_reuse_by_title(title: str, kind: str, detail: Dict[str, Any]) -> None:
    """Best-effort: record a live reuse in the manifest for this title,
    found by scanning title manifests (callers like the web resolver do
    not know the job's duration_minutes). Never raises."""
    try:
        titles_dir = prep_root() / "titles"
        if not titles_dir.is_dir():
            return
        wanted = normalize_title(title)
        for manifest_file in titles_dir.glob("*/manifest.json"):
            manifest = _read_json(manifest_file)
            if not manifest or normalize_title(str(manifest.get("title") or "")) != wanted:
                continue
            manifest.setdefault("reuse_log", []).append({
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "kind": str(kind or ""),
                **(detail or {}),
            })
            manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _write_json_atomic(manifest_file, manifest)
            return
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Prep queue
# ---------------------------------------------------------------------------

def load_prep_queue(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Read the local prep queue.  Format: {"titles": [{title, duration,
    channel}]} or a bare list of the same entries.  Missing file => []."""
    qpath = Path(path) if path is not None else prep_queue_path()
    try:
        data = json.loads(qpath.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        entries = data.get("titles") or []
    elif isinstance(data, list):
        entries = data
    else:
        return []
    out = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        try:
            duration = float(entry.get("duration") or entry.get("duration_minutes") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        out.append({
            "title": title,
            "duration_minutes": duration or 10.0,
            "channel": str(entry.get("channel") or ""),
            "description": str(entry.get("description") or ""),
        })
    return out


# ---------------------------------------------------------------------------
# Prep orchestration (called by scripts/prep/prep-runner.py)
# ---------------------------------------------------------------------------

def _log(message: str) -> None:
    print(f"[prep] {message}", flush=True)


def prep_title(
    entry: Dict[str, Any],
    *,
    should_stop: Optional[Callable[[], bool]] = None,
    script_command: Optional[Sequence[str]] = None,
    skip_youtube: bool = False,
    skip_web: bool = False,
) -> Dict[str, Any]:
    """Prep one title: script (ScriptIT CLI) + media (web + YouTube).
    Returns the manifest.  ``should_stop`` is polled between scenes; when
    it fires, prep aborts cleanly and the manifest records "aborted" with
    whatever is already consistent on disk."""
    title = str(entry.get("title") or "").strip()
    duration_minutes = float(entry.get("duration_minutes") or 10.0)
    channel = str(entry.get("channel") or "")
    manifest = load_manifest(title, duration_minutes)
    if not manifest:
        manifest = new_manifest(title, duration_minutes, channel)
    manifest["status"] = "in_progress"
    manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_manifest(title, duration_minutes, manifest)

    def stopped() -> bool:
        try:
            return bool(should_stop and should_stop())
        except Exception:
            return False

    # ---- script ----
    existing = find_prepped_script(title, duration_minutes)
    if existing:
        manifest["script"] = {
            "path": existing["path"], "words": len(existing["script_text"].split()),
            "source": "prep-cache", "prepped_at": str((existing["meta"] or {}).get("prepped_at") or ""),
        }
        script_text = existing["script_text"]
        _log(f"script cache hit for '{title}'")
    else:
        script_text = _generate_script_via_cli(title, duration_minutes, entry, script_command)
        if script_text:
            path = save_prepped_script(title, duration_minutes, script_text, {"engine": "scriptit"})
            manifest["script"] = {
                "path": path, "words": len(script_text.split()),
                "source": "scriptit", "prepped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        else:
            manifest["errors"].append("script generation failed")
            manifest["status"] = "failed"
            save_manifest(title, duration_minutes, manifest)
            return manifest
    save_manifest(title, duration_minutes, manifest)
    if stopped():
        manifest["status"] = "aborted"
        save_manifest(title, duration_minutes, manifest)
        return manifest

    # ---- scenes + queries (same derivation as the live job) ----
    duration_seconds = max(60.0, duration_minutes * 60.0)
    try:
        scene_texts = derive_scene_texts(script_text, duration_seconds)
    except Exception as exc:
        manifest["errors"].append(f"scene split failed: {type(exc).__name__}: {exc}")
        manifest["status"] = "failed"
        save_manifest(title, duration_minutes, manifest)
        return manifest
    try:
        bundle = derive_query_bundle(title, scene_texts)
    except Exception as exc:
        manifest["errors"].append(f"query derivation failed: {type(exc).__name__}: {exc}")
        manifest["status"] = "failed"
        save_manifest(title, duration_minutes, manifest)
        return manifest
    manifest["scenes"] = [
        {"index": idx + 1, "text": text, "words": len(text.split())}
        for idx, text in enumerate(scene_texts)
    ]
    save_manifest(title, duration_minutes, manifest)

    # ---- web images (live resolver, then persist winners under prep keys) ----
    if not skip_web:
        _prep_web_media(title, scene_texts, bundle, manifest, stopped)
        save_manifest(title, duration_minutes, manifest)
    if stopped():
        manifest["status"] = "aborted"
        save_manifest(title, duration_minutes, manifest)
        return manifest

    # ---- YouTube clips (live search/transcript/moment/download, verified cuts) ----
    if not skip_youtube:
        _prep_youtube_media(title, scene_texts, bundle, manifest, stopped)
        save_manifest(title, duration_minutes, manifest)
    if stopped():
        manifest["status"] = "aborted"
        save_manifest(title, duration_minutes, manifest)
        return manifest

    manifest["status"] = "ready" if not manifest.get("errors") else "partial"
    manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_manifest(title, duration_minutes, manifest)
    return manifest


def _generate_script_via_cli(
    title: str, duration_minutes: float, entry: Dict[str, Any], script_command: Optional[Sequence[str]]
) -> Optional[str]:
    """Run the ScriptIT headless CLI (same engine the live script adapter
    runs) in flags mode and return the narration text, or None."""
    cli = None
    if script_command:
        command = [str(part) for part in script_command]
    else:
        cli = live_repo_root() / "scriptit" / "cli" / "generate.js"
        if not cli.is_file():
            # Fall back to this worktree's own CLI (same code at the base).
            cli = Path(__file__).resolve().parents[3] / "scriptit" / "cli" / "generate.js"
        command = ["node", str(cli)]
    args = list(command) + [
        "--topic", str(title or ""),
        "--duration", f"{int(duration_minutes)}min",
        "--job-id", f"prep-{title_key(title, duration_minutes)[:12]}",
    ]
    description = str(entry.get("description") or "").strip()
    if description:
        args += ["--description", description]
    env = dict(os.environ)
    env["SCRIPTIT_JOB_ID"] = f"prep-{title_key(title, duration_minutes)[:12]}"
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=45 * 60, env=env
        )
    except Exception as exc:
        _log(f"script CLI failed for '{title}': {type(exc).__name__}: {exc}")
        return None
    try:
        payload = json.loads((result.stdout or "").strip().splitlines()[-1])
    except Exception:
        _log(f"script CLI returned no JSON for '{title}' (exit {result.returncode})")
        return None
    if isinstance(payload, dict) and payload.get("error"):
        _log(f"script CLI error for '{title}': {str(payload['error'])[:200]}")
        return None
    text = ""
    if isinstance(payload, dict):
        text = str(payload.get("scriptText") or payload.get("script") or payload.get("narration") or "").strip()
    elif isinstance(payload, str):
        text = payload.strip()
    return text or None


def _prep_web_media(title, scene_texts, bundle, manifest, stopped) -> None:
    from services.media_prescale import ensure_scaled
    from services.web_image_service import resolve_web_images_for_video_fast

    web_rows = list((bundle or {}).get("web") or [])
    script = "\n\n".join(scene_texts)
    durations = [max(3.0, round(len(text.split()) * 0.45, 2)) for text in scene_texts]
    try:
        # Prep must use the SAME web lane the live job uses (round 5:
        # prep banked 0/51 web entries in a verified-good hour, 4th run).
        # The live Mixed Media lane runs with wait_until_complete=True
        # (pipeline_v2_media_adapter sets it for external_agent jobs) and
        # 2 scene workers (_mixed_web_scene_worker_limit, code default 2);
        # prep called the resolver with neither, so it ran the fast-pass
        # budget instead: 8s engine timeout (live: 300s from the runtime
        # .env), 5s late wait (live: 120s), 4 scene workers.  Live engines
        # answer in 11-21s (run 4 server log, 19:50-19:52 UTC) — past
        # prep's 8s first-engine deadline every time — so every prep
        # batch abandoned its engines ("late result will be spooled" 91x
        # in panama-prep-4.log) and the 5s late wait orphaned the rows.
        # wait_until_complete=True restores the 30s engine timeout / 120s
        # late wait defaults when the runtime .env is not loaded, and the
        # runtime .env (300s/120s) still wins when prep-runner loads it.
        # 2 scene workers keeps prep inside the 2-per-host throttle cap
        # the way live is (prep's 4 workers queued behind the cap: "15s
        # budget waiting for a host slot" 6x in panama-prep-4.log).
        # Prep inherits the runtime .env stage wall (180s) through the
        # VIDZEO_ROOT default above — but that wall is a LIVE-job delivery
        # budget, not a prep budget: at 2 workers x ~30s/scene it cuts the
        # stage at 6/17 (verified 01:08 UTC: "complete: 6/17", audit
        # deadline=11). Prep is idle-time work with no render reserve at
        # stake, so give it its own 600s wall; the hard cap (1800s) and
        # the idle-gate abort between prep stages still bound it.
        result = resolve_web_images_for_video_fast(
            script=script,
            title=title,
            scene_texts=list(scene_texts),
            scene_durations=durations,
            scene_queries_override=web_rows or None,
            wait_until_complete=True,
            max_scene_workers=2,
            stage_deadline_seconds=600.0,
        )
    except Exception as exc:
        manifest["errors"].append(f"web prep failed: {type(exc).__name__}: {exc}")
        return
    image_paths = list((result or {}).get("image_paths") or [])
    resolver = _resolver()
    for idx, scene_text in enumerate(scene_texts):
        if stopped():
            return
        path = image_paths[idx] if idx < len(image_paths) else None
        manifest["web"]["attempted"] += 1
        if not path or not Path(str(path)).is_file():
            continue
        row = web_rows[idx] if idx < len(web_rows) and isinstance(web_rows[idx], dict) else {}
        queries = list(row.get("query_variants") or [])
        if not queries and row.get("search_query"):
            queries = [str(row["search_query"])]
        if not queries:
            continue
        primary = str(queries[0])
        asset = {
            "path": str(path),
            "source_url": str(row.get("search_query") or path),
            "title": str(title or ""),
            "active_subject": str(row.get("scene_subject") or row.get("subject_query") or ""),
            "subjects": [str(row.get("scene_subject") or "")] if row.get("scene_subject") else [],
        }
        # Persist one entry per query variant so the live resolver finds the
        # asset whichever (possibly repaired) variant it ends up using.
        recorded = False
        for query in queries:
            if record_prepped_web_asset(title, scene_text, str(query), asset):
                recorded = True
        if recorded:
            manifest["web"]["verified"] += 1
            scaled = ensure_scaled(path)
            entry = {
                "scene": idx + 1, "path": str(path), "query": primary,
                "prep_key": prep_cache_key(title, scene_text, primary),
                "scaled_path": scaled,
            }
            manifest["web"]["entries"].append(entry)
            if scaled:
                manifest["scaled"].append({"scene": idx + 1, "source": str(path), "scaled": scaled})


def _prep_youtube_media(title, scene_texts, bundle, manifest, stopped) -> None:
    """Per scene: live YouTube search -> transcript moment match -> ranged
    download -> deterministic transport/overlay screens -> verified-cut
    cache.  The live job still runs its own scene-specific visual gate on
    a cache hit; prep only removes the search/download cost."""
    from services.media_prescale import ensure_scaled
    from services.youtube_transcript_index import YouTubeTranscriptIndex

    yt_rows = list((bundle or {}).get("yt") or [])
    durations = [max(3.0, round(len(text.split()) * 0.45, 2)) for text in scene_texts]
    transcript_index = YouTubeTranscriptIndex()
    for idx, scene_text in enumerate(scene_texts):
        if stopped():
            return
        manifest["youtube"]["attempted"] += 1
        row = yt_rows[idx] if idx < len(yt_rows) and isinstance(yt_rows[idx], dict) else {}
        query = str(row.get("search_query") or row.get("youtube_query") or "").strip()
        if not query:
            continue
        duration = float(durations[idx]) if idx < len(durations) else 4.0
        try:
            cut = _prep_one_youtube_cut(
                title, scene_text, row, query, duration, transcript_index,
            )
        except Exception as exc:
            manifest["errors"].append(
                f"youtube scene {idx + 1} failed: {type(exc).__name__}: {exc}"
            )
            continue
        if not cut:
            continue
        manifest["youtube"]["verified"] += 1
        scaled = ensure_scaled(cut["path"])
        manifest["youtube"]["entries"].append({
            "scene": idx + 1,
            "video_id": cut["video_id"],
            "start": cut["start"],
            "duration": cut["duration"],
            "query": query,
            "path": cut["path"],
            "scaled_path": scaled,
            "verification": cut.get("verification", "deterministic"),
        })
        if scaled:
            manifest["scaled"].append({"scene": idx + 1, "source": cut["path"], "scaled": scaled})


def _prep_one_youtube_cut(title, scene_text, row, query, duration, transcript_index):
    """One scene's YouTube cut via the live pipeline functions. Returns
    {video_id, start, duration, path, verification} or None."""
    from services.youtube_media_cache import get_verified_cut, put_verified_cut
    from services.youtube_moment_matcher import match_scene_moments
    from services.youtube_pipeline_v2 import _download_clip, _search_youtube_uncached

    candidates = _search_youtube_uncached(query, max_results=8, session_jar=None) or []
    for candidate in candidates:
        video_id = str(
            candidate.get("video_id") or candidate.get("id") or ""
        ).strip()
        if not video_id:
            continue
        document = transcript_index.fetch(video_id)
        moments = match_scene_moments(
            document,
            scene_index=0,
            subject=str(row.get("scene_subject") or row.get("subject_query") or ""),
            scene_text=scene_text,
            visual_facet=str(row.get("visual_intent") or ""),
            positive_terms=list(row.get("retrieval_positive_terms") or []),
            negative_terms=list(row.get("retrieval_negative_terms") or []),
            duration_needed=float(duration),
            video_id=video_id,
        )
        if not moments:
            # Transcript-optional fallback: a mid-video moment is better
            # than nothing only if the live job would accept it — it would
            # not, so skip this candidate rather than caching a blind cut.
            continue
        moment = moments[0]
        start = float(moment.start)
        cached = get_verified_cut(video_id, start, duration)
        if cached and cached.get("path") and Path(str(cached["path"])).is_file():
            return {
                "video_id": video_id, "start": start, "duration": duration,
                "path": str(cached["path"]), "verification": "cache",
            }
        out_dir = runtime_temp_dir() / "prep" / "yt-cuts"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{video_id}_{int(start)}s_{int(duration)}s.mp4"
        downloaded = _download_clip(
            dict(candidate), out_path, start, duration, session_jar=None
        )
        if not downloaded or not out_path.is_file() or out_path.stat().st_size < 8192:
            continue
        if not _deterministic_cut_ok(out_path):
            try:
                out_path.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        stored = put_verified_cut(
            video_id, start, duration, str(out_path),
            metadata={
                "prep_title": normalize_title(title),
                "prep_query": query,
                "prep_narration_sha256": narration_hash(scene_text),
            },
        )
        if not stored:
            continue
        return {
            "video_id": video_id, "start": start, "duration": duration,
            "path": stored, "verification": "deterministic",
        }
    return None


def _deterministic_cut_ok(path: Path) -> bool:
    """Local transport checks only (decode + duration + overlay screens).
    No vision here — the live job still runs its scene-specific visual
    gate on a cache hit (see youtube_media_cache docstring)."""
    try:
        from services.youtube_pool_renderer import (
            _screen_moment_overlays,
            _screen_moment_quality,
        )

        quality = _screen_moment_quality(str(path))
        if isinstance(quality, dict) and quality.get("reject"):
            return False
        overlays = _screen_moment_overlays(str(path))
        if isinstance(overlays, dict) and overlays.get("reject"):
            return False
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        duration = float((result.stdout or "0").strip() or 0)
        return duration > 0
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit("Use scripts/prep/prep-runner.py")
