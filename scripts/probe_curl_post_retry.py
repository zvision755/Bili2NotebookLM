"""Does libcurl (via curl_cffi) silently re-send a POST when a REUSED connection dies without a response?
Local server: conn#1 serves a GET (keep-alive); then on the next request it READS the POST fully, counts it,
and closes with no response (like Google's 'connection closed abruptly' after processing)."""
import asyncio, socket, threading, sys
from curl_cffi.requests import AsyncSession
from curl_cffi import CurlOpt

posts = []
def server(port_holder, stop):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(8); port_holder.append(s.getsockname()[1]); s.settimeout(0.5)
    while not stop.is_set():
        try: c, _ = s.accept()
        except socket.timeout: continue
        c.settimeout(2.0)
        try:
            while True:
                data = c.recv(65536)
                if not data: break
                if data.startswith(b"GET"):
                    c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: keep-alive\r\n\r\nok")
                elif data.startswith(b"POST"):
                    posts.append(1); break   # processed, then die with NO response
        except Exception: pass
        c.close()

async def run(opts):
    posts.clear(); holder=[]; stop=threading.Event(); t=threading.Thread(target=server,args=(holder,stop),daemon=True); t.start()
    while not holder: await asyncio.sleep(0.05)
    base=f"http://127.0.0.1:{holder[0]}"
    kw = {"curl_options": opts} if opts else {}
    async with AsyncSession(**kw) as s:
        await s.get(base+"/a")             # warm a keep-alive connection
        try: await s.post(base+"/create", data=b"title=x", timeout=5)
        except Exception as e: err=type(e).__name__
        else: err="no-error"
    stop.set(); return len(posts), err

async def main():
    print("默认连接复用   -> 服务端收到 POST 次数 / 客户端错误:", await run(None))
    print("FRESH_CONNECT -> 服务端收到 POST 次数 / 客户端错误:", await run({CurlOpt.FRESH_CONNECT: 1, CurlOpt.FORBID_REUSE: 1}))
asyncio.run(main())
