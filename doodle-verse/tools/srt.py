# python -I tools/srt.py work/words.json out/subtitles_en.srt [fixes.json]  -> sentence-sized SRT cues for YouTube closed captions
# Breaks at punctuation, or at a speech pause when Whisper left a stretch unpunctuated.
# Optional fixes.json = {"lowercase word": "Correct Spelling"} for names Whisper gets wrong.
import sys, json
w = json.load(open(sys.argv[1], encoding='utf-8'))
fix = json.load(open(sys.argv[3], encoding='utf-8')) if len(sys.argv) > 3 else {}
fix = {k.lower(): v for k, v in fix.items()}
fix.setdefault('i', 'I')
for x in w:
    core = x['w'].rstrip('.,?!'); tail = x['w'][len(core):]
    if core.lower() in fix: x['w'] = fix[core.lower()] + tail
cues, cur = [], []
for i, x in enumerate(w):
    cur.append(x)
    gap = w[i + 1]['s'] - x['e'] if i + 1 < len(w) else 9
    n = len(' '.join(y['w'] for y in cur))
    if (x['w'][-1] in '.?!' and n > 18) or (x['w'][-1] == ',' and n > 34) or (gap > 0.45 and n > 22) or n > 70: cues.append(cur); cur = []
if cur: cues.append(cur)
ts = lambda t: '%02d:%02d:%02d,%03d' % (t // 3600, t % 3600 // 60, int(t % 60), round((t % 1) * 1000) % 1000)
out, prev_end = [], '.'
for i, c in enumerate(cues):
    txt = ' '.join(y['w'] for y in c)
    if prev_end in '.?!': txt = txt[0].upper() + txt[1:]
    prev_end = c[-1]['w'][-1]
    out.append('%d\n%s --> %s\n%s\n' % (i + 1, ts(c[0]['s']), ts(c[-1]['e']), txt))
open(sys.argv[2], 'w', encoding='utf-8').write('\n'.join(out))
print(len(cues), 'cues')
