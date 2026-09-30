"""Pure-logic tests for the distribution loop (trend/publish/measure/learn).

No network, no API keys, no GPU: YouTube/Claude/IG calls are never touched —
trend falls back to the offline brief without a key, publish defaults to dry-run,
measure uses the simulated path for dry-run publishes.
"""
from __future__ import annotations

import json
import sys

import pytest

from clipper.config import Settings
from clipper import format_library, learn, loop_store, measure, publish, trend


@pytest.fixture()
def s(tmp_path, monkeypatch) -> Settings:
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    st = Settings()
    st.data = tmp_path / "data"
    return st


def _clip(s: Settings, **kw) -> str:
    defaults = dict(source="talk", path="/tmp/clip_00.mp4", start=10.0, end=40.0,
                    hook="the punchline", format_id="bold-claim", score=72.0,
                    meta={"order": 0})
    defaults.update(kw)
    return loop_store.register_clip(s.data, **defaults)


# --- store ---

def test_store_roundtrip(s):
    cid = _clip(s)
    clip = loop_store.get_clip(s.data, cid)
    assert clip["hook"] == "the punchline"
    assert clip["format_id"] == "bold-claim"
    assert clip["meta"]["order"] == 0

    loop_store.insert_publish(s.data, clip_id=cid, platform="instagram",
                              mode="dryrun", status="dryrun", detail={"would_post": {}})
    pubs = loop_store.list_publishes(s.data, clip_id=cid)
    assert len(pubs) == 1 and pubs[0]["status"] == "dryrun"

    loop_store.insert_outcome(s.data, clip_id=cid, platform="instagram",
                              views=1000, engagement=0.05, verified=False,
                              source="simulated", meta={"format_id": "bold-claim"})
    outs = loop_store.list_outcomes(s.data)
    assert len(outs) == 1 and outs[0]["views"] == 1000 and outs[0]["verified"] is False


# --- format library ---

def test_library_seeds_and_persists(s):
    lib = format_library.load_format_library(s.data)
    assert {f["id"] for f in lib} == {f["id"] for f in format_library.SEED_FORMATS}
    lib[0]["weight"] = 2.5
    format_library.save_format_library(s.data, lib)
    again = format_library.load_format_library(s.data)
    assert again[0]["weight"] == 2.5


# --- trend (offline path only) ---

@pytest.fixture()
def no_anthropic(monkeypatch):
    """Force the offline path hermetically: `import anthropic` raises, regardless
    of any real ANTHROPIC_API_KEY in .env."""
    monkeypatch.setitem(sys.modules, "anthropic", None)


def test_trend_offline_brief(s, no_anthropic):
    brief = trend.research("standup comedy", s)
    assert brief["source"] == "offline_seed"
    assert len(brief["recommended_hooks"]) == 5
    assert len(brief["recommended_formats"]) == 5
    assert (s.data / "trend_brief.json").exists()


def test_trend_prompt_section(s, no_anthropic):
    assert trend.prompt_section({}, s.data) == ""
    brief = trend.research("x", s)
    sec = trend.prompt_section(brief, s.data)
    assert "TREND BRIEF" in sec and "format_id" in sec
    assert brief["recommended_formats"][0] in sec


# --- publish (dry-run only) ---

def test_publish_dry_run_posts_nothing(s):
    cid = _clip(s)
    rec = publish.publish_clips(s, [cid], live=False)
    assert rec == [{"clip_id": cid, "status": "dryrun", "mode": "dryrun", "permalink": None}]
    pub = loop_store.list_publishes(s.data)[0]
    assert pub["status"] == "dryrun"
    assert pub["detail"]["would_post"]["file"] == "/tmp/clip_00.mp4"
    assert pub["external_id"] is None  # nothing left the system


def test_publish_missing_clip_is_skipped(s):
    assert publish.publish_clips(s, ["nonexistent"], live=False) == []
    assert loop_store.list_publishes(s.data) == []


# --- measure (simulated path) ---

def test_measure_simulated_is_labeled_and_deterministic(s):
    cid = _clip(s, score=80.0, meta={"order": 2})
    publish.publish_clips(s, [cid], live=False)
    measure.measure(s)
    outs = loop_store.list_outcomes(s.data)
    assert len(outs) == 1
    o = outs[0]
    assert o["source"] == "simulated" and o["verified"] is False
    assert o["views"] == 400 + 80 * 45 + (2 * 137) % 350
    assert o["meta"]["format_id"] == "bold-claim"

    measure.measure(s)  # re-run: simulated outcomes are recorded once
    assert len(loop_store.list_outcomes(s.data)) == 1


# --- learn ---

def test_learn_reweights_winners_up_losers_decay(s):
    lib = format_library.load_format_library(s.data)
    outcomes = [
        {"views": 5000, "engagement": 0.10, "meta": {"format_id": "bold-claim"}},
        {"views": 5000, "engagement": 0.10, "meta": {"format_id": "bold-claim"}},
        {"views": 100, "engagement": 0.01, "meta": {"format_id": "listicle"}},
        {"views": 9999, "engagement": 0.5, "meta": {}},  # unknown format: ignored
    ]
    ranking = learn.rank_formats(outcomes)
    assert set(ranking) == {"bold-claim", "listicle"}
    assert ranking["bold-claim"]["perf"] > ranking["listicle"]["perf"]

    new = {f["id"]: f["weight"] for f in learn.reweight(lib, ranking)}
    assert new["bold-claim"] > 1.0            # winner boosted
    assert new["listicle"] < new["bold-claim"]  # loser gets only a small boost
    assert new["quotable"] == pytest.approx(1.0)  # unranked decays toward baseline
    assert all(0.2 <= w <= 3.0 for w in new.values())


def test_learn_end_to_end(s):
    for i, (fid, score) in enumerate([("bold-claim", 90.0), ("listicle", 20.0)]):
        cid = _clip(s, format_id=fid, score=score, meta={"order": i})
        publish.publish_clips(s, [cid], live=False)
    measure.measure(s)
    what_worked = learn.learn(s)
    assert what_worked[0]["format_id"] == "bold-claim"  # higher score -> more sim views
    lib = {f["id"]: f["weight"] for f in format_library.load_format_library(s.data)}
    assert lib["bold-claim"] > lib["listicle"]
    assert json.loads((s.data / "what_worked.json").read_text()) == what_worked
