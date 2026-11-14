"""
Flickr REST API Image Source.

Uses Flickr's free public API (flickr.photos.search) to return structured
image results with direct download URLs.

URL format: https://live.staticflickr.com/{server}/{id}_{secret}_b.jpg
  - _b suffix = 1024px (largest freely accessible size)
  - _c suffix = 800px
  - _z suffix = 640px

API Key: Free to obtain at https://www.flickr.com/services/api/keys/
Falls back gracefully if no key is available.
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
import time
import urllib.parse
import random
from typing import Dict, List, Optional

import requests

from .runtime import _web_log
from .safety import candidate_is_safe

# ── Public app key (free, low rate-limit tier) ───────────────────────────────
# Users can override via FLICKR_API_KEY env var for higher limits.
_BUNDLED_APP_KEY = "3e7cc266ae2b0e0d78e279ce8e361736"   # public demo key
_FLICKR_REST_URL = "https://www.flickr.com/services/rest/"

# Free-tier Flickr keys trip "Over Rate Limit" under burst load (6 parallel
# scene workers × several queries each).  Serialize calls behind a global
# limiter so bursts become a smooth, safe stream instead of 429 storms.
_flickr_rate_lock = threading.Lock()
_flickr_last_call = 0.0


def _flickr_min_interval() -> float:
    try:
        return max(0.02, min(5.0, float(os.environ.get("VIDZEO_FLICKR_MIN_INTERVAL", "0.18") or 0.18)))
    except Exception:
        return 0.18


def _pace_flickr_call() -> None:
    global _flickr_last_call
    interval = _flickr_min_interval()
    with _flickr_rate_lock:
        elapsed = time.monotonic() - _flickr_last_call
        if elapsed < interval:
            time.sleep(interval - elapsed)
        _flickr_last_call = time.monotonic()


def _web_verbose() -> bool:
    return str(os.environ.get("VIDZEO_WEB_VERBOSE", "0")).strip().lower() in {"1", "true", "yes", "on"}

# Extras to request from Flickr API
_EXTRAS = "url_b,url_c,url_z,url_m,title,tags,description,views,date_taken,owner_name"

# Size preference order (largest → smallest)
_URL_SIZE_ORDER = ["url_b", "url_c", "url_z", "url_m"]

# Hard-reject Flickr tags that indicate non-photographic content
_BAD_FLICKR_TAGS = frozenset({
    "illustration", "digitalart", "drawing", "painting", "artwork",
    "cartoon", "comic", "anime", "manga", "vector", "clipart",
    "screenshot", "render", "3drender", "cgi", "nsfw", "adult", "porn",
    "porno", "xxx", "nude", "nudity", "naked", "topless", "erotic",
    "erotica", "fetish", "sex", "sexual", "lingerie", "thong", "boudoir",
    "bikini", "seductive", "sexy", "gore", "bloody", "meme", "fanart",
})

_FLICKR_GENERIC_QUERY_TERMS = {
    "photo", "photos", "image", "images", "picture", "pictures", "archive",
    "archival", "historical", "historic", "documentary", "old", "photograph",
}

_flickr_session: Optional[requests.Session] = None

# Connection-level failure flags for the resolver's per-job circuit breaker.
# ``search_flickr_api`` swallows exceptions and returns [] for backward
# compatibility, so the resolver must be able to distinguish a dead network
# from a legitimate zero-result query.
_last_search_network_failed: bool = False
_last_search_network_error: str = ""


def flickr_last_search_network_failure() -> Tuple[bool, str]:
    """Return (failed, error) from the most recent ``search_flickr_api`` call."""
    return _last_search_network_failed, _last_search_network_error


def _get_session() -> requests.Session:
    global _flickr_session
    if _flickr_session is None:
        _flickr_session = requests.Session()
        _flickr_session.headers.update({
            "User-Agent": "VidzeoLocal/1.0 (vidzeo.app; image pipeline)",
            "Accept": "application/json",
        })
    return _flickr_session


def _resolve_api_key() -> str:
    """Return API key from env or bundled fallback."""
    return (os.environ.get("FLICKR_API_KEY") or "").strip() or _BUNDLED_APP_KEY


def _retry_count() -> int:
    try:
        return max(1, min(4, int(os.environ.get("VIDZEO_NET_RETRIES", "3") or 3)))
    except Exception:
        return 3


def _retry_delay(attempt: int) -> float:
    try:
        base = max(0.1, min(3.0, float(os.environ.get("VIDZEO_NET_RETRY_BASE", "0.6") or 0.6)))
    except Exception:
        base = 0.6
    return min(6.0, base * (2 ** attempt)) + random.uniform(0.0, 0.25)


def _build_direct_url(photo: Dict) -> str:
    """Extract largest available direct image URL from a Flickr photo dict."""
    for size_key in _URL_SIZE_ORDER:
        url = photo.get(size_key, "")
        if url and url.startswith("http"):
            return url
    # Fallback: construct URL manually from id/server/secret
    photo_id = photo.get("id", "")
    server = photo.get("server", "")
    secret = photo.get("secret", "")
    if photo_id and server and secret:
        return f"https://live.staticflickr.com/{server}/{photo_id}_{secret}_b.jpg"
    return ""


_MUSIC_STAGE_TAGS = frozenset({
    "concert", "concerts", "livemusic", "gig", "gigs", "tour", "musicfestival",
    "band", "bands", "leadguitar", "vocalist", "guitarist", "drummer", "bassist",
    "singer", "singing", "onstage", "performance", "frontman", "backstage",
    "muchmusic", "canadaday", "rockband", "popband", "punkrock", "brampton", "ontario",
})

_MUSIC_QUERY_KEYWORDS = frozenset({
    "music", "band", "bands", "concert", "singer", "singers", "song", "songs",
    "album", "musician", "musicians", "orchestra", "guitar", "drums",
})

_NATURE_GEO_TERMS = frozenset({
    "trench", "ocean", "sea", "hadal", "abyss", "abyssal", "deepsea", "underwater",
    "geology", "volcano", "hydrothermal", "chimney", "seafloor", "subduction",
    "challenger", "tectonic", "marine", "reef", "coral", "glacier", "mountain",
    "canyon", "desert", "jungle", "forest", "river", "island", "depth", "depths",
})


def _tags_bad(tags_str: str, query: str = "") -> bool:
    """Return True if any bad tag signals non-photographic or irrelevant noise content."""
    tags_lower = set(re.findall(r"[a-z0-9]+", (tags_str or "").lower()))
    if bool(_BAD_FLICKR_TAGS & tags_lower):
        return True

    # Filter music/concert tags if the query is not asking for music/bands
    query_words = set(re.findall(r"[a-z0-9]+", (query or "").lower()))
    if not (query_words & _MUSIC_QUERY_KEYWORDS):
        if bool(_MUSIC_STAGE_TAGS & tags_lower):
            return True
    return False


def _flickr_metadata_relevant(query: str, title: str, tags: str, description: str = "") -> bool:
    """Reject Flickr results whose metadata has no connection to the query or matches irrelevant niche."""
    terms = [
        term for term in re.findall(r"[a-z0-9]+", (query or "").lower())
        if len(term) >= 3 and term not in _FLICKR_GENERIC_QUERY_TERMS
    ]
    if not terms:
        return True
    blob = " ".join((title or "", tags or "", description or "")).lower()
    query_lower = (query or "").lower()
    query_words = set(re.findall(r"[a-z0-9]+", query_lower))
    tags_lower = set(re.findall(r"[a-z0-9]+", (tags or "").lower()))

    # Disambiguation for geographic features that collide with bands/brands:
    if "trench" in query_lower and not (query_words & _MUSIC_QUERY_KEYWORDS):

        if "marianastrench" in tags_lower or "marianas trench" in blob:
            marine_context = bool(
                {"ocean", "sea", "underwater", "deepsea", "hadal", "abyss", "challenger", "pacific", "water", "marine", "submersible", "bathyscaphe", "depth", "deep"}
                & (tags_lower | query_words | set(re.findall(r"[a-z0-9]+", blob)))
            )
            # If the only match is the band name without marine context, reject
            blob_without_title = " ".join((tags or "", description or "")).lower()
            if not any(k in blob_without_title for k in ("ocean", "sea", "underwater", "deepsea", "hadal", "abyss", "challenger", "pacific", "marine", "submersible", "bathyscaphe")):
                return False


    return any(re.search(rf"\b{re.escape(term)}\b", blob) for term in terms)


# ── No-key HTML fallback (S7) ─────────────────────────────────────────────
# The REST API above needs a valid key. The bundled demo key is dead
# (Flickr returns "100: Invalid API Key (Key not found)", verified live
# 2026-11-14), and no FLICKR_API_KEY is configured on this VM, so the
# `flickr_api` fast source returned zero rows. Flickr's own search page
# (https://www.flickr.com/search/?text=...) is server-rendered and needs
# no key: each photo is a modelExport object with ownerNsid, title,
# description and a sizes map whose _b entry is the 1024px image at
# live.staticflickr.com. If a valid key is ever configured via
# FLICKR_API_KEY, the API path above is used and this fallback never runs.
# (Flickr also offers a tag-based public feed, used by the universal
# scraper's FlickrEngine; this HTML path is the full-text search, so it
# matches the API's relevance instead of only recent tag matches.)
_FLICKR_SEARCH_URL = "https://www.flickr.com/search/"
_FLICKR_HTML_SIZE_ORDER = ("_b", "_c", "_z", "_m", "_w", "_n", "_q", "_s", "")


def _json_unescape(raw: str) -> str:
    """Unescape one JSON string body (no surrounding quotes)."""
    try:
        return json.loads('"' + raw + '"')
    except Exception:
        return raw.replace('\\"', '"').replace("\\\\", "\\")


def _parse_flickr_html(html_text: str, limit: int) -> List[Dict]:
    """Parse Flickr's server-rendered search page into API-shaped rows.

    Splits the modelExport JSON into per-photo slices at each
    ``"ownerNsid"`` (the first field of a photo object), so a slice is
    exactly one photo and titles/URLs cannot leak across photos.
    """
    text = (html_text or "").replace("\\/", "/")
    starts = [m.start() for m in re.finditer(r'"ownerNsid":"', text)]
    if not starts:
        return []
    rows: List[Dict] = []
    seen = set()
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else min(len(text), start + 20000)
        seg = text[start:end]
        title_m = re.search(r'"title":"((?:[^"\\]|\\.)*)"', seg)
        title = _json_unescape(title_m.group(1)) if title_m else ""
        desc_m = re.search(r'"description":"((?:[^"\\]|\\.)*)"', seg)
        description = _json_unescape(desc_m.group(1)) if desc_m else ""
        owner_m = re.search(r'"ownerNsid":"([^"]+)"', seg)
        # pathAlias (the username slug) sits just before ownerNsid, so it
        # is outside this slice; look back a short window for it.
        alias_m = re.search(
            r'"pathAlias":"((?:[^"\\]|\\.)*)"', text[max(0, start - 500):start]
        )
        owner = (
            _json_unescape(alias_m.group(1))
            if alias_m
            else (owner_m.group(1) if owner_m else "unknown")
        )
        image_url = ""
        thumb_url = ""
        for suffix in _FLICKR_HTML_SIZE_ORDER:
            m = re.search(
                r'live\.staticflickr\.com/\d+/\d+_[a-z0-9]+' + re.escape(suffix) + r'\.jpg',
                seg,
            )
            if m:
                image_url = "https://" + m.group(0)
                break
        if not image_url:
            continue
        id_m = re.search(r'live\.staticflickr\.com/\d+/(\d+)_[a-z0-9]+', seg)
        photo_id = id_m.group(1) if id_m else image_url
        if photo_id in seen:
            continue
        seen.add(photo_id)
        thumb_m = re.search(r'live\.staticflickr\.com/\d+/\d+_[a-z0-9]+_m\.jpg', seg)
        thumb_url = "https://" + thumb_m.group(0) if thumb_m else image_url
        source_url = f"https://www.flickr.com/photos/{owner}/{photo_id}/"
        rows.append({
            "title": title,
            "image_url": image_url,
            "url": image_url,
            "source_url": source_url,
            "page_url": source_url,
            "thumbnail_url": thumb_url,
            "preview_url": thumb_url,
            "domain": "flickr.com",
            "source": "flickr_api",
            "provider": "flickr",
            "resolver_lane": "flickr-html",
            "tags": "",
            "views": 0,
            "owner": owner,
            "description": description[:400],
        })
        if len(rows) >= limit:
            break
    return rows


def _search_flickr_html(query: str, limit: int = 20, min_views: int = 0) -> List[Dict]:
    """Full-text Flickr search via the public search page (no API key)."""
    q = re.sub(r"\s+", " ", (query or "").strip())
    if not q:
        return []
    session = _get_session()
    data = None
    last_error = ""
    for attempt in range(_retry_count()):
        try:
            _pace_flickr_call()
            resp = session.get(
                _FLICKR_SEARCH_URL,
                params={"text": q},
                headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.text
                break
            last_error = f"HTTP {resp.status_code}"
            if resp.status_code not in {408, 425, 429, 500, 502, 503, 504}:
                break
        except Exception as exc:
            last_error = type(exc).__name__
        if attempt < _retry_count() - 1:
            time.sleep(_retry_delay(attempt))
    if data is None:
        _web_log(f"flickr-html: request failed for '{q[:40]}': {last_error or 'unknown'}")
        return []
    results = []
    for cand in _parse_flickr_html(data, limit * 2):
        if len(results) >= limit:
            break
        if _tags_bad(cand.get("tags", ""), q) or not _flickr_metadata_relevant(
            q, cand.get("title", ""), cand.get("tags", ""), cand.get("description", "")
        ):
            continue
        if min_views > 0 and int(cand.get("views", 0) or 0) < min_views:
            continue
        safe, _reason = candidate_is_safe(cand)
        if not safe:
            continue
        results.append(cand)
    if _web_verbose():
        _web_log(f"flickr-html: {len(results)} usable results for '{q[:40]}'")
    return results


def search_flickr_api(
    query: str,
    limit: int = 20,
    min_views: int = 0,
    sort: str = "relevance",
) -> List[Dict]:
    """
    Search Flickr via REST API and return candidate dicts.

    Each dict contains:
      image_url, source_url, title, domain, resolver_lane, tags, views
    """
    q = re.sub(r"\s+", " ", (query or "").strip())
    if not q or len(q) < 2:
        return []
    global _last_search_network_failed, _last_search_network_error
    _last_search_network_failed = False
    _last_search_network_error = ""

    api_key = _resolve_api_key()
    session = _get_session()

    params = {
        "method": "flickr.photos.search",
        "api_key": api_key,
        "text": q,
        "sort": sort,           # relevance | date-posted-desc | interestingness-desc
        "per_page": min(limit * 2, 100),
        "page": 1,
        "extras": _EXTRAS,
        "format": "json",
        "nojsoncallback": 1,
        "safe_search": 1,       # safe only
        "content_type": 1,      # photos only (no screenshots/illustrations)
        "media": "photos",
    }

    data = None
    last_error = ""
    network_failed = False
    last_status = None
    for attempt in range(_retry_count()):
        try:
            _pace_flickr_call()
            resp = session.get(_FLICKR_REST_URL, params=params, timeout=10)
            last_status = resp.status_code
            if resp.status_code == 200:
                data = resp.json()
                if data.get("stat") not in (None, "ok") or data.get("photos") is None:
                    code = data.get("code")
                    message = str(data.get("message") or "flickr stat fail")
                    last_error = f"flickr {code}: {message}"
                    if code in {100, 105, 429} or "rate" in message.lower():
                        network_failed = True
                    data = None
                    if last_status not in {408, 425, 429, 500, 502, 503, 504}:
                        last_status = 429
                else:
                    break
            else:
                last_error = f"HTTP {resp.status_code}"
                if resp.status_code not in {408, 425, 429, 500, 502, 503, 504}:
                    break
        except Exception as exc:
            last_error = type(exc).__name__
            network_failed = True
        if attempt < _retry_count() - 1:
            if _web_verbose():
                _web_log(f"flickr-api: retrying '{q[:40]}' after {last_error}")
            time.sleep(_retry_delay(attempt))
    if data is None:
        # API failed (dead bundled key, rate limit, or network). Fall back
        # to the no-key HTML search page before giving up, so the flickr
        # lane still returns full-text results without a configured key.
        html_rows = _search_flickr_html(q, limit=limit, min_views=min_views)
        if html_rows:
            _last_search_network_failed = False
            _last_search_network_error = ""
            return html_rows
        if last_status in {408, 425, 429, 500, 502, 503, 504}:
            network_failed = True
        _last_search_network_failed = network_failed
        _last_search_network_error = last_error or "unknown"
        _web_log(f"flickr-api: request failed for '{q[:40]}': {last_error or 'unknown'}")
        return []

    photos = (data.get("photos") or {}).get("photo") or []
    if not photos:
        if _web_verbose():
            _web_log(f"flickr-api: 0 results for '{q[:40]}'")
        return []

    results = []
    for photo in photos:
        if len(results) >= limit:
            break

        # Skip entries with bad tags or irrelevant noise
        tags = str(photo.get("tags", "") or "")
        desc_val = photo.get("description", "")
        if isinstance(desc_val, dict):
            description = str(desc_val.get("_content", "") or "")
        else:
            description = str(desc_val or "")
        if _tags_bad(tags, q) or not _flickr_metadata_relevant(q, str(photo.get("title", "") or ""), tags, description):
            continue

        image_url = _build_direct_url(photo)
        if not image_url:
            continue

        title = str(photo.get("title", "") or "").strip()
        owner = str(photo.get("ownername", "") or "").strip()
        views = int(photo.get("views", 0) or 0)
        photo_id = photo.get("id", "")

        source_url = (
            f"https://www.flickr.com/photos/{photo.get('owner', 'unknown')}/{photo_id}/"
        )

        # Skip very low-view photos if threshold set (0 = no filter)
        if min_views > 0 and views < min_views:
            continue

        candidate = {
            "title": title or f"Flickr photo by {owner}",
            "image_url": image_url,
            "url": image_url,
            "source_url": source_url,
            "page_url": source_url,
            "thumbnail_url": photo.get("url_m", ""),
            "preview_url": photo.get("url_m", ""),
            "domain": "flickr.com",
            "source": "flickr_api",
            "provider": "flickr",
            "resolver_lane": "flickr-api",
            "tags": tags,
            "views": views,
            "owner": owner,
            "description": description,
        }
        safe, _reason = candidate_is_safe(candidate)
        if not safe:
            continue
        results.append(candidate)

    if _web_verbose():
        _web_log(f"flickr-api: {len(results)}/{len(photos)} usable results for '{q[:40]}'")
    return results


def search_flickr_api_multi(
    queries: List[str],
    limit_per_query: int = 15,
    sort: str = "relevance",
) -> Dict[str, List[Dict]]:
    """Search multiple queries via Flickr API concurrently using ThreadPoolExecutor."""
    from concurrent.futures import ThreadPoolExecutor
    from ..job_context import JobThreadPoolExecutor as ThreadPoolExecutor
    out: Dict[str, List[Dict]] = {}

    unique_queries = list(dict.fromkeys(q for q in queries if q and len(q.strip()) >= 2))
    if not unique_queries:
        return out

    max_workers = min(10, len(unique_queries))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_query = {
            executor.submit(search_flickr_api, q, limit=limit_per_query, sort=sort): q
            for q in unique_queries
        }
        for future in future_to_query:
            q = future_to_query[future]
            try:
                out[q] = future.result()
            except Exception:
                out[q] = []

    return {q: out.get(q, []) for q in queries}
