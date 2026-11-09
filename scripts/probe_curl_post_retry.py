"""Does libcurl (via curl_cffi) silently re-send a POST when a REUSED connection dies without a response?

A local server answers one GET (keep-alive); on the next request it READS the POST fully, counts it
and closes the connection with NO response - like Google's "connection closed abruptly" after it has
already processed the request. Expected result (Gate 4 finding):

    default connection reuse -> server saw 2 POSTs   (hidden re-send: duplicate notebooks)
    FRESH_CONNECT+FORBID_REUSE -> server saw 1 POST  (the protection bili2nb applies)

Run: python scripts/probe_curl_post_retry.py   (local loopback only, no internet)
"""

from __future__ import annotations

import asyncio
import socket
import threading

from curl_cffi import CurlOpt
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

posts: list[int] = []


def server(port_holder: list[int], stop: threading.Event) -> None:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port_holder.append(srv.getsockname()[1])
    srv.settimeout(0.5)
    try:
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except TimeoutError:
                continue
            conn.settimeout(2.0)
            try:
                while data := conn.recv(65536):
                    if data.startswith(b"GET"):
                        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: keep-alive\r\n\r\nok")
                    elif data.startswith(b"POST"):
                        posts.append(1)  # processed, then die with NO response
                        break
            except OSError as exc:
                print(f"(server side connection error, ignored: {type(exc).__name__})")
            finally:
                conn.close()
    finally:
        srv.close()


async def run(opts: dict | None) -> tuple[int, str]:
    posts.clear()
    holder: list[int] = []
    stop = threading.Event()
    threading.Thread(target=server, args=(holder, stop), daemon=True).start()
    while not holder:
        await asyncio.sleep(0.05)
    base = f"http://127.0.0.1:{holder[0]}"
    kw = {"curl_options": opts} if opts else {}
    err = "no-error"
    async with AsyncSession(**kw) as session:
        await session.get(base + "/a")  # warm a keep-alive connection
        try:
            await session.post(base + "/create", data=b"title=x", timeout=5)
        except RequestException as exc:
            err = type(exc).__name__
    stop.set()
    return len(posts), err


async def main() -> None:
    print("default connection reuse    -> POSTs seen by server / client error:", await run(None))
    fresh = {CurlOpt.FRESH_CONNECT: 1, CurlOpt.FORBID_REUSE: 1}
    print("FRESH_CONNECT + FORBID_REUSE -> POSTs seen by server / client error:", await run(fresh))


if __name__ == "__main__":
    asyncio.run(main())
