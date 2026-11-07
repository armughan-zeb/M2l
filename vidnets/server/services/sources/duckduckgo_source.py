"""
DuckDuckGo Images Source — VQD token + i.js API, no auth.

Free, no key, general image search.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

import requests

from .base_source import BaseSource, ImageCandidate, _accept_language


class DuckDuckGoSource(BaseSource):
    name = "duckduckgo"
    trust_score = 0.5
    tier = 4
    max_results_per_query = 20
    timeout = 8.0

    HOMEPAGE = "https://duckduckgo.com/"
    IMAGE_API = "https://duckduckgo.com/i.js"

    def __init__(self):
        super().__init__()
        self.session = self._create_session()

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
            'Accept-Language': _accept_language(),
        })
        return session

    def search(
        self,
        query: str,
        limit: int = 20,
        filters: Optional[Dict] = None,
    ) -> List[ImageCandidate]:
        clean = self._clean_query(query, max_words=6)
        if not clean:
            return []

        vqd = self._get_vqd(clean)
        if not vqd:
            return []

        candidates = []
        try:
            resp = self.session.get(
                self.IMAGE_API,
                params={
                    'l': 'us-en',
                    'o': 'json',
                    'q': clean,
                    'vqd': vqd,
                    'f': ',,,',
                    'p': '1',
                    'kp': '1',
                },
                headers={
                    'Accept': 'application/json',
                    'Referer': f"https://duckduckgo.com/?q={clean}",
                },
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            for row in data.get('results', []):
                if len(candidates) >= limit:
                    break
                candidate = self._row_to_candidate(row, clean)
                if candidate:
                    candidates.append(candidate)
        except Exception:
            pass

        return candidates

    def _get_vqd(self, query: str) -> Optional[str]:
        try:
            resp = self.session.get(
                self.HOMEPAGE,
                params={'q': query, 'kp': '1'},
                timeout=self.timeout,
            )
            match = re.search(r"vqd=['\"]?([0-9A-Za-z\-_]+)['\"]?", resp.text)
            if not match:
                match = re.search(r"vqd=([0-9A-Za-z\-_]+)&", resp.text)
            return match.group(1) if match else None
        except Exception:
            return None

    def _row_to_candidate(self, row: Dict, query: str) -> Optional[ImageCandidate]:
        image_url = self._normalize_url(row.get('image', ''))
        if not image_url:
            return None

        page_url = self._normalize_url(row.get('url', ''))
        title = row.get('title', '')
        thumbnail = self._normalize_url(row.get('thumbnail', ''))

        domain = self._extract_domain(page_url or image_url)

        # Search queries describe the lookup, not the asset.  Store the
        # query through the normal query_used provenance field upstream;
        # never manufacture a description from it.
        description = str(row.get("description") or "").strip()
        return ImageCandidate(
            id=f"ddg_{hash(image_url) % 10000000}",
            source='duckduckgo',
            title=title[:200],
            description=description[:500],
            download_url=image_url,
            preview_url=thumbnail or image_url,
            width=0,
            height=0,
            media_type='image',
            license="Unknown",
            metadata={'domain': domain, 'page_url': page_url},
        )

    def _extract_domain(self, url: str) -> str:
        try:
            from urllib.parse import urlparse
            return (urlparse(url).netloc or '').lower().replace('www.', '')
        except Exception:
            return ''


def search_ddg(query: str, limit: int = 20) -> List[Dict]:
    source = DuckDuckGoSource()
    items = source.search(query, limit=limit)
    return [item.to_dict() for item in items]


if __name__ == "__main__":
    print("Testing DuckDuckGo source...")
    source = DuckDuckGoSource()
    results = source.search("cricket stadium", limit=5)
    print(f"Found {len(results)} results:")
    for r in results:
        print(f"  - {r.title[:60]}")
        print(f"    {r.download_url[:80]}...")
