import os, sys, time, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Deterministic verification: keep the REAL pooling + assignment + distant-timestamp
# logic, but stub only the network search and the yt-dlp/ffmpeg download/cut calls.
from services import youtube_pipeline_v2 as ytv2
from services.youtube_pipeline_v2 import (
    parse_script_to_scenes, _build_scene_rows, _group_rows, _row_pool_key, _video_id,
)

SOURCES = []   # video_ids whose source was downloaded once
CUTS = []      # (video_id, start, scene_idx)


def _fake_source_once(video_id, url, cancel_event=None, max_end=0.0):
    if video_id:
        SOURCES.append(video_id)
    return f"cached/{video_id}.mp4"


def _fake_cut(source_path, start, duration, out_path, candidate, cancel_event=None):
    vid = _video_id(candidate)
    try:
        scene_idx = int(out_path.stem.split("_")[-1]) - 1
    except Exception:
        scene_idx = -1
    if vid:
        CUTS.append((vid, round(float(start), 1), scene_idx))
    return True


def _fake_score(candidate, subject, scene_text, query, context=None):
    # Force the first-listed candidate to win so each pool narrows to its best
    # video and reuses it for distant segments (download-once, cut-many).
    return float(candidate.get("priority", 100.0))


def _slug(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:6]


def _fake_pools(groups, cancel_event=None):
    pools = {}
    for key, rows in groups.items():
        n = len(rows)
        videos = []
        for i in range(max(2, min(3, n))):
            vid = f"vid_{_slug(key)}_{i}"
            videos.append({
                "id": vid,
                "video_id": vid,
                "webpage_url": f"https://www.youtube.com/watch?v={vid}",
                "title": f"{key} source {i}",
                "duration": 600.0 + i * 120.0,
                "priority": 100.0 - i * 5.0,   # videos[0] preferred
                "query_text": key,
            })
        pools[key] = videos
    # Deliberately share one video across two pools to prove cross-pool reuse
    # (the download phase groups by video_id, so it is fetched only once).
    keys = list(pools.keys())
    if len(keys) >= 2:
        shared = f"vid_shared_crosspool"
        pools[keys[0]][0]["id"] = shared
        pools[keys[0]][0]["video_id"] = shared
        pools[keys[1]][0]["id"] = shared
        pools[keys[1]][0]["video_id"] = shared
    return pools


ytv2._search_container_pools = _fake_pools
ytv2._score_candidate = _fake_score
ytv2._download_source_once = _fake_source_once
ytv2._cut_local_segment = _fake_cut

# ---- a listicle: 5 sections, 3 scenes each = 15 scenes ----
games = [
    "Red Dead 2", "Zelda", "Witcher 3", "Elden Ring", "GTA 5",
]
lines = []
for g in games:
    lines += [
        f"{g} is one of the most ambitious open world games ever created.",
        f"In {g} the map is packed with detail and side activities.",
        f"Fans still replay {g} years after its release.",
    ]
script = "\n".join(lines)
title = "Top 5 Best Open World Games of All Time"

rows0 = parse_script_to_scenes(script, title, "", 15)
scene_texts = [r.get("description") or "" for r in rows0][:15]
num = len(scene_texts)
durations = [3.3] * num

overrides = []
for g in games:
    for _ in range(3):
        overrides.append({"section_subject": g, "scene_subject": g, "subject_query": g})
overrides = overrides[:num]

t0 = time.time()
results = ytv2.get_youtube_clips(
    script=script, title=title, num_scenes=num, scene_durations=durations,
    scene_texts=scene_texts, scene_query_overrides=overrides,
    topic_anchor="open world games",
)
print(f"\npipeline took {time.time()-t0:.1f}s; resolved clips: {sum(1 for r in results if r)}")

# Map scene_idx -> section (pool) using the REAL row builder.
rows = _build_scene_rows(title, scene_texts, overrides, topic_anchor="open world games")
sec_of = {r["scene_index"]: (r.get("section_subject") or r.get("candidate_subject") or "?") for r in rows}

from collections import defaultdict
by_sec = defaultdict(lambda: defaultdict(list))
for vid, start, sidx in CUTS:
    by_sec[sec_of.get(sidx, "?")][vid].append(start)

print(f"\n=== POOL / SECTION VERIFICATION ===")
print(f"distinct source videos downloaded (once each): {len(set(SOURCES))}")
print(f"total distant segments cut: {len(CUTS)}\n")
for sec in by_sec:
    vids = by_sec[sec]
    total = sum(len(v) for v in vids.values())
    print(f"SECTION: {sec}  ({len(vids)} distinct video(s), {total} segment(s))")
    for vid, starts in vids.items():
        print(f"   video {vid}: {len(starts)} distant clip(s) at start(s) {starts}s")
