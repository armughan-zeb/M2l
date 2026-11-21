"""Persistent cache for verified YouTube transport artifacts.

Search-result caching avoids repeated metadata requests, but it does not avoid
the expensive part of YouTube retrieval: downloading and trimming the same
source moment again on a later job.  This cache stores only media that has
passed the local transport/overlay checks.  The caller still runs the
scene-specific semantic gate after a cache hit, so a cached cut is never
treated as universally relevant.

Lookup is exact-first, then overlap-tolerant: the exact key is
(video_id, start, duration), but a later job often re-splits the same script
into different scenes (Whisper split live vs the fallback split at prep
time), so the same source moment is requested with a different duration.
When the exact key misses, a cached cut from the SAME video and the SAME
schema version is reused if its window covers the requested window — its
start within ``OVERLAP_START_TOLERANCE_SECONDS`` of the requested start and
its end at or past the requested end.  A cached cut is never returned for a
shorter window than requested: a fresh download is rejected below the
requested duration, and a cache hit must not smuggle a short clip past that
contract (the render composition has no pad/loop step for short clips).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Older cuts were admitted at 70% of the requested duration. Do not reuse
# those entries under the full-duration contract.
SCHEMA_VERSION = "youtube-cut-v4-duration-handles"
_INIT_LOCK = threading.Lock()
_INITIALIZED: set[str] = set()

# Overlap fallback (see module docstring). The start tolerance absorbs
# moment re-match jitter/rounding between runs; the end tolerance is only
# rounding slack — the cached window must otherwise cover the whole
# requested window, so a reused cut is never shorter than requested.
DEFAULT_OVERLAP_START_TOLERANCE_SECONDS = 0.5
DEFAULT_OVERLAP_END_TOLERANCE_SECONDS = 0.05


def _default_root() -> Path:
    root = Path(os.environ.get("VIDZEO_ROOT") or Path(__file__).resolve().parents[2])
    return root / "temp" / "youtube" / "verified_cut_cache"


def _db_path() -> Path:
    root = Path(os.environ.get("VIDZEO_YT_CUT_CACHE_DIR") or _default_root())
    root.mkdir(parents=True, exist_ok=True)
    return root / "cuts.sqlite3"


def cut_cache_key(video_id: str, start: float, duration: float) -> str:
    material = {
        "schema": SCHEMA_VERSION,
        "video_id": str(video_id or "").strip(),
        "start": round(float(start or 0.0), 2),
        "duration": round(float(duration or 0.0), 2),
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=15)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def _ensure_schema(path: Path) -> None:
    key = str(path)
    if key in _INITIALIZED:
        return
    with _INIT_LOCK:
        if key in _INITIALIZED:
            return
        with _connect(path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS youtube_verified_cuts (
                    cache_key TEXT PRIMARY KEY,
                    video_id TEXT NOT NULL,
                    start_seconds REAL NOT NULL,
                    duration_seconds REAL NOT NULL,
                    media_path TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_used_at REAL NOT NULL,
                    schema_version TEXT NOT NULL
                )
                """
            )
        _INITIALIZED.add(key)


def _valid_file(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size < 8_192:
            return False
        with path.open("rb") as handle:
            head = handle.read(16)
        return len(head) >= 12 and head[4:8] == b"ftyp"
    except OSError:
        return False


def _overlap_tolerances() -> Tuple[float, float]:
    """(start, end) tolerances for the overlap fallback, in seconds."""
    try:
        start_tol = float(
            os.environ.get(
                "VIDZEO_YT_CUT_OVERLAP_START_TOLERANCE_SECONDS",
                str(DEFAULT_OVERLAP_START_TOLERANCE_SECONDS),
            )
        )
    except (TypeError, ValueError):
        start_tol = DEFAULT_OVERLAP_START_TOLERANCE_SECONDS
    try:
        end_tol = float(
            os.environ.get(
                "VIDZEO_YT_CUT_OVERLAP_END_TOLERANCE_SECONDS",
                str(DEFAULT_OVERLAP_END_TOLERANCE_SECONDS),
            )
        )
    except (TypeError, ValueError):
        end_tol = DEFAULT_OVERLAP_END_TOLERANCE_SECONDS
    return max(0.0, start_tol), max(0.0, end_tol)


def _covers_request(
    cached_start: float,
    cached_duration: float,
    start: float,
    duration: float,
    *,
    start_tol: float,
    end_tol: float,
) -> bool:
    """True when the cached window covers the requested window.

    The cached start may sit within ``start_tol`` of the requested start
    (re-match jitter), but the cached end must reach the requested end, so
    the reused cut is never shorter than the scene asked for.
    """
    if duration <= 0 or cached_duration <= 0:
        return False
    if abs(float(cached_start) - float(start)) > start_tol:
        return False
    return float(cached_start) + float(cached_duration) >= float(start) + float(duration) - end_tol


def _get_overlapping_cut(
    path: Path,
    video_id: str,
    start: float,
    duration: float,
    *,
    ttl: float,
) -> Optional[Dict[str, Any]]:
    """Overlap-tolerant fallback for :func:`get_verified_cut`.

    Same video and same schema version only; never crosses videos and never
    reuses a pre-v4 (70%-duration) row.  Returns the best covering cut, or
    ``None``.  Stale/invalid candidates are dropped, mirroring the exact
    path's hygiene.
    """
    if duration <= 0 or start < 0:
        return None
    start_tol, end_tol = _overlap_tolerances()
    try:
        with _connect(path) as connection:
            rows = connection.execute(
                "SELECT cache_key, start_seconds, duration_seconds, media_path,"
                " metadata_json, created_at FROM youtube_verified_cuts"
                " WHERE video_id = ? AND schema_version = ?",
                (str(video_id), SCHEMA_VERSION),
            ).fetchall()
    except Exception:
        return None
    candidates: List[Dict[str, Any]] = []
    for cache_key, cached_start, cached_duration, media_path, metadata_json, created_at in rows:
        try:
            cached_start_f = float(cached_start)
            cached_duration_f = float(cached_duration)
        except (TypeError, ValueError):
            continue
        if not _covers_request(
            cached_start_f, cached_duration_f, start, duration,
            start_tol=start_tol, end_tol=end_tol,
        ):
            continue
        candidates.append({
            "cache_key": str(cache_key),
            "start_seconds": cached_start_f,
            "duration_seconds": cached_duration_f,
            "media_path": str(media_path),
            "metadata_json": metadata_json,
            "created_at": float(created_at or 0.0),
        })
    # Tightest start match first, then the shortest covering cut (least
    # extra footage past the requested end), then the oldest key for a
    # deterministic order.
    candidates.sort(key=lambda row: (
        abs(row["start_seconds"] - float(start)),
        row["duration_seconds"],
        row["cache_key"],
    ))
    for candidate in candidates:
        media_path = Path(str(candidate["media_path"]))
        if time.time() - candidate["created_at"] > ttl or not _valid_file(media_path):
            try:
                with _connect(path) as connection:
                    connection.execute(
                        "DELETE FROM youtube_verified_cuts WHERE cache_key = ?",
                        (candidate["cache_key"],),
                    )
            except Exception:
                pass
            continue
        try:
            with _connect(path) as connection:
                connection.execute(
                    "UPDATE youtube_verified_cuts SET last_used_at = ? WHERE cache_key = ?",
                    (time.time(), candidate["cache_key"]),
                )
        except Exception:
            pass
        metadata = json.loads(candidate["metadata_json"]) if candidate["metadata_json"] else {}
        requested_end = float(start) + float(duration)
        cached_end = candidate["start_seconds"] + candidate["duration_seconds"]
        overlap = max(0.0, min(requested_end, cached_end) - max(float(start), candidate["start_seconds"]))
        return {
            "cache_key": candidate["cache_key"],
            "path": str(media_path),
            "metadata": metadata if isinstance(metadata, dict) else {},
            "tolerant_hit": True,
            "overlap_seconds": round(overlap, 3),
            "coverage": round(overlap / float(duration), 3) if duration else 0.0,
            "cached_start_seconds": candidate["start_seconds"],
            "cached_duration_seconds": candidate["duration_seconds"],
        }
    return None


def get_verified_cut(
    video_id: str,
    start: float,
    duration: float,
    *,
    ttl_seconds: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Return a still-valid cached cut, or ``None`` on a cache miss.

    Exact key first; on an exact miss, a same-video cut whose window covers
    the requested window (see :func:`_get_overlapping_cut`).  Either hit
    still goes through the caller's scene-specific visual gate.
    """
    try:
        ttl = float(
            ttl_seconds
            if ttl_seconds is not None
            else os.environ.get("VIDZEO_YT_CUT_CACHE_TTL_SECONDS", "604800")
        )
    except (TypeError, ValueError):
        ttl = 604800.0
    if ttl <= 0 or not str(video_id or "").strip():
        return None
    path = _db_path()
    _ensure_schema(path)
    key = cut_cache_key(video_id, start, duration)
    try:
        with _connect(path) as connection:
            row = connection.execute(
                "SELECT media_path, metadata_json, created_at FROM youtube_verified_cuts WHERE cache_key = ? AND schema_version = ?",
                (key, SCHEMA_VERSION),
            ).fetchone()
            if row:
                media_path = Path(str(row[0]))
                if time.time() - float(row[2] or 0.0) > ttl or not _valid_file(media_path):
                    connection.execute("DELETE FROM youtube_verified_cuts WHERE cache_key = ?", (key,))
                    row = None
                else:
                    connection.execute(
                        "UPDATE youtube_verified_cuts SET last_used_at = ? WHERE cache_key = ?",
                        (time.time(), key),
                    )
        if row:
            metadata = json.loads(row[1]) if row[1] else {}
            return {
                "cache_key": key,
                "path": str(Path(str(row[0]))),
                "metadata": metadata if isinstance(metadata, dict) else {},
                "tolerant_hit": False,
            }
    except Exception:
        return None
    try:
        return _get_overlapping_cut(
            path, str(video_id), float(start or 0.0), float(duration or 0.0), ttl=ttl
        )
    except Exception:
        return None


def put_verified_cut(
    video_id: str,
    start: float,
    duration: float,
    source_path: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Copy a locally verified cut into the cache and return its stable path."""
    source = Path(str(source_path or ""))
    if not str(video_id or "").strip() or not _valid_file(source):
        return None
    root = _db_path().parent
    _ensure_schema(_db_path())
    key = cut_cache_key(video_id, start, duration)
    target = root / f"{key}.mp4"
    temp = root / f".{key}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        if not target.is_file():
            shutil.copyfile(source, temp)
            os.replace(temp, target)
        now = time.time()
        payload = dict(metadata or {})
        payload.update({
            "video_id": str(video_id),
            "start_seconds": round(float(start or 0.0), 2),
            "duration_seconds": round(float(duration or 0.0), 2),
            "cached_at": now,
            "cache_schema": SCHEMA_VERSION,
        })
        with _connect(_db_path()) as connection:
            connection.execute(
                """
                INSERT INTO youtube_verified_cuts
                    (cache_key, video_id, start_seconds, duration_seconds, media_path,
                     metadata_json, created_at, last_used_at, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    media_path=excluded.media_path,
                    metadata_json=excluded.metadata_json,
                    last_used_at=excluded.last_used_at,
                    schema_version=excluded.schema_version
                """,
                (
                    key, str(video_id), float(start or 0.0), float(duration or 0.0),
                    str(target), json.dumps(payload, ensure_ascii=False), now, now,
                    SCHEMA_VERSION,
                ),
            )
        return str(target)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return None

