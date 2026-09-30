"""Distribution-loop subcommands: clip trend|publish|measure|learn|loop.

These run the parts of the loop that happen AROUND clipping — before (trend
biases the pick) and after (publish -> measure -> learn closes the flywheel).
Nothing ever posts without an explicit --live.
"""
from __future__ import annotations

import argparse
import json

from .config import Settings
from . import loop_store, trend, publish, measure, learn


def main(command: str, argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog=f"clip {command}")
    if command == "trend":
        p.add_argument("goal", help="what this run is about, e.g. 'founder story: why we built X'")
    elif command == "publish":
        p.add_argument("--clip", action="append", default=[],
                       help="loop clip id to publish (repeatable); default: all unpublished clips")
        p.add_argument("--live", action="store_true",
                       help="ACTUALLY POST to the consented Instagram account via the Graph API. "
                            "Default is dry-run: the payload is logged, nothing is posted.")
        p.add_argument("--platform", default=None, help="default: CLIPPER_TARGET_PLATFORM (instagram)")
    args = p.parse_args(argv)

    s = Settings()
    if command == "trend":
        brief = trend.research(args.goal, s)
        print(json.dumps({k: brief[k] for k in ("source", "recommended_hooks", "recommended_formats", "notes")},
                         indent=2))
    elif command == "publish":
        clip_ids = args.clip or _unpublished_clip_ids(s)
        if not clip_ids:
            print("[publish] nothing to publish (no clips registered, or all already published)")
            return
        publish.publish_clips(s, clip_ids, live=args.live, platform=args.platform)
    elif command == "measure":
        measure.measure(s)
    elif command == "learn":
        what_worked = learn.learn(s)
        if what_worked:
            print(json.dumps(what_worked[:5], indent=2))
    elif command == "loop":
        _status(s)


def _unpublished_clip_ids(s: Settings) -> list[str]:
    """Clips with no successful (dry-run or live) publish yet, oldest first."""
    done = {p["clip_id"] for p in loop_store.list_publishes(s.data)
            if p["status"] in ("dryrun", "published")}
    return [c["id"] for c in reversed(loop_store.list_clips(s.data)) if c["id"] not in done]


def _status(s: Settings) -> None:
    clips = loop_store.list_clips(s.data)
    pubs = loop_store.list_publishes(s.data)
    outs = loop_store.list_outcomes(s.data)
    by_clip = {}
    for p in pubs:
        by_clip.setdefault(p["clip_id"], []).append(p)

    print(f"clips: {len(clips)}   publishes: {len(pubs)}   outcomes: {len(outs)}")
    print(f"{'clip':<14}{'score':>6}  {'format':<18}{'status':<12}hook")
    for c in clips:
        ps = by_clip.get(c["id"], [])
        st = ps[-1]["status"] if ps else "rendered"
        if ps and ps[-1].get("permalink"):
            st = ps[-1]["permalink"]
        score = f"{c['score']:.1f}" if c.get("score") is not None else "-"
        print(f"{c['id']:<14}{score:>6}  {(c.get('format_id') or '-'):<18}{st:<12}{c.get('hook', '')[:50]}")
