import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services.sources.yandex_source import YandexSource, _StateAttributeParser

y = YandexSource()
url = "https://yandex.com/images/search"
params = {"text": "Eiffel tower night view", "isize": "large", "nomisspell": "1"}
resp = y.session.get(url, params=params, timeout=10)
html = resp.text

parser = _StateAttributeParser()
parser.feed(html)

for idx, st in enumerate(parser.states):
    data = json.loads(st)
    print(f"Data keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
    if isinstance(data, dict):
        initial = data.get("initialState", {})
        print("initialState keys:", list(initial.keys()) if isinstance(initial, dict) else type(initial))

        # Check serpList or items or entities
        serp = initial.get("serpList", {}) if isinstance(initial, dict) else {}
        print("serpList keys:", list(serp.keys()) if isinstance(serp, dict) else type(serp))
        items = serp.get("items", {}) if isinstance(serp, dict) else {}
        print("items keys:", list(items.keys()) if isinstance(items, dict) else type(items))
        entities = items.get("entities", {}) if isinstance(items, dict) else {}
        print("entities keys:", list(entities.keys()) if isinstance(entities, dict) else type(entities))
        if isinstance(entities, dict):
            print("entities count:", len(entities))
            first_key = list(entities.keys())[0] if entities else None
            if first_key:
                print(f"Sample entity '{first_key}':", list(entities[first_key].keys()) if isinstance(entities[first_key], dict) else entities[first_key])

