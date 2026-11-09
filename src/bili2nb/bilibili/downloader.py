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
    part_title: str = ""  # the 分P's own title (empty when unknown / single-part video)

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


def _view_json(video_id: str) -> tuple[int | None, str, dict]:
    """ONE call to the public view API (no login) -> (code, message, data). code None if unreachable."""
    import json
    import urllib.parse
    import urllib.request

    key = "bvid" if video_id.upper().startswith("BV") else "aid"
    val = video_id if key == "bvid" else video_id[2:]
    url = "https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode({key: val})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) bili2nb"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.load(r)
        code = int(body.get("code", -1))
        return code, str(body.get("message", "")), (body.get("data") or {}) if code == 0 else {}
    except Exception:  # noqa: BLE001 - optional enrichment; yt-dlp remains the fallback
        return None, "", {}


def fetch_view(video_id: str) -> tuple[int | None, str, list[dict]]:
    """Public view API (no login) -> (code, message, pages). code None if unreachable."""
    code, msg, data = _view_json(video_id)
    return code, msg, list(data.get("pages") or [])


@dataclass(frozen=True)
class VideoMeta:
    """Everything the view API says about a video, fetched once (all parts included)."""

    code: int | None  # None = unreachable
    message: str
    bvid: str
    title: str
    uploader: str
    pages: list[dict]


def fetch_video(video_id: str) -> VideoMeta:
    code, msg, data = _view_json(video_id)
    owner = data.get("owner") or {}
    return VideoMeta(
        code=code, message=msg, bvid=str(data.get("bvid") or ""), title=str(data.get("title") or ""),
        uploader=str(owner.get("name") or ""), pages=list(data.get("pages") or []),
    )


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


def _enumerate_with_ytdlp(ref: VideoRef) -> int:
    """Part count according to yt-dlp's playlist view (fallback when the view API is unusable)."""
    import yt_dlp

    try:
        with _ydl(_opts(noplaylist=False, extract_flat="in_playlist")) as ydl:
            d = ydl.extract_info(ref.url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise classify_download_error(str(exc)) from exc
    if d.get("_type") == "playlist":
        return len([e for e in d.get("entries", []) if e])
    return 1


def _part_info(meta: VideoMeta, ref: VideoRef, page: dict, n: int, total: int) -> VideoInfo | None:
    bvid = meta.bvid or ref.video_id
    pname = str(page.get("part", "")).strip()
    dur = page.get("duration")
    if not dur:
        return None  # metadata incomplete: the caller asks yt-dlp for this part only
    cid = page.get("cid")
    title = meta.title or bvid
    if total > 1 and pname and pname not in title:
        title = f"{title} (P{n} {pname})"
    return VideoInfo(
        video_id=bvid, cid=str(cid) if cid else None, part=n, title=title, uploader=meta.uploader,
        duration=float(dur), part_count=total, part_title=pname,
        webpage_url=VideoRef(bvid, n).url if total > 1 or ref.part else VideoRef(bvid, None).url,
    )


def plan_parts(ref: VideoRef) -> list[VideoInfo]:
    """Resolve WHICH parts to process: the explicit ?p=N, else ALL parts. Never a silent P1.

    One view-API call yields every part's cid/title/duration. If that API is unusable (down or
    risk-controlled) and no part was requested, yt-dlp is only used to COUNT the parts; if even that
    fails the user is told to pass --part N.
    """
    meta = fetch_video(ref.video_id)
    if meta.code not in (0, None) and meta.code not in _RISK_CODES:
        raise Bili2nbError(
            "VIDEO_UNAVAILABLE", f"视频不存在、已删除或不可见（B站返回 {meta.code}: {meta.message}）。"
        )
    pages = meta.pages
    if pages:
        total = len(pages)
        if ref.part and ref.part > total:
            raise Bili2nbError("VIDEO_UNAVAILABLE", f"该视频只有 {total} 个分 P，没有 P{ref.part}。")
        wanted = [ref.part] if ref.part else list(range(1, total + 1))
        infos: list[VideoInfo] = []
        for n in wanted:
            info = _part_info(meta, ref, pages[n - 1], n, total)
            if info is None:  # no duration in the API for this part
                info = fetch_info(VideoRef(ref.video_id, n))
            infos.append(info)
        return infos
    # View API gave no pages.
    if ref.part:
        return [fetch_info(ref)]
    try:
        total = _enumerate_with_ytdlp(ref)
    except Bili2nbError as e:
        raise Bili2nbError(
            e.code, f"{e.message} 无法确定该视频共有多少分 P，请用 --part N 指定要处理的分 P。", detail=e.detail
        ) from e
    return [fetch_info(VideoRef(ref.video_id, n)) for n in range(1, total + 1)]
