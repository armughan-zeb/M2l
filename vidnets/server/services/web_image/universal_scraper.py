"""Internal adapter for the parallel multi-engine scraper.

This module is intentionally self contained.  It ports the useful discovery
logic from ``Documents/xyz/Scrappers`` into Vidnets' existing resolver so the
pipeline gets one result contract, one timeout budget, and one set of runtime
logs.  Discovery is deliberately unauthenticated: no cookies, browser state,
or account credentials are used.

The manager fans out across engines at the same time and returns partial
success when one engine is slow or challenged.  A bounded worker pool and
per-engine timeout keep a bad endpoint from holding the media stage hostage.
Pixel download, metadata safety, and vision verification remain the
resolver's responsibility.
"""
from __future__ import annotations

import asyncio
import html
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import logging
import os
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from ..job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from ..media_distinctness import (
    dominant_source as _distinct_dominant_source,
    needs_more_sources as _distinct_needs_more_sources,
    source_host as _distinct_source_host,
)
from dataclasses import dataclass, field, replace as _dc_replace
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import requests

try:  # Optional fast Chrome-TLS transport used by the supplied scraper.
    import primp  # type: ignore
except Exception:  # pragma: no cover - optional in minimal deployments
    primp = None

try:  # Optional parser; regex fallback keeps the adapter lightweight.
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover - optional in minimal deployments
    BeautifulSoup = None

try:  # Optional DDG scraper dependency.
    from ddgs import DDGS  # type: ignore
except Exception:  # pragma: no cover - optional in minimal deployments
    DDGS = None

from .source_broker import WEB_ENGINE_PRIORITY


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class UniversalImageResult:
    """Normalized result copied from the standalone scraper's ImageResult."""

    title: str
    image_url: str
    thumbnail_url: str = ""
    source_url: str = ""
    source_engine: str = ""
    width: int = 0
    height: int = 0
    description: str = ""
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class UniversalEngineStat:
    engine: str
    count: int
    duration_ms: int
    success: bool
    error: str = ""


@dataclass(frozen=True)
class UniversalSearchResponse:
    query: str
    total: int
    results: List[UniversalImageResult]
    stats: List[UniversalEngineStat]
    # Source-diversity summary for the merged rows (GTA VI proof job:
    # every kept web image came from Pinterest while the engines had
    # returned other publishers). ``dominant_host`` / ``dominant_share``
    # and ``needs_more_sources`` let the resolver decide to keep
    # searching instead of settling for a single-source pool. Defaulted
    # so existing positional constructions keep working.
    source_diversity: Dict[str, object] = field(default_factory=dict)


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_DIRECT_SCHEMES = ("http://", "https://")
_BING_META_RE = re.compile(
    r"<a[^>]+class=[\"'][^\"']*iusc[^\"']*[\"'][^>]+m=[\"']([^\"']+)[\"']",
    re.IGNORECASE | re.DOTALL,
)
_BING_META_RE_REVERSE = re.compile(
    r"<a[^>]+m=[\"']([^\"']+)[\"'][^>]+class=[\"'][^\"']*iusc[^\"']*[\"']",
    re.IGNORECASE | re.DOTALL,
)
_Y_IMG_RE = re.compile(r'\\?"img_href\\?"\s*:\s*\\?"(https?://[^"\\]+)', re.I)
_Y_THUMB_RE = re.compile(r'\\?"image\\?"\s*:\s*\\?"(//[^"\\]+|https?://[^"\\]+)', re.I)
_Y_TITLE_RE = re.compile(r'\\?"(?:snippet"\s*:\s*\{[^{}]{0,600}?\\?"title|title)\\?"\s*:\s*\\?"([^"\\]+)', re.I)
_URL_RE = re.compile(r"https?://[^\"'<>\\\s]+", re.I)


# ── Per-host search throttling (S7) ──────────────────────────────────────
# The manager fans engines out in parallel and several scene workers can
# run batches at once, so one host (e.g. yandex.com) can see many
# simultaneous requests — enough to trigger SmartCaptcha / 429. Bound it:
# at most N concurrent requests per host, at least ``delay`` between
# request starts to the same host, and exponential backoff on 429. A 403 is
# definitive for the current egress and is retired by the per-run engine gate.
# The state is module-global, so it bounds ALL managers and scene workers
# collectively, not just one batch. This reuses the download limiter's
# philosophy (per-host caps + backoff) for the search path, which
# previously had no throttle at all. The DuckDuckGo engine is the one
# exception: it goes through a subprocess worker, not a client.get, so it
# is not routed through here (it has its own 15s budget + 2 attempts).
def _universal_per_host() -> int:
    try:
        return max(1, min(8, int(os.environ.get("VIDZEO_UNIVERSAL_PER_HOST", "2") or 2)))
    except (TypeError, ValueError):
        return 2


def _universal_min_delay() -> float:
    try:
        return max(0.0, min(5.0, float(os.environ.get("VIDZEO_UNIVERSAL_MIN_DELAY_SECONDS", "0.5") or 0.5)))
    except (TypeError, ValueError):
        return 0.5


def _universal_retry_count() -> int:
    try:
        return max(0, min(3, int(os.environ.get("VIDZEO_UNIVERSAL_RETRIES", "2") or 2)))
    except (TypeError, ValueError):
        return 2


def _universal_retry_delay(attempt: int) -> float:
    try:
        base = max(0.1, min(3.0, float(os.environ.get("VIDZEO_UNIVERSAL_RETRY_BASE", "1.0") or 1.0)))
    except (TypeError, ValueError):
        base = 1.0
    return min(8.0, base * (2 ** attempt))


def _universal_engine_stagger_seconds() -> float:
    """Extra wall-clock per engine, in priority order.

    Wright (Nov 16): all four engines died in ~20s because they shared one
    batch wall — the slowest engine's timeout was every engine's timeout.
    Each engine after the first now gets one more stagger step, so a slow
    fallback engine is not cut at the same instant as the first engine.
    """
    try:
        return max(
            0.0,
            min(
                15.0,
                float(
                    os.environ.get("VIDZEO_UNIVERSAL_ENGINE_STAGGER_SECONDS", "5")
                    or 5
                ),
            ),
        )
    except (TypeError, ValueError):
        return 5.0


def _universal_empty_retry_backoff_seconds() -> float:
    """Backoff before the single retry of an all-empty, timed-out batch."""
    try:
        return max(
            0.0,
            min(
                10.0,
                float(
                    os.environ.get(
                        "VIDZEO_UNIVERSAL_EMPTY_RETRY_BACKOFF_SECONDS", "2"
                    )
                    or 2
                ),
            ),
        )
    except (TypeError, ValueError):
        return 2.0


_universal_host_state: Dict[str, Dict[str, object]] = {}
_universal_host_guard = threading.Lock()


def _universal_host_slot(host: str) -> Dict[str, object]:
    with _universal_host_guard:
        state = _universal_host_state.get(host)
        if state is None:
            state = {
                "semaphore": threading.Semaphore(_universal_per_host()),
                "last_start": 0.0,
                "lock": threading.Lock(),
            }
            _universal_host_state[host] = state
        return state


def _throttled_get(client, url: str, *, headers, timeout):
    """GET through the per-host throttle, with bounded 429 backoff retries.

    ``client`` is any object with ``.get(url, headers=..., timeout=...)``
    (the isolated native client, a requests.Session, or the requests
    module). Returns the response-like object (status_code + text/json).
    Only 429 is retried with exponential backoff. A 403 is returned
    immediately so the manager can retire that engine for this run rather
    than repeat a refusal for every scene. Other non-200 responses are
    returned as-is and recorded as per-engine failures.

    Queue time counts INSIDE ``timeout`` (GTA fix, Nov 16): the old code
    waited on the min-delay gap and the per-host semaphore outside the
    socket timeout, so one engine call cost 53–200s of stacked 15s
    timeouts plus queueing. A call that cannot even start inside its
    budget now fails fast with TimeoutError instead of burning the batch.
    """
    host = (urlparse(url).netloc or "").lower()
    state = _universal_host_slot(host)
    semaphore = state["semaphore"]
    lock = state["lock"]
    try:
        budget = max(0.5, float(timeout or 15))
    except (TypeError, ValueError):
        budget = 15.0
    started = time.monotonic()

    def _remaining() -> float:
        return budget - (time.monotonic() - started)

    response = None
    for attempt in range(_universal_retry_count() + 1):
        if _remaining() <= 0:
            raise TimeoutError(
                f"throttled request to {host} exceeded its {budget:.0f}s "
                "budget before starting"
            )
        with lock:
            wait = _universal_min_delay() - (time.monotonic() - float(state["last_start"]))
            if wait > 0:
                if wait >= _remaining():
                    raise TimeoutError(
                        f"throttled request to {host} exceeded its "
                        f"{budget:.0f}s budget in queue"
                    )
                time.sleep(wait)
            state["last_start"] = time.monotonic()
        if _remaining() <= 0:
            raise TimeoutError(
                f"throttled request to {host} exceeded its {budget:.0f}s "
                "budget in queue"
            )
        acquired = semaphore.acquire(timeout=_remaining())
        if not acquired:
            raise TimeoutError(
                f"throttled request to {host} exceeded its {budget:.0f}s "
                "budget waiting for a host slot"
            )
        try:
            request_timeout = max(0.5, min(float(timeout or 15), _remaining()))
            response = client.get(url, headers=headers, timeout=request_timeout)
        finally:
            semaphore.release()
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 429 or attempt >= _universal_retry_count():
            return response
        delay = min(_universal_retry_delay(attempt), max(0.0, _remaining()))
        if delay <= 0:
            return response
        time.sleep(delay)
    return response


def _title_is_query_echo(title: object, query: str) -> bool:
    """Identify provider placeholders that repeat the retrieval query."""
    raw_title = re.sub(r"\s+", " ", str(title or "")).strip()
    raw_query = re.sub(r"\s+", " ", str(query or "")).strip()
    if not raw_title or not raw_query:
        return not raw_title
    norm_title = re.sub(r"[^a-z0-9]+", " ", raw_title.casefold()).strip()
    norm_query = re.sub(r"[^a-z0-9]+", " ", raw_query.casefold()).strip()
    if norm_title == norm_query:
        return True
    if norm_title.startswith(norm_query + " "):
        suffix = norm_title[len(norm_query):].strip()
        # Yandex and a few SERP mirrors expose the page shell as every card's
        # title (``<query>: browse and download images â€” Yandex``).  It is a
        # provider label, never an image caption, so do not let it prove
        # subject identity.
        if any(
            marker in suffix
            for marker in ("browse", "download", "search", "image results", "yandex images", "bing images")
        ):
            return True
    # Generic numbered labels are still query echoes, while meaningful
    # captions (including years and event names) are retained as evidence.
    stripped = re.sub(
        r"\s+(?:result|image|photo|picture|thumbnail|item|card|match)\s*#?\s*\d+\s*$",
        "",
        norm_title,
    ).strip()
    return stripped == norm_query


def _normalize_url(value: object) -> str:
    value = html.unescape(str(value or "")).replace("\\/", "/").strip()
    if value.startswith("//"):
        value = "https:" + value
    if not value.startswith(_DIRECT_SCHEMES):
        return ""
    parsed = urlparse(value)
    if not parsed.netloc:
        return ""
    if parsed.scheme not in {"http", "https"}:
        return ""
    return value


def _canonical_url(value: object) -> str:
    url = _normalize_url(value)
    if not url:
        return ""
    parsed = urlparse(url)
    # Query strings on CDN thumbnails frequently contain expiring signatures;
    # retain them for download but dedupe on the stable path when possible.
    return parsed._replace(fragment="").geturl().rstrip("/").casefold()


def _int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _result(
    engine: str,
    query: str,
    image_url: object,
    *,
    title: object = "",
    thumbnail_url: object = "",
    source_url: object = "",
    width: object = 0,
    height: object = 0,
    description: object = "",
    metadata: Optional[Dict[str, object]] = None,
) -> Optional[UniversalImageResult]:
    image = _normalize_url(image_url)
    if not image:
        return None
    resolved_title = str(title or "").strip()
    title_is_query = _title_is_query_echo(resolved_title, query)
    return UniversalImageResult(
        # Keep the query visible in raw scraper diagnostics, but mark it so
        # the resolver never uses this placeholder as asset identity proof.
        title=(resolved_title or query).strip()[:500],
        image_url=image,
        thumbnail_url=_normalize_url(thumbnail_url) or image,
        source_url=_normalize_url(source_url) or image,
        source_engine=engine,
        width=_int(width),
        height=_int(height),
        description=str(description or "").strip()[:1000],
        metadata={**dict(metadata or {}), "title_is_query": title_is_query},
    )


class _Engine:
    name = "engine"

    def search(self, query: str, limit: int, page: int, safe_search: bool) -> List[UniversalImageResult]:
        raise NotImplementedError


class _IsolatedNativeClient:
    """Keep Chrome TLS behavior without a native initialization in the API."""
    def get(self, url, *, headers, timeout):
        from ..job_context import check_job_active, remaining_seconds
        check_job_active()
        budget = min(float(timeout), remaining_seconds(float(timeout)))
        if budget <= 0:
            raise TimeoutError('Search budget is exhausted')
        process = subprocess.Popen(
            [sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'research_search_worker.py')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            output, _ = process.communicate(json.dumps({'kind': 'http', 'url': url,
                'headers': headers, 'timeout': budget}), timeout=budget)
            check_job_active()
            data = json.loads(output[:2200000])
            if not isinstance(data, dict) or not isinstance(data.get('text'), str):
                # The worker writes '[]' for ANY failure (its __main__
                # handler swallows the traceback). Indexing that list raised
                # a bare TypeError that used to be masked by the engines'
                # plain-requests fallback; with the fallback removed it
                # zeroed the whole engine (live, Nov 16: primp DNS dead,
                # plain requests fine). Fail as a clean transport error so
                # the caller can fall back instead of dying on a TypeError.
                raise RuntimeError(
                    'search worker returned an unexpected payload '
                    f'(expected a text response, got {type(data).__name__})'
                )
            body = data['text']
            return SimpleNamespace(status_code=data['status_code'], text=body,
                                   json=lambda: json.loads(body))
        finally:
            if process.poll() is None:
                process.kill()
            try:
                process.communicate(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass


class YandexEngine(_Engine):
    name = "yandex"

    def __init__(self) -> None:
        self._local = threading.local()

    def _get_client(self):
        client = getattr(self._local, "client", None)
        if client is None:
            client = _IsolatedNativeClient() if primp is not None else requests.Session()
            self._local.client = client
        return client

    def search(self, query: str, limit: int, page: int, safe_search: bool) -> List[UniversalImageResult]:
        encoded = urllib.parse.quote(query)
        url = f"https://yandex.com/images/search?text={encoded}&p={max(0, page - 1)}"
        if safe_search:
            url += "&family=yes"
        # Bounded plain-requests fallback (Nov 16 verification fix): the
        # isolated client is one transport, not the only one. When it fails
        # (live: primp DNS dead while plain requests returns 200), the
        # engine falls back instead of returning zero rows. The old
        # 53–200s stacking this fallback once caused is gone: queue time
        # now counts inside each 15s budget, so the two attempts cost at
        # most ~30s, not a runaway wall.
        try:
            response = _throttled_get(self._get_client(), url, headers=_HEADERS, timeout=15)
        except Exception:
            self._local.client = None
            response = _throttled_get(requests, url, headers=_HEADERS, timeout=15)

        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise RuntimeError(f"Yandex returned HTTP status {status}")
        raw = html.unescape(str(getattr(response, "text", "") or ""))
        # Yandex serves its SmartCaptcha page (HTTP 200) instead of results
        # when the egress IP is gated. Same markers as ScriptIT's
        # isYandexChallengePage(): fail fast with the real reason instead of
        # returning a silent empty list that looks like "no results".
        if re.search(r"captcha_smart", raw, re.I) and re.search(r"captcha", raw, re.I) \
                and not re.search(r"serp-item|Organic", raw):
            raise RuntimeError("Yandex bot challenge (SmartCaptcha; egress IP gated)")
        # The source project's unescaped SERP parser is retained here.  The
        # newer state parser often exposes ``img_href`` before card metadata.
        images = [_normalize_url(v) for v in _Y_IMG_RE.findall(raw)]
        thumbs = [_normalize_url(v) for v in _Y_THUMB_RE.findall(raw)]
        titles = [html.unescape(v).replace("\\u002F", "/") for v in _Y_TITLE_RE.findall(raw)]
        out: List[UniversalImageResult] = []
        seen = set()
        for idx, image in enumerate(images):
            key = _canonical_url(image)
            if not key or key in seen:
                continue
            seen.add(key)
            row = _result(
                "Yandex", query, image,
                title=titles[idx] if idx < len(titles) else f"{query} result {idx + 1}",
                thumbnail_url=thumbs[idx] if idx < len(thumbs) else image,
                source_url=image,
                description="Yandex visual search index",
                metadata={"retrieval_method": "unescaped_serp", "title_is_query": not (idx < len(titles) and titles[idx])},
            )
            if row:
                out.append(row)
            if len(out) >= limit:
                break
        return out


class BingEngine(_Engine):
    name = "bing"

    def __init__(self) -> None:
        self._local = threading.local()

    def _get_client(self):
        client = getattr(self._local, "client", None)
        if client is None:
            client = _IsolatedNativeClient() if primp is not None else requests.Session()
            self._local.client = client
        return client

    def search(self, query: str, limit: int, page: int, safe_search: bool) -> List[UniversalImageResult]:
        first = max(0, (page - 1) * limit)
        safe = "strict" if safe_search else "off"
        url = (
            "https://www.bing.com/images/async?"
            f"q={urllib.parse.quote(query)}&first={first}&count={limit}&mmasync=1&adlt={safe}"
        )
        # Bounded plain-requests fallback — see YandexEngine: the isolated
        # transport failing (e.g. primp DNS dead) must not zero the engine.
        try:
            response = _throttled_get(self._get_client(), url, headers=_HEADERS, timeout=15)
        except Exception:
            self._local.client = None
            response = _throttled_get(requests, url, headers=_HEADERS, timeout=15)

        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise RuntimeError(f"Bing returned HTTP status {status}")
        markup = str(getattr(response, "text", "") or "")
        elements: Iterable[object]
        if BeautifulSoup is not None:
            elements = BeautifulSoup(markup, "html.parser").find_all("a", class_="iusc")
        else:
            elements = _BING_META_RE.findall(markup) + _BING_META_RE_REVERSE.findall(markup)
        out: List[UniversalImageResult] = []
        for element in elements:
            if isinstance(element, str):
                raw_meta = element
                anchor_title = query
            else:
                raw_meta = element.get("m")  # type: ignore[union-attr]
                anchor_title = element.get("aria-label") or query  # type: ignore[union-attr]
            try:
                data = json.loads(html.unescape(str(raw_meta or "")))
            except Exception:
                continue
            row = _result(
                "Bing", query, data.get("murl"),
                title=data.get("t") or anchor_title,
                thumbnail_url=data.get("turl"),
                source_url=data.get("purl"),
                width=data.get("w") or data.get("mw"),
                height=data.get("h") or data.get("mh"),
                description=data.get("desc"),
                metadata={"retrieval_method": "bing_async_media", "title_is_query": not bool(data.get("t") or anchor_title != query)},
            )
            if row:
                out.append(row)
            if len(out) >= limit:
                break
        return out


class DuckDuckGoEngine(_Engine):
    name = "duckduckgo"

    def search(self, query: str, limit: int, page: int, safe_search: bool) -> List[UniversalImageResult]:
        if DDGS is None:
            raise RuntimeError("optional dependency 'ddgs' is not installed")
        start = max(0, (page - 1) * limit)
        # DuckDuckGo intermittently serves a 202 anomaly interstitial from
        # this egress IP (verified live 2026-11-10: 0 results on one query,
        # 10 on the next). The old 4s budget was also too tight for a ddgs
        # call through the throttled proxy, so the budget is now 15s and a
        # clean empty result is retried once. A hung worker is different:
        # it must return [] immediately (never retry a hang), per the
        # timeout contract. A persistent clean empty fails loudly instead
        # of returning a silent empty list that hid the bot-gate for weeks.
        for attempt in range(2):
            try:
                out = self._search_once(query, limit, start, safe_search)
            except subprocess.TimeoutExpired:
                return []
            if out:
                return out
            if attempt == 0:
                LOGGER.debug("DuckDuckGo empty on attempt 1 for %r; retrying once", query)
        raise RuntimeError(
            "DuckDuckGo returned no results after 2 attempts (likely bot-gate interstitial)"
        )

    def _search_once(self, query: str, limit: int, start: int, safe_search: bool) -> List[UniversalImageResult]:
        out: List[UniversalImageResult] = []
        process = None
        try:
            from ..job_context import check_job_active, remaining_seconds
            check_job_active()
            budget = min(15.0, remaining_seconds(15.0))
            if budget <= 0:
                return out
            process = subprocess.Popen(
                [sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'research_search_worker.py')],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            output, _ = process.communicate(json.dumps({'kind': 'images', 'query': query,
                'timeout': budget, 'safe_search': safe_search, 'limit': start + limit}), timeout=budget)
            check_job_active()
            raw_items = json.loads(output[:1000000])
            if not isinstance(raw_items, list):
                return out
            for idx, item in enumerate(raw_items or []):
                if idx < start:
                    continue
                row = _result(
                    "DuckDuckGo", query, item.get("image"),
                    title=item.get("title"), thumbnail_url=item.get("thumbnail"),
                    source_url=item.get("url"), width=item.get("width"),
                    height=item.get("height"),
                    description=f"Source: {item.get('source', '')}",
                    metadata={"retrieval_method": "ddgs_image_index"},
                )
                if row:
                    out.append(row)
                if len(out) >= limit:
                    break
        except subprocess.TimeoutExpired:
            # Hung worker: the finally block kills it; let the caller see
            # the timeout so it can return [] without retrying a hang.
            raise
        except Exception as exc:
            LOGGER.debug("DuckDuckGo search error: %s", exc)
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                try:
                    process.communicate(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        return out


class BaiduEngine(_Engine):
    name = "baidu"

    def __init__(self) -> None:
        self._local = threading.local()

    def _get_client(self):
        client = getattr(self._local, "client", None)
        if client is None:
            client = _IsolatedNativeClient() if primp is not None else requests.Session()
            self._local.client = client
        return client

    def search(self, query: str, limit: int, page: int, safe_search: bool) -> List[UniversalImageResult]:
        del safe_search
        start = max(0, (page - 1) * limit)
        encoded = urllib.parse.quote(query)
        url = (
            "https://image.baidu.com/search/acjson?tn=resultjson_com&ipn=rj&ct=201326592"
            f"&fp=result&queryWord={encoded}&word={encoded}&cl=2&lm=-1&ie=utf-8&oe=utf-8"
            f"&pn={start}&rn={limit}"
        )
        # Bounded plain-requests fallback — see YandexEngine: the isolated
        # transport failing (e.g. primp DNS dead) must not zero the engine.
        try:
            response = _throttled_get(self._get_client(), url, headers={**_HEADERS, "Referer": "https://image.baidu.com/"}, timeout=15)
        except Exception:
            self._local.client = None
            response = _throttled_get(requests, url, headers={**_HEADERS, "Referer": "https://image.baidu.com/"}, timeout=15)

        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise RuntimeError(f"Baidu returned HTTP status {status}")
        raw = re.sub(r"\\(?![/\\\"bfnrtu])", r"\\\\", str(response.text or ""))
        try:
            data = json.loads(raw, strict=False)
        except Exception as exc:
            raise RuntimeError(f"Baidu JSON parse failed: {exc}")
        # Baidu answers this egress IP with {"antiFlag": 1, "message":
        # "Forbid spider access"} (HTTP 200, no results). Fail fast with the
        # real reason instead of returning a silent empty list.
        if isinstance(data, dict) and (
            data.get("antiFlag") or "forbid spider" in str(data.get("message") or "").lower()
        ):
            raise RuntimeError("Baidu forbids spider access from this egress IP (antiFlag=1)")
        out: List[UniversalImageResult] = []
        for item in data.get("data", []) if isinstance(data, dict) else []:
            replacements = item.get("replaceUrl") if isinstance(item, dict) else None
            original = ""
            if isinstance(replacements, list) and replacements:
                original = replacements[0].get("ObjUrl") or ""
            row = _result(
                "Baidu", query, original or item.get("thumbURL") or item.get("middleURL"),
                title=item.get("fromPageTitleEnc"), thumbnail_url=item.get("thumbURL"),
                source_url=item.get("fromURL"), width=item.get("width"), height=item.get("height"),
                description=f"Host: {item.get('bdImgLoc', '') or 'Baidu Images'}",
                metadata={"retrieval_method": "baidu_acjson"},
            )
            if row:
                out.append(row)
            if len(out) >= limit:
                break
        return out


class OpenverseEngine(_Engine):
    name = "openverse"

    def __init__(self) -> None:
        self._local = threading.local()

    def _get_client(self):
        client = getattr(self._local, "client", None)
        if client is None:
            client = _IsolatedNativeClient() if primp is not None else requests.Session()
            self._local.client = client
        return client

    def search(self, query: str, limit: int, page: int, safe_search: bool) -> List[UniversalImageResult]:
        del safe_search
        url = (
            f"https://api.openverse.org/v1/images/?q={urllib.parse.quote(query)}"
            f"&page={max(1, page)}&page_size={min(limit, 50)}"
        )
        # Bounded plain-requests fallback — see YandexEngine: the isolated
        # transport failing (e.g. primp DNS dead) must not zero the engine.
        try:
            response = _throttled_get(self._get_client(), url, headers={**_HEADERS, "Accept": "application/json"}, timeout=15)
        except Exception:
            self._local.client = None
            response = _throttled_get(requests, url, headers={**_HEADERS, "Accept": "application/json"}, timeout=15)

        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise RuntimeError(f"Openverse returned HTTP status {status}")
        data = response.json()
        out: List[UniversalImageResult] = []
        for item in data.get("results", []) if isinstance(data, dict) else []:
            license_code = str(item.get("license") or "").upper()
            description = f"Creator: {item.get('creator') or 'Unknown'}"
            if license_code:
                description += f" â€¢ License: {license_code}"
            row = _result(
                "Openverse", query, item.get("url"), title=item.get("title"),
                thumbnail_url=item.get("thumbnail"), source_url=item.get("foreign_landing_url"),
                width=item.get("width"), height=item.get("height"), description=description,
                metadata={
                    "retrieval_method": "openverse_api",
                    "license": license_code,
                    "rights_status": "friendly" if license_code else "unknown",
                    "foreign_url": item.get("foreign_landing_url") or "",
                },
            )
            if row:
                out.append(row)
            if len(out) >= limit:
                break
        return out


class FlickrEngine(_Engine):
    name = "flickr"

    def __init__(self) -> None:
        self._local = threading.local()

    def _get_client(self):
        client = getattr(self._local, "client", None)
        if client is None:
            client = _IsolatedNativeClient() if primp is not None else requests.Session()
            self._local.client = client
        return client

    def search(self, query: str, limit: int, page: int, safe_search: bool) -> List[UniversalImageResult]:
        del safe_search
        terms = re.findall(r"[^\W_]+", query, re.UNICODE)
        stopwords = {"the", "and", "for", "with", "from", "this", "that", "how", "what", "where", "when", "which", "about"}
        tags = [term for term in terms if len(term) >= 3 and term.casefold() not in stopwords][:5]
        if not tags:
            return []
        params = urllib.parse.urlencode({"tags": ",".join(tags), "tagmode": "any",
                                         "format": "json", "nojsoncallback": "1"})
        url = f"https://www.flickr.com/services/feeds/photos_public.gne?{params}"
        # Bounded plain-requests fallback — see YandexEngine: the isolated
        # transport failing (e.g. primp DNS dead) must not zero the engine.
        try:
            response = _throttled_get(self._get_client(), url, headers=_HEADERS, timeout=15)
        except Exception:
            self._local.client = None
            response = _throttled_get(requests, url, headers=_HEADERS, timeout=15)

        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise RuntimeError(f"Flickr returned HTTP status {status}")
        data = response.json()
        out: List[UniversalImageResult] = []
        for item in data.get("items", []) if isinstance(data, dict) else []:
            media = item.get("media") or {}
            thumb = media.get("m") or ""
            high = thumb.replace("_m.jpg", "_b.jpg")
            row = _result(
                "Flickr", query, high or thumb, title=str(item.get("title") or query).strip(),
                thumbnail_url=thumb, source_url=item.get("link"),
                description=f"Author: {str(item.get('author') or '').strip()}",
                metadata={"retrieval_method": "flickr_public_feed", "tags": item.get("tags") or ""},
            )
            if row:
                out.append(row)
            if len(out) >= limit:
                break
        return out


_BOT_GATE_MARKERS = ("bot-gate", "bot challenge", "smartcaptcha", "robot check", "captcha")

# A *guessed* interstitial, not a refusal: DuckDuckGo raises "returned no
# results after 2 attempts (likely bot-gate interstitial)" on two clean
# empty results. That is an inference, not a challenge page or an HTTP
# refusal, and routing it to the long bot-gate skip lost the engine for
# the whole run 15s in (GTA VI). It gets the short refusal cooldown plus
# a re-probe instead (see UniversalScraperManager).
_SUSPECTED_GATE_MARKERS = ("likely bot-gate interstitial",)


def is_suspected_bot_gate_error(exc: BaseException) -> bool:
    """True when the failure is only a guessed interstitial, not a refusal."""
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _SUSPECTED_GATE_MARKERS)


def is_bot_gate_error(exc: BaseException) -> bool:
    """True when an engine failure is a definitive refusal for this run.

    Bot gates, HTTP 403 refusals, and exhausted 429 responses should not be
    repeated for every scene through the same egress. The manager applies a
    short cooldown to HTTP refusals/rate limits and a longer one to bot gates.
    Ordinary transport errors (timeouts, 5xx) remain eligible for later queries.
    A merely *suspected* interstitial (see is_suspected_bot_gate_error) is
    NOT definitive: it takes the refusal cooldown, not the bot-gate skip.
    """
    if is_suspected_bot_gate_error(exc):
        return False
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _BOT_GATE_MARKERS) or bool(
        re.search(r"\b(?:http(?: status)?\s*|status\s*)(?:403|429)\b", text)
    )


class UniversalScraperManager:
    """Run the supplied four-engine search strategy as a resolver lane.

    Baidu and Openverse are NOT in the default set: Baidu answers this
    egress IP with ``{"antiFlag": 1, "message": "Forbid spider access"}``
    and api.openverse.org 504s through the egress proxy (verified live
    2026-11-10). Their engine classes stay available and raise a clear
    error if called directly, so they can be re-enabled if the egress
    IP/proxy situation changes — but production must not spend a worker
    slot on an engine that cannot return results.
    """

    # Canonical engine order: Yandex first (it bot-walls least), then Bing,
    # Flickr, DuckDuckGo. See WEB_ENGINE_PRIORITY in source_broker.py. The
    # manager interleaves results in this order, so Yandex rows surface first
    # and a bot-gated DuckDuckGo never outranks a working Flickr lane.
    DEFAULT_ENGINES = WEB_ENGINE_PRIORITY

    def __init__(
        self,
        *,
        max_workers: int = 6,
        engine_timeout_seconds: float = 10.0,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.max_workers = max(1, min(12, int(max_workers)))
        # Honored as engine latency, not a wave wall (GTA fix, Nov 16): the
        # old 20s constructor clamp was silently bypassed by search()'s
        # 300s ceiling, so the two bounds disagreed. The clamp now agrees
        # with search() — an operator-set timeout is the per-engine budget,
        # and the resolver wave waits for it under liveness gating instead
        # of abandoning the batch at 15s.
        self.engine_timeout_seconds = max(1.0, min(300.0, float(engine_timeout_seconds)))
        self._log_fn = log_fn
        self._engines: Dict[str, _Engine] = {
            "yandex": YandexEngine(),
            "bing": BingEngine(),
            "duckduckgo": DuckDuckGoEngine(),
            "baidu": BaiduEngine(),
            "openverse": OpenverseEngine(),
            "flickr": FlickrEngine(),
        }
        # Reuse one bounded pool for the manager lifetime. Creating a new
        # executor for every query and shutting it down without waiting can
        # leave timed-out HTTP workers running after the resolver has moved
        # on; repeated scenes then accumulate detached workers and late
        # errors. A single ``max_workers`` pool is correct for one search but
        # starves concurrent listicle scene searches: each scene submits six
        # engine tasks and the queued waves cannot start before the scene
        # deadline. Reserve a small number of parallel batches while keeping
        # a hard process-wide cap so this remains safe on slow machines.
        try:
            batch_multiplier = max(
                1,
                min(6, int(os.environ.get("VIDZEO_UNIVERSAL_BATCH_MULTIPLIER", "3") or 3)),
            )
        except (TypeError, ValueError):
            batch_multiplier = 3
        transport_workers = min(36, max(self.max_workers, self.max_workers * batch_multiplier))
        self._executor = ThreadPoolExecutor(
            max_workers=transport_workers,
            thread_name_prefix="universal-scraper",
        )
        self._lock = threading.Lock()
        self._last_response: Optional[UniversalSearchResponse] = None
        # Late-arrival spool (Wright, Nov 16): engines that finish after
        # their batch gave up still hold real candidates — the Wright web
        # run's candidates arrived a minute after the stage had given up
        # and were discarded.  A finished-but-late engine's rows are kept
        # here, keyed by normalized query, so a later re-check (the next
        # search for the same query, or ``collect_late_results``) can still
        # use them instead of re-crawling.  Futures still running past
        # their deadline are tracked in ``_late_pending`` and harvested by
        # the same re-check once they complete.
        self._late_spool: Dict[str, List[UniversalImageResult]] = {}
        self._late_spool_lock = threading.Lock()
        self._late_pending: Dict[str, List[Tuple[str, object]]] = {}
        # Engines that hit a definitive bot challenge or HTTP refusal are
        # cooled down instead of burning their retry/timeout budget once per
        # scene. Keyed by engine name, value is the monotonic time after which
        # that source can be tried again.
        self._bot_gated: Dict[str, float] = {}
        # Re-probe rule (GTA fix, Nov 16): a gated engine gets ONE probe per
        # gate epoch, halfway through its skip. A probe that succeeds clears
        # the gate; one that fails re-gates for a fresh skip. Without this a
        # 15s DDG interstitial disabled the engine for the whole run.
        # _bot_gate_reprobe_due[engine] is the monotonic time a probe opens;
        # _bot_gate_reprobe_spent[engine] records the gate-epoch (the
        # _bot_gated value) the probe was spent on, so one epoch = one probe.
        self._bot_gate_reprobe_due: Dict[str, float] = {}
        self._bot_gate_reprobe_spent: Dict[str, float] = {}
        # Diagnostic kind per gate: "bot_gate" | "http_refusal" |
        # "suspected_interstitial" (a guessed DDG interstitial, short skip).
        self._bot_gate_kind: Dict[str, str] = {}
        # Liveness signal for the resolver wave (GTA fix): monotonic time of
        # the last engine completion (success or failure) in this manager.
        # The wave reads it via last_progress_at / progress_snapshot(); a
        # wave with no engine completing anything for the liveness window
        # is provably dead. Any engine completion refreshes it.
        self._last_progress_at = time.monotonic()
        # Wall of the most recent batch (max per-engine deadline), exposed
        # so the resolver wave knows how long a working batch may take.
        self._last_batch_max_deadline_seconds = 0.0
        try:
            # 900s lost a bot-gated engine for the whole run; 300s plus the
            # halfway re-probe keeps the engine recoverable within one job.
            self._bot_gate_skip_seconds = max(
                0.0,
                float(os.environ.get("VIDZEO_UNIVERSAL_BOT_GATE_SKIP_SECONDS", "300") or 300),
            )
        except (TypeError, ValueError):
            self._bot_gate_skip_seconds = 300.0
        try:
            self._refusal_skip_seconds = max(
                0.0,
                min(
                    600.0,
                    float(os.environ.get("VIDZEO_UNIVERSAL_REFUSAL_SKIP_SECONDS", "60") or 60),
                ),
            )
        except (TypeError, ValueError):
            self._refusal_skip_seconds = 60.0

    @property
    def engines(self) -> Tuple[str, ...]:
        return tuple(self._engines.keys())

    def _log(self, message: str) -> None:
        try:
            if self._log_fn:
                self._log_fn(message)
            else:
                LOGGER.info(message)
        except Exception:
            pass

    def _note_progress(self) -> None:
        """Record an engine completion (success or failure) as wave progress."""
        with self._lock:
            self._last_progress_at = time.monotonic()

    @property
    def last_progress_at(self) -> float:
        """Monotonic timestamp of the last engine completion in this manager.

        Read by the resolver wave (and the driver, via progress_snapshot)
        as the liveness signal: while engines keep completing, the wave is
        alive no matter how slow the proxy is.
        """
        with self._lock:
            return self._last_progress_at

    def progress_snapshot(self) -> Dict[str, object]:
        """Liveness + gate state for the resolver wave / driver (worker D).

        ``last_progress_at`` is a monotonic timestamp; ``gated_engines``
        maps engine → {"until": monotonic expiry, "kind": gate kind}.
        """
        now = time.monotonic()
        with self._lock:
            return {
                "last_progress_at": self._last_progress_at,
                "last_batch_max_deadline_seconds": self._last_batch_max_deadline_seconds,
                "gated_engines": {
                    engine: {
                        "until": until,
                        "kind": self._bot_gate_kind.get(engine, "bot_gate"),
                    }
                    for engine, until in self._bot_gated.items()
                    if until > now
                },
            }

    @property
    def last_batch_max_deadline_seconds(self) -> float:
        """Max per-engine deadline (seconds) of the most recent batch."""
        with self._lock:
            return self._last_batch_max_deadline_seconds

    def _spool_late_results(
        self, query: str, engine: str, rows: Sequence[UniversalImageResult]
    ) -> None:
        """Keep late-arriving rows for a later re-check instead of dropping them."""
        if not rows:
            return
        with self._late_spool_lock:
            spool = self._late_spool.setdefault(query, [])
            seen = {_canonical_url(row.image_url) for row in spool}
            for row in rows:
                key = _canonical_url(row.image_url)
                if not key or key in seen:
                    continue
                seen.add(key)
                spool.append(row)
            del spool[100:]

    def _drain_late_spool(self, query: str) -> List[UniversalImageResult]:
        with self._late_spool_lock:
            return list(self._late_spool.pop(query, []))

    def collect_late_results(
        self, query: str, wait_seconds: float = 0.0
    ) -> List[UniversalImageResult]:
        """Re-check the late spool/queue for ``query`` after a batch wall.

        Waits up to ``wait_seconds`` for engines that were still running
        past their deadline, harvests their rows, and returns every
        salvaged row for the query (spooled + freshly harvested, deduped).
        A still-running engine stays pending for the next re-check.
        """
        clean = re.sub(r"\s+", " ", str(query or "")).strip()
        if not clean:
            return []
        try:
            wait_budget = max(0.0, float(wait_seconds or 0.0))
        except (TypeError, ValueError):
            wait_budget = 0.0
        deadline = time.monotonic() + wait_budget
        while True:
            with self._lock:
                pending = list(self._late_pending.get(clean, []))
            if not any(not future.done() for _, future in pending):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        harvested: List[UniversalImageResult] = []
        with self._lock:
            pending = self._late_pending.pop(clean, [])
        for engine, future in pending:
            if not future.done():
                with self._lock:
                    self._late_pending.setdefault(clean, []).append((engine, future))
                continue
            try:
                _name, rows, _stat = future.result()
            except Exception:
                continue
            harvested.extend(rows or [])
        seen: set = set()
        out: List[UniversalImageResult] = []
        for row in list(harvested) + self._drain_late_spool(clean):
            key = _canonical_url(row.image_url)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(row)
        if out:
            self._log(
                f"[universal] late spool re-check for '{clean[:48]}': "
                f"recovered {len(out)} result(s)"
            )
        return out

    def collect_late_results_for_queries(
        self, queries: Sequence[str], wait_seconds: float = 0.0
    ) -> List[UniversalImageResult]:
        """Re-check the late spool for every query in ``queries`` at once.

        Scene-level harvest (GTA fix): a scene's queries are unique and
        never repeated, so a same-query re-check alone orphans every late
        row. Before the broadened round the resolver harvests all of the
        scene's queries in one call — the wait applies to any listed
        query's still-running engines — instead of re-crawling. No network
        is issued here.
        """
        clean_queries = list(dict.fromkeys(
            re.sub(r"\s+", " ", str(query or "")).strip()
            for query in (queries or [])
        ))
        clean_queries = [query for query in clean_queries if query]
        if not clean_queries:
            return []
        # Wait (bounded) for still-running engines behind ANY listed query,
        # then drain every query's spool. A scene's queries are unique and
        # never repeated, so the harvest must cover all of them — waiting
        # on the first query alone would orphan the rest.
        try:
            wait_budget = max(0.0, float(wait_seconds or 0.0))
        except (TypeError, ValueError):
            wait_budget = 0.0
        deadline = time.monotonic() + wait_budget
        while True:
            with self._lock:
                pending = [
                    future
                    for query in clean_queries
                    for _engine, future in self._late_pending.get(query, [])
                ]
            if not any(not future.done() for future in pending):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        out: List[UniversalImageResult] = []
        seen = set()
        for query in clean_queries:
            for row in self.collect_late_results(query, wait_seconds=0.0):
                key = _canonical_url(row.image_url)
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(row)
        return out

    def _run_engine(
        self,
        engine_name: str,
        clean: str,
        limit: int,
        page: int,
        safe_search: bool,
        batch_finished: threading.Event,
    ) -> Tuple[str, List[UniversalImageResult], UniversalEngineStat]:
        started = time.perf_counter()
        try:
            rows = self._engines[engine_name].search(clean, limit, max(1, int(page)), bool(safe_search))
            duration = int((time.perf_counter() - started) * 1000)
            stat = UniversalEngineStat(engine_name, len(rows), duration, True)
            # A successful probe clears a gate (re-probe rule): the engine
            # proved itself alive again, so the cooldown ends now.
            with self._lock:
                self._bot_gated.pop(engine_name, None)
                self._bot_gate_reprobe_due.pop(engine_name, None)
                self._bot_gate_reprobe_spent.pop(engine_name, None)
                self._bot_gate_kind.pop(engine_name, None)
            self._note_progress()
            if batch_finished.is_set():
                # The rows are NOT discarded: they sit in this future for
                # collect_late_results() to harvest after the batch wall.
                self._log(
                    f"[universal] {engine_name}: late result held for spool collection "
                    f"after batch deadline ({len(rows)} result(s), {duration}ms)"
                )
            else:
                self._log(f"[universal] {engine_name}: {len(rows)} result(s) in {duration}ms")
            return engine_name, rows, stat
        except Exception as exc:
            duration = int((time.perf_counter() - started) * 1000)
            error = f"{type(exc).__name__}: {str(exc)[:160]}"
            # A definitive bot/robot challenge will not clear for the
            # next scene, so skip this engine for the rest of the run
            # instead of paying its retry/timeout budget on every scene.
            # A *suspected* interstitial (DDG's guessed "likely bot-gate"
            # message on two clean empty results) is not a refusal: it
            # takes the short refusal skip, not the long bot-gate skip.
            gate_kind: Optional[str] = None
            skip_seconds: Optional[float] = None
            if is_suspected_bot_gate_error(exc):
                gate_kind = "suspected_interstitial"
                skip_seconds = self._refusal_skip_seconds
            elif is_bot_gate_error(exc):
                is_http_refusal = bool(
                    re.search(r"\b(?:http(?: status)?\s*|status\s*)(?:403|429)\b", error, re.I)
                )
                gate_kind = "http_refusal" if is_http_refusal else "bot_gate"
                skip_seconds = (
                    self._refusal_skip_seconds
                    if is_http_refusal
                    else self._bot_gate_skip_seconds
                )
            if gate_kind is not None and skip_seconds is not None:
                now = time.monotonic()
                with self._lock:
                    gated_until = now + skip_seconds
                    self._bot_gated[engine_name] = gated_until
                    # One re-probe per gate epoch, halfway through the skip.
                    self._bot_gate_reprobe_due[engine_name] = now + skip_seconds / 2.0
                    self._bot_gate_reprobe_spent.pop(engine_name, None)
                    self._bot_gate_kind[engine_name] = gate_kind
                self._log(
                    f"[universal] {engine_name}: temporarily unavailable ({error}); "
                    f"skipping for {skip_seconds:.0f}s ({gate_kind}, "
                    f"re-probe in {skip_seconds / 2.0:.0f}s)"
                )
            self._note_progress()
            if batch_finished.is_set():
                self._log(
                    f"[universal] {engine_name}: late failure ignored after batch deadline "
                    f"({duration}ms, {error})"
                )
            else:
                self._log(f"[universal] {engine_name}: failed in {duration}ms ({error})")
            return engine_name, [], UniversalEngineStat(engine_name, 0, duration, False, error)

    def _collect_batch(
        self,
        futures: Dict[object, str],
        deadlines: Dict[str, float],
        clean: str,
    ) -> Tuple[Dict[str, List[UniversalImageResult]], Dict[str, UniversalEngineStat], List[str]]:
        """Collect one engine wave against staggered per-engine deadlines.

        An engine that finishes after its own deadline has its rows
        salvaged to the late spool instead of merged; an engine still
        running past its deadline is left in ``_late_pending`` for a later
        re-check instead of being cancelled and forgotten.  Returns
        (buckets, stats, timed_out_engine_names).
        """
        buckets: Dict[str, List[UniversalImageResult]] = {}
        stats: Dict[str, UniversalEngineStat] = {}
        timed_out: List[str] = []
        pending = set(futures)
        started = time.monotonic()
        max_deadline = max(deadlines.values()) if deadlines else 0.0
        while pending:
            remaining = max_deadline - (time.monotonic() - started)
            if remaining <= 0:
                break
            done, pending = wait(
                pending, timeout=min(0.25, remaining), return_when=FIRST_COMPLETED
            )
            now = time.monotonic()
            for future in done:
                engine = futures[future]
                try:
                    _name, rows, stat = future.result()
                except Exception as exc:  # pragma: no cover - _run_engine catches
                    rows = []
                    stat = UniversalEngineStat(
                        engine, 0, int((now - started) * 1000), False,
                        f"{type(exc).__name__}: {str(exc)[:160]}",
                    )
                if now - started <= deadlines[engine]:
                    buckets[engine] = rows
                    stats[engine] = stat
                else:
                    self._spool_late_results(clean, engine, rows)
                    stats[engine] = UniversalEngineStat(
                        engine, 0, int((now - started) * 1000), False, "engine timeout"
                    )
                    timed_out.append(engine)
                    self._log(
                        f"[universal] {engine}: arrived after its "
                        f"{deadlines[engine]:.1f}s deadline; {len(rows)} "
                        "result(s) salvaged to the late spool"
                    )
            # Engines whose own staggered deadline passed while still
            # running: stop waiting on them here, but do not cancel — their
            # rows are harvested by collect_late_results() when they land.
            for future in list(pending):
                engine = futures[future]
                if time.monotonic() - started > deadlines[engine]:
                    pending.discard(future)
                    stats[engine] = UniversalEngineStat(
                        engine, 0, int(deadlines[engine] * 1000), False, "engine timeout"
                    )
                    timed_out.append(engine)
                    with self._lock:
                        self._late_pending.setdefault(clean, []).append((engine, future))
                    self._log(
                        f"[universal] {engine}: timed out after "
                        f"{deadlines[engine]:.1f}s (late result will be spooled)"
                    )
        for future in list(pending):
            engine = futures[future]
            if engine not in stats:
                stats[engine] = UniversalEngineStat(
                    engine, 0, int(max_deadline * 1000), False, "engine timeout"
                )
                timed_out.append(engine)
                with self._lock:
                    self._late_pending.setdefault(clean, []).append((engine, future))
        return buckets, stats, timed_out

    def search(
        self,
        query: str,
        *,
        engine_ids: Optional[Sequence[str]] = None,
        limit_per_engine: int = 20,
        page: int = 1,
        safe_search: bool = True,
        timeout_seconds: Optional[float] = None,
        wait_until_complete: bool = False,
    ) -> UniversalSearchResponse:
        clean = re.sub(r"\s+", " ", str(query or "")).strip()
        selected = [str(e).strip().lower() for e in (engine_ids or self.DEFAULT_ENGINES)]
        selected = list(dict.fromkeys(e for e in selected if e in self._engines))
        # Skip engines that hit a definitive bot/robot challenge earlier in
        # this run. A bot-gated engine returns nothing for any further scene,
        # so trying it again only burns its retry budget and a worker slot.
        # Re-probe rule (GTA fix): one probe per gate epoch, halfway through
        # the skip. The probe is spent here (per gate epoch) whether or not
        # it succeeds; a success clears the gate, a failure re-gates.
        self._note_progress()
        now = time.monotonic()
        with self._lock:
            gated = [e for e in selected if self._bot_gated.get(e, 0.0) > now]
            probed: List[str] = []
            for engine in gated:
                due = self._bot_gate_reprobe_due.get(engine, 0.0)
                epoch = self._bot_gated.get(engine, 0.0)
                if now >= due and self._bot_gate_reprobe_spent.get(engine) != epoch:
                    self._bot_gate_reprobe_spent[engine] = epoch
                    probed.append(engine)
            selected = [e for e in selected if e not in gated or e in probed]
        skipped = [e for e in gated if e not in probed]
        if skipped:
            self._log(
                f"[universal] skipping temporarily unavailable engine(s): {','.join(skipped)}"
            )
        if probed:
            self._log(
                f"[universal] re-probing gated engine(s): {','.join(probed)}"
            )
        limit = max(1, min(50, int(limit_per_engine or 20)))
        if not clean or not selected:
            return UniversalSearchResponse(clean, 0, [], [])
        engine_timeout = max(
            0.5,
            min(300.0, float(timeout_seconds or self.engine_timeout_seconds or 6.0))
        )
        # No forced 6s cap here: wait_until_complete means wait for every
        # engine up to the batch timeout. A 6s cap discarded every engine
        # that needed longer than 6s under proxy load (Bing/Yandex return
        # 20 results in ~50s), so batches came back empty. The batch wall
        # itself (engine_timeout) and the stage/delivery walls above it
        # already bound stalled engines.
        #
        # The operator-set engine_timeout is honored as engine latency
        # (GTA fix, Nov 16 — no time limit for web): the resolver wave
        # waits for the batch under liveness gating instead of abandoning
        # it at the old 15s wave deadline, so a generous timeout is a
        # latency budget, not a wall that kills the wave.
        #
        # Wright (Nov 16): the four engines shared ONE wall, so all of them
        # died in ~20s and the candidates that arrived a minute later were
        # discarded ("late result ignored").  Deadlines are now staggered
        # per engine in priority order — the first engine gets the base
        # timeout, each later engine one more stagger step — and a late
        # engine's rows are salvaged to the spool instead of dropped.
        stagger = _universal_engine_stagger_seconds()
        deadlines = {
            engine: engine_timeout + index * stagger
            for index, engine in enumerate(selected)
        }
        max_deadline = max(deadlines.values())
        with self._lock:
            self._last_batch_max_deadline_seconds = max_deadline
        timeout_label = f"{engine_timeout:.1f}s+{stagger:.1f}s/engine"
        self._log(
            f"[universal] batch start query='{clean[:72]}' engines={','.join(selected)} "
            f"parallel={len(selected)} timeout={timeout_label} max={max_deadline:.1f}s"
        )
        # Re-check the spool first: a previous batch's late engines may
        # have landed after that batch gave up.  Those rows are real
        # candidates for this exact query; use them before re-crawling.
        spooled = self.collect_late_results(clean, wait_seconds=0.0)
        batch_finished = threading.Event()
        buckets: Dict[str, List[UniversalImageResult]] = {}
        stats_by_engine: Dict[str, UniversalEngineStat] = {}
        timed_out: List[str] = []
        try:
            futures = {
                self._executor.submit(
                    self._run_engine, engine, clean, limit,
                    max(1, int(page)), bool(safe_search), batch_finished,
                ): engine
                for engine in selected
            }
            buckets, stats_by_engine, timed_out = self._collect_batch(
                futures, deadlines, clean
            )
            # Retry with backoff: an all-empty batch where at least one
            # engine timed out (a transport stall, not a bot gate) gets one
            # more wave at 1.5x timeout.  A batch with any rows at all does
            # not retry — partial success is a result, not a failure.
            if not any(buckets.values()) and timed_out:
                retry_engines = [
                    engine for engine in timed_out
                    if engine in self._engines
                    and self._bot_gated.get(engine, 0.0) <= time.monotonic()
                ]
                if retry_engines:
                    backoff = _universal_empty_retry_backoff_seconds()
                    self._log(
                        f"[universal] empty batch with timed-out engine(s) "
                        f"{','.join(retry_engines)}; retrying once after "
                        f"{backoff:.1f}s backoff"
                    )
                    if backoff:
                        time.sleep(backoff)
                    retry_deadlines = {
                        engine: engine_timeout * 1.5 + index * stagger
                        for index, engine in enumerate(retry_engines)
                    }
                    retry_finished = threading.Event()
                    try:
                        retry_futures = {
                            self._executor.submit(
                                self._run_engine, engine, clean, limit,
                                max(1, int(page)), bool(safe_search), retry_finished,
                            ): engine
                            for engine in retry_engines
                        }
                        retry_buckets, retry_stats, _retry_timed = self._collect_batch(
                            retry_futures, retry_deadlines, clean
                        )
                    finally:
                        retry_finished.set()
                    for engine, rows in retry_buckets.items():
                        if rows:
                            buckets[engine] = rows
                    stats_by_engine.update(retry_stats)
        finally:
            # Do not shut down the shared pool after each batch. Running HTTP
            # calls cannot be force-cancelled by Future.cancel(); closing the
            # executor here created late work that raced interpreter/test
            # teardown. Unfinished calls remain bounded by this manager's
            # fixed worker count and their own socket timeouts, and their
            # rows are harvested from _late_pending by collect_late_results.
            batch_finished.set()

        # Final re-check: engines from this or an earlier batch for this
        # query may have landed while this batch was running.  Harvest
        # them now instead of leaving them for the next scene's query.
        spooled.extend(self.collect_late_results(clean, wait_seconds=0.0))

        # Preserve engine diversity by round-robin interleaving, then remove
        # only exact canonical URL duplicates.  Spooled late rows from an
        # earlier batch for this query go first — they already survived a
        # full engine run — then the fresh batch, deduped against them.
        merged: List[UniversalImageResult] = []
        seen = set()
        for row in spooled:
            key = _canonical_url(row.image_url)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(row)
        max_len = max((len(rows) for rows in buckets.values()), default=0)
        for index in range(max_len):
            for engine in selected:
                rows = buckets.get(engine) or []
                if index >= len(rows):
                    continue
                row = rows[index]
                key = _canonical_url(row.image_url)
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(row)
        stats = [stats_by_engine.get(e, UniversalEngineStat(e, 0, 0, False, "engine timeout")) for e in selected]
        # Host learning (no reordering: engine round-robin order and relevance
        # are untouched). Tag each row with whether its image host is already
        # in the persistent seen-host store, so the resolver can prefer
        # already-approved hosts at download time without spending a fresh
        # per-host approval card. The store is learned from actual downloads
        # (see host_allowlist), never from discovery alone.
        try:
            from .host_allowlist import is_seen_host, load_seen_hosts

            _seen = load_seen_hosts()
            _new_hosts = set()
            annotated: List[UniversalImageResult] = []
            for _row in merged:
                _host = urlparse(_row.image_url).netloc.lower()
                if _host and not is_seen_host(_host, _seen):
                    _new_hosts.add(_host)
                annotated.append(
                    _dc_replace(
                        _row,
                        metadata={**dict(_row.metadata or {}), "host_seen": is_seen_host(_host, _seen)},
                    )
                )
            merged = annotated
            if _new_hosts:
                self._log(f"[universal] batch surfaced {len(_new_hosts)} unseen image host(s)")
        except Exception:
            pass
        response = UniversalSearchResponse(
            clean,
            len(merged),
            merged,
            stats,
            universal_source_diversity(merged),
        )
        with self._lock:
            self._last_response = response
        successes = sum(1 for stat in stats if stat.success)
        _dom = response.source_diversity.get("dominant_host") or "-"
        _dom_share = response.source_diversity.get("dominant_share") or 0.0
        self._log(
            f"[universal] batch complete results={response.total} "
            f"engines={successes}/{len(selected)} "
            f"dominant_source={_dom}:{float(_dom_share):.0%}"
        )
        return response

    async def search_async(self, query: str, **kwargs) -> UniversalSearchResponse:
        return await asyncio.to_thread(self.search, query, **kwargs)


def _result_source_row(row: UniversalImageResult) -> Dict[str, object]:
    """Map a scraper result to the source-host identity mapping."""
    return {
        "url": row.image_url,
        "source_url": row.source_url or row.image_url,
        "page_url": row.source_url or row.image_url,
    }


def universal_source_host_share(
    results: Iterable[UniversalImageResult],
) -> Dict[str, int]:
    """Count of scraper rows per publisher/source host (CDN fronts collapsed).

    ``i.pinimg.com`` image URLs behind Pinterest pin pages count as one
    source (``pinterest.com``); without the collapse a Pinterest-only
    batch looks diverse.
    """
    counts: Dict[str, int] = {}
    for row in results or []:
        host = _distinct_source_host(_result_source_row(row))
        if host:
            counts[host] = counts.get(host, 0) + 1
    return counts


def universal_source_diversity(
    results: Iterable[UniversalImageResult],
) -> Dict[str, object]:
    """Diversity summary for a merged batch: dominant host/share, host counts."""
    rows = list(results or [])
    counts = universal_source_host_share(rows)
    total = sum(counts.values())
    dominant_host, dominant_count, dominant_share = _distinct_dominant_source(
        [_result_source_row(row) for row in rows]
    )
    return {
        "hosts": counts,
        "host_count": len(counts),
        "total": total,
        "dominant_host": dominant_host,
        "dominant_count": dominant_count,
        "dominant_share": dominant_share,
        "needs_more_sources": _distinct_needs_more_sources(
            [_result_source_row(row) for row in rows]
        ),
    }


def universal_needs_more_sources(
    results: Iterable[UniversalImageResult],
    *,
    min_hosts: int = 2,
    max_share: float = 0.6,
) -> bool:
    """Whether a merged batch is too single-source to settle on."""
    rows = [_result_source_row(row) for row in (results or [])]
    return _distinct_needs_more_sources(rows, min_hosts=min_hosts, max_share=max_share)


def universal_result_to_candidate(row: UniversalImageResult, query: str = "") -> Dict[str, object]:
    """Map the standalone scraper result into Vidnets' resolver row contract."""
    engine = row.source_engine.casefold().replace(" ", "_") or "universal"
    title_is_query = bool(
        row.metadata.get("title_is_query")
        or _title_is_query_echo(row.title, query)
    )
    return {
        "url": row.image_url,
        "image_url": row.image_url,
        "preview_url": row.thumbnail_url or row.image_url,
        "thumbnail_url": row.thumbnail_url or row.image_url,
        "source_url": row.source_url or row.image_url,
        "page_url": row.source_url or row.image_url,
        "title": row.title,
        "description": row.description,
        "tags": str(row.metadata.get("tags") or ""),
        "source": "universal_scraper",
        "provider": f"universal_{engine}",
        "query": query,
        "domain": (urlparse(row.source_url or row.image_url).netloc or "").lower(),
        "width": row.width,
        "height": row.height,
        "metadata_pending": True,
        "title_is_query": title_is_query,
        "query_echo": title_is_query,
        "discovery_method": "universal_parallel_scraper",
        "universal_engine": row.source_engine,
        "license": str(row.metadata.get("license") or ""),
        "license_url": str(row.metadata.get("foreign_url") or ""),
        "rights_status": str(row.metadata.get("rights_status") or "unknown"),
        # Set by UniversalScraperManager.search from the persistent
        # seen-host store; the resolver uses it to prefer already-approved
        # hosts at download time. Absent (False) for rows from other lanes.
        "host_seen": bool(row.metadata.get("host_seen")),
    }


__all__ = [
    "UniversalImageResult",
    "UniversalEngineStat",
    "UniversalSearchResponse",
    "UniversalScraperManager",
    "universal_result_to_candidate",
    "universal_source_host_share",
    "universal_source_diversity",
    "universal_needs_more_sources",
]
