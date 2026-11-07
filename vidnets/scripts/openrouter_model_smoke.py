"""Live, secret-safe OpenRouter model smoke test.

Checks the exact cheap scene-brain and vision models used by the app. It never
prints the API key or the model response; only status, latency, and a compact
parse/availability result are reported.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.defaults")
KEY = str(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("VIDZEO_OPENROUTER_API_KEY") or "").strip()
BRAIN = str(
    os.environ.get("VIDZEO_SCENE_BRAIN_MODEL")
    or os.environ.get("VIDZEO_LLM_MODEL")
    or "qwen/qwen3.5-flash-02-23"
).strip()
VISION = str(
    os.environ.get("VIDZEO_QWEN_VISION_MODEL")
    or "qwen/qwen3.5-flash-02-23"
).strip()
IMAGE = ROOT / "output" / "gta_smoke_frame.png"
URL = "https://openrouter.ai/api/v1/chat/completions"


def call(model: str, content, *, json_mode: bool = False) -> dict:
    started = time.perf_counter()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
        "max_tokens": 120,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if model.startswith("qwen/"):
        payload["reasoning"] = {"enabled": False}
    response = requests.post(
        URL,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=45,
    )
    elapsed = round(time.perf_counter() - started, 3)
    if response.status_code >= 400:
        return {"model": model, "ok": False, "status": response.status_code, "elapsed": elapsed}
    data = response.json()
    choices = data.get("choices") or []
    text = ""
    message_keys = []
    finish_reason = ""
    if choices:
        message = choices[0].get("message") or {}
        message_keys = sorted(message.keys())
        finish_reason = str(choices[0].get("finish_reason") or "")
        text = str(message.get("content") or message.get("reasoning") or "").strip()
    return {"model": model, "ok": bool(text), "status": response.status_code, "elapsed": elapsed, "response_present": bool(text), "message_keys": message_keys, "finish_reason": finish_reason}


def main() -> int:
    if not KEY:
        print(json.dumps({"ok": False, "error": "OPENROUTER_API_KEY is not configured"}))
        return 2
    if not IMAGE.is_file():
        print(json.dumps({"ok": False, "error": f"vision fixture missing: {IMAGE}"}))
        return 2
    image_data = base64.b64encode(IMAGE.read_bytes()).decode("ascii")
    vision_content = [
        {"type": "text", "text": "Return JSON with one field subject describing the image in five words or fewer."},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}"}},
    ]
    results = [
        call(BRAIN, "Return JSON with one field subject describing GTA VI in five words or fewer.", json_mode=True),
        call(VISION, vision_content, json_mode=True),
    ]
    output = {"ok": all(item.get("ok") for item in results), "models": results}
    print(json.dumps(output, indent=2))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
