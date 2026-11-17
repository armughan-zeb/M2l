"""Isolated native search: a stalled DDGS/Primp client cannot freeze the API."""
import contextlib
import json
import sys
from urllib.parse import urlparse


def main():
    request = json.loads(sys.stdin.read(16384))
    if request.get('kind') == 'http':
        url = str(request.get('url') or '')
        if urlparse(url).scheme != 'https' or urlparse(url).hostname not in {'yandex.com', 'www.bing.com'}:
            raise ValueError('Unknown native search endpoint')
        with contextlib.redirect_stdout(sys.stderr):
            try:
                import primp
                client = primp.Client()
            except ImportError:
                import requests
                client = requests.Session()
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
    except Exception:
        sys.stdout.write('[]')
