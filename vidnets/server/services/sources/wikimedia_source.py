"""Fast, provenance-rich Wikimedia Commons image search.

Discovery intentionally avoids ``extmetadata`` because Commons documents it
as an expensive property.  Rights metadata is fetched in one small batch for
only the highest-ranked candidates.
"""
from __future__ import annotations

import html
import os
import re
from typing import Dict, Iterable, List, Optional

import requests

from .base_source import BaseSource, ImageCandidate


def _plain_text(value: object, limit: int = 500) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


class WikimediaSource(BaseSource):
    name = "wikimedia"
    trust_score = 0.95
    tier = 1
    max_results_per_query = 30
    timeout = 5.0
    API_URL = "https://commons.wikimedia.org/w/api.php"

    def __init__(self):
        super().__init__()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": os.environ.get(
                "VIDZEO_WIKIMEDIA_USER_AGENT",
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
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "generator": "search",
            "gsrsearch": f"{clean} filetype:bitmap",
            "gsrnamespace": "6",
            "gsrlimit": min(max(1, int(limit)), 50),
            "prop": "imageinfo",
            # Cheap discovery pass: URL/dimensions only.
            "iiprop": "url|size|mime",
            "iiurlwidth": "1280",
            "origin": "*",
        }
        try:
            response = self.session.get(self.API_URL, params=params, timeout=self.timeout)
            response.raise_for_status()
            pages = (response.json().get("query") or {}).get("pages") or []
        except Exception:
            self.record_failure()
            return []

        results: List[ImageCandidate] = []
        for page in pages:
            info_rows = page.get("imageinfo") or []
            if not info_rows:
                continue
            info = info_rows[0]
            mime = str(info.get("mime") or "").lower()
            if mime and not mime.startswith("image/"):
                continue
            url = info.get("thumburl") or info.get("url") or ""
            if not url:
                continue
            page_id = str(page.get("pageid") or "")
            page_url = info.get("descriptionurl") or info.get("descriptionshorturl") or ""
            results.append(ImageCandidate(
                id=f"wikimedia_{page_id}",
                source=self.name,
                title=str(page.get("title") or "").removeprefix("File:")[:200],
                description="",
                download_url=self._normalize_url(url),
                preview_url=self._normalize_url(info.get("thumburl") or url),
                width=int(info.get("thumbwidth") or info.get("width") or 0),
                height=int(info.get("thumbheight") or info.get("height") or 0),
                metadata={
                    "page_id": page_id,
                    "foreign_url": page_url,
                    "provider": self.name,
                },
            ))
        self.record_success()
        return results[:limit]

    def enrich_rights(
        self,
        candidates: Iterable[ImageCandidate],
        limit: int = 6,
    ) -> List[ImageCandidate]:
        """Batch-fetch expensive rights metadata for a bounded shortlist."""
        rows = list(candidates or [])
        shortlist = [
            candidate for candidate in rows[:max(0, int(limit))]
            if str((candidate.metadata or {}).get("page_id") or "")
        ]
        if not shortlist:
            return rows
        by_page_id = {
            str((candidate.metadata or {}).get("page_id")): candidate
            for candidate in shortlist
        }
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "pageids": "|".join(by_page_id),
            "prop": "imageinfo",
            "iiprop": "extmetadata",
            "iiextmetadatafilter": (
                "Artist|Credit|LicenseShortName|LicenseUrl|"
                "ImageDescription|Categories|DateTimeOriginal|DateTime"
            ),
            "iiextmetadatalanguage": "en",
            "origin": "*",
        }
        try:
            response = self.session.get(self.API_URL, params=params, timeout=self.timeout)
            response.raise_for_status()
            pages = (response.json().get("query") or {}).get("pages") or []
        except Exception:
            return rows

        for page in pages:
            candidate = by_page_id.get(str(page.get("pageid") or ""))
            info_rows = page.get("imageinfo") or []
            if candidate is None or not info_rows:
                continue
            metadata = info_rows[0].get("extmetadata") or {}

            def value(key: str, limit: int = 500) -> str:
                return _plain_text((metadata.get(key) or {}).get("value"), limit)

            artist = value("Artist", 200)
            credit = value("Credit", 300)
            candidate.description = value("ImageDescription", 300)
            candidate.license = value("LicenseShortName", 100)
            candidate.categories = [value("Categories", 300)] if value("Categories", 300) else []
            candidate.metadata.update({
                "attribution": credit or artist,
                "artist": artist,
                "creator": artist,
                "license_url": value("LicenseUrl", 300),
                "date": value("DateTimeOriginal", 100) or value("DateTime", 100),
            })
        return rows
