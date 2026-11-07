"""Create Vidzeo's small, original procedural SFX palette.

The sounds are synthesized locally, so projects can use subtle documentary
hits/ticks/whooshes without silently treating scraped audio as licensed.
"""

from __future__ import annotations

import math
import random
import wave
from pathlib import Path


RATE = 48_000


def write_wav(path: Path, duration: float, sample):
    count = max(1, int(duration * RATE))
    frames = bytearray()
    for index in range(count):
        value = max(-1.0, min(1.0, sample(index / RATE, index, count)))
        frames += int(value * 32767).to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(bytes(frames))


def envelope(t: float, duration: float, attack: float = .02, release: float = .18) -> float:
    if t < attack:
        return t / max(.001, attack)
    if t > duration - release:
        return max(0.0, (duration - t) / max(.001, release))
    return 1.0


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    output = root / "public" / "sound-effects" / "vidzeo"
    output.mkdir(parents=True, exist_ok=True)
    random.seed(20260729)

    def noise(seed: int, amount: float = 1.0):
        rng = random.Random(seed)
        return lambda _t, _i, _n: (rng.random() * 2 - 1) * amount

    recipes = {
        "whoosh.wav": (.42, lambda t, i, n: (noise(i, .35)(t, i, n) * envelope(t, .42, .03, .18) * (t / .42))),
        "whoosh_soft.wav": (.48, lambda t, i, n: noise(i + 5, .16)(t, i, n) * envelope(t, .48, .04, .2) * (0.45 + .55 * t / .48)),
        "impact.wav": (.34, lambda t, i, n: (math.sin(2 * math.pi * (82 - 44 * t) * t) * .75 + noise(i + 13, .12)(t, i, n)) * math.exp(-10 * t)),
        "impact_soft.wav": (.28, lambda t, i, n: (math.sin(2 * math.pi * (126 - 26 * t) * t) * .34 + noise(i + 19, .06)(t, i, n)) * math.exp(-13 * t)),
        "sub_boom.wav": (.68, lambda t, i, n: math.sin(2 * math.pi * (54 - 20 * t) * t) * .48 * math.exp(-5 * t)),
        "reverse_swell.wav": (.54, lambda t, i, n: noise(i + 23, .16)(t, i, n) * envelope(t, .54, .04, .03) * (t / .54) ** 2),
        "paper_flip.wav": (.38, lambda t, i, n: noise(i + 41, .26)(t, i, n) * envelope(t, .38, .02, .14) * (.5 + .5 * math.sin(2 * math.pi * 19 * t))),
        "paper_rustle.wav": (.58, lambda t, i, n: noise(i + 47, .14)(t, i, n) * envelope(t, .58, .03, .19) * (.4 + .6 * abs(math.sin(2 * math.pi * 13 * t)))),
        "paper_tear.wav": (.31, lambda t, i, n: noise(i + 49, .2)(t, i, n) * envelope(t, .31, .02, .1) * (1 - .35 * t / .31)),
        "calendar_flip.wav": (.26, lambda t, i, n: noise(i + 50, .15)(t, i, n) * envelope(t, .26, .02, .08) * abs(math.sin(2 * math.pi * 22 * t))),
        "photo_slide.wav": (.46, lambda t, i, n: noise(i + 52, .17)(t, i, n) * envelope(t, .46, .02, .16) * (1 - .55 * t / .46)),
        "camera_shutter.wav": (.17, lambda t, i, n: (noise(i + 58, .19)(t, i, n) + math.sin(2 * math.pi * 740 * t) * .13) * math.exp(-31 * t)),
        "pencil_mark.wav": (.34, lambda t, i, n: noise(i + 63, .1)(t, i, n) * envelope(t, .34, .03, .1) * (.4 + .6 * abs(math.sin(2 * math.pi * 37 * t)))),
        "marker_scratch.wav": (.29, lambda t, i, n: noise(i + 67, .13)(t, i, n) * envelope(t, .29, .02, .09) * (.35 + .65 * abs(math.sin(2 * math.pi * 48 * t)))),
        "stamp_hit.wav": (.20, lambda t, i, n: (math.sin(2 * math.pi * 109 * t) * .35 + noise(i + 71, .13)(t, i, n)) * math.exp(-18 * t)),
        "typewriter.wav": (.10, lambda t, i, n: (math.sin(2 * math.pi * 1850 * t) * .32 + noise(i + 97, .22)(t, i, n)) * math.exp(-48 * t)),
        "typing_burst.wav": (.32, lambda t, i, n: (math.sin(2 * math.pi * 1720 * t) * .12 + noise(i + 98, .09)(t, i, n)) * envelope(t, .32, .01, .06) * abs(math.sin(2 * math.pi * 17 * t))),
        "decision_tick.wav": (.16, lambda t, i, n: (math.sin(2 * math.pi * 970 * t) * .24 + math.sin(2 * math.pi * 1430 * t) * .08) * math.exp(-28 * t)),
        "clock_tick.wav": (.16, lambda t, i, n: (math.sin(2 * math.pi * 720 * t) * .2 + noise(i + 102, .08)(t, i, n)) * math.exp(-35 * t)),
        "data_tick.wav": (.13, lambda t, i, n: math.sin(2 * math.pi * (680 + 370 * t) * t) * .2 * math.exp(-34 * t)),
        "technical_tick.wav": (.14, lambda t, i, n: (math.sin(2 * math.pi * 560 * t) * .18 + math.sin(2 * math.pi * 840 * t) * .08) * math.exp(-31 * t)),
        "signal_ping.wav": (.28, lambda t, i, n: math.sin(2 * math.pi * (960 - 360 * t) * t) * .2 * envelope(t, .28, .01, .13)),
        "route_ping.wav": (.31, lambda t, i, n: (math.sin(2 * math.pi * 520 * t) * .18 + math.sin(2 * math.pi * 780 * t) * .08) * envelope(t, .31, .01, .16)),
        "lens_click.wav": (.16, lambda t, i, n: (noise(i + 111, .12)(t, i, n) + math.sin(2 * math.pi * 920 * t) * .15) * math.exp(-38 * t)),
        "map_pulse.wav": (.42, lambda t, i, n: math.sin(2 * math.pi * (420 - 130 * t) * t) * .24 * envelope(t, .42, .01, .24)),
        "radio_static.wav": (.48, lambda t, i, n: noise(i + 121, .12)(t, i, n) * envelope(t, .48, .02, .14) * (.45 + .55 * abs(math.sin(2 * math.pi * 83 * t)))),
        "vinyl_crackle.wav": (.62, lambda t, i, n: noise(i + 129, .055)(t, i, n) * envelope(t, .62, .03, .18) * (.2 + .8 * abs(math.sin(2 * math.pi * 31 * t)))),
        "low_rumble.wav": (.72, lambda t, i, n: math.sin(2 * math.pi * (42 + 8 * t) * t) * .2 * envelope(t, .72, .08, .25)),
        "suspense_pulse.wav": (.56, lambda t, i, n: math.sin(2 * math.pi * 94 * t) * .18 * envelope(t, .56, .02, .2) * (.4 + .6 * abs(math.sin(2 * math.pi * 2.2 * t)))),
        "riser.wav": (.62, lambda t, i, n: (math.sin(2 * math.pi * (140 + 700 * t) * t) * .16 + noise(i + 149, .075)(t, i, n)) * envelope(t, .62, .03, .12) * (0.3 + .7 * t / .62)),
        "ui_tick.wav": (.12, lambda t, i, n: math.sin(2 * math.pi * 1180 * t) * .22 * math.exp(-36 * t)),
        "water_pulse.wav": (.54, lambda t, i, n: (math.sin(2 * math.pi * 126 * t) * .16 + noise(i + 211, .08)(t, i, n)) * envelope(t, .54, .05, .28)),
        "room_tone.wav": (.55, lambda t, i, n: noise(i + 307, .024)(t, i, n) * envelope(t, .55, .12, .12)),
        "whoosh_air.wav": (.37, lambda t, i, n: noise(i + 331, .13)(t, i, n) * envelope(t, .37, .02, .11) * math.sin(math.pi * t / .37)),
        "whoosh_dark.wav": (.52, lambda t, i, n: (noise(i + 337, .11)(t, i, n) + math.sin(2 * math.pi * 58 * t) * .08) * envelope(t, .52, .04, .2)),
        "whoosh_snap.wav": (.22, lambda t, i, n: noise(i + 347, .18)(t, i, n) * envelope(t, .22, .01, .08) * (1 - t / .22)),
        "impact_metal.wav": (.38, lambda t, i, n: (math.sin(2 * math.pi * 213 * t) * .26 + math.sin(2 * math.pi * 337 * t) * .13 + noise(i + 349, .08)(t, i, n)) * math.exp(-9 * t)),
        "impact_wood.wav": (.28, lambda t, i, n: (math.sin(2 * math.pi * 126 * t) * .32 + noise(i + 353, .11)(t, i, n)) * math.exp(-15 * t)),
        "impact_glass.wav": (.31, lambda t, i, n: (math.sin(2 * math.pi * 1460 * t) * .14 + math.sin(2 * math.pi * 2180 * t) * .08 + noise(i + 359, .05)(t, i, n)) * math.exp(-13 * t)),
        "bass_drop.wav": (.82, lambda t, i, n: math.sin(2 * math.pi * (76 - 42 * t) * t) * .42 * envelope(t, .82, .02, .3)),
        "tension_string.wav": (.88, lambda t, i, n: (math.sin(2 * math.pi * (310 + 54 * t) * t) * .09 + noise(i + 367, .035)(t, i, n)) * envelope(t, .88, .14, .22)),
        "heartbeat.wav": (.72, lambda t, i, n: math.sin(2 * math.pi * 61 * t) * .32 * (math.exp(-45 * (t % .38)) + .62 * math.exp(-52 * ((t - .13) % .38)))),
        "reveal_chime.wav": (.74, lambda t, i, n: (math.sin(2 * math.pi * 523 * t) * .14 + math.sin(2 * math.pi * 659 * t) * .1 + math.sin(2 * math.pi * 784 * t) * .07) * envelope(t, .74, .01, .48)),
        "news_sting.wav": (.64, lambda t, i, n: (math.sin(2 * math.pi * (196 + (i // 4800 % 4) * 52) * t) * .12 + math.sin(2 * math.pi * 392 * t) * .06) * envelope(t, .64, .02, .16)),
        "telegraph.wav": (.42, lambda t, i, n: math.sin(2 * math.pi * 860 * t) * .15 * (1 if (i // 2400) % 3 != 2 else 0) * envelope(t, .42, .01, .04)),
        "keyboard_single.wav": (.13, lambda t, i, n: (math.sin(2 * math.pi * 1320 * t) * .12 + noise(i + 373, .12)(t, i, n)) * math.exp(-39 * t)),
        "mouse_click.wav": (.10, lambda t, i, n: (math.sin(2 * math.pi * 1880 * t) * .12 + noise(i + 379, .08)(t, i, n)) * math.exp(-55 * t)),
        "notification_up.wav": (.27, lambda t, i, n: math.sin(2 * math.pi * (540 + 840 * t) * t) * .17 * envelope(t, .27, .01, .13)),
        "notification_down.wav": (.27, lambda t, i, n: math.sin(2 * math.pi * (1120 - 610 * t) * t) * .17 * envelope(t, .27, .01, .13)),
        "coin_tick.wav": (.28, lambda t, i, n: (math.sin(2 * math.pi * 1640 * t) * .15 + math.sin(2 * math.pi * 2310 * t) * .08) * math.exp(-14 * t)),
        "cash_counter.wav": (.45, lambda t, i, n: (noise(i + 383, .07)(t, i, n) + math.sin(2 * math.pi * 890 * t) * .08) * abs(math.sin(2 * math.pi * 18 * t)) * envelope(t, .45, .01, .08)),
        "camera_wind.wav": (.42, lambda t, i, n: (noise(i + 389, .13)(t, i, n) + math.sin(2 * math.pi * 310 * t) * .05) * envelope(t, .42, .03, .18)),
        "projector.wav": (.58, lambda t, i, n: (noise(i + 397, .045)(t, i, n) + math.sin(2 * math.pi * 24 * t) * .04) * envelope(t, .58, .04, .12)),
        "film_splice.wav": (.19, lambda t, i, n: (noise(i + 401, .17)(t, i, n) + math.sin(2 * math.pi * 420 * t) * .09) * math.exp(-27 * t)),
        "page_stack.wav": (.62, lambda t, i, n: noise(i + 409, .11)(t, i, n) * envelope(t, .62, .02, .17) * (.35 + .65 * abs(math.sin(2 * math.pi * 8.5 * t)))),
        "map_draw.wav": (.68, lambda t, i, n: noise(i + 419, .065)(t, i, n) * envelope(t, .68, .04, .16) * (.45 + .55 * abs(math.sin(2 * math.pi * 27 * t)))),
        "sonar.wav": (.75, lambda t, i, n: math.sin(2 * math.pi * 630 * t) * .14 * envelope(t, .75, .01, .55)),
        "archive_drone.wav": (.92, lambda t, i, n: (math.sin(2 * math.pi * 73 * t) * .08 + math.sin(2 * math.pi * 111 * t) * .035 + noise(i + 421, .018)(t, i, n)) * envelope(t, .92, .14, .28)),
        "wind_room.wav": (.86, lambda t, i, n: noise(i + 431, .028)(t, i, n) * envelope(t, .86, .12, .22) * (.6 + .4 * math.sin(2 * math.pi * .7 * t))),
        "crowd_murmur.wav": (.82, lambda t, i, n: (noise(i + 433, .025)(t, i, n) + math.sin(2 * math.pi * (130 + i % 17) * t) * .012) * envelope(t, .82, .1, .18)),
        "transition_suck.wav": (.39, lambda t, i, n: noise(i + 439, .13)(t, i, n) * envelope(t, .39, .08, .02) * (t / .39) ** 1.6),
    }
    for filename, (duration, sample) in recipes.items():
        target = output / filename
        if not target.exists() or target.stat().st_size < 1024:
            write_wav(target, duration, sample)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
