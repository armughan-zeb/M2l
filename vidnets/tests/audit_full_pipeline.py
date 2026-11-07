"""Full pipeline audit with actual football script text."""
import sys, os
sys.path.insert(0, 'server')

SCRIPT = """Every football generation has its legends. But today's era has produced some of the most complete, talented, and dominant players the sport has ever seen. Here are the top five best footballers of the current era.

At number five is Erling Haaland. The Norwegian goal machine has rewritten scoring records wherever he's played. With incredible strength, blistering pace, and deadly finishing, Haaland is one of the most feared strikers in world football. Give him one chance, and he'll probably score.

At number four is Jude Bellingham. Still incredibly young, Bellingham has already become one of the world's best midfielders. He combines vision, technical ability, defensive work rate, and leadership beyond his years, making him the complete modern midfielder.

Taking the third spot is Kylian Mbappe. Few players in football history possess his combination of speed, skill, and finishing. Whether it's a World Cup final or a Champions League night, Mbappe consistently delivers on the biggest stages and remains one of the most unstoppable attackers in the game.

At number two is Cristiano Ronaldo. Even after two decades at the highest level, Ronaldo continues to break records and score goals. With over 900 career goals, multiple Champions League titles, league championships across different countries, and countless individual awards, his legacy as one of football's greatest icons is unquestionable.

And at number one is Lionel Messi. From winning countless league titles and Champions League trophies to finally lifting the FIFA World Cup, Messi has completed football's greatest collection of achievements. His vision, dribbling, passing, finishing, and ability to decide matches have made him a player unlike any other. For many fans, he is simply the greatest footballer of all time.

Choosing only five players is never easy. Stars like Kevin De Bruyne, Mohamed Salah, Vinicius Junior, Rodri, Harry Kane, and Lamine Yamal have all produced incredible performances and could easily be included in this conversation.

But based on consistency, influence, achievements, and overall impact on modern football, these five players stand above the rest and have defined this generation of the beautiful game."""

TITLE = "Every football generation has its legends"

print("=" * 70)
print("FULL PIPELINE AUDIT")
print("=" * 70)

# LAYER 1: Scene Chunking
print("\n--- LAYER 1: Scene Chunking ---")
from services.scene_splitter import _split_script_visual_units
scenes = _split_script_visual_units(SCRIPT)
print(f"  Split into {len(scenes)} scene fragments")
for i, s in enumerate(scenes[:7]):
    print(f"    F{i}: {s[:90]}...")
print(f"    ... ({len(scenes)} total)")

# LAYER 2: Section Detection
print("\n--- LAYER 2: Section Detection ---")
from services.semantic_query_agent import detect_list_sections
sections = detect_list_sections(list(scenes), TITLE)
distinct = list(dict.fromkeys(s.get("container_subject", "") for s in sections if s.get("container_subject")))
print(f"  Distinct sections: {distinct}")
print(f"  Count: {len(distinct)}")
print(f"  Is listicle: {len(distinct) >= 2}")
for i, s in enumerate(sections):
    subj = s.get("container_subject", "")[:30] or "(none)"
    m = "M" if s.get("has_explicit_marker") else " "
    intro = s.get("is_intro", False)
    status = "INTRO" if intro else m
    print(f"    [{i:2d}] {status:5s} '{subj}'")

# LAYER 3: Query Generation  
print("\n--- LAYER 3: Mechanical Query Generation ---")
from app import _run_mechanical_query_fallback
rows = _run_mechanical_query_fallback(SCRIPT, TITLE, list(scenes), silent=True)
print(f"  Generated {len(rows)} query rows")
for i, r in enumerate(rows[:12]):
    subj = r.get("section_subject", "")[:25] or "(none)"
    yt = r.get("youtube_query", "")[:75]
    print(f"    [{i:2d}] '{subj:25s}' -> '{yt}'")
print(f"    ... ({len(rows)} total)")

# Check section subject coverage
raw_subs = [r.get("section_subject", "") for r in rows]
missing = sum(1 for s in raw_subs if not s)
print(f"  Scenes with section_subject: {len(rows)-missing}/{len(rows)}")
print(f"  Scenes WITHOUT section_subject: {missing}/{len(rows)}")

# LAYER 4: Query Repair
print("\n--- LAYER 4: Query Repair ---")
from app import _repair_concept_topic_query_rows
repaired = _repair_concept_topic_query_rows(rows, TITLE, list(scenes))
print(f"  Repaired {len(repaired)} rows")
for i, r in enumerate(repaired[:12]):
    subj = r.get("section_subject", "")[:25] or "(none)"
    b = "B" if r.get("container_boundary") else " "
    yt = r.get("youtube_query", "")[:75]
    print(f"    [{i:2d}] {b} '{subj:25s}' -> '{yt}'")

# LAYER 5: Cross-Contamination
print("\n--- LAYER 5: Cross-Contamination ---")
contamination = 0
for i, r in enumerate(repaired):
    subj = (r.get("section_subject") or "").lower()
    yt = (r.get("youtube_query") or "").lower()
    if not subj:
        continue
    for j, r2 in enumerate(repaired):
        other = (r2.get("section_subject") or "").lower()
        if i == j or not other or other == subj:
            continue
        if other in yt:
            print(f"    FAIL Scene {i} '{subj}' mentions '{other}': {yt[:60]}")
            contamination += 1
if contamination == 0:
    print(f"  PASS: No cross-contamination across {len(repaired)} scenes")
else:
    print(f"  FAIL: {contamination} issues found")

# LAYER 6: Per-Section Summary
print("\n--- LAYER 6: Per-Section Summary ---")
from collections import defaultdict
sq = defaultdict(list)
for i, r in enumerate(repaired):
    subj = r.get("section_subject", "") or "(intro/no-section)"
    yt = r.get("youtube_query", "")
    sq[subj].append((i, yt))
for subj, items in sq.items():
    print(f"  [{subj}] - {len(items)} scenes:")
    for idx, q in items:
        print(f"    Scene{idx:2d}: {q[:75]}")

# LAYER 7: Domain Anchor in First Position
print("\n--- LAYER 7: Domain Anchor Pollution ---")
domain_terms = {"football", "legends", "generation", "era", "current", "top", "five"}
polluted = 0
for i, r in enumerate(repaired):
    subj = r.get("section_subject", "")
    if not subj:
        continue
    yt = r.get("youtube_query", "").lower()
    words = yt.split()
    if words and words[0] in domain_terms:
        print(f"    FAIL Scene {i}: domain '{words[0]}' is first word: {yt[:60]}")
        polluted += 1
if polluted == 0:
    print("  PASS: No domain terms as first word")
else:
    print(f"  FAIL: {polluted} issues")

# LAYER 8: Query Uniqueness
print("\n--- LAYER 8: Query Uniqueness ---")
from collections import Counter
queries = [r.get("youtube_query", "") for r in repaired]
dupes = {q: c for q, c in Counter(queries).items() if c > 1}
if dupes:
    print(f"  WARN: {sum(dupes.values()) - len(dupes)} duplicate queries")
    for q, c in list(dupes.items())[:5]:
        print(f"    x{c}: '{q[:60]}'")
else:
    print(f"  PASS: All {len(queries)} queries unique")

# LAYER 9: Section Boundary Placement
print("\n--- LAYER 9: Boundaries ---")
boundaries = [(i, r.get("section_subject","")) for i, r in enumerate(repaired) if r.get("container_boundary")]
print(f"  Boundaries at indices: {[b[0] for b in boundaries]}")
print(f"  Subjects: {list(dict.fromkeys(b[1] for b in boundaries))}")

# LAYER 10: Missing Sections
print("\n--- LAYER 10: Missing Section Audit ---")
expected = {"Erling Haaland", "Jude Bellingham", "Kylian Mbappe", "Cristiano Ronaldo", "Lionel Messi"}
found = {s.get("container_subject", "") for s in sections if s.get("container_subject")}
missing_sections = expected - found
if missing_sections:
    print(f"  FAIL: Missing sections: {missing_sections}")
else:
    print(f"  PASS: All 5 sections detected")

# LAYER 11: Non-player mentions (outro/other players)
print("\n--- LAYER 11: Outro Scene Check ---")
other_players = {"De Bruyne", "Salah", "Vinicius", "Rodri", "Kane", "Yamal"}
for i, r in enumerate(repaired):
    yt = r.get("youtube_query", "")
    for p in other_players:
        if p.lower() in yt.lower() and (r.get("section_subject") or "").lower() != p.lower():
            print(f"    INFO Scene {i}: outro scene mentions '{p}' in query")
            break

print("\n" + "=" * 70)
print("AUDIT COMPLETE")
print("=" * 70)
