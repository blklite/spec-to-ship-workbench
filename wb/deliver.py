"""`wb deliver <id>`: the chain after ready, with no model (wb/README.md, "deliver").

The run holds the lock of the item (run.take) to its end, so `wb record` and
`run-next` refuse. It runs only the lists of `[deliver]`, the check of the work
folder (guard.check_git, git rev-parse and machine.uncommitted), `git ls-remote` and push.api, in
this order: the work folder check, the push check and the pull request, the host test,
the code review and the second review, the gate (delivery_check.py), the merge
and the ADO calls. deliver/<head>/ of the item folder holds the output of each
call and 1 marker (<name>.done) for each call that is done: a new run skips
them and starts at the first call that is not. The first red result ends the
run: STOP, or WAIT (machine.wait, case 2) for the owner.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

from runner import UsageError
from wb import cli, events, guard, machine, push, run, state, tracker

NUMBERS = ("retry_seconds", "slot_seconds", "command_seconds")
LISTS = ("gate_source", "ado")
REPO_LISTS = ("host_test", "code_review", "second_review")
FILLS = ("id", "head", "base", "branch", "pr")
BUSY = "refusing: no host-test slot"
SELF_CHECK = re.compile(r"SELF-CHECK: (?:[0-9]+ of [0-9]+ mutants killed, [0-9]+ survived, by (?:builder|rework)"
                        r"|no mutants, by (?:builder|rework): .+)")
MUTANTS = re.compile(r"MUTANTS: ([0-9]+)/([0-9]+) killed")


class Stop(Exception):
    """The run ends with STOP <text>."""


class Wait(Exception):
    """The run ends with WAIT <text>: the item waits for the owner (case 2)."""


def repo_key(st: dict) -> str:
    return st["work"].get("repo") or "default"


def check_config(cfg: dict, st: dict) -> None:
    """A value of a bad type is a usage error; a missing value stops at its call."""
    table = cfg.get("deliver", {})
    if not isinstance(table, dict):
        raise UsageError("the config key deliver must be a table: [deliver]")
    sub = table.get(repo_key(st), {})
    if not isinstance(sub, dict):
        raise UsageError(f"the config key deliver.{repo_key(st)} must be a table")
    for key in NUMBERS:
        value = table.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or value <= 0):
            raise UsageError(f"the config key deliver.{key} must be a number above 0")
    if not isinstance(table.get("review_state", ""), str):
        raise UsageError("the config key deliver.review_state must be a string")
    for key, value in [(k, table.get(k)) for k in LISTS] + [(f"{repo_key(st)}.{k}", sub.get(k)) for k in REPO_LISTS]:
        if value is not None and not (isinstance(value, list) and all(isinstance(w, str) for w in value)):
            raise UsageError(f"the config key deliver.{key} must be a list of strings")


def setting(cfg: dict, key: str):
    """deliver.<key> (a dotted key), or STOP when it is missing or empty."""
    node = cfg.get("deliver")
    for part in key.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    if node in (None, [], ""):
        raise Stop(f"deliver.{key} is not set")
    return node


def reply_line(step: dict | None, pattern: re.Pattern, name: str) -> tuple[str, re.Match]:
    """The last line of the reply of step that is a full match of pattern; STOP with none."""
    path = machine.reply_path(step) if step else "no step"
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines() if step else []
    except OSError:
        lines = []
    for line in reversed(lines):
        m = pattern.fullmatch(line.strip())
        if m:
            return line.strip(), m
    raise Stop(f"no {name} line in {path}")


def last_step(st: dict, roles: tuple, verdicts: tuple | None = None) -> dict | None:
    return next((s for s in reversed(st["steps"]) if s["role"] in roles and s.get("outcome")
                 and (verdicts is None or s["outcome"]["verdict"] in verdicts)), None)


def record(cfg: dict, item_id: str, **args) -> None:
    """1 record of `wb record`, made here: the CLI refuses while this run holds the lock. cli._apply makes the
    side effects of `wb record` (a merge writes the `merge` event)."""
    with state.answer_lock(cfg, item_id):
        cli._apply(cfg, argparse.Namespace(**args), state.load(cfg, item_id))


class Chain:
    def __init__(self, cfg: dict, st: dict, head: str):
        self.cfg, self.st, self.head = cfg, st, head
        self.dir = state.item_dir(cfg, st["id"]) / "deliver" / head
        self.dir.mkdir(parents=True, exist_ok=True)
        self.fills = {"id": st["id"], "head": head, "base": st["work"]["base"], "branch": st["work"]["branch"],
                      "pr": ""}

    def done(self, name: str):
        path = self.dir / f"{name}.done"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def mark(self, name: str, data=True) -> None:
        state.write_json(self.dir / f"{name}.done", data)

    def call(self, name: str, argv: list[str], seconds: float | None = None) -> tuple[int, str, str, str]:
        """Run argv with no shell in the work folder; (exit code, stdout, stderr, the last line of stdout). The
        output stays in the folder. The limit is command_seconds, or seconds when it is less."""
        seconds = min(setting(self.cfg, "command_seconds"), seconds or math.inf)
        try:
            r = subprocess.run(argv, cwd=self.st["work"]["folder"], stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=seconds, shell=False)
        except subprocess.TimeoutExpired:
            raise Stop(f"{name} timed out after {seconds:g} s") from None
        except OSError as e:
            raise Stop(f"{name} did not run: {e}") from None
        (self.dir / f"{name}.out").write_text(r.stdout, encoding="utf-8")
        (self.dir / f"{name}.err").write_text(r.stderr, encoding="utf-8")
        lines = [line.strip() for line in r.stdout.splitlines() if line.strip()]
        return r.returncode, r.stdout, r.stderr, lines[-1] if lines else ""

    def command(self, name: str, key: str, extra: list[str] = (),
                seconds: float | None = None) -> tuple[int, str, str, str]:
        """The list deliver.<key>, each word filled, then extra."""
        words = list(setting(self.cfg, key))
        for k in FILLS:
            words = [w.replace(f"{{{k}}}", str(self.fills[k])) for w in words]
        return self.call(name, words + list(extra), seconds)

    def work_folder(self) -> None:
        """The host test, the reviews and the gate read the work folder: its HEAD must be the head, with no change
        that is not in it (machine.uncommitted, as before a review). guard.check_git first: a step can write .git."""
        unsafe = guard.check_git(self.st["work"]["folder"])
        if unsafe:
            raise Stop(unsafe)
        r = machine._git(self.st["work"]["folder"], "rev-parse", "HEAD")
        if r.returncode != 0 or r.stdout.strip() != self.head:
            raise Stop(f"the work folder is not at the head {self.head}")
        file = state.item_dir(self.cfg, self.st["id"]) / "uncommitted.txt"
        file.unlink(missing_ok=True)
        why = machine.uncommitted(self.cfg, self.st, self.head)
        if why:
            raise Stop(f"the work folder has changes that are not in {self.head}: {file}" if file.is_file() else why)

    def pushed(self) -> None:
        if self.done("push"):
            return
        try:
            if self.st.get("pushed") != self.head or push.remote_head(self.cfg, self.st, self.dir) != self.head:
                raise Stop("the head is not pushed")
        except push.PushError as e:
            raise Stop(str(e)) from None
        self.mark("push")

    def pull_request(self) -> None:
        number = self.done("pr")
        if number is None:
            try:
                number = push.pull_request(self.cfg, self.st, *push.pr_text(self.st))
            except push.PushError as e:
                raise Stop(str(e)) from None
            if not isinstance(number, int) or isinstance(number, bool) or number < 1:
                raise Stop("the pull request has no number")
            if (self.st["records"].get("pr") or {}).get("number") != number:
                record(self.cfg, self.st["id"], kind="pr", number=number)
            self.mark("pr", number)
        self.fills["pr"] = number

    def host_test(self) -> dict:
        result = self.done("host_test")
        if result:
            return result
        key, slot = f"{repo_key(self.st)}.host_test", setting(self.cfg, "slot_seconds")
        deadline = time.monotonic() + slot  # the busy calls count too, not only the sleeps

        def left() -> float:
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise Stop(f"no host-test slot after {slot:g} s")
            return seconds

        while True:
            code, out, err, last = self.command("host_test", key, seconds=left())
            if not (code == 2 and BUSY in out + err):
                break
            time.sleep(min(setting(self.cfg, "retry_seconds"), left()))
        if code != 0 or last != f"HOST-TEST: {self.head} PASS":
            raise Stop(f"the host test: {last or 'no output'} (exit {code})")
        suites = [line for line in out.splitlines() if line.startswith("SUITE")]
        result = {name: sum(int(n) for line in suites for n in re.findall(rf"\b{name}=([0-9]+)", line))
                  for name in ("passed", "failed")}
        self.mark("host_test", result)
        return result

    def review(self, name: str) -> dict:
        """{"line", "seconds", "output"} of the code review or the second review; a second review may be
        UNAVAILABLE, which the gate judges: after a pass of the gate, a new run takes that result again."""
        result = self.done(name) or (self.done("gate") and self.done(f"{name}-unavailable"))
        if result:
            return result
        start = time.monotonic()
        code, _, _, last = self.command(name, f"{repo_key(self.st)}.{name}")
        result = {"line": last, "seconds": time.monotonic() - start, "output": f"{name}.out"}
        if not (name == "second_review" and last.startswith("UNAVAILABLE")):
            what = name.replace("_", " ")
            try:
                counts = cli._review(last) if code == 0 and machine.CODE_REVIEW_LINE.fullmatch(last) else None
            except ValueError:
                counts = None
            if counts is None:
                raise Stop(f"the {what}: {last or 'no output'} (exit {code})")
            if counts["critical"] + counts["high"]:
                raise Stop(f"{counts['critical'] + counts['high']} critical/high findings in the {what}")
            if counts["medium"]:
                raise Stop(f"{counts['medium']} medium findings: a rework round")
            self.mark(name, result)
        else:  # UNAVAILABLE is not green: a new run asks again, until the gate passes
            self.mark(f"{name}-unavailable", result)
        return result

    def gate(self, host: dict, self_check: str, review: dict, reviewer_line: str, second: dict | None,
             mutants: dict) -> str:
        """The 6 records of delivery_check.py at the head, then its gate; then `record gate`."""
        result = self.done("gate")
        if result:
            return result
        script = self.dir / "delivery_check.py"
        if not self.done("gate_source"):
            code, out, _, last = self.command("gate_source", "gate_source")
            if code != 0:
                raise Stop(f"deliver.gate_source: {last or 'no output'} (exit {code})")
            script.write_text(out, encoding="utf-8")
            self.mark("gate_source")
        common = [sys.executable, str(script), "record", "--file", str(self.dir / "checks.jsonl"), "--sha", self.head]
        rows = [("tests", f"{host['passed']} passed"), ("self-check", self_check), ("code-review", review["line"]),
                ("reviewer-review", reviewer_line)] + ([("second-review", second["line"])] if second else [])
        records = [(f"record-{check}", [*common, check, value]) for check, value in rows]
        records.append(("record-host-test", [*common, "--from-host-test", str(self.dir / "host_test.out")]))
        for name, argv in records:
            if self.done(name) != argv[-1]:  # a new result of a check (a second review that ran again) records again
                code, _, _, last = self.call(name, argv)
                if code != 0:
                    raise Stop(f"delivery_check.py {name}: {last or 'no output'} (exit {code})")
                self.mark(name, argv[-1])
        st, work = self.st, self.st["work"]
        code, out, _, last = self.call("gate", [sys.executable, str(script), "gate", "--item", st["id"], "--branch",
                                             work["branch"], "--tier", st["tier"], "--spec", st["spec"]["path"],
                                             "--repo", work["folder"], "--base", work["base"]])
        line = f"DELIVERY-CHECK: PASS {work['branch']} head={self.head}"
        if code != 0 or line not in (x.strip() for x in out.splitlines()):
            why = next((x.strip() for x in out.splitlines() if x.startswith("DELIVERY-CHECK:")), last)
            raise Stop(f"{why or 'no output'} (exit {code})")
        available = second and not second["line"].startswith("UNAVAILABLE")
        record(self.cfg, st["id"], kind="gate", host_passed=host["passed"], host_failed=host["failed"],
               review=cli._review(review["line"]), reviewer="CLOSE" if reviewer_line == "REVIEW: CLOSE" else "REWORK",
               mutants=mutants, second=cli._review(second["line"]) if available else None,
               second_seconds=second["seconds"] if available else None)
        self.mark("gate", line)
        return line

    def merge(self, gate_line: str) -> str:
        """The merge at the gated head with the text of wb, after the secret guard; WAIT when the owner merges."""
        result = self.done("merge")
        if result:
            return result
        sha = self.done("merge-put")  # merged on the forge; the record may not be done
        if not sha:
            sha = self.put(gate_line)
            self.mark("merge-put", sha)
        record(self.cfg, self.st["id"], kind="merge", sha=sha, time=None, by="main")
        self.mark("merge", sha)
        return sha

    def put(self, gate_line: str) -> str:
        """The PUT of the merge, after the tier and the secret guard; the merge sha."""
        if self.st["tier"] == "High":
            raise Wait("a High tier item: the owner merges")
        title = push.pr_text(self.st)[0]
        body = f"Item {self.st['id']}.\n\n{gate_line}\n\nHead: {self.head}\n"
        why = machine.guard_stop(self.cfg, self.st, "the merge", [title, body])
        if why:
            raise Wait(why)
        try:
            return push.merge(self.cfg, self.st, self.fills["pr"], self.head, title, body)
        except push.Moved as e:
            raise Wait(str(e)) from None
        except push.PushError as e:
            try:  # an earlier call merged at this head, and its reply was lost
                sha = push.merged_at(self.cfg, self.st, self.fills["pr"], self.head)
            except push.PushError:
                sha = None
            if not sha:
                raise Stop(str(e)) from None
            return sha

    def ado(self, sha: str, gate_line: str, reviews: list[dict]) -> None:
        """The review state, the cost field and 1 comment, by the list deliver.ado."""
        usd = self.dir / "usd.txt"
        usd.write_text(f"{round(sum(s['usd'] for s in self.st['steps'] if isinstance(s['usd'], (int, float))), 6)}\n",
                       encoding="utf-8")
        parts = [f"Merged: {sha}", gate_line] + [r["line"] for r in reviews]
        text = "".join(f"<p>{html.escape(p)}</p>\n" for p in parts) + "".join(
            f"<pre>{html.escape((self.dir / r['output']).read_text(encoding='utf-8'))}</pre>\n" for r in reviews)
        comment = self.dir / "comment.html"
        comment.write_text(text, encoding="utf-8")
        item = self.st["id"]
        for name, words in (("ado-state", ["state", item, setting(self.cfg, "review_state")]),
                            ("ado-field", ["field", item, self.cfg["tracker"]["subagent_cost_field"], str(usd)]),
                            ("ado-comment", ["comment", item, str(comment)])):
            if not self.done(name):
                code, _, _, last = self.command(name, "ado", words)
                if code != 0:
                    raise Stop(f"deliver.ado {words[0]}: {last or 'no output'} (exit {code})")
                self.mark(name)


def merged(st: dict) -> str | None:
    """The sha of records.merge: a merge record is a completed delivery, whatever the markers say."""
    return (st["records"].get("merge") or {}).get("sha")


def chain(cfg: dict, item_id: str) -> str:
    st = state.load(cfg, item_id)
    if st["status"] != "ready":
        raise Stop(f"item {item_id} is not ready (status {st['status']})")
    sha = merged(st)
    if sha:  # before the head, the config, the replies, the work folder, any marker and any call
        return f"MERGED {sha}"
    head = machine.last_head(st)
    if not head:
        raise Stop(f"item {item_id} has no head")
    check_config(cfg, st)
    self_check, _ = reply_line(last_step(st, ("builder", "rework"), ("READY", "DONE")), SELF_CHECK, "SELF-CHECK")
    reviewer = last_step(st, ("reviewer",))
    _, m = reply_line(reviewer, MUTANTS, "MUTANTS")
    try:
        mutants = cli._mutants(f"{m.group(1)}/{m.group(2)}")
    except ValueError:
        raise Stop(f"{m.group(0)}: the gate needs at least 1 mutant and no more killed than mutants") from None
    for key in NUMBERS + ("review_state",) + LISTS + tuple(
            f"{repo_key(st)}.{k}" for k in REPO_LISTS if k != "second_review" or st["tier"] != "Low"):
        setting(cfg, key)  # before the first call: never a merge, then a stop for a missing list
    c = Chain(cfg, st, head)
    if not c.done("gate"):  # after the gate no call reads the work folder
        c.work_folder()
    c.pushed()
    c.pull_request()
    host = c.host_test()
    review = c.review("code_review")
    second = c.review("second_review") if st["tier"] != "Low" else None  # as machine.roles_of
    gate_line = c.gate(host, self_check, review, reviewer["last_line"], second, mutants)
    sha = c.merge(gate_line)
    c.ado(sha, gate_line, [r for r in (review, second) if r])
    return f"MERGED {sha}"


def wait(cfg: dict, item_id: str, text: str) -> str:
    """machine.wait, case 2, with the save before the tracker write as in run.stopped."""
    with state.answer_lock(cfg, item_id):
        st = state.load(cfg, item_id)
        machine.ask(st, 2, text)
        state.save(cfg, st)
    machine.tell(cfg, tracker.make(cfg), st)
    return f"WAIT {text}"


def deliver(cfg: dict, item_id: str) -> str:
    """The foreground run: the lock, the chain; MERGED <sha40>, WAIT <reason> or STOP <reason>. A STOP writes the
    `stop` event that `wb notify` sends, with the reason as its text and a key of the head and the reason: each new
    STOP of an item alerts, and the same STOP alerts once, as a wait (machine.tell)."""
    result = result_of(cfg, item_id)
    if result.startswith("STOP "):
        text = result[len("STOP "):]
        events.append(cfg, "stop", item_id, text=text, key=events.key("stop", f"{head_of(cfg, item_id)}\n{text}"))
    return result


def head_of(cfg: dict, item_id: str) -> str:
    """The last head of the item; empty for an unknown id or an item with no head."""
    try:
        return machine.last_head(state.load(cfg, item_id)) or ""
    except UsageError:
        return ""


def result_of(cfg: dict, item_id: str) -> str:
    try:
        state.load(cfg, item_id)  # an unknown id makes no item folder
    except UsageError as e:
        return f"STOP {e}"
    path = run.lock_path(cfg, item_id)
    try:
        fd = run.take(path)
    except run.Busy as e:
        return f"STOP {e}"
    try:
        try:
            return chain(cfg, item_id)
        except Wait as e:
            return wait(cfg, item_id, str(e))
    except Stop as e:
        return f"STOP {e}"
    except Exception as e:  # the log still ends with 1 result line
        traceback.print_exc()
        return f"STOP {type(e).__name__}: {' '.join(str(e).split())[:300]}"
    finally:
        run.release(path, fd)
