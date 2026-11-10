"""V1.2 precise local cleanup after a verified import + `bili2nb clean`. Offline, tmp_path only."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bili2nb import cleanup as cl
from bili2nb import cli
from bili2nb.errors import Bili2nbError
from bili2nb.jobs.db import COMPLETED, FAILED, INTERRUPTED, RUNNING, JobStore
from tests.unit import test_import_jobs as sp
from tests.unit.test_multi_import import BV, URL, Env

runner = CliRunner()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BILI2NB_HOME", str(tmp_path / "home"))
    return Env(tmp_path)


def root(env) -> Path:
    return env.tmp / "home" / "output"


def run_default(env, **kw):
    """Import into the DEFAULT output root (no --output): the only case that may be cleaned."""
    return env.importer().run(URL, output=None, **kw)


def user_part(batch: Path, n: int = 9, *, manifest: bool = True) -> Path:
    """A part folder as `bili2nb convert` leaves it (NOT part of the import under test)."""
    d = batch / f"P{n:02d}_other"
    d.mkdir(parents=True)
    (d / "audio.m4a").write_bytes(b"mine" * 50)
    if manifest:
        (d / "manifest.json").write_text(json.dumps({
            "complete": True, "video_id": BV, "cid": str(2000 + n), "part": n, "part_title": "别的",
            "duration_sec": 12.0, "audio": {"file": "audio.m4a"}, "visual": {"status": "skipped"}}), encoding="utf-8")
    return d


# ------------------------------------------------------------------ multi-part success
def test_success_removes_parts_and_empty_batch_folder(env):
    res = run_default(env)
    assert res["status"] == COMPLETED and res["files_cleaned"] is True
    c = res["cleanup"]
    assert c["status"] == "done" and c["freed_bytes"] > 0 and c["kept_other_parts"] == []
    assert f"{BV}/P01_x" in c["removed"] and f"{BV}/manifest.json" in c["removed"] and BV in c["removed"]
    assert not (root(env) / BV).exists()
    assert all(not str(env.tmp) in x for x in c["removed"])  # relative names only
    assert env.store.get(res["job_id"]).files_cleaned is True


def test_merged_files_are_removed_too(env):
    env.real_media = True
    res = run_default(env, merge_sources=True)
    assert res["status"] == COMPLETED and res["upload_plan"]["strategy"] == "merged"
    assert f"{BV}/_merged" in res["cleanup"]["removed"]
    assert not (root(env) / BV).exists()


def test_parts_the_user_converted_but_did_not_select_are_kept(env):
    batch = root(env) / BV
    other = user_part(batch)
    (batch / "notes.txt").write_text("my notes", encoding="utf-8")
    res = run_default(env)
    c = res["cleanup"]
    assert c["status"] == "done" and c["kept_other_parts"] == [f"{BV}/{other.name}"]
    assert (other / "audio.m4a").read_bytes() == b"mine" * 50 and (other / "manifest.json").is_file()
    assert (batch / "notes.txt").read_text(encoding="utf-8") == "my notes"
    assert sorted(p.name for p in batch.iterdir()) == sorted(["manifest.json", "notes.txt", other.name])
    doc = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    assert [p["part"] for p in doc["parts"]] == [9] and doc["parts"][0]["dir"] == other.name
    assert doc["part_count_processed"] == 1


def test_manifest_is_kept_and_reported_when_it_cannot_be_rebuilt(env):
    batch = root(env) / BV
    other = user_part(batch, manifest=False)  # no part manifest -> cannot describe it accurately
    res = run_default(env)
    assert (other / "audio.m4a").is_file() and (batch / "manifest.json").is_file()
    assert any("批次清单" in w for w in res["warnings"])


def test_unrelated_file_keeps_the_batch_folder_but_not_our_manifest(env):
    batch = root(env) / BV
    batch.mkdir(parents=True)
    (batch / "keep.txt").write_text("x", encoding="utf-8")
    run_default(env)
    assert [p.name for p in batch.iterdir()] == ["keep.txt"]


def test_reused_cached_part_folders_are_cleaned_too(env):
    run_default(env, keep_files=True)  # first import keeps its files in the default root
    assert (root(env) / BV / "P01_x" / "audio.m4a").is_file()
    res = run_default(env, force_new=True)  # re-uses those folders, uploads, cleans
    assert res["cleanup"]["status"] == "done" and not (root(env) / BV).exists()


# ------------------------------------------------------------------ when NOT to clean
def test_keep_files_and_output_never_clean(env):
    kept = run_default(env, keep_files=True)
    assert kept["files_cleaned"] is False and kept["cleanup"]["status"] == "kept"
    assert (root(env) / BV / "P01_x").is_dir()
    shutil.rmtree(root(env))
    out = env.run(force_new=True)  # --output
    assert out["files_cleaned"] is False and out["cleanup"]["status"] == "kept"
    assert (env.tmp / "out" / BV / "P01_x" / "audio.m4a").is_file()


def test_failed_and_interrupted_jobs_keep_their_files(env):
    env.convert_fail[2] = [Bili2nbError("MEDIA_ERROR", "boom")]
    with pytest.raises(Bili2nbError) as ei:
        run_default(env)
    assert env.store.get(ei.value.extra["job_id"]).status == FAILED
    assert (root(env) / BV / "P01_x" / "audio.m4a").is_file()

    def hook(title):
        raise KeyboardInterrupt
    env2 = Env(env.tmp / "two", 2)
    env2.nlm.upload_hook = hook
    with pytest.raises(Bili2nbError) as ei2:
        run_default(env2)
    assert env2.store.get(ei2.value.extra["job_id"]).status == INTERRUPTED
    assert (root(env) / BV / "P01_x" / "audio.m4a").is_file()


def test_cloud_check_failure_keeps_files_and_the_job_is_not_completed(env):
    env.nlm.finalize_sources = lambda nb, titles: (sorted(titles)[:1], [])  # one source vanished
    with pytest.raises(Bili2nbError) as ei:
        run_default(env)
    assert ei.value.code == "UPLOAD_FAILED"
    assert env.store.get(ei.value.extra["job_id"]).status == FAILED
    assert (root(env) / BV / "P01_x" / "audio.m4a").is_file()


def test_cleanup_refuses_when_the_cloud_was_not_verified_this_run(env):
    res = run_default(env, keep_files=True)
    job = env.store.get(res["job_id"])
    job = env.store.update(job.job_id, options={**job.options, "keep_files": False})
    imp = env.importer()  # fresh importer: nothing verified
    out = imp._cleanup(job)
    assert out.options["cleanup"]["status"] == "failed_verify" and out.files_cleaned is False
    assert (root(env) / BV / "P01_x").is_dir() and any("云端核对未通过" in w for w in out.warnings)


def test_reused_completed_import_does_not_clean_or_change_anything(env):
    run_default(env, keep_files=True)
    again = run_default(env, keep_files=True)
    assert again["reused"] is True and (root(env) / BV / "P01_x").is_dir()


# ------------------------------------------------------------------ safety
def test_symlinked_and_escaping_paths_are_never_followed(env, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("p", encoding="utf-8")
    res = run_default(env, keep_files=True)
    job = env.store.get(res["job_id"])
    r = root(env)
    # 1. a recorded part folder that is a symlink to an outside folder
    p1 = r / BV / "P01_x"
    shutil.rmtree(p1)
    p1.symlink_to(outside, target_is_directory=True)
    # 2. a recorded part folder that simply lives outside the output root
    parts = [dict(p) for p in job.parts]
    parts[1]["dir"] = str(outside)
    # 3. a symlink INSIDE a part folder pointing outside: removed as a link, target untouched
    (r / BV / "P03_x" / "link").symlink_to(outside, target_is_directory=True)
    job = env.store.update(job.job_id, parts=parts)
    out = cl.cleanup_job_files(job, r)
    assert (outside / "precious.txt").read_text(encoding="utf-8") == "p"
    assert sum("跳过" in w for w in out["warnings"]) == 2
    assert not (r / BV / "P03_x").exists() and (r / BV / "P01_x").is_symlink()


def test_a_failing_removal_is_a_warning_not_an_import_failure(env, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk says no")
    monkeypatch.setattr(cl.shutil, "rmtree", boom)
    res = run_default(env)
    assert res["status"] == COMPLETED and res["files_cleaned"] is False
    assert (root(env) / BV / "P01_x").is_dir() and any("本地清理失败" in w for w in res["warnings"])


def test_an_unexpected_cleanup_error_does_not_fail_the_import(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(cl, "cleanup_job_files", boom)
    res = run_default(env)
    assert res["status"] == COMPLETED and res["files_cleaned"] is False


# ------------------------------------------------------------------ single-part (old path)
@pytest.fixture
def senv(tmp_path, monkeypatch):
    monkeypatch.setenv("BILI2NB_HOME", str(tmp_path / "home"))
    e = sp.Env(tmp_path)
    real = e.convert

    def nested(url, *, output_root, **kw):  # real layout: <root>/<BV>/P01_x
        res = real(url, output_root=Path(output_root) / "BV1GJ411x7h7" / "tmp", **kw)
        dst = Path(output_root) / "BV1GJ411x7h7" / "P01_x"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(Path(res["output_dir"]), dst)
        shutil.rmtree(Path(output_root) / "BV1GJ411x7h7" / "tmp", ignore_errors=True)
        res["output_dir"] = str(dst)
        res["audio_path"] = str(dst / "audio.m4a")
        if res.get("pdf_path"):
            res["pdf_path"] = str(dst / "keyframes.pdf")
        return res
    e.convert = nested
    return e


def test_single_part_is_cleaned_after_cloud_check_and_empty_bv_folder_goes(senv):
    res = senv.importer().run(URL, output=None)
    assert res["cleanup"]["status"] == "done" and res["files_cleaned"] is True
    assert not (senv.tmp / "home" / "output" / "BV1GJ411x7h7").exists()


def test_single_part_keeps_sibling_parts_in_the_same_bv_folder(senv):
    other = senv.tmp / "home" / "output" / "BV1GJ411x7h7" / "P02_mine"
    other.mkdir(parents=True)
    (other / "audio.m4a").write_bytes(b"m")
    res = senv.importer().run(URL, output=None)
    assert res["cleanup"]["status"] == "done" and (other / "audio.m4a").is_file()
    assert res["cleanup"]["kept_other_parts"] == ["BV1GJ411x7h7/P02_mine"]


def test_single_part_cloud_check_failure_keeps_files(senv):
    senv.nlm.list_sources = lambda nb: []  # the notebook shows none of our sources
    res = senv.importer().run(URL, output=None)
    assert res["status"] == COMPLETED and res["files_cleaned"] is False
    assert res["cleanup"]["status"] == "failed_verify" and any("云端核对未通过" in w for w in res["warnings"])
    assert (senv.tmp / "home" / "output" / "BV1GJ411x7h7" / "P01_x" / "audio.m4a").is_file()


def test_single_part_cloud_check_error_keeps_files(senv):
    def err(nb):
        raise Bili2nbError("UPLOAD_FAILED", "x")
    senv.nlm.list_sources = err
    res = senv.importer().run(URL, output=None)
    assert res["cleanup"]["status"] == "failed_verify" and res["files_cleaned"] is False


# ------------------------------------------------------------------ bili2nb clean
@pytest.fixture
def cenv(tmp_path, monkeypatch):
    monkeypatch.setenv("BILI2NB_HOME", str(tmp_path / "home"))
    return tmp_path


def add_job(tmp, name, *, status, superseded=False, files_cleaned=False, options=None, shared=None):
    store = JobStore(tmp / "home" / "jobs.sqlite")
    j = store.create(video_key=f"BV_{name}", account_key="a", mode="auto", url="u", options=options or {},
                     video_id=f"BV{name}", video_title=f"视频{name}")
    d = shared or (tmp / "home" / "output" / f"BV{name}" / "P01_x")
    d.mkdir(parents=True, exist_ok=True)
    (d / "audio.m4a").write_bytes(b"z" * 1000)
    store.update(j.job_id, status=status, superseded=superseded, files_cleaned=files_cleaned,
                 output_dir=str(d.parent), parts=[{"part": 1, "dir": str(d)}])
    return j.job_id, d


def invoke(*args):
    return runner.invoke(cli.app, ["clean", *args])


def test_clean_dry_run_lists_only_safe_candidates_and_deletes_nothing(cenv):
    old, d_old = add_job(cenv, "A", status=FAILED, superseded=True)
    latest, d_latest = add_job(cenv, "B", status=FAILED)
    _run, d_run = add_job(cenv, "C", status=RUNNING)
    done, d_done = add_job(cenv, "D", status=COMPLETED)
    _kept, d_kept = add_job(cenv, "E", status=COMPLETED, options={"keep_files": True})
    _outp, d_outp = add_job(cenv, "F", status=COMPLETED, options={"output": str(cenv / "mine")})
    _cleaned, _ = add_job(cenv, "G", status=COMPLETED, files_cleaned=True)
    r = invoke("--json")
    assert r.exit_code == 0, r.output
    doc = json.loads(r.stdout)  # stdout is ONE json document
    ids = {e["job_id"] for e in doc["jobs"]}
    assert ids == {old, done} and doc["dry_run"] is True and doc["total_bytes"] == 2000
    assert all(d.is_dir() for d in (d_old, d_latest, d_run, d_done, d_kept, d_outp))
    text = invoke().output
    assert old in text and done in text and latest not in text and "预览" in text


def test_clean_yes_deletes_candidates_and_marks_files_cleaned(cenv):
    old, d_old = add_job(cenv, "A", status=FAILED, superseded=True)
    latest, d_latest = add_job(cenv, "B", status=FAILED)
    done, d_done = add_job(cenv, "D", status=INTERRUPTED, superseded=True)
    r = invoke("--yes", "--json")
    doc = json.loads(r.stdout)
    assert doc["dry_run"] is False and doc["total_bytes"] == 2000
    assert not d_old.exists() and not d_done.exists() and d_latest.is_dir()
    assert not (cenv / "home" / "output" / "BVA").exists()  # emptied batch folder removed
    store = JobStore(cenv / "home" / "jobs.sqlite")
    assert store.get(old).files_cleaned and store.get(done).files_cleaned and not store.get(latest).files_cleaned


def test_clean_never_removes_a_folder_a_live_job_still_uses(cenv):
    shared = cenv / "home" / "output" / "BVS" / "P01_x"
    _old, _ = add_job(cenv, "S", status=FAILED, superseded=True, shared=shared)
    _latest, _ = add_job(cenv, "T", status=FAILED, shared=shared)  # retry will need it
    r = invoke("--yes", "--json")
    assert json.loads(r.stdout)["jobs"] == [] and (shared / "audio.m4a").is_file()


def test_clean_with_job_id_can_delete_the_latest_failed_but_not_running_or_kept(cenv):
    latest, d_latest = add_job(cenv, "B", status=FAILED)
    run, d_run = add_job(cenv, "C", status=RUNNING)
    kept, d_kept = add_job(cenv, "E", status=FAILED, options={"keep_files": True})
    for jid, d in ((run, d_run), (kept, d_kept)):
        doc = json.loads(invoke(jid, "--yes", "--json").stdout)
        assert doc["jobs"][0]["skipped"] and d.is_dir()
    doc = json.loads(invoke(latest, "--yes", "--json").stdout)
    assert doc["jobs"][0]["job_id"] == latest and not d_latest.exists()


def test_clean_unknown_job_id_is_a_clean_error(cenv):
    r = invoke("nope", "--json")
    assert r.exit_code != 0 and "JOB_NOT_FOUND" in r.output


def test_clean_keeps_a_completed_job_whose_cloud_check_failed_unless_named(cenv):
    jid, d = add_job(cenv, "V", status=COMPLETED, options={"cleanup": {"status": "failed_verify"}})
    assert json.loads(invoke("--yes", "--json").stdout)["jobs"] == [] and d.is_dir()
    assert json.loads(invoke(jid, "--yes", "--json").stdout)["jobs"][0]["job_id"] == jid and not d.exists()


def test_clean_does_not_touch_anything_outside_the_output_root(cenv):
    outside = cenv / "elsewhere" / "P01_x"
    _jid, _ = add_job(cenv, "O", status=FAILED, superseded=True, shared=outside)
    doc = json.loads(invoke("--yes", "--json").stdout)
    assert doc["jobs"] == [] and (outside / "audio.m4a").is_file()


def test_clean_with_nothing_to_do_says_so(cenv):
    assert "没有可清理" in invoke().output
