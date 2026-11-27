# Off-white textured paper (grain + soft fibres + blotches + vignette) for the collage background.
# python paper.py out.png [w h]
import sys
import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

out = sys.argv[1]
W, H = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (1080, 1920)
r = np.random.default_rng(11)
base = np.array([242, 236, 224], float)          # warm off-white

fine = r.standard_normal((H, W)) * 5.0           # grain
mid = gaussian_filter(r.standard_normal((H, W)), 3) * 18
blot = gaussian_filter(r.standard_normal((H, W)), 60) * 260
# fibres: short streaks
fib = np.zeros((H, W))
for _ in range(2500):
    x, y = r.integers(0, W), r.integers(0, H)
    L = r.integers(8, 40); a = r.uniform(0, np.pi)
    xs = (x + np.cos(a) * np.arange(L)).astype(int) % W
    ys = (y + np.sin(a) * np.arange(L)).astype(int) % H
    fib[ys, xs] += r.uniform(-1, 1) * 9
fib = gaussian_filter(fib, 0.7)

yy, xx = np.mgrid[0:H, 0:W]
d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
vig = -np.clip(d - 0.55, 0, None) ** 2 * 40

L = fine + mid + blot + fib + vig
img = base[None, None, :] + L[..., None] * np.array([1.0, 0.98, 0.93])
Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(out)
print('saved', out)
