"""Format library — proven clip structures the highlight picker can sample from,
and the asset the learn step re-weights from real outcomes (the flywheel).

Ported from the original distribution loop's format_library.py. Lives at data/format_library.json;
seeded on first use so it's a mutable, persisted asset (NOT a cache — don't
delete it with work/).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Starter set distilled from common short-form patterns (OpusClip/Higgsfield-style).
# `weight` is the sampling bias learn.py tunes from real outcomes.
SEED_FORMATS: list[dict[str, Any]] = [
    {
        "id": "hook-question",
        "name": "Question hook",
        "hook_style": "Open on a provocative question the viewer needs answered.",
        "pacing": "fast",
        "structure": "question -> tension -> payoff",
        "best_for": "education, opinion",
        "weight": 1.0,
    },
    {
        "id": "bold-claim",
        "name": "Bold claim / hot take",
        "hook_style": "Lead with a strong, slightly controversial statement.",
        "pacing": "fast",
        "structure": "claim -> justification -> example",
        "best_for": "opinion, thought-leadership",
        "weight": 1.0,
    },
    {
        "id": "story-arc",
        "name": "Micro story",
        "hook_style": "Start mid-action, promise a turn.",
        "pacing": "medium",
        "structure": "setup -> conflict -> resolution",
        "best_for": "founder stories, case studies",
        "weight": 1.0,
    },
    {
        "id": "listicle",
        "name": "Numbered value",
        "hook_style": "'3 things...' — promise a countable payoff.",
        "pacing": "fast",
        "structure": "promise -> item -> item -> item",
        "best_for": "tips, how-to",
        "weight": 1.0,
    },
    {
        "id": "revelation",
        "name": "Surprising reveal",
        "hook_style": "Tease a counterintuitive fact, withhold it briefly.",
        "pacing": "medium",
        "structure": "tease -> build -> reveal",
        "best_for": "data, insights",
        "weight": 1.0,
    },
    {
        "id": "quotable",
        "name": "Quotable moment",
        "hook_style": "Isolate one sharp, tweetable line.",
        "pacing": "medium",
        "structure": "context -> the line -> let it land",
        "best_for": "interviews, talks",
        "weight": 1.0,
    },
    {
        "id": "problem-solution",
        "name": "Problem -> solution",
        "hook_style": "Name a pain the viewer feels, then resolve it.",
        "pacing": "fast",
        "structure": "pain -> agitate -> fix",
        "best_for": "product, practical value",
        "weight": 1.0,
    },
]


def _library_path(data_dir: Path) -> Path:
    return Path(data_dir).resolve() / "format_library.json"


def load_format_library(data_dir: Path) -> list[dict[str, Any]]:
    path = _library_path(data_dir)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    # seed on first use
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(SEED_FORMATS, indent=2))
    return list(SEED_FORMATS)


def save_format_library(data_dir: Path, formats: list[dict[str, Any]]) -> None:
    path = _library_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(formats, indent=2))


def format_library_brief(formats: list[dict[str, Any]]) -> str:
    """Compact text block for prompts (highest-weighted first)."""
    ordered = sorted(formats, key=lambda f: f.get("weight", 1.0), reverse=True)
    lines = []
    for f in ordered:
        lines.append(
            f"- [{f['id']}] {f['name']}: hook = {f['hook_style']} "
            f"(pacing: {f['pacing']}; structure: {f['structure']}; best for: {f['best_for']})"
        )
    return "\n".join(lines)
