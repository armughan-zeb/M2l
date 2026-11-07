"""Vidzeo Visual Intelligence Pipeline (VIP) — VidRush-style visual brain.

A standalone, opt-in intelligence layer that runs on top of the existing
media harvesters. Driven by Qwen 3.5 Flash Vision (qwen/qwen3.5-flash-02-23)
via OpenRouter — the native vision-language Flash tier with text+image+video
input and a 1M token context.

Design principles (per the VidRush specification):
  - The model gives "eyes"; the ranking engine gives "taste".
  - Never make Qwen edit videos. It only produces intelligence metadata.
  - Local system handles downloading, frame extraction, storage, rendering.
  - 3-layer cost optimization:
        Layer 1 — cheap CPU pre-filter (perceptual hash, resolution, aspect)
        Layer 2 — Qwen Flash vision analysis (scoring)
        Layer 3 — deep analysis on the top candidates only
  - CPU-only, 8GB-RAM friendly: batch size 1, frames deleted after analysis.

This package remains opt-in for the legacy pipeline. The web-image resolver
receives a bounded visual gate only when ``config['visual_understanding']`` is
ON, and the aligned media pass follows the same toggle. When it is OFF, the
existing pipeline runs untouched.

Public API:
    run_visual_intelligence_pass(media_plan, job_config, report_progress)
"""
from __future__ import annotations

from .models import ImageAnalysis, ClipAnalysis, RankedAsset, AssetType
from .vision_client import QwenVisionClient, VisionClientError
from .orchestrator_v2 import run_visual_intelligence_pass, VisualIntelligenceError
from .queue import VisualDecisionQueue, get_visual_queue
from .context import VisualDecisionContext, VisualBudgetExceeded
from .decision import SceneVisualDecision, OverlayRegion

__all__ = [
    "run_visual_intelligence_pass",
    "VisualIntelligenceError",
    "QwenVisionClient",
    "VisionClientError",
    "ImageAnalysis",
    "ClipAnalysis",
    "RankedAsset",
    "AssetType",
    "VisualDecisionQueue",
    "get_visual_queue",
    "VisualDecisionContext",
    "VisualBudgetExceeded",
    "SceneVisualDecision",
    "OverlayRegion",
]
