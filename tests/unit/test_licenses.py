"""License hygiene: project metadata is consistent and no dependency brings a restrictive license.

The project is MIT. It only *uses* (does not copy, modify or redistribute) its dependencies, which
are installed from PyPI by the user. A new dependency with a (A/L)GPL-style or non-commercial
license must be reviewed deliberately - this test makes it fail loudly instead of slipping in.
"""

from __future__ import annotations

import re
import tomllib
from importlib import metadata
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = re.compile(
    r"\b(MIT|MIT-CMU|MIT-0|BSD|BSD-2-Clause|BSD-3-Clause|0BSD|Apache|Apache-2\.0|ISC|ISCL|Unlicense|"
    r"Public Domain|MPL|MPL-2\.0|Mozilla Public License|PSF|PSF-2\.0|Python Software Foundation|"
    r"Zlib|CC0-1\.0|HPND)\b",
    re.IGNORECASE,
)
FORBIDDEN = re.compile(
    r"\b(AGPL|GPL|LGPL|GNU (Affero|Lesser|General)|SSPL|BUSL|Commons Clause|CC-BY-NC|proprietary)\b",
    re.IGNORECASE,
)
# Declared license metadata that is empty/ambiguous but verified manually (see docs/DEPENDENCY_LICENSES.md)
MANUALLY_VERIFIED: dict[str, str] = {}


def _license_of(dist: metadata.Distribution) -> str:
    m = dist.metadata
    if expr := m.get("License-Expression"):
        return expr
    classifiers = [c.split("::")[-1].strip() for c in (m.get_all("Classifier") or []) if c.startswith("License ::")]
    if classifiers:
        return "; ".join(classifiers)
    first = (m.get("License") or "").strip().splitlines()
    return first[0][:80] if first else ""


def _closure(root: str = "bili2nb") -> dict[str, metadata.Distribution]:
    seen: dict[str, metadata.Distribution] = {}
    stack = [root]
    while stack:
        name = canonicalize_name(stack.pop())
        if name in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        seen[name] = dist
        for raw in dist.requires or []:
            req = Requirement(raw)
            if req.marker is not None:
                wanted_extra = any(f'"{e}"' in str(req.marker) or f"'{e}'" in str(req.marker)
                                   for e in ("browser", "impersonate"))
                if "extra" in str(req.marker):
                    if not wanted_extra:
                        continue
                elif not req.marker.evaluate():
                    continue
            stack.append(req.name)
    return seen


def test_project_license_metadata_is_consistent():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert pyproject["license"]["text"] == "MIT"
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License") and "Permission is hereby granted, free of charge" in text
    holder = pyproject["authors"][0]["name"]
    assert f"Copyright (c) 2026 {holder}" in text
    assert f"[MIT](LICENSE) © 2026 {holder}" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert metadata.version("bili2nb") == pyproject["version"]


def test_every_dependency_has_a_permissive_license():
    problems = []
    for name, dist in sorted(_closure().items()):
        lic = MANUALLY_VERIFIED.get(name) or _license_of(dist)
        if not lic:
            problems.append(f"{name}: no license metadata (verify manually and add to MANUALLY_VERIFIED)")
        elif FORBIDDEN.search(lic):
            problems.append(f"{name}: restrictive license {lic!r}")
        elif not ALLOWED.search(lic):
            problems.append(f"{name}: unrecognised license {lic!r}")
    assert not problems, "dependency license review needed:\n" + "\n".join(problems)


def test_the_closure_walk_actually_finds_the_known_dependencies():
    names = set(_closure())
    for expected in ("typer", "yt-dlp", "notebooklm-py", "opencv-python-headless", "reportlab", "pypdf",
                     "pillow", "scenedetect", "curl-cffi", "playwright"):
        assert expected in names, f"{expected} missing: the license walk would be vacuous"


def test_license_doc_lists_every_direct_dependency():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    doc = (ROOT / "docs" / "DEPENDENCY_LICENSES.md").read_text(encoding="utf-8")
    for raw in pyproject["dependencies"]:
        name = Requirement(raw).name
        assert name in doc, f"docs/DEPENDENCY_LICENSES.md does not mention {name}"
    assert "MIT" in doc and "FFmpeg" in doc  # the bundled-FFmpeg note must stay documented


@pytest.mark.parametrize("bad", ["GPL-3.0", "AGPL-3.0-only", "LGPL-2.1", "GNU Lesser General Public License v3",
                                 "SSPL-1.0", "CC-BY-NC-4.0", "Proprietary"])
def test_the_forbidden_pattern_really_matches_restrictive_licenses(bad):
    assert FORBIDDEN.search(bad)
