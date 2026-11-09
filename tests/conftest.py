import socket

import pytest


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="run opt-in live tests")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="live test: pass --live to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    """Unit tests are offline by default."""
    if "live" in request.keywords:
        return

    real_connect = socket.socket.connect

    def guarded(self, address, *a, **k):
        # Windows' asyncio builds its self-pipe with a loopback connect(), so loopback must stay
        # open or EVERY asyncio.run() fails there (seen on GitHub Actions). Anything else: blocked.
        host = address[0] if isinstance(address, tuple) else None
        if host in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, address, *a, **k)
        raise RuntimeError("network access blocked in unit tests")

    monkeypatch.setattr(socket.socket, "connect", guarded)


import re as _re

_ANSI = _re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def plain(text: str) -> str:
    """Strip ANSI colour codes (Typer forces colour on GitHub Actions) before asserting on help."""
    return _ANSI.sub("", text)
