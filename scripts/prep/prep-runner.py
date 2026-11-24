#!/usr/bin/env python3
"""Advance-prep runner: pre-generate scripts + pre-fetch/verify media for
queued titles while the VM is idle.

Usage:
    VIDZEO_RENDER_GATE=off <venv python> scripts/prep/prep-runner.py [--once] [--loop SECONDS]
        [--title "Title" --duration 10] [--skip-web] [--skip-youtube] [--force] [--dry-run]

- Titles come from the local prep queue (default
  ~/workspace/M2l/work/prep-queue.json — the CMS exposes no peek endpoint,
  only claim; see docs/ADVANCE_PREP.md).  Override with VIDZEO_PREP_QUEUE.
- Before each title (and between prep stages) the idle gate is re-checked:
  server queue idle, no daemon job, inference pool pending == 0, no render
  running.  A job starting mid-prep aborts the current title cleanly.
- Never starves a live job: prep does no work at all while anything is busy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VIDNETS_SERVER = REPO_ROOT / "vidnets" / "server"

# Prep must load the SAME runtime .env the server loads (round 5: prep
# banked 0/51 web entries, 4th run). The server runs with VIDZEO_ROOT set
# (~/workspace/vidnets-runtime), so services/config.py loads
# VIDZEO_ROOT/.env — VIDZEO_UNIVERSAL_SCRAPER_TIMEOUT_SECONDS=300,
# VIDZEO_UNIVERSAL_LATE_WAIT_SECONDS=120,
# VIDZEO_UNIVERSAL_SCRAPER_WORKERS=12. A prep shell has no VIDZEO_ROOT,
# so config.py only loaded the repo .env (which has none of those vars)
# and prep ran on the code defaults: 8s engine timeout, 5s late wait,
# 6 scraper workers. Default VIDZEO_ROOT to the runtime dir (the same
# default advance_prep already uses for its temp root) when unset and
# present; an operator-set value still wins (setdefault), and the server
# is unaffected — it already sets VIDZEO_ROOT itself. This must happen
# before importing services, because config.py loads .env at import time.
_DEFAULT_RUNTIME_ROOT = Path.home() / "workspace" / "vidnets-runtime"
if not os.environ.get("VIDZEO_ROOT") and _DEFAULT_RUNTIME_ROOT.is_dir():
    os.environ.setdefault("VIDZEO_ROOT", str(_DEFAULT_RUNTIME_ROOT))

for _candidate in (VIDNETS_SERVER, VIDNETS_SERVER.parent):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from services import advance_prep as prep  # noqa: E402


def _log(message: str) -> None:
    print(f"[prep-runner] {message}", flush=True)


def _idle_or_log() -> bool:
    idle, reasons = prep.is_idle()
    if not idle:
        _log("not idle: " + "; ".join(reasons))
    return idle


def run_once(args) -> int:
    """One pass over the queue. Returns the number of titles prepped."""
    entries = prep.load_prep_queue()
    if args.title:
        entries = [{
            "title": args.title,
            "duration_minutes": float(args.duration or 10.0),
            "channel": args.channel or "",
            "description": args.description or "",
        }]
    if not entries:
        _log("prep queue is empty — nothing to do")
        return 0
    prepped = 0
    for entry in entries:
        title = entry["title"]
        duration = float(entry["duration_minutes"])
        manifest = prep.load_manifest(title, duration)
        if manifest.get("status") == "ready" and not args.force:
            _log(f"skip '{title}' ({duration:g} min): already ready")
            continue
        if not _idle_or_log():
            _log("stopping pass: VM is busy")
            break
        if args.dry_run:
            _log(f"would prep '{title}' ({duration:g} min)")
            continue
        _log(f"prepping '{title}' ({duration:g} min)")
        should_stop = lambda: not prep.is_idle()[0]  # noqa: E731
        manifest = prep.prep_title(
            entry,
            should_stop=should_stop,
            skip_youtube=args.skip_youtube,
            skip_web=args.skip_web,
        )
        status = manifest.get("status")
        _log(
            f"'{title}': {status} — script={'yes' if manifest.get('script') else 'no'}, "
            f"web {manifest.get('web', {}).get('verified', 0)}/{manifest.get('web', {}).get('attempted', 0)}, "
            f"youtube {manifest.get('youtube', {}).get('verified', 0)}/{manifest.get('youtube', {}).get('attempted', 0)}"
        )
        if status == "aborted":
            _log("aborted mid-prep: a job started; stopping pass")
            break
        prepped += 1
    return prepped


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="one pass, then exit (default)")
    parser.add_argument("--loop", type=float, default=0, help="keep looping, sleeping N seconds between passes")
    parser.add_argument("--title", default="", help="prep one ad-hoc title instead of the queue")
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--channel", default="")
    parser.add_argument("--description", default="")
    parser.add_argument("--skip-web", action="store_true")
    parser.add_argument("--skip-youtube", action="store_true")
    parser.add_argument("--force", action="store_true", help="re-prep titles already marked ready")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.loop and args.loop > 0:
        while True:
            try:
                run_once(args)
            except Exception as exc:
                _log(f"pass failed: {type(exc).__name__}: {exc}")
            _log(f"sleeping {args.loop:g}s")
            time.sleep(args.loop)
        return 0
    run_once(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
