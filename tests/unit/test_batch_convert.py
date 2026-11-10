"""V1.2: `convert` over all parts - per-part results, batch manifest, failure and duplicate guards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bili2nb import batch
from bili2nb.bilibili.downloader import VideoInfo
from bili2nb.errors import Bili2nbError

BV = "BV1GJ411x7h7"
URL = f"https://www.bilibili.com/video/{BV}"


def _infos(n=3):
    return [VideoInfo(BV, str(1000 + i), i, f"系列 (P{i} 第{i}集)", "u", 60.0 * i,
                      f"{URL}?p={i}", n, f"第{i}集", "系列") for i in range(1, n + 1)]


class Fake:
    def __init__(self, tmp, n=3):
        self.tmp = tmp
        self.infos = _infos(n)
        self.calls: list[int] = []
        self.fail: dict[int, list[Bili2nbError]] = {}
        self.same_audio = False
        self.sleeps: list[float] = []

    def plan(self, ref):
        self.plan_ref = ref
        return [i for i in self.infos if ref.part in (None, i.part)]

    def convert(self, url, *, output_root, frames_mode, part, audio_only, log, info=None):
        self.calls.append(info.part)
        q = self.fail.get(info.part)
        if q:
            raise q.pop(0)
        d = Path(output_root) / info.video_id / f"P{info.part:02d}_x"
        d.mkdir(parents=True, exist_ok=True)
        content = b"same" if self.same_audio else f"audio-{info.part}".encode()
        (d / "audio.m4a").write_bytes(content)
        return {"status": "ok", "video_title": info.title, "duration_sec": info.duration,
                "output_dir": str(d), "audio_path": str(d / "audio.m4a"), "cid": info.cid,
                "part": info.part, "visual": {"status": "skipped"}, "warnings": [],
                "audio": {"file": "audio.m4a", "bytes": 10}}

    def run(self, **kw):
        kw.setdefault("part", None)
        return batch.convert_parts(URL, output_root=self.tmp / "out", frames_mode="auto",
                                   audio_only=False, log=lambda m: None, plan_fn=self.plan,
                                   convert_fn=self.convert, sleep=self.sleeps.append,
                                   backoff_base=1.0, **kw)


@pytest.fixture
def fake(tmp_path):
    return Fake(tmp_path)


def test_all_parts_are_converted_in_order_each_with_its_own_info(fake):
    res = fake.run()
    assert fake.calls == [1, 2, 3]
    assert [p["part"] for p in res["parts"]] == [1, 2, 3]
    assert res["selection"] == "all" and res["part_count"] == 3 and res["video_title"] == "系列"
    assert len({p["audio_path"] for p in res["parts"]}) == 3  # different parts never share a file


def test_explicit_part_converts_only_that_part_and_keeps_the_legacy_shape(fake):
    res = fake.run(part=2)
    assert fake.calls == [2] and res["selection"] == "P2"
    assert res["audio_path"].endswith("audio.m4a") and res["video_title"].startswith("系列")  # legacy keys
    assert len(res["parts"]) == 1


def test_url_part_beats_the_part_option_with_a_warning(fake):
    res = batch.convert_parts(URL + "?p=3", output_root=fake.tmp / "out", frames_mode="auto",
                              audio_only=False, part=1, log=lambda m: None, plan_fn=fake.plan,
                              convert_fn=fake.convert)
    assert fake.calls == [3] and any("忽略 --part" in w for w in res["warnings"])


def test_batch_manifest_lists_every_part_and_its_status(fake):
    fake.run()
    doc = json.loads((fake.tmp / "out" / BV / "manifest.json").read_text(encoding="utf-8"))
    assert doc["selection"] == "all"
    assert [(p["part"], p["cid"], p["status"]) for p in doc["parts"]] == [
        (1, "1001", "done"), (2, "1002", "done"), (3, "1003", "done")]
    assert all(p["dir"].startswith("P0") for p in doc["parts"])  # relative, no absolute paths leaked


def test_failure_in_p2_keeps_p1_and_marks_the_rest(fake):
    fake.fail[2] = [Bili2nbError("MEDIA_ERROR", "boom")]
    with pytest.raises(Bili2nbError) as ei:
        fake.run()
    assert ei.value.code == "MEDIA_ERROR" and fake.calls == [1, 2]  # P3 never attempted
    doc = json.loads((fake.tmp / "out" / BV / "manifest.json").read_text(encoding="utf-8"))
    assert [p["status"] for p in doc["parts"]] == ["done", "failed", "pending"]
    assert (fake.tmp / "out" / BV / "P01_x" / "audio.m4a").is_file()  # P1 product survives
    assert ei.value.extra["failed_part"] == 2


def test_rate_limit_is_retried_with_bounded_backoff(fake):
    fake.fail[1] = [Bili2nbError("RATE_LIMITED", "412")] * 2
    fake.run()
    assert fake.calls == [1, 1, 1, 2, 3] and fake.sleeps == [1.0, 3.0]


def test_identical_audio_across_parts_is_rejected(fake):
    fake.same_audio = True
    with pytest.raises(Bili2nbError) as ei:
        fake.run()
    assert ei.value.code == "MEDIA_ERROR" and "P1" in ei.value.message and "P2" in ei.value.message


def test_single_part_video_skips_the_duplicate_check(tmp_path):
    f = Fake(tmp_path, n=1)
    f.same_audio = True
    assert f.run()["part_count"] == 1
