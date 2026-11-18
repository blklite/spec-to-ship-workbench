"""Run 1 CLI with a time limit, and stop its whole process tree at the limit.

stdout and stderr go straight to 2 files, not to pipes: a grandchild that keeps
a pipe open would hang the read after the kill. The prompt goes to stdin from a
thread, so that a CLI that does not read it cannot block the time limit.

With secrets (for the claude adapter: its credential variables and, unwrapped,
the values of its credentials file; with none of them the step does not start
the CLI), the output goes through 2 pipes instead, and a thread for each
replaces each copy of a secret with <redacted> before a byte reaches the file:
no secret is on the disk while the CLI runs, nor after the runner dies. A
grandchild that keeps a pipe open gets GRACE seconds after the CLI ends; its
later output is dropped. A write or close of a file that fails there (a full
disk, for example) is a capture failure: the output is lost, so the run stops
the CLI tree at once (1 kill for each run, also after the CLI has exited: a
child may hold a pipe), drops the rest, and returns the first error, which the
step reports as FAILED, "the output capture failed: <error>".

On Windows the CLI also starts in a job object: each process that it starts
later is in the job too, also after the CLI has exited, when taskkill /T finds
no tree. The kill ends the job; the end of a run without a kill does not.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

WINDOWS = sys.platform == "win32"
# From the system folder, not from PATH: a taskkill.exe that a work folder or
# PATH puts first must not be the one that runs.
TASKKILL = Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / "taskkill.exe"
GRACE = 5.0


def _kernel32():
    """The job calls of kernel32 (Windows only)."""
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.argtypes, k.CreateJobObjectW.restype = (ctypes.c_void_p, wintypes.LPCWSTR), wintypes.HANDLE
    k.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    k.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    return k


def _job(proc: subprocess.Popen) -> int | None:
    """Windows: a new job with the CLI in it, or None (POSIX, or a job that can
    not take the CLI: then taskkill /T is the only tree kill). A process that
    the CLI started before the job took it is not in the job."""
    if not WINDOWS:
        return None
    k = _kernel32()
    job = k.CreateJobObjectW(None, None)
    if not job:
        return None
    if not k.AssignProcessToJobObject(job, int(proc._handle)):
        k.CloseHandle(job)
        return None
    return job


class Redacted:
    """A file that gets each write with every copy of a secret replaced. The end
    of a write that may start a secret waits for the next write, or the close."""

    def __init__(self, path: Path, secrets: list[bytes]):
        self._secrets = sorted({x for x in secrets if x}, key=len, reverse=True)  # no empty value
        self._file = open(path, "wb")
        self._held = b""
        self._closed = False  # also after a close that failed
        self._lock = threading.Lock()  # a write after the close (a late grandchild) is dropped

    def _start_of_a_secret(self, data: bytes) -> int:
        for k in range(min(len(data), len(self._secrets[0]) - 1), 0, -1):
            if any(x.startswith(data[-k:]) for x in self._secrets):
                return k
        return 0

    def write(self, data: bytes) -> None:
        with self._lock:
            if self._closed:
                return
            data = self._held + data
            for x in self._secrets:
                data = data.replace(x, b"<redacted>")
            k = self._start_of_a_secret(data)
            data, self._held = data[:len(data) - k], data[len(data) - k:]
            self._file.write(data)
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self._file.write(self._held)  # shorter than each secret it starts
                finally:
                    self._file.close()


def _pump(pipe, sink: Redacted, fail) -> None:
    """Copy the pipe to the sink until the pipe closes. At the first capture
    failure, fail(error) stops the CLI tree, and the rest is read and dropped."""
    error = None
    try:
        while data := pipe.read1(65536):
            if error is None:
                try:
                    sink.write(data)
                except (OSError, ValueError) as e:
                    error = e
                    fail(e)
    except (OSError, ValueError):
        pass  # a read error of the pipe: the CLI's end of it is gone


def _close(sink: Redacted, fail) -> None:
    try:
        sink.close()
    except (OSError, ValueError) as e:  # a capture failure, not a CLI that does not start
        fail(e)


def kill_tree(proc: subprocess.Popen, job: int | None = None) -> str | None:
    """Stop the process and every child: the tree and the job (_job) on Windows,
    the group on POSIX.

    The process itself is always killed at the end, also when the tree kill
    fails. Returns None, or why the tree kill failed (a child may still run).
    On Windows a process that has exited has no tree that taskkill /T can find
    (it fails with code 128), so no taskkill runs for it; its job still holds
    its children."""
    problem = None
    try:
        if WINDOWS:
            if proc.poll() is None:
                try:
                    done = subprocess.run([str(TASKKILL), "/T", "/F", "/PID", str(proc.pid)],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                except OSError as e:
                    problem = f"taskkill does not start: {e.strerror or e}"
                else:
                    if done.returncode != 0:
                        problem = f"taskkill exited with code {done.returncode}"
            if job is not None and not _kernel32().TerminateJobObject(job, 1):
                problem = problem or "the job kill failed"
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # the group is gone already
            except PermissionError as e:
                problem = f"the group kill failed: {e.strerror or e}"
    finally:
        try:
            proc.kill()
        except OSError:
            pass
    return problem


def _feed(proc: subprocess.Popen, data: bytes) -> None:
    try:
        proc.stdin.write(data)
    except OSError:
        pass  # the CLI closed its stdin or exited
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass


def run(argv: list[str], cwd: str | Path, stdin: bytes, out_path: Path, err_path: Path,
        timeout_s: float, env: dict | None = None, *,
        secrets: list[bytes] = ()) -> tuple[int | None, bool, float, str | None, Exception | None]:
    """(exit code or None, timed out, seconds used, why the tree kill failed or
    None, the first capture failure or None).

    env is the environment of the CLI; None inherits this process's. secrets:
    the values that no byte of the 2 files may hold."""
    kwargs: dict = {}
    if WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    secrets = [x for x in secrets if x]
    start = time.monotonic()
    errors, kill_problems = [], []
    proc = job = None
    kill = threading.Lock()  # held while the 1 tree kill of the run runs
    done = []  # the kill ran, or the run ended: no more kills

    def stop() -> None:
        with kill:
            if not done:  # else a 2nd failure, or 1 after the timeout kill
                done.append(True)
                kill_problems.append(kill_tree(proc, job))

    def end() -> None:
        with kill:
            done.append(True)
            if job is not None:  # no kill can use it now; the processes of the job run on
                _kernel32().CloseHandle(job)

    def fail(error: Exception) -> None:
        errors.append(error)
        if proc is not None:  # else the CLI did not start
            stop()

    with contextlib.ExitStack() as files:
        files.callback(end)  # the last exit: after the closes, which can still kill
        if secrets:
            sinks = [Redacted(p, secrets) for p in (out_path, err_path)]
            for sink in sinks:
                files.callback(_close, sink, fail)  # also on an exception: the held end is no secret
            out = err = subprocess.PIPE
        else:
            sinks = []
            out, err = (files.enter_context(open(p, "wb")) for p in (out_path, err_path))
        proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.PIPE,
                                stdout=out, stderr=err, env=env, **kwargs)
        job = _job(proc)
        feeder = threading.Thread(target=_feed, args=(proc, stdin), daemon=True)
        feeder.start()
        pumps = [threading.Thread(target=_pump, args=(pipe, sink, fail), daemon=True)
                 for pipe, sink in zip((proc.stdout, proc.stderr), sinks)]
        for pump in pumps:
            pump.start()
        try:
            code = proc.wait(timeout=timeout_s)
            timed_out = False
        except subprocess.TimeoutExpired:
            stop()
            proc.wait()
            code, timed_out = None, True
        feeder.join(timeout=5)
        end = time.monotonic() + GRACE
        for pump in pumps:
            pump.join(timeout=max(0.0, end - time.monotonic()))
    kill_problem = next((p for p in kill_problems if p), None)
    return code, timed_out, time.monotonic() - start, kill_problem, errors[0] if errors else None
