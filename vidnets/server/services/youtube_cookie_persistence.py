"""Write rotated YouTube session cookies back to their source file.

The operator shares YouTube cookies at ``<repo>/cookies.json`` (or any file
in the configured pool). When YouTube rotates session tokens via Set-Cookie
during pipeline use, the new tokens land only in the in-memory jar that
yt-dlp mutated: ``youtube_session._persist`` writes an encrypted
OS-protected snapshot, never the source file, and yt-dlp only saves a jar
to disk when a ``cookiefile`` param is set, which the pipeline never does.
The on-disk file therefore goes stale while the live session was fine, and
the next process start loads the dead tokens.

This module closes that gap: after a jar has been used against YouTube,
:func:`persist_session_jar` writes its current youtube.com cookies back to
the pool file they came from, preserving that file's original format
(Cookie-Editor JSON for ``.json``, Netscape for ``.txt``).

Safety rules (fail closed, never leak values):

* Only youtube.com cookies are written; any other domain's cookies already
  in the file are kept verbatim.
* A jar that lost its auth cookies (SID / SAPISID / LOGIN_INFO / ...) is
  never written back -- the file is left untouched.
* Expired cookies are dropped, not written back.
* Writes are atomic (temp file in the same directory + rename) with mode
  0600, and the previous file is kept as one backup (``<file>.bak``).
* If nothing changed, the file is not touched at all (no rewrite, no backup).
* Logs carry cookie counts and file names only, never cookie values.
* Nothing here ever raises into the pipeline: :func:`persist_session_jar`
  is the best-effort hook entry point and swallows every failure with a
  one-line warning.
"""

from __future__ import annotations

import http.cookiejar
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Auth cookies whose presence proves the jar still carries a signed-in
# session. Mirrors services.youtube_session._AUTH_COOKIE_NAMES; kept local
# so this module never depends on another module's private constant.
AUTH_COOKIE_NAMES = frozenset({
    "SID", "HSID", "SSID", "APISID", "SAPISID", "LOGIN_INFO",
    "__SECURE-1PAPISID", "__SECURE-3PAPISID", "__SECURE-1PSID",
    "__SECURE-3PSID", "__SECURE-1PSIDTS", "__SECURE-3PSIDTS",
})

_WRITEBACK_ENV = "VIDZEO_YT_COOKIE_WRITEBACK"
_BACKUP_SUFFIX = ".bak"
# One process-wide lock: searches and downloads can run on parallel threads
# and must not interleave a read-modify-write of the same pool file.
_LOCK = threading.Lock()


def writeback_enabled() -> bool:
    """Kill switch: VIDZEO_YT_COOKIE_WRITEBACK=0 disables all write-back."""
    return str(os.environ.get(_WRITEBACK_ENV, "1") or "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _is_youtube_domain(domain: Any) -> bool:
    stripped = str(domain or "").lstrip(".").lower()
    return stripped == "youtube.com" or stripped.endswith(".youtube.com")


def _youtube_cookies(jar: Any) -> List[http.cookiejar.Cookie]:
    """Live youtube.com cookies from a jar, oldest/expired ones dropped."""
    now = time.time()
    cookies: List[http.cookiejar.Cookie] = []
    for cookie in jar or []:
        if not getattr(cookie, "name", None):
            continue
        if not _is_youtube_domain(getattr(cookie, "domain", "")):
            continue
        expires = getattr(cookie, "expires", None)
        if expires is not None and float(expires) <= now + 30:
            continue
        cookies.append(cookie)
    return cookies


def _auth_cookie_count(cookies: Iterable[http.cookiejar.Cookie]) -> int:
    return sum(
        1 for cookie in cookies
        if str(getattr(cookie, "name", "") or "").upper() in AUTH_COOKIE_NAMES
    )


def _cookie_key(cookie: http.cookiejar.Cookie) -> Tuple[Any, ...]:
    """Normalized identity+value of one cookie (never logged, only compared)."""
    expires = getattr(cookie, "expires", None)
    return (
        str(getattr(cookie, "name", "") or ""),
        str(getattr(cookie, "domain", "") or "").lower(),
        str(getattr(cookie, "path", "") or "/"),
        str(getattr(cookie, "value", "") or ""),
        int(expires) if expires is not None else None,
        bool(getattr(cookie, "secure", False)),
    )


def _cookie_sort_key(cookie: http.cookiejar.Cookie) -> Tuple[str, str, str]:
    return (
        str(getattr(cookie, "domain", "") or "").lower(),
        str(getattr(cookie, "path", "") or "/"),
        str(getattr(cookie, "name", "") or ""),
    )


def _cookie_to_browser_json(cookie: http.cookiejar.Cookie) -> Dict[str, Any]:
    """One cookie as a Cookie-Editor JSON object (the loader's format)."""
    rest = dict(getattr(cookie, "_rest", None) or {})
    domain = str(getattr(cookie, "domain", "") or ".youtube.com")
    item: Dict[str, Any] = {
        "name": str(getattr(cookie, "name", "") or ""),
        "value": str(getattr(cookie, "value", "") or ""),
        "domain": domain,
        "path": str(getattr(cookie, "path", "") or "/"),
        "secure": bool(getattr(cookie, "secure", False)),
        "hostOnly": not domain.startswith("."),
        "httpOnly": "HttpOnly" in rest,
        "session": bool(getattr(cookie, "expires", None) is None
                         or getattr(cookie, "discard", False)),
    }
    expires = getattr(cookie, "expires", None)
    if expires is not None:
        item["expirationDate"] = int(expires)
    if rest.get("SameSite"):
        item["sameSite"] = str(rest["SameSite"])
    return item


def _cookie_to_netscape_line(cookie: http.cookiejar.Cookie) -> str:
    """One cookie as a Netscape/Mozilla tab-separated line."""
    rest = dict(getattr(cookie, "_rest", None) or {})
    domain = str(getattr(cookie, "domain", "") or "")
    if "HttpOnly" in rest and not domain.startswith("#HttpOnly_"):
        domain = "#HttpOnly_" + domain
    flag = "TRUE" if str(getattr(cookie, "domain", "") or "").startswith(".") else "FALSE"
    secure = "TRUE" if getattr(cookie, "secure", False) else "FALSE"
    expires = getattr(cookie, "expires", None)
    return "\t".join([
        domain,
        flag,
        str(getattr(cookie, "path", "") or "/"),
        secure,
        str(int(expires)) if expires is not None else "0",
        str(getattr(cookie, "name", "") or ""),
        str(getattr(cookie, "value", "") or ""),
    ])


def _json_cookie_key(item: Dict[str, Any]) -> Tuple[Any, ...]:
    expires = item.get("expirationDate", item.get("expires"))
    try:
        expires = int(float(expires)) if expires is not None else None
    except (TypeError, ValueError):
        expires = None
    return (
        str(item.get("name") or ""),
        str(item.get("domain") or "").lower(),
        str(item.get("path") or "/"),
        str(item.get("value") or ""),
        expires,
        bool(item.get("secure", False)),
    )


def _netscape_cookie_key(parts: List[str]) -> Tuple[Any, ...]:
    domain = parts[0]
    if domain.startswith("#HttpOnly_"):
        domain = domain[len("#HttpOnly_"):]
    try:
        expires = int(parts[4])
    except (ValueError, IndexError):
        expires = 0
    return (
        parts[5],
        domain.lower(),
        parts[2],
        parts[6],
        expires if expires else None,
        parts[3] == "TRUE",
    )


def _render_json(raw: bytes, youtube_cookies: List[http.cookiejar.Cookie]
                 ) -> Optional[Tuple[bytes, int]]:
    """Render the JSON file with the jar's youtube cookies merged in.

    Returns ``None`` when the file already holds exactly these cookies.
    Non-YouTube entries are kept verbatim; YouTube entries are replaced.
    """
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cookie JSON unreadable: {type(exc).__name__}") from exc
    if isinstance(payload, dict):
        items = payload.get("cookies", [])
        if not isinstance(items, list):
            raise ValueError("cookie JSON object has no 'cookies' list")
        wrapper: Optional[Dict[str, Any]] = payload
    elif isinstance(payload, list):
        items = payload
        wrapper = None
    else:
        raise ValueError("cookie JSON is neither a list nor an object")
    file_keys = {
        _json_cookie_key(item) for item in items
        if isinstance(item, dict) and _is_youtube_domain(item.get("domain", ""))
    }
    jar_keys = {_cookie_key(cookie) for cookie in youtube_cookies}
    if file_keys == jar_keys:
        return None
    kept = [
        item for item in items
        if not (isinstance(item, dict) and _is_youtube_domain(item.get("domain", "")))
    ]
    new_items = kept + [
        _cookie_to_browser_json(cookie)
        for cookie in sorted(youtube_cookies, key=_cookie_sort_key)
    ]
    output = dict(wrapper, cookies=new_items) if wrapper is not None else new_items
    changed = sum(1 for key in jar_keys if key not in file_keys)
    return (json.dumps(output, indent=2) + "\n").encode("utf-8"), changed


def _render_netscape(raw: bytes, youtube_cookies: List[http.cookiejar.Cookie]
                     ) -> Optional[Tuple[bytes, int]]:
    """Render the Netscape file with the jar's youtube cookies merged in.

    Returns ``None`` when the file already holds exactly these cookies.
    Header/comment lines and non-YouTube cookie lines are kept verbatim.
    """
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"cookie file unreadable: {type(exc).__name__}") from exc
    header: List[str] = []
    kept: List[str] = []
    file_keys = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            header.append(line)
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            # Unparseable line: keep it verbatim rather than dropping data.
            kept.append(line)
            continue
        domain = parts[0][len("#HttpOnly_"):] if parts[0].startswith("#HttpOnly_") else parts[0]
        if _is_youtube_domain(domain):
            file_keys.add(_netscape_cookie_key(parts))
        else:
            kept.append(line)
    jar_keys = {_cookie_key(cookie) for cookie in youtube_cookies}
    if file_keys == jar_keys:
        return None
    if not header:
        header = ["# Netscape HTTP Cookie File",
                  "# http://curl.haxx.se/rfc/cookie_spec.html", ""]
    new_lines = [
        _cookie_to_netscape_line(cookie)
        for cookie in sorted(youtube_cookies, key=_cookie_sort_key)
    ]
    changed = sum(1 for key in jar_keys if key not in file_keys)
    return ("\n".join(header + kept + new_lines) + "\n").encode("utf-8"), changed


def _atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically with mode 0600."""
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def persist_cookie_jar(jar: Any, source_path: Any) -> bool:
    """Write a jar's current youtube.com cookies back to ``source_path``.

    Returns True when the file now holds the jar's cookies (written, or
    already current). Returns False -- leaving the file untouched -- when
    write-back is disabled, the source is missing/unsupported/unreadable,
    or the jar lost its auth cookies. Never raises; never logs values.
    """
    if not writeback_enabled():
        return False
    try:
        path = Path(source_path)
    except (TypeError, ValueError):
        return False
    if not path.is_file():
        logger.warning("[yt-cookies] write-back skipped: source file missing: %s",
                       path.name)
        return False
    youtube_cookies = _youtube_cookies(jar)
    if not youtube_cookies:
        logger.warning(
            "[yt-cookies] write-back refused: jar holds no youtube.com cookies "
            "for %s", path.name)
        return False
    if _auth_cookie_count(youtube_cookies) < 1:
        logger.warning(
            "[yt-cookies] write-back refused: jar lost its auth cookies "
            "(%d youtube cookies, 0 auth) for %s",
            len(youtube_cookies), path.name)
        return False
    with _LOCK:
        return _persist_locked(path, youtube_cookies)


def _persist_locked(path: Path, youtube_cookies: List[http.cookiejar.Cookie]) -> bool:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        logger.warning("[yt-cookies] write-back skipped: cannot read %s (%s)",
                       path.name, type(exc).__name__)
        return False
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            rendered = _render_json(raw, youtube_cookies)
        elif suffix == ".txt":
            rendered = _render_netscape(raw, youtube_cookies)
        else:
            logger.warning(
                "[yt-cookies] write-back skipped: unsupported cookie file "
                "suffix for %s", path.name)
            return False
    except ValueError as exc:
        logger.warning("[yt-cookies] write-back refused: %s (%s)",
                       path.name, exc)
        return False
    if rendered is None:
        return True
    new_bytes, changed = rendered
    if new_bytes == raw:
        return True
    backup = path.with_name(path.name + _BACKUP_SUFFIX)
    try:
        _atomic_write(backup, raw)
        _atomic_write(path, new_bytes)
    except OSError as exc:
        logger.warning("[yt-cookies] write-back failed for %s (%s)",
                       path.name, type(exc).__name__)
        return False
    logger.info(
        "[yt-cookies] wrote back %d youtube cookies (%d changed) to %s",
        len(youtube_cookies), changed, path.name)
    return True


def resolve_session_source_path(session_jar: Any = None, pool: Any = None,
                                index: Any = None) -> Optional[Path]:
    """Resolve the pool file a jar came from, or None when unknown.

    The pool file is tracked on the jar the accessor returns
    (``_vidzeo_session_pool`` / ``_vidzeo_session_index``). Clones made for
    yt-dlp lose those attributes, so callers pass the original jar (or the
    pool + index) alongside the jar that was actually used.
    """
    if pool is None and session_jar is not None:
        pool = getattr(session_jar, "_vidzeo_session_pool", None)
    if index is None and session_jar is not None:
        index = getattr(session_jar, "_vidzeo_session_index", None)
    if pool is None or index is None:
        return None
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return None
    try:
        paths = getattr(pool, "paths", None) or ()
        if 0 <= idx < len(paths):
            return Path(paths[idx])
    except (TypeError, ValueError):
        return None
    return None


def _refresh_encrypted_snapshot(jar: Any) -> None:
    """Best-effort refresh of the encrypted snapshot after a write-back.

    The snapshot is the durable store (survives restarts/reboots).
    refresh_encrypted_snapshot() itself refuses empty/unauthenticated
    jars. Never raises, never logs values.
    """
    try:
        from services.youtube_session import refresh_encrypted_snapshot
        refresh_encrypted_snapshot(jar, meta={"source": "cookie-write-back"})
    except Exception as exc:  # never let the snapshot break the pipeline
        logger.warning("[yt-cookies] snapshot refresh skipped (%s)", type(exc).__name__)


def persist_session_jar(used_jar: Any, session_jar: Any = None,
                        pool: Any = None, index: Any = None) -> bool:
    """Best-effort write-back for a jar just used against YouTube.

    ``used_jar`` is the jar yt-dlp actually mutated (``ydl.cookiejar`` after
    the request); ``session_jar`` is the accessor jar that still carries
    the pool/index attributes (or pass ``pool`` + ``index`` directly).
    Skips silently when the source file is unknown. Never raises.
    """
    try:
        if used_jar is None:
            return False
        source_jar = session_jar if session_jar is not None else used_jar
        source = resolve_session_source_path(
            session_jar=source_jar, pool=pool, index=index)
        if source is None:
            return False
        written = persist_cookie_jar(used_jar, source)
        if written:
            _refresh_encrypted_snapshot(used_jar)
        return written
    except Exception as exc:  # never let write-back break the pipeline
        logger.warning("[yt-cookies] write-back skipped (%s)", type(exc).__name__)
        return False
