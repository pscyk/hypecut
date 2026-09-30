"""Highlight snapping / sentence spans / hook completion + cache behavior."""
import json

from clipper import highlights
from clipper.config import Settings


def w(start, end, text):
    return {"start": start, "end": end, "text": text}


WORDS = [w(i * 1.0, i * 1.0 + 0.8, f"word{i}" + ("." if i % 5 == 4 else ""))
         for i in range(60)]  # a sentence ends every 5 words


def test_snap_to_word_boundaries():
    s = Settings()
    s.min_secs, s.max_secs = 5, 60
    clips = highlights._snap([{"start": 3.2, "end": 20.1, "title": "t"}], WORDS, s)
    assert clips[0]["start"] == 3.0        # nearest word start
    assert clips[0]["end"] == 19.8         # nearest word end


def test_snap_drops_too_short():
    s = Settings()
    s.min_secs, s.max_secs = 15, 60
    assert highlights._snap([{"start": 0, "end": 5, "title": "t"}], WORDS, s) == []


def test_snap_clamps_too_long():
    s = Settings()
    s.min_secs, s.max_secs = 5, 20
    clips = highlights._snap([{"start": 0, "end": 59, "title": "t"}], WORDS, s)
    assert clips[0]["end"] - clips[0]["start"] <= 20


def test_sentence_spans_split_on_terminal_punct():
    spans = highlights._sentence_spans(WORDS[:15])
    assert len(spans) == 3
    assert spans[0] == (0.0, 4.8)  # word0..word4.


def test_sentence_spans_ignore_ellipsis():
    words = [w(0, 0.5, "so..."), w(1, 1.5, "yeah.")]
    assert highlights._sentence_spans(words) == [(0, 1.5)]


def test_complete_hook_snaps_to_whole_sentence():
    clip = {"start": 0.0, "end": 30.0}
    span = highlights._complete_hook(WORDS, 6.2, 8.0, clip)  # mid-sentence pick
    assert span == (5.0, 9.8)  # expanded to the full word5..word9 sentence


def test_pick_cache_slices_to_num(tmp_path):
    s = Settings()
    s.hint = ""
    cache = tmp_path / "clips.json"
    cached = [{"start": i * 10.0, "end": i * 10.0 + 9.0, "title": f"c{i}"} for i in range(8)]
    cache.write_text(json.dumps({"hint": "", "clips": cached}))
    got = highlights.pick(WORDS, s, cache, num=3)
    assert len(got) == 3


def test_pick_cache_invalidated_by_hint(tmp_path, monkeypatch):
    s = Settings()
    s.hint = "the drunk actor bit"
    cache = tmp_path / "clips.json"
    cache.write_text(json.dumps({"hint": "", "clips": [{"start": 0, "end": 9, "title": "c"}]}))
    called = {}

    def boom(*a, **kw):  # any cache miss should call the LLM gateway -> our sentinel
        called["yes"] = True
        raise RuntimeError("cache miss (expected)")

    monkeypatch.setattr(highlights.llm, "tool_json", boom)
    try:
        highlights.pick(WORDS, s, cache, num=1)
    except RuntimeError:
        pass
    assert called.get("yes")
