"""Convert every selected part of a video (the shared loop behind `convert` and `import`)."""

from __future__ import annotations

import hashlib
import sys
import time
from collections.abc import Callable
from pathlib import Path

from bili2nb import layout, pipeline
from bili2nb.bilibili import downloader as dl
from bili2nb.bilibili.parser import VideoRef, parse_url
from bili2nb.config import default_output_root
from bili2nb.errors import Bili2nbError
from bili2nb.retry import with_backoff


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def verify_distinct_audio(parts: list[dict]) -> None:
    """Guard against 'two different parts downloaded the same content' (wrong cid / P mixup)."""
    if len(parts) < 2:
        return
    seen: dict[str, int] = {}
    for r in parts:
        digest = _sha256(Path(r["audio_path"]))
        if digest in seen:
            raise Bili2nbError(
                "MEDIA_ERROR",
                f"P{seen[digest]} 与 P{r['part']} 的音频内容完全相同，下载结果不可信，已中止。",
                extra={"duplicate_parts": [seen[digest], r["part"]]},
            )
        seen[digest] = r["part"]


def _manifest_rows(infos: list[dict], status: dict[int, str], results: dict[int, dict]) -> list[dict]:
    rows = []
    for i in infos:
        r = results.get(i["part"])
        row = {"part": i["part"], "cid": i["cid"], "title": i["title"], "duration_sec": i["duration"],
               "status": status.get(i["part"], "pending")}
        if r:
            d = Path(r["output_dir"])
            row["dir"] = d.name
            row["audio_file"] = Path(r["audio_path"]).name
            if r.get("pdf_path"):
                row["pdf_file"] = Path(r["pdf_path"]).name
            row["visual"] = r.get("visual", {}).get("status")
        rows.append(row)
    return rows


def rows_from_job_parts(parts: list[dict]) -> list[dict]:
    """Batch-manifest rows from the importer's per-part state (relative names only)."""
    rows = []
    for p in parts:
        row = {"part": p["part"], "cid": p["cid"], "title": p["title"], "duration_sec": p["duration"],
               "status": {"done": "done", "failed": "failed"}.get(p.get("convert"), "pending")}
        if p.get("audio_path"):
            row["dir"] = Path(p["dir"]).name if p.get("dir") else Path(p["audio_path"]).parent.name
            row["audio_file"] = Path(p["audio_path"]).name
            if p.get("pdf_path"):
                row["pdf_file"] = Path(p["pdf_path"]).name
            row["visual"] = p.get("visual", {}).get("status")
        rows.append(row)
    return rows


def convert_parts(
    url: str,
    *,
    output_root: Path | None = None,
    frames_mode: str = "auto",
    part: int | None = None,
    audio_only: bool = False,
    log: Callable[[str], None] = _stderr,
    plan_fn: Callable[[VideoRef], list[dl.VideoInfo]] | None = None,
    convert_fn: Callable[..., dict] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    backoff_base: float = 3.0,
) -> dict:
    ref = parse_url(url)
    warnings: list[str] = []
    if part is not None:
        if ref.part is None:
            ref = VideoRef(ref.video_id, part)
        elif ref.part != part:
            warnings.append(f"URL 已指定分 P={ref.part}，忽略 --part {part}。")
    log(f"解析视频信息: {ref.url}")
    infos = (plan_fn or dl.plan_parts)(ref)
    total = infos[0].part_count if infos else 0
    selection = "all" if ref.part is None else f"P{ref.part}"
    log(f"共 {total} 个分 P，本次处理：{'全部' if ref.part is None else selection}（{len(infos)} 个）")
    out_root = (output_root or default_output_root()).expanduser()
    run = convert_fn or pipeline.convert
    meta_rows = [{"part": i.part, "cid": i.cid, "title": i.part_title or i.title, "duration": i.duration}
                 for i in infos]
    status: dict[int, str] = {}
    results: dict[int, dict] = {}
    video_id = infos[0].video_id
    series = infos[0].series_title or infos[0].title

    def save() -> None:
        layout.write_batch_manifest(
            out_root / video_id, video_id=video_id, title=series, selection=selection,
            parts=_manifest_rows(meta_rows, status, results))

    for info in infos:
        status[info.part] = "running"
        log(f"—— P{info.part}/{total}  {info.part_title or info.title}")
        try:
            res = with_backoff(
                lambda info=info: run(info.webpage_url, output_root=out_root, frames_mode=frames_mode,
                                      part=info.part, audio_only=audio_only, log=log, info=info),
                f"P{info.part} convert", log=log, sleep=sleep, base=backoff_base)
        except Bili2nbError as e:
            status[info.part] = "failed"
            save()
            e.extra["failed_part"] = info.part
            raise
        except KeyboardInterrupt:
            status[info.part] = "interrupted"
            save()
            raise
        results[info.part] = res
        status[info.part] = "done"
        save()
        warnings += [f"P{info.part}: {w}" for w in res.get("warnings", [])] if len(infos) > 1 else []
    ordered = [results[i.part] for i in infos]
    verify_distinct_audio(ordered)
    out = {
        "status": "ok", "video_id": video_id, "video_title": series, "selection": selection,
        "part_count": total, "parts": ordered, "warnings": warnings,
        "output_dir": str(out_root / video_id),
    }
    if len(ordered) == 1:  # legacy single-part shape stays valid (audio_path, visual, ... at top level)
        out = {**ordered[0], **out, "output_dir": ordered[0]["output_dir"],
               "warnings": [*ordered[0].get("warnings", []), *warnings]}
    return out
