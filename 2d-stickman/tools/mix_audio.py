#!/usr/bin/env python3
"""Cross-platform Python script to mix narration + sound cues + ambience bed into data/mixed.wav."""
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUES = os.path.join(ROOT, "data", "cues.json")
MIX = os.path.join(ROOT, "data", "mix.wav")
OUT = os.path.join(ROOT, "data", "mixed.wav")
TMP = os.path.join(ROOT, "data", ".mixed.tmp.wav")

def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)

cues, dur, bed = [], None, None
if os.path.exists(CUES):
    try:
        data = json.load(open(CUES, encoding="utf-8"))
    except Exception as e:
        fail(f"cannot read {CUES}: {e}")
    cues = data.get("cues") or []
    bed = data.get("bed")
    dur = data.get("duration")
if dur is None:
    try:
        dur = json.load(open(os.path.join(ROOT, "data", "lines.json"), encoding="utf-8"))["duration"]
    except Exception:
        fail("no film duration: data/cues.json has none and data/lines.json is missing/unreadable")
dur = float(dur)

for c in cues:
    name = c.get("name")
    cue_time = c.get('t', c.get('at', 0))
    if not name or not os.path.exists(os.path.join(ROOT, "sounds", f"{name}.wav")):
        fail(f"cue at t={cue_time} names missing sound file sounds/{name}.wav — run tools/make_sounds.sh or add it")

if bed and not os.path.exists(os.path.join(ROOT, "sounds", f"{bed['name']}.wav")):
    fail(f"ambience bed sounds/{bed['name']}.wav is missing — run tools/make_sounds.sh")

if not cues and not bed:
    if os.path.exists(MIX):
        import shutil
        shutil.copyfile(MIX, OUT)
        print(f"data/mixed.wav: no cues — copied data/mix.wav ({dur:.2f} s)")
    else:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-t", str(dur),
                        "-i", "anullsrc=r=48000:cl=stereo", "-c:a", "pcm_s16le",
                        "-ar", "48000", "-ac", "2", TMP], check=True)
        os.replace(TMP, OUT)
        print(f"data/mixed.wav: no cues, no data/mix.wav — silence ({dur:.2f} s)")
    sys.exit(0)

cmd = ["ffmpeg", "-v", "error", "-y"]
if os.path.exists(MIX):
    cmd += ["-i", MIX]
else:
    cmd += ["-f", "lavfi", "-t", str(dur), "-i", "anullsrc=r=48000:cl=stereo"]
for c in cues:
    cmd += ["-i", os.path.join(ROOT, "sounds", f"{c['name']}.wav")]
if bed:
    cmd += ["-stream_loop", "-1", "-i", os.path.join(ROOT, "sounds", f"{bed['name']}.wav")]

FMT = "aformat=sample_rates=48000:channel_layouts=stereo"
filters, mix_inputs = [f"[0:a]{FMT}[n0]"], ["[n0]"]
for i, c in enumerate(cues):
    ms = max(0, int(round(float(c.get("t", c.get("at", 0))) * 1000)))
    vol = float(c.get("vol", 0.25))
    filters.append(f"[{i + 1}:a]{FMT},adelay={ms}|{ms},volume={vol}[c{i}]")
    mix_inputs.append(f"[c{i}]")
if bed:
    bi = len(cues) + 1
    filters.append(f"[{bi}:a]{FMT},volume={float(bed.get('vol', 0.07))},afade=t=in:st=0:d=1.5,afade=t=out:st={max(0, dur - 2):.2f}:d=2[bed]")
    mix_inputs.append("[bed]")
n = len(mix_inputs)
filters.append(f"{''.join(mix_inputs)}amix=inputs={n}:duration=longest:normalize=0[out]")
cmd += ["-filter_complex", ";".join(filters), "-map", "[out]",
        "-t", str(dur), "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", TMP]
r = subprocess.run(cmd)
if r.returncode:
    fail("ffmpeg mix failed")
os.replace(TMP, OUT)
print(f"data/mixed.wav: {len(cues)} cue(s){' + ambience bed' if bed else ''} over {'data/mix.wav' if os.path.exists(MIX) else 'silence'} ({dur:.2f} s)")
