#!/usr/bin/env python3
"""
ScriptIT narrator transcript helper.

Fetches the transcript (auto subtitles preferred, manual subtitles next) for a
YouTube video using yt_dlp and prints a JSON result to stdout:

  {
    "ok": true,
    "video": {
      "title": "...",
      "channel": "...",
      "durationSec": 123,
      "url": "https://www.youtube.com/watch?v=..."
    },
    "transcript": "...",
    "transcriptChars": 12345,
    "subtitleKind": "auto" | "manual" | "none"
  }

Errors are reported as {"ok": false, "error": "..."}. Transcript text is capped
so long videos do not blow up the summarization prompt.
"""
import json
import os
import re
import sys
import tempfile
import copy
import shutil
from pathlib import Path

MAX_TRANSCRIPT_CHARS = 120000

try:
    import yt_dlp
except ImportError:
    print(json.dumps({"ok": False, "error": "yt_dlp is not installed (pip install yt-dlp)"}))
    sys.exit(0)


def find_sub_files(outdir):
    found = []
    for name in os.listdir(outdir):
        if name.startswith("sub.") and (name.endswith(".vtt") or name.endswith(".srt")):
            found.append(os.path.join(outdir, name))
    return sorted(found)


def strip_vtt(text):
    lines = []
    previous = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "WEBVTT":
            continue
        if "-->" in line:
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}\.\d{3}", line):
            continue
        if re.match(r"^(NOTE|STYLE|REGION|Kind:|Language:)", line, re.IGNORECASE):
            continue
        if re.match(r"^align:|^position:|^line:|^size:", line, re.IGNORECASE):
            continue
        # YouTube's VTT often embeds per-word timestamps and <c> spans in
        # the visible text. They are markup, not narration. Auto captions can
        # also repeat a complete cue immediately after its progressive cue;
        # remove only consecutive exact duplicates so intentional repetition
        # later in the transcript remains intact.
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line and line != previous:
            lines.append(line)
            previous = line
    return " ".join(lines)


def strip_srt(text):
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^\d+$", line):
            continue
        if "-->" in line:
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2},\d{3}", line):
            continue
        lines.append(line)
    return " ".join(lines)


def parse_subtitle_file(filepath):
    with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if filepath.endswith(".vtt"):
        return strip_vtt(text)
    return strip_srt(text)


def pick_transcript(outdir, manual_languages=None):
    for filepath in find_sub_files(outdir):
        text = parse_subtitle_file(filepath).strip()
        if text:
            language = os.path.basename(filepath)[4:].rsplit(".", 1)[0]
            kind = ("manual" if language in manual_languages else "auto") if manual_languages is not None else ("auto" if ".auto." in filepath or "auto" in filepath else "manual")
            return text, kind
    return "", "none"


def attach_saved_session(ydl):
    """Share Vidnets' existing OS-protected session without exporting cookies."""
    engine_server = Path(__file__).resolve().parents[2] / "Vidnets Working Folder" / "server"
    if not engine_server.is_dir():
        return
    sys.path.insert(0, str(engine_server))
    from services.youtube_session import ensure_session
    from yt_dlp.cookies import YoutubeDLCookieJar
    jar, _ = ensure_session()
    adapted = YoutubeDLCookieJar()
    for cookie in jar:
        adapted.set_cookie(copy.copy(cookie))
    ydl.cookiejar = adapted
    if "_opener" in vars(ydl):
        ydl.__dict__["_opener"] = None


def main():
    video_url = sys.argv[1] if len(sys.argv) > 1 else ""
    if not video_url:
        print(json.dumps({"ok": False, "error": "No video URL given"}))
        return

    outdir = sys.argv[2] if len(sys.argv) > 2 else tempfile.mkdtemp(prefix="scriptit-subs-")
    os.makedirs(outdir, exist_ok=True)

    opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "en-orig"],
        "subtitlesformat": "vtt",
        "outtmpl": os.path.join(outdir, "sub.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "ignoreerrors": False,
        "ignore_no_formats_error": True,
        "socket_timeout": 8,
        "retries": 1,
        "extractor_retries": 1,
        "extractor_args": {"youtube": {"player_client": ["android", "web", "web_embedded", "web_creator", "mweb"]}},
    }
    node_runtime = shutil.which("node")
    if node_runtime:
        opts["js_runtimes"] = {"node": {"path": node_runtime}}
        opts["remote_components"] = ["ejs:github"]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            attach_saved_session(ydl)
            info = ydl.extract_info(video_url, download=True)
        if not info:
            print(json.dumps({"ok": False, "error": "yt-dlp could not fetch the video"}))
            return
        transcript, kind = pick_transcript(outdir, set((info.get("subtitles") or {}).keys()))
        if not transcript:
            print(json.dumps({
                "ok": False,
                "error": "No English subtitles available for this video (neither manual nor auto-generated)",
                "video": {
                    "title": info.get("title") or "",
                    "channel": info.get("channel") or info.get("uploader") or "",
                    "durationSec": int(info.get("duration") or 0),
                    "url": video_url,
                },
            }))
            return
        if len(transcript) > MAX_TRANSCRIPT_CHARS:
            step = len(transcript) // MAX_TRANSCRIPT_CHARS + 1
            words = transcript.split(" ")
            sampled = " ".join(words[::step])[:MAX_TRANSCRIPT_CHARS]
            transcript = sampled
        print(json.dumps({
            "ok": True,
            "video": {
                "title": info.get("title") or "",
                "channel": info.get("channel") or info.get("uploader") or "",
                "durationSec": int(info.get("duration") or 0),
                "url": video_url,
            },
            "transcript": transcript,
            "transcriptChars": len(transcript),
            "subtitleKind": kind,
        }))
    except Exception as exc:  # noqa: BLE001 - report anything to the bridge
        print(json.dumps({"ok": False, "error": str(exc)}))


if __name__ == "__main__":
    main()
