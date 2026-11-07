"""Run a fresh, recovery-friendly NFL generation and preserve every log."""

from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
RECORD_DIR = ROOT / "artifacts" / "nfl_test_001_20261002" / "attempts" / "attempt_018"
LOG_PREFIX = ROOT / "logs" / "nfl_test_001_attempt_018_backend"

os.environ["VIDZEO_NFL_TEST_RECORD_DIR"] = str(RECORD_DIR)
os.environ["VIDZEO_NFL_TEST_ATTEMPT_NUMBER"] = "18"
os.environ["VIDZEO_NFL_TEST_OUTPUT_NAME"] = "nfl_test_001_super_bowl_xlix_attempt_018"
os.environ["VIDZEO_NFL_TEST_SERVER_LOG"] = str(LOG_PREFIX.with_name(LOG_PREFIX.name + "-runtime.log"))
os.environ["VIDZEO_NFL_TEST_SERVER_ERR_LOG"] = str(LOG_PREFIX.with_name(LOG_PREFIX.name + "-stderr.log"))

# Keep research and editing grounded while letting the pipeline recover a
# missing scene through relevant stock, a labeled documentary graphic, or an
# explicitly marked emergency rescue. No source timeout can hold the job open
# indefinitely, and a YouTube miss does not block the other media lanes.
os.environ["VIDZEO_NFL_STRICT_RETRIEVAL"] = "false"
os.environ["VIDZEO_NFL_FAIL_ON_UNRESOLVED"] = "false"
os.environ["VIDZEO_NFL_ALLOW_EMERGENCY_MEDIA"] = "true"
os.environ["VIDZEO_NFL_YT_COVERAGE_TARGET"] = "0.20"
os.environ["VIDZEO_NFL_YT_WALL_TIMEOUT_SECONDS"] = "120"
os.environ["VIDZEO_NFL_YT_SOURCE_DEADLINE_SECONDS"] = "60"
os.environ["VIDZEO_NFL_YT_CANDIDATE_LIMIT"] = "4"
os.environ["VIDZEO_NFL_YT_ATTEMPTS_PER_SCENE"] = "2"
os.environ["VIDZEO_NFL_TTS_MODE"] = "Use Audiogram"
os.environ["VIDZEO_NFL_TTS_MODEL"] = "audiogram"
os.environ["VIDZEO_NFL_VOICE"] = "Adam (Male US)"

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_nfl_test_video_001 as runner  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(runner.main())
