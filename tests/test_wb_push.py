"""The build commits, the script pushes. After
`BUILD: READY <sha>` the script checks that <sha> is the head of the work
folder, pushes the branch and opens 1 pull request; after a rework it commits
and pushes again. The token goes only to the push and the API call.

The remote is a local bare repo (push.GITHUB points at its folder) and the API
is a fake (push.api); no test calls GitHub.
"""

from __future__ import annotations

import base64
import http.client
import os
import shutil
import subprocess
import sys
import urllib.error
import uuid
from pathlib import Path

import pytest

from runner import proc
from wb import intake, machine, push, state, tracker
from wb import run as run_mod
from wb_fakes import HEAD, ONE_DOLLAR, SPEC, git, make_work_tree, reply, start

GO = "SPEC-CHECK 7: GO"
READY = "BUILD: READY " + HEAD
CLOSE = "REVIEW: CLOSE"
REWORK = "REVIEW: REWORK - 1 blocking"
CR_CLEAN = "CODE-REVIEW: 0 findings (0 critical, 0 high, 0 medium, 0 low)"
DONE = "REWORK: DONE"
REAL_PUBLISH = push.publish


class FakeApi:
    def __init__(self, open_pulls=()):
        self.calls: list[tuple] = []
        self.pulls = list(open_pulls)

    def __call__(self, method, url, token, body=None):
        self.calls.append((method, url, token, body))
        if method == "POST":
            self.pulls.append({**body, "base": {"ref": body["base"]}})  # as GitHub lists it
            return {"number": len(self.pulls)}
        return list(self.pulls)

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]


@pytest.fixture
def item(tmp_path, monkeypatch):
    """An item with a real push to a bare remote, a fake API and a token file."""
    cfg, personas, r = start(tmp_path, monkeypatch)
    monkeypatch.setattr(push, "publish", REAL_PUBLISH)
    remote = tmp_path / "remote" / "example" / "widget.git"
    remote.mkdir(parents=True)
    git(remote, "init", "-q", "--bare")
    monkeypatch.setattr(push, "GITHUB", (tmp_path / "remote").as_posix())
    api = FakeApi()
    monkeypatch.setattr(push, "api", api)
    token = "wbtest" + uuid.uuid4().hex  # built at run time: no file holds it
    tf = tmp_path / "secrets" / "push-token"
    tf.parent.mkdir()
    tf.write_text(token + "\n", encoding="utf-8")
    os.chmod(tf, 0o600)
    cfg["delivery"]["push_token_file"] = str(tf)
    return {"cfg": cfg, "personas": personas, "r": r, "remote": remote, "api": api, "token": token,
            "work": Path(cfg["work"]["home"]) / "7", "tmp": tmp_path}


def on_step(monkeypatch, role, fn):
    """Run fn(work folder) at the start of each step of the role, as the agent's own edits."""
    inner = machine.run_step

    def step(folder, prompt, timeout, runner, out, *, cfg=None):
        if runner == f"fake-{role}":
            fn(Path(folder))
        return inner(folder, prompt, timeout, runner, out, cfg=cfg)

    monkeypatch.setattr(machine, "run_step", step)


def build_commits(folder: Path):
    (folder / "feature.txt").write_text("a change\n", encoding="utf-8")
    git(folder, "add", "feature.txt")
    git(folder, "commit", "-q", "-m", "feat: a change")


def remote_head(it) -> str | None:
    r = subprocess.run(["git", "rev-parse", "refs/heads/feature/7"], cwd=it["remote"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def run(it):
    return machine.run_next(it["cfg"], it["personas"], "7")


def test_a_wrong_sha_stops_the_item_and_pushes_nothing(item, monkeypatch):
    on_step(monkeypatch, "builder", build_commits)
    item["r"].add("spec-checker", GO).add("builder", "BUILD: READY " + "ab12" * 10)
    st = run(item)
    assert (st["stage"], st["status"], st["question"]["case"]) == ("build", "waiting", 2)
    assert "names " + "ab12" * 10 in st["question"]["text"] and st["steps"][-1]["head_after"] in st["question"]["text"]
    assert remote_head(item) is None and item["api"].calls == []


def test_a_short_sha_of_the_head_is_not_the_head(item, monkeypatch):
    """The reply names the full SHA: a prefix of the head waits too."""
    inner = machine.run_step

    def step(folder, prompt, timeout, runner, out, *, cfg=None):
        if runner == "fake-builder":
            build_commits(Path(folder))
            item["r"].add("builder", "BUILD: READY " + machine.head(folder)[:12])
        return inner(folder, prompt, timeout, runner, out, cfg=cfg)

    monkeypatch.setattr(machine, "run_step", step)
    item["r"].add("spec-checker", GO)
    st = run(item)
    assert (st["stage"], st["status"]) == ("build", "waiting") and remote_head(item) is None


def test_ready_pushes_the_head_and_opens_1_pull_request(item, monkeypatch):
    on_step(monkeypatch, "builder", build_commits)
    item["r"].add("spec-checker", GO).add("builder", READY).add("reviewer", CLOSE).add("second-reviewer", CR_CLEAN)
    st = run(item)
    assert st["status"] == "ready"
    head = st["steps"][1]["head_after"]
    assert remote_head(item) == head == machine.head(item["work"])
    (post,) = item["api"].posts()
    assert post[1].endswith("/repos/example/widget/pulls")
    assert post[3]["head"] == "feature/7" and post[3]["base"] == "main" and post[3]["title"] == st["title"]
    get = item["api"].calls[0]
    assert get[0] == "GET" and "head=example%3Afeature%2F7" in get[1] and "state=open" in get[1]


def test_a_rework_is_committed_by_the_script_and_pushed_with_no_second_pull_request(item, monkeypatch):
    on_step(monkeypatch, "builder", build_commits)
    on_step(monkeypatch, "rework", lambda f: (f / "feature.txt").write_text("repaired\n", encoding="utf-8"))
    r = item["r"]
    r.add("spec-checker", GO).add("builder", READY).add("reviewer", REWORK, CLOSE).add("rework", DONE)
    r.add("second-reviewer", CR_CLEAN)
    st = run(item)
    assert st["status"] == "ready" and st["rounds_used"] == 1
    rework = next(s for s in st["steps"] if s["role"] == "rework")
    assert rework["head_after"] != rework["head_before"]  # the script's commit
    assert git(item["work"], "log", "-1", "--format=%s") == "fix: rework round 1 (item 7)"
    assert git(item["work"], "status", "--porcelain") == ""
    assert remote_head(item) == rework["head_after"]
    assert len(item["api"].posts()) == 1 and [c[0] for c in item["api"].calls] == ["GET", "POST", "GET"]


def test_a_rework_with_no_change_makes_no_commit_and_no_push(item, monkeypatch):
    on_step(monkeypatch, "builder", build_commits)
    r = item["r"]
    r.add("spec-checker", GO).add("builder", READY).add("reviewer", REWORK, CLOSE).add("rework", DONE)
    r.add("second-reviewer", CR_CLEAN)
    st = run(item)
    assert st["status"] == "ready" and st["rounds_used"] == 1
    rework = next(s for s in st["steps"] if s["role"] == "rework")
    assert rework["head_after"] == rework["head_before"]
    assert [c[0] for c in item["api"].calls] == ["GET", "POST"]


def test_an_existing_pull_request_is_not_opened_again(item, monkeypatch):
    """1 pull request, also when the push runs again after an interrupted run."""
    on_step(monkeypatch, "builder", build_commits)
    item["api"].pulls.append({"number": 1, "base": {"ref": "main"}})
    item["r"].add("spec-checker", GO).add("builder", READY).add("reviewer", CLOSE).add("second-reviewer", CR_CLEAN)
    assert run(item)["status"] == "ready"
    assert item["api"].posts() == []


def test_the_token_goes_only_to_the_push_and_the_api(item, monkeypatch):
    seen = []
    real_run, real_sub = proc.run, push.subprocess.run

    def spy_step(argv, cwd, stdin, out_path, err_path, timeout_s, env=None):
        seen.append(("step", list(argv), dict(env or {}), stdin))
        return real_run(argv, cwd, stdin, out_path, err_path, timeout_s, env)

    def spy_git(argv, *a, **kw):
        seen.append(("git", list(argv), dict(kw.get("env") or {}), b""))
        return real_sub(argv, *a, **kw)

    monkeypatch.setattr(proc, "run", spy_step)
    monkeypatch.setattr(push.subprocess, "run", spy_git)
    on_step(monkeypatch, "builder", build_commits)
    on_step(monkeypatch, "rework", lambda f: (f / "feature.txt").write_text("repaired\n", encoding="utf-8"))
    r = item["r"]
    r.add("spec-checker", GO).add("builder", READY).add("reviewer", REWORK, CLOSE).add("rework", DONE)
    r.add("second-reviewer", CR_CLEAN)
    st = run(item)
    assert st["status"] == "ready" and remote_head(item)
    token = item["token"]
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    steps = [s for s in seen if s[0] == "step"]
    assert len(steps) == len(st["steps"]) == 7
    for kind, argv, env, stdin in seen:
        assert not any(token in a or basic in a for a in argv), kind
        assert token.encode() not in stdin
        if kind == "step":
            assert not any(token in str(v) or basic in str(v) for v in env.values())
    header = "Authorization: Basic " + basic
    pushes = [env for kind, argv, env, _ in seen if kind == "git" and "push" in argv]
    assert len(pushes) == 2 and all(env["GIT_CONFIG_VALUE_1"] == header for env in pushes)  # only there
    for p in Path(item["cfg"]["state"]["home"]).rglob("*"):  # run folders, prompts, state.json, tracker, scan files
        if p.is_file():
            assert token.encode() not in p.read_bytes(), p
    assert all(c[2] == token for c in item["api"].calls)


def test_an_error_text_holds_no_token(item, monkeypatch):
    on_step(monkeypatch, "builder", build_commits)
    token = item["token"]

    def broken(method, url, tok, body=None):
        raise urllib.error.URLError(f"refused for {tok}")

    monkeypatch.setattr(push, "api", broken)
    item["r"].add("spec-checker", GO).add("builder", READY)
    st = run(item)
    assert (st["status"], st["question"]["case"]) == ("waiting", 2)
    assert "the pull request call failed" in st["question"]["text"] and "***" in st["question"]["text"]
    assert token not in st["question"]["text"]
    assert all(token not in c["text"] for c in tracker.make(item["cfg"]).get("7")["comments"])


def test_a_broken_api_response_waits(item, monkeypatch):
    """An HTTPException of the pull request call is a PushError."""
    on_step(monkeypatch, "builder", build_commits)

    def broken(method, url, tok, body=None):
        raise http.client.IncompleteRead(b"")

    monkeypatch.setattr(push, "api", broken)
    item["r"].add("spec-checker", GO).add("builder", READY)
    st = run(item)
    assert (st["stage"], st["status"], st["question"]["case"]) == ("build", "waiting", 2)
    assert st["question"]["text"].startswith("The push stopped: the pull request call failed")


@pytest.mark.skipif(sys.platform == "win32", reason="the file mode check is POSIX only")
def test_a_token_file_with_a_wider_mode_is_refused(item, monkeypatch):
    on_step(monkeypatch, "builder", build_commits)
    os.chmod(item["cfg"]["delivery"]["push_token_file"], 0o644)
    item["r"].add("spec-checker", GO).add("builder", READY)
    st = run(item)
    assert st["status"] == "waiting" and "must have mode 0600" in st["question"]["text"]
    assert remote_head(item) is None


@pytest.mark.parametrize("value", [None, "relative/push-token", "missing"])
def test_a_missing_token_file_waits(item, monkeypatch, value):
    on_step(monkeypatch, "builder", build_commits)
    if value is None:
        del item["cfg"]["delivery"]["push_token_file"]
    else:
        item["cfg"]["delivery"]["push_token_file"] = str(item["tmp"] / value) if value == "missing" else value
    item["r"].add("spec-checker", GO).add("builder", READY)
    st = run(item)
    assert (st["status"], st["question"]["case"]) == ("waiting", 2)
    text = st["question"]["text"]
    assert ("push token file" in text or "push_token_file" in text) and remote_head(item) is None


def test_a_branch_that_is_the_base_is_never_pushed(item, monkeypatch):
    on_step(monkeypatch, "builder", build_commits)
    st = state.load(item["cfg"], "7")
    st["work"]["branch"] = "main"
    state.save(item["cfg"], st)
    item["r"].add("spec-checker", GO).add("builder", READY)
    st = run(item)
    assert st["status"] == "waiting" and "is the base main: no push" in st["question"]["text"]
    assert item["api"].calls == []


def test_an_origin_that_is_not_github_waits(item, monkeypatch):
    on_step(monkeypatch, "builder", build_commits)
    git(item["work"], "remote", "set-url", "origin", "https://example.invalid/widget.git")
    item["r"].add("spec-checker", GO).add("builder", READY)
    st = run(item)
    assert st["status"] == "waiting" and "not a GitHub repository" in st["question"]["text"]


@pytest.mark.parametrize("url,want", [
    ("git" + "@github.com:example/widget.git", ("example", "widget")),
    ("https://github.com/example/widget", ("example", "widget")),
    ("ssh://git" + "@github.com/example/wid.get.git", ("example", "wid.get")),
])
def test_owner_and_repo_come_from_origin(item, url, want):
    git(item["work"], "remote", "set-url", "origin", url)
    assert push.repo(item["work"]) == want


def test_a_push_that_git_refuses_waits(item, monkeypatch):
    """No --force: a branch that moved on the remote is not overwritten."""
    on_step(monkeypatch, "builder", build_commits)
    other = item["tmp"] / "other"
    git(item["tmp"], "clone", "-q", str(item["remote"]), str(other))
    git(other, "commit", "-q", "--allow-empty", "-m", "someone else")
    git(other, "push", "-q", "origin", "HEAD:refs/heads/feature/7")
    before = remote_head(item)
    item["r"].add("spec-checker", GO).add("builder", READY)
    st = run(item)
    assert st["status"] == "waiting" and "git push failed" in st["question"]["text"]
    assert remote_head(item) == before


# --- the code review: the script's git calls in a folder that a step wrote ---

def plant_hooks(folder: Path, marker: Path):
    for hook in ("pre-commit", "pre-push", "post-commit"):
        h = folder / ".git" / "hooks" / hook
        h.write_text(f"#!/bin/sh\necho {hook} >> '{marker.as_posix()}'\n", encoding="utf-8", newline="\n")
        os.chmod(h, 0o755)


def test_a_hook_that_a_step_writes_does_not_run(item, monkeypatch):
    marker = item["tmp"] / "hook-ran.txt"

    def build(folder):
        plant_hooks(folder, marker)
        build_commits(folder)

    on_step(monkeypatch, "builder", build)
    on_step(monkeypatch, "rework", lambda f: (f / "feature.txt").write_text("repaired\n", encoding="utf-8"))
    r = item["r"]
    r.add("spec-checker", GO).add("builder", READY).add("reviewer", REWORK, CLOSE).add("rework", DONE)
    r.add("second-reviewer", CR_CLEAN)
    st = run(item)
    assert st["status"] == "ready" and remote_head(item) == st["steps"][-3]["head_after"]
    hooks_ran_in_the_step = marker.read_text(encoding="utf-8").split() if marker.exists() else []
    assert hooks_ran_in_the_step == ["pre-commit", "post-commit"]  # only the step's own commit, in the step


@pytest.mark.parametrize("key,value", [("filter.x.clean", "sh -c true"), ("url.https://example.invalid/.insteadOf",
                                                                           "https://github.com/"),
                                      ("include.path", "/elsewhere"), ("core.sshCommand", "sh")])
def test_a_config_key_that_a_clone_does_not_write_stops_the_item(item, monkeypatch, key, value):
    def build(folder):
        build_commits(folder)
        git(folder, "config", key, value)

    on_step(monkeypatch, "builder", build)
    item["r"].add("spec-checker", GO).add("builder", READY)
    st = run(item)
    assert (st["stage"], st["status"], st["question"]["case"]) == ("build", "waiting", 2)
    assert "keys that a clone does not write: " + key.lower().split(".insteadof")[0] in st["question"]["text"].lower()
    assert remote_head(item) is None and item["api"].calls == []


def test_a_dot_git_that_is_not_a_folder_is_refused(tmp_path):
    from wb import guard

    (tmp_path / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    assert guard.check_git(tmp_path) == "the .git of the work folder is not a folder"


def test_a_rework_that_committed_itself_is_pushed(item, monkeypatch):
    on_step(monkeypatch, "builder", build_commits)

    def rework(folder):
        (folder / "feature.txt").write_text("repaired\n", encoding="utf-8")
        git(folder, "commit", "-q", "-am", "the agent's own commit")

    on_step(monkeypatch, "rework", rework)
    r = item["r"]
    r.add("spec-checker", GO).add("builder", READY).add("reviewer", REWORK, CLOSE).add("rework", DONE)
    r.add("second-reviewer", CR_CLEAN)
    st = run(item)
    rework_step = next(s for s in st["steps"] if s["role"] == "rework")
    assert st["status"] == "ready" and remote_head(item) == rework_step["head_after"] != rework_step["head_before"]


class Died(BaseException):
    pass


def test_the_step_is_saved_before_the_push(item, monkeypatch):
    """A death in the push does not leave an open step, so no reset throws the build away."""
    on_step(monkeypatch, "builder", build_commits)

    def dies(*a, **kw):
        raise Died()

    monkeypatch.setattr(push, "publish", dies)
    item["r"].add("spec-checker", GO).add("builder", READY)
    with pytest.raises(Died):
        run(item)
    st = state.load(item["cfg"], "7")
    assert st["open_step"] is None and st["steps"][-1]["role"] == "builder"
    assert st["steps"][-1]["status"] == "OK" and st["steps"][-1]["head_after"] == machine.head(item["work"])


def test_the_push_goes_to_the_origin_of_before_the_first_step(item, monkeypatch):
    """A step that sets another GitHub origin does not move the push."""
    def build(folder):
        build_commits(folder)
        git(folder, "remote", "set-url", "origin", "https://github.com/other/place.git")

    on_step(monkeypatch, "builder", build)
    item["r"].add("spec-checker", GO).add("builder", READY).add("reviewer", CLOSE).add("second-reviewer", CR_CLEAN)
    st = run(item)
    assert st["status"] == "ready" and st["remote"]["url"] == "https://github.com/example/widget.git"
    assert remote_head(item) == st["steps"][1]["head_after"]
    assert all("/repos/example/widget/" in c[1] for c in item["api"].calls)


# --- the third code review ---

def test_a_rework_commit_whose_push_failed_is_pushed_after_the_answer(item, monkeypatch, tmp_path):
    on_step(monkeypatch, "builder", build_commits)
    rounds = []

    def rework(folder):
        if not rounds:  # the first rework repairs; the second, after the answer, changes nothing
            (folder / "feature.txt").write_text("repaired\n", encoding="utf-8")
        rounds.append(1)

    on_step(monkeypatch, "rework", rework)
    calls = []

    def flaky(cfg, st, sha, title, body):
        calls.append(sha)
        if len(calls) == 2:  # the push after the first rework
            raise push.PushError("the network is down")
        return REAL_PUBLISH(cfg, st, sha, title, body)

    monkeypatch.setattr(push, "publish", flaky)
    r = item["r"]
    r.add("spec-checker", GO).add("builder", READY).add("reviewer", REWORK, CLOSE).add("rework", DONE, DONE)
    r.add("second-reviewer", CR_CLEAN)
    st = run(item)
    assert (st["stage"], st["status"]) == ("rework", "waiting") and "the network is down" in st["question"]["text"]
    commit = machine.head(item["work"])
    assert remote_head(item) != commit
    answer = tmp_path / "answer.md"
    answer.write_text("The network is back.\n", encoding="utf-8")
    assert machine.answer(item["cfg"], "7", str(answer)) is None
    st = run(item)
    assert st["status"] == "ready" and len(rounds) == 2
    assert remote_head(item) == commit == st["pushed"] and calls[-1] == commit


# --- a retry of the push only ---

def failed_push(item, monkeypatch, fails=1):
    """A build whose push fails `fails` times (each time: None); the item waits. Returns the shas of the calls."""
    def build(folder):  # a new commit at each build step
        with (folder / "feature.txt").open("a", encoding="utf-8") as f:
            f.write("a change\n")
        git(folder, "add", "feature.txt")
        git(folder, "commit", "-q", "-m", "feat: a change")

    on_step(monkeypatch, "builder", build)
    calls = []

    def flaky(cfg, st, sha, title, body):
        calls.append(sha)
        if fails is None or len(calls) <= fails:
            raise push.PushError("the network is down")
        return REAL_PUBLISH(cfg, st, sha, title, body)

    monkeypatch.setattr(push, "publish", flaky)
    item["r"].add("spec-checker", GO).add("builder", READY).add("reviewer", CLOSE).add("second-reviewer", CR_CLEAN)
    st = run(item)
    assert (st["stage"], st["status"], st["question"]["case"]) == ("build", "waiting", 2)
    assert st["question"]["text"] == "The push stopped: the network is down" and remote_head(item) is None
    return calls


def answer(item):
    f = item["tmp"] / "answer.md"
    f.write_text("Go on.\n", encoding="utf-8")
    assert machine.answer(item["cfg"], "7", str(f)) is None


def test_an_answer_after_a_failed_push_retries_only_the_push(item, monkeypatch):
    calls = failed_push(item, monkeypatch)
    build = machine.head(item["work"])
    answer(item)
    st = run(item)
    assert item["r"].roles() == ["spec-checker", "builder", "reviewer", "second-reviewer"]
    assert st["status"] == "ready" and remote_head(item) == build == st["pushed"] and calls == [build, build]


def test_a_retry_that_fails_waits(item, monkeypatch):
    calls = failed_push(item, monkeypatch, fails=None)
    answer(item)
    st = run(item)
    assert (st["stage"], st["status"], st["question"]["case"]) == ("build", "waiting", 2)
    assert st["question"]["text"] == "The push stopped: the network is down"
    assert st["question"]["run_folder"] == st["steps"][-1]["run_folder"]
    assert item["r"].roles() == ["spec-checker", "builder"] and len(calls) == 2


def test_a_head_that_changed_after_ready_runs_the_build_again(item, monkeypatch):
    failed_push(item, monkeypatch)
    git(item["work"], "commit", "-q", "--allow-empty", "-m", "the owner's commit")
    answer(item)
    st = run(item)
    assert item["r"].roles() == ["spec-checker", "builder", "builder", "reviewer", "second-reviewer"]
    assert st["status"] == "ready" and remote_head(item) == st["steps"][2]["head_after"]


def test_a_ready_sha_that_is_not_the_head_after_runs_the_build_again(item, monkeypatch):
    """READY names A, the step then commits B; the owner resets the folder to A and answers."""
    def build(folder):  # A: a new commit at each build step, before the reply names the head
        with (folder / "feature.txt").open("a", encoding="utf-8") as f:
            f.write("a change\n")
        git(folder, "add", "feature.txt")
        git(folder, "commit", "-q", "-m", "feat: a change")

    on_step(monkeypatch, "builder", build)
    inner, shas = machine.run_step, {}

    def step(folder, prompt, timeout, runner, out, *, cfg=None):
        result = inner(folder, prompt, timeout, runner, out, cfg=cfg)
        if runner == "fake-builder" and not shas:  # B, after the reply of the first build
            shas["A"] = machine.head(folder)
            git(Path(folder), "commit", "-q", "--allow-empty", "-m", "B")
        return result

    monkeypatch.setattr(machine, "run_step", step)
    calls = []
    monkeypatch.setattr(push, "publish", lambda cfg, st, sha, title, body: calls.append((sha, item["r"].roles())))
    item["r"].add("spec-checker", GO).add("builder", READY).add("reviewer", CLOSE).add("second-reviewer", CR_CLEAN)
    st = run(item)
    assert (st["stage"], st["status"], st["question"]["case"]) == ("build", "waiting", 2) and calls == []
    b = st["steps"][-1]["head_after"]
    assert f"names {shas['A']}, but the head of the work folder is {b}" in st["question"]["text"]
    git(item["work"], "reset", "-q", "--hard", shas["A"])
    answer(item)
    st = run(item)
    assert item["r"].roles() == ["spec-checker", "builder", "builder", "reviewer", "second-reviewer"]
    assert [sha for sha, _ in calls] == [st["steps"][2]["head_after"]] != [shas["A"]]
    assert calls[0][1] == ["spec-checker", "builder", "builder"]


def test_a_new_intake_does_not_retry_the_push(item, monkeypatch):
    failed_push(item, monkeypatch)
    assert intake.intake(item["cfg"], str(SPEC), "7") == []
    st = run(item)
    assert item["r"].roles() == ["spec-checker", "builder", "spec-checker", "builder", "reviewer", "second-reviewer"]
    assert st["status"] == "ready" and remote_head(item) == st["steps"][3]["head_after"]


def test_an_item_at_its_budget_stop_still_pushes(item, monkeypatch):
    failed_push(item, monkeypatch)
    build = machine.head(item["work"])
    answer(item)
    st = state.load(item["cfg"], "7")
    st["usd"] = st["limits"]["usd"]
    state.save(item["cfg"], st)
    st = run(item)
    assert remote_head(item) == build == st["pushed"] and st["stage"] == "review"
    assert (st["status"], st["question"]["case"]) == ("waiting", 1) and item["r"].roles() == ["spec-checker", "builder"]


def test_a_death_in_the_push_keeps_the_totals(item, monkeypatch):
    on_step(monkeypatch, "builder", build_commits)

    def dies(*a, **kw):
        raise Died()

    monkeypatch.setattr(push, "publish", dies)
    item["r"].add("spec-checker", reply(GO, ONE_DOLLAR)).add("builder", reply(READY, ONE_DOLLAR))
    with pytest.raises(Died):
        run(item)
    st = state.load(item["cfg"], "7")
    assert [s["role"] for s in st["steps"]] == ["spec-checker", "builder"]
    assert st["usd"] == pytest.approx(sum(s["usd"] for s in st["steps"])) == pytest.approx(2.0)
    assert st["seconds"] >= sum(s["seconds"] for s in st["steps"])


# --- a short list of changes ---

def test_many_uncommitted_files_give_a_short_list_and_a_file(item, monkeypatch):
    names = [f"{k:02d}-" + "x" * 50 + ".txt" for k in range(25)]

    def build(folder):
        build_commits(folder)
        for name in reversed(names):
            (folder / name).write_text("new\n", encoding="utf-8")

    on_step(monkeypatch, "builder", build)
    item["r"].add("spec-checker", GO).add("builder", READY)
    st = run(item)
    assert (st["stage"], st["status"], st["question"]["case"]) == ("build", "waiting", 2)
    file = state.item_dir(item["cfg"], "7") / "uncommitted.txt"
    assert file.read_text(encoding="utf-8").splitlines() == names
    text = st["question"]["text"]
    assert text.endswith(f" paths are in {file}: {', '.join(names[:10])} and 15 more.") and names[10] not in text
    (comment,) = [c["text"] for c in tracker.make(item["cfg"]).get("7")["comments"] if "uncommitted.txt" in c["text"]]
    assert "Commit them or remove them, then answer." in comment and remote_head(item) is None


def test_ten_uncommitted_files_name_all_ten(item, monkeypatch):
    """Exactly 10 paths, with no ` more`."""
    names = [f"p{k:02d}.txt" for k in range(10)]

    def build(folder):
        build_commits(folder)
        for name in names:
            (folder / name).write_text("new\n", encoding="utf-8")

    on_step(monkeypatch, "builder", build)
    item["r"].add("spec-checker", GO).add("builder", READY)
    st = run(item)
    file = state.item_dir(item["cfg"], "7") / "uncommitted.txt"
    assert st["question"]["text"].endswith(f" paths are in {file}: {', '.join(names)}.")
    assert " more" not in st["question"]["text"] and file.read_text(encoding="utf-8").splitlines() == names


def test_a_passed_change_check_removes_the_change_list(item, monkeypatch):
    """No old uncommitted.txt after the owner removes the changes."""
    def build(folder):
        build_commits(folder)
        (folder / "extra.txt").write_text("new\n", encoding="utf-8")

    on_step(monkeypatch, "builder", build)
    item["r"].add("spec-checker", GO).add("builder", READY).add("reviewer", CLOSE).add("second-reviewer", CR_CLEAN)
    st = run(item)
    file = state.item_dir(item["cfg"], "7") / "uncommitted.txt"
    assert (st["stage"], st["status"]) == ("build", "waiting") and file.read_text(encoding="utf-8") == "extra.txt\n"
    (item["work"] / "extra.txt").unlink()
    answer(item)
    st = run(item)
    assert st["pushed"] == remote_head(item) == st["steps"][1]["head_after"] and not file.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no line feed in a file name")
def test_a_path_with_a_line_feed_takes_one_line(item, monkeypatch):
    """A path with a control character is quoted as JSON; a plain path stays."""
    def build(folder):
        build_commits(folder)
        for name in ("a\nb.txt", "c.txt"):
            (folder / name).write_text("new\n", encoding="utf-8")

    on_step(monkeypatch, "builder", build)
    item["r"].add("spec-checker", GO).add("builder", READY)
    st = run(item)
    file = state.item_dir(item["cfg"], "7") / "uncommitted.txt"
    assert file.read_text(encoding="utf-8").splitlines() == ['"a\\nb.txt"', "c.txt"]
    assert st["question"]["text"].endswith(f'All 2 paths are in {file}: "a\\nb.txt", c.txt.')


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no quote, backslash or U+007F in a file name")
def test_a_quote_a_backslash_and_u007f_are_quoted_and_other_text_stays(item, monkeypatch):
    """The characters ", \\ and U+007F give the JSON form, with no ASCII escape; ü stays."""
    def build(folder):
        build_commits(folder)
        for name in ('d"e.txt', "f\\g.txt", "h\x7f.txt", "ü.txt"):
            (folder / name).write_text("new\n", encoding="utf-8")

    on_step(monkeypatch, "builder", build)
    item["r"].add("spec-checker", GO).add("builder", READY)
    st = run(item)
    lines = ['"d\\"e.txt"', '"f\\\\g.txt"', '"h\x7f.txt"', "ü.txt"]
    file = state.item_dir(item["cfg"], "7") / "uncommitted.txt"
    assert file.read_text(encoding="utf-8").splitlines() == lines
    assert st["question"]["text"].endswith(f"All 4 paths are in {file}: {', '.join(lines)}.")


def test_no_recorded_origin_is_no_push(item):
    st = state.load(item["cfg"], "7")
    assert "remote" not in st
    with pytest.raises(push.PushError, match="no origin URL is recorded"):
        push.publish(item["cfg"], st, "a" * 40, "t", "b")


# --- the push and the scan base follow the repository of the item ---

def test_an_item_with_repo_pushes_to_that_repository(item, monkeypatch):
    other = "https://github.com/example/other.git"
    cfg, work = item["cfg"], item["work"]
    cfg["repos"] = {"other": other}
    # the item is new: no work folder yet; Windows refuses to delete the read-only git objects
    shutil.rmtree(work, onexc=lambda fn, p, _: (os.chmod(p, 0o700), fn(p)))
    st = state.load(cfg, "7")
    st.update(status="waiting", question={"case": 2, "text": "q", "run_folder": None})
    state.save(cfg, st)
    assert intake.intake(cfg, str(SPEC), "7", repo="other") == []
    make_work_tree(cfg, "7")  # the clone of AC 5, with the origin of the item
    git(work, "remote", "set-url", "origin", other)
    remote = item["tmp"] / "remote" / "example" / "other.git"
    remote.mkdir(parents=True)
    git(remote, "init", "-q", "--bare")
    on_step(monkeypatch, "builder", build_commits)
    item["r"].add("spec-checker", GO).add("builder", READY).add("reviewer", CLOSE).add("second-reviewer", CR_CLEAN)
    st = run_mod.run_locked(cfg, item["personas"], "7")
    assert st["status"] == "ready" and st["remote"] == {"url": other, "base_sha": st["steps"][0]["head_before"]}
    assert git(remote, "rev-parse", "refs/heads/feature/7") == st["steps"][1]["head_after"]
    assert remote_head(item) is None  # nothing in example/widget
    assert item["api"].calls and all("/repos/example/other/pulls" in c[1] for c in item["api"].calls)
