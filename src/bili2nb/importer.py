"""import pipeline: URL -> convert -> new NotebookLM notebook -> 2 sources -> READY.

Every stage is checkpointed in the job store BEFORE moving on, so a crash / Ctrl+C / failed
upload can be resumed with `retry JOB_ID` without re-creating the notebook or re-uploading
sources that already exist.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path

from bili2nb import cleanup as cleanup_mod
from bili2nb import pipeline, planner
from bili2nb.bilibili import downloader as dl
from bili2nb.bilibili.parser import VideoRef, parse_url
from bili2nb.config import default_output_root
from bili2nb.errors import Bili2nbError
from bili2nb.importer_multi import MultiPartMixin
from bili2nb.jobs.db import COMPLETED, FAILED, INTERRUPTED, RUNNING, Job, JobStore
from bili2nb.notebook.adapter import make_title, notebook_url
from bili2nb.retry import MAX_ATTEMPTS, RETRYABLE, with_backoff  # noqa: F401  (re-exported)
from bili2nb.selection import resolve_part


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def source_title(kind: str, video_title: str) -> str:
    prefix = "音频" if kind == "audio" else "关键帧PDF"
    t = f"{prefix}｜{video_title}"
    return t if len(t) <= 100 else t[:99] + "…"


class Importer(MultiPartMixin):
    def __init__(
        self,
        store: JobStore,
        adapter,
        *,
        convert_fn: Callable[..., dict] | None = None,
        fetch_info_fn: Callable[[VideoRef], dl.VideoInfo] | None = None,
        plan_parts_fn: Callable[..., list[dl.VideoInfo]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = _stderr,
        backoff_base: float = 3.0,
    ):
        self.store = store
        self.adapter = adapter
        self._convert = convert_fn
        if plan_parts_fn is not None:
            self._plan_fn = plan_parts_fn
        elif fetch_info_fn is not None:  # single-part helper (V1.1 callers/tests)
            self._plan_fn = lambda ref: [fetch_info_fn(ref)]
        else:
            self._plan_fn = dl.plan_parts
        self.sleep = sleep
        self.log = log
        self.backoff_base = backoff_base
        self._cloud_verified: set[str] = set()  # job ids whose sources were confirmed in the cloud THIS run

    # ------------------------------------------------------------ helpers
    def _backoff(self, fn: Callable[[], object], what: str):
        """Bounded retry with exponential backoff, only for errors safe to repeat."""
        return with_backoff(fn, what, log=self.log, sleep=self.sleep, base=self.backoff_base)

    def _preflight(self) -> None:
        """Verify the NotebookLM session BEFORE any media is downloaded."""
        self.log("预检 NotebookLM 登录…")
        self._backoff(self.adapter.check_auth, "auth check")

    def _extra(self, job: Job) -> dict:
        d = {"job_id": job.job_id, "video_key": job.video_key}
        if job.notebook_id:
            d["notebook_id"] = job.notebook_id
            d["notebook_url"] = notebook_url(job.notebook_id)
        if job.sources:
            d["source_ids"] = dict(job.sources)
        return d

    @staticmethod
    def kinds(job: Job) -> list[str]:
        return ["audio", "pdf"] if job.visual.get("status") == "ok" else ["audio"]

    @staticmethod
    def _file_ok(p: str | None) -> bool:
        return bool(p) and Path(p).is_file()  # type: ignore[arg-type]

    def _add_warning(self, job: Job, msg: str) -> Job:
        if msg in job.warnings:
            return job
        return self.store.update(job.job_id, warnings=[*job.warnings, msg])

    def _tick(self, job: Job, name: str, t0: float) -> Job:
        timings = dict(job.timings)
        timings[name] = round(timings.get(name, 0.0) + time.monotonic() - t0, 2)
        return self.store.update(job.job_id, timings=timings)

    # ------------------------------------------------------------ public API
    def run(self, url: str, *, frames_mode: str = "auto", part: int | str | None = None,
            audio_only: bool = False, output: str | None = None, keep_files: bool = False,
            force_new: bool = False, merge_sources: bool = False,
            source_limit: int | str | None = None) -> dict:
        """`merge_sources`: merge consecutive parts into the fewest sources (multi-part only).
        `part`: a part number or a `--part` spec (8 / 8-27 / 1,3,5 / 1-3,7); None = all parts.
        `source_limit`: pretend the account allows at most this many sources (never looser than the real limit)."""
        source_limit = planner.parse_source_limit(source_limit)  # bad value fails before any network
        ref, chosen, warnings = resolve_part(parse_url(url), part)  # bad spec fails before any network
        for w in warnings:
            self.log(w)
        mode = "none" if audio_only else frames_mode
        self._preflight()
        self.log(f"解析视频信息: {ref.url}")
        infos = self._plan_fn(ref) if chosen is None else self._plan_fn(ref, selection=chosen)
        if len(infos) > 1:  # several parts (no ?p=): one notebook for ALL of them
            return self._run_multi(
                ref, infos, mode=mode, frames_mode=frames_mode, audio_only=audio_only,
                output=output, keep_files=keep_files, force_new=force_new, merge_sources=merge_sources,
                source_limit=source_limit)
        if source_limit is not None:
            self.log("提示：只导入 1 个分 P，--source-limit 无意义，已忽略。")
        if merge_sources:
            self.log("提示：只导入 1 个分 P，--merge-sources 无意义，已忽略。")
        info = infos[0]
        account = self.adapter.account_key
        existing = self.store.find_active(info.video_key, account, mode)
        if existing and not force_new:
            if existing.status == COMPLETED:
                if self._notebook_alive(existing):
                    self.log(f"该视频已导入过（任务 {existing.job_id}），返回原笔记本，不重复创建。")
                    return self.result(existing, reused=True)
                self.log("原笔记本已不存在（可能被删除），将创建新任务。")
                self.store.supersede(existing.job_id)
            else:
                self.log(f"发现未完成任务 {existing.job_id}（{existing.status}/{existing.stage}），继续。")
                return self._execute(existing, info)
        elif existing:
            self.store.supersede(existing.job_id)
        job = self.store.create(
            video_key=info.video_key, account_key=account, mode=mode, url=ref.url,
            options={"frames_mode": frames_mode, "part": ref.part, "audio_only": audio_only,
                     "output": output, "keep_files": keep_files},
            video_id=info.video_id, video_title=info.title, duration_sec=info.duration,
        )
        self.log(f"新建任务 {job.job_id}")
        return self._execute(job, info)

    def retry(self, job_id: str) -> dict:
        job = self.store.get(job_id)
        if job is None:
            raise Bili2nbError("JOB_NOT_FOUND", f"找不到任务 {job_id}。用 `bili2nb jobs` 查看。")
        if job.status == COMPLETED:
            self.log("任务已完成，无需重试。")
            return self.result(job, reused=True)
        self._preflight()
        return self._execute(job, None)

    def _notebook_alive(self, job: Job) -> bool:
        if not job.notebook_id:
            return False
        return bool(self._backoff(lambda: self.adapter.notebook_exists(job.notebook_id), "notebook check"))

    # ------------------------------------------------------------ execution
    def _execute(self, job: Job, info: dl.VideoInfo | None) -> dict:
        self.store.bump_attempts(job.job_id)
        job = self.store.update(job.job_id, status=RUNNING, error_code=None, error_message=None,
                                error_detail=None)
        try:
            if job.parts:  # multi-part job (V1.2)
                job = self._multi_stages(job)
            else:  # single part: the proven V1.1 path, also used by every pre-V1.2 job
                job = self._stage_convert(job, info)
                job = self._stage_notebook(job)
                job = self._stage_upload(job)
                job = self._stage_ready(job)
            job = self.store.update(job.job_id, status=COMPLETED, stage="done")
            job = self._cleanup(job)
            return self.result(job)
        except KeyboardInterrupt:
            job = self.store.update(job.job_id, status=INTERRUPTED)
            raise Bili2nbError(
                "INTERRUPTED", f"已中断。进度已保存，可继续: bili2nb retry {job.job_id}",
                extra=self._extra(self.store.get(job.job_id)),  # type: ignore[arg-type]
            ) from None
        except Bili2nbError as e:
            cur = self.store.update(job.job_id, status=FAILED, error_code=e.code,
                                    error_message=e.message, error_detail=e.detail)
            e.extra.update(self._extra(cur))
            e.extra["retry_hint"] = f"bili2nb retry {cur.job_id}"
            raise
        except Exception as exc:
            cur = self.store.update(job.job_id, status=FAILED, error_code="INTERNAL",
                                    error_message=f"未预期错误 ({type(exc).__name__})")
            raise Bili2nbError("INTERNAL", f"未预期错误 ({type(exc).__name__})",
                               extra=self._extra(cur)) from exc

    def _stage_convert(self, job: Job, info: dl.VideoInfo | None) -> Job:
        opts = job.options
        needed = job.stage == "created" or (
            any(k not in job.sources for k in self.kinds(job))
            and not (self._file_ok(job.audio_path)
                     and (self.kinds(job) == ["audio"] or self._file_ok(job.pdf_path)))
        )
        if not needed:
            return job
        self.log("阶段 1/4：转换素材（音频 + 关键帧 PDF）")
        t0 = time.monotonic()
        out_root = Path(opts["output"]).expanduser() if opts.get("output") else default_output_root()
        run = self._convert or pipeline.convert
        res = run(job.url, output_root=out_root, frames_mode=opts["frames_mode"],
                  part=opts.get("part"), audio_only=opts.get("audio_only", False),
                  log=self.log, info=info)
        job = self.store.update(
            job.job_id, stage="converted", video_title=res["video_title"],
            duration_sec=res["duration_sec"], output_dir=res["output_dir"],
            audio_path=res["audio_path"], pdf_path=res.get("pdf_path"), visual=res["visual"],
        )
        for w in res.get("warnings", []):
            job = self._add_warning(job, w)
        if self.kinds(job) == ["audio"]:
            reason = job.visual.get("reason", "")
            job = self._add_warning(job, f"仅有音频一个来源（{job.visual.get('status')}: {reason}）。")
        return self._tick(job, "convert", t0)

    def _stage_notebook(self, job: Job) -> Job:
        t0 = time.monotonic()
        if job.notebook_id:
            if not self._backoff(lambda: self.adapter.notebook_exists(job.notebook_id), "notebook check"):
                raise Bili2nbError(
                    "NOTEBOOK_CREATE_FAILED",
                    "任务记录的笔记本已不存在（可能已被删除）。如需重建请用 import --force-new。",
                )
            return job
        self.log("阶段 2/4：创建新笔记本")
        title = make_title(job.video_title or "")
        nb_id = self._backoff(lambda: self.adapter.create_notebook(title), "create notebook")
        job = self.store.update(job.job_id, notebook_id=nb_id, stage="notebook_created")  # checkpoint
        self.log(f"  笔记本 {nb_id}")
        return self._tick(job, "notebook", t0)

    def _record_orphans(self, job: Job) -> Job:
        """Persist ids of OUR stuck sources the adapter could not delete yet (ownership evidence)."""
        drain = getattr(self.adapter, "drain_orphans", None)
        ids = drain() if drain else []
        if ids:
            job = self.store.update(job.job_id, orphans=sorted(set(job.orphans) | set(ids)))
        return job

    def _stage_upload(self, job: Job) -> Job:
        t0 = time.monotonic()
        nb = job.notebook_id
        cleanup = getattr(self.adapter, "cleanup_owned", None)
        if job.orphans and cleanup:
            # Only ids this job recorded itself are ever deleted (never "same name" guesses).
            remaining = self._backoff(lambda: cleanup(nb, job.orphans), "cleanup orphans")
            job = self.store.update(job.job_id, orphans=sorted(remaining))
        for kind in self.kinds(job):
            if kind in job.sources:
                self.log(f"  {kind} 已上传（{job.sources[kind]}），跳过。")
                continue
            path = Path(job.audio_path if kind == "audio" else job.pdf_path)  # type: ignore[arg-type]
            if not path.is_file():
                raise Bili2nbError("MEDIA_ERROR", f"待上传文件不存在: {path.name}。请重新运行 retry。")
            self.log(f"阶段 3/4：上传 {kind}（{path.stat().st_size / 1e6:.1f} MB）")
            title = source_title(kind, job.video_title or "")
            try:
                sid = self._backoff(
                    lambda path=path, title=title, owned=tuple(job.orphans): self.adapter.upload_file(
                        nb, path, title=title, owned=owned
                    ),
                    f"upload {kind}",
                )
            finally:
                job = self._record_orphans(job)
            job = self.store.update(job.job_id, sources={**job.sources, kind: sid})  # checkpoint
            self.log(f"  {kind} source_id={sid}")
        job = self.store.update(job.job_id, stage="uploaded")
        return self._tick(job, "upload", t0)

    def _stage_ready(self, job: Job) -> Job:
        t0 = time.monotonic()
        nb = job.notebook_id
        timeout = min(3600.0, max(600.0, (job.duration_sec or 0) * 0.5))
        self.log("阶段 4/4：等待 NotebookLM 处理来源（READY）…")
        for kind, sid in list(job.sources.items()):
            if job.ready.get(kind):
                continue
            try:
                self._backoff(
                    lambda sid=sid: self.adapter.wait_ready(nb, sid, timeout=timeout),
                    f"wait {kind}",
                )
            except Bili2nbError as e:
                if e.detail == "SourceProcessingError":  # NotebookLM rejected it: allow re-upload
                    srcs = {k: v for k, v in job.sources.items() if k != kind}
                    self.store.update(job.job_id, sources=srcs)
                    raise Bili2nbError(
                        "SOURCE_NOT_READY",
                        f"NotebookLM 处理 {kind} 失败；已清除该来源记录，retry 将重新上传它。",
                        detail=e.detail,
                    ) from e
                raise
            job = self.store.update(job.job_id, ready={**job.ready, kind: True})  # checkpoint
            self.log(f"  {kind}: READY")
        job = self.store.update(job.job_id, stage="ready")
        return self._tick(job, "ready", t0)

    def _cloud_holds_sources(self, job: Job) -> bool:
        """Read-only check that every recorded source is really in the notebook (single-part path;
        multi-part jobs get the same guarantee from `_m_finalize`)."""
        if job.parts:
            return job.job_id in self._cloud_verified
        want = set(job.sources.values())
        if not job.notebook_id or not want:
            return False
        try:
            have = {s.id: s for s in self._backoff(
                lambda: self.adapter.list_sources(job.notebook_id), "verify sources")}
        except Bili2nbError:
            return False
        return all(sid in have and have[sid].status != "ERROR" for sid in want)

    def _set_cleanup(self, job: Job, info: dict) -> Job:
        return self.store.update(job.job_id, options={**job.options, "cleanup": info})

    def _cleanup(self, job: Job) -> Job:
        """After COMPLETED, and only once the cloud is confirmed to hold every recorded source:
        remove exactly the local files this job used (see bili2nb.cleanup). Never fails the import."""
        opts = job.options
        empty = {"freed_bytes": 0, "removed": [], "kept_other_parts": []}
        if opts.get("keep_files") or opts.get("output"):  # the user wants the files
            return self._set_cleanup(job, {"status": "kept", **empty})
        if not cleanup_mod.job_dirs(job):
            return self._set_cleanup(job, {"status": "skipped", **empty})
        if not self._cloud_holds_sources(job):
            job = self._add_warning(job, "云端核对未通过，已保留本地文件（可稍后用 `bili2nb clean` 清理）。")
            return self._set_cleanup(job, {"status": "failed_verify", **empty})
        try:
            res = cleanup_mod.cleanup_job_files(job, default_output_root())
        except Exception as exc:  # noqa: BLE001 - a cleanup problem must never fail a finished import
            job = self._add_warning(job, f"本地清理出错（已忽略）：{type(exc).__name__}")
            return self._set_cleanup(job, {"status": "skipped", **empty})
        for w in res.pop("warnings"):
            job = self._add_warning(job, w)
        res.pop("manifest", None)
        job = self._set_cleanup(job, res)
        if res["status"] == "done":
            job = self.store.update(job.job_id, files_cleaned=True)
            self.log(f"本地文件已清理，释放 {human_bytes(res['freed_bytes'])}。")
        return job

    # ------------------------------------------------------------ output
    def result(self, job: Job, *, reused: bool = False) -> dict:
        return job_to_dict(job, reused=reused)


def human_bytes(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def job_to_dict(job: Job, *, reused: bool = False) -> dict:
    """Stable, secret-free description of a job (used by import/status/retry --json)."""
    d: dict = {
        "status": job.status,
        "job_id": job.job_id,
        "stage": job.stage,
        "video_id": job.video_id,
        "video_key": job.video_key,
        "video_title": job.video_title,
        "mode": "audio-only" if job.options.get("audio_only") or job.mode == "none"
        else f"frames:{job.mode}",
        "duration_sec": job.duration_sec,
        "notebook_id": job.notebook_id,
        "notebook_url": notebook_url(job.notebook_id) if job.notebook_id else None,
        "source_ids": dict(job.sources),
        "source_ready": dict(job.ready),
        "source_count": len(job.sources),
        "orphan_source_ids": list(job.orphans),
        "visual": job.visual,
        "files_cleaned": job.files_cleaned,
        "cleanup": job.options.get("cleanup"),
        "attempts": job.attempts,
        "timings_sec": job.timings,
        "warnings": list(job.warnings),
        "reused": reused,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
    if job.parts:
        plan = job.plan
        d["part_count"] = job.options.get("part_count") or len(job.parts)  # the video's TOTAL
        d["selection"] = job.options.get("selection", "all")
        d["parts"] = [{k: v for k, v in p.items() if k != "info"} for p in job.parts]
        d["upload_plan"] = {k: plan.get(k) for k in ("strategy", "source_limit", "limit_origin")}
        d["upload_plan"]["merge_requested"] = bool(plan.get("merge_requested", False))
        for k in ("merge_mode", "merge_group_sizes", "merge_group_count"):  # only merged plans carry them
            if k in plan:
                d["upload_plan"][k] = plan[k]
        d["sources"] = [
            {"key": i["key"], "kind": i["kind"], "parts": i["parts"], "title": i["title"],
             "merged": i["merge"], "bytes": i["bytes"], "source_id": i["source_id"], "ready": i["ready"]}
            for i in plan.get("items", [])
        ]
    if Importer._file_ok(job.audio_path):
        d["audio_path"] = job.audio_path
    if Importer._file_ok(job.pdf_path):
        d["pdf_path"] = job.pdf_path
    if job.error_code:
        d["error_code"] = job.error_code
        d["error_message"] = job.error_message
    return d
