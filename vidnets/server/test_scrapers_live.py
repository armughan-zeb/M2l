import os
import sys
import time
import concurrent.futures

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.web_image.hybrid_search import search_bing, search_ddg, _get_bing_session, BING_SEARCH_URL, SEARCH_HEADERS, BING_USER_AGENTS, _BING_PARSE_PATTERN
import random

def test_bing():
    print("\n==========================================")
    print("TESTING BING SCRAPER (hybrid_search.py)")
    print("==========================================")
    
    # Direct inspect HTTP response first
    session = _get_bing_session()
    headers = dict(SEARCH_HEADERS)
    headers["User-Agent"] = random.choice(BING_USER_AGENTS)
    headers["Referer"] = "https://www.bing.com/"
    
    resp = session.get(BING_SEARCH_URL, params={"q": "Cricket match action shot", "first": 1, "count": 35, "form": "HDRSC2", "adlt": "strict"}, headers=headers, timeout=10)
    print(f"Direct Bing HTTP Status: {resp.status_code}, HTML length: {len(resp.text)}")
    matches = list(_BING_PARSE_PATTERN.finditer(resp.text))
    print(f"Direct Regex '_BING_PARSE_PATTERN' matches count: {len(matches)}")
    if not matches:
        print("Sample HTML snippet (first 1000 chars):")
        print(resp.text[:1000].encode('ascii', errors='replace').decode('ascii'))

    queries = [
        "Cricket match action shot",
        "Eiffel tower night view high res",
        "Cyberpunk city skyline neon",
        "Ancient Roman Colosseum historical drawing",
        "Quantum computing microchip close up"
    ]
    
    for q in queries:
        t0 = time.time()
        results = search_bing(q, limit=40)
        dt = time.time() - t0
        print(f"Query: '{q}' -> Found {len(results)} images in {dt:.2f}s")
        if results:
            url = results[0].get('image_url') or results[0].get('url')
            title = (results[0].get('title') or '').encode('ascii', errors='replace').decode('ascii')
            print(f"   Sample 1: {url} (title: {title[:40]}...)")
        else:
            print("   [!] NO RESULTS RETURNED")

def test_yandex():
    print("\n==========================================")
    print("TESTING YANDEX SCRAPER (yandex_source.py)")
    print("==========================================")
    from services.sources.yandex_source import YandexSource
    yandex = YandexSource()
    queries = [
        "Cricket match action shot",
        "Eiffel tower night view high res",
        "Cyberpunk city skyline neon",
        "Ancient Roman Colosseum historical drawing",
        "Quantum computing microchip close up"
    ]
    
    for q in queries:
        t0 = time.time()
        results = yandex.search(q, limit=20)
        dt = time.time() - t0
        print(f"Query: '{q}' -> Found {len(results)} images in {dt:.2f}s (circuit_open: {yandex._circuit_open})")
        if results:
            first = results[0]
            url = getattr(first, 'url', None) or getattr(first, 'image_url', None) or str(first)
            title = getattr(first, 'title', '')
            if isinstance(title, str):
                title = title.encode('ascii', errors='replace').decode('ascii')
            print(f"   Sample 1: {url} (title: {title[:40]}...)")
        else:
            print("   [!] NO RESULTS RETURNED")

def test_concurrency():
    print("\n==========================================")
    print("TESTING CONCURRENT HIGH LOAD (10 Parallel Queries)")
    print("==========================================")
    from services.sources.yandex_source import YandexSource
    queries = [f"Scene subject query test {i}" for i in range(10)]
    
    def fetch_both(q):
        t0 = time.time()
        b_res = search_bing(q, limit=20)
        yandex = YandexSource()
        y_res = yandex.search(q, limit=10)
        dt = time.time() - t0
        return q, len(b_res), len(y_res), dt

    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch_both, q) for q in queries]
        for f in concurrent.futures.as_completed(futures):
            q, b_len, y_len, dt = f.result()
            print(f"Query '{q}': Bing={b_len}, Yandex={y_len} in {dt:.2f}s")
    print(f"Total concurrent load batch completed in {time.time() - t0:.2f}s")

if __name__ == '__main__':
    test_bing()
    test_yandex()
    test_concurrency()
