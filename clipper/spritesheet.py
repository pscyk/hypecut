"""Build a contact sheet (sprite grid) of frames sampled across a candidate clip,
so a vision model can 'watch' the moment: faces, reactions, gestures, the room.

Uses ffmpeg (software decode) — robust across codecs incl. AV1, where OpenCV's
hardware path fails.
"""
from __future__ import annotations
import base64
from pathlib import Path
from .config import run


def make_sheet(src: Path, start: float, end: float, out: Path,
               cols: int = 3, rows: int = 3, tile_w: int = 360) -> Path:
    # tile_w=360 keeps the sheet near the ~1568px vision sweet spot (~1.5k tokens);
    # bigger tiles cost more image tokens without helping the model.
    n = cols * rows
    dur = max(0.1, end - start)
    fps = (n + 0.5) / dur  # slight bias to ensure >= n frames before the tile fills
    # stamp each frame with its source time so the vision model can reason about WHEN
    draw = (f"drawtext=text='%{{eif\\:t+{int(start)}\\:d}}s':x=6:y=6:fontsize=22:"
            f"fontcolor=yellow:borderw=3:bordercolor=black")
    vf = f"fps={fps:.6f},scale={tile_w}:-1,{draw},tile={cols}x{rows}"
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-nostdin", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
         "-vf", vf, "-frames:v", "1", "-q:v", "4", str(out)])
    return out


def to_b64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode()
