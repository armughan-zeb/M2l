"""Offline replay of run-7 (job 6624244a) YT worker WITH the Worker A fix.

Real inputs: run-7 live scene texts/durations (scenes 0-10), the real
223-candidate search pool dumped from the live run, the real prep
manifest (7 banked cuts). Assignment, injection, ranged moments, and
the download scheduler all run for real.

Simulated (labelled, at network boundaries only):
- search: sleeps 55 s (measured live) then returns the dumped pool;
- transcripts: a stubbed transcript index sleeps min(60 s, the real
  ranged deadline) then returns canned documents for every requested
  video (optimistic: full availability after the capped wait);
- _download_clip: sleeps the measured live durations (44.25 / 71.79 /
  76.49 s round-robin) then copies a REAL banked prep cut, so the
  motion/overlay screening runs for real on real pixels.
- vision is NOT replayed (no visual_context): acceptance here is the
  deterministic download verification. Live proof (incl. vision) is
  the coordinator's job.

Reports: phase timings, downloads completed with time per download,
resolved count, replay accepted ratio. Run 7 live for comparison:
search ~55 s, assignment ~150-170 s, transcripts ~30 s (of a 120 s
cap), downloads 4/11 ready -- all 4 prep restores, 0 live downloads
before the 343 s branch wall; final YT 3/13 clips (23.1%).
"""

import copy
import json
import os
import shutil
import sys
import time

sys.path.insert(0, "/home/hatch/workspace/.worktrees/yt-live-download-volume/vidnets/server")
sys.path.insert(0, "/home/hatch/workspace/.worktrees/yt-live-download-volume/vidnets")

os.environ["VIDZEO_RENDER_GATE"] = "off"
os.environ["VIDZEO_YT_PER_CLIP_TIMEOUT"] = "66.7"  # run-7 stage value
os.environ["VIDZEO_PREP_ROOT"] = "/home/hatch/workspace/vidnets-runtime/temp/prep"
os.environ["VIDZEO_YT_CUT_CACHE_DIR"] = "/home/hatch/workspace/vidnets-runtime/temp/youtube/verified_cut_cache"
os.environ["VIDZEO_YT_PREP_MANIFEST_INJECTION"] = "1"

import services.youtube_pipeline_v2 as yt
from services.youtube_pipeline_v2 import get_youtube_clips
from services.youtube_transcript_index import TranscriptDocument, TranscriptSegment

OUT = "/home/hatch/workspace/vidnets-runtime/output/GTA_VI_1Min_20261119_085929/GTA_VI_1Min_20261119_085929_00f71a33"
TITLE = "Grand Theft Auto VI: Return to Vice City"
REAL_CUT = "/home/hatch/workspace/vidnets-runtime/temp/jobs/6624244a/6624244a-run-0001-780f5bcd38cf/yt_clip_scene_008.mp4"
# (11.8 s, 4.8 MB: a real run-7 source slice. The banked prep cuts are
# 3-10 s; the 71 MB run-7 master filled the 512 MB /tmp tmpfs in the
# first replay attempt, so the stub content is a real slice instead.
# Output goes to the worktree (home disk), not /tmp, for the same reason.)
MEASURED_LIVE_DURATIONS = [44.25, 71.79, 76.49]  # run 7 small worker + run 6

manifest = json.load(open(OUT + "/GTA_VI_1Min.manifest.json"))
TEXTS = [str(t) for t in manifest["source_scene_texts"]][:11]
DURATIONS = json.load(open("/tmp/run7_durations.json"))
POOL = json.load(open("/tmp/run7_pool.json"))
VID_TITLES = {}
for items in POOL.values():
    for cand in items:
        VID_TITLES.setdefault(str(cand.get("video_id") or ""), str(cand.get("title") or ""))

timings = {}
download_log = []
_download_counter = {"n": 0}
_download_counter_lock = __import__("threading").Lock()


def _timed_assign(*args, **kwargs):
    started = time.monotonic()
    result = yt._assign_candidates_orig(*args, **kwargs)
    timings["assign_wall"] = round(time.monotonic() - started, 1)
    return result


yt._assign_candidates_orig = yt._assign_candidates
yt._assign_candidates = _timed_assign


def _stub_search(*args, **kwargs):
    started = time.monotonic()
    time.sleep(55.0)  # measured live search time, run 7
    timings["search_wall"] = round(time.monotonic() - started, 1)
    return copy.deepcopy(POOL)


yt._search_container_pools = _stub_search


class _StubTranscriptIndex:
    """Canned transcripts: sleep the deadline-capped wait, then every
    requested video is available with segments carrying its title words
    (so the moment matcher can rank real moments)."""

    def __init__(self, session_jar=None):
        pass

    def fetch_many(self, video_ids, *, workers=None, deadline_seconds=None, force=False):
        wait = min(60.0, float(deadline_seconds or 0.0))
        time.sleep(max(0.0, wait))
        timings["transcript_wait"] = round(wait, 1)
        docs = {}
        for vid in dict.fromkeys(str(v) for v in video_ids if str(v).strip()):
            title = VID_TITLES.get(vid, "Grand Theft Auto VI gameplay")
            segments = []
            for start in range(0, 600, 10):
                segments.append(TranscriptSegment(
                    start=float(start), end=float(start + 10),
                    text=f"{title} gameplay footage scene",
                    normalized_text=f"{title} gameplay footage scene".lower(),
                ))
            docs[vid] = TranscriptDocument(
                video_id=vid, status="ready", segments=segments,
                source="replay-stub", fetched_at=time.time(),
            )
        return docs


import services.youtube_transcript_index as _yti
_yti.YouTubeTranscriptIndex = _StubTranscriptIndex


def _stub_download(candidate, output_path, start, duration, *args, **kwargs):
    vid = str(candidate.get("video_id") or candidate.get("id") or candidate.get("candidate_id") or "")
    with _download_counter_lock:
        # Thread-safe round-robin: the 5 download workers call this in
        # parallel, so the counter must advance under a lock (an unlocked
        # read raced in the first replay run and every call slept 44.25 s).
        sleep_for = MEASURED_LIVE_DURATIONS[_download_counter["n"] % len(MEASURED_LIVE_DURATIONS)]
        _download_counter["n"] += 1
    started = time.monotonic()
    time.sleep(sleep_for)
    shutil.copyfile(REAL_CUT, str(output_path))
    wall = round(time.monotonic() - started, 1)
    download_log.append({"video_id": vid, "sleep_seconds": sleep_for, "wall_seconds": wall})
    print(f"   [replay] download {vid}: slept {sleep_for}s, wall {wall}s", flush=True)
    return True


yt._download_clip = _stub_download
yt._download_youtube_probe = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("probe must not run"))

started = time.monotonic()
result = get_youtube_clips(
    script=" ".join(TEXTS),
    title=TITLE,
    num_scenes=len(TEXTS),
    scene_texts=list(TEXTS),
    scene_durations=list(DURATIONS),
    scene_query_overrides=[{"scene_subject": "Grand Theft Auto VI"}] * len(TEXTS),
    openrouter_api_key="",
    use_authenticated_session=False,
    output_dir="/home/hatch/workspace/.worktrees/yt-live-download-volume/replay_out",
    hard_timeout_seconds=325,
    download_concurrency=5,
)
total_wall = round(time.monotonic() - started, 1)
audit = getattr(result, "audit", {}) or {}
ranged = audit.get("ranged_moment_selection", {})
paths = [path for path in (result or []) if path]
report = {
    "total_wall_seconds": total_wall,
    "hard_timeout_seconds": 325,
    "search_wall_seconds": timings.get("search_wall"),
    "assign_wall_seconds": timings.get("assign_wall"),
    "transcript_wait_seconds": timings.get("transcript_wait"),
    "ranged_deadline_seconds": ranged.get("ranged_deadline_seconds"),
    "download_reserve_seconds": ranged.get("download_reserve_seconds"),
    "download_workers": audit.get("download_workers"),
    "downloads_attempted": len(download_log),
    "downloads": download_log,
    "resolved_scenes": len(paths),
    "requested_scenes": len(TEXTS),
    "replay_accepted_ratio_worker_scenes": round(len(paths) / len(TEXTS), 4) if TEXTS else 0,
    "replay_accepted_ratio_job_scenes_13": round(len(paths) / 13, 4),
    "run7_live_accepted_ratio_job_scenes_13": round(3 / 13, 4),
    "resolved": audit.get("resolved"),
    "resolution_layers": audit.get("resolution_layers"),
    "resolution_layer_by_scene": audit.get("resolution_layer_by_scene"),
}
print(json.dumps(report, indent=2))
with open("/home/hatch/workspace/.worktrees/yt-live-download-volume/replay_out/report.json", "w", encoding="utf-8") as handle:
    json.dump(report, handle, indent=2)
print("report: replay_out/report.json")
