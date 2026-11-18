"""The Codex adapter runs `codex exec --json` with only the
permissions that the runner config states, and reads the final message and the
token usage from the JSONL events.

A fake `codex` on PATH records its argv, folder and stdin and prints a
synthetic JSONL fixture written for this item (tests/fixtures/runner/).
"""

from __future__ import annotations

import json

import pytest

import runner_fakes
from conftest import ROOT
from runner import UsageError, adapters
from runner.adapters import codex
from runner.step import main, run_step

FIX = ROOT / "tests" / "fixtures" / "runner"
ECHO = "The last line of your reply is exactly\n\n```\nECHO: OK\n```\n"
PRICES = ROOT / "tests" / "fixtures" / "cost" / "prices.json"


def table(**kw) -> dict:
    t = {"adapter": "codex", "model": "test-model-a", "sandbox": "read-only"}
    t.update(kw)
    return t


@pytest.fixture
def work(tmp_path, monkeypatch):
    (tmp_path / "work").mkdir()
    (tmp_path / "echo.md").write_text(ECHO, encoding="utf-8")
    runner_fakes.install(monkeypatch, tmp_path, "codex")
    runner_fakes.output(monkeypatch, tmp_path, (FIX / "codex-events.jsonl").read_text(encoding="utf-8"))
    return tmp_path


def run(work, t=None, **kw):
    cfg = {"runners": {"x": t or table()}}
    return run_step(work / "work", work / "echo.md", 60, "x", work / "run", cfg=cfg, **kw)


def counters(work) -> dict:
    return json.loads((work / "run" / "counters.json").read_text(encoding="utf-8"))


def test_argv_folder_and_stdin(work):
    c = run(work)
    assert c["status"] == "OK", c
    log = runner_fakes.logged(work)
    folder = str((work / "work").resolve())
    assert log["argv"] == ["exec", "--json", "-C", folder, "--ephemeral", "--ignore-user-config",
                           "--ignore-rules", "-s", "read-only", "-m", "test-model-a", "-"]
    assert log["cwd"] == folder
    assert log["stdin"].replace("\r\n", "\n") == ECHO


def test_workspace_write_sandbox(work):
    run(work, table(sandbox="workspace-write"))
    argv = runner_fakes.logged(work)["argv"]
    assert argv[argv.index("-s") + 1] == "workspace-write"


def test_final_message_and_usage(work):
    run(work, prices_file=PRICES)
    data = counters(work)
    assert (data["status"], data["last_line"]) == ("OK", "ECHO: OK")
    # the raw values: input_tokens includes the cached tokens and the cache writes
    assert (data["input_tokens"], data["output_tokens"], data["cache_read_tokens"],
            data["cache_write_tokens"]) == (12000, 300, 8000, 0)
    # 4,000 input at $1 + 8,000 cached at $0.10 + 300 output at $10, per million
    assert data["usd"] == pytest.approx(0.004 + 0.0008 + 0.003)


def events(cache_write: str) -> str:
    """The fixture with the cache-write field set to a value, or removed."""
    text = (FIX / "codex-events.jsonl").read_text(encoding="utf-8")
    field = '"cache_write_input_tokens":0,'
    assert field in text
    return text.replace(field, f'"cache_write_input_tokens":{cache_write},' if cache_write else "")


def test_cache_writes_are_counted_and_priced(work, monkeypatch):
    runner_fakes.output(monkeypatch, work, events("2000"))
    run(work, prices_file=PRICES)
    data = counters(work)
    assert (data["input_tokens"], data["cache_read_tokens"], data["cache_write_tokens"]) == (12000, 8000, 2000)
    # 2,000 input at $1 + 8,000 cached at $0.10 + 2,000 written at $1 x 1.25
    # + 300 output at $10, per million: the writes leave the plain input
    assert data["usd"] == pytest.approx(0.002 + 0.0008 + 0.0025 + 0.003)


def test_cache_writes_of_each_turn_add_up():
    text = events("2000")
    turn = text.splitlines()[-1]
    second = turn.replace('"cache_write_input_tokens":2000', '"cache_write_input_tokens":500')
    reply = codex.parse(text + "\n" + second, table())
    assert reply.tokens["cache_write"] == 2500
    assert reply.calls == [{"model": "test-model-a", "in": 24000 - 16000 - 2500, "cr": 16000,
                            "cw5": 2500, "cw1": 0, "out": 600}]


def test_no_cache_write_field_is_null_not_zero():
    reply = codex.parse(events(""), table())
    assert reply.tokens["cache_write"] is None
    assert reply.calls == [{"model": "test-model-a", "in": 4000, "cr": 8000, "cw5": 0, "cw1": 0, "out": 300}]


def test_counts_past_the_input_never_price_below_0():
    """Bad usage (more cached and written than input): no negative token count."""
    reply = codex.parse(events("9000"), table())
    assert reply.calls == [{"model": "test-model-a", "in": 0, "cr": 8000, "cw5": 4000, "cw1": 0, "out": 300}]

def test_parse_takes_the_last_agent_message():
    reply = codex.parse((FIX / "codex-events.jsonl").read_text(encoding="utf-8"), table())
    assert reply.final_text == "I listed the folder.\n\nECHO: OK"
    assert reply.calls == [{"model": "test-model-a", "in": 4000, "cr": 8000, "cw5": 0, "cw1": 0, "out": 300}]


def test_parse_failed_turn():
    reply = codex.parse((FIX / "codex-failed.jsonl").read_text(encoding="utf-8"), table())
    assert reply.final_text is None
    assert reply.error == "the model is not available"
    assert reply.calls == [] and reply.tokens["input"] is None


def test_failed_turn_with_exit_1_is_failed(work, monkeypatch):
    runner_fakes.output(monkeypatch, work, (FIX / "codex-failed.jsonl").read_text(encoding="utf-8"))
    monkeypatch.setenv("FAKE_CLI_EXIT", "1")
    c = run(work)
    assert (c["status"], c["exit_code"], c["usd"]) == ("FAILED", 1, None)
    assert c["reason"] == "codex exited with code 1 (the model is not available)"


def test_failed_turn_with_exit_0_is_invalid(work, monkeypatch):
    runner_fakes.output(monkeypatch, work, (FIX / "codex-failed.jsonl").read_text(encoding="utf-8"))
    c = run(work)
    assert c["status"] == "INVALID"
    assert c["reason"] == "no final text (the model is not available)"


def test_unpriced_model(work):
    run(work, table(model="model-not-in-the-list"), prices_file=PRICES)
    assert counters(work)["usd"] == "unpriced"


def test_unavailable_names_the_cli(tmp_path, monkeypatch):
    (tmp_path / "work").mkdir()
    (tmp_path / "echo.md").write_text(ECHO, encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    c = run(tmp_path)
    assert (c["status"], c["reason"]) == ("UNAVAILABLE", f"codex is not on PATH ({tmp_path / 'empty'})")


def test_unavailable_names_the_searched_path(tmp_path, monkeypatch):
    """The reason keeps its first words and names the PATH it searched."""
    (tmp_path / "work").mkdir()
    (tmp_path / "echo.md").write_text(ECHO, encoding="utf-8")
    searched = str(tmp_path / "empty")
    monkeypatch.setenv("PATH", searched)
    c = run(tmp_path)
    assert c["reason"].startswith("codex is not on PATH")
    assert c["reason"] == f"codex is not on PATH ({searched})"


# --- forbidden settings: each is a usage error, and nothing runs --------------

@pytest.mark.parametrize("bad,match", [
    ({"sandbox": "danger-full-access"}, "danger-full-access is forbidden"),
    ({"sandbox": "full"}, "sandbox takes"),
    ({"sandbox": None}, "sandbox takes"),
    ({"dangerously_bypass_approvals_and_sandbox": True}, "forbidden"),
    ({"dangerously_bypass_hook_trust": True}, "forbidden"),
    ({"search": True}, "forbidden"),
    ({"add_dir": ["/"]}, "forbidden"),
    ({"oss": True}, "forbidden"),
    ({"local_provider": "ollama"}, "forbidden"),
    ({"config": ["sandbox_mode=danger-full-access"]}, "forbidden"),
    ({"profile": "wide"}, "forbidden"),
    ({"enable": ["x"]}, "forbidden"),
    ({"approve_for_me": True}, "forbidden"),
    ({"skip_git_repo_check": True}, "forbidden"),
    ({"extra_args": ["--search"]}, "forbidden"),
    ({"model": "--oss"}, "model must match"),
    ({"model": "--dangerously-bypass-approvals-and-sandbox"}, "asks for a dangerous mode"),
    ({"model": "m -s danger-full-access"}, "model must match"),
    ({"approval": "never"}, "unknown key approval"),
])
def test_forbidden_setting_is_a_usage_error(work, bad, match):
    t = table(**bad)
    if bad.get("sandbox", "") is None:
        del t["sandbox"]
    with pytest.raises(UsageError, match=match):
        run(work, t)
    assert not (work / "cli-log.json").exists()
    assert not (work / "run").exists()


# A literal list: a test that read codex.FORBIDDEN_ARGS would weaken with it.
@pytest.mark.parametrize("arg", [
    "--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust", "--dangerously-new",
    "--search", "--add-dir", "--oss", "--local-provider", "-c", "--config", "-p", "--profile",
    "--enable", "--approve-for-me", "--skip-git-repo-check", "danger-full-access",
    "--sandbox=danger-full-access", "--config=sandbox_mode=danger-full-access",
])
def test_last_guard_refuses_a_forbidden_argument(arg):
    good = codex.argv(table(), "codex", ROOT)
    adapters.check_argv(good, "codex", codex.FORBIDDEN_ARGS)
    with pytest.raises(UsageError):
        adapters.check_argv(good + [arg], "codex", codex.FORBIDDEN_ARGS)


def test_argv_never_holds_a_forbidden_flag():
    for sandbox in ("read-only", "workspace-write"):
        t = table(sandbox=sandbox)
        codex.validate("x", t)
        argv = codex.argv(t, "codex", ROOT)
        assert not set(argv) & codex.FORBIDDEN_ARGS
        assert not any("dangerously" in a for a in argv)
        for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--json"):
            assert flag in argv
        assert argv[argv.index("-s") + 1] == sandbox


# --- a value of the wrong type: a usage error through the command, no crash ---

@pytest.mark.parametrize("value", [['read-only'], 1, 1.5, True, {"a": 1}])
def test_wrong_type_is_a_usage_error_through_the_command(work, capsys, value):
    cfg = {"runners": {"r": table(**{'sandbox': value})}}
    code = main(["--folder", str(work / "work"), "--prompt", str(work / "echo.md"), "--timeout", "30",
                 "--runner", "r", "--out", str(work / "run")], cfg=cfg)
    out, err = capsys.readouterr()
    assert code == 2
    assert "sandbox takes" in err
    assert "STEP:" not in out and "Traceback" not in err
    assert not (work / "run").exists()
    assert not (work / "cli-log.json").exists()


def test_codex_writes_no_reply_file(work):
    """Only an adapter that gives Reply.report writes reply.txt."""
    assert run(work)["status"] == "OK"
    assert not (work / "run" / "reply.txt").exists()
