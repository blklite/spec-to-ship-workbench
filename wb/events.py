"""The event lines: 1 JSON line in events.jsonl of the state home at each change that `wb notify` reports.

Each line is {"v": 1, "time", "kind", "item", ...} with each text cut at 200 characters. The writers are the
run-next child, `wb intake`, `wb record` and `wb deliver`: each line is 1 os.write to a file opened with O_APPEND,
under the OS lock of state.lock_fd, so 2 processes never mix their lines. A failed append warns on stderr and never
stops wb.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

from runner.step import _utc
from wb import state

FILE = "events.jsonl"
MAX_TEXT = 200


def key(case, text: str) -> str:
    """The key of a wait: the first 16 hex characters of the SHA-256 of the case, 1 line feed and the text."""
    return hashlib.sha256(f"{case}\n{text}".encode("utf-8")).hexdigest()[:16]


def append(cfg: dict, kind: str, item: str, **fields) -> None:
    line = {"v": 1, "time": _utc(), "kind": kind, "item": item, **fields}
    data = json.dumps({k: v[:MAX_TEXT] if isinstance(v, str) else v for k, v in line.items()}) + "\n"
    try:
        # O_BINARY: no \r\n on Windows
        fd = os.open(state.home(cfg) / FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0),
                     0o600)
        try:
            state.lock_fd(fd)
            try:
                os.write(fd, data.encode("utf-8"))
            finally:
                state.unlock_fd(fd)
        finally:
            os.close(fd)
    except OSError as e:
        print(f"wb: the {kind} event of item {item} was not written: {e}", file=sys.stderr)
