"""yt-dlp wrapper: metadata, audio-only stream, video-only stream (for frames)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bili2nb.bilibili.parser import VideoRef
from bili2nb.config import VIDEO_MAX_HEIGHT
from bili2nb.errors import Bili2nbError

AUDIO_FORMAT = "ba[ext=m4a]/ba"
VIDEO_FORMAT = (
    f"bv[height<={VIDEO_MAX_HEIGHT}][vcodec^=avc1]/bv[height<={VIDEO_MAX_HEIGHT}]"
    f"/bv*[height<={VIDEO_MAX_HEIGHT}]/bv"
)


@dataclass(frozen=True)
class VideoInfo:
    video_id: str  # BV...
    cid: str | None
    part: int
    title: str
    uploader: str
    duration: float
    webpage_url: str
    part_count: int = 1

    @property
    def video_key(self) -> str:
        return f"{self.video_id}_{self.cid}" if self.cid else f"{self.video_id}_p{self.part}"


def _opts(**extra):
    o = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        "noprogress": True,
        "logger": None,
    }
    o.update(extra)
    return o


def classify_download_error(msg: str) -> Bili2nbError:
    """Map yt-dlp error text to a stable code (never includes cookies/headers)."""
    low = msg.lower()
    if "412" in low or "429" in low or "too many requests" in low:
        return Bili2nbError("RATE_LIMITED", "B站限速/风控 (HTTP 412/429)，请稍后再试。")
    if any(k in low for k in ("403", "404", "unavailable", "not found", "login", "premium",
                              "private", "unsupported url", "region", "geo", "removed",
                              "no video formats", "keyerror", "extractor error")):
        return Bili2nbError("VIDEO_UNAVAILABLE", "视频不可用或无权访问（需要登录/地区限制/已删除）。",
                            detail=msg[:200])
    if any(k in low for k in ("timed out", "connection", "network", "temporary failure", "dns")):
        return Bili2nbError("NETWORK_ERROR", "下载时网络错误。", detail=msg[:200])
    return Bili2nbError("MEDIA_ERROR", "yt-dlp 下载/解析失败。", detail=msg[:200])


def _ydl(opts):
    import yt_dlp

    return yt_dlp.YoutubeDL(opts)


_RISK_CODES = {-412, -352, -799}  # risk-control/rate limiting: not proof the video is gone


def fetch_view(video_id: str) -> tuple[int | None, str, list[dict]]:
    """Public view API (no login) -> (code, message, pages). code None if unreachable."""
    import json
    import urllib.parse
    import urllib.request

    key = "bvid" if video_id.upper().startswith("BV") else "aid"
    val = video_id if key == "bvid" else video_id[2:]
    url = "https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode({key: val})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) bili2nb"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
        code = int(data.get("code", -1))
        pages = list((data.get("data") or {}).get("pages") or []) if code == 0 else []
        return code, str(data.get("message", "")), pages
    except Exception:  # noqa: BLE001 - optional enrichment; yt-dlp remains the fallback
        return None, "", []


def fetch_pages(video_id: str) -> list[dict]:
    return fetch_view(video_id)[2]


def fetch_info(ref: VideoRef) -> VideoInfo:
    import yt_dlp

    code, api_msg, pages = fetch_view(ref.video_id)
    if code not in (0, None) and code not in _RISK_CODES:
        raise Bili2nbError(
            "VIDEO_UNAVAILABLE", f"视频不存在、已删除或不可见（B站返回 {code}: {api_msg}）。"
        )
    want = ref.part or 1
    if pages and want > len(pages):
        raise Bili2nbError("VIDEO_UNAVAILABLE", f"该视频只有 {len(pages)} 个分 P，没有 P{want}。")
    try:
        with _ydl(_opts()) as ydl:
            d = ydl.extract_info(ref.url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise classify_download_error(str(exc)) from exc
    if d.get("_type") == "playlist":  # multi-P without explicit part: be explicit, take entry 1
        entries = [e for e in d.get("entries", []) if e]
        if not entries:
            raise Bili2nbError("VIDEO_UNAVAILABLE", "视频没有可用分 P。")
        d = entries[0]
    dur = d.get("duration")
    if not dur:
        raise Bili2nbError("MEDIA_ERROR", "无法获取视频时长。")
    vid = str(d.get("id", ref.video_id))
    bvid = vid.split("_p")[0]
    part = ref.part or 1
    cid = str(pages[part - 1]["cid"]) if pages else (str(d["cid"]) if d.get("cid") else None)
    title = d.get("title") or bvid
    if len(pages) > 1:
        pname = str(pages[part - 1].get("part", "")).strip()
        if pname and pname not in title:  # yt-dlp usually already appends the part name
            title = f"{title} (P{part} {pname})"
    return VideoInfo(
        video_id=bvid,
        cid=cid,
        part=part,
        part_count=len(pages) or 1,
        title=title,
        uploader=d.get("uploader") or d.get("channel") or "",
        duration=float(dur),
        webpage_url=ref.url,
    )


def _download(ref: VideoRef, fmt: str, dest_dir: Path, stem: str) -> Path:
    import yt_dlp

    dest_dir.mkdir(parents=True, exist_ok=True)
    opts = _opts(format=fmt, outtmpl=str(dest_dir / f"{stem}.%(ext)s"), nopart=True)
    try:
        with _ydl(opts) as ydl:
            ydl.download([ref.url])
    except yt_dlp.utils.DownloadError as exc:
        raise classify_download_error(str(exc)) from exc
    files = [p for p in dest_dir.glob(f"{stem}.*") if p.is_file()]
    if not files:
        raise Bili2nbError("MEDIA_ERROR", "下载完成但未找到输出文件。")
    return files[0]


def download_audio(ref: VideoRef, dest_dir: Path) -> Path:
    """Audio-only stream; no video download, no re-encode."""
    return _download(ref, AUDIO_FORMAT, dest_dir, "audio_src")


def download_video(ref: VideoRef, dest_dir: Path) -> Path:
    """Video-only stream (<=720p) used solely to pick keyframes."""
    return _download(ref, VIDEO_FORMAT, dest_dir, "video_src")
