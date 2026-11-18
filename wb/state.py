"""The item state: 1 folder for each item under `config:state.home`.

The folder holds state.json, the tracker file of the local tracker, the owner's
answers and the run folder of each step. A write goes to a temp file in the
same folder, then a rename, so that a failed write leaves the old file.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from runner import ROOT, UsageError

VERSION = 1
# An item id becomes a folder name: no path separator, no "..", no leading "-".
ITEM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def check_id(item_id: str) -> str:
    if not ITEM_ID.fullmatch(item_id or ""):
        raise UsageError(f"bad item id {item_id!r}: use letters, digits, - and _ only")
    return item_id


def under_root(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def home(cfg: dict) -> Path:
    try:
        return under_root(cfg["state"]["home"])
    except (KeyError, TypeError):
        raise UsageError("the config has no state.home") from None


def item_dir(cfg: dict, item_id: str) -> Path:
    return home(cfg) / check_id(item_id)


def write_json(path: Path, data) -> None:
    """Write to a temp file, then rename over the old file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(data, indent=1))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path, what: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise UsageError(f"no {what}: {path}") from None
    except (OSError, ValueError) as e:
        raise UsageError(f"cannot read {what} {path}: {e}") from e


def exists(cfg: dict, item_id: str) -> bool:
    return (item_dir(cfg, item_id) / "state.json").is_file()


def load(cfg: dict, item_id: str) -> dict:
    st = read_json(item_dir(cfg, item_id) / "state.json", f"state of item {item_id}")
    if not isinstance(st, dict) or st.get("version") != VERSION:
        version = st.get("version") if isinstance(st, dict) else None
        raise UsageError(f"item {item_id}: unknown state version {version!r} (this wb reads {VERSION})")
    st.setdefault("records", {})  # a state of before the records
    return st


@contextmanager
def answer_lock(cfg: dict, item_id: str):
    """An exclusive OS lock on answer.flock of the existing item folder, from the load of the state to the save
    of an answer; it waits for another holder, and the file stays. An OSError is a usage error."""
    path = item_dir(cfg, item_id) / "answer.flock"
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)  # no mkdir: an unknown id makes no folder
    except OSError as e:
        raise UsageError(f"cannot lock the answers of item {item_id}: {e}") from e
    try:
        try:
            lock_fd(fd)
        except OSError as e:
            raise UsageError(f"cannot lock the answers of item {item_id}: {e}") from e
        try:
            yield
        finally:
            unlock_fd(fd)
    finally:
        os.close(fd)  # on POSIX the close ends the flock


def lock_fd(fd: int, wait: bool = True) -> None:
    """An exclusive OS lock on the open file fd: fcntl.flock on POSIX, msvcrt.locking of byte 0 on Windows (it
    locks at the file position). With wait False a held lock is a BlockingIOError or a PermissionError."""
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock_fd(fd: int) -> None:
    """The unlock of lock_fd before the close: on POSIX the close ends the flock."""
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


def save(cfg: dict, st: dict) -> None:
    write_json(item_dir(cfg, st["id"]) / "state.json", st)


def new(item_id: str) -> dict:
    """An empty state; intake fills the spec, work, tier, size and limits."""
    return {
        "version": VERSION,
        "id": check_id(item_id),
        "title": None,
        "spec": {"path": None, "sha256": None},
        "work": {"folder": None, "branch": None, "base": None, "repo": None, "url": None},
        "tier": None,
        "size": None,
        "limits": {"usd": None, "hours": None, "rounds": None},
        "stage": "spec-check",
        "status": "running",
        "rounds_used": 0,
        "usd": 0.0,
        "seconds": 0.0,
        "question": None,
        "steps": [],
        "answers": [],
        "approvals": [],
        "alerts": [],
        "not_verified": [],
        "retried": False,
        "review_pass": {},
        "open_step": None,
        "records": {},
    }
