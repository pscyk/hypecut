"""Transcribe, select, reframe, caption, and export an inspectable result."""
from __future__ import annotations

import json
from pathlib import Path

from . import transcribe, highlights, render, jury, audio, vision_discover
from .config import Settings, ffprobe_dims, run


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    seconds, ms = divmod(milliseconds, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02}:{minute:02}:{sec:02},{ms:03}"


def write_srt(words: list[dict], start: float, end: float, path: Path) -> None:
    relative = render._rel_words(words, start, end)
    groups = [relative[i:i + 3] for i in range(0, len(relative), 3)]
    path.write_text("\n".join(
        f"{i + 1}\n{_srt_time(group[0]['start'])} --> {_srt_time(group[-1]['end'])}\n"
        + " ".join(word["text"] for word in group) + "\n"
        for i, group in enumerate(groups)
    ), encoding="utf-8")


def process(src: Path, s: Settings) -> list[Path]:
    s.work.mkdir(parents=True, exist_ok=True)
    s.out.mkdir(parents=True, exist_ok=True)
    analysis = getattr(s, "analysis", s.work)
    analysis.mkdir(parents=True, exist_ok=True)
    stem = src.stem
    manifest = {"version": 1, "status": "failed", "requested_clips": s.num_clips,
                "clips": [], "errors": []}

    def save() -> None:
        (s.out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

    words = transcribe.transcribe(src, s, analysis / f"{stem}.words.json")
    (s.out / "transcript.json").write_text(json.dumps(words, ensure_ascii=False, indent=2) + "\n")
    if not words:
        manifest["status"] = "no_speech"
        save()
        print("[pipeline] no speech found; nothing to clip")
        return []
    if not s.reaction_peaks:
        s.reaction_peaks = audio.reaction_peaks(src)
    if s.jury:
        pool = max(s.num_clips, s.candidate_pool)
        mode = s.discover
        if mode == "vision":
            candidates = vision_discover.discover(src, words, s, analysis / f"{stem}.candidates_{mode}.json", num=pool)
        else:
            candidates = highlights.pick(words, s, analysis / f"{stem}.candidates_{mode}.json", num=pool)
        clips = jury.rank(candidates, src, words, s, analysis / f"{stem}.jury_{mode}.json")[:s.num_clips]
    else:
        clips = highlights.pick(words, s, analysis / f"{stem}.clips.json")
    if not clips:
        manifest["status"] = "no_highlights"
        save()
        print("[pipeline] no highlights selected")
        return []
    iw, ih = ffprobe_dims(src)
    duration = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)]).stdout.strip())
    outputs = []
    for index, candidate in enumerate(clips):
        out = s.out / f"clip_{index:02d}.mp4"
        try:
            clip = dict(candidate)
            highlights.polish_end(clip, words, src)
            # Sentence/reaction polishing must never exceed the user's bound or EOF.
            clip["start"] = max(0.0, float(clip["start"]))
            clip["end"] = min(float(clip["end"]), duration, clip["start"] + s.max_secs)
            if clip["end"] - clip["start"] + 0.05 < s.min_secs:
                raise ValueError("selected moment is shorter than --min within the source duration")
            out = render.render_clip(src, clip, words, iw, ih, index, s)
            subtitle = out.with_suffix(".srt")
            write_srt(words, clip["start"], clip["end"], subtitle)
            outputs.append(out)
            manifest["clips"].append({"file": out.name, "subtitles": subtitle.name,
                                      "title": clip.get("title", ""), "start": clip["start"], "end": clip["end"],
                                      "reason": clip.get("reason", ""), "width": 1080, "height": 1920})
        except Exception as exc:
            out.unlink(missing_ok=True)
            out.with_suffix(".srt").unlink(missing_ok=True)
            manifest["errors"].append({"clip": index, "error": str(exc)})
            print(f"[pipeline] clip {index:02d} failed: {exc}")
        save()
    manifest["status"] = "partial" if outputs and manifest["errors"] else "complete" if outputs else "failed"
    # Source material can contain fewer valid moments than requested; record both counts.
    manifest["selected_clips"] = len(clips)
    manifest["generated_clips"] = len(outputs)
    save()
    return outputs
