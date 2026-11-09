"""V1.2 upload planner: pure logic, no files, no network."""

from __future__ import annotations

import pytest

from bili2nb import planner
from bili2nb.errors import EXIT_CODES, Bili2nbError
from bili2nb.planner import PartFiles

MB = 1024 * 1024


def _parts(n, *, audio_mb=10, pdf_mb=2, visual=True):
    return [PartFiles(i, f"第{i}集", 600.0, f"/o/P{i}/audio.m4a", audio_mb * MB,
                      f"/o/P{i}/k.pdf" if visual else None, pdf_mb * MB if visual else 0)
            for i in range(1, n + 1)]


def _plan(parts, limit=50, total=None, **kw):
    return planner.plan_uploads(parts, limit=limit, video_title="系列", total_parts=total or len(parts), **kw)


def test_exit_code_for_infeasible_plans():
    assert EXIT_CODES["UPLOAD_PLAN_INFEASIBLE"] == 27


def test_each_part_uploads_audio_and_pdf_when_the_quota_allows():
    plan = _plan(_parts(3))
    assert plan["strategy"] == "per_part" and len(plan["items"]) == 6
    assert [i["key"] for i in plan["items"]] == ["P01:audio", "P01:pdf", "P02:audio", "P02:pdf", "P03:audio", "P03:pdf"]
    assert plan["items"][0]["title"] == "P01｜第1集｜音频" and plan["items"][1]["title"] == "P01｜第1集｜关键帧PDF"
    assert all(i["merge"] is False and i["source_id"] is None and i["ready"] is False for i in plan["items"])


def test_part_without_visuals_contributes_audio_only():
    parts = _parts(3)
    parts[1] = PartFiles(2, "第2集", 600.0, "/o/P2/audio.m4a", 10 * MB)
    plan = _plan(parts)
    assert [i["key"] for i in plan["items"]] == ["P01:audio", "P01:pdf", "P02:audio", "P03:audio", "P03:pdf"]


def test_audio_only_video_has_one_source_per_part():
    assert len(_plan(_parts(3, visual=False))["items"]) == 3


def test_single_part_video_keeps_the_v11_source_names():
    plan = _plan(_parts(1))
    assert [i["title"] for i in plan["items"]] == ["音频｜系列", "关键帧PDF｜系列"]


def test_one_part_of_a_multi_part_video_uses_part_names():
    plan = _plan(_parts(1), total=5)
    assert plan["items"][0]["title"] == "P01｜第1集｜音频"


def test_over_quota_merges_into_the_fewest_consecutive_groups():
    plan = _plan(_parts(30, audio_mb=5), limit=50)  # 60 sources wanted > 50; 150 MB audio fits one file
    assert plan["strategy"] == "merged" and len(plan["items"]) == 2
    a, p = plan["items"]
    assert (a["kind"], a["parts"], a["merge"]) == ("audio", list(range(1, 31)), True)
    assert (p["kind"], p["title"]) == ("pdf", "P01-P30｜关键帧PDF") and a["title"] == "P01-P30｜音频"
    assert a["bytes"] == 30 * 5 * MB and a["path"] is None  # file is built later


def test_over_quota_with_big_audio_makes_a_second_group_not_a_second_notebook():
    plan = _plan(_parts(30, audio_mb=10), limit=50)  # 300 MB > 190 MB cap
    audio = [i for i in plan["items"] if i["kind"] == "audio"]
    assert len(audio) == 2 and audio[0]["parts"][-1] + 1 == audio[1]["parts"][0]
    assert all(i["bytes"] <= planner.GROUP_MAX_BYTES for i in plan["items"])


def test_merged_groups_split_by_size_on_consecutive_parts():
    parts = _parts(10, audio_mb=60)  # 600 MB total, 190 MB cap -> 3 parts per group
    plan = _plan(parts, limit=5, group_max=190 * MB)
    audio = [i for i in plan["items"] if i["kind"] == "audio"]
    assert [i["parts"] for i in audio] == [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10]]
    assert audio[-1]["merge"] is False and audio[-1]["path"] == "/o/P10/audio.m4a"  # singleton: no merge
    assert audio[-1]["title"] == "P10｜第10集｜音频"
    assert all(sum(60 * MB for _ in i["parts"]) <= 190 * MB for i in audio)
    # pdf side is independent: all ten fit in one group
    pdf = [i for i in plan["items"] if i["kind"] == "pdf"]
    assert len(pdf) == 1 and pdf[0]["parts"] == list(range(1, 11))
    assert len(plan["items"]) <= 5


def test_still_too_many_sources_fails_before_anything_is_created():
    with pytest.raises(Bili2nbError) as ei:
        _plan(_parts(10, audio_mb=100), limit=4, group_max=190 * MB)  # 10 audio needs 10 groups
    e = ei.value
    assert e.code == "UPLOAD_PLAN_INFEASIBLE"
    assert e.extra["needed_sources"] > 4 and e.extra["source_limit"] == 4 and e.extra["part_count"] == 10
    assert "--part" in e.message and "4" in e.message


def test_limit_origin_is_recorded():
    assert _plan(_parts(2), limit=50, limit_origin="default")["limit_origin"] == "default"
    assert _plan(_parts(2), limit=300)["source_limit"] == 300


def test_titles_stay_within_100_chars_and_unique():
    parts = [PartFiles(i, "很长的标题" * 40, 60.0, "/a", MB, "/p", MB) for i in (1, 2)]
    titles = [i["title"] for i in _plan(parts)["items"]]
    assert all(len(t) <= 100 for t in titles) and len(set(titles)) == len(titles)
    assert titles[0].startswith("P01｜") and titles[0].endswith("音频")


def test_early_estimate_rejects_hopeless_selections_without_any_download():
    hours = [3600.0] * 400  # 400 h of audio, even at 64 kbps >> 50 x 190 MB
    with pytest.raises(Bili2nbError) as ei:
        planner.estimate_feasibility(hours, limit=50)
    assert ei.value.code == "UPLOAD_PLAN_INFEASIBLE"
    planner.estimate_feasibility([3600.0] * 5, limit=50)  # fine
