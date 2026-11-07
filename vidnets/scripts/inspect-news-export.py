"""Decode and inspect the actual news export, not just the planned manifest."""
import json
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/news_validation_20261005'
VIDEO = OUT / 'crew13-news-preview.mp4'
FFMPEG = ROOT / 'tools/ffmpeg/ffmpeg.exe'
FFPROBE = ROOT / 'tools/ffmpeg/ffprobe.exe'

def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, default=str), encoding='utf-8')

def main():
    result = json.loads((OUT / 'retry-response.json').read_text(encoding='utf-8'))['response']
    manifest = json.loads(Path(result['manifest_path']).read_text(encoding='utf-8'))
    probe = subprocess.run([str(FFPROBE), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(VIDEO)], capture_output=True, text=True, timeout=30)
    metadata = json.loads(probe.stdout)
    write('video-probe.json', metadata)
    decode = subprocess.run([str(FFMPEG), '-v', 'error', '-i', str(VIDEO), '-f', 'null', '-'], capture_output=True, text=True, timeout=150)
    write('decode-check.json', {'exit_code': decode.returncode, 'stderr': decode.stderr})
    duration = float(metadata['format']['duration'])
    video = next(stream for stream in metadata['streams'] if stream['codec_type'] == 'video')
    effect_rows = manifest.get('audio', {}).get('effects') or []
    effects = []
    for effect in effect_rows:
        path = effect.get('src') or effect.get('path') or effect.get('asset') or ''
        if isinstance(path, dict):
            path = path.get('src') or path.get('path') or ''
        effects.append({**effect, 'asset_exists': Path(str(path)).is_file() if path else False})
    write('sound-cue-audit.json', {'cue_count': len(effects), 'cues': effects,
         'limitation': 'Asset and timing audit; this does not certify a full listening review.'})
    sample_times = [i * 2.5 + .8 for i in range(int(duration / 2.5)) if i * 2.5 + .8 < duration]
    # The group profile must be checked at every spoken identity, not just its midpoint.
    for scene in manifest.get('scenes', []):
        if (scene.get('primary_visual') or {}).get('type') == 'PersonProfile':
            sample_times.extend(float(scene.get('start') or 0) + offset for offset in (1.8, 3.6, 5.2, 7.2) if offset < float(scene.get('duration') or 0))
            for person in scene.get('primary_visual', {}).get('props', {}).get('people') or []:
                first_word = str(person.get('label') or '').split()[0]
                cue = next((word for word in scene.get('caption_words') or []
                            if str(word.get('text') or '').strip('.,:;') == first_word), None)
                if cue:
                    sample_times.append(min(float(scene['start']) + float(scene['duration']) - .1,
                                            float(cue['start']) + .18))
    sample_times = sorted(set(round(value, 2) for value in sample_times))
    frames = OUT / 'frames'
    frames.mkdir(exist_ok=True)
    canvas = Image.new('RGB', (1200, 254 * ((len(sample_times)+2)//3)), '#151515')
    draw = ImageDraw.Draw(canvas)
    for index, seconds in enumerate(sample_times):
        frame = frames / f'frame-{seconds:06.2f}.jpg'
        subprocess.run([str(FFMPEG), '-v', 'error', '-y', '-ss', str(seconds), '-i', str(VIDEO), '-frames:v', '1', str(frame)], capture_output=True, timeout=20, check=True)
        image = Image.open(frame).convert('RGB')
        image.thumbnail((400, 225))
        left, top = index % 3 * 400, index // 3 * 254
        canvas.paste(image, (left, top))
        draw.text((left+8, top+231), f'{seconds:.2f}s', fill='white')
    canvas.save(OUT / 'final-contact-sheet.jpg')
    write('export-summary.json', {'decode_ok': decode.returncode == 0, 'duration_seconds': duration,
          'width': video['width'], 'height': video['height'], 'scene_count': len(manifest.get('scenes') or []),
          'sound_cues': len(effects), 'post_media_direction': manifest.get('post_media_direction'),
          'creative_review': manifest.get('creative_review'), 'preview_review': manifest.get('preview_review')})
    print(json.dumps({'decode_ok': decode.returncode == 0, 'duration_seconds': duration, 'frames': len(sample_times)}), flush=True)

if __name__ == '__main__':
    main()
