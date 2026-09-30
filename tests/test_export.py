import json
from pathlib import Path
from types import SimpleNamespace

from clipper import pipeline
from clipper.config import Settings
from clipper.runtime import encoder_args


def test_cpu_encoder_does_not_receive_nvenc_flags():
    args = encoder_args("libx264")
    assert "-crf" in args and "-cq" not in args and "p5" not in args
    assert "yuv420p" in args


def test_srt_uses_relative_timestamps(tmp_path):
    dest = tmp_path / "clip.srt"
    pipeline.write_srt([{"start": 100.1, "end": 100.9, "text": "Hello"}], 100, 105, dest)
    assert "00:00:00,100 --> 00:00:00,900" in dest.read_text()


def test_failed_render_does_not_look_successful(tmp_path, monkeypatch):
    s = Settings(work=tmp_path / "work", out=tmp_path / "out", data=tmp_path / "data", min_secs=1, max_secs=5)
    words = [{"start": 1.0, "end": 3.0, "text": "Hello."}]
    monkeypatch.setattr(pipeline.transcribe, "transcribe", lambda *a: words)
    monkeypatch.setattr(pipeline.audio, "reaction_peaks", lambda *a: [])
    monkeypatch.setattr(pipeline.highlights, "pick", lambda *a: [{"start": 0, "end": 20, "title": "Test"}])
    monkeypatch.setattr(pipeline.highlights, "polish_end", lambda *a: None)
    monkeypatch.setattr(pipeline, "ffprobe_dims", lambda *a: (1920, 1080))
    monkeypatch.setattr(pipeline, "run", lambda *a: SimpleNamespace(stdout="10"))
    def broken(src, clip, *args):
        assert clip["end"] == 5
        (s.out / "clip_00.mp4").write_bytes(b"partial invalid video")
        raise RuntimeError("encoder failed")
    monkeypatch.setattr(pipeline.render, "render_clip", broken)
    assert pipeline.process(tmp_path / "input.mp4", s) == []
    report = json.loads((s.out / "manifest.json").read_text())
    assert report["status"] == "failed" and len(report["errors"]) == 1
    assert not (s.out / "clip_00.mp4").exists()


def test_no_speech_produces_an_honest_manifest(tmp_path, monkeypatch):
    s = Settings(work=tmp_path / "work", out=tmp_path / "out")
    monkeypatch.setattr(pipeline.transcribe, "transcribe", lambda *a: [])
    assert pipeline.process(tmp_path / "silent.mp4", s) == []
    assert json.loads((s.out / "manifest.json").read_text())["status"] == "no_speech"
