"""Internal adapter for the parallel multi-engine scraper.

This module is intentionally self contained.  It ports the useful discovery
logic from ``Documents/xyz/Scrappers`` into Vidnets' existing resolver so the
pipeline gets one result contract, one timeout budget, and one set of runtime
logs.  Discovery is deliberately unauthenticated: no cookies, browser state,
or account credentials are used.

The manager fans out across engines at the same time and returns partial
success when one engine is slow or challenged.  A bounded worker pool and
per-engine timeout keep a bad endpoint from holding the media stage hostage.
Pixel download, metadata safety, and vision verification remain the
resolver's responsibility.
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import json
import subprocess
import sys
from pathlib import Path
import logging
import os
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from ..job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from ..media_distinctness import (
    dominant_source as _distinct_dominant_source,
    needs_more_sources as _distinct_needs_more_sources,
    source_host as _distinct_source_host,
)
from dataclasses import dataclass, field, replace as _dc_replace
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import requests

try:  # Optional parser; regex fallback keeps the adapter lightweight.
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover - optional in minimal deployments
    BeautifulSoup = None

try:  # Optional DDG scraper dependency.
    from ddgs import DDGS  # type: ignore
except Exception:  # pragma: no cover - optional in minimal deployments
    DDGS = None

from .source_broker import WEB_ENGINE_PRIORITY


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class UniversalImageResult:
    """Normalized result copied from the standalone scraper's ImageResult."""

    title: str
    image_url: str
    thumbnail_url: str = ""
    source_url: str = ""
    source_engine: str = ""
    width: int = 0
    height: int = 0
    description: str = ""
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class UniversalEngineStat:
    engine: str
    count: int
    duration_ms: int
    success: bool
    error: str = ""


@dataclass(frozen=True)
class UniversalSearchResponse:
    query: str
    total: int
    results: List[UniversalImageResult]
    stats: List[UniversalEngineStat]
    # Source-diversity summary for the merged rows (GTA VI proof job:
    # every kept web image came from Pinterest while the engines had
    # returned other publishers). ``dominant_host`` / ``dominant_share``
    # and ``needs_more_sources`` let the resolver decide to keep
    # searching instead of settling for a single-source pool. Defaulted
    # so existing positional constructions keep working.
    source_diversity: Dict[str, object] = field(default_factory=dict)


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_DIRECT_SCHEMES = ("http://", "https://")
_BING_META_RE = re.compile(
    r"<a[^>]+class=[\"'][^\"']*iusc[^\"']*[\"'][^>]+m=[\"']([^\"']+)[\"']",
    re.IGNORECASE | re.DOTALL,
)
_BING_META_RE_REVERSE = re.compile(
    r"<a[^>]+m=[\"']([^\"']+)[\"'][^>]+class=[\"'][^\"']*iusc[^\"']*[\"']",
    re.IGNORECASE | re.DOTALL,
)
_Y_IMG_RE = re.compile(r'\\?"img_href\\?"\s*:\s*\\?"(https?://[^"\\]+)', re.I)
_Y_THUMB_RE = re.compile(r'\\?"image\\?"\s*:\s*\\?"(//[^"\\]+|https?://[^"\\]+)', re.I)
_Y_TITLE_RE = re.compile(r'\\?"(?:snippet"\s*:\s*\{[^{}]{0,600}?\\?"title|title)\\?"\s*:\s*\\?"([^"\\]+)', re.I)
_URL_RE = re.compile(r"https?://[^\"'<>\\\s]+", re.I)


# ── Per-host search throttling (S7) ──────────────────────────────────────
# The manager fans engines out in parallel and several scene workers can
# run batches at once, so one host (e.g. yandex.com) can see many
# simultaneous requests — enough to trigger SmartCaptcha / 429. Bound it:
# at most N concurrent requests per host, at least ``delay`` between
# request starts to the same host, and exponential backoff on 429. A 403 is
# definitive for the current egress and is retired by the per-run engine gate.
# The state is module-global, so it bounds ALL managers and scene workers
# collectively, not just one batch. This reuses the download limiter's
# philosophy (per-host caps + backoff) for the search path, which
# previously had no throttle at all.
def _universal_per_host() -> int:
    """Max concurrent search fetches per (host, route).

    Round 6 (test 33): the old default of 2 turned 13 scene queries
    into a 2-slot queue — the tail call waited ~40 s for a slot (repro
    repro-yandex-queue.py: 13 concurrent calls, wall 41.3 s, tail 39.6 s).
    4 concurrent fetches with the 0.5 s min-delay between starts is
    still polite to the host (and the user ordered the sources kept safe:
    do not raise this above 4 without measuring). Env override
    VIDZEO_UNIVERSAL_PER_HOST still wins (clamped 1-8).
    """
    try:
        return max(1, min(8, int(os.environ.get("VIDZEO_UNIVERSAL_PER_HOST", "4") or 4)))
    except (TypeError, ValueError):
        return 4


def _universal_socket_timeout_seconds(timeout) -> float:
    """Full socket timeout (seconds) for one throttled fetch.

    Round 6 (test 33): once a throttle slot is held, the socket gets a
    FULL timeout — never whatever is left of the queue budget. The floor
    (default 5 s, env VIDZEO_UNIVERSAL_SOCKET_TIMEOUT_FLOOR_SECONDS)
    protects callers that pass a tiny timeout: a socket handed 0.7 s
    against a host that needs 5-7 s fails as a ReadTimeout and looks
    like a slow host, when the real fault was the queue.
    """
    try:
        floor = max(
            0.5,
            min(
                30.0,
                float(
                    os.environ.get(
                        "VIDZEO_UNIVERSAL_SOCKET_TIMEOUT_FLOOR_SECONDS", "5"
                    )
                    or 5
                ),
            ),
        )
    except (TypeError, ValueError):
        floor = 5.0
    try:
        value = float(timeout or 15)
    except (TypeError, ValueError):
        value = 15.0
    return max(floor, value)


def _universal_min_delay() -> float:
    try:
        return max(0.0, min(5.0, float(os.environ.get("VIDZEO_UNIVERSAL_MIN_DELAY_SECONDS", "0.5") or 0.5)))
    except (TypeError, ValueError):
        return 0.5


def _universal_retry_count() -> int:
    try:
        return max(0, min(3, int(os.environ.get("VIDZEO_UNIVERSAL_RETRIES", "2") or 2)))
    except (TypeError, ValueError):
        return 2


def _universal_retry_delay(attempt: int) -> float:
    try:
        base = max(0.1, min(3.0, float(os.environ.get("VIDZEO_UNIVERSAL_RETRY_BASE", "1.0") or 1.0)))
    except (TypeError, ValueError):
        base = 1.0
    return min(8.0, base * (2 ** attempt))


def _universal_fetch_timeout_seconds() -> float:
    """Per-route socket timeout for one engine search-page fetch.

    Round 5 (Nov 19): the 15s cap killed every engine in a slow hour —
    prep-4 log (144x ReadTimeout at 15.0s, both routes) and the 01:01 UTC
    curl check (Bing search direct 22.4s, proxy >40s; Yandex images
    proxy 21.8s). Search pages take 20-40s to answer when the egress is
    slow, while homepages answer in 2-3s — so the homepage check can
    never predict this. 30s lets the direct route (22s) and the proxy
    Yandex fetch (21.8s) complete; the batch wall and the route fallback
    still bound a dead route (2 routes x 30s max per engine call).

    Nov 22 (slow-network adaptation): when the manager has recorded
    consecutive all-timeout batches, _run_engine sets a thread-local
    override here, so the fetch timeout grows by the same factor as the
    batch deadline instead of cutting the engine at the same 30s wall
    the batch just died on. The override is capped at the same 120s.
    """
    override = getattr(_fetch_timeout_override_local, "seconds", None)
    if override is not None:
        try:
            return max(5.0, min(120.0, float(override)))
        except (TypeError, ValueError):
            pass
    try:
        return max(
            5.0,
            min(
                120.0,
                float(
                    os.environ.get("VIDZEO_UNIVERSAL_FETCH_TIMEOUT_SECONDS", "30")
                    or 30
                ),
            ),
        )
    except (TypeError, ValueError):
        return 30.0


def _universal_engine_stagger_seconds() -> float:
    """Extra wall-clock per engine, in priority order.

    Wright (Nov 16): all four engines died in ~20s because they shared one
    batch wall — the slowest engine's timeout was every engine's timeout.
    Each engine after the first now gets one more stagger step, so a slow
    fallback engine is not cut at the same instant as the first engine.
    """
    try:
        return max(
            0.0,
            min(
                15.0,
                float(
                    os.environ.get("VIDZEO_UNIVERSAL_ENGINE_STAGGER_SECONDS", "5")
                    or 5
                ),
            ),
        )
    except (TypeError, ValueError):
        return 5.0


def _universal_empty_retry_backoff_seconds() -> float:
    """Backoff before the single retry of an all-empty, timed-out batch."""
    try:
        return max(
            0.0,
            min(
                10.0,
                float(
                    os.environ.get(
                        "VIDZEO_UNIVERSAL_EMPTY_RETRY_BACKOFF_SECONDS", "2"
                    )
                    or 2
                ),
            ),
        )
    except (TypeError, ValueError):
        return 2.0


def _empty_streak_key(query: str) -> str:
    """Normalised key for the per-query empty-streak counter.

    Test 29: the counter was a single global int, so one query's
    failures zeroed 21 other queries' retries and rescues. Queries
    that differ only in case or whitespace are the same query.
    """
    return re.sub(r"\s+", " ", str(query or "")).strip().lower()


def _universal_empty_streak_cap() -> int:
    """Consecutive all-empty stalled batches before retries stop (§2.1).

    Default 3, clamp 1–6 (env VIDZEO_UNIVERSAL_EMPTY_STREAK_CAP).
    When the counter has already reached the cap, the next batch's
    retry waves are skipped and its rescue attempts are 0 — retries
    only, never the first attempt of a batch. Any batch returning
    >= 1 row resets the counter.
    """
    try:
        return max(
            1,
            min(
                6,
                int(
                    os.environ.get(
                        "VIDZEO_UNIVERSAL_EMPTY_STREAK_CAP", "3"
                    )
                    or 3
                ),
            ),
        )
    except (TypeError, ValueError):
        return 3


def _universal_empty_retry_waves() -> int:
    """Retry waves after an all-empty timed-out batch (Nov 22).

    A single slow-hour timeout must not zero the lane: the timed-out
    engines get up to this many more waves, alternating route, each with
    backoff. Bounded to 1-2 waves (2-3 attempts total): more waves would
    just re-pay the same wall while the resolver's own retry/rescue is
    the outer bound. Concurrency is NOT raised — retries reuse the same
    throttled path, they just try again after a backoff.
    """
    try:
        return max(
            1,
            min(
                2,
                int(
                    os.environ.get(
                        "VIDZEO_UNIVERSAL_EMPTY_RETRY_WAVES", "2"
                    )
                    or 2
                ),
            ),
        )
    except (TypeError, ValueError):
        return 2


def _universal_slow_network_step() -> float:
    """Timeout multiplier per consecutive all-timeout batch (Nov 22).

    Job d1ece590: Yandex, Bing and Flickr ALL timed out together in a slow
    egress hour — a shared-network signal, not a per-engine failure. Each
    consecutive all-timeout batch multiplies the effective engine and fetch
    timeouts by this much, up to _universal_slow_network_max_factor().
    """
    try:
        return max(
            1.0,
            min(
                2.0,
                float(
                    os.environ.get("VIDZEO_UNIVERSAL_SLOW_NETWORK_STEP", "1.5")
                    or 1.5
                ),
            ),
        )
    except (TypeError, ValueError):
        return 1.5


def _universal_slow_network_max_factor() -> float:
    """Cap on the slow-network timeout multiplier (Nov 22).

    Capped at 1.5x (muse-search design §2.4): the old 2.0x cap grew
    engine timeouts to 120–180 s in test 30 and deepened slot
    saturation. Operators can still raise it via
    VIDZEO_UNIVERSAL_SLOW_NETWORK_MAX_FACTOR (clamped 1–4x).
    """
    try:
        return max(
            1.0,
            min(
                4.0,
                float(
                    os.environ.get(
                        "VIDZEO_UNIVERSAL_SLOW_NETWORK_MAX_FACTOR", "1.5"
                    )
                    or 1.5
                ),
            ),
        )
    except (TypeError, ValueError):
        return 1.5


# Thread-local override for the per-route fetch timeout, set by
# UniversalScraperManager._run_engine around each engine call so the
# slow-network adaptation reaches the pool worker threads (the same shape
# as the explicit direct= parameter: a caller-thread flag would never
# reach them). _universal_fetch_timeout_seconds() reads it first, and it
# is cleared when the call returns so a later call on the same reused
# worker thread falls back to the env default.
_fetch_timeout_override_local = threading.local()


_universal_host_state: Dict[str, Dict[str, object]] = {}
_universal_host_guard = threading.Lock()


def _universal_host_slot(host: str, route: str = "proxy") -> Dict[str, object]:
    # The throttle namespace is per (host, route): a direct retry must not
    # queue behind the abandoned proxied attempt's held slots (Nov 18:
    # "throttled request to yandex.com exceeded its 15s budget waiting for
    # a host slot" x7 in the Panama log). Each route keeps its own
    # per-host cap (default 4, see _universal_per_host) and 0.5s min
    # delay, so the retry still respects the per-host limit instead of
    # bypassing it.
    key = f"{host}|{route}"
    with _universal_host_guard:
        state = _universal_host_state.get(key)
        if state is None:
            state = {
                "semaphore": threading.Semaphore(_universal_per_host()),
                "last_start": 0.0,
                "lock": threading.Lock(),
            }
            _universal_host_state[key] = state
        return state


# ── Proxy bypass for the web retry (Nov 18, Moon 20s test) ───────────────
# The egress proxy is runtime-managed (https_proxy=http://hatch-egress-proxy:3128,
# re-injected on every shell/restart) and cannot be removed system-wide.
# When a web search fails through the proxy (read timeouts, bot-gates), the
# retry goes DIRECT: direct egress works and is fast (Bing 200 in ~1.5s,
# Yandex 200 in ~1.3s, verified live Nov 18).
# The bypass is per-request, not global: the retry passes direct=True
# explicitly down to _throttled_get, and the request is issued on a
# trust_env=False Session (see _new_route_session) so no env proxy is
# applied at all. The proxies={http: None, https: None} kwarg is still
# passed as well — it is the route signal fake/test clients key on, and
# belt-and-braces on a trust_env=False session — but it is NOT the bypass
# mechanism: on a real Session it only works by an accident of requests'
# internals (merge_environment_settings blocks the env proxy with the
# None keys via setdefault BEFORE merge_setting strips them; a future
# requests version, or a Session.send caller, would re-apply the env
# proxy). Worker F (Nov 18) verified live on requests 2.34.2: the kwarg
# alone DID return 200 under a dead proxy, 3/3 runs — so the earlier
# "merge_setting strips it, proxy re-applied" diagnosis was wrong, and
# the 0-row forced-failure proof did not reproduce in that hour (58 rows
# baseline). The trust_env=False session makes the direct route correct
# by construction instead of by that ordering accident, whatever the
# requests version does next. (The original thread-local flag could never
# reach the pool worker threads; see above.)
# Nov 18 (web-resilience build): the original bypass was a thread-local
# flag set on the CALLER thread — a NO-OP in production, because HTTP runs
# on ThreadPoolExecutor worker threads where the flag is always False
# (repro /tmp/repro_bypass.py). The production path now passes an explicit
# ``direct=`` parameter down search() -> _run_engine() -> engine.search()
# -> _throttled_get(), so the route is chosen on the thread that issues
# the request. ``web_bypass_proxy()`` survives only as a compatibility
# shim for direct callers: it is now a process-wide refcount (not
# thread-local), so a caller-thread flag IS visible on pool threads. The
# window is bounded (one retry leg) and direct is a legitimate route, so
# a concurrent worker briefly going direct is safe — but production code
# must pass direct= explicitly and never rely on this shim.
_WEB_BYPASS_COUNT = 0
_WEB_BYPASS_LOCK = threading.Lock()


def _web_bypass_proxy_enabled() -> bool:
    with _WEB_BYPASS_LOCK:
        return _WEB_BYPASS_COUNT > 0


@contextlib.contextmanager
def web_bypass_proxy():
    """Run the enclosed web requests DIRECT, bypassing the egress proxy.

    Legacy shim (see above): the production retry passes ``direct=True``
    explicitly instead. A request inside the context is issued on the
    trust_env=False direct session (``_fetch`` folds the shim into its
    preferred route), with proxies={http: None, https: None} passed as
    well — the session is the bypass, the kwarg is only a signal.
    """
    global _WEB_BYPASS_COUNT
    with _WEB_BYPASS_LOCK:
        _WEB_BYPASS_COUNT += 1
    try:
        yield
    finally:
        with _WEB_BYPASS_LOCK:
            _WEB_BYPASS_COUNT = max(0, _WEB_BYPASS_COUNT - 1)


# ── Per-engine, per-route health (Nov 18, web-resilience) ───────────────
# Direct is NOT universally better: Flickr via direct egress timed out
# (20s) while via proxy it answered in 2.6s (live Nov 18). The route is
# therefore chosen per engine, not blanket: try the preferred route
# first, fall back to the other route on a transport error. An HTTP
# status (including 403/429/5xx) is an answer, not a route failure, so
# it never triggers a route switch. A route that fails transport 3x in
# a row is skipped fast for 120s, then re-probed.
_ROUTE_FAILURE_THRESHOLD = 3
_ROUTE_SKIP_SECONDS = 120.0
_route_state: Dict[Tuple[str, str], Dict[str, float]] = {}
_route_state_lock = threading.Lock()

# Single-request proxy override: proxies={http: None, https: None} makes
# one requests call skip the env proxy vars (direct egress) — on requests
# 2.34.2, verified live Nov 18 (Worker F), and retained here as the route
# signal fake/test clients key on plus belt-and-braces. It is NOT the
# bypass mechanism for real sessions (see _new_route_session): the
# trust_env=False session is.
_DIRECT_PROXIES = {"http": None, "https": None}


def _new_route_session(*, direct: bool) -> "requests.Session":
    """A requests.Session for one route (proxy or direct).

    Worker F (Nov 18): the direct route MUST go through a Session with
    ``trust_env=False``. A default Session reads the proxy env vars on
    every request, so a direct attempt on it is one requests-internals
    change away from silently going through the (possibly dead) proxy:
    the proxies={http: None, https: None} kwarg only bypasses because
    merge_environment_settings blocks the env proxy via setdefault
    before merge_setting strips the None keys — an ordering accident,
    not a contract. With trust_env=False the session never consults the
    proxy env vars at all (verified: 200 under a dead proxy while a
    trust_env=True session raises ProxyError to 127.0.0.1:9), so the
    direct route holds whatever requests does next. The proxy route
    keeps the default trust_env=True session so the egress proxy keeps
    working. ``session.proxies`` is emptied on the direct session as
    belt-and-braces so no session-level proxy can leak in either.
    """
    session = requests.Session()
    session.trust_env = not direct
    if direct:
        session.proxies = {}
    return session


def _route_proxies(direct: bool) -> Optional[Dict[str, object]]:
    """proxies kwarg for a requests call on the given route (None = env proxy).

    Route signal only: fake/test clients and _throttled_get key the
    direct route on this kwarg. The real bypass is the trust_env=False
    session from _new_route_session, not this kwarg.
    """
    return dict(_DIRECT_PROXIES) if direct else None


def _route_name(direct: bool) -> str:
    return "direct" if direct else "proxy"


# Engines whose hosts answer direct faster than via the egress proxy
# (live route table, Nov 23 round 6): yandex.com direct 3.9 s vs proxy
# 7.4 s; bing.com 2.6 s vs 7.1 s; flickr.com 6.0 s vs 11.3 s;
# api.openverse.org direct 9.9 s vs proxy 9.6 s (a wash — direct is
# fine). The Wikimedia API host is NOT here: direct to
# commons.wikimedia.org is dead, it stays proxy-only (muse_search).
# Route health still adapts: a route failing transport 3x is skipped
# for 120 s, so if direct degrades the proxy takes over by itself.
_DIRECT_FIRST_ENGINES = frozenset({"yandex", "bing", "flickr", "openverse"})


def _engine_prefers_direct(engine_name: str) -> bool:
    """True when this engine's host is proven direct-capable (see above)."""
    return str(engine_name or "").strip().lower() in _DIRECT_FIRST_ENGINES


def _route_order(engine: str, prefer_direct: bool) -> List[str]:
    """Routes to try for one engine fetch, best first, dead routes skipped."""
    order = ["direct", "proxy"] if prefer_direct else ["proxy", "direct"]
    now = time.monotonic()
    live: List[str] = []
    for route in order:
        with _route_state_lock:
            state = _route_state.get((engine, route)) or {}
            failures = float(state.get("failures", 0) or 0)
            last_fail = float(state.get("last_fail", 0) or 0)
        if failures >= _ROUTE_FAILURE_THRESHOLD and now - last_fail < _ROUTE_SKIP_SECONDS:
            LOGGER.debug(
                "[universal] %s: skipping dead route=%s "
                "(%.0f consecutive transport failures)",
                engine, route, failures,
            )
            continue
        live.append(route)
    # Never return empty: if both routes look dead, re-probe the preferred
    # one instead of failing without trying.
    return live or order[:1]


def _note_route_result(engine: str, route: str, ok: bool) -> None:
    with _route_state_lock:
        state = _route_state.setdefault(
            (engine, route), {"failures": 0.0, "last_fail": 0.0, "last_ok": 0.0}
        )
        if ok:
            state["failures"] = 0.0
            state["last_ok"] = time.monotonic()
        else:
            state["failures"] = float(state.get("failures", 0) or 0) + 1.0
            state["last_fail"] = time.monotonic()


def _timeout_kind(exc: BaseException) -> str:
    """Classify a transport exception as queue-wait vs socket-timeout.

    Round 6: ANY throttle-budget failure is a queue-timeout. The three
    queue messages ("budget before starting", "budget in queue",
    "budget waiting for a host slot") all say "budget"; the old check
    also required "queue" or "host slot", so "budget before starting"
    was misclassified as a socket-timeout and struck the route. A
    socket ReadTimeout never mentions "budget", so matching on it alone
    is safe.
    """
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "budget" in text:
        return "queue-timeout"
    if "timeout" in name:
        return "socket-timeout"
    return "transport-error"


def _throttled_get(client, url: str, *, headers, timeout, direct: bool = False):
    """GET through the per-host throttle, with bounded 429 backoff retries.

    ``client`` is any object with ``.get(url, headers=..., timeout=...)``
    (a requests.Session or the requests module). Returns the response-like
    object (status_code + text/json).
    ``direct=True`` issues the call on a trust_env=False session (the
    caller — ``_Engine._fetch`` via ``_get_client(direct=True)`` — must
    supply one; see ``_new_route_session``) so no env proxy is applied,
    and also passes ``proxies={http: None, https: None}`` as the route
    signal fake/test clients key on. The proxies kwarg alone is NOT the
    bypass for a real Session (Worker F, Nov 18: it only works on
    requests 2.34.2 by a setdefault-before-strip ordering accident), so
    a real trust_env=True Session handed in with direct=True is logged
    at debug level — the call still goes out, but the bypass is not
    guaranteed for it. The legacy ``web_bypass_proxy()`` shim has the
    same effect. The throttle slot is per (host, route), so a direct
    retry does not queue behind the abandoned proxied attempt's held
    slots. Clients that do not accept a proxies override are retried
    without it.
    Only 429 is retried with exponential backoff. A 403 is returned
    immediately so the manager can retire that engine for this run rather
    than repeat a refusal for every scene. Other non-200 responses are
    returned as-is and recorded as per-engine failures.

    Round 6 (test 33): the queue wait and the socket call have SEPARATE
    budgets. The old code counted queue time INSIDE ``timeout`` (GTA fix,
    Nov 16) and handed the socket whatever was left:
    ``request_timeout = max(0.5, min(timeout, _remaining()))``. A call
    that queued 44 s of a 45 s budget got a 0.7 s socket timeout against
    a host that needs 5-7 s — test 33's log proves it ("ReadTimeout read
    timeout=0.707s"), and the 45 s failure was queue-wait, not a slow
    host. Now the queue may wait up to ``timeout`` for a slot (failing
    fast as kind=queue-timeout past that), and once the slot is held the
    socket gets a FULL timeout (see
    ``_universal_socket_timeout_seconds``), never leftover crumbs. The
    caller's batch wall still bounds the whole call; each 429 retry
    re-queues under its own fresh queue budget. Queue-wait and
    socket-timeout failures are logged separately (kind=queue-timeout vs
    kind=socket-timeout) so the next outage is diagnosable from the log
    alone.
    """
    host = (urlparse(url).netloc or "").lower()
    route = _route_name(bool(direct) or _web_bypass_proxy_enabled())
    if (
        route == "direct"
        and isinstance(client, requests.Session)
        and getattr(client, "trust_env", False)
    ):
        # The bypass is the session, not the kwarg (see _new_route_session):
        # a trust_env=True Session on the direct route depends on requests'
        # merge ordering and may silently use the env proxy. Production
        # engines always pass a trust_env=False session here; log it so a
        # future caller regression is diagnosable instead of silent.
        LOGGER.debug(
            "[universal] throttled GET route=direct host=%s: client is a "
            "trust_env=True Session; proxies kwarg is the only bypass",
            host,
        )
    state = _universal_host_slot(host, route)
    semaphore = state["semaphore"]
    lock = state["lock"]
    try:
        budget = max(0.5, float(timeout or 15))
    except (TypeError, ValueError):
        budget = 15.0
    # Round 6: the socket timeout is a FULL budget of its own, computed
    # once here — never the leftover of the queue wait (see docstring).
    socket_timeout = _universal_socket_timeout_seconds(timeout)

    response = None
    for attempt in range(_universal_retry_count() + 1):
        # Each attempt gets its own fresh queue budget: a 429 retry
        # re-queues rather than inheriting whatever the first attempt's
        # queue wait left behind.
        attempt_started = time.monotonic()

        def _remaining() -> float:
            return budget - (time.monotonic() - attempt_started)

        if _remaining() <= 0:
            LOGGER.debug(
                "[universal] throttled GET route=%s host=%s kind=queue-timeout "
                "error=budget exhausted before start",
                route, host,
            )
            raise TimeoutError(
                f"throttled request to {host} exceeded its {budget:.0f}s "
                "budget before starting"
            )
        with lock:
            wait = _universal_min_delay() - (time.monotonic() - float(state["last_start"]))
            if wait > 0:
                if wait >= _remaining():
                    LOGGER.debug(
                        "[universal] throttled GET route=%s host=%s "
                        "kind=queue-timeout error=budget in queue",
                        route, host,
                    )
                    raise TimeoutError(
                        f"throttled request to {host} exceeded its "
                        f"{budget:.0f}s budget in queue"
                    )
                time.sleep(wait)
            state["last_start"] = time.monotonic()
        if _remaining() <= 0:
            LOGGER.debug(
                "[universal] throttled GET route=%s host=%s kind=queue-timeout "
                "error=budget in queue",
                route, host,
            )
            raise TimeoutError(
                f"throttled request to {host} exceeded its {budget:.0f}s "
                "budget in queue"
            )
        acquired = semaphore.acquire(timeout=_remaining())
        if not acquired:
            LOGGER.debug(
                "[universal] throttled GET route=%s host=%s "
                "kind=queue-timeout error=budget waiting for a host slot",
                route, host,
            )
            raise TimeoutError(
                f"throttled request to {host} exceeded its {budget:.0f}s "
                "budget waiting for a host slot"
            )
        acquired_at = time.monotonic()
        try:
            request_timeout = socket_timeout
            # Proxy bypass (Nov 18 retry, fixed Nov 18 web-resilience):
            # the route is an explicit parameter now, not a thread-local
            # flag — the worker thread that issues the request sees it.
            # Clients that do not accept a proxies override raise
            # TypeError immediately; retry them without it.
            if route == "direct":
                try:
                    response = client.get(
                        url, headers=headers, timeout=request_timeout,
                        proxies={"http": None, "https": None},
                    )
                except TypeError:
                    response = client.get(url, headers=headers, timeout=request_timeout)
            else:
                response = client.get(url, headers=headers, timeout=request_timeout)
        except Exception as exc:
            queue_ms = int((acquired_at - attempt_started) * 1000)
            socket_ms = int((time.monotonic() - acquired_at) * 1000)
            LOGGER.debug(
                "[universal] throttled GET route=%s host=%s kind=%s "
                "queue_ms=%d socket_ms=%d error=%s: %s",
                route, host, _timeout_kind(exc), queue_ms, socket_ms,
                type(exc).__name__, str(exc)[:120],
            )
            raise
        finally:
            semaphore.release()
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 429 or attempt >= _universal_retry_count():
            return response
        delay = min(_universal_retry_delay(attempt), max(0.0, _remaining()))
        if delay <= 0:
            return response
        time.sleep(delay)
    return response


def _title_is_query_echo(title: object, query: str) -> bool:
    """Identify provider placeholders that repeat the retrieval query."""
    raw_title = re.sub(r"\s+", " ", str(title or "")).strip()
    raw_query = re.sub(r"\s+", " ", str(query or "")).strip()
    if not raw_title or not raw_query:
        return not raw_title
    norm_title = re.sub(r"[^a-z0-9]+", " ", raw_title.casefold()).strip()
    norm_query = re.sub(r"[^a-z0-9]+", " ", raw_query.casefold()).strip()
    if norm_title == norm_query:
        return True
    if norm_title.startswith(norm_query + " "):
        suffix = norm_title[len(norm_query):].strip()
        # Yandex and a few SERP mirrors expose the page shell as every card's
        # title (``<query>: browse and download images â€” Yandex``).  It is a
        # provider label, never an image caption, so do not let it prove
        # subject identity.
        if any(
            marker in suffix
            for marker in ("browse", "download", "search", "image results", "yandex images", "bing images")
        ):
            return True
    # Generic numbered labels are still query echoes, while meaningful
    # captions (including years and event names) are retained as evidence.
    stripped = re.sub(
        r"\s+(?:result|image|photo|picture|thumbnail|item|card|match)\s*#?\s*\d+\s*$",
        "",
        norm_title,
    ).strip()
    return stripped == norm_query


def _normalize_url(value: object) -> str:
    value = html.unescape(str(value or "")).replace("\\/", "/").strip()
    if value.startswith("//"):
        value = "https:" + value
    if not value.startswith(_DIRECT_SCHEMES):
        return ""
    parsed = urlparse(value)
    if not parsed.netloc:
        return ""
    if parsed.scheme not in {"http", "https"}:
        return ""
    return value


def _canonical_url(value: object) -> str:
    url = _normalize_url(value)
    if not url:
        return ""
    parsed = urlparse(url)
    # Query strings on CDN thumbnails frequently contain expiring signatures;
    # retain them for download but dedupe on the stable path when possible.
    return parsed._replace(fragment="").geturl().rstrip("/").casefold()


def _int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _result(
    engine: str,
    query: str,
    image_url: object,
    *,
    title: object = "",
    thumbnail_url: object = "",
    source_url: object = "",
    width: object = 0,
    height: object = 0,
    description: object = "",
    metadata: Optional[Dict[str, object]] = None,
) -> Optional[UniversalImageResult]:
    image = _normalize_url(image_url)
    if not image:
        return None
    resolved_title = str(title or "").strip()
    title_is_query = _title_is_query_echo(resolved_title, query)
    return UniversalImageResult(
        # Keep the query visible in raw scraper diagnostics, but mark it so
        # the resolver never uses this placeholder as asset identity proof.
        title=(resolved_title or query).strip()[:500],
        image_url=image,
        thumbnail_url=_normalize_url(thumbnail_url) or image,
        source_url=_normalize_url(source_url) or image,
        source_engine=engine,
        width=_int(width),
        height=_int(height),
        description=str(description or "").strip()[:1000],
        metadata={**dict(metadata or {}), "title_is_query": title_is_query},
    )


class _Engine:
    name = "engine"
    # Route that served the last fetch ("proxy" | "direct"), and the routes
    # tried by the last failed fetch — read by _run_engine for the
    # route= log marker (item 8: every engine line carries its route).
    _last_route: str = "proxy"
    _last_routes_tried: Tuple[str, ...] = ()

    def search(self, query: str, limit: int, page: int, safe_search: bool, direct: bool = False) -> List[UniversalImageResult]:
        raise NotImplementedError

    def _get_client(self, direct: bool = False):
        """Thread-local requests.Session for one route (Worker F, Nov 18).

        The proxy and direct routes get SEPARATE cached sessions: the
        proxy session is a default Session (trust_env=True, env proxy
        applies); the direct session is built by _new_route_session with
        trust_env=False, so no env proxy is ever applied to it. Sharing
        one session across routes would make the direct route depend on
        the proxies kwarg alone — which only bypasses by a requests
        merge-ordering accident. Sessions stay thread-local (one per
        engine thread per route) because requests.Session is not
        thread-safe for concurrent use.
        """
        local = getattr(self, "_local", None)
        if local is None:
            self._local = local = threading.local()
        attr = "direct_client" if direct else "client"
        client = getattr(local, attr, None)
        if client is None:
            client = _new_route_session(direct=bool(direct))
            setattr(local, attr, client)
        return client

    def _drop_client(self, direct: bool = False) -> None:
        """Drop the cached session for one route (poisoned connection)."""
        local = getattr(self, "_local", None)
        if local is not None:
            setattr(local, "direct_client" if direct else "client", None)

    def _engine_client(self, direct: bool = False):
        """_get_client() tolerating pre-web-resilience test doubles.

        Mirrors _run_engine's handling of engines whose search() takes
        no ``direct`` keyword: a _get_client that takes no argument
        raises TypeError mentioning 'direct'; retry it bare instead of
        failing the engine.
        """
        try:
            return self._get_client(direct=direct)
        except TypeError as exc:
            if "direct" not in str(exc):
                raise
            return self._get_client()

    def _fetch(self, url: str, *, headers, timeout: float = 30, direct: bool = False):
        """GET with per-engine route fallback. Returns (response, route).

        Tries the preferred route first, then the other route on a
        transport error (timeout / connection error / throttle-queue
        timeout). An HTTP status — including 403/429/5xx — is an answer,
        not a transport failure, so it is returned, never route-retried.
        A route that failed transport repeatedly is skipped fast by
        _route_order. Raises the last transport error when every live
        route failed.

        Each route attempt is issued on that route's own session
        (trust_env=False for direct, trust_env=True for proxy), so the
        wire path matches the route label — the direct attempt really
        leaves via direct egress. The legacy web_bypass_proxy() shim is
        folded into the preferred route so a shim-enabled caller also
        gets the direct session, not a proxy session relabelled direct.

        Nov 18 (web-resilience): this replaces the old primp-first +
        plain-requests fallback. primp is DNS-dead from this VM (every
        call paid a wasted subprocess spawn and failed), so plain
        requests is now the only transport and the ROUTE (proxy vs
        direct) is the fallback dimension instead.
        """
        # Round 6: engines whose hosts are proven direct-capable try
        # direct first (direct is 2-3x faster than the proxy for them),
        # with the proxy as fallback. An explicit direct=True or the
        # bypass shim also prefers direct; any other engine stays
        # proxy-first.
        prefer_direct = (
            bool(direct)
            or _web_bypass_proxy_enabled()
            or _engine_prefers_direct(self.name)
        )
        routes = _route_order(self.name, prefer_direct)
        last_exc: Optional[BaseException] = None
        tried: List[str] = []
        for route in routes:
            tried.append(route)
            try:
                response = _throttled_get(
                    self._engine_client(direct=(route == "direct")),
                    url, headers=headers,
                    timeout=timeout, direct=(route == "direct"),
                )
            except Exception as exc:
                last_exc = exc
                kind = _timeout_kind(exc)
                LOGGER.debug(
                    "[universal] %s: route=%s transport failed "
                    "kind=%s error=%s: %s",
                    self.name, route, kind,
                    type(exc).__name__, str(exc)[:120],
                )
                if kind == "queue-timeout":
                    # Round 6: the call never reached the socket — the
                    # route is not sick, the throttle queue is full.
                    # Counting it as a route failure poisons route
                    # health: after 3 queued calls both routes look dead
                    # and _route_order skips them for 120 s (test 33's
                    # log shows routes_tried=proxy only because direct
                    # had been struck out by queue waits, not because
                    # direct was down). No strike, and no session drop —
                    # the connection was never used.
                    continue
                _note_route_result(self.name, route, False)
                # Drop the poisoned session for THIS route only, so the
                # next route gets a fresh one instead of reusing a broken
                # connection. (The other route's session is untouched.)
                drop = getattr(self, "_drop_client", None)
                if drop is not None:
                    drop(direct=(route == "direct"))
                continue
            _note_route_result(self.name, route, True)
            self._last_route = route
            self._last_routes_tried = tuple(tried)
            return response, route
        self._last_routes_tried = tuple(tried)
        raise RuntimeError(
            f"{self.name}: all routes failed ({','.join(tried)}): "
            f"{type(last_exc).__name__}: {str(last_exc)[:120]}"
        ) from last_exc


# NOTE (Nov 18, web-resilience): _IsolatedNativeClient (primp via the
# research_search_worker subprocess) is REMOVED. primp is DNS-dead from
# this VM — primp.Client().get() fails instantly with DNSError, swallowed
# by the worker — so every engine call paid a wasted subprocess spawn and
# then fell back to plain requests anyway. Plain requests is now the only
# transport; the route (proxy vs direct) is the fallback dimension. The
# worker file itself keeps a requests-based 'http' kind for any other
# caller, and now reports its errors as {"error": ...} instead of bare '[]'.


class YandexEngine(_Engine):
    name = "yandex"

    def __init__(self) -> None:
        self._local = threading.local()

    # _get_client(direct=) is inherited from _Engine: per-route cached
    # sessions (trust_env=False for direct). primp removed Nov 18 (DNS-dead).

    def search(self, query: str, limit: int, page: int, safe_search: bool, direct: bool = False) -> List[UniversalImageResult]:
        encoded = urllib.parse.quote(query)
        url = f"https://yandex.com/images/search?text={encoded}&p={max(0, page - 1)}"
        if safe_search:
            url += "&family=yes"
        # Route fallback (Nov 18 web-resilience): plain requests is the
        # only transport now (primp DNS-dead, removed); the fallback
        # dimension is the ROUTE — proxy first, direct on transport
        # failure — with per-route health, inside _fetch().
        response, _route = self._fetch(url, headers=_HEADERS, timeout=_universal_fetch_timeout_seconds(), direct=direct)

        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise RuntimeError(f"Yandex returned HTTP status {status}")
        raw = html.unescape(str(getattr(response, "text", "") or ""))
        # Yandex serves its SmartCaptcha page (HTTP 200) instead of results
        # when the egress IP is gated. Same markers as ScriptIT's
        # isYandexChallengePage(): fail fast with the real reason instead of
        # returning a silent empty list that looks like "no results".
        if re.search(r"captcha_smart", raw, re.I) and re.search(r"captcha", raw, re.I) \
                and not re.search(r"serp-item|Organic", raw):
            raise RuntimeError("Yandex bot challenge (SmartCaptcha; egress IP gated)")
        # The source project's unescaped SERP parser is retained here.  The
        # newer state parser often exposes ``img_href`` before card metadata.
        images = [_normalize_url(v) for v in _Y_IMG_RE.findall(raw)]
        thumbs = [_normalize_url(v) for v in _Y_THUMB_RE.findall(raw)]
        titles = [html.unescape(v).replace("\\u002F", "/") for v in _Y_TITLE_RE.findall(raw)]
        out: List[UniversalImageResult] = []
        seen = set()
        for idx, image in enumerate(images):
            key = _canonical_url(image)
            if not key or key in seen:
                continue
            seen.add(key)
            row = _result(
                "Yandex", query, image,
                title=titles[idx] if idx < len(titles) else f"{query} result {idx + 1}",
                thumbnail_url=thumbs[idx] if idx < len(thumbs) else image,
                source_url=image,
                description="Yandex visual search index",
                metadata={"retrieval_method": "unescaped_serp", "title_is_query": not (idx < len(titles) and titles[idx])},
            )
            if row:
                out.append(row)
            if len(out) >= limit:
                break
        return out


class BingEngine(_Engine):
    name = "bing"

    def __init__(self) -> None:
        self._local = threading.local()

    # _get_client(direct=) is inherited from _Engine: per-route cached
    # sessions (trust_env=False for direct). primp removed Nov 18 (DNS-dead).

    def search(self, query: str, limit: int, page: int, safe_search: bool, direct: bool = False) -> List[UniversalImageResult]:
        first = max(0, (page - 1) * limit)
        safe = "strict" if safe_search else "off"
        url = (
            "https://www.bing.com/images/async?"
            f"q={urllib.parse.quote(query)}&first={first}&count={limit}&mmasync=1&adlt={safe}"
        )
        # Route fallback (Nov 18 web-resilience) — see YandexEngine.
        response, _route = self._fetch(url, headers=_HEADERS, timeout=_universal_fetch_timeout_seconds(), direct=direct)

        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise RuntimeError(f"Bing returned HTTP status {status}")
        markup = str(getattr(response, "text", "") or "")
        elements: Iterable[object]
        if BeautifulSoup is not None:
            elements = BeautifulSoup(markup, "html.parser").find_all("a", class_="iusc")
        else:
            elements = _BING_META_RE.findall(markup) + _BING_META_RE_REVERSE.findall(markup)
        out: List[UniversalImageResult] = []
        for element in elements:
            if isinstance(element, str):
                raw_meta = element
                anchor_title = query
            else:
                raw_meta = element.get("m")  # type: ignore[union-attr]
                anchor_title = element.get("aria-label") or query  # type: ignore[union-attr]
            try:
                data = json.loads(html.unescape(str(raw_meta or "")))
            except Exception:
                continue
            row = _result(
                "Bing", query, data.get("murl"),
                title=data.get("t") or anchor_title,
                thumbnail_url=data.get("turl"),
                source_url=data.get("purl"),
                width=data.get("w") or data.get("mw"),
                height=data.get("h") or data.get("mh"),
                description=data.get("desc"),
                metadata={"retrieval_method": "bing_async_media", "title_is_query": not bool(data.get("t") or anchor_title != query)},
            )
            if row:
                out.append(row)
            if len(out) >= limit:
                break
        return out


class DuckDuckGoEngine(_Engine):
    name = "duckduckgo"

    def search(self, query: str, limit: int, page: int, safe_search: bool, direct: bool = False) -> List[UniversalImageResult]:
        if DDGS is None:
            raise RuntimeError("optional dependency 'ddgs' is not installed")
        start = max(0, (page - 1) * limit)
        # DuckDuckGo intermittently serves a 202 anomaly interstitial from
        # this egress IP (verified live 2026-11-10: 0 results on one query,
        # 10 on the next). The old 4s budget was also too tight for a ddgs
        # call through the throttled proxy, so the budget is now 15s and a
        # clean empty result is retried once. A hung worker is different:
        # it must return [] immediately (never retry a hang), per the
        # timeout contract. A persistent clean empty fails loudly instead
        # of returning a silent empty list that hid the bot-gate for weeks.
        self._last_route = "direct" if direct else "proxy"
        for attempt in range(2):
            try:
                out = self._search_once(query, limit, start, safe_search, direct=direct)
            except subprocess.TimeoutExpired:
                return []
            if out:
                return out
            if attempt == 0:
                LOGGER.debug("DuckDuckGo empty on attempt 1 for %r; retrying once", query)
        raise RuntimeError(
            "DuckDuckGo returned no results after 2 attempts (likely bot-gate interstitial)"
        )

    def _search_once(self, query: str, limit: int, start: int, safe_search: bool, direct: bool = False) -> List[UniversalImageResult]:
        out: List[UniversalImageResult] = []
        process = None
        try:
            from ..job_context import check_job_active, remaining_seconds
            check_job_active()
            budget = min(15.0, remaining_seconds(15.0))
            if budget <= 0:
                return out
            # Route for the subprocess worker (Nov 18 web-resilience): a
            # thread-local flag cannot cross a process boundary, so the
            # route is passed explicitly — the child's proxy env vars are
            # stripped for a direct run, and the worker payload carries
            # 'direct' so it strips them again as belt-and-braces.
            child_env = None
            if direct:
                child_env = {
                    key: value for key, value in os.environ.items()
                    if key.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
                }
            process = subprocess.Popen(
                [sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'research_search_worker.py')],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', env=child_env,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            output, _ = process.communicate(json.dumps({'kind': 'images', 'query': query,
                'timeout': budget, 'safe_search': safe_search, 'limit': start + limit,
                'direct': bool(direct)}), timeout=budget)
            check_job_active()
            raw_items = json.loads(output[:1000000])
            if isinstance(raw_items, dict) and raw_items.get("error"):
                # The worker now reports its failure instead of bare '[]'.
                LOGGER.debug("DuckDuckGo worker error: %s", str(raw_items.get("error"))[:160])
                return out
            if not isinstance(raw_items, list):
                return out
            for idx, item in enumerate(raw_items or []):
                if idx < start:
                    continue
                row = _result(
                    "DuckDuckGo", query, item.get("image"),
                    title=item.get("title"), thumbnail_url=item.get("thumbnail"),
                    source_url=item.get("url"), width=item.get("width"),
                    height=item.get("height"),
                    description=f"Source: {item.get('source', '')}",
                    metadata={"retrieval_method": "ddgs_image_index"},
                )
                if row:
                    out.append(row)
                if len(out) >= limit:
                    break
        except subprocess.TimeoutExpired:
            # Hung worker: the finally block kills it; let the caller see
            # the timeout so it can return [] without retrying a hang.
            raise
        except Exception as exc:
            LOGGER.debug("DuckDuckGo search error: %s", exc)
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                try:
                    process.communicate(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        return out


class BaiduEngine(_Engine):
    name = "baidu"

    def __init__(self) -> None:
        self._local = threading.local()

    # _get_client(direct=) is inherited from _Engine: per-route cached
    # sessions (trust_env=False for direct). primp removed Nov 18 (DNS-dead).

    def search(self, query: str, limit: int, page: int, safe_search: bool, direct: bool = False) -> List[UniversalImageResult]:
        del safe_search
        start = max(0, (page - 1) * limit)
        encoded = urllib.parse.quote(query)
        url = (
            "https://image.baidu.com/search/acjson?tn=resultjson_com&ipn=rj&ct=201326592"
            f"&fp=result&queryWord={encoded}&word={encoded}&cl=2&lm=-1&ie=utf-8&oe=utf-8"
            f"&pn={start}&rn={limit}"
        )
        # Route fallback (Nov 18 web-resilience) — see YandexEngine.
        response, _route = self._fetch(url, headers={**_HEADERS, "Referer": "https://image.baidu.com/"}, timeout=_universal_fetch_timeout_seconds(), direct=direct)

        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise RuntimeError(f"Baidu returned HTTP status {status}")
        raw = re.sub(r"\\(?![/\\\"bfnrtu])", r"\\\\", str(response.text or ""))
        try:
            data = json.loads(raw, strict=False)
        except Exception as exc:
            raise RuntimeError(f"Baidu JSON parse failed: {exc}")
        # Baidu answers this egress IP with {"antiFlag": 1, "message":
        # "Forbid spider access"} (HTTP 200, no results). Fail fast with the
        # real reason instead of returning a silent empty list.
        if isinstance(data, dict) and (
            data.get("antiFlag") or "forbid spider" in str(data.get("message") or "").lower()
        ):
            raise RuntimeError("Baidu forbids spider access from this egress IP (antiFlag=1)")
        out: List[UniversalImageResult] = []
        for item in data.get("data", []) if isinstance(data, dict) else []:
            replacements = item.get("replaceUrl") if isinstance(item, dict) else None
            original = ""
            if isinstance(replacements, list) and replacements:
                original = replacements[0].get("ObjUrl") or ""
            row = _result(
                "Baidu", query, original or item.get("thumbURL") or item.get("middleURL"),
                title=item.get("fromPageTitleEnc"), thumbnail_url=item.get("thumbURL"),
                source_url=item.get("fromURL"), width=item.get("width"), height=item.get("height"),
                description=f"Host: {item.get('bdImgLoc', '') or 'Baidu Images'}",
                metadata={"retrieval_method": "baidu_acjson"},
            )
            if row:
                out.append(row)
            if len(out) >= limit:
                break
        return out


class OpenverseEngine(_Engine):
    name = "openverse"

    def __init__(self) -> None:
        self._local = threading.local()

    # _get_client(direct=) is inherited from _Engine: per-route cached
    # sessions (trust_env=False for direct). primp removed Nov 18 (DNS-dead).

    def search(self, query: str, limit: int, page: int, safe_search: bool, direct: bool = False) -> List[UniversalImageResult]:
        del safe_search
        url = (
            f"https://api.openverse.org/v1/images/?q={urllib.parse.quote(query)}"
            f"&page={max(1, page)}&page_size={min(limit, 50)}"
        )
        # Route fallback (Nov 18 web-resilience) — see YandexEngine.
        response, _route = self._fetch(url, headers={**_HEADERS, "Accept": "application/json"}, timeout=_universal_fetch_timeout_seconds(), direct=direct)

        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise RuntimeError(f"Openverse returned HTTP status {status}")
        data = response.json()
        out: List[UniversalImageResult] = []
        for item in data.get("results", []) if isinstance(data, dict) else []:
            license_code = str(item.get("license") or "").upper()
            description = f"Creator: {item.get('creator') or 'Unknown'}"
            if license_code:
                description += f" â€¢ License: {license_code}"
            row = _result(
                "Openverse", query, item.get("url"), title=item.get("title"),
                thumbnail_url=item.get("thumbnail"), source_url=item.get("foreign_landing_url"),
                width=item.get("width"), height=item.get("height"), description=description,
                metadata={
                    "retrieval_method": "openverse_api",
                    "license": license_code,
                    "rights_status": "friendly" if license_code else "unknown",
                    "foreign_url": item.get("foreign_landing_url") or "",
                },
            )
            if row:
                out.append(row)
            if len(out) >= limit:
                break
        return out


class FlickrEngine(_Engine):
    name = "flickr"

    def __init__(self) -> None:
        self._local = threading.local()

    # _get_client(direct=) is inherited from _Engine: per-route cached
    # sessions (trust_env=False for direct). primp removed Nov 18 (DNS-dead).

    def search(self, query: str, limit: int, page: int, safe_search: bool, direct: bool = False) -> List[UniversalImageResult]:
        del safe_search
        terms = re.findall(r"[^\W_]+", query, re.UNICODE)
        stopwords = {"the", "and", "for", "with", "from", "this", "that", "how", "what", "where", "when", "which", "about"}
        tags = [term for term in terms if len(term) >= 3 and term.casefold() not in stopwords][:5]
        if not tags:
            return []
        params = urllib.parse.urlencode({"tags": ",".join(tags), "tagmode": "any",
                                         "format": "json", "nojsoncallback": "1"})
        url = f"https://www.flickr.com/services/feeds/photos_public.gne?{params}"
        # Route fallback (Nov 18 web-resilience) — see YandexEngine.
        response, _route = self._fetch(url, headers=_HEADERS, timeout=_universal_fetch_timeout_seconds(), direct=direct)

        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise RuntimeError(f"Flickr returned HTTP status {status}")
        data = response.json()
        out: List[UniversalImageResult] = []
        for item in data.get("items", []) if isinstance(data, dict) else []:
            media = item.get("media") or {}
            thumb = media.get("m") or ""
            high = thumb.replace("_m.jpg", "_b.jpg")
            row = _result(
                "Flickr", query, high or thumb, title=str(item.get("title") or query).strip(),
                thumbnail_url=thumb, source_url=item.get("link"),
                description=f"Author: {str(item.get('author') or '').strip()}",
                metadata={"retrieval_method": "flickr_public_feed", "tags": item.get("tags") or ""},
            )
            if row:
                out.append(row)
            if len(out) >= limit:
                break
        return out


_BOT_GATE_MARKERS = ("bot-gate", "bot challenge", "smartcaptcha", "robot check", "captcha")

# A *guessed* interstitial, not a refusal: DuckDuckGo raises "returned no
# results after 2 attempts (likely bot-gate interstitial)" on two clean
# empty results. That is an inference, not a challenge page or an HTTP
# refusal, and routing it to the long bot-gate skip lost the engine for
# the whole run 15s in (GTA VI). It gets the short refusal cooldown plus
# a re-probe instead (see UniversalScraperManager).
_SUSPECTED_GATE_MARKERS = ("likely bot-gate interstitial",)


def is_suspected_bot_gate_error(exc: BaseException) -> bool:
    """True when the failure is only a guessed interstitial, not a refusal."""
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _SUSPECTED_GATE_MARKERS)


def is_bot_gate_error(exc: BaseException) -> bool:
    """True when an engine failure is a definitive refusal for this run.

    Bot gates, HTTP 403 refusals, and exhausted 429 responses should not be
    repeated for every scene through the same egress. The manager applies a
    short cooldown to HTTP refusals/rate limits and a longer one to bot gates.
    Ordinary transport errors (timeouts, 5xx) remain eligible for later queries.
    A merely *suspected* interstitial (see is_suspected_bot_gate_error) is
    NOT definitive: it takes the refusal cooldown, not the bot-gate skip.
    """
    if is_suspected_bot_gate_error(exc):
        return False
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _BOT_GATE_MARKERS) or bool(
        re.search(r"\b(?:http(?: status)?\s*|status\s*)(?:403|429)\b", text)
    )


# Transport-stall markers for the slow-network streak (Nov 22, test 25).
# A stalled engine is one whose failure is the SHARED egress being slow,
# not the provider refusing us: socket/read/connect timeouts, host-slot
# waits inside the throttle budget, connection resets, HTTP 5xx. Refusals
# (bot gates, HTTP 403/429) are excluded with the same markers
# is_bot_gate_error uses — a provider saying "no" must never arm the
# longer deadline.
_TRANSPORT_STALL_MARKERS = (
    "timeout", "timed out", "read timeout", "connect", "connection",
    "slot", "budget", "network", "reset", "unreachable", "refused",
    "eof", "dns", "temporar",
)


def _is_transport_stall_error_text(error: str) -> bool:
    """True when an engine failure text is a transport stall, not a refusal.

    Test 25 (job 655e40d8): every engine failed with a transport error —
    Bing/Flickr ReadTimeout at 30s/route, Yandex a 30s host-slot wait —
    yet the slow-network streak never moved, because the old trigger
    required every engine to still be RUNNING past its staggered batch
    deadline. Fetch failures complete at ~60s (2 routes x 30s) while the
    staggered deadlines are 60/65/70s, so engines 2-3 finish BEFORE their
    deadline with 0 rows and never land in ``timed_out``. The trigger was
    structurally unreachable. Count the failure TEXT instead: a batch is
    stalled when it returns 0 rows and every selected engine either timed
    out or failed with a transport error. A clean empty page (success,
    0 rows) or a refusal (403/429/bot-gate) never counts.
    """
    text = str(error or "").lower()
    if not text:
        return False
    if any(marker in text for marker in _BOT_GATE_MARKERS):
        return False
    if re.search(r"\b(?:http(?: status)?\s*|status\s*)(?:403|429)\b", text):
        return False
    if any(marker in text for marker in _TRANSPORT_STALL_MARKERS):
        return True
    return bool(re.search(r"\b(?:500|502|503|504)\b", text))


class UniversalScraperManager:
    """Run the supplied three-engine search strategy as a resolver lane.

    Baidu, Openverse and DuckDuckGo are NOT in the default set: Baidu answers this
    egress IP with ``{"antiFlag": 1, "message": "Forbid spider access"}``
    and api.openverse.org 504s through the egress proxy (verified live
    2026-11-10), and DuckDuckGo bot-gates this egress (202 anomaly
    interstitial, circuit open — GTA prep, Nov 19, 2026 — while
    Yandex/Bing/Flickr each returned 20 results). Their engine classes stay
    available and raise a clear error if called directly, so they can be
    re-enabled if the egress IP/proxy situation changes — but production must
    not spend a worker slot on an engine that cannot return results.
    """

    # Canonical engine order: Yandex first (it bot-walls least), then Bing,
    # Flickr. See WEB_ENGINE_PRIORITY in source_broker.py. The
    # manager interleaves results in this order, so Yandex rows surface first.
    DEFAULT_ENGINES = WEB_ENGINE_PRIORITY

    def __init__(
        self,
        *,
        max_workers: int = 6,
        engine_timeout_seconds: float = 10.0,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.max_workers = max(1, min(12, int(max_workers)))
        # Honored as engine latency, not a wave wall (GTA fix, Nov 16): the
        # old 20s constructor clamp was silently bypassed by search()'s
        # 300s ceiling, so the two bounds disagreed. The clamp now agrees
        # with search() — an operator-set timeout is the per-engine budget,
        # and the resolver wave waits for it under liveness gating instead
        # of abandoning the batch at 15s.
        self.engine_timeout_seconds = max(1.0, min(300.0, float(engine_timeout_seconds)))
        self._log_fn = log_fn
        self._engines: Dict[str, _Engine] = {
            "yandex": YandexEngine(),
            "bing": BingEngine(),
            "baidu": BaiduEngine(),
            "openverse": OpenverseEngine(),
            "flickr": FlickrEngine(),
        }
        # Reuse one bounded pool for the manager lifetime. Creating a new
        # executor for every query and shutting it down without waiting can
        # leave timed-out HTTP workers running after the resolver has moved
        # on; repeated scenes then accumulate detached workers and late
        # errors. A single ``max_workers`` pool is correct for one search but
        # starves concurrent listicle scene searches: each scene submits six
        # engine tasks and the queued waves cannot start before the scene
        # deadline. Reserve a small number of parallel batches while keeping
        # a hard process-wide cap so this remains safe on slow machines.
        try:
            batch_multiplier = max(
                1,
                min(6, int(os.environ.get("VIDZEO_UNIVERSAL_BATCH_MULTIPLIER", "3") or 3)),
            )
        except (TypeError, ValueError):
            batch_multiplier = 3
        transport_workers = min(36, max(self.max_workers, self.max_workers * batch_multiplier))
        self._executor = ThreadPoolExecutor(
            max_workers=transport_workers,
            thread_name_prefix="universal-scraper",
        )
        self._lock = threading.Lock()
        self._last_response: Optional[UniversalSearchResponse] = None
        # Late-arrival spool (Wright, Nov 16): engines that finish after
        # their batch gave up still hold real candidates — the Wright web
        # run's candidates arrived a minute after the stage had given up
        # and were discarded.  A finished-but-late engine's rows are kept
        # here, keyed by normalized query, so a later re-check (the next
        # search for the same query, or ``collect_late_results``) can still
        # use them instead of re-crawling.  Futures still running past
        # their deadline are tracked in ``_late_pending`` and harvested by
        # the same re-check once they complete.
        self._late_spool: Dict[str, List[UniversalImageResult]] = {}
        self._late_spool_lock = threading.Lock()
        self._late_pending: Dict[str, List[Tuple[str, object]]] = {}
        # Engines that hit a definitive bot challenge or HTTP refusal are
        # cooled down instead of burning their retry/timeout budget once per
        # scene. Keyed by engine name, value is the monotonic time after which
        # that source can be tried again.
        self._bot_gated: Dict[str, float] = {}
        # Re-probe rule (GTA fix, Nov 16): a gated engine gets ONE probe per
        # gate epoch, halfway through its skip. A probe that succeeds clears
        # the gate; one that fails re-gates for a fresh skip. Without this a
        # 15s DDG interstitial disabled the engine for the whole run.
        # _bot_gate_reprobe_due[engine] is the monotonic time a probe opens;
        # _bot_gate_reprobe_spent[engine] records the gate-epoch (the
        # _bot_gated value) the probe was spent on, so one epoch = one probe.
        self._bot_gate_reprobe_due: Dict[str, float] = {}
        self._bot_gate_reprobe_spent: Dict[str, float] = {}
        # Diagnostic kind per gate: "bot_gate" | "http_refusal" |
        # "suspected_interstitial" (a guessed DDG interstitial, short skip).
        self._bot_gate_kind: Dict[str, str] = {}
        # Liveness signal for the resolver wave (GTA fix): monotonic time of
        # the last engine completion (success or failure) in this manager.
        # The wave reads it via last_progress_at / progress_snapshot(); a
        # wave with no engine completing anything for the liveness window
        # is provably dead. Any engine completion refreshes it.
        self._last_progress_at = time.monotonic()
        # Wall of the most recent batch (max per-engine deadline), exposed
        # so the resolver wave knows how long a working batch may take.
        self._last_batch_max_deadline_seconds = 0.0
        # Slow-network state (Nov 22, job d1ece590): consecutive batches
        # in which EVERY non-gated engine timed out — a shared-network
        # signal, not a per-engine failure. While it persists, the
        # effective engine and fetch timeouts grow by
        # _universal_slow_network_step() per batch, capped at
        # _universal_slow_network_max_factor(). Any batch that returns
        # rows resets it: the network recovered. Concurrency is NOT
        # raised (per-host throttle slots untouched) and DuckDuckGo stays
        # skipped; only the deadline adapts.
        self._slow_network_streak = 0
        # Fail-faster (§2.1): consecutive all-empty stalled batches,
        # PER QUERY (test 29: a single global counter let one query's
        # failures zero 21 other queries' retries and rescues — the
        # streak hit 33 while rows still arrived intermittently for
        # other queries, and the run's only 60-row batch came from a
        # retry wave the cap had disabled). Keyed by the normalised
        # query string; a batch returning >= 1 row clears its own
        # query's entry. The slow-network streak stays global: it is
        # a transport-health signal, not a per-query one.
        self._consecutive_empty_stalled_by_query: Dict[str, int] = {}
        try:
            # 900s lost a bot-gated engine for the whole run; 300s plus the
            # halfway re-probe keeps the engine recoverable within one job.
            self._bot_gate_skip_seconds = max(
                0.0,
                float(os.environ.get("VIDZEO_UNIVERSAL_BOT_GATE_SKIP_SECONDS", "300") or 300),
            )
        except (TypeError, ValueError):
            self._bot_gate_skip_seconds = 300.0
        try:
            self._refusal_skip_seconds = max(
                0.0,
                min(
                    600.0,
                    float(os.environ.get("VIDZEO_UNIVERSAL_REFUSAL_SKIP_SECONDS", "60") or 60),
                ),
            )
        except (TypeError, ValueError):
            self._refusal_skip_seconds = 60.0

    @property
    def engines(self) -> Tuple[str, ...]:
        return tuple(self._engines.keys())

    def _log(self, message: str) -> None:
        try:
            if self._log_fn:
                self._log_fn(message)
            else:
                LOGGER.info(message)
        except Exception:
            pass

    def _note_progress(self) -> None:
        """Record an engine completion (success or failure) as wave progress."""
        with self._lock:
            self._last_progress_at = time.monotonic()

    @property
    def last_progress_at(self) -> float:
        """Monotonic timestamp of the last engine completion in this manager.

        Read by the resolver wave (and the driver, via progress_snapshot)
        as the liveness signal: while engines keep completing, the wave is
        alive no matter how slow the proxy is.
        """
        with self._lock:
            return self._last_progress_at

    def progress_snapshot(self, query: Optional[str] = None) -> Dict[str, object]:
        """Liveness + gate state for the resolver wave / driver (worker D).

        ``last_progress_at`` is a monotonic timestamp; ``gated_engines``
        maps engine → {"until": monotonic expiry, "kind": gate kind}.

        ``consecutive_empty_stalled_batches`` is per-query when
        ``query`` is given (test 29: the rescue gate must read only
        the current query's streak); without a query it reports the
        worst streak across queries, for backward compatibility.
        """
        now = time.monotonic()
        with self._lock:
            return {
                "last_progress_at": self._last_progress_at,
                "last_batch_max_deadline_seconds": self._last_batch_max_deadline_seconds,
                "slow_network_streak": self._slow_network_streak,
                "slow_network_factor": self._slow_network_factor_locked(),
                "consecutive_empty_stalled_batches": (
                    self._consecutive_empty_stalled_by_query.get(
                        _empty_streak_key(query), 0
                    )
                    if query is not None
                    else max(
                        self._consecutive_empty_stalled_by_query.values(),
                        default=0,
                    )
                ),
                "gated_engines": {
                    engine: {
                        "until": until,
                        "kind": self._bot_gate_kind.get(engine, "bot_gate"),
                    }
                    for engine, until in self._bot_gated.items()
                    if until > now
                },
            }

    @property
    def last_batch_max_deadline_seconds(self) -> float:
        """Max per-engine deadline (seconds) of the most recent batch."""
        with self._lock:
            return self._last_batch_max_deadline_seconds

    @property
    def slow_network_streak(self) -> int:
        """Consecutive all-timeout batches (shared-network signal)."""
        with self._lock:
            return self._slow_network_streak

    @property
    def slow_network_factor(self) -> float:
        """Current timeout multiplier from the slow-network streak."""
        with self._lock:
            return self._slow_network_factor_locked()

    @property
    def consecutive_empty_stalled_batches(self) -> int:
        """Consecutive all-empty stalled batches (§2.1 fail-faster).

        Per-query since test 29: this reports the worst (max) streak
        across queries, for backward compatibility with callers that
        have no query in hand. The retry/rescue gates read the
        per-query value via progress_snapshot(query).
        """
        with self._lock:
            return max(
                self._consecutive_empty_stalled_by_query.values(), default=0
            )

    def _slow_network_factor_locked(self) -> float:
        """Timeout multiplier for the current streak; call with _lock held."""
        step = _universal_slow_network_step()
        cap = _universal_slow_network_max_factor()
        return min(cap, step ** self._slow_network_streak)

    def _spool_late_results(
        self, query: str, engine: str, rows: Sequence[UniversalImageResult]
    ) -> None:
        """Keep late-arriving rows for a later re-check instead of dropping them."""
        if not rows:
            return
        with self._late_spool_lock:
            spool = self._late_spool.setdefault(query, [])
            seen = {_canonical_url(row.image_url) for row in spool}
            for row in rows:
                key = _canonical_url(row.image_url)
                if not key or key in seen:
                    continue
                seen.add(key)
                spool.append(row)
            del spool[100:]

    def _drain_late_spool(self, query: str) -> List[UniversalImageResult]:
        with self._late_spool_lock:
            return list(self._late_spool.pop(query, []))

    def collect_late_results(
        self, query: str, wait_seconds: float = 0.0
    ) -> List[UniversalImageResult]:
        """Re-check the late spool/queue for ``query`` after a batch wall.

        Waits up to ``wait_seconds`` for engines that were still running
        past their deadline, harvests their rows, and returns every
        salvaged row for the query (spooled + freshly harvested, deduped).
        A still-running engine stays pending for the next re-check.
        """
        clean = re.sub(r"\s+", " ", str(query or "")).strip()
        if not clean:
            return []
        try:
            wait_budget = max(0.0, float(wait_seconds or 0.0))
        except (TypeError, ValueError):
            wait_budget = 0.0
        deadline = time.monotonic() + wait_budget
        while True:
            with self._lock:
                pending = list(self._late_pending.get(clean, []))
            if not any(not future.done() for _, future in pending):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        harvested: List[UniversalImageResult] = []
        with self._lock:
            pending = self._late_pending.pop(clean, [])
        for engine, future in pending:
            if not future.done():
                with self._lock:
                    self._late_pending.setdefault(clean, []).append((engine, future))
                continue
            try:
                _name, rows, _stat = future.result()
            except Exception:
                continue
            harvested.extend(rows or [])
        seen: set = set()
        out: List[UniversalImageResult] = []
        for row in list(harvested) + self._drain_late_spool(clean):
            key = _canonical_url(row.image_url)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(row)
        if out:
            self._log(
                f"[universal] late spool re-check for '{clean[:48]}': "
                f"recovered {len(out)} result(s)"
            )
        return out

    def collect_late_results_for_queries(
        self, queries: Sequence[str], wait_seconds: float = 0.0
    ) -> List[UniversalImageResult]:
        """Re-check the late spool for every query in ``queries`` at once.

        Scene-level harvest (GTA fix): a scene's queries are unique and
        never repeated, so a same-query re-check alone orphans every late
        row. Before the broadened round the resolver harvests all of the
        scene's queries in one call — the wait applies to any listed
        query's still-running engines — instead of re-crawling. No network
        is issued here.
        """
        clean_queries = list(dict.fromkeys(
            re.sub(r"\s+", " ", str(query or "")).strip()
            for query in (queries or [])
        ))
        clean_queries = [query for query in clean_queries if query]
        if not clean_queries:
            return []
        # Wait (bounded) for still-running engines behind ANY listed query,
        # then drain every query's spool. A scene's queries are unique and
        # never repeated, so the harvest must cover all of them — waiting
        # on the first query alone would orphan the rest.
        try:
            wait_budget = max(0.0, float(wait_seconds or 0.0))
        except (TypeError, ValueError):
            wait_budget = 0.0
        deadline = time.monotonic() + wait_budget
        while True:
            with self._lock:
                pending = [
                    future
                    for query in clean_queries
                    for _engine, future in self._late_pending.get(query, [])
                ]
            if not any(not future.done() for future in pending):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        out: List[UniversalImageResult] = []
        seen = set()
        for query in clean_queries:
            for row in self.collect_late_results(query, wait_seconds=0.0):
                key = _canonical_url(row.image_url)
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(row)
        return out

    def _run_engine(
        self,
        engine_name: str,
        clean: str,
        limit: int,
        page: int,
        safe_search: bool,
        batch_finished: threading.Event,
        direct: bool = False,
        fetch_timeout: Optional[float] = None,
    ) -> Tuple[str, List[UniversalImageResult], UniversalEngineStat]:
        started = time.perf_counter()
        # Slow-network adaptation (Nov 22): the per-route fetch timeout
        # for THIS engine call, set thread-locally so it reaches the pool
        # worker thread that issues the request. Cleared in the finally
        # below so a later call on the same reused worker thread falls
        # back to the env default.
        if fetch_timeout is not None:
            _fetch_timeout_override_local.seconds = fetch_timeout
        try:
            try:
                rows = self._engines[engine_name].search(
                    clean, limit, max(1, int(page)), bool(safe_search), direct=direct
                )
            except TypeError as exc:
                # Engines (and test doubles) written before the Nov 18
                # web-resilience build take no ``direct`` keyword; retry
                # without it instead of failing the engine.
                if "direct" not in str(exc):
                    raise
                rows = self._engines[engine_name].search(
                    clean, limit, max(1, int(page)), bool(safe_search)
                )
            duration = int((time.perf_counter() - started) * 1000)
            stat = UniversalEngineStat(engine_name, len(rows), duration, True)
            # A successful probe clears a gate (re-probe rule): the engine
            # proved itself alive again, so the cooldown ends now.
            with self._lock:
                self._bot_gated.pop(engine_name, None)
                self._bot_gate_reprobe_due.pop(engine_name, None)
                self._bot_gate_reprobe_spent.pop(engine_name, None)
                self._bot_gate_kind.pop(engine_name, None)
            self._note_progress()
            # Route marker (Nov 18 web-resilience): every engine line
            # carries route=proxy|direct so the next outage is
            # diagnosable from the log alone.
            route = getattr(self._engines[engine_name], "_last_route", "proxy")
            if batch_finished.is_set():
                # The rows are NOT discarded: they sit in this future for
                # collect_late_results() to harvest after the batch wall.
                self._log(
                    f"[universal] {engine_name}: late result held for spool collection "
                    f"after batch deadline ({len(rows)} result(s), {duration}ms, route={route})"
                )
            else:
                self._log(f"[universal] {engine_name}: {len(rows)} result(s) in {duration}ms route={route}")
            return engine_name, rows, stat
        except Exception as exc:
            duration = int((time.perf_counter() - started) * 1000)
            error = f"{type(exc).__name__}: {str(exc)[:160]}"
            # A definitive bot/robot challenge will not clear for the
            # next scene, so skip this engine for the rest of the run
            # instead of paying its retry/timeout budget on every scene.
            # A *suspected* interstitial (DDG's guessed "likely bot-gate"
            # message on two clean empty results) is not a refusal: it
            # takes the short refusal skip, not the long bot-gate skip.
            gate_kind: Optional[str] = None
            skip_seconds: Optional[float] = None
            if is_suspected_bot_gate_error(exc):
                gate_kind = "suspected_interstitial"
                skip_seconds = self._refusal_skip_seconds
            elif is_bot_gate_error(exc):
                is_http_refusal = bool(
                    re.search(r"\b(?:http(?: status)?\s*|status\s*)(?:403|429)\b", error, re.I)
                )
                gate_kind = "http_refusal" if is_http_refusal else "bot_gate"
                skip_seconds = (
                    self._refusal_skip_seconds
                    if is_http_refusal
                    else self._bot_gate_skip_seconds
                )
            if gate_kind is not None and skip_seconds is not None:
                now = time.monotonic()
                with self._lock:
                    gated_until = now + skip_seconds
                    self._bot_gated[engine_name] = gated_until
                    # One re-probe per gate epoch, halfway through the skip.
                    self._bot_gate_reprobe_due[engine_name] = now + skip_seconds / 2.0
                    self._bot_gate_reprobe_spent.pop(engine_name, None)
                    self._bot_gate_kind[engine_name] = gate_kind
                self._log(
                    f"[universal] {engine_name}: temporarily unavailable ({error}); "
                    f"skipping for {skip_seconds:.0f}s ({gate_kind}, "
                    f"re-probe in {skip_seconds / 2.0:.0f}s)"
                )
            self._note_progress()
            routes_tried = getattr(self._engines[engine_name], "_last_routes_tried", ())
            routes_label = ",".join(routes_tried) if routes_tried else ("direct" if direct else "proxy")
            if batch_finished.is_set():
                self._log(
                    f"[universal] {engine_name}: late failure ignored after batch deadline "
                    f"({duration}ms, {error}, routes_tried={routes_label})"
                )
            else:
                self._log(f"[universal] {engine_name}: failed in {duration}ms ({error}, routes_tried={routes_label})")
            return engine_name, [], UniversalEngineStat(engine_name, 0, duration, False, error)
        finally:
            if fetch_timeout is not None:
                _fetch_timeout_override_local.seconds = None

    def _collect_batch(
        self,
        futures: Dict[object, str],
        deadlines: Dict[str, float],
        clean: str,
    ) -> Tuple[Dict[str, List[UniversalImageResult]], Dict[str, UniversalEngineStat], List[str]]:
        """Collect one engine wave against staggered per-engine deadlines.

        An engine that finishes after its own deadline has its rows
        salvaged to the late spool instead of merged; an engine still
        running past its deadline is left in ``_late_pending`` for a later
        re-check instead of being cancelled and forgotten.  Returns
        (buckets, stats, timed_out_engine_names).
        """
        buckets: Dict[str, List[UniversalImageResult]] = {}
        stats: Dict[str, UniversalEngineStat] = {}
        timed_out: List[str] = []
        pending = set(futures)
        started = time.monotonic()
        max_deadline = max(deadlines.values()) if deadlines else 0.0
        while pending:
            remaining = max_deadline - (time.monotonic() - started)
            if remaining <= 0:
                break
            done, pending = wait(
                pending, timeout=min(0.25, remaining), return_when=FIRST_COMPLETED
            )
            now = time.monotonic()
            for future in done:
                engine = futures[future]
                try:
                    _name, rows, stat = future.result()
                except Exception as exc:  # pragma: no cover - _run_engine catches
                    rows = []
                    stat = UniversalEngineStat(
                        engine, 0, int((now - started) * 1000), False,
                        f"{type(exc).__name__}: {str(exc)[:160]}",
                    )
                if now - started <= deadlines[engine]:
                    buckets[engine] = rows
                    stats[engine] = stat
                else:
                    self._spool_late_results(clean, engine, rows)
                    stats[engine] = UniversalEngineStat(
                        engine, 0, int((now - started) * 1000), False, "engine timeout"
                    )
                    timed_out.append(engine)
                    self._log(
                        f"[universal] {engine}: arrived after its "
                        f"{deadlines[engine]:.1f}s deadline; {len(rows)} "
                        "result(s) salvaged to the late spool"
                    )
            # Engines whose own staggered deadline passed while still
            # running: stop waiting on them here, but do not cancel — their
            # rows are harvested by collect_late_results() when they land.
            for future in list(pending):
                engine = futures[future]
                if time.monotonic() - started > deadlines[engine]:
                    pending.discard(future)
                    stats[engine] = UniversalEngineStat(
                        engine, 0, int(deadlines[engine] * 1000), False, "engine timeout"
                    )
                    timed_out.append(engine)
                    with self._lock:
                        self._late_pending.setdefault(clean, []).append((engine, future))
                    self._log(
                        f"[universal] {engine}: timed out after "
                        f"{deadlines[engine]:.1f}s (late result will be spooled)"
                    )
        for future in list(pending):
            engine = futures[future]
            if engine not in stats:
                stats[engine] = UniversalEngineStat(
                    engine, 0, int(max_deadline * 1000), False, "engine timeout"
                )
                timed_out.append(engine)
                with self._lock:
                    self._late_pending.setdefault(clean, []).append((engine, future))
        return buckets, stats, timed_out

    def search(
        self,
        query: str,
        *,
        engine_ids: Optional[Sequence[str]] = None,
        limit_per_engine: int = 20,
        page: int = 1,
        safe_search: bool = True,
        timeout_seconds: Optional[float] = None,
        wait_until_complete: bool = False,
        direct: bool = False,
    ) -> UniversalSearchResponse:
        """Search all selected engines in parallel.

        ``direct=True`` runs this batch's engines direct (proxy bypass);
        the route is passed explicitly to the pool worker threads (the
        old thread-local bypass flag never reached them). Per-engine
        route fallback inside _fetch() may still switch an individual
        engine to the other route on a transport error.
        """
        clean = re.sub(r"\s+", " ", str(query or "")).strip()
        _streak_key = _empty_streak_key(clean)
        selected = [str(e).strip().lower() for e in (engine_ids or self.DEFAULT_ENGINES)]
        selected = list(dict.fromkeys(e for e in selected if e in self._engines))
        # Skip engines that hit a definitive bot/robot challenge earlier in
        # this run. A bot-gated engine returns nothing for any further scene,
        # so trying it again only burns its retry budget and a worker slot.
        # Re-probe rule (GTA fix): one probe per gate epoch, halfway through
        # the skip. The probe is spent here (per gate epoch) whether or not
        # it succeeds; a success clears the gate, a failure re-gates.
        self._note_progress()
        now = time.monotonic()
        with self._lock:
            gated = [e for e in selected if self._bot_gated.get(e, 0.0) > now]
            probed: List[str] = []
            for engine in gated:
                due = self._bot_gate_reprobe_due.get(engine, 0.0)
                epoch = self._bot_gated.get(engine, 0.0)
                if now >= due and self._bot_gate_reprobe_spent.get(engine) != epoch:
                    self._bot_gate_reprobe_spent[engine] = epoch
                    probed.append(engine)
            selected = [e for e in selected if e not in gated or e in probed]
        skipped = [e for e in gated if e not in probed]
        if skipped:
            self._log(
                f"[universal] skipping temporarily unavailable engine(s): {','.join(skipped)}"
            )
        if probed:
            self._log(
                f"[universal] re-probing gated engine(s): {','.join(probed)}"
            )
        limit = max(1, min(50, int(limit_per_engine or 20)))
        if not clean or not selected:
            return UniversalSearchResponse(clean, 0, [], [])
        base_engine_timeout = max(
            0.5,
            min(300.0, float(timeout_seconds or self.engine_timeout_seconds or 6.0))
        )
        # Slow-network adaptation (Nov 22, job d1ece590): when the previous
        # batch(es) ended with EVERY non-gated engine timing out — the
        # shared egress is slow, not one engine — the batch deadline grows
        # by the streak factor (1.5x per consecutive all-timeout batch,
        # capped at 2x the base), so engines that need 60-90s in a slow
        # hour can complete instead of being cut at the same wall. The
        # per-route fetch timeout grows by the same factor: an engine call
        # is at most two routes, so the fetch timeout and the batch wall
        # keep their existing 1:2 ratio (30s/route against a 60s batch).
        # Both stay bounded — the adapted engine timeout is capped at
        # 300s, the fetch timeout at 120s — and the factor resets the
        # moment any batch returns rows. Concurrency is NOT raised.
        with self._lock:
            slow_factor = self._slow_network_factor_locked()
        base_fetch_timeout = _universal_fetch_timeout_seconds()
        engine_timeout = min(300.0, base_engine_timeout * slow_factor)
        fetch_timeout = min(120.0, base_fetch_timeout * slow_factor)
        # No forced 6s cap here: wait_until_complete means wait for every
        # engine up to the batch timeout. A 6s cap discarded every engine
        # that needed longer than 6s under proxy load (Bing/Yandex return
        # 20 results in ~50s), so batches came back empty. The batch wall
        # itself (engine_timeout) and the stage/delivery walls above it
        # already bound stalled engines.
        #
        # The operator-set engine_timeout is honored as engine latency
        # (GTA fix, Nov 16 — no time limit for web): the resolver wave
        # waits for the batch under liveness gating instead of abandoning
        # it at the old 15s wave deadline, so a generous timeout is a
        # latency budget, not a wall that kills the wave.
        #
        # Wright (Nov 16): the four engines shared ONE wall, so all of them
        # died in ~20s and the candidates that arrived a minute later were
        # discarded ("late result ignored").  Deadlines are now staggered
        # per engine in priority order — the first engine gets the base
        # timeout, each later engine one more stagger step — and a late
        # engine's rows are salvaged to the spool instead of dropped.
        stagger = _universal_engine_stagger_seconds()
        deadlines = {
            engine: engine_timeout + index * stagger
            for index, engine in enumerate(selected)
        }
        max_deadline = max(deadlines.values())
        with self._lock:
            self._last_batch_max_deadline_seconds = max_deadline
        timeout_label = f"{engine_timeout:.1f}s+{stagger:.1f}s/engine"
        slow_label = (
            f" slow_network={slow_factor:.2f}x"
            if slow_factor > 1.0
            else ""
        )
        self._log(
            f"[universal] batch start query='{clean[:72]}' engines={','.join(selected)} "
            f"parallel={len(selected)} timeout={timeout_label} max={max_deadline:.1f}s "
            f"route={'direct' if direct else 'proxy'}{slow_label}"
        )
        # Re-check the spool first: a previous batch's late engines may
        # have landed after that batch gave up.  Those rows are real
        # candidates for this exact query; use them before re-crawling.
        spooled = self.collect_late_results(clean, wait_seconds=0.0)
        batch_finished = threading.Event()
        buckets: Dict[str, List[UniversalImageResult]] = {}
        stats_by_engine: Dict[str, UniversalEngineStat] = {}
        timed_out: List[str] = []
        try:
            futures = {
                self._executor.submit(
                    self._run_engine, engine, clean, limit,
                    max(1, int(page)), bool(safe_search), batch_finished,
                    direct, fetch_timeout,
                ): engine
                for engine in selected
            }
            buckets, stats_by_engine, timed_out = self._collect_batch(
                futures, deadlines, clean
            )
            # Slow-network signal (Nov 22): zero rows AND every selected
            # (non-gated) engine stalled on transport — the shared egress
            # is slow, not one engine. Record it now so the retry wave
            # below — and the next batch — get the longer deadline. A
            # bot-gated engine never counts (it is skipped before the
            # wave, or fails fast with a gate error), so a gate cannot arm
            # this; a partial success does not arm it either.
            #
            # Test 25: the old trigger (every engine still RUNNING past
            # its staggered deadline) never fired in 154 batches. Fetch
            # failures complete at ~60s (2 routes x 30s) while the
            # staggered deadlines are 60/65/70s, so engines 2-3 finish
            # BEFORE their deadline with 0 rows and never land in
            # timed_out — only yandex (deadline 60s, failing at ~60.0s)
            # ever did. Count the failure text instead: a batch is
            # stalled when it returns 0 rows and every selected engine
            # either timed out or failed with a transport error (see
            # _is_transport_stall_error_text). A clean empty page
            # (success, 0 rows) or a refusal never arms this.
            stalled_engines = [
                engine
                for engine in selected
                if engine in timed_out
                or _is_transport_stall_error_text(
                    (
                        stats_by_engine.get(engine)
                        or UniversalEngineStat(engine, 0, 0, False, "")
                    ).error
                )
            ]
            if (
                not any(buckets.values())
                and selected
                and len(stalled_engines) == len(selected)
            ):
                # Fail-faster (§2.1): count this consecutive all-empty
                # stalled batch FOR THIS QUERY. The cap gates RETRIES
                # only: it is read below from the pre-batch value, so
                # this batch still gets its own retry waves and only
                # the next batch for the SAME query at the cap skips
                # them. Other queries keep their own counters (test 29).
                _empty_streak_before = self._consecutive_empty_stalled_by_query.get(
                    _streak_key, 0
                )
                with self._lock:
                    self._slow_network_streak += 1
                    self._consecutive_empty_stalled_by_query[_streak_key] = (
                        _empty_streak_before + 1
                    )
                    slow_factor = self._slow_network_factor_locked()
                fetch_timeout = min(120.0, base_fetch_timeout * slow_factor)
                self._log(
                    f"[universal] slow network: all {len(selected)} engine(s) "
                    f"stalled (transport); timeout factor now {slow_factor:.2f}x "
                    f"(streak {self._slow_network_streak})"
                )
            else:
                _empty_streak_before = self._consecutive_empty_stalled_by_query.get(
                    _streak_key, 0
                )
            # Retries with backoff (Nov 22 user order): an all-empty batch
            # where at least one engine stalled on transport (a transport
            # stall, not a bot gate) gets up to _universal_empty_retry_waves()
            # more waves at 1.5x timeout — alternating route each wave, so a
            # retry does not re-queue behind the dead attempt's held
            # throttle slots (separate per-route namespace) or the same
            # dead route. A batch with any rows at all does not retry —
            # partial success is a result, not a failure. Bounded: at most
            # 2 retry waves (3 waves total), then the batch settles empty
            # and the resolver's own retry/rescue takes over.
            #
            # Test 25: the old retry list (``timed_out`` only) retried
            # yandex 7 times and never bing/flickr — they failed with
            # ReadTimeouts before their staggered deadlines, so they never
            # landed in timed_out. Retry every stalled engine instead.
            retry_waves = _universal_empty_retry_waves()
            # Fail-faster (§2.1): when the consecutive all-empty
            # stalled-batch counter had already reached the cap before
            # this batch, skip the remaining retry waves for this query
            # and return empty immediately, so the resolver's per-scene
            # chain moves to its next query / inline fallback instead of
            # re-paying the wall. Retries only — the first attempt above
            # always runs.
            if _empty_streak_before >= _universal_empty_streak_cap():
                self._log(
                    "[universal] empty-batch streak at cap "
                    f"({_empty_streak_before}>={_universal_empty_streak_cap()}); "
                    "skipping retry waves for this query"
                )
                retry_waves = 0
            retry_direct = not direct
            for _wave in range(retry_waves):
                if any(buckets.values()) or not stalled_engines:
                    break
                retry_engines = [
                    engine for engine in stalled_engines
                    if engine in self._engines
                    and self._bot_gated.get(engine, 0.0) <= time.monotonic()
                ]
                if not retry_engines:
                    break
                backoff = _universal_empty_retry_backoff_seconds()
                # The retry keeps its 1.5x on top of the (possibly
                # just-bumped) slow-network factor, capped at the 300s
                # engine ceiling so a single batch stays bounded.
                retry_engine_timeout = min(
                    300.0, base_engine_timeout * 1.5 * slow_factor
                )
                self._log(
                    f"[universal] empty batch with stalled engine(s) "
                    f"{','.join(retry_engines)}; retrying "
                    f"({_wave + 1}/{retry_waves}) after "
                    f"{backoff:.1f}s backoff "
                    f"(route={'direct' if retry_direct else 'proxy'}, "
                    f"timeout={retry_engine_timeout:.1f}s)"
                )
                if backoff:
                    time.sleep(backoff)
                retry_deadlines = {
                    engine: retry_engine_timeout + index * stagger
                    for index, engine in enumerate(retry_engines)
                }
                retry_finished = threading.Event()
                try:
                    retry_futures = {
                        self._executor.submit(
                            self._run_engine, engine, clean, limit,
                            max(1, int(page)), bool(safe_search), retry_finished,
                            retry_direct, fetch_timeout,
                        ): engine
                        for engine in retry_engines
                    }
                    retry_buckets, retry_stats, timed_out = self._collect_batch(
                        retry_futures, retry_deadlines, clean
                    )
                finally:
                    retry_finished.set()
                for engine, rows in retry_buckets.items():
                    if rows:
                        buckets[engine] = rows
                stats_by_engine.update(retry_stats)
                # Recompute the stalled set from the retry's own stats for
                # the next wave: engines that just failed with a transport
                # error get another wave; engines that returned rows or
                # failed with a refusal do not.
                stalled_engines = [
                    engine
                    for engine in retry_engines
                    if engine in timed_out
                    or _is_transport_stall_error_text(
                        (
                            retry_stats.get(engine)
                            or UniversalEngineStat(engine, 0, 0, False, "")
                        ).error
                    )
                ]
                retry_direct = not retry_direct
        finally:
            # Do not shut down the shared pool after each batch. Running HTTP
            # calls cannot be force-cancelled by Future.cancel(); closing the
            # executor here created late work that raced interpreter/test
            # teardown. Unfinished calls remain bounded by this manager's
            # fixed worker count and their own socket timeouts, and their
            # rows are harvested from _late_pending by collect_late_results.
            batch_finished.set()

        # Final re-check: engines from this or an earlier batch for this
        # query may have landed while this batch was running.  Harvest
        # them now instead of leaving them for the next scene's query.
        spooled.extend(self.collect_late_results(clean, wait_seconds=0.0))

        # Preserve engine diversity by round-robin interleaving, then remove
        # only exact canonical URL duplicates.  Spooled late rows from an
        # earlier batch for this query go first — they already survived a
        # full engine run — then the fresh batch, deduped against them.
        merged: List[UniversalImageResult] = []
        seen = set()
        for row in spooled:
            key = _canonical_url(row.image_url)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(row)
        max_len = max((len(rows) for rows in buckets.values()), default=0)
        for index in range(max_len):
            for engine in selected:
                rows = buckets.get(engine) or []
                if index >= len(rows):
                    continue
                row = rows[index]
                key = _canonical_url(row.image_url)
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(row)
        # Slow-network recovery (Nov 22): any rows at all — fresh or
        # salvaged from the late spool — prove the network is delivering
        # again, so the streak resets instead of holding the longer
        # deadline for the next scene. A still-empty batch keeps the
        # streak it earned (one bump per batch, at most).
        if merged:
            with self._lock:
                if self._slow_network_streak:
                    self._log(
                        "[universal] slow network: batch returned "
                        f"{len(merged)} result(s); timeout factor reset to 1.00x"
                    )
                    self._slow_network_streak = 0
                # Fail-faster (§2.1): any rows reset this query's
                # empty-streak entry — the network is delivering again.
                # Other queries keep their own streaks (test 29).
                self._consecutive_empty_stalled_by_query.pop(_streak_key, None)
        stats = [stats_by_engine.get(e, UniversalEngineStat(e, 0, 0, False, "engine timeout")) for e in selected]
        # Host learning (no reordering: engine round-robin order and relevance
        # are untouched). Tag each row with whether its image host is already
        # in the persistent seen-host store, so the resolver can prefer
        # already-approved hosts at download time without spending a fresh
        # per-host approval card. The store is learned from actual downloads
        # (see host_allowlist), never from discovery alone.
        try:
            from .host_allowlist import is_seen_host, load_seen_hosts

            _seen = load_seen_hosts()
            _new_hosts = set()
            annotated: List[UniversalImageResult] = []
            for _row in merged:
                _host = urlparse(_row.image_url).netloc.lower()
                if _host and not is_seen_host(_host, _seen):
                    _new_hosts.add(_host)
                annotated.append(
                    _dc_replace(
                        _row,
                        metadata={**dict(_row.metadata or {}), "host_seen": is_seen_host(_host, _seen)},
                    )
                )
            merged = annotated
            if _new_hosts:
                self._log(f"[universal] batch surfaced {len(_new_hosts)} unseen image host(s)")
        except Exception:
            pass
        response = UniversalSearchResponse(
            clean,
            len(merged),
            merged,
            stats,
            universal_source_diversity(merged),
        )
        with self._lock:
            self._last_response = response
        successes = sum(1 for stat in stats if stat.success)
        _dom = response.source_diversity.get("dominant_host") or "-"
        _dom_share = response.source_diversity.get("dominant_share") or 0.0
        self._log(
            f"[universal] batch complete results={response.total} "
            f"engines={successes}/{len(selected)} "
            f"dominant_source={_dom}:{float(_dom_share):.0%}"
        )
        return response

    async def search_async(self, query: str, **kwargs) -> UniversalSearchResponse:
        return await asyncio.to_thread(self.search, query, **kwargs)


def _result_source_row(row: UniversalImageResult) -> Dict[str, object]:
    """Map a scraper result to the source-host identity mapping."""
    return {
        "url": row.image_url,
        "source_url": row.source_url or row.image_url,
        "page_url": row.source_url or row.image_url,
    }


def universal_source_host_share(
    results: Iterable[UniversalImageResult],
) -> Dict[str, int]:
    """Count of scraper rows per publisher/source host (CDN fronts collapsed).

    ``i.pinimg.com`` image URLs behind Pinterest pin pages count as one
    source (``pinterest.com``); without the collapse a Pinterest-only
    batch looks diverse.
    """
    counts: Dict[str, int] = {}
    for row in results or []:
        host = _distinct_source_host(_result_source_row(row))
        if host:
            counts[host] = counts.get(host, 0) + 1
    return counts


def universal_source_diversity(
    results: Iterable[UniversalImageResult],
) -> Dict[str, object]:
    """Diversity summary for a merged batch: dominant host/share, host counts."""
    rows = list(results or [])
    counts = universal_source_host_share(rows)
    total = sum(counts.values())
    dominant_host, dominant_count, dominant_share = _distinct_dominant_source(
        [_result_source_row(row) for row in rows]
    )
    return {
        "hosts": counts,
        "host_count": len(counts),
        "total": total,
        "dominant_host": dominant_host,
        "dominant_count": dominant_count,
        "dominant_share": dominant_share,
        "needs_more_sources": _distinct_needs_more_sources(
            [_result_source_row(row) for row in rows]
        ),
    }


def universal_needs_more_sources(
    results: Iterable[UniversalImageResult],
    *,
    min_hosts: int = 2,
    max_share: float = 0.6,
) -> bool:
    """Whether a merged batch is too single-source to settle on."""
    rows = [_result_source_row(row) for row in (results or [])]
    return _distinct_needs_more_sources(rows, min_hosts=min_hosts, max_share=max_share)


def universal_result_to_candidate(row: UniversalImageResult, query: str = "") -> Dict[str, object]:
    """Map the standalone scraper result into Vidnets' resolver row contract."""
    engine = row.source_engine.casefold().replace(" ", "_") or "universal"
    title_is_query = bool(
        row.metadata.get("title_is_query")
        or _title_is_query_echo(row.title, query)
    )
    return {
        "url": row.image_url,
        "image_url": row.image_url,
        "preview_url": row.thumbnail_url or row.image_url,
        "thumbnail_url": row.thumbnail_url or row.image_url,
        "source_url": row.source_url or row.image_url,
        "page_url": row.source_url or row.image_url,
        "title": row.title,
        "description": row.description,
        "tags": str(row.metadata.get("tags") or ""),
        "source": "universal_scraper",
        "provider": f"universal_{engine}",
        "query": query,
        "domain": (urlparse(row.source_url or row.image_url).netloc or "").lower(),
        "width": row.width,
        "height": row.height,
        "metadata_pending": True,
        "title_is_query": title_is_query,
        "query_echo": title_is_query,
        "discovery_method": "universal_parallel_scraper",
        "universal_engine": row.source_engine,
        "license": str(row.metadata.get("license") or ""),
        "license_url": str(row.metadata.get("foreign_url") or ""),
        "rights_status": str(row.metadata.get("rights_status") or "unknown"),
        # Set by UniversalScraperManager.search from the persistent
        # seen-host store; the resolver uses it to prefer already-approved
        # hosts at download time. Absent (False) for rows from other lanes.
        "host_seen": bool(row.metadata.get("host_seen")),
    }


__all__ = [
    "UniversalImageResult",
    "UniversalEngineStat",
    "UniversalSearchResponse",
    "UniversalScraperManager",
    "universal_result_to_candidate",
    "universal_source_host_share",
    "universal_source_diversity",
    "universal_needs_more_sources",
]
