"""V1.2.1: `--part` accepts a range / list (8-27, 1,3,5, 1-3,7) besides a single part number."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from bili2nb import batch, cli, planner
from bili2nb.bilibili import downloader as dl
from bili2nb.bilibili.parser import VideoRef
from bili2nb.errors import Bili2nbError
from bili2nb.importer import job_to_dict
from bili2nb.jobs.db import COMPLETED
from bili2nb.selection import canonical, parse_part_spec, selection_label
from tests.unit.test_multi_import import BV, URL, Env, make_infos

runner = CliRunner()


# ------------------------------------------------------------------ the syntax
@pytest.mark.parametrize("spec, want", [
    ("8", [8]),
    ("8-27", list(range(8, 28))),
    ("1,3,5", [1, 3, 5]),
    ("1-3,7", [1, 2, 3, 7]),
    ("5-5", [5]),
    ("3,1,3,2-3", [1, 2, 3]),  # de-duplicated and ascending
    ("7,1-3", [1, 2, 3, 7]),
    (" 1 , 3 ", [1, 3]),
    ("08", [8]),
    (8, [8]),  # a plain int (library callers, ?p=N) is still accepted
])
def test_valid_specs(spec, want):
    assert parse_part_spec(spec) == want


@pytest.mark.parametrize("spec", [
    "", " ", "0", "-1", "5-3", "a", "1-", "-3", "1,,2", ",1", "1,", "1-2-3", "1.5", "0-3", "1 2",
    "１", "1;2", "1-a", "1--3", 0, -2, "1-99999999", "99999",
])
def test_invalid_specs_give_a_clear_chinese_error(spec):
    with pytest.raises(Bili2nbError) as ei:
        parse_part_spec(spec)
    assert ei.value.code == "INVALID_URL" and ei.value.exit_code == 10
    assert "--part" in ei.value.message and any("一" <= c <= "鿿" for c in ei.value.message)


def test_error_names_the_offending_piece():
    with pytest.raises(Bili2nbError) as ei:
        parse_part_spec("1-3,5-2")
    assert "5-2" in ei.value.message


def test_canonical_is_compact_and_stable():
    assert canonical(list(range(8, 28))) == "8-27"
    assert canonical([1, 2, 3, 7]) == "1-3+7"
    assert canonical([1, 3, 5]) == "1+3+5"
    assert canonical([4]) == "4"
    assert canonical([1, 2]) == "1-2"


def test_label_is_all_for_the_full_set_and_p_prefixed_otherwise():
    assert selection_label([1, 2, 3], total=3) == "all"
    assert selection_label([2], total=3) == "P2"
    assert selection_label([8, 9, 10], total=132) == "P8-10"
    assert selection_label([1, 2, 3, 7], total=132) == "P1-3+7"


# ------------------------------------------------------------------ plan_parts
def _pages(n):
    return [{"cid": 1000 + i, "page": i, "part": f"第{i}集", "duration": 60 * i} for i in range(1, n + 1)]


def _meta(n=30, **kw):
    return dl.VideoMeta(code=kw.get("code", 0), message="0", bvid=BV, title="系列课程", uploader="作者",
                        pages=_pages(n) if n else [])


def test_plan_parts_builds_only_the_selected_parts_from_one_api_call(monkeypatch):
    calls = []
    monkeypatch.setattr(dl, "fetch_video", lambda vid: calls.append(vid) or _meta(30))
    monkeypatch.setattr(dl, "fetch_info", lambda r: pytest.fail("yt-dlp must not be needed"))
    infos = dl.plan_parts(VideoRef(BV, None), selection=[8, 9, 27])
    assert calls == [BV]
    assert [i.part for i in infos] == [8, 9, 27] and [i.cid for i in infos] == ["1008", "1009", "1027"]
    assert all(i.part_count == 30 for i in infos)  # the TOTAL, not the selection size
    assert infos[0].webpage_url == f"https://www.bilibili.com/video/{BV}?p=8"


def test_plan_parts_selection_beyond_the_total_is_the_existing_error(monkeypatch):
    monkeypatch.setattr(dl, "fetch_video", lambda vid: _meta(30))
    with pytest.raises(Bili2nbError) as ei:
        dl.plan_parts(VideoRef(BV, None), selection=[29, 31])
    assert ei.value.code == "VIDEO_UNAVAILABLE" and "30" in ei.value.message and "P31" in ei.value.message


def test_plan_parts_selection_when_api_is_down_asks_ytdlp_for_those_parts_only(monkeypatch):
    monkeypatch.setattr(dl, "fetch_video", lambda vid: _meta(0, code=None))
    monkeypatch.setattr(dl, "_enumerate_with_ytdlp", lambda r: pytest.fail("no need to count parts"))
    got = []

    def fake_info(ref):
        got.append(ref.part)
        return dl.VideoInfo(BV, None, ref.part, "t", "u", 10.0, ref.url, 30)

    monkeypatch.setattr(dl, "fetch_info", fake_info)
    infos = dl.plan_parts(VideoRef(BV, None), selection=[3, 5])
    assert got == [3, 5] and [i.part for i in infos] == [3, 5]


# ------------------------------------------------------------------ planner titles
def _files(nums, mb=1):
    return [planner.PartFiles(n, f"第{n}集", 60.0, f"/a{n}.m4a", mb * planner.MB, f"/p{n}.pdf", 1000)
            for n in nums]


def test_merged_title_for_non_consecutive_parts_does_not_claim_a_range():
    plan = planner.plan_uploads(_files([1, 3, 5]), limit=2, video_title="系列", total_parts=9)
    assert plan["strategy"] == "merged"
    assert {i["title"] for i in plan["items"]} == {"P01+P03+P05｜音频", "P01+P03+P05｜关键帧PDF"}
    assert plan["items"][0]["parts"] == [1, 3, 5]


def test_merged_title_for_mixed_selection_uses_runs():
    plan = planner.plan_uploads(_files([1, 2, 3, 4, 7]), limit=2, video_title="系列", total_parts=9)
    assert plan["items"][0]["title"] == "P01-P04+P07｜音频"


def test_merged_title_for_consecutive_parts_is_unchanged():
    plan = planner.plan_uploads(_files(range(8, 28)), limit=2, video_title="系列", total_parts=132)
    assert plan["items"][0]["title"] == "P08-P27｜音频"


def test_merged_title_stays_short_and_keys_stay_unique_for_scattered_parts():
    plan = planner.plan_uploads(_files(range(1, 60, 2)), limit=2, video_title="系列", total_parts=60)
    for i in plan["items"]:
        assert len(i["title"]) <= 100 and "30" in i["title"]  # says how many parts it holds
    assert len({i["key"] for i in plan["items"]}) == len(plan["items"])


# ------------------------------------------------------------------ importer
class SelEnv(Env):
    def __init__(self, tmp_path, n=10):
        super().__init__(tmp_path, n)
        self.selections: list = []

    def plan(self, ref, selection=None):
        self.plan_calls += 1
        self.selections.append(selection)
        if selection is not None:
            return [i for i in self.infos if i.part in selection]
        return [i for i in self.infos if ref.part in (None, i.part)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BILI2NB_HOME", str(tmp_path / "home"))
    return SelEnv(tmp_path)


def test_subset_import_one_notebook_two_sources_per_selected_part(env):
    res = env.run(part="3-5")
    assert res["status"] == COMPLETED and env.nlm.create_calls == 1
    assert env.converted == [3, 4, 5] and env.plan_calls == 1 and env.selections == [[3, 4, 5]]
    assert res["source_count"] == 6 and len(env.nlm.notebooks["nb-1"]) == 6
    assert env.nlm.titles == ["P03｜第3集｜音频", "P03｜第3集｜关键帧PDF", "P04｜第4集｜音频",
                              "P04｜第4集｜关键帧PDF", "P05｜第5集｜音频", "P05｜第5集｜关键帧PDF"]
    assert res["selection"] == "P3-5" and res["part_count"] == 10
    assert [p["part"] for p in res["parts"]] == [3, 4, 5]
    assert res["video_key"] == f"{BV}_P3-5"
    assert all(res["source_ready"].values())


def test_subset_list_syntax_and_key(env):
    res = env.run(part="1-2,7")
    assert env.converted == [1, 2, 7] and res["video_key"] == f"{BV}_P1-2+7" and res["selection"] == "P1-2+7"


def test_same_selection_written_differently_is_the_same_task(env):
    first = env.run(part="7,1,2")
    again = env.run(part="1-2,7")
    assert again["reused"] is True and again["job_id"] == first["job_id"]
    assert env.nlm.create_calls == 1 and len(env.nlm.titles) == 6 and env.converted == [1, 2, 7]


def test_repeating_a_subset_import_reuses_the_notebook(env):
    first = env.run(part="3-5")
    second = env.run(part="3-5")
    assert second["reused"] is True and second["job_id"] == first["job_id"]
    assert env.nlm.create_calls == 1 and len(env.nlm.titles) == 6 and env.converted == [3, 4, 5]


def test_force_new_makes_exactly_one_more_notebook_for_the_subset(env):
    first = env.run(part="3-5")
    again = env.run(part="3-5", force_new=True)
    assert again["job_id"] != first["job_id"] and env.nlm.create_calls == 2
    assert again["source_count"] == 6 and env.store.get(first["job_id"]).superseded is True


def test_different_selections_are_different_tasks(env):
    a = env.run(part="3-5")
    b = env.run(part="3-6")
    assert a["job_id"] != b["job_id"] and env.nlm.create_calls == 2
    assert a["video_key"] != b["video_key"]
    assert env.store.get(a["job_id"]).superseded is False  # B did not retire A


def test_subset_never_hits_the_all_parts_job_nor_a_single_part_job(env):
    whole = env.run()
    single = env.run(part=3)
    sub = env.run(part="3-5")
    keys = {whole["video_key"], single["video_key"], sub["video_key"]}
    assert len(keys) == 3 and whole["video_key"] == f"{BV}_all" and single["video_key"] == f"{BV}_1003"
    assert sub["job_id"] not in (whole["job_id"], single["job_id"])
    assert env.nlm.create_calls == 3


def test_single_number_in_any_spelling_stays_on_the_v11_single_part_path(env):
    for spec in (3, "3", "3-3", "3,3"):
        res = env.run(part=spec)
        assert res["video_key"] == f"{BV}_1003" and res["source_count"] == 2
    assert env.nlm.create_calls == 1 and env.converted == [3]  # one task, reused thrice
    assert "selection" not in res and "parts" not in res  # the single-part shape is untouched


def test_selecting_every_part_is_the_all_parts_task(env):
    whole = env.run()
    full = env.run(part="1-10")
    assert full["video_key"] == f"{BV}_all" and full["job_id"] == whole["job_id"] and full["reused"] is True
    assert full["selection"] == "all" and env.nlm.create_calls == 1


def test_parts_changed_still_guards_a_reused_subset_job(env):
    env.run(part="3-5")
    env.infos = [i.__class__(**{**i.__dict__, "cid": str(int(i.cid) + 500)}) for i in env.infos]
    with pytest.raises(Bili2nbError) as ei:
        env.run(part="3-5")
    assert ei.value.code == "PARTS_CHANGED" and ei.value.exit_code == 28 and env.nlm.create_calls == 1


def test_url_part_beats_the_selection(env):
    res = env.importer().run(f"{URL}?p=2", part="3-5", output=str(env.tmp / "out"))
    assert env.converted == [2] and res["video_key"] == f"{BV}_1002"
    assert any("忽略 --part" in m for m in env.logs)


def test_invalid_spec_fails_before_login_check_or_any_network(env):
    for bad in ("5-3", "0", "a", "1,,2"):
        with pytest.raises(Bili2nbError) as ei:
            env.run(part=bad)
        assert ei.value.code == "INVALID_URL"
    assert env.nlm.auth_calls == 0 and env.plan_calls == 0 and env.nlm.create_calls == 0


def test_subset_job_with_a_resumable_failure_keeps_its_selection(env):
    from bili2nb.jobs.db import FAILED
    env.convert_fail[4] = [Bili2nbError("MEDIA_ERROR", "boom")]
    with pytest.raises(Bili2nbError) as ei:
        env.run(part="3-5")
    assert env.nlm.create_calls == 0
    job = env.store.get(ei.value.extra["job_id"])
    assert job.status == FAILED and job_to_dict(job)["selection"] == "P3-5"
    res = env.importer().retry(job.job_id)
    assert res["status"] == COMPLETED and res["selection"] == "P3-5" and env.nlm.create_calls == 1


def test_subset_batch_manifest_records_the_selection(env):
    env.run(part="3-5")
    manifest = json.loads((env.tmp / "out" / BV / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["selection"] == "P3-5" and [p["part"] for p in manifest["parts"]] == [3, 4, 5]


def test_subset_over_quota_merges_with_an_honest_title(env):
    env.nlm.limit = 3  # 3 selected parts * 2 = 6 sources > 3
    env.real_media = True  # merging needs real, probe-able audio and PDFs
    res = env.run(part="1,3,5")
    assert res["upload_plan"]["strategy"] == "merged"
    # R=3: the PDFs fold into one source, audio only loses one source -> [P01+P03] merged, P05 per part
    assert {s["title"] for s in res["sources"]} == {"P01+P03｜音频", "P05｜第5集｜音频", "P01+P03+P05｜关键帧PDF"}
    assert env.nlm.create_calls == 1


# ------------------------------------------------------------------ convert
def _convert(env, **kw):
    kw.setdefault("part", None)
    return batch.convert_parts(URL, output_root=env.tmp / "out", frames_mode="auto", audio_only=False,
                               log=lambda m: None, plan_fn=env.plan, convert_fn=env.convert,
                               sleep=env.sleeps.append, backoff_base=1.0, **kw)


def test_convert_subset_outputs_only_the_selected_parts(env):
    res = _convert(env, part="2-4")
    assert env.converted == [2, 3, 4] and res["selection"] == "P2-4" and res["part_count"] == 10
    assert [p["part"] for p in res["parts"]] == [2, 3, 4]
    manifest = json.loads((env.tmp / "out" / BV / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["selection"] == "P2-4" and manifest["part_count_processed"] == 3


def test_convert_single_number_keeps_the_legacy_shape_and_label(env):
    res = _convert(env, part="2")
    assert env.converted == [2] and res["selection"] == "P2" and res["audio_path"].endswith("audio.m4a")


def test_convert_selecting_everything_is_all(env):
    res = _convert(env, part="1-10")
    assert res["selection"] == "all" and len(res["parts"]) == 10


def test_convert_url_part_wins_with_a_warning(env):
    res = batch.convert_parts(URL + "?p=3", output_root=env.tmp / "out", frames_mode="auto",
                              audio_only=False, part="1-2", log=lambda m: None, plan_fn=env.plan,
                              convert_fn=env.convert, sleep=env.sleeps.append, backoff_base=1.0)
    assert env.converted == [3] and any("忽略 --part" in w for w in res["warnings"])


def test_convert_invalid_spec_is_rejected_before_planning(env):
    with pytest.raises(Bili2nbError) as ei:
        _convert(env, part="9-2")
    assert ei.value.code == "INVALID_URL" and env.plan_calls == 0


# ------------------------------------------------------------------ CLI
@pytest.fixture
def cli_env(env, monkeypatch):
    monkeypatch.setattr(cli, "_make_importer", lambda: env.importer())
    monkeypatch.setattr(batch.dl, "plan_parts", env.plan)
    monkeypatch.setattr(batch.pipeline, "convert", env.convert)
    return env


def test_cli_import_range_json_is_a_single_pure_object(cli_env):
    r = runner.invoke(cli.app, [URL, "--part", "3-5", "--json", "--output", str(cli_env.tmp / "o")])
    assert r.exit_code == 0, r.output
    obj = json.loads(r.stdout)  # the WHOLE stdout is one JSON object
    assert obj["selection"] == "P3-5" and obj["part_count"] == 10
    assert [p["part"] for p in obj["parts"]] == [3, 4, 5] and obj["source_count"] == 6
    assert "阶段" not in r.stdout


def test_cli_convert_range_json_is_pure(cli_env):
    r = runner.invoke(cli.app, ["convert", URL, "--part", "2-3", "--json", "--output", str(cli_env.tmp / "o")])
    assert r.exit_code == 0, r.output
    obj = json.loads(r.stdout)
    assert obj["selection"] == "P2-3" and obj["part_count"] == 10 and len(obj["parts"]) == 2


@pytest.mark.parametrize("cmd", [["import"], ["convert"], []])
def test_cli_bad_spec_is_invalid_url_exit_10_and_json_stays_pure(cli_env, cmd):
    r = runner.invoke(cli.app, [*cmd, URL, "--part", "5-3", "--json"])
    assert r.exit_code == 10
    obj = json.loads(r.stdout)
    assert obj["status"] == "failed" and obj["error_code"] == "INVALID_URL" and "5-3" in obj["message"]
    assert cli_env.nlm.auth_calls == 0 and cli_env.plan_calls == 0


def test_cli_single_part_still_works_as_before(cli_env):
    r = runner.invoke(cli.app, [URL, "--part", "2", "--json", "--output", str(cli_env.tmp / "o")])
    assert r.exit_code == 0, r.output
    obj = json.loads(r.stdout)
    assert obj["video_key"] == f"{BV}_1002" and obj["source_count"] == 2


def test_cli_help_documents_the_syntax():
    import re
    for cmd in ("import", "convert"):
        out = re.sub(r"\x1b\[[0-9;]*m", "", runner.invoke(cli.app, [cmd, "--help"]).output)
        flat = " ".join(out.split())
        assert "8-27" in flat and "1,3,5" in flat


def test_make_infos_fixture_is_what_the_tests_assume():
    assert [i.cid for i in make_infos(3)] == ["1001", "1002", "1003"]
