"""Merge per-part audio / PDF files into one upload source (only used when the notebook source
quota forces it). The per-part originals are never modified or removed."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from bili2nb.config import KEEP_AUDIO, MAX_UPLOAD_BYTES
from bili2nb.errors import Bili2nbError
from bili2nb.media import audio as am
from bili2nb.media import pdf as pdf_mod


def _duration(path: Path) -> float:
    return float(am.probe(path)["format"].get("duration", 0))


# ------------------------------------------------------------------------------------- audio
@dataclass
class AudioMerge:
    path: Path
    action: str  # "copy" (no re-encode) | "transcode"
    duration: float
    size: int
    offsets: list[float] = field(default_factory=list)  # start time of each input inside the output


def _signature(path: Path) -> tuple[str, str, int]:
    st = am.audio_stream(am.probe(path)) or {}
    return str(st.get("codec_name", "")), str(st.get("sample_rate", "")), int(st.get("channels") or 0)


def _list_entry(p: Path) -> str:
    return "file '" + p.resolve().as_posix().replace("'", "'\\''") + "'\n"


def merge_audio(files: list[Path], out: Path, *, max_bytes: int = MAX_UPLOAD_BYTES) -> AudioMerge:
    """Concatenate audio files. Same codec/rate/channels -> stream copy (no quality loss);
    otherwise one AAC/M4A re-encode. Verified: total duration, full decode, size limit."""
    am.require_tools()
    if not files:
        raise Bili2nbError("INTERNAL", "merge_audio 需要至少一个文件。")
    durations = [_duration(f) for f in files]
    total = sum(durations)
    offsets = [round(sum(durations[:i]), 2) for i in range(len(files))]
    sigs = {_signature(f) for f in files}
    codec = next(iter(sigs))[0]
    copy = len(sigs) == 1 and codec in KEEP_AUDIO
    if copy:
        out = out.with_suffix(KEEP_AUDIO[codec])
    tmp = out.with_name(f"{out.stem}.tmp{out.suffix}")
    out.parent.mkdir(parents=True, exist_ok=True)
    listing: Path | None = None
    try:
        if copy:
            fd, name = tempfile.mkstemp(suffix=".txt", dir=out.parent)
            listing = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.writelines(_list_entry(f) for f in files)
            cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                   "-vn", "-c", "copy", "-movflags", "+faststart", str(tmp)]
        else:
            chains = "".join(
                f"[{i}:a:0]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}];"
                for i in range(len(files)))
            joined = "".join(f"[a{i}]" for i in range(len(files)))
            graph = f"{chains}{joined}concat=n={len(files)}:v=0:a=1[out]"
            cmd = ["ffmpeg", "-v", "error", "-y"]
            for f in files:
                cmd += ["-i", str(f)]
            cmd += ["-filter_complex", graph, "-map", "[out]", "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart", str(tmp)]
        r = am._run(cmd, 7200)
        if r.returncode != 0 or not tmp.is_file():
            raise Bili2nbError("MEDIA_ERROR", "ffmpeg 合并音频失败。", detail=r.stderr[-200:])
        got = _duration(tmp)
        tol = max(2.0, 0.002 * total)
        if abs(got - total) > tol:
            raise Bili2nbError(
                "MEDIA_ERROR", f"合并音频时长 {got:.1f}s 与各 P 总和 {total:.1f}s 不符（容差 {tol:.1f}s）。")
        size = tmp.stat().st_size
        if size > max_bytes:
            raise Bili2nbError(
                "MEDIA_ERROR", f"合并后的音频 {size / 1e6:.0f} MB 超过上限 {max_bytes / 1e6:.0f} MB。")
        am.decode_check(tmp)
        os.replace(tmp, out)
        return AudioMerge(out, "copy" if copy else "transcode", got, size, offsets)
    finally:
        tmp.unlink(missing_ok=True)
        if listing is not None:
            listing.unlink(missing_ok=True)


# --------------------------------------------------------------------------------------- pdf
@dataclass
class PdfMerge:
    path: Path
    pages: int
    size: int


def _index_page(path: Path, title: str, labels: list[str], note_lines: list[str]) -> None:
    pdf_mod._font()
    font = pdf_mod.FONT
    w, h, m = pdf_mod.PAGE_W, pdf_mod.PAGE_H, pdf_mod.MARGIN
    c = canvas.Canvas(str(path), pagesize=(w, h))
    y = h - 70
    c.setFont(font, 22)
    for line in simpleSplit(title, font, 22, w - 2 * m - 20):
        c.drawString(m + 10, y, line)
        y -= 30
    y -= 10
    c.setFont(font, 13)
    for text in ["包含的分 P：", *labels, "", *note_lines]:
        for line in simpleSplit(text, font, 13, w - 2 * m - 20) or [""]:
            if y < m:
                break
            c.drawString(m + 10, y, line)
            y -= 19
    c.showPage()
    c.save()


def _copy_outline(reader: PdfReader, items, writer: PdfWriter, parent, base: int) -> None:
    last = None
    for it in items:
        if isinstance(it, list):
            if last is not None:
                _copy_outline(reader, it, writer, last, base)
        else:
            page = reader.get_destination_page_number(it)
            last = writer.add_outline_item(str(it.title), base + page, parent=parent)


def merge_pdfs(pdfs: list[Path], labels: list[str], out: Path, *, title: str, note_lines: list[str],
               max_bytes: int = MAX_UPLOAD_BYTES) -> PdfMerge:
    """One PDF: an index page, then every part's pages in part order. One top-level bookmark per
    part (its own bookmarks stay underneath); timestamp links are preserved."""
    if not pdfs or len(pdfs) != len(labels):
        raise Bili2nbError("INTERNAL", "merge_pdfs 参数不一致。")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.stem}.tmp{out.suffix}")
    idx = out.with_name(f"{out.stem}.index.tmp{out.suffix}")
    try:
        _index_page(idx, title, labels, note_lines)
        writer = PdfWriter()
        writer.add_metadata({"/Title": title})
        for page in PdfReader(str(idx)).pages:
            writer.add_page(page)
        writer.add_outline_item("目录 / Index", 0)
        for src, label in zip(pdfs, labels, strict=True):
            reader = PdfReader(str(src))
            base = len(writer.pages)
            for page in reader.pages:
                writer.add_page(page)
            top = writer.add_outline_item(label, base)
            _copy_outline(reader, reader.outline, writer, top, base)
        with open(tmp, "wb") as fh:
            writer.write(fh)
        size = tmp.stat().st_size
        if size > max_bytes:
            raise Bili2nbError(
                "MEDIA_ERROR", f"合并后的 PDF {size / 1e6:.0f} MB 超过上限 {max_bytes / 1e6:.0f} MB。")
        pages = len(PdfReader(str(tmp)).pages)
        os.replace(tmp, out)
        return PdfMerge(out, pages, size)
    finally:
        tmp.unlink(missing_ok=True)
        idx.unlink(missing_ok=True)
