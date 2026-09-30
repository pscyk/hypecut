import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipper import download


@pytest.mark.parametrize("url", ["https://youtu.be/abc", "https://www.youtube.com/watch?v=abc&list=xyz",
    "https://youtube.com/shorts/abc", "https://twitch.tv/videos/1234", "https://www.twitch.tv/user/clip/SomeSlug",
    "https://clips.twitch.tv/SomeSlug"])
def test_supported_urls(url):
    download.validate_url(url)


@pytest.mark.parametrize("url", ["https://youtube.com.evil.example/watch?v=x", "https://youtube.com/playlist?list=x",
    "https://twitch.tv/user", "https://example.com/file.mp4", "https://secret@youtube.com/watch?v=x"])
def test_rejects_channels_playlists_and_wrong_hosts(url):
    with pytest.raises(ValueError):
        download.validate_url(url)


def test_twitch_uses_reported_path_not_newest_mp4(tmp_path, monkeypatch):
    actual = tmp_path / "TwitchClips-different-id.mp4"
    actual.write_bytes(b"download")
    (tmp_path / "newest-but-unrelated.mp4").write_bytes(b"wrong")
    def fake_run(command, **kwargs):
        assert "--no-playlist" in command and "--ignore-config" in command
        assert "!is_live" in command
        receipt = Path(command[command.index("--print-to-file") + 2])
        receipt.write_text(json.dumps({"filepath": str(actual), "title": "The right clip", "heatmap": None}) + "\n")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(download.subprocess, "run", fake_run)
    metadata = {}
    assert download.fetch("https://clips.twitch.tv/Slug", directory=tmp_path, metadata=metadata) == actual
    assert metadata["title"] == "The right clip"


def test_no_receipt_never_reuses_old_video(tmp_path, monkeypatch):
    (tmp_path / "old.mp4").write_bytes(b"old")
    monkeypatch.setattr(download.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    with pytest.raises(RuntimeError, match="No completed video"):
        download.fetch("https://clips.twitch.tv/Slug", directory=tmp_path)


def test_receipt_template_serializes_absent_twitch_heatmap():
    import yt_dlp
    template = '{"filepath":%(filepath)j,"id":%(id)j,"title":%(title)j,"heatmap":%(heatmap)j}'
    with yt_dlp.YoutubeDL({"outtmpl_na_placeholder": "null", "quiet": True}) as ydl:
        rendered = ydl.evaluate_outtmpl(template, {"filepath": "/tmp/clip.mp4", "id": "123", "title": "Twitch"})
    assert json.loads(rendered)["heatmap"] is None
