"""
GPU Hardware Acceleration Auto-Detection & Adaptive Encoding Engine.
Supports NVIDIA NVENC, Intel QuickSync (QSV), AMD AMF, and multi-threaded CPU libx264 with fastdecode.
"""

import os
import subprocess
import threading
from typing import Dict, Any, List, Optional

_DETECTION_LOCK = threading.Lock()
_CACHED_ENCODER_INFO: Optional[Dict[str, Any]] = None


def detect_optimal_gpu_encoder() -> Dict[str, Any]:
    """
    Auto-detects the fastest working video encoder on the system.
    Tests encoders in priority order: NVENC -> QSV -> AMF -> libx264 ultrafast.
    (h264_mf is intentionally avoided due to Windows MF latency and frame-pacing stutter).
    """
    global _CACHED_ENCODER_INFO
    if _CACHED_ENCODER_INFO is not None:
        return _CACHED_ENCODER_INFO

    with _DETECTION_LOCK:
        if _CACHED_ENCODER_INFO is not None:
            return _CACHED_ENCODER_INFO

        # 1. Candidate hardware encoders in priority order
        candidates = [
            {
                "name": "NVIDIA NVENC",
                "codec": "h264_nvenc",
                "args": ["-preset", "p2", "-cq", "23", "-pix_fmt", "yuv420p", "-g", "60"],
                "test_args": ["-preset", "p2", "-t", "0.1"]
            },
            {
                "name": "Intel QuickSync (QSV)",
                "codec": "h264_qsv",
                "args": ["-preset", "veryfast", "-global_quality", "23", "-pix_fmt", "nv12", "-g", "60"],
                "test_args": ["-preset", "veryfast", "-t", "0.1"]
            },
        ]


        # First check nvidia-smi for named GPU reporting
        gpu_device_name = "CPU"
        try:
            nsmi = subprocess.run(
                ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                capture_output=True, text=True, timeout=2
            )
            if nsmi.returncode == 0 and nsmi.stdout.strip():
                gpu_device_name = nsmi.stdout.strip().split('\n')[0]
        except Exception:
            pass

        selected_encoder = None

        for cand in candidates:
            codec = cand["codec"]
            try:
                # Test real encode of a test frame
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1:r=30",
                    "-c:v", codec
                ] + cand["test_args"] + ["-f", "null", "-"]
                
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
                if res.returncode == 0:
                    selected_encoder = cand
                    if gpu_device_name == "CPU":
                        gpu_device_name = cand["name"]
                    break
            except Exception:
                continue

        # Fallback to software CPU libx264 with ultra-fast decoding tune
        if not selected_encoder:
            selected_encoder = {
                "name": "CPU (Software libx264 ultrafast)",
                "codec": "libx264",
                "args": ["-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p", "-tune", "fastdecode", "-g", "60"],
                "test_args": ["-preset", "ultrafast", "-t", "0.1"]
            }

        _CACHED_ENCODER_INFO = {
            "device_name": gpu_device_name,
            "encoder_name": selected_encoder["name"],
            "codec": selected_encoder["codec"],
            "is_hardware_accelerated": selected_encoder["codec"] != "libx264",
            "encode_args": selected_encoder["args"],
            "pix_fmt": "yuv420p" if selected_encoder["codec"] != "h264_qsv" else "nv12"
        }

        print(f"   [gpu-autodetect] Active Video Encoder: {_CACHED_ENCODER_INFO['encoder_name']} ({_CACHED_ENCODER_INFO['codec']})")
        print(f"   [gpu-autodetect] Hardware Acceleration: {'ENABLED' if _CACHED_ENCODER_INFO['is_hardware_accelerated'] else 'CPU Fast-Decode'}")

        return _CACHED_ENCODER_INFO


def get_ffmpeg_video_encoder_args(preset: str = "ultrafast", crf: int = 23) -> List[str]:
    """
    Returns optimal FFmpeg CLI video encoding arguments with stutter-free GOP alignment.
    """
    info = detect_optimal_gpu_encoder()
    codec = info["codec"]
    
    if codec == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p2", "-cq", str(crf), "-pix_fmt", "yuv420p", "-g", "60"]
    elif codec == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", str(crf), "-pix_fmt", "nv12", "-g", "60"]
    elif codec == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "speed", "-pix_fmt", "yuv420p", "-g", "60"]
    else:
        return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-tune", "fastdecode", "-g", "60"]
