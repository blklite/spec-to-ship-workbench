"""An optional `wrap` command for each runner.

The step runs `wrap` + the argv of the adapter. A fake wrap (a shim on
tests/fixtures/runner/fake_wrap.py) records its argv and then runs the command
after its own arguments, so the fake adapter and a fake codex still reply.
"""

from __future__ import annotations

import json
import re
import sys

import pytest

import runner_fakes
from conftest import ROOT
from runner import UsageError
from runner.adapters import fake
from runner.step import main, run_step

FIX = ROOT / "tests" / "fixtures" / "runner"
ECHO = "The last line of your reply is exactly\n\n```\nECHO: OK\n```\n"


@pytest.fixture
def work(tmp_path, monkeypatch):
    (tmp_path / "work").mkdir()
    (tmp_path / "echo.md").write_text(ECHO, encoding="utf-8")
    (tmp_path / "reply.json").write_text(json.dumps({"final_text": "Done.\n\nECHO: OK"}), encoding="utf-8")
    return tmp_path


def fake_table(work, **kw) -> dict:
    t = {"adapter": "fake", "model": "fake-model", "script": str(work / "reply.json")}
    t.update(kw)
    return t


def run(work, t):
    return run_step(work / "work", work / "echo.md", 60, "x", work / "run", cfg={"runners": {"x": t}})


def test_the_step_runs_the_wrap_then_the_adapter_argv(work, monkeypatch):
    wrap = runner_fakes.install_wrap(monkeypatch, work)
    t = fake_table(work, wrap=[wrap])
    c = run(work, t)
    assert (c["status"], c["last_line"]) == ("OK", "ECHO: OK"), c
    log = runner_fakes.wrap_logged(work)
    assert log["argv"] == fake.argv(t, sys.executable, work / "work")
    assert log["cwd"] == str((work / "work").resolve())


def test_the_other_wrap_elements_come_before_the_adapter_argv(work, monkeypatch):
    wrap = runner_fakes.install_wrap(monkeypatch, work)
    monkeypatch.setenv("FAKE_WRAP_SKIP", "2")
    t = fake_table(work, wrap=[wrap, "bind-rw", "key:value.1"])
    c = run(work, t)
    assert c["status"] == "OK", c
    assert runner_fakes.wrap_logged(work)["argv"] == ["bind-rw", "key:value.1",
                                                      *fake.argv(t, sys.executable, work / "work")]


def test_the_counters_record_the_wrap(work, monkeypatch):
    wrap = runner_fakes.install_wrap(monkeypatch, work)
    c = run(work, fake_table(work, wrap=[wrap]))
    written = json.loads((work / "run" / "counters.json").read_text(encoding="utf-8"))
    assert c["wrap"] == written["wrap"] == [wrap]


def test_no_wrap_runs_the_adapter_argv_alone(work, monkeypatch):
    runner_fakes.install_wrap(monkeypatch, work)
    c = run(work, fake_table(work))
    assert c["status"] == "OK"
    assert not (work / "wrap-log.json").exists()
    assert "wrap" not in c


def test_the_codex_adapter_runs_inside_the_wrap(work, monkeypatch):
    runner_fakes.install(monkeypatch, work, "codex")
    runner_fakes.output(monkeypatch, work, (FIX / "codex-events.jsonl").read_text(encoding="utf-8"))
    wrap = runner_fakes.install_wrap(monkeypatch, work)
    c = run(work, {"adapter": "codex", "model": "test-model-a", "sandbox": "workspace-write", "wrap": [wrap]})
    assert (c["status"], c["last_line"]) == ("OK", "ECHO: OK"), c
    folder = str((work / "work").resolve())
    wrapped = runner_fakes.wrap_logged(work)["argv"]
    assert wrapped[1:] == ["exec", "--json", "-C", folder, "--ephemeral", "--ignore-user-config",
                           "--ignore-rules", "-s", "workspace-write", "-m", "test-model-a", "-"]
    assert wrapped[0].lower().startswith(str(work / "bin" / "codex").lower())
    assert runner_fakes.logged(work)["argv"] == wrapped[1:]
    assert runner_fakes.logged(work)["stdin"].replace("\r\n", "\n") == ECHO


def test_a_wrap_that_does_not_start_is_unavailable_and_named(work):
    missing = (work / "no-such-wrap").as_posix()
    c = run(work, fake_table(work, wrap=[missing]))
    assert c["status"] == "UNAVAILABLE"
    assert c["reason"].startswith("no-such-wrap does not start: "), c["reason"]


# --- refused values: each is a usage error, and nothing runs --------------------

DRIVE = "C:/tools/wrap"


@pytest.mark.parametrize("value,match", [
    ("/usr/bin/wrap", "wrap must be a list of strings"),
    ({"a": "/usr/bin/wrap"}, "wrap must be a list of strings"),
    (True, "wrap must be a list of strings"),
    ([], "wrap must not be empty"),
    ([1], "wrap must be a list of strings"),
    (["/usr/bin/wrap", 2], "wrap must be a list of strings"),
    (["usr/bin/wrap"], "the first element of wrap must be an absolute path"),
    (["./wrap"], "the first element of wrap must be an absolute path"),
    (["/"], "the first element of wrap must be an absolute path"),
    (["/usr/bin/my wrap"], "the first element of wrap must be an absolute path"),
    (["/usr/bin/wrap;id"], "the first element of wrap must be an absolute path"),
    (["/usr/bin/$(id)"], "the first element of wrap must be an absolute path"),
    (["C:\\tools\\wrap"], "the first element of wrap must be an absolute path"),
    (["/usr/bin/wrap", "-c"], "wrap must match"),
    (["/usr/bin/wrap", "--"], "wrap must match"),
    (["/usr/bin/wrap", "a b"], "wrap must match"),
    (["/usr/bin/wrap", "x;id"], "wrap must match"),
    (["/usr/bin/wrap", "/abs/arg"], "wrap must match"),
    (["/usr/bin/wrap", ""], "wrap must match"),
    (["/usr/bin/wrap", "dangerously-x"], "asks for a dangerous mode"),
    (["/opt/dangerously/wrap"], "asks for a dangerous mode"),
])
def test_a_bad_wrap_is_a_usage_error(work, monkeypatch, value, match):
    runner_fakes.install_wrap(monkeypatch, work)
    with pytest.raises(UsageError, match=match):
        run(work, fake_table(work, wrap=value))
    assert not (work / "run").exists()
    assert not (work / "wrap-log.json").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="a drive letter is an absolute path on Windows")
def test_a_drive_letter_is_refused_off_windows(work):
    with pytest.raises(UsageError, match="the first element of wrap must be an absolute path"):
        run(work, fake_table(work, wrap=[DRIVE]))


@pytest.mark.skipif(sys.platform != "win32", reason="a drive letter is an absolute path on Windows only")
def test_a_drive_letter_is_an_absolute_path_on_windows(work):
    c = run(work, fake_table(work, wrap=[DRIVE]))  # it passes the check; the program does not exist
    assert (c["status"], c["reason"].split(":")[0]) == ("UNAVAILABLE", "wrap does not start")


def test_a_bad_wrap_through_the_command_exits_2(work, capsys):
    cfg = {"runners": {"r": fake_table(work, wrap=[])}}
    code = main(["--folder", str(work / "work"), "--prompt", str(work / "echo.md"), "--timeout", "30",
                 "--runner", "r", "--out", str(work / "run")], cfg=cfg)
    out, err = capsys.readouterr()
    assert code == 2
    assert "wrap must not be empty" in err
    assert "STEP:" not in out and "Traceback" not in err
    assert not (work / "run").exists()


def test_a_wrap_that_is_a_cmd_shim_gets_the_cmd_exe_guard(work):
    """The guard reads the name only, so a wrap named .cmd takes it on each system."""
    odd = work / "has(paren)"
    odd.mkdir()
    (odd / "reply.json").write_text(json.dumps({"final_text": "ECHO: OK"}), encoding="utf-8")
    t = {"adapter": "fake", "model": "fake-model", "script": str(odd / "reply.json")}
    assert run(work, t)["status"] == "OK"  # no shim: the character is fine
    with pytest.raises(UsageError, match="a character that cmd.exe acts on"):
        run(work, {**t, "wrap": ["/opt/tools/wrap.cmd"]})


META = list('%!^&|<>()"')
CODEX_RO = {"adapter": "codex", "model": "test-model-a", "sandbox": "read-only"}


@pytest.mark.parametrize("ch", META)
def test_a_cli_path_with_a_cmd_exe_character_works_in_a_wrap_that_is_not_a_cmd_file(work, monkeypatch, ch):
    """The CLI path is only an argument of the wrap, so only a cmd.exe wrap must refuse it."""
    monkeypatch.setattr("runner.step.shutil.which", lambda name: f"C:/tools{ch}x/codex.cmd")
    c = run(work, {**CODEX_RO, "wrap": [(work / "no-such-wrap").as_posix()]})
    assert (c["status"], c["reason"].split(":")[0]) == ("UNAVAILABLE", "no-such-wrap does not start")


@pytest.mark.parametrize("ext", [".cmd", ".bat", ".CMD"])
@pytest.mark.parametrize("ch", META)
def test_a_cmd_exe_wrap_refuses_a_cmd_exe_character_in_the_cli_path(work, monkeypatch, ch, ext):
    """cmd.exe reads the whole command after a .cmd wrap, the CLI path too (Codex review, rework 1)."""
    monkeypatch.setattr("runner.step.shutil.which", lambda name: f"C:/tools{ch}x/codex")
    wrap = (work / f"wrap{ext}").as_posix()
    with pytest.raises(UsageError, match=re.escape(f"wrap {wrap}: an argument holds a character that"
                                                   f" cmd.exe acts on: 'C:/tools{ch}x/codex'")):
        run(work, {**CODEX_RO, "wrap": [wrap]})
    assert not (work / "run").exists()


def test_the_cli_keeps_the_cmd_exe_guard_inside_a_wrap(work, monkeypatch):
    """A .cmd CLI in a plain wrap still runs through cmd.exe, so its arguments are checked (review M2)."""
    odd = work / "has(paren)"
    odd.mkdir()
    monkeypatch.setattr("runner.step.shutil.which", lambda name: "C:/tools/codex.cmd")
    for t in (CODEX_RO, {**CODEX_RO, "wrap": [(work / "no-such-wrap").as_posix()]}):
        with pytest.raises(UsageError, match=r"an argument holds a character that cmd.exe acts on: .*has\(paren\)"):
            run_step(odd, work / "echo.md", 60, "x", work / "run", cfg={"runners": {"x": t}})
    assert not (work / "run").exists()


def test_the_last_guard_reads_the_wrap_arguments(work):
    """A value can pass the strict pattern and still be a forbidden argument of the adapter."""
    t = {"adapter": "codex", "model": "test-model-a", "sandbox": "read-only",
         "wrap": ["/opt/tools/wrap", "danger-full-access"]}
    with pytest.raises(UsageError, match="a forbidden argument reached the command line: danger-full-access"):
        run(work, t)
    assert not (work / "run").exists()
