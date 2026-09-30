"""Audio-visual active-speaker tracking.

The reliable signal for "who is talking" is not how much a face moves, but whether
its mouth moves *in sync with the speech audio*. A listener fidgeting or a hand
brushing a cheek produces motion that does NOT correlate with the soundtrack; the
speaker's mouth opens and closes with it.

Pipeline:
  1. ffmpeg → frames (AV1-safe) + raw audio.
  2. Detect faces every frame; cluster their x-positions into stable *seats*
     (this is also the who's-who roster — one cluster per panelist).
  3. Per seat, build a mouth-region motion time series (continuous, using the
     seat's last known box through detection dropouts).
  4. Compute the speech-energy envelope at the same rate.
  5. For each moment, the active speaker is the seat whose recent mouth-motion
     best correlates with the audio energy (when speech is present). Center there.
  6. Fall back to the panel centroid when nobody on screen matches (off-frame
     speaker / reaction shot) instead of zooming a random listener.
"""
from __future__ import annotations
import glob
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import cv2
import numpy as np


def _frames(src: Path, start: float, dur: float, fps: float, det_w: int, tmp: str) -> list[str]:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
         "-vf", f"fps={fps},scale={det_w}:-1", "-q:v", "3", os.path.join(tmp, "f_%05d.jpg")],
        capture_output=True,
    )
    return sorted(glob.glob(os.path.join(tmp, "f_*.jpg")))


def _audio_energy(src: Path, start: float, dur: float, n: int, sr: int = 16000) -> np.ndarray:
    """Per-frame RMS speech energy, length n, normalized 0..1."""
    cp = subprocess.run(
        ["ffmpeg", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
         "-ac", "1", "-ar", str(sr), "-f", "s16le", "pipe:1"],
        capture_output=True,
    )
    a = np.frombuffer(cp.stdout, dtype=np.int16).astype(np.float32)
    if a.size < n or n <= 0:
        return np.zeros(max(n, 1))
    hop = a.size / n
    e = np.array([np.sqrt(np.mean(np.square(a[int(i * hop):int((i + 1) * hop) + 1])) + 1e-6)
                  for i in range(n)])
    e = e - e.min()
    return e / (e.max() + 1e-6)


def _seats(xs: list[float], iw: int, min_members: int) -> list[float]:
    """1D-cluster detection x-centers (source px) into seat positions."""
    if not xs:
        return []
    xs = sorted(xs)
    gap = iw * 0.08
    clusters, cur = [], [xs[0]]
    for x in xs[1:]:
        if x - cur[-1] <= gap:
            cur.append(x)
        else:
            clusters.append(cur)
            cur = [x]
    clusters.append(cur)
    return [float(np.mean(c)) for c in clusters if len(c) >= min_members]


def _mouth_motion(gray: np.ndarray, prev: np.ndarray, box) -> float:
    x, y, w, h = box
    mx, my = max(0, int(x + 0.25 * w)), max(0, int(y + 0.55 * h))
    mw, mh = int(0.5 * w), int(0.38 * h)
    a = gray[my:my + mh, mx:mx + mw]
    b = prev[my:my + mh, mx:mx + mw]
    if a.size == 0 or a.shape != b.shape:
        return 0.0
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16))))


def track(src: Path, start: float, end: float, iw: int, cw: int,
          fps: float = 10.0, det_w: int = 720, win: float = 1.2) -> tuple[list[tuple[float, int]], list[float]]:
    """Return (keyframes [(t_rel, crop_x)], seat_centers_px). Seats = who's-who roster."""
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    dur = end - start
    sx = iw / det_w
    tmp = tempfile.mkdtemp(prefix="clipspk_")
    try:
        frames = _frames(src, start, dur, fps, det_w, tmp)
        if not frames:
            return [], []
        n = len(frames)

        # pass 1: detect faces, gather x-centers + per-frame boxes
        grays, dets = [], []
        all_x: list[float] = []
        for fp in frames:
            img = cv2.imread(fp)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img is not None else None
            grays.append(gray)
            faces = [] if gray is None else list(cascade.detectMultiScale(gray, 1.2, 5, minSize=(40, 40)))
            dets.append(faces)
            for (x, y, w, h) in faces:
                all_x.append((x + w / 2) * sx)

        seats = _seats(all_x, iw, min_members=max(2, n // 12))
        if not seats:
            return [], []
        centroid = float(np.mean(seats))

        # pass 2: per-seat mouth-motion series (continuous via last-known box)
        S = len(seats)
        motion = np.zeros((S, n))
        last_box = [None] * S  # det-resolution boxes
        tol = (iw * 0.08) / sx
        for i in range(n):
            for (x, y, w, h) in dets[i]:
                cxs = (x + w / 2) * sx
                s = int(np.argmin([abs(cxs - c) for c in seats]))
                if abs(((x + w / 2) - seats[s] / sx)) <= tol:
                    last_box[s] = (x, y, w, h)
            if i > 0 and grays[i] is not None and grays[i - 1] is not None:
                for s in range(S):
                    if last_box[s] is not None:
                        motion[s, i] = _mouth_motion(grays[i], grays[i - 1], last_box[s])

        audio = _audio_energy(src, start, dur, n)
        speech = audio > 0.18

        # pass 3: active seat per frame = best audio-correlated mouth motion
        w_half = max(2, int(win * fps))
        active = centroid
        last = centroid
        max_step = iw * 0.12
        centers: list[float] = []
        for i in range(n):
            lo, hi = max(0, i - w_half), min(n, i + w_half + 1)
            ae = audio[lo:hi]
            best_corr, best_seat = 0.18, None
            if speech[i] and ae.std() > 1e-3:
                for s in range(S):
                    ms = motion[s, lo:hi]
                    if ms.std() < 1e-3:
                        continue
                    c = float(np.corrcoef(ms, ae)[0, 1])
                    if c > best_corr:
                        best_corr, best_seat = c, s
            if best_seat is not None:
                active = seats[best_seat]
            # else: hold last active seat (don't chase listeners / reaction shots)
            last += max(-max_step, min(max_step, active - last))
            centers.append(last)

        from .reframe import _smooth, _decimate
        sm = _smooth(centers, 5)
        half = cw / 2
        kf = []
        for i, c in enumerate(sm):
            t = dur * i / (len(sm) - 1) if len(sm) > 1 else 0.0
            x = int(round(min(max(c - half, 0), iw - cw)))
            kf.append((round(t, 2), x))
        return _decimate(kf), seats
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
