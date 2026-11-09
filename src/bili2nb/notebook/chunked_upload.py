"""Chunked, resumable file-body upload for NotebookLM (Google "Scotty" protocol).

Why this exists (live finding, 2026-11-08, see docs/TEST_REPORT.md Gate 3/4): the SDK sends the
whole file in ONE finalize request. On this network the connection is closed ~8 s into that
request; files up to ~2 MB still land server-side, but a 37 MB audio never did. The resumable
protocol supports sending the body in pieces (`x-goog-upload-command: upload` + offset, the last
piece `upload, finalize`) and asking how much the server has (`query`), so each request is
short and a dropped response is resolved by asking the server instead of guessing.

`upload_in_chunks` is transport-agnostic (injected `post` / `query`) so it is unit-testable;
`install_chunked_upload` wires it into the SDK's upload pipeline for one client.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import IO

from bili2nb.errors import Bili2nbError

CHUNK_SIZE = 2 * 1024 * 1024  # <= the largest single body observed to land server-side
MAX_CHUNK_ATTEMPTS = 4  # per chunk; bounded, never an infinite loop


@dataclass(frozen=True)
class ChunkReply:
    """What the server said: HTTP status, upload status header, bytes it has received."""

    status_code: int
    upload_status: str | None  # "active" | "final" | None
    received: int | None


PostFn = Callable[[int, bytes, bool], Awaitable[ChunkReply]]  # (offset, data, finalize)
QueryFn = Callable[[], Awaitable[ChunkReply]]
Retryable = tuple[type[BaseException], ...]


async def upload_in_chunks(
    file_obj: IO[bytes],
    total: int,
    post: PostFn,
    query: QueryFn,
    *,
    retryable: Retryable,
    chunk_size: int = CHUNK_SIZE,
    max_attempts: int = MAX_CHUNK_ATTEMPTS,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Send `total` bytes from `file_obj` in chunks; resume from the server's offset on errors."""
    async def safe_query() -> ChunkReply | None:
        try:
            return await query()
        except retryable:
            return None

    async def read_at(pos: int) -> bytes:
        file_obj.seek(pos)
        return await asyncio.to_thread(file_obj.read, min(chunk_size, total - pos))

    offset = 0
    data = await read_at(0) if total else b""
    attempts = 0
    while True:
        finalize = offset + len(data) >= total
        try:
            reply = await post(offset, data, finalize)
            _require_ok(reply)
        except retryable:
            attempts += 1
            # The response may have been lost although the bytes arrived: ask the server.
            q = await safe_query()
            if q is not None and q.upload_status == "final":
                break  # server already has everything and finalized
            if attempts >= max_attempts:
                raise
            await sleep(min(2.0 * attempts, 8.0))
            if (q is not None and q.received is not None and 0 <= q.received <= total
                    and q.received != offset):  # resume exactly where the server is
                offset = q.received
                data = await read_at(offset) if offset < total else b""
            continue
        attempts = 0
        if finalize:
            break
        offset += len(data)
        if on_progress is not None:
            await on_progress(offset, total)
        data = await read_at(offset)
        if not data:
            raise Bili2nbError("MEDIA_ERROR", "读取待上传文件时提前遇到文件结尾。")
    if on_progress is not None:
        await on_progress(total, total)


def _require_ok(reply: ChunkReply) -> None:
    if not (200 <= reply.status_code < 300):
        raise Bili2nbError("UPLOAD_FAILED", f"上传服务返回 HTTP {reply.status_code}。")


# --------------------------------------------------------------------------- SDK glue
def install_chunked_upload(client, *, chunk_size: int = CHUNK_SIZE) -> None:
    """Replace the SDK pipeline's single-request body upload with the chunked one.

    Touches a private SDK attribute (`client.sources._uploader`). If the SDK layout is not what
    we verified this RAISES `SDK_INCOMPATIBLE`: silently keeping the SDK's single-request upload
    would re-introduce the live failure (large files cut off at ~8 s) while looking fine.
    """
    pipeline = getattr(getattr(client, "sources", None), "_uploader", None)
    needed = ("upload_file_streaming", "_client_factory", "_live_cookies", "_authuser_header",
              "_resolve_upload_timeout", "_track_transport_client", "_transport_clients")
    missing = [n for n in needed if pipeline is None or not hasattr(pipeline, n)]
    if pipeline is None or missing:
        raise Bili2nbError(
            "SDK_INCOMPATIBLE",
            "NotebookLM SDK 的上传管线结构与本工具验证过的不一致，已在发送任何文件之前停止"
            "（不会退回不可靠的单请求上传）。",
            detail="missing: " + (", ".join(missing) if pipeline is not None else "client.sources._uploader"),
        )
    import httpx
    from notebooklm._web.sources import upload as sdk_upload

    from .adapter import net_errors

    async def streaming(
        upload_url,
        file_obj,
        *,
        filename=None,
        on_progress=None,
        total_bytes=None,
        logger=None,
        expected_epoch,
    ):
        pipeline.assert_epoch(expected_epoch)
        try:
            url = sdk_upload._validate_resumable_upload_url(upload_url)
            origin = sdk_upload._upload_url_origin(url)
            base = {
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
                "x-goog-authuser": pipeline._authuser_header(),
                "Origin": origin,
                "Referer": f"{origin}/",
            }
            name = filename or "<file>"
            size = total_bytes if total_bytes is not None else _size(file_obj)

            async def send(headers: dict, data: bytes) -> ChunkReply:
                client_ = pipeline._client_factory()(
                    timeout=pipeline._resolve_upload_timeout(
                        None, httpx.Timeout(connect=10.0, read=120.0, write=60.0, pool=10.0)
                    ),
                    cookies=pipeline._live_cookies(expected_epoch),
                )
                pipeline._track_transport_client(client_, expected_epoch)
                try:
                    async with client_:
                        pipeline.assert_epoch(expected_epoch)
                        resp = await client_.post(url, headers={**base, **headers}, content=data)
                finally:
                    pipeline._transport_clients.discard(client_)
                sdk_upload.raise_for_upload_status(resp, name)
                got = resp.headers.get("x-goog-upload-size-received")
                return ChunkReply(
                    resp.status_code,
                    resp.headers.get("x-goog-upload-status"),
                    int(got) if got and got.isdigit() else None,
                )

            async def post(offset: int, data: bytes, finalize: bool) -> ChunkReply:
                cmd = "upload, finalize" if finalize else "upload"
                return await send(
                    {"x-goog-upload-command": cmd, "x-goog-upload-offset": str(offset)}, data
                )

            async def query() -> ChunkReply:
                return await send({"x-goog-upload-command": "query"}, b"")

            async def progress(done: int, total: int) -> None:
                if on_progress is not None:
                    await sdk_upload.maybe_await_callback(on_progress, done, total)

            await upload_in_chunks(
                file_obj, size, post, query,
                retryable=(*net_errors(), OSError),
                chunk_size=chunk_size,
                on_progress=progress,
            )
        finally:
            try:
                file_obj.close()  # the SDK hands the caller-owned file to this function
            except Exception:  # noqa: BLE001, S110 - best effort
                pass

    pipeline.upload_file_streaming = streaming


def _size(file_obj: IO[bytes]) -> int:
    file_obj.seek(0, 2)
    n = file_obj.tell()
    file_obj.seek(0)
    return n
