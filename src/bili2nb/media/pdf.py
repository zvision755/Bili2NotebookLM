"""keyframes.pdf: cover page + one clear screenshot per page with clickable timestamp."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from bili2nb.config import MAX_UPLOAD_BYTES
from bili2nb.errors import Bili2nbError
from bili2nb.media.frames import Frame, hms

# Chinese text MUST use an EMBEDDED font. A non-embedded CID font (ReportLab's built-in
# STSong-Light) looks fine in desktop viewers (they substitute a local font) but NotebookLM's
# previewer/extractor has no such font and shows mojibake (live finding, Gate 5).
# We embed a subset (a few KB) of a TrueType CJK font that ships with macOS; override with
# BILI2NB_PDF_FONT=/path/to/font.ttf (TrueType outlines only).
FALLBACK_FONT = "STSong-Light"  # last resort only; reported via cjk_font_embedded()
def platform_font_candidates() -> list[tuple[str, int | None]]:
    """TrueType-outline CJK fonts that ship with each OS (ReportLab cannot embed CFF/OTF)."""
    if sys.platform == "darwin":
        return [
            ("/System/Library/Fonts/STHeiti Light.ttc", 0),
            ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", None),
        ]
    if sys.platform.startswith("win"):
        fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        return [
            (os.path.join(fonts, "simhei.ttf"), None),  # plain TTF: embeds most reliably
            (os.path.join(fonts, "msyh.ttc"), 0),
            (os.path.join(fonts, "simsun.ttc"), 0),
        ]
    return [
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
        ("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", None),
    ]


_FONT_CANDIDATES: list[tuple[str, int | None]] = platform_font_candidates()
FONT = FALLBACK_FONT
_resolved = False
_embedded = False
PAGE_W, PAGE_H = landscape(A4)
MARGIN = 30


def cjk_font_embedded() -> bool:
    """True if the last built PDF embeds its CJK font (False => fallback, warn the user)."""
    _font()
    return _embedded


def _font() -> None:
    global FONT, _resolved, _embedded
    if _resolved:
        return
    cands = list(_FONT_CANDIDATES)
    override = os.environ.get("BILI2NB_PDF_FONT")
    if override:
        cands.insert(0, (override, None))
    for i, (path, idx) in enumerate(cands):
        if not Path(path).is_file():
            continue
        name = f"B2NCJK{i}"
        ok = True
        try:
            font = TTFont(name, path, subfontIndex=idx) if idx is not None else TTFont(name, path)
            pdfmetrics.registerFont(font)
        except Exception:  # noqa: BLE001 - unsupported font file: try the next candidate
            ok = False
        if ok:
            FONT, _embedded, _resolved = name, True, True
            return
    try:
        pdfmetrics.getFont(FALLBACK_FONT)
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont(FALLBACK_FONT))
    FONT, _embedded, _resolved = FALLBACK_FONT, False, True


def timestamp_url(video_url: str, t: float) -> str:
    sep = "&" if "?" in video_url else "?"
    return f"{video_url}{sep}t={int(t)}"


def build_pdf(
    frames: list[Frame],
    out_path: Path,
    *,
    title: str,
    uploader: str,
    video_url: str,
    duration: float,
    created: datetime | None = None,
) -> Path:
    if not frames:
        raise Bili2nbError("INTERNAL", "build_pdf 需要至少一张截图。")
    _font()
    created = created or datetime.now()
    tmp = out_path.with_suffix(".pdf.tmp")
    c = canvas.Canvas(str(tmp), pagesize=(PAGE_W, PAGE_H))
    c.setTitle(title)
    c.setAuthor(uploader or "bili2nb")
    c.setSubject(video_url)

    # ---- cover
    y = PAGE_H - 70
    c.bookmarkPage("cover")
    c.addOutlineEntry("封面 / Cover", "cover", level=0)
    c.setFont(FONT, 24)
    for line in simpleSplit(title, FONT, 24, PAGE_W - 2 * MARGIN - 20):
        c.drawString(MARGIN + 10, y, line)
        y -= 32
    y -= 12
    c.setFont(FONT, 14)
    rows = [
        f"作者: {uploader or '-'}",
        f"原视频: {video_url}",
        f"处理日期: {created:%Y-%m-%d %H:%M}",
        f"视频时长: {hms(duration)}",
        f"截图数量: {len(frames)}",
    ]
    for r in rows:
        c.drawString(MARGIN + 10, y, r)
        if r.startswith("原视频"):
            w = pdfmetrics.stringWidth(r, FONT, 14)
            c.linkURL(video_url, (MARGIN + 10, y - 3, MARGIN + 10 + w, y + 12), relative=0)
        y -= 24
    y -= 10
    c.setFont(FONT, 11)
    for line in simpleSplit(
        "说明: 每页为视频中的一张代表性画面，页首时间戳可点击跳转到 B 站对应时间。"
        "截图时间与音频转录内容不保证自动精确对齐。",
        FONT, 11, PAGE_W - 2 * MARGIN - 20,
    ):
        c.drawString(MARGIN + 10, y, line)
        y -= 16
    c.showPage()

    # ---- one screenshot per page
    for i, f in enumerate(frames, 1):
        ts = hms(f.t)
        link = timestamp_url(video_url, f.t)
        key = f"p{i}"
        c.bookmarkPage(key)
        c.addOutlineEntry(f"{ts}  ({i}/{len(frames)})", key, level=0)
        c.setFont(FONT, 14)
        hy = PAGE_H - MARGIN - 8
        c.drawString(MARGIN, hy, f"[{ts}]")
        w = pdfmetrics.stringWidth(f"[{ts}]", FONT, 14)
        c.linkURL(link, (MARGIN, hy - 3, MARGIN + w, hy + 12), relative=0)
        c.setFont(FONT, 10)
        c.drawRightString(PAGE_W - MARGIN, hy, f"{i} / {len(frames)}")
        c.drawString(MARGIN + w + 14, hy, title[:70])
        with Image.open(f.path) as im:
            iw, ih = im.size
        box_w, box_h = PAGE_W - 2 * MARGIN, PAGE_H - 2 * MARGIN - 30
        scale = min(box_w / iw, box_h / ih)
        dw, dh = iw * scale, ih * scale
        c.drawImage(
            ImageReader(str(f.path)),
            (PAGE_W - dw) / 2,
            MARGIN + (box_h - dh) / 2,
            dw,
            dh,
        )
        c.showPage()
    c.save()
    os.replace(tmp, out_path)
    return out_path


def validate_pdf(path: Path, expected_pages: int, video_url: str) -> int:
    """Open with an independent parser; check page count, size and selectable text."""
    try:
        size = path.stat().st_size
        r = PdfReader(str(path))
        pages = len(r.pages)
        cover_text = r.pages[0].extract_text() or ""
    except Exception as exc:
        raise Bili2nbError("MEDIA_ERROR", "生成的 PDF 无法打开。", detail=type(exc).__name__) from exc
    if pages != expected_pages + 1 or pages < 2:
        raise Bili2nbError("MEDIA_ERROR", f"PDF 页数 {pages} 与预期 {expected_pages + 1} 不符。")
    if size <= 0 or size > MAX_UPLOAD_BYTES:
        raise Bili2nbError("MEDIA_ERROR", f"PDF 大小 {size} 字节不合规（上限 200 MB）。")
    if video_url.split("?")[0].split("/")[-1] not in cover_text:
        raise Bili2nbError("MEDIA_ERROR", "PDF 封面缺少可提取的原视频链接文本。")
    return pages
