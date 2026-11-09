"""The offline guard blocks the internet but must leave loopback usable (asyncio on Windows)."""

import socket

import pytest


def test_external_connect_is_blocked():
    s = socket.socket()
    try:
        with pytest.raises(RuntimeError, match="blocked"):
            s.connect(("93.184.216.34", 80))
    finally:
        s.close()


def test_loopback_connect_is_allowed():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    cli = socket.socket()
    try:
        cli.connect(("127.0.0.1", srv.getsockname()[1]))
    finally:
        cli.close()
        srv.close()


def test_asyncio_run_works_under_the_guard():
    import asyncio

    async def go():
        return 7

    assert asyncio.run(go()) == 7
