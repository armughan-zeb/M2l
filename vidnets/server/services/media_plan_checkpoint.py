"""Reuse a completed media coordinator result when a job resumes.

Launch-day job a7f52c58 (Nov 19, 2026) paused on "Processing time limit
reached" at 64% AFTER its media coordinator had completed (53 min 38 s),
then attempt 2 re-ran the whole media stage from scratch (47 min 26 s).
Combined media wall ~1 h 41 m of a 2 h 14 m job — pure rework.

The completed coordinator result is already on disk: the pipeline writes
``checkpoints/media_plan.json`` (stage ``media_ready``) the moment the
media future completes, before validation/render. A resume re-queues the
whole pipeline, but nothing ever read that checkpoint back — audio has
``load_saved_audio_plan``; media had no equivalent. This module is that
equivalent.

Safety: reuse only when the saved plan is complete AND valid for THIS
job/attempt. The checkpoint lives in the job's own output package, so it
is already job-scoped; on top of that we require the saved scene texts to
match the current scene texts exactly (same script/scene split), every
resolved asset to still exist on disk, and the coordinator's terminal
accounting to show every scene accepted with none unresolved. Anything
missing, partial, or stale falls back to re-running media — never ship a
mismatched plan.
"""

import json
from pathlib import Path


def _normalize_scene_text(value):
    """Collapse whitespace and case so equivalent scene texts compare equal."""
    return " ".join(str(value or "").split()).casefold()


def validate_saved_media_plan(plan, scene_texts):
    """Check a loaded media plan is complete and valid for these scenes.

    Returns ``(True, reason)`` when the plan may be reused, otherwise
    ``(False, reason)`` naming the first failed check. Never raises.
    """
    try:
        expected = [_normalize_scene_text(text) for text in (scene_texts or [])]
        if not expected:
            return False, "no current scene texts to match against"
        if not isinstance(plan, dict):
            return False, "saved media plan is not a JSON object"
        media_paths = plan.get("media_paths")
        source_labels = plan.get("source_labels")
        if not isinstance(media_paths, list) or not isinstance(source_labels, list):
            return False, "saved media plan has no media_paths/source_labels lists"
        if len(media_paths) != len(expected) or len(source_labels) != len(expected):
            return False, (
                f"saved media plan covers {len(media_paths)} scene(s), "
                f"current job has {len(expected)}"
            )
        if any(not isinstance(path, str) or not path.strip() for path in media_paths):
            return False, "saved media plan has an empty media path"
        if any(not isinstance(label, str) or not label.strip() for label in source_labels):
            return False, "saved media plan has an empty source label"
        metadata = plan.get("metadata")
        if not isinstance(metadata, dict):
            return False, "saved media plan has no metadata"
        saved_texts = metadata.get("source_scene_texts")
        if not isinstance(saved_texts, list) or len(saved_texts) != len(expected):
            return False, "saved media plan has no matching source_scene_texts"
        if [_normalize_scene_text(text) for text in saved_texts] != expected:
            return False, "saved media plan scene texts differ from the current scenes"
        # Job scoping comes from the checkpoint path itself: the file lives
        # in this job's own output package, and the scene-text match above
        # already rejects a plan written for a different script/title.
        terminal = metadata.get("terminal_accounting")
        rows = metadata.get("media_resolution_v2")
        if isinstance(terminal, dict):
            accepted = terminal.get("accepted")
            unresolved = terminal.get("unresolved", 0)
            if accepted is not None and int(accepted) != len(expected):
                return False, (
                    f"saved media plan accepted {accepted}/{len(expected)} scenes"
                )
            if int(unresolved or 0) != 0:
                return False, (
                    f"saved media plan leaves {unresolved} scene(s) unresolved"
                )
        elif isinstance(rows, list):
            # No terminal accounting: the resolution rows must prove every
            # scene accepted instead.
            if len(rows) != len(expected) or any(
                not isinstance(row, dict) or row.get("status") != "accepted"
                for row in rows
            ):
                return False, "saved media plan resolution rows are not all accepted"
        else:
            return False, "saved media plan has no terminal accounting or resolution rows"
        if isinstance(rows, list):
            if len(rows) != len(expected):
                return False, (
                    f"saved media plan has {len(rows)} resolution row(s) "
                    f"for {len(expected)} scene(s)"
                )
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    return False, f"saved media plan resolution row {index} is not an object"
                if row.get("status") != "accepted":
                    return False, (
                        f"saved media plan scene {index} status "
                        f"is {row.get('status')!r}, not 'accepted'"
                    )
                selected = row.get("selected_path")
                if selected is not None and selected != media_paths[index]:
                    return False, (
                        f"saved media plan scene {index} resolution path "
                        "does not match media_paths"
                    )
        # Every resolved asset must still exist on disk (previous attempt's
        # run temp dir may have been cleaned since the pause). A missing
        # file means re-run media; never render a dangling path.
        missing = [
            path for path in media_paths if not Path(path).is_file()
        ]
        if missing:
            return False, f"{len(missing)} saved media asset(s) no longer on disk"
        empty = [
            path for path in media_paths
            if Path(path).is_file() and Path(path).stat().st_size == 0
        ]
        if empty:
            return False, f"{len(empty)} saved media asset(s) are empty files"
        return True, (
            f"completed media plan for {len(expected)} scene(s), "
            "all assets on disk"
        )
    except (TypeError, ValueError, OSError):
        return False, "saved media plan failed validation"


def load_saved_media_plan(package_dir, scene_texts):
    """Load and validate ``checkpoints/media_plan.json`` for a resume.

    Returns ``(plan, reason)``: the plan dict when it is complete and
    valid for ``scene_texts``, otherwise ``(None, reason)`` explaining
    why media must be re-run. Never raises; a missing or unreadable
    checkpoint is simply "no reusable plan".
    """
    try:
        checkpoint = Path(package_dir) / "checkpoints" / "media_plan.json"
    except (TypeError, ValueError):
        return None, "no output package to load a saved media plan from"
    try:
        raw = checkpoint.read_text(encoding="utf-8")
    except OSError:
        return None, "no saved media plan checkpoint"
    try:
        plan = json.loads(raw)
    except ValueError:
        return None, "saved media plan checkpoint is not valid JSON"
    valid, reason = validate_saved_media_plan(plan, scene_texts)
    if not valid:
        return None, reason
    return plan, reason


def rebuild_media_resolutions(plan):
    """Rebuild the ``{scene_index: SceneResolution}`` ledger from a plan.

    Downstream stages (final visual gate, storyboard persistence) read
    ``job_config['_v2_media_resolutions']`` as ``SceneResolution``
    objects, not the plain-dict rows stored in the checkpoint's
    ``metadata.media_resolution_v2``. Rebuilds best-effort: rows that do
    not round-trip are skipped, and an empty dict is returned when the
    plan carries no rows (callers treat a missing ledger entry as
    "no ledger", never as an error).
    """
    try:
        metadata = (plan or {}).get("metadata") or {}
        rows = metadata.get("media_resolution_v2") or []
        if not isinstance(rows, list) or not rows:
            return {}
        from .media_resolution_coordinator import SceneResolution
        import dataclasses

        fields = {field.name for field in dataclasses.fields(SceneResolution)}
        resolutions = {}
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            scene_index = row.get("scene_index", index)
            try:
                scene_index = int(scene_index)
            except (TypeError, ValueError):
                continue
            try:
                resolutions[scene_index] = SceneResolution(
                    **{key: value for key, value in row.items() if key in fields}
                )
            except (TypeError, ValueError):
                continue
        return resolutions
    except Exception:
        return {}
