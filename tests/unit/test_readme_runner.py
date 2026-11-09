"""The README's install block is executed verbatim in CI (so the docs cannot rot)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("run_readme_block", ROOT / "scripts" / "run_readme_block.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SAMPLE = """# T
### Linux (x)

text

```bash
sudo apt install -y git   # comment
git clone https://example.com/r.git
cd Bili2NotebookLM
uv tool install . --overrides overrides.txt
bili2nb setup
bili2nb doctor
```

### Next
```bash
echo no
```
"""


def test_extracts_only_the_first_fenced_block_after_the_heading(runner):
    lines = runner.extract_block(SAMPLE, "### Linux")
    assert lines[0].startswith("sudo apt install") and "echo no" not in "\n".join(lines)


def test_missing_heading_is_an_error(runner):
    with pytest.raises(SystemExit):
        runner.extract_block(SAMPLE, "### Windows")


def test_skip_patterns_and_cd_rewrite(runner):
    lines = runner.extract_block(SAMPLE, "### Linux")
    out = runner.prepare(lines, skip=["bili2nb setup", "git clone"], cd_to="/work/space")
    text = "\n".join(out)
    executable = "\n".join(ln for ln in out if not ln.lstrip().startswith("#"))  # skipped lines stay as comments
    assert "bili2nb setup" not in executable and "git clone" not in executable
    assert any(ln.startswith("# skipped in CI: bili2nb setup") for ln in out)
    assert 'cd "/work/space"' in text and "cd Bili2NotebookLM" not in text
    assert "uv tool install . --overrides overrides.txt" in text and "bili2nb doctor" in text
    assert out[0].startswith("set -euo pipefail") and 'export PATH="$HOME/.local/bin' in text


def test_the_real_readme_linux_block_has_the_commands_ci_depends_on(runner):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    lines = runner.extract_block(readme, "### Linux")
    text = "\n".join(lines)
    for needle in ("apt install", "fonts-wqy-microhei", "astral.sh/uv/install.sh", "git clone",
                   "uv tool install . --overrides overrides.txt", "bili2nb doctor"):
        assert needle in text, f"README Linux block lost: {needle}"


def test_readme_windows_block_uses_the_verified_package_ids(runner):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    text = "\n".join(runner.extract_block(readme, "### Windows"))
    for needle in ("astral-sh.uv", "Gyan.FFmpeg", "Git.Git", "uv tool install . --overrides overrides.txt"):
        assert needle in text
