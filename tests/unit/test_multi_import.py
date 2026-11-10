"""V1.2 multi-part import: ONE notebook, per-part sources, resumable. Fake NotebookLM, no network.

Single-part videos keep using the proven V1.1 path (see test_import_jobs.py); these tests cover
videos with several parts.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from bili2nb import planner
from bili2nb.bilibili.downloader import VideoInfo
from bili2nb.errors import Bili2nbError
from bili2nb.importer import Importer, job_to_dict
from bili2nb.jobs.db import COMPLETED, FAILED, INTERRUPTED, JobStore
from tests.unit.test_import_jobs import FakeNotebookLM

BV = "BV1GJ411x7h7"
URL = f"https://www.bilibili.com/video/{BV}"
MB = 1024 * 1024


def make_infos(n=3):
    return [VideoInfo(BV, str(1000 + i), i, f"系列 (P{i} 第{i}集)", "作者", 60.0 * i,
                      f"{URL}?p={i}", n, f"第{i}集", "系列") for i in range(1, n + 1)]


class MultiNLM(FakeNotebookLM):
    def __init__(self):
        super().__init__()
        self.limit = 50
        self.limit_origin = "server"
        self.limit_error: Bili2nbError | None = None
        self.titles: list[str] = []
        self.title_failures: dict[str, list[BaseException]] = {}
        self.upload_hook = None
        self.paths: dict[str, Path] = {}
        self.taken_seen: list[tuple[str, ...]] = []

    def source_limit(self):
        if self.limit_error:
            raise self.limit_error
        return self.limit, self.limit_origin

    def list_sources(self, nb):
        from bili2nb.notebook.adapter import SourceInfo
        return [SourceInfo(i, None, st) for i, st in self.notebooks[nb].items()]

    def finalize_sources(self, nb, titles):
        return sorted(i for i in titles if i not in self.notebooks[nb]), []

    def upload_file(self, nb, path, title=None, *, owned=(), taken=()):
        self.titles.append(title)
        self.owned_seen.append(tuple(owned))
        self.taken_seen.append(tuple(taken))
        if self.upload_hook:
            self.upload_hook(title)
        q = self.title_failures.get(title)
        if q:
            raise q.pop(0)
        self._n += 1
        sid = f"src-{self._n}"
        self.notebooks[nb][sid] = "READY"
        self.paths[title] = Path(path)
        self.source_kind[sid] = "pdf" if str(path).endswith(".pdf") else "audio"
        return sid


class Env:
    def __init__(self, tmp_path, n=3):
        self.tmp = tmp_path
        self.store = JobStore(tmp_path / "jobs.sqlite")
        self.nlm = MultiNLM()
        self.infos = make_infos(n)
        self.converted: list[int] = []
        self.convert_fail: dict[int, list[BaseException]] = {}
        self.no_visual: set[int] = set()
        self.audio_mb = 1
        self.real_media = False
        self.plan_calls = 0
        self.sleeps: list[float] = []
        self.logs: list[str] = []

    def plan(self, ref):
        self.plan_calls += 1
        return [i for i in self.infos if ref.part in (None, i.part)]

    def _write_audio(self, path, info):
        if self.real_media:
            from tests.unit import helpers as h
            h.make_audio(path, 2 + 0.5 * info.part, "aac")  # distinct per part
        else:
            path.write_bytes(f"audio-{info.part}".encode() * 10)

    def convert(self, url, *, output_root, frames_mode, part, audio_only, log, info=None):
        self.converted.append(info.part)
        q = self.convert_fail.get(info.part)
        if q:
            raise q.pop(0)
        d = Path(output_root) / info.video_id / f"P{info.part:02d}_x"
        d.mkdir(parents=True, exist_ok=True)
        self._write_audio(d / "audio.m4a", info)
        res = {"video_title": info.title, "duration_sec": info.duration, "output_dir": str(d),
               "audio_path": str(d / "audio.m4a"), "warnings": [], "part": info.part, "cid": info.cid}
        if audio_only or frames_mode == "none":
            res["visual"] = {"status": "skipped", "reason": "audio-only"}
        elif info.part in self.no_visual:
            res["visual"] = {"status": "visual_skipped", "reason": "没有有效画面"}
            res["warnings"].append("visual_skipped: 没有有效画面")
        else:
            if self.real_media:
                self._write_pdf(d / "keyframes.pdf", info)
            else:
                (d / "keyframes.pdf").write_bytes(b"%PDF-fake")
            res["pdf_path"] = str(d / "keyframes.pdf")
            res["visual"] = {"status": "ok", "frame_count": 2, "pages": 3}
        return res

    def _write_pdf(self, path, info):
        import cv2

        from bili2nb.media import frames as fm
        from bili2nb.media.pdf import build_pdf
        from tests.unit import helpers as h

        frames = []
        for i in range(2):
            p = path.parent / f"f{i}.png"
            cv2.imwrite(str(p), h.slide(200 - 40 * i, f"P{info.part}"))
            frames.append(fm.Frame(10.0 * (i + 1), p))
        build_pdf(frames, path, title=info.title, uploader="u", video_url=info.webpage_url, duration=60.0)

    def importer(self):
        return Importer(self.store, self.nlm, convert_fn=self.convert, plan_parts_fn=self.plan,
                        sleep=self.sleeps.append, log=self.logs.append, backoff_base=5.0)

    def run(self, **kw):
        kw.setdefault("output", str(self.tmp / "out"))
        return self.importer().run(URL, **kw)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BILI2NB_HOME", str(tmp_path / "home"))
    return Env(tmp_path)


# ------------------------------------------------------------------ basic shapes
def test_three_parts_one_notebook_six_sources(env):
    res = env.run()
    assert res["status"] == COMPLETED and env.nlm.create_calls == 1
    assert res["source_count"] == 6 and len(env.nlm.titles) == 6
    assert env.nlm.titles == ["P01｜第1集｜音频", "P01｜第1集｜关键帧PDF", "P02｜第2集｜音频",
                              "P02｜第2集｜关键帧PDF", "P03｜第3集｜音频", "P03｜第3集｜关键帧PDF"]
    assert all(res["source_ready"].values()) and len(set(res["source_ids"].values())) == 6
    assert res["upload_plan"]["strategy"] == "per_part"
    assert [p["part"] for p in res["parts"]] == [1, 2, 3] and res["part_count"] == 3
    assert list(env.nlm.notebooks) == ["nb-1"]  # one notebook holds all six sources
    assert len(env.nlm.notebooks["nb-1"]) == 6


def test_selecting_p2_of_three_uses_the_single_part_path_with_two_sources(env):
    res = env.run(part=2)
    assert res["source_count"] == 2 and env.converted == [2] and env.nlm.create_calls == 1


def test_audio_only_uploads_one_audio_per_part(env):
    res = env.run(audio_only=True)
    assert res["source_count"] == 3 and all(t.endswith("音频") for t in env.nlm.titles)


def test_part_without_visuals_uploads_audio_only_with_a_warning(env):
    env.no_visual = {2}
    res = env.run()
    assert res["source_count"] == 5 and "P02｜第2集｜关键帧PDF" not in env.nlm.titles
    assert any("P2" in w or "P02" in w for w in res["warnings"])


def test_no_part_means_all_parts_are_planned_once(env):
    env.run()
    assert env.plan_calls == 1 and env.converted == [1, 2, 3]


# ------------------------------------------------------------------ idempotency
def test_importing_all_parts_again_reuses_job_and_notebook(env):
    first = env.run()
    second = env.run()
    assert second["reused"] is True and second["job_id"] == first["job_id"]
    assert env.nlm.create_calls == 1 and len(env.nlm.titles) == 6 and env.converted == [1, 2, 3]


def test_force_new_creates_exactly_one_more_notebook_with_the_full_set(env):
    first = env.run()
    again = env.run(force_new=True)
    assert again["job_id"] != first["job_id"] and env.nlm.create_calls == 2
    assert again["source_count"] == 6 and env.store.get(first["job_id"]).superseded is True
    assert again["notebook_id"] != first["notebook_id"] and len(env.nlm.notebooks) == 2
    assert all(len(v) == 6 for v in env.nlm.notebooks.values())  # old notebook untouched, new one complete


def test_all_parts_job_never_reuses_a_single_part_job(env):
    single = env.run(part=1)
    allp = env.run()
    assert allp["job_id"] != single["job_id"] and env.nlm.create_calls == 2


def test_changed_part_list_is_not_silently_reused(env):
    env.run()
    env.infos = make_infos(4)  # the video gained a part
    with pytest.raises(Bili2nbError) as ei:
        env.run()
    assert ei.value.code == "PARTS_CHANGED" and "--force-new" in ei.value.message
    assert env.nlm.create_calls == 1


# ------------------------------------------------------------------ failure & resume
def test_p2_download_failure_creates_no_notebook_and_retry_reuses_p1(env):
    env.convert_fail[2] = [Bili2nbError("MEDIA_ERROR", "boom")]
    with pytest.raises(Bili2nbError) as ei:
        env.run()
    assert ei.value.code == "MEDIA_ERROR" and env.nlm.create_calls == 0 and env.nlm.titles == []
    job_id = ei.value.extra["job_id"]
    assert env.store.get(job_id).status == FAILED
    assert env.converted == [1, 2]  # P3 never attempted
    res = env.importer().retry(job_id)
    assert res["status"] == COMPLETED and env.converted == [1, 2, 2, 3]  # P1 NOT converted again
    assert env.nlm.create_calls == 1 and res["source_count"] == 6


def test_p2_upload_failure_retry_does_not_recreate_or_reupload_p1(env):
    env.nlm.title_failures["P02｜第2集｜音频"] = [Bili2nbError("UPLOAD_FAILED", "x")]
    with pytest.raises(Bili2nbError) as ei:
        env.run()
    job_id = ei.value.extra["job_id"]
    assert env.nlm.create_calls == 1 and env.nlm.titles[:3] == ["P01｜第1集｜音频", "P01｜第1集｜关键帧PDF", "P02｜第2集｜音频"]
    assert ei.value.extra["notebook_id"] == "nb-1"
    n_before = len(env.nlm.titles)
    res = env.importer().retry(job_id)
    assert res["status"] == COMPLETED and env.nlm.create_calls == 1
    assert env.nlm.titles[n_before:] == ["P02｜第2集｜音频", "P02｜第2集｜关键帧PDF", "P03｜第3集｜音频", "P03｜第3集｜关键帧PDF"]
    assert len(env.nlm.notebooks["nb-1"]) == 6  # exactly six sources, no duplicates


def test_ctrl_c_mid_upload_resumes_with_the_unfinished_sources(env):
    def hook(title):
        if title == "P02｜第2集｜关键帧PDF":
            raise KeyboardInterrupt
    env.nlm.upload_hook = hook
    with pytest.raises(Bili2nbError) as ei:
        env.run()
    assert ei.value.code == "INTERRUPTED"
    job_id = ei.value.extra["job_id"]
    assert env.store.get(job_id).status == INTERRUPTED
    env.nlm.upload_hook = None
    n_before = len(env.nlm.titles)
    res = env.importer().retry(job_id)
    assert env.nlm.titles[n_before:][0] == "P02｜第2集｜关键帧PDF" and res["source_count"] == 6
    assert len(env.nlm.notebooks["nb-1"]) == 6 and env.nlm.create_calls == 1


def test_rate_limit_during_part_convert_is_retried_then_succeeds(env):
    env.convert_fail[2] = [Bili2nbError("RATE_LIMITED", "412")]
    res = env.run()
    assert res["status"] == COMPLETED and env.sleeps == [5.0] and env.converted == [1, 2, 2, 3]


def test_source_rejected_by_notebooklm_is_reuploaded_on_retry(env):
    env.nlm.wait_failures["pdf"] = [Bili2nbError("SOURCE_NOT_READY", "bad", detail="SourceProcessingError")]
    with pytest.raises(Bili2nbError) as ei:
        env.run()
    assert ei.value.code == "SOURCE_NOT_READY"
    res = env.importer().retry(ei.value.extra["job_id"])
    assert res["status"] == COMPLETED and res["source_count"] == 6 and env.nlm.create_calls == 1


# ------------------------------------------------------------------ quota
def _pdf_text(env, title):
    from pypdf import PdfReader
    r = PdfReader(str(env.nlm.paths[title]))
    return r, " ".join((r.pages[0].extract_text() or "").split())


def test_over_quota_merges_only_the_pdfs_and_keeps_every_audio_per_part(env):
    env.real_media = True
    env.nlm.limit = 4  # 3 parts x 2 = 6 > 4 -> R=2: the three PDFs become ONE source, audio untouched
    res = env.run()
    plan = res["upload_plan"]
    assert plan["strategy"] == "merged" and plan["merge_mode"] == "minimal" and res["source_count"] == 4
    assert plan["merge_group_sizes"] == {"audio": [1, 1, 1], "pdf": [3]}
    assert env.nlm.titles == ["P01｜第1集｜音频", "P02｜第2集｜音频", "P03｜第3集｜音频", "P01-P03｜关键帧PDF"]
    assert env.nlm.create_calls == 1 and all(res["source_ready"].values())
    r, idx = _pdf_text(env, "P01-P03｜关键帧PDF")
    assert len(r.pages) == 1 + 3 * 3  # index + 3 x (cover + 2 pages)
    assert [o.title for o in r.outline if not isinstance(o, list)][1:] == ["P01｜第1集", "P02｜第2集", "P03｜第3集"]
    # audio was NOT merged: the index points at each part's own audio source instead of a time offset
    assert "对应音频来源" in idx and "P02｜第2集｜音频" in idx and "开始" not in idx
    assert sorted(f.name for f in (env.tmp / "out" / BV / "_merged").iterdir()) == ["P01-P03_keyframes.pdf"]


def test_mixed_merge_pdf_index_uses_offsets_for_merged_audio_and_names_for_the_rest(env):
    env.real_media = True
    env.nlm.limit = 3  # R=3: PDFs -2 (one group), audio -1 -> [P01+P02] merged, P03 stays per part
    res = env.run()
    assert res["upload_plan"]["merge_group_sizes"] == {"audio": [2, 1], "pdf": [3]}
    assert env.nlm.titles == ["P01+P02｜音频", "P03｜第3集｜音频", "P01-P03｜关键帧PDF"]
    assert res["source_count"] == 3 and env.nlm.create_calls == 1
    _r, idx = _pdf_text(env, "P01-P03｜关键帧PDF")
    assert "P01 在音频《P01+P02｜音频》中从 00:00:00 开始" in idx
    assert "P02 在音频《P01+P02｜音频》中从" in idx
    assert "P03 对应音频来源《P03｜第3集｜音频》" in idx
    assert env.nlm.paths["P01+P02｜音频"].is_file()


def test_over_quota_rerun_reuses_the_job_and_parts_change_is_still_detected(env):
    env.real_media = True
    env.nlm.limit = 4
    first = env.run()
    again = env.run()
    assert again["job_id"] == first["job_id"] and env.nlm.create_calls == 1 and len(env.nlm.titles) == 4
    env.infos = make_infos(4)
    with pytest.raises(Bili2nbError) as ei:
        env.run()
    assert ei.value.code == "PARTS_CHANGED" and env.nlm.create_calls == 1


def test_infeasible_when_even_the_minimum_does_not_fit_creates_nothing(env, monkeypatch):
    env.nlm.limit = 4
    monkeypatch.setattr(planner, "GROUP_MAX_BYTES", 1)  # nothing can share a file: 6 sources needed
    with pytest.raises(Bili2nbError) as ei:
        env.run()
    assert ei.value.code == "UPLOAD_PLAN_INFEASIBLE" and env.nlm.create_calls == 0


def test_merged_group_split_by_size_still_one_notebook(env, monkeypatch):
    env.real_media = True
    env.nlm.limit = 4
    from bili2nb import planner
    monkeypatch.setattr(planner, "GROUP_MAX_BYTES", 1)  # force singleton groups where possible
    with pytest.raises(Bili2nbError) as ei:  # 3 audio + 3 pdf = 6 groups > 4: infeasible
        env.run()
    assert ei.value.code == "UPLOAD_PLAN_INFEASIBLE" and env.nlm.create_calls == 0


def test_infeasible_plan_fails_before_any_cloud_write_and_keeps_local_files(env, monkeypatch):
    from bili2nb import planner
    env.nlm.limit = 2
    monkeypatch.setattr(planner, "GROUP_MAX_BYTES", 1)
    monkeypatch.setattr(planner, "estimate_feasibility", lambda *a, **k: None)  # reach the real plan
    with pytest.raises(Bili2nbError) as ei:
        env.run()
    e = ei.value
    assert e.code == "UPLOAD_PLAN_INFEASIBLE" and env.nlm.create_calls == 0 and env.nlm.titles == []
    assert e.extra["source_limit"] == 2 and "job_id" in e.extra
    assert (env.tmp / "out" / BV / "P01_x" / "audio.m4a").is_file()  # converted files are kept


def test_hopeless_selection_is_rejected_before_downloading_anything(env):
    env.infos = [VideoInfo(BV, str(i), i, "t", "u", 3600.0 * 10, f"{URL}?p={i}", 40, f"p{i}", "s")
                 for i in range(1, 41)]  # 400 hours
    env.nlm.limit = 5
    with pytest.raises(Bili2nbError) as ei:
        env.run()
    assert ei.value.code == "UPLOAD_PLAN_INFEASIBLE" and env.converted == [] and env.store.list() == []


def test_quota_unreadable_falls_back_to_the_conservative_default_with_a_warning(env):
    env.nlm.limit_error = Bili2nbError("NETWORK_ERROR", "down")
    res = env.run()
    assert res["upload_plan"]["source_limit"] == 50 and res["upload_plan"]["limit_origin"] == "default"
    assert any("来源上限" in w for w in res["warnings"])


def test_quota_auth_error_is_not_swallowed(env):
    env.nlm.limit_error = Bili2nbError("AUTH_REQUIRED", "login")
    with pytest.raises(Bili2nbError) as ei:
        env.run()
    assert ei.value.code == "AUTH_REQUIRED" and env.nlm.create_calls == 0


def test_plan_is_frozen_once_the_notebook_exists(env):
    env.nlm.title_failures["P02｜第2集｜音频"] = [Bili2nbError("UPLOAD_FAILED", "x")]
    with pytest.raises(Bili2nbError) as ei:
        env.run()
    job_id = ei.value.extra["job_id"]
    frozen = env.store.get(job_id).plan
    env.nlm.limit = 3  # the account quota changes between runs
    env.importer().retry(job_id)
    assert env.store.get(job_id).plan["items"][0]["title"] == frozen["items"][0]["title"]
    assert env.store.get(job_id).plan["strategy"] == "per_part"


# ------------------------------------------------------------------ outputs
def test_json_describes_batch_parts_and_sources_without_secrets(env):
    res = env.run()
    blob = json.dumps(res, ensure_ascii=False)
    assert set(res) >= {"parts", "upload_plan", "sources", "part_count", "notebook_url"}
    assert [s["key"] for s in res["sources"]][:2] == ["P01:audio", "P01:pdf"]
    assert all({"key", "kind", "parts", "title", "source_id", "ready"} <= set(s) for s in res["sources"])
    assert all("info" not in p for p in res["parts"])  # internal state is not leaked
    assert "cookie" not in blob.lower() and "storage_state" not in blob
    assert job_to_dict(env.store.get(res["job_id"]))["source_count"] == 6


def test_local_files_are_cleaned_after_success_unless_kept(env):
    r = env.importer().run(URL, output=None)  # default root, no keep -> cleaned
    assert r["files_cleaned"] is True
    kept = env.importer().run(URL, force_new=True, keep_files=True)
    assert kept["files_cleaned"] is False


def test_batch_manifest_is_written_next_to_the_parts(env):
    env.run()
    doc = json.loads((env.tmp / "out" / BV / "manifest.json").read_text(encoding="utf-8"))
    assert [p["status"] for p in doc["parts"]] == ["done"] * 3


def test_missing_local_file_on_resume_is_reconverted(env):
    env.nlm.title_failures["P01｜第1集｜音频"] = [Bili2nbError("UPLOAD_FAILED", "x")]
    with pytest.raises(Bili2nbError) as ei:
        env.run()
    shutil.rmtree(env.tmp / "out" / BV / "P03_x")  # user deleted P3's files meanwhile
    res = env.importer().retry(ei.value.extra["job_id"])
    assert res["status"] == COMPLETED and env.converted.count(3) == 2
