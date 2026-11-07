"""Brave Image Search API adapter.

Uses Brave's documented JSON endpoint instead of scraping search HTML. The
provider is optional and only activates when BRAVE_SEARCH_API_KEY is set.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

import requests

from .base_source import BaseSource, ImageCandidate, _accept_language


class BraveSource(BaseSource):
    name = "brave_images"
    trust_score = 0.7
    tier = 2
    max_results_per_query = 50
    timeout = 8.0
    SEARCH_URL = "https://api.search.brave.com/res/v1/images/search"

    def _key(self) -> str:
        return (os.environ.get("BRAVE_SEARCH_API_KEY")
                or os.environ.get("VIDZEO_BRAVE_SEARCH_API_KEY") or "").strip()

    def is_available(self) -> bool:
        return bool(self._key()) and super().is_available()

    def search(self, query: str, limit: int = 20,
               filters: Optional[Dict] = None) -> List[ImageCandidate]:
        if not self.is_available() or not str(query or "").strip():
            return []
        params = {
            "q": str(query).strip()[:400],
            "count": max(1, min(50, int(limit or 20))),
            "country": str((filters or {}).get("country") or "ALL"),
            "search_lang": str((filters or {}).get("search_lang") or _accept_language()[:2] or "en"),
            "safesearch": "strict",
            "spellcheck": "true",
        }
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self._key(),
            "User-Agent": "VidzeoLocal/1.0 (image search)",
        }
        try:
            response = requests.get(self.SEARCH_URL, params=params, headers=headers,
                                    timeout=self.timeout)
            if response.status_code != 200:
                self.record_failure()
                return []
            payload = response.json() or {}
            results: List[ImageCandidate] = []
            for index, row in enumerate(payload.get("results") or []):
                props = row.get("properties") or {}
                thumb = row.get("thumbnail") or {}
                image_url = str(props.get("url") or thumb.get("src") or "").strip()
                preview_url = str(thumb.get("src") or props.get("placeholder") or image_url).strip()
                page_url = str(row.get("url") or "").strip()
                if not image_url:
                    continue
                width = int(props.get("width") or thumb.get("width") or 0)
                height = int(props.get("height") or thumb.get("height") or 0)
                results.append(ImageCandidate(
                    id=f"brave_{index}_{abs(hash(image_url))}",
                    source=self.name,
                    title=str(row.get("title") or "").strip(),
                    description=str(row.get("description") or "").strip(),
                    download_url=image_url,
                    preview_url=preview_url,
                    width=width,
                    height=height,
                    metadata={
                        "page_url": page_url,
                        "source_domain": str(row.get("source") or "").strip(),
                        "confidence": row.get("confidence") or "",
                        "provider_proxy": preview_url != image_url,
                    },
                    query=str(query),
                ))
            self.record_success()
            return results
        except Exception:
            self.record_failure()
            return []

