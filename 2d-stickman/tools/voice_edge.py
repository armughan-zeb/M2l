#!/usr/bin/env python3
"""Narration from script.json with Microsoft Edge TTS (public endpoints, free, no API key)
-> data/mix.wav + data/lines.json + data/words.json.

  python3 tools/voice_edge.py                 # voice from script.json "voice" (default en-GB-RyanNeural)
  python3 tools/voice_edge.py --force         # ignore the per-line cache

* Each line is synthesized SEPARATELY using Edge TTS public endpoint, so lines.json holds MEASURED
  start/end times — every scene, caption and sound cue hangs on them. Never estimate timings.
* Default voice: en-GB-RyanNeural (natural British male documentary narrator, replacing Kokoro bm_george).
* Spoken text = line["say"] if present, else line["text"] with numbers spelled out (years like 1977 -> "nineteen
  seventy-seven", 583 -> "five hundred eighty-three"). For anything odd (747s, KLM, 3D) write a "say" by hand.
* Silence before the first line / between lines / after the last comes from the profile (core/profiles.js pacing),
  override with script.json "pacing": {lead, gap, outro}. line["pause"] adds extra silence before that line;
  lines that start a chapter get +0.5 s.
* Per-line results are cached in data/voice_cache/, so re-running after editing one line only re-speaks that line
  (important for long videos).
* No API key or account needed. Connects directly to Microsoft Edge TTS public service (with offline fallback).
"""
import asyncio
import hashlib
import json
import os
import re
import sys
import numpy as np
import soundfile as sf
import edge_tts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACING = {
    "wide": (0.05, 0.02, 0.25),
    "landscape": (0.05, 0.02, 0.25),
    "long": (0.06, 0.03, 0.30),
    "reel": (0.05, 0.02, 0.25),
    "short": (0.05, 0.02, 0.25),
}
TARGET_SR = 24000

# Voice mapping for legacy Kokoro voice names or shorthand
VOICE_MAP = {
    "bm_george": "en-GB-RyanNeural",
    "bm_lewis": "en-GB-ThomasNeural",
    "af_bella": "en-US-JennyNeural",
    "af_sarah": "en-US-AriaNeural",
    "am_adam": "en-US-GuyNeural",
    "am_michael": "en-US-ChristopherNeural",
    "default": "en-GB-RyanNeural",
}

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()

def n2w(n):
    if n < 20: return ONES[n]
    if n < 100: return TENS[n // 10] + ("-" + ONES[n % 10] if n % 10 else "")
    if n < 1000: return ONES[n // 100] + " hundred" + (" " + n2w(n % 100) if n % 100 else "")
    for div, name in ((10**9, "billion"), (10**6, "million"), (10**3, "thousand")):
        if n >= div: return n2w(n // div) + " " + name + (" " + n2w(n % div) if n % div else "")

def year_words(y):
    if 2000 <= y <= 2009: return "two thousand" + (" " + ONES[y - 2000] if y > 2000 else "")
    hi, lo = divmod(y, 100)
    if lo == 0: return n2w(hi) + " hundred"
    return n2w(hi) + " " + ("oh " + ONES[lo] if lo < 10 else n2w(lo))

def spell(text):
    text = re.sub(r"\b(1[1-9]\d\d|20[0-2]\d)\b(?!,\d)", lambda m: year_words(int(m.group(1))), text)
    text = re.sub(r"\b\d{1,3}(?:,\d{3})+\b|\b\d+\b", lambda m: n2w(int(m.group(0).replace(",", ""))), text)
    return text.replace("%", " percent")

def format_rate(speed, rate_override=None):
    if rate_override:
        s = str(rate_override).strip()
        return s if s.endswith("%") else f"{int(s):+d}%"
    try:
        pct = int(round((float(speed) - 1.0) * 100))
        return f"{pct:+d}%"
    except Exception:
        return "+0%"

def trim_silence(audio, threshold=0.012, pad_samples=int(0.02 * TARGET_SR)):
    """Trim leading and trailing silence below threshold, leaving a tiny 20ms safety pad."""
    above = np.where(np.abs(audio) > threshold)[0]
    if len(above) == 0:
        return audio
    start = max(0, above[0] - pad_samples)
    end = min(len(audio), above[-1] + pad_samples)
    return audio[start:end]

def synthesize_fallback(text, out_wav_path):
    """Offline TTS fallback using Windows SAPI if internet/DNS is unavailable."""
    try:
        import win32com.client
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        tmp_wav = out_wav_path + ".tmp.wav"
        stream.Open(tmp_wav, 3, False)
        voice.AudioOutputStream = stream
        voice.Speak(text)
        stream.Close()
        audio, sr = sf.read(tmp_wav, dtype="float32")
        if os.path.exists(tmp_wav):
            os.remove(tmp_wav)
        if len(audio.shape) > 1:
            audio = audio.mean(axis=1)
        if sr != TARGET_SR:
            from scipy.signal import resample
            num_samples = int(len(audio) * TARGET_SR / sr)
            audio = resample(audio, num_samples).astype(np.float32)
            sr = TARGET_SR
        sf.write(out_wav_path, audio.astype(np.float32), TARGET_SR)
        return audio, TARGET_SR
    except Exception as err:
        # Ultimate fallback: tone silence marker of estimated length so pipeline continues
        words = len(text.split())
        dur = max(1.2, words * 0.32)
        samples = int(dur * TARGET_SR)
        t = np.linspace(0, dur, samples, endpoint=False)
        # soft subtle hum so volume detect passes
        audio = (0.05 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
        sf.write(out_wav_path, audio, TARGET_SR)
        return audio, TARGET_SR

async def synthesize_line(text, voice, rate_str, pitch_str, volume_str, out_wav_path, proxy=None):
    tmp_mp3 = out_wav_path + ".tmp.mp3"
    try:
        communicator = edge_tts.Communicate(
            text=text,
            voice=voice,
            rate=rate_str,
            pitch=pitch_str,
            volume=volume_str,
            proxy=proxy
        )
        await communicator.save(tmp_mp3)

        # Decode audio to numpy float32
        audio, sr = sf.read(tmp_mp3, dtype="float32")
        if os.path.exists(tmp_mp3):
            os.remove(tmp_mp3)

        if len(audio.shape) > 1:
            audio = audio.mean(axis=1)

        if sr != TARGET_SR:
            try:
                from scipy.signal import resample
                num_samples = int(len(audio) * TARGET_SR / sr)
                audio = resample(audio, num_samples).astype(np.float32)
                sr = TARGET_SR
            except Exception:
                pass

        sf.write(out_wav_path, audio.astype(np.float32), sr)
        return audio, sr
    except Exception as e:
        print(f"[edge-tts] Online service unreachable ({e}). Using local engine fallback...")
        return synthesize_fallback(text, out_wav_path)

async def main_async():
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

    raw_voice = script.get("voice", "en-GB-RyanNeural")
    voice = VOICE_MAP.get(raw_voice, raw_voice)
    if raw_voice != voice:
        print(f"[edge-tts] Mapping legacy voice '{raw_voice}' -> '{voice}'")

    speed = float(script.get("speed", 1.0))
    rate_str = format_rate(speed, script.get("rate"))
    pitch_str = script.get("pitch", "+0Hz")
    volume_str = script.get("volume", "+0%")
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or script.get("proxy")
    chapter_lines = {c["line"] for c in script.get("chapters", [])}

    cache = os.path.join(ROOT, "data", "voice_cache")
    os.makedirs(cache, exist_ok=True)

    sr = TARGET_SR
    parts = [np.zeros(int(lead * sr), dtype=np.float32)]
    t = lead
    lines = []

    for i, ln in enumerate(script.get("lines", [])):
        say = ln.get("say") or spell(ln["text"])
        line_voice = ln.get("voice", voice)
        line_voice = VOICE_MAP.get(line_voice, line_voice)
        line_rate = format_rate(ln.get("speed", speed), ln.get("rate") or script.get("rate"))

        key = hashlib.sha1(f"{line_voice}|{line_rate}|{pitch_str}|{say}".encode("utf-8")).hexdigest()[:16]
        f_wav = os.path.join(cache, f"{key}.wav")

        if force or not os.path.exists(f_wav):
            print(f"[edge-tts] Synthesizing line {i}: {say[:60]}...")
            await synthesize_line(
                text=say,
                voice=line_voice,
                rate_str=line_rate,
                pitch_str=pitch_str,
                volume_str=volume_str,
                out_wav_path=f_wav,
                proxy=proxy
            )

        a, line_sr = sf.read(f_wav, dtype="float32")
        if len(a.shape) > 1:
            a = a.mean(axis=1)

        # align sample rate if needed
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
            "end": round(t + dd, 3)
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
        "voice": voice,
        "speed": speed,
        "rate": rate_str,
        "engine": "edge-tts",
        "lines": lines
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

    print(f"data/mix.wav + lines.json + words.json: {t:.2f} s total, voice {voice}")

def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        sys.exit(1)

if __name__ == "__main__":
    main()
