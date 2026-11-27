# Rich Vox-style textures for Remotion public/:
#   paper2.png  - warm paper with fold creases, faint stains, darker worn edges (1080x1920)
#   grain0..5.png - mid-grey film grain frames, blended with "overlay" and cycled every frame
# python textures.py <public_dir>
import sys, os
import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

out = sys.argv[1]
W, H = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (1080, 1920)
NAME = sys.argv[4] if len(sys.argv) > 4 else 'paper2.png'
GRAIN = len(sys.argv) <= 3          # grain frames only on the default run
r = np.random.default_rng(23)
yy, xx = np.mgrid[0:H, 0:W].astype(float)

# ---------- paper2 ----------
base = np.array([241, 233, 218], float)
L = r.standard_normal((H, W)) * 6.0                                   # grain
L += gaussian_filter(r.standard_normal((H, W)), 2.5) * 22             # tooth
L += gaussian_filter(r.standard_normal((H, W)), 70) * 330             # blotches
fib = np.zeros((H, W))
for _ in range(4000):
    x, y = r.integers(0, W), r.integers(0, H); n = r.integers(10, 50); a = r.uniform(0, np.pi)
    xs = (x + np.cos(a) * np.arange(n)).astype(int) % W; ys = (y + np.sin(a) * np.arange(n)).astype(int) % H
    fib[ys, xs] += r.uniform(-1, 1) * 11
L += gaussian_filter(fib, 0.7)
# fold creases: a soft shadow on one side, a bright ridge on the other
def crease(d, w=10):
    return -np.exp(-((d - 2) / w) ** 2) * 16 + np.exp(-((d + 3) / (w * 0.5)) ** 2) * 9
L += crease(yy - 0.47 * H + 0.02 * (xx - W / 2))
L += crease(xx - 0.62 * W + 0.015 * (yy - H / 2), 8) * 0.8
# faint stains (rings)
for cx, cy, rad in [(0.18 * W, 0.2 * H, 120), (0.82 * W, 0.78 * H, 160)]:
    d = np.hypot(xx - cx, yy - cy)
    L += -np.exp(-((d - rad) / 7) ** 2) * 10 - np.exp(-(d / rad) ** 2) * 5
# worn edges
d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
L += -np.clip(d - 0.6, 0, None) ** 2 * 32
img = base[None, None, :] + L[..., None] * np.array([1.0, 0.97, 0.9])
Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(os.path.join(out, NAME))

# ---------- grain frames (half res, scaled up in the browser = softer, filmic grain) ----------
for k in range(6 if GRAIN else 0):
    g = gaussian_filter(r.standard_normal((H // 2, W // 2)), 0.6) * 38 + 128
    Image.fromarray(np.clip(g, 0, 255).astype(np.uint8), 'L').save(os.path.join(out, f'grain{k}.png'))
print('textures saved to', out)
