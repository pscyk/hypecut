"""Word-by-word captions for the montage HERO BIT, with a GLASS effect on the punch
word (e.g. 'oil'). Burned over phase 1 so the setup->punchline lands even on mute.
Modeled on captions.py but: white text, active word pops, punch word gets a glassy glow.
"""
from __future__ import annotations
import re
from pathlib import Path
from .config import Settings, OUT_W, OUT_H

GAP_BREAK = 0.7


def _ts(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int((t % 3600) // 60):02d}:{t % 60:05.2f}"


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", w.lower())


def _is_punch(word: str, punch: str) -> bool:
    w, p = _norm(word), _norm(punch or "")
    return bool(p) and (w == p or (len(p) >= 4 and (w.startswith(p) or p.startswith(w))))


def _group(words: list[dict], n: int) -> list[list[dict]]:
    groups, cur = [], []
    for w in words:
        if cur and (len(cur) >= n or w["start"] - cur[-1]["end"] > GAP_BREAK):
            groups.append(cur); cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def _line(group: list[dict], active: int, punch: str, base: str) -> str:
    parts = []
    for i, w in enumerate(group):
        txt = w["text"].replace("{", "(").replace("}", ")")
        if i == active:
            parts.append(f"{{\\fscx116\\fscy116}}{txt}{{\\fscx100\\fscy100}}")  # active-word pop
        else:
            parts.append(txt)
    return " ".join(parts)


# words too weak/generic to blow up huge on screen — never glass these (better: no big word)
STOPWORDS = {
    "the", "a", "an", "you", "your", "yours", "i", "me", "my", "we", "us", "he", "she", "it",
    "they", "them", "this", "that", "these", "those", "here", "there", "what", "why", "how",
    "who", "when", "where", "is", "are", "was", "were", "be", "been", "am", "to", "of", "in",
    "on", "at", "for", "with", "as", "by", "and", "or", "but", "so", "if", "then", "do", "does",
    "did", "not", "no", "yes", "just", "really", "very", "too", "also", "like", "get", "got",
    "go", "going", "come", "came", "said", "say", "one", "two", "all", "now", "out", "up", "off",
    "him", "her", "his", "our", "can", "will", "would", "should", "could", "have", "has", "had",
}


def _glass_events(words: list[dict], punch: str) -> list[str]:
    """BIG center-frame FROSTED-GLASS hero word (like the reference's 'OIL'). Built from
    three stacked layers — soft halo glow + frosted translucent fill + crisp bright rim
    + a faint drop shadow for depth — so it reads as glass, not a plain transparent
    outline. Skipped entirely when the punch word is empty or a weak filler word."""
    p = _norm(punch)
    if not p or len(p) < 3 or p in STOPWORDS:
        return []  # no strong standalone word -> caption only, no giant word
    cx, cy = OUT_W // 2, int(OUT_H * 0.40)
    out, occ = [], 0
    for w in words:
        if not _is_punch(w["text"], punch):
            continue
        occ += 1
        if occ > 1:  # once is plenty
            break
        word = (re.sub(r"[^A-Za-z0-9]", "", w["text"]) or p).upper()
        fs = 380 if len(word) <= 4 else (290 if len(word) <= 6 else 200)
        start, end = w["start"], w["end"] + 1.0
        base = f"\\an5\\pos({cx},{cy})\\fs{fs}\\b1\\fsp6"
        anim = ("\\fad(70,180)\\fscx66\\fscy66\\t(0,160,\\fscx112\\fscy112)"
                "\\t(160,320,\\fscx100\\fscy100)")
        # layer 1: soft white halo glow (no fill, fat blurred border)
        glow = f"{{{base}{anim}\\1a&HFF&\\3c&HFFFFFF&\\3a&H40&\\bord16\\blur24\\shad0}}{word}"
        # layer 2: frosted translucent fill (you see the subject through it) + depth shadow
        frost = (f"{{{base}{anim}\\1c&HFFFFFF&\\1a&HA0&\\3c&HBFD8FF&\\3a&H10&\\bord3\\blur5"
                 f"\\shad7\\4c&H101018&\\4a&H50&}}{word}")
        # layer 3: crisp bright specular rim (the glass edge highlight)
        rim = f"{{{base}{anim}\\1a&HFF&\\3c&HFFFFFF&\\3a&H00&\\bord2.5\\blur0.6\\shad0}}{word}"
        for layer, body in ((1, glow), (2, frost), (3, rim)):
            out.append(f"Dialogue: {layer},{_ts(start)},{_ts(end)},Cap,,0,0,0,,{body}")
    return out


def build_ass(words: list[dict], punch: str, path: Path, s: Settings) -> Path:
    """`words` are clip-relative [{start,end,text}]; `punch` is the word to glass."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {OUT_W}
PlayResY: {OUT_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{s.font},104,&H00FFFFFF,&H00FFFFFF,&H00101010,&H96000000,-1,0,0,0,100,100,0,0,1,7,4,2,90,90,420,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    groups = _group(words, s.words_per_group)
    for gi, group in enumerate(groups):
        nxt = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else None
        for i, w in enumerate(group):
            start = w["start"]
            if i + 1 < len(group):
                end = group[i + 1]["start"]
            else:
                end = w["end"] + 0.15
                if nxt is not None:
                    end = min(end, nxt)
            text = _line(group, i, punch, s.base_color)
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Cap,,0,0,0,,{{\\an2}}{text}")
    if s.m_glass:  # big frosted-glass hero word on the punch (off by default — user preference)
        lines.extend(_glass_events(words, punch))
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
