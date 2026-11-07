"""Inspect the real corrected export and preserve a frame sheet and summary."""
import json
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'niche_editing_revision'
PACKAGE = OUT / 'api-smoke' / 'generation-package-final'
video = PACKAGE / 'Adaptive Crime Pipeline Final Test.mp4'
manifest = json.loads((PACKAGE / 'Adaptive Crime Pipeline Final Test_remotion_edit_plan.json').read_text(encoding='utf-8'))
probe = subprocess.run([str(ROOT/'tools/ffmpeg/ffprobe.exe'), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(video)], capture_output=True, text=True, check=True)
info = json.loads(probe.stdout)
duration = float(info['format']['duration'])
frames = OUT / 'final-video-frames'
frames.mkdir(exist_ok=True)
times = [1, 3, 6, 9, 12, min(duration-.5, 16)]
sheet = Image.new('RGB', (960, 604), '#111318')
draw = ImageDraw.Draw(sheet)
font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 18)
for index, second in enumerate(times):
    target = frames / f'frame-{index}.png'
    subprocess.run([str(ROOT/'tools/ffmpeg/ffmpeg.exe'), '-v', 'error', '-y', '-ss', str(second), '-i', str(video), '-frames:v', '1', '-vf', 'scale=320:180', str(target)], check=True)
    x,y = index%3*320,index//3*302
    sheet.paste(Image.open(target).convert('RGB'), (x,y))
    draw.text((x+10,y+190),f'{second:.1f}s',font=font,fill='white')
sheet.save(OUT / 'final-video-contact-sheet.jpg', quality=94)
summary = {'duration_seconds':duration,'streams':[{'type':s['codec_type'],'codec':s['codec_name'],'width':s.get('width'),'height':s.get('height')} for s in info['streams']],
           'scene_count':len(manifest['scenes']), 'media_types':[s.get('media',{}).get('type') for s in manifest['scenes']],
           'visual_types':[s.get('primary_visual',{}).get('type') for s in manifest['scenes']],
           'planned_sound_cues':sum(len(s.get('sound_cues',[])) for s in manifest['scenes']),
           'palette':manifest['visual_dna']['palette'], 'video':str(video)}
(OUT / 'final-video-summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps(summary))
