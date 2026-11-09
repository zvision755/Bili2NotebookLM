"""V1.2: enumerate the parts (分 P) of a video from ONE metadata call; never silently fall back to P1."""

from __future__ import annotations

import pytest

from bili2nb.bilibili import downloader as dl
from bili2nb.bilibili.parser import VideoRef
from bili2nb.errors import Bili2nbError

BV = "BV1GJ411x7h7"


def _pages(n=3, **over):
    return [
        {"cid": 1000 + i, "page": i, "part": f"第{i}集", "duration": 60 * i, **over}
        for i in range(1, n + 1)
    ]


def _meta(pages, *, code=0, title="系列课程", bvid=BV, message="0"):
    return dl.VideoMeta(code=code, message=message, bvid=bvid, title=title, uploader="作者", pages=pages)


@pytest.fixture
def no_ytdlp(monkeypatch):
    """Any per-part yt-dlp call is a failure: the API metadata alone must be enough."""
    def boom(*a, **k):
        raise AssertionError("yt-dlp must not be called when the view API answered")
    monkeypatch.setattr(dl, "fetch_info", boom)
    monkeypatch.setattr(dl, "_enumerate_with_ytdlp", boom)


def _plan(monkeypatch, meta, ref):
    calls = []
    monkeypatch.setattr(dl, "fetch_video", lambda vid: calls.append(vid) or meta)
    return dl.plan_parts(ref), calls


def test_no_part_means_all_parts_from_one_api_call(monkeypatch, no_ytdlp):
    infos, calls = _plan(monkeypatch, _meta(_pages(3)), VideoRef(BV, None))
    assert calls == [BV]  # exactly one metadata request for the whole video
    assert [i.part for i in infos] == [1, 2, 3]
    assert [i.cid for i in infos] == ["1001", "1002", "1003"]  # each part has its OWN cid
    assert [i.duration for i in infos] == [60.0, 120.0, 180.0]
    assert all(i.part_count == 3 and i.video_id == BV for i in infos)
    assert [i.webpage_url for i in infos] == [f"https://www.bilibili.com/video/{BV}?p={n}" for n in (1, 2, 3)]
    assert [i.part_title for i in infos] == ["第1集", "第2集", "第3集"]
    assert len({i.video_key for i in infos}) == 3


def test_explicit_part_selects_only_that_part(monkeypatch, no_ytdlp):
    infos, _ = _plan(monkeypatch, _meta(_pages(3)), VideoRef(BV, 2))
    assert [(i.part, i.cid) for i in infos] == [(2, "1002")]


def test_part_out_of_range_is_an_explicit_error(monkeypatch, no_ytdlp):
    with pytest.raises(Bili2nbError) as ei:
        _plan(monkeypatch, _meta(_pages(3)), VideoRef(BV, 5))
    assert ei.value.code == "VIDEO_UNAVAILABLE" and "3" in ei.value.message


def test_single_part_video_keeps_the_plain_url_and_title(monkeypatch, no_ytdlp):
    infos, _ = _plan(monkeypatch, _meta(_pages(1), title="单集"), VideoRef(BV, None))
    (i,) = infos
    assert i.part_count == 1 and i.title == "单集" and i.webpage_url == f"https://www.bilibili.com/video/{BV}"


def test_multi_part_titles_name_the_part(monkeypatch, no_ytdlp):
    infos, _ = _plan(monkeypatch, _meta(_pages(2)), VideoRef(BV, None))
    assert infos[1].title == "系列课程 (P2 第2集)"


def test_av_input_resolves_to_the_bv_from_the_api(monkeypatch, no_ytdlp):
    infos, _ = _plan(monkeypatch, _meta(_pages(2)), VideoRef("av170001", None))
    assert {i.video_id for i in infos} == {BV}


def test_missing_video_is_reported(monkeypatch, no_ytdlp):
    with pytest.raises(Bili2nbError) as ei:
        _plan(monkeypatch, _meta([], code=-404, message="啥都木有"), VideoRef(BV, None))
    assert ei.value.code == "VIDEO_UNAVAILABLE" and "-404" in ei.value.message


def test_missing_cid_falls_back_to_part_number_key(monkeypatch, no_ytdlp):
    pages = _pages(2)
    del pages[1]["cid"]
    infos, _ = _plan(monkeypatch, _meta(pages), VideoRef(BV, None))
    assert infos[1].cid is None and infos[1].video_key == f"{BV}_p2"


def test_page_without_duration_uses_ytdlp_for_that_part_only(monkeypatch):
    pages = _pages(3)
    pages[1]["duration"] = 0
    fetched = []

    def fake_info(ref):
        fetched.append(ref.part)
        return dl.VideoInfo(BV, "1002", ref.part, "t", "u", 77.0, ref.url, 3, "第2集")

    monkeypatch.setattr(dl, "fetch_info", fake_info)
    monkeypatch.setattr(dl, "fetch_video", lambda vid: _meta(pages))
    infos = dl.plan_parts(VideoRef(BV, None))
    assert fetched == [2] and infos[1].duration == 77.0 and infos[0].duration == 60.0


@pytest.mark.parametrize("code", [None, -412])
def test_api_unreachable_enumerates_with_ytdlp_never_assumes_p1(monkeypatch, code):
    monkeypatch.setattr(dl, "fetch_video", lambda vid: _meta([], code=code, bvid=BV))
    monkeypatch.setattr(dl, "_enumerate_with_ytdlp", lambda ref: 3)
    got = []

    def fake_info(ref):
        got.append(ref.part)
        return dl.VideoInfo(BV, None, ref.part, "t", "u", 10.0, ref.url, 3)

    monkeypatch.setattr(dl, "fetch_info", fake_info)
    infos = dl.plan_parts(VideoRef(BV, None))
    assert got == [1, 2, 3] and [i.part for i in infos] == [1, 2, 3]


def test_api_unreachable_and_enumeration_fails_asks_for_explicit_part(monkeypatch):
    monkeypatch.setattr(dl, "fetch_video", lambda vid: _meta([], code=None))

    def fail(ref):
        raise Bili2nbError("NETWORK_ERROR", "下载时网络错误。")

    monkeypatch.setattr(dl, "_enumerate_with_ytdlp", fail)
    with pytest.raises(Bili2nbError) as ei:
        dl.plan_parts(VideoRef(BV, None))
    assert "--part" in ei.value.message and ei.value.code == "NETWORK_ERROR"


def test_api_unreachable_with_explicit_part_uses_the_single_part_path(monkeypatch):
    monkeypatch.setattr(dl, "fetch_video", lambda vid: _meta([], code=None))
    monkeypatch.setattr(dl, "_enumerate_with_ytdlp", lambda ref: pytest.fail("part is known"))
    monkeypatch.setattr(
        dl, "fetch_info", lambda ref: dl.VideoInfo(BV, "9", ref.part, "t", "u", 5.0, ref.url, 1)
    )
    (i,) = dl.plan_parts(VideoRef(BV, 2))
    assert i.part == 2
