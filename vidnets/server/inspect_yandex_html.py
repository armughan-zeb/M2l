import os
import sys
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services.sources.yandex_source import YandexSource

y = YandexSource()
url = "https://yandex.com/images/search"
params = {"text": "Eiffel tower night view", "isize": "large", "nomisspell": "1"}
resp = y.session.get(url, params=params, timeout=10)
html = resp.text

print("Full HTML total length:", len(html))

# Let's search for 'origUrl' or 'img_href' or 'url' or 'thumb'
for target in ['origUrl', 'img_href', 'thumb', 'serp-item', 'data-state', 'data-bem', 'serpList']:
    matches = [m.start() for m in re.finditer(re.escape(target), html)]
    print(f"Target '{target}': {len(matches)} matches")
    if matches:
        pos = matches[0]
        snippet = html[max(0, pos-100):min(len(html), pos+300)]
        print(f"   Snippet for '{target}':", repr(snippet[:200]))
