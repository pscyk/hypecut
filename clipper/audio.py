"""Audience-reaction detection. The loudest sustained moments in a comedy/panel
show are laughter/applause/gasps — a strong highlight signal the transcript alone
misses (a visual gag with no dialogue still gets a huge laugh)."""
from __future__ import annotations
import subprocess
from pathlib import Path
import numpy as np


def reaction_peaks(src: Path, top: int = 16, sr: int = 16000, hop: float = 0.5,
                   min_gap: float = 8.0) -> list[float]:
    """Return up to `top` timestamps (s) of the loudest sustained reaction moments,
    spaced at least `min_gap` apart."""
    cp = subprocess.run(["ffmpeg", "-nostdin", "-i", str(src), "-ac", "1", "-ar", str(sr),
                         "-f", "s16le", "pipe:1"], capture_output=True)
    a = np.frombuffer(cp.stdout, dtype=np.int16).astype(np.float32)
    h = int(sr * hop)
    if a.size < h * 10:
        return []
    e = np.array([np.sqrt(np.mean(a[i:i + h] ** 2) + 1) for i in range(0, a.size - h, h)])
    w = max(1, int(2.0 / hop))  # smooth over ~2s (sustained reaction, not a transient)
    es = np.convolve(e, np.ones(w) / w, mode="same")
    thr = np.percentile(es, 88)
    peaks: list[float] = []
    for idx in np.argsort(es)[::-1]:
        if es[idx] < thr:
            break
        t = idx * hop
        if all(abs(t - p) > min_gap for p in peaks):
            peaks.append(t)
        if len(peaks) >= top:
            break
    return sorted(peaks)
