#!/usr/bin/env python3
"""Run a fenced code block from README.md verbatim (so the install docs are tested, not trusted).

    run_readme_block.py --heading "### Linux" --skip "bili2nb setup" --skip "git clone" --cd-to "$PWD"

Interactive / network-clone steps are skipped explicitly; `cd Bili2NotebookLM` is rewritten to the
checkout that CI is testing, so the commit under test (not `main`) is what gets installed.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"


def extract_block(text: str, heading: str) -> list[str]:
    """Lines of the first ```bash block after the line that starts with `heading`."""
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith(heading))
    except StopIteration:
        raise SystemExit(f"heading {heading!r} not found in README") from None
    block: list[str] = []
    inside = False
    for ln in lines[start + 1:]:
        if not inside and ln.startswith("```"):
            inside = True
            continue
        if inside and ln.startswith("```"):
            return block
        if inside:
            block.append(ln)
        elif ln.startswith(("### ", "## ")):
            break
    raise SystemExit(f"no fenced code block found under {heading!r}")


def prepare(lines: list[str], skip: list[str], cd_to: str | None) -> list[str]:
    out = ["set -euo pipefail", 'export PATH="$HOME/.local/bin:$PATH"  # = "reopen the terminal"']
    for ln in lines:
        if any(re.search(p, ln) for p in skip):
            out.append(f"# skipped in CI: {ln}")
            continue
        if cd_to and re.match(r"\s*cd\s+Bili2NotebookLM\b", ln):
            out.append(f'cd "{cd_to}"')
            continue
        out.append(ln)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--heading", required=True)
    ap.add_argument("--skip", action="append", default=[], help="regex of lines NOT to run (repeatable)")
    ap.add_argument("--cd-to", default=None, help="replace `cd Bili2NotebookLM` with `cd <this>`")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    script = "\n".join(prepare(extract_block(README.read_text(encoding="utf-8"), args.heading),
                               args.skip, args.cd_to)) + "\n"
    print(script)
    if args.dry_run:
        return 0
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8") as fh:
        fh.write(script)
    return subprocess.run(["bash", fh.name], check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
