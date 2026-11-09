"""Windows-safety guards (the author develops on macOS; these catch POSIX-only habits)."""

from __future__ import annotations

import io
import sys
from pathlib import Path

from bili2nb import cli
from bili2nb.media import pdf as pm

SRC = Path(__file__).resolve().parents[2] / "src"


def _py_files():
    return [p for p in SRC.rglob("*.py")]


def test_text_file_io_always_specifies_encoding():
    """Windows' default text encoding is a legacy code page (e.g. GBK): a title with an emoji or a
    rare character would crash or corrupt manifest.json. Every text read/write must say utf-8."""
    import ast

    bad = []
    for p in _py_files():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name not in ("read_text", "write_text", "open"):
                continue
            if isinstance(f, ast.Attribute) and name == "open" and getattr(f.value, "id", "") in ("Image", "urllib"):
                continue
            if isinstance(f, ast.Attribute) and name == "open" and isinstance(f.value, ast.Attribute):
                continue  # urllib.request.urlopen-style chains
            kws = {k.arg for k in node.keywords}
            mode = next((a.value for a in node.args[1:2] if isinstance(a, ast.Constant)), None)
            mode = mode or next((k.value.value for k in node.keywords if k.arg == "mode"
                                 and isinstance(k.value, ast.Constant)), "")
            if "b" in str(mode) or "encoding" in kws:
                continue
            if name == "open" and not isinstance(f, ast.Name) and not node.args:
                continue  # e.g. Path.open() on a binary path is not used; skip bare method calls
            bad.append(f"{p.name}:{node.lineno}: {name}(...)")
    assert not bad, "text IO without encoding=:\n" + "\n".join(bad)


def test_subprocess_text_mode_decodes_as_utf8():
    for p in _py_files():
        txt = p.read_text(encoding="utf-8")
        if "text=True" in txt and "subprocess" in txt:
            assert 'encoding="utf-8"' in txt, f"{p.name}: subprocess text=True needs encoding='utf-8'"


# ---------------------------------------------------------------- CJK font candidates per OS
def test_font_candidates_cover_each_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert any("STHeiti" in c[0] for c in pm.platform_font_candidates())
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("WINDIR", r"C:\Windows")
    paths = [c[0].replace("\\", "/").lower() for c in pm.platform_font_candidates()]
    assert any(p.endswith("/fonts/simhei.ttf") for p in paths)  # plain TrueType: embeds reliably
    assert any(p.endswith("/fonts/msyh.ttc") for p in paths)
    monkeypatch.setattr(sys, "platform", "linux")
    assert pm.platform_font_candidates()  # never empty


# ---------------------------------------------------------------- stdout/stderr encoding
class _Stream(io.TextIOWrapper):
    pass


def test_ensure_utf8_streams_fixes_legacy_codepage_pipes(monkeypatch):
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    err = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    cli.ensure_utf8_streams()
    print("关键帧｜Excel 🙂")  # must not raise UnicodeEncodeError
    sys.stdout.flush()
    assert sys.stdout.encoding.lower().replace("-", "") == "utf8"
    print("警告 🙂", file=sys.stderr)
    assert sys.stderr.encoding.lower().replace("-", "") == "utf8"
