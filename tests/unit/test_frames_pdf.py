from datetime import datetime

import pytest
from pypdf import PdfReader

from bili2nb.errors import Bili2nbError
from bili2nb.media import frames as fm
from bili2nb.media.pdf import build_pdf, timestamp_url, validate_pdf
from tests.unit import helpers as h


def test_slides_mode_picks_each_slide_and_skips_black(tmp_path):
    v = tmp_path / "v.mp4"
    dur = h.write_video(v, [
        (h.slide(220, "A"), 6), (h.black(), 3), (h.slide(100, "B"), 6),
        (h.slide(170, "C"), 6),
    ])
    r = fm.select_frames(v, dur, "slides", tmp_path / "fr", step=1.0)
    assert r.visual_skipped is None
    assert len(r.frames) == 3, [f.t for f in r.frames]
    ts = [f.t for f in r.frames]
    assert ts == sorted(ts)
    assert not any(6.0 <= t < 9.0 for t in ts), "black gap must not be selected"
    assert all(0 <= t <= dur for t in ts)
    assert all(f.path.exists() and f.path.stat().st_size > 0 for f in r.frames)


def test_slides_mode_drops_duplicate_slide_that_returns(tmp_path):
    v = tmp_path / "v.mp4"
    dur = h.write_video(v, [
        (h.slide(220, "A"), 5), (h.slide(100, "B"), 5), (h.slide(220, "A"), 5),
    ])
    r = fm.select_frames(v, dur, "slides", tmp_path / "fr", step=1.0)
    assert len(r.frames) == 2  # A, B (second A is a duplicate)


def test_all_black_video_is_visual_skipped_not_error(tmp_path):
    v = tmp_path / "v.mp4"
    dur = h.write_video(v, [(h.black(), 8)])
    r = fm.select_frames(v, dur, "auto", tmp_path / "fr", step=1.0)
    assert r.frames == [] and r.visual_skipped


def test_auto_falls_back_to_timed_sampling_with_warning(tmp_path):
    v = tmp_path / "v.mp4"
    dur = h.noisy_video(v, 90)
    r = fm.select_frames(v, dur, "auto", tmp_path / "fr", step=2.0)
    assert r.warnings and "定时取帧" in r.warnings[0]
    assert r.method == "auto+timed"
    assert len(r.frames) >= 1


def test_scene_mode_works_and_fallback_on_detector_failure(tmp_path, monkeypatch):
    v = tmp_path / "v.mp4"
    dur = h.write_video(v, [(h.slide(220, "A"), 6), (h.slide(90, "B"), 6)])
    r = fm.select_frames(v, dur, "scene", tmp_path / "a", step=1.0)
    assert len(r.frames) >= 2 and not r.warnings

    def boom(*a, **k):
        raise RuntimeError("detector broke")

    monkeypatch.setattr(fm, "_scene_times", boom)
    r2 = fm.select_frames(v, dur, "scene", tmp_path / "b", step=1.0)
    assert r2.method == "timed-fallback"
    assert any("SceneDetect 失败" in w for w in r2.warnings)  # never silently "no visuals"
    assert r2.visual_skipped is None


def test_frame_cap_is_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "MAX_FRAMES", 3)
    v = tmp_path / "v.mp4"
    segs = [(h.slide(40 + 30 * i, str(i)), 4) for i in range(7)]
    dur = h.write_video(v, segs)
    r = fm.select_frames(v, dur, "slides", tmp_path / "fr", step=1.0)
    assert len(r.frames) == 3 and any("上限" in w for w in r.warnings)


def test_unreadable_video_is_media_error(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(Bili2nbError) as e:
        fm.select_frames(bad, 10, "auto", tmp_path / "fr")
    assert e.value.code == "MEDIA_ERROR"


def test_bad_mode_rejected(tmp_path):
    with pytest.raises(Bili2nbError):
        fm.select_frames(tmp_path / "x.mp4", 1, "bogus", tmp_path)


def test_hms():
    assert fm.hms(0) == "00:00:00" and fm.hms(3661.9) == "01:01:01" and fm.hms(-5) == "00:00:00"


def _frames(tmp_path, n=3):
    v = tmp_path / "v.mp4"
    segs = [(h.slide(60 + 60 * i, f"S{i}"), 4) for i in range(n)]
    dur = h.write_video(v, segs)
    return fm.select_frames(v, dur, "slides", tmp_path / "fr", step=1.0), dur


def test_pdf_build_and_validate(tmp_path):
    r, dur = _frames(tmp_path)
    out = tmp_path / "keyframes.pdf"
    url = "https://www.bilibili.com/video/BV1GJ411x7h7?p=2"
    build_pdf(r.frames, out, title="测试视频 Test", uploader="作者甲", video_url=url,
              duration=dur, created=datetime(2026, 11, 8, 12, 0))
    assert validate_pdf(out, len(r.frames), url) == len(r.frames) + 1
    rd = PdfReader(str(out))
    cover = rd.pages[0].extract_text()
    assert "测试视频" in cover and "BV1GJ411x7h7" in cover and "00:00:12" in cover  # selectable text
    assert f"截图数量: {len(r.frames)}" in cover
    assert "00:00:0" in rd.pages[1].extract_text()  # HH:MM:SS timestamp on screenshot page
    assert len(rd.outline) == len(r.frames) + 1  # bookmarks
    link = str(rd.pages[1]["/Annots"][0].get_object()["/A"]["/URI"])
    assert link.startswith(url + "&t=")
    assert not (tmp_path / "keyframes.pdf.tmp").exists()  # atomic write left no temp


def test_timestamp_url():
    assert timestamp_url("https://x/v", 75.9) == "https://x/v?t=75"
    assert timestamp_url("https://x/v?p=2", 5) == "https://x/v?p=2&t=5"


def test_validate_pdf_rejects_wrong_page_count_and_garbage(tmp_path):
    r, dur = _frames(tmp_path)
    out = tmp_path / "k.pdf"
    url = "https://www.bilibili.com/video/BV1GJ411x7h7"
    build_pdf(r.frames, out, title="t", uploader="u", video_url=url, duration=dur)
    with pytest.raises(Bili2nbError):
        validate_pdf(out, len(r.frames) + 5, url)
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"%PDF-1.4 garbage")
    with pytest.raises(Bili2nbError) as e:
        validate_pdf(junk, 1, url)
    assert e.value.code == "MEDIA_ERROR"


def test_build_pdf_requires_frames(tmp_path):
    with pytest.raises(Bili2nbError):
        build_pdf([], tmp_path / "x.pdf", title="t", uploader="u", video_url="https://x", duration=1)


@pytest.mark.parametrize("dur,iv,n", [(5, 30, 1), (12, 30, 1), (30, 30, 1), (95, 30, 4), (3600, 90, 40)])
def test_timed_times_always_nonempty_and_inside_video(dur, iv, n):
    ts = fm.timed_times(dur, iv)
    assert len(ts) == n and all(0 < t < dur for t in ts) and ts == sorted(ts)


# ---- Gate 4: burned-in subtitles must not make the same slide look "new" ---------------
import numpy as np

from bili2nb.media import frames as fr


def _slide(seed: int, subtitle: int = 0):
    rng = np.random.default_rng(seed)
    th = np.full((90, 160), 235, dtype=np.uint8)
    for _ in range(6):  # a few dark text-like blocks in the body of the slide
        y, x = rng.integers(5, 60), rng.integers(5, 120)
        th[y:y + 8, x:x + 30] = 40
    if subtitle:  # a subtitle line in the bottom band, different per `subtitle`
        r = np.random.default_rng(1000 + subtitle)
        x = int(r.integers(10, 60))
        th[82:88, x:x + 90] = 30
    return th


def test_frac_diff_ignores_bottom_subtitle_band():
    assert fr.frac_diff(_slide(1, 1), _slide(1, 2)) == 0.0
    assert fr.frac_diff(_slide(1, 0), _slide(1, 3)) == 0.0


def test_frac_diff_still_sees_real_slide_change():
    assert fr.frac_diff(_slide(1), _slide(2)) > fr.DUP_FRAC


def test_dedupe_merges_same_slide_with_different_subtitles():
    items = [(0.0, _slide(1, 1)), (30.0, _slide(1, 2)), (60.0, _slide(1, 3)), (90.0, _slide(2, 1))]
    assert [t for t, _ in fr.dedupe(items)] == [0.0, 90.0]


def test_pick_slides_subtitle_only_changes_do_not_split_a_slide():
    samples = [(float(i * 2), _slide(1, 1 + i % 4)) for i in range(10)] + [
        (float(20 + i * 2), _slide(2, 1 + i % 4)) for i in range(10)
    ]
    assert len(fr.pick_slides(samples)) == 2


# ---- Gate 5: Chinese text must be an EMBEDDED font, or NotebookLM's preview/extractor shows
# garbage (live: cover page of every PDF was mojibake in Gemini Notebook with STSong-Light,
# a non-embedded CID font that only desktop viewers substitute).
def _fonts_of(page):
    res = page["/Resources"].get_object()
    return [f.get_object() for f in res["/Font"].get_object().values()]


def _is_embedded(font) -> bool:
    desc = font
    if "/DescendantFonts" in font:  # Type0 -> CIDFont
        desc = font["/DescendantFonts"][0].get_object()
    d = desc.get("/FontDescriptor")
    if d is None:
        return False
    d = d.get_object()
    return any(k in d for k in ("/FontFile", "/FontFile2", "/FontFile3"))


def _build(tmp_path):
    r, dur = _frames(tmp_path)
    out = tmp_path / "k.pdf"
    build_pdf(r.frames, out, title="关键帧 测试视频", uploader="作者甲",
              video_url="https://www.bilibili.com/video/BV1GJ411x7h7", duration=dur,
              created=datetime(2026, 11, 8, 12, 0))
    return out


def test_cover_uses_embedded_cjk_font(tmp_path):
    from bili2nb.media import pdf as pm

    out = _build(tmp_path)
    cover = PdfReader(str(out)).pages[0]
    # ReportLab always declares the standard-14 Helvetica (/F1, Latin only, never CJK);
    # every OTHER font on the cover must be embedded, and at least one must exist.
    fonts = [f for f in _fonts_of(cover) if f.get("/BaseFont") != "/Helvetica"]
    assert fonts and all(_is_embedded(f) for f in fonts), "CJK font must be embedded in the PDF"
    assert pm.cjk_font_embedded() is True
    assert "关键帧" in cover.extract_text()


def test_missing_system_font_falls_back_and_reports(tmp_path, monkeypatch):
    from bili2nb.media import pdf as pm

    monkeypatch.setattr(pm, "_FONT_CANDIDATES", [("/nonexistent/font.ttc", 0)])
    monkeypatch.delenv("BILI2NB_PDF_FONT", raising=False)
    monkeypatch.setattr(pm, "FONT", pm.FALLBACK_FONT)
    pm._resolved = False  # force re-resolution
    try:
        out = _build(tmp_path)
        assert pm.cjk_font_embedded() is False  # reported so the pipeline can warn
        assert "关键帧" in PdfReader(str(out)).pages[0].extract_text()  # still usable locally
    finally:
        pm._resolved = False
