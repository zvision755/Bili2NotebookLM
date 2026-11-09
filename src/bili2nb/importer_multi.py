"""Multi-part import engine (V1.2): one video with several 分P -> ONE notebook.

Stage order (every stage is checkpointed in the job row):
  1 convert   every part, locally (a failing part leaves the cloud untouched; done parts are kept)
  2 plan      decide the upload items from the account's source quota; build merged files if needed
  3 notebook  create the single notebook (only now, after everything local is verified)
  4 upload    item by item; each success is stored at once, so a retry never re-uploads it
  5 ready     wait per item
The plan is FROZEN once it exists: a quota change between runs cannot shift sources around.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path

from bili2nb import batch, layout, planner
from bili2nb.bilibili import downloader as dl
from bili2nb.config import default_output_root
from bili2nb.errors import Bili2nbError
from bili2nb.jobs.db import COMPLETED, Job
from bili2nb.media import frames as frames_mod
from bili2nb.media import merge as merge_mod


class MultiPartMixin:
    # Provided by Importer: store, adapter, log, _convert, _backoff, _add_warning, _tick,
    # _preflight, _stage_notebook, _record_orphans, _notebook_alive, _execute, result, _file_ok.

    # ------------------------------------------------------------------ entry
    def _run_multi(self, ref, infos, *, mode, frames_mode, audio_only, output, keep_files, force_new) -> dict:
        video_id = infos[0].video_id
        key = f"{video_id}_all"  # never equal to a single-part key (BV_CID): no cross-reuse
        account = self.adapter.account_key
        existing = self.store.find_active(key, account, mode)
        if existing and not force_new:
            have = [(p["part"], p["cid"]) for p in existing.parts]
            want = [(i.part, i.cid) for i in infos]
            if have != want:
                raise Bili2nbError(
                    "PARTS_CHANGED",
                    f"该视频的分 P 与任务 {existing.job_id} 记录的不一致（现在 {len(want)} 个，记录里 {len(have)} 个）。"
                    "为避免把来源放错位置，不会复用旧任务；请用 --force-new 重新导入。",
                    extra={"job_id": existing.job_id},
                )
            if existing.status == COMPLETED:
                if self._notebook_alive(existing):
                    self.log(f"该视频的全部分 P 已导入过（任务 {existing.job_id}），返回原笔记本，不重复创建。")
                    return self.result(existing, reused=True)
                self.log("原笔记本已不存在（可能被删除），将创建新任务。")
                self.store.supersede(existing.job_id)
            else:
                self.log(f"发现未完成任务 {existing.job_id}（{existing.status}/{existing.stage}），继续。")
                return self._execute(existing, None)
        elif existing:
            self.store.supersede(existing.job_id)

        limit, origin, warn = self._source_limit()
        planner.estimate_feasibility([i.duration for i in infos], limit=limit)  # before any download
        series = infos[0].series_title or infos[0].title
        job = self.store.create(
            video_key=key, account_key=account, mode=mode, url=ref.url,
            options={"frames_mode": frames_mode, "part": None, "audio_only": audio_only,
                     "output": output, "keep_files": keep_files, "multi": True},
            video_id=video_id, video_title=series, duration_sec=sum(i.duration for i in infos),
        )
        job = self.store.update(
            job.job_id, parts=[self._part_state(i) for i in infos],
            plan={"source_limit": limit, "limit_origin": origin},
        )
        if warn:
            job = self._add_warning(job, warn)
        self.log(f"新建任务 {job.job_id}（{len(infos)} 个分 P，来源上限 {limit}）")
        return self._execute(job, None)

    @staticmethod
    def _part_state(info: dl.VideoInfo) -> dict:
        return {"part": info.part, "cid": info.cid, "title": info.part_title or info.title,
                "duration": info.duration, "info": asdict(info), "convert": "pending",
                "audio_path": None, "pdf_path": None, "visual": {}, "dir": None,
                "audio_bytes": 0, "pdf_bytes": 0}

    def _source_limit(self) -> tuple[int, str, str | None]:
        fn = getattr(self.adapter, "source_limit", None)
        if fn is None:
            return planner.DEFAULT_SOURCE_LIMIT, "default", None
        try:
            limit, origin = self._backoff(fn, "account limits")
            return limit, origin, None
        except Bili2nbError as e:
            if e.code == "AUTH_REQUIRED":
                raise
            return (planner.DEFAULT_SOURCE_LIMIT, "default",
                    f"无法读取账号的来源上限（{e.code}），按保守值 {planner.DEFAULT_SOURCE_LIMIT} 规划（只会更早合并，不会超限）。")

    # ------------------------------------------------------------------ stages
    def _multi_stages(self, job: Job) -> Job:
        job = self._m_convert(job)
        job = self._m_plan(job)
        job = self._stage_notebook(job)
        job = self._m_upload(job)
        return self._m_ready(job)

    def _out_root(self, job: Job) -> Path:
        out = job.options.get("output")
        return Path(out).expanduser() if out else default_output_root()

    def _write_manifest(self, job: Job, parts: list[dict]) -> None:
        layout.write_batch_manifest(
            self._out_root(job) / job.video_id, video_id=job.video_id, title=job.video_title or "",
            selection="all", parts=batch.rows_from_job_parts(parts))

    def _part_done(self, p: dict) -> bool:
        if p.get("convert") != "done" or not self._file_ok(p.get("audio_path")):
            return False
        return p.get("visual", {}).get("status") != "ok" or self._file_ok(p.get("pdf_path"))

    def _m_convert(self, job: Job) -> Job:
        parts = [dict(p) for p in job.parts]
        todo = [p for p in parts if not self._part_done(p)]
        if todo:
            self.log(f"阶段 1/4：转换 {len(todo)}/{len(parts)} 个分 P（每个分 P 一份音频 + 关键帧 PDF）")
        t0 = time.monotonic()
        opts = job.options
        out_root = self._out_root(job)
        run = self._convert or batch.pipeline.convert
        for p in todo:
            info = dl.VideoInfo(**p["info"])
            self.log(f"—— P{info.part}/{len(parts)}  {p['title']}")
            try:
                res = self._backoff(
                    lambda info=info: run(
                        info.webpage_url, output_root=out_root, frames_mode=opts["frames_mode"],
                        part=info.part, audio_only=opts.get("audio_only", False), log=self.log, info=info),
                    f"P{info.part} convert")
            except Bili2nbError as e:
                p["convert"] = "failed"
                self.store.update(job.job_id, parts=parts)
                self._write_manifest(job, parts)
                e.extra["failed_part"] = info.part
                raise
            audio, pdf = Path(res["audio_path"]), res.get("pdf_path")
            p.update(convert="done", audio_path=str(audio), pdf_path=pdf, visual=res["visual"],
                     dir=res["output_dir"], audio_bytes=audio.stat().st_size,
                     pdf_bytes=Path(pdf).stat().st_size if pdf and Path(pdf).is_file() else 0)
            job = self.store.update(job.job_id, parts=parts, output_dir=str(out_root / job.video_id))
            for w in res.get("warnings", []):
                job = self._add_warning(job, f"P{info.part}: {w}")
            if res["visual"].get("status") != "ok":
                job = self._add_warning(
                    job, f"P{info.part}: 只有音频来源（{res['visual'].get('status')}: {res['visual'].get('reason', '')}）。")
            self._write_manifest(job, parts)
        if todo:
            batch.verify_distinct_audio(
                [{"part": p["part"], "audio_path": p["audio_path"]} for p in parts])
            job = self._tick(job, "convert", t0)
        return self.store.update(job.job_id, stage="converted") if job.stage == "created" else job

    def _m_plan(self, job: Job) -> Job:
        plan = dict(job.plan)
        if "items" not in plan:
            files = [
                planner.PartFiles(
                    p["part"], p["title"], p["duration"], p["audio_path"], p["audio_bytes"],
                    p["pdf_path"] if p["visual"].get("status") == "ok" else None, p["pdf_bytes"])
                for p in job.parts]
            plan = planner.plan_uploads(
                files, limit=plan["source_limit"], video_title=job.video_title or "",
                total_parts=len(job.parts), limit_origin=plan["limit_origin"])
            self.log(f"上传计划：{plan['strategy']}，{len(plan['items'])} 个来源（账号上限 {plan['source_limit']}）")
            job = self.store.update(job.job_id, plan=plan)
        pending = [i for i in plan["items"] if i["merge"] and not i["source_id"] and not self._file_ok(i["path"])]
        if pending:
            self.log("阶段 2/4：生成合并文件（逐 P 原文件保留不动）")
            job = self._build_merged(job, plan, pending)
        if job.stage in ("created", "converted"):
            job = self.store.update(job.job_id, stage="planned")
        return job

    def _build_merged(self, job: Job, plan: dict, pending: list[dict]) -> Job:
        by_part = {p["part"]: p for p in job.parts}
        mdir = Path(job.output_dir) / "_merged"
        audio_titles: dict[int, tuple[str, float | None]] = {}
        for it in plan["items"]:
            if it["kind"] == "audio":
                for n in it["parts"]:
                    off = (it.get("offsets") or {}).get(str(n))
                    audio_titles[n] = (it["title"], off)
        for it in [i for i in pending if i["kind"] == "audio"]:
            name = it["key"].split(":")[0]
            res = merge_mod.merge_audio([Path(by_part[n]["audio_path"]) for n in it["parts"]],
                                        mdir / f"{name}_audio.m4a")
            it.update(path=str(res.path), bytes=res.size,
                      offsets={str(n): off for n, off in zip(it["parts"], res.offsets, strict=True)})
            job = self.store.update(job.job_id, plan=plan)
            for n, off in zip(it["parts"], res.offsets, strict=True):
                audio_titles[n] = (it["title"], off)
        for it in [i for i in pending if i["kind"] == "pdf"]:
            name = it["key"].split(":")[0]
            notes = []
            for n in it["parts"]:
                title, off = audio_titles.get(n, ("", None))
                if off is None:
                    notes.append(f"P{n:02d} 对应音频来源《{title}》" if title else f"P{n:02d}（本部分没有音频来源）")
                else:
                    notes.append(f"P{n:02d} 在音频《{title}》中从 {frames_mod.hms(off)} 开始")
            res = merge_mod.merge_pdfs(
                [Path(by_part[n]["pdf_path"]) for n in it["parts"]],
                [f"P{n:02d}｜{by_part[n]['title']}" for n in it["parts"]],
                mdir / f"{name}_keyframes.pdf", title=job.video_title or "", note_lines=notes)
            it.update(path=str(res.path), bytes=res.size)
            job = self.store.update(job.job_id, plan=plan)
        return job

    @staticmethod
    def _mirror(plan: dict, field: str) -> dict:
        return {i["key"]: (i[field] if field == "source_id" else True)
                for i in plan["items"] if i[field]}

    def _m_upload(self, job: Job) -> Job:
        t0 = time.monotonic()
        nb = job.notebook_id
        cleanup = getattr(self.adapter, "cleanup_owned", None)
        if job.orphans and cleanup:
            remaining = self._backoff(lambda: cleanup(nb, job.orphans), "cleanup orphans")
            job = self.store.update(job.job_id, orphans=sorted(remaining))
        plan = dict(job.plan)
        for it in plan["items"]:
            if it["source_id"]:
                self.log(f"  {it['title']} 已上传（{it['source_id']}），跳过。")
                continue
            path = Path(it["path"]) if it["path"] else None
            if path is None or not path.is_file():
                raise Bili2nbError("MEDIA_ERROR", f"待上传文件不存在: {it['key']}。请重新运行 retry。")
            self.log(f"阶段 3/4：上传 {it['title']}（{path.stat().st_size / 1e6:.1f} MB）")
            taken = tuple(o["source_id"] for o in plan["items"] if o is not it and o["source_id"])
            try:
                sid = self._backoff(
                    lambda path=path, title=it["title"], owned=tuple(job.orphans), taken=taken:
                        self.adapter.upload_file(nb, path, title=title, owned=owned, taken=taken),
                    f"upload {it['key']}")
            finally:
                job = self._record_orphans(job)
            if sid in taken:  # never record one cloud source for two items (live bug 2026-11-10)
                raise Bili2nbError(
                    "UPLOAD_FAILED",
                    f"{it['title']} 的上传结果指向了已被其他来源占用的云端来源，已中止（不会误报成功）。",
                    detail="duplicate source id")
            it["source_id"] = sid
            job = self.store.update(job.job_id, plan=plan, sources=self._mirror(plan, "source_id"))
            self.log(f"  source_id={sid}")
        job = self.store.update(job.job_id, stage="uploaded")
        return self._tick(job, "upload", t0)

    def _m_finalize(self, job: Job, plan: dict) -> Job:
        """Verify the cloud really holds every recorded source, then apply the part titles."""
        nb = job.notebook_id
        want = {it["source_id"]: it["title"] for it in plan["items"] if it["source_id"]}
        missing, untitled = self._backoff(lambda: self.adapter.finalize_sources(nb, want), "verify sources")
        if missing:
            gone = [it["title"] for it in plan["items"] if it["source_id"] in missing]
            for it in plan["items"]:
                if it["source_id"] in missing:
                    it["source_id"], it["ready"] = None, False
            self.store.update(job.job_id, plan=plan, sources=self._mirror(plan, "source_id"),
                              ready=self._mirror(plan, "ready"))
            raise Bili2nbError(
                "UPLOAD_FAILED",
                f"云端笔记本里找不到这些来源：{'、'.join(gone)}。已清除记录，retry 会重新上传它们。",
                detail="sources missing after READY")
        for it in plan["items"]:
            if it["source_id"] in untitled:
                job = self._add_warning(
                    job, f"来源《{it['title']}》的标题未能设置，云端暂显示为文件名（内容不受影响）。")
        return job

    def _m_ready(self, job: Job) -> Job:
        t0 = time.monotonic()
        nb = job.notebook_id
        plan = dict(job.plan)
        by_part = {p["part"]: p for p in job.parts}
        self.log("阶段 4/4：等待 NotebookLM 处理来源（READY）…")
        for it in plan["items"]:
            if it["ready"] or not it["source_id"]:
                continue
            secs = sum(by_part[n]["duration"] for n in it["parts"])
            timeout = min(3600.0, max(600.0, secs * 0.5))
            try:
                self._backoff(
                    lambda sid=it["source_id"], timeout=timeout: self.adapter.wait_ready(nb, sid, timeout=timeout),
                    f"wait {it['key']}")
            except Bili2nbError as e:
                if e.detail == "SourceProcessingError":  # rejected by NotebookLM: allow re-upload of THIS item
                    it["source_id"] = None
                    self.store.update(job.job_id, plan=plan, sources=self._mirror(plan, "source_id"),
                                      ready=self._mirror(plan, "ready"))
                    raise Bili2nbError(
                        "SOURCE_NOT_READY",
                        f"NotebookLM 处理 {it['title']} 失败；已清除该来源记录，retry 将重新上传它。",
                        detail=e.detail) from e
                raise
            it["ready"] = True
            job = self.store.update(job.job_id, plan=plan, ready=self._mirror(plan, "ready"))
            self.log(f"  {it['title']}: READY")
        job = self._m_finalize(job, plan)
        job = self.store.update(job.job_id, stage="ready")
        return self._tick(job, "ready", t0)
