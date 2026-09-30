"""Step 4: build the 9:16 reframe filter. Strategies: center | track.

`build(...)` returns an ffmpeg video-filter snippet that takes the source and
outputs an OUT_W x OUT_H frame. For "track" it analyses faces in the clip and
pans the crop window to follow the speaker.
"""
from __future__ import annotations
import glob
import math
import os
import shutil
import tempfile
from pathlib import Path
import cv2
import numpy as np
from .config import Settings, OUT_W, OUT_H, run, ASSETS

# YuNet face detector (opencv-zoo) — far better than the Haar cascade; falls back to
# Haar if the model is missing.
_YUNET = None
_YUNET_PATH = ASSETS / "models" / "face_detection_yunet_2023mar.onnx"
_HAAR = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def _yunet():
    global _YUNET
    if _YUNET is None:
        if not _YUNET_PATH.exists():
            _YUNET = False
        else:
            try:
                _YUNET = cv2.FaceDetectorYN.create(str(_YUNET_PATH), "", (320, 320), 0.6, 0.3, 5000)
            except Exception:
                _YUNET = False
    return _YUNET or None


def detect_faces(img) -> list[tuple[int, int, int, int, float]]:
    """[(x, y, w, h, score)] via YuNet when available, else Haar."""
    det = _yunet()
    if det is not None:
        h, w = img.shape[:2]
        det.setInputSize((w, h))
        _, faces = det.detect(img)
        if faces is None:
            return []
        return [(int(f[0]), int(f[1]), int(f[2]), int(f[3]), float(f[14])) for f in faces]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return [(int(x), int(y), int(w), int(h), 1.0)
            for (x, y, w, h) in _HAAR.detectMultiScale(gray, 1.2, 5, minSize=(40, 40))]


class OneEuroFilter:
    """1€ filter — smooths a noisy tracked signal with minimal lag (Casiez et al.).
    Pins the crop without the rubber-banding a moving average gives."""

    def __init__(self, freq: float = 5.0, mincutoff: float = 0.8, beta: float = 0.01, dcutoff: float = 1.0):
        self.freq, self.mincutoff, self.beta, self.dcutoff = freq, mincutoff, beta, dcutoff
        self.x_prev = None
        self.dx_prev = 0.0

    def _alpha(self, cutoff: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        te = 1.0 / self.freq
        return 1.0 / (1.0 + tau / te)

    def __call__(self, x: float) -> float:
        if self.x_prev is None:
            self.x_prev = x
            return x
        dx = (x - self.x_prev) * self.freq
        a_d = self._alpha(self.dcutoff)
        dx_hat = a_d * dx + (1 - a_d) * self.dx_prev
        cutoff = self.mincutoff + self.beta * abs(dx_hat)
        a = self._alpha(cutoff)
        x_hat = a * x + (1 - a) * self.x_prev
        self.x_prev, self.dx_prev = x_hat, dx_hat
        return x_hat


def _crop_dims(iw: int, ih: int) -> tuple[int, int]:
    """Largest 9:16 window that fits inside the source."""
    cw = min(iw, int(round(ih * OUT_W / OUT_H)))
    ch = min(ih, int(round(iw * OUT_H / OUT_W)))
    return cw - (cw % 2), ch - (ch % 2)


def _scale() -> str:
    return f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H}"


def _tight_fc(cw: int, ch: int, xs) -> str:
    return f"[0:v]crop={cw}:{ch}:x='{_piecewise(xs)}':y=0,{_scale()}[v]"


def _fit_fc() -> str:
    """Blur-pad: whole frame fit into 9:16 over a blurred zoomed copy. Nobody clipped."""
    return (f"[0:v]split[bg][fg];"
            f"[bg]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H},gblur=sigma=22[bgb];"
            f"[fg]scale={OUT_W}:-2[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2[v]")


def _fit_chain() -> str:
    """Blur-pad as a plain -vf chain (no external labels) so callers can append to it."""
    return (f"split[bg][fg];"
            f"[bg]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H},gblur=sigma=22[bgb];"
            f"[fg]scale={OUT_W}:-2[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2")


def subject_x(src: Path, start: float, end: float, iw: int, cw: int,
              fps: float = 6.0, det_w: int = 720) -> int | None:
    """A SINGLE crop-x that frames the dominant subject across the beat (static, no
    pan). Samples frames, detects faces, and returns the size-weighted median face x
    as a crop offset — or None when no subject is reliably present (wide/confetti/
    crowd shots), signalling the caller to blur-pad instead of crop into emptiness."""
    dur = max(0.1, end - start)
    tmp = tempfile.mkdtemp(prefix="clipmframe_")
    try:
        run(["ffmpeg", "-nostdin", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
             "-vf", f"fps={fps},scale={det_w}:-1", "-q:v", "3", os.path.join(tmp, "f_%03d.jpg")])
        frames = sorted(glob.glob(os.path.join(tmp, "f_*.jpg")))
        if not frames:
            return None
        sx = iw / det_w
        hits = []  # (center_x_src, area_det_px)
        for fp in frames:
            img = cv2.imread(fp)
            if img is None:
                continue
            faces = detect_faces(img)  # YuNet (or Haar fallback)
            if faces:
                x, y, w, h, _sc = max(faces, key=lambda f: f[2] * f[3])
                hits.append(((x + w / 2) * sx, w * h))
        # crop whenever a SUBSTANTIAL face exists (one good frame is enough); only
        # blur-pad when there's no real subject — wide stage / confetti / crowd.
        big = [c for c in hits if c[1] >= (det_w * 0.085) ** 2]
        if not big:
            return None
        big.sort(key=lambda c: c[0])
        cx = big[len(big) // 2][0]  # median substantial-face x in source px
        return int(round(min(max(cx - cw / 2, 0), iw - cw)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def facecam_box(src: Path, start: float, end: float, iw: int, ih: int, s: Settings,
                fps: float = 3.0, det_w: int = 960) -> tuple[int, int, int, int] | None:
    """A STATIC 9:16 crop box (x, y, cw, ch) sized to the streamer's facecam.

    Streams put a webcam in a corner (usually lower-right) over a screen-share. The
    default full-height crop keeps all that dead screen space and leaves the face
    tiny at the bottom. Here we detect the dominant face across the clip and cut a
    tight 9:16 window sized to it (crop height = face height * facecam_zoom), centred
    on the face with headroom — so the streamer fills the vertical frame. Static, not
    panned: a facecam sits still, so a fixed box is stabler than chasing sub-pixel
    wobble. Returns None when no face is reliably found (caller falls back)."""
    import statistics
    dur = max(0.1, end - start)
    tmp = tempfile.mkdtemp(prefix="clipface_")
    try:
        run(["ffmpeg", "-nostdin", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
             "-vf", f"fps={fps},scale={det_w}:-1", "-q:v", "3", os.path.join(tmp, "f_%04d.jpg")])
        frames = sorted(glob.glob(os.path.join(tmp, "f_*.jpg")))
        if not frames:
            return None
        sx = iw / det_w  # detection-res -> source px
        faces = []  # (cx, cy, fh) in source px — the biggest face per frame
        for fp in frames:
            img = cv2.imread(fp)
            if img is None:
                continue
            dets = detect_faces(img)  # YuNet (or Haar fallback)
            if not dets:
                continue
            x, y, w, h, _sc = max(dets, key=lambda f: f[2] * f[3])
            faces.append(((x + w / 2) * sx, (y + h / 2) * sx, h * sx))
        # need a real, stable facecam — not one stray detection in a busy screen-share
        if len(faces) < max(2, len(frames) // 4):
            return None
        cx = statistics.median(f[0] for f in faces)
        cy = statistics.median(f[1] for f in faces)
        fh = statistics.median(f[2] for f in faces)
        ch = min(float(ih), max(fh * 1.8, fh * s.facecam_zoom))  # zoom, but never wider than the frame
        cw = ch * OUT_W / OUT_H
        if cw > iw:                       # facecam bigger than a 9:16 slice: clamp width, refit height
            cw, ch = float(iw), iw * OUT_H / OUT_W
        cw = int(cw) - (int(cw) % 2)
        ch = int(ch) - (int(ch) % 2)
        x = int(round(min(max(cx - cw / 2, 0), iw - cw)))
        y = int(round(min(max(cy - ch * s.facecam_headroom, 0), ih - ch)))
        return x, y, cw, ch
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _facecam_fc(x: int, y: int, cw: int, ch: int) -> str:
    return f"[0:v]crop={cw}:{ch}:{x}:{y},{_scale()}[v]"


def _stream(src: Path, start: float, end: float, iw: int, ih: int, cw: int, ch: int,
            s: Settings, words) -> list[tuple[float, float, str]]:
    """Smart stream reframe: SHOW THE WHOLE FRAME while the streamer discusses on-screen
    content (so the code/demo is visible), and CROP INTO THE FACECAM while they go
    off-topic (rant/joke/reaction) so the webcam fills 9:16. Per-sentence decision."""
    from . import screentalk
    dur = end - start
    cache = s.work / f"{src.stem}.screentalk_{start:.1f}_{end:.1f}.json"
    segs = screentalk.spans(words or [], dur, s, cache)
    box = facecam_box(src, start, end, iw, ih, s) if any(m == "talk" for *_, m in segs) else None
    out = []
    for t0, t1, m in segs:
        if m == "talk" and box:
            x, y, bw, bh = box
            out.append((t0, t1, _facecam_fc(x, y, bw, bh)))
        else:  # 'screen', or 'talk' with no detectable face -> show the entire frame
            out.append((t0, t1, _fit_fc()))
    print(f"[stream] {len(segs)} segs: " + "  ".join(f"{a:.0f}-{b:.0f}s {m}" for a, b, m in segs))
    return out


def _center_fc(iw: int, cw: int, ch: int) -> str:
    if cw >= iw:
        return f"[0:v]{_scale()}[v]"
    return f"[0:v]crop={cw}:{ch}:{(iw - cw) // 2}:0,{_scale()}[v]"


def plan(src: Path, start: float, end: float, iw: int, ih: int, s: Settings,
         words: list[dict] | None = None) -> list[tuple[float, float, str]]:
    """Return reframe SEGMENTS [(t0, t1, filter_complex)] (clip-relative). One segment
    for center/track-fast/track-corr; the default 'track' uses LR-ASD smart-fit:
    tight-crop on the speaker for single-subject shots, blur-pad fit for wide/multi
    shots so nobody is ever clipped."""
    cw, ch = _crop_dims(iw, ih)
    dur = end - start
    center = (0.0, dur, _center_fc(iw, cw, ch))
    if cw >= iw and ch >= ih:
        return [center]
    if s.reframe == "center":
        return [center]
    if s.reframe == "facecam":
        box = facecam_box(src, start, end, iw, ih, s)
        if not box:
            print("[facecam] no stable face found — falling back to center crop")
            return [center]
        x, y, bw, bh = box
        return [(0.0, dur, _facecam_fc(x, y, bw, bh))]
    if s.reframe == "stream":
        return _stream(src, start, end, iw, ih, cw, ch, s, words)
    if s.reframe == "track-fast":
        xs = _face_track(src, start, end, iw, cw)
        return [(0.0, dur, _tight_fc(cw, ch, xs))] if xs else [center]
    if s.reframe == "track-corr":
        from . import speaker
        xs, _ = speaker.track(src, start, end, iw, cw)
        return [(0.0, dur, _tight_fc(cw, ch, xs))] if xs else [center]
    # default "track": LR-ASD smart-fit
    from . import asd
    if not asd.available():
        return [center]
    _fps, segs = asd.plan(src, start, end, iw, cw)
    if not segs:
        return [center]
    out = []
    for t0, t1, m, kf in segs:
        out.append((t0, t1, _tight_fc(cw, ch, kf) if (m == "tight" and kf) else _fit_fc()))
    return out


def build(src: Path, start: float, end: float, iw: int, ih: int, s: Settings, words: list[dict] | None = None) -> str:
    cw, ch = _crop_dims(iw, ih)
    if cw >= iw and ch >= ih:
        return _scale()  # source already <=9:16, just fit
    if s.reframe == "center":
        x = (iw - cw) // 2
        return f"crop={cw}:{ch}:{x}:0,{_scale()}"
    return _track(src, start, end, iw, ih, cw, ch, s, words)


def _name_events(words, roster) -> list[tuple[float, int]]:
    """When a panelist's name is spoken, (t_rel, their_seat_x) — for subject framing."""
    if not words or not roster:
        return []
    firsts = [(p["name"].split()[0].lower(), p["x_px"]) for p in roster if p.get("name")]
    ev = []
    for w in words:
        tok = w["text"].lower().strip(".,!?'\"")
        for fn, x in firsts:
            if len(fn) >= 3 and (tok == fn or (len(tok) >= 4 and (tok[:4] == fn[:4]))):
                ev.append((w["start"], x))
                break
    return ev


def _track(src, start, end, iw, ih, cw, ch, s, words=None) -> str:
    if s.reframe == "track-fast":
        xs = _face_track(src, start, end, iw, cw)        # heuristic: largest mouth-motion
    elif s.reframe == "track-corr":
        from . import speaker                            # audio-energy correlation
        xs, _ = speaker.track(src, start, end, iw, cw)
    else:                                                # default "track": LR-ASD model
        from . import asd
        # NOTE: roster seats are NOT used for crop positioning — the source is
        # multi-camera, so a person's pixel position changes per shot and a fixed
        # seat is stale. Raw LR-ASD gives the correct per-frame speaker position.
        xs = asd.track(src, start, end, iw, cw) if asd.available() else []
    if not xs:
        x = (iw - cw) // 2
        return f"crop={cw}:{ch}:{x}:0,{_scale()}"
    expr = _piecewise(xs)
    return f"crop={cw}:{ch}:x='{expr}':y=0,{_scale()}"


def _face_track(src: Path, start: float, end: float, iw: int, cw: int,
                fps: float = 5.0, smooth: int = 5, det_w: int = 720) -> list[tuple[float, int]]:
    """Sample faces in [start,end]; return [(t_rel, crop_x)] keyframes that center
    on the ACTIVE speaker.

    Frames are pulled via ffmpeg (software decode, works on AV1). For each sampled
    frame we detect faces and pick the one whose mouth region is moving most
    (frame-to-frame) — i.e. who is talking — and center the crop on them. This
    keeps the speaker framed on a multi-person panel instead of locking on whoever
    happens to sit in the middle. cv2.VideoCapture can't decode AV1, hence ffmpeg.
    """
    MOUTH_MOTION_MIN = 2.5  # mean abs luma delta in the mouth patch to count as "talking"
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    dur = end - start
    tmp = tempfile.mkdtemp(prefix="cliptrack_")
    try:
        run(["ffmpeg", "-nostdin", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
             "-vf", f"fps={fps},scale={det_w}:-1", "-q:v", "3", os.path.join(tmp, "f_%05d.jpg")])
        frames = sorted(glob.glob(os.path.join(tmp, "f_*.jpg")))
        sx = iw / det_w  # map detection-resolution x back to source pixels
        max_step = iw * 0.12  # responsive pan — keep up with the active speaker
        centers: list[float] = []
        last = iw / 2.0
        active_cx = iw / 2.0  # current active-speaker center, in source px
        prev_gray = None
        for fp in frames:
            img = cv2.imread(fp)
            if img is None:
                centers.append(last)
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, 1.2, 5, minSize=(40, 40))
            chosen = None
            if len(faces):
                biggest = max(f[2] * f[3] for f in faces)
                near = [f for f in faces if f[2] * f[3] >= 0.25 * biggest]
                if prev_gray is not None and len(near) > 1:
                    best_motion = -1.0
                    for (x, y, w, h) in near:
                        mx, my = max(0, int(x + 0.25 * w)), max(0, int(y + 0.58 * h))
                        mw, mh = int(0.5 * w), int(0.34 * h)
                        pn = gray[my:my + mh, mx:mx + mw]
                        pp = prev_gray[my:my + mh, mx:mx + mw]
                        if pn.size == 0 or pn.shape != pp.shape:
                            continue
                        m = float(np.mean(np.abs(pn.astype(np.int16) - pp.astype(np.int16))))
                        if m > best_motion:
                            best_motion, chosen = m, (x, y, w, h)
                    if best_motion < MOUTH_MOTION_MIN:  # nobody clearly talking — hold on last speaker
                        chosen = min(near, key=lambda f: abs((f[0] + f[2] / 2) * sx - active_cx))
                else:
                    chosen = max(near, key=lambda f: f[2] * f[3])  # single subject: center on them
            if chosen is not None:
                active_cx = (chosen[0] + chosen[2] / 2) * sx
            last += max(-max_step, min(max_step, active_cx - last))  # ease toward speaker, clamped
            centers.append(last)
            prev_gray = gray
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if not centers:
        return []

    # 1€-filter smooth (low lag, no rubber-banding), then map face center -> crop x
    oef = OneEuroFilter(freq=max(1.0, fps), mincutoff=0.7, beta=0.012)
    sm = [oef(c) for c in centers]
    half = cw / 2
    kf: list[tuple[float, int]] = []
    for i, c in enumerate(sm):
        t = dur * i / max(1, len(sm) - 1)
        x = int(round(min(max(c - half, 0), iw - cw)))
        kf.append((round(t, 2), x))
    return _decimate(kf)


def _smooth(vals: list[float], k: int) -> list[float]:
    if k <= 1:
        return vals
    out, n = [], len(vals)
    for i in range(n):
        lo, hi = max(0, i - k // 2), min(n, i + k // 2 + 1)
        out.append(sum(vals[lo:hi]) / (hi - lo))
    return out


def _decimate(kf: list[tuple[float, int]], tol: int = 12, cap: int = 24) -> list[tuple[float, int]]:
    """Drop keyframes whose x barely moved; cap total count so the ffmpeg crop
    expression stays small and the pan stays gentle."""
    def thin(t_):
        out = [kf[0]]
        for t, x in kf[1:-1]:
            if abs(x - out[-1][1]) >= t_:
                out.append((t, x))
        out.append(kf[-1])
        return out
    out = thin(tol)
    while len(out) > cap:
        tol = int(tol * 1.6) + 1
        out = thin(tol)
    return out


def _piecewise(kf: list[tuple[float, int]]) -> str:
    """Linear-interpolated x(t) expression over clip-relative time t."""
    if len(kf) == 1:
        return str(kf[0][1])
    e = str(kf[-1][1])
    for i in range(len(kf) - 2, -1, -1):
        t0, x0 = kf[i]
        t1, x1 = kf[i + 1]
        seg = f"({x0}+{x1 - x0}*(t-{t0})/{t1 - t0})"
        e = f"if(lt(t,{t1}),{seg},{e})"
    return f"if(lt(t,{kf[0][0]}),{kf[0][1]},{e})"
