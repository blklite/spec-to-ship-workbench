"""At the time limit the step stops the CLI and every child
process, and gives TIMEOUT with the seconds used.

A fake `claude` on PATH starts a child that sleeps, writes the child's pid, and
sleeps itself. After the timeout the child must be gone too. On Windows the
fake is a .cmd shim, so the tree is cmd.exe, the fake CLI and its child.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

import runner_fakes
from runner import proc
from runner.step import run_step

ECHO = "The last line of your reply is exactly\n\n```\nECHO: OK\n```\n"
LIMIT = 5.0


def alive(pid: int) -> bool:
    """Whether a process runs. Never os.kill(pid, 0) on Windows: there 0 is CTRL_C_EVENT."""
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                             capture_output=True, text=True).stdout
        return f'"{pid}"' in out
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
    return bool(out.strip()) and not out.strip().startswith("Z")


def gone_within(pid: int, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.2)
    return not alive(pid)


def test_alive_sees_a_running_process():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert alive(p.pid)
    finally:
        p.kill()
        p.wait()
    assert gone_within(p.pid, 5)


@pytest.mark.parametrize("name,t", [
    ("claude", {"adapter": "claude", "model": "m-1", "allowed_tools": ["Read"]}),
    ("codex", {"adapter": "codex", "model": "m-1", "sandbox": "read-only"}),
])
def test_timeout_stops_the_cli_and_its_child(tmp_path, monkeypatch, name, t):
    (tmp_path / "work").mkdir()
    (tmp_path / "echo.md").write_text(ECHO, encoding="utf-8")
    runner_fakes.install(monkeypatch, tmp_path, name)
    pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_CLI_CHILD", str(pid_file))
    started = time.monotonic()
    c = run_step(tmp_path / "work", tmp_path / "echo.md", LIMIT, "r", tmp_path / "run",
                 cfg={"runners": {"r": t}})
    took = time.monotonic() - started
    child = int(pid_file.read_text(encoding="utf-8"))
    try:
        assert c["status"] == "TIMEOUT"
        assert c["exit_code"] is None
        assert LIMIT <= c["seconds"] < LIMIT + 15
        assert took < LIMIT + 20, "the step waited for the child instead of stopping it"
        assert c["reason"] == f"stopped at the time limit after {c['seconds']:.1f} s"
        assert gone_within(child, 10), "the child of the CLI still runs after the timeout"
    finally:
        if alive(child):  # do not leave a process behind when the test fails
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(child)], capture_output=True)
            else:
                import os
                import signal
                os.kill(child, signal.SIGKILL)


def test_nonzero_exit_without_allowed_line_is_failed_with_the_code(tmp_path, monkeypatch):
    (tmp_path / "work").mkdir()
    (tmp_path / "echo.md").write_text(ECHO, encoding="utf-8")
    runner_fakes.install(monkeypatch, tmp_path, "claude")
    runner_fakes.output(monkeypatch, tmp_path, "crashed before any JSON")
    monkeypatch.setenv("FAKE_CLI_EXIT", "7")
    c = run_step(tmp_path / "work", tmp_path / "echo.md", 30, "r", tmp_path / "run",
                 cfg={"runners": {"r": {"adapter": "claude", "model": "m-1"}}})
    assert (c["status"], c["exit_code"]) == ("FAILED", 7)
    assert c["reason"] == "claude exited with code 7 (no JSON result)"


def test_timeout_keeps_the_usage_of_the_finished_turns(tmp_path, monkeypatch):
    """A codex run that printed a finished turn before the limit: its tokens count."""
    (tmp_path / "work").mkdir()
    (tmp_path / "echo.md").write_text(ECHO, encoding="utf-8")
    runner_fakes.install(monkeypatch, tmp_path, "codex")
    fixture = runner_fakes.FAKE_CLI.parent / "codex-events.jsonl"
    runner_fakes.output(monkeypatch, tmp_path, fixture.read_text(encoding="utf-8"))
    pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_CLI_CHILD", str(pid_file))
    c = run_step(tmp_path / "work", tmp_path / "echo.md", LIMIT, "r", tmp_path / "run",
                 cfg={"runners": {"r": {"adapter": "codex", "model": "m-1", "sandbox": "read-only"}}})
    assert c["status"] == "TIMEOUT"
    assert (c["input_tokens"], c["output_tokens"]) == (12000, 300)
    assert c["last_line"] is None
    assert gone_within(int(pid_file.read_text(encoding="utf-8")), 10)


def test_a_cli_that_does_not_start_is_unavailable(tmp_path, monkeypatch):
    """Found on PATH, but not a program: no traceback, a status and counters."""
    (tmp_path / "work").mkdir()
    (tmp_path / "echo.md").write_text(ECHO, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / ("claude.exe" if sys.platform == "win32" else "claude")
    fake.write_bytes(b"this is not a program\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    c = run_step(tmp_path / "work", tmp_path / "echo.md", 30, "r", tmp_path / "run",
                 cfg={"runners": {"r": {"adapter": "claude", "model": "m-1"}}})
    assert c["status"] == "UNAVAILABLE"
    assert c["reason"].startswith("claude does not start: ")
    assert (tmp_path / "run" / "counters.json").is_file()


# --- the tree kill fails: the CLI itself is still killed, and the status stays TIMEOUT ---

def break_the_tree_kill(monkeypatch, tmp_path):
    """A taskkill that cannot start on Windows; a group kill that is refused on POSIX."""
    if sys.platform == "win32":
        monkeypatch.setattr(proc, "TASKKILL", tmp_path / "no-such-taskkill.exe")
        return "taskkill does not start"

    def refused(pid, sig):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(os, "killpg", refused)
    return "the group kill failed"


def sleeper() -> subprocess.Popen:
    kwargs = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32"
              else {"start_new_session": True})
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)


def test_kill_tree_falls_back_to_the_process_kill(tmp_path, monkeypatch):
    why = break_the_tree_kill(monkeypatch, tmp_path)
    p = sleeper()
    try:
        problem = proc.kill_tree(p)
        assert problem is not None and problem.startswith(why)
        assert p.wait(timeout=10) is not None, "the fallback did not kill the process"
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill is Windows only")
def test_kill_tree_reports_a_taskkill_that_fails(tmp_path, monkeypatch):
    failing = tmp_path / "taskkill-fails.cmd"
    failing.write_text("@exit /b 5\r\n", encoding="ascii")
    monkeypatch.setattr(proc, "TASKKILL", failing)
    p = sleeper()
    try:
        assert proc.kill_tree(p) == "taskkill exited with code 5"
        assert p.wait(timeout=10) is not None
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


def test_kill_tree_uses_taskkill_from_the_system_folder():
    if sys.platform != "win32":
        pytest.skip("taskkill is Windows only")
    system = os.environ.get("SystemRoot") or r"C:\Windows"
    assert str(proc.TASKKILL).lower() == os.path.join(system, "System32", "taskkill.exe").lower()
    assert proc.TASKKILL.is_file()


def test_a_failed_tree_kill_keeps_timeout_and_names_it(tmp_path, monkeypatch):
    why = break_the_tree_kill(monkeypatch, tmp_path)
    (tmp_path / "work").mkdir()
    (tmp_path / "echo.md").write_text(ECHO, encoding="utf-8")
    script = tmp_path / "reply.json"
    script.write_text(json.dumps({"final_text": "ECHO: OK", "delay": 60}), encoding="utf-8")
    started = time.monotonic()
    c = run_step(tmp_path / "work", tmp_path / "echo.md", 1.0, "r", tmp_path / "run",
                 cfg={"runners": {"r": {"adapter": "fake", "model": "m-1", "script": str(script)}}})
    assert time.monotonic() - started < 30, "the fallback did not stop the CLI"
    assert (c["status"], c["exit_code"]) == ("TIMEOUT", None)
    assert c["reason"].startswith("stopped at the time limit after ")
    assert f"the process tree kill failed ({why}" in c["reason"]
    data = json.loads((tmp_path / "run" / "counters.json").read_text(encoding="utf-8"))
    assert (data["status"], data["reason"]) == ("TIMEOUT", c["reason"])
