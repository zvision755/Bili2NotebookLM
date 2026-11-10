from __future__ import annotations

import json
import os
import sys
from enum import Enum
from pathlib import Path

import typer
from typer.core import TyperGroup

from bili2nb import config
from bili2nb.bilibili.parser import normalize_input
from bili2nb.errors import Bili2nbError


def ensure_utf8_streams() -> None:
    """Never crash on Chinese/emoji output: Windows pipes default to a legacy code page (GBK...).

    Real consoles keep their own encoding (only made lenient); redirected streams become UTF-8."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        enc = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if stream is None or not hasattr(stream, "reconfigure") or enc == "utf8":
            continue
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


_GROUP_FLAGS = {"--help", "--install-completion", "--show-completion"}


class _DefaultImportGroup(TyperGroup):
    """yt-dlp style: `bili2notebooklm <URL|BV号> [options]` == `bili2notebooklm import ...`.

    If the first argument is not a known sub-command (or a group-level flag), it is treated
    as the arguments of `import`. Known sub-commands are never hijacked.
    """

    def main(self, *args, **kwargs):
        ensure_utf8_streams()
        return super().main(*args, **kwargs)

    def parse_args(self, ctx, args):
        if args and args[0] not in self.commands and args[0] not in _GROUP_FLAGS:
            args = ["import", *args]
        return super().parse_args(ctx, args)


app = typer.Typer(
    cls=_DefaultImportGroup,
    help="Bilibili video -> NotebookLM (audio + keyframe PDF).\n\n"
    "Usage: bili2notebooklm <B站链接 或 BV号>   (first run guides you through setup)\n\n"
    "Multi-part (分P) videos: all parts go into ONE notebook unless you pick some with ?p=N or "
    "--part (8, 8-27, 1,3,5, 1-3,7).",
    no_args_is_help=True,
)
auth_app = typer.Typer(help="NotebookLM authentication.", no_args_is_help=True)
app.add_typer(auth_app, name="auth")


def _fail(err: Bili2nbError, as_json: bool) -> typer.Exit:
    if as_json:
        out = {"status": "failed", "error_code": err.code, "message": err.message}
        out.update(err.extra)
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    else:
        typer.echo(f"[{err.code}] {err.message}", err=True)
        for k in ("job_id", "notebook_url", "retry_hint"):
            if k in err.extra:
                typer.echo(f"  {k}: {err.extra[k]}", err=True)
    return typer.Exit(err.exit_code)


def _make_store():
    from bili2nb.jobs.db import JobStore

    return JobStore()


def _setup_flow(work_dir: Path | None, browser: str | None, login: bool | None, as_json: bool) -> dict:
    """Choose the work dir and (optionally) log in to Google. `login=None` means ask."""
    interactive_dir = work_dir is None
    if interactive_dir:
        current = config.data_dir()
        typed = typer.prompt("工作目录（存放任务记录和临时文件，回车使用默认）", default=str(current))
        work_dir = Path(typed.strip() or str(current))
    chosen = config.save_work_dir(work_dir)
    if not as_json:
        typer.echo(f"工作目录: {chosen}")
        if os.environ.get("BILI2NB_HOME"):
            typer.echo("  注意：环境变量 BILI2NB_HOME 已设置，它会优先于这里的设置。", err=True)
    state = "skipped"
    if login is None and not as_json:
        login = typer.confirm("现在打开浏览器登录 Google（登录状态只保存在本机，不会经过本工具）？", default=True)
    if login:
        _interactive_login(browser, interactive=not as_json and _is_tty())
        state = "done"
    return {"status": "ok", "work_dir": str(chosen), "login": state}


def _run_setup_wizard() -> None:
    _setup_flow(None, None, None, False)


def _ensure_setup(interactive: bool) -> None:
    """First run in a real terminal: guide the user once. Never prompts non-interactively."""
    if interactive and not config.is_configured():
        typer.echo("首次使用：先做一次简单设置。")
        _run_setup_wizard()


def _is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _interactive_login(browser: str | None, *, interactive: bool) -> None:
    """The ONE login entry point (setup / auth login / AUTH_REQUIRED offer): same default everywhere."""
    from bili2nb.notebook import auth

    auth.login_with_fallback(
        browser, interactive=interactive, confirm=lambda msg: typer.confirm(msg, default=True)
    )


def _make_importer():
    from bili2nb.importer import Importer
    from bili2nb.jobs.db import JobStore
    from bili2nb.notebook.adapter import NotebookAdapter

    return Importer(JobStore(), NotebookAdapter())


def _with_login_offer(fn, as_json: bool):
    """Run fn(); on AUTH_REQUIRED offer a one-time login ONLY in an interactive terminal."""
    try:
        return fn()
    except Bili2nbError as e:
        interactive = sys.stdin.isatty() and sys.stdout.isatty() and not as_json
        if e.code != "AUTH_REQUIRED" or not interactive:
            raise
        typer.echo(f"[AUTH_REQUIRED] {e.message}", err=True)
        if not typer.confirm("现在打开浏览器登录 Google？", default=True):
            raise
        _interactive_login(None, interactive=True)
        return fn()  # preflight runs before any download, so re-running is safe


@app.command("setup")
def setup(
    work_dir: Path = typer.Option(None, "--work-dir", help="Work directory (skips the prompt)."),
    browser: str = typer.Option(
        None, help="chromium (default on every OS; downloaded once, ~170 MB), or chrome / msedge to use an installed one."
    ),
    no_login: bool = typer.Option(False, "--no-login", help="Do not open the Google login."),
    as_json: bool = typer.Option(False, "--json", help="Single JSON object on stdout."),
):
    """First-time setup: choose the work directory and log in to Google (once)."""
    try:
        res = _setup_flow(work_dir, browser, False if no_login else None, as_json)
    except Bili2nbError as e:
        raise _fail(e, as_json) from None
    if as_json:
        sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")


@app.command("doctor")
def doctor_cmd(
    online: bool = typer.Option(False, "--online", help="Also make one real, read-only NotebookLM call."),
    as_json: bool = typer.Option(False, "--json", help="Single JSON object on stdout."),
):
    """Check this computer: Python, ffmpeg, libraries, work dir, Google login. Changes nothing."""
    from bili2nb import doctor

    report = doctor.run_checks(online=online)
    if as_json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False) + "\n")
    else:
        typer.echo(f"bili2nb doctor — {report['platform']}")
        tag = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}
        for c in report["checks"]:
            typer.echo(f"{tag[c['status']]} {c['name']:<18} {c['detail']}")
            if c["hint"] and c["status"] != "ok":
                typer.echo(f"       修复: {c['hint']}")
        typer.echo({"ok": "结果：全部正常。", "warn": "结果：可以使用，但有提示项（见 WARN）。",
                    "fail": "结果：有必须修复的问题（见 FAIL）。"}[report["status"]])
    code = doctor.exit_code(report)
    if code:
        raise typer.Exit(code)


@auth_app.command("login")
def auth_login(
    browser: str = typer.Option(
        None, help="chromium (default on every OS; downloaded once, ~170 MB), or chrome / msedge to use an installed one."
    ),
):
    """One-time interactive Google sign-in in a browser window (you sign in yourself)."""
    try:
        _interactive_login(browser, interactive=_is_tty())
    except Bili2nbError as e:
        raise _fail(e, False)
    typer.echo("Login finished. Verify with: bili2nb auth check", err=True)


@auth_app.command("check")
def auth_check(as_json: bool = typer.Option(False, "--json", help="Single JSON object on stdout.")):
    """Verify the saved session by making a real (read-only) NotebookLM call."""
    from bili2nb.notebook.adapter import NotebookAdapter

    try:
        n = NotebookAdapter().check_auth()
    except Bili2nbError as e:
        raise _fail(e, as_json)
    if as_json:
        sys.stdout.write(json.dumps({"status": "ok", "notebook_count": n}) + "\n")
    else:
        typer.echo(f"Auth OK (account has {n} notebooks).")


_PART_HELP = (
    "Only these parts (P) of a multi-part video: 8, a range 8-27, a list 1,3,5, or both 1-3,7. "
    "A ?p=N in the URL wins."
)


class Frames(str, Enum):
    auto = "auto"
    slides = "slides"
    scene = "scene"
    none = "none"


@app.command("convert")
def convert(
    url: str = typer.Argument(..., help="Bilibili video URL or BV number (bilibili.com or b23.tv)"),
    frames: Frames = typer.Option(Frames.auto, "--frames", help="Keyframe mode."),
    output: Path = typer.Option(None, "--output", help="Output root dir (default: app data dir)."),
    part: str = typer.Option(
        None, "--part", metavar="SPEC", help=_PART_HELP + " Default: ALL parts."
    ),
    audio_only: bool = typer.Option(False, "--audio-only", help="Audio only (same as --frames none)."),
    as_json: bool = typer.Option(False, "--json", help="Single JSON object on stdout."),
):
    """Convert locally only (no Google login): per part audio + keyframes.pdf + manifest.json."""
    from bili2nb.batch import convert_parts

    try:
        res = convert_parts(
            normalize_input(url), output_root=output, frames_mode=frames.value, part=part,
            audio_only=audio_only,
        )
    except KeyboardInterrupt:
        raise _fail(Bili2nbError("INTERRUPTED", "已中断，临时文件已清理，未生成不完整的产物。"), as_json)
    except Bili2nbError as e:
        raise _fail(e, as_json)
    if as_json:
        sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
        return
    typer.echo(f"OK  {res['video_title']}  ({len(res['parts'])}/{res['part_count']} part(s))")
    typer.echo(f"  output : {res['output_dir']}")
    for r in res["parts"]:
        tag = "  (reused)" if r.get("reused") else ""
        typer.echo(f"  P{r['part']:<3} {r['audio']['file']}  {r['audio']['bytes'] / 1e6:.1f} MB{tag}")
        v = r["visual"]
        if v["status"] == "ok":
            typer.echo(f"       keyframes.pdf  {v['pages']} pages  {v['pdf_bytes'] / 1e6:.1f} MB")
        else:
            typer.echo(f"       visual : {v['status']} - {v.get('reason', '')}")
    for w in res.get("warnings", []):
        typer.echo(f"  warning: {w}")


def _print_job(res: dict) -> None:
    typer.echo(f"{res['status'].upper()}  job {res['job_id']}  [{res['stage']}]  {res['video_title']}")
    if res.get("notebook_url"):
        typer.echo(f"  notebook : {res['notebook_url']}")
    if res.get("parts"):
        plan = res.get("upload_plan", {})
        typer.echo(f"  parts    : {res['part_count']}  plan: {plan.get('strategy')}  "
                   f"(account source limit {plan.get('source_limit')}, {plan.get('limit_origin')})")
        for p in res["parts"]:
            typer.echo(f"    P{p['part']:<3} {p['convert']:<8} {p['title']}")
        for src in res.get("sources", []):
            ready = "READY" if src["ready"] else ("uploaded" if src["source_id"] else "pending")
            typer.echo(f"  source   : {src['title']}  {src['source_id'] or '-'}  {ready}")
    else:
        for kind, sid in res.get("source_ids", {}).items():
            ready = "READY" if res.get("source_ready", {}).get(kind) else "not ready"
            typer.echo(f"  source   : {kind:<5} {sid}  {ready}")
    typer.echo(f"  mode     : {res['mode']}  ({res['source_count']} source(s))"
               + ("  [reused existing]" if res.get("reused") else ""))
    for k in ("audio_path", "pdf_path"):
        if k in res:
            typer.echo(f"  {k:<9}: {res[k]}")
    if res.get("error_code"):
        typer.echo(f"  error    : {res['error_code']} - {res.get('error_message')}")
    for w in res.get("warnings", []):
        typer.echo(f"  warning  : {w}")
    typer.echo(f"  timings  : {res.get('timings_sec')}")


def _emit(res: dict, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
    else:
        _print_job(res)


def _guard(fn, as_json: bool):
    try:
        return _with_login_offer(fn, as_json)
    except KeyboardInterrupt:
        raise _fail(Bili2nbError("INTERRUPTED", "已中断。"), as_json) from None
    except Bili2nbError as e:
        raise _fail(e, as_json) from None


@app.command("import")
def import_(
    url: str = typer.Argument(..., help="Bilibili video URL (bilibili.com or b23.tv)"),
    frames: Frames = typer.Option(Frames.auto, "--frames", help="Keyframe mode."),
    output: Path = typer.Option(None, "--output", help="Keep imported files under this dir."),
    part: str = typer.Option(
        None, "--part", metavar="SPEC", help=_PART_HELP + " Default: ALL parts, one notebook."
    ),
    audio_only: bool = typer.Option(False, "--audio-only", help="Audio source only (no PDF)."),
    keep_files: bool = typer.Option(False, "--keep-files", help="Keep local audio/PDF after success."),
    force_new: bool = typer.Option(
        False, "--force-new", help="Create another notebook for this video (exactly one, with the full part set)."
    ),
    as_json: bool = typer.Option(False, "--json", help="Single JSON object on stdout."),
):
    """Convert and import into a NEW NotebookLM notebook (audio + keyframe PDF), wait until READY."""

    _ensure_setup(sys.stdin.isatty() and sys.stdout.isatty() and not as_json)

    def go():
        return _make_importer().run(
            normalize_input(url), frames_mode=frames.value, part=part, audio_only=audio_only,
            output=str(output) if output else None, keep_files=keep_files, force_new=force_new,
        )

    _emit(_guard(go, as_json), as_json)


@app.command("status")
def status(job_id: str = typer.Argument(..., help="Job id"),
           as_json: bool = typer.Option(False, "--json", help="Single JSON object on stdout.")):
    """Show the persisted result of a job."""
    from bili2nb.importer import job_to_dict
    from bili2nb.jobs.db import JobStore

    job = JobStore().get(job_id)
    if job is None:
        raise _fail(Bili2nbError("JOB_NOT_FOUND", f"找不到任务 {job_id}。用 `bili2nb jobs` 查看。"), as_json)
    _emit(job_to_dict(job), as_json)


@app.command("retry")
def retry(job_id: str = typer.Argument(..., help="Job id"),
          as_json: bool = typer.Option(False, "--json", help="Single JSON object on stdout.")):
    """Resume a failed/interrupted job from its last checkpoint (no duplicate notebook/sources)."""
    _emit(_guard(lambda: _make_importer().retry(job_id), as_json), as_json)


@app.command("jobs")
def jobs(limit: int = typer.Option(20, "--limit", min=1, help="Max jobs to show."),
         as_json: bool = typer.Option(False, "--json", help="Single JSON array on stdout.")):
    """List local job history (newest first)."""
    from bili2nb.importer import job_to_dict
    rows = _make_store().list(limit)
    if as_json:
        sys.stdout.write(json.dumps([job_to_dict(j) for j in rows], ensure_ascii=False) + "\n")
        return
    if not rows:
        typer.echo("(no jobs yet)")
    for j in rows:
        typer.echo(f"{j.job_id}  {j.status:<11} {j.stage:<16} {(j.notebook_id or '-')[:8]:<9} "
                   f"{(j.video_title or '')[:40]}")
