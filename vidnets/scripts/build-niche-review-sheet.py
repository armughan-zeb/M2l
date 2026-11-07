"""Create a labeled sheet from the requested rendered review frames."""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'niche_editing_revision'
manifest = json.loads((OUT / 'visual-fixtures.json').read_text(encoding='utf-8'))
frames = manifest['review_frames']
sheet = Image.new('RGB', (960, ((len(frames)+1)//2)*302), '#111318')
draw = ImageDraw.Draw(sheet)
font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 18)
for index, frame in enumerate(frames):
    scene = manifest['scenes'][index]
    x, y = (index % 2)*480, (index // 2)*302
    picture = Image.open(OUT / 'review-frames' / f'frame-{frame:06}.png').convert('RGB')
    picture.thumbnail((480, 270))
    sheet.paste(picture, (x, y))
    caption = f"{index+1}. {scene['visual_dna'].get('genre', '')} / {scene['primary_visual']['type']}"
    draw.text((x+10, y+275), caption, font=font, fill='#F7F5EF')
sheet.save(OUT / 'visual-review.jpg', quality=92)
print(OUT / 'visual-review.jpg')
