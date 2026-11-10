"""V1.2: the jobs table gains `parts` / `plan`; V1.0 and V1.1 databases must keep working untouched."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest

from bili2nb.jobs.db import JobStore

V10_SCHEMA = """
CREATE TABLE jobs (
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
  attempts INTEGER NOT NULL DEFAULT 0, superseded INTEGER NOT NULL DEFAULT 0
);
"""


def _legacy_db(path, *, with_orphans: bool):
    schema = V10_SCHEMA
    if with_orphans:
        schema = schema.replace("superseded INTEGER NOT NULL DEFAULT 0\n", "superseded INTEGER NOT NULL DEFAULT 0,\n  orphans TEXT NOT NULL DEFAULT '[]'\n")
    with closing(sqlite3.connect(path)) as c, c:
        c.executescript(schema)
        c.execute(
            "INSERT INTO jobs(job_id, video_key, account_key, mode, url, options, status, stage,"
            " created_at, updated_at, video_id, video_title, duration_sec, audio_path, pdf_path,"
            " notebook_id, sources, ready, visual) VALUES"
            " ('20260101-000000-abcd','BV1GJ411x7h7_999','acct','auto','https://x','{}','completed','done',"
            "  '2026-01-01T00:00:00+00:00','2026-01-01T00:10:00+00:00','BV1GJ411x7h7','旧任务',120.0,"
            "  '/o/a.m4a','/o/k.pdf','nb-1',?,?,?)",
            (json.dumps({"audio": "s1", "pdf": "s2"}), json.dumps({"audio": True, "pdf": True}),
             json.dumps({"status": "ok"})),
        )


@pytest.mark.parametrize("with_orphans", [False, True])
def test_legacy_databases_open_and_keep_their_data(tmp_path, with_orphans):
    p = tmp_path / "jobs.sqlite"
    _legacy_db(p, with_orphans=with_orphans)
    store = JobStore(p)  # migrates in place
    job = store.get("20260101-000000-abcd")
    assert job.notebook_id == "nb-1" and job.sources == {"audio": "s1", "pdf": "s2"}
    assert job.status == "completed" and job.orphans == []
    assert job.parts == [] and job.plan == {}  # legacy single-part job: no V1.2 data yet
    again = store.find_active("BV1GJ411x7h7_999", "acct", "auto")  # idempotency lookup still hits it
    assert again is not None and again.job_id == job.job_id


def test_migration_is_idempotent_and_never_drops_rows(tmp_path):
    p = tmp_path / "jobs.sqlite"
    _legacy_db(p, with_orphans=False)
    for _ in range(3):
        JobStore(p)
    assert len(JobStore(p).list()) == 1


def test_parts_and_plan_round_trip(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    j = store.create(video_key="BV1_all", account_key="a", mode="auto", url="u", options={})
    parts = [{"part": 1, "cid": "11", "title": "第一集", "convert": "done"},
             {"part": 2, "cid": "22", "title": "第二集", "convert": "pending"}]
    plan = {"strategy": "per_part", "source_limit": 50,
            "items": [{"key": "P01:audio", "source_id": None}]}
    store.update(j.job_id, parts=parts, plan=plan)
    got = store.get(j.job_id)
    assert got.parts == parts and got.plan == plan


def test_new_all_parts_job_does_not_collide_with_a_legacy_single_part_job(tmp_path):
    p = tmp_path / "jobs.sqlite"
    _legacy_db(p, with_orphans=True)
    store = JobStore(p)
    assert store.find_active("BV1GJ411x7h7_all", "acct", "auto") is None  # not the legacy P1 job
