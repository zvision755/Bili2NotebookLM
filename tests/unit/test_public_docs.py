"""Public-repo hygiene: nothing personal, no real notebook ids, no stale 'private report' text."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".git", ".venv", "tmp", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
TEXT_SUFFIXES = {".md", ".py", ".toml", ".yml", ".yaml", ".txt", ".json", ".cfg"}


def _files():
    for p in ROOT.rglob("*"):
        if p.is_file() and p.suffix in TEXT_SUFFIXES and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS):
            if p.name == "uv.lock":
                continue
            yield p


def _scan(pattern: str, *, allow: set[str] = frozenset()):
    rx = re.compile(pattern)
    hits = []
    for p in _files():
        rel = p.relative_to(ROOT).as_posix()
        if rel in allow:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if rx.search(line):
                hits.append(f"{rel}:{n}: {line.strip()[:100]}")
    return hits


def test_no_real_notebook_ids_or_urls():
    uuid = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    hits = _scan(r"notebooklm\.google\.com/notebook/" + uuid)
    # tests may use obviously fake ids built from a different shape; real uuids are never allowed
    assert not hits, "real-looking notebook URL:\n" + "\n".join(hits)


def test_no_personal_absolute_paths():
    hits = _scan(r"/Users/(?!<)[A-Za-z0-9._-]+/|C:\\Users\\(?!<|runneradmin)[A-Za-z0-9._-]+\\|/home/(?!runner)[a-z0-9._-]+/",
                 allow={"tests/unit/test_public_docs.py"})
    assert not hits, "personal absolute path:\n" + "\n".join(hits)


def test_no_stale_private_report_wording():
    hits = _scan(r"私有报告|勿外传|私有仓库|手机验证通过", allow={"tests/unit/test_public_docs.py"})
    assert not hits, "\n".join(hits)


def test_no_email_addresses_or_tokens():
    hits = _scan(r"[A-Za-z0-9._%+-]+@(gmail|outlook|hotmail|qq|163|126|smu|zvision)[A-Za-z0-9.-]*\.[a-z]{2,}"
                 r"|gho_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}",
                 allow={"tests/unit/test_public_docs.py"})
    assert not hits, "\n".join(hits)


def test_security_policy_and_credential_docs_exist():
    sec = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    for needle in ("storage_state.json", "AUTH_REQUIRED", "Report a vulnerability", "非官方"):
        assert needle in sec, f"SECURITY.md must mention {needle}"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## 凭证与隐私" in readme and "SECURITY.md" in readme


def test_every_error_code_is_documented_in_the_readme():
    from bili2nb.errors import EXIT_CODES

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for code in sorted(set(EXIT_CODES.values())):
        assert re.search(rf"\|\s*{code}\s*\|", readme), f"exit code {code} missing from README table"


def test_sdk_inventory_doc_covers_every_inventory_entry():
    from bili2nb.notebook.sdk_compat import PINNED_SDK, PRIVATE_API_INVENTORY

    doc = (ROOT / "docs" / "SDK_PRIVATE_API.md").read_text(encoding="utf-8")
    assert PINNED_SDK in doc
    for item in PRIVATE_API_INVENTORY:
        assert item["key"] in doc, f"docs/SDK_PRIVATE_API.md does not mention {item['key']}"
        assert item["why"]
