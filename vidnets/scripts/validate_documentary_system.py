"""Run the documentary visual manager against a saved manifest and frames."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--frames", type=Path, default=None)
    parser.add_argument("--frame-prefix", default=None, help="Only inspect files beginning with this prefix in --frames.")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from server.services.editorial_director.visual_critic import audit
    from server.services.editorial_director.manager import manager_review

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    frame_paths = None
    if args.frames and args.frames.exists():
        pattern = f"{args.frame_prefix}*.jpg" if args.frame_prefix else "*.jpg"
        frame_paths = sorted(str(path) for path in args.frames.glob(pattern))
    report = audit(args.manifest, frame_paths)
    review = manager_review(manifest, report.get("frame_audit"))
    report["manager"] = review
    output = args.output or args.manifest.with_suffix(".critic.json")
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "manifest": str(args.manifest),
        "report": str(output),
        "grammar_sequence": report["manifest_audit"].get("grammar_sequence", []),
        "unique_grammars": report["manifest_audit"].get("unique_grammars", 0),
        "warnings": report["manifest_audit"].get("warnings", 0),
        "errors": report["manifest_audit"].get("errors", 0),
        "ready_for_long_render": review["ready_for_long_render"],
        "revision_instructions": review["revision_instructions"],
    }, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
