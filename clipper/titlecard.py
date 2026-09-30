"""Montage mode — the kinetic title card.

Builds an ASS that reveals each phrase WORD-BY-WORD cumulatively ("WELCOME" ->
"WELCOME TO THE" -> "WELCOME TO THE FIRST"), then swaps to the next phrase, accent
colour alternating per phrase — matching the reference reel's opening. Each new word
pops in. Burned over the first `hook_secs` of the montage.
"""
from __future__ import annotations
from pathlib import Path
from .config import Settings, OUT_W, OUT_H

ACCENT = "0000FF"  # ASS BBGGRR = red
WHITE = "FFFFFF"


def _ts(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int((t % 3600) // 60):02d}:{t % 60:05.2f}"


def build_ass(phrases: list[str], hook_secs: float, path: Path, s: Settings, offset: float = 0.0) -> Path:
    font = s.title_font
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {OUT_W}
PlayResY: {OUT_H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,{font},140,&H00{WHITE},&H00{WHITE},&H00101010,&H64000000,-1,0,0,0,100,100,2,0,1,9,5,5,80,80,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    groups = [p.split() for p in (phrases or []) if p.strip()][:4]
    if not groups:
        path.write_text(header, encoding="utf-8")
        return path
    total_words = sum(len(g) for g in groups) or 1
    cx, cy = OUT_W // 2, int(OUT_H * 0.40)
    win = max(0.6, hook_secs - 0.15)
    t = 0.12
    for gi, words in enumerate(groups):
        colour = ACCENT if gi % 2 == 0 else WHITE
        ptime = win * len(words) / total_words
        per = ptime / len(words)
        for wi in range(len(words)):
            t0 = t + wi * per
            last_word = wi == len(words) - 1
            last_phrase = gi == len(groups) - 1
            if last_word and last_phrase:
                t1 = hook_secs  # final state holds to the end of the hook
            elif last_word:
                t1 = t + ptime
            else:
                t1 = t0 + per
            shown = " ".join(words[: wi + 1]).replace("{", "(").replace("}", ")")
            # cumulative line, colour per phrase, a quick scale-pop as each word lands
            eff = (f"\\an5\\pos({cx},{cy})\\c&H00{colour}&\\fad(40,0)"
                   f"\\fscx112\\fscy112\\t(0,110,\\fscx100\\fscy100)")
            lines.append(f"Dialogue: 0,{_ts(t0 + offset)},{_ts(t1 + offset)},Title,,0,0,0,,{{{eff}}}{shown}")
        t += ptime
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
