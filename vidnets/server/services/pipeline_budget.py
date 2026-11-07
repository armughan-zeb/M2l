from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from typing import Dict


def detect_logical_cores() -> int:
    detected = None

    try:
        result = subprocess.run(
            ["wmic", "cpu", "get", "NumberOfLogicalProcessors"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        values = [
            int(line.strip())
            for line in (result.stdout or "").splitlines()
            if line.strip().isdigit()
        ]
        if values:
            detected = max(values)
    except Exception:
        pass

    if detected is None or detected < 1:
        try:
            detected = int(os.environ.get("NUMBER_OF_PROCESSORS", "0") or "0")
        except Exception:
            detected = None

    if detected is None or detected < 1:
        detected = os.cpu_count() or 2

    return max(1, int(detected))


@lru_cache(maxsize=8)
def _budget_for_cores(logical_cores: int) -> Dict[str, int]:
    logical_cores = max(1, int(logical_cores or 1))
    if logical_cores >= 16:
        return {
            "logical_cores": logical_cores,
            "whisper_workers": 8,
            "web_scene_workers": 64,
            "stock_scene_workers": 32,
            "render_workers": 6,
            "per_web_host_limit": 16,
            "per_stock_provider_limit": 10,
        }
    if logical_cores >= 8:
        return {
            "logical_cores": logical_cores,
            "whisper_workers": 8,
            "web_scene_workers": 48,
            "stock_scene_workers": 24,
            "render_workers": 4,
            "per_web_host_limit": 14,
            "per_stock_provider_limit": 8,
        }
    return {
        "logical_cores": logical_cores,
        "whisper_workers": 3,
        "web_scene_workers": 32,
        "stock_scene_workers": 16,
        "render_workers": 2,
        "per_web_host_limit": 10,
        "per_stock_provider_limit": 6,
    }


def get_pipeline_budget(logical_cores: int | None = None) -> Dict[str, int]:
    detected_cores = detect_logical_cores() if logical_cores is None else max(1, int(logical_cores))
    return dict(_budget_for_cores(detected_cores))
