"""Montage mode — the renderer.

Schedules each EDL beat onto the music's beat grid (so every cut lands on a beat),
renders a normalized 9:16 piece per beat, then a single ffmpeg pass concatenates
them, mixes the music bed under the original audio, and burns the kinetic title card.
"""
from __future__ import annotations
import statistics
from pathlib import Path
from . import reframe, titlecard, music, montage_caps
from .config import Settings, OUT_W, OUT_H, run
from .runtime import encoder_args


# how long each beat HOLDS, by kind — action/payoff beats breathe, talking beats are tight.
KIND_WEIGHT = {"hook": 1.0, "visual_gag": 1.9, "finale": 1.9, "reaction": 1.35, "bit": 1.0}


def _schedule(beats: list[dict], grid: list[float], s: Settings, target_secs: float) -> list[tuple[dict, float]]:
    """Give each beat a whole number of music-beat intervals (cuts land on the beat),
    sized to FILL target_secs and WEIGHTED by kind so performance/payoff beats hold
    longer instead of everything flashing past as 0.5s face-cuts."""
    iv = [grid[i + 1] - grid[i] for i in range(len(grid) - 1) if grid[i + 1] > grid[i]]
    med = statistics.median(iv) if iv else 0.5

    def snap(d: float, lo: int = 1) -> float:  # nearest whole number of beats, >= lo
        return max(lo, round(d / med)) * med

    weights = [KIND_WEIGHT.get(b.get("kind"), 1.0) for b in beats]
    unit = max(med, target_secs) / (sum(weights) or 1)
    sched = []
    for b, w in zip(beats, weights):
        lo = 3 if b.get("kind") in ("visual_gag", "finale") else 2  # action beats >= ~1s
        d = snap(unit * w, lo)
        d = min(d, (b["source_end"] - b["source_start"]) + 0.5)  # don't freeze past the moment
        sched.append((b, round(max(med, d), 3)))
    total = sum(d for _, d in sched)
    print(f"[montage] phase-2 scheduled {len(sched)} beats, ~{total:.1f}s (~{med:.2f}s/beat)")
    return sched


def _aftermath_beats(hero_end: float, grid: list[float], s: Settings) -> list[tuple[float, float]]:
    """The first FEW cuts after the mic-drop — the reaction ON the hero/moment. Only a
    handful, SPACED well apart so they're distinct reaction moments (not adjacent
    near-identical frames flashing). The rest of phase 2 is the varied episode montage."""
    iv = [grid[i + 1] - grid[i] for i in range(len(grid) - 1) if grid[i + 1] > grid[i]]
    med = statistics.median(iv) if iv else 0.5
    out, t = [], hero_end + 0.2
    for _ in range(max(0, s.m_aftermath)):
        out.append((round(t, 3), round(med, 3)))
        t += med * 3.5  # wide spacing -> each is a genuinely different moment of the reaction
    return out


def _cut_start(b: dict, dur: float, peaks: list[float]) -> float:
    """Where to start the slice inside the moment. Entrances/reveals/finales play from
    their START (the walk-out IS the moment). Reaction/bit beats center on the
    laughter PEAK if one falls inside (the payoff); else trust the bracket start."""
    ss, se = b["source_start"], b["source_end"]
    if se - ss <= dur:
        return ss
    if b.get("kind") in ("hook", "visual_gag", "finale"):
        return ss
    inside = sorted(p for p in peaks if ss + 0.2 <= p <= se)
    if inside:
        peak = inside[len(inside) // 2]
        return round(max(ss, min(peak - dur * 0.55, se - dur)), 3)
    return ss


# transition cycle: whip-blur, zoom-blur, directional slides, occasional white flash.
TRANSITIONS = ["zoomin", "hblur", "smoothright", "fadewhite", "hblur", "smoothleft",
               "zoomin", "smoothup", "hblur", "fadewhite", "smoothright", "zoomin"]


def _style_chain(s: Settings, dur: float) -> str:
    """The 'produced' edit texture in ONE zoompan: punchy grade, a steady punch-in
    zoom, and a damped on-beat shake (the zoom base gives the shake margin so frame
    edges never show)."""
    n = max(2, int(dur * s.fps))
    f = s.fps
    base, zmax, a = 1.0 + max(s.m_zoom, 0.03), 1.0 + s.m_zoom + 0.05, s.m_shake_px
    z = f"min({base:.3f}+{s.m_zoom}*on/{n},{zmax:.3f})"
    sx = f"iw/2-(iw/zoom/2)+{a}*sin(40*on/{f})*exp(-9*on/{f})"
    sy = f"ih/2-(ih/zoom/2)+{a}*cos(34*on/{f})*exp(-9*on/{f})"
    grade = f"eq=contrast={s.m_contrast}:saturation={s.m_saturation},curves=preset=increase_contrast"
    # IMPORTANT: upsample to fps BEFORE zoompan. zoompan with d=1 emits one frame per
    # INPUT frame; if the source is 30fps and we ask for 60fps output it just re-stamps
    # 30 frames at 60fps -> 2x speed. Duplicating to fps first keeps real-time speed.
    zp = f"zoompan=z='{z}':x='{sx}':y='{sy}':d=1:s={OUT_W}x{OUT_H}:fps={s.fps}"
    return f"{grade},fps={s.fps},{zp}"


def _frame_fc(src: Path, start: float, dur: float, iw: int, ih: int, s: Settings) -> str:
    """9:16 framing for ONE beat: static-crop onto the detected subject; if no clear
    subject (wide/confetti/crowd), blur-pad the whole shot so nobody is cropped out."""
    cw, ch = reframe._crop_dims(iw, ih)
    if cw >= iw and ch >= ih:
        return reframe._scale()
    x = reframe.subject_x(src, start, start + dur, iw, cw)
    if x is None:
        return reframe._fit_chain()  # nobody clearly in frame -> show it all, blur-padded
    return f"crop={cw}:{ch}:{x}:0,{reframe._scale()}"


def _render_piece(src: Path, start: float, dur: float, iw: int, ih: int,
                  s: Settings, out: Path) -> Path:
    """One normalized 9:16 beat: reframe (+ style texture) + fixed fps/sar/pixfmt so
    pieces xfade/concat cleanly. Rendered ~half-a-transition longer so xfade has
    overlap material to consume on both sides."""
    fc = _frame_fc(src, start, dur, iw, ih, s)
    style = ("," + _style_chain(s, dur)) if s.mstyle else ""
    vf = f"{fc}{style},fps={s.fps},setsar=1,format=yuv420p"
    venc = encoder_args(s.vcodec, s.crf)
    run(["ffmpeg", "-nostdin", "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
         "-vf", vf, "-af", "aresample=44100", "-ac", "2",
         *venc, "-c:a", "aac", "-b:a", "160k", "-ar", "44100", str(out)])
    return out


def _probe_dur(p: Path) -> float:
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "csv=p=0", str(p)]).stdout.strip())


def _esc(p) -> str:
    return str(p).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _render_hero(src: Path, hero: dict, words: list[dict], iw: int, ih: int,
                 s: Settings, out: Path) -> tuple[Path, float]:
    """Phase 1: the setup->punchline bit, played near-full with word-by-word captions
    (punch word in glass), dialogue forward. No flash/shake — this beat must read clean."""
    import copy
    he = hero["source_end"]  # END on the punchline/mic-drop
    hs = hero["source_start"]
    if s.hero_secs > 0 and (he - hs) > s.hero_secs:  # too long: keep the last hero_secs up to the punchline
        hs = he - s.hero_secs
    dur = max(2.0, he - hs)
    venc = encoder_args(s.vcodec, s.crf)

    # HEAD-PIN on the ACTIVE SPEAKER: LR-ASD knows who is talking on a panel, so it
    # crops to the speaker (not the biggest/centered face). Uses reframe.plan — the
    # working ASD path the clip pipeline uses — and renders the body (segments concat).
    st = copy.copy(s)
    st.reframe = "track"
    segs = reframe.plan(src, hs, he, iw, ih, st)
    body = s.work / "montage_hero_body.mp4"
    if len(segs) == 1:
        run(["ffmpeg", "-nostdin", "-y", "-ss", f"{hs:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
             "-filter_complex", segs[0][2], "-map", "[v]", "-map", "0:a",
             *venc, "-c:a", "aac", "-b:a", "160k", "-ar", "44100", str(body)])
    else:
        parts = []
        for si, (t0, t1, fc) in enumerate(segs):
            part = s.work / f"montage_hero_seg{si:02d}.mp4"
            run(["ffmpeg", "-nostdin", "-y", "-ss", f"{hs + t0:.3f}", "-i", str(src), "-t", f"{t1 - t0:.3f}",
                 "-filter_complex", fc, "-map", "[v]", "-map", "0:a",
                 *venc, "-c:a", "aac", "-b:a", "160k", "-ar", "44100", str(part)])
            parts.append(part)
        listf = s.work / "montage_hero.concat.txt"
        listf.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
        run(["ffmpeg", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(listf), "-c", "copy", str(body)])
        for p in (*parts, listf):
            p.unlink(missing_ok=True)

    # second pass: grade + word-by-word captions (glass punch word) + 60fps
    rel = [{"text": w["text"], "start": max(0.0, w["start"] - hs),
            "end": max(0.0, min(w["end"], hs + dur) - hs)}
           for w in words if w["end"] > hs and w["start"] < hs + dur]
    ass = montage_caps.build_ass(rel, hero.get("punch_word", ""), s.work / "montage_hero.ass", s)
    sub = f"subtitles=filename='{_esc(ass)}':fontsdir='{_esc(s.fonts_dir)}'"
    grade = f"eq=contrast={s.m_contrast}:saturation={s.m_saturation},curves=preset=increase_contrast"
    vf = f"{grade},{sub},fps={s.fps},setsar=1,format=yuv420p"
    run(["ffmpeg", "-nostdin", "-y", "-i", str(body), "-vf", vf, *venc, "-c:a", "copy", str(out)])
    body.unlink(missing_ok=True)
    print(f"[montage] phase-1 hero {hs:.1f}-{hs+dur:.1f} ({dur:.1f}s) speaker-framed, punch='{hero.get('punch_word')}'")
    return out, _probe_dur(out)


def _assemble(hero_piece: Path | None, hero_dur: float, mpieces: list[Path], grid: list[float],
              edl: dict, caption: str, idx: int, track: Path, s: Settings) -> Path:
    """One mic-drop reel: [hero_piece] + the shared montage pieces -> xfade chain + the
    music drop + title card -> out/micdrop_0{idx}.mp4."""
    pieces = ([hero_piece] if hero_piece else []) + mpieces
    durs = [_probe_dur(p) for p in pieces]
    n = len(pieces)
    has_hero = hero_piece is not None

    inputs = []
    for p in pieces:
        inputs += ["-i", str(p)]
    inputs += ["-ss", f"{grid[0]:.3f}", "-i", str(track)]
    m = n  # music input index

    if s.m_transitions and n > 1:
        T = min(s.m_trans_dur, min(durs) * 0.6)
        vparts, aparts = [], []
        vprev, aprev, off = "[0:v]", "[0:a]", 0.0
        for j in range(1, n):
            tr = "fadewhite" if (has_hero and j == 1) else TRANSITIONS[(j - 1) % len(TRANSITIONS)]
            off += durs[j - 1] - T
            vout = f"[vx{j}]" if j < n - 1 else "[cv]"
            aout = f"[ax{j}]" if j < n - 1 else "[ca]"
            vparts.append(f"{vprev}[{j}:v]xfade=transition={tr}:duration={T:.3f}:offset={off:.3f}{vout}")
            aparts.append(f"{aprev}[{j}:a]acrossfade=d={T:.3f}{aout}")
            vprev, aprev = vout, aout
        total = sum(durs) - (n - 1) * T
        drop_t = hero_dur - T if has_hero else 0.0
        chain = ";".join(vparts + aparts) + ";"
    else:
        chain = "".join(f"[{i}:v][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[cv][ca];"
        total = sum(durs)
        drop_t = hero_dur

    ass = titlecard.build_ass(edl.get("title_card", []), s.hook_secs,
                              s.work / f"montage_title_{idx:02d}.ass", s, offset=drop_t + 0.05)
    if has_hero:
        orig_env = f"volume=volume='if(lt(t,{drop_t:.3f}),{s.orig_vol},0.5)':eval=frame"
        mus_env = f"volume=volume='if(lt(t,{drop_t:.3f}),{s.music_build_vol},{s.music_vol})':eval=frame"
    else:
        orig_env, mus_env = f"volume={s.orig_vol}", f"volume={s.music_vol}"

    # force yuv420p — xfade/subtitles can promote to 4:4:4 (unplayable on IG/phones).
    fc = (chain +
          f"[cv]subtitles=filename='{_esc(ass)}':fontsdir='{_esc(s.fonts_dir)}',format=yuv420p[v];"
          f"[ca]{orig_env}[a0];"
          f"[{m}:a]{mus_env}[a1];"
          f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[am]")
    out = s.out / f"micdrop_{idx:02d}.mp4"
    venc = ["-c:v", s.vcodec, "-rc", "vbr", "-cq", s.crf, "-b:v", "0", "-preset", "p5", "-pix_fmt", "yuv420p"]
    run(["ffmpeg", "-nostdin", "-y", *inputs, "-filter_complex", fc,
         "-map", "[v]", "-map", "[am]", "-t", f"{total:.3f}",
         *venc, "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart", str(out)])
    tags = " ".join("#" + t.lstrip("#") for t in edl.get("hashtags", []))
    (s.out / f"micdrop_{idx:02d}.caption.txt").write_text((caption + "\n\n" + tags).strip() + "\n", encoding="utf-8")
    print(f"[montage] reel {idx} -> {out.name}  ({total:.1f}s, hero {hero_dur:.1f}s + drop)")
    return out


def render(src: Path, edl: dict, words: list[dict], iw: int, ih: int, s: Settings) -> list[Path]:
    track = music.resolve_track(s.music, s)
    _bpm, grid = music.beat_grid(track)
    if len(grid) < 2:
        grid = [i * 0.5 for i in range(64)]
    peaks = getattr(s, "reaction_peaks", []) or []

    # SHARED varied episode montage (distinct moments — drummer, dancer, other bits),
    # rendered ONCE. This is the bulk of phase 2; per-hero aftermath gets prepended.
    iv = [grid[i + 1] - grid[i] for i in range(len(grid) - 1) if grid[i + 1] > grid[i]]
    med = statistics.median(iv) if iv else 0.5
    montage_target = max(med, s.montage_secs - (s.hero_secs if s.hero_secs > 0 else 0) - s.m_aftermath * med)
    sched = _schedule(edl["beats"], grid, s, montage_target)
    shared = []
    for i, (b, dur) in enumerate(sched):
        p = s.work / f"montage_piece_{i:02d}.mp4"
        try:
            _render_piece(src, _cut_start(b, dur, peaks), dur, iw, ih, s, p)
            shared.append(p)
        except Exception as e:
            print(f"[montage] piece {i:02d} failed ({e})")

    heroes = (edl.get("heroes") or []) if s.hero_secs > 0 else []
    outs = []
    if not heroes:  # fallback: no hero -> just the varied montage
        if shared:
            outs.append(_assemble(None, 0.0, shared, grid, edl, edl.get("caption", ""), 0, track, s))
        for p in shared:
            p.unlink(missing_ok=True)
        return outs

    for idx, hero in enumerate(heroes):
        hp = s.work / f"montage_hero_{idx:02d}.mp4"
        try:
            _, hd = _render_hero(src, hero, words, iw, ih, s, hp)
        except Exception as e:
            print(f"[montage] hero {idx} failed ({e}); skipping")
            continue
        # first few cuts = ON-THE-HERO reaction (the aftermath), then the varied montage
        apieces = []
        for i, (st, dur) in enumerate(_aftermath_beats(hero["source_end"], grid, s)):
            p = s.work / f"after_{idx:02d}_{i:02d}.mp4"
            try:
                _render_piece(src, st, dur, iw, ih, s, p)
                apieces.append(p)
            except Exception as e:
                print(f"[montage] reel {idx} aftermath {i} failed ({e})")
        cap = hero.get("caption") or edl.get("caption", "")
        outs.append(_assemble(hp, hd, apieces + shared, grid, edl, cap, idx, track, s))
        for p in [hp, *apieces]:
            p.unlink(missing_ok=True)
    for p in shared:
        p.unlink(missing_ok=True)
    print(f"[montage] done: {len(outs)} mic-drop reel(s) in {s.out}")
    return outs
