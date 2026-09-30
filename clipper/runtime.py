"""Preflight the tools we actually use; select a working CPU or GPU encoder."""
from __future__ import annotations

import os
import shutil
import subprocess


def encoder_args(codec: str, quality: str = "21") -> list[str]:
    if codec == "h264_nvenc":
        return ["-c:v", codec, "-rc", "vbr", "-cq", quality, "-b:v", "0", "-preset", "p5", "-pix_fmt", "yuv420p"]
    if codec == "libx264":
        return ["-c:v", codec, "-crf", quality, "-preset", "fast", "-pix_fmt", "yuv420p"]
    raise RuntimeError(f"Unsupported encoder: {codec}")


def _can_encode(codec: str) -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=s=320x240:d=0.1",
             "-frames:v", "1", *encoder_args(codec), "-f", "null", "-"],
            capture_output=True, timeout=15,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def preflight(s, *, require_key: bool = True) -> list[str]:
    missing = [name for name in ("ffmpeg", "ffprobe") if not shutil.which(name)]
    if missing:
        raise RuntimeError(f"Missing {', '.join(missing)}. Install FFmpeg: brew install ffmpeg (macOS), or apt install ffmpeg (Ubuntu).")
    filters = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=15)
    if filters.returncode or "subtitles" not in filters.stdout:
        raise RuntimeError("FFmpeg needs the subtitles filter (libass) to burn captions.")
    if require_key:
        if s.provider == "anthropic" and not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("Set ANTHROPIC_API_KEY, or put it in ~/.config/hypecut/.env. Use --provider kimi with KIMI_API_KEY for Kimi.")
        if s.provider == "kimi" and not s.kimi_api_key:
            raise RuntimeError("Set KIMI_API_KEY (or MOONSHOT_API_KEY), or put it in ~/.config/hypecut/.env.")
    import ctranslate2
    cuda = ctranslate2.get_cuda_device_count() > 0
    if cuda:
        from . import _preload_cuda_libs
        import ctypes
        _preload_cuda_libs()
        try:
            ctypes.CDLL("libcublas.so.12")
            ctypes.CDLL("libcudnn.so.9")
        except OSError:
            cuda = False
    if s.whisper_device == "auto":
        s.whisper_device = "cuda" if cuda else "cpu"
    if s.whisper_device == "cuda" and not cuda:
        raise RuntimeError("CUDA needs a compatible device and runtime libraries. Install with uv tool install '.[cuda]', or use --device cpu.")
    if s.whisper_compute == "auto":
        s.whisper_compute = "float16" if s.whisper_device == "cuda" else "int8"
    if s.vcodec == "auto":
        s.vcodec = "h264_nvenc" if cuda and _can_encode("h264_nvenc") else "libx264"
    if not _can_encode(s.vcodec):
        raise RuntimeError(f"FFmpeg cannot use {s.vcodec}. Install an FFmpeg build with libx264, or use --encoder libx264.")
    if s.reframe == "track":
        from . import asd
        if not asd.available():
            raise RuntimeError("--reframe track needs LR-ASD. Set HYPECUT_ASD_PY and HYPECUT_ASD_DIR, or use --reframe track-fast.")
    if not s.fonts_dir.is_dir():
        raise RuntimeError("Bundled caption fonts are missing. Reinstall Hypecut.")
    return ["FFmpeg + captions: ready", f"Transcription: {s.whisper_device} / {s.whisper_compute}",
            f"Encoder: {s.vcodec}", f"Reframe: {s.reframe}"]
