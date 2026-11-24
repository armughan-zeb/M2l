"""Isolated native search: a stalled DDGS client cannot freeze the API.

Nov 18 (web-resilience): primp is REMOVED as a transport. primp.Client()
is DNS-dead from this VM (instant DNSError on every call), so the old
'http' kind paid a subprocess spawn + a primp import + a failure on every
engine call before falling back to plain requests. Plain requests is now
the only transport here too. Failures are reported as
``{"error": "..."}`` on stdout instead of bare ``'[]'`` — the old bare
list hid every failure from the engines (a DNS error looked identical
to "no results"). Callers: DuckDuckGoEngine ('images' kind) and any
direct 'http' fetcher.
"""
import contextlib
import json
import os
import sys
from urllib.parse import urlparse


_PROXY_ENV_KEYS = (
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
)


def _strip_proxy_env():
    """Remove proxy vars so this process's HTTP clients go direct."""
    for key in _PROXY_ENV_KEYS:
        os.environ.pop(key, None)


def main():
    request = json.loads(sys.stdin.read(16384))
    if request.get('direct'):
        # Route is explicit: a thread-local flag cannot cross a process
        # boundary, so the parent passes 'direct' in the payload (and
        # strips the vars in the child env as well, belt-and-braces).
        _strip_proxy_env()
    if request.get('kind') == 'http':
        url = str(request.get('url') or '')
        if urlparse(url).scheme != 'https' or urlparse(url).hostname not in {'yandex.com', 'www.bing.com'}:
            raise ValueError('Unknown native search endpoint')
        with contextlib.redirect_stdout(sys.stderr):
            import requests
            client = requests.Session()
            if request.get('direct'):
                # Belt-and-braces (Worker F, Nov 18): the parent already
                # strips the proxy vars from the child env, and this
                # process stripped them again above — but the bypass must
                # not depend on env handling alone. A trust_env=False
                # session never consults the proxy env vars, so the
                # direct route holds even if a proxy var leaks back in.
                client.trust_env = False
            response = client.get(url, headers=request.get('headers') or {}, timeout=float(request['timeout']))
        json.dump({'status_code': response.status_code, 'text': response.text[:2000000]}, sys.stdout)
        return
    with contextlib.redirect_stdout(sys.stderr):
        from ddgs import DDGS
        # 12s, not 3.5s: a ddgs call through the throttled egress proxy
        # routinely needs more than 3.5s, and the engine gives this worker
        # a 15s budget (see DuckDuckGoEngine). A timeout here surfaces as
        # an empty result, which the engine retries once.
        with DDGS(timeout=min(12.0, max(.1, float(request['timeout'])))) as provider:
            limit = min(100, max(1, int(request.get('limit', 8))))
            search = provider.images if request.get('kind') == 'images' else provider.text
            rows = list(search(str(request['query'])[:300], max_results=limit,
                               safesearch='on' if request.get('safe_search', True) else 'off'))
    json.dump(rows[:limit], sys.stdout)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Report the failure instead of bare '[]': the caller must be able
        # to tell "the worker died" apart from "no results". The
        # DuckDuckGo engine logs error payloads and treats them as empty.
        try:
            sys.stdout.write(json.dumps({'error': f'{type(exc).__name__}: {exc}'[:300]}))
        except Exception:
            sys.stdout.write('[]')
