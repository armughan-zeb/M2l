import json
from pathlib import Path

manifest_path = Path('output/Colosseum Documentary (20260919_202843).manifest.json')
with open(manifest_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

print('=== MANIFEST AUDIT ===')
print(f'Title: {data.get("name")}')
print(f'Duration: {data.get("duration")}s')
print(f'Video clips: {len(data.get("video_clips", []))}')

editorial_cues = data.get('editorial_cues') or []
print(f'Editorial cues count: {len(editorial_cues)}')
for i, cue in enumerate(editorial_cues):
    print(f'  Cue {i}: type={cue.get("type")} text="{cue.get("text")}" start={cue.get("start_time")} dur={cue.get("duration")} sfx={cue.get("sfx")}')

sound_cues = data.get('sound_cues') or []
print(f'Total sound cues: {len(sound_cues)}')
for s in sound_cues[:10]:
    print(f'  SFX: type={s.get("type")} time={s.get("time")} path={s.get("sound_path")}')

component_manifest = data.get('component_manifest') or {}
print(f'Component manifest present: {bool(component_manifest)}')
print(f'Component video clips: {len(component_manifest.get("video_clips", []))}')
print(f'Component audio URL: {component_manifest.get("audio_url")}')
