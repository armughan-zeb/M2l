"""Transfer timestamped YouTube cuts from the local PC via its CMS."""

import math
import os
import re
import subprocess
import time
from pathlib import Path

from .job_context import remaining_seconds

CLIP_WAIT_SECONDS = 235.0
CLIP_WORKERS = 4


def relay_branch_seconds(scene_count):
    """Price search/transcripts and parallel PC download plus tunnel waves."""
    return min(3000.0, max(720.0, 240.0 + math.ceil(max(1, scene_count) / CLIP_WORKERS) * CLIP_WAIT_SECONDS))


def download_pc_cut(job_id, video_id, output, start, duration, *, cancellation=None, deadline_at=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return False, "pc_clip_invalid_id"
    wait = min(CLIP_WAIT_SECONDS, remaining_seconds(CLIP_WAIT_SECONDS))
    if deadline_at is not None:
        wait = min(wait, deadline_at - time.monotonic())
    if wait <= 1.0:
        return False, "transport_timeout"
    if cancellation is not None:
        cancellation.raise_if_cancelled()

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Write to a staging name the branch's disk salvage will not recognize
    # until the transfer has been checked and moved to the candidate path.
    staging = output.with_name(f".pc-{output.name}.download")
    helper = Path(__file__).resolve().parents[3] / "lib" / "pcYoutubeClip.js"
    args = [os.environ.get("VIDZEO_NODE_PATH") or "node", str(helper), job_id, video_id,
            str(start), str(max(1.0, duration)), str(staging), str(wait)]
    end = time.monotonic() + wait
    process = None
    try:
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if cancellation is not None:
            cancellation.register_process(process)
        while True:
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            left = end - time.monotonic()
            if left <= 0:
                return False, "transport_timeout"
            try:
                _, error = process.communicate(timeout=min(0.5, left))
                break
            except subprocess.TimeoutExpired:
                continue
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        if process.returncode == 0 and staging.is_file() and staging.stat().st_size >= 4096:
            staging.replace(output)
            print("   [yt-v2] PC tunnel clip received", flush=True)
            return True, ""
        if "Connect YouTube on the local PC" in error:
            return False, "pc_youtube_session_unavailable"
        if "Local PC clip relay is unavailable" in error:
            return False, "pc_clip_relay_unavailable"
        return False, "pc_clip_unavailable"
    except OSError:
        return False, "pc_clip_unavailable"
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.communicate()
            if cancellation is not None:
                cancellation.unregister_process(process)
        staging.unlink(missing_ok=True)
        staging.with_name(staging.name + ".part").unlink(missing_ok=True)
