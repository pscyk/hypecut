"""Transcript cache keying: same settings hit, changed settings re-transcribe."""
import json

from clipper import transcribe
from clipper.config import Settings

WORDS = [{"start": 0.0, "end": 0.5, "text": "hi"}]


def test_cache_hit_same_settings(tmp_path):
    s = Settings()
    cache = tmp_path / "w.json"
    meta = {"model": s.whisper_model, "task": s.task, "language": s.language or "auto"}
    cache.write_text(json.dumps({"meta": meta, "words": WORDS}))
    assert transcribe.transcribe(tmp_path / "v.mp4", s, cache) == WORDS


def test_legacy_list_cache_still_accepted(tmp_path):
    s = Settings()
    cache = tmp_path / "w.json"
    cache.write_text(json.dumps(WORDS))
    assert transcribe.transcribe(tmp_path / "v.mp4", s, cache) == WORDS


def test_cache_stale_on_translate(tmp_path, monkeypatch):
    s = Settings()
    cache = tmp_path / "w.json"
    meta = {"model": s.whisper_model, "task": "transcribe", "language": s.language or "auto"}
    cache.write_text(json.dumps({"meta": meta, "words": WORDS}))
    s.task = "translate"  # user re-ran with --translate
    called = {}

    def boom(_s):
        called["yes"] = True
        raise RuntimeError("re-transcribe (expected)")

    monkeypatch.setattr(transcribe, "_load", boom)
    try:
        transcribe.transcribe(tmp_path / "v.mp4", s, cache)
    except RuntimeError:
        pass
    assert called.get("yes")
