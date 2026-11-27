"""
Hybrid Search + Download Engine.
URL Discovery: synchronous requests (bypasses Bing bot-detection reliably)
Image Download: aiohttp (parallel, fast, 50 concurrent workers)
"""
from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import logging
import math
import os
import random
import re
import time
import threading
from contextlib import AsyncExitStack, asynccontextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from ..job_context import check_job_active
from ..pipeline_activity import record_activity
from html import unescape as html_unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp
import requests
from requests.adapters import HTTPAdapter

from .runtime import web_direct_first


class HostCircuitOpen(RuntimeError):
    pass


class DownloadSlotTimeout(TimeoutError):
    pass


def _download_slot_wait_seconds() -> float:
    """Max seconds a URL waits for a download slot before failing fast.

    Test 30: 13 URLs never got a slot at all while slow transfers held
    all 12 slots, and 7 more died as capacity-wait after burning their
    whole attempt budget in the queue. Failing fast at 15 s (env
    ``VIDZEO_WEB_DOWNLOAD_SLOT_WAIT_SECONDS``) leaves the batch free to
    move on to the next URL instead of timing out on transfer anyway.

    Round 7 (test 34): the 15 s cap is now the killer, not the cure.
    The test-25 fix gives a transfer a FRESH full budget once its slot
    is held, so waiting no longer burns transfer budget — and with
    45-60 s transfers holding the 3/host slots, URLs 4-6 on a host
    wait out the 15 s cap while siblings finish at ~45-60 s, then die
    as "capacity wait timed out" even though a slot frees seconds
    later (test 34: 4/48 URLs in a healthy hour, most of every early
    chunk in a slow hour). The default is now 60 s — about one round
    of slow transfers — so a URL survives until its slot frees; the
    attempt deadline and the batch wall still bound the total wait.
    Env-overridable via ``VIDZEO_WEB_DOWNLOAD_SLOT_WAIT_SECONDS``.
    """
    try:
        return max(
            1.0,
            min(120.0, float(os.environ.get("VIDZEO_WEB_DOWNLOAD_SLOT_WAIT_SECONDS", "60") or 60)),
        )
    except (TypeError, ValueError):
        return 60.0


def _download_connect_timeout_seconds() -> float:
    """Connect timeout (seconds) for one image-download attempt.

    Round 8 (test 35): the session sets connect=5.0, but every
    per-request timeout overrides the session timeout with a
    total-only ClientTimeout — so a hung connect has NO connect
    limit and burns the whole total budget. Test 35 logged
    'Cannot connect to host live.staticflickr.com:443' /
    'upload.wikimedia.org:443' after ~60s: aiohttp relabels a
    total-timeout-during-connect as a connect error, so those are
    SYN-blackholed connects, not fast refusals. A 10s connect
    timeout was added to fail such an attempt fast and leave the
    batch wall for the other route.

    Round 9 (test 36): the 10s cap itself became the killer. The
    live job landed 0/34 chunks with median transfer 21.3s and
    chunk walls at multiples of ~11s — every attempt died in the
    CONNECT phase at ~10s, on BOTH routes, before a single byte
    flowed. The connect phase is the slow phase on this egress even
    when healthy: direct connects to thumb/upload.wikimedia.org
    take 3.6-5.0s of a 4-6s transfer (measured live, Nov 23), and
    in a slow hour the whole wave runs ~11x slower (muse wave
    80.7s vs 7.1s healthy; test 35's hour 98.9s, wikimedia 38.9s),
    so connects take 10-40s+. A 10s cap kills every slow-but-alive
    connect; only a truly dead (blackholed) connect should fail,
    and the TOTAL timeout (120s) already bounds that. The default
    is therefore 60s — half the 120s per-URL total, so a blackholed
    route can burn at most half the attempt budget and the other
    route still gets its turn inside the 180s batch wall (proven
    live-path: 6/6 land in 151.6s with a 60s connect cap against
    a blackholed direct route + a 15s-delayed proxy CONNECT).
    Env-overridable via VIDZEO_WEB_DOWNLOAD_CONNECT_TIMEOUT_SECONDS.
    """
    try:
        return max(
            2.0,
            min(120.0, float(os.environ.get("VIDZEO_WEB_DOWNLOAD_CONNECT_TIMEOUT_SECONDS", "60") or 60)),
        )
    except (TypeError, ValueError):
        return 60.0


def _download_direct_retry_enabled(direct_retry=None) -> bool:
    """Whether an image download may use the direct route at all.

    Round 6 (test 33, corrected): the direct route is NOT blanket-dead
    — that was over-generalized from the Wikimedia API host. Measured
    live with aiohttp on real muse-row URLs: thumb.wikimedia.org direct
    200 in 2.1 s (proxy 3.8 s), upload.wikimedia.org direct 200 in
    1.8 s, live.staticflickr.com direct 200 in 2.0-3.2 s (proxy 3.2-3.8 s).
    Direct is an independent route that can survive a proxy brownout,
    so it stays ENABLED by default; the per-host order in
    ``_download_route_order`` decides which route goes first. Pass
    direct_retry=False (or set VIDZEO_WEB_DOWNLOAD_DIRECT_RETRY=0) to
    force proxy-only.
    """
    if direct_retry is not None:
        return bool(direct_retry)
    return str(
        os.environ.get("VIDZEO_WEB_DOWNLOAD_DIRECT_RETRY", "1") or "1"
    ).strip().lower() in {"1", "true", "yes", "on"}


# Image hosts proven direct-capable by the round-6 live route table
# (aiohttp, real muse-row URLs, Nov 23): thumb/upload.wikimedia.org and
# live.staticflickr.com all return 200 direct, usually faster than via
# the proxy. The Wikimedia API host (commons.wikimedia.org) is NOT here:
# direct to it is dead — it stays proxy-only, and the search path never
# downloads from it. Unknown hosts default to proxy-first: the safe
# choice for a host never measured. Extend via env
# VIDZEO_WEB_DOWNLOAD_DIRECT_FIRST_HOSTS (comma-separated hostnames,
# replaces this set).
_DIRECT_FIRST_DOWNLOAD_HOSTS = frozenset({
    "thumb.wikimedia.org",
    "upload.wikimedia.org",
    "live.staticflickr.com",
})


def _download_direct_first_hosts() -> set:
    """Hosts whose image downloads try direct before the proxy."""
    raw = os.environ.get("VIDZEO_WEB_DOWNLOAD_DIRECT_FIRST_HOSTS")
    if raw:
        return {h.strip().lower() for h in raw.split(",") if h.strip()}
    return set(_DIRECT_FIRST_DOWNLOAD_HOSTS)


def _download_route_order(host: str, direct_allowed: bool = True) -> List[str]:
    """Attempt order for one image download: direct only (Nov 25 direct-only).

    Owner order Nov 25: media retrieval never uses the egress proxy.
    Every host downloads direct; there is no proxy leg. ``direct_allowed``
    and the direct-first host set are kept for signature compatibility
    and are now ignored: a host whose direct route is dead fails the
    download, and the scene falls to its next candidate (a single bad
    image never blocks a render).
    """
    return ["direct"]


class DownloadLimiter:
    """One budget across scene threads and their independent asyncio loops."""

    def __init__(self, total=12, per_host=3, cdn_per_host=None):
        self.total = total
        self.per_host = per_host
        # Round 7 (test 34): the 3/host cap is the binding download
        # constraint under slow egress — 40 muse rows on 2-3 image CDNs
        # need ~7 min to drain at 3 slots x 60 s transfers, past every
        # batch wall. The known image CDNs (the same hosts round 6
        # measured direct-capable) are built for concurrent fetches, so
        # they get a higher per-host cap; every other host keeps 3, and
        # the search sources' throttle is untouched. Env
        # VIDZEO_WEB_DOWNLOAD_CDN_PER_HOST, default 6.
        self.cdn_per_host = 6 if cdn_per_host is None else max(1, int(cdn_per_host))
        self.active = 0
        self.hosts = {}
        self.cooldowns = {}
        self.failures = {}
        self.lock = threading.Lock()
        # FIFO wait queue: (ticket, host) in arrival order. A waiter may
        # only take a slot when it is the earliest waiter whose host
        # currently has capacity, so no later waiter can overtake an
        # earlier eligible one, and a waiter for a capped host cannot
        # head-of-line-block waiters for hosts with free capacity.
        self._wait_queue: collections.deque = collections.deque()
        self._ticket_seq = 0

    def open_circuit(self, host, seconds=60):
        """Open the host circuit immediately (a 403 is a refusal, not a blip).

        The failure-count path needs 3 failures before it opens; a host that
        answers 403 has already refused, so waiting for 2 more refusals just
        burns candidate attempts and slot waits against a dead host. A 403
        must never be retried either: the same proxy refuses the same URL
        again, so the only useful response is to stop trying that host and
        let the scene fall to its next candidate or lane.
        """
        with self.lock:
            self.cooldowns[host] = time.monotonic() + max(1, seconds)
            self.failures.pop(host, None)
            LOGGER.warning("Web image host circuit opened for %s after HTTP 403 (%ss)", host, max(1, seconds))

    def defer(self, host, seconds=30):
        with self.lock:
            self.cooldowns[host] = max(self.cooldowns.get(host, 0), time.monotonic() + seconds)

    def record_outcome(self, host, failed):
        """Record one download outcome for the host circuit.

        Only a genuine refusal signal may pass failed=True: timeouts are
        shared slow egress, not host failure (test 23), and must never
        trip the circuit — the download loop records them separately.
        A success clears the failure count and any expired cooldown.
        """
        with self.lock:
            if not failed:
                self.failures.pop(host, None)
                if self.cooldowns.get(host, 0) <= time.monotonic():
                    self.cooldowns.pop(host, None)
                return
            count = self.failures.get(host, 0) + 1
            self.failures[host] = count
            if count >= 3 or host in self.cooldowns:
                self.cooldowns[host] = time.monotonic() + 60
                self.failures.pop(host, None)
                LOGGER.warning("Web image host circuit opened for %s (60s)", host)

    def _host_limit(self, host):
        """Concurrent slots for one host: 1 while its circuit is open,
        the CDN cap for the measured image CDNs, else the per-host cap."""
        if host in self.cooldowns:
            return 1
        if host in _DIRECT_FIRST_DOWNLOAD_HOSTS:
            return self.cdn_per_host
        return self.per_host

    @asynccontextmanager
    async def slot(self, host, deadline):
        acquired = False
        ticket = None
        wait_started = time.monotonic()
        # Slot wait is capped separately from the attempt deadline: a URL
        # that waits the whole cap still keeps its full transfer budget
        # (the transfer deadline is computed fresh after acquisition).
        # Round 13: an infinite deadline means wait mode — the URL waits
        # until a slot frees (or the host circuit opens, which is a
        # genuine failure), never timing out on the queue. The per-host
        # throttle itself is untouched: the slot still waits its turn in
        # the FIFO queue; only the clock cap is removed.
        wait_cap = (
            float("inf")
            if not math.isfinite(deadline)
            else min(deadline, wait_started + _download_slot_wait_seconds())
        )
        try:
            with self.lock:
                self._ticket_seq += 1
                ticket = self._ticket_seq
                self._wait_queue.append((ticket, host))
            while time.monotonic() < wait_cap:
                with self.lock:
                    if self.cooldowns.get(host, 0) > time.monotonic():
                        raise HostCircuitOpen("host circuit open after 403, 429 or timeout")
                    host_limit = self._host_limit(host)
                    # Earliest waiter whose host has capacity right now.
                    eligible = None
                    for queued_ticket, queued_host in self._wait_queue:
                        queued_limit = self._host_limit(queued_host)
                        if self.hosts.get(queued_host, 0) < queued_limit:
                            eligible = queued_ticket
                            break
                    if (
                        eligible == ticket
                        and self.active < self.total
                        and self.hosts.get(host, 0) < host_limit
                    ):
                        self.active += 1
                        self.hosts[host] = self.hosts.get(host, 0) + 1
                        self._wait_queue.remove((ticket, host))
                        acquired = True
                if acquired:
                    break
                await asyncio.sleep(0.05)
            if not acquired:
                raise DownloadSlotTimeout("web download capacity wait timed out")
            yield
        finally:
            if ticket is not None and not acquired:
                with self.lock:
                    try:
                        self._wait_queue.remove((ticket, host))
                    except ValueError:
                        pass
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


# Spec (Vidzeo Local): DownloadLimiter 12 total, max 3 per host, 30s
# cooldown on 429 (the 429 defer defaults to 30s in defer() below).
# The resolver can have several scene workers active at once; the
# bounded gate keeps slow/dead hosts from serializing them, while the
# per-host cap protects the sources. Round 7: the measured image CDNs
# get a higher per-host cap (6, env VIDZEO_WEB_DOWNLOAD_CDN_PER_HOST)
# — see DownloadLimiter; all other hosts keep the spec 3.
_DOWNLOAD_LIMITER = DownloadLimiter(
    total=_download_limiter_setting("VIDZEO_WEB_DOWNLOAD_TOTAL", 12, 4, 64),
    per_host=_download_limiter_setting("VIDZEO_WEB_DOWNLOAD_PER_HOST", 3, 1, 12),
    cdn_per_host=_download_limiter_setting("VIDZEO_WEB_DOWNLOAD_CDN_PER_HOST", 6, 1, 12),
)

from .constants import (
    BING_SEARCH_URL,
    SEARCH_HEADERS,
    LOW_CONFIDENCE_DOMAINS,
    REJECT_TEXT_SIGNALS,
    WATERMARK_SIGNALS,
)
from .safety import candidate_is_safe

LOGGER = logging.getLogger(__name__)

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

# Bing pacing (research-pipeline discipline, muse-search design §1.4):
# at least 5 s between Bing calls, serialised across scene workers via
# a module lock, so concurrent scenes cannot burst Bing into a soft
# limit. Env VIDZEO_MUSE_SEARCH_BING_GAP_SECONDS, default 5.
_BING_GAP_LOCK = threading.Lock()
_BING_LAST_CALL_AT = 0.0


def _bing_gap_seconds() -> float:
    try:
        return max(
            0.0,
            min(30.0, float(os.environ.get("VIDZEO_MUSE_SEARCH_BING_GAP_SECONDS", "5") or 5)),
        )
    except (TypeError, ValueError):
        return 5.0


def _bing_pace() -> None:
    """Wait until the minimum Bing gap has elapsed since the last call."""
    gap = _bing_gap_seconds()
    if gap <= 0:
        return
    global _BING_LAST_CALL_AT
    with _BING_GAP_LOCK:
        wait_for = gap - (time.monotonic() - _BING_LAST_CALL_AT)
        if wait_for > 0:
            time.sleep(wait_for)
        _BING_LAST_CALL_AT = time.monotonic()


_BING_DECOY_FILLER = {
    'the', 'and', 'for', 'with', 'from', 'past', 'this', 'that', 'more',
    'which', 'what', 'how', 'why', 'photo', 'photos', 'image', 'images',
    'photograph', 'archival', 'documentary', 'reference', 'editorial',
    'historical', 'useful', 'starting', 'point', 'picture', 'pictures',
}


def bing_rows_are_decoy(results: List[Dict], query: str) -> bool:
    """True when NO returned row contains a query token.

    Bing serves a trending/default result page after a blocked or empty
    query (verified Nov 9 on this egress). A page whose rows share no
    token with the query is a decoy, not an answer: the lane must fail
    (so the resolver records a provider failure and can retry direct)
    instead of banking junk rows that the relevance gate then drops
    silently, looking like "no results".
    """
    rows = list(results or [])
    if not rows:
        return False
    query_terms = {
        term
        for term in re.findall(r"[^\W_]{3,}", str(query or "").casefold(), re.UNICODE)
        if term not in _BING_DECOY_FILLER
    }
    if not query_terms:
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        evidence = " ".join(
            str(row.get(key) or "") for key in (
                "title", "tags", "description", "image_url", "url",
                "source_url", "page_url",
            )
        ).casefold()
        evidence_terms = set(re.findall(r"[^\W_]{3,}", evidence, re.UNICODE))
        if query_terms & evidence_terms:
            return False
    return True


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


# Direct-egress proxy override (Nov 18, web-resilience): proxies=None
# makes one requests call skip the env proxy vars. The resolver's
# route-switching retry re-runs a failed lane direct via direct=True.
_DIRECT_PROXIES = {"http": None, "https": None}


def _request_proxies(direct: bool):
    return dict(_DIRECT_PROXIES) if direct else None


def search_bing(query: str, limit: int = 36, direct: bool = False) -> List[Dict]:
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
        _bing_pace()
        r = _request_get_with_retries(
            session,
            BING_SEARCH_URL,
            params={"q": q, "form": "HDRSC2", "adlt": "strict"},
            headers=headers,
            timeout=10,
            proxies=_request_proxies(direct),
        )
        if r is not None and r.status_code == 200:
            results = _parse_bing_html(r.text, limit)
            for r_item in results:
                url = r_item.get("image_url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r_item)
    except Exception as exc:
        # Was silently swallowed: a dead Bing lane looked identical to a
        # query with no results. Pass 2 still runs, but the failure is now
        # in the log with its error class.
        LOGGER.warning("bing search pass 1 failed for %r: %s: %s", q[:60], type(exc).__name__, exc)

    # Pass 2: editorial site enrichment if we need more results
    if len(all_results) < limit:
        editorial_sites = random.sample(_EDITORIAL_SITE_BOOST, min(2, len(_EDITORIAL_SITE_BOOST)))
        for site_mod in editorial_sites:
            if len(all_results) >= limit:
                break
            try:
                boosted_q = f"{q} {site_mod}"
                _bing_pace()
                r = _request_get_with_retries(
                    session,
                    BING_SEARCH_URL,
                    params={"q": boosted_q, "form": "HDRSC2", "adlt": "strict"},
                    headers=headers,
                    timeout=8,
                    proxies=_request_proxies(direct),
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

    if bing_rows_are_decoy(all_results, q):
        raise RuntimeError(
            "bing decoy results: no row contains a query token "
            "(trending/default page served instead of results)"
        )
    return _relevance_gate(all_results[:limit * 2], q)


_ddg_down_until: float = 0.0


# ── URL DISCOVERY: DuckDuckGo (requests) ────────────────────────────
def search_ddg(query: str, limit: int = 36, direct: bool = False) -> List[Dict]:
    """Search DuckDuckGo synchronously.

    Raises RuntimeError when the lane itself fails (homepage unreachable,
    no vqd token, image API error). The old code returned [] in all those
    cases, so a bot-blocked DDG was indistinguishable from a query with no
    results and the resolver never recorded a provider failure. Callers
    already catch exceptions per source (_discover_hybrid_urls) or per
    query (search_hybrid), so raising here makes the failure visible
    without changing the fallback behaviour.
    """
    global _ddg_down_until
    if time.monotonic() < _ddg_down_until:
        raise RuntimeError("DDG circuit open (cooldown active after bot check/error)")

    q = re.sub(r"\s+", " ", (query or "").strip())
    if not q:
        return []
    headers = dict(SEARCH_HEADERS)
    session = requests.Session()
    session.headers.update(headers)

    # Get vqd token
    try:
        r = _request_get_with_retries(session, DUCKDUCKGO_HOMEPAGE, params={"q": q, "kp": "1"}, timeout=10,
                                        proxies=_request_proxies(direct))
    except Exception as exc:
        _ddg_down_until = time.monotonic() + 30.0
        raise RuntimeError(f"DDG homepage unreachable: {type(exc).__name__}: {exc}") from exc
    if r is None:
        _ddg_down_until = time.monotonic() + 30.0
        raise RuntimeError("DDG homepage unreachable after retries")
    token_match = (
        re.search(r"vqd=['\"]?([0-9A-Za-z\-_]+)['\"]?", r.text)
        or re.search(r"vqd=([0-9A-Za-z\-_]+)&", r.text)
    )
    if not token_match:
        _ddg_down_until = time.monotonic() + 30.0
        raise RuntimeError("DDG vqd token unavailable (bot check or markup change)")
    vqd = token_match.group(1)

    # Get images
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
            proxies=_request_proxies(direct),
        )
    except Exception as exc:
        _ddg_down_until = time.monotonic() + 30.0
        raise RuntimeError(f"DDG image API failed: {type(exc).__name__}: {exc}") from exc
    if r is None:
        _ddg_down_until = time.monotonic() + 30.0
        raise RuntimeError("DDG image API unreachable after retries")
    try:
        payload = r.json()
    except Exception as exc:
        _ddg_down_until = time.monotonic() + 30.0
        raise RuntimeError(f"DDG image API returned unparseable JSON: {type(exc).__name__}: {exc}") from exc

    results = []
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


# ── URL DISCOVERY: Parallel hybrid (Bing via ThreadPoolExecutor) ──
def search_hybrid(
    queries: List[str],
    limit_per_query: int = 20,
    max_workers: int = 16,
) -> Dict[str, List[Dict]]:
    """Search multiple queries in parallel using ThreadPoolExecutor.

    Each query: Bing searched (DuckDuckGo removed Nov 19, 2026 — it
    bot-gates this egress; search_ddg stays defined for direct callers
    and its unit tests, but no live lane calls it).
    Results merged, deduplicated per query.
    """
    if not queries:
        return {}

    def _search_one(q: str) -> Tuple[str, List[Dict]]:
        results = []
        seen = set()
        seen_keys = set()
        # Each engine is isolated: the old code built
        # [search_bing(...), search_ddg(...)] eagerly, so a DDG failure
        # raised away Bing's already-fetched results for the query. A dead
        # engine must cost only its own rows, and the failure is logged
        # with its error class instead of swallowed.
        for engine_name, engine_fn in (("bing", search_bing),):
            try:
                engine_results = engine_fn(q, limit_per_query)
            except Exception as exc:
                LOGGER.warning(
                    "hybrid search engine %s failed for %r: %s: %s",
                    engine_name, q[:60], type(exc).__name__, exc,
                )
                continue
            for r in engine_results:
                uk = r.get("image_url", "")
                if not uk or uk in seen:
                    continue
                # Dedup on (host, path) as well as the exact URL: CDNs serve
                # the same image under many signed/thumbnail query strings,
                # and those duplicates used to fill the per-query shortlist.
                key = _canonical_image_key(uk)
                if key in seen_keys:
                    continue
                seen.add(uk)
                seen_keys.add(key)
                results.append(r)
        return q, results[:limit_per_query]

    out: Dict[str, List[Dict]] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(queries))) as ex:
        futures = {ex.submit(_search_one, q): q for q in queries}
        for future in as_completed(futures):
            try:
                q, results = future.result(timeout=30)
                out[q] = results
            except Exception as exc:
                LOGGER.warning(
                    "hybrid search failed for %r: %s: %s",
                    futures[future][:60], type(exc).__name__, exc,
                )
    return out


def _canonical_image_key(url: str) -> Tuple[str, str]:
    """Host + path dedup key for an image URL (query strings ignored)."""
    try:
        parsed = urlparse(url)
        return (parsed.netloc.lower(), parsed.path.rstrip("/").lower())
    except Exception:
        return ("", str(url or ""))


# ── IMAGE DOWNLOAD: aiohttp bulk (fast, parallel) ────────────────────

def _download_batch_wall_seconds(timeout_seconds: float) -> float:
    """Wall for one download_bulk batch (seconds).

    Nov 22, test 25: the 90s cap was the binding killer, not the 60s
    per-URL timeout. A scene batch holds up to 12 URLs (per-scene
    budget) through 3/host limiter slots at 60s/attempt: 90s lets ~4
    URLs attempt and truncates every retry to ~30s, so a slow hour
    returns 0 even when the network would have answered — test 25
    logged 3-8 "batch budget exhausted" per scene alongside the
    timeouts. 180s lets ~9 URLs on one host attempt with a full retry
    budget each. No concurrency change, no slot change — the wall only
    bounds how long one batch may run; the coordinator stage deadline
    still bounds the job. Env-overridable via
    VIDZEO_WEB_DOWNLOAD_BATCH_WALL_SECONDS (clamped 30-600s); the wall
    never exceeds 3x the per-URL timeout, so fast mode (8s) still gets
    its 24s wall and a 30s timeout still gets 90s.
    """
    try:
        cap = float(
            os.environ.get("VIDZEO_WEB_DOWNLOAD_BATCH_WALL_SECONDS", "180")
            or 180
        )
    except (TypeError, ValueError):
        cap = 180.0
    cap = max(30.0, min(600.0, cap))
    return min(cap, max(0.1, float(timeout_seconds or 0.1)) * 3)


async def _download_bulk_async(
    urls: List[str],
    output_dir: Path,
    max_concurrent: int = 50,
    timeout_seconds: float = 15.0,
    min_size: int = 8 * 1024,
    headers_by_url: Optional[Dict[str, Dict[str, str]]] = None,
    max_bytes: Optional[int] = None,
    direct_retry: Optional[bool] = None,
    stats: Optional[List[Dict[str, Any]]] = None,
    wait_until_complete: bool = False,
) -> List[Tuple[str, Optional[Path], str]]:
    """Download ``urls`` in parallel.

    Each URL is attempted up to twice, in per-host route order (see
    ``_download_route_order``): direct-capable hosts try direct first
    with the proxy as fallback; all other hosts try the proxy first
    with direct as fallback. ``direct_retry`` (default: env
    ``VIDZEO_WEB_DOWNLOAD_DIRECT_RETRY``, 1) is the kill-switch for the
    direct route — pass False for proxy-only downloads, in which case
    the retry re-attempts via the proxy with a fresh budget. ``stats``,
    when given, is filled with one record per URL (``url``, ``host``,
    ``route``, ``slot_wait_ms``, ``transfer_ms``, ``bytes``, ``landed``,
    ``error``) so the caller can tell a slot wait from a slow transfer
    in the per-scene summary.
    """
    direct_retry_enabled = _download_direct_retry_enabled(direct_retry)
    output_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(max_concurrent)
    # Round 13: in wait mode there is no batch wall — every URL gets
    # its per-attempt socket budget (timeout_seconds, 2 attempts = 2
    # routes) and the slot wait is uncapped (see slot()). The batch
    # wall is what killed downloads in job 9ab2eb05's slow hour: the
    # batch deadline expired while transfers were still in flight.
    batch_deadline = (
        float("inf")
        if wait_until_complete
        else time.monotonic() + _download_batch_wall_seconds(timeout_seconds)
    )
    # Round 8 (test 35): one attempt PER ROUTE, always. The two attempts
    # are the two egress paths (direct / proxy), not repeats of the same
    # failing request — the code below promises a transport failure
    # "moves to the next route with a fresh budget". The old
    # min(2, _net_retries()) let VIDZEO_NET_RETRIES=1 (live in the
    # runtime env) silently collapse that to a single attempt, so a
    # direct-first CDN URL whose direct connect blackholed died without
    # ever trying the proxy — test 35's connect-error URLs. The batch
    # wall still bounds the total per URL; the retries knob keeps
    # governing the search-request retries, just not this fallback.
    max_attempts = 2

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

    # Create local sessions for this call to avoid loop closed issues.
    # Two sessions: the proxy session (trust_env=True, env proxy) and a
    # direct session (trust_env=False). NOTE: aiohttp's per-request
    # proxy=None does NOT bypass the proxy — with trust_env=True it
    # falls back to the env proxy — so a timed-out download can only
    # retry direct on a session built without the env proxy.
    connector = aiohttp.TCPConnector(limit=100, limit_per_host=16, ttl_dns_cache=300)
    # Round 9: the session timeout used to hardcode connect=5.0. Every
    # GET passes a per-request timeout, so the session value never
    # fires — but a reader (or a future call without one) would inherit
    # a 5s connect cap that test 36 proved is below even the healthy
    # connect time (~4s, no margin). Keep it aligned with the helper.
    timeout = aiohttp.ClientTimeout(
        total=timeout_seconds, connect=_download_connect_timeout_seconds()
    )

    async with AsyncExitStack() as stack:
        session = await stack.enter_async_context(aiohttp.ClientSession(
            headers=dict(SEARCH_HEADERS),
            timeout=timeout,
            connector=connector,
            trust_env=True,
        ))
        direct_session = None
        if direct_retry_enabled:
            direct_connector = aiohttp.TCPConnector(limit=100, limit_per_host=16, ttl_dns_cache=300)
            direct_session = await stack.enter_async_context(aiohttp.ClientSession(
                headers=dict(SEARCH_HEADERS),
                timeout=timeout,
                connector=direct_connector,
                trust_env=False,
            ))

        @asynccontextmanager
        async def _timed_get(sess, url, info, **kwargs):
            """One GET with transfer timing recorded into ``info``."""
            started = time.monotonic()
            try:
                async with sess.get(url, **kwargs) as resp:
                    yield resp
            finally:
                info["transfer_ms"] += (time.monotonic() - started) * 1000.0

        async def _dl_one(url: str) -> Tuple[str, Optional[Path], str]:
            info: Dict[str, Any] = {
                "url": url,
                "host": urlparse(str(url or "")).netloc.lower(),
                "route": "proxy",
                "slot_wait_ms": 0.0,
                "transfer_ms": 0.0,
                "bytes": 0,
            }
            try:
                check_job_active()
                result = await _dl_one_inner(url, info)
                if info["transfer_ms"] > 0:
                    record_activity("web_download")
            except Exception as exc:
                result = (url, None, str(exc)[:80])
            if stats is not None:
                record = dict(info)
                record["landed"] = bool(result[1] is not None)
                record["error"] = str(result[2] or "")
                record["slot_wait_ms"] = round(float(record["slot_wait_ms"]), 1)
                record["transfer_ms"] = round(float(record["transfer_ms"]), 1)
                stats.append(record)
            return result

        async def _dl_one_inner(url: str, info: Dict[str, Any]) -> Tuple[str, Optional[Path], str]:
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
                host = urlparse(url).netloc.lower()
                # Nov 25 direct-only (see _download_route_order): every
                # attempt downloads direct; there is no proxy leg. A
                # transport failure (timeout / connection error) retries
                # direct with a fresh budget; a 429 retries direct too.
                routes = _download_route_order(host, direct_retry_enabled)
                retry_statuses = {408, 425, 429, 500, 502, 503, 504}
                if batch_deadline <= time.monotonic():
                    return url, None, "web download batch budget exhausted"
                for attempt in range(max_attempts):
                    # Fresh per-attempt budget (Nov 22, test 23): the old
                    # code computed one deadline for the whole URL, so the
                    # retry inherited whatever was left of the first
                    # attempt's budget — usually ~0s — and died instantly.
                    # Each attempt now gets a full timeout_seconds, capped
                    # by the shared batch deadline so a slow URL still
                    # cannot hold the batch past its wall.
                    deadline = min(batch_deadline, time.monotonic() + timeout_seconds)
                    if deadline <= time.monotonic():
                        return url, None, "web download batch budget exhausted"
                    slot_started = time.monotonic()
                    try:
                        # Round 13: in wait mode the slot wait is uncapped
                        # (an infinite deadline tells slot() to wait until
                        # a slot frees); the per-attempt deadline still
                        # bounds the transfer below.
                        slot_deadline = (
                            float("inf") if wait_until_complete else deadline
                        )
                        async with _DOWNLOAD_LIMITER.slot(host, slot_deadline):
                            info["slot_wait_ms"] += (time.monotonic() - slot_started) * 1000.0
                            # Fresh transfer budget once the slot is held
                            # (test 25): the attempt deadline above bounds
                            # the WAIT for a slot, but the transfer used
                            # to inherit whatever was left of it — a
                            # download that waited 55s for one of the 12
                            # shared slots got ~5s to transfer an image
                            # that needed ~8s, and died as "timeout"
                            # (40/86 attempts in test 25). The transfer
                            # now gets a full timeout_seconds of its own,
                            # capped only by the shared batch wall, so a
                            # slow hour costs slot time, not the image.
                            transfer_deadline = min(
                                batch_deadline,
                                time.monotonic() + timeout_seconds,
                            )
                            route = routes[min(attempt, len(routes) - 1)]
                            session_for_attempt = (
                                direct_session
                                if (route == "direct" and direct_session is not None)
                                else session
                            )
                            info["route"] = route
                            async with _timed_get(
                                session_for_attempt,
                                url, info, headers=(headers_by_url or {}).get(url),
                                timeout=aiohttp.ClientTimeout(
                                    total=max(
                                        0.01,
                                        transfer_deadline - time.monotonic(),
                                    ),
                                    # Round 8 (test 35): the session's
                                    # connect=5.0 is overridden by this
                                    # per-request timeout, so pass the
                                    # connect limit here too — otherwise a
                                    # hung connect burns the whole total
                                    # budget before the other route is tried.
                                    connect=_download_connect_timeout_seconds(),
                                ),
                            ) as resp:
                                if resp.status != 200:
                                    last_error = f"HTTP {resp.status}"
                                    if resp.status == 429:
                                        if attempt < max_attempts - 1:
                                            # Nov 25 direct-only: a 429 is
                                            # retried direct; there is no other
                                            # route. The defer below applies
                                            # only once attempts are exhausted.
                                            pass
                                        else:
                                            try:
                                                retry_after = max(1, min(60, int(resp.headers.get("Retry-After", "30"))))
                                            except ValueError:
                                                retry_after = 30
                                            _DOWNLOAD_LIMITER.defer(host, retry_after)
                                            return url, None, last_error
                                    elif resp.status == 403:
                                        # Refusal, not a blip: open the circuit
                                        # at once so the scene stops burning
                                        # candidates against a dead host. Unlike
                                        # 429 (a per-IP rate limit — the other
                                        # route's egress IP may not be limited),
                                        # a 403 is the host refusing this client
                                        # and the other route answers the same
                                        # way. The denied-origin test pins this
                                        # to exactly one hit per host.
                                        _DOWNLOAD_LIMITER.open_circuit(host)
                                        return url, None, last_error
                                    elif resp.status not in retry_statuses:
                                        _DOWNLOAD_LIMITER.record_outcome(host, failed=False)
                                        return url, None, last_error
                                    else:
                                        _DOWNLOAD_LIMITER.record_outcome(host, failed=False)
                                else:
                                    ct = resp.headers.get("Content-Type", "").lower()
                                    if ct and "image" not in ct and "octet" not in ct:
                                        _DOWNLOAD_LIMITER.record_outcome(host, failed=False)
                                        return url, None, f"not image: {ct}"
                                    content_length = resp.headers.get("Content-Length")
                                    try:
                                        declared_length = int(content_length) if content_length else 0
                                    except (TypeError, ValueError):
                                        declared_length = 0
                                    if declared_length > max_bytes:
                                        _DOWNLOAD_LIMITER.record_outcome(host, failed=False)
                                        return url, None, f"too large ({declared_length}B > {max_bytes}B)"

                                    chunks = []
                                    total_bytes = 0
                                    # Stream in bounded chunks rather than using
                                    # resp.read(), which otherwise allocates the
                                    # complete body before the cap can be checked.
                                    async for chunk in resp.content.iter_chunked(64 * 1024):
                                        total_bytes += len(chunk)
                                        if total_bytes > max_bytes:
                                            _DOWNLOAD_LIMITER.record_outcome(host, failed=False)
                                            return url, None, f"too large ({total_bytes}B > {max_bytes}B)"
                                        chunks.append(chunk)
                                    _DOWNLOAD_LIMITER.record_outcome(host, failed=False)
                                    data = b"".join(chunks)
                                    if len(data) < min_size:
                                        return url, None, f"too small ({len(data)}B)"
                                    info["bytes"] = len(data)
                                    path.write_bytes(data)
                                    return url, path, ""
                    except HostCircuitOpen as exc:
                        info["slot_wait_ms"] += (time.monotonic() - slot_started) * 1000.0
                        return url, None, str(exc)
                    except DownloadSlotTimeout as exc:
                        # Round 6 (test 33): a slot wait is contention, not
                        # a transport failure — no transfer budget was spent,
                        # so fall through to the next attempt instead of
                        # killing the URL. Live probe: with 16-18 s transfers
                        # holding the 3/host slots, URLs 4-6 on a host wait
                        # out the 15 s cap while siblings finish at ~18 s;
                        # a second wait acquires the freed slot and lands.
                        # The attempt budget (2) and the batch deadline
                        # still bound the total wait. A circuit-open host
                        # above still returns at once: its cooldown is 60 s.
                        info["slot_wait_ms"] += (time.monotonic() - slot_started) * 1000.0
                        last_error = str(exc)
                    except asyncio.TimeoutError:
                        # A timeout is shared slow egress, not a host
                        # refusal (test 23: 50 timeouts across every host
                        # at once; 3 of them opened the circuit and 56
                        # further attempts then died instantly on it).  Do
                        # NOT record it as a circuit failure: the circuit
                        # is for refusals (403 opens it at once, 429 defers
                        # 30s).  A black-holed host is still bounded by the
                        # per-host slot cap and the per-URL/batch deadlines.
                        # The next attempt tries the next route in the
                        # per-host order (round 6), with a fresh budget.
                        last_error = "timeout"
                    except aiohttp.ClientConnectionError as exc:
                        # Transport failure on this route: the next attempt
                        # tries the next route in the per-host order.
                        last_error = f"connection error: {exc}"[:80]
                    except Exception as e:
                        last_error = str(e)[:80]
                    if attempt < max_attempts - 1:
                        # Gate the retry on the BATCH deadline, not the
                        # per-attempt one: the attempt just spent its whole
                        # budget (that is why it failed), so gating on it
                        # would break out before the retry ever runs.
                        remaining = batch_deadline - time.monotonic()
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
    direct_retry: Optional[bool] = None,
    stats: Optional[List[Dict[str, Any]]] = None,
    wait_until_complete: bool = False,
) -> List[Tuple[str, Optional[Path], str]]:
    """Download multiple image URLs via aiohttp in parallel.

    ``wait_until_complete`` (round 13): when on, the batch wall and
    the slot-wait cap are removed — every URL runs to resolution on
    its per-attempt socket budget. The per-host throttle is untouched.
    """
    return asyncio.run(_download_bulk_async(
        urls, output_dir, max_concurrent, timeout_seconds,
        headers_by_url=headers_by_url, max_bytes=max_bytes,
        direct_retry=direct_retry, stats=stats,
        wait_until_complete=wait_until_complete,
    ))
