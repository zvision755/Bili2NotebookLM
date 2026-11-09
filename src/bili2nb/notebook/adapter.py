"""NotebookAdapter: the ONLY module that talks to notebooklm-py (unofficial SDK).

Everything the SDK raises is mapped to a stable Bili2nbError code. Business code must
not import `notebooklm` directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from bili2nb.errors import Bili2nbError

T = TypeVar("T")
NOTEBOOK_URL = "https://notebooklm.google.com/notebook/{id}"
MAX_TITLE_LEN = 100
_STUCK = {"PREPARING", "ERROR"}  # only these may ever be deleted by our own cleanup
_USABLE = {"READY", "PROCESSING"}  # PREPARING = registered but bytes not yet accepted


def notebook_url(notebook_id: str) -> str:
    return NOTEBOOK_URL.format(id=notebook_id)


def make_title(video_title: str) -> str:
    t = " ".join((video_title or "").split()) or "Untitled"
    t = f"B站｜{t}"
    return t if len(t) <= MAX_TITLE_LEN else t[: MAX_TITLE_LEN - 1] + "…"


@dataclass(frozen=True)
class SourceInfo:
    id: str
    title: str | None
    status: str  # READY / PROCESSING / ERROR / PREPARING / UNKNOWN


@contextlib.contextmanager
def _fresh_connections():
    """Force a brand-new TCP connection for every request made inside the block.

    Live finding (Gate 4): libcurl silently RE-SENDS a POST when a *reused* keep-alive
    connection dies without a response. Google often processes the request and then drops the
    connection, so non-idempotent calls got executed twice (duplicate empty notebooks 2 s
    apart; a stuck twin source registered with every upload). `FRESH_CONNECT`+`FORBID_REUSE`
    remove that hidden retry; `scripts/probe_curl_post_retry.py` demonstrates 2 POSTs -> 1.
    """
    try:
        import curl_cffi.requests as cr
        from curl_cffi import CurlOpt
    except ImportError:  # httpx transport: no libcurl, nothing to do
        yield
        return
    orig = cr.AsyncSession

    class _Fresh(orig):  # type: ignore[valid-type, misc]
        def __init__(self, *a, **kw):
            opts = dict(kw.get("curl_options") or {})
            opts.update({CurlOpt.FRESH_CONNECT: 1, CurlOpt.FORBID_REUSE: 1})
            kw["curl_options"] = opts
            super().__init__(*a, **kw)

    cr.AsyncSession = _Fresh
    try:
        yield
    finally:
        cr.AsyncSession = orig


def _sdk():
    import notebooklm  # imported lazily so convert never needs it

    return notebooklm


def net_errors() -> tuple[type[BaseException], ...]:
    """Errors that mean "the response may have been lost": the operation might have succeeded."""
    import httpx

    nl = _sdk()
    return (nl.NetworkError, nl.ServerError, TimeoutError, httpx.RequestError)


def map_sdk_error(exc: BaseException, *, default: str) -> Bili2nbError:
    """Translate an SDK/network exception into a Bili2nbError (no secrets in message)."""
    if isinstance(exc, Bili2nbError):
        return exc
    nl = _sdk()
    name = type(exc).__name__
    if isinstance(exc, (nl.AuthError, nl.AuthExtractionError, FileNotFoundError)):
        return Bili2nbError(
            "AUTH_REQUIRED",
            "NotebookLM 未登录或会话已失效。请运行: bili2nb auth login",
            detail=name,
        )
    if isinstance(exc, nl.RateLimitError):
        return Bili2nbError("RATE_LIMITED", "NotebookLM 限流，请稍后重试。", detail=name)
    if isinstance(exc, (nl.SourceTimeoutError, nl.WaitTimeoutError)):
        return Bili2nbError("SOURCE_NOT_READY", "等待来源处理完成超时。", detail=name)
    if isinstance(exc, nl.SourceProcessingError):
        return Bili2nbError("SOURCE_NOT_READY", "NotebookLM 报告来源处理失败。", detail=name)
    if isinstance(exc, (*net_errors(), OSError)):
        return Bili2nbError("NETWORK_ERROR", "网络或服务端错误。", detail=name)
    return Bili2nbError(default, f"NotebookLM 调用失败 ({name})", detail=name)


class NotebookAdapter:
    def __init__(self, storage_path: str | None = None, profile: str | None = None):
        self._storage_path = storage_path
        self._profile = profile

    @property
    def account_key(self) -> str:
        """Which saved NotebookLM profile this adapter uses (idempotency scope)."""
        return self._profile or os.environ.get("NOTEBOOKLM_PROFILE") or "default"

    def _client(self, **overrides):
        nl = _sdk()
        # Live finding (2026-11-08): the default httpx transport gets "Server disconnected"
        # on NotebookLM upload endpoints; the SDK's curl_cffi transport (browser TLS
        # fingerprint, PoC-grade in the SDK) uploads fine. User can override via env.
        os.environ.setdefault("NOTEBOOKLM_TRANSPORT", "curl_cffi")
        return nl.NotebookLMClient.from_storage(
            path=self._storage_path, profile=self._profile, **overrides
        )

    def _run(
        self,
        coro_factory: Callable[[Any], Awaitable[T]],
        *,
        default: str,
        client_kwargs: dict | None = None,
        fresh: bool = False,
    ) -> T:
        async def go() -> T:
            async with self._client(**(client_kwargs or {})) as client:
                return await coro_factory(client)

        try:
            if fresh:  # non-idempotent writes: no hidden transport-level re-send
                with _fresh_connections():
                    return asyncio.run(go())
            return asyncio.run(go())
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            raise map_sdk_error(exc, default=default) from exc

    # -- auth -------------------------------------------------------------
    def check_auth(self) -> int:
        """Really call NotebookLM (read-only list). Returns number of notebooks."""

        async def op(client):
            return len(await client.notebooks.list())

        return self._run(op, default="AUTH_REQUIRED")

    # -- notebooks / sources ---------------------------------------------
    def create_notebook(self, title: str, *, reconcile_sec: float = 20.0) -> str:
        """Create a notebook. Creation is NOT idempotent, so if the response is lost
        (network/server error) we look for a NEW notebook with this title before failing,
        and never auto-retry an ambiguous create (that could make duplicate notebooks)."""

        async def op(client):
            before = {n.id for n in await client.notebooks.list()}
            try:
                return (await client.notebooks.create(title)).id
            except net_errors() as exc:
                deadline = time.monotonic() + reconcile_sec
                while True:
                    new = [n for n in await client.notebooks.list()
                           if n.id not in before and n.title == title]
                    if new:
                        return new[0].id
                    if time.monotonic() >= deadline:
                        break
                    await asyncio.sleep(3.0)
                raise Bili2nbError(
                    "NOTEBOOK_CREATE_FAILED",
                    "创建笔记本时连接中断，且未发现新笔记本。为避免重复建本不会自动重试；"
                    "请先在 NotebookLM 确认没有同名新笔记本，再用 retry 重试。",
                    detail=type(exc).__name__,
                ) from exc

        # Live Gate 3 incident: the SDK's own server-error auto-retry re-sent this
        # non-idempotent create and produced a DUPLICATE notebook. Disable it here.
        return self._run(op, default="NOTEBOOK_CREATE_FAILED",
                         client_kwargs={"server_error_max_retries": 0}, fresh=True)

    def notebook_exists(self, notebook_id: str) -> bool:
        async def op(client):
            return (await client.notebooks.get_or_none(notebook_id)) is not None

        return self._run(op, default="NETWORK_ERROR")

    def upload_file(
        self,
        notebook_id: str,
        path: Path,
        title: str | None = None,
        *,
        reconcile_sec: float = 90.0,
    ) -> str:
        """Upload one file WITHOUT waiting for processing; returns the source id.

        Idempotent + reconciling (observed live on 2026-11-08: the server often accepts a
        file but the HTTP response is dropped, so the SDK raises NetworkError although the
        source exists). Therefore:
          1. if a usable source with the same filename/title already exists, reuse it;
          2. on a network-class error, look the source up before ever re-uploading.
        Never blind-retries. Orphans (registered but stuck PREPARING) are reported in the
        error detail as `orphan_source_id=...` and are never reused.
        """
        names = {path.name} | ({title} if title else set())

        async def op(client):
            if os.environ.get("BILI2NB_CHUNKED_UPLOAD", "1") != "0":
                from .chunked_upload import install_chunked_upload

                install_chunked_upload(client)  # short requests; see chunked_upload.py
            before = await client.sources.list(notebook_id)
            # User-authorised (2026-11-08): remove ONLY our own stuck leftovers
            # (same file name, PREPARING/ERROR). READY/PROCESSING and other files are untouched.
            await self._delete_stuck(client, notebook_id, before, names)
            usable = [s for s in before if s.title in names and s.status.name in _USABLE]
            if usable:
                return usable[0].id
            known = {s.id for s in before}
            try:
                src = await client.sources.add_file(notebook_id, path, title=title, wait=False)
                # Live: each upload leaves an extra stuck registration (same name) behind.
                with contextlib.suppress(Exception):
                    twins = [s for s in await client.sources.list(notebook_id) if s.id != src.id]
                    await self._delete_stuck(client, notebook_id, twins, names)
                return src.id
            except net_errors() as exc:
                found = await self._reconcile(client, notebook_id, names, known, reconcile_sec)
                if found is not None and found.status.name in _USABLE:
                    return found.id
                orphan = found.id if found is not None else getattr(exc, "source_id", None)
                if orphan is None:
                    # Nothing landed on the server: repeating is safe -> retryable code.
                    raise Bili2nbError(
                        "NETWORK_ERROR", "上传时连接中断，云端未收到该文件（可安全重试）。",
                        detail=type(exc).__name__,
                    ) from exc
                # Registered but bytes never accepted: remove the stuck source we just made.
                try:
                    current = await client.sources.list(notebook_id)
                except Exception:  # noqa: BLE001 - best effort
                    current = []
                deleted = await self._delete_stuck(
                    client, notebook_id, current, names, extra_ids={orphan}
                )
                if orphan in deleted:
                    raise Bili2nbError(
                        "NETWORK_ERROR", "上传时连接中断；已清理该次留下的未完成来源，可安全重试。",
                        detail=f"{type(exc).__name__} orphan_deleted={orphan}",
                    ) from exc
                raise Bili2nbError(
                    "UPLOAD_FAILED",
                    "上传未完成（服务端断开连接，留下未完成的来源）。已避免重复上传；请使用 retry 重试。",
                    detail=f"{type(exc).__name__} orphan_source_id={orphan}",
                ) from exc

        return self._run(op, default="UPLOAD_FAILED", fresh=True)

    @staticmethod
    async def _delete_stuck(client, notebook_id, sources, names, *, extra_ids=frozenset()) -> list[str]:
        """Best-effort delete of stuck (PREPARING/ERROR) sources that are ours: matching
        file name/title or an explicitly named orphan id. Never touches READY/PROCESSING.
        Returns the ids actually deleted; failures are swallowed (cleanup must not block)."""
        deleted: list[str] = []
        for s in sources:
            if s.status.name not in _STUCK:
                continue
            if s.title not in names and s.id not in extra_ids:
                continue
            with contextlib.suppress(Exception):  # best effort
                await client.sources.delete(notebook_id, s.id)
                deleted.append(s.id)
        return deleted

    def cleanup_stuck(self, notebook_id: str, names: set[str]) -> list[str]:
        """Delete our stuck leftovers (by file name) in one notebook; returns deleted ids."""
        if not names:
            return []

        async def op(client):
            return await self._delete_stuck(
                client, notebook_id, await client.sources.list(notebook_id), names
            )

        return self._run(op, default="NETWORK_ERROR")

    @staticmethod
    async def _reconcile(client, notebook_id, names, known, window_sec):
        """Poll for a NEW source matching the filename/title; wait out PREPARING briefly."""
        deadline = time.monotonic() + window_sec
        found = None
        while True:
            for s in await client.sources.list(notebook_id):
                if s.id not in known and s.title in names and (
                    found is None or s.status.name in _USABLE
                ):
                    found = s  # prefer a usable match over a stuck twin (live: 2 new per attempt)
            if found is not None and found.status.name in _USABLE:
                return found
            if time.monotonic() >= deadline:
                return found
            await asyncio.sleep(3.0)

    def list_sources(self, notebook_id: str) -> list[SourceInfo]:
        async def op(client):
            return [
                SourceInfo(s.id, s.title, s.status.name)
                for s in await client.sources.list(notebook_id)
            ]

        return self._run(op, default="NETWORK_ERROR")

    def wait_ready(self, notebook_id: str, source_id: str, timeout: float = 600.0) -> SourceInfo:
        """Block until the source is READY; raises SOURCE_NOT_READY on error/timeout."""

        async def op(client):
            s = await client.sources.wait_until_ready(notebook_id, source_id, timeout=timeout)
            return SourceInfo(s.id, s.title, s.status.name)

        info = self._run(op, default="SOURCE_NOT_READY")
        if info.status != "READY":
            raise Bili2nbError("SOURCE_NOT_READY", f"来源状态为 {info.status}，不是 READY。")
        return info
