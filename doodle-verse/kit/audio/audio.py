# Music + SFX stems for "Why Is It So Hard to Let Go?". Writes music.wav and sfx.wav (48 kHz stereo).
import numpy as np
from dsp import SR, TAU, ns, tt, rng, lp, hp, bp, pan, place, fades, write_wav, read_wav, make_ir, convolve_st, smooth_noise
import instruments as I

V, _ = read_wav('voice48.wav')
N = V.shape[1]
DUR = N / SR
F = lambda f: f / 30.0  # frame -> seconds
R = rng(11)
m2f = I.m2f

# =============================== MUSIC ===============================
# gentle and reflective: warm pads, a vibraphone arpeggio and a music-box tune in A minor;
# it drops out when the connection disappears, glows in C for the happy memory, and turns to C major for the healing.
BPM = 72.0; B = 60.0 / BPM; BAR = 4 * B
CH = {'Am': ([57, 60, 64], 45), 'F': ([53, 57, 60], 41), 'C': ([55, 60, 64], 48), 'G': ([55, 59, 62], 43),
      'Em': ([52, 55, 59], 40), 'Dm': ([50, 53, 57], 38), 'Fmaj7': ([53, 57, 60, 64], 41)}
MEL_SAD = [(0, 76, 1), (1, 74, 0.5), (1.5, 72, 1.5), (4, 72, 1), (5, 71, 0.5), (5.5, 69, 1.5), (8, 67, 1), (9, 69, 0.5), (9.5, 72, 1.5), (12, 71, 2), (14, 74, 2)]
MEL_HOPE = [(0, 72, 1), (1, 76, 1), (2, 79, 2), (4, 77, 1), (5, 76, 1), (6, 72, 2), (8, 74, 1), (9, 76, 1), (10, 79, 1.5), (12, 79, 1), (13, 81, 1), (14, 84, 2)]

def section(buf, t0, t1, prog, vel=0.3, arp=True, mel=None, mel_vel=0.3, pad_vel=0.16, bass=True, high=0, grow=0.0):
    t = t0; bar = 0
    while t < t1 - 0.2:
        notes, root = CH[prog[bar % len(prog)]]
        k = 1 + grow * (t - t0) / max(1e-3, t1 - t0)
        d = min(BAR, t1 - t)
        place(buf, I.pad([m2f(n) for n in notes], d, pad_vel * k, R, att=0.9, rel=1.2, warm=700), t)
        if bass: place(buf, pan(I.pluck(m2f(root), 0.32 * k, R, dur=2.4, bright=0.25), -0.15), t)
        if arp:
            tones = notes[:3] + [notes[0] + 12]
            for i, ix in enumerate([0, 1, 2, 3, 2, 1, 2, 1]):
                tn = t + i * B / 2
                if tn < t1: place(buf, pan(I.vibes(m2f(tones[ix] + 12 + high), vel * k * (0.85 if i % 2 else 1.0), R, dur=1.8), 0.25 * ((i % 3) - 1)), tn)
        if mel:
            for ob, mid, nb in mel:
                off = ob - (bar % 4) * 4
                if 0 <= off < 4 and t + off * B < t1:
                    place(buf, pan(I.musicbox(m2f(mid), mel_vel * k, R, dur=2.2), 0.2), t + off * B)
        t += BAR; bar += 1

mus = np.zeros((2, N))
# A-D (sunset, reflection)
a = np.zeros((2, N))
section(a, 0.0, F(632), ['Am', 'F', 'C', 'G'], 0.24, mel=MEL_SAD, mel_vel=0.3)
# E-F (the mind): airier, higher arpeggio, the tune rests
section(a, F(632), F(982), ['F', 'G', 'Em', 'Am'], 0.27, mel=None, high=12, pad_vel=0.14)
a[:, ns(F(982)):] *= np.exp(-np.arange(N - ns(F(982))) / SR / 0.12)   # the connection disappears: music drops away
mus += a
# G-H1 (rain, alone): a low drone and a few falling music-box notes
place(mus, I.pad([m2f(33), m2f(40), m2f(45)], F(1262) - F(990), 0.22, R, att=1.2, rel=0.8, warm=380), F(990))
for i, (fr, m) in enumerate([(1050, 76), (1080, 74), (1118, 72), (1142, 71), (1190, 69), (1232, 72), (1247, 71)]):
    place(mus, pan(I.musicbox(m2f(m), 0.28, R, dur=2.6), 0.15), F(fr))
# H2 (the happy memory): bright and quick, in C
hm = np.zeros((2, N))
section(hm, F(1262), F(1352), ['C', 'F'], 0.3, mel=[(0, 79, 0.5), (0.5, 81, 0.5), (1, 84, 1), (2, 79, 0.5), (2.5, 76, 0.5), (3, 79, 1), (4, 77, 0.5), (4.5, 76, 0.5), (5, 74, 1)], mel_vel=0.32, high=12, pad_vel=0.15)
hm[:, ns(F(1352)):] *= np.exp(-np.arange(N - ns(F(1352))) / SR / 0.25)
mus += hm
# I (holding on): back to A minor, sparse
section(mus, F(1352), F(1515), ['Am', 'F', 'Am'], 0.2, mel=MEL_SAD[:6], mel_vel=0.26, pad_vel=0.14)
# J-L (healing, letting go, moving forward): C major, slowly building, final C chord on "forward"
section(mus, F(1515), F(1935), ['F', 'C', 'G', 'Am', 'F', 'G', 'C', 'C'], 0.26, mel=MEL_HOPE, mel_vel=0.32, pad_vel=0.15, grow=0.6)
fin = F(1939)
place(mus, I.pad([m2f(48), m2f(55), m2f(60), m2f(64), m2f(67)], DUR - fin + 0.2, 0.26, R, att=0.25, rel=0.6, warm=900), fin)
for i, m in enumerate([72, 76, 79, 84]): place(mus, pan(I.musicbox(m2f(m), 0.34, R, dur=2.5), -0.3 + 0.2 * i), fin + i * 0.09)
place(mus, pan(I.pluck(m2f(36), 0.4, R, dur=3.0, bright=0.2), 0), fin)
mus[:, ns(DUR - 0.3):] *= np.linspace(1, 0, N - ns(DUR - 0.3))
ir = make_ir(1.8, 5)
mus = convolve_st(mus, ir)[:, :N] * 0.45 + mus * 0.8

# =============================== SFX ===============================
sfx = np.zeros((2, N))
def env_exp(n, k): return np.exp(-np.arange(n) / SR * k)
def noise(d): return R.standard_normal(ns(d))
def whoosh(d=0.45, lo=300, hi=2500, amp=0.6):
    x = noise(d); n = len(x); out = np.zeros(n); sg = 256
    for i in range(0, n, sg):
        p = i / n; fc = lo + (hi - lo) * np.sin(np.pi * p)
        out[i:i + sg] = bp(x[max(0, i - 512):i + sg], max(60, fc * 0.6), fc * 1.4)[-len(out[i:i + sg]):]
    return amp * out * np.sin(np.pi * np.linspace(0, 1, n)) ** 1.5
def pop(f0=700, f1=180, d=0.12, amp=0.7):
    t = tt(d); f = f1 + (f0 - f1) * np.exp(-t * 40)
    return amp * np.sin(TAU * np.cumsum(f) / SR) * env_exp(len(t), 30)
def ting(f=2093, amp=0.5): return amp * I.glock(f, 0.8, R, dur=1.4)
def thud(amp=0.8, f=80, d=0.35):
    t = tt(d); return amp * (np.sin(TAU * f * t) * env_exp(len(t), 14) + 0.6 * lp(noise(d), 400) * env_exp(len(t), 25))
def flap(amp=0.35, d=0.18):
    x = bp(noise(d), 600, 4000); return amp * x * np.abs(np.sin(np.linspace(0, 6 * np.pi, len(x)))) * np.sin(np.pi * np.linspace(0, 1, len(x)))
def wind(d, amp=0.25):
    x = lp(noise(d), 600) * (0.5 + 0.5 * smooth_noise(ns(d), R, 0.8)); return amp * fades(x, 0.8, 0.8)
def shimmer(amp=0.4, up=True, notes=(84, 88, 91, 96, 100)):
    out = np.zeros(ns(1.4)); seq = notes if up else notes[::-1]
    for i, m in enumerate(seq): b = I.glock(m2f(m), 0.5, R, dur=0.9); s = ns(i * 0.06); out[s:s + len(b)] += b[:len(out) - s]
    return amp * out
def step(amp=0.25, f=180, wood=False):
    t = tt(0.12); x = lp(noise(0.12), 1800 if wood else 900) * env_exp(len(t), 45)
    return amp * (x + (0.7 if wood else 0.3) * np.sin(TAU * f * t) * env_exp(len(t), 40))
def hum(d, f0=300, f1=600, amp=0.25):
    t = tt(d); f = f0 + (f1 - f0) * (t / d); y = sum(np.sin(TAU * np.cumsum(f * k) / SR) / k for k in (1, 2, 3))
    return amp * fades(y, 0.05, 0.03)
def twang(amp=0.6):
    t = tt(0.9); f = 520 * (1 - 0.35 * (1 - np.exp(-t * 8)))
    return amp * (np.sin(TAU * np.cumsum(f) / SR) * env_exp(len(t), 5) + 0.5 * hp(noise(0.9), 3000) * env_exp(len(t), 60))
def crack(amp=0.7):
    x = hp(noise(0.25), 1500) * (R.random(ns(0.25)) > 0.55) * env_exp(ns(0.25), 18)
    return amp * x + thud(0.6 * amp, 60, 0.25)[:ns(0.25)]
def rumble(amp=0.4, d=2.4):
    t = tt(d); return amp * lp(noise(d), 160) * (0.4 + 0.6 * np.sin(np.pi * t / d)) * (0.6 + 0.4 * smooth_noise(ns(d), R, 3))
def rain(d, amp=0.2):
    x = bp(noise(d), 1200, 7500) * (0.7 + 0.3 * smooth_noise(ns(d), R, 6))
    drops = (R.random(ns(d)) > 0.9993) * R.uniform(0.5, 1.5, ns(d)); drops = np.convolve(drops, np.exp(-np.arange(200) / 30), 'same')
    return amp * fades(x + 2.5 * hp(drops, 2000), 1.2, 1.2)
def lapping(d, amp=0.18):
    x = lp(noise(d), 500) * (0.35 + 0.65 * (0.5 + 0.5 * np.sin(TAU * 0.35 * tt(d)))) ** 2
    return amp * fades(x, 1.5, 1.0)
def P(x, fr, p=0.0, g=1.0): place(sfx, pan(x, p), F(fr), g)

# A-D: soft breeze at sunset
P(wind(F(632) + 0.5, 0.16), 0)
P(flap(0.12, 0.3), 40, 0.4); P(flap(0.1, 0.3), 150, -0.3)
# B: the memory appears / fades
P(shimmer(0.4), 166, 0.3); P(pop(900, 400, 0.1, 0.25), 168, 0.3)
P(shimmer(0.35, up=False), 238, 0.3); P(whoosh(0.5, 1500, 5000, 0.18), 240, 0.3)
# C: her footsteps, fading as she walks away
for i in range(12):
    fr = 308 + i * 11.1
    P(step(0.3 * (1 - i / 13), 170 + 10 * (i % 2)), fr, -0.3 - 0.04 * i)
# D: polaroids, the thread, the dream bubble
P(whoosh(0.5, 400, 3000, 0.35), 480)
for i in range(6): P(flap(0.28, 0.22), 484 + i * 3, (-1) ** i * 0.5)
P(hum(F(540 - 514), 330, 520, 0.18), 514, -0.3)
P(twang(0.6), 540, -0.3); P(ting(2637, 0.3), 541, -0.3)
P(pop(1200, 500, 0.08, 0.35), 561, 0.4); P(shimmer(0.25), 563, 0.4)
P(pop(700, 200, 0.12, 0.45), 610, 0.4)
# E: crane up into the mind, the heart, the threads
P(whoosh(2.0, 200, 1800, 0.35), 646)
P(ting(1760, 0.45), 673); P(shimmer(0.3), 675)
for i in range(4): P(hum(0.7, 500 + 80 * i, 900 + 80 * i, 0.08), 736 + i * 5, -0.45 + 0.3 * i)
# F: portrait + comfort / happiness / security
P(pop(900, 300, 0.1, 0.4), 829); P(ting(2093, 0.35), 830)
for fr, fq, p in [(853, 2349, -0.4), (884, 2637, 0.0), (916, 3136, 0.4)]: P(pop(900, 300, 0.1, 0.35), fr, p); P(ting(fq, 0.4), fr + 1, p)
# G: the connection disappears; rain and thunder at the bench
P(crack(0.8), 982); P(thud(0.7, 50, 0.6), 982)
for i in range(4): P(pop(500, 120, 0.15, 0.3), 983 + i * 2, -0.5 + 0.33 * i)
P(whoosh(1.3, 1800, 300, 0.3), 1010)
P(rain(F(1262) - F(1036) + 0.3, 0.22), 1036)
P(rumble(0.45, 2.6), 1050)
# H: memory flash, laughter-bright chime; flash back
P(shimmer(0.45), 1260); P(whoosh(0.5, 2000, 6000, 0.2), 1260)
P(shimmer(0.35, up=False), 1350)
# I-L: water lapping at the pier
P(lapping(DUR - F(1352) + 0.3, 0.2), 1352)
for i in range(6): P(flap(0.16, 0.2), 1390 + i * 4, (-1) ** i * 0.4)
# J: sunrise swell, photos float away
P(shimmer(0.35, notes=(72, 76, 79, 84, 88)), 1524)
for i in range(6): P(flap(0.12, 0.25), 1602 + i * 12, (-1) ** i * 0.4)
# K: the balloon rises; hand on heart
P(pop(400, 900, 0.06, 0.2), 1746, -0.2); P(whoosh(1.8, 300, 2400, 0.28), 1748, -0.1)
P(ting(2637, 0.3), 1807, 0.1); P(shimmer(0.25, notes=(84, 88, 91)), 1808, 0.1)
# L: footsteps on the pier, a bright button on "forward"
for i in range(9):
    fr = 1858 + i * 13.2
    if fr < 1960: P(step(0.3, 220 + 12 * (i % 2), wood=True), fr, 0.1 * ((-1) ** i))
P(shimmer(0.4, notes=(84, 88, 91, 96)), 1939)

write_wav('music.wav', mus)
write_wav('sfx.wav', sfx)
print('stems written', DUR)
