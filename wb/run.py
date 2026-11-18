"""The detached run of `wb run-next`: the lock of an item, the work folder, and
the start of the run in a new session.

`wb run-next <id>` starts `python -m wb run-next <id> --foreground` detached
from the terminal (a new session on POSIX, a detached process on Windows),
with stdin the null device and its log in the item folder, and returns. The
foreground run holds an OS lock on run.flock of the item folder for the whole
run, then takes the lock file of the item: its PID and, on Linux, the boot id
and start time of the process, so that a PID that the system gives again after
a reboot does not hold the lock. A held OS lock or a live lock refuses a second
run; a dead lock is replaced, and the stage machine ends the open step of the
dead run (machine.recover). Nothing starts at boot.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from runner import ROOT, UsageError
from runner.step import _utc
from wb import machine, state, tracker

WINDOWS = sys.platform == "win32"


class Busy(Exception):
    """A live run holds the lock of the item; pid None: it has not written run.lock yet."""

    def __init__(self, pid):
        super().__init__("a run is going" if pid is None else f"a run is going (pid {pid})")
        self.pid = pid


def lock_path(cfg: dict, item_id: str) -> Path:
    return state.item_dir(cfg, item_id) / "run.lock"


def log_path(cfg: dict, item_id: str) -> Path:
    return state.item_dir(cfg, item_id) / "run.log"


def start_token(pid: int) -> str | None:
    """Linux: the boot id and the start time of the process; None elsewhere or
    when the process does not exist."""
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return f"{boot}:{stat.rsplit(')', 1)[1].split()[19]}"  # field 22, starttime


def alive(pid, token) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if sys.platform.startswith("linux"):
        return token is not None and start_token(pid) == token
    if WINDOWS:  # never os.kill(pid, 0): on Windows it stops the process
        import ctypes

        k = ctypes.windll.kernel32
        k.OpenProcess.restype = ctypes.c_void_p
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        # shortcut: a PID that Windows gives again counts as alive; the install is Linux
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def holder(path: Path) -> int | None:
    """The PID of the live run that holds the lock, or None."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data["pid"] if alive(data.get("pid"), data.get("token")) else None


def take(path: Path) -> int:
    """Take the lock, or raise Busy; returns the handle of the OS lock for release. An OS lock on run.flock
    beside it is held for the whole run, before run.lock is read: 1 run replaces the lock of a dead run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.with_name("run.flock"), os.O_RDWR | os.O_CREAT, 0o600)  # not inherited: a step child keeps no lock
    try:
        if WINDOWS:
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise Busy(holder(path)) from None
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps({"pid": os.getpid(), "token": start_token(os.getpid()), "start": _utc()}),
                       encoding="utf-8")
        for _ in range(2):
            try:
                os.link(tmp, path)  # create with its whole content, or fail: never a half-written lock
                return fd
            except FileExistsError:
                pid = holder(path)
                if pid is not None:
                    raise Busy(pid) from None
                path.unlink(missing_ok=True)
        raise Busy(holder(path))
    except BaseException:
        _unlock(fd)
        raise
    finally:
        tmp.unlink(missing_ok=True)


def _unlock(fd: int) -> None:
    if WINDOWS:
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    os.close(fd)  # on POSIX the close ends the flock


def release(path: Path, fd: int) -> None:
    """run.lock goes first, then the OS lock; run.flock stays."""
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError, AttributeError):
        pass
    _unlock(fd)


def _git(folder, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=folder, capture_output=True, text=True)
    if r.returncode != 0:
        raise UsageError(f"git {' '.join(args[:2])} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def make_work_folder(st: dict, code_root: Path | None = None) -> None:
    """When the work folder does not exist: a clone of work.url of the item, or
    of the origin of the code clone when it is null, the branch of the item
    from origin/<branch> when the origin has it, else from origin/<base>, and the git identity of the code clone in its local
    config (a step home has no git config). An existing folder of an item with
    a work.url must have that origin."""
    folder = Path(st["work"]["folder"])
    url = st["work"].get("url")
    if folder.exists():
        # the configured value: get-url would give the url.<base>.insteadOf rewrite of the host
        if url and (origin := _git(folder, "config", "--get", "remote.origin.url")) != url:
            raise UsageError(f"the work folder {folder} has the origin {origin}, not the repository of the item"
                             f" {url}; the owner deletes the folder by hand")
        return
    code_root = code_root or ROOT
    url = url or _git(code_root, "remote", "get-url", "origin")
    who = {k: _git(code_root, "config", k) for k in ("user.name", "user.email")}
    try:
        _git(None, "clone", "-q", url, str(folder))
        branch = st["work"]["branch"]
        # a work folder that was lost: the branch on origin goes on, so the next push is a fast-forward
        on_origin = subprocess.run(["git", "rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}"],
                                   cwd=folder, capture_output=True).returncode == 0
        _git(folder, "checkout", "-q", "-b", branch, f"origin/{branch if on_origin else st['work']['base']}")
        for k, v in who.items():
            _git(folder, "config", k, v)
    except UsageError:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def run_locked(cfg: dict, personas: dict, item_id: str) -> dict:
    """The foreground run: the lock, the work folder, then the stage machine."""
    st = state.load(cfg, item_id)
    path = lock_path(cfg, item_id)
    fd = take(path)
    try:
        if st["status"] == "running":
            make_work_folder(st)
        return machine.run_next(cfg, personas, item_id)
    except Exception as e:
        stopped(cfg, item_id, e)
        raise
    finally:
        release(path, fd)


def stopped(cfg: dict, item_id: str, e: Exception) -> None:
    """An error after the lock: an item that is running on disk waits (case 2). The comment names the class
    and the log, never the error text; the error goes on. The wait is saved before the tracker write: a failed
    save changes no tracker item."""
    try:
        st = state.load(cfg, item_id)
        if st["status"] == "running":
            machine.ask(st, 2, f"The run stopped with {type(e).__name__}; the log is {log_path(cfg, item_id)}.")
            state.save(cfg, st)
            machine.tell(cfg, tracker.make(cfg), st)
    except Exception as w:  # never masks the first error
        e.add_note(f"the wait after it failed too: {type(w).__name__}")


def spawn(argv: list[str], log: Path) -> subprocess.Popen:
    """Start argv detached from the terminal, with its output appended to log."""
    kwargs: dict = {}
    if WINDOWS:
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True  # no hangup when the ssh link drops
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as out:
        return subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                close_fds=True, **kwargs)
