from __future__ import annotations

import json

from bili2nb import layout
from bili2nb.bilibili.downloader import VideoInfo


def _info(part=1, cid="1001", title="系列", part_title="第一集", total=3, vid="BV1GJ411x7h7"):
    return VideoInfo(vid, cid, part, title, "u", 60.0, "https://x", total, part_title)


def _write_part(d, *, cid="1001", part=1, complete=True, vid="BV1GJ411x7h7"):
    d.mkdir(parents=True, exist_ok=True)
    (d / "audio.m4a").write_bytes(b"a")
    (d / "manifest.json").write_text(json.dumps({
        "complete": complete, "video_id": vid, "cid": cid, "part": part,
        "audio": {"file": "audio.m4a"}, "visual": {"status": "skipped"}}), encoding="utf-8")


def test_dirname_pads_to_total_and_is_filesystem_safe():
    assert layout.part_dirname(1, "第一集", 3) == "P01_第一集"
    assert layout.part_dirname(7, "a/b:c*?", 120) == "P007_a_b_c"
    assert len(layout.part_dirname(1, "长" * 200, 3)) <= 3 + 1 + 40


def test_new_part_goes_under_the_video_folder(tmp_path):
    assert layout.resolve_part_dir(tmp_path, _info()) == tmp_path / "BV1GJ411x7h7" / "P01_第一集"


def test_parts_never_share_a_folder(tmp_path):
    dirs = {layout.resolve_part_dir(tmp_path, _info(p, str(1000 + p), part_title=f"集{p}")) for p in (1, 2, 3)}
    assert len(dirs) == 3


def test_existing_folder_with_same_cid_wins_even_if_the_title_changed(tmp_path):
    old = tmp_path / "BV1GJ411x7h7" / "P01_旧标题"
    _write_part(old, cid="1001")
    assert layout.resolve_part_dir(tmp_path, _info(part_title="新标题")) == old


def test_other_cid_is_not_mistaken_for_this_part(tmp_path):
    _write_part(tmp_path / "BV1GJ411x7h7" / "P01_x", cid="9999")
    got = layout.resolve_part_dir(tmp_path, _info(cid="1001"))
    assert got.name == "P01_第一集"


def test_read_manifest_requires_complete_and_files(tmp_path):
    d = tmp_path / "p"
    _write_part(d, complete=False)
    assert layout.read_manifest(d) is None
    _write_part(d, complete=True)
    assert layout.read_manifest(d)["cid"] == "1001"
    (d / "audio.m4a").unlink()
    assert layout.read_manifest(d) is None


def test_batch_manifest_is_atomic_and_complete(tmp_path):
    b = tmp_path / "BV1"
    layout.write_batch_manifest(b, video_id="BV1", title="系列", selection="all",
                                parts=[{"part": 1, "status": "done"}, {"part": 2, "status": "failed"}])
    doc = json.loads((b / "manifest.json").read_text(encoding="utf-8"))
    assert doc["selection"] == "all" and [p["status"] for p in doc["parts"]] == ["done", "failed"]
    assert [p.name for p in b.iterdir()] == ["manifest.json"]  # no temp file left behind
