"""Measure (ported from the original distribution loop node 8).

Pulls performance for each posted clip into `outcomes`. For a LIVE post we fetch
real IG Insights; for a dry-run there is no real post, so we record a
CLEARLY-LABELED simulated outcome (source="simulated", verified=False) so the
flywheel has signal to learn from.

Re-running is safe: live posts are re-fetched each time (metrics grow), dry-run
simulations are recorded once per publish.
"""
from __future__ import annotations

from .config import Settings
from . import loop_store


def measure(s: Settings) -> list[dict]:
    clips = {c["id"]: c for c in loop_store.list_clips(s.data)}
    publishes = loop_store.list_publishes(s.data)
    existing = {(o["clip_id"], o["source"]) for o in loop_store.list_outcomes(s.data)}
    collected = 0

    for pub in publishes:
        if pub["status"] not in ("published", "dryrun"):
            continue  # skipped/failed clips have nothing to measure
        clip = clips.get(pub["clip_id"])
        if not clip:
            continue

        if pub["status"] == "published" and pub.get("external_id"):
            outcome = _ig_insights(s, clip, pub)
        else:
            if (clip["id"], "simulated") in existing:
                continue  # simulated once per clip — deterministic, re-adding is noise
            outcome = _simulated_metrics(clip, pub)

        loop_store.insert_outcome(
            s.data, clip_id=clip["id"], platform=pub["platform"],
            views=outcome["views"], engagement=outcome["engagement"],
            verified=outcome["verified"], source=outcome["source"], meta=outcome["meta"])
        collected += 1
        print(f"[measure] clip {clip['id']} {outcome['source']}: "
              f"views={outcome['views']} eng={outcome['engagement']:.3f} verified={outcome['verified']}")

    print(f"[measure] complete — {collected} outcome(s) collected")
    return loop_store.list_outcomes(s.data)


def _simulated_metrics(clip: dict, pub: dict) -> dict:
    """Deterministic stand-in keyed to the clip's jury score, so higher-scored
    clips trend higher — enough signal for the flywheel to rank formats.
    NOT real data; marked source=simulated, verified=False."""
    score = int(clip.get("score") or 50)
    order = int(clip.get("meta", {}).get("order", 0))
    views = 400 + score * 45 + (order * 137) % 350
    engagement = round(0.02 + score / 1200 + (order % 3) * 0.004, 4)
    return {
        "views": views, "engagement": engagement, "verified": False, "source": "simulated",
        "meta": {"format_id": clip.get("format_id", ""), "score": score, "mode": pub["mode"]},
    }


def _ig_insights(s: Settings, clip: dict, pub: dict) -> dict:
    try:
        import requests

        r = requests.get(
            f"https://graph.facebook.com/v21.0/{pub['external_id']}/insights",
            params={"metric": "plays,reach,likes,comments,shares,saved",
                    "access_token": s.ig_access_token},
            timeout=30,
        ).json()
        vals = {m["name"]: (m.get("values", [{}])[0].get("value", 0)) for m in r.get("data", [])}
        reach = max(1, vals.get("reach", 0))
        eng = (vals.get("likes", 0) + vals.get("comments", 0)
               + vals.get("shares", 0) + vals.get("saved", 0)) / reach
        return {
            "views": int(vals.get("plays", 0)), "engagement": round(eng, 4),
            "verified": True, "source": "ig_insights",
            "meta": {"format_id": clip.get("format_id", ""), "raw": vals},
        }
    except Exception as e:  # an analytics hiccup shouldn't kill the loop
        print(f"[measure] live metrics fetch failed ({e}) — recording unverified zero")
        return {
            "views": 0, "engagement": 0.0, "verified": False, "source": "fetch_failed",
            "meta": {"external_id": pub.get("external_id"), "format_id": clip.get("format_id", "")},
        }
