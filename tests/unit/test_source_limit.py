"""V1.2.3: `import --source-limit N` pretends the account has at most N sources per notebook (to test the
merge planner as a free-tier user). Only ever stricter than the real limit. Offline, no network."""

from __future__ import annotations

import json
import re

import pytest
from typer.testing import CliRunner

from bili2nb import batch, cli, planner
from bili2nb.errors import Bili2nbError
from tests.unit.test_multi_import import URL, Env

runner = CliRunner()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BILI2NB_HOME", str(tmp_path / "home"))
    e = Env(tmp_path)
    e.real_media = True
    return e


# ------------------------------------------------------------------ parsing
@pytest.mark.parametrize("raw,want", [("50", 50), (" 2 ", 2), (25, 25), (None, None)])
def test_parse_source_limit_accepts_integers_of_at_least_two(raw, want):
    assert planner.parse_source_limit(raw) == want


@pytest.mark.parametrize("raw", ["1", "0", "-3", "abc", "2.5", "", True, 1.5])
def test_parse_source_limit_rejects_everything_else(raw):
    with pytest.raises(Bili2nbError) as ei:
        planner.parse_source_limit(raw)
    assert ei.value.code == "INVALID_URL" and "--source-limit" in ei.value.message


# ------------------------------------------------------------------ importer
def test_a_smaller_override_wins_over_the_server_limit(env):
    env.nlm.limit = 50
    res = env.run(source_limit=4)  # 3 parts x 2 = 6 > 4 -> merged to 4 sources
    plan = res["upload_plan"]
    assert plan["source_limit"] == 4 and plan["limit_origin"] == "override" and plan["strategy"] == "merged"
    assert res["source_count"] == 4 and env.nlm.create_calls == 1


def test_a_larger_override_never_loosens_the_server_limit(env):
    env.nlm.limit = 4
    res = env.run(source_limit=50)
    assert res["upload_plan"]["source_limit"] == 4 and res["upload_plan"]["limit_origin"] == "server"
    assert res["source_count"] == 4
    assert any("--source-limit" in m for m in env.logs)  # told that it changes nothing


def test_override_below_the_default_fallback_applies_when_the_server_limit_is_unreadable(env):
    env.nlm.limit_error = Bili2nbError("NETWORK_ERROR", "down")
    res = env.run(source_limit=4)
    assert res["upload_plan"]["source_limit"] == 4 and res["upload_plan"]["limit_origin"] == "override"


def test_no_override_keeps_everything_as_before(env):
    res = env.run()
    assert res["upload_plan"]["source_limit"] == 50 and res["upload_plan"]["limit_origin"] == "server"
    assert res["upload_plan"]["strategy"] == "per_part"


def test_invalid_override_fails_before_any_network_call(env):
    for bad in (1, "x", 0):
        with pytest.raises(Bili2nbError) as ei:
            env.run(source_limit=bad)
        assert ei.value.code == "INVALID_URL"
    assert env.plan_calls == 0 and env.nlm.create_calls == 0


def test_existing_job_keeps_its_plan_and_says_so(env):
    first = env.run()
    again = env.run(source_limit=4)
    assert again["job_id"] == first["job_id"] and again["upload_plan"]["strategy"] == "per_part"
    assert env.nlm.create_calls == 1 and any("--source-limit" in m and "--force-new" in m for m in env.logs)


def test_force_new_with_override_builds_a_second_notebook_under_the_small_limit(env):
    env.run()
    again = env.run(source_limit=4, force_new=True)
    assert again["upload_plan"]["limit_origin"] == "override" and again["source_count"] == 4
    assert env.nlm.create_calls == 2


def test_override_does_not_change_the_idempotency_key(env):
    first = env.run(source_limit=4)
    again = env.run()
    assert again["job_id"] == first["job_id"] and env.nlm.create_calls == 1


def test_single_part_import_ignores_the_override_with_a_hint(env):
    res = env.run(part=1, source_limit=4)
    assert res["status"] == "completed" and any("--source-limit" in m for m in env.logs)


# ------------------------------------------------------------------ CLI
@pytest.fixture
def cli_env(env, monkeypatch):
    monkeypatch.setattr(cli, "_make_importer", lambda: env.importer())
    monkeypatch.setattr(batch.dl, "plan_parts", env.plan)
    monkeypatch.setattr(batch.pipeline, "convert", env.convert)
    return env


def test_cli_json_shows_the_override(cli_env):
    r = runner.invoke(cli.app, ["import", URL, "--source-limit", "4", "--json", "--output", str(cli_env.tmp / "o")])
    assert r.exit_code == 0, r.output
    plan = json.loads(r.stdout)["upload_plan"]
    assert (plan["source_limit"], plan["limit_origin"], plan["merge_mode"]) == (4, "override", "minimal")
    assert plan["merge_group_sizes"] == {"audio": [2, 1], "pdf": [2, 1]} and plan["merge_group_count"] == 2


def test_cli_default_form_accepts_the_flag_too(cli_env):
    r = runner.invoke(cli.app, [URL, "--source-limit", "4", "--json", "--output", str(cli_env.tmp / "o")])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["source_count"] == 4


@pytest.mark.parametrize("bad", ["1", "abc", "2.5"])
def test_cli_rejects_invalid_values_with_exit_code_10(cli_env, bad):
    r = runner.invoke(cli.app, ["import", URL, "--source-limit", bad, "--json"])
    assert r.exit_code == 10, r.output
    assert cli_env.nlm.create_calls == 0


def test_convert_has_no_such_flag():
    assert runner.invoke(cli.app, ["convert", URL, "--source-limit", "4"]).exit_code == 2


def test_help_documents_the_flag():
    out = re.sub(r"\x1b\[[0-9;]*m", "", runner.invoke(cli.app, ["import", "--help"]).output)
    flat = " ".join(out.split())
    assert "--source-limit" in flat and "50" in flat
