"""Script -> voiceover (Microsoft Edge neural voices). Default voice: Andrew (en-US-AndrewNeural).

List every voice with:  edge-tts --list-voices

Usage:
  python tools/tts.py script.txt voice.mp3                       (text file or a quoted string)
  python tools/tts.py script.txt voice.mp3 --words work/words.json
        also writes word timings [{"w","s","e"}] in the same format words.py gives,
        so captions.py can run straight away (no Whisper needed)
  options: --voice en-US-AndrewNeural  --rate +0%  --pitch +0Hz  --raw (skip the text fixes)

Text fixes before speaking (the spoken text is saved next to the mp3 as *_spoken.txt):
  "Stephen VI" -> "Stephen the Sixth", "GUILTY" -> "Guilty" (all-caps words get spelled out otherwise),
  "..." / "…" keep their pause.
"""
import argparse, asyncio, json, os, re
import edge_tts

ORD = {"II": "the Second", "III": "the Third", "IV": "the Fourth", "V": "the Fifth", "VI": "the Sixth",
       "VII": "the Seventh", "VIII": "the Eighth", "IX": "the Ninth", "X": "the Tenth", "XI": "the Eleventh",
       "XII": "the Twelfth", "XIII": "the Thirteenth", "XIV": "the Fourteenth", "XV": "the Fifteenth",
       "XVI": "the Sixteenth", "XVII": "the Seventeenth", "XVIII": "the Eighteenth"}
ACRONYMS = {"NASA", "NATO", "UNESCO", "FIFA", "ASAP", "USSR", "UNICEF", "OPEC", "SCUBA", "LASER", "RADAR"}


def fix_text(t):
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"\b([A-Z][a-z]+) (" + "|".join(sorted(ORD, key=len, reverse=True)) + r")\b",
               lambda m: f"{m.group(1)} {ORD[m.group(2)]}", t)
    t = re.sub(r"\b[A-Z]{4,}\b", lambda m: m.group(0) if m.group(0) in ACRONYMS else m.group(0).capitalize(), t)
    return t


async def speak(text, out, voice, rate, pitch, want_words):
    com = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    bounds = []
    with open(out, "wb") as f:
        async for ch in com.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary" and want_words:
                bounds.append((ch["text"], ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7))
    return bounds


def attach_punct(text, bounds):
    """Edge gives bare words; put the script's trailing punctuation back on (captions split on it)."""
    words, pos = [], 0
    for w, s, e in bounds:
        i = text.find(w, pos)
        if i < 0:
            words.append({"w": w, "s": round(s, 3), "e": round(e, 3)}); continue
        j = i + len(w)
        while j < len(text) and text[j] in ".,?!;:…\"')":
            j += 1
        tok = text[i:j].replace("…", ".").replace("...", ".").rstrip("\"')").replace(";", ",").replace(":", ",")
        words.append({"w": tok, "s": round(s, 3), "e": round(e, 3)})
        pos = j
    return words


def main():
    p = argparse.ArgumentParser()
    p.add_argument("text")
    p.add_argument("out")
    p.add_argument("--voice", default="en-US-AndrewNeural")
    p.add_argument("--rate", default="+0%")
    p.add_argument("--pitch", default="+0Hz")
    p.add_argument("--words")
    p.add_argument("--raw", action="store_true")
    a = p.parse_args()
    text = a.text
    if text.lower().endswith(".txt") and os.path.isfile(text):
        with open(text, encoding="utf-8-sig") as f:
            text = f.read()
    if not a.raw:
        text = fix_text(text)
        with open(os.path.splitext(a.out)[0] + "_spoken.txt", "w", encoding="utf-8") as f:
            f.write(text)
    bounds = asyncio.run(speak(text, a.out, a.voice, a.rate, a.pitch, bool(a.words)))
    if a.words:
        words = attach_punct(text, bounds)
        os.makedirs(os.path.dirname(os.path.abspath(a.words)), exist_ok=True)
        json.dump(words, open(a.words, "w", encoding="utf-8"), indent=0, ensure_ascii=False)
        print(f"{len(words)} words, ends at {words[-1]['e']:.2f}s -> {a.words}")
    print("saved", a.out)


if __name__ == "__main__":
    main()
