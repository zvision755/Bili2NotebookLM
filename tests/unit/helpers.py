"""Synthetic media for offline tests (no network, no real videos)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np

W, H, FPS = 640, 360, 10


def slide(bg: int, text: str) -> np.ndarray:
    img = np.full((H, W, 3), bg, np.uint8)
    cv2.putText(img, text, (40, 180), cv2.FONT_HERSHEY_SIMPLEX, 2.2, (0, 0, 0), 6)
    return img


def black() -> np.ndarray:
    return np.zeros((H, W, 3), np.uint8)


def write_video(path: Path, segments: list[tuple[np.ndarray, float]]) -> float:
    """segments: (image, seconds). Returns total duration."""
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    assert vw.isOpened(), "OpenCV VideoWriter(mp4v) unavailable"
    total = 0.0
    for img, sec in segments:
        for _ in range(int(sec * FPS)):
            vw.write(img)
        total += sec
    vw.release()
    return total


def noisy_video(path: Path, seconds: float, seed: int = 0) -> float:
    """Constantly changing content (never stable) - like a music video / camera footage."""
    rng = np.random.default_rng(seed)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for _ in range(int(seconds * FPS)):
        vw.write(rng.integers(0, 255, (H, W, 3), dtype=np.uint8))
    vw.release()
    return seconds


def make_audio(path: Path, seconds: float, codec: str = "aac") -> None:
    args = {"aac": ["-c:a", "aac"], "flac": ["-c:a", "flac"], "mp3": ["-c:a", "libmp3lame"]}[codec]
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         *args, str(path)],
        check=True,
    )
