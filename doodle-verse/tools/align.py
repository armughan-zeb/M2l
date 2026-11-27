# Align a new narrator's words to the base (Andrew) timeline -> piecewise-linear time map.
# python align.py base_words.json new_words.json new_total_sec base_total_sec out.json
import sys, json, re, difflib

def toks(words):
    out = []
    for w in words:
        parts = w['w'].split()
        for k, p in enumerate(parts):
            t = re.sub(r"[^a-z0-9']", "", p.lower())
            if t: out.append((t, w['s'] + k * 0.45))
    return out

base = toks(json.load(open(sys.argv[1], encoding='utf-8')))
new = toks(json.load(open(sys.argv[2], encoding='utf-8')))
new_total, base_total = float(sys.argv[3]), float(sys.argv[4])
sm = difflib.SequenceMatcher(a=[t for t, _ in base], b=[t for t, _ in new], autojunk=False)
pairs = [(0.0, 0.0)]
for blk in sm.get_matching_blocks():
    for k in range(blk.size):
        pairs.append((new[blk.b + k][1], base[blk.a + k][1]))
missing = [t for i, (t, _) in enumerate(base) if not any(b.a <= i < b.a + b.size for b in sm.get_matching_blocks())]
pairs.append((new_total, base_total))
real, b = [], []
for r, s in sorted(pairs):                      # keep strictly increasing in both
    if real and (r <= real[-1] + 0.08 or s <= b[-1] + 0.05): continue
    real.append(round(r, 3)); b.append(round(s, 3))
json.dump({"real": real, "base": b}, open(sys.argv[5], 'w'), indent=0)
print(f"{len(real)} anchors; base words not spoken: {missing}")
