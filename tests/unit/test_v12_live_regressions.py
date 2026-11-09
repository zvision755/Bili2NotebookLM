"""Regressions found by the first REAL multi-part NotebookLM import (V-2, 2026-11-10).

Live facts reproduced here with the REAL NotebookAdapter on a fake SDK client:
  * every part's file is called audio.m4a / keyframes.pdf, and the display-title rename is
    best-effort (live: it often does not stick) -> the cloud title stays the FILE name;
  * the old "same file name already there -> reuse it" shortcut then treated P02's upload as
    already done: P02 was never uploaded, yet the job said completed with 4 sources / 2 real ones.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import notebooklm as nl
import pytest

from bili2nb.errors import Bili2nbError
from bili2nb.jobs.db import COMPLETED
from bili2nb.notebook import adapter as ad
from tests.unit.test_multi_import import URL, Env, make_infos  # noqa: F401  (fixture helpers)


class LiveLikeSources:
    """Fake `client.sources`: titles stay the FILE name unless a rename is explicitly accepted."""

    def __init__(self, *, rename_works=True, rename_after_upload_sticks=False):
        self.items: dict[str, NS] = {}
        self.order: list[str] = []
        self.n = 0
        self.add_calls: list[tuple[str, str]] = []  # (file name, requested title)
        self.rename_calls: list[tuple[str, str]] = []
        self.rename_works = rename_works

    def _put(self, title, status="READY"):
        self.n += 1
        sid = f"cloud-{self.n}"
        self.items[sid] = NS(id=sid, title=title, status=NS(name=status))
        return self.items[sid]

    async def list(self, nb):
        return list(self.items.values())

    async def add_file(self, nb, path, title=None, wait=False):
        self.add_calls.append((Path(path).name, title))
        return self._put(Path(path).name)  # live: the requested title does NOT stick

    async def rename(self, nb, source_id, new_title, *, return_object=True):
        self.rename_calls.append((source_id, new_title))
        if not self.rename_works:
            raise nl.NetworkError("rename dropped")
        self.items[source_id] = NS(id=source_id, title=new_title, status=self.items[source_id].status)
        return self.items[source_id]

    async def delete(self, nb, source_id):
        self.items.pop(source_id, None)

    async def wait_until_ready(self, nb, sid, timeout=600.0):
        return self.items[sid]


class LiveLikeClient:
    def __init__(self, sources):
        self.sources = sources
        self.n = 0
        self.nbs: list[NS] = []
        outer = self

        class Notebooks:
            async def list(self):
                return list(outer.nbs)

            async def create(self, title):
                nb = NS(id=f"nb-{len(outer.nbs) + 1}", title=title)
                outer.nbs.append(nb)
                return nb

        class Settings:
            async def get_account_limits(self):
                return NS(source_limit=300)

        self.notebooks = Notebooks()
        self.settings = Settings()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *x):
        return False


def real_adapter(monkeypatch, sources):
    monkeypatch.setenv("BILI2NB_CHUNKED_UPLOAD", "0")
    a = ad.NotebookAdapter()
    client = LiveLikeClient(sources)
    monkeypatch.setattr(a, "_client", lambda **kw: client)
    return a


# ------------------------------------------------------------------ adapter level
def test_upload_never_reuses_a_source_another_item_already_owns(monkeypatch):
    sources = LiveLikeSources()
    a = real_adapter(monkeypatch, sources)
    first = a.upload_file("nb", Path("/x/P01/audio.m4a"), title="P01｜甲｜音频")
    second = a.upload_file("nb", Path("/x/P02/audio.m4a"), title="P02｜乙｜音频", taken=(first,))
    assert second != first
    assert [c[1] for c in sources.add_calls] == ["P01｜甲｜音频", "P02｜乙｜音频"]


def test_upload_still_reuses_its_own_leftover_by_file_name(monkeypatch):
    sources = LiveLikeSources()
    leftover = sources._put("audio.m4a")  # not owned by any other item -> an earlier attempt's upload
    a = real_adapter(monkeypatch, sources)
    sid = a.upload_file("nb", Path("/x/P02/audio.m4a"), title="P02｜乙｜音频", taken=("someone-else",))
    assert sid == leftover.id and sources.add_calls == []


def test_finalize_renames_wrong_titles_and_reports_missing(monkeypatch):
    sources = LiveLikeSources()
    s1, s2 = sources._put("audio.m4a"), sources._put("keyframes.pdf")
    a = real_adapter(monkeypatch, sources)
    missing, untitled = a.finalize_sources(
        "nb", {s1.id: "P01｜甲｜音频", s2.id: "P01｜甲｜关键帧PDF", "gone": "P02｜乙｜音频"})
    assert missing == ["gone"] and untitled == []
    assert sources.items[s1.id].title == "P01｜甲｜音频" and sources.items[s2.id].title == "P01｜甲｜关键帧PDF"


def test_finalize_reports_titles_it_could_not_fix_without_raising(monkeypatch):
    sources = LiveLikeSources(rename_works=False)
    s1 = sources._put("audio.m4a")
    a = real_adapter(monkeypatch, sources)
    missing, untitled = a.finalize_sources("nb", {s1.id: "P01｜甲｜音频"})
    assert missing == [] and untitled == [s1.id]


# ------------------------------------------------------------------ whole import, real adapter
def live_env(tmp_path, monkeypatch, n=2, **kw):
    monkeypatch.setenv("BILI2NB_HOME", str(tmp_path / "home"))
    env = Env(tmp_path, n=n)
    env.sources = LiveLikeSources(**kw)
    env.nlm = real_adapter(monkeypatch, env.sources)
    return env


def test_two_part_import_really_uploads_all_four_sources_with_part_titles(tmp_path, monkeypatch):
    env = live_env(tmp_path, monkeypatch, n=2)
    res = env.run()
    assert res["status"] == COMPLETED and res["source_count"] == 4
    assert len(set(res["source_ids"].values())) == 4
    assert len(env.sources.items) == 4  # what is REALLY in the cloud
    assert sorted(s.title for s in env.sources.items.values()) == sorted(
        ["P01｜第1集｜音频", "P01｜第1集｜关键帧PDF", "P02｜第2集｜音频", "P02｜第2集｜关键帧PDF"])
    assert res["warnings"] == []


def test_title_that_cannot_be_set_is_a_warning_not_a_false_success_or_failure(tmp_path, monkeypatch):
    env = live_env(tmp_path, monkeypatch, n=2, rename_works=False)
    res = env.run()
    assert res["status"] == COMPLETED and len(env.sources.items) == 4  # still 4 REAL sources
    assert any("标题" in w for w in res["warnings"])


def test_importer_refuses_two_items_sharing_one_source_id(tmp_path, monkeypatch):
    """Defence in depth: whatever the adapter does, never record one source id for two items."""
    monkeypatch.setenv("BILI2NB_HOME", str(tmp_path / "home"))
    env = Env(tmp_path, n=2)
    original = env.nlm.upload_file
    env.nlm.upload_file = lambda nb, path, title=None, *, owned=(), taken=(): (
        original(nb, path, title, owned=owned) if not taken else taken[0])
    with pytest.raises(Bili2nbError) as ei:
        env.run()
    assert ei.value.code == "UPLOAD_FAILED" and "已被" in ei.value.message


def test_ready_stage_fails_when_a_recorded_source_is_not_in_the_cloud(tmp_path, monkeypatch):
    env = live_env(tmp_path, monkeypatch, n=2)
    original = env.nlm.wait_ready

    def vanish(nb, sid, timeout=600.0):
        out = original(nb, sid, timeout=timeout)
        env.sources.items.pop(sid, None)  # server silently dropped it
        return out

    env.nlm.wait_ready = vanish
    with pytest.raises(Bili2nbError) as ei:
        env.run()
    assert ei.value.code == "UPLOAD_FAILED" and "找不到" in ei.value.message
