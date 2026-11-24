"""Muse-search web-image wave (test 30 fix, round 3).

Replaces the UniversalScraper HTML engines (Yandex / Bing / Flickr
search-page scraping) as the PRIMARY web-image retrieval source with
Muse's own direct-search pipeline: the existing Openverse and Wikimedia
Commons JSON-API adapters, run server-side in the resolver, never
through the inference worker pool.

This module is a COORDINATOR, not a new scraper: it fans out to the
existing ``OpenverseSource`` / ``WikimediaSource`` adapters and merges
their ``ImageCandidate`` rows into the exact resolver row shape. The
transport discipline is a port of the research pipeline's rules
(``scriptit/src/agents/researcher.js`` / ``utils/concurrency.js`` /
``research/resilience.js``): 3-at-a-time batching with a shared wave
deadline, per-source timeout / retry / circuit, Bing 5 s pacing +
decoy check (those two live in ``hybrid_search.search_bing`` /
``resolver._discover_hybrid_urls``).

Why the swap alone is not the fix: test 30 returned 280 search rows
and downloaded ZERO of them, so the download path is fixed alongside
(see ``hybrid_search.download_bulk`` direct-route retry, chunked
sub-batches, FIFO slot queue). If the egress itself times out the JSON
APIs past the wave's max wall (~120 s default) on both routes, this
module returns 0 too and the job falls back to universal (bounded,
one attempt) and then graphics — nothing here fixes a dead transport;
it only shrinks every request and fails fast instead of burning the
stage wall. Round 5 (test 32 fix) adds one transport retry under an
extended wall, so a slow-but-alive proxy (TTFB 30–115 s) still lands
rows instead of dying at the first adapter timeout.

Rules (design ``work/muse-search-web-design.md`` §2, as amended round 4,
test 31 fix, and round 5, test 32 fix):
- Wikimedia is the workhorse (500 req/hr unidentified, no daily cap)
  and the proven primary: proxy first, 55 s adapter timeout (env
  ``VIDZEO_WIKIMEDIA_TIMEOUT_SECONDS``), every query. The direct
  route is dead on this egress (Nov 23 live probe: TCP never
  completes), so no direct retry is attempted by default.
- Round 5: when the first wave ends with 0 rows AND a provider
  recorded a transport failure, the wave retries ONCE under an
  extended deadline (wall + ``VIDZEO_MUSE_SEARCH_RETRY_WALL_SECONDS``,
  default 60 s; total capped by
  ``VIDZEO_MUSE_SEARCH_MAX_WALL_SECONDS``, default 180 s) with a
  longer adapter timeout. Test 32 died at the 30 s timeout twice
  while the proxy was degraded past 30 s TTFB — the response may
  just be slow, not absent. A genuine empty answer is never retried.
  Only providers that failed empty are re-run.
- Openverse is the SECONDARY source (it 504s through the egress
  proxy, Nov 23 probe): proxy first (env
  ``VIDZEO_MUSE_SEARCH_OPENVERSE_ROUTE``, default ``proxy``), 45 s
  adapter timeout, first query per scene only (``q_idx == 0``), and
  job-budget-gated (anonymous 200/day cap): env
  ``VIDZEO_MUSE_SEARCH_OPENVERSE_JOB_BUDGET``, default 150. Once
  Wikimedia has delivered rows, Openverse gets only a short grace
  (env ``VIDZEO_MUSE_SEARCH_OPENVERSE_GRACE_SECONDS``, default 5 s)
  inside the wave wall — a hanging secondary must not hold the wave
  open. When Wikimedia returns nothing, Openverse gets the full wall.
- Wave wall 60 s (env ``VIDZEO_MUSE_SEARCH_WAVE_SECONDS``). Past the
  wall the wave resolves with whatever rows arrived; a provider
  still in flight is logged as ``abandoned at wall``, never as a
  0-row instant result.
- Per-source attempts: 1 on the first route by default. The old
  alternate-route (direct) retry is gated by env
  ``VIDZEO_MUSE_SEARCH_DIRECT_RETRY`` (default 0 — direct is dead;
  re-enable only if the egress recovers) and fires only when the
  source recorded a failure (a genuine empty answer is never
  retried).
- One wave per distinct query per job: non-empty results are cached
  on the manager (cleared by ``reset_muse_search_job_budget`` at
  job start). Empty results are NEVER cached — a transient empty
  answer is not permanent negative evidence, same rule as the
  resolver's search cache.
- No API keys anywhere: the Openverse OAuth path stays unused.
- Wikimedia rights enrichment is DEFERRED: it runs only for rows
  that passed download + validation, top 6, via
  :func:`enrich_validated_rows` — never on the hot path.
- Download URL preference: Wikimedia rows use the 1280 px ``thumburl``
  (CDN-served; the adapter already returns it as ``download_url``)
  with the full-res original retained in ``original_url``. Openverse
  rows keep the original URL: Openverse thumbnails are ~600 px on the
  long edge and would fail the resolver's 640 px validation floor, so
  the thumbnail stays in ``preview_url`` only.

Env knobs (defaults in parentheses):
- ``VIDZEO_MUSE_SEARCH_ENABLE`` (1): master switch; 0 restores the
  old source order without a code change.
- ``VIDZEO_MUSE_SEARCH_WAVE_SECONDS`` (60): wave wall.
- ``VIDZEO_MUSE_SEARCH_OPENVERSE_JOB_BUDGET`` (150).
- ``VIDZEO_MUSE_SEARCH_OPENVERSE_ROUTE`` (proxy): first route for
  Openverse; ``direct`` flips it back.
- ``VIDZEO_MUSE_SEARCH_OPENVERSE_GRACE_SECONDS`` (5): extra wait for
  Openverse once Wikimedia already delivered rows, inside the wall.
- ``VIDZEO_MUSE_SEARCH_DIRECT_RETRY`` (0): 1 re-enables the
  alternate-route (direct) retry after a recorded failure.
- ``VIDZEO_WIKIMEDIA_TIMEOUT_SECONDS`` (55): Wikimedia adapter timeout.
- ``VIDZEO_OPENVERSE_TIMEOUT_SECONDS`` (45): Openverse adapter timeout.
- ``VIDZEO_MUSE_SEARCH_RETRY_WALL_SECONDS`` (60): extra wall for one
  transport retry phase; 0 disables the retry.
- ``VIDZEO_MUSE_SEARCH_MAX_WALL_SECONDS`` (180): total wall ceiling
  for one wave including its retry. The resolver's shared-batch
  warm-up floors its batch wall at this value when the muse wave is
  in the batch, so a slow-but-successful wave's rows are not
  cancelled at the 45 s batch wall and orphaned.
- ``VIDZEO_MUSE_SEARCH_FALLBACK_UNIVERSAL`` (1): 0 skips the universal
  fallback entirely (diagnostic).
"""
from __future__ import annotations

import copy
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from services.sources import OpenverseSource, WikimediaSource

from .runtime import _web_log


# ---------------------------------------------------------------------------
# Env knobs
# ---------------------------------------------------------------------------

def muse_search_enabled() -> bool:
    return str(os.environ.get("VIDZEO_MUSE_SEARCH_ENABLE", "1") or "1").strip().lower() in {
        "1", "true", "yes", "on",
    }


def muse_search_wave_seconds() -> float:
    try:
        return max(
            2.0,
            min(120.0, float(os.environ.get("VIDZEO_MUSE_SEARCH_WAVE_SECONDS", "60") or 60)),
        )
    except (TypeError, ValueError):
        return 60.0


def muse_search_openverse_job_budget() -> int:
    try:
        return max(
            0,
            min(10000, int(os.environ.get("VIDZEO_MUSE_SEARCH_OPENVERSE_JOB_BUDGET", "150") or 150)),
        )
    except (TypeError, ValueError):
        return 150


def muse_search_openverse_direct_first() -> bool:
    # Round 4: proxy-first is the default — live probes (Nov 23) show
    # direct TCP never completes on this egress, so direct-first burns
    # the whole adapter timeout on a dead route before proxy is tried.
    return str(
        os.environ.get("VIDZEO_MUSE_SEARCH_OPENVERSE_ROUTE", "proxy") or "proxy"
    ).strip().lower() != "proxy"


def muse_search_direct_retry_enabled() -> bool:
    """Alternate-route (direct) retry after a recorded failure.

    Round 4: default OFF. Direct egress is dead (Nov 23 live probe),
    so retrying on it is pure waste. Re-enable with
    ``VIDZEO_MUSE_SEARCH_DIRECT_RETRY=1`` only if direct recovers.
    """
    return str(
        os.environ.get("VIDZEO_MUSE_SEARCH_DIRECT_RETRY", "0") or "0"
    ).strip().lower() in {"1", "true", "yes", "on"}


def muse_search_openverse_grace_seconds() -> float:
    """Extra wait for secondary Openverse once Wikimedia delivered rows.

    Round 4: 5 s by default, inside the wave wall (never past it).
    When Wikimedia returns nothing, Openverse gets the full wall.
    """
    try:
        return max(
            0.0,
            min(30.0, float(os.environ.get("VIDZEO_MUSE_SEARCH_OPENVERSE_GRACE_SECONDS", "5") or 5)),
        )
    except (TypeError, ValueError):
        return 5.0


def muse_search_fallback_universal_enabled() -> bool:
    return str(
        os.environ.get("VIDZEO_MUSE_SEARCH_FALLBACK_UNIVERSAL", "1") or "1"
    ).strip().lower() in {"1", "true", "yes", "on"}


def muse_search_retry_wall_seconds() -> float:
    """Extra wall for ONE transport retry phase (round 5).

    Test 32: both waves died at the 30 s Wikimedia timeout while the
    proxy was degraded past 30 s TTFB. When the first wave ends with
    0 rows AND the primary recorded a transport failure, the wave
    retries once under an extended deadline (wall + this budget).
    Default 60 s, clamped 0–120 s (0 disables the retry).
    """
    try:
        return max(
            0.0,
            min(120.0, float(os.environ.get("VIDZEO_MUSE_SEARCH_RETRY_WALL_SECONDS", "60") or 60)),
        )
    except (TypeError, ValueError):
        return 60.0


def muse_search_max_wave_wall_seconds() -> float:
    """Total wall ceiling for one wave including its retry (round 5).

    Caps wall + retry so an operator-raised wave wall cannot grow the
    wave without bound. Default 180 s, clamped 60–300 s. The resolver's
    shared-batch warm-up floors its batch wall at this value when the
    muse wave is in the batch — otherwise a slow-but-successful wave's
    rows are cancelled at the 45 s batch wall and orphaned.
    """
    try:
        return max(
            60.0,
            min(300.0, float(os.environ.get("VIDZEO_MUSE_SEARCH_MAX_WALL_SECONDS", "180") or 180)),
        )
    except (TypeError, ValueError):
        return 180.0


def muse_search_max_wave_wall() -> float:
    """Max wall one wave may spend, first attempt + one retry (round 5)."""
    return min(
        muse_search_max_wave_wall_seconds(),
        muse_search_wave_seconds() + muse_search_retry_wall_seconds(),
    )


# ---------------------------------------------------------------------------
# Row mapping
# ---------------------------------------------------------------------------

def candidate_to_row(candidate: Any, query: str) -> Optional[Dict[str, Any]]:
    """Map one adapter ``ImageCandidate`` to the exact resolver row shape.

    ``source`` is ``"muse_search"`` so the wave, audit, and telemetry
    name the lane; ``provider`` keeps the underlying API so
    ``_SOURCE_PRIORITY`` scoring keeps working. Wikimedia rows use the
    1280 px ``thumburl`` as the download URL (the adapter already
    returns it); the full-res original stays in ``original_url`` for
    provenance and as a download fallback. Openverse rows keep the
    original URL — its thumbnail is ~600 px and would fail the
    resolver's 640 px validation floor.
    """
    download_url = str(getattr(candidate, "download_url", "") or "").strip()
    if not download_url:
        return None
    metadata = getattr(candidate, "metadata", None) or {}
    if not isinstance(metadata, Mapping):
        metadata = {}
    provider = str(getattr(candidate, "source", "") or "").strip().lower()
    if provider not in {"wikimedia", "openverse"}:
        provider = str(metadata.get("provider") or "").strip().lower() or "muse_search"
    original_url = str(metadata.get("original_url") or "").strip()
    foreign_url = str(
        metadata.get("foreign_url") or metadata.get("detail_url") or ""
    ).strip()
    categories = getattr(candidate, "categories", None) or []
    if isinstance(categories, str):
        categories = [categories]
    return {
        "url": download_url,
        "image_url": download_url,
        "title": str(getattr(candidate, "title", "") or "")[:200],
        "source": "muse_search",
        "provider": provider,
        "query": query,
        "tags": list(categories),
        "description": str(getattr(candidate, "description", "") or "")[:300],
        "source_url": foreign_url,
        "page_url": foreign_url,
        "preview_url": str(getattr(candidate, "preview_url", "") or ""),
        "thumbnail_url": str(getattr(candidate, "preview_url", "") or ""),
        "domain": provider,
        "license": str(getattr(candidate, "license", "") or ""),
        "license_url": str(metadata.get("license_url") or ""),
        "attribution": str(metadata.get("attribution") or metadata.get("artist") or ""),
        "creator": str(metadata.get("creator") or metadata.get("artist") or ""),
        "width": int(getattr(candidate, "width", 0) or 0),
        "height": int(getattr(candidate, "height", 0) or 0),
        "download_url": download_url,
        "original_url": original_url,
        # Kept so deferred rights enrichment can find the Commons page.
        "page_id": str(metadata.get("page_id") or ""),
    }


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

class MuseSearchManager:
    """Fan out to the Openverse/Wikimedia adapters with wave discipline.

    One manager instance owns the per-job Openverse call budget. The
    resolver resets it at job start
    (``reset_muse_search_job_budget``); standalone callers and tests
    use a fresh manager (or call :meth:`reset_job_budget`) per job.
    """

    def __init__(
        self,
        *,
        openverse_source: Any = None,
        wikimedia_source: Any = None,
        openverse_budget: Optional[int] = None,
        wave_seconds: Optional[float] = None,
        openverse_direct_first: Optional[bool] = None,
        direct_retry: Optional[bool] = None,
        openverse_grace_seconds: Optional[float] = None,
        retry_wall_seconds: Optional[float] = None,
        max_wall_seconds: Optional[float] = None,
    ) -> None:
        self._openverse_source = openverse_source
        self._wikimedia_source = wikimedia_source
        self._openverse_budget_override = openverse_budget
        self._wave_seconds_override = wave_seconds
        self._openverse_direct_first_override = openverse_direct_first
        self._direct_retry_override = direct_retry
        self._openverse_grace_override = openverse_grace_seconds
        self._retry_wall_override = retry_wall_seconds
        self._max_wall_override = max_wall_seconds
        self._lock = threading.Lock()
        self._openverse_calls = 0
        self._openverse_budget_logged = False
        # Per-distinct-query result cache (round 4): one wave per query
        # per job; non-empty results only (see search()).
        self._query_cache: Dict[Tuple[str, int, int], List[Dict[str, Any]]] = {}
        # Round 10 (test 37 / job e4d610b9 fix): the last wave's transport
        # verdict. The wave swallows provider exceptions and returns [],
        # so a caller cannot tell a transport failure (timeouts on every
        # provider) from a genuine empty answer — and the stage-level
        # outage circuit needs exactly that signal. Set on every
        # search(): True only when the wave ended with 0 rows AND at
        # least one provider recorded a transport failure or was
        # abandoned at the wall. A genuine empty answer leaves it False.
        self._last_wave_transport_failed = False

    @property
    def last_wave_transport_failed(self) -> bool:
        """True when the last wave ended 0-row on a transport failure.

        Round 10: the stage outage circuit reads this after a
        muse-search wave returns [] to decide whether the egress is
        down (back off and retry later) or the query simply has no
        images (move on). Thread-safe read; set by search().
        """
        with self._lock:
            return bool(self._last_wave_transport_failed)

    def _retry_wall(self) -> float:
        if self._retry_wall_override is not None:
            return max(0.0, min(120.0, float(self._retry_wall_override)))
        return muse_search_retry_wall_seconds()

    def _max_wall(self) -> float:
        if self._max_wall_override is not None:
            return max(60.0, min(300.0, float(self._max_wall_override)))
        return muse_search_max_wave_wall_seconds()

    # -- lazy adapter construction (no network at import time) ----------

    @property
    def openverse_source(self) -> Any:
        if self._openverse_source is None:
            self._openverse_source = OpenverseSource()
        return self._openverse_source

    @property
    def wikimedia_source(self) -> Any:
        if self._wikimedia_source is None:
            self._wikimedia_source = WikimediaSource()
        return self._wikimedia_source

    # -- per-job Openverse budget ----------------------------------------

    def reset_job_budget(self) -> None:
        with self._lock:
            self._openverse_calls = 0
            self._openverse_budget_logged = False
            # The cache is per-job: a new job must not inherit another
            # job's rows for the same query text.
            self._query_cache = {}

    @property
    def openverse_calls(self) -> int:
        with self._lock:
            return self._openverse_calls

    def _openverse_allowed(self) -> bool:
        """First-query-per-scene Openverse call, while under the job budget."""
        budget = (
            self._openverse_budget_override
            if self._openverse_budget_override is not None
            else muse_search_openverse_job_budget()
        )
        with self._lock:
            if self._openverse_calls >= budget:
                if not self._openverse_budget_logged:
                    self._openverse_budget_logged = True
                    _web_log(
                        "muse-search wave: openverse skipped "
                        f"(job budget {self._openverse_calls}/{budget})"
                    )
                return False
            self._openverse_calls += 1
            return True

    # -- per-source call with route retry ---------------------------------

    def _call_with_direct(self, fn: Any, *args: Any, direct: bool = False, timeout: Optional[float] = None, **kwargs: Any) -> Any:
        """Call ``fn`` with ``direct=``/``timeout=``, tolerating doubles that predate them.

        Round 5: the transport retry passes a longer ``timeout``; fakes
        and older adapters that predate either keyword get a second
        call with the offending keyword dropped (one at a time).
        """
        call_kwargs = dict(kwargs)
        call_kwargs["direct"] = direct
        if timeout is not None:
            call_kwargs["timeout"] = timeout
        try:
            return fn(*args, **call_kwargs)
        except TypeError as exc:
            text = str(exc)
            for key in ("timeout", "direct"):
                if key in text and key in call_kwargs:
                    call_kwargs.pop(key)
                    return fn(*args, **call_kwargs)
            raise

    def _search_one(
        self,
        provider: str,
        query: str,
        limit: int,
        *,
        direct_first: bool,
        timeout: Optional[float] = None,
    ) -> Tuple[str, List[Any], float, str, int, bool]:
        """One provider, 1 attempt on the first route by default.

        Returns ``(provider, candidates, seconds, route, attempts,
        transport_failed)``. ``transport_failed`` is True when the
        empty result came from a recorded provider failure (timeout /
        transport error) rather than a genuine empty answer — the wave's
        transport retry (round 5) fires only on that flag. A genuine
        empty answer is never retried. The alternate-route (direct)
        retry fires only when the source recorded a failure AND direct
        retry is enabled (env ``VIDZEO_MUSE_SEARCH_DIRECT_RETRY``,
        default off — direct egress is dead, Nov 23 probe — so a
        second attempt on it would burn a whole adapter timeout for
        nothing).
        """
        source = (
            self.wikimedia_source if provider == "wikimedia"
            else self.openverse_source
        )
        direct_retry = (
            self._direct_retry_override
            if self._direct_retry_override is not None
            else muse_search_direct_retry_enabled()
        )
        started = time.monotonic()
        route = "direct" if direct_first else "proxy"
        for attempt in range(2):
            direct = direct_first if attempt == 0 else not direct_first
            route = "direct" if direct else "proxy"
            failures_before = int(getattr(source, "_failure_count", 0) or 0)
            try:
                candidates = self._call_with_direct(
                    source.search, query, limit=limit, direct=direct,
                    timeout=timeout,
                )
            except Exception:
                candidates = []
                # A raising adapter is a transport failure: retry once
                # on the alternate route, if that retry is enabled.
                if attempt == 0 and direct_retry:
                    time.sleep(0.5)
                    continue
                return provider, [], time.monotonic() - started, route, attempt + 1, True
            candidates = list(candidates or [])
            if candidates:
                return provider, candidates, time.monotonic() - started, route, attempt + 1, False
            failures_after = int(getattr(source, "_failure_count", 0) or 0)
            if failures_after <= failures_before:
                # Genuine empty answer — do not burn a retry on it.
                return provider, [], time.monotonic() - started, route, attempt + 1, False
            if attempt == 0 and direct_retry:
                time.sleep(0.5)
                continue
            # Failure recorded but the alternate-route retry is off
            # (direct is dead): one attempt is all this wave spends.
            return provider, [], time.monotonic() - started, route, attempt + 1, True
        return provider, [], time.monotonic() - started, route, 2, True

    # -- the wave ----------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        q_idx: int = 0,
        scene_index: Optional[int] = None,
        limit_per_source: int = 20,
        wait_until_complete: bool = False,
    ) -> List[Dict[str, Any]]:
        """Run one muse-search wave for ``query``; return resolver rows.

        Wikimedia (proxy-first primary) and Openverse (proxy-first
        secondary, first query per scene only, job-budget-gated) run
        in parallel under the wave wall. Once Wikimedia has delivered
        rows, Openverse gets only the grace period; a provider still
        in flight at the wall is abandoned, not reported as an
        instant empty answer. Non-empty results are cached per
        distinct query for the job; rows are merged and deduped by URL.

        Round 13 (Nov 23, user order: no timeouts at all for the web
        lane): when ``wait_until_complete`` is on, there is no wave
        wall and no grace abandonment — the wave waits until both
        providers settle (their adapter timeouts, 55 s Wikimedia /
        45 s Openverse, are the genuine per-request bounds). A
        provider still in flight is never cancelled at a clock.
        """
        if not muse_search_enabled():
            return []
        clean = re.sub(r"\s+", " ", str(query or "")).strip()
        if not clean:
            return []
        # Round 10: clear the transport verdict up front. Only a real
        # wave below may set it; early returns (degenerate query, cache
        # hit) must never leave a stale failure behind for the caller.
        with self._lock:
            self._last_wave_transport_failed = False
        try:
            from .query import is_degenerate_query
        except Exception:
            is_degenerate_query = None
        if is_degenerate_query is not None and is_degenerate_query(clean):
            _web_log(
                "muse-search wave: skipping degenerate query "
                f"'{clean[:48]}' (no searchable entity) — no provider call"
            )
            return []

        wave_seconds = (
            self._wave_seconds_override
            if self._wave_seconds_override is not None
            else muse_search_wave_seconds()
        )
        openverse_direct_first = (
            self._openverse_direct_first_override
            if self._openverse_direct_first_override is not None
            else muse_search_openverse_direct_first()
        )
        openverse_grace = (
            self._openverse_grace_override
            if self._openverse_grace_override is not None
            else muse_search_openverse_grace_seconds()
        )
        started = time.monotonic()
        scene_tag = (
            f"scene {int(scene_index) + 1} " if scene_index is not None else ""
        )

        # Round 4: one wave per distinct query per job. A repeated query
        # (the shared topic query re-asked by another scene, a scene
        # query retried after a stall) returns the cached rows with no
        # new provider call — the wave must not run 13x for 13 scenes.
        # Empty results are never cached: a transient empty answer is
        # not permanent negative evidence.
        cache_key = (
            re.sub(r"\s+", " ", clean).strip().casefold(),
            int(q_idx or 0),
            int(limit_per_source),
        )
        with self._lock:
            cached = self._query_cache.get(cache_key)
        if cached:
            _web_log(
                f"muse-search wave {scene_tag}query='{clean[:48]}' "
                f"cache hit ({len(cached)} rows, no provider call)"
            )
            return copy.deepcopy(cached)

        def _run_phase(
            deadline: float,
            timeout: Optional[float],
            providers: Tuple[str, ...],
        ) -> Tuple[Dict[str, Tuple[str, List[Any], float, str, int, bool]], set, Dict[str, str]]:
            """One wave phase: the given providers in parallel under ``deadline``.

            Wikimedia (proxy-first primary) gets up to the deadline; once
            it has delivered rows, Openverse (secondary) gets only the
            grace period. A provider still in flight at the deadline is
            abandoned, not reported as an instant empty answer. Returns
            ``(results, submitted, abandoned)`` with ``_search_one``'s
            6-tuple result shape.
            """
            phase_pool = ThreadPoolExecutor(
                max_workers=3, thread_name_prefix="muse-search"
            )
            futures: Dict[Any, str] = {}
            results: Dict[str, Tuple[str, List[Any], float, str, int, bool]] = {}
            submitted: set = set()
            abandoned: Dict[str, str] = {}
            try:
                wikimedia_future = None
                openverse_future = None
                if "wikimedia" in providers:
                    wikimedia_future = phase_pool.submit(
                        self._search_one, "wikimedia", clean, limit_per_source,
                        direct_first=False, timeout=timeout,
                    )
                    futures[wikimedia_future] = "wikimedia"
                    submitted.add("wikimedia")
                if "openverse" in providers and int(q_idx or 0) == 0 and self._openverse_allowed():
                    openverse_future = phase_pool.submit(
                        self._search_one, "openverse", clean, limit_per_source,
                        direct_first=openverse_direct_first, timeout=timeout,
                    )
                    futures[openverse_future] = "openverse"
                    submitted.add("openverse")
                # Openverse is first-query-per-scene only (§2.2).
                wikimedia_completed = False
                wikimedia_done_at: Optional[float] = None
                if wikimedia_future is not None:
                    # Phase 1 — the primary (Wikimedia) gets up to the deadline.
                    # Round 13: in wait mode the deadline is infinite, so
                    # wait indefinitely (``timeout=None``) — the adapter's
                    # own timeout bounds the call. ``wait`` with an
                    # infinite float timeout is not portable; None is.
                    _phase_timeout = (
                        None
                        if not math.isfinite(deadline)
                        else max(0.1, deadline - time.monotonic())
                    )
                    done, _pending = wait(
                        {wikimedia_future},
                        timeout=_phase_timeout,
                    )
                    wikimedia_completed = wikimedia_future in done
                    if wikimedia_completed:
                        wikimedia_done_at = time.monotonic()
                        try:
                            results["wikimedia"] = wikimedia_future.result()
                        except Exception:
                            results["wikimedia"] = (
                                "wikimedia", [], time.monotonic() - started,
                                "proxy", 0, True,
                            )
                # Phase 2 — Openverse (secondary) gets the remaining
                # deadline, capped by the grace once Wikimedia already
                # delivered rows. A 504-hanging secondary must not hold
                # the wave open after the primary has answered.
                # Round 13: in wait mode there is no grace cap — wait
                # for the secondary to settle too (its adapter timeout
                # bounds it); abandoning it at a clock is the kill this
                # fix removes.
                if openverse_future is not None:
                    wikimedia_rows = len(results.get(
                        "wikimedia", ("", [], 0.0, "", 0, False),
                    )[1] or [])
                    if wait_until_complete:
                        ov_deadline = deadline
                    elif wikimedia_completed and wikimedia_rows > 0:
                        ov_deadline = min(
                            deadline,
                            (wikimedia_done_at or started) + float(openverse_grace),
                        )
                    else:
                        ov_deadline = deadline
                    remaining = (
                        None
                        if not math.isfinite(ov_deadline)
                        else max(0.0, ov_deadline - time.monotonic())
                    )
                    if remaining is None or remaining > 0:
                        done, _pending = wait({openverse_future}, timeout=remaining)
                        if openverse_future in done:
                            try:
                                results["openverse"] = openverse_future.result()
                            except Exception:
                                results["openverse"] = (
                                    "openverse", [], time.monotonic() - started,
                                    "proxy", 0, True,
                                )
                    for future in (wikimedia_future, openverse_future):
                        if future is None:
                            continue
                        provider = futures[future]
                        if provider not in results:
                            # Grace-abandoned when the primary already
                            # delivered rows; wall-abandoned otherwise.
                            reason = (
                                "grace"
                                if wikimedia_completed and wikimedia_rows > 0
                                else "wall"
                            )
                            abandoned[provider] = reason
                            future.cancel()
                elif wikimedia_future is not None and "wikimedia" not in results:
                    abandoned["wikimedia"] = "wall"
                    wikimedia_future.cancel()
            finally:
                phase_pool.shutdown(wait=False, cancel_futures=True)
            return results, submitted, abandoned

        # Round 13: in wait mode there is no wave wall — the first
        # phase waits until both providers settle (their adapter
        # timeouts bound the calls).
        wall_deadline = float("inf") if wait_until_complete else started + float(wave_seconds)
        results, submitted, abandoned = _run_phase(
            wall_deadline, None, ("wikimedia", "openverse"),
        )

        def _merged_rows(
            results: Dict[str, Tuple[str, List[Any], float, str, int, bool]],
        ) -> List[Dict[str, Any]]:
            merged: List[Dict[str, Any]] = []
            seen: set = set()
            for provider in ("wikimedia", "openverse"):
                _prov, candidates, _secs, _route, _attempts, _failed = results.get(
                    provider, (provider, [], 0.0, "proxy", 0, False)
                )
                for candidate in candidates or []:
                    row = candidate_to_row(candidate, clean)
                    if row is None:
                        continue
                    key = str(row.get("url") or "").strip().casefold()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    merged.append(row)
            return merged

        rows = _merged_rows(results)

        # Round 5 — transport retry (test 32 fix). Test 32's wave died
        # at the Wikimedia timeout twice while the proxy was degraded
        # past 30 s TTFB; the response may just be slow, not absent.
        # When the first phase ends with 0 rows AND a provider recorded
        # a transport failure (or was abandoned at the wall), run ONE
        # retry phase under an extended deadline with a longer adapter
        # timeout — long enough for a 55–115 s TTFB to land. A genuine
        # empty answer (no failure recorded) is never retried, and a
        # wave that already holds rows never retries. Only providers
        # that failed empty are re-run; a provider that answered stays
        # answered.
        retried: set = set()
        retry_wall = self._retry_wall()
        max_wall = min(self._max_wall(), float(wave_seconds) + retry_wall)
        # Round 13: in wait mode the retry phase has no wall either —
        # it waits until the retried providers settle.
        extended_deadline = float("inf") if wait_until_complete else started + max_wall
        effective_wall = float(wave_seconds)
        if not rows and retry_wall > 0 and extended_deadline > time.monotonic():
            needs_retry = set()
            for provider in ("wikimedia", "openverse"):
                if provider not in submitted:
                    continue
                _prov, candidates, _secs, _route, _attempts, failed = results.get(
                    provider, (provider, [], 0.0, "proxy", 0, False)
                )
                if candidates:
                    continue
                if failed or provider in abandoned:
                    needs_retry.add(provider)
            if needs_retry:
                # Round 13: in wait mode the retry has no wall; the
                # adapter timeout (None = env default) bounds the call.
                retry_timeout = (
                    None
                    if wait_until_complete
                    else max(5.0, extended_deadline - time.monotonic() - 2.0)
                )
                _web_log(
                    f"muse-search wave {scene_tag}query='{clean[:48]}' "
                    f"transport retry: {','.join(sorted(needs_retry))} "
                    f"failed empty, retrying under +{retry_wall:.0f}s wall "
                    + (
                        "(no wall; adapter timeout)"
                        if retry_timeout is None
                        else f"(timeout {retry_timeout:.0f}s)"
                    )
                )
                retry_results, retry_submitted, retry_abandoned = _run_phase(
                    extended_deadline, retry_timeout,
                    tuple(sorted(needs_retry)),
                )
                for provider in needs_retry:
                    if provider in retry_results:
                        results[provider] = retry_results[provider]
                    if provider in retry_abandoned:
                        abandoned[provider] = retry_abandoned[provider]
                    elif provider in abandoned:
                        del abandoned[provider]
                submitted |= retry_submitted
                retried = set(needs_retry)
                effective_wall = max_wall
                rows = _merged_rows(results)
                _web_log(
                    f"muse-search wave {scene_tag}query='{clean[:48]}' "
                    f"retry merged={len(rows)} in "
                    f"{time.monotonic() - started:.1f}s"
                )

        def _summary(provider: str) -> str:
            # Round 4: a provider still in flight at the wall is
            # "abandoned at wall", and one never submitted is
            # "skipped" — never a fake instant 0-row result. The old
            # default (0 rows, 0.0s, proxy) made a hung provider look
            # like a fast genuine empty answer (test 31 forensics).
            if provider in abandoned:
                if abandoned[provider] == "grace":
                    return (
                        f"{provider}=abandoned after primary grace "
                        f"({openverse_grace:.0f}s)"
                    )
                return f"{provider}=abandoned at wall ({effective_wall:.0f}s)"
            if provider not in submitted:
                return f"{provider}=skipped"
            _prov, candidates, secs, route, _attempts, _failed = results.get(
                provider, (provider, [], 0.0, "proxy", 0, False)
            )
            return f"{provider}={len(candidates or [])} rows ({secs:.1f}s, {route})"

        _web_log(
            f"muse-search wave {scene_tag}query='{clean[:48]}' "
            f"{_summary('wikimedia')} {_summary('openverse')} "
            f"merged={len(rows)} in {time.monotonic() - started:.1f}s"
            + (f" retried={','.join(sorted(retried))}" if retried else "")
        )
        # Round 10: record the wave's transport verdict for the stage
        # outage circuit. A 0-row wave where a provider recorded a
        # transport failure (timeout / connection error) or was
        # abandoned at the wall means the egress failed, not the query;
        # a genuine empty answer (providers answered, no failure) means
        # the query has no images. The flag is read by the caller right
        # after search() returns, while this manager's most recent wave
        # is still this one for that caller.
        with self._lock:
            self._last_wave_transport_failed = bool(
                not rows
                and (
                    any(
                        bool(t[5])
                        for t in results.values()
                        if isinstance(t, tuple) and len(t) >= 6
                    )
                    or bool(abandoned)
                )
            )
        if rows:
            with self._lock:
                self._query_cache[cache_key] = copy.deepcopy(rows)
        return rows


# ---------------------------------------------------------------------------
# Module-level default manager (the resolver's lane)
# ---------------------------------------------------------------------------

_DEFAULT_MANAGER = MuseSearchManager()


def search_muse_sources(
    query: str,
    *,
    q_idx: int = 0,
    scene_index: Optional[int] = None,
    limit_per_source: int = 20,
    manager: Optional[MuseSearchManager] = None,
    wait_until_complete: bool = False,
) -> List[Dict[str, Any]]:
    """Run one muse-search wave via the default (job-shared) manager."""
    return (manager or _DEFAULT_MANAGER).search(
        query,
        q_idx=q_idx,
        scene_index=scene_index,
        limit_per_source=limit_per_source,
        wait_until_complete=wait_until_complete,
    )


def muse_search_last_wave_transport_failed(
    manager: Optional[MuseSearchManager] = None,
) -> bool:
    """True when the manager's last wave ended 0-row on a transport failure.

    Round 10: the stage outage circuit calls this right after a
    muse-search wave returns [] — it is the only way to tell an
    egress failure from a genuine empty answer, since the wave itself
    swallows provider exceptions. False when the last wave returned
    rows, answered empty, or never ran.
    """
    return bool(
        (manager or _DEFAULT_MANAGER).last_wave_transport_failed
    )


def reset_muse_search_job_budget(
    manager: Optional[MuseSearchManager] = None,
) -> None:
    """Reset the per-job Openverse budget (call at job start)."""
    (manager or _DEFAULT_MANAGER).reset_job_budget()


def enrich_validated_rows(
    rows: Iterable[Dict[str, Any]],
    *,
    limit: int = 6,
    direct: bool = False,
) -> List[Dict[str, Any]]:
    """Batch-fetch Wikimedia rights metadata for validated rows only.

    Deferred enrichment (§2.2): the hot path never spends a second API
    call per query on rights metadata the vision gate never reads.
    Call this on rows that already passed download + validation, top
    ``limit``, before the audit manifest is written. Rows are mutated
    in place (license / attribution filled from ``extmetadata``) and
    returned as a list. Never raises.
    """
    rows = list(rows or [])
    if not rows:
        return rows
    try:
        manager = _DEFAULT_MANAGER
        wikimedia_rows = [
            row for row in rows
            if str(row.get("provider") or "").lower() == "wikimedia"
        ][: max(0, int(limit))]
        if not wikimedia_rows:
            return rows
        from services.sources.base_source import ImageCandidate

        candidates = []
        for row in wikimedia_rows:
            candidates.append(ImageCandidate(
                id=str(row.get("id") or ""),
                source="wikimedia",
                title=str(row.get("title") or ""),
                description=str(row.get("description") or ""),
                download_url=str(row.get("url") or ""),
                preview_url=str(row.get("preview_url") or ""),
                width=int(row.get("width") or 0),
                height=int(row.get("height") or 0),
                license=str(row.get("license") or ""),
                metadata={
                    "page_id": str(row.get("page_id") or ""),
                },
            ))
        enriched = manager.wikimedia_source.enrich_rights(
            candidates, limit=len(candidates), direct=direct
        )
        by_url = {
            str(c.download_url or ""): c for c in (enriched or [])
        }
        for row in wikimedia_rows:
            candidate = by_url.get(str(row.get("url") or ""))
            if candidate is None:
                continue
            if getattr(candidate, "license", ""):
                row["license"] = str(candidate.license or "")
            meta = dict(candidate.metadata or {})
            if meta.get("attribution"):
                row["attribution"] = str(meta["attribution"])
            if meta.get("license_url"):
                row["license_url"] = str(meta["license_url"])
            if meta.get("creator"):
                row["creator"] = str(meta["creator"])
    except Exception:
        pass
    return rows
