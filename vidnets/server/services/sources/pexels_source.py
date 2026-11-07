"""
Pexels Source — HTML scraper fallback for when API key is missing.

Scrapes Pexels search page for image/video results.
"""
from __future__ import annotations

import re
import time
import hashlib
import json
import threading
import requests
from html.parser import HTMLParser
from typing import Dict, List, Optional
from urllib.parse import quote_plus, urljoin, urlparse

from .base_source import BaseSource, ImageCandidate, _accept_language

_SEARCH_URL = "https://www.pexels.com/search/videos/{query}/"
_IMAGE_SEARCH_URL = "https://www.pexels.com/search/{query}/"
_TIMEOUT = 7
_MAX_SCRAPER_RESULTS = 8
_SCRAPER_SEMAPHORE = threading.BoundedSemaphore(2)
_SCRAPER_RATE_LOCK = threading.Lock()
_SCRAPER_LAST_REQUEST = 0.0
_SCRAPER_MIN_INTERVAL_SECONDS = 0.25

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": _accept_language(),
}

_RE_VIDEO_ITEM = re.compile(
    r'<article[^>]*class="[^"]*photo-item[^"]*"[^>]*>.*?'
    r'href="([^"]+)"[^>]*>.*?'
    r'<img[^>]*src="([^"]+)"[^>]*>.*?'
    r'data-ld=["\']({.*?})["\']',
    re.DOTALL,
)

_PEXELS_LICENSE_URL = "https://www.pexels.com/license/"


class _SearchResultParser(HTMLParser):
    """Read public result anchors without regex-truncating nested JSON."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag.casefold() != "a":
            return
        values = dict(attrs)
        data = values.get("data-ld")
        href = values.get("href")
        if data and href:
            self.rows.append((href, data))


class PexelsSource(BaseSource):
    name = "pexels"
    trust_score = 0.7
    tier = 2
    max_results_per_query = 24

    def __init__(self, api_key: str = "", config: Optional[Dict] = None, **kwargs):
        super().__init__(api_key=api_key, config=config, **kwargs)
        self.session = requests.Session()
        try:
            self.timeout = max(1.0, min(8.0, float(self.config.get("timeout", _TIMEOUT))))
        except (TypeError, ValueError):
            self.timeout = float(_TIMEOUT)

    def search(
        self,
        query: str,
        limit: int = 20,
        filters: Optional[Dict] = None,
    ) -> List[ImageCandidate]:
        if not self.is_available():
            return []
        try:
            candidates = self._api_search(query, limit) if self.api_key else []
        except requests.RequestException:
            candidates = []
        if not candidates and not self.api_key:
            # Video fallback requests can explicitly disable the image lane;
            # the last-resort stock path uses that mode to avoid unrelated
            # stills being presented as footage.
            requested_type = str((filters or {}).get("media_type") or "both").casefold()
            media_modes = [True] if requested_type == "video" else [True, False]
            for is_video in media_modes:
                try:
                    results = self._scrape(query, is_video)
                    candidates.extend(results)
                    if len(candidates) >= limit:
                        break
                except Exception:
                    continue
        if not candidates:
            self.record_failure()
        else:
            self.record_success()
        return candidates[:limit]

    def _api_search(self, query: str, limit: int) -> List[ImageCandidate]:
        response = self.session.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": self.api_key},
            params={"query": self._clean_query(query), "per_page": max(1, min(limit, self.max_results_per_query))},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        results = []
        for item in response.json().get("photos", [])[:limit]:
            src = item.get("src") or {}
            download_url = src.get("large2x") or src.get("large") or src.get("medium") or ""
            if not download_url:
                continue
            photographer = str(item.get("photographer") or "").strip()
            foreign_url = str(item.get("url") or "").strip()
            results.append(ImageCandidate(
                id=f"pexels_{item.get('id')}", source="pexels",
                title=str(item.get("alt") or ""), description=str(item.get("alt") or ""),
                download_url=download_url, preview_url=src.get("medium") or download_url,
                width=int(item.get("width") or 0), height=int(item.get("height") or 0),
                license="Pexels License", query=query,
                metadata={"attribution": f"Photo by {photographer} on Pexels" if photographer else "Pexels", "foreign_url": foreign_url, "photographer": photographer},
            ))
        return results

    def _scrape(self, query: str, is_video: bool) -> List[ImageCandidate]:
        url_template = _SEARCH_URL if is_video else _IMAGE_SEARCH_URL
        url = url_template.format(query=quote_plus(query))
        global _SCRAPER_LAST_REQUEST
        try:
            if not _SCRAPER_SEMAPHORE.acquire(timeout=self.timeout):
                return []
            try:
                with _SCRAPER_RATE_LOCK:
                    wait_for = _SCRAPER_MIN_INTERVAL_SECONDS - (time.monotonic() - _SCRAPER_LAST_REQUEST)
                    if wait_for > 0:
                        time.sleep(wait_for)
                    _SCRAPER_LAST_REQUEST = time.monotonic()
                resp = self.session.get(
                    url,
                    headers=_HEADERS,
                    timeout=(min(2.0, self.timeout), self.timeout),
                )
            finally:
                _SCRAPER_SEMAPHORE.release()
            if resp.status_code != 200:
                return []
            html = resp.text
        except requests.RequestException:
            return []

        results = []
        seen_ids: set = set()

        parser = _SearchResultParser()
        try:
            parser.feed(html)
        except Exception:
            return []
        for href, data_json in parser.rows:
            try:
                data = json.loads(data_json)
            except (json.JSONDecodeError, ValueError):
                continue

            media_url = data.get("contentUrl") or data.get("content_url") or ""
            if not is_video:
                media_url = media_url or data.get("image") or data.get("thumbnailUrl") or ""
            title = data.get("name") or data.get("caption") or ""
            if not media_url:
                continue
            media_url = urljoin("https://www.pexels.com", str(media_url))
            parsed_media = urlparse(media_url)
            if parsed_media.scheme != "https" or not parsed_media.hostname:
                continue
            media_host = (parsed_media.hostname or "").casefold()
            if is_video:
                if media_host not in {"videos.pexels.com", "player.vimeo.com"}:
                    continue
                if not parsed_media.path.casefold().endswith(".mp4"):
                    continue
            elif not self._is_valid_image_url(media_url):
                continue
            page_url = urljoin("https://www.pexels.com", href)
            author = data.get("author") or data.get("creator") or ""
            if isinstance(author, dict):
                author = author.get("name") or author.get("@id") or ""
            author = str(author or "").strip()
            item_id = f"pexels_{hashlib.sha256(media_url.encode()).hexdigest()[:16]}"
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)

            width = data.get("width", 0) or 0
            height = data.get("height", 0) or 0

            candidate = ImageCandidate(
                id=item_id,
                source="pexels",
                title=title,
                description=title,
                download_url=media_url,
                preview_url=(data.get("thumbnailUrl") or data.get("thumbnail") or media_url),
                width=int(width),
                height=int(height),
                media_type="video" if is_video else "image",
                license="Pexels License (provider page; verify current terms)",
                score=58.0 if is_video else 55.0,
                query=query,
                metadata={
                    "source_url": page_url,
                    "page_url": page_url,
                    "license": "Pexels License (provider page; verify current terms)",
                    "license_url": _PEXELS_LICENSE_URL,
                    "rights_status": "provider_license_check_required",
                    "license_verification_required": True,
                    "attribution": f"Pexels contributor: {author}" if author else "Pexels contributor",
                    "creator": author,
                    "scraped_public_result": True,
                },
            )
            results.append(candidate)

        return results
