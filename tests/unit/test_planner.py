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


def _assert_partition(plan, parts, kind):
    """Every part that has this kind appears in exactly ONE item, in order, in consecutive runs."""
    flat = [m for i in _kind(plan, kind) for m in i["parts"]]
    want = [p.part for p in parts if (p.audio_path if kind == "audio" else p.pdf_path)]
    assert flat == want


def test_over_quota_merges_only_as_much_as_needed_pdf_first():
    plan = _plan(_parts(30, audio_mb=5), limit=50)  # T=60, R=10: only PDFs merge
    assert plan["strategy"] == "merged" and plan["merge_mode"] == "minimal" and len(plan["items"]) == 50
    audio, pdf = _kind(plan, "audio"), _kind(plan, "pdf")
    assert len(audio) == 30 and all(not a["merge"] and len(a["parts"]) == 1 for a in audio)
    assert [a["key"] for a in audio][:2] == ["P01:audio", "P02:audio"] and audio[0]["title"] == "P01｜第1集｜音频"
    assert len(pdf) == 20 and sorted(len(g["parts"]) for g in pdf) == [1] * 10 + [2] * 10
    assert plan["merge_group_sizes"]["pdf"] == [len(g["parts"]) for g in pdf]
    assert plan["merge_group_sizes"]["audio"] == [1] * 30
    first = pdf[0]
    assert (first["merge"], first["parts"], first["path"], first["title"]) == (True, [1, 2], None, "P01+P02｜关键帧PDF")
    assert first["bytes"] == 2 * 2 * MB
    _assert_partition(plan, _parts(30), "audio")
    _assert_partition(plan, _parts(30), "pdf")


def test_quota_of_40_merges_pdfs_three_at_a_time():
    plan = _plan(_parts(30), limit=40)  # R=20 -> 10 PDF groups of 3
    pdf = _kind(plan, "pdf")
    assert len(plan["items"]) == 40 and len(_kind(plan, "audio")) == 30
    assert [len(g["parts"]) for g in pdf] == [3] * 10


def test_quota_of_31_merges_all_pdfs_into_one():
    plan = _plan(_parts(30), limit=31)  # R=29: PDFs fully merged, audio untouched
    pdf = _kind(plan, "pdf")
    assert len(plan["items"]) == 31 and len(_kind(plan, "audio")) == 30
    assert len(pdf) == 1 and pdf[0]["parts"] == list(range(1, 31)) and pdf[0]["title"] == "P01-P30｜关键帧PDF"


def test_quota_of_20_merges_pdfs_fully_then_audio_just_enough():
    plan = _plan(_parts(30), limit=20)  # R=40: PDF -29, audio -11 -> 19 audio groups
    audio, pdf = _kind(plan, "audio"), _kind(plan, "pdf")
    assert len(plan["items"]) == 20 and len(pdf) == 1 and len(audio) == 19
    assert sorted(len(g["parts"]) for g in audio) == [1] * 8 + [2] * 11
    _assert_partition(plan, _parts(30), "audio")


def test_quota_of_2_gives_one_audio_and_one_pdf_when_sizes_allow():
    plan = _plan(_parts(30, audio_mb=5), limit=2)
    assert [(i["kind"], len(i["parts"])) for i in plan["items"]] == [("audio", 30), ("pdf", 30)]


def test_quota_of_2_is_infeasible_when_the_audio_cannot_fit_one_file():
    with pytest.raises(Bili2nbError) as ei:
        _plan(_parts(30, audio_mb=10), limit=2)  # 300 MB audio needs 2 files
    assert ei.value.code == "UPLOAD_PLAN_INFEASIBLE" and ei.value.extra["needed_sources"] == 3


def test_parts_without_pdf_only_reduce_what_exists():
    parts = _parts(30)
    parts[4] = PartFiles(5, "第5集", 600.0, "/o/P5/audio.m4a", 10 * MB)  # no PDF
    parts[9] = PartFiles(10, "第10集", 600.0, "/o/P10/audio.m4a", 10 * MB)
    plan = _plan(parts, limit=45)  # T=58, R=13 -> PDFs 28 -> 15 groups
    assert len(plan["items"]) == 45 and len(_kind(plan, "audio")) == 30 and len(_kind(plan, "pdf")) == 15
    _assert_partition(plan, parts, "pdf")
    assert all(5 not in g["parts"] and 10 not in g["parts"] for g in _kind(plan, "pdf"))


def test_audio_only_over_quota_merges_audio_only():
    plan = _plan(_parts(30, visual=False), limit=20)  # R=10
    assert len(plan["items"]) == 20 and all(i["kind"] == "audio" for i in plan["items"])
    assert plan["merge_group_sizes"]["pdf"] == [] and sorted(plan["merge_group_sizes"]["audio"]) == [1] * 10 + [2] * 10


def test_size_cap_splits_an_even_group_further_when_needed():
    # 6 parts x 100 MB audio, cap 190 -> no two audios fit together; PDFs tiny. limit 7: T=12, R=5 -> PDF -5 -> 1 group
    plan = _plan(_parts(6, audio_mb=100), limit=7, group_max=190 * MB)
    assert len(_kind(plan, "audio")) == 6 and len(_kind(plan, "pdf")) == 1 and len(plan["items"]) == 7


def test_audio_group_sizes_respect_the_cap_and_the_total_is_within_the_quota():
    parts = _parts(10, audio_mb=60)  # T=20
    plan = _plan(parts, limit=5, group_max=190 * MB)  # R=15: PDF -9, audio -6 -> 4 groups
    audio = _kind(plan, "audio")
    assert [i["parts"] for i in audio] == [[1, 2, 3], [4, 5, 6], [7, 8], [9, 10]]
    assert all(i["bytes"] <= 190 * MB for i in plan["items"])
    assert len(_kind(plan, "pdf")) == 1 and len(plan["items"]) == 5


def test_a_single_oversized_even_group_is_split_and_the_search_goes_on():
    # audio: 30 x 10 MB with cap 25 MB: at most 2 per group. limit 20: R=40 -> PDF 1 group; audio must reach 19 groups
    plan = _plan(_parts(30, audio_mb=10, pdf_mb=0), limit=20, group_max=25 * MB)
    audio = _kind(plan, "audio")
    assert len(plan["items"]) <= 20 and all(i["bytes"] <= 25 * MB for i in plan["items"])
    _assert_partition(plan, _parts(30), "audio")
    assert len(audio) == 19


def test_oversized_even_group_is_split_by_size_and_the_search_tries_fewer_groups():
    sizes = [100, 100, 10, 10, 10, 10]
    parts = [PartFiles(i + 1, f"第{i + 1}集", 60.0, f"/a{i}", s * MB) for i, s in enumerate(sizes)]
    plan = _plan(parts, limit=3, group_max=150 * MB)  # g=3 -> [1,2] is 200 MB -> split -> 4 groups (too many)
    assert [i["parts"] for i in plan["items"]] == [[1], [2, 3], [4, 5, 6]]  # g=2 then size-split fits exactly 3
    assert all(i["bytes"] <= 150 * MB for i in plan["items"])


def test_minimal_merge_is_deterministic_and_never_over_quota():
    for limit in (2, 5, 20, 31, 40, 49, 50, 59):
        try:
            a = _plan(_parts(30, audio_mb=5), limit=limit)
        except Bili2nbError:
            assert limit < 2
            continue
        b = _plan(_parts(30, audio_mb=5), limit=limit)
        assert a == b and len(a["items"]) <= limit
        assert len(a["items"]) == min(limit, 60)  # merged JUST enough: as many sources as the quota allows


def test_quota_that_fits_per_part_is_unchanged_and_has_no_merge_fields():
    plan = _plan(_parts(25), limit=50)
    assert plan["strategy"] == "per_part" and "merge_mode" not in plan and len(plan["items"]) == 50


def test_merge_requested_still_means_the_fewest_sources():
    plan = _plan(_parts(30, audio_mb=5), limit=50, merge=True)
    assert plan["merge_mode"] == "fewest" and [(i["kind"], len(i["parts"])) for i in plan["items"]] == [
        ("audio", 30), ("pdf", 30)]


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
