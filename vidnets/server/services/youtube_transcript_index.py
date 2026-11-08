"""Persistent, single-flight transcript indexing for pooled YouTube sources."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

SCHEMA_VERSION = "youtube-transcript-v1"
_INIT_LOCK = threading.Lock()
_INITIALIZED: set[str] = set()
_FLIGHT_LOCK = threading.Lock()
_FLIGHTS: Dict[str, threading.Event] = {}


def _cache_ttl_seconds(status: str) -> float:
    """Return status-aware transcript freshness.

    Successful captions are stable across jobs. Missing captions may appear
    later, while transport/provider failures should be retried quickly; the
    previous unbounded cache made one transient error effectively permanent.
    """
    normalized = str(status or "").strip().lower()
    defaults = {
        "ready": 30.0 * 24.0 * 60.0 * 60.0,
        "missing": 6.0 * 60.0 * 60.0,
        "error": 5.0 * 60.0,
    }
    env_names = {
        "ready": "VIDZEO_YT_TRANSCRIPT_SUCCESS_TTL_SECONDS",
        "missing": "VIDZEO_YT_TRANSCRIPT_MISSING_TTL_SECONDS",
        "error": "VIDZEO_YT_TRANSCRIPT_ERROR_TTL_SECONDS",
    }
    default = defaults.get(normalized, defaults["error"])
    try:
        return max(
            0.0,
            float(os.environ.get(env_names.get(normalized, env_names["error"]), default) or default),
        )
    except (TypeError, ValueError):
        return default


def _transcript_languages() -> List[str]:
    """Return the ordered list of caption languages to try.

    English-first by default (transcripts drive timestamp matching for the
    default English narration), extendable per market via a comma-separated
    env var, e.g. ``VIDZEO_YT_TRANSCRIPT_LANGUAGES=hi,hi-IN,en`` for a Hindi
    deployment. The first available language wins, keeping native-language
    captioning useful instead of dropping non-English sources to score-only.
    """
    configured = str(os.environ.get("VIDZEO_YT_TRANSCRIPT_LANGUAGES", "") or "").strip()
    if configured:
        languages = [
            item.strip()
            for item in configured.split(",")
            if item.strip()
        ]
        if languages:
            return languages
    return ["en", "en-US", "en-GB"]


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str
    normalized_text: str
    language: str = "en"
    source: str = "unknown"
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TranscriptDocument:
    video_id: str
    language: str = "en"
    source: str = "unknown"
    status: str = "missing"
    segments: List[TranscriptSegment] | None = None
    error: str = ""
    fetched_at: float = 0.0

    def __post_init__(self) -> None:
        if self.segments is None:
            self.segments = []

    @property
    def available(self) -> bool:
        return self.status == "ready" and bool(self.segments)

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "language": self.language,
            "source": self.source,
            "status": self.status,
            "segments": [segment.to_dict() for segment in self.segments or []],
            "error": self.error,
            "fetched_at": self.fetched_at,
        }


def normalize_text(value: Any) -> str:
    # Python's Unicode-aware ``\w`` keeps non-Latin captions matchable while
    # still discarding punctuation/emoji noise. The old ASCII-only cleanup
    # reduced Arabic, Urdu, Hindi, and other captions to empty strings.
    text = str(value or "").casefold()
    text = re.sub(r"[^\w\s'\-]+", " ", text, flags=re.UNICODE)
    text = text.replace("_", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalize_segments(
    rows: Iterable[Any],
    *,
    language: str = "en",
    source: str = "unknown",
) -> List[TranscriptSegment]:
    normalized: List[TranscriptSegment] = []
    for row in rows or []:
        if isinstance(row, dict):
            text = row.get("text") or row.get("snippet") or row.get("caption") or ""
            start = row.get("start", row.get("offset", 0.0))
            duration = row.get("duration", 0.0)
            end = row.get("end")
            confidence = row.get("confidence", 0.0)
        else:
            text = getattr(row, "text", "")
            start = getattr(row, "start", getattr(row, "offset", 0.0))
            duration = getattr(row, "duration", 0.0)
            end = getattr(row, "end", None)
            confidence = getattr(row, "confidence", 0.0)
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if not text:
            continue
        try:
            start_value = max(0.0, float(start or 0.0))
            end_value = float(end) if end is not None else start_value + max(0.1, float(duration or 0.0))
        except (TypeError, ValueError):
            continue
        end_value = max(start_value + 0.1, end_value)
        try:
            confidence_value = max(0.0, min(1.0, float(confidence or 0.0)))
        except (TypeError, ValueError):
            confidence_value = 0.0
        normalized.append(TranscriptSegment(
            start=round(start_value, 3),
            end=round(end_value, 3),
            text=text,
            normalized_text=normalize_text(text),
            language=str(language or "en"),
            source=str(source or "unknown"),
            confidence=round(confidence_value, 4),
        ))
    normalized.sort(key=lambda segment: (segment.start, segment.end))
    merged: List[TranscriptSegment] = []
    for segment in normalized:
        if merged and segment.start <= merged[-1].end and normalize_text(segment.text) == merged[-1].normalized_text:
            previous = merged[-1]
            merged[-1] = TranscriptSegment(
                start=previous.start,
                end=max(previous.end, segment.end),
                text=previous.text,
                normalized_text=previous.normalized_text,
                language=previous.language,
                source=previous.source,
                confidence=max(previous.confidence, segment.confidence),
            )
        else:
            merged.append(segment)
    return merged


def _default_cache_path() -> Path:
    root = Path(os.environ.get("VIDZEO_ROOT") or Path(__file__).resolve().parents[2])
    path = root / "temp" / "youtube" / "transcript_cache.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path



class YouTubeTranscriptIndex:
    def __init__(
        self,
        path: Optional[str] = None,
        provider: Optional[Callable[[str], Any]] = None,
        session_jar: Any = None,
    ):
        self.path = str(Path(path) if path else _default_cache_path())
        self.session_jar = session_jar
        self.provider = provider or self._default_provider
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        # WAL is a database-level setting established once in _ensure_schema.
        # Reissuing it on every concurrent read asks SQLite for a write lock and
        # can make a cached transcript wait behind an unrelated slow provider.
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _ensure_schema(self) -> None:
        if self.path in _INITIALIZED:
            return
        with _INIT_LOCK:
            if self.path in _INITIALIZED:
                return
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS youtube_transcripts (
                        video_id TEXT PRIMARY KEY,
                        language TEXT NOT NULL,
                        source TEXT NOT NULL,
                        status TEXT NOT NULL,
                        transcript_json TEXT NOT NULL,
                        error TEXT NOT NULL,
                        fetched_at REAL NOT NULL,
                        schema_version TEXT NOT NULL
                    )
                    """
                )
            _INITIALIZED.add(self.path)

    def get(self, video_id: str, *, language: str = "") -> Optional[TranscriptDocument]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT language, source, status, transcript_json, error, fetched_at, schema_version FROM youtube_transcripts WHERE video_id = ?",
                (str(video_id),),
            ).fetchone()
        if not row or row[6] != SCHEMA_VERSION:
            return None
        try:
            payload = json.loads(row[3])
            segments = normalize_segments(payload, language=row[0], source=row[1])
        except Exception:
            segments = []
        document = TranscriptDocument(
            video_id=str(video_id), language=row[0], source=row[1], status=row[2],
            segments=segments, error=row[4], fetched_at=float(row[5] or 0.0),
        )
        age = max(0.0, time.time() - float(document.fetched_at or 0.0))
        if age > _cache_ttl_seconds(document.status):
            return None
        requested_language = str(language or "").strip().lower()
        cached_language = str(document.language or "").strip().lower()
        if (
            requested_language
            and cached_language
            and requested_language.split("-", 1)[0] != cached_language.split("-", 1)[0]
        ):
            return None
        return document

    def put(self, document: TranscriptDocument) -> None:
        payload = [segment.to_dict() for segment in document.segments or []]
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO youtube_transcripts
                    (video_id, language, source, status, transcript_json, error, fetched_at, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    language=excluded.language, source=excluded.source, status=excluded.status,
                    transcript_json=excluded.transcript_json, error=excluded.error,
                    fetched_at=excluded.fetched_at, schema_version=excluded.schema_version
                """,
                (
                    document.video_id, document.language, document.source, document.status,
                    json.dumps(payload, ensure_ascii=False), document.error,
                    document.fetched_at or time.time(), SCHEMA_VERSION,
                ),
            )

    def fetch(self, video_id: str, *, language: str = "en", force: bool = False) -> TranscriptDocument:
        video_id = str(video_id or "").strip()
        if not video_id:
            return TranscriptDocument(video_id="", status="missing", error="missing_video_id")
        if not force:
            cached = self.get(video_id, language=language)
            if cached is not None:
                return cached
        owner = False
        with _FLIGHT_LOCK:
            event = _FLIGHTS.get(video_id)
            if event is None:
                event = threading.Event()
                _FLIGHTS[video_id] = event
                owner = True
        if not owner:
            event.wait(timeout=60.0)
            return self.get(video_id, language=language) or TranscriptDocument(video_id=video_id, status="missing", error="transcript_flight_timeout")
        try:
            try:
                raw = self.provider(video_id)
                if isinstance(raw, TranscriptDocument):
                    document = raw
                else:
                    rows = raw.get("segments", []) if isinstance(raw, dict) else raw
                    document = TranscriptDocument(
                        video_id=video_id,
                        language=str(raw.get("language", language) if isinstance(raw, dict) else language),
                        source=str(raw.get("source", "provider") if isinstance(raw, dict) else "provider"),
                        status="ready",
                        segments=normalize_segments(rows, language=language, source="provider"),
                        fetched_at=time.time(),
                    )
                    if not document.segments:
                        document.status = "missing"
                        document.error = "empty_transcript"
            except Exception as exc:
                document = TranscriptDocument(
                    video_id=video_id, language=language, status="error",
                    error=f"{type(exc).__name__}:{exc}", fetched_at=time.time(),
                )
            self.put(document)
            return document
        finally:
            with _FLIGHT_LOCK:
                _FLIGHTS.pop(video_id, None)
                event.set()

    def fetch_many(
        self,
        video_ids: Sequence[str],
        *,
        workers: Optional[int] = None,
        deadline_seconds: Optional[float] = None,
        force: bool = False,
    ) -> Dict[str, TranscriptDocument]:
        unique = list(dict.fromkeys(str(video_id).strip() for video_id in video_ids if str(video_id).strip()))
        if not unique:
            return {}
        # Transcript lookup is I/O-bound. Keep enough workers to hide one
        # blocked YouTube request, but cap the fan-out so a large pool does not
        # trigger a rate-limit storm; caption endpoints 429 aggressively.
        configured = workers or int(os.environ.get("VIDZEO_YT_TRANSCRIPT_CONCURRENCY", "3") or 3)
        worker_count = max(1, min(12, int(configured)))
        if deadline_seconds is None:
            try:
                deadline_seconds = float(os.environ.get("VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS", "25") or 25)
            except (TypeError, ValueError):
                deadline_seconds = 25.0
        deadline_seconds = max(0.05, min(300.0, float(deadline_seconds)))
        documents: Dict[str, TranscriptDocument] = {}
        uncached: List[str] = []
        retry_cached_transport: set[str] = set()
        for video_id in unique:
            try:
                cached = None if force else self.get(video_id)
            except Exception:
                cached = None
            # A prior public/unauthenticated attempt can be cached as an
            # IP-block/429 error. Once the verified browser session is
            # available, that error is no longer authoritative; retry it
            # through the authenticated yt-dlp provider instead of waiting
            # for the negative-cache TTL to expire.
            if (
                cached is not None
                and cached.status == "error"
                and self.session_jar is not None
                and any(
                    marker in str(cached.error or "").lower()
                    for marker in ("ipblocked", "too many requests", "http error 429", "http 429")
                )
            ):
                cached = None
                retry_cached_transport.add(video_id)
            if cached is not None:
                documents[video_id] = cached
            else:
                uncached.append(video_id)
        if not uncached:
            return documents

        deadline = time.monotonic() + deadline_seconds
        executor = ThreadPoolExecutor(
            max_workers=min(worker_count, len(uncached)),
            thread_name_prefix="yt-transcript",
        )
        futures = {
            executor.submit(
                self._fetch_resilient,
                video_id,
                force=force or video_id in retry_cached_transport,
            ): video_id
            for video_id in uncached
        }
        pending = set(futures)
        try:
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                done, pending = wait(
                    pending,
                    timeout=min(0.25, remaining),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    try:
                        documents[futures[future]] = future.result()
                    except Exception:
                        # A provider/cache failure for one source must not block
                        # transcript evidence from the other pooled sources.
                        pass

            # Retain completions that race the final wall-clock check.
            finished_at_deadline = {future for future in pending if future.done()}
            pending.difference_update(finished_at_deadline)
            for future in finished_at_deadline:
                try:
                    documents[futures[future]] = future.result()
                except Exception:
                    pass
            if pending:
                for future in pending:
                    future.cancel()
                print(
                    "   [yt-transcript] deadline: "
                    f"retaining {len(documents)}/{len(unique)} completed transcript(s); "
                    f"{len(pending)} unfinished",
                    flush=True,
                )
        finally:
            # Context-manager shutdown waits for every provider thread. That
            # made the nominal transcript deadline ineffective whenever the
            # API stalled. Completed cache writes remain valid; queued work is
            # cancelled and active providers may finish safely in background.
            executor.shutdown(wait=False, cancel_futures=True)
        return documents

    def _fetch_resilient(
        self,
        video_id: str,
        *,
        force: bool = False,
        retries: Optional[int] = None,
    ) -> "TranscriptDocument":
        """Fetch with bounded backoff when YouTube throttles caption lookups.

        The caption endpoints sporadically answer 429/IP-blocked even for the
        authenticated provider, usually right after a search/download burst.
        Retries give a quiet window while staying inside the caller's deadline.
        """
        if retries is None:
            try:
                retries = max(0, min(3, int(
                    os.environ.get("VIDZEO_YT_TRANSCRIPT_RETRIES", "2") or 2
                )))
            except (TypeError, ValueError):
                retries = 2
        attempt = 0
        while True:
            document = self.fetch(video_id, force=force or attempt > 0)
            error = str(getattr(document, "error", "") or "").lower()
            if not any(marker in error for marker in ("429", "too many requests", "ipblocked")):
                return document
            if attempt >= retries:
                return document
            attempt += 1
            backoff = min(20.0, 3.0 * attempt * attempt)
            time.sleep(backoff)

    def _default_provider(self, video_id: str) -> dict:
        errors: List[str] = []
        # yt-dlp can use the same verified in-memory browser session as the
        # media downloader. This is important when youtube-transcript-api is
        # rate-limited (HTTP 429/IP blocked); do not make transcript lookup
        # unauthenticated by accident.
        session_jar = self.session_jar
        try:
            from services.yt_dlp_runtime import load_yt_dlp
            yt_dlp = load_yt_dlp()
        except Exception:
            yt_dlp = None
        if yt_dlp is not None and session_jar is not None:
            try:
                from yt_dlp.cookies import YoutubeDLCookieJar
                if not callable(getattr(session_jar, "get_cookie_header", None)):
                    adapted = YoutubeDLCookieJar()
                    import copy as _copy
                    for cookie in session_jar:
                        adapted.set_cookie(_copy.copy(cookie))
                    session_jar = adapted
            except Exception:
                session_jar = None

        def _yt_dlp_caption_provider() -> dict:
            if yt_dlp is None:
                raise RuntimeError("yt-dlp unavailable")
            configured_clients = str(
                os.environ.get("VIDZEO_YT_TRANSCRIPT_CLIENTS", "web_embedded,android")
                or "web_embedded,android"
            )
            clients = [client.strip() for client in configured_clients.split(",") if client.strip()]
            try:
                timeout = max(2.0, min(30.0, float(
                    os.environ.get("VIDZEO_YT_TRANSCRIPT_CLIENT_TIMEOUT_SECONDS", "8") or 8
                )))
            except (TypeError, ValueError):
                timeout = 8.0
            client_errors: List[str] = []
            info = None
            for client in clients or ["web_embedded", "android"]:
                try:
                    opts = {
                        "quiet": True,
                        "no_warnings": True,
                        "skip_download": True,
                        "noplaylist": True,
                        "socket_timeout": timeout,
                        "retries": 0,
                        # Prefer the system SSL context over certifi so a host
                        # behind a TLS-intercepting proxy still works.
                        "compat_opts": {"no-certifi"},
                        "extractor_args": {"youtube": {"player_client": [client]}},
                    }
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        if session_jar is not None:
                            ydl.cookiejar = session_jar
                        info = ydl.extract_info(video_id, download=False)
                    break
                except Exception as exc:
                    client_errors.append(f"{client}:{type(exc).__name__}:{exc}")
            if info is None:
                raise RuntimeError("yt-dlp:all_clients_failed:" + " | ".join(client_errors))
            try:
                caption_timeout = max(2.0, min(30.0, float(
                    os.environ.get("VIDZEO_YT_TRANSCRIPT_CAPTION_TIMEOUT_SECONDS", "8") or 8
                )))
            except (TypeError, ValueError):
                caption_timeout = 8.0
            import urllib.request as _urlrequest
            import json as _json
            for kind in ("subtitles", "automatic_captions"):
                captions = info.get(kind) or {}
                for language in _transcript_languages():
                    formats = captions.get(language) or []
                    if not formats:
                        continue
                    target = next((fmt for fmt in formats if fmt.get("ext") == "json3"), None) or formats[0]
                    url = (target or {}).get("url")
                    if not url:
                        continue
                    with _urlrequest.urlopen(url, timeout=caption_timeout) as response:
                        payload = _json.loads(response.read().decode("utf-8", "replace"))
                    rows = []
                    for event in payload.get("events") or []:
                        text = "".join(segment.get("utf8", "") for segment in event.get("segs") or [])
                        text = re.sub(r"\[[^\]]*\]|â™ª|>>", " ", text)
                        text = re.sub(r"\s+", " ", text).strip()
                        if not text:
                            continue
                        rows.append({
                            "text": text,
                            "start": int(event.get("tStartMs") or 0) / 1000.0,
                            "duration": max(0.1, int(event.get("dDurationMs") or 0) / 1000.0),
                            "language": language,
                            "confidence": 0.0,
                        })
                    if rows:
                        return {"source": "yt-dlp-authenticated", "language": language, "segments": rows}
            raise RuntimeError("yt-dlp:no_en_captions")

        # Prefer the authenticated yt-dlp caption endpoint when available;
        # otherwise retain the existing API-first public fallback.
        if yt_dlp is not None and session_jar is not None:
            try:
                return _yt_dlp_caption_provider()
            except Exception as exc:
                errors.append(f"yt-dlp-auth:{type(exc).__name__}:{exc}")
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            transcript = YouTubeTranscriptApi().fetch(video_id, languages=_transcript_languages())
            return {"source": "youtube_transcript_api", "language": "en", "segments": transcript.to_raw_data()}
        except Exception as exc:
            errors.append(f"{type(exc).__name__}:{exc}")
        enabled = str(os.environ.get("VIDZEO_YT_TRANSCRIPT_YTDLP_FALLBACK", "1") or "1").strip().lower()
        if enabled in {"1", "true", "yes", "on"}:
            try:
                import json as _json
                import urllib.request

                import yt_dlp

                info = None
                client_errors: List[str] = []
                configured_clients = str(
                    os.environ.get("VIDZEO_YT_TRANSCRIPT_CLIENTS", "web_embedded,android")
                    or "web_embedded,android"
                )
                clients = [
                    client.strip() for client in configured_clients.split(",")
                    if client.strip()
                ] or ["web_embedded", "android"]
                try:
                    client_timeout = max(
                        2.0,
                        min(
                            30.0,
                            float(os.environ.get("VIDZEO_YT_TRANSCRIPT_CLIENT_TIMEOUT_SECONDS", "8") or 8),
                        ),
                    )
                except (TypeError, ValueError):
                    client_timeout = 8.0
                for client in clients:
                    try:
                        with yt_dlp.YoutubeDL({
                            "quiet": True,
                            "no_warnings": True,
                            "skip_download": True,
                            "noplaylist": True,
                            "socket_timeout": client_timeout,
                            "retries": 0,
                            # Prefer the system SSL context over certifi so a
                            # host behind a TLS-intercepting proxy still works.
                            "compat_opts": {"no-certifi"},
                            "extractor_args": {"youtube": {"player_client": [client]}},
                        }) as ydl:
                            info = ydl.extract_info(video_id, download=False)
                        break
                    except Exception as exc:
                        client_errors.append(f"{client}:{type(exc).__name__}:{exc}")
                if info is None:
                    raise RuntimeError(" | ".join(client_errors) or "yt-dlp:all_clients_failed")
                for kind in ("subtitles", "automatic_captions"):
                    captions = info.get(kind) or {}
                    for language in _transcript_languages():
                        formats = captions.get(language) or []
                        if not formats:
                            continue
                        target = next((fmt for fmt in formats if fmt.get("ext") == "json3"), None) or formats[0]
                        url = (target or {}).get("url")
                        if not url:
                            continue
                        try:
                            caption_timeout = max(
                                2.0,
                                min(
                                    30.0,
                                    float(os.environ.get("VIDZEO_YT_TRANSCRIPT_CAPTION_TIMEOUT_SECONDS", "8") or 8),
                                ),
                            )
                        except (TypeError, ValueError):
                            caption_timeout = 8.0
                        with urllib.request.urlopen(url, timeout=caption_timeout) as response:
                            payload = _json.loads(response.read().decode("utf-8", "replace"))
                        rows = []
                        for event in payload.get("events") or []:
                            segments = event.get("segs") or []
                            text = "".join(segment.get("utf8", "") for segment in segments)
                            text = re.sub(r"\[[^\]]*\]|♪|>>", " ", text)
                            text = re.sub(r"\s+", " ", text).strip()
                            if not text:
                                continue
                            start_ms = int(event.get("tStartMs") or 0)
                            duration_ms = int(event.get("dDurationMs") or 0)
                            rows.append({
                                "text": text,
                                "start": start_ms / 1000.0,
                                "duration": max(0.1, duration_ms / 1000.0),
                                "language": language,
                                "confidence": 0.0,
                            })
                        if rows:
                            return {"source": "yt-dlp", "language": language, "segments": rows}
                errors.append("yt-dlp:no_en_captions")
            except Exception as exc:
                errors.append(f"yt-dlp:{type(exc).__name__}:{exc}")
        raise RuntimeError(f"youtube_transcript_unavailable:{' | '.join(errors)}") from None


def transcript_fingerprint(document: TranscriptDocument) -> str:
    payload = json.dumps(document.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
