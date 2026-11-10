"""Decide WHAT to upload to the single NotebookLM notebook, given its source quota.

Pure logic (no files, no network). Strategy:
  per_part  every part uploads its own audio (+ its PDF when it has one)     -> used when it fits
            (unless the user asked for --merge-sources)
  merged    over the quota: consecutive parts form `g` ALIGNED balanced groups ("minimal"): a group's audio and its
            PDF cover exactly the same parts, and `g` is the LARGEST number whose total still fits the quota,
            so files stay as small as possible (a flaky link makes big uploads costly);
            on request (--merge-sources) into as FEW sources as the 200 MB file limit allows ("fewest")
  infeasible even that does not fit -> fail BEFORE creating any cloud resource (never a 2nd notebook)
"""

from __future__ import annotations

import math
import re
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


def parse_source_limit(value) -> int | None:
    """`--source-limit N`: an integer >= 2 (one audio + one PDF is the smallest useful notebook), or None."""
    if value is None:
        return None
    text = str(value).strip()
    if isinstance(value, bool) or not re.fullmatch(r"\d+", text) or int(text) < 2:
        raise Bili2nbError("INVALID_URL", f"--source-limit {value!s} 无效：需要不小于 2 的整数（例如 --source-limit 50）。")
    return int(text)


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


_LABEL_MAX = 40


def _range(parts: list[int]) -> str:
    """Honest name of a group of parts: `P01-P05` only for truly consecutive numbers (and runs of 3+),
    otherwise the members (`P01+P03+P05`, `P01-P04+P07`). Selections may skip parts."""
    runs: list[str] = []
    i = 0
    while i < len(parts):
        j = i
        while j + 1 < len(parts) and parts[j + 1] == parts[j] + 1:
            j += 1
        if j - i >= 2:
            runs.append(f"P{parts[i]:02d}-P{parts[j]:02d}")
        else:
            runs.extend(f"P{n:02d}" for n in parts[i:j + 1])
        i = j + 1
    return "+".join(runs)


def _group_label(parts: list[int]) -> str:
    """Source-title form of `_range`; keeps titles short for scattered selections."""
    full = _range(parts)
    if len(full) <= _LABEL_MAX:
        return full
    return f"P{parts[0]:02d}…P{parts[-1]:02d}（共{len(parts)}个分P）"


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


def _split_even(seq: list, g: int) -> list[list]:
    """Cut a sequence into `g` consecutive chunks whose sizes differ by at most 1 (larger chunks first)."""
    q, r = divmod(len(seq), g)
    out, i = [], 0
    for k in range(g):
        n = q + (1 if k < r else 0)
        out.append(seq[i:i + n])
        i += n
    return out


def _aligned_groups(parts: list[PartFiles], g: int, max_bytes: int) -> list[list[int]]:
    """`g` balanced consecutive groups of part numbers. A group whose merged audio OR merged PDF would exceed
    the byte cap is halved (audio and PDF together, so the boundaries stay aligned), repeatedly if needed."""
    by_part = {p.part: p for p in parts}

    def too_big(members: list[int]) -> bool:
        audio = sum(by_part[m].audio_bytes for m in members if by_part[m].audio_path)
        pdf = sum(by_part[m].pdf_bytes for m in members if by_part[m].pdf_path)
        return audio > max_bytes or pdf > max_bytes

    def halve(members: list[int]) -> list[list[int]]:
        if len(members) > 1 and too_big(members):
            mid = (len(members) + 1) // 2
            return halve(members[:mid]) + halve(members[mid:])
        return [members]

    out: list[list[int]] = []
    for chunk in _split_even([p.part for p in parts], g):
        out.extend(halve(chunk))
    return out


def _source_count(parts: list[PartFiles], groups: list[list[int]]) -> int:
    by_part = {p.part: p for p in parts}
    return sum((any(by_part[m].audio_path for m in grp)) + (any(by_part[m].pdf_path for m in grp)) for grp in groups)


def _aligned_plan(parts: list[PartFiles], limit: int, max_bytes: int) -> tuple[list[list[int]] | None, int]:
    """Largest group count whose total source count fits `limit`. Returns (groups or None, minimum needed)."""
    for g in range(len(parts), 0, -1):
        groups = _aligned_groups(parts, g, max_bytes)
        if _source_count(parts, groups) <= limit:
            return groups, 0
    return None, _source_count(parts, _aligned_groups(parts, 1, max_bytes))


def _infeasible(needed: int, limit: int, parts: list[PartFiles], audio: int, pdf: int) -> Bili2nbError:
    return Bili2nbError(
        "UPLOAD_PLAN_INFEASIBLE",
        f"这 {len(parts)} 个分 P 即使合并也需要至少 {needed} 个来源，超过账号的单笔记本来源上限 {limit}"
        f"（音频合计 {audio / MB:.0f} MB，PDF 合计 {pdf / MB:.0f} MB，单文件上限 200 MB）。"
        "尚未创建任何 NotebookLM 笔记本。请用 --part 缩小范围（如 --part 1-20），或使用来源额度更高的账号。",
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
    merge: bool = False,
) -> dict:
    """`merge=True` (`--merge-sources`): merge consecutive parts into the fewest sources even when the
    quota would allow one source per part. The default (False) merges only when it must."""
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
    base = {"source_limit": limit, "limit_origin": limit_origin, "merge_requested": bool(merge)}
    if len(per_part) <= limit and not merge:
        return {"strategy": "per_part", **base, "items": per_part}

    by_part = {p.part: p for p in parts}
    kinds = (
        ("audio", lambda p: p.audio_path, lambda p: p.audio_bytes, "音频"),
        ("pdf", lambda p: p.pdf_path, lambda p: p.pdf_bytes, "关键帧PDF"),
    )
    # chosen[kind] = [(group index, member parts that HAVE this kind)]; the index orders items group by group
    chosen: dict[str, list[tuple[int, list[int]]]] = {}
    extra: dict = {}
    if merge:
        mode = "fewest"
        for kind, getter_path, getter_size, _ in kinds:
            sized = [(p.part, getter_size(p)) for p in parts if getter_path(p)]
            chosen[kind] = list(enumerate(_pack(sized, group_max)))
        total = sum(len(v) for v in chosen.values())
        if total > limit:
            raise _infeasible(total, limit, parts, sum(p.audio_bytes for p in parts), sum(p.pdf_bytes for p in parts))
    else:
        mode = "minimal"
        groups, needed = _aligned_plan(parts, limit, group_max)
        if groups is None:
            raise _infeasible(needed, limit, parts, sum(p.audio_bytes for p in parts), sum(p.pdf_bytes for p in parts))
        for kind, getter_path, _, _ in kinds:
            chosen[kind] = [(gi, [m for m in grp if getter_path(by_part[m])]) for gi, grp in enumerate(groups)]
            chosen[kind] = [(gi, members) for gi, members in chosen[kind] if members]
        extra["merge_group_count"] = len(groups)

    entries: list[tuple[int, int, dict]] = []
    for ki, (kind, getter_path, getter_size, label) in enumerate(kinds):
        for gi, members in chosen[kind]:
            if len(members) == 1:
                p = by_part[members[0]]
                it = item(f"P{p.part:02d}:{kind}", kind, members, False, getter_path(p),
                          getter_size(p), part_source_title(p.part, p.title, kind))
            else:
                it = item(f"{_range(members)}:{kind}", kind, members, True, None,
                          sum(getter_size(by_part[m]) for m in members), f"{_group_label(members)}｜{label}")
            entries.append((gi if mode == "minimal" else 0, ki, it))
    entries.sort(key=lambda e: (e[0], e[1]))  # stable: "fewest" keeps all audio first, then all PDFs
    return {"strategy": "merged", **base, "merge_mode": mode, **extra,
            "merge_group_sizes": {k: [len(m) for _, m in chosen[k]] for k in ("audio", "pdf")},
            "items": [e[2] for e in entries]}


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
            "请用 --part 缩小范围（如 --part 1-20），或使用来源额度更高的账号。",
            extra={"needed_sources": min_audio_sources, "source_limit": limit, "part_count": len(durations)},
        )
