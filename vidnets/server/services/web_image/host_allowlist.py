"""Persistent seen-host allowlist for web-image fetches.

Problem: the runtime pops one per-host network approval card for every new
image host a job fetches from. Search engines return image URLs hosted
anywhere, so a long job can touch dozens of fresh hosts (the BNSF job's
first 51 candidates alone spanned 15 distinct hosts across all URL fields,
5 of them pixel hosts). A host approved once must be reused, not
rediscovered: this module persists the set of hosts the pipeline has already
fetched from, so it survives daemon/Vidnets restarts.

The store lives OUTSIDE the repo, in the runtime dir, because it is learned
state, not code::

    ~/workspace/vidnets-runtime/seen_hosts.json

Override with ``VIDZEO_SEEN_HOSTS_PATH``. Everything here is best-effort and
never raises: a missing/unwritable/corrupt store degrades to an in-memory
empty set, never to a failed scene. Host preference must never override
relevance — callers use this only to break near-ties and to gate optional
rescue fetches (e.g. thumbnail previews), never to drop the best candidate.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Set

__all__ = [
    "SEED_HOSTS",
    "default_store_path",
    "is_seen_host",
    "load_seen_hosts",
    "load_store",
    "record_hosts",
    "save_store",
]

_STORE_VERSION = 1
_MAX_HOSTS = 500


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm_host(host: object) -> str:
    return str(host or "").strip().lower().rstrip(".")


def _host_of_url(url: object) -> str:
    from urllib.parse import urlparse

    try:
        return _norm_host(urlparse(str(url or "")).netloc)
    except Exception:
        return ""


# Hosts verified fetched by the running BNSF job (work/r_mw85awoskks8lh,
# 2026-11-10, 51 candidate rows, 51/51 downloaded) or by the live scraper
# probes the same day (Yandex/Bing/Flickr/DDG 10/10). These are already
# approved, so a new job may prefer them without popping a fresh card.
# Pixel hosts first, then search-engine / page hosts seen in the same pool.
SEED_HOSTS: Dict[str, str] = {
    # Pixel (image_url) hosts from the BNSF pool.
    "i.ytimg.com": "seed:bnsf-pixel",
    "i.pinimg.com": "seed:bnsf-pixel",
    "live.staticflickr.com": "seed:bnsf-pixel",
    "upload.wikimedia.org": "seed:bnsf-pixel",
    "s3.amazonaws.com": "seed:bnsf-pixel",
    # Page/origin hosts from the same pool.
    "www.pinterest.com": "seed:bnsf-origin",
    "www.youtube.com": "seed:bnsf-origin",
    "www.flickr.com": "seed:bnsf-origin",
    "commons.wikimedia.org": "seed:bnsf-origin",
    "www.rrpicturearchives.net": "seed:bnsf-origin",
    # Search-engine hosts the live probes hit 10/10 on 2026-11-10.
    "yandex.com": "seed:live-probe",
    "www.bing.com": "seed:live-probe",
    "www.flickr.com": "seed:live-probe",
    "duckduckgo.com": "seed:live-probe",
    "html.duckduckgo.com": "seed:live-probe",
    # Research host verified live by ScriptIT (Wikipedia API + RSS).
    "en.wikipedia.org": "seed:research",
}


def default_store_path() -> Path:
    """Where the seen-host store lives (env override wins)."""
    raw = str(os.environ.get("VIDZEO_SEEN_HOSTS_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / "workspace" / "vidnets-runtime" / "seen_hosts.json"


def _empty_store() -> Dict[str, object]:
    now = _utcnow_iso()
    return {
        "version": _STORE_VERSION,
        "hosts": {
            host: {"first_seen": now, "last_seen": now, "fetches": 0, "source": source}
            for host, source in sorted(SEED_HOSTS.items())
        },
        "updated_at": now,
    }


def load_store(path: Optional[Path] = None) -> Dict[str, object]:
    """Load the store; a missing/corrupt file yields a seeded empty store."""
    store_path = Path(path) if path is not None else default_store_path()
    try:
        raw = json.loads(store_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("hosts"), dict):
            raise ValueError("seen-hosts store has no hosts dict")
        hosts: Dict[str, dict] = {}
        for host, entry in raw["hosts"].items():
            norm = _norm_host(host)
            if not norm or not isinstance(entry, dict):
                continue
            hosts[norm] = {
                "first_seen": str(entry.get("first_seen") or _utcnow_iso()),
                "last_seen": str(entry.get("last_seen") or _utcnow_iso()),
                "fetches": max(0, int(entry.get("fetches") or 0)),
                "source": str(entry.get("source") or "job"),
            }
        # Seeds are the floor: a store written before a host was seeded must
        # still know it. Never delete a seeded host by loading an old file.
        for host, source in SEED_HOSTS.items():
            hosts.setdefault(
                host,
                {"first_seen": _utcnow_iso(), "last_seen": _utcnow_iso(), "fetches": 0, "source": source},
            )
        return {"version": _STORE_VERSION, "hosts": hosts, "updated_at": _utcnow_iso()}
    except Exception:
        return _empty_store()


def save_store(store: Mapping[str, object], path: Optional[Path] = None) -> bool:
    """Atomic-ish save (temp file + rename). Returns False instead of raising."""
    store_path = Path(path) if path is not None else default_store_path()
    try:
        store_path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(store)
        payload["updated_at"] = _utcnow_iso()
        tmp = store_path.with_suffix(store_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(store_path)
        return True
    except Exception:
        return False


_LOCK = threading.Lock()


def load_seen_hosts(path: Optional[Path] = None) -> Set[str]:
    """Just the host set (what the resolver/scraper need on the hot path)."""
    try:
        hosts = load_store(path).get("hosts")
        if isinstance(hosts, dict):
            return {_norm_host(h) for h in hosts if _norm_host(h)}
    except Exception:
        pass
    return set(SEED_HOSTS)


def is_seen_host(host: object, seen: Iterable[str]) -> bool:
    """Exact match, or a subdomain of a seen host (cdn.example.com)."""
    host = _norm_host(host)
    if not host:
        return False
    for known in seen:
        known = _norm_host(known)
        if not known:
            continue
        if host == known or host.endswith("." + known):
            return True
    return False


def record_hosts(
    hosts: Iterable[object],
    *,
    source: str = "job",
    path: Optional[Path] = None,
    store: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """Record successfully fetched hosts; persists best-effort, never raises.

    Pass an already-loaded ``store`` to batch many recordings into one
    write; otherwise loads, updates, and saves in one locked step.
    """
    wanted = {_norm_host(h) for h in hosts}
    wanted.discard("")
    if not wanted:
        return store if store is not None else load_store(path)
    now = _utcnow_iso()
    with _LOCK:
        current = store if store is not None else load_store(path)
        entries = current.get("hosts")
        if not isinstance(entries, dict):
            entries = {}
            current["hosts"] = entries
        for host in sorted(wanted):
            entry = entries.get(host)
            if isinstance(entry, dict):
                entry["last_seen"] = now
                entry["fetches"] = max(0, int(entry.get("fetches") or 0)) + 1
            else:
                entries[host] = {
                    "first_seen": now,
                    "last_seen": now,
                    "fetches": 1,
                    "source": source,
                }
        # Bound the file: drop the stalest non-seed hosts past the cap.
        if len(entries) > _MAX_HOSTS:
            keep_seeds = {h for h in entries if h in SEED_HOSTS}
            rest = sorted(
                (h for h in entries if h not in keep_seeds),
                key=lambda h: str((entries[h] or {}).get("last_seen") or ""),
            )
            for host in rest[: len(entries) - _MAX_HOSTS]:
                entries.pop(host, None)
        current["updated_at"] = now
        if store is None:
            save_store(current, path)
        return current


def hosts_of_urls(urls: Iterable[object]) -> Set[str]:
    """Distinct hosts from URL strings (empty strings ignored)."""
    return {h for h in (_host_of_url(u) for u in urls) if h}
