import sys, glob, os
from PIL import Image, ImageDraw
src, dst = sys.argv[1], sys.argv[2]; cols = int(sys.argv[3]) if len(sys.argv) > 3 else 6
fs = sorted(glob.glob(os.path.join(src, '*.jpg')))
w, h = 270, 480; rows = (len(fs) + cols - 1) // cols
S = Image.new('RGB', (cols * w, rows * (h + 22)), 'white'); d = ImageDraw.Draw(S)
for i, f in enumerate(fs):
    im = Image.open(f).resize((w, h)); x, y = (i % cols) * w, (i // cols) * (h + 22)
    S.paste(im, (x, y + 22)); d.text((x + 4, y + 4), os.path.basename(f)[1:5], fill='black')
S.save(dst, quality=88)
