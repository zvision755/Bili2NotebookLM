import notebooklm as nl
import pytest
from typer.testing import CliRunner

from bili2nb.cli import app
from bili2nb.errors import Bili2nbError
from bili2nb.notebook import adapter as ad

runner = CliRunner()


def test_notebook_url():
    assert ad.notebook_url("abc-123") == "https://notebooklm.google.com/notebook/abc-123"


def test_make_title_prefix_truncate_and_empty():
    assert ad.make_title("  hello   world ") == "B站｜hello world"
    assert ad.make_title("") == "B站｜Untitled"
    assert len(ad.make_title("x" * 500)) == ad.MAX_TITLE_LEN


@pytest.mark.parametrize(
    "exc,code",
    [
        (nl.AuthError("bad"), "AUTH_REQUIRED"),
        (FileNotFoundError("storage"), "AUTH_REQUIRED"),
        (nl.RateLimitError("429"), "RATE_LIMITED"),
        (nl.SourceTimeoutError("s", 1.0), "SOURCE_NOT_READY"),
        (nl.NetworkError("down"), "NETWORK_ERROR"),
        (nl.NotebookLMError("weird"), "UPLOAD_FAILED"),
    ],
)
def test_map_sdk_error(exc, code):
    assert ad.map_sdk_error(exc, default="UPLOAD_FAILED").code == code


def test_map_error_never_leaks_message():
    e = ad.map_sdk_error(nl.AuthError("SID=SECRETCOOKIE"), default="X")
    assert "SECRETCOOKIE" not in e.message and "SECRETCOOKIE" not in (e.detail or "")


def test_cli_auth_check_json_failure(monkeypatch):
    def boom(self):
        raise Bili2nbError("AUTH_REQUIRED", "need login")

    monkeypatch.setattr(ad.NotebookAdapter, "check_auth", boom)
    r = runner.invoke(app, ["auth", "check", "--json"])
    assert r.exit_code == 20
    assert r.stdout.count("\n") == 1 and '"AUTH_REQUIRED"' in r.stdout


def test_cli_auth_check_ok(monkeypatch):
    monkeypatch.setattr(ad.NotebookAdapter, "check_auth", lambda self: 3)
    r = runner.invoke(app, ["auth", "check", "--json"])
    assert r.exit_code == 0 and '"notebook_count": 3' in r.stdout


# ---- upload reconcile (regression for live 2026-11-08 "server disconnected" false failures)
from pathlib import Path
from types import SimpleNamespace as NS


def _src(id, title, status):
    return NS(id=id, title=title, status=NS(name=status))


class FakeSources:
    def __init__(self, existing, add_behaviour):
        self.items = list(existing)
        self.add_behaviour = add_behaviour
        self.add_calls = 0
        self.deleted = []
        self.delete_fails = False

    async def list(self, nb):
        return list(self.items)

    async def delete(self, nb, source_id):
        if self.delete_fails:
            raise nl.NetworkError("delete dropped")
        self.deleted.append(source_id)
        self.items = [x for x in self.items if x.id != source_id]

    async def add_file(self, nb, path, title=None, wait=False):
        self.add_calls += 1
        return self.add_behaviour(self, path)


def _run_upload(fake, monkeypatch, reconcile_sec=0.0):
    a = ad.NotebookAdapter()

    class C:
        sources = fake

        async def __aenter__(self):
            return self

        async def __aexit__(self, *x):
            return False

    monkeypatch.setattr(a, "_client", lambda **kw: C())
    return a.upload_file("nb", Path("/x/gate1_page.pdf"), reconcile_sec=reconcile_sec)


def test_upload_reuses_existing_usable_source(monkeypatch):
    fake = FakeSources([_src("s1", "gate1_page.pdf", "READY")], lambda f, p: None)
    assert _run_upload(fake, monkeypatch) == "s1"
    assert fake.add_calls == 0  # no duplicate upload


def test_upload_ignores_stuck_preparing_orphan_and_uploads(monkeypatch):
    def ok(f, p):
        return _src("s2", "gate1_page.pdf", "PROCESSING")

    fake = FakeSources([_src("s1", "gate1_page.pdf", "PREPARING")], ok)
    assert _run_upload(fake, monkeypatch) == "s2"


def test_upload_network_error_but_server_accepted(monkeypatch):
    def dropped(f, p):
        f.items.append(_src("s3", "gate1_page.pdf", "READY"))  # server accepted
        raise nl.NetworkError("Server disconnected")

    fake = FakeSources([], dropped)
    assert _run_upload(fake, monkeypatch) == "s3"
    assert fake.add_calls == 1  # not retried


def test_upload_network_error_orphan_reported(monkeypatch):
    def dropped(f, p):
        f.items.append(_src("s4", "gate1_page.pdf", "PREPARING"))
        raise nl.NetworkError("Server disconnected")

    fake = FakeSources([], dropped)
    fake.delete_fails = True  # cleanup impossible -> orphan really remains
    with pytest.raises(Bili2nbError) as ei:
        _run_upload(fake, monkeypatch)
    assert ei.value.code == "UPLOAD_FAILED" and "orphan_source_ids=s4" in ei.value.detail
    assert fake.add_calls == 1


def test_upload_network_error_nothing_created(monkeypatch):
    def dropped(f, p):
        raise nl.NetworkError("Server disconnected")

    fake = FakeSources([], dropped)
    with pytest.raises(Bili2nbError) as ei:
        _run_upload(fake, monkeypatch)
    # nothing landed on the server -> safe to retry -> retryable code (not an orphan case)
    assert ei.value.code == "NETWORK_ERROR" and "orphan" not in (ei.value.detail or "")


def test_client_defaults_to_curl_cffi_transport(monkeypatch):
    monkeypatch.delenv("NOTEBOOKLM_TRANSPORT", raising=False)
    monkeypatch.setattr(nl.NotebookLMClient, "from_storage", lambda **kw: "client")
    assert ad.NotebookAdapter()._client() == "client"
    import os

    assert os.environ["NOTEBOOKLM_TRANSPORT"] == "curl_cffi"


# ---- create_notebook: lost response must never cause duplicate notebooks
class FakeNotebooks:
    def __init__(self, existing, behaviour):
        self.items = list(existing)
        self.behaviour = behaviour
        self.create_calls = 0

    async def list(self):
        return list(self.items)

    async def create(self, title):
        self.create_calls += 1
        return self.behaviour(self, title)


def _run_create(fake, monkeypatch, title="T"):
    a = ad.NotebookAdapter()

    class C:
        notebooks = fake

        async def __aenter__(self):
            return self

        async def __aexit__(self, *x):
            return False

    monkeypatch.setattr(a, "_client", lambda **kw: C())
    return a.create_notebook(title, reconcile_sec=0.0)


def test_create_notebook_normal(monkeypatch):
    def ok(f, t):
        nb = NS(id="n1", title=t)
        f.items.append(nb)
        return nb

    assert _run_create(FakeNotebooks([], ok), monkeypatch) == "n1"


def test_create_notebook_lost_response_but_created_is_found(monkeypatch):
    def lost(f, t):
        f.items.append(NS(id="n9", title=t))  # server created it
        raise nl.NetworkError("Server disconnected")

    fake = FakeNotebooks([NS(id="old", title="T")], lost)  # an OLD same-title notebook exists
    assert _run_create(fake, monkeypatch) == "n9"  # picks the NEW one, not "old"
    assert fake.create_calls == 1


def test_create_notebook_lost_response_nothing_created_not_auto_retried(monkeypatch):
    def lost(f, t):
        raise nl.NetworkError("Server disconnected")

    fake = FakeNotebooks([], lost)
    with pytest.raises(Bili2nbError) as e:
        _run_create(fake, monkeypatch)
    assert e.value.code == "NOTEBOOK_CREATE_FAILED" and fake.create_calls == 1
    assert "不会自动重试" in e.value.message


def test_create_notebook_rate_limit_maps_to_retryable_code(monkeypatch):
    def limited(f, t):
        raise nl.RateLimitError("429")

    with pytest.raises(Bili2nbError) as e:
        _run_create(FakeNotebooks([], limited), monkeypatch)
    assert e.value.code == "RATE_LIMITED"


# ---- regression: live Gate 3 run 1 (2026-11-08) created a DUPLICATE notebook because the
# SDK's own server-error auto-retry re-sent the non-idempotent create; and an unmapped
# httpx.RequestError skipped upload reconciliation.
def test_create_notebook_disables_sdk_server_error_retries(monkeypatch):
    seen = {}

    class Ctx:
        async def __aenter__(self):
            class C:
                class notebooks:
                    @staticmethod
                    async def list():
                        return []

                    @staticmethod
                    async def create(title):
                        return NS(id="n1", title=title)

            return C()

        async def __aexit__(self, *x):
            return False

    def fake_from_storage(**kw):
        seen.update(kw)
        return Ctx()

    monkeypatch.setattr(nl.NotebookLMClient, "from_storage", staticmethod(fake_from_storage))
    assert ad.NotebookAdapter().create_notebook("T") == "n1"
    assert seen["server_error_max_retries"] == 0  # no hidden duplicate creates


def test_httpx_request_error_is_network_class():
    import httpx

    e = ad.map_sdk_error(httpx.RequestError("reset"), default="UPLOAD_FAILED")
    assert e.code == "NETWORK_ERROR"


def test_upload_reconciles_on_raw_httpx_request_error(monkeypatch):
    import httpx

    def dropped(f, p):
        f.items.append(_src("s7", "gate1_page.pdf", "READY"))
        raise httpx.RequestError("connection reset")

    fake = FakeSources([], dropped)
    assert _run_upload(fake, monkeypatch) == "s7" and fake.add_calls == 1


def test_create_notebook_reconciles_on_raw_httpx_request_error(monkeypatch):
    import httpx

    def lost(f, t):
        f.items.append(NS(id="n5", title=t))
        raise httpx.RequestError("reset")

    assert _run_create(FakeNotebooks([], lost), monkeypatch) == "n5"






def test_upload_deletes_orphan_it_just_created_and_becomes_retryable(monkeypatch):
    def dropped(f, p):
        f.items.append(_src("s9", "gate1_page.pdf", "PREPARING"))
        raise nl.NetworkError("Server disconnected")

    fake = FakeSources([], dropped)
    with pytest.raises(Bili2nbError) as ei:
        _run_upload(fake, monkeypatch)
    assert fake.deleted == ["s9"]
    # nothing remains on the server -> safe to retry -> retryable code
    assert ei.value.code == "NETWORK_ERROR" and "orphan_deleted=s9" in ei.value.detail


def test_upload_cleanup_failure_is_best_effort(monkeypatch):
    def ok(f, p):
        return _src("new", "gate1_page.pdf", "PROCESSING")

    fake = FakeSources([_src("o1", "gate1_page.pdf", "PREPARING")], ok)
    fake.delete_fails = True
    assert _run_upload(fake, monkeypatch) == "new"  # cleanup failure must not block upload




def test_reconcile_prefers_usable_over_stuck_twin(monkeypatch):
    """Live 2026-11-08: one attempt registered two sources; one became READY, one stuck."""
    def dropped(f, p):
        f.items.append(_src("ready1", "gate1_page.pdf", "READY"))
        f.items.append(_src("stuck2", "gate1_page.pdf", "PREPARING"))
        raise nl.NetworkError("Server disconnected")

    fake = FakeSources([], dropped)
    assert _run_upload(fake, monkeypatch) == "ready1"


def test_upload_success_also_removes_stuck_registration_twin(monkeypatch):
    """Live 2026-11-08: every upload leaves one extra PREPARING registration of the same name."""
    def ok(f, p):
        f.items.append(_src("twin", "gate1_page.pdf", "PREPARING"))  # SDK's extra registration
        f.items.append(_src("real", "gate1_page.pdf", "PROCESSING"))
        return f.items[-1]

    fake = FakeSources([], ok)
    assert _run_upload(fake, monkeypatch) == "real"
    assert fake.deleted == ["twin"] and any(x.id == "real" for x in fake.items)




def test_create_and_upload_run_with_fresh_connections(monkeypatch):
    calls = []
    real = ad._fresh_connections

    import contextlib

    @contextlib.contextmanager
    def spy():
        calls.append("enter")
        with real():
            yield

    monkeypatch.setattr(ad, "_fresh_connections", spy)
    a = ad.NotebookAdapter()

    class C:
        class notebooks:
            @staticmethod
            async def list():
                return []

            @staticmethod
            async def create(title):
                return NS(id="nb1")

            @staticmethod
            async def get_or_none(nb):
                return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *x):
            return False

    monkeypatch.setattr(a, "_client", lambda **kw: C())
    assert a.create_notebook("T") == "nb1" and calls == ["enter"]
    # read-only / idempotent calls must NOT pay for fresh connections
    calls.clear()
    assert a.notebook_exists("x") is False
    assert calls == []


# ---------------------------------------------------------------- V1.2 account source quota
def _limit_adapter(monkeypatch, value=None, exc=None):
    import asyncio
    from types import SimpleNamespace

    from bili2nb.notebook.adapter import NotebookAdapter

    class Settings:
        async def get_account_limits(self):
            if exc:
                raise exc
            return SimpleNamespace(source_limit=value)

    client = SimpleNamespace(settings=Settings())
    monkeypatch.setattr(NotebookAdapter, "_run", lambda self, op, **kw: asyncio.run(op(client)))
    return NotebookAdapter()


@pytest.mark.parametrize("value", [50, 300])
def test_source_limit_comes_from_the_server(monkeypatch, value):
    assert _limit_adapter(monkeypatch, value).source_limit() == (value, "server")


@pytest.mark.parametrize("value", [None, 0, -3, True])
def test_unusable_server_value_falls_back_to_the_conservative_default(monkeypatch, value):
    assert _limit_adapter(monkeypatch, value).source_limit() == (50, "default")
