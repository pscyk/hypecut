"""Step 5: build an ASS subtitle file with word-by-word yellow karaoke.

We emit one Dialogue event per word: the whole word-group is shown, but the
currently-spoken word is colored yellow and bumped up in size. Stepping the
active word across events gives the punchy "active word highlight" look.
"""
from __future__ import annotations
from pathlib import Path
from .config import Settings, OUT_W, OUT_H

GAP_BREAK = 0.7  # start a new group after a pause longer than this


def _ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _group(words: list[dict], n: int) -> list[list[dict]]:
    groups, cur = [], []
    for i, w in enumerate(words):
        if cur and (len(cur) >= n or w["start"] - cur[-1]["end"] > GAP_BREAK):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def _line(group: list[dict], active: int, s: Settings) -> str:
    base = f"\\c&H{s.base_color}&"
    hot = f"\\c&H{s.active_color}&"
    parts = []
    for i, w in enumerate(group):
        txt = w["text"].replace("{", "(").replace("}", ")")
        if i == active:
            parts.append(f"{{{hot}\\fscx118\\fscy118}}{txt}{{{base}\\fscx100\\fscy100}}")
        else:
            parts.append(txt)
    return " ".join(parts)


def build_ass(words: list[dict], path: Path, s: Settings) -> Path:
    """`words` are clip-relative (already offset to clip start)."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {OUT_W}
PlayResY: {OUT_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{s.font},108,&H00{s.base_color},&H00{s.active_color},&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,7,4,2,90,90,360,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    groups = _group(words, s.words_per_group)
    for gi, group in enumerate(groups):
        # don't let the last word's tail overlap the next group's first line
        next_start = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else None
        for i, w in enumerate(group):
            start = w["start"]
            if i + 1 < len(group):
                end = group[i + 1]["start"]
            else:
                end = w["end"] + 0.15
                if next_start is not None:
                    end = min(end, next_start)
            text = _line(group, i, s)
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Cap,,0,0,0,,{{\\an2}}{text}")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
