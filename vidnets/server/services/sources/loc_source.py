"""Library of Congress image search with collection-page provenance."""
from __future__ import annotations

import os
import re
import hashlib
from typing import Dict, List, Optional

import requests

from .base_source import BaseSource, ImageCandidate


def _text(value: object, limit: int = 500) -> str:
    if isinstance(value, list):
        value = "; ".join(str(item) for item in value if item)
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


class LibraryOfCongressSource(BaseSource):
    """Search digitized LOC photos through its public JSON API."""

    name = "library_of_congress"
    trust_score = 0.98
    tier = 1
    max_results_per_query = 20
    timeout = 5.0
    API_URL = "https://www.loc.gov/photos/"

    def __init__(self):
        super().__init__()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": os.environ.get(
                "VIDZEO_LOC_USER_AGENT",
                "VidzeoLocal/1.0 (https://vidzeo.app; documentary media retrieval)",
            ),
            "Accept": "application/json",
        })

    def search(
        self,
        query: str,
        limit: int = 20,
        filters: Optional[Dict] = None,
    ) -> List[ImageCandidate]:
        clean = self._clean_query(query, max_words=8)
        if not clean:
            return []
        requested = min(max(1, int(limit)), self.max_results_per_query)
        params = {
            "q": clean,
            "fo": "json",
            "c": requested,
            "sp": 1,
        }
        try:
            response = self.session.get(
                self.API_URL,
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            rows = response.json().get("results") or []
        except Exception:
            self.record_failure()
            return []

        results: List[ImageCandidate] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            image_urls = row.get("image_url") or []
            if isinstance(image_urls, str):
                image_urls = [image_urls]
            usable_urls = [
                str(url)
                for url in image_urls
                if str(url).startswith(("http://", "https://"))
            ]
            if not usable_urls:
                continue
            # LOC normally orders its derivatives from small to large.
            download_url = usable_urls[-1]
            preview_url = usable_urls[min(1, len(usable_urls) - 1)]
            source_url = _text(row.get("url") or row.get("id"), 500)
            rights = _text(
                row.get("rights")
                or row.get("rights_advisory")
                or row.get("rights_information"),
                500,
            )
            contributors = row.get("contributor") or row.get("contributors") or []
            creator = _text(contributors, 250)
            description = _text(
                row.get("description")
                or row.get("other_title")
                or row.get("notes"),
                500,
            )
            item_id = _text(row.get("id") or source_url or download_url, 500)
            results.append(ImageCandidate(
                id=(
                    "loc_"
                    + hashlib.sha256(item_id.encode("utf-8", "replace")).hexdigest()[:16]
                ),
                source=self.name,
                title=_text(row.get("title"), 200),
                description=description,
                download_url=self._normalize_url(download_url),
                preview_url=self._normalize_url(preview_url),
                width=0,
                height=0,
                license=rights,
                categories=[
                    _text(value, 120)
                    for value in (row.get("partof") or [])[:5]
                    if _text(value, 120)
                ],
                metadata={
                    "provider": self.name,
                    "foreign_url": source_url,
                    "attribution": creator or "Library of Congress",
                    "creator": creator,
                    "license_url": source_url,
                    "date": _text(row.get("date"), 100),
                    "digitized": True,
                },
            ))
            if len(results) >= requested:
                break
        self.record_success()
        return results
