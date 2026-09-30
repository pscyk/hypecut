"""Jury scoring math + cache keying."""
import json
from pathlib import Path

from clipper import jury
from clipper.config import Settings


def reaction(**over):
    base = {k: 5 for k in ("stop_scroll", "watch_through", "like", "comment",
                           "save", "share", "hook_score", "clarity_score", "payoff_score")}
    base.update(over)
    return base


def test_weights_sum_to_one():
    assert abs(sum(jury.WEIGHTS.values()) - 1.0) < 1e-9


def test_viral_empty_is_zero():
    assert jury._viral([]) == 0.0


def test_viral_weak_act_drags_score_down():
    flat = jury._viral([reaction()])
    dead_hook = jury._viral([reaction(hook_score=1)])
    assert dead_hook < flat


def test_viral_bounded_0_10():
    lo = jury._viral([reaction(**{k: 0 for k in reaction()})])
    hi = jury._viral([reaction(**{k: 10 for k in reaction()})])
    assert 0.0 <= lo <= hi <= 10.0


def _key(s: Settings, cal: str, hint: str, spans: list) -> dict:
    """The exact cache key rank() computes for these settings."""
    return {"cal": cal, "hint": hint, "spans": spans,
            "prov": f"{jury.llm.provider(s)}:{jury.llm.resolve_model(s, 'jury_model')}"}


def _stub_scoring(monkeypatch, called: dict):
    """On a cache miss, rank() builds sprite sheets then scores via the gateway.
    Stub both so tests stay pure-logic (no ffmpeg, no API)."""
    monkeypatch.setattr(jury.spritesheet, "make_sheet", lambda *a, **k: None)
    monkeypatch.setattr(jury.spritesheet, "to_b64", lambda *a, **k: "")

    def fake_tool_json(s, **kw):
        called["yes"] = True
        return {"reactions": []}

    monkeypatch.setattr(jury.llm, "tool_json", fake_tool_json)


def test_rank_cache_hit_same_key(tmp_path):
    s = Settings()
    s.hint = ""
    s.batch = False  # provider-independent path
    cands = [{"start": 1.0, "end": 20.0, "title": "a"}]
    cache = tmp_path / "jury.json"
    ranked = [{**cands[0], "viral_score": 7.0}]
    cache.write_text(json.dumps(
        {"key": _key(s, jury._CAL, "", [[1.0, 20.0]]), "ranked": ranked}))
    got = jury.rank(cands, Path("x.mp4"), [], s, cache)
    assert got == ranked  # no scoring happens on a cache hit


def test_rank_cache_miss_on_stale_calibration(tmp_path, monkeypatch):
    s = Settings()
    s.hint = ""
    s.batch = False
    cands = [{"start": 1.0, "end": 20.0, "title": "a"}]
    cache = tmp_path / "jury.json"
    cache.write_text(json.dumps(  # ranked under an older persona/weight calibration
        {"key": _key(s, "ig-in-t1-v0", "", [[1.0, 20.0]]),
         "ranked": [{"viral_score": 7.0}]}))
    called = {}
    _stub_scoring(monkeypatch, called)
    jury.rank(cands, Path("x.mp4"), [], s, cache)
    assert called.get("yes")


def test_rank_cache_miss_on_new_hint(tmp_path, monkeypatch):
    s = Settings()
    s.hint = "new steer"
    s.batch = False
    cands = [{"start": 1.0, "end": 20.0, "title": "a"}]
    cache = tmp_path / "jury.json"
    cache.write_text(json.dumps(
        {"key": _key(s, jury._CAL, "", [[1.0, 20.0]]),
         "ranked": [{"viral_score": 7.0}]}))
    called = {}
    _stub_scoring(monkeypatch, called)
    jury.rank(cands, Path("x.mp4"), [], s, cache)
    assert called.get("yes")


def test_rank_cache_miss_on_provider_switch(tmp_path, monkeypatch):
    """A ranking cached under anthropic must NOT be reused after switching to kimi."""
    s = Settings()
    s.hint = ""
    s.batch = False
    cands = [{"start": 1.0, "end": 20.0, "title": "a"}]
    cache = tmp_path / "jury.json"
    cache.write_text(json.dumps(
        {"key": _key(s, jury._CAL, "", [[1.0, 20.0]]),  # cached under anthropic
         "ranked": [{**cands[0], "viral_score": 7.0}]}))
    s.provider = "kimi"  # switch providers -> key changes -> re-rank
    called = {}
    _stub_scoring(monkeypatch, called)
    jury.rank(cands, Path("x.mp4"), [], s, cache)
    assert called.get("yes")
