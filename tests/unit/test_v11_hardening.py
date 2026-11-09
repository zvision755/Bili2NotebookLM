"""V1.1 hardening: SDK compatibility guard, scoped connection control, orphan ownership.

Every scenario here is a real failure mode observed live (Gate 1/3/4) or a safety property the
maintainers promised: never delete a source we cannot prove is ours, never blind-retry a
non-idempotent write, never report an upload as successful when it was not.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import notebooklm as nl
import pytest

from bili2nb.errors import EXIT_CODES, Bili2nbError
from bili2nb.notebook import adapter as ad
from bili2nb.notebook import sdk_compat


def _src(id, title, status):
    return NS(id=id, title=title, status=NS(name=status))


class FakeSources:
    """Minimal SDK `sources` API with failure injection and delayed visibility."""

    def __init__(self, existing=(), add=None):
        self.items = list(existing)
        self.add = add or (lambda f, p: _src("new", p.name, "PROCESSING"))
        self.add_calls = 0
        self.deleted: list[str] = []
        self.undeletable: set[str] = set()
        self.hidden: dict[str, int] = {}  # id -> number of list() calls before it becomes visible
        self.list_calls = 0

    async def list(self, nb):
        self.list_calls += 1
        out = []
        for s in self.items:
            need = self.hidden.get(s.id, 0)
            if self.list_calls > need:
                out.append(s)
        return out

    async def add_file(self, nb, path, title=None, wait=False):
        self.add_calls += 1
        return self.add(self, path)

    async def delete(self, nb, sid):
        if sid in self.undeletable:
            raise nl.NetworkError("delete dropped")
        self.deleted.append(sid)
        self.items = [x for x in self.items if x.id != sid]


def adapter_with(fake, monkeypatch):
    a = ad.NotebookAdapter()

    class C:
        sources = fake

        async def __aenter__(self):
            return self

        async def __aexit__(self, *x):
            return False

    monkeypatch.setattr(a, "_client", lambda **kw: C())
    return a


PDF = Path("/x/keyframes.pdf")


# =============================================================== orphan OWNERSHIP
def test_foreign_same_name_stuck_source_is_never_deleted(monkeypatch):
    """The user uploaded their own keyframes.pdf (stuck/ERROR) before us: not ours, not touched."""
    fake = FakeSources([_src("mine_not", "keyframes.pdf", "ERROR"), _src("also", "keyframes.pdf", "PREPARING")])
    a = adapter_with(fake, monkeypatch)
    assert a.upload_file("nb", PDF) == "new"
    assert fake.deleted == []  # no ownership evidence -> hands off
    assert a.drain_orphans() == []


def test_foreign_stuck_source_untouched_even_when_upload_fails(monkeypatch):
    def lost(f, p):
        raise nl.NetworkError("closed")

    fake = FakeSources([_src("foreign", "keyframes.pdf", "ERROR")], add=lost)
    a = adapter_with(fake, monkeypatch)
    with pytest.raises(Bili2nbError) as e:
        a.upload_file("nb", PDF, reconcile_sec=0)
    assert e.value.code == "NETWORK_ERROR" and fake.deleted == []


def test_owned_stuck_orphan_from_a_previous_attempt_is_removed(monkeypatch):
    fake = FakeSources([_src("old1", "keyframes.pdf", "PREPARING"), _src("foreign", "keyframes.pdf", "ERROR")])
    a = adapter_with(fake, monkeypatch)
    assert a.upload_file("nb", PDF, owned=["old1"]) == "new"
    assert fake.deleted == ["old1"]  # only the id the job recorded


def test_owned_id_that_became_usable_is_not_deleted(monkeypatch):
    """A recorded orphan can finish processing later; deleting it would destroy a good source."""
    fake = FakeSources([_src("late", "keyframes.pdf", "READY")])
    a = adapter_with(fake, monkeypatch)
    assert a.upload_file("nb", PDF, owned=["late"]) == "late"  # reused, not deleted
    assert fake.deleted == [] and fake.add_calls == 0


def test_twin_created_during_this_call_is_removed_but_ready_is_not(monkeypatch):
    def ok(f, p):
        f.items.append(_src("twin", p.name, "PREPARING"))  # SDK's extra registration
        f.items.append(_src("real", p.name, "PROCESSING"))
        return f.items[-1]

    fake = FakeSources([_src("preexisting", "keyframes.pdf", "PREPARING")], add=ok)
    a = adapter_with(fake, monkeypatch)
    assert a.upload_file("nb", PDF) == "real"
    assert fake.deleted == ["twin"]  # new since snapshot + same name + stuck; pre-existing one kept


def test_orphan_id_from_sdk_exception_is_removed(monkeypatch):
    def partial(f, p):
        f.items.append(_src("reg1", p.name, "PREPARING"))
        exc = nl.NetworkError("closed")
        exc.source_id = "reg1"
        raise exc

    fake = FakeSources(add=partial)
    a = adapter_with(fake, monkeypatch)
    with pytest.raises(Bili2nbError) as e:
        a.upload_file("nb", PDF, reconcile_sec=0)
    assert fake.deleted == ["reg1"] and e.value.code == "NETWORK_ERROR"
    assert a.drain_orphans() == []  # deleted -> nothing left to remember


def test_undeletable_orphan_is_reported_for_persistence_and_retried_later(monkeypatch):
    def partial(f, p):
        f.items.append(_src("reg1", p.name, "PREPARING"))
        exc = nl.NetworkError("closed")
        exc.source_id = "reg1"
        raise exc

    fake = FakeSources(add=partial)
    fake.undeletable = {"reg1"}
    a = adapter_with(fake, monkeypatch)
    with pytest.raises(Bili2nbError) as e:
        a.upload_file("nb", PDF, reconcile_sec=0)
    assert e.value.code == "UPLOAD_FAILED" and "reg1" in e.value.extra["orphan_source_ids"]
    assert a.drain_orphans() == ["reg1"] and a.drain_orphans() == []  # drained once
    fake.undeletable = set()
    assert a.cleanup_owned("nb", ["reg1"]) == [] and fake.deleted == ["reg1"]


def test_cleanup_owned_only_touches_listed_ids(monkeypatch):
    fake = FakeSources([_src("a", "x.pdf", "PREPARING"), _src("b", "x.pdf", "ERROR"), _src("c", "x.pdf", "READY")])
    a = adapter_with(fake, monkeypatch)
    assert a.cleanup_owned("nb", ["a", "c", "ghost"]) == []  # a deleted; c is READY (kept); ghost gone
    assert fake.deleted == ["a"]
    assert a.cleanup_owned("nb", []) == []


# =============================================================== lost responses / non-idempotent writes
def test_response_lost_but_source_appears_late_is_reused_not_reuploaded(monkeypatch):
    def lost(f, p):
        f.items.append(_src("landed", p.name, "READY"))
        f.hidden["landed"] = 3  # list() lags: visible only from the 4th call on
        raise nl.NetworkError("closed")

    async def fast_sleep(_):
        return None

    monkeypatch.setattr(ad.asyncio, "sleep", fast_sleep)
    fake = FakeSources(add=lost)
    a = adapter_with(fake, monkeypatch)
    assert a.upload_file("nb", PDF, reconcile_sec=60) == "landed"
    assert fake.add_calls == 1


def test_nothing_landed_means_exactly_one_add_attempt_and_retryable_code(monkeypatch):
    def lost(f, p):
        raise nl.NetworkError("closed")

    fake = FakeSources(add=lost)
    a = adapter_with(fake, monkeypatch)
    with pytest.raises(Bili2nbError) as e:
        a.upload_file("nb", PDF, reconcile_sec=0)
    assert fake.add_calls == 1 and e.value.code == "NETWORK_ERROR"  # the adapter never loops by itself


def test_duplicate_ready_sources_reuse_first_and_delete_nothing(monkeypatch):
    fake = FakeSources([_src("d1", "keyframes.pdf", "READY"), _src("d2", "keyframes.pdf", "READY")])
    a = adapter_with(fake, monkeypatch)
    assert a.upload_file("nb", PDF) == "d1"
    assert fake.deleted == [] and fake.add_calls == 0


def test_upload_never_claims_success_when_the_sdk_raises_a_non_network_error(monkeypatch):
    def broken(f, p):
        raise nl.NotebookLMError("weird")

    fake = FakeSources(add=broken)
    a = adapter_with(fake, monkeypatch)
    with pytest.raises(Bili2nbError) as e:
        a.upload_file("nb", PDF, reconcile_sec=0)
    assert e.value.code == "UPLOAD_FAILED"


def test_create_notebook_is_sent_once_even_when_the_response_is_lost(monkeypatch):
    calls = []

    class NB:
        @staticmethod
        async def list():
            return []

        @staticmethod
        async def create(title):
            calls.append(title)
            raise nl.NetworkError("closed")

    a = ad.NotebookAdapter()

    class C:
        notebooks = NB

        async def __aenter__(self):
            return self

        async def __aexit__(self, *x):
            return False

    monkeypatch.setattr(a, "_client", lambda **kw: C())
    with pytest.raises(Bili2nbError) as e:
        a.create_notebook("T", reconcile_sec=0)
    assert calls == ["T"] and e.value.code == "NOTEBOOK_CREATE_FAILED"


# =============================================================== scoped connection control
def _curl_client_class():
    from notebooklm._curl_cffi_transport import CurlCffiAsyncClient

    return CurlCffiAsyncClient


def _opts(client):
    return dict(getattr(client._curl, "curl_options", {}) or {})


def test_fresh_connections_only_affects_sdk_clients_created_inside_the_block():
    import curl_cffi.requests as cr
    from curl_cffi import CurlOpt

    Client = _curl_client_class()
    before = cr.AsyncSession
    with ad._fresh_connections():
        assert cr.AsyncSession is before  # curl_cffi itself is NOT patched (other users unaffected)
        inside = Client()
        assert _opts(inside).get(CurlOpt.FRESH_CONNECT) == 1 and _opts(inside).get(CurlOpt.FORBID_REUSE) == 1
    outside = Client()
    assert CurlOpt.FRESH_CONNECT not in _opts(outside)  # restored afterwards
    plain = cr.AsyncSession()
    assert CurlOpt.FRESH_CONNECT not in dict(getattr(plain, "curl_options", {}) or {})


def test_fresh_connections_restores_on_error_and_is_reentrant():
    from curl_cffi import CurlOpt

    Client = _curl_client_class()
    original = Client.__init__
    with pytest.raises(RuntimeError), ad._fresh_connections():
        with ad._fresh_connections():  # nested use must not restore too early
            pass
        assert CurlOpt.FRESH_CONNECT in _opts(Client())
        raise RuntimeError("boom")
    assert Client.__init__ is original
    assert CurlOpt.FRESH_CONNECT not in _opts(Client())


def test_fresh_connections_respects_an_explicit_session_factory():
    Client = _curl_client_class()
    marker = []

    def mine(**kw):
        marker.append(kw)
        import curl_cffi.requests as cr

        return cr.AsyncSession(**kw)

    with ad._fresh_connections():
        Client(session_factory=mine)
    assert marker, "caller-supplied session_factory must win"


def test_fresh_connections_is_threadsafe_under_concurrent_use():
    import threading

    Client = _curl_client_class()
    original = Client.__init__
    errors = []

    def worker():
        try:
            for _ in range(20):
                with ad._fresh_connections():
                    Client()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    ts = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors and Client.__init__ is original


# =============================================================== SDK compatibility guard
def test_installed_sdk_passes_the_compatibility_contract():
    """Contract test: fails in CI the moment a dependency bump changes a private API we rely on."""
    assert sdk_compat.check() == []
    assert sdk_compat.PINNED_SDK == "0.8.6"


def test_error_code_exists_and_is_documented():
    assert EXIT_CODES["SDK_INCOMPATIBLE"] == 26


def test_unverified_sdk_version_blocks_writes_before_any_cloud_call(monkeypatch):
    monkeypatch.setattr(sdk_compat, "_installed_version", lambda: "9.9.9")
    monkeypatch.delenv("BILI2NB_ALLOW_UNVERIFIED_SDK", raising=False)
    sdk_compat.check.cache_clear()
    a = ad.NotebookAdapter()
    monkeypatch.setattr(a, "_client", lambda **kw: pytest.fail("must not reach the cloud"))
    with pytest.raises(Bili2nbError) as e:
        a.create_notebook("T")
    assert e.value.code == "SDK_INCOMPATIBLE" and "9.9.9" in e.value.message and "0.8.6" in e.value.message
    with pytest.raises(Bili2nbError) as e2:
        a.upload_file("nb", PDF)
    assert e2.value.code == "SDK_INCOMPATIBLE"
    sdk_compat.check.cache_clear()


def test_override_allows_a_newer_version_only_when_capabilities_still_match(monkeypatch):
    monkeypatch.setattr(sdk_compat, "_installed_version", lambda: "9.9.9")
    monkeypatch.setenv("BILI2NB_ALLOW_UNVERIFIED_SDK", "1")
    sdk_compat.check.cache_clear()
    assert sdk_compat.check() == []  # same private API => allowed
    sdk_compat.check.cache_clear()
    monkeypatch.setattr(sdk_compat, "_capability_problems", lambda: ["SourceUploadPipeline.x 缺失"])
    assert sdk_compat.check() != []  # capability mismatch is never overridable
    sdk_compat.check.cache_clear()


def test_missing_private_api_is_reported_clearly(monkeypatch):
    import notebooklm._web.sources.upload as up

    monkeypatch.delattr(up, "_validate_resumable_upload_url")
    sdk_compat.check.cache_clear()
    problems = sdk_compat.check()
    assert any("_validate_resumable_upload_url" in p for p in problems)
    with pytest.raises(Bili2nbError) as e:
        sdk_compat.require("upload")
    assert e.value.code == "SDK_INCOMPATIBLE"
    sdk_compat.check.cache_clear()


def test_changed_upload_signature_is_reported(monkeypatch):
    from notebooklm._web.sources.upload import SourceUploadPipeline

    async def changed(self, url, data):  # incompatible replacement
        return None

    monkeypatch.setattr(SourceUploadPipeline, "upload_file_streaming", changed)
    sdk_compat.check.cache_clear()
    assert any("upload_file_streaming" in p for p in sdk_compat.check())
    sdk_compat.check.cache_clear()


def test_curl_connection_control_capability_is_checked(monkeypatch):
    import curl_cffi

    monkeypatch.setattr(curl_cffi, "CurlOpt", NS(FORBID_REUSE=1))  # FRESH_CONNECT disappeared
    sdk_compat.check.cache_clear()
    assert any("FRESH_CONNECT" in p for p in sdk_compat.check())
    sdk_compat.check.cache_clear()


@pytest.mark.real_chunked
def test_chunked_install_fails_loudly_instead_of_silently_falling_back():
    """The old behaviour returned False and the SDK quietly used the unreliable single-request
    upload. That must be an error now."""
    from bili2nb.notebook.chunked_upload import install_chunked_upload

    with pytest.raises(Bili2nbError) as e:
        install_chunked_upload(NS(sources=NS()))  # no `_uploader` => SDK layout changed
    assert e.value.code == "SDK_INCOMPATIBLE"


@pytest.mark.real_chunked
def test_chunked_upload_is_mandatory_unless_explicitly_disabled(monkeypatch):
    fake = FakeSources()
    a = adapter_with(fake, monkeypatch)  # fake client has no SDK upload pipeline
    with pytest.raises(Bili2nbError) as e:
        a.upload_file("nb", PDF)
    assert e.value.code == "SDK_INCOMPATIBLE" and fake.add_calls == 0  # nothing was sent


def test_doctor_reports_sdk_compatibility(monkeypatch):
    from bili2nb import doctor

    monkeypatch.setattr(doctor.sdk_compat, "check", lambda: ["上传管线签名已变化"])
    rep = doctor.run_checks()
    c = {x["name"]: x for x in rep["checks"]}
    assert c["sdk-compat"]["status"] == "fail" and "上传管线" in c["sdk-compat"]["detail"]


# =============================================================== persistence of orphan evidence
def test_job_store_persists_orphans_and_migrates_old_databases(tmp_path):
    import sqlite3

    from bili2nb.jobs.db import JobStore

    # an OLD database created before the `orphans` column existed
    path = tmp_path / "old.sqlite"
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE jobs (job_id TEXT PRIMARY KEY, video_key TEXT NOT NULL, account_key TEXT NOT NULL,
      mode TEXT NOT NULL, url TEXT NOT NULL, options TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL,
      stage TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, video_id TEXT,
      video_title TEXT, duration_sec REAL, output_dir TEXT, audio_path TEXT, pdf_path TEXT,
      files_cleaned INTEGER NOT NULL DEFAULT 0, notebook_id TEXT, sources TEXT NOT NULL DEFAULT '{}',
      ready TEXT NOT NULL DEFAULT '{}', visual TEXT NOT NULL DEFAULT '{}', error_code TEXT,
      error_message TEXT, error_detail TEXT, warnings TEXT NOT NULL DEFAULT '[]',
      timings TEXT NOT NULL DEFAULT '{}', attempts INTEGER NOT NULL DEFAULT 0,
      superseded INTEGER NOT NULL DEFAULT 0);
    INSERT INTO jobs(job_id,video_key,account_key,mode,url,status,stage,created_at,updated_at)
      VALUES ('old1','k','a','m','u','failed','created','t','t');
    """)
    con.commit()
    con.close()
    st = JobStore(path)
    assert st.get("old1").orphans == []  # migrated, readable
    st.update("old1", orphans=["s1", "s2"])
    assert JobStore(path).get("old1").orphans == ["s1", "s2"]
    fresh = JobStore(tmp_path / "new.sqlite")
    j = fresh.create(video_key="k", account_key="a", mode="m", url="u", options={})
    assert j.orphans == []
