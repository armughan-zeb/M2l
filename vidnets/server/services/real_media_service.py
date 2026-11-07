# =============================================================================
# REAL MEDIA SERVICE V2 - Complete Media Workflow System
# =============================================================================
#
# Comprehensive media search, scoring, download, and scene assembly system.
#
# Features:
# - Multi-source search (Pexels, Pixabay, Wikimedia, Internet Archive)
# - 4-dimensional scoring (Relevance, Quality, Diversity, Practicality)
# - 5-level intelligent fallback system
# - Media processing and normalization
# - Scene assembly with smart transitions
#
# =============================================================================

import os
import re
import json
import time
import hashlib
import copy
import requests
import urllib.parse
import threading
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any, Callable, Mapping
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TEMP_DIR = Path(os.environ.get('VIDZEO_TEMP_DIR', str(Path(__file__).resolve().parents[2] / 'temp')))
from services.pipeline_budget import get_pipeline_budget


SUPPORTED_STOCK_SOURCES = ('pexels', 'pixabay')


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, str(default)) or default))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)) or default)
    except Exception:
        return default


_CPU_COUNT = max(1, int(os.cpu_count() or 1))
_STOCK_SCENE_WORKERS = max(
    1,
    min(6, _env_int("VIDZEO_STOCK_SCENE_WORKERS", max(2, _CPU_COUNT // 2))),
)
_STOCK_QUERY_VARIANT_LIMIT = max(1, _env_int("VIDZEO_STOCK_QUERY_VARIANTS", 5))
_MAX_PROVIDER_CONCURRENT_REQUESTS = max(
    1,
    min(4, _env_int("VIDZEO_STOCK_PROVIDER_CONCURRENCY", 2)),
)
_MAX_STOCK_SEARCH_WORKERS = max(
    2,
    min(16, _env_int("VIDZEO_STOCK_SEARCH_WORKERS", 12)),
)
_MAX_STOCK_RESULTS_PER_SOURCE = max(3, _env_int("VIDZEO_STOCK_RESULTS_PER_SOURCE", 40))
_MAX_STOCK_GATHERED_CANDIDATES = max(4, _env_int("VIDZEO_STOCK_GATHERED_CANDIDATES", 80))
_MAX_STOCK_DOWNLOAD_ATTEMPTS_PER_SCENE = max(1, _env_int("VIDZEO_STOCK_DOWNLOAD_ATTEMPTS_PER_SCENE", 16))
_STOCK_SOURCE_SEARCH_TIMEOUT = max(1.0, _env_float("VIDZEO_STOCK_SOURCE_SEARCH_TIMEOUT", 8.0))
_STOCK_SCENE_SEARCH_ROUND_TIMEOUT = max(1.5, _env_float("VIDZEO_STOCK_SCENE_SEARCH_ROUND_TIMEOUT", 12.0))
_STOCK_BATCH_RESOLVE_TIMEOUT = max(75.0, _env_float("VIDZEO_STOCK_RESOLVE_TIMEOUT_SECONDS", 90.0))
_PROVIDER_LIMIT_ACQUIRE_TIMEOUT = max(0.5, _env_float("VIDZEO_STOCK_PROVIDER_WAIT_TIMEOUT", 20.0))
_DOWNLOAD_PROVIDER_LIMIT_ACQUIRE_TIMEOUT = max(0.5, _env_float("VIDZEO_STOCK_DOWNLOAD_PROVIDER_WAIT_TIMEOUT", 2.0))
_DOWNLOAD_PROVIDER_WAIT_LOG_INTERVAL_SECONDS = 15.0
_PROVIDER_LIMITER_LOCK = threading.Lock()
_PROVIDER_LIMITERS: Dict[str, threading.Semaphore] = {}
_DOWNLOAD_PROVIDER_LIMITER_LOCK = threading.Lock()
_DOWNLOAD_PROVIDER_LIMITERS: Dict[str, threading.Semaphore] = {}
_DEFAULT_PROVIDER_CONCURRENT_DOWNLOADS = max(
    1,
    min(4, int(get_pipeline_budget().get("per_stock_provider_limit", 4) or 4)),
)
_PROVIDER_DOWNLOAD_LIMIT_OVERRIDES = {
    "pexels": 4,
    "pixabay": 4,
}
_STOCK_METADATA_STOPWORDS = {
    "video", "videos", "footage", "clip", "clips", "stock", "free", "hd", "4k",
    "pexels", "pixabay", "photo", "photos", "image", "images", "background",
}
_GENERIC_STOCK_TERMS = {
    "abstract", "background", "motion", "cinematic", "people", "person", "man",
    "woman", "city", "street", "office", "business", "technology", "nature",
}
_STOCK_MAX_DOWNLOAD_MB = max(8, _env_int("VIDZEO_STOCK_MAX_DOWNLOAD_MB", 64))
_STOCK_DOWNLOAD_TIMEOUT_SECONDS = max(30, _env_int("VIDZEO_STOCK_DOWNLOAD_TIMEOUT_SECONDS", 120))
_STOCK_STALL_KBPS = max(0, _env_int("VIDZEO_STOCK_STALL_KBPS", 64))
_STOCK_DOWNLOAD_RETRIES = max(1, _env_int("VIDZEO_STOCK_DOWNLOAD_RETRIES", 4))
_STOCK_BATCH_TIMEOUT_SECONDS = max(40, _env_int("VIDZEO_STOCK_BATCH_TIMEOUT_SECONDS", 180))
_STOCK_MAX_BATCH_DOWNLOADS = max(0, _env_int("VIDZEO_STOCK_MAX_BATCH_DOWNLOADS", 64))
_STOCK_EARLY_DOWNLOAD = str(os.environ.get("VIDZEO_STOCK_EARLY_DOWNLOAD", "0")).strip().lower() in {
    "1", "true", "yes", "on"
}
_STOCK_NON_PHOTOGRAPHIC_TERMS = (
    "statue", "sculpture", "bust", "marble", "figurine", "figure toy",
    "toy", "doll", "mannequin", "wax figure", "monument", "ornament",
    "illustration", "drawing", "cartoon", "render", "3d", "cgi", "avatar",
    "icon", "clip art", "clipart", "sticker", "emblem", "logo",
)


def _stock_size_mb(item: Optional["MediaItem"]) -> float:
    if not item or not item.size_bytes:
        return 0.0
    return float(item.size_bytes) / (1024 * 1024)


def _stock_candidate_downloadable(item: Optional["MediaItem"]) -> bool:
    if not item:
        return False
    size_mb = _stock_size_mb(item)
    return size_mb <= 0 or size_mb <= _STOCK_MAX_DOWNLOAD_MB


def _short_err(exc: Any, max_len: int = 90) -> str:
    text = str(exc or '').strip().split('\n')[0]
    return (text[:max_len] + '...') if len(text) > max_len else text


def _get_provider_limiter(provider: str) -> threading.Semaphore:
    key = (provider or "unknown").strip().lower()
    with _PROVIDER_LIMITER_LOCK:
        limiter = _PROVIDER_LIMITERS.get(key)
        if limiter is None:
            limiter = threading.Semaphore(_MAX_PROVIDER_CONCURRENT_REQUESTS)
            _PROVIDER_LIMITERS[key] = limiter
        return limiter


def _call_with_provider_limit(provider: str, callback: Callable[[], Any]) -> Any:
    limiter = _get_provider_limiter(provider)
    # Provider contention is not a provider miss.  The old two-second wait
    # caused parallel stock rescue workers to discard healthy Pexels/Pixabay
    # lanes while another scene was still using the same provider.  Keep the
    # wait bounded, but long enough for the active request to release its slot.
    if not limiter.acquire(timeout=_PROVIDER_LIMIT_ACQUIRE_TIMEOUT):
        print(
            f"   [ERR] Provider '{provider}' limit wait timeout "
            f"({_PROVIDER_LIMIT_ACQUIRE_TIMEOUT:.0f}s) - trying the next provider"
        )
        return None
    try:
        return callback()
    finally:
        limiter.release()


def _get_download_provider_limiter(provider: str) -> threading.Semaphore:
    key = (provider or "unknown").strip().lower()
    with _DOWNLOAD_PROVIDER_LIMITER_LOCK:
        limiter = _DOWNLOAD_PROVIDER_LIMITERS.get(key)
        if limiter is None:
            override_limit = int(_PROVIDER_DOWNLOAD_LIMIT_OVERRIDES.get(key, 0) or 0)
            provider_limit = override_limit if override_limit > 0 else _DEFAULT_PROVIDER_CONCURRENT_DOWNLOADS
            limiter = threading.Semaphore(provider_limit)
            _DOWNLOAD_PROVIDER_LIMITERS[key] = limiter
        return limiter


def _acquire_download_provider_slot(provider: str, cache_name: str = "", max_wait_seconds: float = 20.0) -> Optional[threading.Semaphore]:
    limiter = _get_download_provider_limiter(provider)
    wait_label = cache_name or provider or "download"
    waited = 0.0
    while waited < max_wait_seconds:
        if limiter.acquire(timeout=_DOWNLOAD_PROVIDER_LIMIT_ACQUIRE_TIMEOUT):
            if waited > 0:
                print(
                    f"   [stock] provider slot acquired after {waited:.0f}s wait: "
                    f"{provider} {wait_label}"
                )
            return limiter
        waited += _DOWNLOAD_PROVIDER_LIMIT_ACQUIRE_TIMEOUT
        if waited % _DOWNLOAD_PROVIDER_WAIT_LOG_INTERVAL_SECONDS < _DOWNLOAD_PROVIDER_LIMIT_ACQUIRE_TIMEOUT:
            print(
                f"   [stock] waiting for {provider} download slot "
                f"({waited:.0f}s queued): {wait_label}"
            )
    print(f"   [ERR] Provider {provider} download slot wait timeout ({max_wait_seconds}s) for {wait_label}")
    return None


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class MediaConfig:
    """Configuration for media workflow"""
    # API Keys (loaded from environment or passed directly)
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    
    # Search settings
    max_results_per_source: int = 10
    search_timeout: int = 45
    min_resolution: int = 720
    preferred_aspect_ratio: float = 16/9
    
    # Download settings
    cache_dir: str = "cache/media"
    max_concurrent_downloads: int = max(
        1,
        min(4, int(os.environ.get("VIDZEO_STOCK_DOWNLOAD_WORKERS", "4") or 4)),
    )
    download_timeout: int = _STOCK_DOWNLOAD_TIMEOUT_SECONDS
    max_file_size_mb: int = _STOCK_MAX_DOWNLOAD_MB
    
    # Quality thresholds
    min_score_threshold: float = 30.0
    preferred_score_threshold: float = 35.0
    
    # Fallback settings
    max_fallback_levels: int = 5
    fallback_timeout: int = 60
    
    def __post_init__(self):
        # Load from environment if not provided
        if not self.pexels_api_key:
            self.pexels_api_key = os.environ.get('PEXELS_API_KEY', '')
        if not self.pixabay_api_key:
            self.pixabay_api_key = os.environ.get('PIXABAY_API_KEY', '')
        
        # Create cache directory
        os.makedirs(self.cache_dir, exist_ok=True)


# =============================================================================
# DATA STRUCTURES
# =============================================================================

def _provider_asset_metadata(provider: str, asset: Dict) -> Dict:
    """Carry the API's asset identity through ranking and the media ledger."""
    user = asset.get('user') or {}
    if provider == 'pexels':
        creator = (user.get('name', '') if isinstance(user, dict) else '') or asset.get('photographer', '')
        creator_url = (user.get('url', '') if isinstance(user, dict) else '') or asset.get('photographer_url', '')
        page = asset.get('url', '')
        license_name, license_url = 'Pexels License', 'https://www.pexels.com/license/'
    else:
        creator = user if isinstance(user, str) else ''
        creator_url = ''
        page = asset.get('pageURL', '')
        license_name, license_url = 'Pixabay Content License', 'https://pixabay.com/service/license-summary/'
    return {
        'provider_asset_id': str(asset.get('id') or ''),
        'source_url': page, 'page_url': page,
        'creator': creator, 'creator_url': creator_url,
        'attribution': f'{creator} / {provider.title()}' if creator else provider.title(),
        'license': license_name, 'license_url': license_url,
        'rights_status': 'provider_license_check_required',
        'license_verification_required': True,
        'retrieval_method': 'official_api',
    }


@dataclass
class MediaItem:
    """Unified media item from any source"""
    id: str
    source: str  # pexels, pixabay, wikimedia, archive
    type: str  # video, image
    title: str
    description: str
    tags: List[str]
    download_url: str
    preview_url: str
    width: int
    height: int
    duration: float = 0  # For videos
    size_bytes: int = 0
    
    # Scoring
    score: float = 0
    score_breakdown: Dict = field(default_factory=dict)
    
    # Processing
    local_path: Optional[str] = None
    is_downloaded: bool = False
    is_processed: bool = False
    
    def aspect_ratio(self) -> float:
        if self.height == 0:
            return 16/9
        return self.width / self.height
    
    def is_landscape(self) -> bool:
        return self.aspect_ratio() > 1.0


@dataclass
class SceneMedia:
    """Media selected for a specific scene"""
    scene_index: int
    scene_text: str
    scene_type: str
    duration_needed: float
    
    # Media items
    primary_media: Optional[MediaItem] = None
    backup_media: List[MediaItem] = field(default_factory=list)
    
    # Fallback info
    fallback_level: int = 0
    fallback_reason: str = ""
    
    # Transition
    transition_in: str = "cut"
    transition_out: str = "cut"
    
    def is_ready(self) -> bool:
        return self.primary_media is not None and self.primary_media.is_downloaded


# =============================================================================
# MULTI-SOURCE SEARCH ENGINE
# =============================================================================

class MultiSourceSearchEngine:
    """
    Searches multiple media sources in parallel.
    
    Sources:
    - Pexels API (modern stock video/photos)
    - Pixabay API (general stock media)
    - Wikimedia Commons (historical, public domain)
    - Internet Archive (historical footage)
    """
    
    def __init__(self, config: MediaConfig):
        self.config = config
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)
        self.session.headers.update({
            'User-Agent': 'VidzeoLocal/2.0 (Video Generator)'
        })
    
    def search_all_sources(
        self, 
        query: str,
        media_type: str = 'both',  # video, image, both
        source_priorities: Dict[str, float] = None,
        scene_context: Dict = None
    ) -> List[MediaItem]:
        """
        Search all configured sources in parallel.
        
        Args:
            query: Search query (keywords)
            media_type: 'video', 'image', or 'both'
            source_priorities: Source weights (e.g., {'pexels': 0.8, 'wikimedia': 0.5})
            scene_context: Context info (time_period, scene_type, etc.)
            
        Returns:
            List of MediaItem sorted by relevance
        """
        source_priorities = source_priorities or {
            'pexels': 1.0,
            'pixabay': 0.95,
        }
        scene_context = scene_context or {}
        
        all_results = []
        errors = []
        
        # Determine which sources to search
        sources_to_search = []
        if self.config.pexels_api_key:
            sources_to_search.append(('pexels', self._search_pexels))
        if self.config.pixabay_api_key:
            sources_to_search.append(('pixabay', self._search_pixabay))
        
        if not sources_to_search:
            # Public-page video scraping is deliberately restricted to the
            # coordinator's terminal stock rescue. Ordinary searches remain
            # API-key-only, so we do not turn this into a background scraper
            # or silently change the normal provider policy.
            if (
                bool(scene_context.get("_stock_last_resort"))
                and media_type in {"video", "both"}
                and source_priorities.get("pexels", 1.0) > 0.1
            ):
                try:
                    return self._search_pexels_scraper_videos(query, scene_context)
                except Exception as exc:
                    print(f"   [stock] keyless Pexels video rescue failed: {_short_err(exc)}")
            return []

        # Fast path: when only one provider is enabled, call it directly.
        if len(sources_to_search) == 1:
            source_name, search_fn = sources_to_search[0]
            priority = source_priorities.get(source_name, 0.5)
            if priority <= 0.1:
                return []
            try:
                results = search_fn(query, media_type, scene_context) or []
                for item in results:
                    item.score *= priority
                print(f"   [ok] {source_name}: {len(results)} results")
                return results
            except Exception as e:
                print(f"   [ERR] {source_name}: {_short_err(e)}")
                return []

        # Search in parallel, but never block shutdown on straggler providers.
        executor = ThreadPoolExecutor(max_workers=min(4, len(sources_to_search)))
        futures = {}
        try:
            for source_name, search_fn in sources_to_search:
                priority = source_priorities.get(source_name, 0.5)
                if priority > 0.1:  # Skip disabled sources
                    future = executor.submit(
                        search_fn, query, media_type, scene_context
                    )
                    futures[future] = (source_name, priority)

            try:
                for future in as_completed(
                    futures,
                    timeout=min(
                        self.config.search_timeout,
                        float(getattr(self.config, "scene_search_round_timeout", _STOCK_SCENE_SEARCH_ROUND_TIMEOUT)),
                    ),
                ):
                    source_name, priority = futures[future]
                    try:
                        results = future.result(timeout=0)
                        for item in results:
                            item.score *= priority
                        all_results.extend(results)
                        print(f"   [ok] {source_name}: {len(results)} results")
                    except Exception as e:
                        errors.append(f"{source_name}: {e}")
                        print(f"   [ERR] {source_name}: {_short_err(e)}")
            except FuturesTimeoutError:
                timed_out = []
                for future, (source_name, _priority) in futures.items():
                    if not future.done():
                        future.cancel()
                        timed_out.append(source_name)
                if timed_out:
                    print(f"   [stock] search timeout on provider(s): {', '.join(sorted(timed_out))}")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        
        # Sort by score
        all_results.sort(key=lambda x: x.score, reverse=True)
        
        return all_results

    def _search_pexels_scraper_videos(self, query: str, context: Dict) -> List[MediaItem]:
        """Fetch a small public Pexels video result set for terminal rescue."""
        from .sources.pexels_source import PexelsSource

        timeout = min(8.0, max(1.0, float(context.get("stock_search_timeout", 8.0) or 8.0)))
        source = PexelsSource(config={"timeout": timeout})
        candidates = source.search(query, limit=4, filters={"media_type": "video"})
        items = []
        for rank, candidate in enumerate(candidates):
            metadata = dict(candidate.metadata or {})
            if candidate.media_type != "video" or not str(candidate.download_url or "").lower().endswith(".mp4"):
                continue
            item = MediaItem(
                id=candidate.id,
                source="pexels",
                type="video",
                title=candidate.title or "",
                description=candidate.description or "",
                tags=list(candidate.categories or []),
                download_url=candidate.download_url,
                preview_url=candidate.preview_url or "",
                width=int(candidate.width or 0),
                height=int(candidate.height or 0),
                score=58.0,
            )
            item.score_breakdown = {
                "matched_query": query,
                "provider_rank": rank,
                "provider_relevance": max(0.0, 8.0 - rank),
                "source_url": metadata.get("source_url", ""),
                "page_url": metadata.get("page_url", ""),
                "license": metadata.get("license", candidate.license),
                "license_url": metadata.get("license_url", ""),
                "rights_status": metadata.get("rights_status", "provider_license_check_required"),
                "license_verification_required": True,
                "attribution": metadata.get("attribution", "Pexels contributor"),
                "creator": metadata.get("creator", ""),
                "retrieval_method": "bounded_public_html_last_resort",
            }
            items.append(item)
        return items
    
    def _search_pexels(
        self, 
        query: str, 
        media_type: str,
        context: Dict
    ) -> List[MediaItem]:
        """Search Pexels — API for video, HTML scraper for image fallback."""
        if _stock_remaining(self.config, 1) <= 0:
            return []
        if media_type in ('video', 'both') and self.config.pexels_api_key:
            results = self._pexels_videos(query)
            if results:
                return results
        elif media_type in ('video', 'both'):
            results = self._search_pexels_scraper_videos(query, {
                **context, 'stock_search_timeout': _stock_remaining(self.config, 8),
            })
            if results:
                return results
        if media_type in ('image', 'both'):
            if self.config.pexels_api_key:
                results = self._pexels_photos(query)
                if results:
                    return results
            if _stock_remaining(self.config, 1) <= 0:
                return []
            return self._pexels_scraper_images(query)
        return []
    
    def _pexels_scraper_images(self, query: str) -> List[MediaItem]:
        """Fallback: scrape Pexels search page for images when API key missing."""
        try:
            from .sources.pexels_source import PexelsSource
            source = PexelsSource(config={'timeout': max(1, _stock_remaining(self.config, 8))})
            results = source.search(query, limit=8)
            items = []
            for r in results:
                item = MediaItem(
                    id=r.id,
                    source='pexels',
                    type='image',
                    title=r.title or '',
                    description=r.description or '',
                    tags=[],
                    download_url=r.download_url or '',
                    preview_url=r.preview_url or '',
                    width=r.width or 0,
                    height=r.height or 0,
                    score=55,
                )
                item.score_breakdown = {
                    'matched_query': query,
                    'provider_relevance': 5.0,
                }
                items.append(item)
            if items:
                print(f"   [stock] pexels scraper: {len(items)} images for '{query[:30]}'")
            return items
        except Exception as e:
            print(f"   [ERR] Pexels scraper: {e}")
            return []

    def _pexels_videos(self, query: str) -> List[MediaItem]:
        """Search Pexels videos"""
        url = "https://api.pexels.com/videos/search"
        headers = {'Authorization': self.config.pexels_api_key}
        params = {
            'query': query,
            'per_page': self.config.max_results_per_source,
            'orientation': 'landscape',
            'size': 'large'
        }
        
        try:
            response = _call_with_provider_limit(
                'pexels',
                lambda: self.session.get(
                    url, headers=headers, params=params,
                    timeout=(max(.1, _stock_remaining(self.config, 2)), max(.1, _stock_remaining(self.config, _STOCK_SOURCE_SEARCH_TIMEOUT)))
                )
            )
            
            if response is None or response.status_code != 200:
                return []
            
            data = response.json()
            items = []
            
            for rank, video in enumerate(data.get('videos', [])):
                # Find best quality video file
                video_files = video.get('video_files', [])
                best_file = self._find_best_pexels_video(video_files)
                
                if not best_file:
                    continue
                
                item = MediaItem(
                    id=f"pexels_v_{video.get('id')}",
                    source='pexels',
                    type='video',
                    title=video.get('url', '').split('/')[-2],
                    description='',
                    tags=[],
                    download_url=best_file.get('link', ''),
                    preview_url=video.get('image', ''),
                    width=best_file.get('width', 1920),
                    height=best_file.get('height', 1080),
                    duration=video.get('duration', 0),
                    size_bytes=best_file.get('size', 0),
                    score=70  # Base score for Pexels
                )
                item.score_breakdown = {
                    **_provider_asset_metadata('pexels', video),
                    'provider_rank': rank,
                    'matched_query': query,
                    'provider_relevance': max(0.0, 12.0 - (rank * 1.25)),
                }
                items.append(item)
            
            return items
            
        except Exception as e:
            print(f"   [ERR] Pexels videos error: {_short_err(e)}")
            return []
    
    def _find_best_pexels_video(self, video_files: List[Dict]) -> Optional[Dict]:
        """Prefer usable 1080p, then HD; never trade down to SD for transfer size."""
        scored_files = []
        max_bytes = self.config.max_file_size_mb * 1024 * 1024
        
        for f in video_files:
            score = 0
            width = f.get('width', 0)
            height = f.get('height', 0)
            size_bytes = int(f.get('size') or 0)

            if height < max(720, self.config.min_resolution) or width < 1280:
                continue

            if size_bytes and size_bytes > max_bytes:
                continue
            
            # Resolution score
            if width >= 1280 and height >= 720:
                score += 10
            elif width >= 854:
                score += 5
            if width > 1920:
                score -= 4
            
            # Landscape preference
            if width > height:
                score += 5
            else:
                score -= 20  # Penalize portrait
            
            # Quality indicator
            quality = f.get('quality', '')
            if quality == 'hd':
                score += 5
            elif quality == 'sd':
                score += 2

            if size_bytes:
                size_mb = size_bytes / (1024 * 1024)
                if size_mb <= 18:
                    score += 6
                elif size_mb <= self.config.max_file_size_mb:
                    score += 3
                score -= min(8, size_mb * 0.12)
            
            scored_files.append((score, f))
        
        if scored_files:
            scored_files.sort(key=lambda x: (
                int(1920 <= x[1].get('width',0) <= 2560 and x[1].get('height',0) >= 1080),
                x[0]), reverse=True)
            return scored_files[0][1]
        
        return None
    
    def _pexels_photos(self, query: str) -> List[MediaItem]:
        """Search Pexels photos"""
        url = "https://api.pexels.com/v1/search"
        headers = {'Authorization': self.config.pexels_api_key}
        params = {
            'query': query,
            'per_page': self.config.max_results_per_source,
            'orientation': 'landscape',
            'size': 'large'
        }
        
        try:
            response = _call_with_provider_limit(
                'pexels',
                lambda: self.session.get(
                    url, headers=headers, params=params,
                    timeout=(max(.1, _stock_remaining(self.config, 2)), max(.1, _stock_remaining(self.config, _STOCK_SOURCE_SEARCH_TIMEOUT)))
                )
            )
            
            if response is None or response.status_code != 200:
                return []
            
            data = response.json()
            items = []
            
            for rank, photo in enumerate(data.get('photos', [])):
                src = photo.get('src', {})
                
                item = MediaItem(
                    id=f"pexels_p_{photo.get('id')}",
                    source='pexels',
                    type='image',
                    title=photo.get('alt', ''),
                    description=photo.get('alt', ''),
                    tags=[],
                    download_url=src.get('original', src.get('large2x', '')),
                    preview_url=src.get('medium', ''),
                    width=photo.get('width', 1920),
                    height=photo.get('height', 1080),
                    score=65  # Base score for Pexels photos
                )
                item.score_breakdown = {
                    **_provider_asset_metadata('pexels', photo),
                    'provider_rank': rank,
                    'matched_query': query,
                    'provider_relevance': max(0.0, 10.0 - (rank * 1.1)),
                }
                items.append(item)
            
            return items
            
        except Exception as e:
            print(f"   [ERR] Pexels photos error: {_short_err(e)}")
            return []
    
    def _search_pixabay(
        self, 
        query: str, 
        media_type: str,
        context: Dict
    ) -> List[MediaItem]:
        """Search Pixabay — API for video/images, HTML scraper fallback."""
        if _stock_remaining(self.config, 1) <= 0:
            return []
        if self.config.pixabay_api_key:
            if media_type in ('video', 'both'):
                results = self._pixabay_videos(query)
                if results:
                    return results
            if media_type in ('image', 'both'):
                results = self._pixabay_images(query)
                if results:
                    return results
        return self._pixabay_scraper_images(query) if _stock_remaining(self.config, 1) > 0 else []
    
    def _pixabay_scraper_images(self, query: str) -> List[MediaItem]:
        """Fallback: scrape Pixabay search page for images when API key missing."""
        try:
            from .sources.pixabay_source import PixabaySource
            source = PixabaySource(config={
                'timeout': max(1, _stock_remaining(self.config, 8)),
                'deadline_at': getattr(self.config, 'stock_deadline_at', None),
                'cancel_event': getattr(self.config, 'stock_cancel_event', None),
            })
            results = source.search(query, limit=8)
            items = []
            for r in results:
                item = MediaItem(
                    id=r.id,
                    source='pixabay',
                    type='image',
                    title=r.title or '',
                    description=r.description or '',
                    tags=[],
                    download_url=r.download_url or '',
                    preview_url=r.preview_url or '',
                    width=r.width or 0,
                    height=r.height or 0,
                    score=50,
                )
                item.score_breakdown = {
                    'matched_query': query,
                    'provider_relevance': 4.0,
                }
                items.append(item)
            if items:
                print(f"   [stock] pixabay scraper: {len(items)} images for '{query[:30]}'")
            return items
        except Exception as e:
            print(f"   [ERR] Pixabay scraper: {e}")
            return []

    def _pixabay_videos(self, query: str) -> List[MediaItem]:
        """Search Pixabay videos"""
        url = "https://pixabay.com/api/videos/"
        params = {
            'key': self.config.pixabay_api_key,
            'q': query,
            'per_page': self.config.max_results_per_source,
            'video_type': 'film',  # film, animation, all
            'min_width': 1280
        }
        
        try:
            response = _call_with_provider_limit(
                'pixabay',
                lambda: self.session.get(
                    url, params=params, timeout=(max(.1, _stock_remaining(self.config, 2)), max(.1, _stock_remaining(self.config, _STOCK_SOURCE_SEARCH_TIMEOUT)))
                )
            )
            
            if response is None or response.status_code != 200:
                return []
            
            data = response.json()
            items = []
            
            for rank, video in enumerate(data.get('hits', [])):
                videos = video.get('videos', {})
                
                # Prefer the smallest usable rendition. The old large-first logic
                # routinely selected 60-160MB files for 5-second scenes.
                max_bytes = self.config.max_file_size_mb * 1024 * 1024
                scored_variants = []
                for variant_name in ('medium', 'small', 'large', 'tiny'):
                    variant = videos.get(variant_name) or {}
                    if not variant:
                        continue
                    size_bytes = int(variant.get('size') or 0)
                    if size_bytes and size_bytes > max_bytes:
                        continue
                    width = int(variant.get('width') or 0)
                    height = int(variant.get('height') or 0)
                    if height < max(720, self.config.min_resolution) or width < 1280:
                        continue
                    score = 0
                    if width >= 1280 and height >= 720:
                        score += 12
                    elif width >= 960 and height >= 540:
                        score += 8
                    elif width >= 640 and height >= 360:
                        score += 4
                    if width > height:
                        score += 4
                    if variant_name == 'medium':
                        score += 4
                    elif variant_name == 'small':
                        score += 3
                    elif variant_name == 'large':
                        score -= 3
                    if size_bytes:
                        size_mb = size_bytes / (1024 * 1024)
                        if size_mb <= 18:
                            score += 6
                        elif size_mb <= self.config.max_file_size_mb:
                            score += 2
                        score -= min(8, size_mb * 0.10)
                    scored_variants.append((score, variant))

                scored_variants.sort(key=lambda row: (
                    int(1920 <= row[1].get('width',0) <= 2560 and row[1].get('height',0) >= 1080),
                    row[0]), reverse=True)
                best = scored_variants[0][1] if scored_variants else {}
                
                if not best:
                    continue
                
                item = MediaItem(
                    id=f"pixabay_v_{video.get('id')}",
                    source='pixabay',
                    type='video',
                    title=video.get('tags', ''),
                    description=video.get('tags', ''),
                    tags=video.get('tags', '').split(', '),
                    download_url=best.get('url', ''),
                    preview_url=video.get('picture_id', ''),
                    width=best.get('width', 1920),
                    height=best.get('height', 1080),
                    duration=video.get('duration', 0),
                    size_bytes=best.get('size', 0),
                    score=60  # Base score for Pixabay
                )
                item.score_breakdown = {
                    **_provider_asset_metadata('pixabay', video),
                    'provider_rank': rank,
                    'matched_query': query,
                    'provider_relevance': max(0.0, 10.0 - (rank * 1.1)),
                }
                items.append(item)
            
            return items
            
        except Exception as e:
            print(f"   [ERR] Pixabay videos error: {_short_err(e)}")
            return []
    
    def _pixabay_images(self, query: str) -> List[MediaItem]:
        """Search Pixabay images"""
        url = "https://pixabay.com/api/"
        params = {
            'key': self.config.pixabay_api_key,
            'q': query,
            'per_page': self.config.max_results_per_source,
            'image_type': 'photo',
            'orientation': 'horizontal',
            'min_width': 1280
        }
        
        try:
            response = _call_with_provider_limit(
                'pixabay',
                lambda: self.session.get(
                    url, params=params, timeout=(max(.1, _stock_remaining(self.config, 2)), max(.1, _stock_remaining(self.config, _STOCK_SOURCE_SEARCH_TIMEOUT)))
                )
            )
            
            if response is None or response.status_code != 200:
                return []
            
            data = response.json()
            items = []
            
            for rank, image in enumerate(data.get('hits', [])):
                item = MediaItem(
                    id=f"pixabay_i_{image.get('id')}",
                    source='pixabay',
                    type='image',
                    title=image.get('tags', ''),
                    description=image.get('tags', ''),
                    tags=image.get('tags', '').split(', '),
                    download_url=image.get('largeImageURL', ''),
                    preview_url=image.get('webformatURL', ''),
                    width=image.get('imageWidth', 1920),
                    height=image.get('imageHeight', 1080),
                    size_bytes=image.get('imageSize', 0),
                    score=55  # Base score for Pixabay images
                )
                item.score_breakdown = {
                    **_provider_asset_metadata('pixabay', image),
                    'provider_rank': rank,
                    'matched_query': query,
                    'provider_relevance': max(0.0, 8.0 - (rank * 0.9)),
                }
                items.append(item)
            
            return items
            
        except Exception as e:
            print(f"   [ERR] Pixabay images error: {_short_err(e)}")
            return []
    
    def _search_wikimedia(
        self, 
        query: str, 
        media_type: str,
        context: Dict
    ) -> List[MediaItem]:
        """Search Wikimedia Commons"""
        try:
            from .sources.wikimedia_source import WikimediaSource
            
            source = WikimediaSource()
            
            # Wikimedia is primarily for images
            search_type = 'image' if media_type in ('image', 'both') else 'video'
            
            results = source.search(query, search_type, self.config.max_results_per_source)
            
            items = []
            for result in results:
                item_dict = result.to_dict()
                items.append(MediaItem(
                    id=item_dict['id'],
                    source='wikimedia',
                    type=item_dict['type'],
                    title=item_dict['title'],
                    description=item_dict['description'],
                    tags=item_dict['tags'],
                    download_url=item_dict['download_url'],
                    preview_url=item_dict['preview_url'],
                    width=item_dict['width'],
                    height=item_dict['height'],
                    score=50  # Base score for Wikimedia
                ))
            
            return items
            
        except Exception as e:
            print(f"   [ERR] Wikimedia search error: {_short_err(e)}")
            return []
    
    def _search_archive(
        self, 
        query: str, 
        media_type: str,
        context: Dict
    ) -> List[MediaItem]:
        """Search Internet Archive"""
        try:
            from .sources.archive_source import InternetArchiveSource
            
            # Archive is primarily for videos
            if media_type == 'image':
                return []
            
            source = InternetArchiveSource()
            results = source.search(query, 'video', self.config.max_results_per_source)
            
            items = []
            for result in results:
                item_dict = result.to_dict()
                items.append(MediaItem(
                    id=item_dict['id'],
                    source='internet_archive',
                    type=item_dict['type'],
                    title=item_dict['title'],
                    description=item_dict['description'],
                    tags=item_dict['tags'],
                    download_url=item_dict['download_url'],
                    preview_url=item_dict['preview_url'],
                    width=item_dict['width'],
                    height=item_dict['height'],
                    duration=item_dict['duration'],
                    score=45  # Base score for Archive
                ))
            
            return items
            
        except Exception as e:
            print(f"   [ERR] Archive search error: {_short_err(e)}")
            return []


# =============================================================================
# MULTI-DIMENSIONAL SCORER
# =============================================================================

class MediaScorer:
    """
    Scores media items using 4-dimensional scoring system:
    - Relevance (40 points max)
    - Quality (30 points max)  
    - Diversity (20 points max)
    - Practicality (10 points max)
    - Penalties (negative)
    """
    
    def __init__(self, config: MediaConfig):
        self.config = config
    
    def score_item(
        self, 
        item: MediaItem, 
        keywords: List[str],
        scene_context: Dict,
        existing_items: List[MediaItem] = None
    ) -> MediaItem:
        """
        Score a media item comprehensively.
        
        Args:
            item: Media item to score
            keywords: Target keywords
            scene_context: Scene metadata
            existing_items: Already selected items (for diversity)
            
        Returns:
            MediaItem with updated score
        """
        existing_items = existing_items or []
        
        provider_meta = dict(item.score_breakdown or {})

        # Calculate component scores
        relevance = self._score_relevance(item, keywords, scene_context)
        quality = self._score_quality(item, scene_context)
        diversity = self._score_diversity(item, existing_items)
        practicality = self._score_practicality(item)
        penalties = self._calculate_penalties(item)
        
        # Combine scores
        total = relevance + quality + diversity + practicality - penalties
        total = max(0, min(100, total))  # Clamp to 0-100
        
        # Store breakdown
        item.score = total
        item.score_breakdown = {
            **provider_meta,
            'relevance': relevance,
            'quality': quality,
            'diversity': diversity,
            'practicality': practicality,
            'penalties': penalties,
            'total': total
        }
        
        return item
    
    def _score_relevance(
        self, 
        item: MediaItem, 
        keywords: List[str],
        context: Dict
    ) -> float:
        """Score relevance (max 40 points)"""
        score = 0
        
        # Combine searchable text
        searchable = f"{item.title} {item.description} {' '.join(item.tags)}".lower()
        query_terms = [keyword.lower() for keyword in keywords[:8] if keyword and len(keyword) > 2]
        provider_meta = item.score_breakdown or {}
        matched_query = str(provider_meta.get('matched_query') or '').lower()
        provider_relevance = float(provider_meta.get('provider_relevance') or 0.0)
        provider_query_terms = [
            term for term in re.findall(r"[a-z0-9]+", matched_query)
            if len(term) > 2 and term not in _STOCK_METADATA_STOPWORDS
        ]
        has_metadata_match = bool(query_terms and any(term in searchable for term in query_terms))
        has_query_term_match = bool(provider_query_terms and set(query_terms) & set(provider_query_terms))
        if query_terms and not has_metadata_match and not has_query_term_match:
            # Provider search order is useful but weaker than real candidate metadata.
            score += min(8.0, provider_relevance * 0.55)
        
        # Keyword matches
        matched = 0
        for keyword in keywords[:10]:  # Limit to top 10 keywords
            if keyword.lower() in searchable:
                matched += 1
        
        # Score based on match ratio
        if keywords:
            match_ratio = matched / min(len(keywords), 10)
            score += match_ratio * 20  # Up to 20 points

        if provider_query_terms:
            query_overlap = len(set(query_terms) & set(provider_query_terms)) if query_terms else 0
            score += min(8.0, query_overlap * 2.0 + min(3.0, provider_relevance * 0.25))
        
        # Exact phrase bonus
        query = ' '.join(keywords[:5]).lower()
        if query in searchable:
            score += 8  # Bonus for exact phrase
        
        # Context alignment
        time_period = context.get('time_period', '')
        scene_type = context.get('scene_type', '')
        
        # Source-context alignment
        if scene_type == 'historical' and item.source in ('wikimedia', 'archive'):
            score += 5
        elif scene_type in ('news', 'interview') and item.source in ('pexels', 'pixabay'):
            score += 5
        
        # Time period alignment (for historical)
        if time_period in ['ancient', 'medieval', '19th_century']:
            if item.source in ('wikimedia', 'archive'):
                score += 4

        query_profile = context.get('query_profile', {}) or {}
        preferred_query = query_profile.get('primary_query') or ''
        alt_query = query_profile.get('alt_query') or ''
        fallback_query = query_profile.get('fallback_query') or ''
        preferred_source = query_profile.get('preferred_source') or context.get('preferred_source', '')
        if preferred_query and preferred_query.lower() in searchable:
            score += 8
        if alt_query and alt_query.lower() in searchable:
            score += 4
        if fallback_query and fallback_query.lower() in searchable:
            score += 2
        for token in [
            query_profile.get('subject', ''),
            query_profile.get('category', ''),
            query_profile.get('setting', ''),
        ]:
            if token and token.lower() in searchable:
                score += 2
        if preferred_source and item.source == preferred_source:
            score += 5

        # Entity match bonus (people, places)
        entities = context.get('entities', [])
        for entity in entities[:3]:
            if entity.lower() in searchable:
                score += 3
        
        return min(40, score)
    
    def _score_quality(self, item: MediaItem, context: Dict) -> float:
        """Score quality (max 30 points)"""
        score = 0
        
        # Resolution score (up to 10 points)
        width = item.width
        if width >= 3840:  # 4K
            score += 10
        elif width >= 1920:  # Full HD
            score += 8
        elif width >= 1280:  # HD
            score += 6
        elif width >= 854:  # SD
            score += 4
        else:
            score += 2
        
        # Aspect ratio (up to 5 points)
        target_ratio = 16/9
        actual_ratio = item.aspect_ratio()
        ratio_diff = abs(actual_ratio - target_ratio) / target_ratio
        
        if ratio_diff < 0.05:  # Within 5%
            score += 5
        elif ratio_diff < 0.15:  # Within 15%
            score += 3
        elif ratio_diff < 0.25:
            score += 1
        # No points if too far off
        
        # Duration appropriateness (for videos, up to 10 points)
        if item.type == 'video':
            needed_duration = context.get('duration_needed', 10)
            actual_duration = item.duration
            
            if actual_duration > 0:
                if actual_duration >= needed_duration * 0.8 and actual_duration <= needed_duration * 3:
                    # Perfect or reasonable range
                    score += 10
                elif actual_duration >= needed_duration * 0.5:
                    score += 7
                elif actual_duration >= needed_duration * 0.3:
                    score += 4
                else:
                    score += 2  # Too short but usable
        else:
            # Images get base points for duration consideration
            score += 8
        
        # Source quality bonus
        if item.source == 'pexels':
            score += 5  # Known high quality
        elif item.source == 'pixabay':
            score += 3
        elif item.source == 'wikimedia':
            score += 2
        
        return min(30, score)
    
    def _score_diversity(
        self, 
        item: MediaItem, 
        existing_items: List[MediaItem]
    ) -> float:
        """Score diversity (max 20 points)"""
        if not existing_items:
            return 15  # First item gets base diversity score
        
        score = 20  # Start with max and subtract
        
        # Penalize same source consecutively
        source_counts = {}
        recent_items = existing_items[-4:]
        for existing in recent_items:
            source_counts[existing.source] = source_counts.get(existing.source, 0) + 1
        
        if item.source in source_counts:
            score -= source_counts[item.source] * 5  # Stronger penalty per same source
        if recent_items and recent_items[-1].source == item.source:
            score -= 4  # Avoid same-source back-to-back when alternatives exist
        
        # Penalize same media type consecutively
        recent_types = [e.type for e in existing_items[-2:]]
        if recent_types and all(t == item.type for t in recent_types):
            score -= 3  # Encourage media type variety

        recent_signatures = {_media_signature(e) for e in recent_items}
        candidate_signature = _media_signature(item)
        if candidate_signature and candidate_signature in recent_signatures:
            score -= 12

        # Penalize near-duplicate semantic content in the recent window
        highest_similarity = 0.0
        for existing in recent_items:
            highest_similarity = max(highest_similarity, _media_similarity(item, existing))
        if highest_similarity >= 0.92:
            score -= 12
        elif highest_similarity >= 0.82:
            score -= 8
        elif highest_similarity >= 0.72:
            score -= 5
        
        # Bonus for different source variety
        unique_sources = set(e.source for e in existing_items)
        if item.source not in unique_sources:
            score += 3  # Bonus for new source
        
        return max(0, min(20, score))
    
    def _score_practicality(self, item: MediaItem) -> float:
        """Score practicality (max 10 points)"""
        score = 0
        
        # File size (prefer smaller files for faster processing)
        size_mb = item.size_bytes / (1024 * 1024) if item.size_bytes else 0
        
        if size_mb == 0:  # Unknown size
            score += 3
        elif size_mb < 20:
            score += 5
        elif size_mb < 50:
            score += 4
        elif size_mb < 100:
            score += 2
        elif size_mb < self.config.max_file_size_mb:
            score += 1
        # Files over max size get 0
        
        # Download URL reliability
        url = item.download_url.lower()
        if 'pexels.com' in url or 'pixabay.com' in url:
            score += 3  # Reliable CDNs
        elif 'archive.org' in url or 'wikimedia.org' in url:
            score += 2  # Public archives
        else:
            score += 1  # Unknown sources
        
        # Has preview (helps with verification)
        if item.preview_url:
            score += 2
        
        return min(10, score)
    
    def _calculate_penalties(self, item: MediaItem) -> float:
        """Calculate penalty points"""
        penalties = 0
        
        # Watermark detection (heuristic - check tags/description)
        watermark_indicators = ['watermark', 'watermarked', 'getty', 'shutterstock', 'istock']
        searchable = f"{item.title} {item.description} {' '.join(item.tags)}".lower()
        
        for indicator in watermark_indicators:
            if indicator in searchable:
                penalties += 30
                break
        
        # Portrait orientation penalty
        if item.height > item.width:
            penalties += 25
        
        # Very long video penalty (hard to segment)
        if item.type == 'video' and item.duration > 300:  # > 5 minutes
            penalties += 15
        
        # Low resolution penalty
        if item.width < 720 or item.height < 480:
            penalties += 10
        
        # No metadata penalty
        if not item.title and not item.description:
            penalties += 5
        
        return penalties
    
    def score_all(
        self, 
        items: List[MediaItem], 
        keywords: List[str],
        scene_context: Dict
    ) -> List[MediaItem]:
        """Score all items and return sorted by score"""
        scored_items = []
        
        for i, item in enumerate(items):
            # Pass already scored items for diversity calculation
            scored = self.score_item(item, keywords, scene_context, scored_items)
            scored_items.append(scored)
        
        # Re-sort by final score
        scored_items.sort(key=lambda x: x.score, reverse=True)
        
        return scored_items


# =============================================================================
# INTELLIGENT FALLBACK SYSTEM
# =============================================================================

class FallbackManager:
    """
    5-level fallback system for when optimal media isn't found.
    
    Levels:
    0 - OPTIMAL: High-quality exact matches
    1 - RELAXED: Broader keywords, lower quality acceptable
    2 - STOCK: Generic B-roll and related concepts
    3 - HYBRID: Mix of real + AI-generated
    4 - TERMINAL: No result (the caller must retry or report the scene)
    """
    
    FALLBACK_LEVELS = {
        0: "OPTIMAL",
        1: "RELAXED", 
        2: "STOCK",
        3: "HYBRID",
        4: "TERMINAL"
    }
    
    def __init__(self, config: MediaConfig, search_engine: MultiSourceSearchEngine):
        self.config = config
        self.search_engine = search_engine
    
    def find_media(
        self, 
        keywords: List[str],
        scene_context: Dict,
        scorer: MediaScorer,
        max_level: int = 4
    ) -> Tuple[Optional[MediaItem], int, str]:
        """
        Find best media with automatic fallback.
        
        Args:
            keywords: Primary search keywords
            scene_context: Scene metadata
            scorer: MediaScorer instance
            max_level: Maximum fallback level to try
            
        Returns:
            (best_item, fallback_level, fallback_reason)
        """
        for level in range(min(max_level + 1, 5)):
            result, reason = self._try_level(level, keywords, scene_context, scorer)
            
            if result:
                print(f"   [ok] Found media at level {level} ({self.FALLBACK_LEVELS[level]})")
                return result, level, reason if level > 0 else ""
            
            print(f"   ⬇️ Level {level} failed: {reason}")
        
        # Complete failure - return None
        return None, 4, "All fallback levels exhausted"
    
    def _try_level(
        self, 
        level: int, 
        keywords: List[str],
        scene_context: Dict,
        scorer: MediaScorer
    ) -> Tuple[Optional[MediaItem], str]:
        """Try to find media at a specific fallback level"""
        
        if level == 0:
            return self._try_optimal(keywords, scene_context, scorer)
        elif level == 1:
            return self._try_relaxed(keywords, scene_context, scorer)
        elif level == 2:
            return self._try_stock(keywords, scene_context, scorer)
        elif level == 3:
            return self._try_hybrid(keywords, scene_context, scorer)
        elif level == 4:
            # A synthetic/text-card placeholder is not media.  Return a
            # terminal miss so the caller can retry another provider rather
            # than emitting an empty scene slot.
            return None, "No real media candidate remained"
        
        return None, "Invalid level"
    
    def _try_optimal(
        self, 
        keywords: List[str],
        scene_context: Dict,
        scorer: MediaScorer
    ) -> Tuple[Optional[MediaItem], str]:
        """Level 0: Try to find optimal, high-quality match"""
        query = ' '.join(keywords[:5])  # Use top keywords
        
        results = self.search_engine.search_all_sources(
            query, 'both', scene_context.get('source_priorities'), scene_context
        )
        
        if not results:
            return None, "No results found"
        
        # Score and filter
        scored = scorer.score_all(results, keywords, scene_context)
        
        # Need high score for optimal
        for item in scored:
            if item.score >= self.config.preferred_score_threshold:
                return item, ""
        
        return None, f"No items met threshold ({self.config.preferred_score_threshold})"
    
    def _try_relaxed(
        self, 
        keywords: List[str],
        scene_context: Dict,
        scorer: MediaScorer
    ) -> Tuple[Optional[MediaItem], str]:
        """Level 1: Relax keywords and lower quality threshold"""
        # Expand keywords with synonyms
        expanded = self._expand_keywords(keywords)
        
        for query_set in expanded:
            query = ' '.join(query_set)
            
            results = self.search_engine.search_all_sources(
                query, 'both', None, scene_context
            )
            
            if results:
                scored = scorer.score_all(results, query_set, scene_context)
                
                for item in scored:
                    if item.score >= self.config.min_score_threshold:
                        return item, f"Relaxed keywords: {query}"
        
        return None, "Relaxed search failed"
    
    def _expand_keywords(self, keywords: List[str]) -> List[List[str]]:
        """Generate expanded keyword variations"""
        variations = []
        
        # Original keywords
        variations.append(keywords[:5])
        
        # Broader keywords (first 3 only)
        if len(keywords) >= 3:
            variations.append(keywords[:3])
        
        # Even broader (first 2)
        if len(keywords) >= 2:
            variations.append(keywords[:2])
        
        # Single keyword fallback
        if keywords:
            variations.append([keywords[0]])
        
        # Add common synonyms if available
        synonym_map = {
            'person': ['people', 'human', 'individual'],
            'business': ['corporate', 'office', 'professional'],
            'technology': ['tech', 'digital', 'computer'],
            'nature': ['outdoor', 'landscape', 'environment'],
            'city': ['urban', 'metropolitan', 'downtown'],
        }
        
        for keyword in keywords[:3]:
            kw_lower = keyword.lower()
            if kw_lower in synonym_map:
                for synonym in synonym_map[kw_lower]:
                    new_keywords = [synonym] + [k for k in keywords[1:3] if k.lower() != kw_lower]
                    variations.append(new_keywords)
        
        return variations
    
    def _try_stock(
        self, 
        keywords: List[str],
        scene_context: Dict,
        scorer: MediaScorer
    ) -> Tuple[Optional[MediaItem], str]:
        """Level 2: Try generic stock footage"""
        scene_type = scene_context.get('scene_type', 'generic')
        
        # Generic B-roll queries based on scene type
        broll_queries = {
            'interview': ['business meeting', 'conversation', 'office discussion'],
            'news': ['news studio', 'city street', 'crowd'],
            'historical': ['old photograph', 'vintage', 'archive'],
            'nature': ['landscape', 'wildlife', 'scenery'],
            'technology': ['computer', 'technology', 'digital'],
            'generic': ['abstract', 'motion background', 'cinematic'],
        }
        
        queries = broll_queries.get(scene_type, broll_queries['generic'])
        
        for query in queries:
            results = self.search_engine.search_all_sources(
                query, 'video', None, scene_context
            )
            
            if results:
                scored = scorer.score_all(results, query.split(), scene_context)
                
                if scored and scored[0].score >= 20:  # Low threshold for B-roll
                    return scored[0], f"Stock B-roll: {query}"
        
        return None, "No suitable B-roll found"
    
    def _try_hybrid(
        self, 
        keywords: List[str],
        scene_context: Dict,
        scorer: MediaScorer
    ) -> Tuple[Optional[MediaItem], str]:
        """Level 3: Use AI-generated media if real media unavailable"""
        # This would integrate with AI image generation
        # For now, try to find any usable media
        
        generic_queries = ['abstract background', 'motion graphics', 'cinematic']
        
        for query in generic_queries:
            results = self.search_engine.search_all_sources(
                query, 'both', None, scene_context
            )
            
            if results:
                # Accept any result at this level
                return results[0], f"Hybrid fallback: {query}"
        
        # If AI generator is available, use it
        return None, "Hybrid approach failed"


# =============================================================================
# MEDIA DOWNLOADER
# =============================================================================

class StockMediaPool:
    """Job-owned provider results, download coordination and diverse allocation.

    Only raw provider metadata/bytes are shared. Scene scoring, contracts and
    visual decisions stay private to each caller.
    """
    def __init__(self, namespace=""):
        self.scope = hashlib.sha256(str(namespace or f'{os.getpid()}:{time.time_ns()}').encode()).hexdigest()[:20]
        self.lock = threading.Lock()
        self.download_lock = threading.Lock()
        self.downloads = {}
        self.cached = {}
        self.pending = {}
        self.uses = {}
        self.stats = {'provider_searches': 0, 'search_cache_hits': 0, 'shared_search_waits': 0,
                      'distinct_selections': 0, 'repeat_selections': 0}

    def search(self, key, loader, *, timeout=8.0, cancel_event=None):
        with self.lock:
            if key in self.cached:
                self.stats['search_cache_hits'] += 1
                return copy.deepcopy(self.cached[key])
            event = self.pending.get(key)
            owner = event is None
            if owner:
                event = self.pending[key] = threading.Event()
                self.stats['provider_searches'] += 1
            else:
                self.stats['shared_search_waits'] += 1
        if not owner:
            until = time.monotonic() + max(0.0, float(timeout))
            while time.monotonic() < until:
                if cancel_event is not None and cancel_event.is_set():
                    return []
                if event.wait(min(.05, max(0.0, until - time.monotonic()))):
                    with self.lock:
                        return copy.deepcopy(self.cached.get(key, []))
            return []
        try:
            result = loader() or []
            if result:
                with self.lock:
                    # Bound a long job's raw metadata cache; failures are not cached.
                    if len(self.cached) >= 128:
                        self.cached.pop(next(iter(self.cached)))
                    self.cached[key] = copy.deepcopy(result)
            return result
        finally:
            with self.lock:
                self.pending.pop(key, None)
                event.set()

    def choose(self, candidates, *, duration_needed=0.0):
        candidates = [item for item in candidates if item is not None]
        if not candidates:
            return None
        # Diversity must not promote a weak match over the scene's good
        # finalists. Keep a narrow score band around the preferred candidate.
        quality_floor = float(candidates[0].score or 0) - 8.0
        candidates = [item for item in candidates if float(item.score or 0) >= quality_floor]
        covering = [item for item in candidates if item.type != 'video' or not item.duration
                    or float(item.duration) >= float(duration_needed or 0)]
        if covering:
            candidates = covering
        preferred_type = candidates[0].type
        def transfer_rank(item):
            # Rank comparable real assets by rendition quality, then transfer
            # cost. Unknown-size long masters should not beat a short HD clip
            # just because a provider returned them first.
            resolution = 0 if item.width >= 1920 and item.height >= 1080 else 1
            estimated_mb = (_stock_size_mb(item) or
                            max(1.0,float(item.duration or 30)) * max(.25,item.width*item.height/(1920*1080)) * .4)
            return (item.type != preferred_type, resolution, estimated_mb)
        with self.lock:
            # Candidates have already passed the scene's relevance and quality
            # gates. Preserve their rank among equally unused provider assets.
            chosen = min(enumerate(candidates), key=lambda pair:
                         (self.uses.get(_stock_candidate_asset_key(pair[1]), 0),
                          transfer_rank(pair[1]), pair[0]))[1]
            key = _stock_candidate_asset_key(chosen)
            prior = self.uses.get(key, 0)
            self.uses[key] = prior + 1
            self.stats['repeat_selections' if prior else 'distinct_selections'] += 1
            return chosen

    def release(self, item):
        if item is not None:
            with self.lock:
                key = _stock_candidate_asset_key(item)
                self.uses[key] = max(0, self.uses.get(key, 0) - 1)

    def report(self):
        with self.lock:
            return dict(self.stats)


def _stock_cache_filename(item: MediaItem, extension: str) -> str:
    """Cache provider bytes per job; scene review receipts remain independent."""
    breakdown = item.score_breakdown if isinstance(item.score_breakdown, dict) else {}
    scope = str(breakdown.get("shared_download_scope") or breakdown.get("cache_scope") or "legacy")
    identity = "|".join([
        str(item.source or ""),
        str(item.id or ""),
        str(item.download_url or item.preview_url or ""),
        scope,
    ])
    digest = hashlib.sha256(identity.encode("utf-8", "replace")).hexdigest()[:16]
    source = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(item.source or "stock"))[:20]
    item_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(item.id or "asset"))[:40]
    return f"{source}_{item_id}_{digest}{extension}"

class MediaDownloader:
    """Downloads and caches media files"""
    
    def __init__(self, config: MediaConfig):
        self.config = config
        self.session = requests.Session()
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            status=2,
            backoff_factor=0.75,
            status_forcelist=(408, 429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=max(20, self.config.max_concurrent_downloads),
            pool_maxsize=max(20, self.config.max_concurrent_downloads),
        )
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)
        # CRITICAL: Wikimedia blocks requests without proper User-Agent
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'image/*, video/*, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://commons.wikimedia.org/'
        })
        self._inflight_lock = threading.Lock()
        self._inflight_downloads: Dict[str, threading.Event] = {}

    def _mark_downloaded(self, item: MediaItem, local_path: str) -> bool:
        item.local_path = local_path
        item.is_downloaded = True
        return True

    def _download_item_owned(self, item: MediaItem, cache_name: str, local_path: str) -> bool:
        if _stock_remaining(self.config, 1) <= 0:
            return False
        connect_timeout = 8
        read_timeout = max(8, int(self.config.download_timeout or _STOCK_DOWNLOAD_TIMEOUT_SECONDS))
        item_hard_timeout = max(18, int(self.config.download_timeout or _STOCK_DOWNLOAD_TIMEOUT_SECONDS) + 8)
        chunk_size = 65536
        max_attempts = _STOCK_DOWNLOAD_RETRIES
        max_bytes = self.config.max_file_size_mb * 1024 * 1024

        if item.size_bytes and item.size_bytes > max_bytes:
            print(
                f"   [stock] skip oversized metadata "
                f"({item.size_bytes / 1024 / 1024:.1f}MB): {cache_name}"
            )
            return False

        provider_limiter = _acquire_download_provider_slot(item.source, cache_name, _stock_remaining(self.config, 20))
        if not provider_limiter:
            print(f"   [ERR] Skipping download for {item.id} due to provider slot timeout.")
            return False

        try:
            print(f"   [stock] downloading {item.source} {item.type}: {cache_name}")

            for attempt in range(1, max_attempts + 1):
                if _stock_remaining(self.config, 1) <= 0:
                    return False
                item_start = time.monotonic()
                response = None
                tmp_path = f"{local_path}.{os.getpid()}.{threading.get_ident()}.{attempt}.tmp"
                bytes_written = 0

                try:
                    response = self.session.get(
                        item.download_url,
                        timeout=(max(.1, _stock_remaining(self.config, connect_timeout)), max(.1, _stock_remaining(self.config, read_timeout))),
                        stream=True,
                    )

                    status_code = getattr(response, "status_code", None)
                    if response is None or status_code != 200:
                        status_label = status_code if status_code is not None else "no-response"
                        print(f"   [stock] download failed HTTP {status_label}: {cache_name}")
                        if response is not None:
                            response.close()
                        if attempt < max_attempts and status_code in {None, 408, 429, 500, 502, 503, 504}:
                            time.sleep(min(2.0 * attempt, 4.0))
                            continue
                        return False

                    content_length = int(response.headers.get('content-length', 0))
                    if content_length > max_bytes:
                        print(f"   [stock] file too large ({content_length / 1024 / 1024:.1f}MB), skipping")
                        response.close()
                        return False

                    size_str = f" ({content_length / 1024 / 1024:.1f}MB)" if content_length else ""
                    print(f"   [stock] streaming{size_str}: {cache_name}")

                    with open(tmp_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=chunk_size):
                            if _stock_remaining(self.config, 1) <= 0:
                                raise TimeoutError('Stock recovery was cancelled or its deadline expired')
                            if not chunk:
                                continue
                            elapsed = time.monotonic() - item_start
                            if _STOCK_STALL_KBPS > 0 and elapsed > 15:
                                throughput_kbps = (bytes_written / 1024.0) / elapsed
                                if throughput_kbps < _STOCK_STALL_KBPS:
                                    print(
                                        f"   [stock] throughput stall "
                                        f"({throughput_kbps:.0f}KB/s < {_STOCK_STALL_KBPS}KB/s) "
                                        f"after {elapsed:.0f}s - aborting {cache_name}"
                                    )
                                    response.close()
                                    try:
                                        os.remove(tmp_path)
                                    except OSError:
                                        pass
                                    return False
                            if elapsed > item_hard_timeout:
                                print(
                                    f"   [stock] hard timeout ({item_hard_timeout}s) "
                                    f"reached at {bytes_written / 1024:.0f}KB - aborting {cache_name}"
                                )
                                response.close()
                                try:
                                    os.remove(tmp_path)
                                except OSError:
                                    pass
                                return False
                            if bytes_written + len(chunk) > max_bytes:
                                print(
                                    f"   [stock] size cap ({self.config.max_file_size_mb}MB) "
                                    f"reached - aborting {cache_name}"
                                )
                                response.close()
                                try:
                                    os.remove(tmp_path)
                                except OSError:
                                    pass
                                return False
                            f.write(chunk)
                            bytes_written += len(chunk)
                    response.close()
                    if _stock_remaining(self.config, 1) <= 0:
                        raise TimeoutError('Stock recovery deadline expired before save')
                    os.replace(tmp_path, local_path)

                    elapsed = time.monotonic() - item_start
                    print(f"   [stock] done: {cache_name} ({bytes_written / 1024:.0f}KB in {elapsed:.1f}s)")
                    return self._mark_downloaded(item, local_path)
                except Exception as exc:
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                    if response is not None:
                        response.close()
                    if attempt < max_attempts and _stock_remaining(self.config, 1) > 0:
                        print(f"   [stock] retrying {cache_name} after error: {exc}")
                        time.sleep(min(2.0 * attempt, 4.0))
                        continue
                    raise
        except Exception as e:
            print(f"   [stock] download error ({item.source}): {e}")
            return False
        finally:
            provider_limiter.release()
    
    def download_item(self, item: MediaItem) -> bool:
        """Download a single media item with stall-detection and progress logging."""
        if _stock_remaining(self.config, 1) <= 0:
            return False
        if item.is_downloaded and item.local_path:
            return True

        if not item.download_url:
            return False

        ext = self._get_extension(item.download_url, item.type)
        cache_name = _stock_cache_filename(item, ext)
        local_path = os.path.join(self.config.cache_dir, cache_name)
        inflight_event: Optional[threading.Event] = None
        owns_download = False

        if os.path.exists(local_path):
            return self._mark_downloaded(item, local_path)

        with self._inflight_lock:
            inflight_event = self._inflight_downloads.get(local_path)
            if inflight_event is None:
                inflight_event = threading.Event()
                self._inflight_downloads[local_path] = inflight_event
                owns_download = True

        try:
            if not owns_download:
                if inflight_event and inflight_event.wait(timeout=_stock_remaining(self.config, max(60, int(self.config.download_timeout or _STOCK_DOWNLOAD_TIMEOUT_SECONDS) + 20))):
                    if os.path.exists(local_path):
                        return self._mark_downloaded(item, local_path)
                if os.path.exists(local_path):
                    return self._mark_downloaded(item, local_path)
                return False

            return self._download_item_owned(item, cache_name, local_path)
        finally:
            if owns_download and inflight_event is not None:
                inflight_event.set()
                with self._inflight_lock:
                    if self._inflight_downloads.get(local_path) is inflight_event:
                        self._inflight_downloads.pop(local_path, None)

        # Per-chunk read timeout (seconds); also used as the connect timeout.
        _CONNECT_TIMEOUT = 10
        _READ_TIMEOUT    = 30   # each read() call must return within 30s
        # Hard cap on the whole download (seconds).
        _ITEM_HARD_TIMEOUT = 300
        _CHUNK_SIZE = 65536   # 64 KB

        try:
            ext        = self._get_extension(item.download_url, item.type)
            cache_name = _stock_cache_filename(item, ext)
            local_path = os.path.join(self.config.cache_dir, cache_name)

            # Return cached file immediately.
            if os.path.exists(local_path):
                item.local_path    = local_path
                item.is_downloaded = True
                return True

            print(f"   [stock] downloading {item.source} {item.type}: {cache_name}")
            item_start = time.monotonic()

            response = _call_with_provider_limit(
                item.source,
                lambda: self.session.get(
                    item.download_url,
                    timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                    stream=True,
                )
            )

            if response is None or response.status_code != 200:
                print(f"   [stock] download failed HTTP {response.status_code}: {cache_name}")
                return False

            content_length = int(response.headers.get('content-length', 0))
            if content_length > self.config.max_file_size_mb * 1024 * 1024:
                print(f"   [stock] file too large ({content_length / 1024 / 1024:.1f}MB), skipping")
                return False

            size_str = f" ({content_length / 1024 / 1024:.1f}MB)" if content_length else ""
            print(f"   [stock] streaming{size_str}: {cache_name}")

            bytes_written = 0
            tmp_path = local_path + ".tmp"
            try:
                with open(tmp_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                        if not chunk:
                            continue
                        elapsed = time.monotonic() - item_start
                        if elapsed > _ITEM_HARD_TIMEOUT:
                            print(
                                f"   [stock] hard timeout ({_ITEM_HARD_TIMEOUT}s) "
                                f"reached at {bytes_written / 1024:.0f}KB — aborting {cache_name}"
                            )
                            response.close()
                            return False
                        f.write(chunk)
                        bytes_written += len(chunk)
                response.close()
                os.replace(tmp_path, local_path)
            except Exception:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                if 'response' in locals():
                    response.close()
                raise

            elapsed = time.monotonic() - item_start
            print(f"   [stock] done: {cache_name} ({bytes_written / 1024:.0f}KB in {elapsed:.1f}s)")
            item.local_path    = local_path
            item.is_downloaded = True
            return True

        except Exception as e:
            print(f"   [stock] download error ({item.source}): {e}")
            return False

    def download_batch(self, items: List[MediaItem]) -> List[MediaItem]:
        """Download multiple items in parallel with per-item and batch timeouts."""
        if not items:
            return []

        oversized = [item for item in items if not _stock_candidate_downloadable(item)]
        if oversized:
            print(
                f"   [stock] skipped {len(oversized)} oversized download request(s) "
                f"over {_STOCK_MAX_DOWNLOAD_MB}MB before queueing"
            )
        items = [item for item in items if _stock_candidate_downloadable(item)]
        if not items:
            return []

        per_item_timeout = max(20, int(self.config.download_timeout or _STOCK_DOWNLOAD_TIMEOUT_SECONDS) + 12)
        batch_timeout = max(
            40.0,
            float(getattr(self.config, "batch_download_timeout", _STOCK_BATCH_TIMEOUT_SECONDS) or _STOCK_BATCH_TIMEOUT_SECONDS),
        )
        batch_timeout = _stock_remaining(self.config, batch_timeout)
        if batch_timeout <= 0:
            return []
        successful: List[MediaItem] = []
        batch_start = time.monotonic()
        grouped_items: Dict[str, List[MediaItem]] = {}

        for item in items:
            cache_key = os.path.join(
                self.config.cache_dir,
                _stock_cache_filename(item, self._get_extension(item.download_url, item.type)),
            )
            grouped_items.setdefault(cache_key, []).append(item)

        def _download_sort_key(item: MediaItem) -> Tuple[int, float, float]:
            cache_path = os.path.join(
                self.config.cache_dir,
                _stock_cache_filename(item, self._get_extension(item.download_url, item.type)),
            )
            size_mb = _stock_size_mb(item)
            unknown_size = 1 if size_mb <= 0 else 0
            return (0 if os.path.exists(cache_path) else 1, unknown_size, size_mb or _STOCK_MAX_DOWNLOAD_MB)

        unique_items = sorted((group[0] for group in grouped_items.values()), key=_download_sort_key)
        if _STOCK_MAX_BATCH_DOWNLOADS and len(unique_items) > _STOCK_MAX_BATCH_DOWNLOADS:
            skipped_count = len(unique_items) - _STOCK_MAX_BATCH_DOWNLOADS
            unique_items = unique_items[:_STOCK_MAX_BATCH_DOWNLOADS]
            print(
                f"   [stock] capped batch downloads at {_STOCK_MAX_BATCH_DOWNLOADS}; "
                f"deferred {skipped_count} lower-priority item(s)"
            )
        print(f"   [stock] batch: {len(unique_items)} unique item(s) downloading in parallel...")
        if len(unique_items) != len(items):
            print(f"   [stock] deduped {len(items) - len(unique_items)} duplicate download request(s)")

        executor = ThreadPoolExecutor(max_workers=self.config.max_concurrent_downloads)
        try:
            futures = {
                executor.submit(self.download_item, item): (
                    item,
                    os.path.join(
                        self.config.cache_dir,
                        _stock_cache_filename(item, self._get_extension(item.download_url, item.type)),
                    ),
                )
                for item in unique_items
            }

            try:
                download_done = 0
                last_download_progress = time.monotonic()
                for future in as_completed(futures, timeout=batch_timeout):
                    item, cache_key = futures[future]
                    remaining = max(1.0, batch_timeout - (time.monotonic() - batch_start))
                    try:
                        ok = future.result(timeout=min(per_item_timeout, remaining))
                        if ok:
                            linked_items = grouped_items.get(cache_key, [item])
                            for linked_item in linked_items:
                                if item.local_path:
                                    linked_item.local_path = item.local_path
                                    linked_item.is_downloaded = True
                                successful.append(linked_item)
                    except TimeoutError:
                        print(f"   [stock] item timed out: {item.source} {item.id}")
                    except Exception as e:
                        print(f"   [stock] batch download error: {e}")
                    download_done += 1
                    now = time.monotonic()
                    if (now - last_download_progress) >= 4.0 or download_done == len(unique_items):
                        print(
                            f"   [stock] phase 2 download: {download_done}/{len(unique_items)} files checked, "
                            f"{len(successful)}/{len(items)} scene assets ready, "
                            f"{now - batch_start:.1f}s elapsed"
                        )
                        last_download_progress = now
            except FuturesTimeoutError:
                print(f"   [stock] batch overall timeout ({batch_timeout}s) - partial results")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        print(f"   [stock] batch complete: {len(successful)}/{len(items)} downloaded")
        return successful

    def _get_extension(self, url: str, media_type: str) -> str:
        """Get file extension from URL"""
        # Try to extract from URL
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.lower()
        
        for ext in ['.mp4', '.webm', '.mov', '.avi', '.jpg', '.jpeg', '.png', '.gif', '.webp']:
            if path.endswith(ext):
                return ext
        
        # Default based on type
        return '.mp4' if media_type == 'video' else '.jpg'


# =============================================================================
# MAIN WORKFLOW MANAGER
# =============================================================================

class RealMediaManager:
    """
    Main orchestrator for the complete media workflow.
    
    Coordinates:
    - Script analysis
    - Multi-source search
    - Scoring and ranking
    - Fallback handling
    - Download and caching
    """
    
    def __init__(
        self, 
        pexels_key: str = "", 
        pixabay_key: str = "",
        cache_dir: str = "cache/media"
    ):
        self.config = MediaConfig(
            pexels_api_key=pexels_key,
            pixabay_api_key=pixabay_key,
            cache_dir=cache_dir
        )
        
        self.search_engine = MultiSourceSearchEngine(self.config)
        self.scorer = MediaScorer(self.config)
        self.fallback_manager = FallbackManager(self.config, self.search_engine)
        self.downloader = MediaDownloader(self.config)
    
    def find_media_for_scenes(
        self,
        scenes: List[Dict],
        title: str = "",
    ) -> List[SceneMedia]:
        """
        Find and download media for all scenes.
        
        Args:
            scenes: List of scene dictionaries with text, keywords, duration
            title: Video title for context
            
        Returns:
            List of SceneMedia with downloaded media
        """
        print(f"\n[vid] REAL MEDIA WORKFLOW")
        print(f"   Title: {title}")
        print(f"   Scenes: {len(scenes)}")
        print("=" * 50)
        
        scene_media_list = []
        
        for i, scene in enumerate(scenes):
            print(f"\n📍 Scene {i + 1}/{len(scenes)}")
            
            scene_media = self._process_scene(i, scene, scene_media_list)
            scene_media_list.append(scene_media)
        
        # Download all primary media in batch
        print(f"\n📥 Downloading media...")
        primary_items = [sm.primary_media for sm in scene_media_list if sm.primary_media]
        self.downloader.download_batch(primary_items)
        
        # Summary
        ready_count = sum(1 for sm in scene_media_list if sm.is_ready())
        fallback_count = sum(1 for sm in scene_media_list if sm.fallback_level > 0)
        
        print(f"\n✅ WORKFLOW COMPLETE")
        print(f"   Ready: {ready_count}/{len(scenes)}")
        print(f"   Used fallbacks: {fallback_count}")
        
        return scene_media_list
    
    def _process_scene(
        self, 
        index: int, 
        scene: Dict,
        existing: List[SceneMedia]
    ) -> SceneMedia:
        """Process a single scene"""
        text = scene.get('text', '')
        keywords = scene.get('keywords', [])
        duration = scene.get('duration', 10)
        scene_type = scene.get('scene_type', 'generic')
        
        # Build context
        context = {
            'text': text,
            'scene_type': scene_type,
            'duration_needed': duration,
            'entities': scene.get('entities', []),
            'time_period': scene.get('time_period', 'contemporary'),
            'source_priorities': scene.get('source_priorities', {}),
        }
        
        print(f"   Keywords: {', '.join(keywords[:5])}")
        print(f"   Type: {scene_type}, Duration: {duration}s")
        
        # Find media with fallback
        item, level, reason = self.fallback_manager.find_media(
            keywords, context, self.scorer
        )
        
        # Determine transition based on context
        transition_in = self._determine_transition(index, existing, scene_type)
        
        return SceneMedia(
            scene_index=index,
            scene_text=text,
            scene_type=scene_type,
            duration_needed=duration,
            primary_media=item,
            fallback_level=level,
            fallback_reason=reason,
            transition_in=transition_in
        )
    
    def _determine_transition(
        self, 
        index: int, 
        existing: List[SceneMedia],
        scene_type: str
    ) -> str:
        """Determine appropriate transition"""
        if index == 0:
            return 'fade_in'
        
        prev = existing[-1] if existing else None
        
        if prev and prev.scene_type != scene_type:
            return 'crossfade'
        elif scene_type == 'historical':
            return 'dissolve'
        else:
            return 'cut'
    
    def get_local_paths(self, scene_media_list: List[SceneMedia]) -> List[str]:
        """Get list of local file paths for downloaded media"""
        paths = []
        
        for sm in scene_media_list:
            if sm.primary_media and sm.primary_media.local_path:
                paths.append(sm.primary_media.local_path)
            else:
                raise ValueError(
                    f"Scene {sm.scene_index + 1} has no downloaded media; "
                    "blank/text-card fallback is disabled"
                )
        
        return paths


# =============================================================================
# SEMANTIC QUERY GENERATION V5 - Perfect Script-to-Visual Matching
# =============================================================================

def generate_semantic_media_queries_v5(
    script: str,
    title: str,
    num_scenes: int,
    api_key: str = "",
    total_duration: float = 60.0
) -> Tuple[List[str], List[Dict]]:
    """
    Generate intelligent search queries using Semantic Script Analysis.
    
    V5 SEMANTIC APPROACH:
    - Deep script analysis with LLM
    - Entity extraction (people, places, events, objects)
    - Temporal and contextual understanding
    - Precision query generation with exclusion filters
    - Multi-layer matching preparation
    
    Args:
        script: The full script text
        title: Video title
        num_scenes: Number of scenes/images needed
        api_key: Optional query-provider key (unused by the curated fallback)
        total_duration: Total video duration in seconds
        
    Returns:
        Tuple of (list of queries, list of beat requirements for matching)
    """
    print(f"\n{'='*60}")
    print(f"🧠 SEMANTIC MEDIA QUERIES V5")
    print(f"{'='*60}")
    print(f"   Title: {title}")
    print(f"   Scenes: {num_scenes}")
    print(f"   Duration: {total_duration:.1f}s")

    print(f"   📋 Generating media search queries...")
    simple_queries = generate_media_search_queries(script, title, num_scenes, api_key)
    simple_requirements = [{"primary_query": q} for q in simple_queries]
    return simple_queries, simple_requirements


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_media_manager(pexels_key: str = "", pixabay_key: str = "") -> RealMediaManager:
    """Create a configured RealMediaManager"""
    return RealMediaManager(pexels_key, pixabay_key)


def generate_media_search_queries(
    script: str,
    title: str,
    num_scenes: int,
    api_key: str = ""
) -> List[str]:
    """
    Generate intelligent search queries using LLM.
    
    LLM APPROACH (v4):
    - LLM reads the script and title
    - Generates simple, searchable queries for Wikimedia
    - Matches queries to script content contextually
    - Returns queries that WILL find relevant images
    
    Args:
        script: The full script text
        title: Video title
        num_scenes: Number of scenes/images needed
        api_key: Optional query-provider key (unused by the curated fallback)
        
    Returns:
        List of simple search queries, one per scene
    """
    print(f"\n🔍 GENERATING MEDIA SEARCH QUERIES v4 (LLM)")
    print(f"   Title: {title}")
    print(f"   Scenes: {num_scenes}")
    
    # Fallback to curated approach
    print("   📋 Using curated fallback...")
    return _generate_curated_queries_fallback(script, title, num_scenes)


def _generate_curated_queries_fallback(script: str, title: str, num_scenes: int) -> List[str]:
    """
    Fallback: Generate queries using curated topic lists.
    """
    # Fix common misspellings
    main_topic = _extract_and_fix_topic(title)
    
    # Get curated queries for this topic
    query_pool = _get_curated_queries(main_topic, script)
    
    # Distribute across scenes
    queries = []
    for i in range(num_scenes):
        query_idx = i % len(query_pool)
        queries.append(query_pool[query_idx])
    
    # Log distribution
    unique_queries = list(set(queries))
    print(f"   Curated Distribution ({len(unique_queries)} unique):")
    for q in unique_queries[:5]:
        count = queries.count(q)
        print(f"     '{q}': {count} scenes")
    
    return queries


def _extract_and_fix_topic(title: str) -> str:
    """
    Extract main topic from title and FIX common misspellings.
    """
    # Common misspellings/variations → correct spellings
    spelling_fixes = {
        # Historical figures
        'gangez khan': 'Genghis Khan',
        'gengis khan': 'Genghis Khan',
        'ghengis khan': 'Genghis Khan',
        'chinggis khan': 'Genghis Khan',
        'gangez': 'Genghis',
        'gengis': 'Genghis',
        'ceasar': 'Caesar',
        'ceaser': 'Caesar',
        'napolean': 'Napoleon',
        'shakespear': 'Shakespeare',
        'einstien': 'Einstein',
        'ghandi': 'Gandhi',
        'mandella': 'Mandela',
        'rossevelt': 'Roosevelt',
        'churchil': 'Churchill',
        'hittler': 'Hitler',
        'mussolini': 'Mussolini',
        'cleoptra': 'Cleopatra',
        'alexander the grate': 'Alexander the Great',
        # Countries/Places
        'afganistan': 'Afghanistan',
        'ukrain': 'Ukraine',
        'mongolia': 'Mongolia',
        # Modern figures
        'elon musk': 'Elon Musk',
        'donald trump': 'Donald Trump',
        'joe biden': 'Joe Biden',
    }
    
    title_lower = title.lower()
    
    # Apply spelling fixes
    for wrong, correct in spelling_fixes.items():
        if wrong in title_lower:
            title_lower = title_lower.replace(wrong, correct.lower())
            print(f"   [Spelling Fix] '{wrong}' → '{correct}'")
    
    # Remove filler words
    stop_words = {'facts', 'about', 'the', 'a', 'an', 'story', 'of', 'how', 'why',
                  'what', 'when', 'where', 'history', 'untold', 'real', 'true',
                  'amazing', 'incredible', 'shocking', 'secret', 'hidden', 'unknown'}
    
    words = title_lower.split()
    important_words = [w.capitalize() for w in words if w not in stop_words and len(w) > 2]
    
    if important_words:
        return ' '.join(important_words[:3])
    return title[:30]


def _get_curated_queries(main_topic: str, script: str) -> List[str]:
    """
    Get a curated list of queries that WILL return results.
    
    Instead of extracting random words like 'Think' or 'Listen',
    we use topic-specific curated lists.
    """
    queries = []
    topic_lower = main_topic.lower()
    
    # Add main topic variations
    queries.append(main_topic)
    queries.append(f"{main_topic} portrait")
    
    # Topic-specific curated queries (these WILL return Wiki results)
    if any(word in topic_lower for word in ['genghis', 'khan', 'mongol']):
        queries.extend([
            'Genghis Khan',
            'Mongol Empire',
            'Mongol warrior',
            'Mongolian steppe',
            'Mongol cavalry',
            'Mongolian yurt',
            'Silk Road',
            'Karakorum',
            'Asian horse',
            'Medieval battle'
        ])
    elif any(word in topic_lower for word in ['rome', 'roman', 'caesar', 'julius']):
        queries.extend([
            'Julius Caesar',
            'Roman Empire',
            'Roman soldiers',
            'Colosseum Rome',
            'Roman legionary',
            'Ancient Rome',
            'Roman Senate',
            'Roman architecture'
        ])
    elif any(word in topic_lower for word in ['egypt', 'pharaoh', 'pyramid', 'cleopatra']):
        queries.extend([
            'Ancient Egypt',
            'Egyptian pyramids',
            'Pharaoh',
            'Nile River',
            'Sphinx Egypt',
            'Egyptian hieroglyphics',
            'Egyptian temple'
        ])
    elif any(word in topic_lower for word in ['trump', 'donald']):
        queries.extend([
            'Donald Trump',
            'White House',
            'US Capitol',
            'American flag',
            'Washington DC',
            'US President'
        ])
    elif any(word in topic_lower for word in ['musk', 'elon', 'tesla', 'spacex']):
        queries.extend([
            'Elon Musk',
            'Tesla car',
            'SpaceX rocket',
            'Space launch',
            'Electric vehicle',
            'Starship rocket'
        ])
    elif any(word in topic_lower for word in ['napoleon', 'france', 'french']):
        queries.extend([
            'Napoleon Bonaparte',
            'French Empire',
            'Battle of Waterloo',
            'Paris France',
            'French soldiers',
            'Napoleonic war'
        ])
    elif any(word in topic_lower for word in ['world war', 'ww1', 'ww2', 'hitler']):
        queries.extend([
            'World War II',
            'Military soldiers',
            'War battlefield',
            'Tank military',
            'Military aircraft',
            'War memorial'
        ])
    else:
        # Generic fallbacks that always work
        queries.extend([
            'Historical portrait',
            'Historical painting',
            'Ancient civilization',
            'World history',
            'Documentary image',
            'Historical photo'
        ])
    
    # Extract proper nouns from script (but filter aggressively)
    proper_nouns = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b', script)
    
    # Words that are NEVER useful as queries
    useless_words = {
        'The', 'This', 'That', 'These', 'Those', 'There', 'Here',
        'What', 'When', 'Where', 'Why', 'How', 'Which',
        'But', 'And', 'Or', 'Yet', 'For', 'From', 'With',
        'His', 'Her', 'Their', 'Its', 'Our', 'Your',
        'Some', 'Many', 'Most', 'All', 'Any', 'Each', 'Every',
        'First', 'Second', 'Third', 'Last', 'Next',
        'Before', 'After', 'Now', 'Then', 'Today', 'Yesterday',
        'Think', 'Listen', 'Look', 'See', 'Watch', 'Hear',
        'Check', 'Find', 'Know', 'Believe', 'Consider',
        'However', 'Although', 'Despite', 'Because', 'Therefore',
        'Meanwhile', 'Indeed', 'Perhaps', 'Maybe', 'Surely',
        'Sure', 'Just', 'Only', 'Even', 'Still', 'Already'
    }
    
    for noun in proper_nouns[:15]:
        if noun not in useless_words and len(noun) > 3:
            if noun not in queries:
                queries.append(noun)
    
    # Remove duplicates
    seen = set()
    unique = []
    for q in queries:
        q_lower = q.lower()
        if q_lower not in seen:
            seen.add(q_lower)
            unique.append(q)
    
    return unique[:20]  # Max 20 queries


def _extract_main_topic(title: str) -> str:
    """Extract the main searchable topic from title."""
    # Remove common filler words
    stop_words = {'facts', 'about', 'the', 'a', 'an', 'story', 'of', 'how', 'why', 
                  'what', 'when', 'where', 'history', 'untold', 'real', 'true',
                  'amazing', 'incredible', 'shocking', 'secret', 'hidden'}
    
    words = title.lower().split()
    important_words = [w for w in words if w not in stop_words and len(w) > 2]
    
    # Capitalize each word for proper noun treatment
    if important_words:
        return ' '.join(w.capitalize() for w in important_words[:3])
    return title[:30]


def _extract_script_entities(script: str, main_topic: str) -> List[str]:
    """
    Extract searchable entities from script.
    Focus on: People, Places, Events, Objects - things that have images.
    """
    entities = set()
    
    # Add main topic variations
    topic_parts = main_topic.split()
    for part in topic_parts:
        if len(part) > 2:
            entities.add(part)
    entities.add(main_topic)
    
    # Extract capitalized phrases (proper nouns)
    proper_nouns = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b', script)
    
    # Filter out common sentence starters and useless words
    skip_words = {
        'The', 'This', 'That', 'These', 'Those', 'There', 'Here', 'What', 'When',
        'Where', 'Why', 'How', 'But', 'And', 'Or', 'Yet', 'For', 'From', 'With',
        'His', 'Her', 'Their', 'Its', 'Our', 'Your', 'Some', 'Many', 'Most',
        'First', 'Second', 'Third', 'Last', 'Next', 'Before', 'After', 'Now'
    }
    
    for noun in proper_nouns:
        if noun not in skip_words and len(noun) > 2:
            entities.add(noun)
    
    # Extract common historical/documentary keywords
    keyword_patterns = [
        r'\b(empire|kingdom|dynasty|civilization|war|battle|conquest)\b',
        r'\b(emperor|king|queen|ruler|leader|warrior|army|soldier)\b',
        r'\b(palace|castle|temple|monument|city|capital)\b',
        r'\b(horse|sword|armor|weapon|shield)\b',
    ]
    
    for pattern in keyword_patterns:
        matches = re.findall(pattern, script.lower())
        entities.update(m.capitalize() for m in matches)
    
    return list(entities)[:20]  # Limit to 20 entities


def _create_query_pool(main_topic: str, entities: List[str]) -> List[str]:
    """
    Create a pool of SIMPLE, GENERAL search queries.
    
    These should be queries that WILL return results on Wikimedia/stock sites.
    """
    queries = []
    
    # Main topic variations (most important)
    queries.append(main_topic)
    queries.append(f"{main_topic} portrait")
    queries.append(f"{main_topic} historical")
    
    # Entity-based queries
    for entity in entities[:10]:
        if entity.lower() != main_topic.lower():
            queries.append(entity)
            # Add context for better search
            if len(entity.split()) == 1:
                queries.append(f"{entity} historical")
    
    # Generic fallback queries based on topic type
    topic_lower = main_topic.lower()
    
    if any(word in topic_lower for word in ['khan', 'mongol', 'genghis']):
        queries.extend(['Mongol Empire', 'Mongolian steppe', 'Mongol warriors', 
                       'ancient Mongolia', 'horse warriors', 'Asian steppe'])
    elif any(word in topic_lower for word in ['rome', 'roman', 'caesar']):
        queries.extend(['Roman Empire', 'Roman soldiers', 'Colosseum', 
                       'ancient Rome', 'Roman architecture'])
    elif any(word in topic_lower for word in ['egypt', 'pharaoh', 'pyramid']):
        queries.extend(['Ancient Egypt', 'Egyptian pyramids', 'Pharaoh', 
                       'Nile River', 'Egyptian hieroglyphics'])
    elif any(word in topic_lower for word in ['trump', 'biden', 'president']):
        queries.extend(['White House', 'US Capitol', 'American flag', 
                       'Washington DC', 'presidential portrait'])
    elif any(word in topic_lower for word in ['musk', 'tesla', 'spacex']):
        queries.extend(['Tesla car', 'SpaceX rocket', 'electric vehicle', 
                       'space launch', 'technology innovation'])
    else:
        # Generic historical/documentary queries
        queries.extend(['historical painting', 'ancient civilization', 
                       'world history', 'historical portrait', 'documentary'])
    
    # Remove duplicates while preserving order
    seen = set()
    unique = []
    for q in queries:
        q_lower = q.lower()
        if q_lower not in seen:
            seen.add(q_lower)
            unique.append(q)
    
    return unique


def get_real_media_for_video(
    scene_prompts: List[str],
    scene_durations: List[float],
    title: str = "",
    pexels_key: str = "",
    pixabay_key: str = "",
    beat_requirements: List[Dict] = None,
    media_sources: List[str] = None,
    scene_queries: List[Dict] = None,
    runtime_options: Optional[Dict[str, Any]] = None,
) -> List[Optional[str]]:
    """
    Main entry point for app.py integration.
    
    Searches for and downloads real media for each scene.
    Returns list of local file paths (None for failed scenes).
    
    V5 SEMANTIC MATCHING:
    - If beat_requirements provided, uses 4-layer semantic scoring
    - Scores: Entity (30%), Action (25%), Context (20%), Temporal (15%), Quality (10%)
    - Rejects media with score < 60
    
    Args:
        scene_prompts: List of scene descriptions/prompts
        scene_durations: Duration needed for each scene
        title: Video title for context
        pexels_key: Pexels API key
        pixabay_key: Pixabay API key
        beat_requirements: Semantic requirements for each scene (from V5 analyzer)
        media_sources: Optional list of selected media sources (e.g. ['pexels', 'pixabay'])
        
    Returns:
        List of local file paths (or None for failed scenes)
    """
    use_semantic = beat_requirements is not None and len(beat_requirements) > 0
    # Environment keys are a legitimate configuration source (the manager
    # falls back to them too); resolve them up front so status lines and the
    # scraper-fallback gate below reflect reality instead of "MISSING".
    pexels_key = str(pexels_key or '').strip() or os.environ.get('PEXELS_API_KEY', '')
    pixabay_key = str(pixabay_key or '').strip() or os.environ.get('PIXABAY_API_KEY', '')
    
    print(f"\n[vid] REAL MEDIA WORKFLOW {'V5 SEMANTIC' if use_semantic else 'V2'}")
    print(f"   Title: {title}")
    print(f"   Scenes: {len(scene_prompts)}")
    if use_semantic:
        print(f"   Mode: Semantic Matching (4-layer scoring)")
    
    # Process media sources logic — default to both providers when not configured
    media_sources = [source for source in (media_sources or ['pexels', 'pixabay']) if source in SUPPORTED_STOCK_SOURCES]
    if not media_sources:
        media_sources = ['pexels', 'pixabay']
    print(f"   Sources: {', '.join(media_sources)}")
    print(f"   Pexels API key: {'SET' if pexels_key else 'MISSING (using scraper fallback for images)'}")
    print(f"   Pixabay API key: {'SET' if pixabay_key else 'MISSING (using scraper fallback for images)'}")
    if not pexels_key and not pixabay_key:
        print("   NOTE: API stock video search needs configured keys; bounded public stock searches remain available.")
        print("         Free API keys are available at:")
        print("         https://www.pexels.com/api/  and  https://pixabay.com/api/docs/")
        print("         Public source availability is checked at runtime; video then image rescue remains bounded.")
    source_priorities = {
        'pexels': 1.0 if 'pexels' in media_sources else 0.0,
        'pixabay': 1.0 if 'pixabay' in media_sources else 0.0,
    }
        
    print("=" * 50)
    
    # Create manager
    manager = create_media_manager(pexels_key, pixabay_key)
    # Render-repair stock is a bounded rescue after the primary web/YouTube
    # lanes already ran. Keep its provider search parallel and preserve real
    # media quality gates, but avoid repeating five long queries and 120s
    # per-item retries for every unresolved scene. These values are runtime
    # fields on this manager, so concurrent jobs do not mutate module globals.
    runtime_options = runtime_options or {}
    media_pool = runtime_options.get('_stock_media_pool')
    manager.config.media_pool = media_pool if isinstance(media_pool, StockMediaPool) else StockMediaPool()
    manager.downloader._inflight_lock = manager.config.media_pool.download_lock
    manager.downloader._inflight_downloads = manager.config.media_pool.downloads
    manager.config.stock_deadline_at = runtime_options.get('_stock_deadline_at')
    manager.config.stock_cancel_event = runtime_options.get('_stock_cancel_event')
    if runtime_options.get('_stock_last_resort'):
        # There is one parent recovery deadline. HTTP adapter retries must not
        # multiply it underneath the coordinator's bounded stock window.
        adapter = HTTPAdapter(max_retries=0, pool_connections=20, pool_maxsize=20)
        for session in (manager.search_engine.session, manager.downloader.session):
            session.mount('https://', adapter)
            session.mount('http://', adapter)
    if runtime_options.get('_render_repair_mode'):
        def _repair_float(name: str, default: float) -> float:
            try:
                return max(1.0, float(runtime_options.get(name, default) or default))
            except (TypeError, ValueError):
                return default

        def _repair_int(name: str, default: int) -> int:
            try:
                return max(1, int(runtime_options.get(name, default) or default))
            except (TypeError, ValueError):
                return default

        manager.config.search_timeout = int(_repair_float('stock_repair_search_timeout_seconds', 20.0))
        manager.config.download_timeout = int(_repair_float('stock_repair_download_timeout_seconds', 45.0))
        manager.config.max_concurrent_downloads = max(
            manager.config.max_concurrent_downloads,
            _repair_int('stock_repair_download_workers', 4),
        )
        manager.config.query_variant_limit = _repair_int('stock_repair_query_variants', 2)
        manager.config.max_results_per_source = _repair_int('stock_repair_results_per_source', 8)
        manager.config.max_gathered_candidates = _repair_int('stock_repair_gathered_candidates', 16)
        manager.config.scene_search_round_timeout = _repair_float('stock_repair_scene_search_timeout_seconds', 20.0)
        manager.config.batch_resolve_timeout = _repair_float('stock_repair_resolve_timeout_seconds', 60.0)
        manager.config.batch_download_timeout = _repair_float('stock_repair_batch_download_timeout_seconds', 75.0)
        print(
            "   [stock] repair profile: "
            f"{manager.config.query_variant_limit} query variants, "
            f"{manager.config.max_concurrent_downloads} download workers, "
            f"{manager.config.batch_download_timeout:.0f}s batch wall",
            flush=True,
        )
    
    # Build scene data from prompts
    scenes = []
    for i, (prompt, duration) in enumerate(zip(scene_prompts, scene_durations)):
        # Extract keywords from prompt
        keywords = _extract_keywords_from_prompt(prompt)
        
        # Detect scene type from prompt
        scene_type = _detect_scene_type(prompt)
        
        q_prof = scene_queries[i] if scene_queries and i < len(scene_queries) and isinstance(scene_queries[i], dict) else {}
        rt_contracts = (runtime_options.get("scene_contracts") or []) if isinstance(runtime_options, dict) else []
        rt_contract = rt_contracts[i] if i < len(rt_contracts) and isinstance(rt_contracts[i], dict) else {}
        forbidden_val = (
            q_prof.get("forbidden_subjects")
            or rt_contract.get("forbidden_subjects")
            or (rt_contract.get("visual_requirement") or {}).get("forbidden_subjects")
            or (runtime_options.get("forbidden_subjects") if isinstance(runtime_options, dict) else None)
        )
        visual_req = (
            rt_contract.get("visual_requirement")
            or q_prof.get("visual_requirement")
            or (runtime_options.get("visual_requirement") if isinstance(runtime_options, dict) else None)
        )
        scenes.append({
            'text': prompt,
            'keywords': keywords,
            'duration': duration,
            'scene_type': scene_type,
            'index': i,
            'source_priorities': source_priorities,
            'query_profile': q_prof,
            'forbidden_subjects': forbidden_val,
            'visual_requirement': visual_req,
            '_stock_video_only': bool((runtime_options or {}).get('stock_video_only')),
            '_stock_last_resort': bool(
                runtime_options.get('_stock_last_resort')
                or runtime_options.get('_render_repair_mode')
            ) if isinstance(runtime_options, dict) else False,
        })

    if scene_queries or media_sources:
        scene_media_list = _find_stock_media_for_scenes(
            manager=manager,
            scenes=scenes,
            title=title,
            media_sources=media_sources or [],
            progress_callback=(runtime_options or {}).get("_progress_callback")
            if isinstance(runtime_options, dict)
            else None,
            retry_rounds=(runtime_options or {}).get("stock_repair_retry_rounds")
            if isinstance(runtime_options, dict)
            else None,
        )
    else:
        # Backward-compatible fallback path (no sources configured)
        scene_media_list = manager.find_media_for_scenes(scenes, title)

    # Extract local paths and retain the provider metadata.  The normal public
    # API still returns a plain path list; the terminal coordinator can opt in
    # to the metadata payload so its no-vision stock lane can reject obvious
    # synthetic/non-photographic substitutions.
    paths = []
    metadata = []
    for sm in scene_media_list:
        item = sm.primary_media if sm else None
        if item and item.is_downloaded:
            paths.append(item.local_path)
        else:
            paths.append(None)
        if item is None:
            metadata.append({})
        else:
            metadata.append({
                'title': str(item.title or ''),
                'description': str(item.description or ''),
                'tags': list(item.tags or []),
                'categories': list(item.tags or []),
                'source': str(item.source or 'stock'),
                'provider': str(item.source or 'stock'),
                'matched_query': str((item.score_breakdown or {}).get('matched_query') or ''),
                'source_url': str((item.score_breakdown or {}).get('source_url') or ''),
                'page_url': str((item.score_breakdown or {}).get('page_url') or ''),
                'license': str((item.score_breakdown or {}).get('license') or ''),
                'license_url': str((item.score_breakdown or {}).get('license_url') or ''),
                'rights_status': str((item.score_breakdown or {}).get('rights_status') or 'provider_license_check_required'),
                'license_verification_required': bool((item.score_breakdown or {}).get('license_verification_required', True)),
                'attribution': str((item.score_breakdown or {}).get('attribution') or ''),
                'creator': str((item.score_breakdown or {}).get('creator') or ''),
                'creator_url': str((item.score_breakdown or {}).get('creator_url') or ''),
                'provider_asset_id': str((item.score_breakdown or {}).get('provider_asset_id') or ''),
                'score': float(item.score or 0.0),
                'width': int(item.width or 0),
                'height': int(item.height or 0),
                'media_type': str(item.type or 'image'),
                'retrieval_method': str((item.score_breakdown or {}).get('retrieval_method') or ''),
            })

    # Fallback: if all None and API keys are missing, try HTML scrapers for images
    success_count = sum(1 for p in paths if p is not None)
    if success_count == 0 and not pexels_key and not pixabay_key and _stock_remaining(manager.config, 1) > 0:
        print("\n   Stock search produced no results (API keys missing).")
        print("   Trying HTML scrapers for stock images as last resort...")
        for i, (prompt, duration) in enumerate(zip(scene_prompts, scene_durations)):
            if _stock_remaining(manager.config, 1) <= 0:
                break
            if i < len(paths) and paths[i] is not None:
                continue
            try:
                query = " ".join(_extract_keywords_from_prompt(prompt)[:4])
                if not query:
                    query = " ".join(prompt.split()[:4])
                import hashlib
                from pathlib import Path
                image = _scrape_stock_image(query, fallback_providers=True)
                if image and image.get('download_url'):
                    local_path = _download_stock_fallback(image, query)
                    if local_path:
                        paths[i] = local_path
                        if i >= len(metadata):
                            metadata.extend({} for _ in range(i + 1 - len(metadata)))
                        metadata[i] = {
                            'title': str(image.get('title') or image.get('alt') or ''),
                            'description': str(image.get('description') or image.get('alt') or ''),
                            'tags': list(image.get('tags') or image.get('categories') or []),
                            'source': str(image.get('source') or 'stock'),
                            'provider': str(image.get('source') or 'stock'),
                            'source_url': str(image.get('source_url') or image.get('page_url') or ''),
                            'page_url': str(image.get('page_url') or image.get('source_url') or ''),
                            'matched_query': str(query),
                            'media_type': 'image',
                        }
                        print(f"   [stock] scene {i + 1}: fallback stock image from {image.get('source', 'unknown')}")
            except Exception as exc:
                print(f"   [stock] scene {i + 1} scraper fallback failed: {exc}")
        success_count = sum(1 for p in paths if p is not None)

    # Summary
    print(f"\nREAL MEDIA RESULT: {success_count}/{len(scene_prompts)} scenes")
    
    if isinstance(runtime_options, dict) and runtime_options.get('_return_metadata'):
        return {'paths': paths, 'metadata': metadata}
    return paths


def _dedupe_query_variants(queries: List[str]) -> List[str]:
    seen = set()
    result = []
    for query in queries:
        normalized = " ".join((query or "").split()).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _dedupe_words(words: List[str]) -> List[str]:
    seen = set()
    result = []
    for word in words:
        normalized = re.sub(r"\s+", " ", str(word or "")).strip(" ,.;:!?-").lower()
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _normalize_media_text(value: str) -> str:
    value = re.sub(r'[_\-]+', ' ', str(value or '').lower())
    value = re.sub(r'[^a-z0-9\s]', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


def _media_tokens(item: MediaItem) -> List[str]:
    text = " ".join([
        item.title or '',
        item.description or '',
        " ".join(item.tags or []),
    ])
    tokens = []
    for token in _normalize_media_text(text).split():
        if len(token) <= 2:
            continue
        if token in _STOCK_METADATA_STOPWORDS:
            continue
        tokens.append(token)
    return _dedupe_words(tokens)


def _candidate_query_tokens(item: MediaItem) -> List[str]:
    provider_query = ""
    if item and isinstance(item.score_breakdown, dict):
        provider_query = str(item.score_breakdown.get("matched_query") or "")
    tokens = []
    for token in _normalize_media_text(provider_query).split():
        if len(token) <= 2:
            continue
        if token in _STOCK_METADATA_STOPWORDS:
            continue
        tokens.append(token)
    return _dedupe_words(tokens)


def _media_concept_tokens(item: MediaItem) -> List[str]:
    return _dedupe_words(_media_tokens(item) + _candidate_query_tokens(item))


def _media_source_key(item: MediaItem) -> str:
    """Stable provider key used to avoid reusing the same stock asset."""
    if not item:
        return ""
    source = (item.source or "").strip().lower()
    item_id = _normalize_media_text(item.id)
    if source and item_id:
        return f"{source}:{item_id}"

    for raw_value in [item.download_url, item.preview_url, item.local_path]:
        parsed = urllib.parse.urlparse(str(raw_value or ""))
        path = _normalize_media_text(parsed.path or raw_value)
        if path:
            return f"{source}:{path}" if source else path
    return ""


def _stock_candidate_asset_key(item: MediaItem) -> str:
    """Provider/rendition-independent key for duplicate stock search results."""
    if not item:
        return ""
    source_key = _media_source_key(item)
    if source_key:
        return source_key
    for raw_value in (item.download_url, item.preview_url, item.local_path):
        raw_text = str(raw_value or "").strip()
        if not raw_text:
            continue
        parsed = urllib.parse.urlparse(raw_text)
        path = parsed.path or raw_text
        path = re.sub(r"/(sd|hd|full_hd|large|medium|small|tiny|original|large2x)/", "/", path, flags=re.IGNORECASE)
        path = re.sub(r"[-_](\d{3,4}x\d{3,4}|sd|hd|fullhd|large|medium|small|tiny)(?=\.)", "", path, flags=re.IGNORECASE)
        normalized = _normalize_media_text(path)
        if normalized:
            return f"{(item.source or '').lower()}:{normalized}"
    title_tokens = _dedupe_words(_media_tokens(item)[:10])
    return " ".join(title_tokens)


def _media_signature(item: MediaItem) -> str:
    source_key = _media_source_key(item)
    if source_key:
        return source_key
    for raw_value in [item.id, item.download_url, item.preview_url, item.local_path]:
        normalized = _normalize_media_text(raw_value)
        if normalized:
            return normalized
    title_signature = " ".join(_media_tokens(item)[:8])
    return title_signature


def _media_similarity(left: MediaItem, right: MediaItem) -> float:
    if not left or not right:
        return 0.0
    left_signature = _media_signature(left)
    right_signature = _media_signature(right)
    if left_signature and right_signature and left_signature == right_signature:
        return 1.0

    left_tokens = set(_media_concept_tokens(left))
    right_tokens = set(_media_concept_tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0

    overlap = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return overlap / union if union else 0.0


def _is_recent_duplicate(candidate: MediaItem, existing_items: List[MediaItem]) -> bool:
    recent_items = existing_items[-4:]
    candidate_signature = _media_signature(candidate)

    for existing in recent_items:
        if candidate_signature and candidate_signature == _media_signature(existing):
            return True
        if _media_similarity(candidate, existing) >= 0.88:
            return True
    return False


def _is_global_duplicate(candidate: MediaItem, existing_items: List[MediaItem]) -> bool:
    candidate_signature = _media_signature(candidate)
    for existing in existing_items:
        if candidate_signature and candidate_signature == _media_signature(existing):
            return True
        if _media_similarity(candidate, existing) >= 0.82:
            return True
    return False


def _query_profile_terms(query_profile: Dict) -> List[str]:
    terms: List[str] = []
    for value in [
        query_profile.get('primary_query', ''),
        query_profile.get('alt_query', ''),
        query_profile.get('fallback_query', ''),
        " ".join(query_profile.get('query_variants') or []),
        " ".join(query_profile.get('scene_focus_terms') or []),
        " ".join(query_profile.get('support_terms') or []),
        query_profile.get('subject', ''),
        query_profile.get('category', ''),
        query_profile.get('setting', ''),
    ]:
        if value:
            terms.extend(_extract_keywords_from_prompt(str(value)))
    return _dedupe_words(terms)[:12]


def _scene_query_alignment(candidate: MediaItem, scene_context: Dict) -> float:
    query_profile = scene_context.get('query_profile', {}) or {}
    query_terms = _query_profile_terms(query_profile)
    if not query_terms:
        query_terms = _dedupe_words(scene_context.get('entities') or [])[:10]
    if not query_terms:
        return 0.0

    searchable_terms = set(_media_tokens(candidate))
    if not searchable_terms:
        return 0.0

    overlap = len(searchable_terms & set(query_terms))
    if not overlap:
        return 0.0

    score = min(12.0, overlap * 2.5)
    preferred_source = query_profile.get('preferred_source') or scene_context.get('preferred_source', '')
    if preferred_source and candidate.source == preferred_source:
        score += 1.5
    preferred_media_type = scene_context.get('media_type', 'video')
    if preferred_media_type == 'video' and candidate.type == 'video':
        score += 1.0
    elif preferred_media_type == 'image' and candidate.type == 'image':
        score += 1.0
    return min(14.0, score)


def _stock_visual_fit(candidate: MediaItem, scene_context: Dict) -> float:
    """Score how well the provider query and real metadata fit this scene."""
    query_profile = scene_context.get('query_profile', {}) or {}
    scene_terms = set(_query_profile_terms(query_profile))
    metadata_terms = set(_media_tokens(candidate))
    provider_terms = set(_candidate_query_tokens(candidate))
    if not scene_terms:
        scene_terms = set(_dedupe_words(scene_context.get('entities') or []))

    score = 0.0
    metadata_overlap = len(metadata_terms & scene_terms)
    provider_overlap = len(provider_terms & scene_terms)

    if metadata_overlap:
        score += min(10.0, metadata_overlap * 2.8)
    if provider_overlap:
        score += min(8.0, provider_overlap * 1.8)

    focus_terms = set(_dedupe_words(query_profile.get('scene_focus_terms') or []))
    if focus_terms:
        focus_overlap = len((metadata_terms | provider_terms) & focus_terms)
        score += min(6.0, focus_overlap * 2.0)

    primary_terms = set(_extract_keywords_from_prompt(query_profile.get('primary_query') or ''))
    if primary_terms and provider_terms:
        primary_overlap = len(primary_terms & provider_terms)
        score += min(4.0, primary_overlap * 1.5)

    provider_query = ""
    provider_relevance = 0.0
    provider_rank = 99
    if isinstance(candidate.score_breakdown, dict):
        provider_query = str(candidate.score_breakdown.get('matched_query') or '')
        provider_relevance = float(candidate.score_breakdown.get('provider_relevance') or 0.0)
        try:
            provider_rank = int(candidate.score_breakdown.get('provider_rank') or 99)
        except (TypeError, ValueError):
            provider_rank = 99

    specificity = _stock_query_specificity(provider_query)
    if specificity <= 0:
        score -= 6.0
    elif specificity >= 4:
        score += 2.0

    # Pexels video metadata is sparse. Trust provider rank only as a small tie-breaker.
    if not metadata_overlap and candidate.source == 'pexels' and provider_rank <= 2:
        score += min(4.0, provider_relevance * 0.3)

    if not metadata_overlap and not provider_overlap:
        score -= 8.0

    return max(-12.0, min(24.0, score))


def _least_duplicate_similarity(candidate: MediaItem, selected_items: List[MediaItem]) -> float:
    if not selected_items:
        return 0.0
    return max(_media_similarity(candidate, existing) for existing in selected_items)


def _choose_scene_candidate(scene_media: SceneMedia, selected_items: List[MediaItem]) -> Optional[MediaItem]:
    candidate_pool = [scene_media.primary_media] + list(scene_media.backup_media or [])
    ranked_pool = []
    seen_signatures = set()
    for candidate in candidate_pool:
        if not candidate:
            continue
        signature = _media_signature(candidate) or candidate.id
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        ranked_pool.append(candidate)

    for candidate in ranked_pool:
        if not _is_global_duplicate(candidate, selected_items):
            return candidate

    if not ranked_pool:
        return None
    return sorted(
        ranked_pool,
        key=lambda candidate: (
            _least_duplicate_similarity(candidate, selected_items),
            -(candidate.score or 0),
        ),
    )[0]


def _build_source_order(
    media_sources: List[str],
    query_profile: Dict,
    existing_items: List[MediaItem],
) -> List[str]:
    enabled_sources = [source for source in SUPPORTED_STOCK_SOURCES if source in media_sources]
    if not enabled_sources:
        return []

    preferred_source = query_profile.get('preferred_source')
    source_counts = {source: 0 for source in enabled_sources}
    for item in existing_items:
        if item.source in source_counts:
            source_counts[item.source] += 1

    last_source = existing_items[-1].source if existing_items else None

    def sort_key(source: str):
        return (
            0 if source != last_source else 1,
            0 if source == preferred_source else 1,
            source_counts.get(source, 0),
            ['pexels', 'pixabay'].index(source),
        )

    return sorted(enabled_sources, key=sort_key)


def _prefer_stock_media_type(scene: Dict, title: str) -> str:
    if scene.get('_stock_video_only'):
        return 'video'
    text = " ".join([
        scene.get('text', ''),
        title,
        (scene.get('query_profile', {}) or {}).get('query', ''),
        (scene.get('query_profile', {}) or {}).get('primary_query', ''),
    ]).lower()
    duration = float(scene.get('duration', 0) or 0)
    scene_type = (scene.get('scene_type') or '').lower()
    query_profile = scene.get('query_profile', {}) or {}
    forced = str(query_profile.get('preferred_media_type') or '').strip().lower()
    if forced in {'image', 'video', 'both'}:
        return forced

    motion_signals = {
        'walk', 'walking', 'running', 'match', 'game', 'goal', 'celebration',
        'crowd', 'waving', 'driving', 'flying', 'launch', 'arrives', 'arrival',
        'traffic', 'storm', 'ocean', 'wildlife', 'factory', 'robot', 'protest',
        'battle', 'explosion', 'parade', 'ceremony', 'summit',
    }
    static_types = {'historical', 'interview', 'news'}
    static_visual_signals = {
        'portrait', 'logo', 'chart', 'graph', 'document', 'map', 'diagram',
        'poster', 'screenshot', 'interface', 'dashboard', 'filing',
    }

    if any(signal in text for signal in static_visual_signals):
        return 'image' if duration <= 2.5 else 'both'
    if duration >= 2.2 and any(signal in text for signal in motion_signals):
        return 'video'
    if duration >= 3.0 and scene_type not in static_types:
        return 'video'
    if scene_type in static_types:
        return 'both'
    return 'video'


def _stock_type_priority(item: MediaItem, preferred_media_type: str) -> int:
    if preferred_media_type == 'video':
        if item.type == 'video':
            return 0
        if item.type == 'image':
            return 1
    if preferred_media_type == 'both':
        if item.type == 'video':
            return 0
        if item.type == 'image':
            return 1
    return 0 if item.type == 'image' else 1


def _stock_candidate_compatibility(item: MediaItem, scene_context: Dict, preferred_media_type: str) -> float:
    """Reward clips that fit the render shape and scene duration without being huge."""
    score = 0.0
    duration_needed = max(0.5, float(scene_context.get('duration_needed') or 4.0))

    if item.type == preferred_media_type:
        score += 5.0
    elif preferred_media_type == 'both' and item.type == 'video':
        score += 3.0
    elif preferred_media_type == 'video' and item.type == 'image':
        score -= 2.0

    ratio = item.aspect_ratio()
    if ratio >= 1.55:
        score += 5.0
    elif ratio >= 1.25:
        score += 2.0
    else:
        score -= 3.0

    if item.width >= 1920 and item.height >= 1080:
        score += 4.0
    elif item.width >= 1280 and item.height >= 720:
        score += 2.5
    elif item.width < 960 or item.height < 540:
        score -= 2.0

    if item.type == 'video':
        duration = float(item.duration or 0)
        if duration <= 0:
            score -= 1.0
        elif duration >= duration_needed * 0.9 and duration <= max(duration_needed * 4.0, duration_needed + 18.0):
            score += 7.0
        elif duration >= duration_needed * 0.55:
            score += 3.0
        else:
            score -= 2.0
        if duration > 120:
            score -= 1.0

    size_mb = item.size_bytes / (1024 * 1024) if item.size_bytes else 0
    if size_mb > _STOCK_MAX_DOWNLOAD_MB:
        score -= 35.0
    elif 0 < size_mb <= 18:
        score += 5.0
    elif 0 < size_mb <= _STOCK_MAX_DOWNLOAD_MB:
        score += 2.0

    return score


def _stock_query_specificity(query: str) -> float:
    terms = [
        term for term in re.findall(r"[a-zA-Z0-9'-]+", query or "")
        if len(term) > 2 and term.lower() not in _STOCK_METADATA_STOPWORDS
    ]
    unique_terms = _dedupe_words(terms)
    if not unique_terms:
        return -4.0
    generic_count = sum(1 for term in unique_terms if term.lower() in _GENERIC_STOCK_TERMS)
    return min(6.0, len(unique_terms) * 1.2) - (generic_count * 1.5)


def _stock_query_overrides(query_profile: Dict, title: str, scene_text: str) -> List[str]:
    """Concrete stock B-roll substitutes for narration-heavy biography beats."""
    combined = f"{title} {scene_text} {json.dumps(query_profile, ensure_ascii=True)}".lower()
    if re.search(r'\b(?:beers?|brewery|breweries|brewing)\b', combined):
        if re.search(r'\b(?:shelves|bottles|brands|labels)\b', str(scene_text).lower()):
            return ['beer bottles shelf', 'beer bottle label', 'pub beer taps']
        if re.search(r'\b(?:pub|pint|drinking|taste)\b', str(scene_text).lower()):
            return ['beer tap pouring', 'traditional pub interior', 'glass beer bar']
        return ['brewery beer production', 'brewery tanks', 'beer bottling factory']
    if not ({"elon", "musk"} <= set(_extract_keywords_from_prompt(combined)) or "elon musk" in combined):
        return []

    rules = [
        (("emerald", "mine", "errol", "father", "zambia"), ["mine worker", "gemstone mining", "south africa landscape"]),
        (("south africa", "military", "pretoria", "childhood", "bullied"), ["school hallway teenager", "south africa city", "young student classroom"]),
        (("canada", "backpack", "arrived", "queen", "airport"), ["young traveler backpack airport", "airport arrival traveler", "student backpack campus"]),
        (("boiler", "cleaning", "lumber", "farm", "odd job"), ["industrial boiler room", "factory worker machinery", "warehouse worker"]),
        (("blastar", "game", "computer", "sold magazine"), ["retro computer programming", "computer code screen", "teen programmer laptop"]),
        (("zip2", "palo alto", "ymca", "compaq", "city guide"), ["startup office laptop", "programmer office night", "small tech office"]),
        (("x.com", "online bank", "paypal", "confinity", "peter thiel"), ["fintech laptop payment", "online banking laptop", "startup team office"]),
        (("spacex", "falcon", "rocket", "nasa", "launch", "explosion", "separation"), ["rocket launch smoke", "space rocket launch", "rocket engine fire"]),
        (("factory", "engine", "rocket engine", "spacex office"), ["rocket engine factory", "factory assembly line", "engine manufacturing"]),
        (("tesla", "cybertruck", "electric car", "model"), ["electric car factory", "car assembly line", "electric vehicle showroom"]),
        (("twitter", "bought twitter"), ["social media phone", "office phone app", "tech office screens"]),
    ]
    for triggers, queries in rules:
        if any(trigger in combined for trigger in triggers):
            return queries
    return ["tech entrepreneur office", "startup office laptop", "electric car factory"]


def _expand_stock_queries(queries: List[str], query_profile: Dict, title: str, scene_text: str) -> List[str]:
    """Add concrete non-YouTube stock alternatives for weak/repeated query packs."""
    expanded = list(queries or [])
    subject = str(query_profile.get("subject") or "").strip()
    category = str(query_profile.get("category") or "").strip()
    setting = str(query_profile.get("setting") or "").strip()
    focus_terms = _dedupe_words(query_profile.get("scene_focus_terms") or [])
    support_terms = _dedupe_words(query_profile.get("support_terms") or [])
    title_terms = _dedupe_words(_extract_keywords_from_prompt(title or "")[:5])
    scene_terms = _dedupe_words(_extract_keywords_from_prompt(scene_text or "")[:8])

    concrete_terms = [
        term for term in focus_terms + support_terms + scene_terms
        if term not in _GENERIC_STOCK_TERMS and term not in _STOCK_METADATA_STOPWORDS
    ]
    anchors = [value for value in [subject, category, setting] if value and value.lower() not in {"abstract", "other", "generic"}]

    if anchors and concrete_terms:
        expanded.append(" ".join(_dedupe_words(anchors[:1] + concrete_terms[:3])))
    if category and setting:
        expanded.append(f"{category} {setting}")
    if setting and concrete_terms:
        expanded.append(" ".join(_dedupe_words([setting] + concrete_terms[:3])))
    if title_terms and concrete_terms:
        expanded.append(" ".join(_dedupe_words(title_terms[:2] + concrete_terms[:3])))
    if concrete_terms:
        expanded.append(" ".join(concrete_terms[:4]))

    lower_text = f"{title} {scene_text} {subject} {category} {setting}".lower()
    domain_rules = [
        (("locomotive", "train", "railway", "railroad", "steam engine", "mallard", "stoke bank", "kylchap"), [
            "steam locomotive moving",
            "vintage train tracks",
            "steam train engine room",
            "train wheels track",
            "steam locomotive smoke",
        ]),
        (("stadium", "football", "fifa", "world cup", "match"), [
            "football stadium crowd",
            "soccer stadium seats",
            "stadium exterior fans",
            "football fans concourse",
        ]),
        (("calculation", "percent", "capacity", "number"), [
            "calculator paper numbers",
            "sports statistics screen",
            "notebook calculation",
        ]),
        (("ticket", "attendance", "empty", "seat"), [
            "empty stadium seats",
            "sparse stadium crowd",
            "ticket booth stadium",
        ]),
        (("camera", "broadcast", "aerial"), [
            "stadium broadcast camera",
            "sports camera operator",
            "stadium drone view",
        ]),
    ]
    for triggers, variants in domain_rules:
        if any(trigger in lower_text for trigger in triggers):
            expanded.extend(variants)

    return expanded


def _rank_stock_queries(queries: List[str], query_profile: Dict, title: str) -> List[str]:
    """Prefer concrete scene phrases over broad fallback searches."""
    scene_text = str(query_profile.get("scene_text") or query_profile.get("text") or "")
    override_queries = _stock_query_overrides(query_profile, title, scene_text)
    override_set = {q.lower() for q in override_queries}
    deduped = _dedupe_query_variants(override_queries + _expand_stock_queries(queries, query_profile, title, scene_text))
    scene_terms = set(_query_profile_terms(query_profile))
    title_terms = set(_extract_keywords_from_prompt(title or "")[:6])
    bad_terms = {
        "people", "street", "landscape", "fact", "facts", "nobody", "not",
        "thing", "things", "matter", "matters", "obvious", "threshold",
        "hagiography", "written", "sign-off", "privilege", "thought",
    }

    def query_score(query: str) -> float:
        terms = set(_extract_keywords_from_prompt(query))
        score = _stock_query_specificity(query)
        if query.lower() in override_set:
            score += 12.0
        score -= len(terms & bad_terms) * 3.0
        if terms & scene_terms:
            score += min(5.0, len(terms & scene_terms) * 1.5)
        if terms & title_terms:
            # Stock libraries usually do not have exact celebrity/news footage.
            # Do not let names beat concrete B-roll concepts.
            score += min(1.0, len(terms & title_terms) * 0.35)
        if any(term in terms for term in {"office", "factory", "laptop", "programmer", "rocket", "launch", "car", "airport", "worker", "engine", "payment"}):
            score += 3.0
        if query == query_profile.get("primary_query") or query == query_profile.get("query"):
            score += 2.0
        if query == query_profile.get("fallback_query"):
            score -= 1.5
        return score

    return sorted(deduped, key=query_score, reverse=True)


def _build_stock_keywords(scene: Dict, title: str) -> List[str]:
    query_profile = scene.get('query_profile', {}) or {}
    terms = []
    for value in [
        scene.get('text', ''),
        title,
        query_profile.get('query', ''),
        query_profile.get('alt', ''),
        query_profile.get('primary_query', ''),
        query_profile.get('alt_query', ''),
        query_profile.get('fallback_query', ''),
        " ".join(query_profile.get('query_variants') or []),
        " ".join(query_profile.get('scene_focus_terms') or []),
        " ".join(query_profile.get('support_terms') or []),
        query_profile.get('subject', ''),
        query_profile.get('category', ''),
        query_profile.get('setting', ''),
    ]:
        if value:
            terms.extend(_extract_keywords_from_prompt(str(value)))
    return _dedupe_words(terms)[:10]


def stock_scene_allowed(scene: Dict) -> Tuple[bool, str]:
    """Apply the V2 guarded-stock policy without guessing from a title.

    Storyboard V2 supplies an explicit ``source_policy.stock_allowed`` value.
    Legacy scenes remain eligible unless they carry an explicit named/strict
    factual marker; this preserves the public API while making new contracts
    fail closed.
    """
    row = scene if isinstance(scene, dict) else {}
    policy = row.get("source_policy") or {}
    if hasattr(policy, "stock_allowed"):
        allowed = bool(getattr(policy, "stock_allowed"))
        return allowed, "contract_allowed" if allowed else "contract_forbids_stock"
    if isinstance(policy, dict) and "stock_allowed" in policy:
        allowed = bool(policy.get("stock_allowed"))
        return allowed, "contract_allowed" if allowed else "contract_forbids_stock"
    if bool(row.get("named_factual") or row.get("strict_factual")):
        return False, "named_factual_scene"
    visual_intent = str(row.get("visual_intent") or "").strip().lower()
    positive_subjects = [str(value).strip() for value in (row.get("positive_subjects") or []) if str(value).strip()]
    if positive_subjects and visual_intent in {
        "identity", "named_person", "named_place", "named_event", "document", "date",
    }:
        return False, "named_factual_visual_intent"
    return True, "legacy_or_generic_scene"


def _stock_resolution_cache_scope(
    scene_index: int,
    scene: Dict,
    provider_query: str,
    provider: str,
) -> str:
    payload = "|".join([
        str(
            scene.get("run_id")
            or scene.get("job_id")
            or scene.get("output_namespace")
            or scene.get("_stock_run_scope")
            or "unscoped"
        ),
        str(scene.get("scene_id") or scene_index),
        str(scene.get("container_id") or "container"),
        str(scene.get("contract_hash") or "legacy"),
        str(provider_query or ""),
        str(provider or ""),
    ])
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()


def _stock_rescue_metadata_gate(candidate: MediaItem, scene: Mapping[str, Any]) -> Tuple[bool, str]:
    from .media_resolution_coordinator import _stock_terminal_metadata_gate
    metadata = candidate.score_breakdown or {}
    return _stock_terminal_metadata_gate(scene, {
        'provider': 'stock', 'stock_last_resort': True,
        'title': candidate.title, 'description': candidate.description,
        'tags': candidate.tags, 'source_url': metadata.get('source_url', ''),
        'page_url': metadata.get('page_url', ''),
    })


def _stock_candidate_passes_accuracy_gate(
    candidate: MediaItem,
    config: MediaConfig,
    scene: Optional[Mapping[str, Any]] = None,
) -> Tuple[bool, str]:
    breakdown = candidate.score_breakdown if isinstance(candidate.score_breakdown, dict) else {}
    try:
        total_floor = max(
            float(config.min_score_threshold),
            float(os.environ.get("VIDZEO_STOCK_MIN_ACCEPT_SCORE", "35") or 35),
        )
    except (TypeError, ValueError):
        total_floor = max(float(config.min_score_threshold), 35.0)
    try:
        relevance_floor = max(
            0.0,
            float(os.environ.get("VIDZEO_STOCK_MIN_RELEVANCE", "6") or 6),
        )
    except (TypeError, ValueError):
        relevance_floor = 6.0
    relevance = float(breakdown.get("relevance") or 0.0)
    if float(candidate.score or 0.0) < total_floor:
        return False, f"score_below_floor:{float(candidate.score or 0.0):.2f}<{total_floor:.2f}"
    if relevance < relevance_floor:
        return False, f"relevance_below_floor:{relevance:.2f}<{relevance_floor:.2f}"
    qwen = breakdown.get("qwen_result")
    if isinstance(qwen, dict):
        accepted = qwen.get("accepted")
        entity_conflict = bool(qwen.get("entity_conflict") or qwen.get("fatal_mismatch"))
        if accepted is False or entity_conflict:
            return False, "qwen_rejected"

    if scene and scene.get('_stock_last_resort'):
        # Apply the terminal contract BEFORE downloading and returning only
        # one winner. Previously the stock runner chose unrelated generic
        # people footage, then the coordinator rejected that sole result.
        # All other API candidates were lost and every retry repeated it.
        passed, reason = _stock_rescue_metadata_gate(candidate, scene)
        if not passed:
            return False, reason

    blob_parts = []
    if getattr(candidate, "title", None):
        blob_parts.append(str(candidate.title))
    if getattr(candidate, "description", None):
        blob_parts.append(str(candidate.description))
    tags = getattr(candidate, "tags", None)
    if isinstance(tags, (list, tuple, set)):
        blob_parts.extend(str(tag) for tag in tags)
    for extra_key in ("categories", "caption", "alt", "tags"):
        val = breakdown.get(extra_key)
        if isinstance(val, (list, tuple, set)):
            blob_parts.extend(str(item) for item in val)
        elif val:
            blob_parts.append(str(val))
    blob = " ".join(blob_parts).casefold()

    if blob:
        for term in _STOCK_NON_PHOTOGRAPHIC_TERMS:
            if re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", blob):
                return False, f"non_photographic_metadata:{term}"

        if scene and isinstance(scene, Mapping):
            requirement = scene.get("visual_requirement")
            forbidden = ()
            if isinstance(requirement, Mapping):
                forbidden = requirement.get("forbidden_subjects") or ()
            if not forbidden:
                forbidden = scene.get("forbidden_subjects") or ()
            if isinstance(forbidden, str):
                forbidden = [forbidden]
            for forbidden_term in forbidden:
                term = str(forbidden_term or "").strip().casefold()
                if not term:
                    continue
                if re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", blob):
                    return False, f"forbidden_subject_metadata:{term}"

    return True, "passed"


def _stock_remaining(config, default: float) -> float:
    event = getattr(config, 'stock_cancel_event', None)
    if event is not None and event.is_set():
        return 0.0
    deadline = getattr(config, 'stock_deadline_at', None)
    return min(float(default), max(0.0, deadline - time.monotonic())) if deadline is not None else float(default)


def _search_stock_candidates(
    manager: RealMediaManager,
    query: str,
    source: str,
    scene_context: Dict,
    media_type: str = 'image',
) -> List[MediaItem]:
    if not query or _stock_remaining(manager.config, 1) <= 0:
        return []

    search_engine = manager.search_engine
    source_key = (source or "").strip().lower()
    def load():
        if source_key == 'pexels':
            return search_engine._search_pexels(query, media_type, scene_context) or []
        if source_key == 'pixabay':
            return search_engine._search_pixabay(query, media_type, scene_context) or []
        return []
    pool = getattr(manager.config, 'media_pool', None)
    if isinstance(pool, StockMediaPool):
        key = (source_key, ' '.join(query.casefold().split()), media_type,
               manager.config.min_resolution, manager.config.max_results_per_source,
               manager.config.preferred_aspect_ratio)
        results = pool.search(key, load, timeout=_stock_remaining(manager.config, manager.config.search_timeout),
                              cancel_event=getattr(manager.config, 'stock_cancel_event', None))
    else:
        results = load()
    return results if _stock_remaining(manager.config, 1) > 0 else []


def _stock_last_resort_queries(
    scene: Mapping[str, Any],
    title: str,
    query_profile: Mapping[str, Any],
) -> list[str]:
    """Build a couple of contextual stock queries from the actual scene.

    Provider-planner labels can be empty or misleading (for example,
    ``people outdoor``). Last-resort stock is general B-roll, so search by the
    narrated action/domain and period without claiming an exact identity.
    """
    text = str(scene.get("text") or query_profile.get("scene_text") or "")
    context = " ".join((str(title or ""), text, str(query_profile.get("subject") or "")))
    normalized = context.casefold()
    # Stock libraries index visible subjects/actions rather than proper names
    # or narrative prose. These queries are contextual B-roll, never proof of
    # a specific person or event.
    if re.search(r"\b(?:beers?|ales?|lager|stout|brews?|brewing|brewer(?:y|ies))\b", normalized):
        local = text.casefold()
        if re.search(r"\b(?:bottl|cans?|label|packag)", local):
            return ['beer bottles brewery', 'beer bottling production']
        if re.search(r"\b(?:brew|malt|coke|hop|ferment|barrel|keg)", local):
            return ['beer brewing brewery', 'brewery fermentation tanks']
        return ['beer pint pub bar', 'beer pouring glass']
    if re.search(r"\b(?:trains?|railway|railroad|locomotives?)\b", normalized):
        if re.search(r"\b(?:steam|scotsman|mallard|tornado)\b", normalized):
            return ['steam train locomotive', 'steam locomotive railway']
        return ['train railway tracks', 'train station railway']
    if re.search(r"\b(?:royal|royalty|sovereign|monarch|garter|chivalry|throne|castle|chapel|palace|prince|princess|buckingham|kensington)\b", normalized):
        local = text.casefold()
        if re.search(r"\b(?:lunch|banquet|dining)\b", local):
            return ['formal banquet dining table', 'palace dining hall']
        if re.search(r"\b(?:carriages?|cars|return)\b", local):
            return ['horse carriage procession', 'English castle exterior']
        if re.search(r"\b(?:chapel|church|service)\b", local):
            return ['English gothic church interior', 'historic castle exterior']
        if re.search(r"\b(?:tickets|public|watch|procession|pageantry)\b", local):
            return ['crowd watching parade', 'ceremonial procession castle']
        # A named royal's contemporary story needs institutional context,
        # rather than a medieval interior or a prose-length "lifestyle"
        # search. This is illustrative B-roll, never a depicted diagnosis.
        if re.search(r"\b(?:prince|princess|buckingham|kensington|palace)\b", normalized):
            return ['Buckingham Palace London exterior', 'Kensington Palace London exterior']
        return ['English castle interior', 'medieval castle architecture']
    if re.search(r"\b(?:childhood|children|child|boys?|girls?)\b", normalized) and re.search(
        r"\b(?:victorian|historical|historic|britain|british|century|old)\b", normalized
    ):
        return [
            "Victorian era British children playing street games archival photograph",
            "historic British children playing outdoors vintage photo",
        ]
    if re.search(r"\b(?:classic|vintage|antique)\b", normalized) and re.search(
        r"\b(?:car|cars|automotive|automobile|vehicle|auction)\b", normalized
    ):
        return [
            "vintage classic cars driving archival footage",
            "classic car auction vintage automobile detail",
        ]
    setting = str(query_profile.get("setting") or "").strip()
    category = str(query_profile.get("category") or "").strip()
    planned = str(query_profile.get('primary_query') or query_profile.get('query') or '').strip()
    # A concise planner query is more useful to a stock API than a narrated
    # sentence. Strip narrative/style filler and cap generic recovery queries
    # so repeated related scenes share provider pages and choose distinct assets.
    raw_focus = planned if 1 < len(planned.split()) <= 8 else " ".join(
        value for value in (category, setting, str(title or '')) if value)
    filler = {'the', 'a', 'an', 'of', 'and', 'or', 'to', 'in', 'on', 'for', 'from',
              'with', 'that', 'this', 'which', 'was', 'were', 'is', 'are', 'as',
              'documentary', 'archival', 'photograph', 'photo', 'footage', 'roll',
              'other', 'outdoor', 'scene', 'past', 'what', 'how', 'why', 'names',
              'still', 'exist', 'different', 'useful', 'starting', 'point'}
    words = re.findall(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", raw_focus, re.UNICODE)
    scene_focus = ' '.join(dict.fromkeys(word for word in words if word.casefold() not in filler))
    scene_focus = ' '.join(scene_focus.split()[:6])
    if not scene_focus:
        scene_focus = str(title or "documentary scene").strip()
    return [
        scene_focus,
        f"{scene_focus} detail",
    ]


def _resolve_stock_scene_media(
    manager: RealMediaManager,
    scene_index: int,
    scene: Dict,
    existing_items: List[MediaItem],
    title: str,
    media_sources: List[str],
    download_media: bool = True,
    search_executor: Optional[ThreadPoolExecutor] = None,
) -> SceneMedia:
    text = scene.get('text', '')
    duration = scene.get('duration', 10)
    scene_type = scene.get('scene_type', 'generic')
    query_profile = dict(scene.get('query_profile', {}) or {})
    query_profile.setdefault('scene_text', text)
    stock_allowed, stock_policy_reason = stock_scene_allowed(scene)
    if not stock_allowed:
        return SceneMedia(
            scene_index=scene_index,
            scene_text=text,
            scene_type=scene_type,
            duration_needed=duration,
            primary_media=None,
            fallback_level=4,
            fallback_reason=stock_policy_reason,
            transition_in='cut' if scene_index > 0 else 'fade_in',
        )

    source_priorities = {
        'pexels': 1.0 if 'pexels' in media_sources else 0.0,
        'pixabay': 1.0 if 'pixabay' in media_sources else 0.0,
    }
    source_order = _build_source_order(media_sources, query_profile, existing_items)
    default_source = source_order[0] if source_order else 'pexels'
    query_preferred_source = query_profile.get('preferred_source') or default_source
    if query_preferred_source not in {'pexels', 'pixabay'}:
        query_preferred_source = default_source
    preferred_media_type = _prefer_stock_media_type(scene, title)
    # Emergency stock is a last-resort availability lane.  Prefer a small
    # still image when the caller explicitly requests image-only rescue so a
    # slow Pexels video download cannot hold the whole job open for minutes.
    # The normal stock planner keeps its existing motion preference.
    image_only_rescue = bool(
        scene.get("_stock_image_only")
        or query_profile.get("_stock_image_only")
        or query_profile.get("preferred_media_type") == "image"
    )
    if image_only_rescue:
        preferred_media_type = "image"

    scene_context = {
        'text': text,
        'scene_type': scene_type,
        'duration_needed': duration,
        'entities': _build_stock_keywords(scene, title),
        'time_period': 'contemporary',
        'source_priorities': source_priorities,
        'query_profile': query_profile,
        'preferred_source': query_preferred_source,
        'media_type': preferred_media_type,
        '_stock_last_resort': bool(scene.get('_stock_last_resort')),
    }

    keywords = _build_stock_keywords(scene, title)
    primary_query = query_profile.get('primary_query') or query_profile.get('query') or text or title
    alt_query = query_profile.get('alt_query') or query_profile.get('alt') or query_profile.get('fallback_query') or primary_query
    fallback_query = query_profile.get('fallback_query') or " ".join(
        part for part in [
            query_profile.get('subject', ''),
            query_profile.get('category', ''),
            query_profile.get('setting', ''),
        ] if part
    )

    query_variant_limit = max(
        1,
        min(
            _STOCK_QUERY_VARIANT_LIMIT,
            int(getattr(manager.config, "query_variant_limit", _STOCK_QUERY_VARIANT_LIMIT) or _STOCK_QUERY_VARIANT_LIMIT),
        ),
    )
    max_results_per_source = max(
        3,
        min(
            _MAX_STOCK_RESULTS_PER_SOURCE,
            int(getattr(manager.config, "max_results_per_source", _MAX_STOCK_RESULTS_PER_SOURCE) or _MAX_STOCK_RESULTS_PER_SOURCE),
        ),
    )
    max_gathered_candidates = max(
        4,
        min(
            _MAX_STOCK_GATHERED_CANDIDATES,
            int(getattr(manager.config, "max_gathered_candidates", _MAX_STOCK_GATHERED_CANDIDATES) or _MAX_STOCK_GATHERED_CANDIDATES),
        ),
    )
    query_candidates = _rank_stock_queries(
        list(query_profile.get('query_variants') or []) + [primary_query, alt_query, fallback_query],
        query_profile,
        title,
    )[:query_variant_limit]
    if scene.get('_stock_last_resort'):
        # Query the contextual stock vocabulary immediately; repeating exact
        # event names first wastes the small rescue window on empty results.
        query_candidates = _stock_last_resort_queries(scene, title, query_profile)[:query_variant_limit]
    source_query_map = []
    for source in source_order:
        if source_priorities.get(source, 0.0) <= 0:
            continue
        if source == query_preferred_source:
            queries = query_candidates[:query_variant_limit] or query_candidates[:1]
        else:
            queries = query_candidates[:1]
        source_query_map.append((source, queries))

    gathered: List[MediaItem] = []
    seen_keys = set()
    search_media_types = []
    # ``both`` can re-introduce a video candidate when an image query is
    # empty (Pexels' API checks videos first for ``both``).  That is desirable
    # for an ordinary motion request, but defeats the bounded emergency image
    # lane and causes large video downloads.  Keep the image-only contract
    # strict; the coordinator can still reuse an already downloaded asset.
    media_type_order = (
        ['image'] if image_only_rescue
        else [preferred_media_type, 'image'] if scene.get('_stock_last_resort')
        else [preferred_media_type, 'both', 'image']
    )
    for media_type in media_type_order:
        if media_type not in search_media_types:
            search_media_types.append(media_type)

    for search_media_type in search_media_types:
        if scene.get('_stock_video_only') and search_media_type != 'video':
            continue
        if _stock_remaining(manager.config, 1) <= 0:
            break
        per_source_counts = {source: 0 for source, _ in source_query_map}
        search_tasks = [
            (source, query)
            for source, query_list in source_query_map
            for query in query_list
        ]
        if not search_tasks:
            continue

        max_parallel_searches = max(1, min(len(search_tasks), len(source_query_map) * _MAX_PROVIDER_CONCURRENT_REQUESTS))
        owns_executor = search_executor is None
        active_executor = search_executor or ThreadPoolExecutor(max_workers=max_parallel_searches)
        try:
            print(f"   [stock] scene {scene_index + 1}: querying {len(search_tasks)} variants in parallel...")
            future_map = {
                active_executor.submit(
                    _search_stock_candidates,
                    manager,
                    query,
                    source,
                    scene_context,
                    media_type=search_media_type,
                ): (source, query)
                for source, query in search_tasks
            }

            try:
                for future in as_completed(
                    future_map,
                    timeout=min(
                        _stock_remaining(manager.config, 120),
                        manager.config.search_timeout,
                        float(getattr(manager.config, "scene_search_round_timeout", _STOCK_SCENE_SEARCH_ROUND_TIMEOUT)),
                    ),
                ):
                    source, query = future_map[future]
                    try:
                        results = future.result(timeout=0)
                        if results is None:
                            continue
                        print(f"   [ok] {source}: {len(results)} results for {query[:30]}...")
                    except Exception as exc:
                        print(f"   [ERR] Stock search failed ({source} / {query[:40]}): {_short_err(exc)}")
                        continue

                    results = results or []
                    for candidate in results[:10]:
                        if scene.get('_stock_last_resort') and not _stock_rescue_metadata_gate(candidate, scene)[0]:
                            # Empty/irrelevant video results must leave room
                            # for the image query wave, not suppress it merely
                            # because an API returned a nonempty page.
                            continue
                        key = _stock_candidate_asset_key(candidate) or _media_source_key(candidate) or candidate.download_url or candidate.preview_url or candidate.id
                        if not key or key in seen_keys:
                            continue
                        if per_source_counts.get(source, 0) >= max_results_per_source:
                            continue
                        seen_keys.add(key)
                        gathered.append(candidate)
                        per_source_counts[source] = per_source_counts.get(source, 0) + 1
                    if len(gathered) >= max_gathered_candidates:
                        break
            except FuturesTimeoutError:
                timed_out_sources = set()
                for future, (source, query) in future_map.items():
                    if not future.done():
                        future.cancel()
                        timed_out_sources.add(source)
                if timed_out_sources:
                    print(f"   [stock] scene {scene_index + 1}: search timed out for {', '.join(sorted(timed_out_sources))}")
        finally:
            if owns_executor:
                active_executor.shutdown(wait=False, cancel_futures=True)
        if gathered:
            break

    # === Last-resort generic fallback ===
    # When all query variants return 0, try a broad genre-based query
    if not gathered and not scene.get('_stock_last_resort') and _stock_remaining(manager.config, 1) > 0:
        generic_terms = []
        for field in ['subject', 'category', 'setting']:
            val = str(query_profile.get(field, '') or '').strip()
            if val and val not in generic_terms and val not in ('abstract', 'other'):
                generic_terms.append(val)
        generic_query = query_profile.get('primary_query') or query_profile.get('query') or text or title
        if generic_terms:
            generic_query = " ".join(generic_terms[:3])
        if scene.get('_stock_last_resort') and str(generic_query).strip().casefold() in {
            'people outdoor', 'people', 'outdoor', 'abstract', 'other',
        }:
            # Generic tags like "people outdoor" discard the documentary
            # subject. The bounded rescue below uses the actual scene/title.
            generic_query = ''
        if generic_query:
            print(f"   [stock] scene {scene_index + 1}: all variants empty, trying generic fallback '{generic_query}'")
        fallback_media_type = 'video' if scene.get('_stock_video_only') else ('image' if preferred_media_type == 'video' else preferred_media_type)
        for source in source_order if generic_query else ():
            if source_priorities.get(source, 0.0) <= 0:
                continue
            try:
                results = _search_stock_candidates(manager, generic_query, source, scene_context, media_type=fallback_media_type) or []
                if results:
                    print(f"   [ok] {source}: {len(results)} generic results for '{generic_query[:40]}'")
                    for candidate in results[:max_results_per_source]:
                        gate_passed, _reason = _stock_candidate_passes_accuracy_gate(candidate, manager.config, scene=scene)
                        if not gate_passed:
                            continue
                        key = _stock_candidate_asset_key(candidate) or _media_source_key(candidate) or candidate.download_url or candidate.preview_url or candidate.id
                        if key and key not in seen_keys:
                            seen_keys.add(key)
                            gathered.append(candidate)
                            break
                    if gathered:
                        break
            except Exception as exc:
                print(f"   [ERR] Stock generic fallback failed ({source}): {_short_err(exc)}")
                continue

    if not gathered and not scene.get('_stock_last_resort') and _stock_remaining(manager.config, 1) > 0:
        allow_broad_rescue = bool(scene.get('_stock_last_resort')) or str(
            os.environ.get("VIDZEO_STOCK_ALLOW_BROAD_RESCUE", "0")
        ).strip().lower() in {"1", "true", "yes", "on"}
        if not allow_broad_rescue:
            print(
                f"   [stock] scene {scene_index + 1}: no targeted candidates; "
                "broad generic stock rescue disabled for accuracy"
            )
        weak_terms = {'people outdoor', 'people', 'outdoor', 'abstract', 'other'}
        broad_queries = [] if not allow_broad_rescue else _dedupe_query_variants(
            _stock_last_resort_queries(scene, title, query_profile)
        )
        broad_queries = [
            query for query in broad_queries
            if query and query.strip().casefold() not in weak_terms
        ][:2]
        if broad_queries:
            print(
                f"   [stock] scene {scene_index + 1}: no targeted candidates; "
                f"trying anchored broad stock rescue ({len(broad_queries)} queries)"
            )
        for broad_query in broad_queries:
            for source in source_order:
                if source_priorities.get(source, 0.0) <= 0:
                    continue
                for rescue_media_type in (('video',) if scene.get('_stock_video_only') else ('video', 'image')):
                    try:
                        results = _search_stock_candidates(
                            manager,
                            broad_query,
                            source,
                            scene_context,
                            media_type=rescue_media_type,
                        ) or []
                    except Exception as exc:
                        print(f"   [ERR] Stock broad rescue failed ({source} / {broad_query[:30]}): {_short_err(exc)}")
                        continue
                    for candidate in results[:max_results_per_source]:
                        key = _stock_candidate_asset_key(candidate) or _media_source_key(candidate) or candidate.download_url or candidate.preview_url or candidate.id
                        if key and key not in seen_keys:
                            seen_keys.add(key)
                            gathered.append(candidate)
                            break
                    if gathered:
                        break
                if gathered:
                    break
            if gathered:
                break

    scored = []
    for candidate in gathered:
        candidate.score_breakdown = {
            **(candidate.score_breakdown or {}),
            "cache_scope": _stock_resolution_cache_scope(
                scene_index,
                scene,
                str((candidate.score_breakdown or {}).get("matched_query") or ""),
                candidate.source,
            ),
            "stock_policy_reason": stock_policy_reason,
        }
        media_pool = getattr(manager.config, 'media_pool', None)
        if isinstance(media_pool, StockMediaPool):
            candidate.score_breakdown['shared_download_scope'] = media_pool.scope
        scored_candidate = manager.scorer.score_item(candidate, keywords, scene_context, existing_items)
        provider_query = (scored_candidate.score_breakdown or {}).get('matched_query', '')
        alignment = _scene_query_alignment(scored_candidate, scene_context)
        visual_fit = _stock_visual_fit(scored_candidate, scene_context)
        compatibility = _stock_candidate_compatibility(scored_candidate, scene_context, preferred_media_type)
        specificity = _stock_query_specificity(str(provider_query or ''))
        duplicate_penalty = _least_duplicate_similarity(scored_candidate, existing_items) * 8.0
        recent_query_penalty = 0.0
        if provider_query and existing_items:
            recent_queries = [
                str((item.score_breakdown or {}).get('matched_query') or '').strip().lower()
                for item in existing_items[-4:]
            ]
            recent_query_penalty = recent_queries.count(str(provider_query).strip().lower()) * 2.0
        final_score = (scored_candidate.score or 0) + alignment + visual_fit + compatibility + specificity - duplicate_penalty
        final_score -= recent_query_penalty
        scored_candidate.score = min(100, max(0, final_score))
        scored_candidate.score_breakdown = {
            **(scored_candidate.score_breakdown or {}),
            'scene_alignment': alignment,
            'stock_visual_fit': visual_fit,
            'stock_compatibility': compatibility,
            'query_specificity': specificity,
            'duplicate_penalty': duplicate_penalty,
            'recent_query_penalty': recent_query_penalty,
            'stock_total': scored_candidate.score,
        }
        scored.append(scored_candidate)
    scored.sort(key=lambda item: item.score, reverse=True)
    oversized_count = sum(1 for candidate in scored if not _stock_candidate_downloadable(candidate))
    if oversized_count:
        print(
            f"   [stock] scene {scene_index + 1}: skipped {oversized_count} oversized "
            f"candidate(s) over {_STOCK_MAX_DOWNLOAD_MB}MB"
        )
    scored = [candidate for candidate in scored if _stock_candidate_downloadable(candidate)]
    gated_scored = []
    for candidate in scored:
        gate_passed, gate_reason = _stock_candidate_passes_accuracy_gate(candidate, manager.config, scene=scene)
        candidate.score_breakdown = {
            **(candidate.score_breakdown or {}),
            "stock_accuracy_gate": gate_reason,
            "stock_accuracy_gate_passed": gate_passed,
        }
        if gate_passed:
            gated_scored.append(candidate)
    scored = gated_scored
    if scored:
        top_debug = []
        for item in scored[:3]:
            breakdown = item.score_breakdown or {}
            top_debug.append(
                f"{item.source}:{item.type} score={item.score:.1f} "
                f"q='{str(breakdown.get('matched_query') or '')[:34]}' "
                f"fit={float(breakdown.get('stock_visual_fit') or 0):.1f} "
                f"dup={float(breakdown.get('duplicate_penalty') or 0):.1f}"
            )
        print(f"   [stock] scene {scene_index + 1}: top candidates -> " + " | ".join(top_debug))

    filtered_scored = [candidate for candidate in scored if not _is_recent_duplicate(candidate, existing_items)]
    selection_pool = filtered_scored or scored
    selection_pool = sorted(
        selection_pool,
        key=lambda candidate: (
            _stock_type_priority(candidate, preferred_media_type),
            -(candidate.score or 0),
        ),
    )
    unique_selection_pool = []
    selected_keys = set()
    for candidate in selection_pool:
        asset_key = _stock_candidate_asset_key(candidate) or _media_signature(candidate)
        if asset_key and asset_key in selected_keys:
            continue
        unique_selection_pool.append(candidate)
        if asset_key:
            selected_keys.add(asset_key)
    selection_pool = unique_selection_pool
    backup_limit = min(79, max(10, len(selection_pool) - 1)) if isinstance(
        getattr(manager.config, 'media_pool', None), StockMediaPool) else 10

    for candidate in selection_pool[:_MAX_STOCK_DOWNLOAD_ATTEMPTS_PER_SCENE]:
        if not download_media:
            backup_media = [alt for alt in selection_pool if alt.id != candidate.id][:backup_limit]
            return SceneMedia(
                scene_index=scene_index,
                scene_text=text,
                scene_type=scene_type,
                duration_needed=duration,
                primary_media=candidate,
                backup_media=backup_media,
                fallback_level=0 if candidate.score >= manager.config.preferred_score_threshold else 1,
                fallback_reason="" if candidate.score >= manager.config.preferred_score_threshold else "relaxed stock ranking",
                transition_in='cut' if scene_index > 0 else 'fade_in',
            )

        if manager.downloader.download_item(candidate):
            backup_media = [alt for alt in selection_pool if alt.id != candidate.id][:backup_limit]
            return SceneMedia(
                scene_index=scene_index,
                scene_text=text,
                scene_type=scene_type,
                duration_needed=duration,
                primary_media=candidate,
                backup_media=backup_media,
                fallback_level=0 if candidate.score >= manager.config.preferred_score_threshold else 1,
                fallback_reason="" if candidate.score >= manager.config.preferred_score_threshold else "relaxed stock ranking",
                transition_in='cut' if scene_index > 0 else 'fade_in',
            )

    return SceneMedia(
        scene_index=scene_index,
        scene_text=text,
        scene_type=scene_type,
        duration_needed=duration,
        primary_media=None,
        fallback_level=4,
        fallback_reason="No stock media candidate passed download/validation",
        transition_in='cut' if scene_index > 0 else 'fade_in',
    )


def _find_stock_media_for_scenes(
    manager: RealMediaManager,
    scenes: List[Dict],
    title: str,
    media_sources: List[str],
    progress_callback: Optional[Callable[[float, str], None]] = None,
    retry_rounds: Optional[int] = None,
) -> List[SceneMedia]:
    # Legacy callers do not carry a job/run ID. Give each resolver invocation
    # an isolated scope rather than allowing identical scene numbers from two
    # simultaneous jobs to share an in-flight or cached download.
    fallback_run_scope = hashlib.sha256(
        f"{os.getpid()}:{threading.get_ident()}:{time.time_ns()}".encode("utf-8")
    ).hexdigest()[:20]
    scenes = [
        {
            **dict(scene or {}),
            "_stock_run_scope": str(
                (scene or {}).get("_stock_run_scope")
                or (scene or {}).get("run_id")
                or (scene or {}).get("job_id")
                or fallback_run_scope
            ),
        }
        for scene in scenes
    ]
    print(f"\n[vid] STOCK MEDIA WORKFLOW")
    print(f"   Title: {title}")
    print(f"   Scenes: {len(scenes)}")
    source_label = ", ".join(media_sources) if media_sources else "none"
    print(f"   Sources: {source_label}")
    print("=" * 50)

    scene_media_list: List[Optional[SceneMedia]] = [None] * len(scenes)

    worker_count = max(1, min(len(scenes), _STOCK_SCENE_WORKERS))
    print(f"   [start] Resolving {len(scenes)} stock scenes in parallel ({worker_count} workers)...")
    search_worker_count = max(
        worker_count,
        min(_MAX_STOCK_SEARCH_WORKERS, worker_count * max(2, _MAX_PROVIDER_CONCURRENT_REQUESTS)),
    )
    search_executor = ThreadPoolExecutor(max_workers=search_worker_count)
    executor = ThreadPoolExecutor(max_workers=worker_count)
    early_download_executor = ThreadPoolExecutor(max_workers=manager.config.max_concurrent_downloads)
    early_downloads: Dict[str, Any] = {}

    def _download_key(item: MediaItem) -> str:
        return _media_source_key(item) or item.download_url or item.id

    def _schedule_early_download(item: Optional[MediaItem]) -> None:
        # Early downloads used to overlap search, but a slow URL remained
        # in-flight and phase 2 waited on the same request again. That turned
        # one bad stock candidate into a two-minute fallback stall. Keep the
        # overlap available as an explicit tuning switch; production defaults
        # to one bounded download attempt per selected item.
        if not _STOCK_EARLY_DOWNLOAD:
            return
        if not item or item.is_downloaded or not _stock_candidate_downloadable(item):
            return
        key = _download_key(item)
        if not key or key in early_downloads:
            return
        early_downloads[key] = early_download_executor.submit(manager.downloader.download_item, item)

    try:
        futures = {}
        for i, scene in enumerate(scenes):
            print(f"   Scene {i + 1}/{len(scenes)} search: stock ({source_label})")
            future = executor.submit(
                _resolve_stock_scene_media,
                manager,
                i,
                scene,
                [],
                title,
                media_sources,
                False,
                search_executor,
            )
            futures[future] = i

        scene_resolve_max_wait = max(
            45.0,
            float(getattr(manager.config, "batch_resolve_timeout", _STOCK_BATCH_RESOLVE_TIMEOUT) or _STOCK_BATCH_RESOLVE_TIMEOUT),
        )
        scene_resolve_max_wait = _stock_remaining(manager.config, scene_resolve_max_wait)
        try:
            stock_resolve_started = time.monotonic()
            stock_checked_count = 0
            stock_last_progress = stock_resolve_started
            for future in as_completed(futures, timeout=scene_resolve_max_wait):
                scene_index = futures[future]
                try:
                    scene_media = future.result(timeout=0)
                    scene_media_list[scene_index] = scene_media
                    chosen = scene_media.primary_media if scene_media else None
                    if chosen:
                        print(
                            f"   [stock] scene {scene_index + 1}/{len(scenes)} resolved: "
                            f"{chosen.source} {chosen.type}"
                        )
                        _schedule_early_download(chosen)
                    else:
                        print(f"   [stock] scene {scene_index + 1}/{len(scenes)} unresolved - will retry/rescue later")
                except Exception as exc:
                    print(f"   [ERR] Scene {scene_index + 1} resolution failed: {exc}")
                    scene_media_list[scene_index] = SceneMedia(
                        scene_index=scene_index,
                        scene_text=scenes[scene_index].get("text", ""),
                        scene_type=scenes[scene_index].get("scene_type", "generic"),
                        duration_needed=scenes[scene_index].get("duration", 10),
                        primary_media=None,
                        fallback_level=4,
                        fallback_reason=str(exc),
                    )
                stock_checked_count += 1
                now = time.monotonic()
                if (now - stock_last_progress) >= 5.0 or stock_checked_count == len(scenes):
                    ready_count = sum(1 for sm in scene_media_list if sm and sm.primary_media)
                    print(
                        f"   [stock] phase 1 search: {stock_checked_count}/{len(scenes)} scenes checked, "
                        f"{ready_count} candidates ready, {len(scenes) - stock_checked_count} pending, "
                        f"{now - stock_resolve_started:.1f}s elapsed"
                    )
                    stock_last_progress = now
                    if callable(progress_callback):
                        try:
                            progress_callback(
                                min(0.45, 0.45 * stock_checked_count / max(1, len(scenes))),
                                f"Stock footage {stock_checked_count}/{len(scenes)} checked; {ready_count} ready",
                            )
                        except Exception:
                            pass
        except FuturesTimeoutError:
            for future, scene_index in futures.items():
                if not future.done():
                    future.cancel()
                    print(f"   [ERR] Scene {scene_index + 1} resolution timed out")
            print(f"   [ERR] Stock resolution total timeout ({scene_resolve_max_wait}s) - moving to partial assembly")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        search_executor.shutdown(wait=False, cancel_futures=True)

    resolved_scene_media_list: List[SceneMedia] = []
    selected_items: List[MediaItem] = []
    for scene_media in scene_media_list:
        if not scene_media:
            resolved_scene_media_list.append(SceneMedia(
                scene_index=len(resolved_scene_media_list),
                scene_text="",
                scene_type="generic",
                duration_needed=10,
                primary_media=None,
                fallback_level=4,
                fallback_reason="timed out",
            ))
            continue

        media_pool = getattr(manager.config, 'media_pool', None)
        if isinstance(media_pool, StockMediaPool):
            chosen = media_pool.choose([scene_media.primary_media] + list(scene_media.backup_media or []),
                                       duration_needed=scene_media.duration_needed)
        else:
            chosen = _choose_scene_candidate(scene_media, selected_items)
        if chosen is None:
            chosen = scene_media.primary_media

        scene_media.primary_media = chosen
        if chosen:
            selected_items.append(chosen)
        resolved_scene_media_list.append(scene_media)

    to_download = [
        sm.primary_media
        for sm in resolved_scene_media_list
        if sm.primary_media and not sm.primary_media.is_downloaded
    ]
    if to_download and early_downloads:
        still_pending: List[MediaItem] = []
        early_checked = 0
        early_started = time.monotonic()
        for item in to_download:
            future = early_downloads.get(_download_key(item))
            if not future:
                still_pending.append(item)
                continue
            try:
                future.result(timeout=max(2.0, min(30.0, float(manager.config.download_timeout or 30.0))))
            except Exception as exc:
                print(f"   [stock] early download failed ({item.id}): {exc}")
            if not item.is_downloaded:
                still_pending.append(item)
            early_checked += 1
            if early_checked == len(to_download) or early_checked % 4 == 0:
                ready_early = len(to_download) - len(still_pending)
                print(
                    f"   [stock] phase 2 early downloads: {early_checked}/{len(to_download)} checked, "
                    f"{ready_early} ready, {time.monotonic() - early_started:.1f}s elapsed"
                )
        to_download = still_pending
    if to_download:
        print(f"\nDownloading {len(to_download)} stock media item(s) in parallel...")
        if callable(progress_callback):
            try:
                progress_callback(0.50, f"Stock footage downloading {len(to_download)} assets")
            except Exception:
                pass
        manager.downloader.download_batch(to_download)
        # A provider can return a valid search result and still reject the
        # binary request later (Pixabay 429/403 is common during bursts).
        # Do not terminalize that scene after the first URL.  Each resolver
        # keeps a distinct backup pool; retry the next candidates in bounded
        # parallel rounds so another provider/URL can satisfy the contract.
        retry_round = 0
        try:
            max_retry_rounds = max(
                0,
                min(2, int(retry_rounds if retry_rounds is not None else 2)),
            )
        except (TypeError, ValueError):
            max_retry_rounds = 2
        attempted_backup_keys: set[str] = set()
        media_pool = getattr(manager.config, 'media_pool', None)
        if isinstance(media_pool, StockMediaPool):
            for scene_media in resolved_scene_media_list:
                if scene_media and scene_media.primary_media and not scene_media.primary_media.is_downloaded:
                    media_pool.release(scene_media.primary_media)
        while retry_round < max_retry_rounds:
            failed_media = [
                sm for sm in resolved_scene_media_list
                if sm and sm.primary_media and not sm.primary_media.is_downloaded
            ]
            if not failed_media:
                break
            retry_items: list[tuple[SceneMedia, MediaItem]] = []
            for scene_media in failed_media:
                primary = scene_media.primary_media
                candidates = list(scene_media.backup_media or [])
                selected_backup = None
                media_pool = getattr(manager.config, 'media_pool', None)
                if isinstance(media_pool, StockMediaPool):
                    candidates = [item for item in candidates if _download_key(item) not in attempted_backup_keys
                                  and (not primary or _download_key(item) != _download_key(primary))
                                  and _stock_candidate_downloadable(item)]
                    selected_backup = media_pool.choose(candidates,duration_needed=scene_media.duration_needed)
                    if selected_backup is not None:
                        attempted_backup_keys.add(_download_key(selected_backup))
                    candidates = []
                for candidate in candidates:
                    key = _download_key(candidate)
                    if not key or key in attempted_backup_keys:
                        continue
                    if not _stock_candidate_downloadable(candidate):
                        attempted_backup_keys.add(key)
                        continue
                    if primary and key == _download_key(primary):
                        attempted_backup_keys.add(key)
                        continue
                    selected_backup = candidate
                    attempted_backup_keys.add(key)
                    break
                if selected_backup is not None:
                    retry_items.append((scene_media, selected_backup))
            if not retry_items:
                break
            retry_round += 1
            print(
                f"   [stock] retry round {retry_round}: "
                f"trying {len(retry_items)} alternate candidate(s) after download failures"
            )
            manager.downloader.download_batch([candidate for _sm, candidate in retry_items])
            for scene_media, candidate in retry_items:
                if candidate.is_downloaded:
                    scene_media.primary_media = candidate
                    print(
                        f"   [stock] alternate recovered scene {scene_media.scene_index + 1}: "
                        f"{candidate.source} {candidate.type}"
                    )
                else:
                    pool = getattr(manager.config, 'media_pool', None)
                    if isinstance(pool, StockMediaPool):
                        pool.release(candidate)
            if callable(progress_callback):
                try:
                    ready_retry = sum(
                        1 for sm in resolved_scene_media_list
                        if sm and sm.primary_media and sm.primary_media.is_downloaded
                    )
                    progress_callback(
                        0.50 + min(0.43, 0.43 * ready_retry / max(1, len(scenes))),
                        f"Stock footage {ready_retry}/{len(scenes)} assets ready",
                    )
                except Exception:
                    pass
    early_download_executor.shutdown(wait=False, cancel_futures=True)

    ready_count = sum(1 for sm in resolved_scene_media_list if sm.is_ready())
    print(f"\nSTOCK MEDIA RESULT: {ready_count}/{len(scenes)} scenes")
    if callable(progress_callback):
        try:
            progress_callback(1.0, f"Stock footage {ready_count}/{len(scenes)} ready")
        except Exception:
            pass
    return resolved_scene_media_list


def _extract_keywords_from_prompt(prompt: str) -> List[str]:
    """
    Extract SEARCHABLE keywords from a scene prompt.
    
    For Wikimedia, we need NOUNS and ENTITIES (people, places, things).
    Style words like 'cinematic', 'dramatic', 'realistic' are useless for search.
    
    Example:
        Input: "Cinematic Thriller, David Fincher style - A dimly lit ancient Roman Colosseum at sunset"
        Output: ['colosseum', 'roman', 'ancient', 'sunset', 'rome']
    """
    # STEP 1: Extract capitalized phrases (likely named entities - highest priority)
    entities = re.findall(r'\b([A-Z][a-zA-Z]+(?:\s+[A-Za-z]+)*)\b', prompt)
    
    # Common capitalized words that aren't useful entities
    skip_entities = {
        'The', 'A', 'An', 'This', 'That', 'These', 'Those', 'In', 'On', 'At',
        'To', 'For', 'From', 'With', 'By', 'As', 'Is', 'Are', 'Was', 'Were',
        'Cinematic', 'Thriller', 'Style', 'Scene', 'Image', 'Photo', 'Picture',
        'Frame', 'Shot', 'View', 'David', 'Fincher', 'Christopher', 'Nolan',
        'Dramatic', 'Professional', 'Realistic', 'Modern', 'Classic'
    }
    
    # STEP 2: Filter stop words and style modifiers
    style_stopwords = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'from', 'as', 'is', 'are', 'was', 'were', 'be',
        'this', 'that', 'these', 'those', 'it', 'its', 'they', 'their',
        # Action/style words (useless for media search)
        'show', 'showing', 'depicting', 'display', 'displaying', 'image',
        'photo', 'photograph', 'picture', 'scene', 'video', 'clip', 'frame',
        'create', 'creating', 'visualize', 'visualizing', 'illustrate',
        # Style descriptors (useless for media search)
        'style', 'realistic', 'cinematic', 'dramatic', 'professional',
        'beautiful', 'stunning', 'amazing', 'epic', 'intense', 'sleek',
        'modern', 'classic', 'vintage', 'retro', 'high', 'quality',
        'detailed', 'dimly', 'lit', 'bright', 'dark', 'soft', 'hard',
        'thriller', 'horror', 'comedy', 'action', 'adventure',
        'atmosphere', 'atmospheric', 'moody', 'vibrant', 'dynamic',
    }
    
    keywords = []
    
    # Add valid entities first (highest priority)
    for entity in entities:
        if entity not in skip_entities and len(entity) > 2:
            keywords.append(entity.lower())
    
    # STEP 3: Extract nouns - words that tend to be subjects/objects
    # Common noun patterns (things you can photograph)
    noun_indicators = {
        'person', 'people', 'man', 'woman', 'child', 'soldier', 'warrior', 'king', 'queen',
        'city', 'town', 'village', 'building', 'house', 'castle', 'palace', 'temple', 'church',
        'mountain', 'river', 'ocean', 'sea', 'forest', 'desert', 'valley', 'island',
        'car', 'ship', 'plane', 'train', 'horse', 'animal', 'dog', 'cat', 'lion', 'eagle',
        'war', 'battle', 'army', 'weapon', 'sword', 'shield', 'armor',
        'sun', 'moon', 'star', 'sky', 'cloud', 'rain', 'snow', 'storm',
        'fire', 'water', 'earth', 'stone', 'gold', 'silver', 'iron', 'steel',
        'computer', 'robot', 'machine', 'technology', 'science', 'laboratory',
        'food', 'fruit', 'tree', 'flower', 'plant', 'garden', 'farm',
    }
    
    # STEP 4: Extract all remaining meaningful words
    words = re.findall(r'\b[a-zA-Z][a-zA-Z0-9-]*\b', prompt.lower())
    
    for word in words:
        if word in style_stopwords or len(word) <= 2:
            continue
        if word not in keywords:  # Avoid duplicates
            # Prioritize words that look like nouns
            if word in noun_indicators or word.endswith(('tion', 'sion', 'ment', 'ness', 'ity')):
                keywords.insert(len([k for k in keywords if k in entities]) + 1, word)
            else:
                keywords.append(word)
    
    # STEP 5: Remove duplicates while preserving order
    seen = set()
    unique = []
    for kw in keywords:
        kw_lower = kw.lower()
        if kw_lower not in seen:
            seen.add(kw_lower)
            unique.append(kw_lower)
    
    # Return top 8 keywords (more focused for search)
    return unique[:8]


def _detect_scene_type(prompt: str) -> str:
    """Detect scene type from prompt content"""
    prompt_lower = prompt.lower()
    
    type_patterns = {
        'interview': ['interview', 'conversation', 'discussion', 'talking', 'speaking'],
        'historical': ['history', 'historical', 'ancient', 'century', 'war', 'battle', 'empire'],
        'news': ['news', 'breaking', 'report', 'announcement', 'update'],
        'nature': ['nature', 'wildlife', 'animal', 'forest', 'ocean', 'mountain', 'landscape'],
        'technology': ['technology', 'tech', 'computer', 'digital', 'software', 'ai', 'robot'],
        'business': ['business', 'office', 'corporate', 'meeting', 'professional'],
        'sports': ['sport', 'game', 'match', 'player', 'team', 'athlete'],
        'science': ['science', 'scientific', 'research', 'laboratory', 'experiment'],
    }
    
    for scene_type, patterns in type_patterns.items():
        for pattern in patterns:
            if pattern in prompt_lower:
                return scene_type
    
    return 'generic'


def _scrape_stock_image(query: str, fallback_providers: bool = True) -> Optional[Dict]:
    """Try Pexels and Pixabay HTML scrapers for a stock image."""
    from .sources.pexels_source import PexelsSource
    from .sources.pixabay_source import PixabaySource

    for source_cls, name in [(PexelsSource, 'pexels'), (PixabaySource, 'pixabay')]:
        try:
            source = source_cls()
            results = source.search(query, limit=5)
            if results:
                r = results[0]
                return {
                    'download_url': r.download_url,
                    'source': name,
                    'id': r.id,
                    'title': r.title,
                }
        except Exception:
            continue
    return None


def _download_stock_fallback(image: Dict, query: str) -> Optional[str]:
    """Download a scraper-found stock image to temp cache."""
    url = image.get('download_url', '')
    source = image.get('source', 'stock')
    img_id = image.get('id', 'unknown')
    if not url:
        return None
    try:
        ext = os.path.splitext(url.split('?')[0])[1] or '.jpg'
        cache_dir = Path(TEMP_DIR) / "stock_fallback"
        cache_dir.mkdir(parents=True, exist_ok=True)
        local_path = str(cache_dir / f"{source}_{img_id}{ext}")
        if os.path.exists(local_path):
            return local_path
        import requests as req
        r = req.get(url, timeout=15, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        })
        if r.status_code == 200:
            with open(local_path, 'wb') as f:
                f.write(r.content)
            return local_path
    except Exception:
        pass
    return None


# =============================================================================
# MAIN (TEST)
# =============================================================================

if __name__ == "__main__":
    # Test with sample scenes
    manager = create_media_manager(
        pexels_key=os.environ.get('PEXELS_API_KEY', ''),
        pixabay_key=os.environ.get('PIXABAY_API_KEY', '')
    )
    
    test_scenes = [
        {
            'text': 'The history of ancient Rome',
            'keywords': ['ancient', 'Rome', 'history', 'Colosseum'],
            'duration': 10,
            'scene_type': 'historical'
        },
        {
            'text': 'Modern technology innovations',
            'keywords': ['technology', 'innovation', 'computer', 'AI'],
            'duration': 8,
            'scene_type': 'technology'
        },
    ]
    
    results = manager.find_media_for_scenes(test_scenes, "Test Video")
    
    for sm in results:
        status = "[ok]" if sm.is_ready() else "✗"
        print(f"\n{status} Scene {sm.scene_index + 1}: {sm.scene_type}")
        if sm.primary_media:
            print(f"   Media: {sm.primary_media.title[:40]}")
            print(f"   Score: {sm.primary_media.score:.1f}")
            print(f"   Source: {sm.primary_media.source}")
