"""Strict Bilibili URL validation (anti-SSRF) and b23.tv short-link resolution."""

from __future__ import annotations

import http.client
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlparse

from bili2nb.errors import Bili2nbError

ALLOWED_HOSTS = {"www.bilibili.com", "bilibili.com", "m.bilibili.com"}
SHORT_HOSTS = {"b23.tv"}
_VIDEO_PATH = re.compile(r"^/video/(BV[0-9A-Za-z]{10}|av\d+)/?$", re.IGNORECASE)
_UA = "Mozilla/5.0 (Macintosh) bili2nb"


@dataclass(frozen=True)
class VideoRef:
    video_id: str  # "BV..." or "av123"
    part: int | None  # explicit ?p=N, else None

    @property
    def url(self) -> str:
        base = f"https://www.bilibili.com/video/{self.video_id}"
        return f"{base}?p={self.part}" if self.part else base


_BARE_ID = re.compile(r"^(BV[0-9A-Za-z]{10}|av\d+)(\?p=\d+)?$", re.IGNORECASE)


def normalize_input(text: str) -> str:
    """Accept a bare BV/av number (yt-dlp style) and turn it into a canonical video URL.

    Anything else is returned stripped but unchanged, so the strict URL validation in
    `parse_url` stays the single gatekeeper (no new way around the host allow-list).
    """
    s = text.strip()
    m = _BARE_ID.match(s)
    if not m:
        return s
    vid, query = m.group(1), m.group(2) or ""
    vid = "BV" + vid[2:] if vid[:2].lower() == "bv" else "av" + vid[2:]
    return f"https://www.bilibili.com/video/{vid}{query}"


def _invalid(msg: str) -> Bili2nbError:
    return Bili2nbError("INVALID_URL", msg)


def _basic_checks(url: str):
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https"):
        raise _invalid("只支持 http/https 链接。")
    if u.username or u.password:
        raise _invalid("链接不能包含用户名/密码。")
    if u.port not in (None, 80, 443):
        raise _invalid("不允许自定义端口。")
    return u


def resolve_short(url: str, max_hops: int = 3, timeout: float = 10.0) -> str:
    """Follow b23.tv redirects manually; every hop host must be b23.tv or bilibili."""
    cur = url
    for _ in range(max_hops):
        u = _basic_checks(cur)
        host = (u.hostname or "").lower()
        if host in ALLOWED_HOSTS:
            return cur
        if host not in SHORT_HOSTS:
            raise _invalid(f"短链跳转到了不受信任的主机: {host}")
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        conn = http.client.HTTPSConnection(host, timeout=timeout)
        try:
            conn.request("GET", path, headers={"User-Agent": _UA})
            resp = conn.getresponse()
            status, loc = resp.status, resp.getheader("Location")
        except OSError as exc:
            raise Bili2nbError("NETWORK_ERROR", "解析 b23.tv 短链失败（网络）。") from exc
        finally:
            conn.close()
        if status not in (301, 302, 303, 307, 308) or not loc:
            raise _invalid(f"b23.tv 短链无法解析 (HTTP {status})。")
        cur = urljoin(cur, loc)
    u = _basic_checks(cur)
    if (u.hostname or "").lower() in ALLOWED_HOSTS:
        return cur
    raise _invalid("b23.tv 短链跳转次数过多。")


def parse_url(url: str, resolver: Callable[[str], str] = resolve_short) -> VideoRef:
    u = _basic_checks(url)
    host = (u.hostname or "").lower()
    if host in SHORT_HOSTS:
        u = _basic_checks(resolver(url))
        host = (u.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise _invalid("仅支持 bilibili.com 与 b23.tv 的视频链接。")
    m = _VIDEO_PATH.match(u.path)
    if not m:
        raise _invalid("不是单个视频链接（需要 /video/BV...）。")
    vid = m.group(1)
    vid = "BV" + vid[2:] if vid[:2].lower() == "bv" else vid.lower()
    part = None
    p = parse_qs(u.query).get("p")
    if p:
        if not p[0].isdigit() or int(p[0]) < 1:
            raise _invalid("分 P 参数 p 必须是正整数。")
        part = int(p[0])
    return VideoRef(video_id=vid, part=part)


def safe_name(s: str, max_len: int = 60) -> str:
    """Filesystem-safe fragment (no separators / control chars), length-trimmed."""
    s = re.sub(r'[\x00-\x1f/\\:*?"<>|]+', "_", s).strip(" ._")
    return (s or "video")[:max_len]
