"""Free SearXNG image-search adapter.

SearXNG exposes a small JSON search endpoint on many public instances. We use
several operator-configurable instances, rotate on failure, and keep requests
slow enough to avoid turning a public instance into a scraping target.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Dict, List, Optional
from urllib.parse import urljoin

import requests

from .base_source import BaseSource, ImageCandidate, _accept_language


class SearXNGSource(BaseSource):
    name = "searxng_images"
    trust_score = 0.55
    tier = 3
    max_results_per_query = 30
    timeout = 10.0

    _default_instances = (
        "https://searx.tiekoetter.com",
    )
    _instance_cache: List[str] = []
    _instance_cache_at = 0.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = threading.Lock()
        self._next_instance = 0
        self._cooldown_until: Dict[str, float] = {}

    def _instances(self) -> List[str]:
        raw = os.environ.get("VIDZEO_SEARXNG_URLS") or os.environ.get("VIDZEO_SEARXNG_URL") or ""
        values = [x.strip().rstrip("/") for x in raw.split(",") if x.strip()]
        if values:
            return list(dict.fromkeys(values))
        now = time.monotonic()
        if now - self._instance_cache_at > 900 or not self._instance_cache:
            try:
                payload = requests.get(
                    "https://searx.space/data/instances.json",
                    headers={"Accept": "application/json", "User-Agent": "VidzeoLocal/1.0"},
                    timeout=5,
                ).json() or {}
                ranked = []
                for url, info in (payload.get("instances") or {}).items():
                    if not isinstance(info, dict) or info.get("network_type") != "normal":
                        continue
                    if (info.get("http") or {}).get("status_code") != 200:
                        continue
                    engines = info.get("engines") or {}
                    image_engines = [
                        value for key, value in engines.items()
                        if "image" in str(key).lower()
                    ]
                    if not image_engines or all(
                        float((value or {}).get("error_rate") or 0) >= 100
                        for value in image_engines if isinstance(value, dict)
                    ):
                        continue
                    timing = (info.get("timing") or {}).get("search") or {}
                    success = float(timing.get("success_percentage") or 0)
                    mean = float((timing.get("all") or {}).get("mean") or 999)
                    if success <= 0:
                        continue
                    ranked.append((success, mean, str(url).rstrip("/")))
                ranked.sort(key=lambda row: (-row[0], row[1]))
                self._instance_cache = [row[2] for row in ranked[:8]]
                self._instance_cache_at = now
            except Exception:
                pass
        return list(dict.fromkeys(self._instance_cache or self._default_instances))

    def is_available(self) -> bool:
        return bool(self._instances()) and super().is_available()

    def _ordered_instances(self) -> List[str]:
        instances = self._instances()
        with self._lock:
            start = self._next_instance % len(instances)
            self._next_instance += 1
        return instances[start:] + instances[:start]

    def search(self, query: str, limit: int = 20,
               filters: Optional[Dict] = None) -> List[ImageCandidate]:
        if not self.is_available() or not str(query or "").strip():
            return []
        params = {
            "q": str(query).strip()[:240],
            "categories": "images",
            "format": "json",
            "language": str((filters or {}).get("language") or _accept_language()[:2] or "en"),
            "safesearch": 2,
        }
        for instance in self._ordered_instances():
            if self._cooldown_until.get(instance, 0) > time.monotonic():
                continue
            try:
                response = requests.get(urljoin(instance + "/", "search"), params=params,
                                        headers={"Accept": "application/json", "User-Agent": "VidzeoLocal/1.0"},
                                        timeout=self.timeout)
                if response.status_code != 200:
                    self._cooldown_until[instance] = time.monotonic() + 60.0
                    continue
                payload = response.json() or {}
                results: List[ImageCandidate] = []
                for index, row in enumerate(payload.get("results") or []):
                    resolution = str(row.get("resolution") or "")
                    try:
                        width, height = (int(value) for value in resolution.lower().split("x", 1))
                    except (TypeError, ValueError):
                        width = height = 0
                    image_url = str(row.get("img_src") or row.get("url") or "").strip()
                    preview = str(row.get("thumbnail_src") or image_url).strip()
                    if not image_url or not image_url.startswith(("http://", "https://")):
                        continue
                    results.append(ImageCandidate(
                        id=f"searxng_{index}_{abs(hash(image_url))}", source=self.name,
                        title=str(row.get("title") or "").strip(),
                        description=str(row.get("content") or "").strip(),
                        download_url=image_url, preview_url=preview,
                        width=width, height=height,
                        metadata={"page_url": str(row.get("url") or ""),
                                  "source_domain": str(row.get("source") or ""),
                                  "instance": instance},
                        query=str(query),
                    ))
                    if len(results) >= max(1, min(30, int(limit or 20))):
                        break
                if results:
                    self.record_success()
                    return results
                self._cooldown_until[instance] = time.monotonic() + 30.0
            except Exception:
                self._cooldown_until[instance] = time.monotonic() + 60.0
        self.record_failure()
        return []
