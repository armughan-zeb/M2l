"""Make a Remotion-ready copy of a completed Vidzeo export.

The production FFmpeg renderer intentionally cleans transient web downloads
after muxing. For a deterministic post-render Remotion validation we therefore
sample one frame per final scene from the saved export and keep those frames in
the manifest's media slots. This does not alter the production output.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    source = Path(sys.argv[1] if len(sys.argv) > 1 else root / "output" / "semantic_400_rms_titanic.mp4").resolve()
    manifest_path = Path(sys.argv[2] if len(sys.argv) > 2 else root / "output" / "semantic_400_rms_titanic_manifest.json").resolve()
    output_manifest = Path(sys.argv[3] if len(sys.argv) > 3 else root / "output" / "semantic_400_rms_titanic_remotion_manifest.json").resolve()
    media_dir = root / "temp" / "semantic_400_remotion_media"
    public_media_dir = root / "public" / "remotion-manifest-media"
    media_dir.mkdir(parents=True, exist_ok=True)
    public_media_dir.mkdir(parents=True, exist_ok=True)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = payload.get("semantic_edit_manifest") or payload.get("manifest") or payload
    for index, scene in enumerate(manifest.get("scenes") or []):
        start = float(scene.get("start") or 0.0)
        duration = float(scene.get("duration") or 1.0)
        frame = media_dir / f"scene_{index + 1:03d}.jpg"
        if not frame.exists() or frame.stat().st_size < 1024:
            timestamp = max(0.0, start + min(duration * 0.45, max(0.05, duration - 0.05)))
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{timestamp:.3f}",
                "-i", str(source), "-frames:v", "1", "-q:v", "3", str(frame),
            ], check=True, timeout=30)
        public_frame = public_media_dir / frame.name
        if not public_frame.exists() or public_frame.stat().st_mtime < frame.stat().st_mtime:
            public_frame.write_bytes(frame.read_bytes())
        media = scene.setdefault("media", {})
        # Flask serves the repository's temp directory at /temp; do not repeat
        # the directory name when converting the filesystem-relative path.
        media["src"] = f"/remotion-manifest-media/{frame.name}"
        media["path"] = str(frame)
    voice = (manifest.get("audio") or {}).get("voiceover")
    if isinstance(voice, dict):
        voice_path = Path(voice.get("path") or "")
        if voice_path.is_file():
            public_voice = public_media_dir / voice_path.name
            if not public_voice.exists() or public_voice.stat().st_mtime < voice_path.stat().st_mtime:
                public_voice.write_bytes(voice_path.read_bytes())
            voice["src"] = f"/remotion-manifest-media/{voice_path.name}"
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(json.dumps({"semantic_edit_manifest": manifest}, indent=2), encoding="utf-8")
    print(output_manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
