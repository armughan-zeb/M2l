import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services.sources.yandex_source import YandexSource, _StateAttributeParser

y = YandexSource()
url = "https://yandex.com/images/search"
params = {"text": "Eiffel tower night view", "isize": "large", "nomisspell": "1"}
resp = y.session.get(url, params=params, timeout=10)
html = resp.text

parser = _StateAttributeParser()
parser.feed(html)
print(f"Parsed {len(parser.states)} state payloads")

candidates = y._parse_response(html, "Eiffel tower night view", 20)
print(f"Candidates parsed: {len(candidates)}")
for c in candidates[:5]:
    title = c.title.encode('ascii', errors='replace').decode('ascii')
    print(f"   -> Candidate: {c.download_url} | Title: {title[:50]}")
