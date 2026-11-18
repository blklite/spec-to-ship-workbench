"""A run outlives the ssh link. `wb run-next <id>` starts
the run detached, with its log in the item folder, and returns. A lock file
with the PID refuses a second run. A lock with a dead PID: the open step ends
FAILED (interrupted), the work folder goes back to the head before the step,
and the step runs 1 time again. run-next makes the work folder.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from runner import UsageError
from runner.step import _utc
from wb import intake, machine, run, state, tracker
from wb.cli import main
from wb_fakes import HEAD, SPEC, Replies, git, make_cfg, start

GO = "SPEC-CHECK 7: GO"
READY = "BUILD: READY " + HEAD
CLOSE = "REVIEW: CLOSE"
CR_CLEAN = "CODE-REVIEW: 0 findings (0 critical, 0 high, 0 medium, 0 low)"


@pytest.fixture
def item(tmp_path, monkeypatch):
    return start(tmp_path, monkeypatch)


@pytest.fixture
def spawned(monkeypatch):
    calls = []
    monkeypatch.setattr(run, "spawn", lambda argv, log: calls.append((argv, log)))
    return calls


def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def write_lock(cfg, pid, token):
    path = run.lock_path(cfg, "7")
    path.write_text(json.dumps({"pid": pid, "token": token, "start": _utc()}), encoding="utf-8")
    return path


def test_run_next_starts_a_detached_run_and_returns(item, spawned, capsys):
    cfg, personas, r = item
    assert main(["run-next", "7"], cfg=cfg, personas=personas) == 0
    log = state.item_dir(cfg, "7") / "run.log"
    assert capsys.readouterr().out.splitlines()[-1] == f"WB: STARTED 7 - log {log}"
    assert spawned == [([sys.executable, "-m", "wb", "run-next", "7", "--foreground"], log)]
    assert r.calls == [] and not run.lock_path(cfg, "7").exists()


def test_the_spawn_detaches_the_child_with_its_log(tmp_path):
    log = tmp_path / "item" / "run.log"
    child = ("import os, sys, time\n"
             "print('stdin', repr(sys.stdin.read()))\n"
             "print('session', os.getsid(0) if hasattr(os, 'getsid') else None)\n"
             "time.sleep(1)\n"
             "print('end')\n")
    t = time.monotonic()
    p = run.spawn([sys.executable, "-c", child], log)
    assert time.monotonic() - t < 1  # it returns at once, the child goes on
    assert p.wait(timeout=60) == 0
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "stdin ''" and lines[-1] == "end"
    if hasattr(os, "getsid"):  # a new session: the hangup of the ssh link does not reach it
        assert int(lines[1].split()[1]) == p.pid != os.getsid(0)


def test_a_live_lock_refuses_a_second_run(item, spawned, capsys):
    cfg, personas, r = item
    write_lock(cfg, os.getpid(), run.start_token(os.getpid()))
    for argv in (["run-next", "7"], ["run-next", "7", "--foreground"]):
        assert main(argv, cfg=cfg, personas=personas) == 1
        assert capsys.readouterr().out.splitlines()[-1] == f"WB: RUN REFUSE 7 - a run is going (pid {os.getpid()})"
    assert spawned == [] and r.calls == []


def test_the_lock_names_the_run_and_is_released(item, monkeypatch):
    cfg, personas, r = item
    seen = []
    inner = machine.run_step

    def step(folder, prompt, timeout, runner, out, *, cfg=None):
        seen.append((run.holder(run.lock_path(cfg_, "7")), state.load(cfg_, "7")["open_step"]))
        return inner(folder, prompt, timeout, runner, out, cfg=cfg)

    cfg_ = cfg
    monkeypatch.setattr(machine, "run_step", step)
    r.add("spec-checker", "SPEC-CHECK 7: QUESTIONS - 1 questions")
    st = run.run_locked(cfg, personas, "7")
    assert st["status"] == "waiting" and st["open_step"] is None
    ((pid, open_step),) = seen
    assert pid == os.getpid()
    assert (open_step["n"], open_step["role"], open_step["head_before"]) == (1, "spec-checker",
                                                                             st["steps"][0]["head_before"])
    assert not run.lock_path(cfg, "7").exists()


def test_the_lock_is_released_when_the_run_fails(item):
    cfg, personas, _ = item
    personas["roles"]["spec-checker"]["runner"] = "no-such-runner"
    with pytest.raises(UsageError):
        run.run_locked(cfg, personas, "7")
    assert not run.lock_path(cfg, "7").exists()


class Died(BaseException):
    """The host dies in the middle of a step."""


def interrupted_build(cfg, personas, r, monkeypatch):
    """A run that died in its build step, after the builder made a commit and a file."""
    work = Path(cfg["work"]["home"]) / "7"
    before = machine.head(work)
    inner = machine.run_step

    def step(folder, prompt, timeout, runner, out, *, cfg=None):
        if runner == "fake-builder":
            git(work, "commit", "-q", "--allow-empty", "-m", "half a build")
            (work / "half.txt").write_text("x\n", encoding="utf-8")
            raise Died()
        return inner(folder, prompt, timeout, runner, out, cfg=cfg)

    monkeypatch.setattr(machine, "run_step", step)
    r.add("spec-checker", GO)
    with pytest.raises(Died):
        machine.run_next(cfg, personas, "7")
    monkeypatch.setattr(machine, "run_step", inner)
    st = state.load(cfg, "7")
    assert (st["stage"], st["open_step"]["role"], st["open_step"]["head_before"]) == ("build", "builder", before)
    assert machine.head(work) != before
    write_lock(cfg, dead_pid(), "a-dead-run")
    return work, before


def test_a_dead_pid_gives_1_retry_from_the_old_head(item, capsys, monkeypatch):
    cfg, personas, r = item
    work, before = interrupted_build(cfg, personas, r, monkeypatch)
    seen = []
    inner = machine.run_step

    def step(folder, prompt, timeout, runner, out, *, cfg=None):
        seen.append((machine.head(folder), (Path(folder) / "half.txt").exists()))
        return inner(folder, prompt, timeout, runner, out, cfg=cfg)

    monkeypatch.setattr(machine, "run_step", step)
    r.add("builder", READY).add("reviewer", CLOSE).add("second-reviewer", CR_CLEAN)
    assert main(["run-next", "7", "--foreground"], cfg=cfg, personas=personas) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "WB: READY 7"
    st = state.load(cfg, "7")
    gone = st["steps"][1]
    assert (gone["n"], gone["role"], gone["status"], gone["reason"]) == (2, "builder", "FAILED", "interrupted")
    assert gone["usd"] is None and gone["head_after"] == before
    assert st["not_verified"] == ["step 2: interrupted: its dollars and time are not known"]
    assert [s["role"] for s in st["steps"]] == ["spec-checker", "builder", "builder", "reviewer", "second-reviewer"]
    assert seen[0] == (before, False)  # the retry starts from the old head, with the half build gone
    assert not run.lock_path(cfg, "7").exists()


def test_a_dead_pid_and_a_retry_that_fails_waits(item, monkeypatch):
    cfg, personas, r = item
    interrupted_build(cfg, personas, r, monkeypatch)
    r.add("builder", "no last line")
    st = run.run_locked(cfg, personas, "7")
    assert (st["stage"], st["status"], st["question"]["case"]) == ("build", "waiting", 2)
    assert [s["status"] for s in st["steps"][1:]] == ["FAILED", "INVALID"]


def test_a_dead_run_with_no_open_step_only_clears_the_lock(item):
    cfg, personas, r = item
    write_lock(cfg, dead_pid(), "a-dead-run")
    r.add("spec-checker", "SPEC-CHECK 7: QUESTIONS - 1 questions")
    st = run.run_locked(cfg, personas, "7")
    assert [s["status"] for s in st["steps"]] == ["OK"] and st["not_verified"] == []


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the start token is read from /proc")
def test_a_reused_pid_does_not_hold_the_lock(item):
    cfg, _, _ = item
    path = write_lock(cfg, os.getpid(), "another-boot:1")
    assert run.holder(path) is None
    write_lock(cfg, os.getpid(), run.start_token(os.getpid()))
    assert run.holder(path) == os.getpid()


# --- an OS lock on run.flock for the whole run ---

def test_a_held_os_lock_refuses_a_second_take(item, monkeypatch):
    cfg, _, _ = item
    path = run.lock_path(cfg, "7")
    fd = run.take(path)
    try:
        before = path.read_bytes()
        monkeypatch.setattr(run, "holder", lambda p: None)  # as if run.lock named a dead run
        with pytest.raises(run.Busy):
            run.take(path)
        assert path.read_bytes() == before
    finally:
        run.release(path, fd)
    assert not path.exists() and path.with_name("run.flock").exists()
    run.release(path, run.take(path))  # free again after the release


def flock_handles(monkeypatch):
    """The handles of run.flock that os.open gives, and each handle that os.close gets."""
    seen = {"opened": [], "closed": []}
    real_open, real_close = os.open, os.close

    def opens(file, *a, **kw):
        fd = real_open(file, *a, **kw)
        if Path(file).name == "run.flock":
            seen["opened"].append(fd)
        return fd

    def closes(fd):
        seen["closed"].append(fd)
        real_close(fd)

    monkeypatch.setattr(os, "open", opens)
    monkeypatch.setattr(os, "close", closes)
    return seen


def test_a_refusal_by_the_held_os_lock_closes_its_handle(item, monkeypatch):
    """The close at the refusal of the OS lock."""
    cfg, _, _ = item
    path = run.lock_path(cfg, "7")
    fd = run.take(path)
    try:
        seen = flock_handles(monkeypatch)
        with pytest.raises(run.Busy):
            run.take(path)
        monkeypatch.undo()
        assert len(seen["opened"]) == 1 and seen["opened"] == seen["closed"]
    finally:
        run.release(path, fd)


def test_a_refusal_by_a_live_run_lock_closes_its_handle(item, monkeypatch):
    """The close at the refusal of a live run.lock, with the OS lock free."""
    cfg, _, _ = item
    write_lock(cfg, os.getpid(), run.start_token(os.getpid()))
    seen = flock_handles(monkeypatch)
    with pytest.raises(run.Busy):
        run.take(run.lock_path(cfg, "7"))
    monkeypatch.undo()
    assert len(seen["opened"]) == 1 and seen["opened"] == seen["closed"]


def test_a_held_os_lock_with_no_run_lock_refuses_with_no_pid(item, capsys):
    """Before run.lock is written, the refusal names no pid."""
    cfg, personas, r = item
    assert str(run.Busy(None)) == "a run is going" and str(run.Busy(5)) == "a run is going (pid 5)"
    path = run.lock_path(cfg, "7")
    fd = run.take(path)
    path.unlink()  # the OS lock is held, run.lock is not written yet
    try:
        assert main(["run-next", "7", "--foreground"], cfg=cfg, personas=personas) == 1
    finally:
        run.release(path, fd)
    out = capsys.readouterr().out
    assert out.splitlines()[-1] == "WB: RUN REFUSE 7 - a run is going" and "None" not in out and r.calls == []


# Child 1 reads the dead lock first and returns only after child 0 has tried: with no OS lock both replace it.
CHILD = """
import sys, time
from pathlib import Path
from wb import run
path, me = Path(sys.argv[1]), sys.argv[2]
mark = lambda name: (path.parent / name).write_text("x")
def until(name):
    end = time.monotonic() + 30
    while not (path.parent / name).exists() and time.monotonic() < end:
        time.sleep(0.01)
real = run.holder
def holder(p):
    pid = real(p)
    mark("read1")
    until("tried0")
    return pid
if me == "1":
    run.holder = holder
else:
    until("read1")
try:
    run.take(path)
    print("took", flush=True)
except run.Busy:
    print("busy", flush=True)
if me == "0":
    mark("tried0")
until("end")
"""


def test_2_processes_on_1_dead_lock_take_it_once(item):
    cfg, _, _ = item
    path = write_lock(cfg, dead_pid(), "a-dead-run")
    kids = [subprocess.Popen([sys.executable, "-c", CHILD, str(path), me], cwd=run.ROOT, stdout=subprocess.PIPE,
                             text=True) for me in ("0", "1")]
    try:
        said = [k.stdout.readline().strip() for k in kids]  # both have tried while the taker still holds it
    finally:
        (path.parent / "end").write_text("x")
        for k in kids:
            k.communicate(timeout=60)
    assert sorted(said) == ["busy", "took"]


# --- a run that stops with an error waits ---

def test_a_run_that_stops_with_an_error_waits(item, monkeypatch, capsys):
    cfg, personas, r = item

    def broken(*a):
        raise UsageError("the hidden error text")

    monkeypatch.setattr(machine, "recover", broken)
    assert main(["run-next", "7", "--foreground"], cfg=cfg, personas=personas) == 2
    assert "the hidden error text" in capsys.readouterr().err  # the error goes on as before
    st = state.load(cfg, "7")
    assert (st["status"], st["question"]["case"]) == ("waiting", 2) and r.calls == []
    log = str(run.log_path(cfg, "7"))
    assert st["question"]["text"] == f"The run stopped with UsageError; the log is {log}."
    (comment,) = tracker.make(cfg).get("7")["comments"]
    assert "UsageError" in comment["text"] and log in comment["text"] and "hidden" not in comment["text"]
    assert not run.lock_path(cfg, "7").exists()


def test_an_error_after_a_saved_wait_keeps_that_wait(item, monkeypatch):
    cfg, personas, _ = item

    def waits_then_fails(cfg, personas, item_id):
        st = state.load(cfg, item_id)
        machine.wait(cfg, tracker.make(cfg), st, 2, "the first question")
        state.save(cfg, st)
        raise UsageError("after the wait")

    monkeypatch.setattr(machine, "run_next", waits_then_fails)
    with pytest.raises(UsageError, match="after the wait"):
        run.run_locked(cfg, personas, "7")
    assert state.load(cfg, "7")["question"]["text"] == "the first question"


def test_a_failed_wait_does_not_mask_the_error(item, monkeypatch):
    cfg, personas, _ = item

    def no_disk(*a, **kw):
        raise OSError("the disk is full")

    def broken(*a):
        monkeypatch.setattr(state, "save", no_disk)  # the save of the wait is the first write of stopped
        raise UsageError("the first error")

    monkeypatch.setattr(machine, "recover", broken)
    with pytest.raises(UsageError, match="the first error") as e:
        run.run_locked(cfg, personas, "7")
    assert e.value.__notes__ == ["the wait after it failed too: OSError"]
    assert state.load(cfg, "7")["status"] == "running" and not run.lock_path(cfg, "7").exists()


def test_a_failed_save_of_the_stop_makes_no_tracker_change(item, monkeypatch):
    """Stopped saves the wait first; a failed save leaves the tracker as it was."""
    cfg, personas, _ = item
    file = state.item_dir(cfg, "7") / "tracker.json"
    seen = {}

    def no_disk(*a, **kw):
        raise OSError("the disk is full")

    def broken(*a):
        seen["tracker"] = file.read_bytes()
        monkeypatch.setattr(state, "save", no_disk)  # after the saves of run_next: the save of stopped fails
        raise UsageError("the first error")

    monkeypatch.setattr(machine, "recover", broken)
    with pytest.raises(UsageError, match="the first error") as e:
        run.run_locked(cfg, personas, "7")
    assert e.value.__notes__ == ["the wait after it failed too: OSError"]
    assert file.read_bytes() == seen["tracker"] and state.load(cfg, "7")["status"] == "running"


def test_a_failed_tracker_comment_after_the_save_keeps_the_wait(item, monkeypatch):
    """The wait is on disk before the tracker write that fails."""
    cfg, personas, _ = item

    def broken(*a):
        raise UsageError("the first error")

    def no_tracker(*a, **kw):
        raise OSError("the tracker is down")

    monkeypatch.setattr(machine, "recover", broken)
    monkeypatch.setattr(tracker.LocalTracker, "comment", no_tracker)
    with pytest.raises(UsageError, match="the first error") as e:
        run.run_locked(cfg, personas, "7")
    assert e.value.__notes__ == ["the wait after it failed too: OSError"]
    st = state.load(cfg, "7")
    assert (st["status"], st["question"]["case"]) == ("waiting", 2)


@pytest.mark.parametrize("content", ["", "{not json", "[]", '{"pid": "1"}', '{"token": "x"}'])
def test_a_broken_lock_is_replaced(item, content):
    cfg, personas, r = item
    run.lock_path(cfg, "7").write_text(content, encoding="utf-8")
    r.add("spec-checker", "SPEC-CHECK 7: QUESTIONS - 1 questions")
    assert run.run_locked(cfg, personas, "7")["status"] == "waiting"


# --- run-next makes the work folder ---

@pytest.fixture
def code_clone(tmp_path, monkeypatch):
    """A code clone whose origin is a bare repo with 1 commit on main, and a git identity."""
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    (seed / "README.md").write_text("base\n", encoding="utf-8")
    git(seed, "add", "README.md")
    git(seed, "commit", "-q", "-m", "base")
    bare = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(bare))
    clone = tmp_path / "code"
    git(tmp_path, "clone", "-q", str(bare), str(clone))
    git(clone, "config", "user.name", "Code Clone")
    git(clone, "config", "user.email", "clone@example.invalid")
    monkeypatch.setattr(run, "ROOT", clone)
    return clone, bare, git(seed, "rev-parse", "HEAD")


def test_run_next_makes_the_work_folder(tmp_path, monkeypatch, code_clone):
    _, bare, base = code_clone
    cfg, personas = make_cfg(tmp_path)
    assert intake.intake(cfg, str(SPEC), "7") == []
    r = Replies(monkeypatch, cfg).add("spec-checker", "SPEC-CHECK 7: QUESTIONS - 1 questions")
    st = run.run_locked(cfg, personas, "7")
    work = Path(cfg["work"]["home"]) / "7"
    assert st["status"] == "waiting" and r.roles() == ["spec-checker"]
    assert git(work, "rev-parse", "--abbrev-ref", "HEAD") == "feature/7"
    assert git(work, "rev-parse", "HEAD") == base == git(work, "rev-parse", "origin/main")
    assert git(work, "remote", "get-url", "origin") == str(bare)
    assert (git(work, "config", "--local", "user.name"), git(work, "config", "--local", "user.email")) == (
        "Code Clone", "clone@example.invalid")


def test_a_lost_work_folder_starts_from_the_branch_on_the_origin(tmp_path, monkeypatch, code_clone):
    """The push after a new clone is a fast-forward."""
    clone, bare, base = code_clone
    git(clone, "checkout", "-q", "-b", "feature/7")
    git(clone, "commit", "-q", "--allow-empty", "-m", "the build before the folder was lost")
    git(clone, "push", "-q", "origin", "feature/7")
    ahead = git(clone, "rev-parse", "HEAD")
    cfg, _ = make_cfg(tmp_path)
    assert intake.intake(cfg, str(SPEC), "7") == []
    run.make_work_folder(state.load(cfg, "7"))
    work = Path(cfg["work"]["home"]) / "7"
    assert git(work, "rev-parse", "HEAD") == ahead != base and git(work, "rev-parse", "origin/main") == base
    assert git(work, "rev-parse", "--abbrev-ref", "HEAD") == "feature/7"
    assert machine.guard.check_git(work) is None  # the upstream keys of the checkout are keys of a clone
    git(work, "commit", "-q", "--allow-empty", "-m", "a new commit")
    git(work, "push", "-q", str(bare), "feature/7")  # no force
    assert git(bare, "rev-parse", "refs/heads/feature/7") == git(work, "rev-parse", "HEAD")


def test_a_failed_clone_leaves_no_work_folder(tmp_path, monkeypatch, code_clone, capsys):
    clone, _, _ = code_clone
    git(clone, "remote", "set-url", "origin", str(tmp_path / "no-such-repo.git"))
    cfg, personas = make_cfg(tmp_path)
    assert intake.intake(cfg, str(SPEC), "7") == []
    assert main(["run-next", "7", "--foreground"], cfg=cfg, personas=personas) == 2
    assert "wb: git clone" in capsys.readouterr().err
    assert not (Path(cfg["work"]["home"]) / "7").exists()
    assert not run.lock_path(cfg, "7").exists()


def test_an_existing_work_folder_is_kept(item, code_clone):
    cfg, personas, r = item
    work = Path(cfg["work"]["home"]) / "7"
    head = machine.head(work)
    r.add("spec-checker", "SPEC-CHECK 7: QUESTIONS - 1 questions")
    run.run_locked(cfg, personas, "7")
    assert machine.head(work) == head and git(work, "remote", "get-url", "origin").startswith("https://")


# --- the work folder clones work.url of the item ---

@pytest.fixture
def other_repo(tmp_path):
    """A second bare repo with its own commit on main: (bare, base)."""
    seed = tmp_path / "seed-b"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    git(seed, "commit", "-q", "--allow-empty", "-m", "base of b")
    bare = tmp_path / "other.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(bare))
    return bare, git(seed, "rev-parse", "HEAD")


def with_url(cfg, url):
    """An item after intake whose work.url is url (a local bare repo: [repos] takes GitHub URLs only)."""
    assert intake.intake(cfg, str(SPEC), "7") == []
    st = state.load(cfg, "7")
    st["work"].update(repo="other", url=str(url))
    state.save(cfg, st)


def test_the_work_folder_clones_the_url_of_the_state(tmp_path, monkeypatch, code_clone, other_repo):
    bare, base = other_repo
    cfg, personas = make_cfg(tmp_path)
    with_url(cfg, bare)
    r = Replies(monkeypatch, cfg).add("spec-checker", "SPEC-CHECK 7: QUESTIONS - 1 questions")
    st = run.run_locked(cfg, personas, "7")
    work = Path(cfg["work"]["home"]) / "7"
    assert st["status"] == "waiting" and r.roles() == ["spec-checker"]
    assert git(work, "remote", "get-url", "origin") == str(bare) == st["remote"]["url"]
    assert git(work, "rev-parse", "HEAD") == base == st["remote"]["base_sha"]
    assert git(work, "rev-parse", "--abbrev-ref", "HEAD") == "feature/7"
    assert git(work, "config", "--local", "user.name") == "Code Clone"


def test_a_folder_with_another_origin_stops_before_any_step(tmp_path, monkeypatch, code_clone, other_repo, capsys):
    _, a, _ = code_clone
    b, _ = other_repo
    cfg, personas = make_cfg(tmp_path)
    with_url(cfg, b)
    work = Path(cfg["work"]["home"]) / "7"
    git(tmp_path, "clone", "-q", str(a), str(work))
    r = Replies(monkeypatch, cfg)
    assert main(["run-next", "7", "--foreground"], cfg=cfg, personas=personas) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"wb: the work folder {work} has the origin {a}, not the repository of the item {b}")
    st = state.load(cfg, "7")
    assert r.calls == [] and st["steps"] == [] and st["open_step"] is None and "remote" not in st
    assert not run.lock_path(cfg, "7").exists()


def test_a_folder_with_the_origin_of_the_state_is_kept(tmp_path, monkeypatch, code_clone, other_repo):
    b, _ = other_repo
    cfg, personas = make_cfg(tmp_path)
    with_url(cfg, b)
    work = Path(cfg["work"]["home"]) / "7"
    git(tmp_path, "clone", "-q", str(b), str(work))
    git(work, "checkout", "-q", "-b", "feature/7")
    r = Replies(monkeypatch, cfg).add("spec-checker", "SPEC-CHECK 7: QUESTIONS - 1 questions")
    assert run.run_locked(cfg, personas, "7")["steps"][0]["role"] == "spec-checker" and r.roles() == ["spec-checker"]


def test_the_origin_check_ignores_an_insteadof_rewrite_of_the_host(tmp_path, monkeypatch, code_clone, other_repo):
    """Code review: get-url gives the rewrite, so a folder cloned from work.url would never match."""
    b, _ = other_repo
    cfg, personas = make_cfg(tmp_path)
    with_url(cfg, b)
    work = Path(cfg["work"]["home"]) / "7"
    git(tmp_path, "clone", "-q", str(b), str(work))
    git(work, "checkout", "-q", "-b", "feature/7")
    git(work, "config", f"url.{b}.insteadOf", "rewritten:")
    git(work, "remote", "set-url", "origin", "rewritten:")
    with pytest.raises(UsageError, match="has the origin rewritten:, not"):
        run.make_work_folder(state.load(cfg, "7"))
    git(work, "remote", "set-url", "origin", str(b))
    git(work, "config", f"url.{tmp_path / 'elsewhere.git'}.insteadOf", str(b))
    run.make_work_folder(state.load(cfg, "7"))  # the configured origin is work.url


def test_an_item_that_does_not_run_starts_no_child(item, spawned, capsys):
    cfg, personas, r = item
    st = state.load(cfg, "7")
    st.update(status="ready", stage="delivery")
    state.save(cfg, st)
    assert main(["run-next", "7"], cfg=cfg, personas=personas) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "WB: READY 7"
    assert spawned == []


def test_a_step_that_does_not_start_leaves_no_open_step(item, tmp_path):
    cfg, personas, _ = item
    cfg["runners"]["fake-spec-checker"]["script"] = str(tmp_path / "no-such-reply.json")
    with pytest.raises(UsageError, match="reply script does not exist"):
        run.run_locked(cfg, personas, "7")
    st = state.load(cfg, "7")
    assert st["open_step"] is None and st["steps"] == []
