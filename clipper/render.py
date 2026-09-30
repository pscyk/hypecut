"""Step 3+4+5+6: cut, reframe, burn captions, encode — one ffmpeg pass per clip."""
from __future__ import annotations
from pathlib import Path
from . import reframe, captions
from .config import Settings, run
from .runtime import encoder_args


def _rel_words(words: list[dict], start: float, end: float) -> list[dict]:
    out = []
    for w in words:
        if w["end"] <= start or w["start"] >= end:
            continue
        out.append({
            "text": w["text"],
            "start": max(0.0, w["start"] - start),
            "end": max(0.0, min(w["end"], end) - start),
        })
    return out


def _esc(p: Path) -> str:
    return str(p).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _render_piece(src: Path, start: float, end: float, words: list[dict],
                  iw: int, ih: int, s: Settings, out: Path, tag: str) -> Path:
    """Render one continuous [start,end] piece: smart-fit reframe + burned captions."""
    rel = _rel_words(words, start, end)
    ass = captions.build_ass(rel, s.work / f"{tag}.ass", s)
    sub = f"subtitles=filename='{_esc(ass)}':fontsdir='{_esc(s.fonts_dir)}'"
    segments = reframe.plan(src, start, end, iw, ih, s, rel)
    venc = encoder_args(s.vcodec, s.crf)
    if len(segments) == 1:  # fast path: one reframe + captions in a single pass
        _, _, fc = segments[0]
        run(["ffmpeg", "-nostdin", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
             "-filter_complex", f"{fc};[v]{sub}[vo]", "-map", "[vo]", "-map", "0:a",
             *venc, "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)])
        return out
    parts = []  # smart-fit: render each segment (tight/blur-pad), concat, burn captions
    for si, (t0, t1, fc) in enumerate(segments):
        part = s.work / f"{tag}_seg{si:02d}.mp4"
        run(["ffmpeg", "-nostdin", "-y", "-ss", f"{start + t0:.3f}", "-to", f"{start + t1:.3f}", "-i", str(src),
             "-filter_complex", f"{fc}", "-map", "[v]", "-map", "0:a",
             *venc, "-c:a", "aac", "-b:a", "160k", str(part)])
        parts.append(part)
    listf = s.work / f"{tag}.concat.txt"
    listf.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    body = s.work / f"{tag}.body.mp4"
    run(["ffmpeg", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(listf), "-c", "copy", str(body)])
    run(["ffmpeg", "-nostdin", "-y", "-i", str(body), "-vf", sub,
         *venc, "-c:a", "copy", "-movflags", "+faststart", str(out)])
    for p in (*parts, listf, body):
        p.unlink(missing_ok=True)
    return out


def render_clip(src: Path, clip: dict, words: list[dict], iw: int, ih: int,
                idx: int, s: Settings) -> Path:
    out = s.out / f"clip_{idx:02d}.mp4"
    hook = clip.get("hook")
    if not hook:
        print(f"[render] clip {idx:02d}: {clip['start']:.1f}-{clip['end']:.1f}s -> {out.name}")
        return _render_piece(src, clip["start"], clip["end"], words, iw, ih, s, out, f"clip_{idx:02d}")

    # cold open: render the teaser + the full body, then crossfade between them (clean
    # dissolve, not a jarring hard cut) so the teased line flows into the clip's real start.
    print(f"[render] clip {idx:02d}: cold-open teaser {hook['start']:.1f}-{hook['end']:.1f}s "
          f"+ body {clip['start']:.1f}-{clip['end']:.1f}s -> {out.name}")
    teaser = s.work / f"clip_{idx:02d}_teaser.mp4"
    body = s.work / f"clip_{idx:02d}_full.mp4"
    _render_piece(src, hook["start"], hook["end"], words, iw, ih, s, teaser, f"clip_{idx:02d}_t")
    _render_piece(src, clip["start"], clip["end"], words, iw, ih, s, body, f"clip_{idx:02d}_b")
    d = 0.3  # crossfade length
    tdur = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "csv=p=0", str(teaser)]).stdout.strip())
    off = max(0.0, tdur - d)
    venc = encoder_args(s.vcodec, s.crf)
    run(["ffmpeg", "-nostdin", "-y", "-i", str(teaser), "-i", str(body),
         "-filter_complex",
         f"[0:v][1:v]xfade=transition=fade:duration={d}:offset={off:.3f}[v];"
         f"[0:a][1:a]acrossfade=d={d}[a]",
         "-map", "[v]", "-map", "[a]", *venc, "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", str(out)])
    for p in (teaser, body):
        p.unlink(missing_ok=True)
    return out
