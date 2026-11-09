"""Content keyframe selection (not codec I-frames).

Modes
  slides : stable-slide detection on a low-res timeline; keeps the most complete stable
           state of each slide (handles progressive build-ups), drops transitions/black.
  scene  : PySceneDetect cut points (+ timed samples inside long scenes); falls back to
           timed sampling with a warning if scene detection fails.
  auto   : slides, then timed sampling when slides found too little (talking head/screencast).
No OCR / VLM / semantic judging.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from bili2nb.config import MAX_FRAMES
from bili2nb.errors import Bili2nbError

THUMB = (160, 90)
PIX_DIFF = 25  # per-pixel gray difference counted as "changed"
STABLE_FRAC = 0.010  # <= this fraction changed between consecutive samples => stable
CHANGE_FRAC = 0.040  # > this vs segment anchor => a new slide/segment
DUP_FRAC = 0.020  # <= this vs an already kept frame => duplicate
JPEG_QUALITY = 88
# Burned-in subtitles live in the bottom band and change every few seconds while the slide
# stays the same (live Gate 4: PPT lecture with subtitles gave ~35% duplicate pages), so
# comparisons ignore the bottom 14% of the frame. The saved frames are never cropped.
SUBTITLE_BAND = 0.14
_BODY_ROWS = int(THUMB[1] * (1 - SUBTITLE_BAND))


@dataclass
class Frame:
    t: float
    path: Path


@dataclass
class FrameResult:
    frames: list[Frame] = field(default_factory=list)
    method: str = ""
    warnings: list[str] = field(default_factory=list)
    visual_skipped: str | None = None  # reason when there is genuinely nothing meaningful


def gray_thumb(img: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return cv2.resize(g, THUMB, interpolation=cv2.INTER_AREA)


def frac_diff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(cv2.absdiff(a[:_BODY_ROWS], b[:_BODY_ROWS]) > PIX_DIFF))


def is_blank(th: np.ndarray) -> bool:
    """Black / near-uniform (fade, black screen, empty transition)."""
    return float(th.mean()) < 12.0 or float(th.std()) < 4.0


def dedupe(items: list[tuple[float, np.ndarray]]) -> list[tuple[float, np.ndarray]]:
    kept: list[tuple[float, np.ndarray]] = []
    for t, th in items:
        if all(frac_diff(th, k) > DUP_FRAC for _, k in kept):
            kept.append((t, th))
    return kept


def sample_timeline(cap: cv2.VideoCapture, duration: float, step: float):
    """Sequentially decode once; keep low-res thumbs every `step` seconds."""
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    every = max(1, round(step * fps))
    out: list[tuple[float, np.ndarray]] = []
    idx = 0
    while True:
        if not cap.grab():
            break
        t = idx / fps
        if t > duration + 5:
            break
        if idx % every == 0:
            ok, img = cap.retrieve()
            if ok and img is not None:
                out.append((t, gray_thumb(img)))
        idx += 1
    return out


def pick_slides(samples: list[tuple[float, np.ndarray]]) -> list[tuple[float, np.ndarray]]:
    reps: list[tuple[float, np.ndarray]] = []
    anchor = None
    best = None
    n = len(samples)
    for i, (t, th) in enumerate(samples):
        if is_blank(th):
            continue
        nxt = samples[i + 1][1] if i + 1 < n else th
        if frac_diff(th, nxt) > STABLE_FRAC:
            continue  # still transitioning/animating
        if anchor is None:
            anchor, best = th, (t, th)
        elif frac_diff(th, anchor) > CHANGE_FRAC:
            reps.append(best)  # close the previous segment with its most complete state
            anchor, best = th, (t, th)
        else:
            best = (t, th)  # same slide, later (more complete) stable state
    if best is not None:
        reps.append(best)
    return dedupe(reps)


def timed_times(duration: float, interval: float) -> list[float]:
    """Evenly spaced sample centers, at least one even for very short videos."""
    n = max(1, math.ceil(duration / interval))
    step = duration / n
    return [step * (k + 0.5) for k in range(n)]


def grab_at(cap: cv2.VideoCapture, t: float):
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
    ok, img = cap.read()
    return img if ok and img is not None else None


def _scene_times(video: Path, duration: float) -> list[float]:
    from scenedetect import AdaptiveDetector, detect

    scenes = detect(str(video), AdaptiveDetector(), show_progress=False)
    times: list[float] = []
    bounds = [(s.seconds, e.seconds) for s, e in scenes] or [(0.0, duration)]
    for s, e in bounds:
        span = max(0.0, e - s)
        times.append(s + min(2.0, span / 2))
        k = 1
        while s + 30.0 * k < e - 5:  # long scene (screen recording): add timed samples
            times.append(s + 30.0 * k)
            k += 1
    return sorted({round(min(t, duration - 0.5), 2) for t in times})


def _thin(items: list, limit: int) -> list:
    if len(items) <= limit:
        return items
    idxs = np.linspace(0, len(items) - 1, limit).round().astype(int)
    return [items[i] for i in sorted(set(idxs.tolist()))]


def _extract(cap, times: list[float], duration: float):
    """Full-res frames at times -> [(t, img, thumb)] excluding unreadable/blank."""
    out = []
    for t in times:
        t = min(t, duration - 0.1)
        img = grab_at(cap, t)
        if img is None:
            continue
        th = gray_thumb(img)
        if is_blank(th):
            continue
        out.append((t, img, th))
    return out


def select_frames(
    video: Path, duration: float, mode: str, frames_dir: Path, *, step: float | None = None
) -> FrameResult:
    if mode not in ("auto", "slides", "scene"):
        raise Bili2nbError("INTERNAL", f"bad frames mode: {mode}")
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise Bili2nbError("MEDIA_ERROR", "无法解码视频流（OpenCV 打不开）。")
    res = FrameResult(method=mode)
    try:
        step = step or (2.0 if duration <= 1800 else 3.0)
        times: list[float]
        if mode == "scene":
            try:
                times = _scene_times(video, duration)
            except Exception as exc:  # noqa: BLE001 - fall back, never pretend "no visuals"
                res.warnings.append(
                    f"SceneDetect 失败 ({type(exc).__name__})，已回退为定时取帧（每 30 秒）。"
                )
                res.method = "timed-fallback"
                times = timed_times(duration, 30.0)
            cands = _extract(cap, times, duration)
            kept = dedupe([(t, th) for t, _, th in cands])
            keep_t = {t for t, _ in kept}
            chosen = [(t, img) for t, img, _ in cands if t in keep_t]
        else:
            samples = sample_timeline(cap, duration, step)
            if not samples:
                raise Bili2nbError("MEDIA_ERROR", "视频流没有可读取的帧。")
            reps = pick_slides(samples)
            times = [t for t, _ in reps]
            need = max(3, int(duration // 120))
            if mode == "auto" and len(reps) < need:
                res.warnings.append(
                    f"画面变化很少（幻灯片检测到 {len(reps)} 张），已补充定时取帧。"
                )
                res.method = "auto+timed"
                times = sorted(set(times) | set(timed_times(duration, max(30.0, duration / 40))))
            cands = _extract(cap, times, duration)
            kept = dedupe([(t, th) for t, _, th in cands])
            keep_t = {t for t, _ in kept}
            chosen = [(t, img) for t, img, _ in cands if t in keep_t]
        if len(chosen) > MAX_FRAMES:
            res.warnings.append(f"候选帧 {len(chosen)} 超过上限 {MAX_FRAMES}，已均匀抽稀。")
            chosen = _thin(chosen, MAX_FRAMES)
        if not chosen:
            res.visual_skipped = "未找到有意义的画面（全黑/纯色/无法读取）。"
            return res
        frames_dir.mkdir(parents=True, exist_ok=True)
        for i, (t, img) in enumerate(chosen, 1):
            p = frames_dir / f"frame_{i:04d}.jpg"
            if not cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]):
                raise Bili2nbError("MEDIA_ERROR", "写入截图失败。")
            res.frames.append(Frame(t=float(t), path=p))
        return res
    finally:
        cap.release()


def hms(t: float) -> str:
    t = max(0, math.floor(t))
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"
