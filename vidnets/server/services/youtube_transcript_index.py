"""Persistent, single-flight transcript indexing for pooled YouTube sources."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
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


def _cookie_opener(session_jar: Any) -> Any:
    """URL opener that sends the shared YouTube session cookies, or None.

    Caption URLs are pre-signed, but the session still belongs on the
    request: an authenticated fetch survives the anonymous 429/IP-block
    that otherwise kills this lane.  The jar is copied, never written.
    """
    if session_jar is None:
        return None
    try:
        import copy as _copy
        import http.cookiejar as _cookiejar
        import urllib.request as _urlrequest

        jar = _cookiejar.CookieJar()
        for cookie in session_jar:
            if hasattr(cookie, "name"):
                jar.set_cookie(_copy.copy(cookie))
        if sum(1 for _ in jar) == 0:
            return None
        return _urlrequest.build_opener(_urlrequest.HTTPCookieProcessor(jar))
    except Exception:
        return None


def _api_timeout_seconds() -> float:
    """Per-request timeout bound for the youtube-transcript-api leg.

    The library calls ``session.get/post`` without a timeout, so one
    stalled watch-page fetch could hang a transcript worker thread for
    minutes (run 2: orphaned threads finished ~4 min after the 25 s
    deadline). 60 s is generous against the measured ~9.5 s bad-hour
    YouTube latency and still lets the fetch_many wall bound the lane
    (round 12: the old 30 s cap timed out with the 15 s client leg in
    job 9ab2eb05's slow hour).
    """
    try:
        return max(
            2.0,
            min(
                120.0,
                float(os.environ.get("VIDZEO_YT_TRANSCRIPT_API_TIMEOUT_SECONDS", "60") or 60),
            ),
        )
    except (TypeError, ValueError):
        return 60.0


def _transcript_client_timeout_seconds() -> float:
    """Per-attempt socket timeout for the yt-dlp caption-track fetch.

    Round 12 (Nov 23): job 9ab2eb05's transcript leg died on 15 s read
    timeouts in a slow hour (yt-dlp "Read timed out. (read
    timeout=15.0)" on every candidate) while the same calls answer in
    ~9.5 s in a good hour and a live transcript fetch took 20.6 s.
    60 s keeps a slow hour from zeroing the lane; the transcript
    deadline (300 s) still bounds the lane as a whole.
    ``VIDZEO_YT_TRANSCRIPT_CLIENT_TIMEOUT_SECONDS`` restores the old value.
    """
    try:
        return max(
            2.0,
            min(
                60.0,
                float(os.environ.get("VIDZEO_YT_TRANSCRIPT_CLIENT_TIMEOUT_SECONDS", "60") or 60),
            ),
        )
    except (TypeError, ValueError):
        return 60.0


def _transcript_caption_timeout_seconds() -> float:
    """Per-request timeout for the caption-file download itself.

    Same round-12 rationale as :func:`_transcript_client_timeout_seconds`:
    the caption download is the second half of the same provider chain,
    and 15 s timed it out in job 9ab2eb05's slow hour.
    ``VIDZEO_YT_TRANSCRIPT_CAPTION_TIMEOUT_SECONDS`` restores the old value.
    """
    try:
        return max(
            2.0,
            min(
                60.0,
                float(os.environ.get("VIDZEO_YT_TRANSCRIPT_CAPTION_TIMEOUT_SECONDS", "60") or 60),
            ),
        )
    except (TypeError, ValueError):
        return 60.0


def _transcript_per_video_wall_seconds() -> float:
    """Wall for one video's transcript race (seconds).

    Round 13 (Nov 23, job 9ab2eb05): the two provider legs race under
    one wall so a single video cannot eat the fetch_many batch — 8
    videos on 6 workers at a 45 s wall is ~2 waves x 45 s = 90 s,
    inside the 120 s batch deadline that retained 0/8 when each video
    chained ~120 s of sequential provider timeouts. YT may keep a
    timeout; this is it for the transcript fetch. Env
    ``VIDZEO_YT_TRANSCRIPT_PER_VIDEO_SECONDS`` (10–120).
    """
    try:
        return max(
            10.0,
            min(
                120.0,
                float(os.environ.get("VIDZEO_YT_TRANSCRIPT_PER_VIDEO_SECONDS", "45") or 45),
            ),
        )
    except (TypeError, ValueError):
        return 45.0


class _DefaultTimeoutSession:
    """Mixin giving a ``requests`` session a default per-request timeout.

    youtube-transcript-api never passes ``timeout`` to the http client, so
    the session itself must supply one. Implemented as a mixin so the
    session class stays a plain ``requests.Session`` subclass.
    """

    _vidzeo_default_timeout = 60.0  # round 12: matches _api_timeout_seconds()

    def request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("timeout", self._vidzeo_default_timeout)
        return super().request(method, url, **kwargs)  # type: ignore[misc]


def _transcript_api_client(session_jar: Any) -> Any:
    """A ``requests`` session carrying the shared cookies for youtube-transcript-api.

    The library's ``YouTubeTranscriptApi(http_client=...)`` accepts a
    requests session, so the transcript API call can run authenticated
    instead of anonymous.  Returns None when there is no session.
    """
    if session_jar is None:
        return None
    try:
        import copy as _copy

        import requests

        class _TimedSession(_DefaultTimeoutSession, requests.Session):
            pass

        client = _TimedSession()
        client._vidzeo_default_timeout = _api_timeout_seconds()
        for cookie in session_jar:
            if hasattr(cookie, "name"):
                client.cookies.set_cookie(_copy.copy(cookie))
        if len(client.cookies) == 0:
            return None
        return client
    except Exception:
        return None


def _is_definite_no_transcript_error(exc: BaseException) -> bool:
    """True when the API answered definitively that no transcript exists.

    TranscriptsDisabled / NoTranscriptFound / AgeRestricted /
    VideoUnavailable / InvalidVideoId / VideoUnplayable all mean the video
    has no retrievable transcript, so re-running the yt-dlp extraction
    cannot conjure captions. Everything else (timeouts, 429/IP-block,
    unparsable responses, consent failures) is transport-class and worth
    one retry through the next leg.
    """
    return type(exc).__name__ in {
        "TranscriptsDisabled",
        "NoTranscriptFound",
        "AgeRestricted",
        "VideoUnavailable",
        "InvalidVideoId",
        "VideoUnplayable",
    }


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


# Default yt-dlp player clients for the transcript legs, in try order.
# ``web`` first, ``web_embedded`` as fallback. Measured live (Nov 19,
# yt-dlp 2026.10.26, same video kZzKlAKNhOU, authenticated): ``web``
# extracted in 20.9 s vs 77.7 s for ``web_embedded`` — and ``web``
# never takes yt-dlp's embedded-ytcfg path, the unguarded
# ``_fix_embedded_ytcfg`` that raises raw ``KeyError('INNERTUBE_CONTEXT')``
# when YouTube serves an embed page without a ytcfg and killed all 6
# run-4 downloads. ``web_embedded`` stays second because the Nov 18
# measurement showed it returns the full automatic-caption list (the
# android player response carries no captions at all); a KeyError there
# is contained per-client by the caller, so listing it second is safe.
# Env ``VIDZEO_YT_TRANSCRIPT_CLIENTS`` still overrides both.
_DEFAULT_TRANSCRIPT_CLIENTS = "web,web_embedded"


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

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        # WAL is a database-level setting established once in _ensure_schema.
        # Reissuing it on every concurrent read asks SQLite for a write lock and
        # can make a cached transcript wait behind an unrelated slow provider.
        connection.execute("PRAGMA synchronous=NORMAL")
        try:
            with connection:
                yield connection
        finally:
            try:
                connection.close()
            except Exception:
                pass

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

    def _log_fetch_many_count(
        self,
        documents: Dict[str, TranscriptDocument],
        total: int,
        *,
        deadline_seconds: float,
        cached_only: bool = False,
    ) -> None:
        """Print the completed/unfinished transcript accounting, always.

        Run 7 gap: the count line only printed when the deadline was
        hit with futures still pending, so a worker whose transcripts
        all finished in time logged no accounting at all (main 9/11,
        small 2/3 — ranged-moment evidence only, no count line). Every
        fetch_many call with work to do prints this line, including the
        all-cached path, so every YT worker's transcript accounting is
        visible in its log. The ``retaining X/Y completed
        transcript(s); Z unfinished`` wording is kept verbatim so
        existing log analysis still matches; the deadline applied and
        the ready sub-count are appended.
        """
        completed = len(documents)
        unfinished = max(0, total - completed)
        ready = sum(1 for doc in documents.values() if getattr(doc, "available", False))
        suffix = "; all cached" if cached_only else ""
        print(
            "   [yt-transcript] deadline "
            f"{deadline_seconds:g}s: retaining {completed}/{total} completed transcript(s); "
            f"{unfinished} unfinished; {ready} ready{suffix}",
            flush=True,
        )

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
        # Default 6 (was 3): the pool path is the one the live mixed-media
        # worker uses, and 3 workers could not clear 11 videos inside the
        # transcript deadline — run 4 kept 2/11 at an 18 s wall. 6 matches
        # the ranged-moments caller (which already passes workers=6) and
        # fits the measured cost (isolated 12/12 in 94.7 s, ~8 s/video:
        # 11 videos at 6 workers is ~2 waves, ~16-30 s in a good hour).
        configured = workers or int(os.environ.get("VIDZEO_YT_TRANSCRIPT_CONCURRENCY", "6") or 6)
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
            self._log_fetch_many_count(documents, len(unique), deadline_seconds=deadline_seconds, cached_only=True)
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
        # Round 13: the transcript stage was invisible while it ran — the
        # worker's progress poller only recognised the final count line,
        # so a long fetch looked like "Searching YouTube" for its whole
        # duration (job 9ab2eb05: 197–870 s). Emit a progress line every
        # 10 s so the heartbeat states the true stage and counts.
        _progress_at = time.monotonic()
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
                if time.monotonic() - _progress_at >= 10.0:
                    _progress_at = time.monotonic()
                    _ready = sum(
                        1 for _doc in documents.values()
                        if getattr(_doc, "available", False)
                    )
                    print(
                        f"   [yt-transcript] fetching {len(documents)}/{len(unique)} "
                        f"completed transcript(s); {_ready} ready",
                        flush=True,
                    )

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
            # Unconditional: the count line is the transcript accounting
            # for this worker — it must appear whether the deadline was
            # hit or every transcript finished in time (see
            # _log_fetch_many_count).
            self._log_fetch_many_count(documents, len(unique), deadline_seconds=deadline_seconds)
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
            # ``web`` first (see _DEFAULT_TRANSCRIPT_CLIENTS): the android
            # player response carries no caption tracks at all
            # (en_auto/en_subs False on every probed video), so the android
            # round only burned time — ~4 s in a good hour, up to ~24 s of
            # socket-timeout burn in a bad one. The env override still
            # re-adds clients when an operator needs them.
            configured_clients = str(
                os.environ.get("VIDZEO_YT_TRANSCRIPT_CLIENTS", _DEFAULT_TRANSCRIPT_CLIENTS)
                or _DEFAULT_TRANSCRIPT_CLIENTS
            )
            clients = [client.strip() for client in configured_clients.split(",") if client.strip()]
            timeout = _transcript_client_timeout_seconds()
            client_errors: List[str] = []
            info = None
            for client in clients or _DEFAULT_TRANSCRIPT_CLIENTS.split(","):
                try:
                    opts = {
                        "quiet": True,
                        "no_warnings": True,
                        "skip_download": True,
                        "noplaylist": True,
                        "socket_timeout": timeout,
                        "retries": 0,
                        # This leg only needs the caption tracks from the
                        # info dict. Without the JS challenge solver the
                        # player response carries storyboard formats only,
                        # so format selection raises "Requested format is
                        # not available" and discards the captions that
                        # were already fetched (run 2: every extract_info
                        # died this way before the API leg ran). Ignore the
                        # format error and keep the info dict.
                        "ignore_no_formats_error": True,
                        # Prefer the system SSL context over certifi so a host
                        # behind a TLS-intercepting proxy still works.
                        "compat_opts": {"no-certifi"},
                        "extractor_args": {"youtube": {"player_client": [client]}},
                    }
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        if session_jar is not None:
                            ydl.cookiejar = session_jar
                        candidate = ydl.extract_info(video_id, download=False)
                    # Only stop at the first client whose player response
                    # actually carries caption tracks for a wanted language.
                    # The ``web`` response can lack captions the
                    # ``web_embedded`` response has (measured live Nov 19:
                    # nnsAUPuOYaY — ``web`` none, ``web_embedded`` auto),
                    # so breaking on the first successful extraction would
                    # throw away the fallback client's captions.
                    wanted = _transcript_languages()
                    has_captions = any(
                        (candidate.get(kind) or {}).get(language)
                        for kind in ("subtitles", "automatic_captions")
                        for language in wanted
                    )
                    if has_captions:
                        info = candidate
                        break
                    client_errors.append(f"{client}:no_captions")
                except Exception as exc:
                    client_errors.append(f"{client}:{type(exc).__name__}:{exc}")
            if info is None:
                raise RuntimeError("yt-dlp:all_clients_failed:" + " | ".join(client_errors))
            caption_timeout = _transcript_caption_timeout_seconds()
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
                    opener = _cookie_opener(session_jar)
                    if opener is not None:
                        response_cm = opener.open(url, timeout=caption_timeout)
                    else:
                        response_cm = _urlrequest.urlopen(url, timeout=caption_timeout)
                    with response_cm as response:
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

        # Round 13 (Nov 23, job 9ab2eb05): the two legs used to run
        # SEQUENTIALLY — auth yt-dlp (2 clients x socket timeout +
        # caption) then the transcript API (30/60 s) then a THIRD
        # yt-dlp re-fetch — so one slow video cost up to ~120 s (r11)
        # while fetch_many ran 8 videos on 6 workers against a 120 s
        # deadline: 0/8 retained, every fetch still in flight when the
        # deadline fired. Race the legs concurrently instead (first
        # success wins) under one per-video wall. A definitive
        # no-captions answer from either leg ends the race at once
        # (the other leg cannot succeed for the same video).
        def _api_provider() -> dict:
            from youtube_transcript_api import YouTubeTranscriptApi

            api_client = _transcript_api_client(session_jar)
            api = (
                YouTubeTranscriptApi(http_client=api_client)
                if api_client is not None
                else YouTubeTranscriptApi()
            )
            transcript = api.fetch(video_id, languages=_transcript_languages())
            return {"source": "youtube_transcript_api", "language": "en", "segments": transcript.to_raw_data()}

        auth_attempted = yt_dlp is not None and session_jar is not None
        race_wall = _transcript_per_video_wall_seconds()
        race_legs = []
        if auth_attempted:
            race_legs.append(("yt-dlp-auth", _yt_dlp_caption_provider))
        race_legs.append(("api", _api_provider))
        auth_transport_failed = False
        auth_no_captions = False
        api_definitive = False
        api_transport_failed = False
        race_pool = ThreadPoolExecutor(
            max_workers=len(race_legs), thread_name_prefix="yt-transcript-race",
        )
        race_futures = {race_pool.submit(fn): name for name, fn in race_legs}
        race_pending = set(race_futures)
        race_deadline = time.monotonic() + race_wall
        try:
            while race_pending:
                race_remaining = race_deadline - time.monotonic()
                if race_remaining <= 0:
                    break
                race_done, race_pending = wait(
                    race_pending,
                    timeout=min(0.25, race_remaining),
                    return_when=FIRST_COMPLETED,
                )
                for race_future in race_done:
                    race_leg = race_futures[race_future]
                    try:
                        return race_future.result()
                    except Exception as exc:
                        errors.append(f"{race_leg}:{type(exc).__name__}:{exc}")
                        if race_leg == "api":
                            if _is_definite_no_transcript_error(exc):
                                api_definitive = True
                                for _other in race_pending:
                                    _other.cancel()
                                raise RuntimeError(
                                    f"youtube_transcript_unavailable:{' | '.join(errors)}"
                                ) from None
                            api_transport_failed = True
                        else:
                            if "yt-dlp:no_en_captions" in str(exc):
                                auth_no_captions = True
                                for _other in race_pending:
                                    _other.cancel()
                                raise RuntimeError(
                                    f"youtube_transcript_unavailable:{' | '.join(errors)}"
                                ) from None
                            auth_transport_failed = True
            for _leftover in race_pending:
                _leftover.cancel()
        finally:
            race_pool.shutdown(wait=False, cancel_futures=True)
        # Both legs failed on transport (or the per-video wall fired).
        # Fall through to the guard below. The third sequential yt-dlp
        # re-fetch is the retry for transport failures (the
        # cookie-wiring test pins it), so it still runs when both legs
        # failed on transport — but it shares the per-video race wall
        # (see the wall check inside the fallback block), it does not
        # get a fresh budget. When the auth leg never ran, the fallback
        # below is still the FIRST yt-dlp attempt for this video.
        # The yt-dlp fallback re-fetches the same player response the
        # authenticated leg already saw, so it only helps when BOTH prior
        # legs failed on transport (a transient blip worth one retry) —
        # never after a definitive no-captions answer from either leg, and
        # never when the authenticated leg already proved there are no
        # captions. Run 2 burned two extra extract_info rounds per video
        # after the API had already answered TranscriptsDisabled.
        # When the authenticated leg never ran (no yt-dlp / no session) the
        # fallback is the first yt-dlp attempt, so it still runs on an API
        # transport failure.
        if (
            api_definitive
            or auth_no_captions
            or not api_transport_failed
            or (auth_attempted and not auth_transport_failed)
        ):
            raise RuntimeError(f"youtube_transcript_unavailable:{' | '.join(errors)}") from None
        enabled = str(os.environ.get("VIDZEO_YT_TRANSCRIPT_YTDLP_FALLBACK", "1") or "1").strip().lower()
        if enabled in {"1", "true", "yes", "on"}:
            # Round 13 correction: the fallback shares the per-video
            # race wall — it is the retry for transport failures, not a
            # fresh budget. If the race already spent the wall, the
            # video's fetch ends here; the per-video total stays inside
            # the wall either way, so fetch_many's arithmetic holds.
            if time.monotonic() >= race_deadline:
                raise RuntimeError(
                    f"youtube_transcript_unavailable:{' | '.join(errors)}"
                ) from None
            try:
                import json as _json
                import urllib.request

                import yt_dlp

                info = None
                client_errors: List[str] = []
                configured_clients = str(
                    os.environ.get("VIDZEO_YT_TRANSCRIPT_CLIENTS", _DEFAULT_TRANSCRIPT_CLIENTS)
                    or _DEFAULT_TRANSCRIPT_CLIENTS
                )
                clients = [
                    client.strip() for client in configured_clients.split(",")
                    if client.strip()
                ] or _DEFAULT_TRANSCRIPT_CLIENTS.split(",")
                # The fallback's per-client socket timeout is capped at
                # the remaining race wall, so one slow client cannot push
                # the per-video fetch past the wall.
                client_timeout = min(
                    _transcript_client_timeout_seconds(),
                    max(1.0, race_deadline - time.monotonic()),
                )
                for client in clients:
                    # Do not start another client attempt past the wall.
                    if time.monotonic() >= race_deadline:
                        break
                    try:
                        with yt_dlp.YoutubeDL({
                            "quiet": True,
                            "no_warnings": True,
                            "skip_download": True,
                            "noplaylist": True,
                            "socket_timeout": client_timeout,
                            "retries": 0,
                            # Same as the primary leg: only the caption
                            # tracks matter, so a storyboard-only player
                            # response must not raise away the info dict.
                            "ignore_no_formats_error": True,
                            # Prefer the system SSL context over certifi so a
                            # host behind a TLS-intercepting proxy still works.
                            "compat_opts": {"no-certifi"},
                            "extractor_args": {"youtube": {"player_client": [client]}},
                        }) as ydl:
                            # Anonymous spot (fixed Nov 17): this fallback
                            # ran without the session even when one was
                            # configured. Attach it like the primary path.
                            if session_jar is not None:
                                ydl.cookiejar = session_jar
                            candidate = ydl.extract_info(video_id, download=False)
                        # Same as the primary leg: only stop at a client
                        # whose player response carries caption tracks —
                        # ``web`` can lack captions ``web_embedded`` has.
                        wanted = _transcript_languages()
                        if any(
                            (candidate.get(kind) or {}).get(language)
                            for kind in ("subtitles", "automatic_captions")
                            for language in wanted
                        ):
                            info = candidate
                            break
                        client_errors.append(f"{client}:no_captions")
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
                        # The caption download is capped at the remaining
                        # race wall, like the client attempts above.
                        caption_timeout = min(
                            _transcript_caption_timeout_seconds(),
                            max(1.0, race_deadline - time.monotonic()),
                        )
                        caption_opener = _cookie_opener(session_jar)
                        if caption_opener is not None:
                            response_cm = caption_opener.open(url, timeout=caption_timeout)
                        else:
                            response_cm = urllib.request.urlopen(url, timeout=caption_timeout)
                        with response_cm as response:
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
