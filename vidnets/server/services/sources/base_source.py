"""
Base Source — Abstract base class for all media sources.

Every source (Wikimedia, Reddit, Openverse, NASA, etc.) inherits from this
and implements search(). Returns standardized ImageCandidate objects.
"""
from __future__ import annotations

import os
import re
import time
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional


def _accept_language() -> str:
    """Return the Accept-Language header for web image sources.

    Defaults to English so existing behavior is unchanged; international
    deployments set ``VIDZEO_WEB_ACCEPT_LANGUAGE`` (e.g. ``hi-IN,hi;q=0.9,en;q=0.8``)
    to bias provider result languages toward the target market.
    """
    configured = str(os.environ.get("VIDZEO_WEB_ACCEPT_LANGUAGE", "") or "").strip()
    return configured or "en-US,en;q=0.9"


@dataclass
class ImageCandidate:
    """Standardized image candidate from any source."""
    id: str
    source: str
    title: str
    description: str
    download_url: str
    preview_url: str
    width: int = 0
    height: int = 0
    media_type: str = "image"
    license: str = ""
    categories: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)
    score: float = 0.0
    phash: str = ""
    query: str = ""
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()
        if not self.id and self.download_url:
            self.id = f"{self.source}_{hashlib.sha256(self.download_url.encode()).hexdigest()[:12]}"

    @property
    def url(self) -> str:
        return self.download_url

    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'source': self.source,
            'type': self.media_type,
            'title': self.title,
            'description': self.description,
            'tags': self.categories,
            'url': self.download_url,
            'download_url': self.download_url,
            'preview_url': self.preview_url,
            'width': self.width,
            'height': self.height,
            'license': self.license,
            'score': self.score,
            'phash': self.phash,
            'query': self.query,
            'metadata': self.metadata,
        }


class BaseSource(ABC):
    """Abstract base class all media sources inherit from."""

    name: str = "base"
    trust_score: float = 0.5
    tier: int = 4
    max_results_per_query: int = 30
    timeout: float = 10.0

    def __init__(self, api_key: str = "", config: Optional[Dict] = None, **kwargs):
        self.api_key = str(api_key or "").strip()
        self.config = dict(config or {})
        self.config.update(kwargs)
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._circuit_open = False

    @abstractmethod
    def search(
        self,
        query: str,
        limit: int = 20,
        filters: Optional[Dict] = None,
    ) -> List[ImageCandidate]:
        """Search this source for images matching query.

        Args:
            query: Search query string
            limit: Maximum results to return
            filters: Optional source-specific filters

        Returns:
            List of ImageCandidate objects
        """
        pass

    def is_available(self) -> bool:
        """Health check — returns False if circuit breaker is open."""
        if self._circuit_open:
            if time.time() - self._last_failure_time > 60.0:
                self._circuit_open = False
                self._failure_count = 0
            return False
        return True

    def record_success(self):
        self._failure_count = 0
        self._circuit_open = False

    def record_failure(self):
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= 3:
            self._circuit_open = True

    def _clean_query(self, query: str, max_words: int = 6) -> str:
        """Clean and truncate query for search APIs."""
        q = re.sub(r'[^\w\s\-]', ' ', query.lower())
        q = re.sub(r'\s+', ' ', q).strip()
        words = q.split()[:max_words]
        return ' '.join(words)

    def _is_valid_image_url(self, url: str) -> bool:
        """Check if URL looks like a direct image."""
        if not url or not url.startswith(('http://', 'https://')):
            return False
        image_exts = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff')
        url_lower = url.lower().split('?')[0]
        return any(url_lower.endswith(ext) for ext in image_exts)

    def _normalize_url(self, url: str) -> str:
        """Normalize URL — fix common issues."""
        if not url:
            return ""
        url = url.strip()
        if url.startswith('//'):
            url = 'https:' + url
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        return url

    def __repr__(self):
        return f"<{self.__class__.__name__}(tier={self.tier}, trust={self.trust_score})>"
