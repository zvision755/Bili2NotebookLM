"""convert pipeline: URL -> verified audio (+ keyframes.pdf) + manifest.json.

Work happens in a temp dir and is published with an atomic rename, so a half-written
output can never look like a complete cache (manifest.json is written last, inside the
staged dir, before the rename).
"""

from __future__ import annotations

import json
import secrets
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from bili2nb import __version__, layout
from bili2nb.bilibili import downloader as dl
from bili2nb.bilibili.parser import VideoRef, parse_url
from bili2nb.config import default_output_root, work_root
from bili2nb.errors import Bili2nbError
from bili2nb.media import audio as audio_mod
from bili2nb.media import frames as frames_mod
from bili2nb.media import pdf as pdf_mod

FRAME_MODES = ("auto", "slides", "scene", "none")


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _check_disk(duration: float, want_video: bool) -> None:
    need = int(100e6 + duration * (0.8e6 if want_video else 0.05e6))
    free = shutil.disk_usage(work_root()).free
    if free < need:
        raise Bili2nbError(
            "DISK_FULL", f"磁盘剩余 {free / 1e9:.1f} GB，预计至少需要 {need / 1e9:.1f} GB。"
        )


_read_manifest = layout.read_manifest  # kept name; the logic lives with the layout


def _publish(stage: Path, final: Path) -> None:
    """Atomic publish: rename (same volume) or copy to a sibling temp then rename."""
    final.parent.mkdir(parents=True, exist_ok=True)
    try:
        stage.rename(final)
        return
    except OSError:
        pass
    tmp = final.with_name(f"{final.name}.partial-{secrets.token_hex(4)}")
    try:
        shutil.copytree(stage, tmp)
        tmp.rename(final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def convert(
    url: str,
    *,
    output_root: Path | None = None,
    frames_mode: str = "auto",
    part: int | None = None,
    audio_only: bool = False,
    log: Callable[[str], None] = _stderr,
    resolver: Callable[[str], str] | None = None,
    info: dl.VideoInfo | None = None,
) -> dict:
    if frames_mode not in FRAME_MODES:
        raise Bili2nbError("INTERNAL", f"bad frames mode {frames_mode}")
    if audio_only:
        frames_mode = "none"
    t_start = time.monotonic()
    timings: dict[str, float] = {}

    def stage(name: str, t0: float) -> None:
        timings[name] = round(time.monotonic() - t0, 2)
        log(f"  [{name}] {timings[name]}s")

    t0 = time.monotonic()
    ref = parse_url(url, resolver) if resolver else parse_url(url)
    warnings: list[str] = []
    if part is not None:
        if ref.part is None:
            ref = VideoRef(ref.video_id, part)
        elif ref.part != part:
            warnings.append(f"URL 已指定分 P={ref.part}，忽略 --part {part}。")
    audio_mod.require_tools()  # fail early, before any download
    log(f"解析视频信息: {ref.url}")
    info = info or dl.fetch_info(ref)
    stage("metadata", t0)
    if info.cid is None:
        warnings.append("未能获取 CID，video_key 使用分 P 序号代替。")
    log(f"标题: {info.title} | 时长 {frames_mod.hms(info.duration)} | key {info.video_key}")

    out_root = (output_root or default_output_root()).expanduser()
    want_visual = frames_mode != "none"
    final = layout.resolve_part_dir(out_root, info)
    cached = _read_manifest(final) if final.exists() else None
    if cached is None:  # a complete V1.1-style folder (<BV>_<CID>) is re-used read-only, never moved
        old = layout.legacy_dir(out_root, info)
        old_m = _read_manifest(old) if old.is_dir() else None
        if old_m is not None and old_m.get("options", {}).get("frames_mode") == frames_mode:
            final, cached = old, old_m
    if cached and cached.get("options", {}).get("frames_mode") == frames_mode:
        log(f"已存在完整产物，直接复用: {final}")
        cached["reused"] = True
        out = {**cached, "status": "ok", "output_dir": str(final),
               "audio_path": str(final / cached["audio"]["file"])}
        if cached.get("visual", {}).get("status") == "ok":
            out["pdf_path"] = str(final / "keyframes.pdf")
        return out
    if final.exists() and cached is None and not (final / "manifest.json").exists():
        raise Bili2nbError("INTERNAL", f"目标目录已存在且不是 bili2nb 产物: {final}")
    _check_disk(info.duration, want_visual)

    work = Path(tempfile.mkdtemp(prefix=f"{info.video_key}_", dir=work_root()))
    try:
        stage_dir = work / "stage"
        stage_dir.mkdir()
        # ---- audio
        t0 = time.monotonic()
        log("下载音频流（仅音频，不下整段视频）…")
        src = dl.download_audio(ref, work / "dl")
        dl_bytes = src.stat().st_size
        log(f"校验/处理音频（{src.suffix}, {dl_bytes / 1e6:.1f} MB）…")
        aud = audio_mod.prepare_audio(src, stage_dir, info.duration)
        stage("audio", t0)
        log(f"音频: {aud.path.name} {aud.codec} {aud.duration:.1f}s {aud.size / 1e6:.1f}MB ({aud.action})")

        # ---- visual
        visual: dict
        pdf_path: Path | None = None
        if not want_visual:
            visual = {"status": "skipped", "reason": "audio-only (--frames none / --audio-only)"}
        else:
            t0 = time.monotonic()
            log("下载视频流（≤720p，仅用于选取关键帧）…")
            vsrc = dl.download_video(ref, work / "dl")
            dl_bytes += vsrc.stat().st_size
            stage("video_download", t0)
            t0 = time.monotonic()
            log(f"选取关键帧（模式 {frames_mode}）…")
            fr = frames_mod.select_frames(vsrc, info.duration, frames_mode, work / "frames")
            stage("frames", t0)
            warnings += fr.warnings
            if fr.visual_skipped:
                visual = {"status": "visual_skipped", "reason": fr.visual_skipped}
                warnings.append(f"visual_skipped: {fr.visual_skipped}")
            else:
                t0 = time.monotonic()
                pdf_path = stage_dir / "keyframes.pdf"
                pdf_mod.build_pdf(
                    fr.frames, pdf_path, title=info.title, uploader=info.uploader,
                    video_url=info.webpage_url, duration=info.duration,
                )
                pages = pdf_mod.validate_pdf(pdf_path, len(fr.frames), info.webpage_url)
                if not pdf_mod.cjk_font_embedded():
                    warnings.append(
                        "未找到可嵌入的中文字体，PDF 封面中文在 NotebookLM 预览中可能显示为乱码"
                        "（可用环境变量 BILI2NB_PDF_FONT 指定 TrueType 字体）。"
                    )
                stage("pdf", t0)
                visual = {
                    "status": "ok", "method": fr.method, "frame_count": len(fr.frames),
                    "pages": pages, "pdf_bytes": pdf_path.stat().st_size,
                    "frame_times": [round(f.t, 1) for f in fr.frames],
                }
                log(f"PDF: {pages} 页, {pdf_path.stat().st_size / 1e6:.1f} MB, {len(fr.frames)} 张截图")

        elapsed = round(time.monotonic() - t_start, 2)
        manifest = {
            "schema": 1,
            "complete": True,
            "bili2nb_version": __version__,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "video_key": info.video_key,
            "video_id": info.video_id,
            "cid": info.cid,
            "part": info.part,
            "part_title": info.part_title,
            "video_title": info.title,
            "uploader": info.uploader,
            "source_url": info.webpage_url,
            "duration_sec": round(info.duration, 1),
            "options": {"frames_mode": frames_mode},
            "audio": {
                "file": aud.path.name, "codec": aud.codec, "action": aud.action,
                "duration_sec": round(aud.duration, 1), "bytes": aud.size,
            },
            "visual": visual,
            "downloaded_bytes": dl_bytes,
            "timings_sec": timings,
            "elapsed_sec": elapsed,
            "warnings": warnings,
        }
        (stage_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if final.exists():  # our own previous output (different options): replace
            old = final.with_name(f"{final.name}.old-{secrets.token_hex(3)}")
            final.rename(old)
            _publish(stage_dir, final)
            shutil.rmtree(old, ignore_errors=True)
        else:
            _publish(stage_dir, final)
        result = {
            **manifest,
            "status": "ok",
            "output_dir": str(final),
            "audio_path": str(final / aud.path.name),
            "reused": False,
        }
        if pdf_path is not None:
            result["pdf_path"] = str(final / "keyframes.pdf")
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)
