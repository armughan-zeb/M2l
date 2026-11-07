"""Persistent cache for verified YouTube transport artifacts.

Search-result caching avoids repeated metadata requests, but it does not avoid
the expensive part of YouTube retrieval: downloading and trimming the same
source moment again on a later job.  This cache stores only media that has
passed the local transport/overlay checks.  The caller still runs the
scene-specific semantic gate after a cache hit, so a cached cut is never
treated as universally relevant.
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
from typing import Any, Dict, Optional


SCHEMA_VERSION = "youtube-cut-v2-sharp-source"
_INIT_LOCK = threading.Lock()
_INITIALIZED: set[str] = set()


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


def get_verified_cut(
    video_id: str,
    start: float,
    duration: float,
    *,
    ttl_seconds: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Return a still-valid cached cut, or ``None`` on a cache miss."""
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
            if not row:
                return None
            media_path = Path(str(row[0]))
            if time.time() - float(row[2] or 0.0) > ttl or not _valid_file(media_path):
                connection.execute("DELETE FROM youtube_verified_cuts WHERE cache_key = ?", (key,))
                return None
            connection.execute(
                "UPDATE youtube_verified_cuts SET last_used_at = ? WHERE cache_key = ?",
                (time.time(), key),
            )
        metadata = json.loads(row[1]) if row[1] else {}
        return {
            "cache_key": key,
            "path": str(media_path),
            "metadata": metadata if isinstance(metadata, dict) else {},
        }
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

