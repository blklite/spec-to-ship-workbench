"""wb run-next runs steps until the item waits or is ready;
1 fake-adapter test for each transition, and 1 for the round limit."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import runner_fakes

from runner import UsageError
from wb import machine, state, tracker
from wb.cli import main
from wb_fakes import GOOD_SPEC_TEXT, HEAD, make_cfg, start

GO = "Notes.\n\nSPEC-CHECK 7: GO"
QUESTIONS = "Notes.\n\nSPEC-CHECK 7: QUESTIONS - 2 questions"
READY = "Built.\n\nBUILD: READY " + HEAD
CLOSE = "Review.\n\nREVIEW: CLOSE"
REWORK = "Review.\n\nREVIEW: REWORK - 2 blocking"
CR_CLEAN = "No findings.\n\nCODE-REVIEW: 0 findings (0 critical, 0 high, 0 medium, 0 low)"
DONE = "Repaired.\n\nREWORK: DONE"


@pytest.fixture
def item(tmp_path, monkeypatch):
    return start(tmp_path, monkeypatch)


def run(item):
    cfg, personas, _ = item
    return machine.run_next(cfg, personas, "7")


def happy(r):
    """The good reply for each role whose queue a test did not fill."""
    for role, text in (("spec-checker", GO), ("builder", READY), ("reviewer", CLOSE), ("second-reviewer", CR_CLEAN)):
        if not r.queues[role]:
            r.add(role, text)
    return r


def test_go_build_ready_review_close_is_ready(item):
    cfg, personas, r = item
    st = run((cfg, personas, happy(r)))
    assert (st["stage"], st["status"]) == ("delivery", "ready")
    assert r.roles() == ["spec-checker", "builder", "reviewer", "second-reviewer"]
    assert [s["runner"] for s in st["steps"]] == [f"fake-{x}" for x in r.roles()]  # from the role map
    assert [s["status"] for s in st["steps"]] == ["OK"] * 4
    assert st["rounds_used"] == 0 and st["not_verified"] == []
    assert state.load(cfg, "7") == st


def test_spec_check_questions_waits(item):
    cfg, _, r = item
    r.add("spec-checker", QUESTIONS).add("builder", READY)
    st = run(item)
    assert (st["stage"], st["status"]) == ("spec-check", "waiting")
    assert st["question"]["case"] == 2 and st["question"]["spec_questions"]
    assert r.roles() == ["spec-checker"]
    assert tracker.make(cfg).get("7")["state"] == "Waiting for Owner"


def test_build_blocked_waits(item):
    _, _, r = item
    r.add("spec-checker", GO).add("builder", "BUILD: BLOCKED - the spec names a missing file")
    st = run(item)
    assert (st["stage"], st["status"], st["question"]["case"]) == ("build", "waiting", 2)
    assert "the spec names a missing file" in st["question"]["text"]
    assert r.roles() == ["spec-checker", "builder"]


def test_review_rework_goes_to_rework_and_done_counts_1_round(item):
    _, _, r = item
    happy(r.add("reviewer", REWORK, CLOSE).add("rework", DONE))
    st = run(item)
    assert (st["stage"], st["status"]) == ("delivery", "ready")
    assert r.roles() == ["spec-checker", "builder", "reviewer", "second-reviewer", "rework",
                         "reviewer", "second-reviewer"]
    assert st["rounds_used"] == 1


def test_second_review_high_findings_are_blocking(item):
    _, _, r = item
    happy(r.add("second-reviewer", "CODE-REVIEW: 3 findings (0 critical, 1 high, 2 medium, 0 low)", CR_CLEAN))
    r.add("rework", DONE)
    st = run(item)
    assert st["status"] == "ready"
    assert r.roles()[4:] == ["rework", "reviewer", "second-reviewer"]


def test_second_review_medium_and_low_findings_do_not_block(item):
    _, _, r = item
    happy(r.add("second-reviewer", "CODE-REVIEW: 3 findings (0 critical, 0 high, 2 medium, 1 low)"))
    st = run(item)
    assert st["status"] == "ready" and r.roles()[-1] == "second-reviewer"


def test_rework_blocked_waits(item):
    _, _, r = item
    happy(r.add("reviewer", REWORK).add("rework", "REWORK: BLOCKED - a test must change"))
    st = run(item)
    assert (st["stage"], st["status"], st["question"]["case"]) == ("rework", "waiting", 2)
    assert st["rounds_used"] == 0


def test_the_round_limit_waits_with_case_4(item):
    _, _, r = item
    happy(r.add("reviewer", REWORK).add("rework", DONE))
    st = run(item)
    assert st["limits"]["rounds"] == 2
    assert r.roles() == ["spec-checker", "builder"] + ["reviewer", "second-reviewer", "rework"] * 2 + [
        "reviewer", "second-reviewer"]
    assert (st["stage"], st["status"], st["question"]["case"], st["rounds_used"]) == ("rework", "waiting", 4, 2)


def test_a_step_that_is_not_ok_runs_once_more(item):
    _, _, r = item
    happy(r.add("builder", "no last line", READY))
    st = run(item)
    assert st["status"] == "ready"
    assert [s["status"] for s in st["steps"]][:3] == ["OK", "INVALID", "OK"]


def test_a_step_that_fails_twice_waits_with_its_reason(item):
    _, _, r = item
    happy(r.add("builder", "no last line", "still none", READY))
    st = run(item)
    assert (st["stage"], st["status"], st["question"]["case"]) == ("build", "waiting", 2)
    assert "INVALID twice" in st["question"]["text"] and "still none" in st["question"]["text"]
    assert r.roles() == ["spec-checker", "builder", "builder"]
    assert st["retried"] is False


def test_a_last_line_of_another_item_is_not_ok(item):
    _, _, r = item
    r.add("spec-checker", "SPEC-CHECK 8: GO")
    st = run(item)
    assert st["status"] == "waiting" and r.roles() == ["spec-checker", "spec-checker"]
    assert "does not fit item 7" in st["question"]["text"]


def test_rework_with_0_blocking_is_not_a_pass(item):
    _, _, r = item
    happy(r.add("reviewer", "REVIEW: REWORK - 0 blocking"))
    st = run(item)
    assert st["status"] == "waiting" and r.roles()[2:] == ["reviewer", "reviewer"]


def test_second_reviewer_unavailable_is_not_verified_and_does_not_stop(item):
    cfg, _, r = item
    cfg["runners"]["fake-second-reviewer"]["command"] = "no-such-cli-for-the-workbench"
    st = run((cfg, item[1], happy(r)))
    assert st["status"] == "ready"
    assert r.roles() == ["spec-checker", "builder", "reviewer", "second-reviewer"]  # no second try
    assert st["steps"][-1]["status"] == "UNAVAILABLE" and st["steps"][-1]["outcome"] is None
    assert len(st["not_verified"]) == 1 and "second review did not run" in st["not_verified"][0]


def test_second_reviewer_unavailable_keeps_the_reviewer_findings(item):
    cfg, _, r = item
    cfg["runners"]["fake-second-reviewer"]["command"] = "no-such-cli-for-the-workbench"
    happy(r.add("reviewer", REWORK, CLOSE).add("rework", DONE))
    st = run(item)
    assert st["status"] == "ready" and st["rounds_used"] == 1
    assert len(st["not_verified"]) == 2


def test_low_tier_has_no_second_review(tmp_path, monkeypatch):
    cfg, personas, r = start(tmp_path, monkeypatch, spec_text=GOOD_SPEC_TEXT.replace(
        "**Tier:** Medium", "**Tier:** Low"))
    happy(r)
    st = machine.run_next(cfg, personas, "7")
    assert st["status"] == "ready" and "second-reviewer" not in r.roles()


def test_head_before_and_after_are_recorded(item):
    cfg, _, r = item
    head = machine.head(cfg["work"]["home"] + "/7")
    st = run((cfg, item[1], happy(r)))
    assert len(head) == 40
    assert all((s["head_before"], s["head_after"]) == (head, head) for s in st["steps"])


def test_a_waiting_or_ready_item_runs_no_step(item):
    _, _, r = item
    happy(r)
    run(item)
    n = len(r.calls)
    assert run(item)["status"] == "ready" and len(r.calls) == n


def test_the_work_folder_must_be_a_git_work_tree(tmp_path):
    from wb import intake
    from wb_fakes import SPEC

    cfg, personas = make_cfg(tmp_path)
    assert intake.intake(cfg, str(SPEC), "7") == []
    with pytest.raises(UsageError, match="git work tree"):
        machine.run_next(cfg, personas, "7")
    (tmp_path / "work" / "7").mkdir(parents=True)  # a plain folder inside no work tree of its own
    with pytest.raises(UsageError, match="git work tree"):
        machine.run_next(cfg, personas, "7")


def test_cli_run_next_prints_the_result(item, capsys):
    cfg, personas, r = item
    r.add("spec-checker", QUESTIONS)
    assert main(["run-next", "7", "--foreground"], cfg=cfg, personas=personas) == 0
    assert capsys.readouterr().out.splitlines()[-1].startswith("WB: WAITING 7 - case 2: The spec check asks")
    st = state.load(cfg, "7")
    st.update(status="ready", stage="delivery")
    state.save(cfg, st)
    assert main(["run-next", "7", "--foreground"], cfg=cfg, personas=personas) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "WB: READY 7"


# --- the runner deletes the review token of a step that the wrapper could not end ---

class Died(Exception):
    """The host dies in the middle of a step."""


posix = pytest.mark.skipif(os.name != "posix", reason="a symlink can need a privilege on Windows")
no_bits = pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="Windows and root ignore the mode of a folder")
TOKEN_CASES = ["file", "no file", "folder, no file", "token folder", pytest.param("workbench link", marks=posix),
               pytest.param("config link", marks=posix), pytest.param("no bits", marks=no_bits),
               pytest.param("home no bits", marks=no_bits), pytest.param("home link", marks=posix),
               pytest.param("token link", marks=posix)]


def put_token(st: dict, case: str, victim: Path) -> Path:
    """What a step leaves in its home: the review token file, nothing, its folders with no file (the wrapper
    deleted it), a folder at the path of the file, a symlink to a folder outside the step home that holds a
    review-token, the file in folders with no bits, a step home that is a symlink to an outside home with the
    file, or a review-token that is a symlink to an outside folder. Returns the path of the file."""
    config = machine.step_dir(st) / "home" / ".config"
    rf = config / "workbench" / "review-token"
    if case in ("file", "no bits", "home no bits"):
        rf.parent.mkdir(parents=True, exist_ok=True)
        rf.write_text("x\n", encoding="utf-8")
    if case == "folder, no file":
        rf.parent.mkdir(parents=True, exist_ok=True)
    elif case == "token folder":
        rf.mkdir(parents=True)
    elif case == "no bits":  # a step that takes the bits of its folders away
        rf.parent.chmod(0o500)
        config.chmod(0)
    elif case == "home no bits":  # chmod 000 "$HOME"
        config.parent.chmod(0)
    elif case == "workbench link":
        config.mkdir(parents=True, exist_ok=True)
        os.symlink(victim, rf.parent)
    elif case == "config link":
        os.symlink(victim.parent, config)
    elif case == "home link":
        outside = outside_home(victim) / ".config" / "workbench"
        outside.mkdir(parents=True, exist_ok=True)
        (outside / "review-token").write_text("keep\n", encoding="utf-8")
        shutil.rmtree(config.parent)  # run_one made the step home
        os.symlink(outside_home(victim), config.parent)
    elif case == "token link":
        rf.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(victim, rf)
    return rf


def outside_home(victim: Path) -> Path:
    return victim.parent / "home"


def victim_of(tmp_path: Path) -> Path:
    victim = tmp_path / "outside" / "workbench"
    victim.mkdir(parents=True)
    (victim / "review-token").write_text("keep\n", encoding="utf-8")
    return victim


def check_token(case: str, rf: Path, victim: Path) -> None:
    if case in ("file", "no file", "folder, no file", "no bits", "home no bits"):
        assert not rf.exists()
    elif case == "token folder":  # left for the next step, which deletes its whole step folder
        assert rf.is_dir()
    elif case == "home link":
        rf = outside_home(victim) / ".config" / "workbench" / "review-token"
        assert rf.read_text(encoding="utf-8") == "keep\n"
    elif case == "token link":  # the link goes, and its target stays
        assert not rf.is_symlink() and not rf.exists()
        assert (victim / "review-token").read_text(encoding="utf-8") == "keep\n"
    else:  # the delete follows no symlink
        assert (victim / "review-token").read_text(encoding="utf-8") == "keep\n"


LINKS = {"home link": ("home",), "config link": ("home", ".config"), "workbench link": ("home", ".config", "workbench")}


def check_link_noted(st: dict, case: str, steps: list[int]) -> None:
    """A link case: 1 line for each step that names the linked folder and no token value. Other cases: none."""
    lines = [x for x in st["not_verified"] if ": the review token was not deleted: " in x]
    assert len(lines) == (len(steps) if case in LINKS else 0), st["not_verified"]
    for n, line in zip(steps, lines):
        folder = machine.step_dir(st).joinpath(*LINKS[case])
        assert line.startswith(f"step {n}: ") and f"{folder} is a symlink" in line and "keep" not in line, line


def wrapped(cfg: dict, monkeypatch, tmp_path: Path) -> None:
    cfg["runners"]["fake-builder"]["wrap"] = [runner_fakes.install_wrap(monkeypatch, tmp_path)]


@pytest.mark.parametrize("case", TOKEN_CASES)
def test_the_runner_deletes_the_review_token_after_a_timeout(item, tmp_path, monkeypatch, case):
    """A timeout kills the wrapper, which then cannot delete the file."""
    cfg, personas, r = item
    wrapped(cfg, monkeypatch, tmp_path)
    victim = victim_of(tmp_path)
    inner, files = machine.run_step, []

    def step(folder, prompt, timeout, runner, out, *, cfg=None):
        c = inner(folder, prompt, timeout, runner, out, cfg=cfg)
        if runner != "fake-builder":
            return c
        files.append(put_token(state.load(cfg, "7"), case, victim))
        return {**c, "status": "TIMEOUT", "reason": "stopped at the time limit"}

    monkeypatch.setattr(machine, "run_step", step)
    st = run((cfg, personas, r.add("spec-checker", GO).add("builder", READY).add("builder", READY)))
    assert [(s["role"], s["status"]) for s in st["steps"]] == [("spec-checker", "OK"), ("builder", "TIMEOUT"),
                                                               ("builder", "TIMEOUT")]
    check_token(case, files[-1], victim)
    check_link_noted(st, case, [2, 3])


@pytest.mark.parametrize("case", TOKEN_CASES)
def test_the_runner_deletes_the_review_token_of_an_interrupted_step(item, tmp_path, monkeypatch, case):
    """A reboot kills the wrapper: run-next ends the open step and deletes the file."""
    cfg, personas, r = item
    wrapped(cfg, monkeypatch, tmp_path)
    victim = victim_of(tmp_path)
    inner = machine.run_step

    def step(folder, prompt, timeout, runner, out, *, cfg=None):
        if runner != "fake-builder":
            return inner(folder, prompt, timeout, runner, out, cfg=cfg)
        raise Died()

    monkeypatch.setattr(machine, "run_step", step)
    with pytest.raises(Died):
        run((cfg, personas, r.add("spec-checker", GO)))
    st = state.load(cfg, "7")
    assert st["open_step"]["role"] == "builder"
    rf = put_token(st, case, victim)  # after the death: a reboot runs no finally of run_one
    machine.recover(cfg, tracker.make(cfg), st)
    assert st["open_step"] is None and st["steps"][-1]["reason"] == "interrupted"
    check_token(case, rf, victim)
    check_link_noted(state.load(cfg, "7"), case, [2])


def test_a_refused_recovery_still_deletes_the_review_token(item, tmp_path, monkeypatch):
    """The file goes before the checks of the work folder, which can stop run-next."""
    cfg, personas, r = item
    st = state.load(cfg, "7")
    st["open_step"] = {"n": 1, "stage": "spec-check", "role": "spec-checker", "runner": "fake-spec-checker",
                       "run_folder": str(tmp_path / "run"), "head_before": HEAD, "start": "x"}
    rf = put_token(st, "file", tmp_path)
    monkeypatch.setattr(machine.guard, "check_git", lambda folder: "an unsafe .git")
    with pytest.raises(UsageError):
        machine.recover(cfg, tracker.make(cfg), st)
    assert not rf.exists()


# --- a step that locks its folders cannot stop a run ---

def old_home(item, tmp_path, monkeypatch) -> Path:
    """The step home that an earlier wrapped step left, before the next wrapped step."""
    cfg, personas, r = item
    wrapped(cfg, monkeypatch, tmp_path)
    home = machine.step_dir(state.load(cfg, "7")) / "home"
    home.mkdir(parents=True)
    return home


def next_step(item) -> dict:
    cfg, personas, r = item
    st = run((cfg, personas, r.add("spec-checker", GO).add("builder", "BUILD: BLOCKED - stop here")))
    assert [s["role"] for s in st["steps"]] == ["spec-checker", "builder"]
    return st


@no_bits
def test_a_mode_0_folder_two_levels_below_the_step_home_does_not_stop_the_next_step(item, tmp_path, monkeypatch):
    home = old_home(item, tmp_path, monkeypatch)
    locked = home / "a" / "b"
    (locked / "c").mkdir(parents=True)
    (locked / "c" / "f.txt").write_text("x", encoding="utf-8")
    locked.chmod(0)
    next_step(item)
    assert list(home.iterdir()) == []


@no_bits
def test_deep_locked_folders_and_folders_with_no_x_bit_do_not_stop_the_next_step(item, tmp_path, monkeypatch):
    """400 nested folders of mode 0 (a recursion in the callback hit the limit), and folders of mode 400."""
    home = old_home(item, tmp_path, monkeypatch)
    chain = [home / "deep"]
    for _ in range(399):
        chain.append(chain[-1] / "d")
    chain[-1].mkdir(parents=True)
    for name in ("no-x", "no-x-with-a-folder"):
        (home / name / "sub").mkdir(parents=True)
    (home / "no-x" / "sub").rmdir()
    for folder in [*reversed(chain), home / "no-x", home / "no-x-with-a-folder"]:
        folder.chmod(0 if folder in chain else 0o400)
    next_step(item)
    assert list(home.iterdir()) == []


@no_bits
def test_the_step_folder_delete_follows_no_symlink(item, tmp_path, monkeypatch):
    """Links in a folder of mode 500 and in a folder of mode 0, so that the delete of each link needs the fix."""
    home = old_home(item, tmp_path, monkeypatch)
    targets = {}
    for mode in (0o700, 0o500):
        t = tmp_path / f"outside-{mode:o}"
        t.mkdir()
        (t / "keep.txt").write_text("keep\n", encoding="utf-8")
        t.chmod(mode)
        targets[t] = mode
    for name, mode in (("read-only", 0o500), ("no-bits", 0)):
        folder = home / name
        folder.mkdir()
        for t in targets:
            os.symlink(t, folder / t.name)
        folder.chmod(mode)
    next_step(item)
    assert list(home.iterdir()) == []
    for t, mode in targets.items():
        assert (t.stat().st_mode & 0o777, (t / "keep.txt").read_text(encoding="utf-8")) == (mode, "keep\n")


@no_bits
def test_a_locked_folder_deeper_than_path_max_does_not_stop_the_next_step(item, tmp_path, monkeypatch):
    """20 levels of 250 characters: the full path is longer than PATH_MAX, so the tree is made with dir_fd."""
    home = old_home(item, tmp_path, monkeypatch)
    target = tmp_path / "outside"
    target.mkdir()
    target.chmod(0o700)
    fds = [os.open(home, os.O_RDONLY | os.O_DIRECTORY)]
    try:
        for _ in range(20):
            os.mkdir("d" * 250, dir_fd=fds[-1])
            fds.append(os.open("d" * 250, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fds[-1]))
        os.mkdir("locked", dir_fd=fds[-1])
        os.symlink(target, "link", dir_fd=fds[-1])
        os.chmod("locked", 0, dir_fd=fds[-1])
    finally:
        for fd in fds:
            os.close(fd)
    next_step(item)
    assert list(home.iterdir()) == [] and target.stat().st_mode & 0o777 == 0o700


def test_a_step_folder_that_stays_stops_the_step_with_its_path(item, tmp_path, monkeypatch):
    cfg, personas, r = item
    home = old_home(item, tmp_path, monkeypatch)
    monkeypatch.setattr(machine.shutil, "rmtree", lambda *a, **k: None)
    monkeypatch.setattr(machine.subprocess, "run",
                        lambda args, **k: subprocess.CompletedProcess(args, 1, "", "Permission denied"))
    with pytest.raises(UsageError) as e:
        machine.clear_step_dir(home.parent)
    assert str(home.parent) in str(e.value)
    if os.name == "posix":  # the stderr of chmod and rm: the stub of rmtree on Windows gives no text
        assert "Permission denied" in str(e.value)


def test_an_oserror_of_the_non_posix_delete_gives_the_usual_error(tmp_path, monkeypatch):
    folder = tmp_path / "x.step"
    folder.mkdir()

    def refused(path, **kwargs):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(machine.shutil, "rmtree", refused)
    with pytest.raises(UsageError) as e, monkeypatch.context() as m:
        m.setattr(machine.os, "name", "nt")
        machine.clear_step_dir(folder)
    assert f"cannot delete the step folder {folder}." in str(e.value) and "[Errno 13] Permission denied" in str(e.value)


@posix
@pytest.mark.parametrize("tool", ["chmod", "rm"])
def test_a_missing_tool_of_the_delete_gives_the_usual_error(tmp_path, monkeypatch, tool):
    """No chmod: rm still deletes the folder, and nothing is raised. No rm: the folder stays."""
    folder = tmp_path / "x.step"
    (folder / "home").mkdir(parents=True)
    inner = subprocess.run

    def run(args, **kwargs):
        if args[0] == tool:
            raise FileNotFoundError(2, "No such file or directory", tool)
        return inner(args, **kwargs)

    monkeypatch.setattr(machine.subprocess, "run", run)
    if tool == "chmod":
        machine.clear_step_dir(folder)
        assert not os.path.lexists(folder)
        return
    with pytest.raises(UsageError) as e:
        machine.clear_step_dir(folder)
    assert f"cannot delete the step folder {folder}." in str(e.value)
    assert "[Errno 2] No such file or directory: 'rm'" in str(e.value)



@no_bits
def test_the_callback_of_the_delete_does_not_chmod_through_a_symlink(tmp_path):
    """A link to a folder that rmtree could not delete goes, and its target keeps its mode."""
    target = tmp_path / "outside"
    target.mkdir()
    target.chmod(0o500)
    os.symlink(target, tmp_path / "link")
    machine._writable(os.unlink, str(tmp_path / "link"), None)
    assert not (tmp_path / "link").is_symlink() and target.stat().st_mode & 0o777 == 0o500


@no_bits
def test_the_callback_removes_a_locked_folder_and_follows_no_link(tmp_path):
    """A mode 0 folder that holds a file, a nested mode 0 folder with a file, and a link to an outside folder
    of mode 500. The callback removes a folder whatever the function that failed on it, here os.open."""
    target = tmp_path / "outside"
    target.mkdir()
    (target / "keep.txt").write_text("keep\n", encoding="utf-8")
    target.chmod(0o500)
    locked = tmp_path / "locked"
    (locked / "nested").mkdir(parents=True)
    (locked / "f.txt").write_text("x", encoding="utf-8")
    (locked / "nested" / "f.txt").write_text("x", encoding="utf-8")
    os.symlink(target, locked / "link")
    (locked / "nested").chmod(0)
    locked.chmod(0)
    machine._writable(os.open, str(locked), None)
    assert not os.path.lexists(locked)
    assert (target.stat().st_mode & 0o777, (target / "keep.txt").read_text(encoding="utf-8")) == (0o500, "keep\n")


def test_the_callback_returns_when_the_path_is_gone(tmp_path):
    """The outer rmtree can call os.rmdir on a folder that the callback already removed."""
    machine._writable(os.rmdir, str(tmp_path / "gone"), None)


def refuse_token_delete(monkeypatch, st: dict, how: str) -> str:
    """A review token file with a value made at run time, and a Path.unlink or Path.chmod that raises
    PermissionError in the step folder; returns the value."""
    value = "wbreview" + os.urandom(8).hex()
    rf = put_token(st, "file", Path())
    rf.write_text(value + "\n", encoding="utf-8")
    inner, root = getattr(Path, how), machine.step_dir(st)

    def refused(self, *args, **kwargs):
        if self == root or root in self.parents:
            raise PermissionError(13, "Permission denied", str(self))
        return inner(self, *args, **kwargs)

    monkeypatch.setattr(Path, how, refused)
    return value


def check_noted(cfg: dict, n: int, value: str) -> dict:
    """The saved state: no open step, and 1 line for step n that names no token value."""
    st = state.load(cfg, "7")
    lines = [x for x in st["not_verified"] if x.startswith(f"step {n}: the review token was not deleted: ")]
    assert st["open_step"] is None and len(lines) == 1, st["not_verified"]
    assert all(value not in x for x in st["not_verified"])
    return st


@pytest.mark.parametrize("how", ["unlink", "chmod"])
def test_a_refused_token_delete_after_a_step_is_noted_and_the_step_is_recorded(item, tmp_path, monkeypatch, how):
    cfg, personas, r = item
    wrapped(cfg, monkeypatch, tmp_path)
    inner, values = machine.run_step, []

    def step(folder, prompt, timeout, runner, out, *, cfg=None):
        c = inner(folder, prompt, timeout, runner, out, cfg=cfg)
        if runner == "fake-builder":
            values.append(refuse_token_delete(monkeypatch, state.load(cfg, "7"), how))
        return c

    monkeypatch.setattr(machine, "run_step", step)
    st = run((cfg, personas, r.add("spec-checker", GO).add("builder", "BUILD: BLOCKED - stop here")))
    assert [(s["role"], s["status"]) for s in st["steps"]] == [("spec-checker", "OK"), ("builder", "OK")]
    assert check_noted(cfg, 2, values[0])["steps"][-1]["outcome"] == st["steps"][-1]["outcome"]


@pytest.mark.parametrize("how", ["unlink", "chmod"])
def test_a_refused_token_delete_in_the_recovery_is_noted_and_the_step_ends(item, tmp_path, monkeypatch, how):
    cfg, personas, r = item
    wrapped(cfg, monkeypatch, tmp_path)
    inner = machine.run_step

    def step(folder, prompt, timeout, runner, out, *, cfg=None):
        if runner != "fake-builder":
            return inner(folder, prompt, timeout, runner, out, cfg=cfg)
        raise Died()

    monkeypatch.setattr(machine, "run_step", step)
    with pytest.raises(Died):
        run((cfg, personas, r.add("spec-checker", GO)))
    st = state.load(cfg, "7")
    value = refuse_token_delete(monkeypatch, st, how)
    machine.recover(cfg, tracker.make(cfg), st)
    assert check_noted(cfg, 2, value)["steps"][-1]["reason"] == "interrupted"


@pytest.mark.parametrize("how", ["unlink", "chmod"])
def test_a_refused_token_delete_keeps_the_usage_error_of_the_step(item, tmp_path, monkeypatch, how):
    cfg, personas, r = item
    wrapped(cfg, monkeypatch, tmp_path)
    inner, values = machine.run_step, []

    def step(folder, prompt, timeout, runner, out, *, cfg=None):
        if runner != "fake-builder":
            return inner(folder, prompt, timeout, runner, out, cfg=cfg)
        values.append(refuse_token_delete(monkeypatch, state.load(cfg, "7"), how))
        raise UsageError("the runner did not start")

    monkeypatch.setattr(machine, "run_step", step)
    with pytest.raises(UsageError, match="the runner did not start"):
        run((cfg, personas, r.add("spec-checker", GO)))
    check_noted(cfg, 2, values[0])


def test_a_refused_token_delete_in_a_refused_recovery_is_saved_once(item, tmp_path, monkeypatch):
    """The check of the work folder stops run-next after the note: the saved state holds the line, 1 time
    after 2 run-nexts."""
    cfg, personas, r = item
    st = state.load(cfg, "7")
    st["open_step"] = {"n": 1, "stage": "spec-check", "role": "spec-checker", "runner": "fake-spec-checker",
                       "run_folder": str(tmp_path / "run"), "head_before": HEAD, "start": "x"}
    state.save(cfg, st)
    value = refuse_token_delete(monkeypatch, st, "unlink")
    monkeypatch.setattr(machine.guard, "check_git", lambda folder: "an unsafe .git")
    for _ in range(2):
        with pytest.raises(UsageError):
            machine.recover(cfg, tracker.make(cfg), state.load(cfg, "7"))
    lines = [x for x in state.load(cfg, "7")["not_verified"] if value not in x
             and x.startswith("step 1: the review token was not deleted: ")]
    assert len(lines) == 1, state.load(cfg, "7")["not_verified"]


def test_the_note_of_a_refused_token_delete_is_added_once(item, monkeypatch):
    cfg, personas, r = item
    st = state.load(cfg, "7")
    refuse_token_delete(monkeypatch, st, "chmod")
    machine.note_review_token(st, 3)
    machine.note_review_token(st, 3)
    assert len([x for x in st["not_verified"] if x.startswith("step 3: the review token")]) == 1, st["not_verified"]
