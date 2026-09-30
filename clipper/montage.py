"""Montage mode — the selection brain.

Where highlights.pick chooses a few SELF-CONTAINED clips, this picks MANY short
BEATS and ORDERS them into one trailer/sizzle reel: a scroll-stopping hook, a fast
escalating middle, a big visual finale. It also returns the kinetic title-card
phrases for the opening and a ready-to-paste IG caption + hashtags.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from .config import Settings
from . import llm
from .highlights import _transcript_lines, FUNNY_SIGNALS

TOOL = {
    "name": "submit_montage",
    "description": "Submit an ordered trailer-style montage: title card, beats, caption.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title_card": {
                "type": "array", "items": {"type": "string"},
                "description": "2-4 SHORT punchy phrases shown word-by-word as the opening title "
                               "overlay, e.g. ['WELCOME TO THE','WILDEST','INDIA'S GOT LATENT']. "
                               "ALL CAPS, a few words each.",
            },
            "heroes": {
                "type": "array",
                "description": "The N hardest-hitting MIC-DROPS in the whole show — each opens its own "
                               "reel. A mic-drop = a SETUP that builds to a SAVAGE, UNEXPECTED, QUOTABLE "
                               "punchline; a line so good the room reacts and you'd screenshot it (e.g. "
                               "listing someone's awards then 'why the f**k are you here?'). Rank PURELY "
                               "by punchline IMPACT. A weak intro line or a purely visual gag is NOT a "
                               "mic-drop. MULTIPLE mic-drops from the SAME person are encouraged (the "
                               "host's roast lines are often all the best ones) — do NOT diversify by "
                               "speaker. Just never repeat the same line. Best-first.",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_start": {"type": "number", "description": "start of the setup, a few seconds before source_end (the lead-in to the punchline)"},
                        "source_end": {"type": "number", "description": "the EXACT second the PUNCHLINE's LAST WORD finishes — read it off the transcript timestamps. NOT the audience reaction after it, NOT the next line. The punchline MUST be the last thing said before this time. Getting this precise is critical: the clip ends here."},
                        "punch_word": {"type": "string", "description": "OPTIONAL big glass hero-word, MUST be a word actually SPOKEN here AND a single VIVID CONCRETE standalone word (noun/concept like 'OIL','BUFFALO','BALD'). NEVER a filler word or one not spoken. Leave EMPTY if none."},
                        "punch_line": {"type": "string", "description": "the punchline text, for reference"},
                        "caption": {"type": "string", "description": "IG caption for THIS reel; first ~40 chars a hook"},
                    },
                    "required": ["source_start", "source_end"],
                },
            },
            "beats": {
                "type": "array",
                "description": "Ordered montage beats. First beat is the HOOK (strongest scroll-"
                               "stopper); last is the FINALE (biggest visual/laugh). Middle beats "
                               "escalate. 8-14 beats.",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_start": {"type": "number", "description": "moment start (s) in the source"},
                        "source_end": {"type": "number", "description": "moment end (s) in the source"},
                        "kind": {"type": "string", "enum": ["hook", "bit", "reaction", "visual_gag", "finale"]},
                        "punch": {"type": "string", "description": "one line: what lands here"},
                    },
                    "required": ["source_start", "source_end", "kind", "punch"],
                },
            },
            "caption": {"type": "string", "description": "IG post caption. First ~40 chars must be a hook."},
            "hashtags": {"type": "array", "items": {"type": "string"}, "description": "5-10 hashtags, no # sign"},
        },
        "required": ["title_card", "heroes", "beats", "caption", "hashtags"],
    },
}


def build_edl(words: list[dict], s: Settings, cache: Path) -> dict:
    if cache.exists():
        print(f"[montage] using cache {cache.name}")
        return json.loads(cache.read_text())

    n = max(1, s.num_clips)
    transcript = _transcript_lines(words)
    total = words[-1]["end"] if words else 0
    react = ""
    if getattr(s, "reaction_peaks", None):
        rp = ", ".join(f"{t:.0f}s" for t in s.reaction_peaks)
        react = (f"\nAUDIENCE-REACTION TIMESTAMPS (the crowd laughed / woo'd / applauded hardest here): "
                 f"{rp}.\nThis is your #1 signal for finding MIC-DROPS: the funniest self-contained line "
                 f"is almost always the one spoken in the ~5s JUST BEFORE one of these spikes. Scan the "
                 f"transcript right before each spike for the savage one-liner that caused it.\n")
    if getattr(s, "heatmap", None):  # YouTube most-replayed: viewers re-watch the best mic-drops
        top = sorted(s.heatmap, key=lambda h: h["value"], reverse=True)[:12]
        spans = ", ".join(f"{p['start']:.0f}-{p['end']:.0f}s" for p in sorted(top, key=lambda h: h["start"]))
        react += (f"\nYOUTUBE MOST-REPLAYED windows (viewers re-watched these MOST — another top signal "
                  f"for mic-drops): {spans}. Strongly favor heroes whose punchline lands inside these.\n")
    steer = f"\nEDITOR'S STEER (use these moments): \"{s.hint}\"\n" if s.hint else ""
    prompt = (
        f"You are an elite short-form TRAILER editor. Below is a timestamped transcript of a "
        f"{total/60:.1f}-minute show. Each line begins with [start_seconds].\n{steer}{react}\n"
        f"Each reel has a TWO-PHASE structure. Build {n} of them.\n\n"
        f"PHASE 1 — THE MIC-DROP (~{s.hero_secs:.0f}s): open on a SETUP that builds to a SAVAGE, "
        f"UNEXPECTED, QUOTABLE punchline — a true mic-drop the room reacts to. Return the {n} "
        f"HARDEST-HITTING such moments as 'heroes', ranked PURELY by punchline impact (NOT by variety, "
        f"NOT by whether it has a nice keyword). Several can be from the SAME person — the host's roast "
        f"lines are often all the best mic-drops, and that's fine; do NOT spread picks across speakers "
        f"for variety. Just never repeat the same line.\n\n"
        f"TWO SIGNALS THAT FIND THE REAL MIC-DROPS (use both):\n"
        f"A) SELF-CONTAINED & POWERFUL ALONE: the line must hit HARD with ZERO prior context — a stranger "
        f"scrolling gets it and laughs instantly, no setup needed. Gold-standard examples FROM THIS SHOW: "
        f"'You want oil? Come to the island, my friend' / 'I don't like it when women get empowered' / "
        f"'we were playing Counter-Strike with my friends, I told them to bomb Iran'. Absurd, savage, "
        f"quotable on their own. A line that only makes sense with what came before is NOT this.\n"
        f"B) FOLLOW THE LAUGHTER: a real mic-drop is IMMEDIATELY followed by a big audience LAUGH / WOO / "
        f"applause. The line spoken JUST BEFORE a reaction spike is almost always a mic-drop. Use the "
        f"reaction timestamps below to hunt them down.\n"
        f"A weak intro line, a setup with no payoff, or a purely VISUAL gag is NOT a mic-drop. For each "
        f"hero: source_start (setup), source_end (the punchline), and a 'caption' for that reel.\n"
        f"OPTIONAL, secondary: 'punch_word' — set ONLY if a single concrete word naturally dominates the "
        f"punchline; otherwise leave EMPTY. It must never influence WHICH bit you pick.\n\n"
        f"HARD RULES for the heroes (most failures come from breaking these):\n"
        f"1) TIMESTAMP PRECISION: source_end MUST be the exact transcript time where the punchline's "
        f"LAST WORD ends. The punchline has to be the LAST thing said in [source_start, source_end] — "
        f"if it runs into the next line or into 'wow/applause' reactions, you picked source_end too late.\n"
        f"2) REAL PUNCHLINES ONLY: the line must genuinely LAND as a joke in the transcript. Do NOT "
        f"invent a pun out of ordinary speech — 'Alia Bhatt, man' is NOT 'Bhatman'. If it isn't actually "
        f"funny written down, it's not a mic-drop.\n"
        f"3) QUALITY OVER QUANTITY: return FEWER than {n} heroes if the show has fewer than {n} genuine "
        f"mic-drops. One great reel beats three padded with weak/fake ones. Never pad.\n\n"
        f"PHASE 2 — THE DROP (shared by all reels): right after the punchline the MUSIC DROPS and a fast montage of the "
        f"best moments elevates the energy. Return an ORDERED list of {6}-{9} montage beats:\n"
        f"- BEAT 1 = the first hit after the drop: a wild visual or the hardest laugh.\n"
        f"- MIDDLE beats escalate: the actual PAYOFFS — a roast landing, a costume reveal, an "
        f"entrance, confetti, someone dancing or moving, a physical/visual gag, the room exploding.\n"
        f"- LAST beat = the FINALE: the biggest visual payoff or laugh to end on.\n\n"
        f"CRITICAL — this is what makes it a HIGHLIGHTS reel and not a slideshow of faces:\n"
        f"- Favor PERFORMANCE, MOTION and VISUAL PAYOFFS (someone DOING a bit, moving, a reveal, "
        f"confetti, dancing, a big physical reaction) OVER static shots of seated people just talking. "
        f"A seated talking head is the WEAKEST kind of beat — use few of them.\n"
        f"- source_start/source_end must TIGHTLY bracket the ACTION or the laugh/reaction PEAK itself "
        f"— NOT the setup or the talking lead-in before it. Point me at the moment that LANDS.\n"
        f"- Fewer, stronger moments held a beat longer beats lots of half-second face flashes.\n\n"
        f"For each beat give source_start/source_end, a kind, and a one-line 'punch'. Order for "
        f"rising energy.\n\n"
        f"{FUNNY_SIGNALS}\n"
        f"Also write the TITLE CARD (2-4 short ALL-CAPS phrases that animate over the hook) and a "
        f"ready-to-post IG CAPTION (first ~40 chars a hook) plus hashtags.\n\n"
        f"Return everything via submit_montage.\n\nTRANSCRIPT:\n{transcript}"
    )
    edl = llm.tool_json(s, model=llm.resolve_model(s, "pick_model"),
                        tool=TOOL, prompt=prompt, max_tokens=2500)
    edl["beats"] = _clean(edl.get("beats", []))
    edl["heroes"] = [_snap_hero(h, words) for h in edl.get("heroes", [])
                     if h.get("source_end", 0) > h.get("source_start", 0)][:n]
    print(f"[montage] {len(edl['heroes'])} hero(es); title={edl.get('title_card')}")
    for i, h in enumerate(edl["heroes"]):
        print(f"  HERO {i}  {h.get('source_start'):.1f}-{h.get('source_end'):.1f}  punch='{h.get('punch_word')}'  {h.get('punch_line','')}")
    for b in edl["beats"]:
        print(f"  beat  {b['source_start']:7.2f}-{b['source_end']:7.2f}  {b['kind']:10s}  {b['punch']}")
    cache.write_text(json.dumps(edl, ensure_ascii=False, indent=1))
    return edl


def _snap_hero(h: dict, words: list[dict]) -> dict:
    """The LLM is unreliable at the exact punchline end-time, so snap source_end to the
    transcript: find where the punchline's LAST words actually finish (matching the
    punch_line text near the LLM's guess) so we never cut the mic-drop or trail into
    reactions. source_start follows so the bit still ends ON the punchline."""
    pl = h.get("punch_line") or ""
    toks = [t for t in (re.sub(r"[^a-z0-9]", "", w.lower()) for w in pl.split()) if t]
    if not toks:
        return h
    se = float(h.get("source_end", 0))
    win = [w for w in words if se - 16 <= w["end"] <= se + 8]
    wn = [re.sub(r"[^a-z0-9]", "", w["text"].lower()) for w in win]
    for k in (4, 3, 2, 1):  # match the last k punchline words; take the LATEST occurrence
        gram = toks[-k:]
        if len(gram) > len(wn):
            continue
        hits = [i for i in range(len(wn) - k + 1) if wn[i:i + k] == gram]
        if hits:
            j = hits[-1] + k - 1
            h["source_end"] = round(win[j]["end"] + 0.2, 2)
            break
    if h.get("source_start", 0) >= h["source_end"] - 1.5:  # keep a sane setup lead-in
        h["source_start"] = round(h["source_end"] - 6.0, 2)
    return h


def _clean(beats: list[dict]) -> list[dict]:
    out = []
    for b in beats:
        ss, se = float(b.get("source_start", 0)), float(b.get("source_end", 0))
        if se <= ss:
            se = ss + 1.0
        out.append({"source_start": round(ss, 3), "source_end": round(se, 3),
                    "kind": b.get("kind", "bit"), "punch": b.get("punch", "")})
    return out
