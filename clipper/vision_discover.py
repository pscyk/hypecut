"""Vision-first highlight DISCOVERY (two-pass).

Pass 1 (coarse): sprite-sheet the whole video in ~75s windows (16 timestamped
frames each, ~4.7s/frame) and let Claude *watch* and flag candidate highlight
regions — catching visual gags / reactions the transcript misses.

Pass 2 (fine): zoom into each candidate region with a ~1s/frame timestamped sprite
so Claude pinpoints the exact peak moment and filters out coarse false-positives.

Output is the same candidate shape as highlights.pick, so it feeds the jury unchanged.
"""
from __future__ import annotations
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import anthropic
from .config import Settings, run
from . import spritesheet, llm

WINDOW, STEP = 75.0, 70.0   # coarse window + advance (5s overlap)
GRID = (4, 4)               # 16 frames per sprite (legible tiles, within vision limits)
FINE_PAD = 4.0             # seconds of context added around each coarse region
MAX_REGIONS = 24           # how many candidate regions to refine in pass 2

TOOL = {
    "name": "highlights",
    "description": "Report the highlight moments you can SEE.",
    "input_schema": {
        "type": "object",
        "properties": {
            "moments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "number", "description": "start time in seconds (read the frame time labels)"},
                        "end": {"type": "number", "description": "end time in seconds"},
                        "reason": {"type": "string", "description": "what you SEE that makes it a highlight (visual gag, big reaction, physical comedy, shocked/funny face, a bit landing, reveal)"},
                        "strength": {"type": "integer", "description": "0-10 how strong / viral-worthy"},
                    },
                    "required": ["start", "end", "reason", "strength"],
                },
            }
        },
        "required": ["moments"],
    },
}


def _dur(src: Path) -> float:
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "csv=p=0", str(src)]).stdout.strip())


def _windows(dur: float, peaks: list[float], heatmap: list[dict]) -> list[tuple[float, float]]:
    """Coarse-scan windows. When audio-reaction peaks / most-replayed spans exist,
    scan ONLY windows overlapping them — a visual gag on a comedy show almost always
    lands with an audible reaction, so the quiet stretches (the bulk of a long
    episode) are safely skipped. No signals -> full sweep."""
    full, t = [], 0.0
    while t < dur:
        full.append((t, min(t + WINDOW, dur)))
        t += STEP
    # a reaction FOLLOWS its gag: cover the run-up too
    spans = [(p - 20.0, p + 5.0) for p in (peaks or [])]
    spans += [(h["start"], h["end"]) for h in (heatmap or [])]
    if not spans:
        return full
    kept = [(a, b) for (a, b) in full if any(a < s1 and b > s0 for (s0, s1) in spans)]
    if not kept:  # signals exist but match nothing (shouldn't happen) — don't scan blind
        return full
    print(f"[discover] reaction/replay gating: scanning {len(kept)}/{len(full)} windows "
          f"({len(full) - len(kept)} quiet windows skipped)")
    return kept


def _steer(hint: str) -> str:
    return f" The editor especially wants moments like: \"{hint}\"." if hint else ""


def _merge(moments: list[dict], min_secs: float, max_secs: float) -> list[dict]:
    moments = sorted((m for m in moments if m.get("start") is not None), key=lambda m: m["start"])
    out: list[dict] = []
    for m in moments:
        if out and m["start"] - out[-1]["end"] < 3.0:
            out[-1]["end"] = max(out[-1]["end"], m["end"])
            out[-1]["strength"] = max(out[-1].get("strength", 0), m.get("strength", 0))
            if len(m.get("reason", "")) > len(out[-1].get("reason", "")):
                out[-1]["reason"] = m["reason"]
        else:
            out.append(dict(m))
    for m in out:
        if m["end"] - m["start"] < min_secs:
            m["end"] = m["start"] + min_secs
        if m["end"] - m["start"] > max_secs:
            m["end"] = m["start"] + max_secs
    return out


def _anthropic_batch_vision(model: str, jobs: list[tuple[str, str, str]]) -> list[dict]:
    """jobs = [(custom_id, image_b64, prompt)] -> flat list of moment dicts.
    Anthropic Batches API: 50% off, async."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    client = anthropic.Anthropic()
    reqs = [Request(custom_id=cid, params=MessageCreateParamsNonStreaming(
        model=model, max_tokens=900, tools=[TOOL],
        tool_choice={"type": "tool", "name": "highlights"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": prompt}]}])) for cid, b64, prompt in jobs]
    batch = client.messages.batches.create(requests=reqs)
    while client.messages.batches.retrieve(batch.id).processing_status != "ended":
        time.sleep(15)
    moments = []
    for res in client.messages.batches.results(batch.id):
        if res.result.type != "succeeded":
            continue
        try:
            out = next(b.input for b in res.result.message.content if b.type == "tool_use")
            moments += out.get("moments", [])
        except StopIteration:
            pass
    return moments


def _batch_vision(s: Settings, model: str, jobs: list[tuple[str, str, str]]) -> list[dict]:
    """Provider-aware fan-out: anthropic uses the Batches API; other providers
    run the same vision calls live + parallel."""
    if llm.supports_batch(s):
        return _anthropic_batch_vision(model, jobs)

    def one(job) -> list[dict]:
        cid, b64, prompt = job
        try:
            return llm.tool_json(s, model=model, tool=TOOL, b64=b64,
                                 prompt=prompt, max_tokens=900).get("moments", [])
        except Exception as e:
            print(f"[discover] {cid} failed: {e}")
            return []

    with ThreadPoolExecutor(max_workers=6) as ex:
        return [m for moments in ex.map(one, jobs) for m in moments]


def discover(src: Path, words: list[dict], s: Settings, cache: Path, num: int) -> list[dict]:
    if cache.exists():
        data = json.loads(cache.read_text())
        clips = data["clips"] if isinstance(data, dict) else data
        if len(clips) >= num:
            print(f"[discover] using cache {cache.name}")
            return clips[:num]

    dur = _dur(src)
    hint = (s.hint or "").strip()
    sheets = s.work / "disc_sheets"
    sheets.mkdir(exist_ok=True)
    cols, rows = GRID
    print(f"[discover] provider={llm.provider(s)} "
          f"({'batch' if llm.supports_batch(s) else 'live+parallel'})")

    # ---- pass 1: coarse scan (gated to reaction/replay windows, cheap model) ----
    windows = _windows(dur, getattr(s, "reaction_peaks", []), getattr(s, "heatmap", []))
    print(f"[discover] pass 1/2: coarse-scanning {len(windows)} windows over {dur/60:.0f} min (~{WINDOW/(cols*rows):.1f}s/frame)")
    jobs = []
    for wi, (a, b) in enumerate(windows):
        sheet = spritesheet.make_sheet(src, a, b, sheets / f"c_{wi:03d}.jpg", cols=cols, rows=rows)
        prompt = (f"This is a {cols}x{rows} grid of {cols*rows} frames (L-R, top-bottom) across a "
                  f"{b-a:.0f}s window of a Hindi/English comedy-roast show; each frame is labeled with "
                  f"its source time in seconds.{_steer(hint)}\nBy WATCHING, flag candidate highlight "
                  f"moments (big reactions, visual gags, physical comedy, reveals, funny faces). Use the "
                  f"time labels for start/end. Empty list if nothing notable.")
        jobs.append((f"c{wi}", spritesheet.to_b64(sheet), prompt))
    regions = _merge(_batch_vision(s, llm.resolve_model(s, "discover_model"), jobs), s.min_secs, s.max_secs)
    regions.sort(key=lambda m: m.get("strength", 0), reverse=True)
    regions = regions[:MAX_REGIONS]

    # ---- pass 2: fine refine each candidate region (~1s/frame) ----
    print(f"[discover] pass 2/2: refining {len(regions)} regions at ~1s/frame")
    jobs2 = []
    for ri, r in enumerate(regions):
        a = max(0.0, r["start"] - FINE_PAD)
        b = min(dur, min(r["end"] + FINE_PAD, a + s.max_secs + 8))
        sheet = spritesheet.make_sheet(src, a, b, sheets / f"f_{ri:03d}.jpg", cols=cols, rows=rows)
        prompt = (f"This is a FINE-GRAINED {cols}x{rows} grid zooming into a candidate highlight "
                  f"({b-a:.0f}s, ~{(b-a)/(cols*rows):.1f}s/frame), each frame labeled with its source "
                  f"time in seconds.{_steer(hint)}\nPinpoint the BEST start/end for a viral short within "
                  f"this region (use the time labels), rate its strength, and say what lands. If it's "
                  f"actually weak, give it a low strength.")
        jobs2.append((f"f{ri}", spritesheet.to_b64(sheet), prompt))
    refined = _merge(_batch_vision(s, llm.resolve_model(s, "model"), jobs2), s.min_secs, s.max_secs)
    refined.sort(key=lambda m: m.get("strength", 0), reverse=True)

    pool = max(num, s.candidate_pool)
    clips = [{"start": round(m["start"], 2), "end": round(m["end"], 2),
              "title": m.get("reason", "")[:60], "reason": m.get("reason", ""),
              "strength": m.get("strength", 0), "matches_steer": False}
             for m in refined[:pool]]
    print(f"[discover] -> {len(clips)} candidates (vision two-pass)")
    cache.write_text(json.dumps({"clips": clips}, ensure_ascii=False, indent=1))
    return clips
