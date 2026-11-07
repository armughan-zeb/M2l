"""
Multi-site gallery search — uses gallery-dl CLI for true parallelism.
Each site extraction runs as a separate subprocess (no thread conflicts).
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import aiohttp

# ── Sources (editorial-first, multi-niche coverage) ──────────────────────
# Primary: editorial/news/photo agencies
# Secondary: social platforms (used only when primary returns few results)
SOURCES: Dict[str, str] = {
    # PRIMARY — editorial & photo agencies
    "flickr":     "https://www.flickr.com/search/?text={query}&license=0%2C1%2C2%2C3%2C4%2C5%2C6%2C7%2C8%2C9%2C10",
    "wikimedia":  "https://commons.wikimedia.org/w/index.php?search={query}&title=Special:MediaSearch&type=image",
    "pexels":     "https://www.pexels.com/search/{query}/",
    "unsplash":   "https://unsplash.com/s/photos/{query}",
    # SECONDARY — social/community (fallback only)
    "pinterest":  "https://www.pinterest.com/search/pins/?q={query}",
    "500px":      "https://500px.com/search?q={query}&type=photos",
}

# ── gallery-dl CLI extraction (subprocess = true parallelism) ──────────
_GALLERY_DL_PATH: Optional[str] = None

def _resolve_gallery_dl() -> Optional[str]:
    """Find gallery-dl executable."""
    global _GALLERY_DL_PATH
    if _GALLERY_DL_PATH:
        return _GALLERY_DL_PATH
    
    # Try common locations
    candidates = []
    if os.name == "nt":
        # Windows: check venv, system PATH, common install dirs
        venv_scripts = Path(sys.executable).parent / "gallery-dl.exe"
        if venv_scripts.exists():
            candidates.append(str(venv_scripts))
        # Check parent dir (might be Scripts\)
        parent = Path(sys.executable).parent
        for p in parent.glob("gallery-dl*"):
            candidates.append(str(p))
    
    # Check PATH
    try:
        result = subprocess.run(["gallery-dl", "--version"], capture_output=True, timeout=5)
        if result.returncode == 0:
            _GALLERY_DL_PATH = "gallery-dl"
            return _GALLERY_DL_PATH
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    for c in candidates:
        try:
            result = subprocess.run([c, "--version"], capture_output=True, timeout=5)
            if result.returncode == 0:
                _GALLERY_DL_PATH = c
                return _GALLERY_DL_PATH
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    
    return None

def _extract_cli(url_str: str, limit: int, timeout: float = 8.0) -> List[str]:
    """Extract URLs using gallery-dl CLI subprocess."""
    gdl = _resolve_gallery_dl()
    if not gdl:
        return []
    
    try:
        cmd = [gdl, "--get-urls", "--range", f"1-{limit}", url_str]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        )
        if result.returncode != 0:
            return []
        
        urls = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line and line.startswith("http") and "ytdl:" not in line:
                urls.append(line)
                if len(urls) >= limit:
                    break
        return urls
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return []

def _extract_site(site: str, url_str: str, limit: int) -> List[Tuple[str, str]]:
    """Extract URLs from a single site via CLI subprocess."""
    urls = _extract_cli(url_str, limit, timeout=8.0)
    return [(u, site) for u in urls]

def harvest_urls_parallel(query: str, limit: int = 30) -> List[Tuple[str, str]]:
    """Extract from all sites in PARALLEL using subprocess (no global lock)."""
    q = re.sub(r"\s+", " ", (query or "").strip())
    if not q: return []
    
    results = []
    seen = set()
    target = limit
    
    # Run all sites in parallel via threads (each thread spawns a subprocess)
    threads = []
    site_results: Dict[str, List[Tuple[str, str]]] = {}
    lock = threading.Lock()
    
    def _run_site(name, url_str):
        urls = _extract_site(name, url_str, limit)
        with lock:
            site_results[name] = urls
    
    for name, tmpl in SOURCES.items():
        url_str = tmpl.format(query=urllib.parse.quote(q))
        t = threading.Thread(target=_run_site, args=(name, url_str), daemon=True)
        threads.append(t)
        t.start()
    
    # Wait for all threads with timeout (parallel, not sequential!)
    deadline = time.time() + 12.0
    for t in threads:
        remaining = max(0.5, deadline - time.time())
        t.join(timeout=remaining)
    
    # Collect results in source order
    for name, tmpl in SOURCES.items():
        for u, site in site_results.get(name, []):
            if u and u not in seen:
                seen.add(u)
                results.append((u, site))
        
        if len(results) >= target:
            break
    
    return results[:limit * 2]

# ── URL normalization ────────────────────────────────────────────────
def _normalize_url(u: str) -> str:
    import html
    u = html.unescape(str(u or "")).strip()
    if not u: return ""
    if u.startswith("//"): u = f"https:{u}"
    p = urlparse(u)
    if p.scheme not in {"http", "https"} or not p.netloc: return ""
    return u

def _extract_domain(u: str) -> str:
    try: return (urlparse(u).netloc or "").lower().replace("www.", "").split(":")[0]
    except: return ""

# ── aiohttp parallel downloader ──────────────────────────────────────
async def _download_async(
    urls: List[str], output_dir: Path,
    max_concurrent: int = 20, timeout: float = 12.0,
    min_size: int = 8 * 1024,
) -> List[Tuple[str, Optional[Path], str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    
    async def _dl(url: str) -> Tuple[str, Optional[Path], str]:
        try:
            ext = ".jpg"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    if resp.status != 200: return url, None, f"http {resp.status}"
                    data = await resp.read()
                    if len(data) < min_size: return url, None, "too small"
                    ct = resp.headers.get("Content-Type", "")
                    if "jpeg" in ct: ext = ".jpg"
                    elif "png" in ct: ext = ".png"
                    elif "webp" in ct: ext = ".webp"
                    h = hashlib.sha256(data).hexdigest()[:12]
                    path = output_dir / f"{h}{ext}"
                    path.write_bytes(data)
                    return url, path, ""
        except asyncio.TimeoutError: return url, None, "timeout"
        except Exception as e: return url, None, str(e)[:80]
    
    sem = asyncio.Semaphore(max_concurrent)
    async def _dl_sem(url):
        async with sem: return await _dl(url)
    
    tasks = [_dl_sem(u) for u in urls]
    return await asyncio.gather(*tasks)

def download_bulk(
    urls: List[str], output_dir: Path,
    max_concurrent: int = 8, timeout: float = 6.0,
) -> List[Tuple[str, Optional[Path], str]]:
    return asyncio.run(_download_async(urls, output_dir, max_concurrent, timeout))

# ── Cache ────────────────────────────────────────────────────────────
_CACHE: Dict[str, Tuple[float, List[Dict]]] = {}
_CACHE_LOCK = threading.Lock()

def _ckey(q: str) -> str:
    return re.sub(r"\s+", " ", q.strip().lower())[:60]

def _cache_get(q: str) -> Optional[List[Dict]]:
    k = _ckey(q)
    with _CACHE_LOCK:
        e = _CACHE.get(k)
        if e and time.time() - e[0] < 600:
            return [dict(x) for x in e[1]]
    return None

def _cache_put(q: str, rows: List[Dict]):
    if not rows:
        return
    k = _ckey(q)
    with _CACHE_LOCK:
        _CACHE[k] = (time.time(), list(rows))
        if len(_CACHE) > 200:
            old = min(_CACHE.items(), key=lambda x: x[1][0])
            del _CACHE[old[0]]

def clear_cache():
    with _CACHE_LOCK: _CACHE.clear()

# ── Main search + download ───────────────────────────────────────────
def _subject_key(query: str) -> str:
    """Extract base subject from query. 'virat kohli match cricket' → 'virat kohli'"""
    q = query.lower().strip()
    words = q.split()
    for i in range(len(words) - 1):
        pair = f"{words[i]} {words[i+1]}"
        if (words[i].isalpha() and words[i+1].isalpha() and
            len(words[i]) > 2 and len(words[i+1]) > 2 and
            words[i] not in {"cricket","stadium","match","world","cup","team",
                            "player","photo","portrait","batting","bowling",
                            "crowd","celebration","training","trophy","jersey",
                            "final","tournament","championship","history","innings",
                            "century","official","archive","india","australia",
                            "new","zealand","mumbai","london","england","pakistan"}):
            return pair
    return " ".join(words[:3]) if len(words) >= 3 else q

def search_gallery(
    query: str,
    limit: int = 30,
    download: bool = True,
    output_dir: Optional[Path] = None,
    dl_concurrent: int = 16,
) -> List[Dict]:
    """Multi-site parallel search + download."""
    cached = _cache_get(query)
    if cached is not None:
        return cached[:limit]
    
    subj = _subject_key(query)
    if subj != query:
        cached = _cache_get(subj)
        if cached is not None:
            return cached[:limit]
    
    # Phase 1: Harvest URLs from ALL sites
    url_pairs = harvest_urls_parallel(query, limit)
    print(f"  [gallery_search] harvest '{query[:40]}': {len(url_pairs)} URLs")
    if not url_pairs and subj != query:
        url_pairs = harvest_urls_parallel(subj, limit)
        print(f"  [gallery_search] harvest subj '{subj[:40]}': {len(url_pairs)} URLs")
    if not url_pairs:
        return []
    
    urls = [u for u, _ in url_pairs]
    
    # Phase 2: Download (limit to target count for speed)
    rows: List[Dict] = []
    if download and output_dir:
        dl_dir = output_dir / re.sub(r"[^a-z0-9]+", "_", query.lower())[:40]
        try:
            dl_results = download_bulk(urls[:limit], dl_dir, max_concurrent=dl_concurrent)
        except Exception:
            dl_results = []
        for u, path, err in dl_results:
            if path:
                dom = _extract_domain(u)
                _score = 30.0
                if "artstation" in dom.lower(): _score = 45.0
                elif "500px" in dom.lower(): _score = 42.0
                elif "reddit" in dom.lower(): _score = 35.0
                elif "pinimg" in dom.lower(): _score = 30.0
                elif "wallhaven" in dom.lower(): _score = 38.0
                elif "tenor" in dom.lower(): _score = 25.0
                elif "bsky" in dom.lower(): _score = 32.0
                elif "civitai" in dom.lower(): _score = 35.0
                rows.append({
                    "title": "", "image_url": u, "source_url": u, "page_url": u,
                    "thumbnail_url": "", "domain": dom,
                    "resolver_lane": "gallery-dl", "_ddg_direct": True,
                    "score": _score, "local_path": str(path),
                })
                if len(rows) >= limit: break
    
    # Fallback: return undownloaded URLs
    if not rows and urls:
        for u in urls[:limit]:
            dom = _extract_domain(u)
            _score = 30.0
            if "artstation" in dom.lower(): _score = 45.0
            elif "500px" in dom.lower(): _score = 42.0
            elif "reddit" in dom.lower(): _score = 35.0
            elif "pinimg" in dom.lower(): _score = 30.0
            elif "wallhaven" in dom.lower(): _score = 38.0
            elif "tenor" in dom.lower(): _score = 25.0
            elif "bsky" in dom.lower(): _score = 32.0
            elif "civitai" in dom.lower(): _score = 35.0
            rows.append({
                "title": "", "image_url": u, "source_url": u, "page_url": u,
                "thumbnail_url": "", "domain": dom,
                "resolver_lane": "gallery-dl", "_ddg_direct": True,
                "score": _score,
            })
    
    _cache_put(query, rows)
    if subj != query:
        _cache_put(subj, rows)
    return rows[:limit]
