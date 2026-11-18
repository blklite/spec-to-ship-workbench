"""The secret guard. Before a guarded step and before each
push, the diff of the work folder against the base (untracked files too), the
commit messages since the base and the pull request text go to
scripts/secret-scan.sh --message with WORKBENCH_DENYLIST unset. A hit makes the
item wait (case 2) with the class and the place of each hit, never the matched
text; any other exit waits too. A fake runner has no guard unless it says so.

Each secret-like value is built at run time from pieces (AGENTS.md rule 6).
"""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
from pathlib import Path

import pytest

from wb import guard, machine, push, state, tracker
from wb_fakes import HEAD, git, start

GO = "SPEC-CHECK 7: GO"
READY = "BUILD: READY " + HEAD
CLOSE = "REVIEW: CLOSE"
CR_CLEAN = "CODE-REVIEW: 0 findings (0 critical, 0 high, 0 medium, 0 low)"
SECRET = "gh" + "p_" + "Q7" * 18  # the shape of a GitHub token: class github-token


@pytest.fixture
def item(tmp_path, monkeypatch, bash):
    monkeypatch.setenv("WORKBENCH_BASH", bash)
    cfg, personas, r = start(tmp_path, monkeypatch)
    return cfg, personas, r, Path(cfg["work"]["home"]) / "7"


@pytest.fixture
def scans(monkeypatch):
    calls = []
    real = guard.scan

    def spy(st, item_dir, extra=()):
        calls.append(list(extra))
        return real(st, item_dir, extra)

    monkeypatch.setattr(guard, "scan", spy)
    return calls


def comments(cfg) -> list[str]:
    return [c["text"] for c in tracker.make(cfg).get("7")["comments"]]


def test_a_planted_pattern_stops_the_item_before_a_guarded_step(item):
    cfg, personas, r, work = item
    cfg["runners"]["fake-builder"]["guard"] = True
    r.add("spec-checker", GO).add("builder", READY)
    (work / "notes.txt").write_text("line one\nkey = " + SECRET + "\n", encoding="utf-8")  # untracked
    st = machine.run_next(cfg, personas, "7")
    assert (st["stage"], st["status"], st["question"]["case"]) == ("build", "waiting", 2)
    assert r.roles() == ["spec-checker"]  # the build step did not run
    text = st["question"]["text"]
    assert text.startswith("Before the builder step, the secret guard found 1 hits: notes.txt:2 github-token")
    assert all(SECRET not in c for c in comments(cfg)) and SECRET not in text
    assert any("notes.txt:2 github-token" in c for c in comments(cfg))
    scan_file = Path(cfg["state"]["home"]) / "7" / "guard.txt"
    assert SECRET in scan_file.read_text(encoding="utf-8") and work not in scan_file.parents


def test_a_hit_in_a_commit_message_or_a_committed_file_stops_the_push(item):
    cfg, personas, r, work = item
    (work / "a.txt").write_text("ok\n" + SECRET + "\n", encoding="utf-8")
    git(work, "add", "a.txt")
    git(work, "commit", "-q", "-m", "add " + SECRET)
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert (st["stage"], st["status"]) == ("build", "waiting")
    text = st["question"]["text"]
    assert text.startswith("Before the push, the secret guard found 3 hits: a.txt:2 github-token;"
                           " a commit, a.txt:2 github-token; a commit message github-token")
    assert SECRET not in text and r.pushes == []


def test_a_hit_in_the_pull_request_text_stops_the_push(item, monkeypatch):
    cfg, personas, r, _ = item
    monkeypatch.setattr(push, "pr_text", lambda st: ("A title", "body " + SECRET))
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert st["status"] == "waiting" and "the pull request title or body github-token" in st["question"]["text"]
    assert r.pushes == []


def test_a_fake_runner_skips_the_scan_before_its_step(item, scans):
    cfg, personas, r, _ = item
    r.add("spec-checker", GO).add("builder", READY).add("reviewer", CLOSE).add("second-reviewer", CR_CLEAN)
    st = machine.run_next(cfg, personas, "7")
    assert st["status"] == "ready" and len(r.pushes) == 1
    assert len(scans) == 1 and scans[0] == list(push.pr_text(st))  # the push only: no guarded step


def test_a_guarded_fake_runner_scans_before_each_of_its_steps(item, scans):
    cfg, personas, r, _ = item
    for role in ("spec-checker", "reviewer"):
        cfg["runners"][f"fake-{role}"]["guard"] = True
    r.add("spec-checker", GO).add("builder", READY).add("reviewer", CLOSE).add("second-reviewer", CR_CLEAN)
    assert machine.run_next(cfg, personas, "7")["status"] == "ready"
    assert [bool(x) for x in scans] == [False, True, False]  # spec check, push, review


def test_the_scan_runs_with_no_private_list(item, tmp_path, monkeypatch):
    """WORKBENCH_DENYLIST is unset for the guard: a private word alone is not a hit."""
    cfg, personas, r, work = item
    word = "wb" + "privateword"
    deny = tmp_path / "deny.txt"
    deny.write_text(f"name {word}\n", encoding="utf-8")
    monkeypatch.setenv("WORKBENCH_DENYLIST", str(deny))
    (work / "b.txt").write_text(word + "\n", encoding="utf-8")
    git(work, "add", "b.txt")  # committed: a push now refuses an untracked file (rework 2)
    git(work, "commit", "-q", "-m", "add b.txt")
    r.add("spec-checker", GO).add("builder", READY).add("reviewer", CLOSE).add("second-reviewer", CR_CLEAN)
    assert machine.run_next(cfg, personas, "7")["status"] == "ready"


def test_any_other_exit_of_the_scan_waits(item, tmp_path, monkeypatch):
    cfg, personas, r, _ = item
    fake_root = tmp_path / "fake-root"
    (fake_root / "scripts").mkdir(parents=True)
    (fake_root / "scripts" / "secret-scan.sh").write_text("exit 3\n", encoding="utf-8")
    monkeypatch.setattr(guard, "ROOT", fake_root)
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert st["status"] == "waiting" and "the secret scan exited with code 3" in st["question"]["text"]
    assert r.pushes == []


def test_no_bash_waits(item, monkeypatch):
    cfg, personas, r, _ = item
    monkeypatch.setattr(guard, "find_bash", lambda: (None, "no bash found"))
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert st["status"] == "waiting" and "the secret guard did not run: no bash found" in st["question"]["text"]


def test_a_missing_base_waits(item):
    cfg, personas, r, work = item
    git(work, "update-ref", "-d", "refs/remotes/origin/main")
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert (st["status"], st["question"]["case"]) == ("waiting", 2)
    assert "The work folder has no origin or no origin/main" in st["question"]["text"]
    assert r.calls == [] and r.pushes == [] and "remote" not in st  # before any step


def test_no_recorded_base_is_no_scan(item):
    cfg, _, _, _ = item
    st = state.load(cfg, "7")
    assert "remote" not in st  # recorded by the first run-next only
    assert guard.scan(st, Path(cfg["state"]["home"]) / "7") == "the secret guard did not run: no base commit is recorded"


def test_the_places_of_the_diff_lines():
    diff = ["diff --git a/x b/x", "index 1..2 100644", "--- a/x", "+++ b/x", "@@ -1,2 +4,3 @@",
            " same", "-gone", "+++ a line that starts with ++", "+new", "diff --git a/y b/y",
            "+++ /dev/null", "@@ -1 +0,0 @@", "-old"]
    assert guard._places(diff) == ["line 1 of the diff", "line 2 of the diff", "line 3 of the diff",
                                   "line 4 of the diff", "line 5 of the diff", "x:4", "line 7 of the diff",
                                   "x:5", "x:6", "line 10 of the diff", "line 11 of the diff",
                                   "line 12 of the diff", "line 13 of the diff"]


def test_each_line_of_the_pull_request_text_has_its_place(item, monkeypatch):
    cfg, personas, r, _ = item
    monkeypatch.setattr(push, "pr_text", lambda st: ("A title", "line 1\nline 2\nbody " + SECRET))
    git(Path(cfg["work"]["home"]) / "7", "commit", "-q", "--allow-empty", "-m", "first\n\nsecond " + SECRET)
    r.add("spec-checker", GO).add("builder", READY)
    text = machine.run_next(cfg, personas, "7")["question"]["text"]
    assert "hits: a commit message github-token; the pull request title or body github-token (scan" in text


# --- the second code review ---

def test_a_secret_that_a_later_commit_removes_still_stops_the_push(item):
    cfg, personas, r, work = item
    (work / "a.txt").write_text("ok\n" + SECRET + "\n", encoding="utf-8")
    git(work, "add", "a.txt")
    git(work, "commit", "-q", "-m", "add a file")
    (work / "a.txt").unlink()
    git(work, "commit", "-q", "-am", "remove it again")
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert st["status"] == "waiting" and "a commit, a.txt:2 github-token" in st["question"]["text"]
    assert r.pushes == []


def test_a_secret_in_a_binary_file_is_found(item):
    cfg, personas, r, work = item
    (work / "blob.bin").write_bytes(b"\x00\x01head\n" + SECRET.encode() + b"\n")
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert st["status"] == "waiting" and "blob.bin:2 github-token" in st["question"]["text"]


def test_a_step_that_moves_the_base_ref_does_not_hide_a_secret(item, monkeypatch):
    cfg, personas, r, work = item
    inner = machine.run_step

    def step(folder, prompt, timeout, runner, out, *, cfg=None):
        if runner == "fake-builder":
            (work / "a.txt").write_text(SECRET + "\n", encoding="utf-8")
            git(work, "add", "a.txt")
            git(work, "commit", "-q", "-m", "add a file")
            git(work, "update-ref", "refs/remotes/origin/main", "HEAD")  # the diff and the log would be empty
        return inner(folder, prompt, timeout, runner, out, cfg=cfg)

    monkeypatch.setattr(machine, "run_step", step)
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert st["remote"]["base_sha"] != machine.head(work)
    assert st["status"] == "waiting" and "a.txt:1 github-token" in st["question"]["text"]


@pytest.mark.parametrize("name", ["commondir", "gitdir", "config.worktree"])
def test_a_dot_git_that_points_at_another_config_is_refused(tmp_path, name):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / name).write_text("elsewhere\n", encoding="utf-8")
    assert guard.check_git(tmp_path) == f"the .git of the work folder has a {name} file, which a clone does not write"


# --- rework round 1 ---

def _in_the_builder_step(monkeypatch, act):
    inner = machine.run_step

    def step(folder, prompt, timeout, runner, out, *, cfg=None):
        if runner == "fake-builder":
            act()
        return inner(folder, prompt, timeout, runner, out, cfg=cfg)

    monkeypatch.setattr(machine, "run_step", step)


def test_a_replace_ref_does_not_hide_a_secret(item, monkeypatch):
    """git diff and git log honor refs/replace, but a push sends the real objects."""
    cfg, personas, r, work = item
    base = git(work, "rev-parse", "HEAD")

    def act():
        (work / "a.txt").write_text(SECRET + "\n", encoding="utf-8")
        git(work, "add", "a.txt")
        git(work, "commit", "-q", "-m", "add a file")
        git(work, "replace", git(work, "rev-parse", "HEAD"), base)
        (work / "a.txt").unlink()

    _in_the_builder_step(monkeypatch, act)
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert st["status"] == "waiting" and "a commit, a.txt:" in st["question"]["text"]
    assert r.pushes == []


def test_a_grafts_file_stops_the_item(item, monkeypatch):
    """A graft gives the last commit the base as its parent: the log would skip the secret commit."""
    cfg, personas, r, work = item
    base = git(work, "rev-parse", "HEAD")

    def act():
        (work / "a.txt").write_text(SECRET + "\n", encoding="utf-8")
        git(work, "add", "a.txt")
        git(work, "commit", "-q", "-m", "add a file")
        git(work, "rm", "-q", "a.txt")
        git(work, "commit", "-q", "-m", "remove it again")
        (work / ".git" / "info").mkdir(exist_ok=True)
        (work / ".git" / "info" / "grafts").write_text(f"{git(work, 'rev-parse', 'HEAD')} {base}\n",
                                                       encoding="utf-8")

    _in_the_builder_step(monkeypatch, act)
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert st["status"] == "waiting" and "has a info/grafts file" in st["question"]["text"]
    assert r.pushes == []


def test_a_shallow_file_is_refused(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "shallow").write_text("0" * 40 + "\n", encoding="utf-8")
    assert guard.check_git(tmp_path) == "the .git of the work folder has a shallow file, which a clone does not write"


EXCLUDE = "the .git of the work folder has an info/exclude rule, which a clone does not write"


@pytest.mark.parametrize("text, why", [
    (None, None), ("# a comment\n\n   \n", None), ("# a comment\r\n\r\n", None),
    ("*.env\n", EXCLUDE), ("# a comment\n secret.txt\n", EXCLUDE), ("\\#name\n", EXCLUDE), ("\t\n", EXCLUDE),
    (" #x\n", EXCLUDE)])
def test_an_info_exclude_rule_is_refused(tmp_path, text, why):
    """A rule hides a file from the diff and from git status. The git init template
    holds only comments."""
    git(tmp_path, "init", "-q")
    exclude = tmp_path / ".git" / "info" / "exclude"
    assert guard.check_git(tmp_path) is None  # the template
    exclude.unlink()
    if text is not None:
        exclude.write_bytes(text.encode())
    assert guard.check_git(tmp_path) == why


def test_an_info_exclude_link_or_folder_is_refused(tmp_path):
    git(tmp_path, "init", "-q")
    exclude = tmp_path / ".git" / "info" / "exclude"
    exclude.unlink()
    exclude.mkdir()
    assert guard.check_git(tmp_path).startswith("cannot read the info/exclude of the work folder: ")
    exclude.rmdir()
    (tmp_path / "comments").write_text("# a comment\n", encoding="utf-8")
    _link(tmp_path / "comments", exclude)
    assert guard.check_git(tmp_path) == EXCLUDE


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="a FIFO needs POSIX")
def test_an_info_exclude_that_is_a_fifo_is_refused(tmp_path):
    """A read of a FIFO blocks until a writer comes: the guard would hang, and git with it."""
    git(tmp_path, "init", "-q")
    exclude = tmp_path / ".git" / "info" / "exclude"
    exclude.unlink()
    os.mkfifo(exclude)
    # not an OSError: check_git would catch it and give the text of a read that failed
    old = signal.signal(signal.SIGALRM, lambda *_: pytest.fail("check_git blocked on the FIFO for 10 s"))
    signal.alarm(10)
    try:
        assert guard.check_git(tmp_path) == "the info/exclude of the work folder is not a regular file"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def test_a_tracked_file_that_gitignore_matches_is_scanned(item):
    cfg, personas, r, work = item
    (work / "t.txt").write_text("ok\n", encoding="utf-8")
    git(work, "add", "t.txt")
    git(work, "commit", "-q", "-m", "a tracked file")
    git(work, "update-ref", "refs/remotes/origin/main", "HEAD")
    (work / ".gitignore").write_text("t.txt\n", encoding="utf-8")
    (work / "t.txt").write_text("ok\n" + SECRET + "\n", encoding="utf-8")
    cfg["runners"]["fake-builder"]["guard"] = True
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert st["status"] == "waiting" and "t.txt:2 github-token" in st["question"]["text"]
    assert r.roles() == ["spec-checker"]


def test_a_secret_that_a_merge_adds_and_a_merge_removes_stops_the_push(item, monkeypatch):
    """git log -p shows no patch for a merge. A plain commit that removes the
    secret would leave the + lines of the commit that added it, so the removal
    here is a merge too."""
    cfg, personas, r, work = item

    def merge(name, change):
        git(work, "checkout", "-q", "-b", name)
        git(work, "commit", "-q", "--allow-empty", "-m", f"on {name}")
        git(work, "checkout", "-q", "-")
        git(work, "commit", "-q", "--allow-empty", "-m", f"beside {name}")
        git(work, "merge", "-q", "--no-ff", "--no-commit", name)
        change()
        git(work, "commit", "-q", "-m", f"merge {name}")

    def add():
        (work / "r.txt").write_text(SECRET + "\n", encoding="utf-8")
        git(work, "add", "r.txt")

    _in_the_builder_step(monkeypatch, lambda: (merge("side1", add), merge("side2", lambda: git(work, "rm", "-q", "r.txt"))))
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert st["status"] == "waiting" and "a commit, r.txt:1 github-token" in st["question"]["text"]
    assert r.pushes == []


# --- rework round 2 ---

@pytest.mark.parametrize("dirty", [True, False])
def test_changes_that_the_build_did_not_commit_stop_the_push(item, monkeypatch, dirty):
    """The review reads the work folder: it must be the tree of the push."""
    cfg, personas, r, work = item

    def act():
        (work / "m.txt").write_text("one\n", encoding="utf-8")
        git(work, "add", "m.txt")
        git(work, "commit", "-q", "-m", "add a file")
        if dirty:
            (work / "m.txt").write_text("two\n", encoding="utf-8")
            (work / "sub").mkdir()
            (work / "sub" / "new.txt").write_text("new\n", encoding="utf-8")

    _in_the_builder_step(monkeypatch, act)
    r.add("spec-checker", GO).add("builder", READY).add("reviewer", CLOSE).add("second-reviewer", CR_CLEAN)
    st = machine.run_next(cfg, personas, "7")
    if not dirty:
        assert st["status"] == "ready" and r.pushes == [machine.head(work)]
        return
    assert (st["stage"], st["status"], r.pushes) == ("build", "waiting", [])
    assert st["question"]["case"] == 2
    text = st["question"]["text"]
    assert "m.txt" in text and "sub/new.txt" in text and "two" not in text and "new\n" not in text
    assert r.pushes == [] and r.roles() == ["spec-checker", "builder"]


def test_a_git_status_that_fails_stops_the_push(item, monkeypatch):
    cfg, personas, r, _ = item
    real = machine._git

    def fake(folder, *args, env=None):
        if args[0] == "status":
            return subprocess.CompletedProcess(args, 128, "", "fatal: a broken index\n")
        return real(folder, *args, env=env)

    monkeypatch.setattr(machine, "_git", fake)
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert (st["status"], r.pushes) == ("waiting", [])
    assert "git status failed: fatal: a broken index" in st["question"]["text"]


def test_the_content_of_the_diff_lines_has_no_marker():
    NONL = r"\ No newline at end of file"
    diff = ["diff --git a/x b/x", "index 1..2 100644", "--- a/x", "+++ b/x", "@@ -1,2 +4,3 @@",
            " same", "-gone", "+++ a line that starts with ++", "+new", NONL,
            "diff --git a/y b/y", "+++ /dev/null", "@@ -1 +0,0 @@", "-old", ""]
    assert guard._content(diff) == ["diff --git a/x b/x", "index 1..2 100644", "--- a/x", "+++ b/x",
                                    "@@ -1,2 +4,3 @@", "", "", "++ a line that starts with ++", "new",
                                    NONL, "diff --git a/y b/y", "+++ /dev/null",
                                    "@@ -1 +0,0 @@", "", ""]
    assert len(guard._content(diff)) == len(guard._places(diff)) == len(diff)


def test_a_decorator_on_an_added_line_is_not_an_email(item):
    """A diff line starts with + and the email class allows + before @: the marker is not scanned."""
    cfg, personas, r, work = item
    (work / "t.py").write_text("import pytest\n\n\n" + "@" + "pytest.fixture\ndef f():\n    return 1\n", encoding="utf-8")
    git(work, "add", "t.py")
    git(work, "commit", "-q", "-m", "add a fixture")
    r.add("spec-checker", GO).add("builder", READY).add("reviewer", CLOSE).add("second-reviewer", CR_CLEAN)
    st = machine.run_next(cfg, personas, "7")
    assert st["status"] == "ready" and len(r.pushes) == 1


def test_an_email_on_an_added_line_is_still_found(item):
    cfg, personas, r, work = item
    who = "ab" + "@" + "corp" + ".example.invalid"
    (work / "m.txt").write_text("ok\n" + who + "\n", encoding="utf-8")
    git(work, "add", "m.txt")
    git(work, "commit", "-q", "-m", "add a file")
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert st["status"] == "waiting" and "m.txt:2 email" in st["question"]["text"]
    assert who not in st["question"]["text"] and r.pushes == []


# --- the scan holds only the lines that the push sends for the first time ---

HOME_HIT = "path /ho" + "me/" + "someone"  # class user-path


def _base(work, lines):
    """Commit c.txt as the base commit: the line of the planted value is already on the remote."""
    (work / "c.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    git(work, "add", "c.txt")
    git(work, "commit", "-q", "-m", "the base")
    git(work, "update-ref", "refs/remotes/origin/main", "HEAD")


def _commit(work, lines, message):
    (work / "c.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    git(work, "commit", "-q", "-am", message)


def _to_ready(cfg, personas, r):
    r.add("spec-checker", GO).add("builder", READY).add("reviewer", CLOSE).add("second-reviewer", CR_CLEAN)
    return machine.run_next(cfg, personas, "7")


def test_a_hit_on_a_context_line_does_not_stop_the_push(item):
    cfg, personas, r, work = item
    _base(work, ["one", HOME_HIT, "three"])
    _commit(work, ["one", HOME_HIT, "three, edited"], "edit the line next to it")
    st = _to_ready(cfg, personas, r)
    assert st["status"] == "ready" and len(r.pushes) == 1


def test_a_hit_on_a_removed_line_does_not_stop_the_push(item):
    cfg, personas, r, work = item
    _base(work, ["one", HOME_HIT, "three"])
    _commit(work, ["one", "three"], "delete the line")
    st = _to_ready(cfg, personas, r)
    assert st["status"] == "ready" and len(r.pushes) == 1


def test_added_lines_above_a_base_hit_and_edited_again_are_clean(item):
    """A case seen in use: the hunks of the diff and of both commits show the base line as context."""
    cfg, personas, r, work = item
    _base(work, ["one", HOME_HIT, "three"])
    _commit(work, ["one", "new a", "new b", HOME_HIT, "three"], "add 2 lines")
    _commit(work, ["one", "new a, edited", "new b, edited", HOME_HIT, "three"], "edit them")
    st = _to_ready(cfg, personas, r)
    assert st["status"] == "ready" and len(r.pushes) == 1
    scan_file = Path(cfg["state"]["home"]) / "7" / "guard.txt"
    text = scan_file.read_text(encoding="utf-8")
    assert "new b, edited" in text and HOME_HIT not in text


# --- the scan holds the ignored files, the caches go, a hidden change stops the push ---

def _ignore(work, *patterns):
    """Commit a .gitignore as the base commit."""
    (work / ".gitignore").write_text("".join(p + "\n" for p in patterns), encoding="utf-8")
    git(work, "add", ".gitignore")
    git(work, "commit", "-q", "-m", "ignore")
    git(work, "update-ref", "refs/remotes/origin/main", "HEAD")


def _scan(cfg, work):
    """guard.scan of the work folder against origin/main, as before a push."""
    st = state.load(cfg, "7")
    st["remote"] = {"base_sha": git(work, "rev-parse", "origin/main")}
    return guard.scan(st, state.item_dir(cfg, "7"))


def _scan_file(cfg) -> str:
    return (Path(cfg["state"]["home"]) / "7" / "guard.txt").read_text(encoding="utf-8")


posix_user = pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="the mode bits need POSIX, not root")


def test_an_ignored_env_stops_a_guarded_step(item):
    cfg, personas, r, work = item
    _ignore(work, ".env")
    (work / ".env").write_text("TOKEN=" + SECRET + "\n", encoding="utf-8")
    cfg["runners"]["fake-builder"]["guard"] = True
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert (st["stage"], st["status"], st["question"]["case"]) == ("build", "waiting", 2)
    assert r.roles() == ["spec-checker"]
    text = st["question"]["text"]
    assert text.startswith("Before the builder step, the secret guard found 1 hits: an ignored file, .env:1 github-token")
    assert SECRET not in text and all(SECRET not in c for c in comments(cfg))


def test_an_env_from_the_builder_stops_the_push(item, monkeypatch):
    cfg, personas, r, work = item
    _ignore(work, ".env")
    _in_the_builder_step(monkeypatch, lambda: (work / ".env").write_text("A=1\nB=" + SECRET + "\n", encoding="utf-8"))
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert (st["status"], st["question"]["case"], r.pushes) == ("waiting", 2, [])
    assert "Before the push, the secret guard found 1 hits: an ignored file, .env:2 github-token" in st["question"]["text"]


def test_a_file_in_a_nested_git_folder_stops_the_push(item, monkeypatch):
    """The index of the work folder holds the nested folder as 1 link to its commit, not its files."""
    cfg, personas, r, work = item

    def act():
        (work / "sub").mkdir()
        git(work / "sub", "init", "-q")
        (work / "sub" / "x.txt").write_text(SECRET + "\n", encoding="utf-8")
        git(work / "sub", "add", "x.txt")
        git(work / "sub", "commit", "-q", "-m", "nested")

    _in_the_builder_step(monkeypatch, act)
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert (st["status"], r.pushes) == ("waiting", [])
    assert "an ignored file, sub/x.txt:1 github-token" in st["question"]["text"]


@posix_user
@pytest.mark.parametrize("where", ["locked", "sub/locked"])  # sub: a nested git folder, which no removal enters
def test_a_folder_that_cannot_be_read_waits(item, where):
    cfg, personas, r, work = item
    _ignore(work, "locked/", "sub/")
    (work / where).mkdir(parents=True)
    (work / where / "a.txt").write_text(SECRET + "\n", encoding="utf-8")
    if where.startswith("sub/"):
        (work / "sub" / "b.txt").write_text("b\n", encoding="utf-8")
        git(work / "sub", "init", "-q")
        git(work / "sub", "add", "b.txt")
        git(work / "sub", "commit", "-q", "-m", "nested")
    os.chmod(work / where, 0)
    try:
        r.add("spec-checker", GO).add("builder", READY)
        st = machine.run_next(cfg, personas, "7")
    finally:
        os.chmod(work / where, 0o700)
    assert (st["status"], r.pushes) == ("waiting", [])
    assert "Before the push, the secret guard did not run: " in st["question"]["text"]


def test_an_unchanged_tracked_file_is_not_in_the_scan_file(item):
    cfg, _, _, work = item
    (work / "t.txt").write_text(SECRET + "\n", encoding="utf-8")
    git(work, "add", "t.txt")
    git(work, "commit", "-q", "-m", "the base")
    git(work, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert _scan(cfg, work) is None and SECRET not in _scan_file(cfg)


def _link(target, link):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("no link support")


def test_a_link_to_an_outside_file_is_not_followed(item, tmp_path):
    cfg, _, _, work = item
    _ignore(work, "out.txt", "out-dir")
    for name in ("outside", "outside-dir"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "s.txt").write_text(SECRET + "\n", encoding="utf-8")
    _link(tmp_path / "outside" / "s.txt", work / "out.txt")
    _link(tmp_path / "outside-dir", work / "out-dir")
    assert _scan(cfg, work) is None
    lines = _scan_file(cfg).split("\n")
    assert str(tmp_path / "outside" / "s.txt") in lines and str(tmp_path / "outside-dir") in lines
    assert SECRET not in "\n".join(lines)


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or os.name != "posix", reason="a socket file needs POSIX")
def test_a_file_that_is_not_regular_is_not_opened(item):
    """Opening a socket fails, and opening a FIFO blocks: neither is a regular file."""
    cfg, _, _, work = item
    _ignore(work, "s.sock")
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind(str(work / "s.sock"))
        assert _scan(cfg, work) is None


def _caches(root: Path, token: str = SECRET) -> None:
    """The caches that a test run leaves, with a token in a .pyc and in v/cache/nodeids."""
    (root / "__pycache__").mkdir(parents=True)
    (root / "__pycache__" / "m.cpython-313.pyc").write_bytes(b"\x00\x01" + token.encode() + b"\n")
    cache = root / ".pytest_cache"
    (cache / "v" / "cache").mkdir(parents=True)
    for name in ("CACHEDIR.TAG", "README.md", ".gitignore"):
        (cache / name).write_text("# made by pytest\n", encoding="utf-8")
    (cache / "v" / "cache" / "nodeids").write_text('["t.py::' + token + '"]\n', encoding="utf-8")
    (cache / "v" / "cache" / "lastfailed").write_text("{}\n", encoding="utf-8")


def test_the_caches_of_a_build_go_before_the_push(item, monkeypatch):
    cfg, personas, r, work = item
    _ignore(work, "__pycache__/", ".pytest_cache/")
    _in_the_builder_step(monkeypatch, lambda: (_caches(work), _caches(work / "pkg")))
    st = _to_ready(cfg, personas, r)
    assert st["status"] == "ready" and len(r.pushes) == 1
    for gone in ("__pycache__", ".pytest_cache", "pkg/__pycache__", "pkg/.pytest_cache"):
        assert not (work / gone).exists(), gone
    assert (work / "pkg").is_dir()
    assert SECRET not in _scan_file(cfg)


def test_a_file_outside_the_forms_stays_in_a_cache_folder(item, monkeypatch):
    cfg, personas, r, work = item
    _ignore(work, "__pycache__/", ".pytest_cache/")

    def act():
        _caches(work, "clean")
        (work / "__pycache__" / "notes.txt").write_text(SECRET + "\n", encoding="utf-8")
        (work / ".pytest_cache" / "keep.txt").write_text(SECRET + "\n", encoding="utf-8")
        (work / "__pycache__" / "deep").mkdir()
        for pyc in ("x.pyc", "__pycache__/deep/x.pyc", ".pytest_cache/x.pyc"):  # not directly in __pycache__
            (work / pyc).write_bytes(b"\x00x")

    _in_the_builder_step(monkeypatch, act)
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert (st["status"], r.pushes) == ("waiting", [])
    text = st["question"]["text"]
    assert ("an ignored file, .pytest_cache/keep.txt:1 github-token" in text
            and "an ignored file, __pycache__/notes.txt:1 github-token" in text)
    assert (work / "__pycache__" / "notes.txt").exists() and (work / ".pytest_cache" / "keep.txt").exists()
    assert not (work / "__pycache__" / "m.cpython-313.pyc").exists()
    assert all((work / pyc).exists() for pyc in ("x.pyc", "__pycache__/deep/x.pyc", ".pytest_cache/x.pyc"))
    assert not (work / ".pytest_cache" / "README.md").exists() and not (work / ".pytest_cache" / "v" / "cache" / "nodeids").exists()
    assert (work / ".pytest_cache" / "v" / "cache").is_dir()  # only an empty cache folder goes, with its empty folders


def test_a_committed_or_staged_pyc_stays(item):
    cfg, _, _, work = item
    _ignore(work, "__pycache__/")
    (work / "__pycache__").mkdir()
    for name in ("c.pyc", "s.pyc", "u.pyc"):
        (work / "__pycache__" / name).write_bytes(b"\x00" + name.encode())
    git(work, "add", "-f", "__pycache__/c.pyc")
    git(work, "commit", "-q", "-m", "a committed pyc")
    (work / "__pycache__" / "h.pyc").write_bytes(b"\x00h")
    git(work, "add", "-f", "__pycache__/h.pyc")
    git(work, "commit", "-q", "-m", "a pyc in HEAD only")
    git(work, "rm", "-q", "--cached", "__pycache__/h.pyc")
    git(work, "add", "-f", "__pycache__/s.pyc")  # in the index only
    assert "__pycache__/s.pyc" not in git(work, "ls-tree", "-r", "--name-only", "HEAD").split("\n")
    assert _scan(cfg, work) is None
    assert sorted(p.name for p in (work / "__pycache__").iterdir()) == ["c.pyc", "h.pyc", "s.pyc"]


def _raw_name(folder: Path, raw: bytes) -> Path:
    """A path whose name is not UTF-8, as os.walk gives it on POSIX; skip where the file system refuses it."""
    path = folder / os.fsdecode(raw)
    try:
        path.write_bytes(b"")
    except (OSError, UnicodeError):
        pytest.skip("the file system refuses a name that is not UTF-8")
    return path


@pytest.mark.skipif(os.name != "posix", reason="a name that is not UTF-8 needs POSIX")
def test_an_ignored_file_does_not_hide_behind_a_name_that_is_not_utf8(item):
    """git decodes b"\\xff.txt" to "\\ufffd.txt"; the walk must not take the real "\\ufffd.txt" for it."""
    cfg, _, _, work = item
    _ignore(work, "�.txt")
    _raw_name(work, b"\xff.txt").write_text("words\n", encoding="utf-8")  # untracked: the index holds it
    (work / "�.txt").write_text(SECRET + "\n", encoding="utf-8")
    why = _scan(cfg, work)
    assert why and "an ignored file, �.txt:1 github-token" in why


@pytest.mark.skipif(os.name != "posix", reason="a name that is not UTF-8 needs POSIX")
def test_a_committed_or_staged_pyc_whose_name_is_not_utf8_stays(item):
    cfg, _, _, work = item
    _ignore(work, "__pycache__/")
    (work / "__pycache__").mkdir()
    committed = _raw_name(work / "__pycache__", b"c\xff.pyc")
    staged = _raw_name(work / "__pycache__", b"s\xff.pyc")
    git(work, "add", "-f", committed.relative_to(work).as_posix())
    git(work, "commit", "-q", "-m", "a committed pyc")
    git(work, "add", "-f", staged.relative_to(work).as_posix())
    assert _scan(cfg, work) is None
    assert committed.exists() and staged.exists()


@pytest.mark.skipif(os.name != "posix", reason="a link target that is not UTF-8 needs POSIX")
def test_a_link_whose_target_is_not_utf8_is_scanned(item):
    """os.readlink gives a surrogate for the byte: the scan file still takes the target as text."""
    cfg, _, _, work = item
    _ignore(work, "out")
    _link(os.fsdecode(SECRET.encode() + b"\xff"), work / "out")
    why = _scan(cfg, work)
    assert why and "an ignored file, out:1 github-token" in why


def test_no_removal_follows_a_link_or_enters_a_nested_git_folder(item, tmp_path):
    cfg, _, _, work = item
    _ignore(work, "__pycache__", "pkg/", "pkg2/", "pkg3/", "sub/")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.pyc").write_bytes(b"\x00x")
    _link(outside, work / "__pycache__")
    (work / "pkg" / "__pycache__").mkdir(parents=True)
    _link(outside / "x.pyc", work / "pkg" / "__pycache__" / "x.pyc")
    (work / "sub" / "__pycache__").mkdir(parents=True)
    (work / "sub" / "__pycache__" / "n.pyc").write_bytes(b"\x00n")
    (work / "sub" / "a.txt").write_text("a\n", encoding="utf-8")
    git(work / "sub", "init", "-q")
    git(work / "sub", "add", "a.txt")
    git(work / "sub", "commit", "-q", "-m", "nested")
    (work / "pkg2" / "__pycache__" / "n" / ".git").mkdir(parents=True)  # an empty .git folder in a cache folder
    (work / "pkg3" / "__pycache__").mkdir(parents=True)
    _link(outside, work / "pkg3" / "__pycache__" / "d")  # a cache folder that holds only a link to a folder
    assert _scan(cfg, work) is None
    assert (work / "pkg3" / "__pycache__" / "d").is_symlink()
    assert (work / "pkg2" / "__pycache__" / "n" / ".git").is_dir()
    assert (work / "__pycache__").is_symlink() and (outside / "x.pyc").exists()
    assert (work / "pkg" / "__pycache__" / "x.pyc").is_symlink() and (work / "sub" / "__pycache__" / "n.pyc").exists()


@posix_user
def test_a_failed_removal_waits(item):
    cfg, _, _, work = item
    _ignore(work, "__pycache__/")
    (work / "__pycache__").mkdir()
    (work / "__pycache__" / "m.pyc").write_bytes(b"\x00m")
    os.chmod(work / "__pycache__", 0o500)
    try:
        why = _scan(cfg, work)
    finally:
        os.chmod(work / "__pycache__", 0o700)
    assert why.startswith("the secret guard did not run: ") and (work / "__pycache__" / "m.pyc").exists()


@pytest.mark.parametrize("bit", ["--skip-worktree", "--assume-unchanged"])
def test_a_change_behind_an_index_bit_stops_the_push(item, monkeypatch, bit):
    cfg, personas, r, work = item

    def act():
        (work / "s.txt").write_text("one\n", encoding="utf-8")
        git(work, "add", "s.txt")
        git(work, "commit", "-q", "-m", "add a file")
        git(work, "update-index", bit, "s.txt")
        (work / "s.txt").write_text("hidden words\n", encoding="utf-8")

    _in_the_builder_step(monkeypatch, act)
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert (st["stage"], st["status"], st["question"]["case"], r.pushes) == ("build", "waiting", 2, [])
    text = st["question"]["text"]
    assert "the work folder has changes that are not in" in text and "s.txt" in text and "hidden words" not in text


@pytest.mark.parametrize("bit", ["--skip-worktree", "--assume-unchanged"])
def test_a_rework_that_hides_a_change_pushes_no_new_head(item, monkeypatch, bit):
    cfg, personas, r, work = item
    inner = machine.run_step

    def step(folder, prompt, timeout, runner, out, *, cfg=None):
        if runner == "fake-builder":
            (work / "s.txt").write_text("one\n", encoding="utf-8")
            git(work, "add", "s.txt")
            git(work, "commit", "-q", "-m", "add a file")
        if runner == "fake-rework":
            git(work, "update-index", bit, "s.txt")
            (work / "s.txt").write_text("hidden words\n", encoding="utf-8")
        return inner(folder, prompt, timeout, runner, out, cfg=cfg)

    monkeypatch.setattr(machine, "run_step", step)
    r.add("spec-checker", GO).add("builder", READY).add("reviewer", "REVIEW: REWORK - 1 blocking", CLOSE)
    r.add("second-reviewer", CR_CLEAN).add("rework", "REWORK: DONE")
    st = machine.run_next(cfg, personas, "7")
    assert (st["stage"], st["status"], st["question"]["case"]) == ("rework", "waiting", 2)
    assert r.pushes == [machine.head(work)] and r.roles()[-1] == "rework"  # the push of the build only
    assert "s.txt" in st["question"]["text"] and "hidden words" not in st["question"]["text"]


def test_a_builder_step_that_adds_an_exclude_rule_waits(item, monkeypatch):
    cfg, personas, r, work = item

    def act():
        with open(work / ".git" / "info" / "exclude", "a", encoding="utf-8") as f:
            f.write("notes.txt\n")
        (work / "notes.txt").write_text("words\n", encoding="utf-8")

    _in_the_builder_step(monkeypatch, act)
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert (st["status"], st["question"]["case"], r.pushes) == ("waiting", 2, [])
    assert "Before the push, the secret guard did not run: " + EXCLUDE in st["question"]["text"]


# --- exact git reads ---

def test_the_scan_of_a_merge_does_not_obey_log_diffmerges(item, tmp_path, monkeypatch):
    cfg, _, _, work = item
    git(work, "checkout", "-q", "-b", "side")
    (work / "s.txt").write_text("from the side\n", encoding="utf-8")
    git(work, "add", "s.txt")
    git(work, "commit", "-q", "-m", "on side")
    git(work, "checkout", "-q", "-")
    (work / "m.txt").write_text("beside the side\n", encoding="utf-8")
    git(work, "add", "m.txt")
    git(work, "commit", "-q", "-m", "beside side")
    git(work, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    files = []
    for name, text in (("none", ""), ("first-parent", "[log]\n\tdiffMerges = first-parent\n")):
        conf = tmp_path / f"global-{name}"
        conf.write_text(text, encoding="utf-8")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(conf))
        assert _scan(cfg, work) is None
        files.append(_scan_file(cfg))
    assert files[0] == files[1] and files[0].count("beside the side") == 3  # the diff, its commit, the merge against side


def test_a_config_key_with_a_space_is_1_key(tmp_path):
    git(tmp_path, "init", "-q")
    with (tmp_path / ".git" / "config").open("a", encoding="utf-8") as f:
        f.write('[branch "a.remote remote.origin"]\n\turl = x\n')
    assert guard.check_git(tmp_path) == ("the git config of the work folder has keys that a clone does not write:"
                                         " branch.a.remote remote.origin.url")


# --- a junction is a link, a link target splits on a line feed, no hunk-header context ---

SECRET_2 = "gh" + "p_" + "R8" * 18  # a second value of class github-token


def _junctions(monkeypatch, targets: dict) -> None:
    """Each real folder of targets counts as a junction to its fake target, as on Windows: os.path.isjunction
    (3.12+; raising=False for 3.11) and os.readlink."""
    key = lambda p: os.path.normcase(os.path.normpath(os.fspath(p)))  # noqa: E731
    fake, real = {key(p): t for p, t in targets.items()}, os.readlink
    monkeypatch.setattr(os.path, "isjunction", lambda p: key(p) in fake, raising=False)
    monkeypatch.setattr(os, "readlink", lambda p, *a, **k: fake[key(p)] if key(p) in fake else real(p, *a, **k))


@pytest.mark.skipif(os.name != "nt", reason="a junction needs Windows")
def test_a_junction_is_never_entered(item, tmp_path):
    cfg, _, _, work = item
    if re.search(r"[A-Za-z]:[\\/]+Users[\\/]", str(tmp_path)):  # os.readlink of j is then a hit of class user-path
        pytest.skip("the temp folder is a user path: run with --basetemp outside C:\\Users")
    _ignore(work, "j", "pkg/")
    outside, empty = tmp_path / "outside", tmp_path / "outside-empty"
    (outside / "__pycache__").mkdir(parents=True)
    (outside / "__pycache__" / "x.pyc").write_bytes(b"\x00x")
    (outside / "s.txt").write_text(SECRET + "\n", encoding="utf-8")
    (empty / "sub").mkdir(parents=True)
    (work / "pkg" / "__pycache__").mkdir(parents=True)
    for target, junction in ((outside, work / "j"), (empty, work / "pkg" / "__pycache__" / "d")):
        subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(target)], check=True, capture_output=True)
    assert _scan(cfg, work) is None
    assert (outside / "__pycache__" / "x.pyc").exists() and (outside / "s.txt").exists() and (empty / "sub").is_dir()
    assert os.path.isjunction(work / "j") and os.path.isjunction(work / "pkg" / "__pycache__" / "d")
    lines = _scan_file(cfg).split("\n")
    assert os.readlink(work / "j") in lines and SECRET not in "\n".join(lines)


def test_a_folder_that_the_link_check_finds_is_a_link(item, monkeypatch):
    """AC 1 (b): the check patched True for a real folder j, as for a junction on Windows."""
    cfg, _, _, work = item
    _ignore(work, "j/")
    (work / "j" / "__pycache__").mkdir(parents=True)
    (work / "j" / "__pycache__" / "x.pyc").write_bytes(b"\x00" + SECRET.encode() + b"\n")
    _junctions(monkeypatch, {work / "j": "the target " + SECRET_2})
    why = _scan(cfg, work)
    assert why and why.startswith("the secret guard found 1 hits: an ignored file, j:1 github-token")
    assert (work / "j" / "__pycache__" / "x.pyc").exists()
    assert SECRET not in _scan_file(cfg) and "the target " + SECRET_2 in _scan_file(cfg).split("\n")


def test_a_cache_folder_that_holds_a_junction_stays(item, monkeypatch):
    """The empty check of drop_caches: a real folder d with an empty subfolder, the check patched True for d."""
    cfg, _, _, work = item
    _ignore(work, "pkg/")
    (work / "pkg" / "__pycache__" / "d" / "sub").mkdir(parents=True)
    _junctions(monkeypatch, {work / "pkg" / "__pycache__" / "d": "elsewhere"})
    assert _scan(cfg, work) is None
    assert (work / "pkg" / "__pycache__" / "d" / "sub").is_dir()


def test_a_dot_git_junction_is_refused(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    _junctions(monkeypatch, {tmp_path / ".git": "elsewhere"})
    assert guard.check_git(tmp_path) == "the .git of the work folder is not a folder"


@pytest.mark.skipif(os.name != "posix", reason="a line feed in a link target needs POSIX")
def test_a_link_target_with_a_line_feed_gives_the_place_of_each_line(item):
    cfg, _, _, work = item
    _ignore(work, "l")
    _link("a\n" + SECRET, work / "l")
    why = _scan(cfg, work)
    assert why and why.startswith("the secret guard found 1 hits: an ignored file, l:2 github-token")


def test_a_hunk_header_loses_its_context_text():
    assert guard._content(["@@ -1,2 +1,3 @@ x"]) == ["@@ -1,2 +1,3 @@"]
    assert guard._content(["@@ -1 +1 x"]) == ["@@ -1 +1 x"]  # no second @@: kept whole


def test_a_base_line_in_a_hunk_header_does_not_stop_the_push(item):
    """git copies the nearest line above the hunk that starts with a letter, _ or $ into the hunk header."""
    cfg, _, _, work = item
    lines = ["key " + SECRET] + [f"  {k}" for k in range(6)]
    _base(work, lines)
    _commit(work, lines + ["added"], "add a line")
    assert _scan(cfg, work) is None


# --- the mutants that survived the probe of the ignored-files scan ---

def test_a_clean_ignored_file_from_the_builder_is_no_uncommitted_change(item, monkeypatch):
    """M33: git status lists no ignored file."""
    cfg, personas, r, work = item
    _ignore(work, "build.log")
    _in_the_builder_step(monkeypatch, lambda: (work / "build.log").write_text("all tests pass\n", encoding="utf-8"))
    st = _to_ready(cfg, personas, r)
    assert st["status"] == "ready" and len(r.pushes) == 1


def test_a_nul_in_an_ignored_file_does_not_hide_a_token(item):
    """M20: grep skips a file with a NUL."""
    cfg, personas, r, work = item
    _ignore(work, "n.bin")
    (work / "n.bin").write_bytes(b"\x00head\n" + SECRET.encode() + b"\n")
    r.add("spec-checker", GO).add("builder", READY)
    st = machine.run_next(cfg, personas, "7")
    assert (st["status"], r.pushes) == ("waiting", [])
    assert "an ignored file, n.bin:2 github-token" in st["question"]["text"] and "\0" not in _scan_file(cfg)


def test_a_file_in_a_folder_of_pytest_cache_outside_v_stays(item):
    """M17: of .pytest_cache, only the files of its top and the files under v/ go."""
    cfg, _, _, work = item
    _ignore(work, ".pytest_cache/")
    (work / ".pytest_cache" / "d").mkdir(parents=True)
    (work / ".pytest_cache" / "d" / "keep.txt").write_text("words\n", encoding="utf-8")
    assert _scan(cfg, work) is None
    assert (work / ".pytest_cache" / "d" / "keep.txt").exists()


def test_a_cache_folder_in_a_cache_folder_goes(item):
    """M26: the deepest first, else the walk of the inner folder fails after the outer removal."""
    cfg, _, _, work = item
    _ignore(work, "__pycache__/")
    (work / "__pycache__" / "__pycache__").mkdir(parents=True)
    (work / "__pycache__" / "__pycache__" / "m.pyc").write_bytes(b"\x00m")
    assert _scan(cfg, work) is None
    assert not (work / "__pycache__").exists()


def test_no_removal_enters_the_top_dot_git(item):
    """M29."""
    cfg, _, _, work = item
    (work / ".git" / "x" / "__pycache__").mkdir(parents=True)
    (work / ".git" / "x" / "__pycache__" / "m.pyc").write_bytes(b"\x00m")
    assert _scan(cfg, work) is None
    assert (work / ".git" / "x" / "__pycache__" / "m.pyc").exists()


# --- the removal walk of drop_caches enters no link and no .git ---

def test_the_removal_walk_enters_no_link(item, monkeypatch):
    """AC 1: os.scandir raises for a folder d that the link check finds and for a .git in a cache folder."""
    _, _, _, work = item
    _ignore(work, "pkg/", "pkg2/")
    d, dot = work / "pkg" / "__pycache__" / "d", work / "pkg2" / "__pycache__" / "n" / ".git"
    (d / "sub").mkdir(parents=True)
    dot.mkdir(parents=True)
    _junctions(monkeypatch, {d: "elsewhere"})
    key = lambda p: os.path.normcase(os.path.normpath(os.fspath(p)))  # noqa: E731
    shut, real = {key(d), key(dot)}, os.scandir

    def scandir(path=".", *a, **k):
        if key(path) in shut:
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real(path, *a, **k)

    monkeypatch.setattr(os, "scandir", scandir)
    guard.drop_caches(work)  # direct: the walk of _ignored reads a nested .git
    assert (d / "sub").is_dir() and dot.is_dir()


@pytest.mark.skipif(os.name != "nt", reason="a junction needs Windows")
def test_a_junction_to_a_removed_folder_does_not_stop_the_guard(item, tmp_path):
    """AC 2: the target goes after mklink, by its own path; a bottom-up walk then fails on the junction."""
    cfg, _, _, work = item
    if re.search(r"[A-Za-z]:[\\/]+Users[\\/]", str(tmp_path)):  # os.readlink of d is then a hit of class user-path
        pytest.skip("the temp folder is a user path: run with --basetemp outside C:\\Users")
    _ignore(work, "pkg/")
    outside, d = tmp_path / "outside", work / "pkg" / "__pycache__" / "d"
    outside.mkdir()
    d.parent.mkdir(parents=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(d), str(outside)], check=True, capture_output=True)
    outside.rmdir()
    assert _scan(cfg, work) is None
    assert os.path.isjunction(d)
