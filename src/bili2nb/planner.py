"""Decide WHAT to upload to the single NotebookLM notebook, given its source quota.

Pure logic (no files, no network). Strategy:
  per_part  every part uploads its own audio (+ its PDF when it has one)     -> used when it fits
            (unless the user asked for --merge-sources)
  merged    over the quota: consecutive parts are merged JUST ENOUGH to fit the quota ("minimal": PDFs first, as
            they are small and cheap to re-upload on a flaky link, audio only when PDFs alone cannot do it);
            on request (--merge-sources) into as FEW sources as the 200 MB file limit allows ("fewest")
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


def _groups_for(sized: list[tuple[int, int]], g: int, max_bytes: int) -> list[list[int]]:
    """`g` near-even consecutive groups of parts; a group over the byte cap is split again by size."""
    if not sized:
        return []
    sizes = dict(sized)
    out: list[list[int]] = []
    for chunk in _split_even([part for part, _ in sized], g):
        if sum(sizes[m] for m in chunk) > max_bytes and len(chunk) > 1:
            out.extend(_pack([(m, sizes[m]) for m in chunk], max_bytes))
        else:
            out.append(chunk)
    return out


def _minimal_groups(
    sized: dict[str, list[tuple[int, int]]], limit: int, max_bytes: int,
) -> dict[str, list[list[int]]] | None:
    """Fewest merges that bring the total source count within `limit`; PDFs are merged before audio.

    Stage 1: audio stays one source per part, PDFs go from "none merged" down to one group.
    Stage 2: PDFs sit at their minimum, audio goes from "none merged" down to its minimum.
    The first (least merged) candidate that fits wins. None when nothing fits."""
    audio, pdf = sized["audio"], sized["pdf"]

    def candidate(ga: int, gp: int) -> dict[str, list[list[int]]]:
        return {"audio": _groups_for(audio, ga, max_bytes), "pdf": _groups_for(pdf, gp, max_bytes)}

    def fits(c: dict[str, list[list[int]]]) -> bool:
        return len(c["audio"]) + len(c["pdf"]) <= limit

    for gp in range(len(pdf), 0, -1):
        c = candidate(len(audio), gp)
        if fits(c):
            return c
    gp_min = 1 if pdf else 0
    for ga in range(len(audio) - 1, 0, -1):
        c = candidate(ga, gp_min)
        if fits(c):
            return c
    return None


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
    sized = {kind: [(p.part, getter_size(p)) for p in parts if getter_path(p)]
             for kind, getter_path, getter_size, _ in kinds}
    fewest = _fewest_groups(sized, group_max)
    if merge:
        chosen, mode = fewest, "fewest"
        if sum(len(g) for g in chosen.values()) > limit:
            raise _too_many(chosen, limit, parts)
    else:
        chosen, mode = _minimal_groups(sized, limit, group_max), "minimal"
        if chosen is None:
            raise _too_many(fewest, limit, parts)

    items: list[dict] = []
    for kind, getter_path, getter_size, label in kinds:
        for members in chosen[kind]:
            if len(members) == 1:
                p = by_part[members[0]]
                items.append(item(f"P{p.part:02d}:{kind}", kind, members, False, getter_path(p),
                                  getter_size(p), part_source_title(p.part, p.title, kind)))
            else:
                items.append(item(f"{_range(members)}:{kind}", kind, members, True, None,
                                  sum(getter_size(by_part[m]) for m in members),
                                  f"{_group_label(members)}｜{label}"))
    return {"strategy": "merged", **base, "merge_mode": mode,
            "merge_group_sizes": {k: [len(g) for g in chosen[k]] for k in ("audio", "pdf")}, "items": items}


def _fewest_groups(sized: dict[str, list[tuple[int, int]]], max_bytes: int) -> dict[str, list[list[int]]]:
    return {kind: _pack(sized[kind], max_bytes) for kind in ("audio", "pdf")}


def _too_many(fewest: dict[str, list[list[int]]], limit: int, parts: list[PartFiles]) -> Bili2nbError:
    return _infeasible(sum(len(g) for g in fewest.values()), limit, parts,
                       sum(p.audio_bytes for p in parts), sum(p.pdf_bytes for p in parts))


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
