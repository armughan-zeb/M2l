# python3 mix.py voice48.wav music.wav sfx.wav -> mix.wav
# Voice first: music bed = MUSIC x the voice's gated RMS (0.16 = -16 dB),
# with a 1-4 kHz dip so it doesn't mask speech, and ducked a further DUCK dB while the voice is talking (back up in the gaps).
# SFX = SFX x gated RMS (0.28 = -11 dB), peaks capped at 0.5x the voice peak. Voice untouched.
# Then ONE linear gain to about -14 LUFS plus a -1 dBFS peak limiter on the whole mix.
import sys
import numpy as np
from dsp import SR, read_wav, write_wav, integrated, bp, lp

def gated_rms(x, block=0.4, hop=0.1):
    m = x.mean(0) if x.ndim == 2 else x
    p = (x ** 2).mean(0) if x.ndim == 2 else x ** 2
    n, h = int(block * SR), int(hop * SR)
    pw = np.array([p[i:i + n].mean() for i in range(0, len(p) - n, h)])
    pw = pw[pw > 10 ** (-60 / 10)]                     # absolute gate -60 dBFS
    if not len(pw): return 0.0
    rel = pw.mean() * 10 ** (-12 / 10)                 # relative gate -12 dB
    return float(np.sqrt(pw[pw > rel].mean()))

def limit(x, ceil, att=0.001, rel=0.08):
    """peak limiter: smooth gain so |x| <= ceil (2 ms lookahead)"""
    a = np.abs(x).max(0)
    need = np.minimum(1.0, ceil / np.maximum(a, 1e-9))
    la = int(0.002 * SR)
    from scipy.ndimage import minimum_filter1d
    need = minimum_filter1d(need, 2 * la + 1)
    g = np.empty_like(need); cur = 1.0
    ka, kr = np.exp(-1 / (att * SR)), np.exp(-1 / (rel * SR))
    for i in range(len(need)):
        t = need[i]; cur = t if t < cur else kr * cur + (1 - kr) * t
        g[i] = cur
    return x * g, float(g.min())

def speech_gain(v, duck_db, att=0.04, rel=0.45):
    """1.0 in pauses, duck_db while the voice is talking; smooth attack/release so the music breathes, never pumps"""
    e = np.sqrt(np.maximum(lp((v ** 2).mean(0), 25), 0))                    # ~40 ms voice envelope
    talk = (e > 10 ** (-38 / 20) * np.abs(v).max()).astype(float)           # talking = within 38 dB of the voice peak
    g = np.empty_like(talk); cur = 0.0
    ka, kr = np.exp(-1 / (att * SR)), np.exp(-1 / (rel * SR))
    for i in range(len(talk)):
        k = ka if talk[i] > cur else kr; cur = k * cur + (1 - k) * talk[i]; g[i] = cur
    return 10 ** (duck_db * g / 20), g

MUSIC, DUCK, SFX = 0.16, -5.0, 0.28
db = lambda r: 20 * np.log10(max(r, 1e-12))
v, _ = read_wav(sys.argv[1]); m, _ = read_wav(sys.argv[2]); s, _ = read_wav(sys.argv[3])
n = v.shape[1]; m = m[:, :n]; s = s[:, :n]
if v.shape[0] == 1: v = np.concatenate([v, v])
rv, pv = gated_rms(v), np.abs(v).max()
m = m - 0.45 * bp(m, 1000, 4000)                                            # ~-5 dB presence dip: leave room for the voice
m = m * (MUSIC * rv / gated_rms(m))
dg, talk = speech_gain(v, DUCK)
m = m * dg
s = s * (SFX * rv / gated_rms(s))
s, gmin = limit(s, 0.5 * pv)
rm, rs = gated_rms(m), gated_rms(s)
on, off = talk > 0.9, talk < 0.1
rms = lambda x, k: float(np.sqrt((x[:, k] ** 2).mean())) if k.any() else 0.0
print(f'voice gated RMS {db(rv):.2f} dBFS, peak {db(pv):.2f} dBFS')
print(f'music vs voice: {db(rm / rv):+.2f} dB overall; while talking {db(rms(m, on) / rv):+.2f} dB, in pauses {db(rms(m, off) / rv):+.2f} dB')
print(f'sfx   vs voice: {db(rs / rv):+.2f} dB, sfx peak {db(np.abs(s).max() / pv):+.2f} dB vs voice peak, limiter min gain {db(gmin):.1f} dB')
mix = v + m + s
L = integrated(mix)
g = 10 ** ((-14 - L) / 20)
mix, gm = limit(mix * g, 10 ** (-1 / 20))
print(f'mix: {L:.2f} LUFS -> gain {db(g):+.2f} dB -> {integrated(mix):.2f} LUFS, peak {db(np.abs(mix).max()):.2f} dBFS')
write_wav('mix.wav', mix)
