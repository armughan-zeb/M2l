"""Fail-fast audit for production editing features used by packaged builds."""

from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]).resolve()
    os.environ["VIDZEO_RESOURCE_ROOT"] = str(root)
    os.environ.setdefault("VIDZEO_ROOT", str(root))
    sys.path.insert(0, str(root / "server"))

    from services.editing_libraries import TRANSITION_PACKS
    from services.native_render_policy import procedural_sound_library
    from services.semantic_edit_engine import build_semantic_edit_manifest
    from services.semantic_ffmpeg_renderer import _build_audio_concat, _ffmpeg, _transition_filter

    library = procedural_sound_library()
    editorial_keys = [key for key in library if key.startswith("edit_")]
    if len(library) < 80 or len(editorial_keys) < 20:
        raise RuntimeError(
            f"Incomplete sound library: {len(library)} total, {len(editorial_keys)} editorial"
        )

    transition_ids = {item for values in TRANSITION_PACKS.values() for item in values}
    unmapped = sorted(item for item in transition_ids if item != "cut" and not _transition_filter(item, 0.3))
    if unmapped:
        raise RuntimeError(f"Production transitions have no FFmpeg implementation: {unmapped}")

    texts = [
        "In April 1912 the expedition began.",
        "Captain Smith led the mission.",
        "The route crossed the North Atlantic.",
        "Seventy three percent changed overnight.",
        "The archive warning arrived too late.",
        "The final evidence changed history.",
        "The team made a decisive choice.",
        "Subscribe for the next investigation.",
    ]
    beats = [
        {"role": "chapter", "treatment": "date", "importance": 0.95},
        {"role": "person", "treatment": "person", "importance": 0.85},
        {"role": "location", "treatment": "location", "importance": 0.80},
        {"role": "evidence", "treatment": "counter", "importance": 0.90},
        {"role": "evidence", "treatment": "document", "importance": 0.86},
        {"role": "climax", "importance": 0.95},
        {"role": "turn", "importance": 0.90},
        {"role": "cta", "treatment": "cta", "importance": 0.90},
    ]
    manifest = build_semantic_edit_manifest(
        texts,
        [2.5] * len(texts),
        media=[{"path": "editing-audit.jpg", "type": "image"}] * len(texts),
        editorial_beats=beats,
        topic="Atlantic investigation",
        category="history",
        config={
            "sound_library": library,
            "theme_id": "history_archive",
            "editorial_cue_v2_enabled": False,
        },
    )
    scenes = list(manifest.get("scenes") or [])
    designed = [scene for scene in scenes if (scene.get("graphic") or {}).get("template_id") != "full-bleed"]
    # The production grammar deliberately alternates designed beats with
    # clean cinematic media.  Its canonical cadence is one graphic every two
    # scenes, so 50% is the minimum for this audit; demanding 60% would reject
    # the same 2-scene rhythm enforced by the cadence regression tests.
    minimum_designed = max(1, (len(scenes) + 1) // 2)
    if len(designed) < minimum_designed:
        raise RuntimeError(
            f"Designed-beat coverage is too low: {len(designed)}/{len(scenes)} "
            f"(minimum {minimum_designed})"
        )
    if any((scene.get("camera") or {}).get("motion") in {"", "static", None} for scene in scenes):
        raise RuntimeError("At least one image scene has no production motion")
    if not any((scene.get("transition") or {}).get("template_id") != "cut" for scene in scenes[1:]):
        raise RuntimeError("No rendered transition was planned")
    cues = [cue for scene in scenes for cue in (scene.get("sound_cues") or [])]
    if not cues or not all(Path(str(cue.get("src") or "")).is_file() for cue in cues):
        raise RuntimeError("Planned sound cues do not resolve to packaged files")
    if not any(str(cue.get("kind") or "").startswith("edit_") for cue in cues):
        raise RuntimeError("No editorial text-card/transition sound was planned")

    # Exercise the real voice + music + SFX filter graph. This catches FFmpeg
    # filter errors that a manifest-only check cannot see.
    ffmpeg = _ffmpeg()
    ffprobe = str(Path(ffmpeg).with_name("ffprobe.exe"))
    with tempfile.TemporaryDirectory(prefix="vidzeo_editing_audit_") as temp:
        temp_dir = Path(temp)
        voice = temp_dir / "voice.wav"
        music = temp_dir / "music.wav"
        for target, frequency in ((voice, 330), (music, 110)):
            subprocess.run(
                [
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"sine=frequency={frequency}:sample_rate=44100", "-t", "2.5", str(target),
                ],
                check=True,
            )
        mixed = _build_audio_concat(
            str(voice),
            str(music),
            0.20,
            [
                {"src": library["edit_whoosh_light"]["src"], "start": 0.2, "duration": 0.4, "volume": 0.18},
                {"src": library["impact_soft"]["src"], "start": 1.2, "duration": 0.5, "volume": 0.20},
            ],
            2.5,
            temp,
        )
        if not mixed or Path(mixed).stat().st_size < 20_000:
            raise RuntimeError("Voice/music/SFX production mix did not render")
        probe = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name,sample_rate,channels,duration",
                "-of",
                "json",
                mixed,
            ],
            check=True,
            capture_output=True,
        )
        streams = (json.loads(probe.stdout.decode("utf-8", errors="replace") or "{}") or {}).get("streams") or []
        if not streams:
            raise RuntimeError("Production audio mix has no audio stream")
        stream = streams[0]
        if str(stream.get("sample_rate") or "") != "48000" or int(stream.get("channels") or 0) != 2:
            raise RuntimeError(
                "Production audio mix is not fixed 48 kHz stereo: "
                f"sample_rate={stream.get('sample_rate')!r}, channels={stream.get('channels')!r}"
            )
        if abs(float(stream.get("duration") or 0.0) - 2.5) > 0.08:
            raise RuntimeError(
                "Production audio mix duration drifted: "
                f"{stream.get('duration')!r}s (expected 2.5s)"
            )

    print(
        "production editing verified: "
        f"cards={len(designed)}/{len(scenes)}, cues={len(cues)}, "
        f"editorial_sfx={len(editorial_keys)}, transitions={len(transition_ids) - 1}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
