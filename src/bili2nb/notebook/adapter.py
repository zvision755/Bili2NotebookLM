"""NotebookAdapter: the ONLY module that talks to notebooklm-py (unofficial SDK).

Everything the SDK raises is mapped to a stable Bili2nbError code. Business code must
not import `notebooklm` directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from bili2nb.errors import Bili2nbError
from bili2nb.notebook import sdk_compat

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


_fresh_lock = threading.Lock()
_fresh_depth = 0
_fresh_orig_init = None


@contextlib.contextmanager
def _fresh_connections():
    """Force a brand-new TCP connection for every request made by SDK clients created inside.

    Live finding (Gate 4): libcurl silently RE-SENDS a POST when a *reused* keep-alive
    connection dies without a response. Google often processes the request and then drops the
    connection, so non-idempotent calls were executed twice (duplicate empty notebooks 2 s
    apart; a stuck twin source registered with every upload). `FRESH_CONNECT`+`FORBID_REUSE`
    remove that hidden retry; `scripts/probe_curl_post_retry.py` demonstrates 2 POSTs -> 1.

    Scope (V1.1): only `CurlCffiAsyncClient` instances CREATED while the block is active get
    the options (through the SDK's own `session_factory` hook). curl_cffi itself is not
    patched, so any other curl_cffi user in the process is unaffected. The patch is reference-
    counted under a lock: nested or concurrent blocks restore the original exactly once.
    """
    global _fresh_depth, _fresh_orig_init
    try:
        import curl_cffi.requests as cr
        from curl_cffi import CurlOpt
        from notebooklm import _curl_cffi_transport as transport
    except ImportError:  # httpx transport / SDK without curl_cffi: nothing to control here
        yield
        return
    cls = transport.CurlCffiAsyncClient
    with _fresh_lock:
        if _fresh_depth == 0:
            orig = cls.__init__

            def fresh_session(**kw):
                opts = dict(kw.pop("curl_options", None) or {})
                opts.update({CurlOpt.FRESH_CONNECT: 1, CurlOpt.FORBID_REUSE: 1})
                return cr.AsyncSession(curl_options=opts, **kw)

            def init(self, *a, **kw):
                if kw.get("session_factory") is None:  # an explicit caller factory always wins
                    kw["session_factory"] = fresh_session
                orig(self, *a, **kw)

            _fresh_orig_init = orig
            cls.__init__ = init
        _fresh_depth += 1
    try:
        yield
    finally:
        with _fresh_lock:
            _fresh_depth -= 1
            if _fresh_depth == 0:
                cls.__init__ = _fresh_orig_init
                _fresh_orig_init = None


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
        # Source ids THIS process registered/observed as stuck and could not delete yet. Callers
        # (the importer) persist them in the job record via `drain_orphans()`.
        self._pending_orphans: set[str] = set()

    def drain_orphans(self) -> list[str]:
        """Hand over (and forget) the ids of our own stuck sources that still need deleting."""
        out = sorted(self._pending_orphans)
        self._pending_orphans.clear()
        return out

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

    def source_limit(self) -> tuple[int, str]:
        """Per-notebook source quota of THIS account -> (limit, origin).

        origin "server": read from NotebookLM (public `settings.get_account_limits()`); "default":
        the server gave no usable number, so the conservative documented free-tier value is used
        (which can only make the planner merge MORE, never exceed a real quota)."""
        from bili2nb.planner import DEFAULT_SOURCE_LIMIT

        async def op(client):
            return (await client.settings.get_account_limits()).source_limit

        value = self._run(op, default="NETWORK_ERROR")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value, "server"
        return DEFAULT_SOURCE_LIMIT, "default"

    # -- notebooks / sources ---------------------------------------------
    def create_notebook(self, title: str, *, reconcile_sec: float = 20.0) -> str:
        """Create a notebook. Creation is NOT idempotent, so if the response is lost
        (network/server error) we look for a NEW notebook with this title before failing,
        and never auto-retry an ambiguous create (that could make duplicate notebooks)."""

        sdk_compat.require("create_notebook")  # stop BEFORE any cloud write if the SDK changed

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
        owned=(),
        taken=(),
    ) -> str:
        """Upload one file WITHOUT waiting for processing; returns the source id.

        Idempotent + reconciling (observed live: the server often accepts a file but the HTTP
        response is dropped, so the SDK raises NetworkError although the source exists):
          1. if a usable source with the same filename/title already exists, reuse it;
          2. on a network-class error, look the source up (up to `reconcile_sec`) before ever
             re-uploading. This method itself never loops: it makes ONE add_file attempt.

        Deleting stuck sources (PREPARING/ERROR) requires OWNERSHIP EVIDENCE, never just a
        matching name (a same-named source may be the user's own):
          * ids in `owned` (recorded in the job from an earlier attempt) or in this adapter's
            pending list; and/or
          * sources that appeared DURING this call (not in the pre-call snapshot), carry our
            file name/title, and are stuck; and/or
          * the id the SDK attached to its exception (`source_id`).
        READY/PROCESSING sources are never deleted.

        `taken` = ids of sources this job already assigned to OTHER items. They are never reused
        as "this file's earlier upload": every part of a multi-part video uploads a file with the
        same name (audio.m4a / keyframes.pdf), and the display-title rename does not reliably
        stick, so a bare name match would swallow the next part's upload (live, 2026-11-10).
        """
        sdk_compat.require("upload_file")  # stop BEFORE any cloud write if the SDK changed
        names = {path.name} | ({title} if title else set())
        owned_ids = {*owned, *self._pending_orphans}
        taken_ids = set(taken)

        async def op(client):
            if os.environ.get("BILI2NB_CHUNKED_UPLOAD", "1") != "0":
                from .chunked_upload import install_chunked_upload

                install_chunked_upload(client)  # raises SDK_INCOMPATIBLE instead of degrading
            before = await client.sources.list(notebook_id)
            before_ids = {s.id for s in before}
            # (1) leftovers we can PROVE are ours from earlier attempts
            await self._delete_owned(client, notebook_id, before, owned_ids)
            usable = [s for s in before
                      if s.id not in taken_ids and s.title in names and s.status.name in _USABLE]
            if usable:
                return usable[0].id
            try:
                src = await client.sources.add_file(notebook_id, path, title=title, wait=False)
            except net_errors() as exc:
                found = await self._reconcile(client, notebook_id, names, before_ids, reconcile_sec)
                if found is not None and found.status.name in _USABLE:
                    return found.id
                try:
                    now = await client.sources.list(notebook_id)
                except Exception:  # noqa: BLE001 - best effort
                    now = []
                evidence = {getattr(exc, "source_id", None)} - {None}
                evidence |= {s.id for s in now if s.id not in before_ids and s.title in names}
                _, failed = await self._delete_owned(client, notebook_id, now, evidence)
                if failed:
                    raise Bili2nbError(
                        "UPLOAD_FAILED",
                        "上传未完成（服务端断开连接，留下未能清理的未完成来源）。已避免重复上传；请使用 retry 重试。",
                        detail=f"{type(exc).__name__} orphan_source_ids={','.join(sorted(failed))}",
                        extra={"orphan_source_ids": sorted(failed)},
                    ) from exc
                if evidence:
                    raise Bili2nbError(
                        "NETWORK_ERROR", "上传时连接中断；已清理该次留下的未完成来源，可安全重试。",
                        detail=f"{type(exc).__name__} orphan_deleted={','.join(sorted(evidence))}",
                    ) from exc
                # Nothing of ours landed on the server: repeating is safe -> retryable code.
                raise Bili2nbError(
                    "NETWORK_ERROR", "上传时连接中断，云端未收到该文件（可安全重试）。",
                    detail=type(exc).__name__,
                ) from exc
            # Live: each upload leaves an extra stuck registration (same name) behind. It is ours
            # because it appeared during THIS call.
            with contextlib.suppress(Exception):
                after = await client.sources.list(notebook_id)
                twins = {s.id for s in after if s.id not in before_ids and s.id != src.id
                         and s.title in names}
                await self._delete_owned(client, notebook_id, after, twins)
            return src.id

        return self._run(op, default="UPLOAD_FAILED", fresh=True)

    async def _delete_owned(self, client, notebook_id, sources, owned_ids) -> tuple[list[str], list[str]]:
        """Delete stuck (PREPARING/ERROR) sources whose id is in `owned_ids`. Never touches
        READY/PROCESSING or anything else. Returns (deleted, failed); failed ids are remembered
        in `_pending_orphans` so they can be persisted and retried. Cleanup never raises."""
        deleted: list[str] = []
        failed: list[str] = []
        for s in sources:
            if s.id not in owned_ids or s.status.name not in _STUCK:
                continue
            try:
                await client.sources.delete(notebook_id, s.id)
                deleted.append(s.id)
                self._pending_orphans.discard(s.id)
            except Exception:  # noqa: BLE001 - best effort, but remember it
                failed.append(s.id)
                self._pending_orphans.add(s.id)
        return deleted, failed

    def cleanup_owned(self, notebook_id: str, source_ids) -> list[str]:
        """Delete recorded orphans of this job that are STILL stuck. Returns the ids that remain
        (could not be deleted). Ids that vanished or became READY/PROCESSING are dropped, not deleted."""
        ids = set(source_ids)
        if not ids:
            return []
        sdk_compat.require("cleanup_owned")

        async def op(client):
            sources = await client.sources.list(notebook_id)
            _, failed = await self._delete_owned(client, notebook_id, sources, ids)
            return failed

        return self._run(op, default="NETWORK_ERROR", fresh=True)

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

    def finalize_sources(self, notebook_id: str, titles: dict[str, str]) -> tuple[list[str], list[str]]:
        """After READY: check what is REALLY in the notebook and apply the display titles.

        `titles` maps source id -> wanted title. Returns (missing, untitled): ids that are not in
        the cloud at all, and ids still carrying another title after a best-effort rename (the
        server often keeps the file name; harmless for content, so it is only a warning). Only
        sources this job recorded are renamed; nothing is ever deleted here."""

        async def op(client):
            have = {s.id: s for s in await client.sources.list(notebook_id)}
            missing = sorted(i for i in titles if i not in have)
            for sid, want in titles.items():
                if sid in have and have[sid].title != want:
                    with contextlib.suppress(Exception):  # cosmetic; reported via `untitled`
                        await client.sources.rename(notebook_id, sid, want)
            now = {s.id: s for s in await client.sources.list(notebook_id)}
            untitled = sorted(i for i, want in titles.items() if i in now and now[i].title != want)
            return missing, untitled

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
