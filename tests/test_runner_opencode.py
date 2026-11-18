"""The opencode adapter runs `opencode run --format
json` on 1 local OpenAI-compatible provider, with only the permissions that the
runner table states, and reads the final text and the token counts from the
JSON events.

A fake `opencode` on PATH records its argv, folder, stdin and OPENCODE_*
environment, and prints a synthetic JSONL fixture written for this item
(tests/fixtures/runner/opencode-*.jsonl).
"""

from __future__ import annotations

import json
import os

import pytest

import runner_fakes
from conftest import ROOT
from runner import UsageError, adapters, config
from runner.adapters import opencode
from runner.step import main, run_step

FIX = ROOT / "tests" / "fixtures" / "runner"
ECHO = "The last line of your reply is exactly\n\n```\nECHO: OK\n```\n"
BASE_URL = "http://model-host.example:11434/v1"


def table(**kw) -> dict:
    t = {"adapter": "opencode", "model": "test-local:20b", "base_url": BASE_URL,
         "edit": "allow", "bash": "deny"}
    t.update(kw)
    return t


def expected_config(edit="allow", bash="deny", base_url=BASE_URL, model="test-local:20b") -> dict:
    return {
        "provider": {"local": {"npm": "@ai-sdk/openai-compatible", "name": "local",
                               "options": {"baseURL": base_url}, "models": {model: {"name": model}}}},
        "enabled_providers": ["local"],
        "model": f"local/{model}",
        "permission": {"edit": edit, "bash": bash, "webfetch": "deny"},
        "mcp": {},
        "plugin": [],
        "share": "disabled",
        "autoupdate": False,
    }


@pytest.fixture
def work(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.upper().startswith("OPENCODE_"):
            monkeypatch.delenv(name)
    (tmp_path / "work" / ".git").mkdir(parents=True)  # the git top level: the folder check stops here
    (tmp_path / "echo.md").write_text(ECHO, encoding="utf-8")
    runner_fakes.install(monkeypatch, tmp_path, "opencode")
    runner_fakes.output(monkeypatch, tmp_path, (FIX / "opencode-events.jsonl").read_text(encoding="utf-8"))
    return tmp_path


def run(work, t=None, folder=None, **kw):
    cfg = {"runners": {"x": t or table()}}
    return run_step(folder or work / "work", work / "echo.md", 60, "x", work / "run", cfg=cfg, **kw)


def counters(work) -> dict:
    return json.loads((work / "run" / "counters.json").read_text(encoding="utf-8"))


def nothing_ran(work) -> bool:
    return not (work / "cli-log.json").exists() and not (work / "run").exists()


# --- AC 2: the command -------------------------------------------------------

def test_argv_folder_and_stdin(work):
    c = run(work)
    assert c["status"] == "OK", c
    log = runner_fakes.logged(work)
    assert log["argv"] == ["run", "--format", "json", "-m", "local/test-local:20b"]
    assert log["cwd"] == str((work / "work").resolve())
    assert log["stdin"].replace("\r\n", "\n") == ECHO


@pytest.mark.parametrize("key", ["webfetch", "websearch", "mcp", "plugin", "share", "provider", "agent",
                                 "api_key", "extra_args"])
def test_a_key_that_widens_the_permissions_is_refused_by_name(work, key):
    with pytest.raises(UsageError, match=f"{key} is forbidden"):
        run(work, table(**{key: "x"}))
    assert nothing_ran(work)


@pytest.mark.parametrize("key", ["edit", "bash"])
def test_the_value_ask_is_refused_by_name(work, key):
    with pytest.raises(UsageError, match=f"{key} ask is forbidden"):
        run(work, table(**{key: "ask"}))
    assert nothing_ran(work)


@pytest.mark.parametrize("bad,match", [
    ({"edit": "yes"}, "edit takes"),
    ({"bash": ["allow"]}, "bash takes"),
    ({"bash": None}, "bash takes"),
    ({"edit": None}, "edit takes"),
    ({"base_url": None}, "base_url must be"),
    ({"base_url": "ftp://model-host.example/v1"}, "base_url must be"),
    ({"base_url": "http://model-host.example:11434"}, "base_url must be"),
    ({"base_url": "http://model-host.example:11434/v1/"}, "base_url must be"),
    ({"base_url": "http://user" + "@" + "model-host.example/v1"}, "base_url must be"),  # built: no address in the file
    ({"base_url": "http://model-host.example/v1?x=1"}, "base_url must be"),
    ({"base_url": "http://model-host.example:123456/v1"}, "base_url must be"),
    ({"model": "--auto"}, "model must match"),
    ({"model": "m --auto"}, "model must match"),
    ({"model": "--dangerously-skip-permissions"}, "asks for a dangerous mode"),
    ({"timeout": 5}, "unknown key timeout"),
])
def test_a_bad_table_is_a_usage_error(work, bad, match):
    t = table(**bad)
    for key, value in bad.items():
        if value is None:
            del t[key]
    with pytest.raises(UsageError, match=match):
        run(work, t)
    assert nothing_ran(work)


@pytest.mark.parametrize("url", ["http://localhost:11434/v1", "https://model-host.example/v1",
                                 "http://10.0.0.5:8080/v1"])
def test_a_good_base_url(url):
    opencode.validate("x", table(base_url=url))


# A literal list: a test that read opencode.FORBIDDEN_ARGS would weaken with it.
@pytest.mark.parametrize("arg", [
    "--auto", "--yolo", "--dangerously-skip-permissions", "--attach", "--port", "--share", "--agent",
    "--dir", "--file", "--server", "--attach=http://other.example:4096",
])
def test_last_guard_refuses_a_forbidden_argument(arg):
    good = opencode.argv(table(), "opencode", ROOT)
    adapters.check_argv(good, "opencode", opencode.FORBIDDEN_ARGS)
    with pytest.raises(UsageError):
        adapters.check_argv(good + [arg], "opencode", opencode.FORBIDDEN_ARGS)


# --- AC 3: the config of a step ----------------------------------------------

def test_the_env_of_the_cli_holds_the_config(work):
    run(work)
    env = runner_fakes.logged(work)["env"]
    assert set(env) == {"OPENCODE_CONFIG_CONTENT"}
    assert json.loads(env["OPENCODE_CONFIG_CONTENT"]) == expected_config()


def test_edit_and_bash_come_from_the_table(work):
    run(work, table(edit="deny", bash="allow"))
    config_ = json.loads(runner_fakes.logged(work)["env"]["OPENCODE_CONFIG_CONTENT"])
    assert config_["permission"] == {"edit": "deny", "bash": "allow", "webfetch": "deny"}


def test_an_inherited_config_content_is_replaced(work, monkeypatch):
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", '{"permission": {"webfetch": "allow"}}')
    run(work)
    assert json.loads(runner_fakes.logged(work)["env"]["OPENCODE_CONFIG_CONTENT"]) == expected_config()


@pytest.mark.parametrize("var", ["OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "OPENCODE_PERMISSION",
                                 "OPENCODE_ENABLE_EXA", "opencode_config"])
def test_an_inherited_opencode_variable_is_a_usage_error(work, monkeypatch, var):
    monkeypatch.setenv(var, "x")
    with pytest.raises(UsageError, match="inherited"):
        run(work)
    assert nothing_ran(work)


@pytest.mark.parametrize("name", ["opencode.json", "opencode.jsonc", ".opencode"])
@pytest.mark.parametrize("where", ["folder", "parent"])
def test_an_opencode_config_in_the_folder_or_a_parent_is_a_usage_error(work, name, where):
    top = work / "repo"
    folder = top / "sub" / "work"
    folder.mkdir(parents=True)
    (top / ".git").mkdir()
    place = folder if where == "folder" else top
    if name == ".opencode":
        (place / name).mkdir()
    else:
        (place / name).write_text("{}", encoding="utf-8")
    with pytest.raises(UsageError, match=name.replace(".", r"\.")):
        run(work, folder=folder)
    assert nothing_ran(work)


def test_a_config_above_the_git_top_level_is_not_read(work):
    """opencode reads project config up to the git top level only."""
    top = work / "outer" / "repo"
    (top / ".git").mkdir(parents=True)
    (work / "outer" / "opencode.json").write_text("{}", encoding="utf-8")
    assert run(work, folder=top)["status"] == "OK"


def test_with_no_git_top_level_each_parent_is_checked(work):
    folder = work / "plain" / "a" / "b"
    folder.mkdir(parents=True)
    (work / "plain" / "opencode.jsonc").write_text("{}", encoding="utf-8")
    with pytest.raises(UsageError, match=r"opencode\.jsonc"):
        run(work, folder=folder)


def test_other_adapters_get_no_opencode_env():
    """The claude and codex adapters have no env function: the CLI env is the inherited one."""
    for name in ("claude", "codex", "fake"):
        assert not hasattr(adapters.get(name), "env"), name


# --- AC 4: the counters and $0 -----------------------------------------------

def test_parse_sums_the_step_events_and_takes_the_last_text():
    reply = opencode.parse((FIX / "opencode-events.jsonl").read_text(encoding="utf-8"), table())
    assert reply.final_text == "I listed the folder.\n\nECHO: OK"
    assert reply.tokens == {"input": 2500, "output": 130, "cache_read": 1000, "cache_write": 100}
    assert reply.calls == [{"model": "local", "in": 2500, "cr": 1000, "cw5": 100, "cw1": 0, "out": 130}]


def test_parse_with_no_counts_gives_null_counts_and_1_call():
    reply = opencode.parse((FIX / "opencode-no-counts.jsonl").read_text(encoding="utf-8"), table())
    assert reply.final_text == "I received the prompt.\nECHO: OK"
    assert reply.tokens == {"input": None, "output": None, "cache_read": None, "cache_write": None}
    assert reply.calls == [{"model": "local", "in": 0, "cr": 0, "cw5": 0, "cw1": 0, "out": 0}]


def test_a_count_that_the_events_lack_is_null():
    line = ('{"type":"step_finish","part":{"type":"step-finish","tokens":{"input":7,"output":3,'
            '"reasoning":0}}}')
    reply = opencode.parse(line, table())
    assert reply.tokens == {"input": 7, "output": 3, "cache_read": None, "cache_write": None}


def test_parse_skips_lines_that_are_not_events():
    text = "not json\n[1]\n" + (FIX / "opencode-events.jsonl").read_text(encoding="utf-8")
    assert opencode.parse(text, table()).tokens["input"] == 2500


def test_an_error_event_gives_the_reason(work, monkeypatch):
    runner_fakes.output(monkeypatch, work, (FIX / "opencode-error.jsonl").read_text(encoding="utf-8"))
    monkeypatch.setenv("FAKE_CLI_EXIT", "1")
    c = run(work)
    assert (c["status"], c["reason"]) == ("FAILED", "opencode exited with code 1 (the model is not available)")


@pytest.mark.parametrize("fixture,tokens", [
    ("opencode-events.jsonl", (2500, 130, 1000, 100)),
    ("opencode-no-counts.jsonl", (None, None, None, None)),
])
def test_usd_is_0_from_the_shipped_prices(work, monkeypatch, fixture, tokens):
    runner_fakes.output(monkeypatch, work, (FIX / fixture).read_text(encoding="utf-8"))
    run(work)  # cost/prices.json, the shipped file
    data = counters(work)
    assert data["status"] == "OK"
    assert (data["input_tokens"], data["output_tokens"], data["cache_read_tokens"],
            data["cache_write_tokens"]) == tokens
    assert data["usd"] == 0.0 and isinstance(data["usd"], float)
    assert '"usd": 0.0' in (work / "run" / "counters.json").read_text(encoding="utf-8")


def test_the_local_price_is_0_for_each_token():
    from runner import pricing

    assert pricing.load()["models"]["local"] == {"input": 0.0, "output": 0.0, "cache_read": 0.0}


def test_an_unlisted_codex_model_stays_unpriced_with_the_shipped_prices():
    from runner import pricing

    calls = [{"model": "model-not-in-the-list", "in": 10, "cr": 0, "cw5": 0, "cw1": 0, "out": 1}]
    assert pricing.dollars(calls, pricing.load()) == "unpriced"


@pytest.mark.parametrize("adapter", ["claude", "codex", "fake"])
def test_only_the_opencode_adapter_may_name_the_model_local(adapter):
    """The model local is priced at $0: a paid runner must not take that price."""
    cfg = {"runners": {"x": {"adapter": adapter, "model": "local"}}}
    with pytest.raises(UsageError, match="model local"):
        config.runner_config(cfg, "x")
    assert config.runner_config({"runners": {"x": table(model="local")}}, "x")["model"] == "local"


def test_a_usage_error_through_the_command_prints_no_step_line(work, capsys):
    cfg = {"runners": {"r": table(edit="ask")}}
    code = main(["--folder", str(work / "work"), "--prompt", str(work / "echo.md"), "--timeout", "30",
                 "--runner", "r", "--out", str(work / "run")], cfg=cfg)
    out, err = capsys.readouterr()
    assert code == 2 and "edit ask is forbidden" in err
    assert "STEP:" not in out and "Traceback" not in err
    assert nothing_ran(work)
