"""HTTP service wrapping the clip pipeline: POST a video URL, poll for clips.

Runs on the GPU box (anton) as a systemd unit; the katas worker on cloudy
drives it over Tailscale with a shared bearer token. One job runs at a time
(the GPU is serial); jobs queue in-process and persist status under
work/jobs/<id>/status.json so a restart never loses a finished result.
"""
from __future__ import annotations

import hmac
import json
import os
import queue
import re
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import download, pipeline
from .config import ROOT, Settings, run

JOBS = ROOT / "work" / "jobs"
TOKEN = os.getenv("CLIPPER_SERVICE_TOKEN", "")
RETENTION_SECS = 24 * 3600  # finished job dirs are wiped after a day
POLL_NONE = queue.Empty()

if not TOKEN:
    raise RuntimeError("CLIPPER_SERVICE_TOKEN is not set — refusing to start unauthenticated")

app = FastAPI(title="clipper", version="0.1.0")


# ---------------------------------------------------------------------------
# auth

def _auth(authorization: str = Header(default="")) -> None:
    """Bearer-token gate on every /v1/* route. compare_digest so a timing
    probe can't walk the token byte by byte."""
    want = f"Bearer {TOKEN}"
    if not hmac.compare_digest(authorization, want):
        raise HTTPException(status_code=401, detail="unauthorized")


# ---------------------------------------------------------------------------
# request/response models

class JobRequest(BaseModel):
    source_url: str = Field(min_length=8, max_length=2048)
    num_clips: int = Field(default=5, ge=1, le=10)
    min_secs: float = Field(default=15, ge=5, le=300)
    max_secs: float = Field(default=60, ge=10, le=600)
    hint: str = Field(default="", max_length=500)
    hook: bool = False
    jury: bool = True
    language: str | None = Field(default=None, max_length=32)
    translate: bool = False


# ---------------------------------------------------------------------------
# job store: one dir per job under work/jobs, status.json is the source of truth

def _job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f-]{36}", job_id):
        raise HTTPException(status_code=404, detail="job not found")
    d = JOBS / job_id
    if not d.is_dir():
        raise HTTPException(status_code=404, detail="job not found")
    return d


def _read_status(job_id: str) -> dict:
    f = _job_dir(job_id) / "status.json"
    try:
        return json.loads(f.read_text())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="job not found")


def _write_status(job_id: str, st: dict) -> None:
    tmp = JOBS / job_id / "status.json.tmp"
    tmp.write_text(json.dumps(st, indent=2))
    tmp.replace(JOBS / job_id / "status.json")  # atomic rename: readers never see a torn write


def _recover_on_boot() -> None:
    """Jobs left 'queued'/'running' by a crash or restart can never resume
    (the worker thread is gone) — flip them to failed so pollers terminate."""
    if not JOBS.is_dir():
        return
    for d in JOBS.iterdir():
        f = d / "status.json"
        if not f.exists():
            continue
        try:
            st = json.loads(f.read_text())
        except Exception:
            continue
        if st.get("status") in ("queued", "running"):
            st["status"] = "failed"
            st["error"] = "clipper service restarted mid-job"
            st["completed_at"] = time.time()
            _write_status(d.name, st)
            print(f"[service] recovered {d.name} -> failed (restart)")


# ---------------------------------------------------------------------------
# worker thread

_q: "queue.Queue[str]" = queue.Queue()


def _probe_duration_secs(path: Path) -> float:
    cp = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "csv=p=0", str(path)])
    return round(float(cp.stdout.strip()), 3)


def _run_job(job_id: str) -> None:
    st = _read_status(job_id)
    st["status"] = "running"
    st["started_at"] = time.time()
    _write_status(job_id, st)
    try:
        opts = st["options"]
        s = Settings()
        job_dir = JOBS / job_id
        s.work = job_dir / "cache"
        s.out = job_dir / "out"
        s.num_clips = opts["num_clips"]
        s.min_secs, s.max_secs = opts["min_secs"], opts["max_secs"]
        s.hint, s.hook, s.jury = opts["hint"], opts["hook"], opts["jury"]
        s.language = opts["language"]
        if opts["translate"]:
            s.task = "translate"

        url = st["source_url"]
        if download.is_url(url):
            s.heatmap = download.heatmap(url)  # YouTube most-replayed, when present
            src = download.fetch(url)
        else:
            src = Path(url)  # local path — used by ops smoke tests only
        outputs = pipeline.process(src.resolve(), s)
        if not outputs:
            raise RuntimeError("Nothing relevant found: 0 clips published")
        clips = [
            {
                "name": p.name,
                "duration_s": _probe_duration_secs(p),
                "size_bytes": p.stat().st_size,
            }
            for p in sorted(outputs)
        ]
        st = _read_status(job_id)
        st["status"] = "done"
        st["clips"] = clips
        st["completed_at"] = time.time()
        _write_status(job_id, st)
        print(f"[service] {job_id} done: {len(clips)} clips")
    except Exception as e:
        tail = "\n".join(traceback.format_exc().splitlines()[-6:])
        print(f"[service] {job_id} failed: {e}\n{tail}")
        st = _read_status(job_id)
        st["status"] = "failed"
        st["error"] = str(e)[:1000]
        st["completed_at"] = time.time()
        _write_status(job_id, st)


def _worker_loop() -> None:
    while True:
        job_id = _q.get()
        try:
            _run_job(job_id)
        except Exception:  # _run_job already traps; this is belt-and-braces for the loop
            traceback.print_exc()
        finally:
            _q.task_done()


def _sweeper_loop() -> None:
    """Wipe finished job dirs past RETENTION_SECS. Output mp4s are fetched by
    the katas worker right after completion, so a day of local retention is
    pure slack for re-downloads/debugging."""
    while True:
        time.sleep(1800)
        try:
            now = time.time()
            for d in (JOBS.iterdir() if JOBS.is_dir() else []):
                f = d / "status.json"
                if not f.exists():
                    continue
                try:
                    st = json.loads(f.read_text())
                except Exception:
                    continue
                if st.get("status") not in ("done", "failed"):
                    continue
                if now - st.get("completed_at", now) > RETENTION_SECS:
                    shutil.rmtree(d, ignore_errors=True)
                    print(f"[service] swept {d.name}")
        except Exception:
            traceback.print_exc()


@app.on_event("startup")
def _startup() -> None:
    JOBS.mkdir(parents=True, exist_ok=True)
    _recover_on_boot()
    threading.Thread(target=_worker_loop, daemon=True).start()
    threading.Thread(target=_sweeper_loop, daemon=True).start()


# ---------------------------------------------------------------------------
# routes

@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "queued": _q.qsize()}


@app.post("/v1/jobs", status_code=202, dependencies=[Depends(_auth)])
def create_job(req: JobRequest) -> dict:
    if not (req.source_url.startswith("https://") or req.source_url.startswith("http://")):
        raise HTTPException(status_code=400, detail="source_url must be http(s)")
    if req.min_secs >= req.max_secs:
        raise HTTPException(status_code=400, detail="min_secs must be < max_secs")
    job_id = str(uuid.uuid4())
    (JOBS / job_id / "out").mkdir(parents=True)
    _write_status(job_id, {
        "id": job_id,
        "status": "queued",
        "source_url": req.source_url,
        "options": req.model_dump(),
        "clips": [],
        "error": None,
        "created_at": time.time(),
        "started_at": None,
        "completed_at": None,
    })
    _q.put(job_id)
    return {"job_id": job_id, "status": "queued"}


@app.get("/v1/jobs/{job_id}", dependencies=[Depends(_auth)])
def get_job(job_id: str) -> dict:
    return _read_status(job_id)


@app.get("/v1/jobs/{job_id}/clips/{name}", dependencies=[Depends(_auth)])
def get_clip(job_id: str, name: str) -> FileResponse:
    if not re.fullmatch(r"clip_\d+\.mp4", name):
        raise HTTPException(status_code=404, detail="clip not found")
    f = _job_dir(job_id) / "out" / name
    if not f.exists():
        raise HTTPException(status_code=404, detail="clip not found")
    return FileResponse(f, media_type="video/mp4", filename=name)


@app.get("/media/{clip_id}")
def serve_loop_media(clip_id: str) -> FileResponse:
    """Serve a loop-registered clip by id. UNAUTHENTICATED BY DESIGN: the Meta
    Graph API fetches the video from this public URL when publishing a Reel (it
    can't present our bearer token). Expose only via a pointed tunnel/base URL
    (PUBLIC_MEDIA_BASE_URL); clip ids are unguessable 12-hex uuids."""
    from . import loop_store

    if not re.fullmatch(r"[0-9a-f]{12}", clip_id):
        raise HTTPException(status_code=404, detail="clip not found")
    row = loop_store.get_clip(ROOT / "data", clip_id)
    if not row:
        raise HTTPException(status_code=404, detail="clip not found")
    f = Path(row["path"])
    if not f.exists():
        raise HTTPException(status_code=404, detail="clip file gone")
    return FileResponse(f, media_type="video/mp4", filename=f.name)
