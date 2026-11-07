"""Run the next preserved NFL regression attempt through the live app queue."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_nfl_test_video_001 as test_runner


ROOT = Path(__file__).resolve().parents[1]
test_runner.RECORD_DIR = (
    ROOT / "artifacts" / "nfl_test_001_20261002" / "attempts" / "attempt_011"
)
test_runner.OUTPUT_NAME = "nfl_test_001_super_bowl_xlix_attempt_011"
test_runner.ATTEMPT_NUMBER = 11


if __name__ == "__main__":
    try:
        raise SystemExit(test_runner.main())
    except Exception as exc:
        test_runner.RECORD_DIR.mkdir(parents=True, exist_ok=True)
        test_runner.append_event(test_runner.RECORD_DIR / "logs" / "queue_events.jsonl", {
            "event": "attempt_011_runner_error",
            "error_type": type(exc).__name__,
            "error": str(exc)[:3000],
        })
        test_runner.write_json(test_runner.RECORD_DIR / "runner_error.json", {
            "error_type": type(exc).__name__,
            "error": str(exc),
        })
        raise
