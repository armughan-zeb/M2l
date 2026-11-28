#!/usr/bin/env python3
"""Narration from script.json with Muse TTS (primary, /opt/hatch/bin/tts)
-> data/mix.wav + data/lines.json + data/words.json.

  python3 tools/voice_muse.py                 # voice from script.json "voice" (Muse voice id, e.g. avocado_v2:MAI_03)
  python3 tools/voice_muse.py --force         # ignore the per-line cache

* Muse TTS is the PRIMARY narration voice on Linux, exactly as in the
  Vidnets documentary pipeline (services/edge_tts_service.py): the bundled
  Muse CLI (/opt/hatch/bin/tts speak --text-stdin --voice {voice}
  --speed {speed} --format mp3 --output {output}), default voice
  avocado_v2:MAI_03, speed 100. Overrides: VIDZEO_LINUX_TTS_COMMAND,
  VIDZEO_LINUX_TTS_VOICE, VIDZEO_LINUX_TTS_SPEED.
* Each line is synthesized SEPARATELY, so lines.json holds MEASURED
  start/end times — every scene, caption and sound cue hangs on them.
  Never estimate timings.
* Per-line results are cached in data/voice_cache/ (key includes the
  "muse" engine tag, so Edge cache files never collide), so re-running
  after editing one line only re-speaks that line.
* Edge TTS is only the fallback: if the Muse CLI fails a line after its
  retries, that line is synthesized with edge_tts directly (no SAPI, no
  tone-silence marker — a line that cannot be spoken fails the run).
* Spoken text = line["say"] if present, else line["text"] with numbers
  spelled out (same rules as voice_edge.py).
* Silence before the first line / between lines / after the last comes
  from the profile (core/profiles.js pacing), override with script.json
  "pacing": {lead, gap, outro}. line["pause"] adds extra silence before
  that line; lines that start a chapter get +0.5 s.
"""
import asyncio
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys

import numpy as np
import soundfile as sf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACING = {
    "wide": (0.05, 0.02, 0.25),
    "landscape": (0.05, 0.02, 0.25),
    "long": (0.06, 0.03, 0.30),
    "reel": (0.05, 0.02, 0.25),
    "short": (0.05, 0.02, 0.25),
}
TARGET_SR = 24000

_MUSE_TTS_BINARY = "/opt/hatch/bin/tts"
_MUSE_TTS_COMMAND_TEMPLATE = (
    "/opt/hatch/bin/tts speak --text-stdin --voice {voice} "
    "--speed {speed} --format mp3 --output {output}"
)
_MUSE_TTS_DEFAULT_VOICE = "avocado_v2:MAI_03"
_MUSE_TTS_DEFAULT_SPEED = "100"

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def n2w(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("-" + ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return ONES[n // 100] + " hundred" + (" " + n2w(n % 100) if n % 100 else "")
    for div, name in ((10**9, "billion"), (10**6, "million"), (10**3, "thousand")):
        if n >= div:
            return n2w(n // div) + " " + name + (" " + n2w(n % div) if n % div else "")


def year_words(y):
    if 2000 <= y <= 2009:
        return "two thousand" + (" " + ONES[y - 2000] if y > 2000 else "")
    hi, lo = divmod(y, 100)
    if lo == 0:
        return n2w(hi) + " hundred"
    return n2w(hi) + " " + ("oh " + ONES[lo] if lo < 10 else n2w(lo))


def spell(text):
    text = re.sub(r"\b(1[1-9]\d\d|20[0-2]\d)\b(?!\d)", lambda m: year_words(int(m.group(1))), text)
    text = re.sub(r"\b\d{1,3}(?:,\d{3})+\b|\b\d+\b", lambda m: n2w(int(m.group(0).replace(",", ""))), text)
    return text.replace("%", " percent")


def trim_silence(audio, threshold=0.012, pad_samples=int(0.02 * TARGET_SR)):
    """Trim leading and trailing silence below threshold, leaving a tiny 20ms safety pad."""
    above = np.where(np.abs(audio) > threshold)[0]
    if len(above) == 0:
        return audio
    start = max(0, above[0] - pad_samples)
    end = min(len(audio), above[-1] + pad_samples)
    return audio[start:end]


def _decode_to_target_sr(path):
    """Decode an audio file to float32 mono at TARGET_SR (resampling if needed)."""
    audio, sr = sf.read(path, dtype="float32")
    if len(audio.shape) > 1:
        audio = audio.mean(axis=1)
    if sr != TARGET_SR:
        from scipy.signal import resample

        num_samples = int(len(audio) * TARGET_SR / sr)
        audio = resample(audio, num_samples).astype(np.float32)
    return audio.astype(np.float32), TARGET_SR


def _resolve_muse_voice(script_voice, speed):
    """Resolve (command_template, voice, speed) the Vidnets way.

    A script voice that already looks like a Muse voice id (contains ':',
    e.g. avocado_v2:MAI_03) is used as-is; a legacy Edge Neural name falls
    through to the Muse default, since Edge is only the fallback here.
    """
    command = str(os.environ.get("VIDZEO_LINUX_TTS_COMMAND", "") or "").strip()
    if not command:
        if os.path.isfile(_MUSE_TTS_BINARY) and os.access(_MUSE_TTS_BINARY, os.X_OK):
            command = _MUSE_TTS_COMMAND_TEMPLATE
        else:
            return None
    voice = str(os.environ.get("VIDZEO_LINUX_TTS_VOICE", "") or "").strip()
    if not voice:
        candidate = str(script_voice or "").strip()
        voice = candidate if ":" in candidate else _MUSE_TTS_DEFAULT_VOICE
    speed_str = str(os.environ.get("VIDZEO_LINUX_TTS_SPEED", "") or "").strip()
    if not speed_str:
        speed_str = str(int(round(float(speed) * 100))) if speed else _MUSE_TTS_DEFAULT_SPEED
    return command, voice, speed_str


def _muse_timeout_seconds():
    try:
        return float(os.environ.get("VIDZEO_LINUX_TTS_TIMEOUT_SECONDS", "60") or 60)
    except ValueError:
        return 60.0


def synthesize_muse_line(text, voice, speed_str, out_wav_path, command_template):
    """One line via the Muse TTS CLI. Raises on failure (caller falls back)."""
    tmp_mp3 = out_wav_path + ".tmp.mp3"
    argv = [
        part.format(text_file="", output=tmp_mp3, voice=voice, speed=speed_str)
        for part in shlex.split(command_template)
    ]
    timeout = _muse_timeout_seconds()
    last_err = None
    for attempt in range(3):
        if os.path.exists(tmp_mp3):
            os.remove(tmp_mp3)
        try:
            result = subprocess.run(
                argv, input=text.encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Muse TTS failed ({result.returncode}): "
                    f"{result.stderr.decode('utf-8', 'replace').strip()[-400:]}"
                )
            if not os.path.isfile(tmp_mp3) or os.path.getsize(tmp_mp3) < 128:
                raise RuntimeError("Muse TTS produced no audio file.")
            audio, _sr = _decode_to_target_sr(tmp_mp3)
            if len(audio) == 0:
                raise RuntimeError("Muse TTS produced unreadable audio.")
            sf.write(out_wav_path, audio.astype(np.float32), TARGET_SR)
            return audio, TARGET_SR
        except Exception as err:  # noqa: BLE001 - retried, then surfaced to fallback
            last_err = err
            print(f"[muse-tts] attempt {attempt + 1}/3 failed: {err}", flush=True)
    if os.path.exists(tmp_mp3):
        os.remove(tmp_mp3)
    raise RuntimeError(f"Muse TTS failed after 3 attempts: {last_err}")


async def synthesize_edge_line(text, voice, rate_str, out_wav_path):
    """Fallback: one line via Edge TTS directly. Fail-closed: no tone marker."""
    import edge_tts

    tmp_mp3 = out_wav_path + ".tmp.mp3"
    communicator = edge_tts.Communicate(
        text=text, voice=voice, rate=rate_str, pitch="+0Hz", volume="+0%"
    )
    await communicator.save(tmp_mp3)
    try:
        audio, _sr = _decode_to_target_sr(tmp_mp3)
        if len(audio) == 0:
            raise RuntimeError("Edge TTS fallback produced unreadable audio.")
        sf.write(out_wav_path, audio.astype(np.float32), TARGET_SR)
        return audio, TARGET_SR
    finally:
        if os.path.exists(tmp_mp3):
            os.remove(tmp_mp3)


def main():
    force = "--force" in sys.argv
    script_file = os.path.join(ROOT, "script.json")
    if not os.path.exists(script_file):
        sys.exit(f"Error: {script_file} not found")

    with open(script_file, "r", encoding="utf-8") as f:
        script = json.load(f)

    prof = script.get("profile") or ("reel" if script.get("format") == "vertical" else "wide")
    lead, gap, outro = PACING.get(prof, PACING["wide"])
    pc = script.get("pacing", {})
    lead, gap, outro = pc.get("lead", lead), pc.get("gap", gap), pc.get("outro", outro)

    speed = float(script.get("speed", 1.0))
    resolved = _resolve_muse_voice(script.get("voice", ""), speed)
    if resolved is None:
        sys.exit("Error: Muse TTS CLI not found at /opt/hatch/bin/tts and no VIDZEO_LINUX_TTS_COMMAND set")
    command_template, muse_voice, speed_str = resolved
    print(f"[muse-tts] voice {muse_voice} speed {speed_str} (Edge fallback only)")

    # Edge fallback voice: the script voice when it names an Edge voice,
    # otherwise the documentary default.
    raw_voice = str(script.get("voice", "")).strip()
    edge_voice = raw_voice if "Neural" in raw_voice else "en-GB-RyanNeural"
    rate_str = f"{int(round((speed - 1.0) * 100)):+d}%"

    chapter_lines = {c["line"] for c in script.get("chapters", [])}
    cache = os.path.join(ROOT, "data", "voice_cache")
    os.makedirs(cache, exist_ok=True)

    sr = TARGET_SR
    parts = [np.zeros(int(lead * sr), dtype=np.float32)]
    t = lead
    lines = []
    fallback_lines = []

    for i, ln in enumerate(script.get("lines", [])):
        say = ln.get("say") or spell(ln["text"])

        key = hashlib.sha1(f"muse|{muse_voice}|{speed_str}|{say}".encode("utf-8")).hexdigest()[:16]
        f_wav = os.path.join(cache, f"{key}.wav")

        if force or not os.path.exists(f_wav):
            print(f"[muse-tts] Synthesizing line {i}: {say[:60]}...")
            try:
                synthesize_muse_line(say, muse_voice, speed_str, f_wav, command_template)
            except Exception as err:  # noqa: BLE001 - Edge fallback, then hard fail
                print(f"[muse-tts] Muse failed for line {i} ({err}); falling back to Edge TTS...")
                asyncio.run(synthesize_edge_line(say, edge_voice, rate_str, f_wav))
                fallback_lines.append(i)

        a, line_sr = sf.read(f_wav, dtype="float32")
        if len(a.shape) > 1:
            a = a.mean(axis=1)
        if line_sr != sr:
            from scipy.signal import resample

            num_samples = int(len(a) * sr / line_sr)
            a = resample(a, num_samples).astype(np.float32)

        # Strip artificial TTS dead air at start and end
        a = trim_silence(a)

        if i > 0:
            extra = gap + float(ln.get("pause", 0)) + (0.05 if i in chapter_lines else 0.0)
            parts.append(np.zeros(int(extra * sr), dtype=np.float32))
            t += extra
        elif ln.get("pause"):
            extra = float(ln["pause"])
            parts.append(np.zeros(int(extra * sr), dtype=np.float32))
            t += extra

        dd = len(a) / sr
        lines.append({
            "i": i,
            "text": ln["text"],
            "say": say,
            "start": round(t, 3),
            "end": round(t + dd, 3),
        })
        parts.append(a)
        t += dd
        print(f"line {i}: {dd:5.2f}s  {say[:70]}")

    parts.append(np.zeros(int(outro * sr), dtype=np.float32))
    t += outro

    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    mix_wav = os.path.join(ROOT, "data", "mix.wav")
    sf.write(mix_wav, np.concatenate(parts), sr)

    lines_data = {
        "duration": round(t, 3),
        "voice": muse_voice,
        "speed": speed,
        "rate": rate_str,
        "engine": "muse-tts",
        "fallback_lines": fallback_lines,
        "lines": lines,
    }
    with open(os.path.join(ROOT, "data", "lines.json"), "w", encoding="utf-8") as fp:
        json.dump(lines_data, fp, indent=1)

    words = []
    for ln in lines:
        ws = ln["text"].split()
        tot = sum(len(w) for w in ws) or 1
        tt = ln["start"]
        dd = ln["end"] - ln["start"]
        for w in ws:
            e = tt + dd * len(w) / tot
            words.append({"w": w, "line": ln["i"], "s": round(tt, 3), "e": round(e, 3)})
            tt = e

    with open(os.path.join(ROOT, "data", "words.json"), "w", encoding="utf-8") as fp:
        json.dump(words, fp)

    print(f"data/mix.wav + lines.json + words.json: {t:.2f} s total, voice {muse_voice}"
          + (f" (Edge fallback on lines {fallback_lines})" if fallback_lines else ""))


if __name__ == "__main__":
    main()
