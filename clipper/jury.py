"""Stage 2: an audience 'jury' watches each candidate (sprite sheet + transcript)
and predicts Instagram-style engagement. Clips are ranked by a weighted blend of
the signals IG's algorithm actually favors (watch-through + shares highest).
"""
from __future__ import annotations
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import anthropic
from .config import Settings
from . import spritesheet, llm

# Panel calibrated for the TARGET MARKET: Instagram Reels, India tier-1 cities
# (Mumbai/Delhi NCR/Bangalore/Pune/Hyderabad), 18-40, Hinglish-fluent. Bump _CAL
# when personas/weights change so cached rankings don't survive a recalibration.
_CAL = "ig-in-t1-v1"
PERSONAS = [
    ("metro_genz", "You are a 20-year-old college student in Mumbai who lives on Instagram Reels. Hinglish comedy is your native tongue and you know every meme format. You stop for instant chaos, savage roasts, and freeze-frame-able moments; you share constantly — close-friends story and straight into group DMs — but only clips your friends will get within 2 seconds, no context needed."),
    ("corporate_millennial", "You are a 28-year-old office worker in Bangalore scrolling Reels on your commute and lunch break. You send relatable, easy-to-explain clips to friends' DMs and the office group chat — roasts, workplace-adjacent humor, anything that says 'this is so us'. Cringe, in-jokes needing context, or slow clips get scrolled past instantly."),
    ("meme_admin", "You run a desi meme page with 200k followers. You judge clips as raw material: is there a freeze-frame, a quotable Hinglish line, a reaction face worth reposting or remixing? Pure save/share instinct, zero sentimentality — if it won't travel outside this fandom, it's dead to you."),
    ("standup_fan", "You are a metro comedy-club regular who follows the Indian standup and roast scene closely — you know the performers, the running bits, the callbacks. You rate writing, timing, and audacity; you share genuinely great bits to fellow fans and call out lazy or recycled ones."),
    ("hater", "You are a hater scrolling to find reasons to mock. You rarely like or share, but you COMMENT a lot to criticize, nitpick, or call things cringe/overrated. Your comments still drive reach."),
    ("doomscroller", "You are a generic fast scroller. You stop only for a strong visual hook or novelty in the first second, and bail the instant you're bored. You rarely engage unless genuinely surprised."),
    ("family_sharer", "You are a 45-year-old parent in Delhi, on Instagram in the evenings. You forward funny-clean, shocking, or wholesome clips to the family WhatsApp group — that cross-posting is how clips break out beyond Instagram. Anything crude-for-no-reason, too fast, or needing context gets nothing from you."),
]

# Weights (sum to 1.0) tuned to how IG actually ranks Reels: sends-per-reach
# (DM/WhatsApp shares) and watch time dominate; likes are the weakest signal.
WEIGHTS = {"stop_scroll": 0.10, "watch_through": 0.30, "share": 0.30,
           "save": 0.10, "comment": 0.12, "like": 0.08}

_REACT_PROPS = {
    "stop_scroll": {"type": "integer", "description": "0-10: would the first ~1s stop your scroll?"},
    "watch_through": {"type": "integer", "description": "0-10: would you watch to the end / replay?"},
    "like": {"type": "integer", "description": "0-10: likelihood you tap like"},
    "comment": {"type": "integer", "description": "0-10: likelihood you comment (praise OR hate)"},
    "save": {"type": "integer", "description": "0-10: likelihood you save it"},
    "share": {"type": "integer", "description": "0-10: likelihood you send/share it"},
    "predicted_comment": {"type": "string", "description": "the comment you'd actually leave, if any"},
    "hook_score": {"type": "integer", "description": "0-10: the OPENING segment (first 1-2s) — does it grab you and stop the scroll?"},
    "hook_feedback": {"type": "string", "description": "one line: what would make the opening stronger?"},
    "clarity_score": {"type": "integer", "description": "0-10: the MAIN BODY (middle) — is it clear, easy to follow without context, and does it hold attention?"},
    "clarity_feedback": {"type": "string", "description": "one line: what's confusing, slow, or loses you in the middle?"},
    "payoff_score": {"type": "integer", "description": "0-10: the ENDING segment — does it land a satisfying punchline/payoff?"},
    "payoff_feedback": {"type": "string", "description": "one line: does the ending land, or is it cut short / flat?"},
}
_REACT_REQUIRED = ["stop_scroll", "watch_through", "like", "comment", "save", "share",
                   "hook_score", "clarity_score", "payoff_score"]

# Collapsed jury: ONE call per clip; Claude role-plays all personas and returns one
# reaction each (7x fewer calls than one call per persona, ~same quality).
PANEL_TOOL = {
    "name": "panel",
    "description": "Return one reaction per audience persona for this clip.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reactions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"persona": {"type": "string", "enum": [p[0] for p in PERSONAS]},
                                   **_REACT_PROPS},
                    "required": ["persona", *_REACT_REQUIRED],
                },
            }
        },
        "required": ["reactions"],
    },
}
PANEL_SYSTEM = ("You simulate a focus group of distinct audience personas reacting to the SAME clip, "
                "which will be posted as an INSTAGRAM REEL targeting India tier-1 city audiences "
                "(Mumbai/Delhi NCR/Bangalore/Pune/Hyderabad; 18-40; fully Hinglish-fluent — untranslated "
                "Hindi punchlines land fine). On Instagram, virality is driven hardest by the "
                "send-to-a-friend impulse (DM/WhatsApp shares) and watch-through; judge with that bar. "
                "Stay in character for each persona — they disagree. The personas:\n"
                + "\n".join(f"- {name}: {desc}" for name, desc in PERSONAS)
                + "\nReturn exactly one reaction per persona via the panel tool.")

TOOL = {
    "name": "react",
    "description": "Predict your own engagement with this clip, as this persona.",
    "input_schema": {
        "type": "object",
        "properties": dict(_REACT_PROPS),
        "required": list(_REACT_REQUIRED),
    },
}


def _snippet(words: list[dict], start: float, end: float) -> str:
    return " ".join(w["text"] for w in words if w["end"] > start and w["start"] < end)


def _score_one(client, model, b64, dur, snippet, persona) -> dict | None:
    name, system = persona
    try:
        msg = client.messages.create(
            model=model, max_tokens=400, system=system,
            tools=[TOOL], tool_choice={"type": "tool", "name": "react"},
            messages=_user_content(b64, dur, snippet),
        )
        out = next(b.input for b in msg.content if b.type == "tool_use")
        out["persona"] = name
        return out
    except Exception as e:
        print(f"[jury] {name} failed: {e}")
        return None


def _user_content(b64: str, dur: float, snippet: str) -> list[dict]:
    return [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
        {"type": "text", "text":
            f"This is a 3x3 grid of 9 frames sampled left-to-right, top-to-bottom across a "
            f"{dur:.0f}s vertical clip from a Hindi/English comedy-roast show.\n\n"
            f"TRANSCRIPT (English):\n{snippet}\n\n"
            f"As yourself (this persona), predict your real engagement AND judge the clip's "
            f"hook, clarity, and payoff (with one-line feedback for each) via the react tool."},
    ]}]


def _panel_prompt(dur: float, snippet: str) -> str:
    return (
        f"This is a 3x3 grid of 9 frames sampled left-to-right, top-to-bottom across a "
        f"{dur:.0f}s vertical clip from a Hindi/English comedy-roast show.\n\n"
        f"TRANSCRIPT (English):\n{snippet}\n\n"
        f"For EACH persona, predict their real engagement AND grade the clip's three-act arc: "
        f"HOOK (the opening — does it grab you), CLARITY (the body — is the middle clear and "
        f"holds attention), PAYOFF (the ending — does it land), with one-line feedback each. "
        f"Return one reaction per persona via the panel tool.")


def _panel_content(b64: str, dur: float, snippet: str) -> list[dict]:
    return [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
        {"type": "text", "text": _panel_prompt(dur, snippet)},
    ]}]


def _score_panel(s: Settings, model: str, b64: str, dur: float, snippet: str) -> list[dict]:
    """One call -> all personas' reactions for this clip. Provider-agnostic (llm.py)."""
    try:
        out = llm.tool_json(s, model=model, tool=PANEL_TOOL, system=PANEL_SYSTEM,
                            b64=b64, prompt=_panel_prompt(dur, snippet), max_tokens=1800)
        return out.get("reactions", [])
    except Exception as e:
        print(f"[jury] panel failed: {e}")
        return []


def _score_panel_batch(client, model, sheets) -> dict[int, list[dict]]:
    """One Batches request PER CLIP (not per persona) — 7x fewer calls."""
    import time
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    reqs = [Request(custom_id=str(ci), params=MessageCreateParamsNonStreaming(
        model=model, max_tokens=1800, system=PANEL_SYSTEM,
        tools=[PANEL_TOOL], tool_choice={"type": "tool", "name": "panel"},
        messages=_panel_content(b64, c["end"] - c["start"], snippet)))
        for ci, (c, b64, snippet) in enumerate(sheets)]
    batch = client.messages.batches.create(requests=reqs)
    print(f"[jury] panel batch {batch.id} ({len(reqs)} clips) — polling...")
    while client.messages.batches.retrieve(batch.id).processing_status != "ended":
        time.sleep(15)
    results: dict[int, list[dict]] = {i: [] for i in range(len(sheets))}
    for res in client.messages.batches.results(batch.id):
        if res.result.type != "succeeded":
            continue
        try:
            out = next(b.input for b in res.result.message.content if b.type == "tool_use")
            results[int(res.custom_id)] = out.get("reactions", [])
        except StopIteration:
            pass
    return results


def _score_batch(client, model, sheets) -> dict[int, list[dict]]:
    """Submit all (candidate x persona) reactions as one Batches job (50% cheaper,
    async). custom_id = '<candidate_index>__<persona>'. Polls until done."""
    import time
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    reqs = []
    for ci, (c, b64, snippet) in enumerate(sheets):
        for name, system in PERSONAS:
            reqs.append(Request(
                custom_id=f"{ci}__{name}",
                params=MessageCreateParamsNonStreaming(
                    model=model, max_tokens=400, system=system,
                    tools=[TOOL], tool_choice={"type": "tool", "name": "react"},
                    messages=_user_content(b64, c["end"] - c["start"], snippet),
                ),
            ))
    batch = client.messages.batches.create(requests=reqs)
    print(f"[jury] batch {batch.id} submitted ({len(reqs)} requests) — polling...")
    while True:
        b = client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        time.sleep(15)
    print(f"[jury] batch ended: {b.request_counts.succeeded} ok, {b.request_counts.errored} errored")

    results: dict[int, list[dict]] = {i: [] for i in range(len(sheets))}
    for res in client.messages.batches.results(batch.id):
        if res.result.type != "succeeded":
            continue
        ci = int(res.custom_id.split("__")[0])
        persona = res.custom_id.split("__")[1]
        try:
            out = next(blk.input for blk in res.result.message.content if blk.type == "tool_use")
            out["persona"] = persona
            results[ci].append(out)
        except StopIteration:
            pass
    return results


def _avg(reactions: list[dict], k: str) -> float:
    return sum(r.get(k, 0) or 0 for r in reactions) / len(reactions)


def _viral(reactions: list[dict]) -> float:
    """Score = IG engagement (50%) + clip-arc quality (50%). The arc is hook (open) +
    clarity (body) + payoff (end), weighted EQUALLY, but the WEAKEST segment drags the
    score down — a clip is only as good as its weakest act (great payoff, dead hook = bad)."""
    if not reactions:
        return 0.0
    eng = sum(w * _avg(reactions, k) for k, w in WEIGHTS.items())
    h = _avg(reactions, "hook_score")
    c = _avg(reactions, "clarity_score")
    p = _avg(reactions, "payoff_score")
    # hook & clarity weighted more (scroll-stop + hold matter most for short-form),
    # then the weakest segment still drags it down
    wmean = (1.35 * h + 1.2 * c + 1.0 * p) / 3.55
    arc = 0.5 * wmean + 0.5 * min(h, c, p)
    return round(0.5 * eng + 0.5 * arc, 2)


def _quality(reactions: list[dict]) -> dict:
    """Aggregate hook/clarity/payoff scores + a sample of each feedback line."""
    def fb(k):
        return [r[k] for r in reactions if r.get(k)][:2]
    return {
        "hook": round(_avg(reactions, "hook_score"), 1),
        "clarity": round(_avg(reactions, "clarity_score"), 1),
        "payoff": round(_avg(reactions, "payoff_score"), 1),
        "hook_feedback": fb("hook_feedback"),
        "clarity_feedback": fb("clarity_feedback"),
        "payoff_feedback": fb("payoff_feedback"),
    }


def rank(candidates: list[dict], src: Path, words: list[dict], s: Settings, cache: Path) -> list[dict]:
    # cache is only valid for the same calibration + candidates + steer + PROVIDER
    # (a new --hint re-picks candidates and changes the hint boost; a provider/model
    # switch must re-rank — a stale ranking would silently win)
    jmodel = llm.resolve_model(s, "jury_model")
    key = {"cal": _CAL, "hint": (s.hint or "").strip(),
           "prov": f"{llm.provider(s)}:{jmodel}",
           "spans": [[c["start"], c["end"]] for c in candidates]}
    if cache.exists():
        data = json.loads(cache.read_text())
        if isinstance(data, dict) and data.get("key") == key:
            print(f"[jury] using cache {cache.name}")
            return data["ranked"]
        print("[jury] cache stale (candidates or hint changed); re-ranking")

    # 0 candidates (sparse transcript, no highlight-worthy moments) is a
    # VALID outcome, not an error — short-circuit before the panel batch:
    # client.messages.batches.create(requests=[]) 400s Anthropic-side
    # ("requests: List should have at least 1 item"), masking the real
    # result ("no clips in this video") behind an API crash.
    if not candidates:
        print("[jury] 0 candidates — nothing to rank")
        return []

    sheets = []
    for i, c in enumerate(candidates):
        sheet = spritesheet.make_sheet(src, c["start"], c["end"], s.work / f"sheet_{i:02d}.jpg")
        sheets.append((c, spritesheet.to_b64(sheet), _snippet(words, c["start"], c["end"])))

    batch = s.batch and llm.supports_batch(s)  # Batches API is anthropic-only
    if s.batch and not llm.supports_batch(s):
        print(f"[jury] provider={llm.provider(s)} has no batch API — scoring live (parallel)")
    mode = "batch (50% off, async)" if batch else "live"
    print(f"[jury] {len(candidates)} clips x 1 panel call ({len(PERSONAS)} personas each) "
          f"[{llm.provider(s)}:{jmodel}, {mode}]")

    if batch:
        results = _score_panel_batch(anthropic.Anthropic(), jmodel, sheets)
    else:
        def run_job(ci):
            c, b64, snippet = sheets[ci]
            return ci, _score_panel(s, jmodel, b64, c["end"] - c["start"], snippet)

        results = {i: [] for i in range(len(sheets))}
        with ThreadPoolExecutor(max_workers=6) as ex:
            for ci, reactions in ex.map(run_job, range(len(sheets))):
                results[ci] = reactions

    ranked = []
    for i, c in enumerate(candidates):
        reactions = results[i]
        base = _viral(reactions)
        boost = s.hint_bonus if c.get("matches_steer") else 0.0
        ranked.append({**c, "base_score": base, "hint_boost": boost,
                       "viral_score": round(base + boost, 2),
                       "quality": _quality(reactions) if reactions else {}, "reactions": reactions})
    ranked.sort(key=lambda x: x["viral_score"], reverse=True)

    print("[jury] ranked candidates (★ = steer-matched, +bonus):")
    for r in ranked:
        star = f" ★+{r['hint_boost']:.1f}" if r["hint_boost"] else ""
        q = r.get("quality", {})
        qs = f"  [hook {q.get('hook','-')} clarity {q.get('clarity','-')} payoff {q.get('payoff','-')}]" if q else ""
        print(f"  {r['viral_score']:4.1f}  {r['start']:7.1f}-{r['end']:7.1f}  {r.get('title','')}{star}{qs}")
    cache.write_text(json.dumps({"key": key, "ranked": ranked}, ensure_ascii=False, indent=1))
    return ranked
