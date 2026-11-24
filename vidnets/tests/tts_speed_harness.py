"""Timed harness: narration speed before vs after the server-speed fix.

Runs a fresh ~2,711-word script (same size as live job 26fa018b) through
_generate_linux_tts_chunked twice, both with a cold chunk cache:

  BEFORE — the old shape: default ~360-char splitter (~56 chunks) at 2
           workers (the live daemon's shape: no VIDZEO_LINUX_TTS_WORKERS
           set, 56 chunks, ~5s each, ~280s wall on 2026-11-21).
  AFTER  — the new shape: balanced ~1,600-char chunks (<=8) at 4 workers.

Usage (from vidnets/):
    VIDZEO_RENDER_GATE=off python3 tests/tts_speed_harness.py

Prints wall time, audio duration, chunk count and progress events for
each run. Exits non-zero unless AFTER is under 100s and faster than BEFORE.
Then runs a ~5,500-word long script (after-shape only) to prove the
chunking scales: bounded chunk size, one progress event per chunk, no
timeout failure. This calls the real Muse TTS CLI, so it takes minutes
and needs network; it is intentionally NOT a pytest test.
"""

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services import edge_tts_service as tts

WORDS = 2711
LONG_WORDS = 5422


def build_script(seed: int, words: int = WORDS) -> str:
    """~`words` words of UNIQUE sentences (fresh text => cold TTS cache).

    Every sentence carries its own index and a distinct detail, so no
    two chunks share text and the chunk cache cannot short-circuit the
    measurement (the first harness run showed repeated sentences being
    served from cache).
    """
    subjects = [
        "harbor master", "lighthouse keeper", "tide watcher", "gull counter",
        "sail maker", "compass reader", "anchor smith", "storm glass keeper",
        "beacon tender", "channel pilot", "dune walker", "current mapper",
        "reef diver", "fog bell ringer",
    ]
    verbs = [
        "charted", "measured", "logged", "surveyed", "recorded", "mapped",
        "tracked", "noted", "sketched", "counted", "timed", "weighed",
    ]
    details = [
        "the morning tide", "the evening swell", "the northern current",
        "the harbor mouth", "the cliff path", "the salt marsh",
        "the outer reef", "the ferry lane", "the fish market",
        "the boat shed", "the signal tower", "the breakwater",
    ]
    sentences = []
    count = 0
    i = 0
    while count < words:
        # The seed shifts the day number and rotates the picks, so each
        # seed yields different text (a cold TTS cache). A seed that
        # only changed the loop bound produced identical text and the
        # before-run was served from the server-side cache in 18s.
        sentence = (
            f"On day {seed * 1000 + i + 1}, the {subjects[(i + seed) % len(subjects)]} "
            f"{verbs[(i * 5 + seed) % len(verbs)]} {details[(i * 7 + seed) % len(details)]} "
            f"while the wind shifted over the water and the light changed "
            f"across the bay in a way nobody aboard had quite seen before."
        )
        sentence_words = sentence.split()
        sentences.append(sentence)
        count += len(sentence_words)
        i += 1
    return " ".join(sentences)


def _max_tts_rss_mb(stop_event, out):
    """Sample peak RSS of live tts CLI processes while a run is in flight."""
    import subprocess
    peak = 0.0
    while not stop_event.is_set():
        try:
            ps = subprocess.run(
                ["ps", "-eo", "rss,comm"], capture_output=True, text=True, timeout=5
            )
            for line in ps.stdout.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == "tts":
                    peak = max(peak, int(parts[0]) / 1024.0)
        except Exception:
            pass
        stop_event.wait(0.5)
    out["peak_mb"] = peak


def run_once(label, script, *, old_shape, tmp_root):
    tmp_dir = Path(tmp_root) / label
    tmp_dir.mkdir(parents=True, exist_ok=True)
    original_temp = tts.TEMP_DIR
    tts.TEMP_DIR = tmp_dir
    events = []
    stop = threading.Event()
    rss = {}
    sampler = threading.Thread(target=_max_tts_rss_mb, args=(stop, rss), daemon=True)
    try:
        if old_shape:
            os.environ["VIDZEO_LINUX_TTS_WORKERS"] = "2"
            real_split = tts._split_script_for_parallel_tts
            tts._split_script_for_parallel_tts = lambda s, **kw: real_split(s)
        else:
            os.environ.pop("VIDZEO_LINUX_TTS_WORKERS", None)
        sampler.start()
        start = time.monotonic()
        path, duration = tts._generate_linux_tts_chunked(
            script,
            progress_callback=lambda frac, msg: events.append(
                (round(time.monotonic() - start, 1), round(frac, 2), msg)),
        )
        wall = time.monotonic() - start
    finally:
        stop.set()
        sampler.join(timeout=5)
        if old_shape:
            tts._split_script_for_parallel_tts = real_split
        tts.TEMP_DIR = original_temp
    chunks = len(tts._split_script_for_parallel_tts(script)) if old_shape else len(
        tts._split_script_for_parallel_tts(
            script, target_chunk_chars=tts._LINUX_TTS_TARGET_CHUNK_CHARS,
            max_chunks=tts._LINUX_TTS_MAX_CHUNKS))
    print(f"\n[{label}] wall={wall:.1f}s audio={duration:.1f}s chunks={chunks} "
          f"peak_tts_rss={rss.get('peak_mb', 0):.0f} MB")
    print(f"[{label}] progress events: {len(events)} "
          f"(first={events[0] if events else None}, last={events[-1] if events else None})")
    return wall, duration, len(events), chunks


def main():
    # Seed 11: fresh sentences the TTS service has never seen. The
    # server-side TTS caches by text, so reusing seed 7 (already
    # synthesized in earlier runs) made the before-run finish in 18s
    # on warm cache instead of the true ~150-225s.
    script = build_script(11)
    print(f"script: {len(script.split())} words, {len(script)} chars")
    # Temp audio lives on disk, not /tmp: /tmp is a 512 MB tmpfs and a
    # 42-chunk narration needs ~90 MB there (decoded wavs + final wav),
    # which fails the concat with ffmpeg rc=228 (ENOSPC) when pytest
    # basetemps fill it. The live server already uses the disk TEMP_DIR.
    tmp_root_base = Path.home() / "workspace" / ".tts-harness-tmp"
    tmp_root_base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tts-speed-", dir=str(tmp_root_base)) as tmp_root:
        before = run_once("before", script, old_shape=True, tmp_root=tmp_root)
        after = run_once("after", script, old_shape=False, tmp_root=tmp_root)
    print(f"\nBEFORE: {before[0]:.1f}s  AFTER: {after[0]:.1f}s  "
          f"speedup: {before[0] / max(after[0], 0.1):.1f}x")
    ok = after[0] < 100.0 and after[0] < before[0]
    print("PASS" if ok else "FAIL: after-run not under 100s or not faster")
    if not ok:
        return 1

    # Long-job scaling: a ~5,500-word script (would have been ~80 old
    # 360-char chunks). The 100s bar applies to the 2,700-word script
    # above; here the bar is that the chunking scales (bounded chunk
    # size, one progress event per chunk, no timeout failure) instead
    # of collapsing into a few giant chunks or stalling.
    long_script = build_script(11, words=LONG_WORDS)
    long_chunks = tts._split_script_for_parallel_tts(
        long_script,
        target_chunk_chars=tts._LINUX_TTS_TARGET_CHUNK_CHARS,
        max_chunks=tts._LINUX_TTS_MAX_CHUNKS,
    )
    max_chunk = max(len(chunk) for chunk in long_chunks)
    print(f"\nlong script: {len(long_script.split())} words, "
          f"{len(long_script)} chars -> {len(long_chunks)} chunks, "
          f"max {max_chunk} chars")
    if max_chunk > 2200:
        print(f"FAIL: long-script chunk too large ({max_chunk} chars)")
        return 1
    tmp_root_base = Path.home() / "workspace" / ".tts-harness-tmp"
    tmp_root_base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tts-speed-long-", dir=str(tmp_root_base)) as tmp_root:
        long_after = run_once("long-after", long_script, old_shape=False,
                              tmp_root=tmp_root)
    long_ok = long_after[2] == len(long_chunks) and long_after[3] == len(long_chunks)
    print(f"LONG: {long_after[0]:.1f}s for {len(long_chunks)} chunks, "
          f"{long_after[2]} progress events")
    print("LONG PASS" if long_ok else "LONG FAIL: progress events != chunks")
    return 0 if long_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
