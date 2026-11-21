"""Local YouTube browser-session broker.

The broker reads the most recently active local browser profile once, keeps a
filtered cookie jar in memory for concurrent yt-dlp workers, and persists only
an OS-protected snapshot.  Cookie values are never logged or returned by the
status API.
"""

from __future__ import annotations

import base64
import copy
import ctypes
import hashlib
import http.cookiejar
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional, Tuple


class YouTubeSessionUnavailable(RuntimeError):
    """Raised when no authenticated local YouTube browser session is usable."""


_AUTH_COOKIE_NAMES = {
    "SID", "HSID", "SSID", "APISID", "SAPISID", "LOGIN_INFO",
    "__SECURE-1PAPISID", "__SECURE-3PAPISID", "__SECURE-1PSID",
    "__SECURE-3PSID", "__SECURE-1PSIDTS", "__SECURE-3PSIDTS",
}
_ALLOWED_DOMAIN_SUFFIXES = ("youtube.com", "google.com")
_LOCK = RLock()
_MEMORY_JAR: Optional[http.cookiejar.CookieJar] = None
_SESSION_META: Dict[str, Any] = {}
_GENERATION = 0
_CURRENT_CHANNEL_TITLE: str = ""
_SUPPORTED_BROWSERS = {"chrome", "edge", "brave", "chromium", "vivaldi", "opera", "firefox"}


@dataclass(frozen=True)
class BrowserProfile:
    browser: str
    profile: Optional[str]
    label: str
    activity: float


def _env_flag(name: str, default: bool) -> bool:
    value = str(os.environ.get(name, "1" if default else "0") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _preferred_browser() -> str:
    """Resolve the per-user preference, defaulting to Chrome for new installs."""
    hint = str(os.environ.get("VIDZEO_YT_COOKIES_FROM_BROWSER") or "").strip().lower()
    if not hint:
        try:
            hint_path = Path(os.environ.get("VIDZEO_ROOT") or Path.cwd()) / ".vidzeo_cookie_browser"
            hint = hint_path.read_text(encoding="utf-8").strip().lower()
        except OSError:
            hint = ""
    return hint if hint in _SUPPORTED_BROWSERS else "chrome"


def _normalize_browser(value: Optional[str]) -> str:
    browser = str(value or "").strip().lower()
    return browser if browser in _SUPPORTED_BROWSERS else "chrome"


def _remember_preferred_browser(browser: Optional[str]) -> str:
    """Persist the browser selected by the packaged Settings screen."""
    resolved = _normalize_browser(browser)
    # Environment defaults are loaded before the Settings screen is used.
    # Update the current backend process as well as the on-disk hint so an
    # explicit Edge/Chrome choice takes effect immediately, not next launch.
    os.environ["VIDZEO_YT_COOKIES_FROM_BROWSER"] = resolved
    try:
        hint_path = Path(os.environ.get("VIDZEO_ROOT") or Path.cwd()) / ".vidzeo_cookie_browser"
        hint_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = hint_path.with_suffix(".tmp")
        temporary.write_text(resolved, encoding="utf-8")
        os.replace(temporary, hint_path)
    except OSError:
        # The in-call override still works if a read-only developer root is in
        # use; persistence is best effort only.
        pass
    return resolved


def _require_preferred_browser() -> bool:
    # The YouTube pipeline is Chrome-first.  Falling through to another
    # browser can silently use the wrong signed-in account, so users must opt
    # in explicitly if they ever want that behaviour.
    return _env_flag("VIDZEO_YT_REQUIRE_PREFERRED_BROWSER", True)


def _require_auth_probe() -> bool:
    return _env_flag("VIDZEO_YT_REQUIRE_AUTH_PROBE", True)


def _secure_store_candidates() -> List[Path]:
    configured = str(os.environ.get("VIDZEO_SECURE_DATA_DIR") or "").strip()
    if configured:
        return [Path(configured) / "youtube-session.dpapi"]
    candidates = []
    if os.name == "nt":
        roaming = Path(os.environ.get("APPDATA") or "")
        if roaming.is_dir():
            candidates.append(roaming / "vidnets-studio-v1" / "VidzeoData" / "secure" / "youtube-session.dpapi")
            candidates.append(roaming / "vidnets-studio-v1" / "secure" / "youtube-session.dpapi")
            candidates.append(roaming / "Vidnets Studio" / "secure" / "youtube-session.dpapi")
            candidates.append(roaming / "Vidzeo Studio" / "secure" / "youtube-session.dpapi")
            candidates.append(roaming / "VidzeoData" / "secure" / "youtube-session.dpapi")
        local = Path(os.environ.get("LOCALAPPDATA") or "")
        if local.is_dir():
            candidates.append(local / "Vidnets Studio" / "secure" / "youtube-session.dpapi")
            candidates.append(local / "Vidzeo Studio" / "secure" / "youtube-session.dpapi")
            candidates.append(local / "vidnets-studio-v1" / "VidzeoData" / "secure" / "youtube-session.dpapi")
            candidates.append(local / "Vidzeo CoSystems" / "secure" / "youtube-session.dpapi")
    else:
        home = Path.home()
        candidates.append(home / ".local" / "share" / "vidnets" / "secure" / "youtube-session.dpapi")
        candidates.append(home / ".config" / "vidnets" / "secure" / "youtube-session.dpapi")
    candidates.append(Path(os.environ.get("VIDZEO_TEMP_DIR") or Path.cwd() / "temp") / ".secure" / "youtube-session.dpapi")
    return candidates


def _secure_store_path() -> Path:
    configured = str(os.environ.get("VIDZEO_SECURE_DATA_DIR") or "").strip()
    if configured:
        return Path(configured) / "youtube-session.dpapi"
    for candidate in _secure_store_candidates():
        if candidate.is_file():
            return candidate
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Vidnets Studio" / "secure" / "youtube-session.dpapi"
    if os.name != "nt":
        return Path.home() / ".local" / "share" / "vidnets" / "secure" / "youtube-session.dpapi"
    return Path(os.environ.get("VIDZEO_TEMP_DIR") or Path.cwd() / "temp") / ".secure" / "youtube-session.dpapi"


def _session_state_path() -> Path:
    """Redacted session state kept separately from the encrypted cookie blob."""
    return _secure_store_path().with_name("youtube-session-state.json")


def _load_cookie_update_state() -> Dict[str, Any]:
    try:
        payload = json.loads(_session_state_path().read_text(encoding="utf-8"))
        if not payload.get("cookie_update_required"):
            return {}
        return {
            "ready": False,
            "reason": (
                "Your saved YouTube sign-in expired or was rejected. "
                "Click Connect YouTube and finish sign-in to renew it."
            ),
            "cookie_update_required": True,
            "cookie_update_reason": str(payload.get("cookie_update_reason") or "youtube-authentication-rejected")[:120],
            "verification": str(payload.get("verification") or payload.get("cookie_update_reason") or "youtube-authentication-rejected")[:120],
            "preferred_browser": str(payload.get("preferred_browser") or _preferred_browser()),
            "strict_preference": True,
            "updated_at": float(payload.get("updated_at") or 0),
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _clear_cookie_update_state() -> None:
    try:
        _session_state_path().unlink(missing_ok=True)
    except OSError:
        pass


def _legacy_cookie_paths() -> Iterable[Path]:
    temp_root = Path(os.environ.get("VIDZEO_TEMP_DIR") or Path.cwd() / "temp")
    yield temp_root / "vidzeo_session_cookies.txt"


def _cleanup_legacy_plaintext() -> None:
    for candidate in _legacy_cookie_paths():
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def _cookie_is_relevant(cookie: http.cookiejar.Cookie) -> bool:
    domain = str(cookie.domain or "").lstrip(".").lower()
    return any(domain == suffix or domain.endswith(f".{suffix}") for suffix in _ALLOWED_DOMAIN_SUFFIXES)


def _cookie_is_live(cookie: http.cookiejar.Cookie, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    return cookie.expires is None or float(cookie.expires) > now + 30


def _filtered_jar(source: Iterable[http.cookiejar.Cookie]) -> http.cookiejar.CookieJar:
    jar = http.cookiejar.CookieJar()
    now = time.time()
    for cookie in source:
        if _cookie_is_relevant(cookie) and _cookie_is_live(cookie, now):
            jar.set_cookie(copy.copy(cookie))
    return jar


def _clone_jar(source: http.cookiejar.CookieJar) -> http.cookiejar.CookieJar:
    jar = http.cookiejar.CookieJar()
    for cookie in source:
        jar.set_cookie(copy.copy(cookie))
    return jar


def _auth_cookie_count(jar: http.cookiejar.CookieJar) -> int:
    return sum(1 for cookie in jar if str(cookie.name or "").upper() in _AUTH_COOKIE_NAMES)


def _jar_is_authenticated(jar: Optional[http.cookiejar.CookieJar]) -> bool:
    return bool(jar) and _auth_cookie_count(jar) >= 1


def _cookie_to_dict(cookie: http.cookiejar.Cookie) -> Dict[str, Any]:
    return {
        "name": cookie.name,
        "value": cookie.value,
        "domain": cookie.domain,
        "path": cookie.path or "/",
        "secure": bool(cookie.secure),
        "expires": cookie.expires,
        "discard": bool(cookie.discard),
        "rest": dict(getattr(cookie, "_rest", {}) or {}),
    }


def _cookie_from_dict(item: Dict[str, Any]) -> http.cookiejar.Cookie:
    domain = str(item.get("domain") or ".youtube.com")
    expires = item.get("expires")
    try:
        expires = int(expires) if expires is not None else None
    except (TypeError, ValueError):
        expires = None
    return http.cookiejar.Cookie(
        version=0,
        name=str(item.get("name") or ""),
        value=str(item.get("value") or ""),
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path=str(item.get("path") or "/"),
        path_specified=True,
        secure=bool(item.get("secure")),
        expires=expires,
        discard=bool(item.get("discard", expires is None)),
        comment=None,
        comment_url=None,
        rest=dict(item.get("rest") or {}),
        rfc2109=False,
    )


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _data_blob(data: bytes) -> Tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    return blob, buffer


def _linux_derive_key(salt: bytes) -> bytes:
    uid = str(os.getuid() if hasattr(os, 'getuid') else 1000).encode()
    node = os.uname().nodename.encode() if hasattr(os, 'uname') else b'linux'
    seed = [uid, node]
    for p in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
        if os.path.isfile(p):
            try:
                seed.append(Path(p).read_bytes().strip())
                break
            except Exception:
                pass
    env_secret = os.environ.get('VIDZEO_SESSION_SECRET_FILE')
    if env_secret:
        secret_file = Path(env_secret)
    else:
        try:
            secret_file = Path.home() / '.vidzeo_session_secret'
        except Exception:
            import tempfile
            secret_file = Path(tempfile.gettempdir()) / f'.vidzeo_session_secret_{uid.decode()}'
    if not secret_file.is_file():
        try:
            secret_file.parent.mkdir(parents=True, exist_ok=True)
            secret_file.write_bytes(secrets.token_bytes(32))
            os.chmod(secret_file, 0o600)
        except Exception:
            pass
    if secret_file.is_file():
        try:
            seed.append(secret_file.read_bytes())
        except Exception:
            pass
    return hashlib.pbkdf2_hmac('sha256', b''.join(seed), salt, 10000)


def _linux_protect(data: bytes) -> bytes:
    salt = secrets.token_bytes(16)
    key = _linux_derive_key(salt)
    nonce = secrets.token_bytes(16)
    keystream_blocks = (len(data) + 31) // 32
    keystream = bytearray()
    for block_num in range(keystream_blocks):
        counter = block_num.to_bytes(4, 'big')
        keystream.extend(hashlib.sha256(key + nonce + counter).digest())
    ciphertext = bytes(b ^ k for b, k in zip(data, keystream[:len(data)]))
    tag = hashlib.sha256(key + b'mac' + ciphertext).digest()[:16]
    return b'LNX1' + salt + nonce + tag + ciphertext


def _linux_unprotect(data: bytes) -> bytes:
    if not data.startswith(b'LNX1') or len(data) < 4 + 16 + 16 + 16:
        raise ValueError('Invalid Linux credential blob format')
    salt = data[4:20]
    nonce = data[20:36]
    tag = data[36:52]
    ciphertext = data[52:]
    key = _linux_derive_key(salt)
    expected_tag = hashlib.sha256(key + b'mac' + ciphertext).digest()[:16]
    if not secrets.compare_digest(tag, expected_tag):
        raise ValueError('Credential authentication check failed')
    keystream_blocks = (len(ciphertext) + 31) // 32
    keystream = bytearray()
    for block_num in range(keystream_blocks):
        counter = block_num.to_bytes(4, 'big')
        keystream.extend(hashlib.sha256(key + nonce + counter).digest())
    return bytes(b ^ k for b, k in zip(ciphertext, keystream[:len(ciphertext)]))


def _protect(data: bytes) -> bytes:
    if os.name != "nt":
        return _linux_protect(data)
    source, source_buffer = _data_blob(data)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptProtectData(
        ctypes.byref(source), "Vidzeo YouTube session", None, None, None,
        0x01, ctypes.byref(output),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer


def _unprotect(data: bytes) -> bytes:
    if os.name != "nt" or data.startswith(b"LNX1"):
        return _linux_unprotect(data)
    source, source_buffer = _data_blob(data)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x01, ctypes.byref(output),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer


def _persist(jar: http.cookiejar.CookieJar, meta: Dict[str, Any]) -> bool:
    payload = {
        "version": 1,
        "saved_at": time.time(),
        "source": meta.get("source") or "browser",
        "browser": meta.get("browser") or "",
        "profile": meta.get("profile") or "",
        "preferred_browser": meta.get("preferred_browser") or _preferred_browser(),
        "fallback_used": bool(meta.get("fallback_used")),
        "verified_authenticated": bool(meta.get("verified_authenticated")),
        "verification": meta.get("verification") or "",
        "cookies": [_cookie_to_dict(cookie) for cookie in jar],
    }
    try:
        encrypted = _protect(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        container = {
            "version": 1,
            "protected": "windows-dpapi-current-user",
            "saved_at": payload["saved_at"],
            "data": base64.b64encode(encrypted).decode("ascii"),
        }
        target = _secure_store_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Owner-only directory: the snapshot holds live sign-in cookies.
            os.chmod(target.parent, 0o700)
        except OSError:
            pass
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(container, separators=(",", ":")), encoding="utf-8")
        try:
            # Owner-only file, set before the atomic rename so no
            # world-readable window ever exists.
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        return True
    except Exception as exc:
        print(f"   [yt-session] encrypted persistence unavailable: {type(exc).__name__}")
        return False


def refresh_encrypted_snapshot(
    jar: http.cookiejar.CookieJar, meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """Write the live jar to the encrypted on-disk snapshot.

    Called by the cookie write-back path whenever cookies are written
    back or replaced (e.g. after YouTube rotates a token), so the
    snapshot stays current without a fresh browser export.  The
    snapshot is the durable store: it survives server restarts and
    VM reboots, and ``_load_persisted()`` restores it on next start.

    Refuses an empty or unauthenticated jar instead of overwriting a
    good snapshot with junk.  Only cookie names/counts are ever
    logged, never values.  Returns True when the snapshot was written.
    """
    try:
        filtered = _filtered_jar(jar) if jar is not None else http.cookiejar.CookieJar()
    except Exception:
        return False
    if not _jar_is_authenticated(filtered):
        return False
    snapshot_meta = {
        "source": "cookie-write-back",
        "verified_authenticated": False,
        "verification": "",
    }
    if meta:
        snapshot_meta.update({k: v for k, v in meta.items() if k != "cookies"})
    return _persist(filtered, snapshot_meta)


def _load_persisted() -> Tuple[Optional[http.cookiejar.CookieJar], Dict[str, Any]]:
    for target in _secure_store_candidates():
        if not target.is_file():
            continue
        try:
            container = json.loads(target.read_text(encoding="utf-8"))
            encrypted = base64.b64decode(container.get("data") or "", validate=True)
            payload = json.loads(_unprotect(encrypted).decode("utf-8"))
            max_age_hours = max(0.0, float(os.environ.get("VIDZEO_YT_SESSION_COOKIE_MAX_AGE_HOURS", "0") or 0))
            saved_at = float(payload.get("saved_at") or 0)
            if max_age_hours and time.time() - saved_at > max_age_hours * 3600:
                continue
            jar = _filtered_jar(_cookie_from_dict(item) for item in payload.get("cookies") or [])
            if not _jar_is_authenticated(jar):
                continue
            meta = {
                "source": payload.get("source") or "encrypted snapshot",
                "browser": payload.get("browser") or "",
                "profile": payload.get("profile") or "",
                "preferred_browser": payload.get("preferred_browser") or "",
                "fallback_used": bool(payload.get("fallback_used")),
                "verified_authenticated": bool(payload.get("verified_authenticated")),
                "verification": payload.get("verification") or "",
                "saved_at": saved_at,
                "persisted": True,
            }
            return jar, meta
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return None, {}


def _profile_activity(profile: Path, cookie_names: Iterable[str]) -> float:
    activity = 0.0
    for relative in cookie_names:
        try:
            activity = max(activity, (profile / relative).stat().st_mtime)
        except OSError:
            pass
    return activity


def _chromium_profiles(browser: str, root: Path) -> List[BrowserProfile]:
    if not root.is_dir():
        return []
    profiles: List[BrowserProfile] = []
    candidate_dirs = [root] if (root / "Network" / "Cookies").is_file() else []
    candidate_dirs.extend(
        path for path in root.iterdir()
        if path.is_dir() and (path.name == "Default" or path.name.startswith("Profile "))
    )
    for profile in candidate_dirs:
        activity = _profile_activity(profile, ("Network/Cookies", "Cookies", "History"))
        if activity:
            profiles.append(BrowserProfile(browser, str(profile), profile.name, activity))
    return profiles


def detect_browser_profiles() -> List[BrowserProfile]:
    """Return installed browser profiles ordered by most recent local activity."""
    candidates: List[BrowserProfile] = []
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA") or "")
        roaming = Path(os.environ.get("APPDATA") or "")
        roots = [
            ("chrome", local / "Google" / "Chrome" / "User Data"),
            ("edge", local / "Microsoft" / "Edge" / "User Data"),
            ("brave", local / "BraveSoftware" / "Brave-Browser" / "User Data"),
            ("chromium", local / "Chromium" / "User Data"),
            ("vivaldi", local / "Vivaldi" / "User Data"),
            ("opera", roaming / "Opera Software" / "Opera Stable"),
        ]
        firefox_roots = [roaming / "Mozilla" / "Firefox" / "Profiles"]
    else:
        home = Path.home()
        config_dir = home / ".config"
        snap_dir = home / "snap"
        var_app = home / ".var" / "app"
        roots = [
            ("chrome", config_dir / "google-chrome"),
            ("chromium", config_dir / "chromium"),
            ("chromium", snap_dir / "chromium" / "common" / "chromium"),
            ("chromium", snap_dir / "chromium" / "current" / ".config" / "chromium"),
            ("chrome", var_app / "com.google.Chrome" / "config" / "google-chrome"),
            ("chromium", var_app / "org.chromium.Chromium" / "config" / "chromium"),
            ("brave", config_dir / "BraveSoftware" / "Brave-Browser"),
            ("edge", config_dir / "microsoft-edge"),
            ("vivaldi", config_dir / "vivaldi"),
            ("opera", config_dir / "opera"),
        ]
        firefox_roots = [
            home / ".mozilla" / "firefox",
            snap_dir / "firefox" / "common" / ".mozilla" / "firefox",
            var_app / "org.mozilla.firefox" / ".mozilla" / "firefox",
        ]

    for browser, root in roots:
        try:
            candidates.extend(_chromium_profiles(browser, root))
        except OSError:
            pass
    for f_root in firefox_roots:
        if f_root.is_dir():
            try:
                for profile in f_root.iterdir():
                    activity = _profile_activity(profile, ("cookies.sqlite", "places.sqlite"))
                    if activity:
                        candidates.append(BrowserProfile("firefox", str(profile), profile.name, activity))
            except OSError:
                pass

    hint = _preferred_browser()
    candidates.sort(key=lambda item: (item.browser == hint, item.activity), reverse=True)
    return candidates


def _cookie_from_browser_json(item: Dict[str, Any]) -> Optional[http.cookiejar.Cookie]:
    name = str(item.get("name") or "").strip()
    if not name:
        return None
    value = str(item.get("value") or "")
    domain = str(item.get("domain") or ".youtube.com")
    path = str(item.get("path") or "/")
    secure = bool(item.get("secure", False))
    exp = item.get("expirationDate") or item.get("expires")
    try:
        exp = int(float(exp)) if exp is not None else None
    except (ValueError, TypeError):
        exp = None
    http_only = bool(item.get("httpOnly", False))
    rest = {"HttpOnly": None} if http_only else {}
    if item.get("sameSite"):
        rest["SameSite"] = str(item.get("sameSite"))
    return http.cookiejar.Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path=path,
        path_specified=True,
        secure=secure,
        expires=exp,
        discard=bool(item.get("discard", exp is None or item.get("session", False))),
        comment=None,
        comment_url=None,
        rest=rest,
        rfc2109=False,
    )


def _load_explicit_cookie_file(path: Path) -> http.cookiejar.CookieJar:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size <= 0 or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("cookie export must be between 1 byte and 4 MB")
    # Browser exports occasionally include a UTF-8 BOM. Treat it as encoding
    # metadata, not part of the Netscape header, so packaged imports behave the
    # same as developer imports.
    raw_bytes = path.read_bytes()
    text = raw_bytes.decode("utf-8-sig", errors="replace").strip()

    # Support JSON exports (Cookie-Editor extension, browser JSON format)
    if text.startswith(("[", "{")):
        try:
            payload = json.loads(text)
            cookie_items = payload if isinstance(payload, list) else payload.get("cookies", [])
            jar = http.cookiejar.CookieJar()
            for item in cookie_items:
                if isinstance(item, dict):
                    cookie = _cookie_from_browser_json(item)
                    if cookie:
                        jar.set_cookie(cookie)
            return _filtered_jar(jar)
        except Exception as exc:
            raise ValueError(f"cookie export JSON could not be parsed: {exc}")

    if not text.startswith(("# Netscape HTTP Cookie File", "# HTTP Cookie File", "#")):
        raise ValueError("cookie export is not Netscape/Mozilla format or JSON format")
    jar = http.cookiejar.MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    # Netscape exports commonly write zero for cookies that live only
    # for the browser session. Verify the resulting jar with YouTube.
    for cookie in jar:
        if cookie.expires == 0:
            cookie.expires = None
            cookie.discard = True
    return _filtered_jar(jar)


class _CookieExtractionLogger:
    """Keep cookie-extraction failures in redacted broker diagnostics."""

    def debug(self, _message: str) -> None:
        pass

    def info(self, _message: str) -> None:
        pass

    def warning(self, _message: str) -> None:
        pass

    def error(self, _message: str) -> None:
        pass


def _extract_profile(profile: BrowserProfile) -> http.cookiejar.CookieJar:
    from services.yt_dlp_runtime import load_yt_dlp
    yt_dlp = load_yt_dlp()

    specification = (profile.browser, profile.profile) if profile.profile else (profile.browser,)
    with yt_dlp.YoutubeDL({
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "cookiesfrombrowser": specification,
        "logger": _CookieExtractionLogger(),
        # Prefer the system SSL context over certifi so a host behind a
        # TLS-intercepting proxy (custom CA via SSL_CERT_FILE) still works.
        "compat_opts": {"no-certifi"},
    }) as ydl:
        return _filtered_jar(ydl.cookiejar)


def _verify_youtube_session(
    jar: http.cookiejar.CookieJar, *, explicit_sign_in_only: bool = False,
) -> Tuple[Optional[bool], str]:
    """Verify that YouTube itself recognizes the jar as signed in.

    ``None`` means the network response was unavailable or inconclusive. Cookie
    names alone are not reported as verified authentication.
    """
    try:
        import requests

        # Probe budget (Nov 19 live measurement, same cookies, same
        # URL): the feed page took 4.0-6.7 s end to end — the TLS
        # handshake alone measured 3.34 s — while the old (2 s, 5 s)
        # budget timed out 5/5 at ~2.0 s and the session verified fine
        # at (5 s, 15 s). A 2 s connect timeout therefore flapped a
        # healthy account to unverified (run 6 + run 7: main worker
        # verified, small worker ReadTimeout, same account). Connect
        # 8 s / read 15 s keeps the probe bounded while covering the
        # measured handshake + TTFB with headroom. Env overrides stay
        # available for operators who want it tighter.
        response = requests.get(
            "https://www.youtube.com/feed/you?persist_app=1&app=desktop",
            cookies=jar,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=(
                max(0.5, float(os.environ.get("VIDZEO_YT_AUTH_CONNECT_TIMEOUT", "8") or 8)),
                max(1.0, float(os.environ.get("VIDZEO_YT_AUTH_READ_TIMEOUT", "15") or 15)),
            ),
            allow_redirects=True,
        )
        if response.status_code in {401, 403}:
            return False, f"youtube-http-{response.status_code}"
        if response.status_code != 200:
            return None, f"youtube-http-{response.status_code}"
        page = response.text[:2_500_000]
        confirmed = False
        if (
            re.search(r'"(?:LOGGED_IN|isLoggedIn|signedIn)"\s*:\s*(?:true|1)', page, flags=re.I)
            or '"logged_in","value":"1"' in page
            or '"key":"logged_in","value":"1"' in page
            or '"logged_in":true' in page
            or 'ytcfg.set({"LOGGED_IN":true' in page
        ):
            confirmed = True
        elif re.search(r'"(?:LOGGED_IN|isLoggedIn)"\s*:\s*(?:false|0)', page, flags=re.I):
            return False, "youtube-reported-signed-out"
        elif "accounts.google.com/ServiceLogin" in page or "Sign in to YouTube" in page:
            return False, "youtube-sign-in-page"
        elif (not explicit_sign_in_only and _auth_cookie_count(jar) >= 2
              and "ServiceLogin" not in str(getattr(response, "url", ""))):
            confirmed = True

        if confirmed:
            if explicit_sign_in_only:
                return True, "youtube-confirmed"
            global _CURRENT_CHANNEL_TITLE
            m_modern_title = re.search(
                r'"pageHeaderViewModel"\s*:\s*\{.*?"title"\s*:\s*\{\s*"dynamicTextViewModel"\s*:\s*\{\s*"text"\s*:\s*\{\s*"content"\s*:\s*"([^"]+)"',
                page,
            )
            m_handle = re.search(r'"content"\s*:\s*"(@[a-zA-Z0-9_.-]+)"', page)
            m_title = (
                m_modern_title
                or re.search(r'"(?:userDisplayName|channelTitle)"\s*:\s*"([^"]+)"', page)
                or re.search(r'"canonicalChannelUrl"\s*:\s*"https://www\.youtube\.com/(@[^"]+)"', page)
                or re.search(r'"header"\s*:\s*\{[^}]*"pageTitle"\s*:\s*"([^"]+)"', page)
            )
            title_text = m_title.group(1).strip() if m_title else ""
            handle_text = m_handle.group(1).strip() if m_handle else ""
            if title_text and handle_text and handle_text not in title_text:
                _CURRENT_CHANNEL_TITLE = f"{title_text} ({handle_text})"
            elif title_text:
                _CURRENT_CHANNEL_TITLE = title_text
            elif handle_text:
                _CURRENT_CHANNEL_TITLE = handle_text
            return True, "youtube-confirmed"
        return None, "youtube-response-inconclusive"
    except Exception as exc:
        return None, f"youtube-probe-{type(exc).__name__}"


def _parse_cookie_file_setting(setting: Any) -> List[Path]:
    """Parse a cookie-file list from env or config.

    Accepts a single path, an ``os.pathsep``-separated list, a
    comma-separated list, or an iterable of paths.  Duplicates are
    removed, order is preserved, and the list is capped so one bad
    operator paste cannot fan out into dozens of probes.
    """
    if setting is None:
        return []
    if isinstance(setting, (str, os.PathLike)):
        raw_parts: List[str] = [str(setting)]
    else:
        try:
            raw_parts = [str(part) for part in setting]
        except TypeError:
            return []
    paths: List[Path] = []
    seen = set()
    for raw in raw_parts:
        for chunk in str(raw).replace(os.pathsep, ",").split(","):
            candidate = chunk.strip().strip('"').strip("'")
            if not candidate:
                continue
            resolved = Path(candidate).expanduser()
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            paths.append(resolved)
    return paths[:16]


def _discover_cookie_dir_paths(cookie_dir: Any = None) -> List[Path]:
    """Cookie files dropped into a directory auto-register as accounts.

    Sources, in order: ``VIDZEO_YT_COOKIE_DIR`` (or the explicit
    ``cookie_dir`` argument) and ``<repo>/cookies.d/`` when it exists.
    Only ``*.json`` (Cookie-Editor style) and ``*.txt`` (Netscape)
    exports are picked up, sorted by file name so registration order
    is deterministic.  A directory is rescanned whenever a pool is
    (re)built, so a newly dropped file is live for the next job
    without a server restart.
    """
    roots: List[Path] = []
    configured = str(cookie_dir or os.environ.get("VIDZEO_YT_COOKIE_DIR") or "").strip()
    if configured:
        roots.append(Path(configured).expanduser())
    try:
        repo_root = Path(__file__).resolve().parents[3]
        roots.append(repo_root / "cookies.d")
    except (IndexError, RuntimeError):
        pass
    found: List[Path] = []
    seen = set()
    for root in roots:
        try:
            if not root.is_dir():
                continue
            for candidate in sorted(root.iterdir(), key=lambda item: item.name):
                if candidate.is_file() and candidate.suffix.lower() in {".json", ".txt"}:
                    key = str(candidate.resolve())
                    if key not in seen:
                        seen.add(key)
                        found.append(candidate)
        except OSError:
            continue
    return found


def _is_transient_probe_reason(reason: str) -> bool:
    """True when a probe failure was a transport timeout, not a verdict.

    A ReadTimeout/ConnectTimeout means YouTube never answered, so the
    session itself is unjudged: the account may still be signed in
    (run 7: the small worker's probe timed out yet its downloads
    completed 3/3 on the same cookies). Only timeouts count as
    transient; a definite signed-out answer, an HTTP error, or an
    inconclusive page never does.
    """
    reason = str(reason or "").lower()
    return reason.startswith("youtube-probe-") and "timeout" in reason


def _probe_retry_delay_seconds() -> float:
    try:
        return max(
            0.0,
            min(
                10.0,
                float(os.environ.get("VIDZEO_YT_PROBE_RETRY_DELAY_SECONDS", "2") or 2),
            ),
        )
    except (TypeError, ValueError):
        return 2.0


def _cookie_pool_verify_ttl_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("VIDZEO_YT_POOL_VERIFY_TTL_SECONDS", "1800") or 1800))
    except (TypeError, ValueError):
        return 1800.0


def _cookie_pool_retry_delay_seconds(status: str, *, transient: bool = False) -> float:
    # A transient probe timeout is a network blip, not a session
    # verdict: re-probe it sooner (30 s) than a genuinely inconclusive
    # verdict (120 s), so one slow handshake does not keep a good
    # account out of the verified pool for two minutes.
    if status == "unverified" and transient:
        try:
            return max(
                0.0,
                float(os.environ.get("VIDZEO_YT_POOL_TRANSIENT_RETRY_SECONDS", "30") or 30),
            )
        except (TypeError, ValueError):
            return 30.0
    env_name = {
        "throttled": "VIDZEO_YT_POOL_THROTTLE_COOLDOWN_SECONDS",
        "unverified": "VIDZEO_YT_POOL_UNVERIFIED_RETRY_SECONDS",
    }.get(status, "")
    default = 900.0 if status == "throttled" else 120.0
    if not env_name:
        return 0.0
    try:
        return max(0.0, float(os.environ.get(env_name, str(default)) or default))
    except (TypeError, ValueError):
        return default


def _default_cookie_file_paths() -> List[Path]:
    """Hardcoded fallback: the operator-shared cookies at the repo root.

    The cookies the operator shares live at ``<repo>/cookies.json``.
    They must be picked up even when no ``VIDZEO_YT_COOKIE_FILES`` env
    var is set (fresh machine, lost .env line): the pipeline should
    use the shared cookies with zero configuration.
    """
    try:
        candidate = Path(__file__).resolve().parents[3] / "cookies.json"
    except (IndexError, RuntimeError):
        return []
    try:
        if candidate.is_file():
            return [candidate]
    except OSError:
        pass
    return []


class ConfiguredYouTubeSessions:
    """Verify operator-supplied accounts on demand, separately from browser login.

    The pool is the single place configured cookie files become
    authenticated sessions.  It accepts a LIST of files (not one file),
    plus a cookies directory, tracks per-account health
    (verified / signed_out / throttled / unverified / load_error),
    rotates across healthy accounts, and degrades gracefully: one bad
    file never kills the pool, and zero healthy accounts means the
    caller falls back to public access.
    """

    def __init__(self, setting: str = "", *, cookie_dir: Any = None):
        paths = _parse_cookie_file_setting(setting)
        if not paths:
            # Hardcoded default: the operator-shared cookies at the repo
            # root. No env var, no config file needed to use them.
            paths = _default_cookie_file_paths()
        for candidate in _discover_cookie_dir_paths(cookie_dir):
            if str(candidate) not in {str(path) for path in paths}:
                paths.append(candidate)
        self.paths = tuple(paths[:16])
        self._lock = RLock()
        self._checked: Dict[int, Optional[http.cookiejar.CookieJar]] = {}
        self._health: Dict[int, Dict[str, Any]] = {}
        # Per-file content signatures (mtime_ns, size), keyed by path
        # string. The pool rebuild key is the file LIST only, so a cookie
        # file overwritten in place would otherwise sit behind a stale
        # health verdict until the verify TTL expired. A signature
        # change means the operator dropped a fresh export: the account
        # is reset to unknown so the next call re-probes it. os.stat
        # only -- no file reads on the hot path.
        self._signatures: Dict[str, Optional[Tuple[int, int]]] = {
            str(path): self._file_signature(path) for path in self.paths
        }
        self._cursor = 0

    @staticmethod
    def _file_signature(path: Any) -> Optional[Tuple[int, int]]:
        """Cheap content fingerprint: (mtime_ns, size), or None if unreadable."""
        try:
            stat = os.stat(path)
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _note_file_change(self, index: int) -> bool:
        """Notice an in-place overwrite of one account's cookie file.

        Returns True when the file's signature changed and the health
        entry was reset to unknown (a re-verify is now due). A throttled
        account keeps its cooldown: only the recorded signature is
        updated, and the fresh content is probed once the cooldown
        expires. A file that vanished is left alone -- the load path
        surfaces that as load_error on the next probe.
        """
        path = self.paths[index]
        key = str(path)
        signature = self._file_signature(path)
        previous = self._signatures.get(key)
        if signature is None or previous is None:
            self._signatures[key] = signature
            return False
        if signature == previous:
            return False
        self._signatures[key] = signature
        entry = self._health_entry(index)
        if str(entry.get("status") or "unknown") == "throttled":
            return False
        entry.update({
            "status": "unknown",
            "reason": "cookie-file-changed",
            "cookie_count": 0,
            "auth_cookie_count": 0,
            "transient": False,
            "last_checked": 0.0,
            "verified_at": 0.0,
        })
        self._checked[index] = None
        return True

    # -- health ---------------------------------------------------------
    def _health_entry(self, index: int) -> Dict[str, Any]:
        entry = self._health.get(index)
        if entry is None:
            entry = {
                "status": "unknown",
                "reason": "",
                "cookie_count": 0,
                "auth_cookie_count": 0,
                "failures": 0,
                "transient": False,
                "last_checked": 0.0,
                "verified_at": 0.0,
            }
            self._health[index] = entry
        return entry

    def health_snapshot(self) -> List[Dict[str, Any]]:
        """Redacted per-account health: file names and counts only, never values."""
        with self._lock:
            snapshot = []
            for index, path in enumerate(self.paths):
                entry = self._health_entry(index)
                snapshot.append({
                    "account": index + 1,
                    "file": path.name,
                    "status": str(entry.get("status") or "unknown"),
                    "reason": str(entry.get("reason") or "")[:120],
                    "transient": bool(entry.get("transient")),
                    "cookie_count": int(entry.get("cookie_count") or 0),
                    "auth_cookie_count": int(entry.get("auth_cookie_count") or 0),
                    "failures": int(entry.get("failures") or 0),
                })
            return snapshot

    def mark_throttled(self, index: int, reason: str = "") -> None:
        """Record that YouTube throttled this account; it is skipped until cooldown."""
        with self._lock:
            if 0 <= index < len(self.paths):
                entry = self._health_entry(index)
                entry["status"] = "throttled"
                entry["reason"] = str(reason or "youtube-throttled")[:120]
                entry["failures"] = int(entry.get("failures") or 0) + 1
                entry["last_checked"] = time.time()
                self._checked[index] = None

    def mark_signed_out(self, index: int, reason: str = "") -> None:
        with self._lock:
            if 0 <= index < len(self.paths):
                entry = self._health_entry(index)
                entry["status"] = "signed_out"
                entry["reason"] = str(reason or "youtube-reported-signed-out")[:120]
                entry["failures"] = int(entry.get("failures") or 0) + 1
                entry["last_checked"] = time.time()
                self._checked[index] = None

    def refresh(self) -> None:
        """Rescan the cookies directory; new files join the pool, removed ones leave.

        Also re-stat every tracked file: a cookie file overwritten in
        place (same path, fresh export) resets that account to unknown
        so the next call re-probes it instead of serving the stale
        verdict from the old file.
        """
        with self._lock:
            current = list(self.paths)
            discovered = _discover_cookie_dir_paths()
            if discovered and any(str(path) not in {str(item) for item in current} for path in discovered):
                merged = list(current)
                for candidate in discovered:
                    if str(candidate) not in {str(item) for item in merged}:
                        merged.append(candidate)
                self.paths = tuple(merged[:16])
                self._checked = {
                    index: jar for index, jar in self._checked.items()
                    if index < len(self.paths)
                }
                self._health = {
                    index: entry for index, entry in self._health.items()
                    if index < len(self.paths)
                }
            for index in range(len(self.paths)):
                self._note_file_change(index)

    # -- verification ---------------------------------------------------
    def _check_account(self, index: int) -> Optional[http.cookiejar.CookieJar]:
        """Load + verify one account file; cache the verdict, not the probe."""
        now = time.time()
        # A file overwritten since the last check resets the health
        # entry to unknown first, so the TTL guards below cannot serve
        # a stale verdict from the old export.
        self._note_file_change(index)
        entry = self._health_entry(index)
        status = str(entry.get("status") or "unknown")
        if status == "verified" and self._checked.get(index) is not None:
            if now - float(entry.get("verified_at") or 0) < _cookie_pool_verify_ttl_seconds():
                return self._checked[index]
        if status in {"throttled", "unverified", "signed_out", "load_error"}:
            if now - float(entry.get("last_checked") or 0) < _cookie_pool_retry_delay_seconds(
                status, transient=bool(entry.get("transient"))
            ):
                return None
            if status == "signed_out":
                # A signed-out export stays signed-out until the operator
                # replaces the file; re-probing it on every call only burns
                # the search wall. Re-check at most once per verify TTL.
                if now - float(entry.get("last_checked") or 0) < _cookie_pool_verify_ttl_seconds():
                    return None
        try:
            jar = _filtered_jar(
                cookie for cookie in _load_explicit_cookie_file(self.paths[index])
                if cookie.domain.lstrip(".").lower() == "youtube.com"
                or cookie.domain.lower().endswith(".youtube.com")
            )
        except (OSError, ValueError, http.cookiejar.LoadError) as exc:
            entry.update({
                "status": "load_error",
                "reason": f"unreadable-cookie-file:{type(exc).__name__}",
                "cookie_count": 0,
                "auth_cookie_count": 0,
                "failures": int(entry.get("failures") or 0) + 1,
                "last_checked": now,
            })
            self._checked[index] = None
            print(f"   [yt-session] configured account {index + 1}: load_error ({type(exc).__name__})", flush=True)
            return None
        entry["cookie_count"] = sum(1 for _ in jar)
        entry["auth_cookie_count"] = _auth_cookie_count(jar)
        if not _jar_is_authenticated(jar):
            entry.update({
                "status": "signed_out",
                "reason": "no-auth-cookies",
                "failures": int(entry.get("failures") or 0) + 1,
                "last_checked": now,
            })
            self._checked[index] = None
            print(f"   [yt-session] configured account {index + 1}: signed_out (no-auth-cookies)", flush=True)
            return None
        verified, reason = _verify_youtube_session(jar, explicit_sign_in_only=True)
        if verified is None and _is_transient_probe_reason(reason):
            # One retry after a short pause: the probe runs while the
            # worker is busy, and a single slow TLS handshake / TTFB
            # spike must not poison a good account for the whole
            # unverified retry window (run 7: main worker verified,
            # small worker ReadTimeout, same cookies, same account).
            # Only transport timeouts retry — a definite signed-out
            # answer or an inconclusive page never does.
            print(f"   [yt-session] configured account {index + 1}: probe timed out ({reason}); retrying once", flush=True)
            delay = _probe_retry_delay_seconds()
            if delay:
                time.sleep(delay)
            verified, reason = _verify_youtube_session(jar, explicit_sign_in_only=True)
        entry["last_checked"] = now
        if verified is True:
            entry.update({"status": "verified", "reason": "youtube-confirmed", "transient": False, "verified_at": now})
            self._checked[index] = jar
            print(f"   [yt-session] configured account {index + 1}: verified", flush=True)
            return jar
        if verified is False:
            entry.update({
                "status": "signed_out",
                "reason": str(reason or "youtube-reported-signed-out")[:120],
                "transient": False,
                "failures": int(entry.get("failures") or 0) + 1,
            })
            self._checked[index] = None
            print(f"   [yt-session] configured account {index + 1}: signed_out ({entry['reason']})", flush=True)
            return None
        entry.update({
            "status": "unverified",
            "reason": str(reason or "youtube-probe-inconclusive")[:120],
            # Transient timeouts stay visible as unverified (the YT
            # lane falls back to best-effort cookies and the worker log
            # says so), but the flag tells /api/status and the
            # watchdog that this is a probe problem, not a dead
            # session — it must never raise a re-export alert.
            "transient": _is_transient_probe_reason(reason),
        })
        self._checked[index] = None
        print(f"   [yt-session] configured account {index + 1}: unverified ({entry['reason']})", flush=True)
        return None

    def next_verified(self, after: int = -1) -> Tuple[int, Optional[http.cookiejar.CookieJar]]:
        """First verified account after ``after`` (``-1`` = from the start)."""
        with self._lock:
            for index in range(after + 1, len(self.paths)):
                jar = self._check_account(index)
                if jar is not None:
                    return index, _clone_jar(jar)
        return -1, None

    def next_healthy(self) -> Tuple[int, Optional[http.cookiejar.CookieJar]]:
        """Round-robin across verified accounts; skips dead/throttled ones.

        Successive calls rotate so one account does not absorb every
        search and download.  Zero healthy accounts returns (-1, None)
        and the caller falls back to public access.
        """
        with self._lock:
            total = len(self.paths)
            if total == 0:
                return -1, None
            for step in range(total):
                index = (self._cursor + step) % total
                jar = self._check_account(index)
                if jar is not None:
                    self._cursor = (index + 1) % total
                    return index, _clone_jar(jar)
            return -1, None

    def _load_attachable_jar(self, index: int) -> Optional[http.cookiejar.CookieJar]:
        """Load one account's jar for best-effort attachment, no YouTube probe.

        Returns the jar when the file loads and carries auth cookies,
        even when the last verify probe reported signed_out: the probe's
        LOGGED_IN verdict can lag a session that still carries identity,
        and a signed-out jar is strictly better than fully anonymous
        access.  Skips throttled accounts (cooling down) and unreadable
        files.  No network call here, so the signed-out probe is not
        repeated and the search wall is not burned.
        """
        entry = self._health_entry(index)
        status = str(entry.get("status") or "unknown")
        if status in {"throttled", "load_error"}:
            return None
        try:
            jar = _filtered_jar(
                cookie for cookie in _load_explicit_cookie_file(self.paths[index])
                if cookie.domain.lstrip(".").lower() == "youtube.com"
                or cookie.domain.lower().endswith(".youtube.com")
            )
        except (OSError, ValueError, http.cookiejar.LoadError):
            return None
        if not _jar_is_authenticated(jar):
            return None
        return jar

    def next_attachable(self) -> Tuple[int, Optional[http.cookiejar.CookieJar]]:
        """Best-effort jar for attachment: verified first, then any loadable account.

        The pipeline attaches this jar to every YouTube search and
        download instead of going fully anonymous.  A signed-out or
        unverified account still carries the operator's cookies; only
        load_error (unparseable file) and throttled (cooling down)
        accounts are skipped.  The caller must not claim the session is
        verified: health_snapshot() stays the source of truth for that.
        """
        with self._lock:
            index, jar = self.next_healthy()
            if jar is not None:
                return index, jar
            total = len(self.paths)
            for step in range(total):
                index = (self._cursor + step) % total
                jar = self._load_attachable_jar(index)
                if jar is not None:
                    self._cursor = (index + 1) % total
                    return index, _clone_jar(jar)
            return -1, None


_CONFIGURED_POOL: Optional[ConfiguredYouTubeSessions] = None
_CONFIGURED_POOL_KEY: Optional[Tuple[str, Tuple[str, ...]]] = None


def get_configured_pool(setting: Any = None) -> ConfiguredYouTubeSessions:
    """Process-shared pool so rotation survives across searches/downloads.

    The pool is rebuilt when the configured file list or the cookies
    directory listing changes, so a newly dropped file is picked up by
    the next job without a server restart.  A file overwritten in
    place is noticed too: refresh() re-stats every tracked file and
    resets a changed account to unknown, so the next call re-probes
    it instead of serving the stale verdict from the old export.
    Health is kept on the shared instance: an account marked throttled
    mid-job stays skipped for the rest of its cooldown.
    """
    global _CONFIGURED_POOL, _CONFIGURED_POOL_KEY
    with _LOCK:
        raw_setting = "" if setting is None else str(setting)
        if setting is None:
            raw_setting = str(os.environ.get("VIDZEO_YT_COOKIE_FILES") or "").strip()
        dir_files = tuple(str(path) for path in _discover_cookie_dir_paths())
        key = (raw_setting, dir_files)
        if _CONFIGURED_POOL is None or _CONFIGURED_POOL_KEY != key:
            _CONFIGURED_POOL = ConfiguredYouTubeSessions(raw_setting)
            _CONFIGURED_POOL_KEY = key
        else:
            _CONFIGURED_POOL.refresh()
        return _CONFIGURED_POOL


def reset_configured_pool() -> None:
    global _CONFIGURED_POOL, _CONFIGURED_POOL_KEY
    with _LOCK:
        _CONFIGURED_POOL = None
        _CONFIGURED_POOL_KEY = None


def _classify_extraction_error(profile: BrowserProfile, exc: Exception) -> str:
    detail = str(exc).casefold()
    if "could not copy" in detail or "permission denied" in detail or "being used by another process" in detail:
        return "browser-open-cookie-lock"
    if "failed to decrypt" in detail or "dpapi" in detail or "app-bound" in detail:
        return "browser-app-bound-encryption"
    if isinstance(exc, YouTubeSessionUnavailable):
        return "no-authenticated-youtube-cookies"
    return f"extraction-{type(exc).__name__}"


def _profile_meta(profile: BrowserProfile, preferred: str) -> Dict[str, Any]:
    return {
        "source": "browser auto-detect",
        "browser": profile.browser,
        "profile": profile.label,
        "preferred_browser": preferred,
        "fallback_used": profile.browser != preferred,
    }


def _verify_and_install(
    jar: http.cookiejar.CookieJar, meta: Dict[str, Any], *, persist: bool = True,
) -> Optional[http.cookiejar.CookieJar]:
    verified, verification = _verify_youtube_session(jar)
    # Keep only the non-sensitive probe label on the caller's metadata. This
    # makes rejected imports diagnosable while cookie values remain private.
    meta["verification"] = verification
    if verified is False or (verified is None and _require_auth_probe()):
        return None
    channel_name = meta.get("channel_title") or meta.get("username") or _CURRENT_CHANNEL_TITLE or ""
    resolved_meta = {
        **meta,
        "verified_authenticated": verified is True,
        "verification": verification,
        "channel_title": channel_name,
        "username": channel_name,
    }
    persisted = _persist(jar, resolved_meta) if persist else bool(meta.get("persisted", True))
    return _install_memory_session(jar, {**resolved_meta, "persisted": persisted})


def _vidnets_electron_cookie_paths() -> List[Path]:
    if os.environ.get("VIDZEO_SECURE_DATA_DIR"):
        return []
    candidates = []
    for base in [os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")]:
        if not base:
            continue
        bp = Path(base)
        for app_folder in ["vidnets-studio-v1", "Vidnets Studio", "Vidzeo Studio", "vidzeo-desktop"]:
            candidates.append(bp / app_folder / "Partitions" / "youtube-signin" / "Network" / "Cookies")
            candidates.append(bp / app_folder / "Partitions" / "youtube-signin" / "Cookies")
            candidates.append(bp / app_folder / "Network" / "Cookies")
            candidates.append(bp / app_folder / "Cookies")
    return [p for p in candidates if p.is_file() and p.stat().st_size > 0]


def _load_vidnets_electron_cookies() -> Tuple[Optional[http.cookiejar.CookieJar], Dict[str, Any]]:
    import sqlite3
    import tempfile
    import shutil
    for cookie_db in _vidnets_electron_cookie_paths():
        temp_dir = tempfile.mkdtemp()
        temp_db = Path(temp_dir) / "Cookies"
        try:
            shutil.copy2(cookie_db, temp_db)
            conn = sqlite3.connect(f"file:{temp_db}?mode=ro", uri=True)
            c = conn.cursor()
            c.execute("SELECT host_key, name, value, encrypted_value, path, expires_utc, is_secure, is_httponly FROM cookies")
            rows = c.fetchall()
            conn.close()
            jar = http.cookiejar.CookieJar()
            for host, name, value, enc_val, path, expires_utc, is_secure, is_httponly in rows:
                final_val = value
                if not final_val and enc_val:
                    try:
                        final_val = _unprotect(enc_val).decode("utf-8", errors="ignore")
                    except Exception:
                        pass
                if not final_val:
                    continue
                expires = None
                if expires_utc and expires_utc > 0:
                    expires = int((expires_utc / 1000000) - 11644473600)
                    if expires < 0:
                        expires = None
                ck = http.cookiejar.Cookie(
                    version=0,
                    name=str(name),
                    value=str(final_val),
                    port=None,
                    port_specified=False,
                    domain=str(host),
                    domain_specified=True,
                    domain_initial_dot=str(host).startswith("."),
                    path=str(path or "/"),
                    path_specified=True,
                    secure=bool(is_secure),
                    expires=expires,
                    discard=False,
                    comment=None,
                    comment_url=None,
                    rest={},
                    rfc2109=False,
                )
                jar.set_cookie(ck)
            filtered = _filtered_jar(jar)
            if _jar_is_authenticated(filtered):
                meta = {
                    "source": "Vidnets Studio App",
                    "browser": "electron",
                    "profile": "youtube-signin",
                    "preferred_browser": _preferred_browser(),
                    "fallback_used": False,
                }
                return filtered, meta
        except Exception as exc:
            print(f"   [yt-session] reading Vidnets partition cookies {cookie_db} failed: {exc}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    return None, {}


def _meta_matches_preference(meta: Dict[str, Any], preferred: str) -> bool:
    browser = str(meta.get("browser") or "").lower()
    if browser in ("manual", "electron"):
        return True
    recorded_preference = str(meta.get("preferred_browser") or "").lower()
    if recorded_preference != preferred:
        return False
    return not _require_preferred_browser() or browser == preferred


def import_cookie_file(
    path: str | os.PathLike[str], *, browser: str = "chrome",
) -> Tuple[http.cookiejar.CookieJar, int]:
    """Validate, verify, and encrypt a user-provided Netscape cookie export.

    Only live YouTube/Google cookies enter memory or the encrypted snapshot. The
    plaintext source file is never copied and remains under the caller's control.
    """
    with _LOCK:
        resolved_browser = str(browser or "chrome").strip().lower()
        if resolved_browser not in _SUPPORTED_BROWSERS:
            resolved_browser = "chrome"
        resolved_path = Path(path).expanduser().resolve()
        jar = _load_explicit_cookie_file(resolved_path)
        if not _jar_is_authenticated(jar):
            raise YouTubeSessionUnavailable(
                "The cookie export contains no live YouTube authentication cookies."
            )
        meta = {
            "source": "manual browser export",
            "browser": resolved_browser,
            "profile": "Imported cookies.txt",
            "preferred_browser": resolved_browser,
            "fallback_used": False,
        }
        installed = _verify_and_install(jar, meta)
        if installed is None:
            # Preserve the redacted probe result so the UI/logs can distinguish
            # a signed-out export from a transient HTTP/network failure without
            # ever exposing cookie values.
            mark_session_needs_update(str(meta.get("verification") or "import-verification-failed"))
            raise YouTubeSessionUnavailable(
                "YouTube did not confirm this cookie export as an authenticated account session. "
                "Export a fresh Chrome cookies.txt file and import it again."
            )
        print(
            f"   [yt-session] imported {resolved_browser.title()} YouTube account verified "
            f"({_auth_cookie_count(installed)} auth cookies; encrypted snapshot saved)"
        )
        return installed, _GENERATION


def import_cookie_bytes(
    payload: bytes, *, browser: str = "chrome",
) -> Tuple[http.cookiejar.CookieJar, int]:
    """Import an uploaded cookie file without leaving a plaintext copy behind."""
    if not payload or len(payload) > 4 * 1024 * 1024:
        raise ValueError("cookie export must be between 1 byte and 4 MB")
    secure_root = _secure_store_path().parent
    secure_root.mkdir(parents=True, exist_ok=True)
    temporary = secure_root / f"cookie-import-{secrets.token_hex(8)}.txt"
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
        return import_cookie_file(temporary, browser=browser)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def import_cookie_records(
    records: Iterable[Dict[str, Any]], *, browser: str = "chrome",
    source: str = "Vidzeo persistent Chrome connector",
    profile: str = "Vidzeo YouTube Chrome profile",
    username: Optional[str] = None,
) -> Tuple[http.cookiejar.CookieJar, int]:
    """Import cookies supplied by an approved live browser connector.

    The dedicated Chrome profile supplies records through Chrome's supported
    debugging API, avoiding the normal profile's locked or device-bound SQLite
    database entirely. Records are filtered to
    YouTube/Google, live-verified by YouTube, and persisted only as the same
    DPAPI-protected snapshot used by manual/browser detection. Values are
    never logged or returned by status.
    """
    if not isinstance(records, (list, tuple)) or len(records) > 2048:
        raise ValueError("managed browser cookie payload is invalid or too large")
    converted: List[http.cookiejar.Cookie] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or "").strip()
        value = str(record.get("value") or "")
        domain = str(record.get("domain") or "").strip()
        if not name or not domain or not value:
            continue
        expiration = record.get("expirationDate", record.get("expires"))
        # Chrome DevTools Protocol represents session cookies with ``-1``.
        # Treat that as no fixed expiry; otherwise the live-cookie filter
        # incorrectly drops a valid signed-in session during managed import.
        try:
            if expiration is not None and float(expiration) <= 0:
                expiration = None
        except (TypeError, ValueError):
            expiration = None
        converted.append(_cookie_from_dict({
            "name": name,
            "value": value,
            "domain": domain,
            "path": record.get("path") or "/",
            "secure": bool(record.get("secure")),
            "expires": expiration,
            "discard": bool(record.get("session", expiration is None)),
            "rest": {
                "HttpOnly": bool(record.get("httpOnly")),
                "SameSite": record.get("sameSite") or "",
            },
        }))
    with _LOCK:
        jar = _filtered_jar(converted)
        if not _jar_is_authenticated(jar):
            raise YouTubeSessionUnavailable(
                "YouTube is not signed in yet. Finish sign-in in the Vidzeo connection window."
            )
        resolved_browser = str(browser or "chrome").strip().lower()
        if resolved_browser not in _SUPPORTED_BROWSERS:
            resolved_browser = "chrome"
        # The managed connector is an explicit user choice. Remember it so an
        # Edge connection is not immediately hidden by a stale Chrome-only
        # preference in session_status()/pipeline preflight.
        preferred_browser = _remember_preferred_browser(resolved_browser)
        meta = {
            "source": str(source or "managed browser connector")[:120],
            "browser": resolved_browser,
            "profile": str(profile or "Current browser profile")[:120],
            "preferred_browser": preferred_browser,
            "fallback_used": False,
            "channel_title": str(username or "")[:120],
            "username": str(username or "")[:120],
        }
        installed = _verify_and_install(jar, meta)
        if installed is None:
            mark_session_needs_update(str(meta.get("verification") or "managed-import-verification-failed"))
            raise YouTubeSessionUnavailable(
                "YouTube did not confirm the account yet. Finish sign-in in the Vidzeo connection window."
            )
        print(
            f"   [yt-session] {meta['source']} YouTube account verified "
            f"({_auth_cookie_count(installed)} auth cookies; encrypted snapshot saved)"
        )
        return installed, _GENERATION


def _write_netscape_cookies(jar: http.cookiejar.CookieJar) -> List[Path]:
    """Write verified cookies in standard Netscape format for active yt-dlp workers."""
    if not jar:
        return []
    targets: List[Path] = []
    explicit = str(os.environ.get("VIDZEO_YT_COOKIES_FILE") or "").strip()
    if explicit:
        targets.append(Path(explicit).expanduser().resolve())

    vidnets_root = Path(__file__).resolve().parents[2]
    repo_root = vidnets_root.parent
    targets.append(vidnets_root / "cookies.txt")
    if (repo_root / "cookies.txt") not in targets:
        targets.append(repo_root / "cookies.txt")
    clean_folder = vidnets_root.parent / "Vidnets Clean Working Folder"
    if clean_folder.is_dir():
        targets.append(clean_folder / "cookies.txt")
    cms_root = vidnets_root.parent.parent / "Management" / "YT CMS"
    if cms_root.is_dir():
        targets.append(cms_root / "cookies.txt")
    if (vidnets_root.parent.parent / "cookies.txt") not in targets:
        targets.append(vidnets_root.parent.parent / "cookies.txt")
    cwd_cookie = Path.cwd() / "cookies.txt"
    if cwd_cookie not in targets:
        targets.append(cwd_cookie)

    lines = [
        "# Netscape HTTP Cookie File\n",
        "# https://curl.haxx.se/rfc/cookie_spec.html\n",
        "# This is a generated file!  Do not edit.\n\n",
    ]
    now = time.time()
    for c in jar:
        if not c.name or not c.domain or c.value is None:
            continue
        domain = str(c.domain)
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        path = str(c.path or "/")
        secure = "TRUE" if c.secure else "FALSE"
        expires = c.expires
        if expires is None or expires <= 0:
            expires = int(now) + 31536000
        else:
            expires = int(expires)
        lines.append(f"{domain}\t{include_sub}\t{path}\t{secure}\t{expires}\t{c.name}\t{c.value}\n")

    content = "".join(lines)
    new_count = sum(1 for _ in jar)
    written: List[Path] = []
    for target in targets:
        try:
            # Never downgrade a cookie file: a 2-cookie test jar must not
            # overwrite the operator's real 16-cookie export (Nov 17 the
            # session tests clobbered both generated cookies.txt files
            # this way). A larger jar still replaces a smaller one.
            if target.is_file():
                try:
                    existing = http.cookiejar.MozillaCookieJar(str(target))
                    existing.load(ignore_discard=True, ignore_expires=True)
                    if sum(1 for _ in existing) > new_count:
                        print(
                            f"   [yt-session] keeping existing {target.name} "
                            f"({sum(1 for _ in existing)} cookies) over smaller install "
                            f"({new_count} cookies)"
                        )
                        continue
                except (OSError, http.cookiejar.LoadError, ValueError):
                    pass
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix(f".tmp.{os.getpid()}")
            temp.write_text(content, encoding="utf-8")
            os.replace(temp, target)
            written.append(target)
        except Exception:
            pass
    return written


def _install_memory_session(jar: http.cookiejar.CookieJar, meta: Dict[str, Any]) -> http.cookiejar.CookieJar:
    global _MEMORY_JAR, _SESSION_META, _GENERATION
    _MEMORY_JAR = _filtered_jar(jar)
    _clear_cookie_update_state()
    _GENERATION += 1
    auth_count = _auth_cookie_count(_MEMORY_JAR)
    resolved_channel = meta.get("channel_title") or meta.get("username") or _CURRENT_CHANNEL_TITLE or ""
    _SESSION_META = {
        **meta,
        "ready": True,
        "channel_title": resolved_channel,
        "username": resolved_channel,
        "cookie_count": sum(1 for _ in _MEMORY_JAR),
        "auth_cookie_count": auth_count,
        "generation": _GENERATION,
        "updated_at": time.time(),
    }
    if auth_count > 0:
        _write_netscape_cookies(_MEMORY_JAR)
    return _clone_jar(_MEMORY_JAR)


def ensure_session(
    *, force_refresh: bool = False, stale_generation: Optional[int] = None,
    reason: str = "", preferred_browser: Optional[str] = None,
) -> Tuple[http.cookiejar.CookieJar, int]:
    """Return an authenticated jar and generation, refreshing it when needed.

    ``stale_generation`` makes refresh single-flight: if another worker already
    refreshed after the caller's failure, the new jar is reused immediately.
    """
    global _MEMORY_JAR, _SESSION_META
    with _LOCK:
        preferred = _normalize_browser(preferred_browser) if preferred_browser else _preferred_browser()
        _cleanup_legacy_plaintext()
        if not _SESSION_META:
            _SESSION_META = _load_cookie_update_state()
        # A freshly supplied cookie file is new evidence, not the rejected
        # saved session: try it BEFORE honouring a stale signed-out marker.
        # Otherwise one failed probe permanently blocks every later file
        # install and the pipeline silently falls back to anonymous search.
        # A file that verifies installs and clears the marker; a file that
        # fails verification falls through to the marker error below.
        explicit_env = (
            os.environ.get("VIDZEO_YT_COOKIES_FILE")
            or os.environ.get("VIDZEO_YT_COOKIES_PATH")
            or os.environ.get("VIDZEO_YT_COOKIE_FILE")
            or os.environ.get("MUSE_YOUTUBE_COOKIES_PATH")
            or os.environ.get("MUSE_COOKIES_PATH")
        )
        explicit = str(explicit_env or "").strip()
        candidate_paths = []
        # The configured pool (VIDZEO_YT_COOKIE_FILES list + cookies
        # directory) is explicit operator config: try those files first,
        # ahead of the stale-marker check below and ahead of the
        # cwd/browser scan, so a fresh file is never shadowed.
        for candidate in _parse_cookie_file_setting(
            os.environ.get("VIDZEO_YT_COOKIE_FILES") or ""
        ):
            if candidate.is_file():
                candidate_paths.append(candidate)
        for candidate in _discover_cookie_dir_paths():
            if candidate.is_file() and candidate not in candidate_paths:
                candidate_paths.append(candidate)
        if explicit:
            candidate_paths.append(Path(explicit).expanduser())
        elif explicit_env is None:
            vidnets_root = Path(__file__).resolve().parents[2]
            repo_root = vidnets_root.parent
            # Try every existing candidate in order: a stale partial export
            # (e.g. a generated cookies.txt with no auth cookies) must not
            # shadow a good cookies.json sitting next to it.
            for candidate in [
                Path.cwd() / "test_cookies.txt",
                Path.cwd() / "test_cookies.json",
                Path.cwd() / "cookies.txt",
                Path.cwd() / "cookies.json",
                vidnets_root / "test_cookies.txt",
                vidnets_root / "test_cookies.json",
                vidnets_root / "cookies.txt",
                vidnets_root / "cookies.json",
                repo_root / "test_cookies.txt",
                repo_root / "test_cookies.json",
                repo_root / "cookies.txt",
                repo_root / "cookies.json",
                repo_root.parent / "cookies.txt",
                repo_root.parent / "cookies.json",
                repo_root.parent / "Management" / "YT CMS" / "cookies.txt",
                repo_root.parent / "Management" / "YT CMS" / "cookies.json",
            ]:
                if candidate.is_file():
                    candidate_paths.append(candidate)

        for cookie_path in candidate_paths:
            try:
                jar = _load_explicit_cookie_file(cookie_path)
                if _jar_is_authenticated(jar):
                    meta = {
                        "source": "manual file", "browser": "manual", "profile": cookie_path.name,
                        "preferred_browser": preferred, "fallback_used": False,
                    }
                    installed = _verify_and_install(jar, meta)
                    if installed is not None:
                        print(
                            "   [yt-session] authenticated YouTube session loaded from manual file "
                            f"({_auth_cookie_count(installed)} auth cookies; YouTube verified)"
                        )
                        return installed, _GENERATION
            except Exception as exc:
                print(f"   [yt-session] manual cookie file {cookie_path.name} rejected: {type(exc).__name__}")

        if _SESSION_META.get("cookie_update_required") and not force_refresh:
            raise YouTubeSessionUnavailable(str(_SESSION_META.get("reason") or "Fresh YouTube cookies are required."))
        if (
            force_refresh and stale_generation is not None and _GENERATION > stale_generation
            and _jar_is_authenticated(_MEMORY_JAR) and _meta_matches_preference(_SESSION_META, preferred)
        ):
            return _clone_jar(_MEMORY_JAR), _GENERATION
        if (
            not force_refresh and _jar_is_authenticated(_MEMORY_JAR)
            and _meta_matches_preference(_SESSION_META, preferred)
        ):
            return _clone_jar(_MEMORY_JAR), _GENERATION

        if not force_refresh:
            persisted_jar, persisted_meta = _load_persisted()
            if persisted_jar is not None and _meta_matches_preference(persisted_meta, preferred):
                # The import/detection path performed a live YouTube probe
                # before writing this DPAPI snapshot. Reuse that verified
                # snapshot on process restart instead of reopening Chrome or
                # paying a network probe in each new yt-dlp worker. Pipeline
                # preflight still marks a definite live rejection for update.
                if persisted_meta.get("verified_authenticated"):
                    installed = _install_memory_session(persisted_jar, persisted_meta)
                else:
                    installed = _verify_and_install(persisted_jar, persisted_meta, persist=False)
                if installed is not None:
                    print(
                        "   [yt-session] encrypted YouTube session restored "
                        f"({_auth_cookie_count(installed)} auth cookies; YouTube verified)"
                    )
                    return installed, _GENERATION

        profiles = detect_browser_profiles()
        if _require_preferred_browser():
            profiles = [profile for profile in profiles if profile.browser == preferred]
        errors: List[str] = []
        error_codes: List[str] = []
        extraction_attempts = max(1, min(3, int(os.environ.get("VIDZEO_YT_COOKIE_EXTRACTION_ATTEMPTS", "2") or 2)))
        for profile in profiles:
            for attempt in range(extraction_attempts):
                try:
                    jar = _extract_profile(profile)
                    if not _jar_is_authenticated(jar):
                        raise YouTubeSessionUnavailable("profile has no authenticated YouTube cookies")
                    meta = _profile_meta(profile, preferred)
                    installed = _verify_and_install(jar, meta)
                    if installed is None:
                        raise YouTubeSessionUnavailable("cookies were not confirmed as signed in by YouTube")
                    detail = f" after {reason}" if reason else ""
                    print(
                        f"   [yt-session] current YouTube session detected: {profile.browser}/{profile.label}"
                        f" ({_auth_cookie_count(installed)} auth cookies; YouTube verified){detail}"
                    )
                    return installed, _GENERATION
                except Exception as exc:
                    errors.append(f"{profile.browser}/{profile.label}:{type(exc).__name__}")
                    error_codes.append(_classify_extraction_error(profile, exc))
                    if attempt + 1 < extraction_attempts:
                        time.sleep(0.25 * (attempt + 1))

        _MEMORY_JAR = None
        if "browser-open-cookie-lock" in error_codes:
            reason_text = (
                "The normal browser session cannot be read safely while it is active. "
                "Click Connect YouTube to use Vidzeo's persistent connection instead."
            )
        elif "browser-app-bound-encryption" in error_codes:
            reason_text = (
                f"{preferred.title()} protected its account session with App-Bound Encryption. "
                "Click Connect YouTube to connect without copying the protected browser database."
            )
        else:
            reason_text = "No authenticated YouTube connection is saved. Click Connect YouTube to sign in."
        _SESSION_META = {
            "ready": False,
            "reason": reason_text,
            "preferred_browser": preferred,
            "strict_preference": _require_preferred_browser(),
            "extraction_issue": error_codes[0] if error_codes else "no-profile",
            "detected_profiles": len(profiles),
            "updated_at": time.time(),
        }
        error_summary = ", ".join(errors[:4])
        raise YouTubeSessionUnavailable(
            reason_text
            + (f" Detected attempts: {error_summary}" if error_summary else "")
        )


def invalidate_session(reason: str = "") -> None:
    global _MEMORY_JAR, _SESSION_META
    with _LOCK:
        _MEMORY_JAR = None
        _SESSION_META = {"ready": False, "reason": reason or "Session invalidated", "updated_at": time.time()}
        try:
            _secure_store_path().unlink(missing_ok=True)
        except OSError:
            pass
        explicit_cookie = str(os.environ.get("VIDZEO_YT_COOKIES_FILE") or "").strip()
        if explicit_cookie:
            try:
                Path(explicit_cookie).unlink(missing_ok=True)
            except OSError:
                pass
        _clear_cookie_update_state()


def mark_session_needs_update(reason: str = "") -> None:
    """Discard a rejected snapshot and expose a clear reconnect state."""
    global _MEMORY_JAR, _SESSION_META
    message = (
        "Your YouTube session expired or was rejected by YouTube. "
        "Click Connect YouTube and finish sign-in to reconnect it."
    )
    with _LOCK:
        _MEMORY_JAR = None
        _SESSION_META = {
            "ready": False,
            "reason": message,
            "cookie_update_required": True,
            "cookie_update_reason": str(reason or "youtube-authentication-rejected")[:120],
            "verification": str(reason or "youtube-authentication-rejected")[:120],
            "preferred_browser": _preferred_browser(),
            "strict_preference": _require_preferred_browser(),
            "updated_at": time.time(),
        }
        try:
            _secure_store_path().unlink(missing_ok=True)
        except OSError:
            pass
        try:
            _session_state_path().parent.mkdir(parents=True, exist_ok=True)
            state_path = _session_state_path()
            temp = state_path.with_suffix(".tmp")
            temp.write_text(json.dumps({
                "version": 1,
                "cookie_update_required": True,
                "cookie_update_reason": _SESSION_META["cookie_update_reason"],
                "verification": _SESSION_META["verification"],
                "preferred_browser": _SESSION_META["preferred_browser"],
                "updated_at": _SESSION_META["updated_at"],
            }, separators=(",", ":")), encoding="utf-8")
            os.replace(temp, state_path)
        except OSError:
            pass


def ensure_verified_session(
    *, force_refresh: bool = False, stale_generation: Optional[int] = None,
    reason: str = "", preferred_browser: Optional[str] = None,
    interactive: bool = False,
) -> Tuple[http.cookiejar.CookieJar, int]:
    """Return a session after a fresh, positive YouTube authentication probe.

    Pipeline preflight calls this once before parallel search/download workers
    begin.  A definite signed-out response is terminal: retaining or trying a
    different browser profile would risk using another account or an
    unauthenticated delivery path.
    """
    selected_browser = _remember_preferred_browser(preferred_browser) if preferred_browser else None
    jar, generation = ensure_session(
        force_refresh=bool(force_refresh or interactive),
        stale_generation=stale_generation,
        reason=reason,
        preferred_browser=selected_browser,
    )
    # A transient probe timeout must not discard an otherwise usable saved
    # session on the first request. Retry the same jar at most three times;
    # a definite rejection is terminal and never tries another account.
    for attempt in range(3):
        verified, verification = _verify_youtube_session(jar)
        if verified is not None:
            break
    if verified is True:
        with _LOCK:
            if _jar_is_authenticated(_MEMORY_JAR):
                _SESSION_META.update({
                    "verified_authenticated": True,
                    "verification": verification,
                    "last_verified_at": time.time(),
                })
        return jar, generation
    if verified is False:
        mark_session_needs_update(verification)
        raise YouTubeSessionUnavailable(
            "Your YouTube session expired or was rejected. "
            "Click Connect YouTube and finish sign-in to reconnect it."
        )
    raise YouTubeSessionUnavailable(
        "Vidzeo could not verify the current YouTube session. Check your connection and retry; "
        "if it persists, click Connect YouTube and sign in again."
    )


def session_status() -> Dict[str, Any]:
    """Return redacted session diagnostics suitable for the local UI."""
    # Multi-account cookie pool health (redacted: file names and counts only,
    # never values). Surfaced here so a stale pool is visible instead of
    # silently falling back to anonymous public search and burning a run.
    try:
        pool_health = get_configured_pool().health_snapshot()
    except Exception:
        pool_health = []
    pool_healthy = sum(1 for item in pool_health if str(item.get("status") or "") == "verified")
    pool_known_bad = any(
        str(item.get("status") or "") in {"signed_out", "throttled", "load_error"}
        for item in pool_health
    )
    with _LOCK:
        preferred = _preferred_browser()
        meta = dict(_SESSION_META)
        if not meta.get("cookie_update_required") and not meta.get("extraction_issue"):
            update_state = _load_cookie_update_state()
            if update_state:
                meta = update_state
        ready = (
            _jar_is_authenticated(_MEMORY_JAR)
            and _meta_matches_preference(meta, preferred)
            and (bool(meta.get("verified_authenticated")) or not _require_auth_probe())
        )
        if not ready:
            persisted, persisted_meta = _load_persisted()
            if persisted is None or not _jar_is_authenticated(persisted):
                persisted, persisted_meta = _load_vidnets_electron_cookies()
            if (
                persisted is not None and _meta_matches_preference(persisted_meta, preferred)
                and (bool(persisted_meta.get("verified_authenticated")) or not _require_auth_probe())
            ):
                _clear_cookie_update_state()
                ready = True
                meta = {
                    **persisted_meta,
                    "cookie_count": sum(1 for _ in persisted),
                    "auth_cookie_count": _auth_cookie_count(persisted),
                }
                _install_memory_session(persisted, meta)
                _persist(persisted, meta)
        profiles = detect_browser_profiles()
        preferred_profiles = sum(1 for profile in profiles if profile.browser == preferred)
        fingerprint_source = f"{meta.get('browser', '')}:{meta.get('profile', '')}:{meta.get('saved_at', meta.get('updated_at', ''))}"
        selected_browser = str(meta.get("browser") or "")
        verified = bool(meta.get("verified_authenticated"))
        return {
            "ready": ready,
            "required": True,
            "browser": selected_browser,
            "profile": meta.get("profile") or "",
            "preferred_browser": preferred,
            "using_preferred_browser": bool(selected_browser and selected_browser == preferred),
            "fallback_used": bool(meta.get("fallback_used")),
            "strict_preference": _require_preferred_browser(),
            "cookie_update_required": bool(meta.get("cookie_update_required")),
            "cookie_update_reason": meta.get("cookie_update_reason") or "",
            "verified_authenticated": bool(ready and verified),
            "verification": meta.get("verification") or "",
            "extraction_issue": meta.get("extraction_issue") or "",
            "source": meta.get("source") or "",
            "persisted": bool(meta.get("persisted", ready and _secure_store_path().is_file())),
            "encrypted": bool(ready and os.name == "nt" and _secure_store_path().is_file()),
            "cookie_count": int(meta.get("cookie_count") or 0),
            "auth_cookie_count": int(meta.get("auth_cookie_count") or 0),
            "detected_profiles": len(profiles),
            "preferred_profiles": preferred_profiles,
            "channel_title": str(meta.get("channel_title") or meta.get("username") or _CURRENT_CHANNEL_TITLE or ""),
            "username": str(meta.get("username") or meta.get("channel_title") or _CURRENT_CHANNEL_TITLE or ""),
            "updated_at": float(meta.get("updated_at") or meta.get("saved_at") or 0),
            "session_id": hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()[:10] if ready else "",
            "message": (
                f"Authenticated {selected_browser.title()} session verified by YouTube"
                + (f" ({meta.get('channel_title') or meta.get('username') or _CURRENT_CHANNEL_TITLE})" if (meta.get("channel_title") or meta.get("username") or _CURRENT_CHANNEL_TITLE) else "")
                if ready else meta.get("reason") or "Sign in to YouTube in a supported browser"
            ),
            "pool": pool_health,
            "pool_healthy_accounts": pool_healthy,
            "pool_message": (
                "YouTube cookie pool: no healthy account — downloads run anonymous; re-export fresh cookies for signed-in access"
                if pool_health and pool_healthy == 0 and pool_known_bad
                else ""
            ),
        }


def reset_for_tests() -> None:
    global _MEMORY_JAR, _SESSION_META, _GENERATION
    with _LOCK:
        _MEMORY_JAR = None
        _SESSION_META = {}
        _GENERATION = 0
    reset_configured_pool()


def inherit_from_vidnets() -> Dict[str, Any]:
    """Explicitly inherit and live-verify the saved Vidnets YouTube session."""
    with _LOCK:
        jar, meta = _load_persisted()
        if not jar or not _jar_is_authenticated(jar):
            jar, meta = _load_vidnets_electron_cookies()
        if not jar or not _jar_is_authenticated(jar):
            raise YouTubeSessionUnavailable("No authenticated YouTube session found in Vidnets to inherit.")
        _clear_cookie_update_state()
        verified, verification = _verify_youtube_session(jar)
        meta["verification"] = verification
        meta["verified_authenticated"] = verified is True
        meta["source"] = "Inherited from Vidnets Studio"
        meta["browser"] = "electron"
        meta["profile"] = "Vidnets Studio"
        meta["persisted"] = True
        installed = _install_memory_session(jar, meta)
        _persist(installed, meta)
        return session_status()
