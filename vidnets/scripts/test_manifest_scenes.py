import sys
import json
sys.path.insert(0, 'server')
from services.semantic_edit_engine import build_semantic_edit_manifest

texts = [
    'In February 2003, an Italian crew orchestrated the largest diamond heist in world history beneath the streets',
    'of Antwerp.',
    'The thieves breached a subterranean vault protected by ten layers of security to seize a staggering $100',
    'million in loose diamonds.',
    'Vault security recorded zero alarms,',
    'bypassing 100 million lock combinations,',
    '63 security cameras, and infrared Doppler detectors.',
    'Between 2000 and 2003, mastermind Leonardo Notarbartolo rented an office in the Diamond Center for three years',
    'to study every guard rotation.',
    'The perpetrators fled from Antwerp across the Belgian border to Italy,',
    'discarding DNA evidence on a sandwich crust in a nearby forest.',
    'Police detectives later declared:',
    '"It was the most sophisticated bank burglary Europe had ever witnessed,',
    'leaving 80 percent of the diamonds unrecovered today."'
]
durations = [4.0] * len(texts)

manifest = build_semantic_edit_manifest(
    scene_texts=texts,
    scene_durations=durations,
    topic="Antwerp diamond heist",
    config={"graphic_density": 0.65}
)

scenes = manifest.get("scenes", [])
for i, s in enumerate(scenes):
    pv = s.get("primary_visual") or {}
    print(f"Scene {i+1}: {pv.get('type')} / {pv.get('variant')} | text: {repr(s.get('narration')[:50])}")
    if pv.get('type') == 'Chart':
        print(f"   -> Chart data: {pv.get('props', {}).get('data')}")
    elif pv.get('type') == 'AnimatedMap':
        print(f"   -> Map props: from={pv.get('props', {}).get('from')}, to={pv.get('props', {}).get('to')}, loc={pv.get('props', {}).get('location')}")
    elif pv.get('type') == 'QuoteCard':
        print(f"   -> Quote: {pv.get('props', {}).get('quote')} | speaker: {pv.get('props', {}).get('speaker')}")
