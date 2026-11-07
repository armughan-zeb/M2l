import math
import time
import threading
from concurrent.futures import Future, ThreadPoolExecutor
import pytest

from services.visual_intelligence.context import VisualDecisionContext, VisualBudgetExceeded
from services.real_media_service import (
    MediaItem,
    MediaConfig,
    _stock_candidate_passes_accuracy_gate,
)


def test_visual_decision_context_refresh_deadline():
    """Verify refresh_deadline updates deadline_at and clears degraded state."""
    past_deadline = time.monotonic() - 50.0
    context = VisualDecisionContext(
        enabled=True,
        api_key="test-key",
        model="qwen/test-model",
        scene_count=5,
        max_calls=10,
        budget_usd=1.0,
        deadline_at=past_deadline,
    )
    # Trigger deadline expiry
    with pytest.raises(VisualBudgetExceeded):
        context._reserve_call()
    assert context.degraded_reason == "visual_deadline_exceeded"
    assert context.get_client() is None

    # Refresh deadline with finite seconds
    context.refresh_deadline(deadline_seconds=300.0)
    assert context.degraded_reason == ""
    assert context.deadline_at > time.monotonic() + 250.0

    # Call _reserve_call successfully
    context._reserve_call()
    assert context.calls == 1

    # Refresh deadline with float('inf')
    context.refresh_deadline(deadline_at=float("inf"))
    assert math.isinf(context.deadline_at)


def test_windows_infinite_timeout_semaphore_acquire():
    """Verify _acquire_before_deadline does not raise OverflowError on Windows with infinite deadline."""
    context = VisualDecisionContext(
        enabled=True,
        api_key="test-key",
        model="qwen/test-model",
        scene_count=5,
        max_calls=10,
        budget_usd=1.0,
        deadline_at=float("inf"),
    )
    sem = threading.BoundedSemaphore(1)
    # Should acquire without raising OverflowError: timestamp out of range for platform time_t
    context._acquire_before_deadline(sem)
    sem.release()


def test_windows_infinite_timeout_future_result_pattern():
    """Verify Future.result invocation with sanitized inf pattern does not crash on Windows."""
    future = Future()
    future.set_result("success")
    deadline = float("inf")
    remaining = float("inf")

    if math.isinf(deadline) or math.isinf(remaining):
        res = future.result()
    else:
        res = future.result(timeout=remaining)

    assert res == "success"


def test_stock_candidate_passes_accuracy_gate_forbidden_subject():
    """Verify _stock_candidate_passes_accuracy_gate rejects candidates matching forbidden_subjects."""
    config = MediaConfig(min_score_threshold=30.0)
    candidate = MediaItem(
        id="pixabay_123",
        source="pixabay",
        type="image",
        title="The Frog Prince",
        description="Fairy tale illustration with a frog",
        tags=["frog", "prince", "fairytale"],
        download_url="https://example.com/frog.jpg",
        preview_url="https://example.com/frog_thumb.jpg",
        width=1920,
        height=1080,
        score=75.0,
        score_breakdown={"relevance": 10.0},
    )
    scene = {
        "visual_requirement": {
            "canonical_entity": "Prince Harry",
            "forbidden_subjects": ("frog", "toad", "harry potter"),
        }
    }

    passed, reason = _stock_candidate_passes_accuracy_gate(candidate, config, scene=scene)
    assert not passed
    assert "forbidden_subject_metadata:frog" in reason or "non_photographic_metadata" in reason


def test_stock_candidate_passes_accuracy_gate_non_photographic():
    """Verify _stock_candidate_passes_accuracy_gate rejects statues and toys."""
    config = MediaConfig(min_score_threshold=30.0)
    candidate = MediaItem(
        id="pexels_456",
        source="pexels",
        type="image",
        title="Marble bust statue of prince",
        description="Ancient marble statue in gallery",
        tags=["statue", "prince", "museum"],
        download_url="https://example.com/statue.jpg",
        preview_url="https://example.com/statue_thumb.jpg",
        width=1920,
        height=1080,
        score=65.0,
        score_breakdown={"relevance": 8.0},
    )
    scene = {
        "visual_requirement": {
            "canonical_entity": "Prince Harry",
        }
    }

    passed, reason = _stock_candidate_passes_accuracy_gate(candidate, config, scene=scene)
    assert not passed
    assert "non_photographic_metadata" in reason


def test_stock_candidate_passes_accuracy_gate_valid_item():
    """Verify _stock_candidate_passes_accuracy_gate accepts clean photographic stock items."""
    config = MediaConfig(min_score_threshold=30.0)
    candidate = MediaItem(
        id="pexels_789",
        source="pexels",
        type="image",
        title="Buckingham Palace London",
        description="Front gates of Buckingham Palace during changing of guard",
        tags=["palace", "london", "royal", "england"],
        download_url="https://example.com/palace.jpg",
        preview_url="https://example.com/palace_thumb.jpg",
        width=1920,
        height=1080,
        score=80.0,
        score_breakdown={"relevance": 12.0},
    )
    scene = {
        "visual_requirement": {
            "canonical_entity": "King Charles III",
            "forbidden_subjects": ("frog", "harry potter"),
        }
    }

    passed, reason = _stock_candidate_passes_accuracy_gate(candidate, config, scene=scene)
    assert passed
    assert reason == "passed"
