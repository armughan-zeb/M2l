"""
Hybrid Search + Download Engine.
URL Discovery: synchronous requests (bypasses Bing bot-detection reliably)
Image Download: aiohttp (parallel, fast, 50 concurrent workers)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import time
import threading
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from html import unescape as html_unescape
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp
import requests
from requests.adapters import HTTPAdapter


class DownloadLimiter:
    """One budget across scene threads and their independent asyncio loops."""

    def __init__(self, total=12, per_host=3):
        self.total = total
        self.per_host = per_host
        self.active = 0
        self.hosts = {}
        self.cooldowns = {}
        self.lock = threading.Lock()

    def defer(self, host, seconds=30):
        with self.lock:
            self.cooldowns[host] = time.monotonic() + seconds

    @asynccontextmanager
    async def slot(self, host, deadline):
        acquired = False
        try:
            while time.monotonic() < deadline:
                with self.lock:
                    if self.cooldowns.get(host, 0) > time.monotonic():
                        raise RuntimeError("host cooling down after HTTP 429")
                    if self.active < self.total and self.hosts.get(host, 0) < self.per_host:
                        self.active += 1
                        self.hosts[host] = self.hosts.get(host, 0) + 1
                        acquired = True
                if acquired:
                    break
                await asyncio.sleep(0.05)
            if not acquired:
                raise asyncio.TimeoutError()
            yield
        finally:
            if acquired:
                with self.lock:
                    self.active -= 1
                    self.hosts[host] -= 1
                    if not self.hosts[host]:
                        del self.hosts[host]


def _download_limiter_setting(name: str, default: int, floor: int, ceiling: int) -> int:
    try:
        return max(floor, min(ceiling, int(os.environ.get(name, str(default)) or default)))
    except (TypeError, ValueError):
        return default


# The resolver can have several scene workers active at once.  A twelve-slot
# global gate serialized those workers behind slow/dead hosts, which is why a
# long job accumulated minutes of download tail time even though discovery
# was already complete.  Keep the limiter bounded and configurable while
# giving the default production path enough parallel lanes to fill a scene's
# shortlist promptly.
_DOWNLOAD_LIMITER = DownloadLimiter(
    total=_download_limiter_setting("VIDZEO_WEB_DOWNLOAD_TOTAL", 24, 4, 64),
    per_host=_download_limiter_setting("VIDZEO_WEB_DOWNLOAD_PER_HOST", 4, 1, 12),
)

from .constants import (
    BING_SEARCH_URL,
    SEARCH_HEADERS,
    LOW_CONFIDENCE_DOMAINS,
    REJECT_TEXT_SIGNALS,
    WATERMARK_SIGNALS,
)
from .safety import candidate_is_safe

DUCKDUCKGO_HOMEPAGE = "https://duckduckgo.com/"
DUCKDUCKGO_IMAGE_API = "https://duckduckgo.com/i.js"

BING_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
]

_BING_PARSE_PATTERN = re.compile(
    r'class="iusc"[^>]*?\bm="([^"]+)"', re.IGNORECASE | re.DOTALL
)
_BING_PARSE_SINGLE_QUOTE_PATTERN = re.compile(
    r"class=['\"][^>]*\biusc\b[^>]*\bm='([^']+)'",
    re.IGNORECASE | re.DOTALL,
)

# ── Shared HTTP session for Bing (avoids cold-start each time) ──────
_bing_session: Optional[requests.Session] = None


def _net_retries() -> int:
    try:
        return max(1, min(4, int(os.environ.get("VIDZEO_NET_RETRIES", "3") or 3)))
    except Exception:
        return 3


def _net_delay(attempt: int) -> float:
    try:
        base = max(0.1, min(3.0, float(os.environ.get("VIDZEO_NET_RETRY_BASE", "0.6") or 0.6)))
    except Exception:
        base = 0.6
    return min(6.0, base * (2 ** attempt)) + random.uniform(0.0, 0.25)


def _request_get_with_retries(session: requests.Session, url: str, **kwargs) -> Optional[requests.Response]:
    retry_statuses = {408, 425, 429, 500, 502, 503, 504}
    last_response = None
    for attempt in range(_net_retries()):
        try:
            response = session.get(url, **kwargs)
            last_response = response
            if response.status_code not in retry_statuses:
                return response
        except requests.RequestException:
            last_response = None
        if attempt < _net_retries() - 1:
            time.sleep(_net_delay(attempt))
    return last_response


def _get_bing_session() -> requests.Session:
    global _bing_session
    if _bing_session is None:
        _bing_session = requests.Session()
        _bing_session.headers.update(dict(SEARCH_HEADERS))
        # Scene workers share this session.  Requests' default pool of ten
        # connections is smaller than the mixed-media fan-out, which emitted
        # ``Connection pool is full`` warnings and discarded keep-alive
        # sockets during large jobs.  A bounded larger pool keeps reuse
        # efficient without allowing an unbounded connection storm.
        adapter = HTTPAdapter(
            pool_connections=16,
            pool_maxsize=32,
            max_retries=0,
            pool_block=False,
        )
        _bing_session.mount("http://", adapter)
        _bing_session.mount("https://", adapter)
        # Warm up: visit Bing homepage for cookies
        try:
            _bing_session.get("https://www.bing.com/", timeout=10)
        except Exception:
            pass
    return _bing_session


def _normalize_url(url: str) -> str:
    cleaned = html_unescape(str(url or "")).strip()
    if not cleaned:
        return ""
    if cleaned.startswith("//"):
        cleaned = f"https:{cleaned}"
    lowered = cleaned.lower()
    if lowered.startswith(("data:", "blob:", "javascript:", "about:", "file:")):
        return ""
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"}:
        return ""
    if not parsed.netloc:
        return ""
    return cleaned


def _extract_domain(url: str) -> str:
    try:
        netloc = (urlparse(url).netloc or "").lower().replace("www.", "")
        return netloc.split(":")[0]
    except Exception:
        return ""


def _requests_image_fallback(
    url: str,
    path: Path,
    headers: Optional[Dict[str, str]],
    timeout_seconds: float,
    min_size: int,
    max_bytes: int,
) -> Tuple[Optional[Path], str]:
    """Retry a hotlink with the requests transport after an aiohttp 403.

    A few otherwise reliable image hosts (notably Wikimedia's upload CDN)
    reject aiohttp's TLS/client fingerprint while returning the same URL to a
    normal browser or requests.  This is a narrow transport fallback, only
    used for 403/406 responses; it does not weaken relevance or safety gates.
    """
    request_headers = dict(SEARCH_HEADERS)
    if headers:
        request_headers.update(headers)
    try:
        response = requests.get(
            url,
            headers=request_headers,
            timeout=max(2.0, min(20.0, float(timeout_seconds))),
            stream=True,
        )
        if response.status_code != 200:
            return None, f"HTTP {response.status_code}"
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if content_type and "image" not in content_type and "octet" not in content_type:
            return None, f"not image: {content_type}"
        declared = response.headers.get("Content-Length")
        try:
            if declared and int(declared) > max_bytes:
                return None, f"too large ({declared}B > {max_bytes}B)"
        except (TypeError, ValueError):
            pass
        chunks: List[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                return None, f"too large ({total}B > {max_bytes}B)"
            chunks.append(chunk)
        data = b"".join(chunks)
        if len(data) < min_size:
            return None, f"too small ({len(data)}B)"
        path.write_bytes(data)
        return path, ""
    except Exception as exc:
        return None, str(exc)[:80]


# ── URL DISCOVERY: Bing (requests — reliable, bypasses bot detection) ──
def _parse_bing_html(html_text: str, limit: int) -> List[Dict]:
    results = []
    matches = list(_BING_PARSE_PATTERN.finditer(html_text))
    matches.extend(_BING_PARSE_SINGLE_QUOTE_PATTERN.finditer(html_text))
    for m in matches:
        try:
            meta = json.loads(html_unescape(m.group(1)))
        except Exception:
            continue
        image_url = _normalize_url(meta.get("murl", ""))
        page_url = _normalize_url(meta.get("purl", ""))
        title = html_unescape(meta.get("t", "") or meta.get("desc", "") or "")
        if not image_url:
            continue
        domain = _extract_domain(page_url or image_url)
        dl = (domain or "").lower()
        if any(sig in dl for sig in LOW_CONFIDENCE_DOMAINS):
            continue
        blob = (title + " " + image_url).lower()
        if any(sig in blob for sig in REJECT_TEXT_SIGNALS):
            continue
        candidate = {
            "title": title, "image_url": image_url,
            "source_url": page_url or image_url, "page_url": page_url,
            "thumbnail_url": _normalize_url(meta.get("turl", "")),
            "preview_url": _normalize_url(meta.get("turl", "")),
            "domain": domain, "source": "bing_hybrid", "resolver_lane": "bing",
            "width": meta.get("w", 0) or 0, "height": meta.get("h", 0) or 0,
            "description": html_unescape(meta.get("desc", "") or ""),
        }
        safe, _reason = candidate_is_safe(candidate)
        if not safe:
            continue
        results.append(candidate)
        if len(results) >= limit:
            break
    return results


# ── Trusted editorial/news domains (PRIMARY sources) ───────────────
TRUSTED_DOMAINS: frozenset = frozenset({
    # Wire services / editorial giants
    "gettyimages", "reuters", "apnews", "afp", "dpa",
    # Major news outlets
    "bbc", "theguardian", "nytimes", "washingtonpost", "wsj",
    "aljazeera", "cnn", "nbcnews", "cbsnews", "abcnews",
    "telegraph", "independent", "dailymail", "mirror",
    "hindustantimes", "timesofindia", "ndtv", "indiatoday",
    "thehindu", "indianexpress", "firstpost",
    # Sports-specific editorial
    "espncricinfo", "icc-cricket", "bcci", "cricbuzz", "wisden",
    "espn", "skysports", "goal", "sportskeeda", "bleacherreport",
    "theathletic", "sportingnews", "cricket", "iplt20",
    # Reference / encyclopedia
    "wikipedia", "wikimedia", "britannica", "nationalgeographic",
    "smithsonianmag", "history",
    # Photo agencies
    "flickr", "unsplash", "pexels", "pixabay",
})


def _score_candidate(candidate: Dict, query_terms: Set[str]) -> float:
    """Weighted relevance score for a single candidate.
    
    Returns score where:
      >= 3.0 : clearly relevant (accept)
      0.8-3.0: partially relevant (accept if no better)
      < 0.8  : likely garbage (soft reject)
    """
    score = 0.0
    title = str(candidate.get("title", "") or "").lower()
    domain = str(candidate.get("domain", "") or "").lower()
    image_url = str(candidate.get("image_url", "") or "").lower()
    tags = str(candidate.get("tags", "") or "").lower()
    # Combined searchable text (title + tags + domain)
    search_blob = f"{title} {tags}"

    # ── Term matches in title + tags ─────────────────────────────
    for term in query_terms:
        if term in search_blob:
            # Multi-word terms count more
            if " " in term or len(term) > 4:
                score += 2.5
            else:
                score += 1.5
        # Partial word match in url as a bonus signal
        elif term in image_url and len(term) > 4:
            score += 0.5
    
    # ── Domain trust (editorial sources get heavy bonus) ─────────
    editorial_tier1 = {"gettyimages", "reuters", "apnews", "afp", "bbc", "wikimedia"}
    editorial_tier2 = {"theguardian", "nytimes", "aljazeera", "britannica", "espncricinfo", "cricbuzz"}
    flickr_trusted = {"flickr.com", "staticflickr.com", "live.staticflickr.com"}
    if any(td in domain for td in editorial_tier1):
        score += 6.0
    elif any(td in domain for td in editorial_tier2):
        score += 4.0
    elif any(td in domain for td in flickr_trusted):
        # Flickr photos are generally real photography — give good base bonus
        score += 3.5
    elif any(td in domain for td in TRUSTED_DOMAINS):
        score += 3.0
    
    # ── Domain appears related to query ──────────────────────────
    for term in query_terms:
        if term in domain:
            score += 1.0
            break

    # ── Landscape orientation bonus from URL hints ───────────────
    if any(p in image_url for p in ("/landscape", "/wide", "/banner", "/16x9", "/1920", "/1280")):
        score += 3.0
    if any(p in image_url for p in ("/portrait", "/square", "/avatar", "/320x")):
        score -= 4.0
    
    return score


def _relevance_gate(results: List[Dict], query: str) -> List[Dict]:
    """Filter candidates by weighted relevance score.
    
    Require topical metadata before applying publisher/quality bonuses.
    Terse partial captions can reach the pixel verifier, but an unrelated
    trending page cannot qualify through domain trust alone.
    """
    if not results:
        return results
    # Search engines sometimes serve a trending/default result page after a
    # blocked or empty query. Publisher reputation and image dimensions cannot
    # turn those unrelated rows into relevant candidates.
    content_query = re.sub(r"\bsite:\S+", "", (query or "").casefold())
    filler = {'the', 'and', 'for', 'with', 'from', 'past', 'this', 'that', 'more',
              'which', 'what', 'how', 'why', 'photo', 'photos', 'image', 'images',
              'photograph', 'archival', 'documentary', 'reference', 'editorial',
              'historical', 'useful', 'starting', 'point', 'picture', 'pictures'}
    query_terms = set(re.findall(r"[^\W_]{3,}", content_query, re.UNICODE)) - filler
    if not query_terms:
        return results

    # Extract proper nouns for SOFT penalty (not hard rejection)
    words = (query or "").strip().split()
    proper_nouns = []
    for i, w in enumerate(words):
        clean_w = re.sub(r'[^\w]', '', w)
        if clean_w and clean_w[0].isupper():
            if i == 0 and clean_w.lower() in {"the", "a", "an", "in", "on", "at", "of", "and", "or", "for", "with", "as"}:
                continue
            proper_nouns.append(clean_w.lower())

    scored = []
    for r in results:
        title_lower = (r.get("title") or "").lower()
        tags_lower = str(r.get("tags", "") or "").lower()
        combined_text = f"{title_lower} {tags_lower}"
        from urllib.parse import unquote
        evidence_text = unquote(' '.join(str(r.get(key) or '') for key in
            ('title', 'tags', 'description', 'image_url', 'url', 'source_url', 'page_url'))).casefold()
        evidence_terms = set(re.findall(r"[^\W_]{3,}", evidence_text, re.UNICODE))
        matched = {term for term in query_terms if term in evidence_terms
                   or term.rstrip('s') in evidence_terms
                   or any(word.rstrip('s') == term for word in evidence_terms)}
        if query_terms & {'beer', 'beers', 'brewing', 'brewery', 'breweries'}:
            if re.search(r'\b(?:beers?|brews?|brewing|brewer(?:y|ies)|ales?|lagers?|stouts?|pubs?|taverns?)\b', evidence_text):
                matched.add('beer')
            else:
                continue
        if not matched:
            continue
        
        s = _score_candidate(r, query_terms)
        
        # Soft proper-noun penalty: -1.5 pts if no proper noun found in title+tags
        # This is NOT a hard rejection — results from trusted domains still pass.
        if proper_nouns and not any(pn in combined_text for pn in proper_nouns):
            s -= 1.5
            
        r["_relevance_score"] = s
        r["_query_term_matches"] = sorted(matched)
        scored.append((s, r))

    # Keep candidates with score >= 0.8 (lower threshold = more candidates reach download)
    kept = [r for s, r in sorted(scored, key=lambda x: x[0], reverse=True) if s >= 0.8]
    return kept


# ── Editorial domain boost for Bing searches ───────────────────────
_EDITORIAL_SITE_BOOST = [
    "site:wikimedia.org", "site:britannica.com", "site:nationalgeographic.com",
    "site:bbc.com", "site:reuters.com", "site:apnews.com",
    "site:theguardian.com", "site:nytimes.com", "site:washingtonpost.com",
    "site:gettyimages.com", "site:unsplash.com", "site:flickr.com",
]


def search_bing(query: str, limit: int = 36) -> List[Dict]:
    """Search Bing synchronously (requests — reliable).
    
    Runs direct search first for maximum coverage and speed, then enriches
    with high-authority documentary/editorial sites if needed.
    """
    q = re.sub(r"\s+", " ", (query or "").strip())
    if not q:
        return []
    headers = dict(SEARCH_HEADERS)
    headers["User-Agent"] = random.choice(BING_USER_AGENTS)
    headers["Referer"] = "https://www.bing.com/"

    session = _get_bing_session()
    all_results = []
    seen_urls = set()

    # Pass 1: plain direct search (fastest, broadest coverage)
    try:
        r = _request_get_with_retries(
            session,
            BING_SEARCH_URL,
            params={"q": q, "form": "HDRSC2", "adlt": "strict"},
            headers=headers,
            timeout=10,
        )
        if r is not None and r.status_code == 200:
            results = _parse_bing_html(r.text, limit)
            for r_item in results:
                url = r_item.get("image_url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r_item)
    except Exception:
        pass

    # Pass 2: editorial site enrichment if we need more results
    if len(all_results) < limit:
        editorial_sites = random.sample(_EDITORIAL_SITE_BOOST, min(2, len(_EDITORIAL_SITE_BOOST)))
        for site_mod in editorial_sites:
            if len(all_results) >= limit:
                break
            try:
                boosted_q = f"{q} {site_mod}"
                r = _request_get_with_retries(
                    session,
                    BING_SEARCH_URL,
                    params={"q": boosted_q, "form": "HDRSC2", "adlt": "strict"},
                    headers=headers,
                    timeout=8,
                )
                if r is not None and r.status_code == 200:
                    results = _parse_bing_html(r.text, limit)
                    for r_item in results:
                        url = r_item.get("image_url", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            r_item["_editorial_boost"] = True
                            all_results.append(r_item)
            except Exception:
                continue

    return _relevance_gate(all_results[:limit * 2], q)


# ── URL DISCOVERY: DuckDuckGo (requests) ────────────────────────────
def search_ddg(query: str, limit: int = 36) -> List[Dict]:
    """Search DuckDuckGo synchronously."""
    q = re.sub(r"\s+", " ", (query or "").strip())
    if not q:
        return []
    headers = dict(SEARCH_HEADERS)
    session = requests.Session()
    session.headers.update(headers)

    # Get vqd token
    try:
        r = _request_get_with_retries(session, DUCKDUCKGO_HOMEPAGE, params={"q": q, "kp": "1"}, timeout=10)
        if r is None:
            return []
        token_match = (
            re.search(r"vqd=['\"]?([0-9A-Za-z\-_]+)['\"]?", r.text)
            or re.search(r"vqd=([0-9A-Za-z\-_]+)&", r.text)
        )
        if not token_match:
            return []
        vqd = token_match.group(1)
    except Exception:
        return []

    # Get images
    results = []
    try:
        r = _request_get_with_retries(
            session,
            DUCKDUCKGO_IMAGE_API,
            params={"l": "us-en", "o": "json", "q": q, "vqd": vqd, "f": ",,,", "p": "1", "kp": "1"},
            headers={
                **headers,
                "Accept": "application/json",
                "Referer": f"https://duckduckgo.com/?q={q}",
            },
            timeout=10,
        )
        if r is None:
            return []
        payload = r.json()
    except Exception:
        return []

    for row in payload.get("results") or []:
        if not isinstance(row, dict):
            continue
        image_url = _normalize_url(row.get("image") or "")
        page_url = _normalize_url(row.get("url") or "")
        if not image_url:
            continue
        domain = _extract_domain(page_url or image_url)
        if any(sig in (domain or "").lower() for sig in LOW_CONFIDENCE_DOMAINS):
            continue
        candidate = {
            "title": html_unescape(str(row.get("title") or "")).strip(),
            "image_url": image_url,
            "source_url": page_url or image_url,
            "page_url": page_url,
            "thumbnail_url": _normalize_url(row.get("thumbnail") or ""),
            "preview_url": _normalize_url(row.get("thumbnail") or ""),
            "domain": domain,
            "source": "ddg_hybrid",
            "resolver_lane": "ddg-json",
        }
        safe, _reason = candidate_is_safe(candidate)
        if not safe:
            continue
        results.append(candidate)
        if len(results) >= limit:
            break
    return _relevance_gate(results[:limit * 2], q)


# ── URL DISCOVERY: Parallel hybrid (Bing + DDG via ThreadPoolExecutor) ──
def search_hybrid(
    queries: List[str],
    limit_per_query: int = 20,
    max_workers: int = 16,
) -> Dict[str, List[Dict]]:
    """Search multiple queries in parallel using ThreadPoolExecutor.

    Each query: Bing + DDG searched concurrently.
    Results merged, deduplicated per query.
    """
    if not queries:
        return {}

    def _search_one(q: str) -> Tuple[str, List[Dict]]:
        results = []
        seen = set()
        # Bing + DDG in sequence within one thread (2 HTTP calls max)
        for engine_results in [search_bing(q, limit_per_query), search_ddg(q, limit_per_query)]:
            for r in engine_results:
                uk = r.get("image_url", "")
                if uk and uk not in seen:
                    seen.add(uk)
                    results.append(r)
        return q, results[:limit_per_query]

    out: Dict[str, List[Dict]] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(queries))) as ex:
        futures = {ex.submit(_search_one, q): q for q in queries}
        for future in as_completed(futures):
            try:
                q, results = future.result(timeout=30)
                out[q] = results
            except Exception:
                pass
    return out


# ── IMAGE DOWNLOAD: aiohttp bulk (fast, parallel) ────────────────────

async def _download_bulk_async(
    urls: List[str],
    output_dir: Path,
    max_concurrent: int = 50,
    timeout_seconds: float = 15.0,
    min_size: int = 8 * 1024,
    headers_by_url: Optional[Dict[str, Dict[str, str]]] = None,
    max_bytes: Optional[int] = None,
) -> List[Tuple[str, Optional[Path], str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(max_concurrent)

    # Keep the transport cap aligned with resolver validation.  Enforce it
    # while reading the response so a hostile or accidentally huge publisher
    # image cannot consume tens or hundreds of megabytes before being
    # rejected by the later PIL/dimension gate.
    if max_bytes is None:
        try:
            configured_mb = float(os.environ.get("VIDZEO_WEB_MAX_IMAGE_MB", "50") or 50)
        except (TypeError, ValueError):
            configured_mb = 50.0
        max_bytes = int(max(1.0, min(200.0, configured_mb)) * 1024 * 1024)
    else:
        try:
            max_bytes = max(1, int(max_bytes))
        except (TypeError, ValueError):
            max_bytes = 50 * 1024 * 1024

    # Create a local session for this call to avoid loop closed issues
    connector = aiohttp.TCPConnector(limit=100, limit_per_host=16, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=5.0)
    
    async with aiohttp.ClientSession(
        headers=dict(SEARCH_HEADERS),
        timeout=timeout,
        connector=connector,
    ) as session:

        async def _dl_one(url: str) -> Tuple[str, Optional[Path], str]:
            async with semaphore:
                url = _normalize_url(url)
                if not url:
                    return url, None, "invalid URL"
                ext = ".jpg"
                for e in (".png", ".webp", ".jpg", ".jpeg", ".bmp"):
                    if urlparse(url).path.lower().endswith(e):
                        ext = e
                        break
                uh = hashlib.sha256(url.encode()).hexdigest()[:12]
                ts = hex(int(time.time_ns()))[-6:]
                path = output_dir / f"img_{uh}_{ts}{ext}"
                last_error = ""
                retry_statuses = {408, 425, 429, 500, 502, 503, 504}
                deadline = time.monotonic() + timeout_seconds
                for attempt in range(_net_retries()):
                    try:
                        async with _DOWNLOAD_LIMITER.slot(urlparse(url).netloc.lower(), deadline), session.get(
                            url, headers=(headers_by_url or {}).get(url),
                            timeout=aiohttp.ClientTimeout(total=max(0.01, deadline - time.monotonic())),
                        ) as resp:
                            if resp.status != 200:
                                last_error = f"HTTP {resp.status}"
                                if resp.status == 429:
                                    _DOWNLOAD_LIMITER.defer(urlparse(url).netloc.lower())
                                    return url, None, last_error
                                if resp.status in {403, 406}:
                                    # Some CDNs fingerprint aiohttp and return
                                    # 403 while accepting the same request via
                                    # the requests transport. Keep this retry
                                    # bounded and only invoke it for these
                                    # hotlink responses.
                                    fallback_path, fallback_error = await asyncio.to_thread(
                                        _requests_image_fallback,
                                        url,
                                        path,
                                        (headers_by_url or {}).get(url),
                                        max(2.0, deadline - time.monotonic()),
                                        min_size,
                                        max_bytes,
                                    )
                                    if fallback_path:
                                        return url, fallback_path, ""
                                    last_error = fallback_error or last_error
                                if resp.status not in retry_statuses:
                                    return url, None, last_error
                            else:
                                ct = resp.headers.get("Content-Type", "").lower()
                                if ct and "image" not in ct and "octet" not in ct:
                                    return url, None, f"not image: {ct}"
                                content_length = resp.headers.get("Content-Length")
                                try:
                                    declared_length = int(content_length) if content_length else 0
                                except (TypeError, ValueError):
                                    declared_length = 0
                                if declared_length > max_bytes:
                                    return url, None, f"too large ({declared_length}B > {max_bytes}B)"

                                chunks = []
                                total_bytes = 0
                                # Stream in bounded chunks rather than using
                                # resp.read(), which otherwise allocates the
                                # complete body before the cap can be checked.
                                async for chunk in resp.content.iter_chunked(64 * 1024):
                                    total_bytes += len(chunk)
                                    if total_bytes > max_bytes:
                                        return url, None, f"too large ({total_bytes}B > {max_bytes}B)"
                                    chunks.append(chunk)
                                data = b"".join(chunks)
                                if len(data) < min_size:
                                    return url, None, f"too small ({len(data)}B)"
                                path.write_bytes(data)
                                return url, path, ""
                    except asyncio.TimeoutError:
                        last_error = "timeout"
                    except Exception as e:
                        last_error = str(e)[:80]
                    if attempt < _net_retries() - 1:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        await asyncio.sleep(min(_net_delay(attempt), remaining))
                return url, None, last_error or "download failed"

        tasks = [_dl_one(u) for u in urls]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        
    cleaned = []
    for i, r in enumerate(gathered):
        if isinstance(r, Exception):
            cleaned.append((urls[i], None, str(r)[:80]))
        else:
            cleaned.append(r)
    return cleaned


def download_bulk(
    urls: List[str],
    output_dir: Path,
    max_concurrent: int = 50,
    timeout_seconds: float = 15.0,
    headers_by_url: Optional[Dict[str, Dict[str, str]]] = None,
    max_bytes: Optional[int] = None,
) -> List[Tuple[str, Optional[Path], str]]:
    """Download multiple image URLs via aiohttp in parallel."""
    return asyncio.run(_download_bulk_async(
        urls, output_dir, max_concurrent, timeout_seconds,
        headers_by_url=headers_by_url, max_bytes=max_bytes,
    ))
