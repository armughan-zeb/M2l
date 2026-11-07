"""content_database.json persistence for the Visual Intelligence Pipeline.

Writes a timestamped audit manifest with every analyzed asset, its scores,
verdict, and the model used. Lives next to the existing web_image audit
manifests so the rest of the UI/admin tooling can discover it.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _default_output_dir() -> Path:
    base = Path(os.environ.get("VIDZEO_ROOT", str(Path(__file__).resolve().parents[3])))
    out = base / "temp" / "visual_intelligence"
    out.mkdir(parents=True, exist_ok=True)
    return out



def write_content_database(
    records: List[Dict[str, Any]],
    *,
    job_name: str = "video",
    model: str = "",
    summary: Optional[Dict[str, Any]] = None,
    output_dir: Optional[str] = None,
) -> str:
    """Persist the ranked asset database. Returns the manifest path."""
    out_dir = Path(output_dir) if output_dir else _default_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in (job_name or "video")[:48]).strip("_") or "video"
    path = out_dir / f"content_database_{safe_name}_{ts}.json"
    payload = {
        "schema": "visual_intelligence/1",
        "generated_at": ts,
        "model": model,
        "summary": summary or {},
        "assets": records,
    }
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
    except Exception:
        # Best-effort persistence; never fail the pipeline on audit write.
        pass
    return str(path)


def build_summary(verdicts: List[str], vision_available: bool, model: str) -> Dict[str, Any]:
    keep = sum(1 for v in verdicts if v == "keep")
    replace = sum(1 for v in verdicts if v == "replace")
    emergency = sum(1 for v in verdicts if v == "emergency")
    return {
        "total": len(verdicts),
        "kept": keep,
        "replaced": replace,
        "emergency": emergency,
        "vision_available": vision_available,
        "model": model,
    }