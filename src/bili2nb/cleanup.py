"""Precise local cleanup of the files ONE import job used (V1.2).

Called only after the cloud really holds every recorded source (the caller verifies that), or by
the explicit `bili2nb clean` command. It removes exactly what the job recorded:

  * each part's folder  (`parts[i]["dir"]`, or the single-part `output_dir`)  -- re-used ones too
  * the job's `<BV>/_merged/` folder (only if the job planned merged sources)
  * `<BV>/manifest.json` and `<BV>/` itself, ONLY when no other part folder is left inside

Part folders the job did not use (e.g. made earlier by `bili2nb convert`) and unrelated files are
never touched; the batch manifest is then rewritten to describe just the remaining parts.
Every path must resolve inside the output root (no symlink / `..` escapes); a failed removal is a
warning, never an error.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
from pathlib import Path

from bili2nb.jobs.db import Job
from bili2nb.layout import BATCH_MANIFEST, _raw_manifest
from bili2nb.selection import canonical

MERGED_DIR = "_merged"


def job_dirs(job: Job) -> list[str]:
    """Part folders this job recorded (absolute strings as stored; validated later)."""
    if job.parts:
        return [p["dir"] for p in job.parts if p.get("dir")]
    return [job.output_dir] if job.output_dir else []


def _inside(path: Path, root: Path) -> Path | None:
    """`path` resolved, if it is strictly inside `root` and is not itself a symlink; else None."""
    try:
        if path.is_symlink():
            return None
        real = path.resolve(strict=False)
        real.relative_to(root)
    except (OSError, ValueError):
        return None
    return None if real == root else real


def dir_size(path: Path) -> int:
    """Bytes under `path` without following symlinks; hard-linked files (upload staging) count once."""
    seen: set[tuple[int, int]] = set()
    total = 0
    for dp, _dn, files in os.walk(path, followlinks=False):
        for f in files:
            try:
                st = os.lstat(os.path.join(dp, f))
            except OSError:
                continue
            key = (st.st_dev, st.st_ino)
            if key in seen:
                continue
            seen.add(key)
            total += st.st_size
    return total


def _rewrite_manifest(batch: Path, remaining: list[Path]) -> bool:
    """Rebuild `<BV>/manifest.json` from the remaining parts' own manifests. False = left as is."""
    try:
        old = json.loads((batch / BATCH_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(old, dict):
        return False
    rows = []
    for d in remaining:
        m = _raw_manifest(d)
        if not m or not isinstance(m.get("part"), int):
            return False  # cannot describe it accurately: keep the original manifest
        row = {"part": m["part"], "cid": m.get("cid"), "title": m.get("part_title") or m.get("video_title", ""),
               "duration_sec": m.get("duration_sec"), "status": "done" if m.get("complete") else "pending",
               "dir": d.name}
        if m.get("audio", {}).get("file"):
            row["audio_file"] = m["audio"]["file"]
        if (d / "keyframes.pdf").is_file():
            row["pdf_file"] = "keyframes.pdf"
        row["visual"] = m.get("visual", {}).get("status")
        rows.append(row)
    rows.sort(key=lambda r: r["part"])
    doc = {**old, "selection": "P" + canonical([r["part"] for r in rows]),
           "part_count_processed": len(rows), "parts": rows}
    tmp = batch / f".{BATCH_MANIFEST}.{secrets.token_hex(3)}.tmp"
    try:
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(batch / BATCH_MANIFEST)
    except OSError:
        tmp.unlink(missing_ok=True)
        return False
    return True


def cleanup_job_files(job: Job, out_root: Path, *, protect: frozenset[Path] = frozenset(),
                      dry_run: bool = False) -> dict:
    """Remove this job's local files under `out_root` (`protect`: resolved folders that other live jobs
    still need, never removed; `dry_run`: only measure). Returns
    {"status": "done"|"skipped", "freed_bytes", "removed": [relative names], "kept_other_parts": [...],
     "manifest": ..., "warnings": [...]}. Never raises for filesystem problems."""
    root = Path(out_root).expanduser().resolve(strict=False)
    removed: list[str] = []
    warnings: list[str] = []
    kept_other: list[str] = []
    freed = 0
    manifest_state = None
    batches: set[Path] = set()  # batch folders (<BV>) that lost something

    def rm(path: Path, real: Path) -> bool:
        nonlocal freed
        size = dir_size(real) if real.is_dir() else 0
        if dry_run:
            freed += size
            removed.append(real.relative_to(root).as_posix())
            return True
        try:
            if real.is_dir():
                shutil.rmtree(real)
            else:
                real.unlink()
        except OSError as e:
            warnings.append(f"本地清理失败（已忽略）：{real.name}（{type(e).__name__}）")
            return False
        freed += size
        removed.append(real.relative_to(root).as_posix())
        return True

    # 1. part folders this job used
    for raw in dict.fromkeys(job_dirs(job)):
        d = Path(raw).expanduser()
        if not d.exists() and not d.is_symlink():
            continue
        real = _inside(d, root)
        if real is None:
            warnings.append(f"跳过输出根目录之外或符号链接的路径：{d.name}")
            continue
        if real in protect:
            warnings.append(f"保留（仍被其它任务使用）：{d.name}")
            continue
        if rm(d, real) and real.parent != root:
            batches.add(real.parent)

    # 2. merged files folder (only when this job planned merged sources)
    batch_dir = Path(job.output_dir).expanduser() if job.parts and job.output_dir else None
    if batch_dir is not None and any(it.get("merge") for it in (job.plan or {}).get("items", [])):
        merged = batch_dir / MERGED_DIR
        if merged.exists() or merged.is_symlink():
            real = _inside(merged, root)
            if real is None:
                warnings.append(f"跳过输出根目录之外或符号链接的路径：{MERGED_DIR}")
            elif real in protect:
                warnings.append(f"保留（仍被其它任务使用）：{MERGED_DIR}")
            elif rm(merged, real) and real.parent != root:
                batches.add(real.parent)
    if batch_dir is not None:
        real_b = _inside(batch_dir, root)
        if real_b is not None and real_b.is_dir() and real_b.parent == root:
            batches.add(real_b)

    # 3. batch folders: tidy up, never delete part folders we did not use
    if dry_run:
        batches = set()
    for batch in sorted(batches):
        if not batch.is_dir():
            continue
        try:
            left = sorted(p for p in batch.iterdir() if p.is_dir() and not p.is_symlink() and p.name != MERGED_DIR)
        except OSError:
            continue
        if left:
            kept_other += [f"{batch.name}/{p.name}" for p in left]
            if (batch / BATCH_MANIFEST).is_file():
                if _rewrite_manifest(batch, left):
                    manifest_state = "updated"
                else:
                    manifest_state = "kept"
                    warnings.append(f"{batch.name} 里还有其它分 P，批次清单无法准确更新，已原样保留。")
            continue
        try:
            for stray in [batch / BATCH_MANIFEST, *batch.glob(f".{BATCH_MANIFEST}.*.tmp")]:
                if stray.is_file() and not stray.is_symlink():
                    sz = stray.stat().st_size
                    stray.unlink()
                    freed += sz
                    removed.append(f"{batch.name}/{stray.name}")
            batch.rmdir()  # only succeeds when empty; unrelated files keep the folder
            removed.append(batch.name)
        except OSError:
            if any(batch.iterdir()):
                warnings.append(f"{batch.name} 里还有无关文件，文件夹已保留。")
    return {"status": "done" if removed else "skipped", "freed_bytes": freed, "removed": removed,
            "kept_other_parts": kept_other, "manifest": manifest_state, "warnings": warnings}


# ------------------------------------------------------------------ `bili2nb clean`
def _user_kept(job: Job) -> bool:
    return bool(job.options.get("keep_files") or job.options.get("output"))


def protected_dirs(jobs: list[Job], skip: set[str], out_root: Path) -> frozenset[Path]:
    """Folders other jobs still need: running jobs, failed/interrupted jobs that `retry` can resume,
    and anything the user asked to keep (--keep-files / --output)."""
    root = Path(out_root).expanduser().resolve(strict=False)
    keep: set[Path] = set()
    for j in jobs:
        if j.job_id in skip:
            continue
        live = j.status == "running" or (j.status in ("failed", "interrupted") and not j.superseded)
        if not (live or _user_kept(j)):
            continue
        for raw in job_dirs(j):
            real = _inside(Path(raw).expanduser(), root)
            if real is not None:
                keep.add(real)
        if j.parts and j.output_dir:
            real = _inside(Path(j.output_dir).expanduser() / MERGED_DIR, root)
            if real is not None:
                keep.add(real)
    return frozenset(keep)


def plan_clean(jobs: list[Job], out_root: Path, *, job_id: str | None = None) -> list[dict]:
    """What `bili2nb clean` would remove (read-only). Default candidates: failed/interrupted jobs that a newer
    job superseded, and completed jobs whose files are still on disk (and not user-kept). The newest
    failed job (retry needs it), running jobs and user-kept locations are never candidates; naming a
    `job_id` additionally allows the retryable failed job and a completed job whose cloud check failed."""
    out: list[dict] = []
    if job_id is not None:
        picked = [j for j in jobs if j.job_id == job_id]
        if not picked:
            raise KeyError(job_id)
        candidates = picked
    else:
        candidates = [j for j in jobs if (
            (j.status in ("failed", "interrupted") and j.superseded)
            or (j.status == "completed" and not j.files_cleaned
                and (j.options.get("cleanup") or {}).get("status") != "failed_verify"))]
    for j in candidates:
        entry = {"job_id": j.job_id, "video": j.video_title, "video_key": j.video_key, "status": j.status,
                 "size_bytes": 0, "removed": [], "skipped": None}
        if j.status == "running":
            entry["skipped"] = "任务正在运行，不会清理。"
        elif _user_kept(j):
            entry["skipped"] = "这是 --keep-files / --output 指定保留的位置，不会清理。"
        else:
            res = cleanup_job_files(j, out_root, dry_run=True,
                                    protect=protected_dirs(jobs, {j.job_id}, out_root))
            entry.update(size_bytes=res["freed_bytes"], removed=res["removed"])
        if job_id is not None or entry["size_bytes"] or entry["removed"]:
            out.append(entry)
    return out
