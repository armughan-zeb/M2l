# python -I tools/words.py voice.mp3 work/words.json [lang] [prompt]  -> word timestamps [{"w","s","e"}] for captions.py
# lang: en (default, small.en on the CPU, fast and accurate for English), auto, or any Whisper language code
# (e.g. tl for Tagalog/Taglish, es, pt). Non-English uses large-v3 on an NVIDIA GPU when available,
# otherwise medium on the CPU (slower, still good).
# Decodes with ffmpeg (more robust than faster-whisper's own decoder on some installs).
import sys, json, os, glob, site, subprocess
import numpy as np

for sp in site.getsitepackages():  # pip nvidia-cublas/cudnn DLLs (Windows GPU), harmless if absent
    for d in glob.glob(os.path.join(sp, 'nvidia', '*', 'bin')):
        try:
            os.add_dll_directory(d); os.environ['PATH'] = d + os.pathsep + os.environ['PATH']
        except (AttributeError, OSError):
            pass
from faster_whisper import WhisperModel

lang = sys.argv[3] if len(sys.argv) > 3 else "en"
if lang == "en":
    m = WhisperModel("small.en", device="cpu", compute_type="int8")
else:
    try:
        m = WhisperModel("large-v3", device="cuda", compute_type="float16")
    except Exception:
        print("no CUDA GPU, using medium on the CPU (slower)")
        m = WhisperModel("medium", device="cpu", compute_type="int8")
pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", sys.argv[1], "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                     capture_output=True, check=True).stdout
prompt = sys.argv[4] if len(sys.argv) > 4 else ("Minsan, may makikilala kang tao. Taglish, Tagalog na salita." if lang == 'tl' else None)
segs, info = m.transcribe(np.frombuffer(pcm, np.float32), word_timestamps=True, beam_size=5,
                          language=None if lang == 'auto' else lang, task='transcribe', initial_prompt=prompt,
                          condition_on_previous_text=False)
if lang == 'auto':
    print('language:', info.language, round(info.language_probability, 2))
words = []
for s in segs:
    for w in s.words:
        t = w.word.strip()
        if t.startswith('-') and words:  # "ma" + "-fall" -> "ma-fall"
            words[-1]['w'] += t; words[-1]['e'] = round(w.end, 3)
        else:
            words.append({"w": t, "s": round(w.start, 3), "e": round(w.end, 3)})
os.makedirs(os.path.dirname(os.path.abspath(sys.argv[2])), exist_ok=True)
json.dump(words, open(sys.argv[2], "w", encoding="utf-8"), indent=0, ensure_ascii=False)
print(" ".join(f"{x['w']}[{x['s']:.2f}]" for x in words))
