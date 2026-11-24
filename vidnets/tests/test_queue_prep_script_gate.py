"""Unit tests for the prep-script gate in the Flask queue path.

The advance-prep script store (services/advance_prep.find_prepped_script)
was reachable only from the Node script adapter (daemon/CMS path), so a
job dispatched via /api/queue/add with no pasted script failed at
_ensure_script_for_queue even when a ready script sat in the prep store
(Panama job 2521f7de). These tests prove the queue gate now checks the
store first, and that a miss preserves the original error.

No network, no live server, no render: the prep store is isolated under
tmp_path via VIDZEO_PREP_ROOT, and the gate is called directly.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

import app as server_app  # noqa: E402
from services import advance_prep as prep  # noqa: E402


TITLE = "How the Panama Canal Changed World Trade"
SCRIPT = (
    "Cross from the Pacific to the Caribbean in just two hours through the Panama Canal. "
    "Two oceans, two hours, one canal. "
    "That is the promise of this crossing, and the question of this film is what two hours "
    "between oceans does to world trade. "
) * 4  # 176 words, mirroring the prepped Panama narration


@pytest.fixture()
def prep_env(tmp_path, monkeypatch):
    """Isolate the prep root under tmp_path."""
    root = tmp_path / "prep-root"
    monkeypatch.setenv("VIDZEO_PREP_ROOT", str(root))
    return root


def _seed_script(title=TITLE, duration_minutes=1.0, script=SCRIPT):
    return prep.save_prepped_script(title, duration_minutes, script, {"engine": "scriptit"})


def _panama_dispatch(**overrides):
    """The exact shape of the failed Panama dispatch: title + duration, no script."""
    config = {"title": TITLE, "duration": "1min", "type": "long"}
    config.update(overrides)
    return config


# ---------------------------------------------------------------------------
# prep hit
# ---------------------------------------------------------------------------

def test_prep_hit_uses_stored_script(prep_env):
    _seed_script()
    result = server_app._ensure_script_for_queue(_panama_dispatch())
    assert result["script"] == SCRIPT.strip()
    assert result["_script_source"] == "prep-cache"
    assert result["_prep_script_path"] is not None
    assert result["_prep_script_path"].endswith("script.txt")


@pytest.mark.parametrize("duration", ["1min", "1 min", "1 minute", 60, "60 seconds"])
def test_prep_hit_across_duration_forms(prep_env, duration):
    """The dispatch duration is free-form; every form must resolve to 1.0 min."""
    _seed_script()
    result = server_app._ensure_script_for_queue(_panama_dispatch(duration=duration))
    assert result["script"] == SCRIPT.strip()


def test_prep_hit_with_duration_minutes_field(prep_env):
    _seed_script()
    result = server_app._ensure_script_for_queue(
        {"title": TITLE, "duration_minutes": 1, "type": "long"}
    )
    assert result["script"] == SCRIPT.strip()


def test_prep_hit_via_content_title(prep_env):
    """The queue worker normalizes content_title before the gate runs."""
    _seed_script()
    result = server_app._ensure_script_for_queue(
        {"content_title": TITLE, "duration": "1min", "type": "long"}
    )
    assert result["script"] == SCRIPT.strip()


def test_prep_hit_reports_progress(prep_env):
    _seed_script()
    seen = []

    def update_progress(percent, message):
        seen.append((percent, message))

    server_app._ensure_script_for_queue(_panama_dispatch(), update_progress=update_progress)
    assert seen, "expected a progress report on a prep hit"
    assert any("prep" in message.lower() for _pct, message in seen)


def test_prep_hit_case_insensitive_title(prep_env):
    _seed_script()
    result = server_app._ensure_script_for_queue(
        _panama_dispatch(title="how the panama canal changed world trade")
    )
    assert result["script"] == SCRIPT.strip()


# ---------------------------------------------------------------------------
# prep miss / old behaviour preserved
# ---------------------------------------------------------------------------

def test_prep_miss_keeps_old_behaviour(prep_env):
    with pytest.raises(ValueError, match="Paste a finished narration script"):
        server_app._ensure_script_for_queue(_panama_dispatch())


def test_no_title_still_errors(prep_env):
    _seed_script()
    with pytest.raises(ValueError, match="Paste a finished narration script"):
        server_app._ensure_script_for_queue({"duration": "1min", "type": "long"})


def test_wrong_duration_misses(prep_env):
    """The store key is (title, duration): a 10-min script is not a 1-min hit."""
    _seed_script(duration_minutes=10.0)
    with pytest.raises(ValueError, match="Paste a finished narration script"):
        server_app._ensure_script_for_queue(_panama_dispatch())


def test_unparseable_duration_still_errors(prep_env):
    _seed_script()
    with pytest.raises(ValueError, match="Paste a finished narration script"):
        server_app._ensure_script_for_queue(_panama_dispatch(duration="soon"))


def test_short_prepped_script_still_errors(prep_env):
    _seed_script(script="too short")
    with pytest.raises(ValueError, match="Paste a finished narration script"):
        server_app._ensure_script_for_queue(_panama_dispatch())


def test_supplied_script_wins_without_store_lookup(prep_env):
    """A pasted script keeps the original fast path; the store is not needed."""
    supplied = "A finished narration script, pasted verbatim by the dispatcher."
    result = server_app._ensure_script_for_queue(_panama_dispatch(script=supplied))
    assert result["script"] == supplied
    assert "_script_source" not in result


def test_supplied_script_wins_over_prepped_script(prep_env):
    """A pasted script is never overwritten by a prepped one."""
    _seed_script()
    supplied = "A finished narration script, pasted verbatim by the dispatcher."
    result = server_app._ensure_script_for_queue(_panama_dispatch(script=supplied))
    assert result["script"] == supplied
