"""Decide WHAT to upload to the single NotebookLM notebook, given its source quota.

Pure logic (no files, no network). Strategy:
  per_part  every part uploads its own audio (+ its PDF when it has one)     -> used when it fits
  merged    otherwise consecutive parts are merged into as FEW sources as the 200 MB file limit
            allows (audio and PDF grouped independently)
  infeasible even that does not fit -> fail BEFORE creating any cloud resource (never a 2nd notebook)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from bili2nb.errors import Bili2nbError

MB = 1024 * 1024
GROUP_MAX_BYTES = 190 * MB  # merged files stay well under NotebookLM's 200 MB per-file limit
DEFAULT_SOURCE_LIMIT = 50  # documented free-tier value; only a conservative FALLBACK
_MIN_AUDIO_BYTES_PER_SEC = 8000  # 64 kbps: deliberately LOW so the early estimate never rejects wrongly
_TITLE_MAX = 100


@dataclass(frozen=True)
class PartFiles:
    part: int
    title: str
    duration: float
    audio_path: str | None
    audio_bytes: int
    pdf_path: str | None = None
    pdf_bytes: int = 0


def _clip(prefix: str, title: str, suffix: str) -> str:
    room = _TITLE_MAX - len(prefix) - len(suffix)
    t = title if len(title) <= room else title[: max(room - 1, 1)] + "…"
    return f"{prefix}{t}{suffix}"


def part_source_title(part: int, title: str, kind: str) -> str:
    return _clip(f"P{part:02d}｜", title, "｜音频" if kind == "audio" else "｜关键帧PDF")


def legacy_source_title(kind: str, video_title: str) -> str:
    prefix = "音频" if kind == "audio" else "关键帧PDF"
    t = f"{prefix}｜{video_title}"
    return t if len(t) <= _TITLE_MAX else t[: _TITLE_MAX - 1] + "…"


def _range(parts: list[int]) -> str:
    return f"P{parts[0]:02d}-P{parts[-1]:02d}"


def _pack(sized: list[tuple[int, int]], max_bytes: int) -> list[list[int]]:
    """Greedy packing of CONSECUTIVE parts: provably the fewest groups for an ordered sequence."""
    groups: list[list[int]] = []
    cur: list[int] = []
    total = 0
    for part, size in sized:
        if cur and total + size > max_bytes:
            groups.append(cur)
            cur, total = [], 0
        cur.append(part)
        total += size
    if cur:
        groups.append(cur)
    return groups


def _infeasible(needed: int, limit: int, parts: list[PartFiles], audio: int, pdf: int) -> Bili2nbError:
    return Bili2nbError(
        "UPLOAD_PLAN_INFEASIBLE",
        f"这 {len(parts)} 个分 P 即使合并也需要至少 {needed} 个来源，超过账号的单笔记本来源上限 {limit}"
        f"（音频合计 {audio / MB:.0f} MB，PDF 合计 {pdf / MB:.0f} MB，单文件上限 200 MB）。"
        "尚未创建任何 NotebookLM 笔记本。请用 --part N 缩小范围，或使用来源额度更高的账号。",
        extra={"needed_sources": needed, "source_limit": limit, "part_count": len(parts),
               "audio_total_bytes": audio, "pdf_total_bytes": pdf},
    )


def plan_uploads(
    parts: list[PartFiles],
    *,
    limit: int,
    video_title: str,
    total_parts: int,
    limit_origin: str = "server",
    group_max: int | None = None,
) -> dict:
    group_max = GROUP_MAX_BYTES if group_max is None else group_max
    legacy = total_parts == 1 and len(parts) == 1

    def title_for(p: PartFiles, kind: str) -> str:
        return legacy_source_title(kind, video_title) if legacy else part_source_title(p.part, p.title, kind)

    def item(key: str, kind: str, members: list[int], merge: bool, path, size: int, title: str) -> dict:
        return {"key": key, "kind": kind, "parts": members, "merge": merge, "path": path,
                "bytes": size, "title": title, "source_id": None, "ready": False}

    per_part = []
    for p in parts:
        per_part.append(item(f"P{p.part:02d}:audio", "audio", [p.part], False, p.audio_path,
                             p.audio_bytes, title_for(p, "audio")))
        if p.pdf_path:
            per_part.append(item(f"P{p.part:02d}:pdf", "pdf", [p.part], False, p.pdf_path,
                                 p.pdf_bytes, title_for(p, "pdf")))
    base = {"source_limit": limit, "limit_origin": limit_origin}
    if len(per_part) <= limit:
        return {"strategy": "per_part", **base, "items": per_part}

    by_part = {p.part: p for p in parts}
    items: list[dict] = []
    for kind, getter_path, getter_size, label in (
        ("audio", lambda p: p.audio_path, lambda p: p.audio_bytes, "音频"),
        ("pdf", lambda p: p.pdf_path, lambda p: p.pdf_bytes, "关键帧PDF"),
    ):
        sized = [(p.part, getter_size(p)) for p in parts if getter_path(p)]
        for members in _pack(sized, group_max):
            if len(members) == 1:
                p = by_part[members[0]]
                items.append(item(f"P{p.part:02d}:{kind}", kind, members, False, getter_path(p),
                                  getter_size(p), part_source_title(p.part, p.title, kind)))
            else:
                items.append(item(f"{_range(members)}:{kind}", kind, members, True, None,
                                  sum(getter_size(by_part[m]) for m in members),
                                  f"{_range(members)}｜{label}"))
    if len(items) > limit:
        raise _infeasible(len(items), limit, parts, sum(p.audio_bytes for p in parts),
                          sum(p.pdf_bytes for p in parts))
    return {"strategy": "merged", **base, "items": items}


def estimate_feasibility(durations: list[float], *, limit: int, group_max: int | None = None) -> None:
    """Cheap pre-download check from durations alone, using a LOWER bound for audio size.

    Only rejects selections that cannot fit even in the best case, so it never blocks a valid import
    but saves downloading tens of hours of media that could never be uploaded anyway."""
    group_max = GROUP_MAX_BYTES if group_max is None else group_max
    lower_bound = sum(durations) * _MIN_AUDIO_BYTES_PER_SEC
    min_audio_sources = max(1, math.ceil(lower_bound / group_max))
    if min_audio_sources > limit:
        raise Bili2nbError(
            "UPLOAD_PLAN_INFEASIBLE",
            f"{len(durations)} 个分 P 共 {sum(durations) / 3600:.0f} 小时，音频至少需要 {min_audio_sources} 个来源"
            f"（单文件 200 MB 上限），超过账号上限 {limit}。尚未下载或创建任何内容。"
            "请用 --part N 缩小范围，或使用来源额度更高的账号。",
            extra={"needed_sources": min_audio_sources, "source_limit": limit, "part_count": len(durations)},
        )
