#!/usr/bin/env python3
"""CI smoke test for an installed bili2nb command (dev env, `python -m`, or the global launcher).

Runs only offline, credential-free paths: it never configures a Google login and never calls the
real NotebookLM create/upload API. It verifies, with EXACT expectations:

* `--help` exits 0;
* `doctor --json` exits 0, prints exactly one valid JSON object, every REQUIRED check is "ok";
  login/browser checks are OPTIONAL (CI has no Google account) and may warn;
* an unauthenticated `import --json` exits with the AUTH_REQUIRED code (20) and a JSON body whose
  `error_code` is AUTH_REQUIRED, *before* anything is downloaded;
* error paths (`INVALID_URL`, `JOB_NOT_FOUND`) return their exact codes;
* every `--json` stdout is a single valid JSON document (no progress bars, logs or credentials);
* no output contains cookie/token-like text.

Usage: ci_smoke.py [--cmd "bili2nb"] [--expect-auth-exit 20]
Exit status 0 = all good, 1 = at least one expectation failed (each is printed).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

#: doctor checks that must be "ok" in CI. google-login / login-browser are optional.
REQUIRED_DOCTOR_OK = ["python", "ffmpeg", "ffprobe", "yt-dlp", "opencv", "notebooklm-py",
                      "sdk-compat", "pdf-font", "work-dir"]
OPTIONAL_DOCTOR = {"google-login", "login-browser", "notebooklm-online"}
SECRET_MARKERS = ("SAPISID", "__Secure-", "Set-Cookie", "Cookie:", "ya29.", "storage_state\":")


class SmokeFailure(Exception):
    pass


def assert_no_secrets(label: str, text: str) -> None:
    for marker in SECRET_MARKERS:
        if marker in text:
            raise SmokeFailure(f"{label}: output contains credential-like text {marker!r}")


def parse_json_stdout(label: str, stdout: str):
    """stdout must be exactly ONE JSON document followed only by a newline."""
    body = stdout.strip("\n")
    if not body:
        raise SmokeFailure(f"{label}: stdout is empty (expected one JSON document)")
    try:
        obj = json.loads(body)
    except ValueError as exc:
        raise SmokeFailure(f"{label}: stdout is not a single valid JSON document ({exc})") from exc
    if body.count("\n") != 0 and "\n" in json.dumps(obj, ensure_ascii=False):  # pragma: no cover
        raise SmokeFailure(f"{label}: unexpected multi-line output")
    return obj


def doctor_problems(report: dict) -> list[str]:
    problems = []
    checks = {c.get("name"): c for c in report.get("checks", [])}
    for name in REQUIRED_DOCTOR_OK:
        c = checks.get(name)
        if c is None:
            problems.append(f"doctor: required check {name!r} is missing")
        elif c.get("status") != "ok":
            problems.append(f"doctor: required check {name!r} is {c.get('status')!r}, expected 'ok'")
    for name, c in checks.items():
        if name not in REQUIRED_DOCTOR_OK and name not in OPTIONAL_DOCTOR and c.get("status") == "fail":
            problems.append(f"doctor: check {name!r} failed")
    return problems


def resolve_cmd(cmd: list[str]) -> list[str]:
    """A bare `python`/`python3` means THIS interpreter (sys.executable), not whatever PATH finds.

    On the Windows runner, `python` resolved to the system Python that has no bili2nb installed
    (the project lives in uv's venv). Launchers such as `bili2nb` still go through PATH, which is
    exactly what the global-install job wants to test."""
    if cmd and cmd[0] in ("python", "python3"):
        return [sys.executable, *cmd[1:]]
    return list(cmd)


def _invoke(cmd: list[str], args: list[str], env: dict, cwd: str):
    return subprocess.run(
        [*cmd, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=cwd, timeout=180, check=False,
    )


def run(cmd: list[str], expect_auth_exit: int = 20) -> list[str]:
    """Return a list of failed expectations (empty = all passed)."""
    problems: list[str] = []
    cmd = resolve_cmd(cmd)
    with tempfile.TemporaryDirectory(prefix="b2n-smoke-") as tmp:
        tmp_p = Path(tmp)
        env = {
            **os.environ,
            "NOTEBOOKLM_HOME": str(tmp_p / "no-google-login"),  # guaranteed: no credentials
            "BILI2NB_HOME": str(tmp_p / "work"),
            "PYTHONUTF8": "1", "NO_COLOR": "1", "TERM": "dumb",
        }
        env.pop("NOTEBOOKLM_PROFILE", None)
        cwd = str(tmp_p)  # run from outside the project: proves the launcher is global

        def check(label: str, args: list[str], want_exit: int, *, want_code: str | None = None,
                  is_json: bool = True, doctor: bool = False):
            try:
                p = _invoke(cmd, args, env, cwd)
            except (OSError, subprocess.SubprocessError) as exc:
                problems.append(f"{label}: could not run {' '.join(cmd)}: {exc}")
                return
            try:
                assert_no_secrets(label + " stdout", p.stdout)
                assert_no_secrets(label + " stderr", p.stderr)
                if p.returncode != want_exit:
                    raise SmokeFailure(f"{label}: exit code {p.returncode}, expected {want_exit}\n"
                                       f"  stdout: {p.stdout[:300]!r}\n  stderr: {p.stderr[-300:]!r}")
                if is_json:
                    obj = parse_json_stdout(label, p.stdout)
                    if want_code is not None and obj.get("error_code") != want_code:
                        raise SmokeFailure(f"{label}: error_code {obj.get('error_code')!r}, expected {want_code!r}")
                    if doctor:
                        problems.extend(doctor_problems(obj))
            except SmokeFailure as exc:
                problems.append(str(exc))

        check("--help", ["--help"], 0, is_json=False)
        check("doctor --json", ["doctor", "--json"], 0, doctor=True)
        check("unauthenticated import", ["BV1VV4y1V7Pz", "--json"], expect_auth_exit,
              want_code="AUTH_REQUIRED")
        check("unauthenticated explicit import", ["import", "BV1VV4y1V7Pz", "--json"], expect_auth_exit,
              want_code="AUTH_REQUIRED")
        check("invalid url", ["convert", "https://example.com/x", "--json"], 10, want_code="INVALID_URL")
        check("unknown job", ["status", "no-such-job", "--json"], 30, want_code="JOB_NOT_FOUND")
        check("jobs --json", ["jobs", "--json"], 0)

        # AUTH_REQUIRED must be raised BEFORE anything is downloaded.
        work = tmp_p / "work" / "work"
        if work.exists() and any(work.iterdir()):
            problems.append("unauthenticated import left files in the work dir (it must fail before downloading)")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cmd", default="bili2nb", help='command to test, e.g. "bili2nb" or "python -m bili2nb"')
    ap.add_argument("--expect-auth-exit", type=int, default=20,
                    help="expected exit code of an unauthenticated import (AUTH_REQUIRED = 20)")
    args = ap.parse_args(argv)
    problems = run(shlex.split(args.cmd, posix=os.name != "nt"), args.expect_auth_exit)
    if problems:
        print("CI SMOKE FAILED:", file=sys.stderr)
        for p in problems:
            print(f" - {p}", file=sys.stderr)
        return 1
    print("CI smoke: all expectations met")
    return 0


if __name__ == "__main__":
    sys.exit(main())
