import json
import shutil

import pytest
from typer.testing import CliRunner

import bili2nb.pipeline as pl
from bili2nb.bilibili import downloader as dl
from bili2nb.bilibili.downloader import VideoInfo
from bili2nb.cli import app
from bili2nb.errors import Bili2nbError
from bili2nb.media import audio as am
from tests.conftest import plain
from tests.unit import helpers as h

runner = CliRunner()


# ---------------- audio
def test_aac_is_kept_not_reencoded(tmp_path):
    src = tmp_path / "a.m4a"
    h.make_audio(src, 6, "aac")
    orig = src.read_bytes()
    r = am.prepare_audio(src, tmp_path / "out", 6.0)
    assert r.action == "kept" and r.path.name == "audio.m4a" and r.codec == "aac"
    assert r.path.read_bytes() == orig  # byte-identical: no transcode
    assert not src.exists()


def test_mp3_is_kept_with_correct_extension(tmp_path):
    src = tmp_path / "a.bin"
    h.make_audio(src.with_suffix(".mp3"), 5, "mp3")
    r = am.prepare_audio(src.with_suffix(".mp3"), tmp_path / "out", 5.0)
    assert r.path.suffix == ".mp3" and r.action == "kept"


def test_flac_is_transcoded_to_m4a_and_extension_matches(tmp_path):
    src = tmp_path / "a.flac"
    h.make_audio(src, 5, "flac")
    r = am.prepare_audio(src, tmp_path / "out", 5.0)
    assert r.action == "transcoded" and r.path.suffix == ".m4a"
    assert am.audio_stream(am.probe(r.path))["codec_name"] == "aac"  # extension == real codec


def test_truncated_audio_is_rejected(tmp_path):
    src = tmp_path / "a.m4a"
    h.make_audio(src, 5, "aac")
    with pytest.raises(Bili2nbError) as e:
        am.prepare_audio(src, tmp_path / "out", 60.0)  # metadata says 60s, file has 5s
    assert e.value.code == "MEDIA_ERROR" and "截断" in e.value.message


def test_duration_tolerance_is_max_2s_or_1pct(tmp_path):
    src = tmp_path / "a.m4a"
    h.make_audio(src, 100, "aac")
    am.prepare_audio(src, tmp_path / "out", 101.5)  # within 2s -> ok


def test_no_audio_stream_and_garbage(tmp_path):
    g = tmp_path / "g.m4a"
    g.write_bytes(b"garbage")
    with pytest.raises(Bili2nbError) as e:
        am.prepare_audio(g, tmp_path / "o", 5)
    assert e.value.code == "MEDIA_ERROR"
    v = tmp_path / "v.mp4"
    h.write_video(v, [(h.black(), 2)])  # video only, no audio stream
    with pytest.raises(Bili2nbError) as e2:
        am.prepare_audio(v, tmp_path / "o2", 2)
    assert "没有音频流" in e2.value.message


def test_oversize_audio_is_transcoded_down_then_rejected_if_still_too_big(tmp_path, monkeypatch):
    monkeypatch.setattr(am, "MAX_UPLOAD_BYTES", 20_000)
    src = tmp_path / "a.m4a"
    h.make_audio(src, 20, "aac")
    with pytest.raises(Bili2nbError) as e:
        am.prepare_audio(src, tmp_path / "o", 20.0)
    assert e.value.code == "MEDIA_ERROR" and "200 MB" in e.value.message  # never truncates


def test_decode_failure_detected(tmp_path):
    src = tmp_path / "a.m4a"
    h.make_audio(src, 10, "aac")
    data = bytearray(src.read_bytes())
    for i in range(len(data) // 2, len(data) // 2 + 600):  # corrupt payload, keep headers
        data[i] = 0xFF
    src.write_bytes(bytes(data))
    with pytest.raises(Bili2nbError) as e:
        am.decode_check(src)
    assert e.value.code == "MEDIA_ERROR"


def test_require_tools_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda t: None)
    with pytest.raises(Bili2nbError) as e:
        am.require_tools()
    assert e.value.code == "TOOL_MISSING" and am.ffmpeg_install_hint() in e.value.message  # per-OS hint


# ---------------- pipeline (downloads mocked with synthetic media)
URL = "https://www.bilibili.com/video/BV1GJ411x7h7"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BILI2NB_HOME", str(tmp_path / "home"))
    dur = 24.0
    info = VideoInfo("BV1GJ411x7h7", "999", 1, "测试视频", "作者", dur, URL)
    calls = {"audio": 0, "video": 0}

    def fake_audio(ref, dest):
        calls["audio"] += 1
        dest.mkdir(parents=True, exist_ok=True)
        p = dest / "audio_src.m4a"
        h.make_audio(p, dur, "aac")
        return p

    def fake_video(ref, dest):
        calls["video"] += 1
        dest.mkdir(parents=True, exist_ok=True)
        p = dest / "video_src.mp4"
        h.write_video(p, [(h.slide(220, "A"), 8), (h.slide(100, "B"), 8), (h.slide(170, "C"), 8)])
        return p

    monkeypatch.setattr(dl, "fetch_info", lambda ref: info)
    monkeypatch.setattr(dl, "plan_parts", lambda ref: [info])  # CLI path: no network
    monkeypatch.setattr(dl, "download_audio", fake_audio)
    monkeypatch.setattr(dl, "download_video", fake_video)
    return tmp_path, calls


def test_convert_success_outputs_and_manifest(env):
    tmp, _ = env
    res = pl.convert(URL, output_root=tmp / "out", frames_mode="slides", log=lambda m: None)
    d = tmp / "out" / "BV1GJ411x7h7" / "P01_测试视频"
    assert res["status"] == "ok" and not res["reused"]
    assert (d / "audio.m4a").exists() and (d / "keyframes.pdf").exists()
    m = json.loads((d / "manifest.json").read_text())
    assert m["complete"] is True and m["visual"]["status"] == "ok" and m["visual"]["pages"] == 4
    assert m["audio"]["action"] == "kept" and m["video_key"] == "BV1GJ411x7h7_999"
    assert set(m["timings_sec"]) >= {"metadata", "audio", "frames", "pdf"}
    blob = json.dumps(m)
    assert "cookie" not in blob.lower() and "SAPISID" not in blob
    assert not list((tmp / "home" / "work").iterdir())  # temp dir cleaned
    assert sorted(p.name for p in d.iterdir()) == ["audio.m4a", "keyframes.pdf", "manifest.json"]


def test_convert_reuses_complete_cache_without_downloading(env):
    tmp, calls = env
    pl.convert(URL, output_root=tmp / "out", frames_mode="slides", log=lambda m: None)
    again = pl.convert(URL, output_root=tmp / "out", frames_mode="slides", log=lambda m: None)
    assert again["reused"] is True and calls == {"audio": 1, "video": 1}


def test_audio_only_skips_video_download(env):
    tmp, calls = env
    res = pl.convert(URL, output_root=tmp / "out", audio_only=True, log=lambda m: None)
    assert calls["video"] == 0 and "pdf_path" not in res
    assert res["visual"]["status"] == "skipped" and (tmp / "out/BV1GJ411x7h7/P01_测试视频/audio.m4a").exists()


def test_different_mode_replaces_own_previous_output(env):
    tmp, _ = env
    pl.convert(URL, output_root=tmp / "out", audio_only=True, log=lambda m: None)
    res = pl.convert(URL, output_root=tmp / "out", frames_mode="slides", log=lambda m: None)
    assert res["visual"]["status"] == "ok" and not res["reused"]
    assert not [p for p in (tmp / "out").iterdir() if ".old-" in p.name]


def test_visual_skipped_is_explicit(env, monkeypatch):
    tmp, _ = env

    def black_video(ref, dest):
        dest.mkdir(parents=True, exist_ok=True)
        p = dest / "video_src.mp4"
        h.write_video(p, [(h.black(), 24)])
        return p

    monkeypatch.setattr(dl, "download_video", black_video)
    res = pl.convert(URL, output_root=tmp / "out", frames_mode="slides", log=lambda m: None)
    assert res["visual"]["status"] == "visual_skipped" and "pdf_path" not in res
    assert any("visual_skipped" in w for w in res["warnings"])


def test_failure_leaves_no_half_finished_cache(env, monkeypatch):
    tmp, _ = env

    def broken_video(ref, dest):
        raise Bili2nbError("MEDIA_ERROR", "boom")

    monkeypatch.setattr(dl, "download_video", broken_video)
    with pytest.raises(Bili2nbError):
        pl.convert(URL, output_root=tmp / "out", frames_mode="slides", log=lambda m: None)
    assert not (tmp / "out" / "BV1GJ411x7h7" / "P01_测试视频").exists()
    assert not list((tmp / "home" / "work").iterdir())


def test_existing_foreign_directory_is_never_overwritten(env):
    tmp, _ = env
    foreign = tmp / "out" / "BV1GJ411x7h7" / "P01_测试视频"
    foreign.mkdir(parents=True)
    (foreign / "mine.txt").write_text("keep")
    with pytest.raises(Bili2nbError):
        pl.convert(URL, output_root=tmp / "out", frames_mode="slides", log=lambda m: None)
    assert (foreign / "mine.txt").read_text() == "keep"


def test_ffmpeg_missing_fails_before_any_download(env, monkeypatch):
    tmp, calls = env
    monkeypatch.setattr(shutil, "which", lambda t: None)
    with pytest.raises(Bili2nbError) as e:
        pl.convert(URL, output_root=tmp / "out", log=lambda m: None)
    assert e.value.code == "TOOL_MISSING" and calls == {"audio": 0, "video": 0}


def test_disk_full_detected_before_download(env, monkeypatch):
    tmp, calls = env
    monkeypatch.setattr(shutil, "disk_usage", lambda p: type("U", (), {"free": 1000})())
    with pytest.raises(Bili2nbError) as e:
        pl.convert(URL, output_root=tmp / "out", log=lambda m: None)
    assert e.value.code == "DISK_FULL" and calls["audio"] == 0


def test_invalid_url_fails_fast(env):
    tmp, calls = env
    with pytest.raises(Bili2nbError) as e:
        pl.convert("https://evil.com/video/BV1GJ411x7h7", output_root=tmp / "out")
    assert e.value.code == "INVALID_URL" and calls["audio"] == 0


def test_part_option_applies_and_url_part_wins(env, monkeypatch):
    tmp, _ = env
    seen = []

    def spy(ref):
        seen.append(ref.part)
        return VideoInfo("BV1GJ411x7h7", "999", ref.part or 1, "t", "u", 24.0, ref.url)

    monkeypatch.setattr(dl, "fetch_info", spy)
    pl.convert(URL, output_root=tmp / "o1", audio_only=True, part=3, log=lambda m: None)
    r = pl.convert(URL + "?p=2", output_root=tmp / "o2", audio_only=True, part=5, log=lambda m: None)
    assert seen == [3, 2] and any("忽略 --part" in w for w in r["warnings"])


# ---------------- CLI
def test_cli_convert_json_is_one_object_and_progress_not_in_stdout(env):
    tmp, _ = env
    r = runner.invoke(app, ["convert", URL, "--json", "--frames", "slides",
                            "--output", str(tmp / "out")])
    assert r.exit_code == 0, r.output
    obj = json.loads(r.stdout)  # entire stdout is exactly one JSON document
    assert obj["status"] == "ok" and obj["audio_path"].endswith("audio.m4a")
    assert obj["pdf_path"].endswith("keyframes.pdf") and obj["video_title"] == "测试视频"
    assert "[metadata]" not in r.stdout  # progress goes to stderr


def test_cli_convert_error_codes_and_json_errors(env):
    r = runner.invoke(app, ["convert", "https://evil.com/x", "--json"])
    assert r.exit_code == 10
    obj = json.loads(r.stdout)
    assert obj == {"status": "failed", "error_code": "INVALID_URL", "message": obj["message"]}


def test_cli_audio_only_flag(env):
    tmp, calls = env
    r = runner.invoke(app, ["convert", URL, "--audio-only", "--output", str(tmp / "out")])
    assert r.exit_code == 0 and "audio.m4a" in r.stdout and calls["video"] == 0


def test_cli_interrupt_is_clean(env, monkeypatch):
    def ctrl_c(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(pl, "convert", ctrl_c)
    r = runner.invoke(app, ["convert", URL, "--json"])
    assert r.exit_code == 130 and json.loads(r.stdout)["error_code"] == "INTERRUPTED"


def test_cli_help_lists_only_implemented_options():
    out = plain(runner.invoke(app, ["convert", "--help"]).output)
    for opt in ("--frames", "--output", "--part", "--audio-only", "--json"):
        assert opt in out
    for not_yet in ("--keep-files", "--force-new", "--no-wait"):
        assert not_yet not in out


def test_legacy_v11_folder_is_reused_read_only(env):
    """A complete V1.1 `<BV>_<CID>` folder is reused, never moved or rewritten."""
    tmp, calls = env
    legacy = tmp / "out" / "BV1GJ411x7h7_999"
    pl.convert(URL, output_root=tmp / "out", frames_mode="slides", log=lambda m: None)
    new = tmp / "out" / "BV1GJ411x7h7" / "P01_测试视频"
    new.rename(legacy)  # pretend an old run produced it
    before = sorted(p.name for p in legacy.iterdir())
    res = pl.convert(URL, output_root=tmp / "out", frames_mode="slides", log=lambda m: None)
    assert res["reused"] is True and res["output_dir"] == str(legacy)
    assert calls == {"audio": 1, "video": 1} and sorted(p.name for p in legacy.iterdir()) == before


def test_renamed_part_title_still_hits_the_cid_cache(env, monkeypatch):
    tmp, calls = env
    pl.convert(URL, output_root=tmp / "out", frames_mode="slides", log=lambda m: None)
    renamed = VideoInfo("BV1GJ411x7h7", "999", 1, "改名后的标题", "作者", 24.0, URL)
    monkeypatch.setattr(dl, "fetch_info", lambda ref: renamed)
    res = pl.convert(URL, output_root=tmp / "out", frames_mode="slides", log=lambda m: None)
    assert res["reused"] is True and calls == {"audio": 1, "video": 1}
