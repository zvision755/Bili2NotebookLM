"""V1.2.2: optional `--merge-sources` (merge consecutive parts into the fewest sources even when the
quota is not exceeded). Default behaviour is unchanged; no network."""

from __future__ import annotations

import json
import re

import pytest
from typer.testing import CliRunner

from bili2nb import batch, cli, planner
from bili2nb.jobs.db import COMPLETED
from bili2nb.planner import PartFiles
from tests.unit.test_multi_import import URL, Env

runner = CliRunner()
MB = 1024 * 1024


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BILI2NB_HOME", str(tmp_path / "home"))
    e = Env(tmp_path)
    e.real_media = True  # merging needs real audio/PDF files
    return e


def _parts(n, *, audio_mb=6.0, pdf_mb=2.4):
    return [PartFiles(i, f"第{i}集", 600.0, f"/o/P{i}/a.m4a", int(audio_mb * MB),
                      f"/o/P{i}/k.pdf", int(pdf_mb * MB)) for i in range(8, 8 + n)]


def _plan(parts, **kw):
    return planner.plan_uploads(parts, limit=300, video_title="系列", total_parts=132, **kw)


# ------------------------------------------------------------------ planner
def test_default_plan_is_per_part_and_records_merge_not_requested():
    plan = _plan(_parts(20))
    assert plan["strategy"] == "per_part" and len(plan["items"]) == 40
    assert plan["merge_requested"] is False


def test_merge_requested_merges_even_though_the_quota_is_plentiful():
    plan = _plan(_parts(20), merge=True)
    assert plan["strategy"] == "merged" and plan["merge_requested"] is True
    assert [(i["key"], i["title"], i["merge"]) for i in plan["items"]] == [
        ("P08-P27:audio", "P08-P27｜音频", True), ("P08-P27:pdf", "P08-P27｜关键帧PDF", True)]
    assert all(i["bytes"] <= planner.GROUP_MAX_BYTES for i in plan["items"])


def test_merged_groups_never_exceed_the_per_file_cap():
    plan = _plan(_parts(60, audio_mb=10), merge=True)  # 600 MB of audio
    audio = [i for i in plan["items"] if i["kind"] == "audio"]
    assert len(audio) == 4 and all(i["bytes"] <= planner.GROUP_MAX_BYTES for i in audio)
    assert sorted(m for i in audio for m in i["parts"]) == list(range(8, 68))  # nothing lost, in order


def test_merge_does_not_change_default_when_not_requested_over_quota():
    plan = planner.plan_uploads(_parts(20), limit=4, video_title="系列", total_parts=132)
    assert plan["strategy"] == "merged" and plan["merge_requested"] is False


def test_merge_requested_with_a_single_member_group_keeps_that_item_unmerged():
    plan = _plan(_parts(1), merge=True)
    assert [i["merge"] for i in plan["items"]] == [False, False]


# ------------------------------------------------------------------ importer
def test_merge_sources_gives_two_sources_in_one_notebook(env):
    res = env.run(merge_sources=True)
    assert res["status"] == COMPLETED and env.nlm.create_calls == 1
    assert res["source_count"] == 2 and env.nlm.titles == ["P01-P03｜音频", "P01-P03｜关键帧PDF"]
    assert res["upload_plan"]["strategy"] == "merged" and res["upload_plan"]["merge_requested"] is True
    assert all(s["merged"] for s in res["sources"]) and all(res["source_ready"].values())
    assert all(s["bytes"] <= planner.GROUP_MAX_BYTES for s in res["sources"])


def test_merged_pdf_index_tells_which_part_starts_where_in_the_merged_audio(env):
    env.run(merge_sources=True)
    from pypdf import PdfReader
    idx = PdfReader(str(env.nlm.paths["P01-P03｜关键帧PDF"])).pages[0].extract_text() or ""
    flat = re.sub(r"\s+", "", idx)
    for n in (1, 2, 3):
        assert f"P0{n}" in flat
    assert "P01-P03｜音频" in flat.replace("\n", "") or "音频" in flat
    assert "0:00:00" in flat or "00:00" in flat  # P01 starts at the beginning of the merged audio


def test_default_run_is_unchanged_per_part(env):
    res = env.run()
    assert res["upload_plan"]["strategy"] == "per_part" and res["upload_plan"]["merge_requested"] is False
    assert res["source_count"] == 6


def test_single_part_ignores_the_flag_with_a_one_line_hint(env):
    res = env.run(part=2, merge_sources=True)
    assert res["status"] == COMPLETED and res["source_count"] == 2
    assert env.nlm.titles == ["音频｜系列", "关键帧PDF｜系列"] or len(env.nlm.titles) == 2
    hints = [m for m in env.logs if "--merge-sources" in m]
    assert len(hints) == 1 and "\n" not in hints[0]


def test_one_part_subset_range_also_ignores_the_flag(env):
    res = env.run(part="2-2", merge_sources=True)
    assert res["source_count"] == 2 and any("--merge-sources" in m for m in env.logs)


def test_the_flag_does_not_change_the_idempotency_key(env):
    first = env.run()
    again = env.run(merge_sources=True)
    assert again["reused"] is True and again["job_id"] == first["job_id"]
    assert again["source_count"] == 6 and env.nlm.create_calls == 1
    assert any("--force-new" in m for m in env.logs)  # tells the user how to really redo it


def test_merged_job_is_reused_by_a_plain_rerun_too(env):
    first = env.run(merge_sources=True)
    again = env.run()
    assert again["reused"] is True and again["job_id"] == first["job_id"] and again["source_count"] == 2
    assert env.nlm.create_calls == 1


def test_force_new_with_the_flag_makes_one_more_merged_notebook(env):
    env.run()
    res = env.run(merge_sources=True, force_new=True)
    assert env.nlm.create_calls == 2 and res["source_count"] == 2
    assert res["upload_plan"]["merge_requested"] is True


def test_plan_stays_frozen_and_merged_across_retry(env):
    from bili2nb.errors import Bili2nbError
    env.nlm.title_failures["P01-P03｜关键帧PDF"] = [Bili2nbError("UPLOAD_FAILED", "x")]
    with pytest.raises(Bili2nbError) as ei:
        env.run(merge_sources=True)
    job_id = ei.value.extra["job_id"]
    frozen = env.store.get(job_id).plan
    assert frozen["merge_requested"] is True
    env.nlm.limit = 3  # quota changes between runs: the frozen plan must not move
    res = env.importer().retry(job_id)
    assert res["status"] == COMPLETED and res["source_count"] == 2
    assert env.store.get(job_id).plan["items"][0]["title"] == frozen["items"][0]["title"]
    assert env.store.get(job_id).plan["merge_requested"] is True


def test_old_jobs_without_the_field_still_render(env):
    res = env.run()
    job = env.store.get(res["job_id"])
    plan = {k: v for k, v in job.plan.items() if k != "merge_requested"}
    env.store.update(job.job_id, plan=plan)
    from bili2nb.importer import job_to_dict
    assert job_to_dict(env.store.get(job.job_id))["upload_plan"]["merge_requested"] is False


# ------------------------------------------------------------------ CLI
@pytest.fixture
def cli_env(env, monkeypatch):
    monkeypatch.setattr(cli, "_make_importer", lambda: env.importer())
    monkeypatch.setattr(batch.dl, "plan_parts", env.plan)
    monkeypatch.setattr(batch.pipeline, "convert", env.convert)
    return env


def test_cli_import_merge_sources_json_is_pure_and_shows_the_field(cli_env):
    r = runner.invoke(cli.app, [URL, "--merge-sources", "--json", "--output", str(cli_env.tmp / "o")])
    assert r.exit_code == 0, r.output
    obj = json.loads(r.stdout)
    assert obj["upload_plan"]["merge_requested"] is True and obj["source_count"] == 2


def test_cli_explicit_import_command_accepts_the_flag(cli_env):
    r = runner.invoke(cli.app, ["import", URL, "--merge-sources", "--json", "--output", str(cli_env.tmp / "o")])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["source_count"] == 2


def test_cli_without_the_flag_is_unchanged(cli_env):
    r = runner.invoke(cli.app, [URL, "--json", "--output", str(cli_env.tmp / "o")])
    obj = json.loads(r.stdout)
    assert obj["upload_plan"]["merge_requested"] is False and obj["source_count"] == 6


def test_convert_has_no_such_flag():
    r = runner.invoke(cli.app, ["convert", URL, "--merge-sources"])
    assert r.exit_code == 2


def test_import_help_documents_the_flag_and_idempotency():
    out = re.sub(r"\x1b\[[0-9;]*m", "", runner.invoke(cli.app, ["import", "--help"]).output)
    flat = " ".join(out.split())
    assert "--merge-sources" in flat and "--force-new" in flat
