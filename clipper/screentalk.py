"""Decide, sentence by sentence, whether a stream clip should SHOW THE SCREEN or
CROP TO THE FACE.

Streams mix two very different beats:
  - "screen": the streamer is talking ABOUT what's on the shared screen — walking
    through code, pointing at output, "look at this line", "run it". Cropping to the
    webcam here throws away the whole point, so we must show the WHOLE frame.
  - "talk": an off-topic rant, opinion, joke or reaction that stands on its own
    without the screen ("if it works, it's good code", "do not touch your python").
    Here the webcam IS the content, so we crop into the facecam and let it fill 9:16.

`spans()` returns clip-relative [(t0, t1, "screen"|"talk")] with short runs merged
away so the framing doesn't flip-flop. One cheap Claude call per clip, cached.
"""
from __future__ import annotations
import json
from pathlib import Path
from .config import Settings
from . import llm

MIN_SEG = 2.5  # seconds — shorter runs get merged into a neighbour (no strobing)

TOOL = {
    "name": "label_views",
    "description": "Label each transcript line as screen-referential or standalone talk.",
    "input_schema": {
        "type": "object",
        "properties": {
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "number", "description": "the line's [start] timestamp, copied exactly"},
                        "mode": {"type": "string", "enum": ["screen", "talk"],
                                 "description": "'screen' if the speaker is discussing what's shown on the shared "
                                                "screen (code, editor, terminal, a demo, docs — 'this function', "
                                                "'look here', 'run it', reading output). 'talk' if it's a general "
                                                "opinion/rant/joke/story/reaction that makes sense WITHOUT seeing "
                                                "the screen."},
                    },
                    "required": ["start", "mode"],
                },
            }
        },
        "required": ["lines"],
    },
}


def _lines(words: list[dict]) -> list[tuple[float, str]]:
    """(start_rel, text) per sentence-ish chunk of the clip-relative words."""
    out, cur = [], []
    for w in words:
        cur.append(w)
        if w["text"][-1:] in ".!?" or len(cur) >= 18:
            out.append((cur[0]["start"], " ".join(x["text"] for x in cur)))
            cur = []
    if cur:
        out.append((cur[0]["start"], " ".join(x["text"] for x in cur)))
    return out


def _classify(lines: list[tuple[float, str]], s: Settings) -> list[tuple[float, str]]:
    """Ask Claude to tag each line screen/talk. Returns [(start, mode)]; on any
    failure, default every line to 'screen' (safer — never silently hide context)."""
    body = "\n".join(f"[{t:7.2f}] {txt}" for t, txt in lines)
    prompt = (
        "This is a transcript of one clip from a live STREAM where the person shares "
        "their screen (usually code/an editor) with a webcam in the corner.\n\n"
        "For EACH line below, decide the best vertical framing:\n"
        "  - 'screen': they are talking about WHAT'S ON THE SCREEN — explaining code, "
        "pointing at output, reading/editing, referencing 'this', 'here', 'this line'. "
        "The viewer needs to SEE the screen for it to make sense.\n"
        "  - 'talk': a standalone opinion, rant, joke, story or reaction that lands "
        "WITHOUT seeing the screen.\n\n"
        "When unsure, prefer 'screen' (losing on-screen context is worse than a wide shot). "
        "Copy each line's [start] exactly. Label every line via the label_views tool.\n\n"
        f"TRANSCRIPT:\n{body}"
    )
    try:
        labels = llm.tool_json(s, model=llm.resolve_model(s, "model"),
                               tool=TOOL, prompt=prompt, max_tokens=1500)["lines"]
        by_start = {round(float(x["start"]), 2): x["mode"] for x in labels if x.get("mode") in ("screen", "talk")}
        return [(t, by_start.get(round(t, 2), "screen")) for t, _ in lines]
    except Exception as e:
        print(f"[screentalk] classify failed ({e}) — defaulting to full-frame")
        return [(t, "screen") for t, _ in lines]


def _runs(labeled: list[tuple[float, str]], dur: float) -> list[tuple[float, float, str]]:
    """Per-line labels -> merged [(t0, t1, mode)] spans covering [0, dur], with runs
    shorter than MIN_SEG dissolved into a neighbour so the framing doesn't strobe."""
    if not labeled:
        return [(0.0, dur, "screen")]
    # collapse consecutive same-mode lines into spans; first span starts at 0.0
    spans: list[list] = []
    for i, (t, m) in enumerate(labeled):
        t0 = 0.0 if i == 0 else t
        if spans and spans[-1][2] == m:
            continue
        if spans:
            spans[-1][1] = t0
        spans.append([t0, dur, m])
    spans[-1][1] = dur
    # dissolve too-short runs (merge into the longer neighbour), repeat until stable
    changed = True
    while changed and len(spans) > 1:
        changed = False
        for i, sp in enumerate(spans):
            if sp[1] - sp[0] >= MIN_SEG:
                continue
            if i == 0:
                spans[1][0] = sp[0]
            elif i == len(spans) - 1:
                spans[-2][1] = sp[1]
            else:  # give the gap to whichever neighbour is longer
                prev, nxt = spans[i - 1], spans[i + 1]
                if (prev[1] - prev[0]) >= (nxt[1] - nxt[0]):
                    prev[1] = sp[1]
                else:
                    nxt[0] = sp[0]
            spans.pop(i)
            changed = True
            break
    # coalesce any now-adjacent same-mode spans
    out: list[list] = []
    for sp in spans:
        if out and out[-1][2] == sp[2]:
            out[-1][1] = sp[1]
        else:
            out.append(sp)
    return [(a, b, m) for a, b, m in out]


def spans(words: list[dict], dur: float, s: Settings, cache: Path | None = None) -> list[tuple[float, float, str]]:
    """Clip-relative [(t0, t1, 'screen'|'talk')] for the whole clip. `words` are
    clip-relative. Caches the raw per-line labels so re-renders skip the Claude call."""
    lines = _lines(words)
    if not lines:
        return [(0.0, dur, "screen")]
    labeled = None
    if cache and cache.exists():
        try:
            labeled = [(float(t), m) for t, m in json.loads(cache.read_text())]
        except Exception:
            labeled = None
    if labeled is None:
        labeled = _classify(lines, s)
        if cache:
            cache.write_text(json.dumps(labeled))
    return _runs(labeled, dur)
