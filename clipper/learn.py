"""Learn / flywheel (ported from oxcorp-loop node 9).

Ranks a cross-clip "what worked" view from outcomes, then re-weights the format
library so trend research + highlight picking bias toward winners and stale
formats decay. V1 = ranking + weighting, no model training. This is what makes
the loop compounding: the next --trend run samples the updated library.
"""
from __future__ import annotations

import json
from collections import defaultdict

from .config import Settings
from .format_library import load_format_library, save_format_library
from . import loop_store

_DECAY = 0.9        # pull every format toward baseline each cycle (retire the stale)
_BOOST = 1.0        # how hard winners are rewarded
_MIN_W, _MAX_W = 0.2, 3.0


def learn(s: Settings) -> list[dict]:
    outcomes = loop_store.list_outcomes(s.data)
    ranking = rank_formats(outcomes)

    library = load_format_library(s.data)
    library = reweight(library, ranking)
    save_format_library(s.data, library)

    what_worked = [
        {
            "format_id": fid,
            "name": next((f["name"] for f in library if f["id"] == fid), fid),
            "clips": r["n"],
            "avg_views": round(r["avg_views"]),
            "avg_engagement": round(r["avg_engagement"], 4),
            "weight": next((round(f["weight"], 3) for f in library if f["id"] == fid), None),
        }
        for fid, r in sorted(ranking.items(), key=lambda kv: kv[1]["perf"], reverse=True)
    ]
    s.data.mkdir(parents=True, exist_ok=True)
    (s.data / "what_worked.json").write_text(json.dumps(what_worked, indent=2))

    if what_worked:
        top = what_worked[0]
        print(f"[learn] flywheel: top format '{top['format_id']}' "
              f"(avg {top['avg_views']} views) -> weight {top['weight']}")
    print(f"[learn] complete — ranked {len(ranking)} format(s); library re-weighted")
    return what_worked


def rank_formats(outcomes: list[dict]) -> dict[str, dict]:
    """Average views/engagement per format_id; perf = views weighted by engagement."""
    buckets: dict[str, list] = defaultdict(list)
    for o in outcomes:
        fid = o.get("meta", {}).get("format_id") or "unknown"
        buckets[fid].append(o)
    ranking = {}
    for fid, os in buckets.items():
        if fid == "unknown":
            continue
        avg_views = sum(o["views"] for o in os) / len(os)
        avg_eng = sum(o["engagement"] for o in os) / len(os)
        ranking[fid] = {
            "n": len(os),
            "avg_views": avg_views,
            "avg_engagement": avg_eng,
            "perf": avg_views * (1 + avg_eng),
        }
    return ranking


def reweight(library: list[dict], ranking: dict[str, dict]) -> list[dict]:
    """Decay every weight toward 1.0, then boost winners proportional to perf."""
    top_perf = max((r["perf"] for r in ranking.values()), default=1.0) or 1.0
    for f in library:
        base = f.get("weight", 1.0) * _DECAY + (1 - _DECAY)  # decay toward 1.0
        r = ranking.get(f["id"])
        boost = _BOOST * (r["perf"] / top_perf) if r else 0.0
        f["weight"] = round(max(_MIN_W, min(_MAX_W, base + boost)), 4)
    return library
