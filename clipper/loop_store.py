"""Loop persistence — the distribution loop's memory (stdlib sqlite3, no deps).

Three tables, mirroring oxcorp-loop's models minus the run concept (clipper's
pipeline is keyed by source file, not runs):

  clips     every rendered clip registered by the pipeline (path, hook, format_id, jury score)
  publishes every publish attempt (dry-run AND live) with the full payload
  outcomes  performance per published clip (real IG insights, or CLEARLY-LABELED simulated)

DB lives at data/loop.sqlite — a persisted asset like the format library, not a
work/ cache. Rows are plain dicts; JSON columns (meta/detail) are text.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  path TEXT NOT NULL,
  start REAL NOT NULL,
  end REAL NOT NULL,
  hook TEXT DEFAULT '',
  caption TEXT DEFAULT '',
  format_id TEXT DEFAULT '',
  score REAL,
  meta TEXT DEFAULT '{}',
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS publishes (
  id TEXT PRIMARY KEY,
  clip_id TEXT NOT NULL REFERENCES clips(id),
  platform TEXT NOT NULL,
  mode TEXT NOT NULL,      -- dryrun | live
  status TEXT NOT NULL,    -- dryrun | published | failed | skipped
  external_id TEXT,
  permalink TEXT,
  detail TEXT DEFAULT '{}',
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS outcomes (
  id TEXT PRIMARY KEY,
  clip_id TEXT NOT NULL REFERENCES clips(id),
  platform TEXT NOT NULL,
  views INTEGER DEFAULT 0,
  engagement REAL DEFAULT 0.0,
  verified INTEGER DEFAULT 0,
  source TEXT DEFAULT '',  -- ig_insights | simulated | fetch_failed
  meta TEXT DEFAULT '{}',
  collected_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_publishes_clip ON publishes(clip_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_clip ON outcomes(clip_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(data_dir: Path) -> sqlite3.Connection:
    d = Path(data_dir).resolve()
    d.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(d / "loop.sqlite")
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def _row_dict(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    for k in ("meta", "detail"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except json.JSONDecodeError:
                d[k] = {}
    if "verified" in d:
        d["verified"] = bool(d["verified"])
    return d


# --- clips ---

def register_clip(data_dir: Path, *, source: str, path: str, start: float, end: float,
                  hook: str = "", caption: str = "", format_id: str = "",
                  score: Optional[float] = None, meta: Optional[dict] = None) -> str:
    clip_id = uuid.uuid4().hex[:12]
    with connect(data_dir) as con:
        con.execute(
            "INSERT INTO clips (id, source, path, start, end, hook, caption, format_id, score, meta, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (clip_id, source, path, start, end, hook, caption, format_id, score,
             json.dumps(meta or {}), _now()),
        )
    return clip_id


def get_clip(data_dir: Path, clip_id: str) -> Optional[dict[str, Any]]:
    with connect(data_dir) as con:
        row = con.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    return _row_dict(row) if row else None


def list_clips(data_dir: Path) -> list[dict[str, Any]]:
    with connect(data_dir) as con:
        rows = con.execute("SELECT * FROM clips ORDER BY created_at DESC").fetchall()
    return [_row_dict(r) for r in rows]


# --- publishes ---

def insert_publish(data_dir: Path, *, clip_id: str, platform: str, mode: str, status: str,
                   external_id: Optional[str] = None, permalink: Optional[str] = None,
                   detail: Optional[dict] = None) -> str:
    pub_id = uuid.uuid4().hex[:12]
    with connect(data_dir) as con:
        con.execute(
            "INSERT INTO publishes (id, clip_id, platform, mode, status, external_id, permalink, detail, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (pub_id, clip_id, platform, mode, status, external_id, permalink,
             json.dumps(detail or {}), _now()),
        )
    return pub_id


def list_publishes(data_dir: Path, clip_id: Optional[str] = None) -> list[dict[str, Any]]:
    with connect(data_dir) as con:
        if clip_id:
            rows = con.execute(
                "SELECT * FROM publishes WHERE clip_id = ? ORDER BY created_at", (clip_id,)).fetchall()
        else:
            rows = con.execute("SELECT * FROM publishes ORDER BY created_at").fetchall()
    return [_row_dict(r) for r in rows]


# --- outcomes ---

def insert_outcome(data_dir: Path, *, clip_id: str, platform: str, views: int = 0,
                   engagement: float = 0.0, verified: bool = False, source: str = "",
                   meta: Optional[dict] = None) -> str:
    out_id = uuid.uuid4().hex[:12]
    with connect(data_dir) as con:
        con.execute(
            "INSERT INTO outcomes (id, clip_id, platform, views, engagement, verified, source, meta, collected_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (out_id, clip_id, platform, views, engagement, int(verified), source,
             json.dumps(meta or {}), _now()),
        )
    return out_id


def list_outcomes(data_dir: Path) -> list[dict[str, Any]]:
    with connect(data_dir) as con:
        rows = con.execute("SELECT * FROM outcomes ORDER BY collected_at").fetchall()
    return [_row_dict(r) for r in rows]
