# python captions.py work/words.json work/captions.ass  -> word-highlight captions for 1080x1920
# words.json = [{"w","s","e"}] from Whisper. Chunks of <= 3 words / 16 chars, split at punctuation;
# the spoken word turns yellow, each chunk pops in. Burn with: -vf "subtitles=work/captions.ass"
import sys, json
words = json.load(open(sys.argv[1], encoding='utf-8'))
MAXW, MAXC, Y = 3, 16, 430                      # Y = bottom margin, clear of the Shorts/TikTok UI

chunks, cur = [], []
for w in words:
    tail = w['w'][-1] in '.,?!' and len(w['w']) <= 4   # don't strand a short sentence end ("on?") on its own
    if cur and not tail and (len(cur) == MAXW or len(' '.join(x['w'] for x in cur + [w])) > MAXC):
        chunks.append(cur); cur = []
    cur.append(w)
    if w['w'][-1] in '.,?!': chunks.append(cur); cur = []
if cur: chunks.append(cur)

ts = lambda t: f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"
head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial Black,96,&H00FFFFFF,&H00FFFFFF,&H00180F14,&H96000000,-1,0,0,0,100,100,1,0,1,7,4,2,60,60,{Y},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
ev = []
for ci, c in enumerate(chunks):
    end = min(c[-1]['e'] + 0.45, chunks[ci + 1][0]['s']) if ci + 1 < len(chunks) else c[-1]['e'] + 0.6
    for wi, w in enumerate(c):
        a = w['s'] if wi else c[0]['s']
        b = c[wi + 1]['s'] if wi + 1 < len(c) else end
        say = lambda x: x['w'].rstrip('.,').upper()     # keep ? and !, drop . and ,
        txt = ' '.join(('{\\c&H00F5FF&}' + say(x) + '{\\c&HFFFFFF&}') if j == wi else say(x) for j, x in enumerate(c))
        plain = ' '.join(say(x) for x in c)               # squeeze chunks wider than the frame (Arial Black caps ~0.78 em)
        k = min(100, int(100 * 940 / (96 * (0.78 * sum(ch != ' ' for ch in plain) + 0.33 * plain.count(' ')))))
        pop = f'{{\\fscx{int(k * .82)}\\fscy{int(k * .82)}\\t(0,90,\\fscx{k}\\fscy{k})}}' if wi == 0 else (f'{{\\fscx{k}\\fscy{k}}}' if k < 100 else '')
        ev.append(f"Dialogue: 0,{ts(a)},{ts(b)},Cap,,0,0,0,,{pop}{txt}")
open(sys.argv[2], 'w', encoding='utf-8').write(head + '\n'.join(ev) + '\n')
print(len(chunks), 'chunks:', ' | '.join(' '.join(x['w'] for x in c) for c in chunks))
