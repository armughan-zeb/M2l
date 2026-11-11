"""Shared controls for fast, rights-aware web-image discovery.

The resolver runs many scenes concurrently and may be invoked again by repair
passes.  This module keeps those calls bounded, removes obvious cross-job query
contamination, and records a source-by-source retrieval funnel.
"""
from __future__ import annotations

import re
import threading
import time
from collections import Counter, defaultdict, deque
from typing import Callable, Dict, Iterable, List, Optional, Tuple


# Canonical web-image provider map.  Keep this in the broker (the shared
# boundary used by the resolver and Mixed Media) so new callers do not need to
# duplicate the provider list or accidentally omit a fallback lane.
WEB_IMAGE_SOURCE_CATALOG: Tuple[Dict[str, object], ...] = (
    {"name": "primary_archive", "tier": 0, "kind": "hybrid", "role": "official_publisher_discovery", "default": True},
    {"name": "universal_scraper", "tier": 0, "kind": "universal", "role": "parallel_multiengine_scraper", "default": True},
    {"name": "yandex", "tier": 0, "kind": "api", "role": "fast_scraper", "default": True},
    {"name": "bing_hybrid", "tier": 0, "kind": "hybrid", "role": "fast_scraper", "default": True},
    {"name": "ddg_hybrid", "tier": 0, "kind": "hybrid", "role": "fast_scraper", "default": False},
    {"name": "flickr_api", "tier": 0, "kind": "flickr_api", "role": "fast_api", "default": True},
    {"name": "openverse", "tier": 1, "kind": "api", "role": "rights_friendly_api", "default": False},
    {"name": "wikimedia", "tier": 1, "kind": "api", "role": "rights_friendly_api", "default": True},
    {"name": "google_images", "tier": 1, "kind": "api", "role": "html_scraper", "default": True},
    {"name": "duckduckgo", "tier": 1, "kind": "api", "role": "html_scraper", "default": False},
    {"name": "brave_images", "tier": 1, "kind": "api", "role": "optional_api", "default": False},
    {"name": "searxng_images", "tier": 1, "kind": "api", "role": "metasearch", "default": False},
    {"name": "getty_bing", "tier": 2, "kind": "hybrid", "role": "editorial_site_filter", "default": False},
    {"name": "getty_ddg", "tier": 2, "kind": "hybrid", "role": "editorial_site_filter", "default": False},
    {"name": "pinterest", "tier": 3, "kind": "gdl", "role": "gallery_dl", "default": False},
    {"name": "reddit", "tier": 3, "kind": "api", "role": "community_api", "default": False},
    {"name": "flickr", "tier": 3, "kind": "gdl", "role": "gallery_dl", "default": False},
)


def web_image_source_catalog() -> List[Dict[str, object]]:
    """Return a copy of the complete provider map for audits and UI clients."""
    return [dict(row) for row in WEB_IMAGE_SOURCE_CATALOG]


def web_image_fallback_layers() -> List[Dict[str, object]]:
    """Return the ordered, fail-soft web retrieval waterfall.

    Providers within a tier may run concurrently.  The next tier is a rescue
    layer used when the preceding tier is empty, unhealthy, or unable to
    produce enough distinct candidates.
    """
    grouped: Dict[int, List[str]] = defaultdict(list)
    for row in WEB_IMAGE_SOURCE_CATALOG:
        grouped[int(row["tier"])].append(str(row["name"]))
    labels = {
        0: "fast_primary",
        1: "rights_and_html_rescue",
        2: "editorial_site_rescue",
        3: "slow_gallery_last_resort",
    }
    return [
        {"tier": tier, "name": labels.get(tier, f"tier_{tier}"), "providers": providers}
        for tier, providers in sorted(grouped.items())
    ]


_SPORTS_TERMS = {
    "football", "soccer", "fifa", "stadium", "supporters", "fanbase",
    "cricket", "batting", "bowling", "innings", "wicket", "t20", "odi",
    "ipl", "touchdown", "quarterback", "basketball", "baseball", "rugby",
}
_SPORTS_WEAK_TERMS = {"match", "matches", "game", "games", "team", "teams"}
_NON_PERSON_SUBJECT_TERMS = {
    "rms", "ship", "shipwreck", "liner", "titanic", "war", "battle",
    "disaster", "earthquake", "hurricane", "tsunami", "city", "country",
    "ocean", "sea", "aircraft", "company", "technology",
}


def _tokens(text: str) -> List[str]:
    return re.findall(r"[^\W_]+(?:['’\-][^\W_]+)*", str(text or ""), re.UNICODE)


def _sports_context(subject: str, scene_text: str) -> bool:
    """Require a strong sports signal; the word ``match`` alone is ambiguous."""
    context = set(token.lower() for token in _tokens(f"{subject} {scene_text}"))
    return bool(context & _SPORTS_TERMS)


def sanitize_source_query(query: str, subject: str = "", scene_text: str = "") -> str:
    """Repair a provider query without changing a legitimate sports query.

    Cross-job leakage observed in production appended terms such as
    ``football portrait`` and ``match`` to maritime-documentary searches.
    This function removes those terms only when the active subject/scene has no
    strong sports signal, collapses adjacent duplicates, and restores the
    active subject anchor if it was lost.
    """
    raw_tokens = _tokens(query)
    if not raw_tokens:
        return ""

    sports_context = _sports_context(subject, scene_text)
    had_sports_leak = False
    cleaned: List[str] = []
    for token in raw_tokens:
        lower = token.lower()
        if not sports_context and (lower in _SPORTS_TERMS or lower in _SPORTS_WEAK_TERMS):
            had_sports_leak = True
            continue
        if cleaned and cleaned[-1].lower() == lower:
            continue
        cleaned.append(token)

    subject_tokens = _tokens(subject)
    subject_lower = {token.lower() for token in subject_tokens}
    looks_non_person = bool(subject_lower & _NON_PERSON_SUBJECT_TERMS)
    if had_sports_leak and looks_non_person:
        cleaned = [token for token in cleaned if token.lower() != "portrait"]

    cleaned_lower = {token.lower() for token in cleaned}
    significant_subject = {
        token.lower() for token in subject_tokens
        if len(token) >= 3 and token.lower() not in {"the", "and", "for"}
    }
    titled_tokens = [
        token for token in cleaned
        if token[:1].isupper() and token.lower() not in {"the", "a", "an"}
    ]
    has_named_anchor = (
        any(token.isupper() and len(token) >= 2 for token in cleaned)
        or len(titled_tokens) >= 2
    )
    if (
        significant_subject
        and not (cleaned_lower & significant_subject)
        and not has_named_anchor
    ):
        # A capitalized facet (e.g. ``Lookouts portrait``) is not a safe
        # replacement for the documentary container. Always restore the
        # canonical topic anchor so provider results cannot drift to generic
        # lookouts, tomatoes, or unrelated people.
        cleaned = subject_tokens + cleaned

    # Provider relevance generally degrades after a compact subject + visual
    # object phrase.  Ten tokens leaves room for a proper name and a date.
    return " ".join(cleaned[:10]).strip()


def sanitize_query_pack(
    queries: Iterable[str],
    subject: str = "",
    scene_text: str = "",
    limit: Optional[int] = None,
) -> Tuple[List[str], List[Dict[str, str]]]:
    """Return de-duplicated provider queries plus an audit trail of repairs."""
    clean_queries: List[str] = []
    repairs: List[Dict[str, str]] = []
    seen = set()
    for query in queries or []:
        original = re.sub(r"\s+", " ", str(query or "")).strip()
        repaired = sanitize_source_query(original, subject, scene_text)
        if original and repaired and original != repaired:
            repairs.append({"original": original, "repaired": repaired})
        key = repaired.casefold()
        if repaired and key not in seen:
            seen.add(key)
            clean_queries.append(repaired)
        if limit is not None and len(clean_queries) >= max(0, int(limit)):
            break
    return clean_queries, repairs


def fair_round_robin_indices(group_keys: Iterable[str]) -> List[int]:
    """Interleave scene indexes by container/subject without reordering a group.

    Provider budgets used to be consumed by the first scenes submitted to the
    executor.  This deterministic ordering gives every list item/container its
    first retrieval slot before any item receives its second slot.
    """
    buckets: Dict[str, deque] = {}
    order: List[str] = []
    for index, raw_key in enumerate(group_keys or []):
        key = re.sub(r"\s+", " ", str(raw_key or "")).strip().casefold()
        key = key or f"__scene_{index}"
        if key not in buckets:
            buckets[key] = deque()
            order.append(key)
        buckets[key].append(index)
    result: List[int] = []
    while order:
        next_order: List[str] = []
        for key in order:
            bucket = buckets[key]
            if bucket:
                result.append(bucket.popleft())
            if bucket:
                next_order.append(key)
        order = next_order
    return result


class SourceHealth:
    """Windowed circuit breaker for flaky provider networks.

    A provider that fails with connection-level errors several times inside a
    short window is skipped for a cooldown period instead of letting every
    scene query burn its retry budget against a dead endpoint.  A cheap DNS
    probe at stage start trips the breaker for hosts that cannot resolve at
    all (e.g. a poisoned ``commons.wikimedia.org`` entry).
    """

    def __init__(
        self,
        failure_threshold: int = 2,
        cooldown_seconds: float = 45.0,
        window_seconds: float = 90.0,
    ) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(1.0, float(cooldown_seconds))
        self.window_seconds = max(5.0, float(window_seconds))
        self._lock = threading.Lock()
        self._failures: Dict[str, deque] = defaultdict(deque)
        self._down_until: Dict[str, float] = {}
        self._preflight_done: Dict[str, bool] = {}

    def preflight(self, source: str, host: str) -> bool:
        """Probe one provider host once; permanent DNS failures trip the breaker."""
        if not source or not host or source in self._preflight_done:
            return True
        self._preflight_done[source] = True
        try:
            import socket
            socket.getaddrinfo(
                host, None, socket.AF_INET, socket.SOCK_STREAM, 0, socket.AI_ADDRCONFIG
            )
        except Exception as exc:
            self.note_failure(source, f"preflight dns: {type(exc).__name__}")
            return False
        return True

    def healthy(self, source: str) -> bool:
        if not source:
            return False
        with self._lock:
            return self._down_until.get(source, 0.0) <= time.monotonic()

    def note_success(self, source: str) -> None:
        if not source:
            return
        with self._lock:
            self._failures.pop(source, None)
            self._down_until.pop(source, None)

    def note_failure(self, source: str, reason: str = "") -> None:
        if not source:
            return
        now = time.monotonic()
        with self._lock:
            window = self._failures.setdefault(source, deque())
            while window and now - window[0] > self.window_seconds:
                window.popleft()
            window.append(now)
            threshold = (self.failure_threshold + 4) if source == "universal_scraper" else self.failure_threshold
            cooldown = 15.0 if source == "universal_scraper" else self.cooldown_seconds
            if len(window) >= threshold and self._down_until.get(source, 0.0) <= now:
                self._down_until[source] = now + cooldown
                print(
                    f"   [web] provider '{source}' temporarily disabled "
                    f"({len(window)} failures in {self.window_seconds:.0f}s window, "
                    f"reason: {reason or 'unknown'})",
                    flush=True,
                )

    def trip(self, source: str, reason: str = "") -> None:
        """Open a provider circuit immediately for a definitive outage.

        Some providers return a bot/robot challenge rather than a transport
        exception. Retrying that response for every scene only increases
        throttling and wastes the job deadline, so callers can trip the
        per-job breaker after one definitive challenge.
        """
        if not source:
            return
        now = time.monotonic()
        with self._lock:
            # Multiple scene workers can observe the same definitive
            # challenge at nearly the same time. Keep the first cooldown and
            # emit one actionable log line instead of extending the outage or
            # spamming the log once per worker.
            if self._down_until.get(source, 0.0) > now:
                return
            self._failures.pop(source, None)
            self._down_until[source] = now + self.cooldown_seconds
            print(
                f"   [web] provider '{source}' temporarily disabled "
                f"(definitive response, reason: {reason or 'unknown'})",
                flush=True,
            )


class SourceTelemetry:
    """Thread-safe source discovery/acceptance funnel for one resolver call."""

    def __init__(self) -> None:
        self._providers: Dict[str, Counter] = defaultdict(Counter)
        self._repairs: List[Dict[str, object]] = []
        self._lock = threading.Lock()

    def record(self, source: str, event: str, count: int = 1) -> None:
        if not source or not event or count <= 0:
            return
        with self._lock:
            self._providers[str(source)][str(event)] += int(count)

    def record_query_repairs(
        self,
        scene_number: int,
        repairs: Iterable[Dict[str, str]],
    ) -> None:
        rows = list(repairs or [])
        if not rows:
            return
        with self._lock:
            for row in rows:
                if len(self._repairs) >= 200:
                    break
                self._repairs.append({
                    "scene_number": int(scene_number),
                    "original": str(row.get("original") or ""),
                    "repaired": str(row.get("repaired") or ""),
                })

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            providers = {
                source: dict(sorted(counter.items()))
                for source, counter in sorted(self._providers.items())
            }
            repairs = list(self._repairs)

        totals: Counter = Counter()
        for counter in providers.values():
            totals.update(counter)
        for source, counter in providers.items():
            discovered = int(counter.get("discovered", 0))
            accepted = int(counter.get("accepted", 0))
            counter["acceptance_rate"] = round(accepted / discovered, 4) if discovered else 0.0
        return {
            "providers": providers,
            "totals": dict(sorted(totals.items())),
            "query_repairs": repairs,
        }


class DiscoveryCache:
    """TTL cache with in-flight coalescing for concurrent provider searches."""

    def __init__(
        self,
        success_ttl: float = 24 * 60 * 60,
        empty_ttl: float = 60,
        max_entries: int = 2048,
        max_provider_requests: Optional[int] = None,
    ) -> None:
        self.success_ttl = float(success_ttl)
        self.empty_ttl = float(empty_ttl)
        self.max_entries = max(32, int(max_entries))
        self.max_provider_requests = (
            None
            if max_provider_requests is None
            else max(1, int(max_provider_requests))
        )
        self.provider_requests_started = 0
        self.provider_requests_rejected = 0
        self.entries: Dict[Tuple[str, str], object] = {}
        self.inflight: Dict[Tuple[str, str], threading.Event] = {}
        self.lock = threading.RLock()

    def request_budget_snapshot(self) -> Dict[str, Optional[int]]:
        with self.lock:
            return {
                "cap": self.max_provider_requests,
                "started": self.provider_requests_started,
                "rejected": self.provider_requests_rejected,
            }

    @staticmethod
    def key(source: str, query: str) -> Tuple[str, str]:
        normalized = re.sub(r"\s+", " ", str(query or "")).strip().casefold()
        return str(source), normalized

    def _read_locked(self, key: Tuple[str, str], now: float) -> Optional[List[Dict]]:
        entry = self.entries.get(key)
        # Backward compatibility for tests or callers that directly seed the
        # old list-valued resolver cache.
        if isinstance(entry, list):
            return list(entry)
        if not isinstance(entry, dict):
            return None
        if float(entry.get("expires_at") or 0.0) <= now:
            self.entries.pop(key, None)
            return None
        return list(entry.get("results") or [])

    def _store_locked(self, key: Tuple[str, str], results: List[Dict], now: float) -> None:
        ttl = self.success_ttl if results else self.empty_ttl
        self.entries[key] = {
            "created_at": now,
            "expires_at": now + ttl,
            "results": list(results),
        }
        if len(self.entries) <= self.max_entries:
            return
        ordered = sorted(
            self.entries.items(),
            key=lambda pair: float(pair[1].get("created_at") or 0.0)
            if isinstance(pair[1], dict) else 0.0,
        )
        for stale_key, _entry in ordered[:len(self.entries) - self.max_entries]:
            self.entries.pop(stale_key, None)

    def prune(self) -> None:
        now = time.time()
        with self.lock:
            for key in list(self.entries):
                self._read_locked(key, now)

    def peek(self, source: str, query: str) -> Optional[List[Dict]]:
        """Return a cached value without starting or waiting for a fetch."""
        key = self.key(source, query)
        with self.lock:
            return self._read_locked(key, time.time())

    def get_or_fetch(
        self,
        source: str,
        query: str,
        fetch: Callable[[], List[Dict]],
        telemetry: Optional[SourceTelemetry] = None,
        wait_timeout: Optional[float] = 15.0,
    ) -> List[Dict]:
        key = self.key(source, query)
        if telemetry:
            telemetry.record(source, "query_requests")

        owner = False
        request_budget_exhausted = False
        with self.lock:
            cached = self._read_locked(key, time.time())
            if cached is not None:
                if telemetry:
                    telemetry.record(source, "cache_hits")
                return cached
            event = self.inflight.get(key)
            if event is None:
                if (
                    self.max_provider_requests is not None
                    and self.provider_requests_started >= self.max_provider_requests
                ):
                    self.provider_requests_rejected += 1
                    request_budget_exhausted = True
                else:
                    event = threading.Event()
                    self.inflight[key] = event
                    self.provider_requests_started += 1
                    owner = True

        if request_budget_exhausted:
            if telemetry:
                telemetry.record(source, "request_budget_exhausted")
            return []

        if not owner:
            if wait_timeout is None:
                event.wait()
            else:
                event.wait(max(0.1, float(wait_timeout)))
            with self.lock:
                cached = self._read_locked(key, time.time())
            if cached is not None:
                if telemetry:
                    telemetry.record(source, "coalesced_hits")
                return cached
            if telemetry:
                telemetry.record(source, "coalesced_timeouts")
            return []

        if telemetry:
            telemetry.record(source, "provider_requests")
        results: List[Dict] = []
        started_at = time.monotonic()
        try:
            results = list(fetch() or [])
        except Exception:
            if telemetry:
                telemetry.record(source, "provider_errors")
        finally:
            if telemetry:
                telemetry.record(source, "latency_ms", int(max(0.0, (time.monotonic() - started_at) * 1000.0)))
            with self.lock:
                self._store_locked(key, results, time.time())
                pending = self.inflight.pop(key, None)
                if pending is not None:
                    pending.set()

        if telemetry:
            telemetry.record(source, "discovered", len(results))
            if not results:
                telemetry.record(source, "empty_responses")
        return results
