"""Separate pipeline for --montage (sizzle/trailer reels).

Kept apart from the main highlight-clipping pipeline (pipeline.py) on purpose: this
is a different product (one beat-synced montage with a hero bit + music drop), not
the standalone-highlight cutter. It only shares low-level infra (transcribe, audio
reaction peaks, reframe), never the highlight selection/jury path.
"""
from __future__ import annotations
from pathlib import Path
from . import transcribe, audio, montage, montage_render
from .config import Settings, ffprobe_dims


def process(src: Path, s: Settings) -> list[Path]:
    s.work.mkdir(parents=True, exist_ok=True)
    s.out.mkdir(parents=True, exist_ok=True)
    stem = src.stem

    words = transcribe.transcribe(src, s, s.work / f"{stem}.words.json")
    if not words:
        print("[montage] no speech found; nothing to build")
        return []

    if not s.reaction_peaks:  # laughter peaks: used to land montage cuts on the payoff
        s.reaction_peaks = audio.reaction_peaks(src)
        if s.reaction_peaks:
            print(f"[audio] {len(s.reaction_peaks)} audience-reaction peaks detected")

    edl = montage.build_edl(words, s, s.work / f"{stem}.montage.json")
    iw, ih = ffprobe_dims(src)
    return montage_render.render(src, edl, words, iw, ih, s)
