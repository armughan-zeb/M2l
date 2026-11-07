"""Fast, best-effort Yandex Images adapter.

Yandex currently embeds the image result graph in a JSON ``data-state``
attribute.  The older ``data-bem``/``serp-item`` markup is retained as a
compatibility fallback because regional responses and fixtures can still use
it.  Search failures are intentionally fail-soft; other providers continue
serving the scene.
"""
from __future__ import annotations

import json
import os
import re
import html as _html
import shutil
import subprocess
import threading
import time
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

import requests

from .base_source import BaseSource, ImageCandidate, _accept_language


class _StateAttributeParser(HTMLParser):
    """Collect JSON-bearing Yandex state attributes without regex balancing."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.states: List[str] = []

    def handle_starttag(self, _tag: str, attrs: Iterable[tuple]) -> None:
        for key, value in attrs:
            if key in {"data-state", "data-bem"} and value:
                if "origUrl" in value or "serp-item" in value or "serpList" in value:
                    self.states.append(value)


class YandexSource(BaseSource):
    name = "yandex"
    trust_score = 0.45
    tier = 4
    max_results_per_query = 12
    timeout = 4.0
    # Robot responses are session/fingerprint dependent; retry after a short
    # cooldown instead of suppressing this source for the entire job lifetime.
    circuit_cooldown_seconds = 90.0
    SEARCH_URL = "https://yandex.com/images/search"
    SEARCH_URL_RU = "https://yandex.ru/images/search"
    _LEGACY_ITEM_RE = re.compile(r'data-bem="(\{[^\"]*?serp-item[^\"]*?\})"')
    # Browser-rendered Yandex cards expose the original image in the card
    # link (``img_url=...``), even when the embedded JSON schema is absent or
    # has changed. Keep this as a transport-level fallback, not a separate
    # provider, so the normal metadata and safety gates still apply.
    _IMAGE_LINK_RE = re.compile(
        r'<a[^>]+href=["\']([^"\']*?(?:img_url|url)=.*?)["\']',
        re.IGNORECASE,
    )
    _browser_cache: Dict[str, tuple[float, List[ImageCandidate]]] = {}
    _browser_cache_lock = threading.Lock()
    # Short-lived memory cache keeps repeated scene searches off the network.
    # It is deliberately process-local and contains only parsed candidates,
    # never cookies or account state.
    _result_cache: Dict[str, tuple[float, List[ImageCandidate]]] = {}
    _result_cache_lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__()
        self.session = self._create_session()
        self._state_lock = threading.Lock()
        self._last_schema_failure = 0.0
        self._robot_failure_count = 0
        # Yandex challenge state is process/session scoped. A single-flight
        # gate prevents every scene worker from issuing the same challenged
        # request concurrently before the first worker can open the circuit.
        self._search_inflight = False

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": _accept_language(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Referer": "https://yandex.com/images/",
            "Cache-Control": "max-age=0",
            "Pragma": "no-cache",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1",
        })
        return session

    def is_available(self) -> bool:
        if str(os.environ.get("VIDZEO_YANDEX_IMAGES", "1")).strip().lower() not in {"1", "true", "yes", "on"}:
            return False
        with self._state_lock:
            if self._circuit_open:
                if time.time() - self._last_failure_time > self.circuit_cooldown_seconds:
                    self._circuit_open = False
                    self._failure_count = 0
                else:
                    return False
            return True

    def _record_schema_failure(self) -> None:
        with self._state_lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            self._last_schema_failure = self._last_failure_time
            if self._failure_count >= 3:
                self._circuit_open = True

    def _record_success(self) -> None:
        with self._state_lock:
            self._failure_count = 0
            self._circuit_open = False
            self._robot_failure_count = 0

    def search(
        self,
        query: str,
        limit: int = 20,
        filters: Optional[Dict] = None,
    ) -> List[ImageCandidate]:
        with self._state_lock:
            if self._search_inflight:
                return []
            self._search_inflight = True
        try:
            return self._search_impl(query, limit=limit, filters=filters)
        finally:
            with self._state_lock:
                self._search_inflight = False

    def _search_impl(
        self,
        query: str,
        limit: int = 20,
        filters: Optional[Dict] = None,
    ) -> List[ImageCandidate]:
        del filters
        clean = self._clean_query(query, max_words=8)
        if not clean:
            return []

        cache_ttl = self._cache_ttl()
        if cache_ttl > 0:
            with self._result_cache_lock:
                cached = self._result_cache.get(clean.casefold())
                if cached and time.time() - cached[0] < cache_ttl:
                    return list(cached[1][:limit])
        if not self.is_available():
            return []

        params = {"text": clean, "isize": "large", "nomisspell": "1"}
        region_fallback = str(
            os.environ.get("VIDZEO_YANDEX_REGION_FALLBACK", "1")
        ).strip().lower() in {"1", "true", "yes", "on"}
        urls = [self.SEARCH_URL]
        if region_fallback:
            urls.extend([self.SEARCH_URL_RU, "https://yandex.com.tr/images/search"])
        last_error = ""
        robot_seen = False

        # curl_cffi speaks HTTP/2 and impersonates Chrome's TLS fingerprint.
        # It is much faster than launching a browser and carries no cookies.
        # A caller may replace ``session.get`` with a deterministic transport
        # (the normal pattern in integration tests).  In that case the
        # replacement is the source of truth; bypass optional transports that
        # would otherwise escape the mock and hit the network.
        mocked_transport = self._is_mocked_transport()
        fast_candidates, fast_robot, fast_error = (
            ([], False, "") if mocked_transport
            else self._fast_http_search(clean, params, limit)
        )
        if fast_candidates:
            self._store_result_cache(clean, fast_candidates)
            self._record_success()
            return fast_candidates
        robot_seen = robot_seen or fast_robot
        last_error = fast_error or last_error
        for url in urls:
            for attempt in range(1):
                try:
                    response = self.session.get(
                        url,
                        params=params,
                        timeout=float(self.timeout),
                    )
                    if response.status_code != 200:
                        last_error = f"HTTP {response.status_code}"
                        continue
                    if re.search(r"<title[^>]*>\s*Are you not a robot\?", response.text, re.I):
                        last_error = "robot_check"
                        robot_seen = True
                        continue
                    candidates = self._parse_response(response.text, clean, limit)
                    if candidates:
                        self._store_result_cache(clean, candidates)
                        self._record_success()
                        return candidates
                    last_error = "empty parse (schema drift?)"
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    continue
                break

        # Requests-based sessions are often challenged while a real Chrome
        # session on the same machine is allowed through. Use the bundled
        # Playwright bridge as a bounded fallback so Yandex remains useful
        # without requiring a paid proxy or an official API key.
        if robot_seen and not mocked_transport:
            browser_candidates = self._browser_search(clean, limit)
            if browser_candidates:
                self._store_result_cache(clean, browser_candidates)
                self._record_success()
                return browser_candidates

        if last_error and str(os.environ.get("VIDZEO_WEB_VERBOSE", "0")).strip().lower() in {"1", "true", "yes", "on"}:
            print(f"[yandex] search failed for '{clean[:40]}': {last_error}")
        if robot_seen:
            # A robot page can be transient and differs between browser and
            # server sessions. Keep the source unavailable for this resolver
            # pass after an all-region challenge, while the short circuit
            # cooldown lets a later browser-like session try again.
            with self._state_lock:
                self._robot_failure_count = getattr(self, "_robot_failure_count", 0) + 1
                self._circuit_open = True
                self._last_failure_time = time.time()
            print(
                "[yandex] Robot check returned; pausing this Yandex session and "
                "retrying later with browser-like headers. Other image sources remain active.",
                flush=True,
            )
        else:
            self._record_schema_failure()
        return []

    def _is_mocked_transport(self) -> bool:
        """Return whether the session transport was replaced by a test double."""
        getter = getattr(self.session, "get", None)
        module = str(getattr(type(getter), "__module__", ""))
        name = str(getattr(type(getter), "__name__", ""))
        return module.startswith("unittest.mock") or name in {"Mock", "MagicMock"}

    @staticmethod
    def _cache_ttl() -> float:
        try:
            return max(0.0, min(900.0, float(os.environ.get("VIDZEO_YANDEX_CACHE_TTL_SECONDS", "300"))))
        except (TypeError, ValueError):
            return 300.0

    @classmethod
    def _store_result_cache(cls, query: str, candidates: List[ImageCandidate]) -> None:
        if not candidates or cls._cache_ttl() <= 0:
            return
        with cls._result_cache_lock:
            cls._result_cache[query.casefold()] = (time.time(), list(candidates))

    def _fast_http_search(self, query: str, params: Dict[str, str], limit: int) -> tuple[List[ImageCandidate], bool, str]:
        """Try a Chrome-fingerprinted HTTP/2 request before requests/browser."""
        enabled = str(os.environ.get("VIDZEO_YANDEX_CURL_IMPERSONATE", "0") or "0").strip().lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return [], False, ""
        try:
            from curl_cffi import requests as curl_requests
            impersonate = str(os.environ.get("VIDZEO_YANDEX_CURL_PROFILE", "chrome") or "chrome").strip()
            session = curl_requests.Session(impersonate=impersonate)
            session.headers.update(dict(self.session.headers))
            # Yandex ranks the first page very heavily toward one publisher.
            # Pull a few inexpensive pagination pages in the same
            # Chrome-fingerprinted session so a scene receives a real pool of
            # distinct images instead of the same top result for every query.
            merged: list[ImageCandidate] = []
            seen_urls: set[str] = set()
            robot = False
            for page in range(3):
                page_params = dict(params)
                page_params["p"] = str(page)
                response = session.get(self.SEARCH_URL, params=page_params, timeout=float(self.timeout))
                if response.status_code != 200:
                    if page == 0:
                        return [], False, f"HTTP {response.status_code}"
                    continue
                body = str(response.text or "")
                if re.search(r"<title[^>]*>\s*Are you not a robot\?", body, re.I):
                    robot = True
                    continue
                for candidate in self._parse_response(body, query, limit):
                    key = str(candidate.download_url or candidate.preview_url or "").strip().casefold()
                    if not key or key in seen_urls:
                        continue
                    seen_urls.add(key)
                    merged.append(candidate)
                    if len(merged) >= limit:
                        break
                if len(merged) >= limit:
                    break
            return merged[:limit], robot, "" if merged else "empty parse (schema drift?)"
        except Exception as exc:
            # Optional transport: a missing wheel or transient TLS failure
            # should fall through to the proven requests/browser lanes.
            return [], False, f"{type(exc).__name__}: {exc}"

    def _browser_search(self, query: str, limit: int) -> List[ImageCandidate]:
        """Fetch Yandex through a local Chrome instance when HTTP is challenged."""
        # Keep the library default deterministic for callers that import the
        # source directly (including offline/unit-test harnesses).  The app's
        # checked-in defaults explicitly enable this bounded fallback.
        if str(os.environ.get("VIDZEO_YANDEX_BROWSER_FALLBACK", "0") or "0").strip().lower() not in {"1", "true", "yes", "on"}:
            return []
        bridge = os.path.join(os.path.dirname(__file__), "yandex_browser_bridge.js")
        node = os.environ.get("VIDZEO_NODE_PATH") or shutil.which("node")
        if not node or not os.path.isfile(bridge):
            return []
        cache_key = self._clean_query(query, max_words=8).casefold()
        with self._browser_cache_lock:
            cached = self._browser_cache.get(cache_key)
            if cached and time.time() - cached[0] < 120.0:
                return list(cached[1][:limit])
        try:
            completed = subprocess.run(
                [node, bridge, query],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(12.0, min(40.0, float(os.environ.get("VIDZEO_YANDEX_BROWSER_TIMEOUT", "30")))),
                check=False,
            )
            if completed.returncode != 0 or not completed.stdout.strip():
                return []
            payload = json.loads(completed.stdout)
            if str(payload.get("challenge") or "").lower() in {"1", "true", "yes"}:
                return []
            fragments = []
            for item in payload.get("rows") or []:
                if not isinstance(item, dict):
                    continue
                state = item.get("state") or item.get("bem")
                href = item.get("href")
                if state:
                    fragments.append(f'<div data-state="{_html.escape(str(state), quote=True)}"></div>')
                elif href:
                    fragments.append(f'<a href="{_html.escape(str(href), quote=True)}"></a>')
            if not fragments:
                return []
            candidates = self._parse_response("".join(fragments), query, limit)
            if candidates:
                with self._browser_cache_lock:
                    self._browser_cache[cache_key] = (time.time(), list(candidates))
            return candidates
        except Exception:
            return []

    def _parse_response(self, html: str, query: str, limit: int) -> List[ImageCandidate]:
        parser = _StateAttributeParser()
        parser.feed(str(html or ""))
        rows: List[Dict[str, Any]] = []
        for raw in parser.states:
            try:
                data = json.loads(raw)
            except Exception:
                continue
            rows.extend(self._find_result_rows(data))

        # Keep the old fixture/region shape working if data-state is absent.
        if not rows:
            from html import unescape
            for match in self._LEGACY_ITEM_RE.finditer(str(html or "")):
                try:
                    rows.append(json.loads(unescape(match.group(1))))
                except Exception:
                    continue

        # Browser cards also carry a stable ``img_url`` query parameter. This
        # fallback makes the scraper resilient to JS/schema changes that leave
        # the links in the response but remove data-state/data-bem blobs.
        if not rows:
            from html import unescape
            for match in self._IMAGE_LINK_RE.finditer(str(html or "")):
                raw_href = unescape(match.group(1))
                parsed = parse_qs(urlparse(raw_href).query)
                image_url = (parsed.get("img_url") or parsed.get("url") or [""])[0]
                image_url = unquote(image_url)
                if image_url.startswith(("http://", "https://")):
                    rows.append({"origUrl": image_url, "snippet": {"url": raw_href}})

        candidates: List[ImageCandidate] = []
        seen = set()
        for row in rows:
            candidate = self._row_to_candidate(row, query)
            if not candidate:
                continue
            key = candidate.download_url.split("#", 1)[0]
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
            if len(candidates) >= max(1, min(int(limit or self.max_results_per_query), self.max_results_per_query)):
                break
        return candidates

    @classmethod
    def _find_result_rows(cls, value: Any, _depth: int = 0) -> List[Dict[str, Any]]:
        """Recursively extract image result rows from Yandex JSON state blobs.

        Handles multiple known Yandex HTML response shapes:
        - Legacy data-bem: {"serp-item": {"origUrl": ...}}
        - Modern data-state serpList: {"serpList": {"items": {"entities": [...]}}}
        - Modern initialState images array: {"images": [{"origUrl": ...}]}
        """
        if _depth > 12:
            return []
        rows: List[Dict[str, Any]] = []
        if isinstance(value, dict):
            # Direct image record
            if value.get("origUrl") or value.get("orig_url"):
                rows.append(value)
                return rows  # Don't recurse into an already-found row
            # Legacy serp-item wrapper
            item = value.get("serp-item")
            if isinstance(item, dict):
                rows.extend(cls._find_result_rows(item, _depth + 1))
            # Modern serpList shape: {"items": {"entities": {"id1": {...}, "id2": {...}}}} or list
            serp_list = value.get("serpList")
            if isinstance(serp_list, dict):
                entities = (serp_list.get("items") or {}).get("entities") or {}
                if isinstance(entities, dict):
                    for ent in entities.values():
                        rows.extend(cls._find_result_rows(ent, _depth + 1))
                elif isinstance(entities, list):
                    for ent in entities:
                        rows.extend(cls._find_result_rows(ent, _depth + 1))
            # Modern initialState images array
            images_list = value.get("images")
            if isinstance(images_list, list):
                for img in images_list:
                    rows.extend(cls._find_result_rows(img, _depth + 1))
            # General recursive scan for remaining keys
            for key, child in value.items():
                if key in {"serp-item", "serpList", "images"}:
                    continue  # Already handled above
                rows.extend(cls._find_result_rows(child, _depth + 1))
        elif isinstance(value, list):
            for child in value:
                rows.extend(cls._find_result_rows(child, _depth + 1))
        return rows


    def _row_to_candidate(self, row: Dict[str, Any], query: str) -> Optional[ImageCandidate]:
        if "serp-item" in row and isinstance(row.get("serp-item"), dict):
            row = row["serp-item"]
        image = row.get("image") if isinstance(row.get("image"), dict) else {}
        image_url = self._normalize_url(
            row.get("origUrl") or row.get("orig_url") or image.get("origUrl") or image.get("url") or ""
        )
        if not image_url or urlparse(image_url).scheme not in {"http", "https"}:
            return None
        preview = row.get("preview") if isinstance(row.get("preview"), dict) else {}
        preview_url = self._normalize_url(
            (preview.get("url") if isinstance(preview, dict) else "")
            or row.get("previewUrl")
            or row.get("preview_url")
            or row.get("thumbnailUrl")
            or row.get("thumbnail_url")
            or image.get("previewUrl")
            or image.get("preview_url")
        ) or image_url
        snippet = row.get("snippet") if isinstance(row.get("snippet"), dict) else {}
        title = str(row.get("alt") or snippet.get("title") or row.get("title") or "").strip()
        page_url = self._normalize_url(snippet.get("url") or "")
        domain = str(snippet.get("domain") or self._extract_domain(page_url or image_url)).lower()
        width = self._int(row.get("origWidth") or row.get("width") or image.get("width"))
        height = self._int(row.get("origHeight") or row.get("height") or image.get("height"))
        # Keep retrieval intent in the caller's ``query_used`` field.  A
        # provider-generated string such as ``Yandex result for: <query>`` is
        # not a description of the returned pixels and must never become
        # semantic identity evidence.
        description = str(
            snippet.get("description")
            or row.get("description")
            or ""
        ).strip()
        title_norm = re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()
        query_norm = re.sub(r"[^a-z0-9]+", " ", str(query or "").casefold()).strip()
        title_is_query = bool(
            not title_norm
            or title_norm == query_norm
            or (
                query_norm
                and title_norm.startswith(query_norm + " ")
                and any(marker in title_norm[len(query_norm):] for marker in ("browse", "download", "yandex", "search"))
            )
        )
        return ImageCandidate(
            id=f"yandex_{abs(hash(image_url)) % 10000000}",
            source=self.name,
            title=title[:200],
            description=description[:500],
            download_url=image_url,
            preview_url=preview_url,
            width=width,
            height=height,
            media_type="image",
            license="Unknown",
            metadata={
                "domain": domain,
                "page_url": page_url,
                "foreign_url": page_url,
                "rights_status": "unknown",
                "rights_complete": False,
                "title_is_query": title_is_query,
            },
        )

    # Backward-compatible name used by older tests/callers.
    def _blob_to_candidate(self, data: Dict, query: str) -> Optional[ImageCandidate]:
        return self._row_to_candidate(data, query)

    @staticmethod
    def _int(value: Any) -> int:
        try:
            return max(0, int(float(value or 0)))
        except Exception:
            return 0

    @staticmethod
    def _extract_domain(url: str) -> str:
        return (urlparse(url).netloc or "").lower().removeprefix("www.")


def search_yandex(query: str, limit: int = 12) -> List[Dict]:
    return [item.to_dict() for item in YandexSource().search(query, limit=limit)]


if __name__ == "__main__":
    results = YandexSource().search("cricket stadium", limit=5)
    print(f"Found {len(results)} Yandex results")
