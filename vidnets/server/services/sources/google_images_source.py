"""Bounded, best-effort Google Images HTML discovery.

This is a normal unauthenticated search-page request, not an anti-bot bypass.
It never solves challenges, rotates identities, or retries rate limits.  When
Google does not return a usable page the provider fails soft and the official
Google CSE/other image providers continue.
"""
from __future__ import annotations

import hashlib
import html
import os
import re
import threading
import time
from typing import Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

import requests

from .base_source import BaseSource, ImageCandidate, _accept_language


_IMGURL_RE = re.compile(r"(?:imgurl|mediaurl)=([^&\"']+)", re.I)
_JSON_IMAGE_RE = re.compile(
    r'["\'](https?://[^"\']+?\.(?:jpe?g|png|webp)(?:\?[^"\']*)?)["\']',
    re.I,
)


class GoogleImagesSource(BaseSource):
    name = "google_images"
    trust_score = 0.45
    tier = 4
    max_results_per_query = 12
    timeout = 5.0
    SEARCH_URL = "https://www.google.com/search"
    circuit_cooldown_seconds = 10 * 60.0

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept-Language": _accept_language(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

    def is_available(self) -> bool:
        enabled = str(os.environ.get("VIDZEO_WEB_GOOGLE_IMAGES", "1")).lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return False
        with self._lock:
            if self._circuit_open:
                if time.time() - self._last_failure_time < self.circuit_cooldown_seconds:
                    return False
                self._circuit_open = False
                self._failure_count = 0
        return True

    def search(
        self,
        query: str,
        limit: int = 12,
        filters: Optional[Dict] = None,
    ) -> List[ImageCandidate]:
        del filters
        clean = self._clean_query(query, max_words=10)
        if not clean or not self.is_available():
            return []
        try:
            response = self.session.get(
                self.SEARCH_URL,
                params={"tbm": "isch", "q": clean, "safe": "active"},
                timeout=float(os.environ.get("VIDZEO_GOOGLE_IMAGES_TIMEOUT_SECONDS", "5") or 5),
            )
            if response.status_code != 200:
                self.record_failure()
                return []
            markup = response.text
            # Google serves a JS bootstrap shell (enablejs redirect) instead of
            # result markup to non-JS clients.  Nothing parseable is inside.
            if (
                "/httpservice/retry/enablejs" in markup
                or "Please click" in markup
                or ("AF_initDataCallback" not in markup and "imgurl=" not in markup.lower())
            ):
                self.record_failure()
                return []
            rows = self._parse_response(markup, clean, limit)
            if rows:
                self.record_success()
            else:
                self.record_failure()
            return rows
        except requests.RequestException:
            self.record_failure()
            return []

    def _parse_response(self, markup: str, query: str, limit: int) -> List[ImageCandidate]:
        decoded = html.unescape(str(markup or "")).replace("\\u003d", "=").replace("\\u0026", "&")
        urls: List[str] = []
        urls.extend(unquote(match.group(1)) for match in _IMGURL_RE.finditer(decoded))
        urls.extend(match.group(1).replace("\\/", "/") for match in _JSON_IMAGE_RE.finditer(decoded))
        seen = set()
        results: List[ImageCandidate] = []
        cap = max(1, min(int(limit or 12), self.max_results_per_query))
        for raw in urls:
            url = self._normalize_url(raw)
            parsed = urlparse(url)
            domain = (parsed.netloc or "").lower()
            if not url or not domain or domain.endswith(("google.com", "gstatic.com", "googleusercontent.com")):
                continue
            canonical = parsed._replace(fragment="").geturl()
            if canonical in seen:
                continue
            seen.add(canonical)
            digest = hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest()[:14]
            results.append(ImageCandidate(
                id=f"google_images_{digest}",
                source=self.name,
                title="",
                description="Google Images discovery result",
                download_url=canonical,
                preview_url=canonical,
                license="Unknown",
                query=query,
                metadata={
                    "publisher_domain": domain,
                    "rights_status": "unknown",
                    "rights_complete": False,
                    "retrieval_method": "google_images_html",
                },
            ))
            if len(results) >= cap:
                break
        return results


def search_google_images(query: str, limit: int = 12) -> List[Dict]:
    return [row.to_dict() for row in GoogleImagesSource().search(query, limit=limit)]
