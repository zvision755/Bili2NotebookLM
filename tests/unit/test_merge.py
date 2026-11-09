"""V1.2 merge: real ffmpeg / pypdf on tiny synthetic media (no network)."""

from __future__ import annotations

import shutil
import subprocess

import pytest
from pypdf import PdfReader

from bili2nb.errors import Bili2nbError
from bili2nb.media import audio as am
from bili2nb.media import frames as fm
from bili2nb.media import merge
from bili2nb.media.pdf import build_pdf
from tests.unit import helpers as h

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _aac(tmp, name, sec, rate=None):
    p = tmp / name
    if rate is None:
        h.make_audio(p, sec, "aac")
    else:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={sec}",
                        "-ar", str(rate), "-c:a", "aac", str(p)], check=True)
    return p


# ------------------------------------------------------------------ audio
def test_same_codec_is_concatenated_without_re_encoding(tmp_path):
    files = [_aac(tmp_path, f"a{i}.m4a", s) for i, s in enumerate((2, 3, 4), 1)]
    res = merge.merge_audio(files, tmp_path / "merged.m4a")
    assert res.action == "copy" and res.path.suffix == ".m4a"
    assert abs(res.duration - 9.0) <= 0.5 and len(res.offsets) == 3
    assert res.offsets[0] == 0.0 and abs(res.offsets[1] - 2.0) < 0.3 and abs(res.offsets[2] - 5.0) < 0.5
    am.decode_check(res.path)  # plays end-to-end


def test_mixed_codecs_are_converted_to_one_aac_file(tmp_path):
    a = _aac(tmp_path, "a.m4a", 2)
    b = tmp_path / "b.mp3"
    h.make_audio(b, 3, "mp3")
    res = merge.merge_audio([a, b], tmp_path / "merged.m4a")
    assert res.action == "transcode" and abs(res.duration - 5.0) <= 0.6
    am.decode_check(res.path)


def test_mixed_sample_rates_are_normalised(tmp_path):
    res = merge.merge_audio([_aac(tmp_path, "a.m4a", 2, 44100), _aac(tmp_path, "b.m4a", 2, 48000)],
                            tmp_path / "m.m4a")
    assert res.action == "transcode" and abs(res.duration - 4.0) <= 0.6


def test_size_limit_is_enforced_and_no_partial_output_is_left(tmp_path):
    files = [_aac(tmp_path, "a.m4a", 2), _aac(tmp_path, "b.m4a", 2)]
    with pytest.raises(Bili2nbError) as ei:
        merge.merge_audio(files, tmp_path / "m.m4a", max_bytes=1000)
    assert ei.value.code == "MEDIA_ERROR" and not (tmp_path / "m.m4a").exists()
    assert not list(tmp_path.glob("*.tmp*"))


def test_duration_mismatch_is_detected(tmp_path, monkeypatch):
    files = [_aac(tmp_path, "a.m4a", 4), _aac(tmp_path, "b.m4a", 4)]  # 8 s total vs 4 s reported
    real = merge._duration
    monkeypatch.setattr(merge, "_duration", lambda p: real(p) / 2 if p.name.startswith("m") else real(p))
    with pytest.raises(Bili2nbError) as ei:
        merge.merge_audio(files, tmp_path / "m.m4a")
    assert "时长" in ei.value.message and not (tmp_path / "m.m4a").exists()


def test_file_names_with_quotes_and_spaces_are_safe(tmp_path):
    a = _aac(tmp_path, "it's a name.m4a", 2)
    b = _aac(tmp_path, "b c.m4a", 2)
    assert abs(merge.merge_audio([a, b], tmp_path / "m.m4a").duration - 4.0) <= 0.5


# ------------------------------------------------------------------ pdf
def _pdf(tmp, name, title, n_frames, url):
    frames = []
    for i in range(n_frames):
        p = tmp / f"{name}_{i}.png"
        import cv2
        cv2.imwrite(str(p), h.slide(200 - i * 30, f"{name}{i}"))
        frames.append(fm.Frame(t=10.0 * (i + 1), path=p))
    out = tmp / f"{name}.pdf"
    build_pdf(frames, out, title=title, uploader="u", video_url=url, duration=100.0)
    return out


def test_pdf_merge_keeps_order_and_adds_one_bookmark_per_part(tmp_path):
    u = "https://www.bilibili.com/video/BV1GJ411x7h7?p="
    p1 = _pdf(tmp_path, "one", "第一集", 2, u + "1")   # cover + 2 pages
    p2 = _pdf(tmp_path, "two", "第二集", 3, u + "2")   # cover + 3 pages
    out = tmp_path / "merged.pdf"
    res = merge.merge_pdfs([p1, p2], ["P01｜第一集", "P02｜第二集"], out, title="系列",
                           note_lines=["P01 在音频中从 00:00:00 开始", "P02 在音频中从 00:02:00 开始"])
    r = PdfReader(str(out))
    assert res.pages == len(r.pages) == 1 + 3 + 4  # index page + (cover+2) + (cover+3)
    tops = [o.title for o in r.outline if not isinstance(o, list)]
    assert tops == ["目录 / Index", "P01｜第一集", "P02｜第二集"]
    # each part's bookmark points at that part's own cover page, in order
    pages = [r.get_destination_page_number(o) for o in r.outline if not isinstance(o, list)]
    assert pages == [0, 1, 4]
    assert "P02" in (r.pages[0].extract_text() or "")  # the index page lists the parts
    assert "第二集" in (r.pages[4].extract_text() or "")  # content order preserved


def test_pdf_merge_preserves_each_parts_own_bookmarks_as_children(tmp_path):
    u = "https://www.bilibili.com/video/BV1GJ411x7h7?p="
    p1 = _pdf(tmp_path, "one", "第一集", 2, u + "1")
    out = tmp_path / "m.pdf"
    merge.merge_pdfs([p1], ["P01｜第一集"], out, title="t", note_lines=[])
    r = PdfReader(str(out))
    children = [o for o in r.outline if isinstance(o, list)]
    assert children and len(children[0]) >= 3  # cover + 2 timestamp entries survive


def test_pdf_merge_size_limit(tmp_path):
    u = "https://www.bilibili.com/video/BV1GJ411x7h7?p=1"
    p1 = _pdf(tmp_path, "one", "第一集", 1, u)
    with pytest.raises(Bili2nbError) as ei:
        merge.merge_pdfs([p1], ["P01"], tmp_path / "m.pdf", title="t", note_lines=[], max_bytes=100)
    assert ei.value.code == "MEDIA_ERROR" and not (tmp_path / "m.pdf").exists()
