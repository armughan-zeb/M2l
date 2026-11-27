"""Killable yt-dlp range attempt, with an extraction wall before transfer starts.

Nov 26 (user order: remove every timeout that stops a download): there is
no clip deadline any more (``deadline=None`` in production). The extraction
wall still bounds each attempt, but it is a *rotation* trigger now — when it
fires, the caller retries the candidate with the next player-client list
instead of failing it.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time

_RESULT_PREFIX = b"VIDZEO_YOUTUBE_RANGE_RESULT "
_SERVER = Path(__file__).resolve().parents[1]

# Proxy env vars the Hatch egress proxy injects into every shell. The
# range-attempt subprocess inherits the server env, so without this the
# child could reach the proxy through any library that reads them.
_PROXY_ENV_KEYS = frozenset({
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
})


def _child_env():
    """Child env for the range-attempt subprocess.

    Nov 26 (user order: restore the Test 21 recipe fully): the child
    inherits the egress proxy vars by default, as on Nov 15 (c5133b4)
    when 6 of 7 clips landed. VIDZEO_YT_DIRECT=1 restores the Nov 25
    direct-only behaviour (proxy vars stripped; yt-dlp also forced
    direct via options["proxy"] = "" in _execute) for when the proxy
    itself dies.
    """
    if str(os.environ.get("VIDZEO_YT_DIRECT", "") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return {
            key: value for key, value in os.environ.items()
            if key.lower() not in _PROXY_ENV_KEYS
        }
    return dict(os.environ)


class YouTubeExtractionTimeout(TimeoutError):
    """yt-dlp made no attempt to transfer media within its extraction wall."""


def _cookie_rows(jar):
    return [vars(cookie).copy() for cookie in jar] if jar is not None else None


def _cookie_jar(rows):
    if rows is None:
        return None
    jar = http.cookiejar.CookieJar()
    for row in rows:
        fields = dict(row)
        fields["rest"] = fields.pop("_rest", {})
        jar.set_cookie(http.cookiejar.Cookie(**fields))
    return jar


def _stop_attempt(process):
    if process.poll() is not None:
        return
    # The attempt inherits the branch's process group so cancelling the
    # branch also stops its attempts. A clip timeout kills just this subtree.
    try:
        import psutil
    except ImportError:
        process.kill()
        process.wait(timeout=5)
        return

    try:
        owner = psutil.Process(process.pid)
        try:
            owner.suspend()
        except psutil.Error:
            pass
        descendants = owner.children(recursive=True)
    except psutil.Error:
        descendants = []
    try:
        if process.poll() is None:
            process.kill()
        for child in reversed(descendants):
            try:
                child.kill()
            except psutil.Error:
                pass
    finally:
        process.wait(timeout=5)


def _monitor_attempt(command, request, transfer_marker, *, extraction_seconds, deadline=None,
                     cancellation=None, check_transfer=None, on_process=None):
    if cancellation is not None:
        cancellation.raise_if_cancelled()
    extraction_deadline = time.monotonic() + extraction_seconds
    transferring = False
    with subprocess.Popen(
        command, cwd=str(_SERVER), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env=_child_env(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ) as process:
        # Keep the deadline independent of pipe I/O. Windows communicate()
        # can block writing a large request while the child is still starting.
        finished = threading.Event()
        output = None
        io_error = None

        def exchange():
            nonlocal output, io_error
            try:
                output, _ = process.communicate(input=request)
            except Exception as exc:
                io_error = exc
            finally:
                finished.set()

        exchange_thread = threading.Thread(target=exchange, name="yt-range-io", daemon=True)
        exchange_thread.start()
        try:
            if on_process is not None:
                on_process(process.pid)
            while True:
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                if finished.is_set():
                    if check_transfer is not None:
                        check_transfer()
                    if io_error is not None:
                        raise io_error
                    return output
                now = time.monotonic()
                if transfer_marker.is_file():
                    transferring = True
                elif transferring:
                    transferring = False
                    extraction_deadline = now + extraction_seconds
                if not transferring and now >= extraction_deadline:
                    raise YouTubeExtractionTimeout(
                        f"youtube_extraction_timeout_after_{extraction_seconds:g}s"
                    )
                # Nov 26 (user order): no clip deadline — deadline is None
                # in production. The extraction wall above is a rotation
                # trigger (the caller tries the next client list), not a
                # failure.
                if deadline is not None and now >= deadline:
                    raise TimeoutError("youtube_clip_timeout_in_range_attempt")
                if transferring and check_transfer is not None:
                    check_transfer()
                if deadline is None:
                    remaining = None
                else:
                    remaining = deadline - now
                if not transferring:
                    remaining = extraction_deadline - now if remaining is None else min(remaining, extraction_deadline - now)
                finished.wait(0.2 if remaining is None else min(0.2, remaining))
        finally:
            try:
                if process.poll() is None:
                    _stop_attempt(process)
                exchange_thread.join(timeout=2)
            finally:
                if on_process is not None:
                    on_process(None)


def _jsonable(value):
    """Make a yt-dlp options payload JSON-safe for the subprocess boundary.

    In-process yt-dlp accepts set-valued options (e.g. compat_opts is a
    ``{"no-certifi"}`` set), but json.dumps does not: every range attempt
    died in ~0.1s with "Object of type set is not JSON serializable"
    before yt-dlp even started. Sets become sorted lists; yt-dlp only
    does membership tests on them, so a list behaves identically.
    """
    if isinstance(value, set):
        return sorted((_jsonable(item) for item in value), key=str)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def download_range_attempt(options, url, clients, jar, ranges, *, work_dir,
                           extraction_seconds, deadline=None, cancellation, check_transfer, on_process):
    from .youtube_pipeline_v2 import _youtube_cached_info, _youtube_evict_info, _youtube_store_info

    with tempfile.TemporaryDirectory(prefix=".yt-attempt-", dir=work_dir) as temp:
        marker = Path(temp) / "transfer-started"
        payload = {
            "options": {key: value for key, value in options.items()
                        if key not in {"logger", "progress_hooks", "download_ranges"}},
            "url": url,
            "clients": clients,
            "cookies": _cookie_rows(jar),
            "ranges": ranges,
            "transfer_marker": str(marker.resolve()),
        }
        payload["options"]["outtmpl"] = os.path.abspath(payload["options"]["outtmpl"])
        # yt-dlp takes a set for compat_opts. The request boundary is JSON.
        if "compat_opts" in payload["options"]:
            payload["options"]["compat_opts"] = sorted(payload["options"]["compat_opts"])
        cached = _youtube_cached_info(url, clients)
        if cached is not None:
            # Non-serializable live metadata is usable only within the process
            # that fetched it. Fresh extraction still uses yt-dlp native data.
            try:
                payload["cached_info"] = json.dumps(cached)
            except (TypeError, ValueError, RecursionError):
                pass
        try:
            stdout = _monitor_attempt(
                [sys.executable, "-m", "services.youtube_range_attempt"],
                json.dumps(_jsonable(payload)).encode("utf-8"),
                marker,
                extraction_seconds=extraction_seconds, deadline=deadline,
                cancellation=cancellation, check_transfer=check_transfer, on_process=on_process,
            )
            receipt_line = next(
                (line for line in reversed(stdout.splitlines()) if line.startswith(_RESULT_PREFIX)), None,
            )
            if receipt_line is None:
                raise RuntimeError("YouTube range attempt exited without its result")
            receipt = json.loads(receipt_line[len(_RESULT_PREFIX):])
        except Exception:
            if "cached_info" in payload:
                _youtube_evict_info(url, clients)
            raise
        options["logger"].categories.extend(
            category for category in receipt["warnings"] if category not in options["logger"].categories
        )
        if receipt.get("cached_info"):
            _youtube_store_info(url, clients, json.loads(receipt["cached_info"]))
        elif not receipt["ok"] and "cached_info" in payload:
            _youtube_evict_info(url, clients)
        if not receipt["ok"]:
            raise RuntimeError(receipt["error"])
        return _cookie_jar(receipt["cookies"])


def _execute(request, logger):
    from yt_dlp.utils import download_range_func

    from .youtube_pipeline_v2 import (
        _install_youtube_session, _youtube_download_with_cached_info, _youtube_store_info,
    )
    from .yt_dlp_runtime import load_yt_dlp

    url = request["url"]
    clients = request["clients"]
    if request.get("cached_info"):
        _youtube_store_info(url, clients, json.loads(request["cached_info"]))
    jar = _cookie_jar(request["cookies"])
    options = dict(
        request["options"], logger=logger,
        download_ranges=download_range_func(None, request["ranges"]),
    )
    # Nov 26 (user order: restore the Test 21 recipe fully): the
    # subprocess uses the egress proxy via the inherited env vars, as
    # on Nov 15. Only VIDZEO_YT_DIRECT=1 forces direct mode here.
    if str(os.environ.get("VIDZEO_YT_DIRECT", "") or "").strip().lower() in {"1", "true", "yes", "on"}:
        options["proxy"] = ""
    if "compat_opts" in options:
        options["compat_opts"] = set(options["compat_opts"])
    marker = Path(request["transfer_marker"])
    with load_yt_dlp().YoutubeDL(options) as ydl:
        _install_youtube_session(ydl, jar)
        extract_info, download = ydl.extract_info, ydl.dl

        def extract(*args, **kwargs):
            marker.unlink(missing_ok=True)
            return extract_info(*args, **kwargs)

        def transfer(*args, **kwargs):
            marker.touch()
            return download(*args, **kwargs)

        ydl.extract_info = extract
        ydl.dl = transfer
        _youtube_download_with_cached_info(ydl, url, clients)
        used_jar = ydl.__dict__.get("cookiejar", jar)
    return {"ok": True, "cookies": _cookie_rows(used_jar)}


def main():
    from .yt_dlp_runtime import YouTubeRuntimeLog

    logger = YouTubeRuntimeLog()
    request = {}
    try:
        request = json.load(sys.stdin.buffer)
        receipt = _execute(request, logger)
    except Exception as exc:
        # Caller retains the error category without exposing signed URLs.
        error = re.sub(r"https?://\S+", "[provider URL]", str(exc))
        receipt = {"ok": False, "error": error[:500]}
    # A format failure can still leave reusable extraction metadata for the
    # next selector. Let the caller reuse it without requesting the watch page again.
    if request.get("url"):
        from .youtube_pipeline_v2 import _youtube_cached_info

        info = _youtube_cached_info(request["url"], request["clients"])
        if info is not None:
            try:
                receipt["cached_info"] = json.dumps(info)
            except (TypeError, ValueError, RecursionError):
                pass
    receipt["warnings"] = logger.categories
    # stdout is an owned pipe. The parent consumes this receipt without
    # forwarding it to logs because it can contain rotated cookies.
    sys.stdout.flush()
    sys.stdout.buffer.write(b"\n" + _RESULT_PREFIX + json.dumps(receipt).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
