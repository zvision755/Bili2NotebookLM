"""Environment hygiene: exactly ONE OpenCV distribution may be installed.

`scenedetect` hard-requires `opencv-python`; installed next to `opencv-python-headless` both
write into the same `cv2/` directory (uninstalling either breaks the other). pyproject drops the
GUI build via a uv override so only the headless one is present.
"""

from importlib import metadata


def test_only_headless_opencv_is_installed():
    names = {(d.metadata["Name"] or "").lower() for d in metadata.distributions()}
    assert "opencv-python-headless" in names
    assert "opencv-python" not in names, "opencv-python must not be installed alongside headless"


def test_cv2_still_works_for_frame_processing():
    import cv2
    import numpy as np

    img = np.zeros((90, 160, 3), dtype=np.uint8)
    assert cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).shape == (90, 160)
    assert hasattr(cv2, "VideoCapture")


def test_overrides_file_exists_for_uv_tool_install():
    """`uv tool install` ignores [tool.uv] in pyproject, so the same override ships as a file."""
    from pathlib import Path

    text = Path("overrides.txt").read_text(encoding="utf-8")
    assert "opencv-python; sys_platform == 'never'" in text
