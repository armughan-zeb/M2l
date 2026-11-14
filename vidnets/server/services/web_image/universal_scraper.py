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
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from ..job_context import JobThreadPoolExecutor as ThreadPoolExecutor
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
# request starts to the same host, and exponential backoff on 429/403.
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
    """GET through the per-host throttle, with 429/403 backoff retries.

    ``client`` is any object with ``.get(url, headers=..., timeout=...)``
    (the isolated native client, a requests.Session, or the requests
    module). Returns the response-like object (status_code + text/json).
    A 429/403 is retried with exponential backoff; a final non-200 is
    returned as-is so the engine can raise its usual error and the
    manager can record a per-engine failure without losing the batch.
    """
    host = (urlparse(url).netloc or "").lower()
    state = _universal_host_slot(host)
    semaphore = state["semaphore"]
    lock = state["lock"]
    response = None
    for attempt in range(_universal_retry_count() + 1):
        with lock:
            wait = _universal_min_delay() - (time.monotonic() - float(state["last_start"]))
            if wait > 0:
                time.sleep(wait)
            state["last_start"] = time.monotonic()
        with semaphore:
            response = client.get(url, headers=headers, timeout=timeout)
        status = int(getattr(response, "status_code", 0) or 0)
        if status not in (429, 403) or attempt >= _universal_retry_count():
            return response
        time.sleep(_universal_retry_delay(attempt))
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
            return SimpleNamespace(status_code=data['status_code'], text=data['text'])
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
        try:
            response = _throttled_get(self._get_client(), url, headers=_HEADERS, timeout=300)
        except Exception:
            self._local.client = None
            response = _throttled_get(requests, url, headers=_HEADERS, timeout=300)
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
        try:
            response = _throttled_get(self._get_client(), url, headers=_HEADERS, timeout=300)
        except Exception:
            self._local.client = None
            response = _throttled_get(requests, url, headers=_HEADERS, timeout=300)
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
        try:
            response = _throttled_get(self._get_client(), url, headers={**_HEADERS, "Referer": "https://image.baidu.com/"}, timeout=300)
        except Exception:
            self._local.client = None
            response = _throttled_get(requests, url, headers={**_HEADERS, "Referer": "https://image.baidu.com/"}, timeout=300)
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
        try:
            response = _throttled_get(self._get_client(), url, headers={**_HEADERS, "Accept": "application/json"}, timeout=300)
        except Exception:
            self._local.client = None
            response = _throttled_get(requests, url, headers={**_HEADERS, "Accept": "application/json"}, timeout=300)
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
        tags = urllib.parse.quote(query.replace(" ", ","))
        url = f"https://www.flickr.com/services/feeds/photos_public.gne?tags={tags}&format=json&nojsoncallback=1"
        try:
            response = _throttled_get(self._get_client(), url, headers=_HEADERS, timeout=300)
        except Exception:
            self._local.client = None
            response = _throttled_get(requests, url, headers=_HEADERS, timeout=300)
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


def is_bot_gate_error(exc: BaseException) -> bool:
    """True when an engine failure is a definitive bot/robot challenge.

    A bot-gated engine will not return results for any further scene in this
    run (DuckDuckGo's 202 anomaly interstitial, Yandex SmartCaptcha), so the
    manager skips it instead of paying its retry/timeout budget once per
    scene. Ordinary transport errors (timeouts, 5xx) are NOT bot-gates and
    keep their normal per-query retry behaviour.
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _BOT_GATE_MARKERS)


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
        self.engine_timeout_seconds = max(1.0, min(20.0, float(engine_timeout_seconds)))
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
        # Engines that hit a definitive bot/robot challenge are skipped for
        # the rest of this run instead of burning their retry/timeout budget
        # once per scene. Keyed by engine name, value is the monotonic time
        # after which the engine may be tried again (a gated egress IP
        # rarely recovers mid-job; a fresh manager starts clean).
        self._bot_gated: Dict[str, float] = {}
        try:
            self._bot_gate_skip_seconds = max(
                0.0,
                float(os.environ.get("VIDZEO_UNIVERSAL_BOT_GATE_SKIP_SECONDS", "900") or 900),
            )
        except (TypeError, ValueError):
            self._bot_gate_skip_seconds = 900.0

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
        now = time.monotonic()
        with self._lock:
            gated = [e for e in selected if self._bot_gated.get(e, 0.0) > now]
            selected = [e for e in selected if e not in gated]
        if gated:
            self._log(
                f"[universal] skipping bot-gated engine(s) for this run: {','.join(gated)}"
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
        timeout_label = f"{engine_timeout:.1f}s"
        self._log(
            f"[universal] batch start query='{clean[:72]}' engines={','.join(selected)} "
            f"parallel={len(selected)} timeout={timeout_label}"
        )
        batch_finished = threading.Event()

        def run(engine_name: str) -> Tuple[str, List[UniversalImageResult], UniversalEngineStat]:
            started = time.perf_counter()
            try:
                rows = self._engines[engine_name].search(clean, limit, max(1, int(page)), bool(safe_search))
                duration = int((time.perf_counter() - started) * 1000)
                stat = UniversalEngineStat(engine_name, len(rows), duration, True)
                if batch_finished.is_set():
                    self._log(
                        f"[universal] {engine_name}: late result ignored after batch deadline "
                        f"({len(rows)} result(s), {duration}ms)"
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
                if is_bot_gate_error(exc):
                    with self._lock:
                        self._bot_gated[engine_name] = (
                            time.monotonic() + self._bot_gate_skip_seconds
                        )
                    self._log(
                        f"[universal] {engine_name}: bot-gated ({error}); "
                        f"skipping for {self._bot_gate_skip_seconds:.0f}s"
                    )
                if batch_finished.is_set():
                    self._log(
                        f"[universal] {engine_name}: late failure ignored after batch deadline "
                        f"({duration}ms, {error})"
                    )
                else:
                    self._log(f"[universal] {engine_name}: failed in {duration}ms ({error})")
                return engine_name, [], UniversalEngineStat(engine_name, 0, duration, False, error)

        buckets: Dict[str, List[UniversalImageResult]] = {e: [] for e in selected}
        stats_by_engine: Dict[str, UniversalEngineStat] = {}
        # ``requests`` and the optional clients enforce their own socket
        # timeouts.  The manager timeout is a wall-clock guard around each
        # future so one stalled engine cannot delay the batch indefinitely.
        futures = {self._executor.submit(run, engine): engine for engine in selected}
        try:
            for future in as_completed(
                futures,
                timeout=engine_timeout,
            ):
                engine, rows, stat = future.result()
                buckets[engine] = rows
                stats_by_engine[engine] = stat
        except (TimeoutError, FuturesTimeoutError):
            for future, engine in futures.items():
                if not future.done():
                    future.cancel()
                    stats_by_engine[engine] = UniversalEngineStat(
                        engine, 0, int(engine_timeout * 1000), False, "engine timeout"
                    )
                    self._log(f"[universal] {engine}: timed out after {engine_timeout:.1f}s")
        finally:
            # Do not shut down the shared pool after each batch. Running HTTP
            # calls cannot be force-cancelled by Future.cancel(); closing the
            # executor here created late work that raced interpreter/test
            # teardown. Unfinished calls remain bounded by this manager's
            # fixed worker count and their own socket timeouts.
            batch_finished.set()

        # Preserve engine diversity by round-robin interleaving, then remove
        # only exact canonical URL duplicates.
        merged: List[UniversalImageResult] = []
        seen = set()
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
        response = UniversalSearchResponse(clean, len(merged), merged, stats)
        with self._lock:
            self._last_response = response
        successes = sum(1 for stat in stats if stat.success)
        self._log(
            f"[universal] batch complete results={response.total} "
            f"engines={successes}/{len(selected)}"
        )
        return response

    async def search_async(self, query: str, **kwargs) -> UniversalSearchResponse:
        return await asyncio.to_thread(self.search, query, **kwargs)


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
]
