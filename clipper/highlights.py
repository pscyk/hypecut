"""Step 2: pick highlight segments from the transcript with Claude."""
from __future__ import annotations
import json
from pathlib import Path
from . import trend, llm
from .config import Settings, run

# Concrete comedic patterns to scan the transcript for (ported from the clipify skill).
# These sharpen TEXT-based picking — they tell the model what "funny" looks like in words.
FUNNY_SIGNALS = (
    "Scan for these concrete comedic patterns (not just 'high energy'):\n"
    "- PUNCHLINE / REACTION beats: a sharp one-liner, then words like 'what', 'wait', "
    "'no way', 'bhai', swearing, or audible laughter right after.\n"
    "- REVERSALS: a setup or straight question that gets an unexpected / absurd answer.\n"
    "- AWKWARD PAUSES: a long gap or filler ('uh', 'umm', '...') where the silence is the joke.\n"
    "- SELF-ROAST / QUOTABLE one-liners: short declarative lines that stand alone out of context.\n"
    "- RAPID BACK-AND-FORTH: fast alternating short lines (a roast volley building to a burn).\n"
)


def _transcript_lines(words: list[dict]) -> str:
    """Compact timestamped transcript: one line per sentence-ish chunk."""
    lines, cur = [], []
    for w in words:
        cur.append(w)
        if w["text"][-1:] in ".!?" or len(cur) >= 18:
            start = cur[0]["start"]
            text = " ".join(x["text"] for x in cur)
            lines.append(f"[{start:7.2f}] {text}")
            cur = []
    if cur:
        lines.append(f"[{cur[0]['start']:7.2f}] " + " ".join(x['text'] for x in cur))
    return "\n".join(lines)


TOOL = {
    "name": "submit_clips",
    "description": "Submit the chosen highlight clips.",
    "input_schema": {
        "type": "object",
        "properties": {
            "clips": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "number", "description": "start time in seconds"},
                        "end": {"type": "number", "description": "end time in seconds"},
                        "title": {"type": "string", "description": "punchy title / hook"},
                        "reason": {"type": "string", "description": "why it will perform"},
                        "matches_steer": {"type": "boolean", "description": "true if this clip is specifically what the editor's steer asked for"},
                        "format_id": {"type": "string", "description": "the ONE winning-format id from the trend brief this clip best matches (only when a trend brief is given)"},
                    },
                    "required": ["start", "end", "title"],
                },
            }
        },
        "required": ["clips"],
    },
}


def pick(words: list[dict], s: Settings, cache: Path, num: int | None = None) -> list[dict]:
    num = num or s.num_clips
    hint = (s.hint or "").strip()
    brief = getattr(s, "trend_brief", None) or {}
    # cache is keyed by steer AND brief — a new trend brief must re-pick.
    # Empty brief -> '' so legacy caches (written before the trend key) still hit.
    tsig = f"{brief.get('source', '')}|{','.join(brief.get('recommended_formats', []))}" if brief else ""
    if cache.exists():
        data = json.loads(cache.read_text())
        if (isinstance(data, dict) and data.get("hint", "") == hint
                and data.get("trend", "") == tsig and len(data["clips"]) >= num):
            print(f"[highlights] using cache {cache.name}")
            return data["clips"][:num]

    transcript = _transcript_lines(words)
    total = words[-1]["end"] if words else 0
    heat = ""
    if getattr(s, "heatmap", None):
        peaks = sorted(s.heatmap, key=lambda h: h["value"], reverse=True)[:12]
        peaks = sorted(peaks, key=lambda h: h["start"])
        spans = ", ".join(f"{p['start']:.0f}-{p['end']:.0f}s" for p in peaks)
        heat = (f"\nYouTube viewers REPLAYED these moments the most (a strong signal that "
                f"they're the highlights): {spans}\nFavor clips overlapping these windows.\n")
    if getattr(s, "reaction_peaks", None):
        rp = ", ".join(f"{t:.0f}s" for t in s.reaction_peaks)
        heat += (f"\nThe LIVE AUDIENCE laughed/reacted hardest at these timestamps (a visual "
                 f"gag or punchline landed here — these are often the best clips even if the "
                 f"words alone look plain): {rp}\nStrongly favor moments at/just before these.\n")
    steer = (
        f"\nEDITOR'S STEER — the human knows this footage and says the best moments are:\n"
        f"  \"{hint}\"\n"
        f"Strongly prioritize moments matching this steer; it overrides generic picks. "
        f"Mark matches_steer=true on every clip that fulfills this steer. "
        f"Each chosen clip must still be self-contained.\n" if hint else ""
    )
    trend_sec = trend.prompt_section(brief, s.data) if brief else ""
    prompt = (
        f"You are an expert short-form video editor. Below is a timestamped transcript "
        f"of a {total/60:.1f}-minute video. Each line begins with [start_seconds].\n"
        f"{steer}{heat}{trend_sec}\n"
        f"Pick the {num} BEST standalone moments to cut as vertical shorts. Each clip must:\n"
        f"- be self-contained: open with a hook, deliver a complete thought, land an ending\n"
        f"- be between {s.min_secs:.0f} and {s.max_secs:.0f} seconds long\n"
        f"- start and end on natural sentence boundaries (use the timestamps)\n"
        f"- have real COMEDIC/narrative SUBSTANCE — a joke, roast, story, reveal or take that "
        f"lands. NOT mere high-energy moments: a celebrity's entrance, applause, cheering, a "
        f"music sting or confetti is HYPE, not a highlight, unless something funny is actually said.\n\n"
        f"{FUNNY_SIGNALS}\n"
        f"IMPORTANT about the signals above: audience-reaction and most-replayed timestamps are "
        f"HINTS, not commands. Verify each against the transcript — only favor it if a joke/bit "
        f"actually lands there; ignore peaks that are just an entrance or applause with no payoff.\n\n"
        f"Return start/end in seconds via the submit_clips tool.\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )

    clips = llm.tool_json(s, model=llm.resolve_model(s, "pick_model"),
                          tool=TOOL, prompt=prompt, max_tokens=2000)["clips"]
    clips = _snap(clips, words, s)
    print(f"[highlights] {len(clips)} clips chosen")
    for c in clips:
        print(f"  {c['start']:7.2f}-{c['end']:7.2f} ({c['end']-c['start']:4.0f}s)  {c['title']}")
    cache.write_text(json.dumps({"hint": hint, "trend": tsig, "clips": clips}, ensure_ascii=False, indent=1))
    return clips[:num]


def _next_scene_cut(src, t0: float, window: float, thresh: float = 0.4) -> float | None:
    """Time of the show's next hard cut in (t0, t0+window], or None. The broadcast
    holds a shot through the visual payoff and cuts when the beat is done."""
    import re, subprocess
    cp = subprocess.run(["ffmpeg", "-nostdin", "-ss", f"{t0:.3f}", "-t", f"{window:.3f}", "-i", str(src), "-an",
                         "-vf", f"select='gt(scene,{thresh})',metadata=print", "-f", "null", "-"],
                        capture_output=True, text=True)
    times = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", cp.stdout + cp.stderr)]
    times = [t for t in times if t > 0.35]  # ignore a cut right at t0
    return (t0 + min(times)) if times else None


def _reaction_subsides(src, t0: float, window: float, sr: int = 16000, hop: float = 0.1) -> float | None:
    """When the audience reaction (laughter/applause) after t0 quiets down — i.e. the
    beat has landed. Returns that time, or t0+window if it never quiets (act ongoing)."""
    import subprocess
    import numpy as np
    cp = subprocess.run(["ffmpeg", "-nostdin", "-ss", f"{t0:.3f}", "-t", f"{window:.3f}", "-i", str(src),
                         "-ac", "1", "-ar", str(sr), "-f", "s16le", "pipe:1"], capture_output=True)
    a = np.frombuffer(cp.stdout, dtype=np.int16).astype(np.float32)
    h = int(sr * hop)
    if a.size < h * 5:
        return None
    e = np.array([np.sqrt(np.mean(a[i:i + h] ** 2) + 1) for i in range(0, a.size - h, h)])
    head = e[:int(1.0 / hop)]
    thresh = max((np.percentile(head, 90) if head.size else e.max()) * 0.4, e.max() * 0.2)
    run_q = int(0.6 / hop)  # 0.6s of sustained quiet = reaction over
    for i in range(len(e) - run_q):
        if bool(np.all(e[i:i + run_q] < thresh)):
            return t0 + i * hop
    return t0 + window


def polish_end(clip: dict, words: list[dict], src, max_extend: float = 6.0, tail: float = 0.4) -> dict:
    """End when the ACT lands, not on the last word: complete the sentence, then
    extend through the audience reaction (until it subsides), without crossing the
    show's next cut into the next bit."""
    start, end = clip["start"], clip["end"]
    inside = [w for w in words if w["start"] >= start - 0.05 and w["end"] <= end + 0.05]
    if not (inside and inside[-1]["text"].rstrip()[-1:] in ".!?\""):
        term = None
        for w in words:  # complete the current sentence first
            if w["end"] > end and w["start"] <= end + max_extend and w["text"].rstrip()[-1:] in ".!?\"":
                term = w["end"]
                break
        if term is not None:
            end = term
        else:
            # No terminal punctuation within reach — Whisper often drops the final
            # period, so a phrase like "...do not touch your python" has none. Don't
            # cut mid-phrase: extend through the continuous speech RUN (words with only
            # small gaps) following `end`, bounded by max_extend.
            run_end = end
            for w in words:
                if w["end"] <= end + 0.05:
                    continue  # already inside the clip
                if w["start"] > run_end + 0.7 or w["start"] > end + max_extend:
                    break     # a real pause (or too far) — phrase boundary
                run_end = w["end"]
            end = run_end
    cut = _next_scene_cut(src, end, max_extend)        # the next bit — hard upper bound
    bound = (cut - 0.12) if cut else (end + max_extend)
    react = _reaction_subsides(src, end, bound - end)  # where the reaction quiets
    new_end = react if react else (end + tail)
    # give the visual beat at least ~1.5s to land (the act often outlasts the words),
    # but never cross into the next shot
    new_end = min(max(new_end, end + 1.5), bound)
    clip["end"] = round(max(new_end, end + tail), 3)
    return clip


HOOK_TOOL = {
    "name": "hooks",
    "description": "Pick a cold-open hook teaser for each clip.",
    "input_schema": {
        "type": "object",
        "properties": {
            "clips": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "needs_hook": {"type": "boolean", "description": "true ONLY if the clip's natural opening is weak/slow/context-dependent and would NOT grab a stranger scrolling"},
                        "use_hook": {"type": "boolean", "description": "true only if needs_hook AND a good teaser line exists LATER in the clip; false otherwise"},
                        "hook_start": {"type": "number", "description": "start time (s) of the COMPLETE punchy line(s) to paste at the front. Must come from LATER in the clip, not the opening seconds. Start on the FIRST word of the sentence."},
                        "hook_end": {"type": "number", "description": "end time (s) — the LAST word of the line, INCLUDING its final word and punctuation. Never cut a sentence off mid-thought."},
                    },
                    "required": ["index", "use_hook"],
                },
            }
        },
        "required": ["clips"],
    },
}


def _sentence_spans(words: list[dict]) -> list[tuple[float, float]]:
    """(start, end) for each sentence — split on terminal . ! ? punctuation."""
    spans, cur = [], None
    for w in words:
        if cur is None:
            cur = w["start"]
        t = w["text"].rstrip()
        if t[-1:] in ".!?" and not t.endswith("..."):  # "..." = trailing off, not a real stop
            spans.append((cur, w["end"]))
            cur = None
    if cur is not None and words:
        spans.append((cur, words[-1]["end"]))
    return spans


def _complete_hook(words: list[dict], hs: float, he: float, c: dict, max_len: float = 6.0):
    """Snap the model's [hs,he] out to WHOLE sentences inside the clip so the teaser is a
    complete line (never dropping the last word, never starting mid-sentence). Returns
    (start, end) or None."""
    spans = [(a, b) for (a, b) in _sentence_spans(words)
             if a >= c["start"] - 0.15 and b <= c["end"] + 0.15]
    if not spans:
        return None
    hit = [(a, b) for (a, b) in spans if b > hs + 0.05 and a < he - 0.05]  # sentences the pick covers
    if not hit:
        hit = [min(spans, key=lambda sp: abs(sp[0] - hs))]  # nearest whole sentence
    start, end = hit[0][0], hit[-1][1]
    if end - start > max_len:  # too long: keep the single sentence closest to the pick's intent
        start, end = min(hit, key=lambda sp: abs(sp[0] - hs))
    return start, end


def optimize_hooks(clips: list[dict], words: list[dict], s: Settings) -> list[dict]:
    """COLD OPEN: pick the punchiest COMPLETE line within each clip to paste at the front as a
    hook teaser (non-continuous). The clip then plays normally from its real start, so the
    teased moment also appears in context. Sets clip['hook'] = {start, end} (or leaves none)."""
    blocks = []
    for i, c in enumerate(clips):
        seg = [w for w in words if c["start"] - 0.1 <= w["start"] <= c["end"] + 0.1]
        txt = " ".join(f"[{w['start']:.1f}] {w['text']}" for w in seg)
        blocks.append(f"CLIP {i} ({c['start']:.1f}-{c['end']:.1f}):\n{txt}")
    prompt = (
        "Each clip below is a finished short. A COLD-OPEN hook means pasting a punchy line from "
        "the clip at the very front as a teaser; the clip then plays normally from its real start.\n\n"
        "For EACH clip make two judgments:\n\n"
        "1) DOES IT NEED A HOOK? Look at the clip's NATURAL OPENING (its first sentence or two). "
        "If that opening already grabs a stranger scrolling — a strong, self-contained, "
        "scroll-stopping line — the clip does NOT need a hook (needs_hook=false, use_hook=false). "
        "Only flag needs_hook=true when the opening is slow, weak, setup-heavy, or only makes sense "
        "with context a stranger doesn't have.\n\n"
        "2) IS THERE A GOOD ONE TO USE? A hook is only worth adding if a COMPLETE, punchy line "
        "exists LATER in the clip — intriguing, savage, shocking or absurd ON ITS OWN. Crucially it "
        "must come from well AFTER the opening: prepending a line that's already at/near the start "
        "just makes it play twice in a row (an ugly repeat) — never do that. If the only strong "
        "line is already the opening, set use_hook=false. If no line lands cold, use_hook=false.\n\n"
        "Set use_hook=true ONLY when BOTH hold: the opening is weak AND a strong line from later "
        "in the clip can tease it forward. Avoid in-jokes/callbacks (name-drops, 'thank you', "
        "asides) — they're meaningless out of context.\n\n"
        "RULES when use_hook=true:\n"
        "- hook_start MUST be the first word of a sentence; hook_end MUST be the last word of a "
        "sentence (include the final word + its punctuation). Never cut a line off mid-thought.\n"
        "- Use the [timestamps]. hook_start/hook_end must lie inside the clip, after its opening.\n\n"
        + "\n\n".join(blocks)
    )
    try:
        picks = llm.tool_json(s, model=llm.resolve_model(s, "pick_model"),
                              tool=HOOK_TOOL, prompt=prompt, max_tokens=900)["clips"]
    except Exception:
        return clips
    by_idx = {h["index"]: h for h in picks}
    for i, c in enumerate(clips):
        h = by_idx.get(i)
        if not h or not h.get("use_hook"):
            continue
        hs, he = h.get("hook_start"), h.get("hook_end")
        if hs is None or he is None or he <= hs:
            continue
        span = _complete_hook(words, hs, he, c)  # expand to whole sentence(s); keep the last word
        if not span:
            continue
        hs, he = span
        if hs - c["start"] < 8.0:  # teased line is already ~the opening: prepending it just repeats
            print(f"[hook] clip {i}: skip — strongest line is already at the start (no teaser)")
            continue
        if 0.7 <= he - hs <= 6.5:
            c["hook"] = {"start": round(hs, 3), "end": round(he, 3)}
            print(f"[hook] clip {i}: cold-open teaser {hs:.1f}-{he:.1f} ({he-hs:.1f}s) then full clip")
    return clips


def _snap(clips: list[dict], words: list[dict], s: Settings) -> list[dict]:
    """Snap each start/end to the nearest word boundary; clamp duration."""
    starts = [w["start"] for w in words]
    ends = [w["end"] for w in words]
    out = []
    for c in clips:
        st = min(starts, key=lambda x: abs(x - c["start"]))
        en = min(ends, key=lambda x: abs(x - c["end"]))
        if en - st > s.max_secs:  # clamp over-long picks to the last word boundary within max
            cap = st + s.max_secs
            within = [e for e in ends if st < e <= cap]
            en = max(within) if within else cap
        if en - st < s.min_secs or en <= st:
            continue
        out.append({"start": round(st, 3), "end": round(en, 3),
                    "title": c.get("title", ""), "reason": c.get("reason", ""),
                    "matches_steer": bool(c.get("matches_steer", False)),
                    "format_id": c.get("format_id", "")})
    return out
