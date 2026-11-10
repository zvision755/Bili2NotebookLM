"""Which parts (分P) to process: the `--part` syntax and the names derived from a selection.

  8        one part          8-27     a range          1,3,5    a list          1-3,7    both

Pure logic (no network, no files). A selection of exactly one part is NOT a "selection": it keeps
the V1.1 single-part path and key (`BV_cid`), so older jobs keep matching.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence

from bili2nb.bilibili.parser import VideoRef
from bili2nb.errors import Bili2nbError

MAX_PART_NUMBER = 9999  # far above any real video (Bilibili caps a video at a few hundred parts)
_MAX_KEY_SUFFIX = 48  # longer canonical forms are hashed so keys stay short and stable
_USAGE = "可用写法：单个 8，范围 8-27，列表 1,3,5，混合 1-3,7（编号从 1 开始）。"
_NUM = re.compile(r"[0-9]+")


def _bad(spec: object, why: str) -> Bili2nbError:
    return Bili2nbError("INVALID_URL", f"--part {spec!s} 无法识别：{why}。{_USAGE}")


def _number(piece: str, spec: object) -> int:
    if not _NUM.fullmatch(piece):
        raise _bad(spec, f"“{piece}”不是正整数")
    n = int(piece)
    if n < 1:
        raise _bad(spec, f"分 P 编号从 1 开始，不能是 {n}")
    if n > MAX_PART_NUMBER:
        raise _bad(spec, f"分 P 编号 {n} 太大")
    return n


def parse_part_spec(spec: int | str) -> list[int]:
    """`8-27` / `1,3,5` / `1-3,7` / `8` -> sorted, de-duplicated part numbers. Raises INVALID_URL."""
    if isinstance(spec, int):
        if spec < 1:
            raise _bad(spec, "分 P 编号从 1 开始")
        return [spec]
    text = str(spec).strip()
    if not text:
        raise _bad(repr(spec), "不能为空")
    chosen: set[int] = set()
    for raw in text.split(","):
        piece = raw.strip()
        if not piece:
            raise _bad(spec, "逗号之间或首尾有空项")
        lo, sep, hi = piece.partition("-")
        if not sep:
            chosen.add(_number(piece, spec))
            continue
        a, b = _number(lo.strip(), spec), _number(hi.strip(), spec)
        if a > b:
            raise _bad(spec, f"范围 {piece} 写反了（应为 {b}-{a}）")
        chosen.update(range(a, b + 1))
    return sorted(chosen)


def canonical(parts: Iterable[int]) -> str:
    """Compact, deterministic spelling: [1,2,3,7] -> '1-3+7', [8..27] -> '8-27'."""
    nums = sorted(set(parts))
    runs: list[str] = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        runs.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return "+".join(runs)


def selection_label(parts: Sequence[int], total: int) -> str:
    """'all' when every part is selected, else 'P' + canonical (`P2`, `P8-27`, `P1-3+7`)."""
    nums = sorted(set(parts))
    if nums == list(range(1, total + 1)):
        return "all"
    return f"P{canonical(nums)}"


def selection_key(video_id: str, parts: Sequence[int], total: int) -> str:
    """Idempotency key of a multi-part import (two or more parts). Never collides with a
    single-part key (`BV_cid`): the subset form `BV_P8-27` / `BV_P1-3+7` contains '-' or '+'."""
    label = selection_label(parts, total)
    if label == "all":
        return f"{video_id}_all"
    if len(label) > _MAX_KEY_SUFFIX:
        label = "P#" + hashlib.sha256(canonical(parts).encode()).hexdigest()[:16]
    return f"{video_id}_{label}"


def resolve_part(ref: VideoRef, part: int | str | None) -> tuple[VideoRef, list[int] | None, list[str]]:
    """Combine the URL's ?p=N with `--part SPEC` -> (ref, multi-part selection or None, warnings).

    * ?p=N in the URL always wins (a warning when --part says something else);
    * a spec naming ONE part becomes that single part (the V1.1 path);
    * a spec naming several parts becomes `selection`; None means "all parts".
    The spec is validated even when ignored, so a typo never passes silently.
    """
    if part is None:
        return ref, None, []
    wanted = parse_part_spec(part)
    if ref.part is not None:
        warnings = [] if wanted == [ref.part] else [f"URL 已指定分 P={ref.part}，忽略 --part {part}。"]
        return ref, None, warnings
    if len(wanted) == 1:
        return VideoRef(ref.video_id, wanted[0]), None, []
    return ref, wanted, []
