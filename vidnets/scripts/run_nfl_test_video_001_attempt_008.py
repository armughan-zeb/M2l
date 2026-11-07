"""Run a fresh, separately archived 60-second NFL pipeline test.

The earlier NFL attempts are intentionally left untouched. This runner reuses
the established queue monitor and QA capture, while giving attempt 008 its own
script, generated package, and runtime logs.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import run_nfl_test_video_001 as runner


ATTEMPT_DIR = ROOT / "artifacts" / "nfl_test_001_20261002" / "attempts" / "attempt_008"
BOOT_LOG_DIR = ROOT / "artifacts" / "nfl_test_001_20261002" / "attempts" / "attempt_008_boot" / "logs"

runner.RECORD_DIR = ATTEMPT_DIR
runner.SERVER_LOG = BOOT_LOG_DIR / "server-runtime.log"
runner.SERVER_ERR_LOG = BOOT_LOG_DIR / "server-process-stderr.log"
runner.TITLE = "One Yard, One Read: The Play That Ended Super Bowl XLIX"
runner.OUTPUT_NAME = "nfl_test_001_super_bowl_xlix_attempt_008"
runner.SCRIPT = """One yard. Twenty-six seconds. One snap between Seattle and another title.

At Super Bowl XLIX, the Seahawks trail the Patriots, 28–24. Jermaine Kearse survives four bobbles on a catch, and Seattle reaches second-and-goal at New England’s one.

Marshawn Lynch waits behind Russell Wilson. Seattle spreads out. Wilson takes the snap and throws a quick slant to Ricardo Lockette. Cornerback Malcolm Butler reads it, cuts across the route, and intercepts the pass at the goal line. The game is over.

Undrafted, Butler had seen that play in practice. He recognized the route and attacked the passing lane.

Pete Carroll later said an incompletion would have left two more downs to run.

On February 1, 2015, Butler’s interception ended Seattle’s bid for back-to-back titles and delivered New England’s fourth championship.

One yard. One read. A split second rewrote the ending."""


if __name__ == "__main__":
    raise SystemExit(runner.main())
