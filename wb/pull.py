"""`wb pull-answers --queue DIR`: answer case 2 waits from the files of the answer forms (see wb/README.md).

Each *.json file at the top of DIR is {"id": ..., "qhash": ..., "option": ...}. A good file answers its item
with the option line through the code of `wb answer` and moves to DIR/done. Any other file is deleted, since it
can hold a key, and DIR/refused gets 1 reason line for it, which never quotes the file.
For a known id the pass holds the OS lock on answer.flock of the item, as `wb answer` does, from the load of
the state to the save: 2 runs that overlap give 1 answer. A name in DIR/done and DIR/refused keeps at most the
first 200 bytes of the part of the queue name before .json, cut at a whole UTF-8 character.
"""

from __future__ import annotations

import json
import os
import stat
from contextlib import ExitStack
from pathlib import Path

from runner import UsageError
from wb import machine, pages, state

MAX_BYTES = 4096
NAME_BYTES = 200
FIELDS = {"id", "qhash", "option"}


def check(cfg: dict, data: bytes, held: ExitStack) -> tuple[dict, str] | str:
    """(the state, the option line) of a good file, or the reason of a refusal. For a known id, held takes
    answer.flock of the item before the load of the state."""
    try:
        f = json.loads(data.decode("utf-8"))
    except ValueError:
        return "the file is not JSON in UTF-8"
    if not isinstance(f, dict):
        return "the file is not a JSON object"
    if set(f) != FIELDS:
        return "the fields must be exactly id, qhash and option"
    if not all(isinstance(v, str) for v in f.values()):
        return "a field is not a string"
    if not state.ITEM_ID.fullmatch(f["id"]):
        return "the id has a bad form"
    if not state.exists(cfg, f["id"]):
        return "the id is unknown"
    held.enter_context(state.answer_lock(cfg, f["id"]))  # a usage error: no except below catches it
    try:
        st = state.load(cfg, f["id"])
    except UsageError:
        return "the state of the item cannot be read"
    q = st.get("question") or {}
    if not (isinstance(q, dict) and isinstance(st.get("status"), str) and isinstance(st.get("answers"), list)):
        return "the state of the item cannot be read"
    if st["status"] == "waiting" and q.get("case") != 2:
        return f"the item waits at case {q.get('case')}, which the owner answers at the host terminal"
    why = machine.refusal(st)
    if why:
        return why
    opts = pages.options(q)
    if not opts:
        return "the question has no options"
    if f["qhash"] != pages.qhash(q, opts):
        return "the qhash does not match the current question"
    chosen = [o for o in opts if o[0] == f["option"]]
    if not chosen:
        return "the letter is not an option of the question"
    return st, chosen[0]


def free(folder: Path, name: str) -> Path:
    """folder/name, or with a number before the last suffix when that is taken: never an overwrite."""
    path, k = folder / name, 0
    while os.path.lexists(path):
        k += 1
        stem, dot, ext = name.rpartition(".")
        path = folder / f"{stem}.{k}.{ext}"
    return path


def short(name: str) -> str:
    """name, with the part before .json cut to its first 200 bytes at a whole UTF-8 character when longer."""
    stem = os.fsencode(name[:-5])
    if len(stem) <= NAME_BYTES:
        return name
    i = NAME_BYTES
    while i > NAME_BYTES - 3 and stem[i] & 0xC0 == 0x80:  # the first byte left out continues a character
        i -= 1
    return os.fsdecode(stem[:i]) + name[-5:]


def pull(cfg: dict, queue: str) -> tuple[int, int]:
    """(answered, refused) after 1 pass over the queue."""
    top = Path(queue)
    if not top.is_dir():
        raise UsageError(f"--queue is not a folder: {top}")
    done, refused = top / "done", top / "refused"
    try:
        done.mkdir(exist_ok=True)
        refused.mkdir(exist_ok=True)
    except OSError as e:
        raise UsageError(f"cannot make done and refused in the queue: {e}") from e
    answered = bad = 0
    for path in sorted(p for p in top.glob("*.json") if not p.name.startswith(".")):
        with ExitStack() as held:
            try:
                mode = os.lstat(path).st_mode
                if stat.S_ISDIR(mode):  # a folder is never a file of the route: left alone
                    continue
                if not stat.S_ISREG(mode):  # a link or a pipe: never followed or read
                    result = "the file is not a regular file"
                else:
                    # no link and no wait on a pipe that took the place of the file after the lstat
                    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
                    with os.fdopen(fd, "rb") as fh:
                        regular = stat.S_ISREG(os.fstat(fd).st_mode)
                        data = fh.read(MAX_BYTES + 1) if regular else b""
                    result = ("the file is not a regular file" if not regular
                              else "the file is above 4096 bytes" if len(data) > MAX_BYTES
                              else check(cfg, data, held))
            except FileNotFoundError:  # another run took it
                continue
            except OSError:  # no read right, a link that took the place of the file: never a stop of the run
                result = "the file cannot be read"
            if not isinstance(result, str):
                st, line = result
                machine.store(cfg, st, (line + "\n").encode("utf-8"))
        if isinstance(result, str):
            path.unlink(missing_ok=True)  # first: a refused file can hold a key, also in bad JSON
            free(refused, short(path.name) + ".reason").write_text(result + "\n", encoding="utf-8")
            bad += 1
            continue
        path.rename(free(done, short(path.name)))
        answered += 1
    return answered, bad
