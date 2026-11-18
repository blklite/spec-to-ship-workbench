"""`wb deliver <id>`, the chain after ready, with no model.

Each command of [deliver] is 1 fake script in tmp_path that logs its argv and
prints what the test plans; the remote is a local bare repo (push.GITHUB), the
API is a fake (push.api), and the clock and the sleep of wb/deliver.py are
patched. No test calls the network, ssh or a real host.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import types
import urllib.error
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from wb import cli, deliver, events, machine, notify, push, run, state
from wb_fakes import SPEC, commit, git, make_cfg, make_work_tree

GOOD_CR = "CODE-REVIEW: 1 findings (0 critical, 0 high, 0 medium, 1 low)"
SECOND = "CODE-REVIEW: 0 findings (0 critical, 0 high, 0 medium, 0 low)"
SELF_CHECK = "SELF-CHECK: 12 of 14 mutants killed, 2 survived, by builder"
MERGE_SHA = "ab12" * 10

FAKE = '''import json, os, sys
from pathlib import Path
here = Path(__file__).parent
name = sys.argv[1]
with (here / "calls.jsonl").open("a", encoding="utf-8") as f:
    f.write(json.dumps({"name": name, "argv": sys.argv[2:], "cwd": os.getcwd()}) + "\\n")
plan = here / (name + ".json")
queue = json.loads(plan.read_text(encoding="utf-8")) if plan.exists() else [{}]
step = queue[0]
if len(queue) > 1:
    plan.write_text(json.dumps(queue[1:]), encoding="utf-8")
sys.stdout.write(step.get("out", ""))
sys.stderr.write(step.get("err", ""))
sys.exit(step.get("code", 0))
'''

# The delivery_check.py that gate_source prints: it logs its argv; the gate prints LINE and exits CODE.
CHECK = '''import json, sys
with open(LOG, "a", encoding="utf-8") as f:
    f.write(json.dumps({"name": "delivery_check", "argv": sys.argv[1:]}) + "\\n")
if sys.argv[1] == "gate":
    print("a table of the checks")
    print(LINE)
    print("gh pr merge --match-head-commit HEAD")
    sys.exit(CODE)
'''


class FakeApi:
    """The list GET gives every open pull request, whatever its query: wb checks the base itself. A GET of
    1 pull request gives `pull`."""

    def __init__(self, open_pulls=(), merge_error=None):
        self.calls: list[tuple] = []
        self.pulls = list(open_pulls)
        self.merge_error = merge_error
        self.pull = None

    def __call__(self, method, url, token, body=None):
        self.calls.append((method, url, token, body))
        if method == "POST":
            self.pulls.append({**body, "number": 40 + len(self.pulls), "base": {"ref": body["base"]}})
            return self.pulls[-1]
        if method == "PUT":
            if self.merge_error:
                raise urllib.error.HTTPError(url, self.merge_error, "refused", {}, None)
            return {"sha": MERGE_SHA, "merged": True}
        if re.search(r"/pulls/[0-9]+$", url):
            return self.pull
        return list(self.pulls)

    def methods(self):
        return [c[0] for c in self.calls]


class Item:
    """A ready item, its fakes and its plans."""

    def __init__(self, tmp_path, monkeypatch):
        from wb import intake

        self.tmp = tmp_path
        self.cfg, self.personas = make_cfg(tmp_path)
        self.work = make_work_tree(self.cfg, "7")
        assert intake.intake(self.cfg, str(SPEC), "7") == []
        base = git(self.work, "rev-parse", "HEAD")
        self.head = commit(self.work, "feat: a greeting")
        self.remote = tmp_path / "remote" / "example" / "widget.git"
        self.remote.mkdir(parents=True)
        git(self.remote, "init", "-q", "--bare")
        git(self.work, "push", "-q", str(self.remote), f"{self.head}:refs/heads/feature/7")
        monkeypatch.setattr(push, "GITHUB", (tmp_path / "remote").as_posix())
        self.api = FakeApi([{"number": 5, "base": {"ref": "main"}}])
        monkeypatch.setattr(push, "api", self.api)
        token = tmp_path / "secrets" / "push-token"
        token.parent.mkdir()
        token.write_text("wbtest" + uuid.uuid4().hex + "\n", encoding="utf-8")  # built at run time
        os.chmod(token, 0o600)
        self.cfg["delivery"]["push_token_file"] = str(token)
        self.fakes = tmp_path / "fakes"
        self.fakes.mkdir()
        (self.fakes / "fake.py").write_text(FAKE, encoding="utf-8")

        def cmd(name, *words):
            return [sys.executable, str(self.fakes / "fake.py"), name, *words]

        self.cfg["deliver"] = {"retry_seconds": 300, "slot_seconds": 14400, "command_seconds": 60,
                               "review_state": "In Review", "gate_source": cmd("gate_source"), "ado": cmd("ado"),
                               "default": {"host_test": cmd("host_test", "{head}"),
                                           "code_review": cmd("code_review", "{pr}"),
                                           "second_review": cmd("second_review", "{branch}")}}
        self.sleeps: list[float] = []
        self.now = [1000.0]
        self.tick = 7.5  # each read: a review between 2 reads takes 7.5 s

        def monotonic():
            self.now[0] += self.tick
            return self.now[0]

        def sleep(seconds):
            self.sleeps.append(seconds)
            self.now[0] += seconds

        monkeypatch.setattr(deliver, "time", types.SimpleNamespace(sleep=sleep, monotonic=monotonic))
        st = state.load(self.cfg, "7")
        runs = state.item_dir(self.cfg, "7") / "runs"
        steps = []
        for n, role, verdict, line, text in (
                (1, "builder", "READY", f"BUILD: READY {self.head}", f"{SELF_CHECK}\n\nBUILD: READY {self.head}\n"),
                (2, "reviewer", "CLOSE", "REVIEW: CLOSE", "MUTANTS: 10/12 killed\n\nREVIEW: CLOSE\n")):
            folder = runs / f"{n:03d}-{role}"
            folder.mkdir(parents=True)
            (folder / "reply.txt").write_text(text, encoding="utf-8")
            steps.append({"n": n, "stage": "build" if n == 1 else "review", "role": role, "run_folder": str(folder),
                          "status": "OK", "last_line": line, "usd": 1.25 * n, "head_after": self.head,
                          "outcome": {"verdict": verdict}})
        st.update(status="ready", stage="delivery", steps=steps, pushed=self.head,
                  remote={"base_sha": base, "url": "https://github.com/example/widget.git"})
        state.save(self.cfg, st)
        self.plan("host_test", {"out": f"SUITE a passed=10 failed=0\nSUITE b passed=5 failed=1\n"
                                       f"HOST-TEST: {self.head} PASS\n"})
        self.plan("code_review", {"out": "the <b>review</b> output\n" + GOOD_CR + "\n"})
        self.plan("second_review", {"out": "the second output\n" + SECOND + "\n"})
        self.gate()

    def plan(self, name: str, *steps: dict) -> None:
        (self.fakes / f"{name}.json").write_text(json.dumps(list(steps)), encoding="utf-8")

    def gate(self, line: str | None = None, code: int = 0, stream: str = "") -> None:
        line = line or f"DELIVERY-CHECK: PASS feature/7 head={self.head}"
        source = (CHECK.replace("print(LINE)", f"print(LINE{stream})").replace("LOG", repr(str(self.fakes / "calls.jsonl"))).replace("LINE", repr(line))
                  .replace("CODE", str(code)))
        self.plan("gate_source", {"out": source})

    def calls(self, name: str | None = None) -> list[dict]:
        path = self.fakes / "calls.jsonl"
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        return [r for r in rows if name in (None, r["name"])]

    def state(self) -> dict:
        return state.load(self.cfg, "7")

    def set(self, **values) -> None:
        st = self.state()
        st.update(values)
        state.save(self.cfg, st)

    def reply(self, n: int, text: str) -> None:
        (Path(self.state()["steps"][n - 1]["run_folder"]) / "reply.txt").write_text(text, encoding="utf-8")

    def run(self) -> str:
        return deliver.deliver(self.cfg, "7")


@pytest.fixture
def item(tmp_path, monkeypatch):
    return Item(tmp_path, monkeypatch)


def names(it) -> list[str]:
    return [c["name"] for c in it.calls()]


def records(it) -> list[list[str]]:
    return [c["argv"] for c in it.calls("delivery_check")]


# --- AC 1: the command ---

def test_the_log_ends_with_1_result_line(item, capsys, monkeypatch):
    spawned = []
    monkeypatch.setattr(run, "spawn", lambda argv, log: spawned.append((argv, log)))
    assert cli.main(["deliver", "7"], cfg=item.cfg, personas=item.personas) == 0
    log = state.item_dir(item.cfg, "7") / "deliver.log"
    assert spawned == [([sys.executable, "-m", "wb", "deliver", "7", "--foreground"], log)]
    assert capsys.readouterr().out == f"WB: DELIVER 7 STARTED - log {log}\n"
    assert cli.main(["deliver", "7", "--foreground"], cfg=item.cfg, personas=item.personas) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [f"WB: DELIVER 7 MERGED {MERGE_SHA}"]
    assert re.fullmatch(r"WB: DELIVER 7 (MERGED [0-9a-f]{40}|WAIT .+|STOP .+)", lines[-1])


def test_an_error_still_ends_with_1_result_line(item, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("a broken\nfake")

    monkeypatch.setattr(push, "pull_request", boom)
    assert item.run() == "STOP RuntimeError: a broken fake"
    assert "Traceback" in capsys.readouterr().err


def test_a_live_lock_stops(item, monkeypatch, capsys):
    path = run.lock_path(item.cfg, "7")
    fd = run.take(path)
    try:
        assert item.run().startswith("STOP a run is going")
    finally:
        run.release(path, fd)
    assert item.calls() == [] and item.api.calls == []
    refusals = []
    real = push.pull_request

    def during(cfg, st, title, body):  # while deliver holds the lock: wb record and run-next refuse
        refusals.append(cli.main(["record", "7", "pr", "--number", "9"], cfg=item.cfg, personas=item.personas))
        with pytest.raises(run.Busy):
            run.take(path)
        return real(cfg, st, title, body)

    monkeypatch.setattr(push, "pull_request", during)
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert refusals == [1] and "WB: RECORD REFUSE 7 - a run is going" in capsys.readouterr().out
    assert not path.exists()  # released at the end


@pytest.mark.parametrize("status", ["running", "waiting"])
def test_an_item_that_is_not_ready_stops(item, status):
    item.set(status=status)
    assert item.run() == f"STOP item 7 is not ready (status {status})"
    assert item.calls() == [] and item.api.calls == []


# --- AC 2: the config ---

@pytest.mark.parametrize("key", ["default.host_test", "default", "gate_source", "ado"])
def test_an_empty_command_stops(item, key):
    table = item.cfg["deliver"]
    if key == "default":
        del table["default"]
    elif key.startswith("default."):
        table["default"]["host_test"] = []
    else:
        table[key] = []
    want = "deliver.default.host_test" if key.startswith("default") else f"deliver.{key}"
    assert item.run() == f"STOP {want} is not set"
    assert item.calls() == [] and item.api.calls == []  # before the first call: never a merge, then a stop


def test_an_unknown_id_stops_and_makes_no_folder(item):
    assert deliver.deliver(item.cfg, "nosuch").startswith("STOP no state of item nosuch")
    assert not state.item_dir(item.cfg, "nosuch").exists()


@pytest.mark.parametrize("key,value", [("retry_seconds", "300"), ("command_seconds", True), ("slot_seconds", 0),
                                       ("review_state", 3), ("ado", "ado"), ("gate_source", [1])])
def test_a_bad_type_is_a_usage_error(item, monkeypatch, capsys, key, value):
    spawned = []
    monkeypatch.setattr(run, "spawn", lambda argv, log: spawned.append(argv))
    item.cfg["deliver"][key] = value
    assert cli.main(["deliver", "7"], cfg=item.cfg, personas=item.personas) == 2
    assert f"deliver.{key}" in capsys.readouterr().err and spawned == []


def test_a_bad_type_of_a_repo_list_is_a_usage_error(item, monkeypatch, capsys):
    monkeypatch.setattr(run, "spawn", lambda argv, log: pytest.fail("spawned"))
    item.cfg["deliver"]["default"]["code_review"] = "a string"
    assert cli.main(["deliver", "7"], cfg=item.cfg, personas=item.personas) == 2
    assert "deliver.default.code_review" in capsys.readouterr().err


def test_the_words_are_filled_with_no_shell(item, monkeypatch):
    seen = []
    real = subprocess.run

    def spy(argv, **kwargs):
        seen.append((argv, kwargs))
        return real(argv, **kwargs)

    monkeypatch.setattr(deliver, "subprocess", types.SimpleNamespace(
        run=spy, DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired))
    item.cfg["deliver"]["default"]["host_test"].append("{head};x")
    item.cfg["deliver"]["default"]["host_test"].append("{id}|{branch}|{base}|{pr}|{other}")
    assert item.run() == f"MERGED {MERGE_SHA}"
    (host,) = item.calls("host_test")
    assert host["argv"] == [item.head, f"{item.head};x", "7|feature/7|main|5|{other}"]
    assert Path(host["cwd"]) == item.work
    assert seen and all(k["shell"] is False and k["cwd"] == str(item.work) and k["stdin"] == subprocess.DEVNULL
                        for _, k in seen)
    out = state.item_dir(item.cfg, "7") / "deliver" / item.head / "host_test.out"
    assert out.read_text(encoding="utf-8").endswith(f"HOST-TEST: {item.head} PASS\n")


def test_an_item_of_a_named_repo_runs_the_lists_of_its_subtable(item):
    st = item.state()
    st["work"]["repo"] = "other"
    state.save(item.cfg, st)
    lists = item.cfg["deliver"]
    lists["other"] = {k: [*v, "other"] for k, v in lists["default"].items()}
    lists["default"] = {k: [sys.executable, "-c", "raise SystemExit(9)"] for k in lists["default"]}
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert [(c["name"], c["argv"][-1]) for c in item.calls() if c["name"] in deliver.REPO_LISTS] == [
        ("host_test", "other"), ("code_review", "other"), ("second_review", "other")]


def test_a_command_past_its_limit_stops(item):
    item.cfg["deliver"]["command_seconds"] = 0.5
    item.cfg["deliver"]["default"]["host_test"] = [sys.executable, "-c", "import time; time.sleep(5)"]
    assert item.run() == "STOP host_test timed out after 0.5 s"


# --- AC 3: the push and the pull request ---

def test_an_open_pull_request_is_found_and_recorded(item):
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert item.api.methods() == ["GET", "PUT"]
    get = item.api.calls[0]
    assert get[1].startswith(f"{push.API}/repos/example/widget/pulls?") and "head=example%3Afeature%2F7" in get[1]
    assert item.state()["records"]["pr"]["number"] == 5
    assert item.calls("code_review")[0]["argv"] == ["5"]


def test_no_pull_request_opens_1(item):
    item.api.pulls.clear()
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert item.api.methods() == ["GET", "POST", "PUT"]
    assert item.api.calls[1][3]["head"] == "feature/7" and item.api.calls[1][3]["base"] == "main"
    assert item.state()["records"]["pr"]["number"] == 40
    assert item.api.calls[2][1].endswith("/repos/example/widget/pulls/40/merge")


def test_a_pull_request_to_another_base_is_not_merged(item):
    item.api.pulls[:] = [{"number": 9, "base": {"ref": "release"}}]
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert item.api.methods() == ["GET", "POST", "PUT"]
    assert "base=main" in item.api.calls[0][1].split("?")[1].split("&")
    assert item.api.calls[1][3]["base"] == "main"
    assert item.state()["records"]["pr"]["number"] == 41
    assert item.api.calls[2][1].endswith("/repos/example/widget/pulls/41/merge")
    assert item.calls("code_review")[0]["argv"] == ["41"]


def test_the_pull_request_to_the_base_is_found_after_another(item):
    item.api.pulls[:] = [{"number": 9, "base": {"ref": "release"}}, {"number": 5, "base": {"ref": "main"}}]
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert item.api.methods() == ["GET", "PUT"]
    assert item.api.calls[1][1].endswith("/repos/example/widget/pulls/5/merge")


def test_a_known_pull_request_number_is_not_recorded_again(item):
    st = item.state()
    st["records"]["pr"] = {"number": 5, "time": "2026-01-01T00:00:00Z"}
    state.save(item.cfg, st)
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert item.state()["records"]["pr"]["time"] == "2026-01-01T00:00:00Z"


def test_another_remote_head_stops_before_any_command(item):
    other = commit(item.work, "another")
    git(item.work, "push", "-q", "-f", str(item.remote), f"{other}:refs/heads/feature/7")
    git(item.work, "reset", "-q", "--hard", item.head)
    assert item.run() == "STOP the head is not pushed"
    assert item.calls() == [] and item.api.calls == []


@pytest.mark.parametrize("case", ["another head", "an untracked file", "a staged file"])
def test_a_work_folder_that_is_not_the_head_stops_before_any_call(item, case):
    if case == "another head":
        commit(item.work, "a commit after ready")
        why = f"the work folder is not at the head {item.head}"
    else:
        (item.work / "extra.py").write_text("x = 1\n", encoding="utf-8")
        if case == "a staged file":
            git(item.work, "add", "extra.py")
        why = (f"the work folder has changes that are not in {item.head}: "
               f"{state.item_dir(item.cfg, '7') / 'uncommitted.txt'}")
    assert item.run() == f"STOP {why}"
    assert item.calls() == [] and item.api.calls == []


def test_a_planted_git_filter_stops_before_any_git_call_in_the_work_folder(item, monkeypatch):
    """A step after the push scan can write .git: the check of the work folder runs guard.check_git first, so
    a filter there never runs on the host."""
    (item.work / "greeting.txt").write_text("hello\n", encoding="utf-8")
    git(item.work, "add", "greeting.txt")
    head = commit(item.work, "feat: a tracked file")  # the filter runs on a tracked file
    git(item.work, "push", "-q", "-f", str(item.remote), f"{head}:refs/heads/feature/7")
    st = item.state()
    st["steps"][-1]["head_after"] = head
    st["pushed"] = head
    state.save(item.cfg, st)
    marker = item.tmp / "the filter ran"
    git(item.work, "config", "filter.x.clean", f"{sys.executable} -c \"open({str(marker)!r}, 'w')\"")
    (item.work / ".git" / "info").mkdir(exist_ok=True)
    (item.work / ".git" / "info" / "attributes").write_text("* filter=x\n", encoding="utf-8")
    seen = []

    def spy(argv, **kwargs):
        seen.append(kwargs.get("cwd"))
        return subprocess.run(argv, **kwargs)

    monkeypatch.setattr(machine, "subprocess", types.SimpleNamespace(run=spy, DEVNULL=subprocess.DEVNULL))
    assert item.run() == "STOP the git config of the work folder has keys that a clone does not write: filter.x.clean"
    assert str(item.work) not in [str(c) for c in seen] and not marker.exists()
    assert item.calls() == [] and item.api.calls == []


@pytest.mark.parametrize("case", ["not recorded", "no branch"])
def test_a_head_that_is_not_pushed_stops(item, case):
    if case == "not recorded":
        item.set(pushed="cd34" * 10)
    else:
        git(item.remote, "update-ref", "-d", "refs/heads/feature/7")
    assert item.run() == "STOP the head is not pushed"
    assert item.calls() == [] and item.api.calls == []


# --- AC 4: the host test ---

def test_a_busy_host_test_waits_and_tries_again(item):
    busy = {"err": "refusing: no host-test slot\n", "code": 2}
    item.plan("host_test", busy, busy, {"out": f"SUITE x passed=3 failed=0\nHOST-TEST: {item.head} PASS\n"})
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert len(item.calls("host_test")) == 3 and item.sleeps == [300, 300]
    assert item.state()["records"]["gates"][0]["host"] == {"passed": 3, "failed": 0}


def test_a_host_test_busy_past_the_slot_time_stops(item):
    item.cfg["deliver"]["slot_seconds"] = 600
    item.plan("host_test", {"out": "refusing: no host-test slot\n", "code": 2})
    item.tick, start = 0, item.now[0]
    assert item.run() == "STOP no host-test slot after 600 s"
    # calls at 0 s and 300 s; at 600 s the deadline has passed: no 3rd call
    assert len(item.calls("host_test")) == 2 and item.sleeps == [300, 300] and item.now[0] - start == 600


@pytest.mark.parametrize("slot, sleeps", [(600, [300]), (500, [200])])
def test_the_slot_time_counts_the_time_of_the_busy_commands(item, monkeypatch, slot, sleeps):
    item.cfg["deliver"].update(slot_seconds=slot, command_seconds=7200)
    item.plan("host_test", {"out": "refusing: no host-test slot\n", "code": 2})
    item.tick, start, limits = 0, item.now[0], []
    call = deliver.Chain.call

    def busy_300_s(self, name, argv, seconds=None):
        limits.append(seconds)
        result = call(self, name, argv, seconds)
        item.now[0] += 300  # each busy command takes 300 s of the clock
        return result

    monkeypatch.setattr(deliver.Chain, "call", busy_300_s)
    assert item.run() == f"STOP no host-test slot after {slot} s"
    assert item.now[0] - start <= slot + 300  # not 1500 s: the busy calls count, the last sleep is cut
    assert len(item.calls("host_test")) == 1 and item.sleeps == sleeps and limits == [slot]


def test_exit_2_without_the_busy_text_stops_at_once(item):
    item.plan("host_test", {"out": "a usage error\n", "code": 2})
    assert item.run() == "STOP the host test: a usage error (exit 2)"
    assert item.sleeps == [] and names(item) == ["host_test"]


def test_a_failed_host_test_stops_with_its_line(item):
    item.plan("host_test", {"out": f"SUITE x passed=3 failed=1\nHOST-TEST: {item.head} FAIL\n", "code": 1})
    assert item.run() == f"STOP the host test: HOST-TEST: {item.head} FAIL (exit 1)"
    assert names(item) == ["host_test"]


def test_a_pass_line_with_a_red_exit_code_stops(item):
    item.plan("host_test", {"out": f"SUITE x passed=3 failed=0\nHOST-TEST: {item.head} PASS\n", "code": 1})
    assert item.run() == f"STOP the host test: HOST-TEST: {item.head} PASS (exit 1)"
    assert names(item) == ["host_test"]


def test_a_pass_of_another_sha_stops(item):
    item.plan("host_test", {"out": f"HOST-TEST: {'cd34' * 10} PASS\n"})
    assert item.run() == f"STOP the host test: HOST-TEST: {'cd34' * 10} PASS (exit 0)"


def test_the_host_counts_are_the_sums_of_the_suite_lines(item):
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert item.state()["records"]["gates"][0]["host"] == {"passed": 15, "failed": 1}
    assert records(item)[0][-2:] == ["tests", "15 passed"]


# --- AC 5: the 2 reviews ---

def test_green_reviews_go_on_and_s_is_the_wall_time(item):
    assert item.run() == f"MERGED {MERGE_SHA}"
    gate = item.state()["records"]["gates"][0]
    assert gate["review"] == {"findings": 1, "critical": 0, "high": 0, "medium": 0, "low": 1}
    assert gate["second"] == {"findings": 0, "critical": 0, "high": 0, "medium": 0, "low": 0, "seconds": 7.5}
    assert item.calls("second_review")[0]["argv"] == ["feature/7"]


@pytest.mark.parametrize("name", ["code_review", "second_review"])
def test_a_medium_finding_stops_for_a_rework_round(item, name):
    item.plan(name, {"out": "CODE-REVIEW: 3 findings (0 critical, 0 high, 2 medium, 1 low)\n"})
    assert item.run() == "STOP 2 medium findings: a rework round"
    assert "delivery_check" not in names(item) and "PUT" not in item.api.methods()


@pytest.mark.parametrize("line", ["CODE-REVIEW: 1 findings (1 critical, 0 high, 0 medium, 0 low)",
                                  "CODE-REVIEW: 2 findings (0 critical, 2 high, 0 medium, 0 low)"])
def test_a_critical_or_high_finding_stops(item, line):
    item.plan("code_review", {"out": line + "\n"})
    n = re.findall(r"[0-9]+", line)[0]
    assert item.run() == f"STOP {n} critical/high findings in the code review"


@pytest.mark.parametrize("plan,why", [({"out": "no line\n"}, "no line (exit 0)"),
                                      ({"out": GOOD_CR + "\n", "code": 1}, GOOD_CR + " (exit 1)"),
                                      ({"out": "CODE-REVIEW: 5 findings (0 critical, 0 high, 0 medium, 1 low)\n"},
                                       "CODE-REVIEW: 5 findings (0 critical, 0 high, 0 medium, 1 low) (exit 0)")])
def test_a_review_with_no_good_line_stops(item, plan, why):
    item.plan("code_review", plan)
    assert item.run() == f"STOP the code review: {why}"


def test_an_unavailable_second_review_goes_to_the_gate(item):
    item.plan("second_review", {"out": "UNAVAILABLE: the second model did not answer <x>\n", "code": 1})
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert item.state()["records"]["gates"][0]["second"] is None
    assert ["second-review", "UNAVAILABLE: the second model did not answer <x>"] in [r[-2:] for r in records(item)]
    comment = (state.item_dir(item.cfg, "7") / "deliver" / item.head / "comment.html").read_text()
    assert "<p>UNAVAILABLE: the second model did not answer &lt;x&gt;</p>" in comment and "<x>" not in comment


def test_an_unavailable_second_review_runs_again_after_a_stop(item):
    item.plan("second_review", {"out": "UNAVAILABLE: no answer\n"}, {"out": SECOND + "\n"})
    item.gate("DELIVERY-CHECK: REFUSE second-review", 1)
    assert item.run().startswith("STOP DELIVERY-CHECK: REFUSE")
    item.gate()
    (state.item_dir(item.cfg, "7") / "deliver" / item.head / "gate_source.done").unlink()
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert len(item.calls("second_review")) == 2 and len(item.calls("code_review")) == 1
    second = [r[-1] for r in records(item) if r[-2] == "second-review"]
    assert second == ["UNAVAILABLE: no answer", SECOND]
    assert item.state()["records"]["gates"][0]["second"]["findings"] == 0


def test_a_low_tier_item_has_no_second_review(item):
    item.set(tier="Low")
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert item.calls("second_review") == []
    assert len(records(item)) == 5 + 1 and "second-review" not in [r[-2] for r in records(item)]


# --- AC 6: the lines of the replies ---

@pytest.mark.parametrize("n,name", [(1, "SELF-CHECK"), (2, "MUTANTS")])
def test_a_reply_with_no_line_stops(item, n, name):
    item.reply(n, "a reply\n\nBUILD: READY x\n")
    path = Path(item.state()["steps"][n - 1]["run_folder"]) / "reply.txt"
    assert item.run() == f"STOP no {name} line in {path}"
    assert item.calls() == [] and item.api.calls == []


def test_the_no_mutants_form_and_the_rework_reply_count(item):
    st = item.state()
    folder = Path(st["steps"][0]["run_folder"]).with_name("003-rework")
    folder.mkdir()
    line = "SELF-CHECK: no mutants, by rework: a change of wording only"
    (folder / "reply.txt").write_text(f"{line}\nREWORK: DONE\n", encoding="utf-8")
    st["steps"].append({**st["steps"][0], "n": 3, "role": "rework", "run_folder": str(folder),
                        "outcome": {"verdict": "DONE"}})
    state.save(item.cfg, st)
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert ["self-check", line] in [r[-2:] for r in records(item)]


def test_0_mutants_stops(item):
    item.reply(2, "MUTANTS: 0/0 killed\nREVIEW: CLOSE\n")
    assert item.run().startswith("STOP MUTANTS: 0/0 killed: ")


# --- AC 7: the gate ---

def test_the_gate_records_6_checks_then_judges(item):
    assert item.run() == f"MERGED {MERGE_SHA}"
    folder = state.item_dir(item.cfg, "7") / "deliver" / item.head
    common = ["record", "--file", str(folder / "checks.jsonl"), "--sha", item.head]
    st = item.state()
    assert records(item) == [
        [*common, "tests", "15 passed"], [*common, "self-check", SELF_CHECK], [*common, "code-review", GOOD_CR],
        [*common, "reviewer-review", "REVIEW: CLOSE"], [*common, "second-review", SECOND],
        [*common, "--from-host-test", str(folder / "host_test.out")],
        ["gate", "--item", "7", "--branch", "feature/7", "--tier", "Medium", "--spec", st["spec"]["path"],
         "--repo", str(item.work), "--base", "main"]]
    (gate,) = st["records"]["gates"]
    assert gate["head"] == item.head and gate["reviewer"] == "CLOSE"
    assert gate["mutants"] == {"killed": 10, "total": 12}


def test_a_refused_gate_stops_with_its_line(item):
    item.gate(f"DELIVERY-CHECK: REFUSE feature/7 head={item.head}", 1)
    assert item.run() == f"STOP DELIVERY-CHECK: REFUSE feature/7 head={item.head} (exit 1)"
    assert "gates" not in item.state()["records"] and "PUT" not in item.api.methods()


def test_a_gate_pass_line_with_a_red_exit_code_stops(item):
    item.gate(code=1)
    assert item.run() == f"STOP DELIVERY-CHECK: PASS feature/7 head={item.head} (exit 1)"
    assert "gates" not in item.state()["records"] and "PUT" not in item.api.methods()


def test_a_pass_line_on_stderr_is_not_a_pass(item):
    item.gate(stream=", file=sys.stderr")
    assert item.run() == "STOP gh pr merge --match-head-commit HEAD (exit 0)"
    assert "gates" not in item.state()["records"]


def test_a_pass_line_of_another_head_stops(item):
    item.gate(f"DELIVERY-CHECK: PASS feature/7 head={'cd34' * 10}")
    assert item.run() == f"STOP DELIVERY-CHECK: PASS feature/7 head={'cd34' * 10} (exit 0)"
    assert "gates" not in item.state()["records"]


# --- AC 8: the merge ---

def test_the_merge_holds_the_head_and_the_text_of_wb(item):
    assert item.run() == f"MERGED {MERGE_SHA}"
    put = item.api.calls[-1]
    assert put[0] == "PUT" and put[1] == f"{push.API}/repos/example/widget/pulls/5/merge"
    st = item.state()
    assert put[3] == {"sha": item.head, "merge_method": "merge", "commit_title": push.pr_text(st)[0],
                      "commit_message": f"Item 7.\n\nDELIVERY-CHECK: PASS feature/7 head={item.head}\n\n"
                                        f"Head: {item.head}\n"}
    assert st["records"]["merge"]["sha"] == MERGE_SHA and st["records"]["merge"]["by"] == "main"
    assert st["status"] == "ready"


def test_the_guard_scans_the_merge_text_and_a_hit_waits(item, monkeypatch):
    seen = []

    def hit(cfg, st, what, extra=()):
        seen.append((what, list(extra)))
        return "Before the merge, the secret guard found a hit."

    monkeypatch.setattr(machine, "guard_stop", hit)
    assert item.run() == "WAIT Before the merge, the secret guard found a hit."
    assert seen == [("the merge", [item.state()["title"],
                                   f"Item 7.\n\nDELIVERY-CHECK: PASS feature/7 head={item.head}\n\nHead: {item.head}\n"])]
    assert "PUT" not in item.api.methods()
    st = item.state()
    assert (st["status"], st["question"]["case"]) == ("waiting", 2)


def test_a_high_tier_item_waits_with_no_merge(item):
    item.set(tier="High")
    assert item.run() == "WAIT a High tier item: the owner merges"
    st = item.state()
    assert "PUT" not in item.api.methods() and "merge" not in st["records"]
    assert (st["status"], st["question"]["case"]) == ("waiting", 2)
    comments = json.loads((state.item_dir(item.cfg, "7") / "tracker.json").read_text())["comments"]
    assert "a High tier item: the owner merges" in comments[-1]["text"]


def test_a_failed_tracker_write_still_saves_the_wait_and_ends_with_1_line(item, monkeypatch):
    def down(*a):
        raise RuntimeError("the tracker is down")

    monkeypatch.setattr(machine, "tell", down)
    item.set(tier="High")
    assert item.run() == "STOP RuntimeError: the tracker is down"
    st = item.state()
    assert (st["status"], st["question"]["text"]) == ("waiting", "a High tier item: the owner merges")


def test_a_moved_head_waits(item):
    item.api.merge_error = 409
    assert item.run() == f"WAIT the head moved: the pull request is not at {item.head}"
    assert "merge" not in item.state()["records"] and item.state()["status"] == "waiting"


def test_another_merge_error_stops_with_no_token(item):
    item.api.merge_error = 405
    token = Path(item.cfg["delivery"]["push_token_file"]).read_text().strip()
    result = item.run()
    assert result.startswith("STOP the merge call failed: HTTP Error 405") and token not in result
    assert item.state()["status"] == "ready"


def test_a_merge_with_a_lost_reply_is_found_by_the_next_run(item):
    """GitHub merged, but the reply was lost: the PUT of the next run gets 405, and the pull request at the
    gated head says merged."""
    item.api.merge_error = 405
    item.api.pull = {"number": 5, "merged": True, "head": {"sha": item.head}, "merge_commit_sha": MERGE_SHA}
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert item.api.methods() == ["GET", "PUT", "GET"]
    assert item.api.calls[2][1] == f"{push.API}/repos/example/widget/pulls/5"
    assert item.state()["records"]["merge"]["sha"] == MERGE_SHA
    assert len(item.calls("ado")) == 3


@pytest.mark.parametrize("pull", [
    {"merged": False, "head": {"sha": "HEAD"}, "merge_commit_sha": MERGE_SHA},
    {"merged": True, "head": {"sha": "cd34" * 10}, "merge_commit_sha": MERGE_SHA},
    {"merged": True, "head": {"sha": "HEAD"}, "merge_commit_sha": "not a sha"},
    None])
def test_a_refused_merge_that_is_not_merged_at_the_head_stops(item, pull):
    item.api.merge_error = 405
    item.api.pull = json.loads(json.dumps(pull).replace("HEAD", item.head))
    assert item.run().startswith("STOP the merge call failed: HTTP Error 405")
    assert "merge" not in item.state()["records"] and item.calls("ado") == []


def test_a_failed_merge_record_does_not_merge_again(item, monkeypatch):
    real, failed = deliver.record, []

    def once(cfg, item_id, **args):
        if args["kind"] == "merge" and not failed:
            failed.append(1)
            raise RuntimeError("the record failed")
        real(cfg, item_id, **args)

    monkeypatch.setattr(deliver, "record", once)
    assert item.run() == "STOP RuntimeError: the record failed"
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert item.api.methods().count("PUT") == 1
    assert item.state()["records"]["merge"]["sha"] == MERGE_SHA


# --- AC 9: ADO, and no follow-up ---

def test_ado_gets_the_state_the_cost_and_1_comment(item):
    assert item.run() == f"MERGED {MERGE_SHA}"
    ado = [c["argv"] for c in item.calls("ado")]
    folder = state.item_dir(item.cfg, "7") / "deliver" / item.head
    assert ado == [["state", "7", "In Review"], ["field", "7", "Custom.CostUSD", str(folder / "usd.txt")],
                   ["comment", "7", str(folder / "comment.html")]]
    assert (folder / "usd.txt").read_text() == "3.75\n"
    comment = (folder / "comment.html").read_text()
    for part in (MERGE_SHA, f"DELIVERY-CHECK: PASS feature/7 head={item.head}", GOOD_CR, SECOND,
                 "the &lt;b&gt;review&lt;/b&gt; output", "the second output"):
        assert part in comment, part
    assert "<b>" not in comment
    assert "follow_ups" not in item.state()["records"]


def test_a_failed_ado_call_stops_and_the_next_run_does_not_merge_again(item):
    """The merge record wins over the missing ado markers: MERGED with the recorded sha and 0 calls."""
    item.plan("ado", {"code": 0}, {"out": "no such field\n", "code": 1}, {"code": 0})
    assert item.run() == "STOP deliver.ado field: no such field (exit 1)"
    calls, api = len(item.calls()), len(item.api.calls)
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert len(item.calls()) == calls and len(item.api.calls) == api
    assert item.api.methods().count("PUT") == 1
    assert [c["argv"][0] for c in item.calls("ado")] == ["state", "field"]


# --- AC 10: start again ---

def test_a_stop_at_the_gate_then_a_pass_runs_no_host_test_or_review_again(item):
    item.gate("DELIVERY-CHECK: REFUSE something", 1)
    assert item.run().startswith("STOP DELIVERY-CHECK: REFUSE")
    first = len(records(item))
    item.gate()
    (state.item_dir(item.cfg, "7") / "deliver" / item.head / "gate_source.done").unlink()  # a new script
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert len(item.calls("host_test")) == len(item.calls("code_review")) == len(item.calls("second_review")) == 1
    assert len(records(item)) == first + 1  # only the gate again: each record of this head is done
    assert item.api.methods().count("GET") == 1


def test_a_merged_item_gives_merged_with_0_calls(item):
    assert item.run() == f"MERGED {MERGE_SHA}"
    calls, api = len(item.calls()), len(item.api.calls)
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert len(item.calls()) == calls and len(item.api.calls) == api


def test_a_merged_item_with_an_unavailable_second_review_gives_merged_with_0_calls(item):
    medium = "CODE-REVIEW: 1 findings (0 critical, 0 high, 1 medium, 0 low)"
    item.plan("second_review", {"out": "UNAVAILABLE: no answer\n"}, {"out": medium + "\n"})
    assert item.run() == f"MERGED {MERGE_SHA}"
    calls, api = len(item.calls()), len(item.api.calls)
    commit(item.work, "the work folder moves on after the merge")
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert len(item.calls()) == calls and len(item.api.calls) == api


def test_a_merge_record_with_no_markers_gives_merged_with_0_calls(item):
    """The merge record is the completed delivery: with the deliver folder gone, the moved work folder, the
    UNAVAILABLE second review that would now give a medium and the missing ado list are never reached."""
    medium = "CODE-REVIEW: 1 findings (0 critical, 0 high, 1 medium, 0 low)"
    item.plan("second_review", {"out": "UNAVAILABLE: no answer\n"}, {"out": medium + "\n"})
    assert item.run() == f"MERGED {MERGE_SHA}"
    calls, api = len(item.calls()), len(item.api.calls)
    shutil.rmtree(state.item_dir(item.cfg, "7") / "deliver")
    commit(item.work, "the work folder moves on after the merge")
    item.cfg["deliver"]["ado"] = []
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert len(item.calls()) == calls and len(item.api.calls) == api


def test_an_unavailable_second_review_is_not_asked_again_after_the_gate(item):
    item.plan("second_review", {"out": "UNAVAILABLE: no answer\n"}, {"out": SECOND + "\n"})
    item.plan("ado", {"code": 0}, {"out": "no such field\n", "code": 1}, {"code": 0})
    assert item.run() == "STOP deliver.ado field: no such field (exit 1)"
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert len(item.calls("second_review")) == 1
    comment = (state.item_dir(item.cfg, "7") / "deliver" / item.head / "comment.html").read_text()
    assert "<p>UNAVAILABLE: no answer</p>" in comment


def test_an_unavailable_second_review_is_reused_after_a_gate_pass_and_a_stop_before_the_merge(item):
    """A stop after the gate and before the merge record (a refused PUT): the next run takes the UNAVAILABLE
    result that the gate judged, and does not ask again (it would now give a medium and stop)."""
    medium = "CODE-REVIEW: 1 findings (0 critical, 0 high, 1 medium, 0 low)"
    item.plan("second_review", {"out": "UNAVAILABLE: no answer\n"}, {"out": medium + "\n"})
    item.api.merge_error = 405
    assert item.run().startswith("STOP the merge call failed: HTTP Error 405")
    item.api.merge_error = None
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert len(item.calls("second_review")) == 1 and len(item.calls("code_review")) == 1
    assert item.state()["records"]["gates"][0]["second"] is None


def test_a_merge_record_with_a_marker_for_another_head_gives_merged_with_0_calls(item):
    """A new head pushed after the merge of an older one: the merge record wins, whatever the markers say."""
    assert item.run() == f"MERGED {MERGE_SHA}"
    calls, api = len(item.calls()), len(item.api.calls)
    new = commit(item.work, "fix: rework")
    git(item.work, "push", "-q", str(item.remote), f"{new}:refs/heads/feature/7")
    st = item.state()
    st["steps"][-1]["head_after"] = new
    st["pushed"] = new
    state.save(item.cfg, st)
    item.plan("host_test", {"out": f"HOST-TEST: {new} PASS\n"})
    item.gate(f"DELIVERY-CHECK: PASS feature/7 head={new}")
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert not (state.item_dir(item.cfg, "7") / "deliver" / new).exists()
    assert len(item.calls()) == calls and len(item.api.calls) == api


# --- the events of wb notify: a merge and a stop of wb deliver ---

def event_lines(it, kind: str) -> list[dict]:
    path = state.home(it.cfg) / events.FILE
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    return [{k: v for k, v in r.items() if k not in ("v", "time")} for r in rows if r["kind"] == kind]


def test_a_merge_of_wb_deliver_writes_1_merge_event(item):
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert item.run() == f"MERGED {MERGE_SHA}"  # a merged item: 0 calls and no second line
    assert event_lines(item, "merge") == [{"kind": "merge", "item": "7", "sha": MERGE_SHA, "by": "main"}]
    assert event_lines(item, "stop") == []


def test_wb_record_and_wb_deliver_share_1_function_for_the_side_effects_of_a_record(item, monkeypatch, capsys):
    """A side effect added later to the record of the CLI reaches the record of wb deliver too."""
    seen, real = [], cli._apply

    def spy(cfg, args, st):
        seen.append(args.kind)
        return real(cfg, args, st)

    monkeypatch.setattr(cli, "_apply", spy)
    assert item.run() == f"MERGED {MERGE_SHA}"
    assert seen == ["pr", "gate", "merge"]
    assert cli.main(["record", "7", "follow-up", "--item", "9"], cfg=item.cfg, personas=item.personas) == 0
    assert seen == ["pr", "gate", "merge", "follow-up"]
    assert event_lines(item, "merge") == [{"kind": "merge", "item": "7", "sha": MERGE_SHA, "by": "main"}]


def notify_at_8(it, monkeypatch) -> tuple[dt.datetime, list[dict]]:
    """wb notify for the item at 8:00 in the zone, out of the quiet hours; its first run is done. The pushes."""
    zone = ZoneInfo("America/New_York")
    t8 = dt.datetime(2026, 11, 16, 8, tzinfo=zone).astimezone(dt.timezone.utc)  # out of the quiet hours
    monkeypatch.setattr(events, "_utc", lambda: t8.isoformat(timespec="seconds").replace("+00:00", "Z"))
    it.cfg["pages"]["time_zone"] = "America/New_York"
    it.cfg["notify"] = {"command": ["unused"], "page_url": "", "quiet": "22:00-06:30", "digests": ["07:00", "19:00"],
                        "fyi_minutes": 30, "remind_hours": 4}
    pushes = []
    monkeypatch.setattr(notify, "send", lambda command, push: pushes.append(push))
    assert notify.run(it.cfg, t8) == (0, 0, 0)  # the first run starts at the end of the file
    return t8, pushes


def test_a_stop_of_wb_deliver_writes_1_stop_event_and_wb_notify_sends_it(item, monkeypatch):
    t8, pushes = notify_at_8(item, monkeypatch)
    item.gate("DELIVERY-CHECK: REFUSE something", 1)
    result = item.run()
    assert result == "STOP DELIVERY-CHECK: REFUSE something (exit 1)"
    text = result[len("STOP "):]
    assert event_lines(item, "stop") == [{"kind": "stop", "item": "7", "text": text,
                                          "key": events.key("stop", f"{item.head}\n{text}")}]
    assert event_lines(item, "merge") == []
    assert notify.run(item.cfg, t8 + dt.timedelta(minutes=2)) == (1, 0, 0)
    assert [(p["title"], p["body"], p["priority"]) for p in pushes] == [
        ("#7 stopped", "DELIVERY-CHECK: REFUSE something (exit 1)", "high")]


def test_each_new_stop_of_an_item_alerts_and_the_same_stop_alerts_once(item, monkeypatch):
    """The key of a stop is its head and its reason, as the key of a wait is its case and its text."""
    t8, pushes = notify_at_8(item, monkeypatch)
    item.gate("DELIVERY-CHECK: REFUSE something", 1)
    assert item.run() == "STOP DELIVERY-CHECK: REFUSE something (exit 1)"
    assert notify.run(item.cfg, t8 + dt.timedelta(minutes=2))[0] == 1
    assert item.run() == "STOP DELIVERY-CHECK: REFUSE something (exit 1)"  # the same stop again
    assert notify.run(item.cfg, t8 + dt.timedelta(minutes=4))[0] == 0
    item.gate("DELIVERY-CHECK: REFUSE another thing", 1)
    (state.item_dir(item.cfg, "7") / "deliver" / item.head / "gate_source.done").unlink()
    assert item.run() == "STOP DELIVERY-CHECK: REFUSE another thing (exit 1)"
    assert notify.run(item.cfg, t8 + dt.timedelta(minutes=6))[0] == 1
    new = commit(item.work, "fix: rework")  # a rework round: the same reason at a new head is a new stop
    git(item.work, "push", "-q", str(item.remote), f"{new}:refs/heads/feature/7")
    st = item.state()
    st["steps"][-1]["head_after"] = new
    st["pushed"] = new
    state.save(item.cfg, st)
    item.plan("host_test", {"out": f"HOST-TEST: {new} PASS\n"})
    assert item.run() == "STOP DELIVERY-CHECK: REFUSE another thing (exit 1)"
    assert notify.run(item.cfg, t8 + dt.timedelta(minutes=7))[0] == 1
    path = state.home(item.cfg) / notify.STATE
    ns = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**ns, "offset": 0}), encoding="utf-8")  # each stop event read a second time
    assert notify.run(item.cfg, t8 + dt.timedelta(minutes=8))[0] == 0
    assert [(p["title"], p["body"]) for p in pushes] == [
        ("#7 stopped", "DELIVERY-CHECK: REFUSE something (exit 1)"),
        ("#7 stopped", "DELIVERY-CHECK: REFUSE another thing (exit 1)"),
        ("#7 stopped", "DELIVERY-CHECK: REFUSE another thing (exit 1)")]
    assert len({e["key"] for e in event_lines(item, "stop")}) == 3

def test_a_wait_of_wb_deliver_writes_its_wait_event_and_no_stop_event(item):
    item.set(tier="High")
    assert item.run() == "WAIT a High tier item: the owner merges"
    assert [e["text"] for e in event_lines(item, "wait")] == ["a High tier item: the owner merges"]
    assert event_lines(item, "stop") == [] and event_lines(item, "merge") == []


# --- AC 11: nothing else ---

def test_deliver_runs_only_its_lists_ls_remote_and_the_api(item, monkeypatch):
    seen = []

    def proxy(module):
        def spy(argv, **kwargs):
            seen.append(argv)
            return subprocess.run(argv, **kwargs)
        return types.SimpleNamespace(run=spy, DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired)

    monkeypatch.setattr(deliver, "subprocess", proxy(deliver))
    monkeypatch.setattr(push, "subprocess", proxy(push))
    monkeypatch.setattr(machine, "subprocess", proxy(machine))
    assert item.run() == f"MERGED {MERGE_SHA}"
    fake = str(item.fakes / "fake.py")
    check = str(state.item_dir(item.cfg, "7") / "deliver" / item.head / "delivery_check.py")
    kinds = []
    for argv in seen:
        if argv[:2] == [sys.executable, fake]:
            kinds.append(argv[2])
        elif argv[:2] == [sys.executable, check]:
            kinds.append("delivery_check")
        elif argv[:2] == ["git", "--no-replace-objects"] and argv[6] in ("rev-parse", "read-tree", "status"):
            kinds.append(argv[6])  # the check of the work folder
        else:
            assert argv[-3] == "ls-remote" and argv[0] == "git", argv
            kinds.append("ls-remote")
    assert kinds == ["rev-parse", "read-tree", "status", "ls-remote", "host_test", "code_review", "second_review", "gate_source"] + [
        "delivery_check"] * 7 + ["ado"] * 3
    source = Path(deliver.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^(import|from) (urllib|http|socket)", source, re.M) and "Popen" not in source
