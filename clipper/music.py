"""Montage mode — the music bed: resolve a track and detect its beat grid.

Cuts in a trailer/sizzle montage land on the beat, so we need (a) an audio track
and (b) the times of its beats. librosa does the beat tracking; the track can be a
local file, a URL (yt-dlp), or 'auto' (borrow the audio of the most recently
downloaded reel — i.e. a real trending sound).
"""
from __future__ import annotations
import glob
import os
from pathlib import Path
from .config import Settings, run


def _extract_audio(src: Path, out: Path) -> Path:
    run(["ffmpeg", "-nostdin", "-y", "-i", str(src), "-vn", "-ac", "2", "-ar", "44100",
         "-c:a", "aac", "-b:a", "192k", str(out)])
    return out


def _ytdlp_audio(url: str, out_base: Path) -> Path:
    # IG/private URLs need cookies; set CLIPPER_COOKIES_FROM_BROWSER="firefox:/path/to/profile"
    cookies = os.getenv("CLIPPER_COOKIES_FROM_BROWSER", "")
    cmd = ["yt-dlp", "-x", "--audio-format", "m4a", "-o", f"{out_base}.%(ext)s"]
    if cookies and "instagram.com" in url:
        cmd[1:1] = ["--cookies-from-browser", cookies]
    cmd.append(url)
    run(cmd)
    hits = sorted(glob.glob(f"{out_base}.*"))
    if not hits:
        raise RuntimeError(f"yt-dlp produced no audio for {url}")
    return Path(hits[0])


def resolve_track(spec: str, s: Settings) -> Path:
    """Resolve a --music spec to a local audio file in work/.
    spec is a local file | a URL | 'auto' (newest videos/reel_*.mp4 audio)."""
    out = s.work / "montage_music.m4a"
    if spec == "auto":
        reels = sorted(glob.glob(str(s.out.parent / "videos" / "reel_*.mp4")), key=os.path.getmtime)
        if not reels:
            raise RuntimeError("--music auto: no videos/reel_*.mp4 to borrow trending audio from")
        print(f"[music] auto: borrowing trending audio from {Path(reels[-1]).name}")
        return _extract_audio(Path(reels[-1]), out)
    p = Path(spec)
    if p.exists():
        print(f"[music] using {p.name}")
        return _extract_audio(p, out)
    if spec.startswith("http"):
        print(f"[music] downloading audio from {spec}")
        return _extract_audio(_ytdlp_audio(spec, s.work / "montage_music_dl"), out)
    raise RuntimeError(f"--music: not a file, URL, or 'auto': {spec!r}")


def beat_grid(track: Path) -> tuple[float, list[float]]:
    """(tempo_bpm, [beat_times_s]) via librosa beat tracking."""
    import librosa
    y, sr = librosa.load(str(track), sr=22050, mono=True)
    tempo, frames = librosa.beat.beat_track(y=y, sr=sr)
    beats = [round(float(t), 3) for t in librosa.frames_to_time(frames, sr=sr)]
    bpm = float(tempo if not hasattr(tempo, "__len__") else tempo[0])
    print(f"[music] tempo ~{bpm:.0f} BPM, {len(beats)} beats detected")
    return bpm, beats
