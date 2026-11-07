"""
Pixabay Source — HTML scraper fallback for when API key is missing.

Scrapes Pixabay search page for image/video results.
"""
from __future__ import annotations

import json
import re
import time
import requests
from typing import Dict, List, Optional
from urllib.parse import quote_plus

from .base_source import BaseSource, ImageCandidate, _accept_language

_SEARCH_URL = "https://pixabay.com/photos/search/{query}/"
_VIDEO_SEARCH_URL = "https://pixabay.com/videos/search/{query}/"
_TIMEOUT = 12

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": _accept_language(),
}

_RE_IMAGE_ITEM = re.compile(
    r'<div[^>]*class="[^"]*item[^"]*"[^>]*>.*?'
    r'<img[^>]*src="([^"]+)"[^>]*.*?'
    r'alt="([^"]*)"',
    re.DOTALL,
)

_RE_VIDEO_ITEM = re.compile(
    r'<video[^>]*poster="([^"]+)"[^>]*>.*?'
    r'<source[^>]*src="([^"]+)"[^>]*>',
    re.DOTALL,
)

_RE_JSON_DATA = re.compile(
    r'<script[^>]*id="__NEXT_DATA__"[^>]*type="application/json"[^>]*>(.*?)</script>',
    re.DOTALL,
)


class PixabaySource(BaseSource):
    name = "pixabay"
    trust_score = 0.65
    tier = 2
    max_results_per_query = 24
    CACHE_SECONDS = 24 * 60 * 60

    def __init__(self, api_key: str = "", config: Optional[Dict] = None, **kwargs):
        super().__init__(api_key=api_key, config=config, **kwargs)
        self.session = requests.Session()
        self._api_cache = {}
        self.timeout = max(1.0, min(float(_TIMEOUT), float(self.config.get('timeout', _TIMEOUT))))

    def _active(self):
        event = self.config.get('cancel_event')
        deadline = self.config.get('deadline_at')
        return not (event is not None and event.is_set()) and (deadline is None or time.monotonic() < deadline)

    def search(
        self,
        query: str,
        limit: int = 20,
        filters: Optional[Dict] = None,
    ) -> List[ImageCandidate]:
        if not self.is_available() or not self._active():
            return []
        cache_key = self._clean_query(query)
        cached = self._api_cache.get(cache_key)
        if self.api_key and cached and time.time() - cached[0] < self.CACHE_SECONDS:
            return cached[1][:limit]
        candidates = []
        try:
            candidates = self._api_search(query, limit) if self.api_key else []
        except requests.RequestException:
            candidates = []
        if self.api_key:
            self._api_cache[cache_key] = (time.time(), candidates)
        if not candidates and not self.api_key:
            try:
                candidates = self._scrape_next_data(query)
            except Exception:
                pass
            if not candidates and self._active():
                try:
                    candidates = self._scrape_html(query)
                except Exception:
                    pass
        if not candidates:
            self.record_failure()
        else:
            self.record_success()
        return candidates[:limit]

    def _api_search(self, query: str, limit: int) -> List[ImageCandidate]:
        response = self.session.get(
            "https://pixabay.com/api/",
            params={
                "key": self.api_key, "q": self._clean_query(query),
                "per_page": max(3, min(limit, self.max_results_per_query)),
                "safesearch": "true", "orientation": "horizontal",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        results = []
        for item in response.json().get("hits", [])[:limit]:
            download_url = item.get("largeImageURL") or item.get("webformatURL") or ""
            preview_url = item.get("previewURL") or download_url
            if not download_url:
                continue
            user = str(item.get("user") or "").strip()
            foreign_url = str(item.get("pageURL") or "").strip()
            results.append(ImageCandidate(
                id=f"pixabay_{item.get('id')}", source="pixabay",
                title=str(item.get("tags") or ""), description=str(item.get("tags") or ""),
                download_url=download_url, preview_url=preview_url,
                width=int(item.get("imageWidth") or item.get("webformatWidth") or 0),
                height=int(item.get("imageHeight") or item.get("webformatHeight") or 0),
                license="Pixabay Content License", query=query,
                metadata={"attribution": f"{user} on Pixabay" if user else "Pixabay", "foreign_url": foreign_url, "user": user},
            ))
        return results

    def _scrape_next_data(self, query: str) -> List[ImageCandidate]:
        url = _SEARCH_URL.format(query=quote_plus(query))
        try:
            if not self._active():
                return []
            resp = requests.get(url, headers=_HEADERS, timeout=self.timeout)
            if resp.status_code != 200:
                return []
            html = resp.text
        except requests.RequestException:
            return []

        match = _RE_JSON_DATA.search(html)
        if not match:
            return []

        try:
            data = json.loads(match.group(1))
            items = (
                data.get("props", {})
                .get("pageProps", {})
                .get("searchResults", {})
                .get("items", [])
            )
        except (json.JSONDecodeError, AttributeError):
            return []

        results = []
        seen_ids: set = set()
        for item in items:
            item_id = str(item.get("id", ""))
            if not item_id or item_id in seen_ids:
                continue
            seen_ids.add(item_id)

            preview_url = ""
            download_url = ""
            if item.get("previewURL"):
                preview_url = item["previewURL"]
                download_url = item.get("largeImageURL") or item.get("webformatURL") or preview_url
            elif item.get("videos"):
                best = self._best_video(item["videos"])
                if best:
                    download_url = best
                    preview_url = item.get("previewURL") or item.get("userImageURL") or ""

            if not download_url:
                continue

            candidate = ImageCandidate(
                id=f"pixabay_{item_id}",
                source="pixabay",
                title=item.get("tags", ""),
                description=item.get("tags", ""),
                download_url=download_url,
                preview_url=preview_url,
                width=int(item.get("imageWidth", 0) or item.get("webformatWidth", 0) or 0),
                height=int(item.get("imageHeight", 0) or item.get("webformatHeight", 0) or 0),
                media_type="video" if item.get("videos") else "image",
                score=50.0,
            )
            results.append(candidate)

        return results

    def _scrape_html(self, query: str) -> List[ImageCandidate]:
        url = _SEARCH_URL.format(query=quote_plus(query))
        try:
            if not self._active():
                return []
            resp = requests.get(url, headers=_HEADERS, timeout=self.timeout)
            if resp.status_code != 200:
                return []
            html = resp.text
        except requests.RequestException:
            return []

        results = []
        seen_ids: set = set()

        for match in _RE_IMAGE_ITEM.finditer(html):
            img_url, alt = match.group(1), match.group(2)
            if not img_url:
                continue
            item_id = f"pixabay_{hash(img_url) & 0xFFFF:04x}"
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            results.append(ImageCandidate(
                id=item_id,
                source="pixabay",
                title=alt or "",
                description=alt or "",
                download_url=img_url,
                preview_url=img_url,
                media_type="image",
                score=45.0,
            ))

        return results

    def _best_video(self, videos: dict) -> str:
        for quality in ("large", "medium", "small", "tiny"):
            entry = videos.get(quality)
            if entry and isinstance(entry, dict):
                url = entry.get("url", "")
                if url:
                    return url
        return ""
