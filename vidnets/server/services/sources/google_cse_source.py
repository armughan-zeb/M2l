"""Optional official Google Programmable Search image adapter.

The lane is activated only when both ``GOOGLE_CSE_API_KEY`` and
``GOOGLE_CSE_ID`` are configured, so it adds no latency for default jobs.
"""
from __future__ import annotations

import os
import hashlib
from typing import Dict, List, Optional

import requests

from .base_source import BaseSource, ImageCandidate


class GoogleCSESource(BaseSource):
    name = "google_cse"
    trust_score = 0.75
    tier = 2
    max_results_per_query = 10
    timeout = 5.0
    API_URL = "https://www.googleapis.com/customsearch/v1"

    def __init__(self):
        super().__init__()
        self.api_key = str(os.environ.get("GOOGLE_CSE_API_KEY") or "").strip()
        self.engine_id = str(os.environ.get("GOOGLE_CSE_ID") or "").strip()
        self.session = requests.Session()

    def is_available(self) -> bool:
        enabled = str(os.environ.get("VIDZEO_ENABLE_GOOGLE_DISCOVERY", "1") or "1").lower()
        return bool(
            enabled in {"1", "true", "yes", "on"}
            and self.api_key
            and self.engine_id
            and super().is_available()
        )

    def search(
        self,
        query: str,
        limit: int = 10,
        filters: Optional[Dict] = None,
    ) -> List[ImageCandidate]:
        if not self.is_available():
            return []
        clean = self._clean_query(query, max_words=10)
        if not clean:
            return []
        params = {
            "key": self.api_key,
            "cx": self.engine_id,
            "q": clean,
            "searchType": "image",
            "safe": "active",
            "num": min(max(1, int(limit)), 10),
            "imgSize": "large",
        }
        if (filters or {}).get("rights"):
            params["rights"] = str(filters["rights"])
        try:
            response = self.session.get(self.API_URL, params=params, timeout=self.timeout)
            response.raise_for_status()
            rows = response.json().get("items") or []
        except Exception:
            self.record_failure()
            return []
        results = []
        for index, row in enumerate(rows):
            image = row.get("image") or {}
            url = str(row.get("link") or "")
            if not url:
                continue
            results.append(ImageCandidate(
                id=f"google_cse_{index}_{hashlib.sha256(url.encode()).hexdigest()[:10]}",
                source="google_cse",
                title=str(row.get("title") or "")[:200],
                description=str(row.get("snippet") or "")[:300],
                download_url=url,
                preview_url=str(image.get("thumbnailLink") or url),
                width=int(image.get("width") or 0),
                height=int(image.get("height") or 0),
                metadata={
                    "foreign_url": str(image.get("contextLink") or ""),
                    "display_link": str(row.get("displayLink") or ""),
                },
            ))
        self.record_success()
        return results
