"""The Claude adapter runs `claude -p` with only the permissions
that the runner config states, and reads the text, usage and duration from JSON.

A fake `claude` on PATH records its argv, folder and stdin and prints a
synthetic result (tests/fixtures/runner/claude-result.json).
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

import runner_fakes
from conftest import ROOT
from runner import UsageError, adapters, proc
from runner.adapters import claude
from runner.step import main, run_step

FIX = ROOT / "tests" / "fixtures" / "runner"
PROMPT = ROOT / "prompts" / "spec-check.md"
ECHO = "The last line of your reply is exactly\n\n```\nECHO: OK\n```\n"
PRICES = ROOT / "tests" / "fixtures" / "cost" / "prices.json"


def table(**kw) -> dict:
    t = {"adapter": "claude", "model": "test-model-a", "allowed_tools": ["Read", "Grep"]}
    t.update(kw)
    return t


@pytest.fixture
def work(tmp_path, monkeypatch):
    (tmp_path / "work").mkdir()
    (tmp_path / "echo.md").write_text(ECHO, encoding="utf-8")
    runner_fakes.install(monkeypatch, tmp_path, "claude")
    runner_fakes.output(monkeypatch, tmp_path, (FIX / "claude-result.json").read_text(encoding="utf-8"))
    return tmp_path


def run(work, t=None, **kw):
    cfg = {"runners": {"c": t or table()}}
    return run_step(work / "work", work / "echo.md", 60, "c", work / "run", cfg=cfg, **kw)


def test_argv_folder_and_stdin(work):
    c = run(work)
    assert c["status"] == "OK", c
    log = runner_fakes.logged(work)
    assert log["argv"] == ["-p", "--output-format", "stream-json", "--verbose", "--model", "test-model-a",
                           "--allowedTools", "Read,Grep", "--permission-mode", "default",
                           "--setting-sources", "", "--strict-mcp-config"]
    assert log["cwd"] == str((work / "work").resolve())
    assert log["stdin"].replace("\r\n", "\n") == ECHO


def test_setting_sources_and_permission_mode_from_config(work):
    run(work, table(setting_sources=["project", "local"], permission_mode="dontAsk"))
    argv = runner_fakes.logged(work)["argv"]
    assert argv[argv.index("--setting-sources") + 1] == "project,local"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "--strict-mcp-config" in argv


@pytest.mark.parametrize("mode", ["default", "dontAsk", "plan"])
def test_each_accepted_permission_mode_reaches_the_cli(work, mode):
    run(work, table(permission_mode=mode))
    argv = runner_fakes.logged(work)["argv"]
    assert argv[argv.index("--permission-mode") + 1] == mode


def test_the_accepted_permission_modes_are_exactly_3():
    # a literal set: a test that read claude.PERMISSION_MODES would widen with it
    assert claude.PERMISSION_MODES == {"default", "dontAsk", "plan"}


def test_permission_mode_is_always_passed(work):
    """With no permission_mode, the step still passes default: a named setting
    source must not set the mode through its permissions.defaultMode."""
    run(work, table(setting_sources=["project"]))
    argv = runner_fakes.logged(work)["argv"]
    assert argv.count("--permission-mode") == 1
    assert argv[argv.index("--permission-mode") + 1] == "default"


def test_no_allowed_tools_passes_no_flag(work):
    run(work, table(allowed_tools=[]))
    argv = runner_fakes.logged(work)["argv"]
    assert "--allowedTools" not in argv
    assert argv[argv.index("--setting-sources") + 1] == ""


def test_text_usage_and_duration_from_json(work):
    run(work, prices_file=PRICES)
    data = json.loads((work / "run" / "counters.json").read_text(encoding="utf-8"))
    assert (data["status"], data["last_line"], data["exit_code"]) == ("OK", "ECHO: OK", 0)
    assert (data["input_tokens"], data["output_tokens"], data["cache_read_tokens"],
            data["cache_write_tokens"]) == (1000, 500, 20000, 3000)
    assert data["cli_seconds"] == 4.2
    # priced by the model of modelUsage, which the fixture prices do not list
    assert data["usd"] == "unpriced"
    assert (work / "run" / "stderr.txt").read_text(encoding="utf-8").strip() == "fake cli stderr"


def test_price_by_model_usage_with_the_ttl_split():
    out = (FIX / "claude-result.json").read_text(encoding="utf-8").replace("claude-haiku-5-5", "test-model-a")
    reply = claude.parse(out, table())
    assert reply.calls == [{"model": "test-model-a", "in": 1000, "cr": 20000, "cw5": 2000, "cw1": 1000, "out": 500}]


def test_price_by_config_model_without_model_usage():
    data = json.loads((FIX / "claude-result.json").read_text(encoding="utf-8"))
    del data["modelUsage"]
    reply = claude.parse(json.dumps(data), table())
    assert reply.calls == [{"model": "test-model-a", "in": 1000, "cr": 20000, "cw5": 2000, "cw1": 1000, "out": 500}]
    del data["usage"]["cache_creation"]
    reply = claude.parse(json.dumps(data), table())
    assert (reply.calls[0]["cw5"], reply.calls[0]["cw1"]) == (3000, 0)


def test_dollars_through_prices(work, monkeypatch):
    out = (FIX / "claude-result.json").read_text(encoding="utf-8").replace("claude-haiku-5-5", "test-model-a")
    runner_fakes.output(monkeypatch, work, out)
    run(work, prices_file=PRICES)
    data = json.loads((work / "run" / "counters.json").read_text(encoding="utf-8"))
    # input 0.001 + out 0.005 + read 0.002 + 5-min write 0.0025 + 1-hour write 0.002
    assert data["usd"] == pytest.approx(0.0125)


def test_result_in_a_list_and_garbage():
    result = (FIX / "claude-result.json").read_text(encoding="utf-8")
    listed = json.dumps([{"type": "system"}, json.loads(result)])
    assert claude.parse(listed, table()).final_text.endswith("ECHO: OK")
    assert claude.parse("log line\n" + result, table()).final_text.endswith("ECHO: OK")
    r = claude.parse("not json", table())
    assert r.final_text is None and r.error == "no JSON result" and r.calls == []


def test_error_result_with_exit_1_is_failed(work, monkeypatch):
    runner_fakes.output(monkeypatch, work, json.dumps(
        {"type": "result", "subtype": "error_max_turns", "is_error": True, "result": "stopped"}))
    monkeypatch.setenv("FAKE_CLI_EXIT", "1")
    c = run(work)
    assert (c["status"], c["exit_code"]) == ("FAILED", 1)
    assert c["usd"] is None


def test_unavailable_names_the_cli(tmp_path, monkeypatch):
    (tmp_path / "work").mkdir()
    (tmp_path / "echo.md").write_text(ECHO, encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    c = run(tmp_path)
    assert (c["status"], c["reason"]) == ("UNAVAILABLE", f"claude is not on PATH ({tmp_path / 'empty'})")


def test_unavailable_names_the_searched_path(tmp_path, monkeypatch):
    """The reason keeps its first words and names the PATH it searched."""
    (tmp_path / "work").mkdir()
    (tmp_path / "echo.md").write_text(ECHO, encoding="utf-8")
    searched = str(tmp_path / "empty")
    monkeypatch.setenv("PATH", searched)
    c = run(tmp_path)
    assert c["reason"].startswith("claude is not on PATH")
    assert c["reason"] == f"claude is not on PATH ({searched})"


# --- forbidden settings: each is a usage error, and nothing runs --------------

@pytest.mark.parametrize("bad,match", [
    ({"permission_mode": "bypassPermissions"}, "bypassPermissions is forbidden"),
    ({"permission_mode": "auto"}, "permission_mode takes"),
    # acceptEdits approves file edits that allowed_tools does not name
    ({"permission_mode": "acceptEdits"}, "permission_mode takes"),
    ({"permission_mode": "Default"}, "permission_mode takes"),
    ({"permission_mode": ""}, "permission_mode takes"),
    ({"dangerously_skip_permissions": True}, "forbidden"),
    ({"allow_dangerously_skip_permissions": True}, "forbidden"),
    ({"allowed_tools": ["--dangerously-skip-permissions"]}, "asks for a dangerous mode"),
    ({"allowed_tools": ["Read", "--add-dir"]}, "allowed_tools must match"),
    ({"allowed_tools": ["Bash(rm *)"]}, "allowed_tools must match"),
    ({"allowed_tools": "Read"}, "list of strings"),
    ({"setting_sources": ["user", "--settings"]}, "setting_sources takes"),
    ({"setting_sources": "user"}, "list of strings"),
    ({"model": "--dangerously-skip-permissions"}, "asks for a dangerous mode"),
    ({"model": "x --permission-mode bypassPermissions"}, "model must match"),
    ({"model": "-x"}, "model must match"),
    ({"settings": "{}"}, "forbidden"),
    ({"mcp_config": "servers.json"}, "forbidden"),
    ({"add_dir": "/"}, "forbidden"),
    ({"extra_args": ["--verbose"]}, "forbidden"),
    ({"tools": ["Bash"]}, "unknown key tools"),
])
def test_forbidden_setting_is_a_usage_error(work, bad, match):
    with pytest.raises(UsageError, match=match):
        run(work, table(**bad))
    assert not (work / "cli-log.json").exists()
    assert not (work / "run").exists()


@pytest.mark.parametrize("arg", ["--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
                                 "bypassPermissions", "--settings", "--mcp-config", "--add-dir",
                                 "--permission-mode=bypassPermissions", "--dangerously-any-new-flag",
                                 "--DANGEROUSLY-skip"])
def test_last_guard_refuses_a_forbidden_argument(arg):
    good = claude.argv(table(), "claude", ROOT)
    adapters.check_argv(good, "claude", claude.FORBIDDEN_ARGS)
    with pytest.raises(UsageError):
        adapters.check_argv(good + [arg], "claude", claude.FORBIDDEN_ARGS)


def test_argv_never_holds_a_bypass():
    for t in (table(), table(permission_mode="plan", setting_sources=["user"])):
        claude.validate("c", t)
        argv = claude.argv(t, "claude", ROOT)
        assert not any("dangerously" in a or "bypass" in a.lower() for a in argv)
        assert "--strict-mcp-config" in argv and "--setting-sources" in argv


def test_cmd_shim_refuses_cmd_metacharacters():
    with pytest.raises(UsageError, match="cmd.exe"):
        adapters.check_argv(["x.cmd", "-C", "a&b"], "x.cmd", set())
    adapters.check_argv(["x.exe", "-C", "a&b"], "x.exe", set())


# --- a value of the wrong type: a usage error through the command, no crash ---

@pytest.mark.parametrize("value", [['default'], 1, 1.5, True, {"a": 1}])
def test_wrong_type_is_a_usage_error_through_the_command(work, capsys, value):
    cfg = {"runners": {"r": table(**{'permission_mode': value})}}
    code = main(["--folder", str(work / "work"), "--prompt", str(work / "echo.md"), "--timeout", "30",
                 "--runner", "r", "--out", str(work / "run")], cfg=cfg)
    out, err = capsys.readouterr()
    assert code == 2
    assert "permission_mode takes" in err
    assert "STEP:" not in out and "Traceback" not in err
    assert not (work / "run").exists()
    assert not (work / "cli-log.json").exists()


# --- the stream: each turn of the reply is kept --------------------

BUILD = ROOT / "prompts" / "build.md"
SHA = "a" * 40


def stream(*texts, last=None) -> str:
    """A stream-json output: a system line, then 1 assistant line and 1 result
    line for each text (None: a result with no text). `last` updates the last result."""
    lines = [{"type": "system", "subtype": "init"}]
    for i, t in enumerate(texts):
        lines.append({"type": "assistant", "message": {"content": [{"type": "text", "text": t or ""}]}})
        r = {"type": "result", "subtype": "success", "is_error": False, "result_index": i}
        if t is not None:
            r["result"] = t
        lines.append(r)
    lines[-1].update(last or {})
    return "".join(json.dumps(x) + "\n" for x in lines)


def test_stdout_holds_each_result_line(work, monkeypatch):
    out = stream("A\n\nECHO: OK", "B\n\nECHO: OK", "C\n\nECHO: OK")
    runner_fakes.output(monkeypatch, work, out)
    run(work)
    stored = (work / "run" / "stdout.txt").read_text(encoding="utf-8")
    assert stored == out
    assert [json.loads(x)["result"] for x in stored.splitlines() if json.loads(x)["type"] == "result"] == [
        "A\n\nECHO: OK", "B\n\nECHO: OK", "C\n\nECHO: OK"]


def test_report_holds_each_turn_in_order():
    reply = claude.parse(stream("A", "B", "C"), table())
    assert reply.report == "A\n\nB\n\nC"
    assert reply.final_text == "C"


def test_a_result_with_no_text_adds_nothing_to_the_report():
    reply = claude.parse(stream("A", None, "", "C"), table())
    assert reply.report == "A\n\nC"
    lines = stream("A", "C").splitlines()
    lines.insert(3, json.dumps({"type": "result", "result": 7}))
    assert claude.parse("\n".join(lines), table()).report == "A\n\nC"
    reply = claude.parse(stream("A", None), table())
    assert (reply.report, reply.final_text) == ("A", None)



def test_a_line_separator_in_a_text_does_not_split_the_result():
    """Code review: the CLI writes U+2028, U+2029 and U+0085 as they are."""
    out = stream("A\u2028a", "B\x85\u2029b")
    for c in "\u2028\u2029\x85":
        out = out.replace(json.dumps(c)[1:-1], c)
    assert "\u2028" in out and "\\u2028" not in out
    reply = claude.parse(out.replace("\n", "\r\n"), table())
    assert (reply.report, reply.final_text) == ("A\u2028a\n\nB\x85\u2029b", "B\x85\u2029b")

def test_the_old_forms_give_a_report_of_1_text():
    result = (FIX / "claude-result.json").read_text(encoding="utf-8")
    text = json.loads(result)["result"]
    listed = json.dumps([{"type": "system"}, json.loads(result)])
    for out in (result, listed, "log line\n" + result):
        reply = claude.parse(out, table())
        assert (reply.final_text, reply.report) == (text, text)
    assert claude.parse("not json", table()).report == ""


def real_case() -> tuple[dict, dict]:
    """The 2 results of a build step: the report, then the reply to a
    watcher. modelUsage and total_cost_usd hold the whole run; usage and
    duration_ms the last turn."""
    first = {"result": f"I built the item.\n\n## Report\n\nBUILD: READY {SHA}", "num_turns": 40,
             "duration_ms": 900000, "usage": {"input_tokens": 9, "output_tokens": 9}}
    last = json.loads((FIX / "claude-result.json").read_text(encoding="utf-8"))
    last.update({"result": f"The report above stands.\n\nBUILD: READY {SHA}", "num_turns": 1,
                 "duration_ms": 2366, "result_index": 2, "origin": {"kind": "task-notification"}})
    return first, last


def counters_of(work, monkeypatch, out) -> dict:
    runner_fakes.output(monkeypatch, work, out)
    c = run_step(work / "work", BUILD, 60, "c", work / "run", cfg={"runners": {"c": table()}},
                 prices_file=PRICES)
    return {k: c[k] for k in ("status", "last_line", "reason", "exit_code", "cli_seconds", "usd", "input_tokens",
                              "output_tokens", "cache_read_tokens", "cache_write_tokens")}


def test_the_last_line_and_the_counters_come_from_the_last_result(work, monkeypatch):
    first, last = real_case()
    lines = stream(first["result"], last["result"]).splitlines()
    lines[2] = json.dumps({**json.loads(lines[2]), **first})
    lines[4] = json.dumps({**json.loads(lines[4]), **last})
    out = "\n".join(lines) + "\n"
    two = counters_of(work, monkeypatch, out)
    alone = counters_of(work, monkeypatch, json.dumps(last))
    assert two == alone
    assert (two["status"], two["last_line"], two["cli_seconds"]) == ("OK", f"BUILD: READY {SHA}", 2.366)
    assert two["input_tokens"] == 1000
    assert claude.parse(out, table()).report == first["result"] + "\n\n" + last["result"]


@pytest.mark.parametrize("last", ["The report above stands.", None])
def test_an_allowed_report_line_does_not_make_the_last_result_ready(work, monkeypatch, last):
    c = counters_of(work, monkeypatch, stream(f"Report\n\nBUILD: READY {SHA}", last))
    assert c["status"] == "INVALID"
    assert c["last_line"] == (last and "The report above stands.")


def test_the_run_folder_holds_the_report(work, monkeypatch):
    runner_fakes.output(monkeypatch, work, stream("Première\n\nECHO: OK", "B\n\nECHO: OK"))
    run(work)
    assert (work / "run" / "reply.txt").read_bytes() == "Première\n\nECHO: OK\n\nB\n\nECHO: OK".encode("utf-8")


@pytest.mark.parametrize("out", ["not json", stream(None)], ids=["not-json", "no-text"])
def test_no_result_text_writes_no_reply_file(work, monkeypatch, out):
    runner_fakes.output(monkeypatch, work, stream("A\n\nECHO: OK"))
    run(work)
    assert (work / "run" / "reply.txt").exists()
    runner_fakes.output(monkeypatch, work, out)
    run(work)  # the same run folder: the old reply.txt goes
    assert not (work / "run" / "reply.txt").exists()


# --- rework: the exit code, and no token in a stored file --------

def test_a_crash_after_a_ready_turn_is_failed(work, monkeypatch):
    """Review: result 1 ends with an allowed line, then a later
    turn crashes with no result of its own; the step is not OK on result 1."""
    out = stream(f"Report\n\nBUILD: READY {SHA}") + json.dumps({"type": "assistant", "message": {}}) + "\n"
    monkeypatch.setenv("FAKE_CLI_EXIT", "1")
    c = counters_of(work, monkeypatch, out)
    assert (c["status"], c["exit_code"], c["last_line"]) == ("FAILED", 1, f"BUILD: READY {SHA}")
    assert c["reason"] == "claude exited with code 1"


# a literal list: a test that read claude.SECRET_ENV would narrow with it
@pytest.mark.parametrize("name", ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"])
def test_no_token_of_the_environment_reaches_a_stored_file(work, monkeypatch, name):
    """Review: --verbose stores tool results, so a step that runs
    env would store the token of an unwrapped CLI. The value is made at run time."""
    value = "wbtest" + uuid.uuid4().hex
    monkeypatch.setenv(name, value)
    tool = {"type": "user", "message": {"content": [{"type": "tool_result", "content": f"{name}={value}\n"}]}}
    out = json.dumps(tool) + "\n" + stream(f"It is {value}.\n\nECHO: OK")
    runner_fakes.output(monkeypatch, work, out)
    c = run(work)
    assert c["status"] == "OK"
    stored = {p.name: p.read_bytes() for p in (work / "run").iterdir()}
    assert set(stored) == {"stdout.txt", "stderr.txt", "reply.txt", "counters.json"}
    leaked = [n for n, b in stored.items() if value.encode() in b]
    assert not leaked, leaked  # the names of the files only: a failure prints no token
    assert stored["stdout.txt"] == out.replace(value, "<redacted>").encode()
    assert stored["reply.txt"] == b"It is <redacted>.\n\nECHO: OK"


# --- rework 2: no token on the disk while the CLI runs -----------

def wait_for(check, seconds=30.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return False


def a_running_step(work, monkeypatch):
    """A fake CLI that prints a tool result with a token, then sleeps with a child.
    Returns the token, made at run time, and the stream with it redacted."""
    value = "wbtest" + uuid.uuid4().hex
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", value)
    tool = {"type": "user", "message": {"content": [{"type": "tool_result", "content": f"T={value}\n"}]}}
    out = json.dumps(tool) + "\n" + stream("Done\n\nECHO: OK")
    runner_fakes.output(monkeypatch, work, out)
    monkeypatch.setenv("FAKE_CLI_CHILD", str(work / "child.pid"))
    return value, out.replace(value, "<redacted>").encode()


def leaks(run_dir, value) -> list[str]:
    """The names of the files that hold the value: a failure prints no token."""
    return [p.name for p in run_dir.iterdir() if value.encode() in p.read_bytes()]


def test_no_token_is_on_the_disk_while_the_cli_runs(work, monkeypatch):
    """Second review: the step redacted only after the CLI
    ended, so the token sat in stdout.txt during the run."""
    value, clean = a_running_step(work, monkeypatch)
    done = []
    t = threading.Thread(target=lambda: done.append(run_step(
        work / "work", work / "echo.md", 8, "c", work / "run", cfg={"runners": {"c": table()}})))
    t.start()
    try:
        assert wait_for(lambda: (work / "child.pid").is_file()), "the fake CLI did not start"
        stdout = work / "run" / "stdout.txt"
        assert wait_for(lambda: stdout.read_bytes() == clean), "the stream did not reach stdout.txt"
        assert not done, "the step ended before the check"
        assert not leaks(work / "run", value)
    finally:
        t.join(60)
    assert done[0]["status"] == "TIMEOUT"
    assert not leaks(work / "run", value)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL and process groups are POSIX")
def test_no_token_is_on_the_disk_after_the_runner_is_killed(work, monkeypatch):
    """Second review: a runner killed before its redaction left
    the token in stdout.txt for good."""
    value, clean = a_running_step(work, monkeypatch)
    code = ("import json, sys; from runner.step import run_step; "
            "run_step(sys.argv[1], sys.argv[2], 60, 'c', sys.argv[3], cfg=json.loads(sys.argv[4]))")
    runner = subprocess.Popen([sys.executable, "-c", code, str(work / "work"), str(work / "echo.md"),
                               str(work / "run"), json.dumps({"runners": {"c": table()}})], cwd=ROOT)
    try:
        assert wait_for(lambda: (work / "child.pid").is_file()), "the fake CLI did not start"
        assert wait_for(lambda: (work / "run" / "stdout.txt").read_bytes() == clean)
    finally:
        runner.kill()
        runner.wait()
        if (work / "child.pid").is_file():  # the CLI is in its own group, with its child
            try:
                os.killpg(os.getpgid(int((work / "child.pid").read_text(encoding="utf-8"))), signal.SIGKILL)
            except ProcessLookupError:
                pass
    assert not leaks(work / "run", value)


def test_a_token_split_over_2_writes_reaches_neither_file(tmp_path):
    """proc.run with secrets: stdout and stderr, a token cut in 2 writes, and an
    end that starts a token but is none."""
    value = "wbtest" + uuid.uuid4().hex
    code = ("import os, sys, time\n"
            "v = os.environ['WB_TEST_VALUE'].encode()\n"
            "for f in (sys.stdout.buffer, sys.stderr.buffer):\n"
            "    f.write(b'a ' + v[:9]); f.flush(); time.sleep(0.3)\n"
            "    f.write(v[9:] + b' b ' + v + b' c ' + v[:12]); f.flush()\n")
    out, err = tmp_path / "out", tmp_path / "err"
    result = proc.run([sys.executable, "-c", code], tmp_path, b"", out, err, 30,
                      {**os.environ, "WB_TEST_VALUE": value}, secrets=[value.encode(), b""])
    assert result[:2] == (0, False)
    want = b"a <redacted> b <redacted> c " + value[:12].encode()
    assert (out.read_bytes() == want, err.read_bytes() == want) == (True, True)  # no token in a diff


def test_a_child_that_keeps_the_pipe_open_does_not_hold_the_step(tmp_path, monkeypatch):
    """The pipes of the redaction: a child of the CLI that outlives it gets proc.GRACE."""
    monkeypatch.setattr(proc, "GRACE", 0.5)
    code = ("import subprocess, sys\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "open(sys.argv[1], 'w').write(str(child.pid)); print('done', flush=True)\n")
    pid_file = tmp_path / "child.pid"
    started = time.monotonic()
    try:
        result = proc.run([sys.executable, "-c", code, str(pid_file)], tmp_path, b"", tmp_path / "out",
                          tmp_path / "err", 30, secrets=[b"wbtest" + uuid.uuid4().hex.encode()])
        assert time.monotonic() - started < 15, "the step waited for the child"
        assert result[:2] == (0, False)
        assert (tmp_path / "out").read_bytes().replace(b"\r\n", b"\n") == b"done\n"
    finally:
        if pid_file.is_file():
            pid = int(pid_file.read_text(encoding="utf-8"))
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            else:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


# --- a capture failure fails the step --------------------------------

def full_disk(monkeypatch, at, error=None):
    """proc.Redacted, but its 2nd write (at="write") or its close raises the
    error (a new one each time), by default ENOSPC."""
    error = error or (lambda: OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)))

    class Full(proc.Redacted):
        def write(self, data):
            self.writes = getattr(self, "writes", 0) + 1
            if at == "write" and self.writes == 2:
                raise error()
            super().write(data)

        def close(self):
            super().close()
            if at == "close":
                raise error()

    monkeypatch.setattr(proc, "Redacted", Full)


def kill_child(work):
    """The fake CLI's child, if a failed tree kill left it."""
    if (work / "child.pid").is_file():
        pid = int((work / "child.pid").read_text(encoding="utf-8"))
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("last", ["ECHO: OK", "Not done"], ids=["allowed", "not-allowed"])
def test_a_failed_capture_write_stops_the_cli_and_fails_the_step(work, monkeypatch, last):
    """The pump swallowed the error of a write: the CLI ran on, its output lost,
    and the step could give OK on an earlier allowed line."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "wbtest" + uuid.uuid4().hex)
    runner_fakes.output(monkeypatch, work, stream(f"Done\n\n{last}"))
    monkeypatch.setenv("FAKE_CLI_MORE", "more\n")
    monkeypatch.setenv("FAKE_CLI_CHILD", str(work / "child.pid"))  # then it sleeps, with a child
    full_disk(monkeypatch, "write")
    started = time.monotonic()
    try:
        c = run(work)
    finally:
        kill_child(work)
    assert time.monotonic() - started < 15, "the CLI ran on after the capture failure"
    assert c["status"] == "FAILED", c
    assert c["reason"] == "the output capture failed: " + os.strerror(errno.ENOSPC)


@pytest.mark.parametrize("timed_out", [False, True], ids=["exit", "time-limit"])
def test_a_failed_capture_close_is_failed_not_unavailable(work, monkeypatch, timed_out):
    """The close error went out of proc.run as if the CLI did not start."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "wbtest" + uuid.uuid4().hex)
    if timed_out:
        monkeypatch.setenv("FAKE_CLI_CHILD", str(work / "child.pid"))
    full_disk(monkeypatch, "close")
    try:
        c = run_step(work / "work", work / "echo.md", 2 if timed_out else 60, "c", work / "run",
                     cfg={"runners": {"c": table()}})
    finally:
        kill_child(work)
    assert (c["status"], c["reason"]) == ("FAILED", "the output capture failed: " + os.strerror(errno.ENOSPC))
    assert runner_fakes.logged(work)  # the CLI ran


@pytest.mark.parametrize("at", ["write", "close"])
def test_a_value_error_of_the_capture_fails_the_step(work, monkeypatch, at):
    """Review: AC 1 names a ValueError too. A pump that caught
    OSError only lost its thread at a ValueError: the CLI blocked on a full pipe
    until the time limit, and the step was TIMEOUT."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "wbtest" + uuid.uuid4().hex)
    runner_fakes.output(monkeypatch, work, stream("Done\n\nECHO: OK"))
    if at == "write":
        monkeypatch.setenv("FAKE_CLI_MORE", "more\n")
        monkeypatch.setenv("FAKE_CLI_CHILD", str(work / "child.pid"))  # then it sleeps, with a child
    full_disk(monkeypatch, at, lambda: ValueError("I/O operation on closed file"))
    started = time.monotonic()
    try:
        c = run(work)
    finally:
        kill_child(work)
    assert time.monotonic() - started < 15, "the CLI ran on after the capture failure"
    assert (c["status"], c["reason"]) == ("FAILED", "the output capture failed: I/O operation on closed file")


def test_a_failed_capture_kill_names_a_failed_tree_kill(work, monkeypatch):
    """The capture kill reports a failed tree kill as the timeout kill does."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "wbtest" + uuid.uuid4().hex)
    monkeypatch.setenv("FAKE_CLI_MORE", "more\n")
    monkeypatch.setenv("FAKE_CLI_CHILD", str(work / "child.pid"))
    full_disk(monkeypatch, "write")
    real = proc.kill_tree
    monkeypatch.setattr(proc, "kill_tree", lambda p, job=None: real(p, job) or "the group kill failed: test")
    try:
        c = run(work)
    finally:
        kill_child(work)
    assert c["status"] == "FAILED"
    assert c["reason"] == ("the output capture failed: " + os.strerror(errno.ENOSPC)
                           + "; the process tree kill failed (the group kill failed: test), so only the CLI was killed")


def test_the_pump_drops_the_rest_after_a_capture_failure():
    """1 fail call at the first failed write; the pump reads on to the end and writes no more."""
    class Pipe:
        chunks = [b"a", b"b", b"c", b"d"]

        def read1(self, n):
            return self.chunks.pop(0) if self.chunks else b""

    class Sink:
        def __init__(self):
            self.got = []

        def write(self, data):
            self.got.append(data)
            if data == b"b":
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    pipe, sink, failed = Pipe(), Sink(), []
    proc._pump(pipe, sink, failed.append)
    assert (sink.got, pipe.chunks, [e.errno for e in failed]) == ([b"a", b"b"], [], [errno.ENOSPC])


def test_a_write_after_a_failed_close_is_dropped(tmp_path):
    """A late grandchild writes after the close; that close raised."""
    class Broken:
        def __init__(self, f):
            self.f = f
            self.write, self.flush = f.write, f.flush

        def close(self):
            self.f.close()
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    sink = proc.Redacted(tmp_path / "out", [b"wbtest" + uuid.uuid4().hex.encode()])
    sink.write(b"early ")
    sink._file = Broken(sink._file)
    with pytest.raises(OSError):
        sink.close()
    sink.write(b"late")  # no error: dropped
    assert (tmp_path / "out").read_bytes() == b"early "


def test_a_failed_write_of_the_held_end_at_the_close_raises(tmp_path):
    """The close writes the held start of a secret; a full disk there is a capture failure too."""
    secret = b"wbtest" + uuid.uuid4().hex.encode()
    sink = proc.Redacted(tmp_path / "out", [secret])
    sink.write(b"early " + secret[:3])  # the end waits: it may start the secret

    class Full:
        def __init__(self, f):
            self.f, self.closed = f, False

        def write(self, data):
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

        def close(self):
            self.closed = True
            self.f.close()

    sink._file = full = Full(sink._file)
    with pytest.raises(OSError):
        sink.close()
    assert full.closed  # the file closes also after the failed write


def gone(pid: int) -> bool:
    """The process ends within 10 s; a zombie that no one reaps counts as ended."""
    stop = time.monotonic() + 10
    while time.monotonic() < stop:
        if sys.platform == "win32":  # os.kill(pid, 0) would end it there
            listed = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                                    capture_output=True, text=True).stdout
            if f'"{pid}"' not in listed:
                return True
            time.sleep(0.1)
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        stat = Path(f"/proc/{pid}/stat")
        with contextlib.suppress(OSError):
            if stat.read_text(encoding="ascii").rsplit(")", 1)[1].split()[0] == "Z":
                return True
        time.sleep(0.1)
    return False


def test_a_failed_capture_close_stops_a_child_that_outlives_the_cli(tmp_path, monkeypatch):
    """The close error was only recorded: a child of the CLI that held the pipe ran on.
    Second review: on Windows too, where taskkill /T finds no
    tree of a CLI that has exited."""
    monkeypatch.setattr(proc, "GRACE", 0.5)
    full_disk(monkeypatch, "close")
    code = ("import subprocess, sys\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "open(sys.argv[1], 'w').write(str(child.pid)); print('done', flush=True)\n")
    pid_file = tmp_path / "child.pid"
    try:
        result = proc.run([sys.executable, "-c", code, str(pid_file)], tmp_path, b"", tmp_path / "out",
                          tmp_path / "err", 30, secrets=[b"wbtest" + uuid.uuid4().hex.encode()])
        assert result[4] is not None and result[4].errno == errno.ENOSPC
        assert gone(int(pid_file.read_text(encoding="utf-8"))), "the child of the CLI runs on"
    finally:
        kill_child(tmp_path)


@pytest.mark.parametrize("timeout_s", [30, 1], ids=["exit", "time-limit"])
def test_a_capture_failure_kills_the_tree_once(tmp_path, monkeypatch, timeout_s):
    """Both closes fail: 1 kill, also after the kill at the time limit."""
    full_disk(monkeypatch, "close")
    calls, real = [], proc.kill_tree
    monkeypatch.setattr(proc, "kill_tree", lambda p, job=None: calls.append(p) or real(p, job))
    code = "import sys, time; print('done', flush=True); time.sleep(float(sys.argv[1]))"
    result = proc.run([sys.executable, "-c", code, "0" if timeout_s == 30 else "30"], tmp_path, b"",
                      tmp_path / "out", tmp_path / "err", timeout_s,
                      secrets=[b"wbtest" + uuid.uuid4().hex.encode()])
    assert (result[1], result[3], result[4].errno) == (timeout_s == 1, None, errno.ENOSPC)
    assert len(calls) == 1


def test_the_first_capture_failure_is_the_one_returned(tmp_path, monkeypatch):
    """Both closes fail, each with its own error: the first one goes to the step."""
    codes = iter([errno.ENOSPC, errno.EIO])
    full_disk(monkeypatch, "close", lambda: OSError(next(codes), "full"))
    result = proc.run([sys.executable, "-c", "print('done')"], tmp_path, b"", tmp_path / "out",
                      tmp_path / "err", 30, secrets=[b"wbtest" + uuid.uuid4().hex.encode()])
    assert result[4].errno == errno.ENOSPC


def test_kill_tree_runs_no_taskkill_for_a_cli_that_has_exited(tmp_path, monkeypatch):
    """taskkill /T of a pid that is gone fails (code 128): that is no failed tree kill."""
    monkeypatch.setattr(proc, "WINDOWS", True)
    monkeypatch.setattr(proc, "TASKKILL", tmp_path / "missing" / "taskkill.exe")
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert proc.kill_tree(p) is None


class Kernel32:
    """The job calls of proc._kernel32(), with the result that each one gives."""

    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def CreateJobObjectW(self, attributes, name):
        self.calls.append(("create",))
        return 7

    def AssignProcessToJobObject(self, job, handle):
        self.calls.append(("assign", job, handle))
        return self.ok

    def TerminateJobObject(self, job, code):
        self.calls.append(("terminate", job))
        return self.ok

    def CloseHandle(self, handle):
        self.calls.append(("close", handle))
        return 1


@pytest.mark.parametrize("ok", [True, False], ids=["ended", "job-kill-fails"])
def test_kill_tree_ends_the_job_of_a_cli_that_has_exited(tmp_path, monkeypatch, ok):
    """Second review: on Windows, a capture failure after the CLI
    had exited killed no child, since taskkill /T finds no tree of a pid that is
    gone. The job of the CLI still holds the child."""
    monkeypatch.setattr(proc, "WINDOWS", True)
    monkeypatch.setattr(proc, "TASKKILL", tmp_path / "missing" / "taskkill.exe")
    kernel32 = Kernel32(ok)
    monkeypatch.setattr(proc, "_kernel32", lambda: kernel32)
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert proc.kill_tree(p, 7) == (None if ok else "the job kill failed")
    assert kernel32.calls == [("terminate", 7)]


@pytest.mark.parametrize("ok", [True, False], ids=["assigned", "not-assigned"])
def test_the_cli_starts_in_a_job_on_windows(monkeypatch, ok):
    """The job that kill_tree ends; a job that can not take the CLI is closed, and taskkill /T is left."""
    monkeypatch.setattr(proc, "WINDOWS", True)
    kernel32 = Kernel32(ok)
    monkeypatch.setattr(proc, "_kernel32", lambda: kernel32)

    class Cli:
        _handle = 42

    assert proc._job(Cli()) == (7 if ok else None)
    assert kernel32.calls == [("create",), ("assign", 7, 42)] + ([] if ok else [("close", 7)])


def test_no_job_on_posix(monkeypatch):
    monkeypatch.setattr(proc, "WINDOWS", False)
    monkeypatch.setattr(proc, "_kernel32", lambda: pytest.fail("kernel32 on POSIX"))
    assert proc._job(object()) is None


# --- the credentials file of the CLI ------------------------------

def test_the_credentials_dir_is_a_temporary_folder(tmp_path_factory):
    """No test reads the credentials of the host (tests/conftest.py)."""
    folder = Path(os.environ["CLAUDE_CONFIG_DIR"]).resolve()
    assert folder.is_relative_to(tmp_path_factory.getbasetemp().resolve())
    assert (folder / ".credentials.json").is_file()


def no_token_variable(monkeypatch):
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def credentials_folder(work, monkeypatch, where) -> Path:
    """The folder that the CLI reads its .credentials.json from: CLAUDE_CONFIG_DIR, or the default."""
    if where == "config_dir":
        folder = work / "config"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(folder))
    else:
        if where == "empty_config_dir":  # empty is unset
            monkeypatch.setenv("CLAUDE_CONFIG_DIR", "")
        else:
            monkeypatch.delenv("CLAUDE_CONFIG_DIR")
        monkeypatch.setenv("HOME", str(work / "home"))
        monkeypatch.setenv("USERPROFILE", str(work / "home"))
        folder = work / "home" / ".claude"
    folder.mkdir(parents=True)
    return folder


@pytest.mark.parametrize("where", ["config_dir", "default_path", "empty_config_dir"])
def test_the_credentials_file_is_redacted(work, monkeypatch, where):
    """Review: a CLI that signs in through its credentials file
    has no token in the environment, so a tool result that prints the file was
    stored as it is. The values are made at run time."""
    no_token_variable(monkeypatch)
    token, refresh = "wbtest" + uuid.uuid4().hex, "wbtest" + uuid.uuid4().hex
    deep, short = "wbtest" + uuid.uuid4().hex[:14], "wbtest" + uuid.uuid4().hex[:13]  # 20 and 19 characters
    creds = {"claudeAiOauth": {"accessToken": token, "refreshToken": refresh, "expiresAt": 1,
                               "scopes": ["user:inference", [deep]], "note": short}}
    text = json.dumps(creds)
    (credentials_folder(work, monkeypatch, where) / ".credentials.json").write_text(text, encoding="utf-8")
    tool = {"type": "user", "message": {"content": [{"type": "tool_result", "content": text}]}}
    out = json.dumps(tool) + "\n" + stream(f"It is {token}.\n\nECHO: OK")
    runner_fakes.output(monkeypatch, work, out)
    c = run(work)
    assert c["status"] == "OK"
    for value in (token, refresh, deep):
        assert not leaks(work / "run", value)
    clean = out
    for value in (token, refresh, deep):
        clean = clean.replace(value, "<redacted>")
    assert short in clean
    assert (work / "run" / "stdout.txt").read_bytes() == clean.encode()  # no token in a diff: all are gone
    assert (work / "run" / "reply.txt").read_bytes() == b"It is <redacted>.\n\nECHO: OK"


def test_a_relative_config_dir_is_read_from_the_work_folder(work, monkeypatch):
    """Second review: a relative CLAUDE_CONFIG_DIR was read from
    the runner's folder, but the CLI reads it from the work folder."""
    no_token_variable(monkeypatch)
    monkeypatch.chdir(work)  # the runner's folder; the CLI starts in work/work
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "config")
    (work / "work" / "config").mkdir()
    token = "wbtest" + uuid.uuid4().hex
    (work / "work" / "config" / ".credentials.json").write_text(json.dumps({"accessToken": token}),
                                                                 encoding="utf-8")
    runner_fakes.output(monkeypatch, work, stream(f"It is {token}.\n\nECHO: OK"))
    c = run(work)
    assert (c["status"], c["reason"]) == ("OK", "ECHO: OK")
    assert not leaks(work / "run", token)


# a value of a secret's length that the CLI may print in another form (JSON escapes it)
NOT_PLAIN = {"e-acute": "\u00e9", "quote": '"', "backslash": "\\", "control": "\t"}


@pytest.mark.parametrize("case, reason", [
    ("no-file", "no credential to redact"),
    ("no-long-string", "no credential to redact"),
    ("bad-json", "the credentials file of the CLI can not be read"),
    ("bad-utf-8", "the credentials file of the CLI can not be read"),
    ("too-deep", "the credentials file of the CLI can not be read"),
    ("a-folder", "the credentials file of the CLI can not be read"),
    ("e-acute", "the credentials file of the CLI can not be read"),
    ("quote", "the credentials file of the CLI can not be read"),
    ("backslash", "the credentials file of the CLI can not be read"),
    ("control", "the credentials file of the CLI can not be read"),
])
def test_no_credential_is_unavailable_before_the_start(work, monkeypatch, case, reason):
    """An unwrapped claude step with no secret to redact does not start."""
    no_token_variable(monkeypatch)
    folder = credentials_folder(work, monkeypatch, "config_dir")
    secret = "wbtest" + uuid.uuid4().hex
    file = folder / ".credentials.json"
    if case == "no-long-string":
        file.write_text(json.dumps({"scopes": ["user:inference"], "expiresAt": 1}), encoding="utf-8")
    elif case == "bad-json":
        file.write_text('{"accessToken": "' + secret, encoding="utf-8")
    elif case == "bad-utf-8":
        file.write_bytes(b'{"accessToken": "' + secret.encode() + b'\xff"}')
    elif case == "too-deep":
        file.write_text("[" * 100000 + json.dumps(secret) + "]" * 100000, encoding="utf-8")
    elif case == "a-folder":
        file.mkdir()
    elif case in NOT_PLAIN:
        file.write_text(json.dumps({"accessToken": secret + NOT_PLAIN[case]}), encoding="utf-8")
    c = run(work)
    assert (c["status"], c["reason"]) == ("UNAVAILABLE", reason)
    assert not (work / "cli-log.json").exists(), "the CLI started"
    assert {p.name: p.read_bytes() for p in (work / "run").iterdir() if p.suffix == ".txt"} == \
        {"stdout.txt": b"", "stderr.txt": b""}
    written = (work / "run" / "counters.json").read_text(encoding="utf-8")
    assert secret not in written and str(folder.name) not in written  # no file name, no value


def test_a_short_value_that_is_not_plain_starts_the_step(work, monkeypatch):
    """This binds the secrets only: a value under SECRET_MIN is no secret, whatever its characters."""
    no_token_variable(monkeypatch)
    text = json.dumps({"accessToken": "wbtest" + uuid.uuid4().hex, "name": 'Ren\u00e9e "R" \\'}, ensure_ascii=False)
    (credentials_folder(work, monkeypatch, "config_dir") / ".credentials.json").write_text(text, encoding="utf-8")
    assert run(work)["status"] == "OK"


def test_a_variable_alone_starts_the_step(work, monkeypatch):
    """No credentials file, but a token in the environment: the step starts."""
    no_token_variable(monkeypatch)
    credentials_folder(work, monkeypatch, "config_dir")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "wbtest" + uuid.uuid4().hex)
    assert run(work)["status"] == "OK"


def test_the_file_is_read_also_with_a_token_variable(work, monkeypatch):
    """Review: the file values join the env secrets. A
    step that skipped the file when a variable was set stored the file's other
    values as they are. The values are made at run time."""
    no_token_variable(monkeypatch)
    env_token = "wbtest" + uuid.uuid4().hex
    token, refresh = "wbtest" + uuid.uuid4().hex, "wbtest" + uuid.uuid4().hex
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", env_token)
    text = json.dumps({"claudeAiOauth": {"accessToken": token, "refreshToken": refresh}})
    (credentials_folder(work, monkeypatch, "config_dir") / ".credentials.json").write_text(text, encoding="utf-8")
    tool = {"type": "user", "message": {"content": [{"type": "tool_result", "content": f"T={env_token}\n{text}"}]}}
    out = json.dumps(tool) + "\n" + stream(f"It is {env_token} {token} {refresh}.\n\nECHO: OK")
    runner_fakes.output(monkeypatch, work, out)
    c = run(work)
    assert c["status"] == "OK"
    for value in (env_token, token, refresh):
        assert not leaks(work / "run", value)
    clean = out
    for value in (env_token, token, refresh):
        clean = clean.replace(value, "<redacted>")
    assert (work / "run" / "stdout.txt").read_bytes() == clean.encode()  # no token in a diff: all are gone
    assert (work / "run" / "reply.txt").read_bytes() == b"It is <redacted> <redacted> <redacted>.\n\nECHO: OK"


@pytest.mark.parametrize("case", ["bad-json", "a-folder", *NOT_PLAIN])
def test_a_bad_file_refuses_the_step_also_with_a_token_variable(work, monkeypatch, case):
    """Review: a file the step can not read or parse
    refuses the step, also when a token variable is set."""
    no_token_variable(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "wbtest" + uuid.uuid4().hex)
    file = credentials_folder(work, monkeypatch, "config_dir") / ".credentials.json"
    if case == "bad-json":
        file.write_text('{"accessToken": "' + "wbtest" + uuid.uuid4().hex, encoding="utf-8")
    elif case == "a-folder":
        file.mkdir()
    else:
        file.write_text(json.dumps({"accessToken": "wbtest" + uuid.uuid4().hex + NOT_PLAIN[case]}),
                        encoding="utf-8")
    c = run(work)
    assert (c["status"], c["reason"]) == ("UNAVAILABLE", "the credentials file of the CLI can not be read")
    assert not (work / "cli-log.json").exists(), "the CLI started"


def test_a_wrapped_runner_with_no_credential_starts(work, monkeypatch):
    """The wrap has its own home and redacts: the step reads no credentials file for it."""
    no_token_variable(monkeypatch)
    (credentials_folder(work, monkeypatch, "config_dir") / ".credentials.json").write_text("{", encoding="utf-8")
    c = run(work, table(wrap=[runner_fakes.install_wrap(monkeypatch, work)]))
    assert c["status"] == "OK", c
    assert runner_fakes.wrap_logged(work) and runner_fakes.logged(work)


def test_a_missing_cli_with_no_credential_is_not_on_path(work, monkeypatch):
    no_token_variable(monkeypatch)
    credentials_folder(work, monkeypatch, "config_dir")
    monkeypatch.setenv("PATH", str(work / "no-bin"))
    c = run(work)
    assert c["status"] == "UNAVAILABLE"
    assert c["reason"].startswith("claude is not on PATH"), c["reason"]
