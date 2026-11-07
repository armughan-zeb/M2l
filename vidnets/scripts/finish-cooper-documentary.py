"""Preserve the generated master and prepare its exact 60-second review cut."""
from collections import Counter
import json
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'cooper_one_minute_20261005'
FFMPEG = ROOT / 'tools/ffmpeg/ffmpeg.exe'
FFPROBE = ROOT / 'tools/ffmpeg/ffprobe.exe'


def probe(path):
    result = subprocess.run([str(FFPROBE), '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)],
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def main():
    response_path = OUT / 'render-result.json' if (OUT / 'render-result.json').exists() else OUT / 'response.json'
    response = json.loads(response_path.read_text(encoding='utf-8'))
    if not response.get('response', {}).get('success'):
        raise RuntimeError('Generation has not completed successfully')
    result = response['response']
    source = Path(result['video_path'])
    original = probe(source)
    duration = float(original['format']['duration'])
    target = OUT / 'D.B. Cooper - 60 second documentary.mp4'
    tempo = duration / 60
    if not .8 <= tempo <= 1.3:
        raise RuntimeError('Narration pace adjustment is too large for a clean one-minute cut')
    cmd = [str(FFMPEG), '-y', '-filter_threads', '1', '-i', str(source), '-vf', f'setpts=(PTS-STARTPTS)/{tempo:.10f},fps=24',
           '-af', f'atempo={tempo:.10f},apad', '-t', '60', '-c:v', 'libx264', '-preset', 'fast',
           '-crf', '18', '-threads', '2', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(target)]
    with (OUT / 'pace-fit.log').open('w', encoding='utf-8') as log:
        subprocess.run(cmd, stdout=log, stderr=log, check=True)
    final = probe(target)
    video = next(row for row in final['streams'] if row['codec_type'] == 'video')
    audio = next(row for row in final['streams'] if row['codec_type'] == 'audio')
    assert abs(float(final['format']['duration']) - 60) < .15
    frames = OUT / 'final-frames'
    frames.mkdir(exist_ok=True)
    samples = [1.5, 6, 12, 18, 25, 32, 40, 48, 56]
    images = []
    for index, seconds in enumerate(samples):
        frame = frames / f'frame-{index:02}.jpg'
        subprocess.run([str(FFMPEG), '-y', '-ss', str(seconds), '-i', str(target), '-frames:v', '1', '-q:v', '2', str(frame)],
                       capture_output=True, check=True)
        images.append((seconds, frame))
    sheet = Image.new('RGB', (960, 630), '#111111')
    draw = ImageDraw.Draw(sheet)
    for index, (seconds, frame) in enumerate(images):
        image = Image.open(frame).convert('RGB').resize((320, 180))
        x, y = (index % 3) * 320, (index // 3) * 210
        sheet.paste(image, (x, y + 25))
        draw.text((x + 8, y + 6), f'{seconds:.1f}s', fill='white')
    sheet.save(OUT / 'final-contact-sheet.jpg', quality=92)
    manifests = [OUT / 'repaired-manifest.json'] if (OUT / 'repaired-manifest.json').exists() else list((OUT / 'generation-package').glob('*.manifest.json'))
    manifest = json.loads(manifests[0].read_text(encoding='utf-8')) if manifests else {}
    scenes = manifest.get('scenes') or []
    summary = {'video': str(target), 'original_master': str(source), 'duration_seconds': float(final['format']['duration']),
               'resolution': [video['width'], video['height']], 'video_codec': video['codec_name'],
               'audio_codec': audio['codec_name'], 'source_duration_seconds': duration,
               'pitch_preserving_tempo_factor': tempo, 'generation_elapsed_seconds': response['elapsed_seconds'],
               'scene_count': len(scenes), 'components': dict(Counter((row.get('primary_visual') or {}).get('type') for row in scenes)),
               'creative_review': manifest.get('creative_review'), 'release_review': manifest.get('release_review'),
               'production_audit': manifest.get('production_audit'),
               'note': 'All overlays/captions/graphics were rendered by Remotion. The exact-duration copy fits the already edited master to 60 seconds, preserving voice pitch and scaling all visual/audio cues together. Original manifest timings belong to the preserved master.'}
    (OUT / 'video-summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps({key: summary[key] for key in ('video', 'duration_seconds', 'resolution', 'scene_count', 'components')}))


if __name__ == '__main__':
    main()
