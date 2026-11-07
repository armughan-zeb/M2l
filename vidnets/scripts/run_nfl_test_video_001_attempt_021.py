"""Run a clean NFL generation using the known-responsive director route."""

from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
RECORD_DIR = ROOT / "artifacts" / "nfl_test_001_20261002" / "attempts" / "attempt_021"
LOG_PREFIX = ROOT / "logs" / "nfl_test_001_attempt_021_backend"

os.environ["VIDZEO_NFL_TEST_RECORD_DIR"] = str(RECORD_DIR)
os.environ["VIDZEO_NFL_TEST_ATTEMPT_NUMBER"] = "21"
os.environ["VIDZEO_NFL_TEST_OUTPUT_NAME"] = "nfl_test_001_super_bowl_xlix_attempt_021"
os.environ["VIDZEO_NFL_TEST_SERVER_LOG"] = str(LOG_PREFIX.with_name(LOG_PREFIX.name + "-runtime.log"))
os.environ["VIDZEO_NFL_TEST_SERVER_ERR_LOG"] = str(LOG_PREFIX.with_name(LOG_PREFIX.name + "-stderr.log"))

# Keep the pipeline permissive about rescue media while retaining its existing
# relevance/vision checks. Distant moments from one relevant NFL source may be
# reused when distinct clips cannot be acquired.
os.environ["VIDZEO_NFL_STRICT_RETRIEVAL"] = "false"
os.environ["VIDZEO_NFL_FAIL_ON_UNRESOLVED"] = "false"
os.environ["VIDZEO_NFL_ALLOW_EMERGENCY_MEDIA"] = "true"
os.environ["VIDZEO_NFL_YT_COVERAGE_TARGET"] = "0.20"
os.environ["VIDZEO_NFL_YT_WALL_TIMEOUT_SECONDS"] = "150"
os.environ["VIDZEO_NFL_YT_SOURCE_DEADLINE_SECONDS"] = "75"
os.environ["VIDZEO_NFL_YT_CANDIDATE_LIMIT"] = "6"
os.environ["VIDZEO_NFL_YT_ATTEMPTS_PER_SCENE"] = "3"
os.environ["VIDZEO_NFL_TTS_MODE"] = "Use Audiogram"
os.environ["VIDZEO_NFL_TTS_MODEL"] = "audiogram"
os.environ["VIDZEO_NFL_VOICE"] = "Adam (Male US)"

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_nfl_test_video_001 as runner  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(runner.main())

