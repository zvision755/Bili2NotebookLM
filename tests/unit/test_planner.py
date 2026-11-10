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


def _kind(plan, kind):
    return [i for i in plan["items"] if i["kind"] == kind]


def _sizes(plan, kind):
    return [len(i["parts"]) for i in _kind(plan, kind)]


def _assert_partition(plan, parts, kind):
    """Every part that has this kind appears in exactly ONE item, in order."""
    flat = [m for i in _kind(plan, kind) for m in i["parts"]]
    assert flat == [p.part for p in parts if (p.audio_path if kind == "audio" else p.pdf_path)]


def test_30_parts_with_quota_50_gives_25_aligned_groups_of_audio_and_pdf():
    parts = _parts(30, audio_mb=5)
    plan = _plan(parts, limit=50)  # 60 wanted -> g=25: 5 groups of two + 20 single parts
    assert plan["strategy"] == "merged" and plan["merge_mode"] == "minimal" and len(plan["items"]) == 50
    audio, pdf = _kind(plan, "audio"), _kind(plan, "pdf")
    assert _sizes(plan, "audio") == [2] * 5 + [1] * 20 and _sizes(plan, "pdf") == _sizes(plan, "audio")
    assert [a["parts"] for a in audio] == [p["parts"] for p in pdf]  # SAME boundaries for audio and PDF
    assert (audio[0]["merge"], audio[0]["title"], audio[0]["path"]) == (True, "P01+P02｜音频", None)
    assert pdf[0]["title"] == "P01+P02｜关键帧PDF" and audio[0]["bytes"] == 2 * 5 * MB and pdf[0]["bytes"] == 2 * 2 * MB
    assert (audio[5]["merge"], audio[5]["key"], audio[5]["title"]) == (False, "P11:audio", "P11｜第11集｜音频")
    assert (pdf[5]["merge"], pdf[5]["path"]) == (False, "/o/P11/k.pdf")  # single-member group stays per part
    assert plan["merge_group_sizes"] == {"audio": _sizes(plan, "audio"), "pdf": _sizes(plan, "pdf")}
    assert plan["merge_group_count"] == 25
    _assert_partition(plan, parts, "audio")
    _assert_partition(plan, parts, "pdf")


def test_items_are_ordered_group_by_group_audio_then_pdf():
    plan = _plan(_parts(30), limit=50)
    assert [i["key"] for i in plan["items"]][:4] == ["P01+P02:audio", "P01+P02:pdf", "P03+P04:audio", "P03+P04:pdf"]


def test_132_parts_with_quota_50_makes_25_groups_of_5_or_6():
    parts = _parts(132)
    plan = _plan(parts, limit=50)
    assert len(plan["items"]) == 50 and _sizes(plan, "audio") == [6] * 7 + [5] * 18
    assert [a["parts"] for a in _kind(plan, "audio")] == [p["parts"] for p in _kind(plan, "pdf")]
    assert _kind(plan, "audio")[0]["title"] == "P01-P06｜音频" and _kind(plan, "pdf")[-1]["title"] == "P128-P132｜关键帧PDF"
    _assert_partition(plan, parts, "audio")


def test_quota_of_40_gives_20_groups():
    plan = _plan(_parts(30), limit=40)
    assert len(plan["items"]) == 40 and _sizes(plan, "audio") == [2] * 10 + [1] * 10


def test_quota_of_31_cannot_use_16_groups_so_it_uses_15():
    plan = _plan(_parts(30), limit=31)  # g=16 would be 32 sources
    assert len(plan["items"]) == 30 and _sizes(plan, "audio") == [2] * 15


def test_60_parts_with_quota_50_gives_groups_of_2_or_3():
    plan = _plan(_parts(60), limit=50)
    assert len(plan["items"]) == 50 and _sizes(plan, "audio") == [3] * 10 + [2] * 15


def test_quota_of_2_is_one_group_when_sizes_allow():
    plan = _plan(_parts(30, audio_mb=5), limit=2)
    assert [(i["kind"], len(i["parts"])) for i in plan["items"]] == [("audio", 30), ("pdf", 30)]
    assert plan["merge_group_count"] == 1


def test_quota_of_2_is_infeasible_when_the_audio_cannot_fit_one_file():
    with pytest.raises(Bili2nbError) as ei:
        _plan(_parts(30, audio_mb=10), limit=2)  # 300 MB audio: two groups -> 4 sources
    assert ei.value.code == "UPLOAD_PLAN_INFEASIBLE" and ei.value.extra["needed_sources"] == 4


def test_audio_only_over_quota_uses_up_to_limit_groups():
    parts = _parts(132, visual=False)
    plan = _plan(parts, limit=50)
    assert len(plan["items"]) == 50 and all(i["kind"] == "audio" for i in plan["items"])
    assert plan["merge_group_sizes"]["pdf"] == [] and sum(plan["merge_group_sizes"]["audio"]) == 132
    assert plan["merge_group_count"] == 50


def test_parts_without_pdf_are_counted_correctly_and_never_enter_a_pdf_group():
    parts = _parts(10)
    for i in range(5):  # P1..P5 have no PDF
        parts[i] = PartFiles(i + 1, f"第{i + 1}集", 600.0, f"/o/P{i + 1}/audio.m4a", 10 * MB)
    plan = _plan(parts, limit=12)  # T=15; g=7 -> [1,2][3,4][5,6] 7 8 9 10 = 7 audio + 5 pdf
    assert plan["merge_group_count"] == 7 and len(plan["items"]) == 12
    assert [a["parts"] for a in _kind(plan, "audio")] == [[1, 2], [3, 4], [5, 6], [7], [8], [9], [10]]
    pdf = _kind(plan, "pdf")
    assert [g["parts"] for g in pdf] == [[6], [7], [8], [9], [10]]
    assert pdf[0]["merge"] is False and pdf[0]["title"] == "P06｜第6集｜关键帧PDF"
    _assert_partition(plan, parts, "pdf")


def test_a_group_with_no_pdf_at_all_has_only_an_audio_source():
    parts = _parts(4)
    parts[0] = PartFiles(1, "第1集", 600.0, "/o/P1/a", 10 * MB)
    parts[1] = PartFiles(2, "第2集", 600.0, "/o/P2/a", 10 * MB)
    plan = _plan(parts, limit=5)  # T=6; g=3 -> [1,2][3][4]: 3 audio + 2 pdf
    assert [i["key"] for i in plan["items"]] == ["P01+P02:audio", "P03:audio", "P03:pdf", "P04:audio", "P04:pdf"]


def test_size_cap_halves_a_group_and_keeps_audio_and_pdf_aligned():
    sizes = [100, 100, 10, 10, 10, 10]
    parts = [PartFiles(i + 1, f"第{i + 1}集", 60.0, f"/a{i}", s * MB, f"/p{i}", MB) for i, s in enumerate(sizes)]
    plan = _plan(parts, limit=8, group_max=150 * MB)  # T=12; g=3 -> [1,2] is 200 MB -> [1][2] -> 4 groups = 8
    assert [a["parts"] for a in _kind(plan, "audio")] == [[1], [2], [3, 4], [5, 6]]
    assert [a["parts"] for a in _kind(plan, "pdf")] == [[1], [2], [3, 4], [5, 6]]
    assert all(i["bytes"] <= 150 * MB for i in plan["items"]) and len(plan["items"]) == 8


def test_a_pdf_over_the_cap_splits_the_whole_group_too():
    parts = [PartFiles(i, f"第{i}集", 60.0, f"/a{i}", MB, f"/p{i}", 100 * MB) for i in (1, 2, 3, 4)]
    with pytest.raises(Bili2nbError) as ei:  # no two PDFs fit one file (200 MB > 150), so every group is one part
        _plan(parts, limit=7, group_max=150 * MB)  # T=8 > 7 and nothing can merge
    assert ei.value.code == "UPLOAD_PLAN_INFEASIBLE" and ei.value.extra["needed_sources"] == 8


def test_every_quota_uses_the_largest_possible_number_of_groups():
    for limit in range(2, 60):
        plan = _plan(_parts(30, audio_mb=5), limit=limit)
        assert len(plan["items"]) == 2 * (limit // 2)  # as many aligned groups as the quota allows
        assert plan["merge_group_count"] == limit // 2


def test_balanced_deterministic_and_complete_for_many_shapes():
    for n in (3, 7, 30, 61, 132):
        for limit in (2, 5, 17, 40, 50):
            parts = _parts(n, audio_mb=1, pdf_mb=1)
            if 2 * n <= limit:
                continue
            a, b = _plan(parts, limit=limit), _plan(parts, limit=limit)
            assert a == b and len(a["items"]) <= limit
            sizes = _sizes(a, "audio")
            assert max(sizes) - min(sizes) <= 1 and sum(sizes) == n
            _assert_partition(a, parts, "audio")
            _assert_partition(a, parts, "pdf")


def test_quota_that_fits_per_part_is_unchanged_and_has_no_merge_fields():
    plan = _plan(_parts(25), limit=50)
    assert plan["strategy"] == "per_part" and "merge_mode" not in plan and len(plan["items"]) == 50


def test_merge_requested_still_means_the_fewest_sources():
    plan = _plan(_parts(30, audio_mb=5), limit=50, merge=True)
    assert plan["merge_mode"] == "fewest" and [(i["kind"], len(i["parts"])) for i in plan["items"]] == [
        ("audio", 30), ("pdf", 30)]
    assert "merge_group_count" not in plan


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
