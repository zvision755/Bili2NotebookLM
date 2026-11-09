"""SQLite job store: one row per import job, checkpointed after every stage.

Holds ids and local paths only. NEVER store cookies/tokens/headers here.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from bili2nb.config import data_dir

# status
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
INTERRUPTED = "interrupted"
# stages (checkpoints, in order)
STAGES = ("created", "converted", "notebook_created", "uploaded", "ready", "done")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  video_key TEXT NOT NULL, account_key TEXT NOT NULL, mode TEXT NOT NULL,
  url TEXT NOT NULL, options TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL, stage TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  video_id TEXT, video_title TEXT, duration_sec REAL,
  output_dir TEXT, audio_path TEXT, pdf_path TEXT, files_cleaned INTEGER NOT NULL DEFAULT 0,
  notebook_id TEXT, sources TEXT NOT NULL DEFAULT '{}', ready TEXT NOT NULL DEFAULT '{}',
  visual TEXT NOT NULL DEFAULT '{}',
  error_code TEXT, error_message TEXT, error_detail TEXT,
  warnings TEXT NOT NULL DEFAULT '[]', timings TEXT NOT NULL DEFAULT '{}',
  attempts INTEGER NOT NULL DEFAULT 0, superseded INTEGER NOT NULL DEFAULT 0,
  orphans TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_jobs_key ON jobs(video_key, account_key, mode);
"""
_JSON_FIELDS = {"options", "sources", "ready", "visual", "warnings", "timings", "orphans"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Job:
    job_id: str
    video_key: str
    account_key: str
    mode: str
    url: str
    status: str
    stage: str
    created_at: str
    updated_at: str
    options: dict = field(default_factory=dict)
    video_id: str | None = None
    video_title: str | None = None
    duration_sec: float | None = None
    output_dir: str | None = None
    audio_path: str | None = None
    pdf_path: str | None = None
    files_cleaned: bool = False
    notebook_id: str | None = None
    sources: dict = field(default_factory=dict)  # {"audio": id, "pdf": id}
    ready: dict = field(default_factory=dict)  # {"audio": True, ...}
    visual: dict = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    error_detail: str | None = None
    warnings: list = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    attempts: int = 0
    superseded: bool = False
    # Source ids this job registered that got stuck (PREPARING/ERROR) and could not be deleted yet.
    # This is the ONLY evidence (besides 'created during the current call') that lets the tool
    # delete a source: same-name alone never is.
    orphans: list = field(default_factory=list)


class JobStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "jobs.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._conn()) as c:
            c.executescript(_SCHEMA)
            self._migrate(c)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _migrate(c: sqlite3.Connection) -> None:
        """Add columns introduced after V1 to existing databases (idempotent)."""
        cols = {r[1] for r in c.execute("PRAGMA table_info(jobs)")}
        if "orphans" not in cols:
            c.execute("ALTER TABLE jobs ADD COLUMN orphans TEXT NOT NULL DEFAULT '[]'")
            c.commit()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        d = dict(row)
        for k in _JSON_FIELDS:
            d[k] = json.loads(d[k])
        d["files_cleaned"] = bool(d["files_cleaned"])
        d["superseded"] = bool(d["superseded"])
        return Job(**d)

    def create(self, *, video_key: str, account_key: str, mode: str, url: str, options: dict,
               video_id: str | None = None, video_title: str | None = None,
               duration_sec: float | None = None) -> Job:
        job_id = f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        now = _now()
        with closing(self._conn()) as c, c:
            c.execute(
                "INSERT INTO jobs(job_id, video_key, account_key, mode, url, options, status, stage,"
                " created_at, updated_at, video_id, video_title, duration_sec)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, video_key, account_key, mode, url, json.dumps(options), RUNNING,
                 "created", now, now, video_id, video_title, duration_sec),
            )
        return self.get(job_id)  # type: ignore[return-value]

    def get(self, job_id: str) -> Job | None:
        with closing(self._conn()) as c:
            row = c.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def update(self, job_id: str, **fields) -> Job:
        """Persist checkpoint fields immediately."""
        if not fields:
            return self.get(job_id)  # type: ignore[return-value]
        cols, vals = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            vals.append(json.dumps(v, ensure_ascii=False) if k in _JSON_FIELDS else
                        (int(v) if isinstance(v, bool) else v))
        cols.append("updated_at=?")
        vals.append(_now())
        with closing(self._conn()) as c, c:
            c.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE job_id=?", (*vals, job_id))
        return self.get(job_id)  # type: ignore[return-value]

    def bump_attempts(self, job_id: str) -> None:
        with closing(self._conn()) as c, c:
            c.execute("UPDATE jobs SET attempts=attempts+1 WHERE job_id=?", (job_id,))

    def find_active(self, video_key: str, account_key: str, mode: str) -> Job | None:
        """Newest non-superseded job for this video+account+mode (the idempotency key)."""
        with closing(self._conn()) as c:
            row = c.execute(
                "SELECT * FROM jobs WHERE video_key=? AND account_key=? AND mode=? AND superseded=0"
                " ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (video_key, account_key, mode),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def supersede(self, job_id: str) -> None:
        self.update(job_id, superseded=True)

    def list(self, limit: int = 50) -> list[Job]:
        with closing(self._conn()) as c:
            rows = c.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_job(r) for r in rows]
