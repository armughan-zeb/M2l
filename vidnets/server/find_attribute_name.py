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

pos = html.find('origUrl')
# Find the start of the tag or attribute preceding pos
tag_start = html.rfind('<', 0, pos)
print("Tag starting at:", tag_start)
print("Tag snippet (500 chars):")
print(html[tag_start:tag_start+500])
