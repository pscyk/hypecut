"""Trend research (ported from oxcorp-loop node 2.5, adapted to the anthropic SDK).

Pulls lightweight signal (YouTube most-popular + a goal-driven search), then
distills a compact brief (recommended hooks + formats) — synthesized by Claude
when reachable, or derived from the format library offline. The brief biases
highlight picking (see highlights.py) and its recommended_formats are what
clips get tagged with — which is what learn.py later re-weights.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from .config import Settings
from . import llm
from .format_library import format_library_brief, load_format_library

_YT = "https://www.googleapis.com/youtube/v3"
_STOP = set(
    "the a an and or of to in for on with your you how why what best top new is are this that "
    "i we it my me from at as be do can will vs".split()
)


def research(goal: str, s: Settings) -> dict:
    """Produce a trend brief and persist it to data/trend_brief.json."""
    library = load_format_library(s.data)
    signals = _youtube_signals(s, goal)
    brief = _synthesize(s, goal, signals, library)

    s.data.mkdir(parents=True, exist_ok=True)
    (s.data / "trend_brief.json").write_text(json.dumps(brief, indent=2))
    print(f"[trend] brief ready (source={brief['source']}, hooks={len(brief['recommended_hooks'])})")
    return brief


def _youtube_signals(s: Settings, goal: str) -> dict:
    if not s.youtube_api_key:
        return {}
    try:
        import requests
    except ImportError:
        print("[trend] requests not installed — skipping YouTube signal")
        return {}
    try:
        popular = requests.get(
            f"{_YT}/videos",
            params={
                "part": "snippet,statistics", "chart": "mostPopular",
                "regionCode": s.youtube_region, "maxResults": 15,
                "key": s.youtube_api_key,
            },
            timeout=20,
        ).json()
        search = requests.get(
            f"{_YT}/search",
            params={
                "part": "snippet", "q": goal or "trending", "type": "video",
                "order": "viewCount", "maxResults": 10,
                "regionCode": s.youtube_region, "key": s.youtube_api_key,
            },
            timeout=20,
        ).json()
    except Exception as e:
        print(f"[trend] YouTube API call failed ({e}) — falling back to offline brief")
        return {}

    trending = [i.get("snippet", {}).get("title", "") for i in popular.get("items", [])]
    searched = [i.get("snippet", {}).get("title", "") for i in search.get("items", [])]
    terms = Counter(
        w for t in trending + searched for w in re.findall(r"[a-zA-Z']+", t.lower())
        if w not in _STOP and len(w) > 2
    )
    return {
        "trending_titles": [t for t in trending if t][:15],
        "top_search_titles": [t for t in searched if t][:10],
        "common_terms": [w for w, _ in terms.most_common(12)],
    }


def _synthesize(s: Settings, goal: str, signals: dict, library: list[dict]) -> dict:
    lib_brief = format_library_brief(library)
    top_ids = [f["id"] for f in sorted(library, key=lambda x: x.get("weight", 1.0), reverse=True)]

    try:
        prompt = (
            "You are a short-form video strategist. Given trend signals and a library of "
            "proven clip formats, produce a compact brief to guide clip selection.\n\n"
            f"TARGET PLATFORM: {s.target_platform}\nGOAL: {goal or '(none given)'}\n\n"
            f"TREND SIGNALS:\n{json.dumps(signals, indent=2) if signals else '(none — no YouTube key)'}\n\n"
            f"FORMAT LIBRARY:\n{lib_brief}\n\n"
            "Return ONLY JSON: {\"recommended_hooks\": [5 short hook templates], "
            "\"recommended_formats\": [3-5 format ids from the library], \"notes\": \"1-2 sentences\"}"
        )
        data = llm.text_json(s, model=llm.resolve_model(s, "model"),
                             prompt=prompt, max_tokens=800)
        return {
            "source": "youtube+llm" if signals else "llm",
            "target": s.target_platform,
            "goal": goal,
            "signals": signals,
            "recommended_hooks": [str(h) for h in data.get("recommended_hooks", [])][:6],
            "recommended_formats": [str(f) for f in data.get("recommended_formats", top_ids)][:5],
            "notes": str(data.get("notes", "")),
        }
    except Exception as e:
        print(f"[trend] LLM synthesis unavailable ({e}) — using offline brief")

    # Offline: derive hooks/formats straight from the (possibly re-weighted) library.
    ordered = sorted(library, key=lambda x: x.get("weight", 1.0), reverse=True)
    return {
        "source": "youtube" if signals else "offline_seed",
        "target": s.target_platform,
        "goal": goal,
        "signals": signals,
        "recommended_hooks": [f["hook_style"] for f in ordered[:5]],
        "recommended_formats": [f["id"] for f in ordered[:5]],
        "notes": "Offline brief derived from the format library. Add a YouTube key to enrich.",
    }


def prompt_section(brief: dict, data_dir: Path) -> str:
    """Render a brief as a prompt section for the highlight picker ('' when no brief)."""
    if not brief:
        return ""
    library = {f["id"]: f for f in load_format_library(data_dir)}
    hooks = "\n".join(f"  - {h}" for h in brief.get("recommended_hooks", []))
    fmts = []
    for fid in brief.get("recommended_formats", []):
        f = library.get(fid)
        fmts.append(f"  - [{fid}] {f['hook_style']}" if f else f"  - [{fid}]")
    return (
        "\nTREND BRIEF — what's working on the platform right now:\n"
        f"Recommended hook styles:\n{hooks}\n"
        f"Winning formats:\n" + "\n".join(fmts) + "\n"
        + (f"Strategist notes: {brief['notes']}\n" if brief.get("notes") else "")
        + "Prefer moments that fit these hooks/formats, and set format_id on each clip "
          "to the ONE winning-format id it best matches (use the id that fits best even "
          "if imperfect — never invent new ids).\n"
    )
