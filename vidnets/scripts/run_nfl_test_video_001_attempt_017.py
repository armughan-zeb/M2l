"""Run a fresh, preserved NFL pipeline attempt with bounded shared-source YT."""

from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
os.environ["VIDZEO_NFL_TEST_RECORD_DIR"] = str(
    ROOT / "artifacts" / "nfl_test_001_20261002" / "attempts" / "attempt_017"
)
os.environ["VIDZEO_NFL_TEST_ATTEMPT_NUMBER"] = "17"
os.environ["VIDZEO_NFL_TEST_OUTPUT_NAME"] = "nfl_test_001_super_bowl_xlix_attempt_017"
os.environ["VIDZEO_NFL_TEST_SERVER_LOG"] = str(
    ROOT / "logs" / "nfl_test_001_attempt_017_backend-runtime.log"
)
os.environ["VIDZEO_NFL_TEST_SERVER_ERR_LOG"] = str(
    ROOT / "logs" / "nfl_test_001_attempt_017_backend-stderr.log"
)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_nfl_test_video_001 as runner  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(runner.main())
