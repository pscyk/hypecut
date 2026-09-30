"""hypecut <URL>: download, find moments, reframe, caption, export."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import uuid

from . import __version__


def _positive_int(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 50:
        raise argparse.ArgumentTypeError("choose a number from 1 to 50")
    return number


def _seconds(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("seconds must be finite and greater than zero")
    return number


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hypecut", description="Drop a link. Cut the good stuff. YouTube and Twitch → captioned vertical clips.",
                                epilog="First run: export ANTHROPIC_API_KEY=...  •  Check your setup: hypecut doctor")
    p.add_argument("source", nargs="?", help="YouTube URL, Twitch VOD/clip URL, local video, or 'doctor'")
    p.add_argument("--version", action="version", version=f"hypecut {__version__}")
    p.add_argument("-n", "--num-clips", type=_positive_int, default=None, help="number of highlights (default: 5)")
    p.add_argument("-o", "--out", type=Path, help="output root; each run gets a new subdirectory (default: ./hypecut-out)")
    p.add_argument("--min", type=_seconds, help="minimum clip seconds (default: 15)")
    p.add_argument("--max", type=_seconds, help="maximum clip seconds (default: 60)")
    p.add_argument("--hint", help="tell the editor which moments to look for")
    p.add_argument("--reframe", choices=["track-fast", "center", "facecam", "stream", "track-corr", "track"],
                   help="framing strategy (default: track-fast; track needs optional LR-ASD)")
    p.add_argument("--device", choices=["auto", "cpu", "cuda"], help="transcription device (default: auto)")
    p.add_argument("--encoder", choices=["auto", "libx264", "h264_nvenc"], help="video encoder (default: auto)")
    p.add_argument("--whisper", help="Whisper model (default: small)")
    p.add_argument("--fast", action="store_true", help="use the tiny multilingual Whisper model, without jury reranking")
    p.add_argument("--lang", help="source language code; default: detect")
    p.add_argument("--translate", action="store_true", help="translate speech to English captions")
    p.add_argument("--font", help="caption font (default: bundled Anton)")
    p.add_argument("--provider", choices=["anthropic", "kimi"], help="highlight provider (default: anthropic)")
    p.add_argument("--model", help="override the highlight and jury model")
    p.add_argument("--jury", action="store_true", help="rerank extra candidates with a visual audience jury (more API calls)")
    p.add_argument("--cookies", type=Path, help="Netscape cookies file for a video requiring your login")
    p.add_argument("--cache-dir", type=Path, help="download/transcript cache (default: ~/.cache/hypecut)")
    p.add_argument("--json", action="store_true", help="emit one result object to stdout; progress goes to stderr")
    p.add_argument("--verbose", action="store_true", help="include a traceback when a run fails")
    return p


def cache_key(src: Path, s) -> str:
    stat = src.stat()
    selection = {name: getattr(s, name) for name in (
        "whisper_model", "task", "language", "provider", "model", "pick_model", "jury_model",
        "kimi_model", "kimi_pick_model", "kimi_jury_model", "kimi_base_url", "hint", "min_secs", "max_secs", "num_clips", "jury", "candidate_pool", "discover", "discover_model", "kimi_discover_model")}
    data = [str(src.resolve()), stat.st_size, stat.st_mtime_ns, selection]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:24]


def execute(args, p) -> tuple[dict, int]:
    # Imports are intentionally lazy: --help and --version don't load GPU/CV models.
    from .config import Settings, ROOT
    from . import download, runtime
    s = Settings()
    for option, setting in (("num_clips", "num_clips"), ("min", "min_secs"), ("max", "max_secs"),
                            ("hint", "hint"), ("reframe", "reframe"), ("device", "whisper_device"),
                            ("encoder", "vcodec"), ("whisper", "whisper_model"), ("lang", "language"),
                            ("font", "font"), ("provider", "provider")):
        value = getattr(args, option)
        if value is not None:
            setattr(s, setting, value)
    if s.min_secs > s.max_secs:
        p.error("--min must be less than or equal to --max")
    if not (1 <= s.num_clips <= 50) or not all(math.isfinite(x) and x > 0 for x in (s.min_secs, s.max_secs)):
        p.error("clip count must be 1–50 and durations must be finite positive seconds")
    if s.provider not in {"anthropic", "kimi"}:
        p.error("provider must be anthropic or kimi")
    if args.model:
        s.model = args.model
        s.pick_model = s.jury_model = s.discover_model = args.model
        s.kimi_model = s.kimi_pick_model = s.kimi_jury_model = args.model
    if args.translate:
        s.task = "translate"
    s.jury, s.batch = args.jury and not args.fast, False
    if args.fast and not args.whisper:
        s.whisper_model = "tiny"
    if args.source == "doctor":
        checks = runtime.preflight(s, require_key=True)
        print("Hypecut setup\n" + "\n".join(f"  ✓ {line}" for line in checks))
        return {"status": "ready", "checks": checks}, 0
    remote = download.is_url(args.source)
    if remote:
        try:
            download.validate_url(args.source)
        except ValueError as exc:
            p.error(str(exc))
    else:
        src = Path(args.source).expanduser().resolve()
        if not src.is_file():
            p.error(f"no such video file: {src}")
    if args.cookies:
        args.cookies = args.cookies.expanduser().resolve()
        if not args.cookies.is_file():
            p.error(f"no such cookies file: {args.cookies}")
    checks = runtime.preflight(s)
    print(f"HYPECUT / {s.num_clips} clips / 1080 × 1920")
    print("  " + " · ".join(checks[1:]))
    cache = (args.cache_dir or ROOT).expanduser().resolve()
    meta = {}
    if remote:
        src = download.fetch(args.source, directory=cache / "videos", cookies=args.cookies, metadata=meta)
    s.title = meta.get("title") or src.stem
    s.heatmap = [{"start": h["start_time"], "end": h["end_time"], "value": h["value"]} for h in (meta.get("heatmap") or [])]
    key = cache_key(src, s)
    # Separate cached analysis from per-run render intermediates to avoid collisions.
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", s.title).strip("-")[:60] or "video"
    s.out = (args.out or Path.cwd() / "hypecut-out").expanduser().resolve() / f"{slug}-{run_id}"
    s.work = cache / "runs" / run_id
    s.analysis = cache / "analysis" / key
    s.data = cache / "data"
    from . import pipeline
    outputs = pipeline.process(src, s)
    report = json.loads((s.out / "manifest.json").read_text())
    report["input"] = args.source if not remote else _public_url(args.source)
    report["title"] = s.title
    report["output_dir"] = str(s.out)
    report["device"] = s.whisper_device
    report["encoder"] = s.vcodec
    (s.out / "manifest.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"\n{len(outputs)} clip(s) saved → {s.out}")
    for output in outputs:
        print(f"  {output.name}")
    print("  manifest.json")
    return report, 0 if report["status"] == "complete" else 1


def _public_url(url: str) -> str:
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    parts = urlsplit(url)
    # Keep only the video identifier, not login/query tokens, in exported metadata.
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k == "v"])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def main(argv: list[str] | None = None) -> int:
    p = parser()
    args = p.parse_args(argv)
    if not args.source:
        p.print_help()
        return 0
    try:
        # Native downloader output is also redirected using stderr in download.py.
        with redirect_stdout(sys.stderr):
            report, code = execute(args, p)
        if args.json:
            print(json.dumps(report, ensure_ascii=False))
        return code
    except KeyboardInterrupt:
        print("\nHypecut cancelled. Finished clips and caches are kept.", file=sys.stderr)
        return 130
    except Exception as exc:
        if args.verbose:
            import traceback
            traceback.print_exc()
        print(f"hypecut: {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"status": "failed", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
