"""Generate pristine, unpolluted 1080p footage for the British Royal Family production."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "assets" / "royal_family_footage"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SOURCES = {
    "scene_1_ceremony.mp4": {
        "type": "video",
        "path": ROOT / "cache" / "media" / "pexels_pexels_v_30049197_f93bbf947c1a7fbd.mp4",
        "ss": 0,
        "t": 14,
    },
    "scene_2_buckingham.mp4": {
        "type": "video",
        "path": ROOT / "cache" / "media" / "pexels_pexels_v_12695092_baf89ada78c0c868.mp4",
        "ss": 0,
        "t": 14,
    },
    "scene_3_queen_legacy.mp4": {
        "type": "image",
        "path": ROOT / "temp" / "codex_validated_royal_20260916" / "temp" / "jobs" / "c3e128c5" / "c3e128c5-run-0001-0b49226283f5" / "render_media" / "web_v2_1789574320024_791c23" / "scene_0003.jpg",
        "t": 14,
    },
    "scene_4_king_charles.mp4": {
        "type": "image",
        "path": ROOT / "output" / "king_charles_news_2min" / "bermuda_royal.jpg",
        "t": 14,
    },
    "scene_5_commonwealth.mp4": {
        "type": "image",
        "path": ROOT / "output" / "king_charles_news_2min" / "commonwealth_games.jpg",
        "t": 14,
    },
}

def main():
    print("Generating pristine 1080p clips in assets/royal_family_footage/...")
    for filename, config in SOURCES.items():
        src_path = config["path"]
        if not src_path.exists():
            raise FileNotFoundError(f"Source missing: {src_path}")
        
        out_path = OUT_DIR / filename
        duration = config.get("t", 14)
        print(f"Processing {filename} from {src_path.name} ({config['type']})...")
        
        if config["type"] == "video":
            ss = config.get("ss", 0)
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(ss),
                "-i", str(src_path),
                "-t", str(duration),
                "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-r", "30",
                "-an",
                str(out_path)
            ]
        elif filename == "scene_4_king_charles.mp4":
            # Scale to width 1920 and crop top 1080 to keep King Charles face and uniform framed
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1",
                "-i", str(src_path),
                "-t", str(duration),
                "-vf", "scale=1920:-1,crop=1920:1080:0:120,zoompan=z='min(zoom+0.0004,1.10)':d=420:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080:fps=30",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-r", "30",
                "-an",
                str(out_path)
            ]
        else:
            # Ken Burns subtle slow push for static imagery
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1",
                "-i", str(src_path),
                "-t", str(duration),
                "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,zoompan=z='min(zoom+0.0005,1.12)':d=420:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080:fps=30",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-r", "30",
                "-an",
                str(out_path)
            ]
        
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"ERROR rendering {filename}:\n{res.stderr}")
            return 1
        print(f"  --> {filename} generated successfully ({out_path.stat().st_size / 1024 / 1024:.2f} MB)")
        
    print("\nAll 5 clips generated cleanly!")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
