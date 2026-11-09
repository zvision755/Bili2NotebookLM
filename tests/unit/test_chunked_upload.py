"""Chunked resumable upload logic against a fake Scotty server (offline)."""

from __future__ import annotations

import asyncio
import io

import pytest

from bili2nb.errors import Bili2nbError
from bili2nb.notebook.chunked_upload import ChunkReply, upload_in_chunks


class Boom(Exception):
    """Stands in for a network-class error (response lost / connection closed)."""


class FakeServer:
    def __init__(self, total: int):
        self.total, self.received, self.final = total, 0, False
        self.posts: list[tuple[int, int, bool]] = []  # (offset, len, finalize)
        self.fail_plan: list[str] = []  # per post: "ok" | "lost-after" | "lost-before"
        self.query_fails = 0

    def _apply(self, offset, data, finalize):
        assert offset == self.received, f"server expects offset {self.received}, got {offset}"
        self.received += len(data)
        if finalize and self.received == self.total:
            self.final = True

    async def post(self, offset, data, finalize):
        self.posts.append((offset, len(data), finalize))
        mode = self.fail_plan.pop(0) if self.fail_plan else "ok"
        if mode == "lost-before":
            raise Boom("closed before the server got it")
        self._apply(offset, data, finalize)
        if mode == "lost-after":
            raise Boom("closed after the server got it")
        return self._reply()

    async def query(self):
        if self.query_fails:
            self.query_fails -= 1
            raise Boom("query dropped")
        return self._reply()

    def _reply(self):
        return ChunkReply(200, "final" if self.final else "active", self.received)


def run(total=10, chunk=4, plan=(), **kw):
    srv = FakeServer(total)
    srv.fail_plan = list(plan)
    progress: list[tuple[int, int]] = []

    async def on_progress(d, t):
        progress.append((d, t))

    async def nosleep(_):
        return None

    async def go():
        await upload_in_chunks(
            io.BytesIO(b"x" * total),
            total, srv.post, srv.query, retryable=(Boom,), chunk_size=chunk,
            on_progress=on_progress, sleep=nosleep, **kw,
        )

    asyncio.run(go())
    return srv, progress


def test_sends_sequential_chunks_and_only_last_is_finalize():
    srv, prog = run(total=10, chunk=4)
    assert srv.posts == [(0, 4, False), (4, 4, False), (8, 2, True)]
    assert srv.final and prog[-1] == (10, 10)


def test_exact_multiple_of_chunk_size_finalizes_on_last_chunk():
    srv, _ = run(total=8, chunk=4)
    assert srv.posts == [(0, 4, False), (4, 4, True)] and srv.final


def test_response_lost_but_chunk_landed_is_not_resent():
    srv, _ = run(total=10, chunk=4, plan=["lost-after"])
    assert srv.posts == [(0, 4, False), (4, 4, False), (8, 2, True)]  # no duplicate send
    assert srv.final


def test_chunk_not_landed_is_resent_from_same_offset():
    srv, _ = run(total=10, chunk=4, plan=["ok", "lost-before"])
    assert srv.posts == [(0, 4, False), (4, 4, False), (4, 4, False), (8, 2, True)]
    assert srv.final


def test_final_reply_lost_but_server_finalized_is_success_without_resend():
    srv, _ = run(total=10, chunk=4, plan=["ok", "ok", "lost-after"])
    assert len(srv.posts) == 3 and srv.final


def test_all_bytes_received_but_not_finalized_sends_empty_finalize():
    srv = FakeServer(8)
    srv.fail_plan = ["ok", "lost-after"]
    orig = srv._apply

    def no_final_on_first_try(offset, data, finalize):  # server got bytes, did not finalize
        orig(offset, data, False if len(srv.posts) == 2 else finalize)

    srv._apply = no_final_on_first_try

    async def nosleep(_):
        return None

    asyncio.run(upload_in_chunks(io.BytesIO(b"x" * 8), 8, srv.post, srv.query,
                                 retryable=(Boom,), chunk_size=4, sleep=nosleep))
    assert srv.posts[-1] == (8, 0, True) and srv.final  # empty finalize at the end


def test_persistent_failure_is_bounded_and_raises():
    srv = FakeServer(10)
    srv.fail_plan = ["lost-before"] * 100

    async def nosleep(_):
        return None

    with pytest.raises(Boom):
        asyncio.run(upload_in_chunks(io.BytesIO(b"x" * 10), 10, srv.post, srv.query,
                                     retryable=(Boom,), chunk_size=4, max_attempts=3,
                                     sleep=nosleep))
    assert len(srv.posts) == 3  # exactly max_attempts, not an endless loop


def test_query_failure_does_not_mask_retry():
    srv = FakeServer(6)
    srv.fail_plan = ["lost-before"]
    srv.query_fails = 1

    async def nosleep(_):
        return None

    asyncio.run(upload_in_chunks(io.BytesIO(b"x" * 6), 6, srv.post, srv.query,
                                 retryable=(Boom,), chunk_size=4, sleep=nosleep))
    assert srv.final


def test_non_retryable_error_propagates_immediately():
    class Fatal(Exception):
        pass

    async def post(o, d, f):
        raise Fatal("auth")

    async def query():
        raise AssertionError("must not query")

    with pytest.raises(Fatal):
        asyncio.run(upload_in_chunks(io.BytesIO(b"x" * 6), 6, post, query, retryable=(Boom,)))


def test_http_error_status_is_upload_failed():
    async def post(o, d, f):
        return ChunkReply(500, None, None)

    async def query():
        return ChunkReply(200, "active", 0)

    with pytest.raises(Bili2nbError) as e:
        asyncio.run(upload_in_chunks(io.BytesIO(b"x" * 3), 3, post, query, retryable=(Boom,)))
    assert e.value.code == "UPLOAD_FAILED"
