"""Gate 3 logic tests: idempotency, checkpoint/resume, bounded backoff, interrupt, JSON.

These use a FAKE NotebookLM (in-memory). They prove the orchestration logic only; they are
NOT evidence that the real NotebookLM import works (that is the opt-in live run).
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bili2nb import cli
from bili2nb.bilibili.downloader import VideoInfo
from bili2nb.errors import Bili2nbError
from bili2nb.importer import MAX_ATTEMPTS, Importer, job_to_dict
from bili2nb.jobs.db import COMPLETED, FAILED, INTERRUPTED, JobStore
from tests.conftest import plain

URL = "https://www.bilibili.com/video/BV1GJ411x7h7"
INFO = VideoInfo("BV1GJ411x7h7", "999", 1, "测试视频", "作者", 120.0, URL)


class FakeNotebookLM:
    """In-memory stand-in for NotebookAdapter with failure injection."""

    def __init__(self):
        self.account_key = "default"
        self.notebooks: dict[str, dict[str, str]] = {}  # nb_id -> {source_id: status}
        self.create_calls = 0
        self.upload_calls: list[str] = []  # file names, in order
        self.auth_calls = 0
        self.auth_error: Bili2nbError | None = None
        self.upload_failures: dict[str, list[BaseException]] = {}  # filename -> queue
        self.wait_failures: dict[str, list[BaseException]] = {}  # source title kind -> queue
        self.source_kind: dict[str, str] = {}  # source_id -> "audio"/"pdf"
        self.pending_orphans: set[str] = set()  # ids a failed upload could not delete
        self.undeletable: set[str] = set()
        self.cleanup_calls: list[tuple[str, list[str]]] = []
        self.owned_seen: list[tuple[str, ...]] = []
        self._n = 0

    def check_auth(self):
        self.auth_calls += 1
        if self.auth_error:
            raise self.auth_error
        return len(self.notebooks)

    def create_notebook(self, title):
        self.create_calls += 1
        self._n += 1
        nb = f"nb-{self._n}"
        self.notebooks[nb] = {}
        return nb

    def notebook_exists(self, nb):
        return nb in self.notebooks

    def drain_orphans(self):
        out, self.pending_orphans = sorted(self.pending_orphans), set()
        return out

    def cleanup_owned(self, nb, ids):
        self.cleanup_calls.append((nb, list(ids)))
        return [i for i in ids if i in self.undeletable]

    def upload_file(self, nb, path, title=None, *, owned=()):
        name = Path(path).name
        self.upload_calls.append(name)
        self.owned_seen.append(tuple(owned))
        q = self.upload_failures.get(name)
        if q:
            raise q.pop(0)
        self._n += 1
        sid = f"src-{self._n}"
        self.notebooks[nb][sid] = "READY"
        self.source_kind[sid] = "pdf" if name.endswith(".pdf") else "audio"
        return sid

    def wait_ready(self, nb, sid, timeout=600.0):
        q = self.wait_failures.get(self.source_kind[sid])
        if q:
            raise q.pop(0)

    def list_sources(self, nb):
        return []


class Env:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.store = JobStore(tmp_path / "jobs.sqlite")
        self.nlm = FakeNotebookLM()
        self.convert_calls = 0
        self.fetch_calls = 0
        self.sleeps: list[float] = []
        self.logs: list[str] = []
        self.black_visual = False
        self.info = INFO

    def fetch(self, ref):
        self.fetch_calls += 1
        return self.info

    def convert(self, url, *, output_root, frames_mode, part, audio_only, log, info=None):
        self.convert_calls += 1
        d = Path(output_root) / self.info.video_key
        d.mkdir(parents=True, exist_ok=True)
        (d / "audio.m4a").write_bytes(b"a" * 100)
        res = {"video_title": self.info.title, "duration_sec": self.info.duration,
               "output_dir": str(d), "audio_path": str(d / "audio.m4a"), "warnings": []}
        if audio_only or frames_mode == "none":
            res["visual"] = {"status": "skipped", "reason": "audio-only (--frames none / --audio-only)"}
        elif self.black_visual:
            res["visual"] = {"status": "visual_skipped", "reason": "未找到有意义的画面"}
            res["warnings"].append("visual_skipped: 未找到有意义的画面")
        else:
            (d / "keyframes.pdf").write_bytes(b"%PDF-fake")
            res["pdf_path"] = str(d / "keyframes.pdf")
            res["visual"] = {"status": "ok", "frame_count": 3, "pages": 4}
        return res

    def importer(self):
        return Importer(self.store, self.nlm, convert_fn=self.convert, fetch_info_fn=self.fetch,
                        sleep=self.sleeps.append, log=self.logs.append, backoff_base=5.0)

    def run(self, **kw):
        kw.setdefault("output", str(self.tmp / "out"))
        return self.importer().run(URL, **kw)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BILI2NB_HOME", str(tmp_path / "home"))
    return Env(tmp_path)


# ------------------------------------------------------------------ happy path
def test_import_creates_notebook_and_two_ready_sources(env):
    res = env.run()
    assert res["status"] == COMPLETED and res["stage"] == "done"
    assert res["notebook_id"] == "nb-1" and res["notebook_url"].endswith("/notebook/nb-1")
    assert set(res["source_ids"]) == {"audio", "pdf"}
    assert len(set(res["source_ids"].values())) == 2  # distinct source ids
    assert res["source_ready"] == {"audio": True, "pdf": True} and res["source_count"] == 2
    assert env.nlm.create_calls == 1 and env.nlm.upload_calls == ["audio.m4a", "keyframes.pdf"]
    job = env.store.get(res["job_id"])
    assert job.notebook_id == "nb-1" and job.sources == res["source_ids"]  # persisted
    assert res["reused"] is False and "audio_path" in res and "pdf_path" in res  # kept (--output)


def test_auth_preflight_happens_before_any_download(env):
    env.nlm.auth_error = Bili2nbError("AUTH_REQUIRED", "need login")
    with pytest.raises(Bili2nbError) as e:
        env.run()
    assert e.value.code == "AUTH_REQUIRED"
    assert env.fetch_calls == 0 and env.convert_calls == 0  # nothing downloaded
    assert env.nlm.create_calls == 0 and env.store.list() == []  # no job, no notebook


def test_local_files_cleaned_after_success_unless_kept(env):
    r = env.importer().run(URL)  # no --output, no --keep-files -> default cache dir, cleaned
    assert r["files_cleaned"] is True and "audio_path" not in r
    env2_dir = Path(r["video_key"])  # noqa: F841 - key only
    kept = env.importer().run(URL, force_new=True, keep_files=True)
    assert kept["files_cleaned"] is False and Path(kept["audio_path"]).is_file()


def test_audio_only_has_one_source_and_says_so(env):
    res = env.run(audio_only=True)
    assert res["source_count"] == 1 and set(res["source_ids"]) == {"audio"}
    assert res["mode"] == "audio-only"
    assert any("仅有音频一个来源" in w for w in res["warnings"])
    assert env.nlm.upload_calls == ["audio.m4a"]


def test_visual_skipped_yields_audio_only_with_explicit_warning(env):
    env.black_visual = True
    res = env.run()
    assert res["source_count"] == 1 and res["visual"]["status"] == "visual_skipped"
    assert any("仅有音频一个来源" in w for w in res["warnings"])


# ------------------------------------------------------------------ idempotency
def test_second_import_of_same_video_reuses_job_and_notebook(env):
    first = env.run()
    second = env.run()
    assert second["reused"] is True and second["job_id"] == first["job_id"]
    assert second["notebook_id"] == first["notebook_id"]
    assert env.nlm.create_calls == 1 and env.nlm.upload_calls == ["audio.m4a", "keyframes.pdf"]
    assert env.convert_calls == 1  # no re-conversion either
    assert len(env.store.list()) == 1


def test_force_new_creates_another_notebook(env):
    first = env.run()
    again = env.run(force_new=True)
    assert again["job_id"] != first["job_id"] and again["notebook_id"] != first["notebook_id"]
    assert env.nlm.create_calls == 2
    assert env.store.get(first["job_id"]).superseded is True
    third = env.run()  # default run now returns the NEWEST job, not the superseded one
    assert third["job_id"] == again["job_id"] and third["reused"] is True


def test_different_mode_or_account_is_a_different_job(env):
    a = env.run()
    b = env.run(audio_only=True)
    assert a["job_id"] != b["job_id"] and a["notebook_id"] != b["notebook_id"]
    env.nlm.account_key = "other"
    c = env.run()
    assert c["job_id"] not in (a["job_id"], b["job_id"]) and env.nlm.create_calls == 3


def test_deleted_notebook_makes_new_job_for_completed_video(env):
    a = env.run()
    env.nlm.notebooks.pop(a["notebook_id"])  # user deleted it in NotebookLM
    b = env.run()
    assert b["job_id"] != a["job_id"] and b["reused"] is False and env.nlm.create_calls == 2


# ------------------------------------------------------------------ failure + resume
def test_pdf_upload_failure_then_retry_does_not_duplicate_anything(env):
    env.nlm.upload_failures["keyframes.pdf"] = [Bili2nbError("UPLOAD_FAILED", "disconnected")]
    with pytest.raises(Bili2nbError) as e:
        env.run()
    assert e.value.code == "UPLOAD_FAILED"
    jid = e.value.extra["job_id"]
    assert e.value.extra["notebook_id"] == "nb-1" and set(e.value.extra["source_ids"]) == {"audio"}
    assert e.value.extra["retry_hint"] == f"bili2nb retry {jid}"
    job = env.store.get(jid)
    assert job.status == FAILED and job.error_code == "UPLOAD_FAILED" and job.stage == "notebook_created"

    res = env.importer().retry(jid)
    assert res["status"] == COMPLETED and res["job_id"] == jid
    assert env.nlm.create_calls == 1  # SAME notebook reused
    assert env.nlm.upload_calls == ["audio.m4a", "keyframes.pdf", "keyframes.pdf"]  # audio NOT re-uploaded
    assert env.convert_calls == 1  # files still present: no re-convert
    assert len(env.nlm.notebooks["nb-1"]) == 2  # exactly two sources


def test_rerun_import_resumes_unfinished_job_instead_of_new_notebook(env):
    env.nlm.upload_failures["keyframes.pdf"] = [Bili2nbError("UPLOAD_FAILED", "x")]
    with pytest.raises(Bili2nbError):
        env.run()
    res = env.run()  # same command again
    assert res["status"] == COMPLETED and env.nlm.create_calls == 1 and len(env.store.list()) == 1


def test_ctrl_c_marks_interrupted_and_retry_resumes(env):
    env.nlm.upload_failures["keyframes.pdf"] = [KeyboardInterrupt()]
    with pytest.raises(Bili2nbError) as e:
        env.run()
    assert e.value.code == "INTERRUPTED" and "bili2nb retry" in e.value.message
    jid = e.value.extra["job_id"]
    assert env.store.get(jid).status == INTERRUPTED
    res = env.importer().retry(jid)
    assert res["status"] == COMPLETED and env.nlm.create_calls == 1
    assert env.nlm.upload_calls.count("audio.m4a") == 1


def test_not_ready_timeout_keeps_ids_and_retry_only_waits(env):
    env.nlm.wait_failures["pdf"] = [Bili2nbError("SOURCE_NOT_READY", "timeout", detail="SourceTimeoutError")]
    with pytest.raises(Bili2nbError) as e:
        env.run()
    assert e.value.code == "SOURCE_NOT_READY" and set(e.value.extra["source_ids"]) == {"audio", "pdf"}
    jid = e.value.extra["job_id"]
    assert env.store.get(jid).ready == {"audio": True}  # audio READY already checkpointed
    res = env.importer().retry(jid)
    assert res["status"] == COMPLETED and env.nlm.upload_calls == ["audio.m4a", "keyframes.pdf"]


def test_source_rejected_by_notebooklm_is_reuploaded_on_retry(env):
    env.nlm.wait_failures["pdf"] = [Bili2nbError("SOURCE_NOT_READY", "bad", detail="SourceProcessingError")]
    with pytest.raises(Bili2nbError) as e:
        env.run()
    jid = e.value.extra["job_id"]
    assert "pdf" not in env.store.get(jid).sources and "audio" in env.store.get(jid).sources
    res = env.importer().retry(jid)
    assert res["status"] == COMPLETED
    assert env.nlm.upload_calls == ["audio.m4a", "keyframes.pdf", "keyframes.pdf"]
    assert env.nlm.create_calls == 1


def test_files_lost_between_failure_and_retry_are_regenerated(env):
    env.nlm.upload_failures["keyframes.pdf"] = [Bili2nbError("UPLOAD_FAILED", "x")]
    with pytest.raises(Bili2nbError) as e:
        env.run()
    jid = e.value.extra["job_id"]
    for p in (env.tmp / "out").rglob("*.pdf"):
        p.unlink()
    res = env.importer().retry(jid)
    assert res["status"] == COMPLETED and env.convert_calls == 2 and env.nlm.create_calls == 1
    assert env.nlm.upload_calls.count("audio.m4a") == 1


def test_resume_with_notebook_gone_fails_clearly(env):
    env.nlm.upload_failures["keyframes.pdf"] = [Bili2nbError("UPLOAD_FAILED", "x")]
    with pytest.raises(Bili2nbError) as e:
        env.run()
    env.nlm.notebooks.clear()
    with pytest.raises(Bili2nbError) as e2:
        env.importer().retry(e.value.extra["job_id"])
    assert e2.value.code == "NOTEBOOK_CREATE_FAILED" and "--force-new" in e2.value.message
    assert env.nlm.create_calls == 1  # never silently creates a replacement


def test_retry_unknown_job_and_completed_job(env):
    with pytest.raises(Bili2nbError) as e:
        env.importer().retry("nope")
    assert e.value.code == "JOB_NOT_FOUND"
    done = env.run()
    again = env.importer().retry(done["job_id"])
    assert again["reused"] is True and env.nlm.create_calls == 1


def test_retry_also_preflights_auth(env):
    env.nlm.upload_failures["keyframes.pdf"] = [Bili2nbError("UPLOAD_FAILED", "x")]
    with pytest.raises(Bili2nbError) as e:
        env.run()
    env.nlm.auth_error = Bili2nbError("AUTH_REQUIRED", "expired")
    with pytest.raises(Bili2nbError) as e2:
        env.importer().retry(e.value.extra["job_id"])
    assert e2.value.code == "AUTH_REQUIRED"


# ------------------------------------------------------------------ bounded backoff
def test_rate_limit_backs_off_then_succeeds(env):
    env.nlm.upload_failures["audio.m4a"] = [Bili2nbError("RATE_LIMITED", "429")] * 2
    res = env.run()
    assert res["status"] == COMPLETED and env.sleeps == [5.0, 15.0]  # exponential, bounded
    assert env.nlm.upload_calls.count("audio.m4a") == 3


def test_persistent_rate_limit_stops_after_limit_and_stays_resumable(env):
    env.nlm.upload_failures["audio.m4a"] = [Bili2nbError("RATE_LIMITED", "429")] * 10
    with pytest.raises(Bili2nbError) as e:
        env.run()
    assert e.value.code == "RATE_LIMITED"
    assert env.nlm.upload_calls.count("audio.m4a") == MAX_ATTEMPTS == 5  # bounded, not infinite
    assert env.sleeps == [5.0, 15.0, 45.0, 135.0]  # exponential backoff, N-1 waits
    assert env.store.get(e.value.extra["job_id"]).status == FAILED


def test_ambiguous_upload_failure_is_not_blindly_retried(env):
    env.nlm.upload_failures["audio.m4a"] = [Bili2nbError("UPLOAD_FAILED", "disconnect")]
    with pytest.raises(Bili2nbError):
        env.run()
    assert env.nlm.upload_calls.count("audio.m4a") == 1 and env.sleeps == []


def test_unexpected_exception_is_recorded_as_internal_not_lost(env):
    env.nlm.upload_failures["audio.m4a"] = [RuntimeError("boom")]
    with pytest.raises(Bili2nbError) as e:
        env.run()
    assert e.value.code == "INTERNAL" and env.store.get(e.value.extra["job_id"]).status == FAILED


# ------------------------------------------------------------------ job store
def test_job_store_persists_across_instances_and_is_private(tmp_path):
    p = tmp_path / "j.sqlite"
    s1 = JobStore(p)
    j = s1.create(video_key="k", account_key="a", mode="auto", url="u", options={"x": 1})
    s1.update(j.job_id, notebook_id="nb", sources={"audio": "s1"}, warnings=["w"], files_cleaned=True)
    got = JobStore(p).get(j.job_id)
    assert got.notebook_id == "nb" and got.sources == {"audio": "s1"} and got.warnings == ["w"]
    assert got.files_cleaned is True and got.options == {"x": 1}
    if sys.platform != "win32":  # Windows has no POSIX modes; the profile dir ACL protects it
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert JobStore(p).find_active("k", "a", "auto").job_id == j.job_id
    assert JobStore(p).find_active("k", "a", "none") is None


def test_job_dict_has_no_secret_fields(env):
    res = env.run()
    blob = json.dumps(res).lower()
    for bad in ("cookie", "token", "authorization", "sid", "csrf", "password"):
        assert bad not in blob.replace("source_ids", "").replace("source_ready", "").replace("source_count", "")
    assert set(job_to_dict(env.store.get(res["job_id"]))) >= {
        "job_id", "status", "video_id", "video_title", "notebook_id", "notebook_url",
        "source_ids", "warnings", "duration_sec"}


# ------------------------------------------------------------------ CLI
runner = CliRunner()


@pytest.fixture
def cli_env(env, monkeypatch):
    monkeypatch.setattr(cli, "_make_importer", lambda: env.importer())
    monkeypatch.setattr("bili2nb.jobs.db.JobStore", lambda *a, **k: env.store)  # status/jobs cmds
    return env


def test_cli_import_json_success_single_object(cli_env):
    r = runner.invoke(cli.app, ["import", URL, "--json", "--output", str(cli_env.tmp / "o")])
    assert r.exit_code == 0, r.output
    obj = json.loads(r.stdout)  # whole stdout is exactly one JSON object
    for k in ("job_id", "status", "notebook_id", "notebook_url", "source_ids", "warnings", "video_title"):
        assert k in obj
    assert obj["status"] == "completed" and len(obj["source_ids"]) == 2
    assert "阶段" not in r.stdout  # progress/diagnostics are on stderr only


def test_cli_import_failure_json_has_job_and_codes(cli_env):
    cli_env.nlm.upload_failures["keyframes.pdf"] = [Bili2nbError("UPLOAD_FAILED", "disc")]
    r = runner.invoke(cli.app, ["import", URL, "--json", "--output", str(cli_env.tmp / "o")])
    assert r.exit_code == 22
    obj = json.loads(r.stdout)
    assert obj["status"] == "failed" and obj["error_code"] == "UPLOAD_FAILED"
    assert obj["job_id"] and obj["notebook_id"] and obj["source_ids"].keys() == {"audio"}
    assert obj["retry_hint"].startswith("bili2nb retry ")


def test_cli_auth_required_is_non_interactive_in_json_mode(cli_env):
    cli_env.nlm.auth_error = Bili2nbError("AUTH_REQUIRED", "need login")
    r = runner.invoke(cli.app, ["import", URL, "--json"], input="y\n")  # would "accept" a prompt
    assert r.exit_code == 20 and json.loads(r.stdout)["error_code"] == "AUTH_REQUIRED"
    assert "浏览器登录" not in r.output  # never prompted


def test_cli_auth_required_never_prompts_when_not_a_tty(cli_env):
    cli_env.nlm.auth_error = Bili2nbError("AUTH_REQUIRED", "need login")
    r = runner.invoke(cli.app, ["import", URL], input="y\n")  # CliRunner stdin is not a TTY
    assert r.exit_code == 20 and "bili2nb auth login" not in r.stdout


def test_cli_status_retry_jobs_commands(cli_env):
    cli_env.nlm.upload_failures["keyframes.pdf"] = [Bili2nbError("UPLOAD_FAILED", "disc")]
    bad = runner.invoke(cli.app, ["import", URL, "--json", "--output", str(cli_env.tmp / "o")])
    jid = json.loads(bad.stdout)["job_id"]

    st = runner.invoke(cli.app, ["status", jid, "--json"])
    assert st.exit_code == 0
    s = json.loads(st.stdout)
    assert s["status"] == "failed" and s["error_code"] == "UPLOAD_FAILED" and s["stage"] == "notebook_created"

    jl = runner.invoke(cli.app, ["jobs", "--json"])
    assert [j["job_id"] for j in json.loads(jl.stdout)] == [jid]
    assert jid in runner.invoke(cli.app, ["jobs"]).stdout

    rt = runner.invoke(cli.app, ["retry", jid, "--json"])
    assert rt.exit_code == 0 and json.loads(rt.stdout)["status"] == "completed"
    assert cli_env.nlm.create_calls == 1

    nf = runner.invoke(cli.app, ["status", "nope", "--json"])
    assert nf.exit_code == 30 and json.loads(nf.stdout)["error_code"] == "JOB_NOT_FOUND"
    assert runner.invoke(cli.app, ["retry", "nope", "--json"]).exit_code == 30


def test_cli_ctrl_c_json(cli_env):
    cli_env.nlm.upload_failures["keyframes.pdf"] = [KeyboardInterrupt()]
    r = runner.invoke(cli.app, ["import", URL, "--json", "--output", str(cli_env.tmp / "o")])
    obj = json.loads(r.stdout)
    assert r.exit_code == 130 and obj["error_code"] == "INTERRUPTED" and obj["job_id"]


def test_cli_help_shows_only_implemented_options():
    out = plain(runner.invoke(cli.app, ["import", "--help"]).output)
    for opt in ("--frames", "--output", "--part", "--audio-only", "--keep-files", "--force-new", "--json"):
        assert opt in out
    assert "--no-wait" not in out
    top = plain(runner.invoke(cli.app, ["--help"]).output)
    for cmd in ("convert", "import", "status", "retry", "jobs", "auth"):
        assert cmd in top


def test_network_errors_are_retried_up_to_the_bound_then_job_stays_resumable(env):
    env.nlm.upload_failures["audio.m4a"] = [Bili2nbError("NETWORK_ERROR", "reset")] * 4
    res = env.run()  # 4 failures then success = within the 5-attempt bound
    assert res["status"] == COMPLETED and len(env.sleeps) == 4
    env2 = Env(env.tmp / "b")
    env2.nlm.upload_failures["audio.m4a"] = [Bili2nbError("NETWORK_ERROR", "reset")] * 99
    with pytest.raises(Bili2nbError) as e:
        env2.run()
    assert e.value.code == "NETWORK_ERROR" and env2.nlm.upload_calls.count("audio.m4a") == MAX_ATTEMPTS
    assert env2.store.get(e.value.extra["job_id"]).status == FAILED
    assert env2.nlm.create_calls == 1


# ---- Gate 4 fault-injection matrix: NotebookLM 5xx / hung upload / auth loss, all bounded
@pytest.mark.parametrize("exc_factory,code", [
    (lambda: __import__("notebooklm").ServerError("HTTP 503"), "NETWORK_ERROR"),
    (lambda: TimeoutError("upload hung"), "NETWORK_ERROR"),
    (lambda: __import__("notebooklm").RateLimitError("429"), "RATE_LIMITED"),
])
def test_sdk_failure_classes_map_to_retryable_codes(exc_factory, code):
    from bili2nb.notebook.adapter import map_sdk_error

    assert map_sdk_error(exc_factory(), default="UPLOAD_FAILED").code == code


@pytest.mark.parametrize("code", ["NETWORK_ERROR", "RATE_LIMITED"])
def test_persistent_retryable_failure_is_bounded_and_job_resumable(env, code):
    """5xx / stuck upload surface as NETWORK_ERROR; never an infinite loop, job kept for retry."""
    env.nlm.upload_failures["audio.m4a"] = [Bili2nbError(code, "injected")] * 50
    with pytest.raises(Bili2nbError) as e:
        env.run()
    assert env.nlm.upload_calls.count("audio.m4a") == MAX_ATTEMPTS
    job = env.store.get(e.value.extra["job_id"])
    assert job.status == FAILED and job.notebook_id  # notebook id kept -> retry reuses it
    env.nlm.upload_failures.clear()
    res = env.importer().retry(job.job_id)
    assert res["status"] == COMPLETED and env.nlm.create_calls == 1  # no duplicate notebook


def test_non_retryable_error_is_not_backed_off(env):
    env.nlm.upload_failures["audio.m4a"] = [Bili2nbError("AUTH_REQUIRED", "expired")]
    with pytest.raises(Bili2nbError) as e:
        env.run()
    assert e.value.code == "AUTH_REQUIRED" and env.sleeps == []


# ---- V1.1: ownership evidence for orphan cleanup survives process restarts
def test_orphans_reported_by_a_failed_upload_are_persisted_and_passed_back_on_retry(env):
    env.nlm.pending_orphans = {"stuck-1"}
    env.nlm.upload_failures["audio.m4a"] = [Bili2nbError("UPLOAD_FAILED", "disconnect")]
    with pytest.raises(Bili2nbError) as e:
        env.run()
    job = env.store.get(e.value.extra["job_id"])
    assert job.status == FAILED and job.orphans == ["stuck-1"]  # persisted evidence
    assert job_to_dict(job)["orphan_source_ids"] == ["stuck-1"]
    env.nlm.upload_failures.clear()
    res = env.importer().retry(job.job_id)
    assert res["status"] == COMPLETED
    assert env.nlm.cleanup_calls == [(job.notebook_id, ["stuck-1"])]  # cleaned by recorded id only
    assert env.store.get(job.job_id).orphans == []  # cleared once deleted


def test_orphans_that_cannot_be_deleted_stay_recorded(env):
    env.nlm.pending_orphans = {"stuck-9"}
    env.nlm.undeletable = {"stuck-9"}
    env.nlm.upload_failures["audio.m4a"] = [Bili2nbError("UPLOAD_FAILED", "disconnect")]
    with pytest.raises(Bili2nbError) as e:
        env.run()
    jid = e.value.extra["job_id"]
    env.nlm.upload_failures.clear()
    env.importer().retry(jid)
    assert env.store.get(jid).orphans == ["stuck-9"]  # still ours, retried next time


def test_no_cleanup_call_when_the_job_recorded_no_orphans(env):
    env.run()
    assert env.nlm.cleanup_calls == []  # nothing is deleted without recorded evidence
