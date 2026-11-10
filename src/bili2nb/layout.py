"""Local output layout for (multi-part) videos.

    <output>/<BV>/manifest.json                 batch manifest: every part, its status and files
    <output>/<BV>/P01_<title>/{audio.m4a, keyframes.pdf, manifest.json}   one folder per part

A part's cache identity is its CID (stored in the part's own manifest), NOT its folder name, so a
renamed part title never triggers a re-download. V1.1 folders (`<BV>_<CID>`) are never touched,
moved or deleted; a complete one is simply re-used.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from bili2nb.bilibili.parser import safe_name

BATCH_MANIFEST = "manifest.json"


def part_dirname(part: int, title: str, total: int) -> str:
    width = max(2, len(str(total)))
    return f"P{part:0{width}d}_{safe_name(title, 40)}"


def read_manifest(d: Path) -> dict | None:
    """Manifest of a COMPLETE product only: marked complete AND its files really exist."""
    try:
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if m.get("complete") is not True or not (d / m.get("audio", {}).get("file", "")).is_file():
        return None
    if m.get("visual", {}).get("status") == "ok" and not (d / "keyframes.pdf").is_file():
        return None
    return m


def _raw_manifest(d: Path) -> dict | None:
    try:
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return m if isinstance(m, dict) else None


def _same_part(m: dict, info) -> bool:
    if m.get("video_id") != info.video_id:
        return False
    if info.cid:
        return str(m.get("cid")) == str(info.cid)
    return m.get("cid") in (None, "None", "") and m.get("part") == info.part


def resolve_part_dir(out_root: Path, info) -> Path:
    """Folder for this part: an existing one with the same CID wins; else the canonical new name."""
    batch = out_root / info.video_id
    if batch.is_dir():
        for sub in sorted(p for p in batch.iterdir() if p.is_dir()):
            m = _raw_manifest(sub)
            if m is not None and _same_part(m, info):
                return sub
    return batch / part_dirname(info.part, info.part_title or info.title, info.part_count)


def legacy_dir(out_root: Path, info) -> Path:
    return out_root / info.video_key


def write_batch_manifest(batch_dir: Path, *, video_id: str, title: str, parts: list[dict],
                         selection: str) -> None:
    """Atomic top-level manifest (temp file + rename). Holds ids/paths/status only, never secrets."""
    batch_dir.mkdir(parents=True, exist_ok=True)
    doc = {"schema": 1, "video_id": video_id, "video_title": title, "selection": selection,
           "part_count_processed": len(parts), "parts": parts}
    tmp = batch_dir / f".{BATCH_MANIFEST}.{secrets.token_hex(3)}.tmp"
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(batch_dir / BATCH_MANIFEST)
