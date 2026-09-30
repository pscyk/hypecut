"""Download exactly one completed YouTube or Twitch video with yt-dlp."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import ROOT

VIDEOS = ROOT / "videos"


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("Use a public YouTube or Twitch video URL.")
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        query = parse_qs(parsed.query)
        if path == "/watch" and query.get("v"):
            return
        if any(path.startswith(prefix) and len(path) > len(prefix) for prefix in ("/shorts/", "/live/", "/embed/")):
            return
    elif host == "youtu.be" and path.count("/") == 1 and len(path) > 1:
        return
    elif host == "clips.twitch.tv" and len(path) > 1:
        return
    elif host in {"twitch.tv", "www.twitch.tv", "m.twitch.tv"}:
        parts = path.strip("/").split("/")
        if (len(parts) == 2 and parts[0] == "videos" and parts[1].isdigit()) or (len(parts) == 3 and parts[1] == "clip" and parts[2]):
            return
    raise ValueError("Use a YouTube video, Twitch VOD, or Twitch clip URL. Channel pages, playlists, and live streams are not supported.")


def heatmap(url: str) -> list[dict]:
    """Optional legacy signal; the CLI reads this from the single download pass."""
    return []


def fetch(url: str, max_height: int = 1080, *, directory: Path | None = None,
          cookies: Path | None = None, metadata: dict | None = None) -> Path:
    validate_url(url)
    directory = (directory or VIDEOS).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # yt-dlp reports the final post-merge pathname. Never guess by URL ID or mtime:
    # Twitch slugs differ from video IDs, and a stale MP4 can belong to another URL.
    with tempfile.TemporaryDirectory(prefix="hypecut-download-") as temp:
        receipt = Path(temp) / "result.jsonl"
        command = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist",
                   "--match-filters", "!is_live", "--no-simulate", "--newline",
                   "--socket-timeout", "30", "--retries", "3",
                   "-f", f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
                   "--merge-output-format", "mp4", "--restrict-filenames",
                   "--output-na-placeholder", "null",
                   "-o", str(directory / "%(extractor_key)s-%(id)s.%(ext)s"),
                   "--print-to-file", 'after_move:{"filepath":%(filepath)j,"id":%(id)j,"title":%(title)j,"heatmap":%(heatmap)j}', str(receipt)]
        if cookies:
            command += ["--cookies", str(cookies)]
        # Explicitly enable an installed JS runtime for current YouTube challenges.
        import shutil
        for runtime in ("deno", "node", "bun"):
            if shutil.which(runtime):
                command += ["--js-runtimes", runtime]
                break
        print("[download] fetching video", flush=True)
        result = subprocess.run([*command, "--", url], stdout=sys.stderr, stderr=sys.stderr)
        if result.returncode:
            raise RuntimeError("Download failed. Check the URL and access; update yt-dlp or use --cookies FILE if sign-in is required.")
        rows = receipt.read_text().splitlines() if receipt.exists() else []
        if len(rows) != 1:
            raise RuntimeError("No completed video was downloaded. Use an archived video or clip, not a live stream.")
        info = json.loads(rows[0])
        path = Path(info["filepath"]).resolve()
        if not path.is_file() or not path.is_relative_to(directory):
            raise RuntimeError("yt-dlp did not produce the reported video file.")
        if metadata is not None:
            metadata.update(info)
        return path
