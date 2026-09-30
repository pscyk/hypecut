"""Active-speaker reframing via LR-ASD (runs in the isolated .venv-asd).

Calls vendor/LR-ASD/asd_infer.py as a subprocess (torch lives in its own venv so
it can't disturb the whisper env), reads per-frame active-speaker centers, and
turns them into a smooth crop-x track:

  - pick the speaker only when ASD confidence is real (conf > CONF_MIN),
  - over a short window choose the *dominant* speaker (sum of confidence), so a
    half-second crosstalk blip doesn't yank the frame,
  - hold the last speaker through laughter / reaction shots (no chasing),
  - ease toward the target with a velocity clamp, then smooth + decimate.
"""
from __future__ import annotations
import json
import os
import subprocess
import tempfile
from pathlib import Path
from .config import ROOT
from .reframe import _smooth, _decimate

ASD_PY = Path(os.getenv("HYPECUT_ASD_PY", str(ROOT / ".venv-asd" / "bin" / "python"))).expanduser()
ASD_DIR = Path(os.getenv("HYPECUT_ASD_DIR", str(ROOT / "vendor" / "LR-ASD"))).expanduser()
ASD_SCRIPT = ASD_DIR / "asd_infer.py"
CONF_MIN = 0.0  # LR-ASD score > 0 ~ speaking


def available() -> bool:
    return ASD_PY.exists() and ASD_SCRIPT.exists()


def _infer(src: Path, start: float, end: float) -> dict | None:
    cache = ROOT / "work" / f"{src.stem}.asd_{start:.1f}_{end:.1f}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        return json.loads(cache.read_text())
    save = tempfile.mkdtemp(prefix="asd_")
    # NOTE: out must live OUTSIDE `save` — asd_infer.py rmtree()s savePath after
    # writing --out, so an --out inside `save` gets deleted before we can read it.
    out = Path(save + "_out.json")
    try:
        cp = subprocess.run(
            [str(ASD_PY), "asd_infer.py",
             "--videoPath", str(src.resolve()), "--start", f"{start:.3f}", "--duration", f"{end - start:.3f}",
             "--savePath", save, "--out", str(out)],
            cwd=str(ASD_DIR), capture_output=True, text=True,
        )
        if cp.returncode != 0 or not out.exists():
            print(f"[asd] inference failed: {cp.stderr.strip().splitlines()[-1:] }")
            return None
        data = json.loads(out.read_text())
        cache.write_text(json.dumps(data))  # cache so framing tweaks skip re-running the model
        return data
    finally:
        import shutil
        shutil.rmtree(save, ignore_errors=True)
        out.unlink(missing_ok=True)


def _smooth_modes(mode: list[str], min_tight: int, min_fit: int) -> list[str]:
    """Dissolve too-short runs into a neighbour to avoid flicker — but ASYMMETRIC:
    blur-pad (fit) shots are the safe multi-person framing, so keep them even when
    brief (min_fit small); only brief TIGHT pops get merged away (min_tight large)."""
    if not mode:
        return mode
    runs, s = [], 0
    for i in range(1, len(mode) + 1):
        if i == len(mode) or mode[i] != mode[s]:
            runs.append([s, i, mode[s]])
            s = i
    for _ in range(6):
        changed = False
        for idx in range(len(runs)):
            a, b, m = runs[idx]
            floor = min_tight if m == "tight" else min_fit
            if b - a < floor:
                nb = runs[idx - 1] if idx > 0 else (runs[idx + 1] if idx + 1 < len(runs) else None)
                if nb is not None and nb[2] != runs[idx][2]:
                    runs[idx][2] = nb[2]
                    changed = True
        merged = []
        for a, b, m in runs:
            if merged and merged[-1][2] == m:
                merged[-1][1] = b
            else:
                merged.append([a, b, m])
        runs = merged
        if not changed:
            break
    out = []
    for a, b, m in runs:
        out += [m] * (b - a)
    return out


def plan(src: Path, start: float, end: float, iw: int, cw: int):
    """Smart-fit plan: returns (fps, segments). Each segment is
    (t0, t1, 'tight', keyframes) — crop on the speaker — or (t0, t1, 'fit', None) —
    wide shot (2+ faces spread wider than a tight crop, or no face): show the whole
    frame blur-padded so nobody is clipped."""
    data = _infer(src, start, end)
    if not data or not data.get("centers"):
        return 25.0, []
    fps = data["fps"]
    rows = data["centers"]
    n = len(rows)

    def cell(i):
        r = rows[i]
        x = r[1]
        conf = r[2]
        nf = r[3] if len(r) > 3 else (1 if x is not None else 0)
        span = r[4] if len(r) > 4 else 0
        return x, conf, nf, span

    from .reframe import _smooth
    confs = [(cell(i)[1] if cell(i)[1] is not None else -9.0) for i in range(n)]
    xpos = [cell(i)[0] for i in range(n)]

    # smoothed LOCAL confidence (don't bleed speech across a cut)
    kc = max(1, int(0.4 * fps))
    sconf = [sum(confs[max(0, i - kc):min(n, i + kc + 1)]) / len(confs[max(0, i - kc):min(n, i + kc + 1)])
             for i in range(n)]
    # speaker-position INSTABILITY: is the "active speaker" bouncing between people?
    kp = max(1, int(0.5 * fps))
    unstable = []
    for i in range(n):
        pts = [xpos[j] for j in range(max(0, i - kp), min(n, i + kp + 1))
               if xpos[j] is not None and confs[j] > 0.2]
        if len(pts) >= 3:
            m = sum(pts) / len(pts)
            std = (sum((p - m) ** 2 for p in pts) / len(pts)) ** 0.5
            unstable.append(std > iw * 0.16)
        else:
            unstable.append(False)

    # tight ONLY when clearly speaking AND the subject is stable (not bouncing);
    # else blur-pad fit. A bouncing/ambiguous exchange becomes a calm wide shot, not a whip.
    ENTER, EXIT = 0.6, -0.2
    mode, cur = [], "fit"
    for i in range(n):
        if cur == "fit" and sconf[i] > ENTER and not unstable[i]:
            cur = "tight"
        elif cur == "tight" and (sconf[i] < EXIT or unstable[i]):
            cur = "fit"
        mode.append(cur)
    # keep blur-pad shots down to ~1.2s; only merge tight pops shorter than ~2.5s
    mode = _smooth_modes(mode, int(2.5 * fps), int(1.2 * fps))

    # target = windowed MEDIAN of confident speaker x (robust to brief bounces),
    # gentle velocity clamp + heavy smoothing => cinematic pans, never whips
    wf = max(1, int(0.7 * fps))
    half, max_step = cw / 2, iw * 0.035
    last, last_t = iw / 2.0, iw / 2.0
    raw = []
    for i in range(n):
        pts = sorted(xpos[j] for j in range(max(0, i - wf), min(n, i + wf + 1))
                     if xpos[j] is not None and confs[j] > 0.3)
        if pts:
            last_t = pts[len(pts) // 2]
        last += max(-max_step, min(max_step, last_t - last))
        raw.append(last)
    raw = _smooth(raw, 9)
    cropx = [int(round(min(max(c - half, 0), iw - cw))) for c in raw]

    from .reframe import _decimate
    segs, i = [], 0
    while i < n:
        j = i
        while j < n and mode[j] == mode[i]:
            j += 1
        t0, t1 = round(i / fps, 3), round(j / fps, 3)
        if mode[i] == "tight":
            kf = _decimate([((k - i) / fps, cropx[k]) for k in range(i, j)])
            segs.append((t0, t1, "tight", kf))
        else:
            segs.append((t0, t1, "fit", None))
        i = j
    return fps, segs


NAME_HOLD = 1.6  # seconds to favor a named subject after their name is spoken


def track(src: Path, start: float, end: float, iw: int, cw: int,
          seats: list[float] | None = None,
          name_events: list[tuple[float, int]] | None = None) -> list[tuple[float, int]]:
    """`seats` (source-px x of each roster person) enables seat-snapping + two-shot.
    `name_events` [(t_rel, seat_x)] enables subject-aware framing: when a panelist is
    named ('a 10 from Alia'), briefly cut to that person's seat (their reaction)
    instead of the announcer. Subject override wins over the active speaker."""
    data = _infer(src, start, end)
    if not data or not data.get("centers"):
        return []
    fps = data["fps"]
    centers = data["centers"]  # [[t, x|None, conf|None], ...]
    n = len(centers)

    # precompute per-frame subject (named person) override
    subject = [None] * n
    for te, sx in (name_events or []):
        for f in range(max(0, int(te * fps)), min(n, int((te + NAME_HOLD) * fps) + 1)):
            subject[f] = sx  # later mention wins
    wf = max(1, int(0.5 * fps))           # +/- 0.5s window for dominant speaker
    gap = iw * 0.08                        # cluster width (seat separation)
    half = cw / 2
    max_step = iw * 0.10                   # pan speed cap

    last_target = iw / 2.0
    last = iw / 2.0
    raw: list[float] = []
    for i in range(n):
        lo, hi = max(0, i - wf), min(n, i + wf + 1)
        cands = [(r[1], r[2]) for r in centers[lo:hi]  # rows are [t, x, conf, nf, span]
                 if r[1] is not None and r[2] is not None and r[2] > CONF_MIN]
        conf_by_seat = {}
        if cands and seats:
            tol = cw * 0.45  # only snap when the speaker is actually AT a panel seat
            for x, c in cands:
                si = min(range(len(seats)), key=lambda k: abs(seats[k] - x))
                if abs(seats[si] - x) <= tol:
                    conf_by_seat[si] = conf_by_seat.get(si, 0.0) + c
        if conf_by_seat:
            top = sorted(conf_by_seat.items(), key=lambda kv: kv[1], reverse=True)
            # two-shot ONLY when both seats are close enough that both faces fully fit
            # (gap small) AND both are clearly speaking — otherwise the speaker gets
            # clipped at the edge. Wide panel spacing falls through to single-speaker.
            two = (len(top) >= 2 and top[1][1] >= 0.7 * top[0][1]
                   and abs(seats[top[0][0]] - seats[top[1][0]]) <= cw * 0.40)
            if two:
                last_target = (seats[top[0][0]] + seats[top[1][0]]) / 2   # two-shot midpoint
            else:
                last_target = seats[top[0][0]]                            # snap to the speaker
        elif cands:  # no roster, or speaker not at a panel seat (e.g. standup)
            cands.sort()
            cl, cur = [], [cands[0]]
            for x, c in cands[1:]:
                if x - cur[-1][0] <= gap:
                    cur.append((x, c))
                else:
                    cl.append(cur); cur = [(x, c)]
            cl.append(cur)
            best = max(cl, key=lambda s: sum(c for _, c in s))
            last_target = sum(x for x, _ in best) / len(best)
        # else: hold last_target (laughter / off-frame speaker)
        if subject[i] is not None:
            last_target = subject[i]   # subject-aware: cut to the named person
        last += max(-max_step, min(max_step, last_target - last))
        raw.append(last)

    sm = _smooth(raw, 5)
    kf = []
    for i, c in enumerate(sm):
        t = i / fps
        x = int(round(min(max(c - half, 0), iw - cw)))
        kf.append((round(t, 2), x))
    return _decimate(kf)
