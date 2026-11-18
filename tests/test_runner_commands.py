"""A run with no command is not a result.

The codex adapter counts the command items of the run (`commands` in the
counters). With `require_commands = true` the step is UNAVAILABLE when no
command item ran, when no command ended with exit code 0, or when an error item
is not a stream retry, whatever the last line says.

Synthetic fixtures (tests/fixtures/runner/): codex-no-commands.jsonl (no
command, a valid last line), codex-error-item.jsonl (1 command, 1 stream retry
and 1 other error item) and codex-events.jsonl (1 command).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

import pytest

import runner_fakes
from conftest import ROOT
from runner import UsageError
from runner.adapters import codex
from runner.step import run_step

FIX = ROOT / "tests" / "fixtures" / "runner"
ECHO = "The last line of your reply is exactly\n\n```\nECHO: OK\n```\n"
NO_COMMAND = "no command ran"
NO_SUCCESS = "no command ran (no command ended with exit code 0)"
ERROR_ITEM = "no command ran (an error item that is not a stream retry)"


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def without(text: str, *needles: str) -> str:
    """The JSONL text without the lines that hold any of the needles."""
    lines = text.splitlines()
    kept = [line for line in lines if not any(n in line for n in needles)]
    assert len(kept) < len(lines)
    return "\n".join(kept) + "\n"


def table(**kw) -> dict:
    t = {"adapter": "codex", "model": "test-model-a", "sandbox": "workspace-write"}
    t.update(kw)
    return t


@pytest.fixture
def work(tmp_path, monkeypatch):
    (tmp_path / "work").mkdir()
    (tmp_path / "echo.md").write_text(ECHO, encoding="utf-8")
    runner_fakes.install(monkeypatch, tmp_path, "codex")
    return tmp_path


def run(work, monkeypatch, events: str, **kw):
    runner_fakes.output(monkeypatch, work, events)
    return run_step(work / "work", work / "echo.md", 60, "x", work / "run",
                    cfg={"runners": {"x": table(**kw)}})


# --- the count -------------------------------------------------------------------

def test_the_command_items_are_counted_by_id():
    assert codex.parse(fixture("codex-events.jsonl"), table()).commands == 1
    assert codex.parse(fixture("codex-no-commands.jsonl"), table()).commands == 0
    # item.started and item.completed of item_1 are 1 command
    assert codex.parse(fixture("codex-error-item.jsonl"), table()).commands == 1
    second = fixture("codex-events.jsonl").replace('"item_1"', '"item_9"')
    assert codex.parse(fixture("codex-events.jsonl") + second, table()).commands == 2


def test_the_counters_hold_the_commands(work, monkeypatch):
    c = run(work, monkeypatch, fixture("codex-events.jsonl"))
    written = json.loads((work / "run" / "counters.json").read_text(encoding="utf-8"))
    assert c["commands"] == written["commands"] == 1
    assert c["status"] == "OK"


def test_without_the_key_a_run_with_no_command_is_ok(work, monkeypatch):
    c = run(work, monkeypatch, fixture("codex-no-commands.jsonl"))
    assert (c["status"], c["commands"]) == ("OK", 0)
    c = run(work, monkeypatch, fixture("codex-no-commands.jsonl"), require_commands=False)
    assert c["status"] == "OK"


# --- require_commands = true ---------------------------------------------------------

def test_1_command_with_exit_code_0_is_ok(work, monkeypatch):
    c = run(work, monkeypatch, fixture("codex-events.jsonl"), require_commands=True)
    assert (c["status"], c["last_line"], c["commands"]) == ("OK", "ECHO: OK", 1)


def test_no_command_with_a_valid_last_line_is_unavailable(work, monkeypatch):
    c = run(work, monkeypatch, fixture("codex-no-commands.jsonl"), require_commands=True)
    assert (c["status"], c["reason"], c["commands"]) == ("UNAVAILABLE", NO_COMMAND, 0)


def test_an_error_item_is_unavailable(work, monkeypatch):
    c = run(work, monkeypatch, fixture("codex-error-item.jsonl"), require_commands=True)
    assert (c["status"], c["reason"], c["commands"]) == ("UNAVAILABLE", ERROR_ITEM, 1)


@pytest.mark.parametrize("message", [
    "Reconnecting... 2/5 (stream disconnected before completion: an example)",
    "stream disconnected before completion: an example",
    "stream disconnected - retrying sampling request (1/5 in 200ms)...",
])
def test_a_stream_retry_is_not_an_error(work, monkeypatch, message):
    events = without(fixture("codex-error-item.jsonl"), '"item_3"').replace(
        "Reconnecting... 1/5 (stream disconnected before completion: an example)", message)
    c = run(work, monkeypatch, events, require_commands=True)
    assert (c["status"], c["last_line"]) == ("OK", "ECHO: OK"), c


@pytest.mark.parametrize("message", [
    "failed to reconnect: stream disconnected before completion: an example",
    " Reconnecting... 1/5",
    "Reconnecting...",
    None,
])
def test_an_error_item_that_is_not_a_retry_is_unavailable(work, monkeypatch, message):
    events = without(fixture("codex-error-item.jsonl"), '"item_3"').replace(
        '"Reconnecting... 1/5 (stream disconnected before completion: an example)"', json.dumps(message))
    c = run(work, monkeypatch, events, require_commands=True)
    assert (c["status"], c["reason"]) == ("UNAVAILABLE", ERROR_ITEM)


@pytest.mark.parametrize("code", ["1", "null", "false", '"0"'])
def test_no_command_with_exit_code_0_is_unavailable(work, monkeypatch, code):
    events = fixture("codex-events.jsonl").replace('"exit_code":0', f'"exit_code":{code}')
    c = run(work, monkeypatch, events, require_commands=True)
    assert (c["status"], c["reason"], c["commands"]) == ("UNAVAILABLE", NO_SUCCESS, 1)


def test_a_command_that_only_started_did_not_succeed(work, monkeypatch):
    events = without(fixture("codex-error-item.jsonl"), '"item_3"', '"status":"completed"')
    c = run(work, monkeypatch, events, require_commands=True)
    assert (c["status"], c["reason"], c["commands"]) == ("UNAVAILABLE", NO_SUCCESS, 1)


def test_1_failed_and_1_good_command_is_ok(work, monkeypatch):
    good = fixture("codex-events.jsonl")
    failed = [line.replace('"item_1"', '"item_8"').replace('"exit_code":0', '"exit_code":2')
              for line in good.splitlines() if "command_execution" in line]
    c = run(work, monkeypatch, failed[0] + "\n" + good, require_commands=True)
    assert (c["status"], c["commands"]) == ("OK", 2)


def test_a_later_failed_end_of_the_same_item_is_not_a_success():
    lines = fixture("codex-events.jsonl").splitlines()
    command = next(line for line in lines if "command_execution" in line)
    events = "\n".join([*lines, command.replace('"exit_code":0', '"exit_code":1')])
    assert codex.parse(events, table(require_commands=True)).unavailable == NO_SUCCESS


def test_the_last_line_does_not_save_a_run_with_no_command(work, monkeypatch):
    monkeypatch.setenv("FAKE_CLI_EXIT", "1")
    c = run(work, monkeypatch, fixture("codex-no-commands.jsonl"), require_commands=True)
    assert (c["status"], c["reason"], c["exit_code"]) == ("UNAVAILABLE", NO_COMMAND, 1)
    monkeypatch.delenv("FAKE_CLI_EXIT")
    events = fixture("codex-no-commands.jsonl").replace("ECHO: OK", "Done.")
    c = run(work, monkeypatch, events, require_commands=True)
    assert (c["status"], c["reason"]) == ("UNAVAILABLE", NO_COMMAND)


@pytest.mark.parametrize("value", ["true", 1, ["true"]])
def test_require_commands_takes_a_boolean(work, monkeypatch, value):
    with pytest.raises(UsageError, match="require_commands must be true or false"):
        run(work, monkeypatch, fixture("codex-events.jsonl"), require_commands=value)
    assert not (work / "run").exists()
    assert not (work / "cli-log.json").exists()


def test_a_timeout_stays_a_timeout(work, monkeypatch):
    """The time limit is the stronger fact: a run stopped early has no command yet."""
    pid_file = work / "child.pid"
    monkeypatch.setenv("FAKE_CLI_CHILD", str(pid_file))
    runner_fakes.output(monkeypatch, work, fixture("codex-no-commands.jsonl"))
    try:
        c = run_step(work / "work", work / "echo.md", 2, "x", work / "run",
                     cfg={"runners": {"x": table(require_commands=True)}})
        assert (c["status"], c["commands"]) == ("TIMEOUT", 0), c
    finally:  # the step kills the tree; this only keeps a failed test from leaving a child
        if pid_file.is_file():
            child = int(pid_file.read_text(encoding="utf-8"))
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(child)], capture_output=True)
            else:
                try:
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass
