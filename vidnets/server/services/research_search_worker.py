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
        with DDGS(timeout=min(3.5, max(.1, float(request['timeout'])))) as provider:
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
