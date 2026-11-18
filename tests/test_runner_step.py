"""1 operation, 5 statuses, driven by the fake adapter.

The fake adapter runs a reply script as a real subprocess, so the exit code,
the time limit and the stdout file take the real path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import ROOT
from runner import UsageError
from runner.step import STATUSES, main, run_step

PROMPT = ROOT / "prompts" / "spec-check.md"
GOOD = "Notes.\n\nSPEC-CHECK 7: GO"


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    return tmp_path


def fake_cfg(tmp_path, **reply) -> dict:
    script = tmp_path / "reply.json"
    script.write_text(json.dumps(reply), encoding="utf-8")
    return {"runners": {"fake": {"adapter": "fake", "model": "fake-model", "script": str(script)}}}


def step(tmp_path, cfg, timeout=30.0, prompt=PROMPT, runner="fake"):
    return run_step(tmp_path / "work", prompt, timeout, runner, tmp_path / "run", cfg=cfg)


def test_statuses():
    assert STATUSES == ("OK", "INVALID", "TIMEOUT", "UNAVAILABLE", "FAILED")


def test_ok(work):
    c = step(work, fake_cfg(work, final_text=GOOD))
    assert (c["status"], c["reason"], c["last_line"], c["exit_code"]) == (
        "OK", "SPEC-CHECK 7: GO", "SPEC-CHECK 7: GO", 0)


def test_invalid_last_line(work):
    c = step(work, fake_cfg(work, final_text="SPEC-CHECK 7: GO\nDone."))
    assert c["status"] == "INVALID"
    assert c["reason"] == "the last line is not an allowed line: Done."


@pytest.mark.parametrize("reply", [{"final_text": ""}, {"final_text": None}, {"stdout": "not json"}])
def test_invalid_no_final_text(work, reply):
    c = step(work, fake_cfg(work, **reply))
    assert c["status"] == "INVALID"
    assert c["reason"].startswith("no final text")
    assert c["last_line"] is None


def test_timeout(work):
    c = step(work, fake_cfg(work, final_text=GOOD, delay=30), timeout=1.0)
    assert c["status"] == "TIMEOUT"
    assert c["exit_code"] is None
    assert 1.0 <= c["seconds"] < 20
    assert c["reason"].startswith("stopped at the time limit after ")


def test_unavailable(work):
    cfg = fake_cfg(work, final_text=GOOD)
    cfg["runners"]["fake"]["command"] = "no-such-cli-for-the-workbench"
    c = step(work, cfg)
    assert c["status"] == "UNAVAILABLE"
    assert c["reason"] == f"no-such-cli-for-the-workbench is not on PATH ({os.environ.get('PATH', os.defpath)})"
    assert c["exit_code"] is None
    assert (work / "run" / "counters.json").is_file()


def test_unavailable_names_the_searched_path(work, monkeypatch):
    """The reason keeps its first words and names the PATH it searched."""
    cfg = fake_cfg(work, final_text=GOOD)
    cfg["runners"]["fake"]["command"] = "no-such-cli-for-the-workbench"
    searched = str(work / "empty")
    monkeypatch.setenv("PATH", searched)
    c = step(work, cfg)
    assert c["reason"].startswith("no-such-cli-for-the-workbench is not on PATH")
    assert c["reason"] == f"no-such-cli-for-the-workbench is not on PATH ({searched})"


def test_failed(work):
    c = step(work, fake_cfg(work, final_text="it broke", exit_code=3))
    assert c["status"] == "FAILED"
    assert c["exit_code"] == 3
    assert "exited with code 3" in c["reason"]


def test_failed_with_no_text(work):
    c = step(work, fake_cfg(work, final_text=None, exit_code=1))
    assert (c["status"], c["exit_code"]) == ("FAILED", 1)


def test_nonzero_exit_with_an_allowed_line_is_ok(work):
    """AC 8: FAILED needs a non-zero exit with no allowed last line."""
    c = step(work, fake_cfg(work, final_text=GOOD, exit_code=1))
    assert (c["status"], c["exit_code"]) == ("OK", 1)


def test_the_fake_reads_its_script_from_the_config(work):
    """AC 7: the reply script that config names gives the text and the counters."""
    c = step(work, fake_cfg(work, final_text=GOOD,
                            counters={"input": 11, "output": 7, "cache_read": 3, "cache_write": 2}))
    assert (c["input_tokens"], c["output_tokens"], c["cache_read_tokens"], c["cache_write_tokens"]) == (
        11, 7, 3, 2)


def test_example_fake_runner_replays(work):
    """The fake runner of the example config, with a prompt that wants ECHO: OK."""
    prompt = work / "echo.md"
    prompt.write_text("The last line of your reply is exactly\n\n```\nECHO: OK\n```\n", encoding="utf-8")
    c = run_step(work / "work", prompt, 30, "fake", work / "run", cfg=ROOT / "config" / "workbench.example.toml")
    assert c["status"] == "OK"


def test_a_fenced_allowed_line_is_ok(work):
    """The line inside a final fence is the last line."""
    fence = "`" * 3
    c = step(work, fake_cfg(work, final_text=f"Notes.\n\n{fence}\nSPEC-CHECK 7: GO\n{fence}\n"))
    assert (c["status"], c["reason"], c["last_line"]) == ("OK", "SPEC-CHECK 7: GO", "SPEC-CHECK 7: GO")


def test_a_fenced_wrong_line_is_invalid_and_named(work):
    fence = "`" * 3
    c = step(work, fake_cfg(work, final_text=f"Notes.\n\n{fence}\nSPEC-CHECK 7: go\n{fence}"))
    assert (c["status"], c["last_line"]) == ("INVALID", "SPEC-CHECK 7: go")
    assert c["reason"] == "the last line is not an allowed line: SPEC-CHECK 7: go"

# --- the command line -------------------------------------------------------

def args(work, timeout="30", prompt=PROMPT, runner="fake"):
    return ["--folder", str(work / "work"), "--prompt", str(prompt), "--timeout", timeout,
            "--runner", runner, "--out", str(work / "run")]


@pytest.mark.parametrize("reply,status", [
    ({"final_text": GOOD}, "OK"),
    ({"final_text": "nope"}, "INVALID"),
    ({"final_text": GOOD, "delay": 30}, "TIMEOUT"),
    ({"final_text": "x", "exit_code": 4}, "FAILED"),
])
def test_cli_status_line(work, capsys, reply, status):
    timeout = "1" if reply.get("delay") else "30"
    assert main(args(work, timeout), cfg=fake_cfg(work, **reply)) == 0
    last = capsys.readouterr().out.rstrip("\n").split("\n")[-1]
    assert last.startswith(f"STEP: {status} fake: ")
    if status == "OK":
        assert last == "STEP: OK fake: SPEC-CHECK 7: GO"


def test_cli_unavailable(work, capsys):
    cfg = fake_cfg(work, final_text=GOOD)
    cfg["runners"]["fake"]["command"] = "no-such-cli-for-the-workbench"
    assert main(args(work), cfg=cfg) == 0
    last = capsys.readouterr().out.rstrip("\n").split("\n")[-1]
    assert last == f"STEP: UNAVAILABLE fake: no-such-cli-for-the-workbench is not on PATH ({os.environ.get('PATH', os.defpath)})"


def test_cli_timeout_takes_a_float(work, capsys):
    assert main(args(work, "0.5"), cfg=fake_cfg(work, final_text=GOOD, delay=30)) == 0
    assert capsys.readouterr().out.rstrip("\n").split("\n")[-1].startswith("STEP: TIMEOUT fake: ")


def test_reason_is_kept_on_1_line(work, capsys):
    main(args(work), cfg=fake_cfg(work, final_text="a\nlast line\twith  tabs"))
    last = capsys.readouterr().out.rstrip("\n").split("\n")[-1]
    assert last == "STEP: INVALID fake: the last line is not an allowed line: last line with tabs"


@pytest.mark.parametrize("bad", [
    {"runner": "nope"},
    {"timeout": "0"},
    {"timeout": "-1"},
    {"timeout": "soon"},
    {"prompt": "README.md"},
    {"prompt": "no-such-prompt.md"},
])
def test_cli_usage_errors(work, capsys, bad):
    a = args(work, timeout=bad.get("timeout", "30"),
             prompt=ROOT / bad.get("prompt", "prompts/spec-check.md"), runner=bad.get("runner", "fake"))
    code = exit_code(a, cfg=fake_cfg(work, final_text=GOOD))
    out, err = capsys.readouterr()
    assert code == 2
    assert "STEP:" not in out
    assert err
    assert not (work / "run").exists()


def exit_code(argv, cfg) -> int:
    """main's return value, or the code of argparse's SystemExit."""
    try:
        return main(argv, cfg=cfg)
    except SystemExit as e:
        return e.code


def test_missing_work_folder_is_a_usage_error(work):
    with pytest.raises(UsageError, match="work folder"):
        run_step(work / "nowhere", PROMPT, 30, "fake", work / "run", cfg=fake_cfg(work, final_text=GOOD))
    assert not (work / "run").exists()


def test_missing_reply_script_is_a_usage_error(work):
    cfg = {"runners": {"fake": {"adapter": "fake", "model": "m", "script": str(work / "none.json")}}}
    with pytest.raises(UsageError, match="reply script"):
        step(work, cfg)


def test_unknown_key_is_a_usage_error(work):
    cfg = fake_cfg(work, final_text=GOOD)
    cfg["runners"]["fake"]["extra_args"] = ["--anything"]
    with pytest.raises(UsageError, match="unknown key extra_args"):
        step(work, cfg)


def test_module_entry_point_usage_error_exits_2():
    r = subprocess.run([sys.executable, "-m", "runner.step", "--folder", "."],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 2
    assert "STEP:" not in r.stdout


def test_module_entry_point_runs(tmp_path):
    """python -m runner.step with the example config's fake runner (skipped when
    a local config exists: a test never reads the owner's local file)."""
    if (ROOT / "config" / "workbench.toml").exists():
        pytest.skip("a local config/workbench.toml exists")
    (tmp_path / "work").mkdir()
    prompt = tmp_path / "echo.md"
    prompt.write_text("The last line of your reply is exactly\n\n```\nECHO: OK\n```\n", encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "runner.step", "--folder", str(tmp_path / "work"),
                        "--prompt", str(prompt), "--timeout", "30", "--runner", "fake",
                        "--out", str(tmp_path / "run")], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.rstrip("\n").split("\n")[-1] == "STEP: OK fake: ECHO: OK"
