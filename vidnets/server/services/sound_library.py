"""Small, compliance-aware sound asset adapter for semantic SFX cues.

The planner works without this module. Downloads are opt-in, limited to hosts
explicitly configured by the user, and obey each host's robots.txt. The adapter
never bypasses authentication, paywalls, rate limits, or anti-bot controls.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
import urllib.robotparser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac", ".opus"}
MAX_SOUND_BYTES = 12 * 1024 * 1024
FREESOUND_API = "https://freesound.org/apiv2"


def _enabled() -> bool:
    return str(os.environ.get("VIDZEO_SFX_WEB_SCRAPE", "0")).strip().lower() in {"1", "true", "yes", "on"}


def _allowed_hosts() -> set[str]:
    raw = os.environ.get("VIDZEO_SFX_ALLOWED_HOSTS", "")
    return {part.strip().lower().lstrip(".") for part in raw.split(",") if part.strip()}


def _host_allowed(url: str) -> bool:
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        return bool(host and any(host == allowed or host.endswith("." + allowed) for allowed in _allowed_hosts()))
    except Exception:
        return False


def _audio_url(url: str) -> bool:
    try:
        suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
        return suffix in AUDIO_EXTENSIONS
    except Exception:
        return False


def _robots_allowed(url: str, timeout: float = 4.0) -> bool:
    parsed = urllib.parse.urlparse(url)
    if not parsed.hostname:
        return False
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    try:
        request = urllib.request.Request(robots_url, headers={"User-Agent": "VidzeoStudio/1.0 (sound asset audit)"})
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - explicit user allowlist
            parser.parse(response.read(256 * 1024).decode("utf-8", errors="replace").splitlines())
        return parser.can_fetch("VidzeoStudio", url)
    except Exception:
        # A host that does not expose robots.txt is not treated as permission to
        # scrape. The configured local library remains fully usable.
        return False


def scrape_sound_candidates(page_url: str, timeout: float = 5.0, limit: int = 12) -> List[Dict[str, Any]]:
    """Extract direct audio references from an allowlisted HTML page."""
    if not _enabled() or not _host_allowed(page_url) or not _robots_allowed(page_url):
        return []
    request = urllib.request.Request(page_url, headers={"User-Agent": "VidzeoStudio/1.0 (sound asset audit)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - explicit allowlist
            html = response.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
    except Exception:
        return []
    raw_urls = re.findall(r"(?:src|href)\s*=\s*[\"']([^\"']+)[\"']", html, flags=re.I)
    candidates: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_urls:
        url = urllib.parse.urljoin(page_url, raw.strip())
        if not url.startswith("https://") or not _host_allowed(url) or not _audio_url(url):
            continue
        canonical = url.split("#", 1)[0]
        if canonical in seen:
            continue
        seen.add(canonical)
        candidates.append({"src": canonical, "source_url": page_url, "publisher": urllib.parse.urlparse(page_url).hostname or "", "rights_status": "unknown", "license": "Unknown", "attribution": "Verify rights before distribution"})
        if len(candidates) >= max(1, int(limit)):
            break
    return candidates


def download_sound_asset(asset: Mapping[str, Any], destination_dir: str | os.PathLike[str], timeout: float = 10.0) -> Dict[str, Any]:
    """Download one already-discovered sound reference with bounded size."""
    url = str(asset.get("src") or "").strip()
    if not _enabled() or not url.startswith("https://") or not _host_allowed(url) or not _robots_allowed(url):
        raise PermissionError("Sound URL is not enabled, allowlisted, or robots-permitted")
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix not in AUDIO_EXTENSIONS:
        raise ValueError("Unsupported sound extension")
    target = destination / f"sfx_{hashlib.sha256(url.encode()).hexdigest()[:16]}{suffix}"
    request = urllib.request.Request(url, headers={"User-Agent": "VidzeoStudio/1.0 (sound asset audit)"})
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, target.open("wb") as handle:  # nosec B310 - explicit allowlist
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_SOUND_BYTES:
                    raise ValueError("Sound asset exceeds the 12 MB safety limit")
                handle.write(chunk)
    except Exception:
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    return {**dict(asset), "path": str(target), "src": str(target), "bytes": total, "rights_status": asset.get("rights_status") or "unknown"}


def normalize_library(items: Optional[Iterable[Mapping[str, Any]]] = None) -> Dict[str, Dict[str, Any]]:
    """Normalize user-provided/local SFX metadata for the semantic planner."""
    result: Dict[str, Dict[str, Any]] = {}
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        key = str(item.get("kind") or item.get("id") or "").strip().lower()
        src = str(item.get("src") or item.get("path") or "").strip()
        if not key or not src:
            continue
        result[key] = {"src": src, "rights_status": item.get("rights_status") or item.get("license") or "unknown", "license": item.get("license") or "Unknown", "source_url": item.get("source_url") or "", "publisher": item.get("publisher") or "", "attribution": item.get("attribution") or ""}
    return result


def _commercial_sound_license(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return (
        "creative commons 0" in text
        or "cc0" in text
        or ("attribution" in text and "noncommercial" not in text and "sampling+" not in text)
    )


def search_freesound(
    query: str,
    *,
    token: str = "",
    limit: int = 12,
    timeout: float = 8.0,
) -> List[Dict[str, Any]]:
    """Search Freesound's documented API with commercial-safe license filtering.

    The adapter is opt-in because each project must decide whether attribution
    licenses are acceptable.  It returns preview URLs plus full provenance; it
    never bulk-copies the catalogue.
    """
    api_token = str(token or os.environ.get("FREESOUND_API_TOKEN") or os.environ.get("VIDZEO_FREESOUND_API_TOKEN") or "").strip()
    enabled = str(os.environ.get("VIDZEO_SFX_FREESOUND", "0")).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled or not api_token or not str(query or "").strip():
        return []
    params = urllib.parse.urlencode({
        "query": str(query).strip(),
        "page_size": max(1, min(30, int(limit))),
        "fields": "id,name,url,license,username,previews,duration,tags",
        "filter": "duration:[0.05 TO 8]",
    })
    request = urllib.request.Request(
        f"{FREESOUND_API}/search/text/?{params}",
        headers={"Authorization": f"Token {api_token}", "User-Agent": "VidzeoStudio/1.0 (licensed sound search)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=max(2.0, min(20.0, float(timeout)))) as response:  # nosec B310 - fixed official API
            payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8", errors="replace"))
    except Exception:
        return []
    results: List[Dict[str, Any]] = []
    for item in payload.get("results") or []:
        if not isinstance(item, Mapping) or not _commercial_sound_license(item.get("license")):
            continue
        previews = item.get("previews") if isinstance(item.get("previews"), Mapping) else {}
        preview = str(previews.get("preview-hq-mp3") or previews.get("preview-lq-mp3") or "").strip()
        if not preview.startswith("https://"):
            continue
        license_name = str(item.get("license") or "")
        username = str(item.get("username") or "Freesound contributor")
        results.append({
            "id": f"freesound:{item.get('id')}",
            "kind": re.sub(r"[^a-z0-9]+", "_", str(query).lower()).strip("_")[:48],
            "name": str(item.get("name") or ""),
            "src": preview,
            "source_url": str(item.get("url") or ""),
            "publisher": username,
            "license": license_name,
            "rights_status": "licensed",
            "attribution": f"{item.get('name') or 'Sound'} by {username} — {license_name}",
            "duration": item.get("duration"),
            "tags": list(item.get("tags") or [])[:20],
            "provider": "freesound_api",
        })
        if len(results) >= max(1, int(limit)):
            break
    return results


def download_freesound_preview(
    asset: Mapping[str, Any],
    destination_dir: str | os.PathLike[str],
    *,
    timeout: float = 12.0,
) -> Dict[str, Any]:
    url = str(asset.get("src") or "").strip()
    if asset.get("provider") != "freesound_api" or not _commercial_sound_license(asset.get("license")):
        raise PermissionError("Only licensed Freesound API results can be imported")
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if not url.startswith("https://") or not (host == "freesound.org" or host.endswith(".freesound.org")):
        raise PermissionError("Unexpected Freesound preview host")
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"freesound_{hashlib.sha256(url.encode()).hexdigest()[:16]}.mp3"
    request = urllib.request.Request(url, headers={"User-Agent": "VidzeoStudio/1.0 (licensed sound import)"})
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, target.open("wb") as handle:  # nosec B310 - validated provider host
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_SOUND_BYTES:
                    raise ValueError("Sound asset exceeds the 12 MB safety limit")
                handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {**dict(asset), "path": str(target), "src": str(target), "bytes": total, "retrieved_via": "freesound_api"}
