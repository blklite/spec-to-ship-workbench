"""The stage machine: the prompt of each step, and `wb run-next`.

The prompt of a step is an Inputs block, then the stage prompt byte for byte.
Replies pass by path: the script reads only the status and the last line that
run_step gives, never a reply.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

from runner import ROOT, UsageError, config
from runner.step import _utc, run_step
from wb import events, guard, push, state, tracker

PROMPTS = {
    "spec-checker": "spec-check.md",
    "builder": "build.md",
    "reviewer": "review.md",
    "second-reviewer": "code-review.md",
    "rework": "rework.md",
}
STAGE_ROLES = {
    "spec-check": ("spec-checker",),
    "build": ("builder",),
    "review": ("reviewer", "second-reviewer"),
    "rework": ("rework",),
}
# The 4 cases of "When the workbench stops and asks" (process/stages.md)
CASES = {
    1: "the budget is not sufficient",
    2: "a decision is outside the decision rights",
    3: "the action is a High tier action",
    4: "the checks stay red after the allowed rework rounds",
}
REWORK_LINE = re.compile(r"REVIEW: REWORK - ([0-9]+) blocking")
SHA40 = re.compile(r"[0-9a-f]{40}")
APPROVALS = ("merge", "live", "high")
CODE_REVIEW_LINE = re.compile(r"CODE-REVIEW: [0-9]+ findings \(([0-9]+) critical, ([0-9]+) high,"
                              r" [0-9]+ medium, [0-9]+ low\)")


def reply_path(step: dict) -> str:
    """reply.txt, the text of each turn, else stdout.txt: another adapter, or a run folder from before reply.txt."""
    run = Path(step["run_folder"])
    return str(run / "reply.txt") if (run / "reply.txt").is_file() else str(run / "stdout.txt")


# The verdict of a step whose reply a later step needs
DONE_VERDICTS = {"spec-check": "GO", "build": "READY", "rework": "DONE"}


def _ok_steps(st: dict, stage: str) -> list[dict]:
    return [s for s in st["steps"] if s["stage"] == stage and s.get("outcome")
            and s["outcome"]["verdict"] == DONE_VERDICTS.get(stage, s["outcome"]["verdict"])]


def inputs(st: dict, role: str) -> list[dict]:
    """The earlier steps whose replies the step of `role` needs. A blocked step
    is no input: the step after an answer gets the replies of the step before it."""
    last = {stage: (_ok_steps(st, stage) or [None])[-1] for stage in DONE_VERDICTS}
    if role == "builder":
        return [s for s in [last["spec-check"]] if s]
    if role in ("reviewer", "second-reviewer"):
        build, rework = last["build"], last["rework"]
        return [s for s in [build, rework] if s and (s is build or not build or s["n"] > build["n"])]
    if role == "rework":
        since = max([s["n"] for s in (last["build"], last["rework"]) if s] or [0])
        return [s for s in _ok_steps(st, "review") if s["n"] > since]
    return []


def _writable(func, path, _) -> None:
    """The callback of the step folder delete when it is not POSIX: u+rwx, then again. Its full paths and its
    inner rmtree have the limits of PATH_MAX and of the open files, so POSIX takes clear_step_dir's tools.
    No symlink is followed: os.chmod would follow one, so the lstat and the islink check each path."""
    parent = os.path.dirname(path)
    os.chmod(parent, os.stat(parent).st_mode | 0o700)
    if not os.path.lexists(path):  # the outer rmtree can retry a folder that the callback removed
        return
    if not stat.S_ISDIR(os.lstat(path).st_mode):
        func(path)
        return
    os.chmod(path, 0o700)
    for root, dirs, _ in os.walk(path):  # os.walk enters no symlink, but lists one in dirs
        for d in dirs:
            if not os.path.islink(os.path.join(root, d)):
                os.chmod(os.path.join(root, d), 0o700)
    shutil.rmtree(path)


def clear_step_dir(folder: Path) -> None:
    """A step can leave a folder with no write bit in its home (a go module cache), or with no bits, at any depth.
    On POSIX chmod -R and rm -rf walk with no path or depth limit and follow no symlink. A folder that stays
    stops the step: the error names it."""
    why = []
    if os.name == "posix":
        for args in (["chmod", "-R", "u+rwx", "--", str(folder)], ["rm", "-rf", "--", str(folder)]):
            try:
                why.append(subprocess.run(args, capture_output=True, text=True, errors="replace").stderr.strip())
            except OSError as e:  # a tool that is not on PATH: rm still runs after chmod
                why.append(str(e))
    else:  # onexc is new in Python 3.12, and onerror is deprecated there
        try:
            shutil.rmtree(folder, **{"onexc" if sys.version_info >= (3, 12) else "onerror": _writable})
        except OSError as e:
            why.append(str(e))
    if os.path.lexists(folder):
        raise UsageError(" ".join([f"cannot delete the step folder {folder}.", *filter(None, why)]))


def step_dir(st: dict) -> Path:
    """The folder beside the work folder that a wrapped step gets: `home` (the
    step home) and `inputs` (copies of its inputs); scripts/wrap-step.sh binds it."""
    folder = Path(st["work"]["folder"])
    return folder.with_name(folder.name + ".step")


def drop_review_token(st: dict) -> str | None:
    """The wrapper cannot delete the review token of its step after a KILL (a
    timeout) or a reboot: delete it here. No symlink that the step made is
    followed, and a missing file is no error. None, or why the token can stay
    (a failed delete, a token folder that is a symlink): it must not stop the run."""
    folder = step_dir(st)
    try:
        for part in ("home", ".config", "workbench"):
            folder = folder / part
            if folder.is_symlink():  # a step that moved its token folder away: the path only, never a value
                return f"{folder} is a symlink, which the delete does not follow"
            if not folder.is_dir():
                return None
            folder.chmod(folder.stat().st_mode | 0o700)  # a step can take the bits of its own folder away
        token = folder / "review-token"
        if token.is_symlink() or not token.is_dir():  # a folder there goes with the step folder of the next step
            token.unlink(missing_ok=True)
    except OSError as e:  # the text of an OSError holds a path, never the content of a file
        return str(e)
    return None


def note_review_token(st: dict, n: int) -> None:
    why = drop_review_token(st)
    line = f"step {n}: the review token was not deleted: {why}"
    if why and line not in st["not_verified"]:  # a refused recovery runs again at each run-next
        st["not_verified"].append(line)


def _copy(src: str, dst: Path) -> str:
    shutil.copyfile(src, dst)
    return str(dst)


def write_prompt(st: dict, role: str, run_dir: Path, inputs_dir: Path | None = None) -> Path:
    """inputs_dir: for a wrapped step, which sees no state home, the inputs are
    copied there and the Inputs block names the copies."""
    spec, answers = st["spec"]["path"], list(st["answers"])
    replies = [(s, reply_path(s)) for s in inputs(st, role)]
    if inputs_dir is not None:
        spec = _copy(spec, inputs_dir / "spec.md")
        replies = [(s, _copy(p, inputs_dir / f"reply-{s['n']:03d}-{s['role']}.txt")) for s, p in replies]
        answers = [_copy(a, inputs_dir / f"answer-{k:03d}.md") for k, a in enumerate(answers, 1)]
    lines = ["# Inputs", "",
             f"- Item: {st['id']}",
             f"- Spec: {spec}",
             f"- Work folder: {st['work']['folder']}",
             f"- Branch: {st['work']['branch']}",
             f"- Base: {st['work']['base']}"]
    lines += [f"- Reply of step {s['n']} ({s['role']}): {p}" for s, p in replies]
    lines += [f"- Owner answer: {a}" for a in answers]
    prompt = Path(run_dir) / "prompt.md"
    prompt.write_bytes(("\n".join(lines) + "\n\n").encode("utf-8")
                       + (ROOT / "prompts" / PROMPTS[role]).read_bytes())
    return prompt


def outcome(st: dict, role: str, line: str | None) -> dict | None:
    """The verdict of a step with status OK, or None when its last line does
    not fit this item (another item id, a count of 0 blocking)."""
    line = line or ""
    if role == "spec-checker":
        if line == f"SPEC-CHECK {st['id']}: GO":
            return {"verdict": "GO"}
        if re.fullmatch(rf"SPEC-CHECK {re.escape(st['id'])}: QUESTIONS - .+ questions", line):
            return {"verdict": "QUESTIONS"}
    elif role == "builder":
        for verdict in ("READY", "BLOCKED"):
            if line.startswith(f"BUILD: {verdict} "):
                return {"verdict": verdict}
    elif role == "reviewer":
        if line == "REVIEW: CLOSE":
            return {"verdict": "CLOSE", "blocking": 0}
        m = REWORK_LINE.fullmatch(line)
        if m and int(m.group(1)) > 0:  # REWORK with 0 blocking contradicts itself: never a pass
            return {"verdict": "REWORK", "blocking": int(m.group(1))}
    elif role == "second-reviewer":
        m = CODE_REVIEW_LINE.fullmatch(line)
        if m:  # its critical and high findings are blocking
            return {"verdict": "CODE-REVIEW", "blocking": int(m.group(1)) + int(m.group(2))}
    elif role == "rework":
        if line == "REWORK: DONE":
            return {"verdict": "DONE"}
        if line.startswith("REWORK: BLOCKED - "):
            return {"verdict": "BLOCKED"}
    return None


def head(folder) -> str | None:
    r = subprocess.run([*guard.GIT, "rev-parse", "HEAD"], cwd=folder, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def check_work_tree(folder: str) -> None:
    """The work folder must be the top of a git work tree; it is made before the first step."""
    try:
        r = subprocess.run([*guard.GIT, "rev-parse", "--show-toplevel"], cwd=folder, capture_output=True, text=True)
    except OSError:
        r = None
    if r is None or r.returncode != 0 or Path(r.stdout.strip()).resolve() != Path(folder).resolve():
        raise UsageError(f"the work folder is not the top of a git work tree: {folder}")


def message(cfg: dict, q: dict) -> str:
    """The comment of a wait: the case, the question, the run folder; at most messages.max_chars."""
    limit = cfg["messages"]["max_chars"]
    tail = f" Run folder: {q['run_folder']}" if q.get("run_folder") else ""
    text = f"Waiting for the owner, case {q['case']} ({CASES[q['case']]}): {' '.join(q['text'].split())}"
    room = limit - len(tail)
    if len(text) > room:
        text = text[:max(room - 3, 0)] + "..."
    return (text + tail)[:limit]


def wait(cfg: dict, tr, st: dict, case: int, text: str, run_folder: str | None = None, **extra) -> None:
    ask(st, case, text, run_folder, **extra)
    tell(cfg, tr, st)


def ask(st: dict, case: int, text: str, run_folder: str | None = None, **extra) -> None:
    """The state part of a wait."""
    st.update(status="waiting", retried=False,
              question={"case": case, "text": text, "run_folder": run_folder, **extra})


def tell(cfg: dict, tr, st: dict) -> None:
    """The tracker part of a wait: the waiting state and the comment of the question."""
    q = st["question"]
    events.append(cfg, "wait", st["id"], case=q["case"], text=q["text"], key=events.key(q["case"], q["text"]))
    tr.set_state(st["id"], cfg["owner"]["waiting_state"])
    tr.comment(st["id"], message(cfg, q))


def roles_of(st: dict) -> tuple:
    roles = STAGE_ROLES[st["stage"]]
    # the second review runs at Medium and High tier only
    return roles[:1] if st["stage"] == "review" and st["tier"] == "Low" else roles


def next_role(st: dict) -> str:
    return next(r for r in roles_of(st) if r not in st["review_pass"])


def _over(spent, limit, percent) -> bool:
    return spent * 100 >= percent * limit


def time_left(st: dict) -> float:
    return st["limits"]["hours"] * 3600 - st["seconds"]


def step_timeout(cfg: dict, st: dict) -> float:
    return min(float(cfg["steps"]["max_seconds"]), time_left(st))


def budget_stop(cfg: dict, st: dict) -> str | None:
    """The reason to wait (case 1) before a step, or None."""
    p, lim = cfg["budget"]["stop_percent"], st["limits"]
    if _over(st["usd"], lim["usd"], p):
        return f"The item has spent ${st['usd']:.2f} of its ${lim['usd']:g} (the stop is at {p} percent)."
    if _over(st["seconds"], lim["hours"] * 3600, p) or time_left(st) <= 0:
        return (f"The item has used {st['seconds'] / 3600:.2f} of its {lim['hours']:g} hours"
                f" (the stop is at {p} percent).")
    return None


def budget_alerts(cfg: dict, tr, st: dict) -> None:
    """1 tracker comment the first time a limit reaches the alert share."""
    p, lim = cfg["budget"]["alert_percent"], st["limits"]
    for key, spent, limit, unit in (("usd", st["usd"], lim["usd"], "dollars"),
                                    ("time", st["seconds"] / 3600, lim["hours"], "hours"),
                                    ("rounds", st["rounds_used"], lim["rounds"], "rework rounds")):
        if key not in st["alerts"] and limit and _over(spent, limit, p):
            st["alerts"].append(key)
            events.append(cfg, "budget_alert", st["id"], limit=key)
            tr.comment(st["id"], f"Budget alert: {spent:.2f} of {limit:g} {unit} used, at or above {p} percent"
                                 " of the limit.")


def set_head(st: dict, step: dict, sha: str | None) -> None:
    step["head_after"] = sha
    for a in st["approvals"]:  # a later head voids a merge or live approval
        if a["kind"] != "high" and not a.get("void") and sha not in (None, a["head"]):
            a["void"] = _utc()


def run_one(cfg: dict, personas: dict, tr, st: dict, role: str) -> dict:
    n = len(st["steps"]) + 1
    run_dir = state.item_dir(cfg, st["id"]) / "runs" / f"{n:03d}-{role}"
    run_dir.mkdir(parents=True, exist_ok=True)
    runner = config.role_runner(personas, role)
    inputs_dir = None
    if config.runner_config(cfg, runner).get("wrap"):  # a new step home and inputs folder for each step
        if step_dir(st).exists():
            clear_step_dir(step_dir(st))
        (step_dir(st) / "home").mkdir(parents=True)
        inputs_dir = step_dir(st) / "inputs"
        inputs_dir.mkdir()
    prompt = write_prompt(st, role, run_dir, inputs_dir)
    folder = st["work"]["folder"]
    tr.set_state(st["id"], "In Review" if st["stage"] == "spec-check" else "Executing")
    before = head(folder)
    # the open step: a run that dies (a reboot) leaves it, and the next run-next ends it
    st["open_step"] = {"n": n, "stage": st["stage"], "role": role, "runner": runner, "run_folder": str(run_dir),
                       "head_before": before, "start": _utc()}
    state.save(cfg, st)
    if role == "builder":
        events.append(cfg, "build", st["id"])
    try:
        c = run_step(folder, prompt, step_timeout(cfg, st), runner, run_dir, cfg=cfg)
    except UsageError:  # the step did not start: nothing to end after a reboot
        st["open_step"] = None
        raise
    finally:
        note_review_token(st, n)
        state.save(cfg, st)
    st["open_step"] = None
    step = {"n": n, "stage": st["stage"], "role": role, "runner": runner, "run_folder": str(run_dir),
            "status": c["status"], "last_line": c["last_line"], "reason": c["reason"],
            "usd": c["usd"], "seconds": c["seconds"],
            "tokens": {k: c.get(f"{k}_tokens") for k in ("input", "cache_read", "cache_write", "output")},
            "head_before": before, "head_after": None,
            "outcome": outcome(st, role, c["last_line"]) if c["status"] == "OK" else None}
    st["steps"].append(step)
    set_head(st, step, head(folder))
    state.save(cfg, st)  # a death in the push after it must not reset a finished step
    return step


def _git(folder, *args: str, env=None) -> subprocess.CompletedProcess:
    return subprocess.run([*guard.GIT, *args], cwd=folder, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)


def record_diff(st: dict) -> None:
    """records.diff at ready: the files and lines of base...pushed. A git error is recorded, never a wait;
    a binary file counts as 1 file and 0 lines."""
    base, pushed = (st.get("remote") or {}).get("base_sha"), st.get("pushed")
    rec = {"base": base, "head": pushed}
    if not (base and pushed):
        error = "no base sha or no pushed head"
    else:
        try:
            r = _git(st["work"]["folder"], "diff", "--numstat", f"{base}...{pushed}")
            error = None if r.returncode == 0 else " ".join(r.stderr.split()) or f"git diff exited {r.returncode}"
        except OSError as e:
            error = " ".join(str(e).split())
    if error is None:
        rows = [line.split("\t", 2) for line in r.stdout.splitlines() if line]
        rec.update(files=len(rows), added=sum(int(a) for a, _, _ in rows if a != "-"),
                   deleted=sum(int(d) for _, d, _ in rows if d != "-"))
    else:
        rec["error"] = error[:200]
    rec["time"] = _utc()
    st["records"]["diff"] = rec


def remember_remote(cfg: dict, st: dict) -> str | None:
    """The base commit and the origin URL, read before the first step: a later
    step can rewrite the .git of the work folder, so the scan and the push use
    only these. None, or why the item waits before any step."""
    if st.get("remote"):
        return None
    folder = st["work"]["folder"]
    sha = _git(folder, "rev-parse", "--verify", "-q", f"origin/{st['work']['base']}^{{commit}}")
    url = _git(folder, "remote", "get-url", "origin")
    if sha.returncode != 0 or url.returncode != 0:
        return (f"The work folder has no origin or no origin/{st['work']['base']}: the base of the scan and the"
                " target of the push are not known.")
    st["remote"] = {"base_sha": sha.stdout.strip(), "url": url.stdout.strip()}
    state.save(cfg, st)
    return None


def recover(cfg: dict, tr, st: dict) -> None:
    """The open step of a run that died ends FAILED (interrupted): the work
    folder goes back to its head before the step, and the retry rule applies."""
    o = st.get("open_step")
    if not o:
        return
    note_review_token(st, o["n"])  # first: a check below can stop run-next
    state.save(cfg, st)
    folder = st["work"]["folder"]
    unsafe = guard.check_git(folder)
    if unsafe:
        raise UsageError(f"cannot reset the work folder after an interrupted step: {unsafe}")
    if o["head_before"]:
        for args in (("reset", "-q", "--hard", o["head_before"]), ("clean", "-q", "-fd")):
            r = _git(folder, *args)
            if r.returncode != 0:
                raise UsageError(f"cannot reset the work folder after an interrupted step: {r.stderr.strip()}")
    step = {"n": o["n"], "stage": o["stage"], "role": o["role"], "runner": o["runner"],
            "run_folder": o["run_folder"], "status": "FAILED", "last_line": None, "reason": "interrupted",
            "usd": None, "seconds": 0.0, "tokens": {k: None for k in ("input", "cache_read", "cache_write", "output")},
            "head_before": o["head_before"], "head_after": None, "outcome": None}
    st["steps"].append(step)
    st["open_step"] = None
    set_head(st, step, head(folder))
    st["not_verified"].append(f"step {o['n']}: interrupted: its dollars and time are not known")
    advance(cfg, tr, st, o["role"], step)
    state.save(cfg, st)


def guard_stop(cfg: dict, st: dict, what: str, extra: list[str] = ()) -> str | None:
    why = guard.scan(st, state.item_dir(cfg, st["id"]), extra)
    return f"Before {what}, {why}." if why else None


def uncommitted(cfg: dict, st: dict, sha: str) -> str | None:
    """None, or why: the review reads the work folder, so it must hold only the
    pushed tree. Files that git ignores do not count: no commit holds them, and
    a step that runs the tests leaves them (__pycache__, .pytest_cache; the guard
    removes these before). git status runs on a temporary index in the item
    folder, from read-tree HEAD: a change behind --skip-worktree or
    --assume-unchanged in the real index shows, with its path and no content.
    The text names at most 10 paths, sorted; uncommitted.txt in the item folder
    holds all of them, and a check with no path removes it. A path with a
    character below U+0020, U+007F, " or \\ is quoted, as json.dumps writes it.
    commit_rework runs this check too when the rework commits nothing."""
    folder, index = st["work"]["folder"], state.item_dir(cfg, st["id"]) / "status.index"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    index.unlink(missing_ok=True)
    try:  # the real index can hide a change behind --skip-worktree or --assume-unchanged
        for args in (("read-tree", "HEAD"), ("status", "--porcelain", "-z", "--untracked-files=all", "--no-renames")):
            r = _git(folder, *args, env=env)
            if r.returncode != 0:
                return f"Before the push, git {args[0]} failed: {' '.join(r.stderr.split())}"
    finally:
        index.unlink(missing_ok=True)
    paths = sorted({e[3:] for e in r.stdout.split("\0") if e})
    file = state.item_dir(cfg, st["id"]) / "uncommitted.txt"
    if not paths:
        file.unlink(missing_ok=True)
        return None
    # 1 line for each path: a control character, a quote or a backslash gives the JSON form
    paths = [json.dumps(p, ensure_ascii=False) if re.search(r'[\x00-\x1f\x7f"\\]', p) else p for p in paths]
    file.write_text("".join(p + "\n" for p in paths), encoding="utf-8")
    more = f" and {len(paths) - 10} more" if len(paths) > 10 else ""
    # the instruction before the list: the cut at messages.max_chars shortens only the list
    return (f"Before the push, the work folder has changes that are not in {sha}. Commit them or remove them,"
            f" then answer. All {len(paths)} paths are in {file}: {', '.join(paths[:10])}{more}.")


def deliver(cfg: dict, st: dict, sha: str) -> str | None:
    """The scan, then the push of `sha` and the pull request; None, or why the item waits."""
    title, body = push.pr_text(st)
    why = guard_stop(cfg, st, "the push", [title, body])  # whatever the guard of the runner
    if why:
        return why
    why = uncommitted(cfg, st, sha)  # the scan ran check_git
    if why:
        return why
    try:
        push.publish(cfg, st, sha, title, body)
    except push.PushError as e:
        return f"The push stopped: {e}"
    st["pushed"] = sha
    return None


def push_retry(st: dict) -> str | None:
    """The sha of the READY of the last step, a build whose push failed, while it is still the head of the work
    folder; else None. A head that changed after READY runs the build again."""
    step = (st["steps"] or [None])[-1]
    if (st["stage"] != "build" or not step or step["role"] != "builder"
            or (step["outcome"] or {}).get("verdict") != "READY"):
        return None
    sha = step["last_line"][len("BUILD: READY "):]
    return sha if sha == step["head_after"] == head(st["work"]["folder"]) and sha != st.get("pushed") else None


def commit_rework(cfg: dict, st: dict, step: dict) -> str | None:
    """The rework step does not commit: the script commits the work folder, then
    pushes. No change: no commit, and a push only of a head that is not pushed yet."""
    folder = st["work"]["folder"]
    unsafe = guard.check_git(folder)
    if unsafe:
        return f"Before the commit after the rework, {unsafe}."
    r = _git(folder, "add", "-A")
    if r.returncode == 0:
        r = _git(folder, "diff", "--cached", "--quiet")
        if r.returncode == 0:
            # an unpushed head (a push that failed before an answer, a commit of the step) is pushed now
            # add -A skips a change behind --skip-worktree or --assume-unchanged
            return (uncommitted(cfg, st, st["pushed"]) if step["head_after"] == st.get("pushed")
                    else deliver(cfg, st, step["head_after"]))
        if r.returncode == 1:
            r = _git(folder, "commit", "-q", "--no-verify", "-m",
                     f"fix: rework round {st['rounds_used'] + 1} (item {st['id']})")
    if r.returncode != 0:
        return f"The commit after the rework failed: {' '.join((r.stderr or r.stdout).split())}"
    set_head(st, step, head(folder))
    return deliver(cfg, st, step["head_after"])


def advance(cfg: dict, tr, st: dict, role: str, step: dict) -> None:
    """The transition after 1 step."""
    stage, line, folder = st["stage"], step["last_line"], step["run_folder"]
    if not step["outcome"]:
        events.append(cfg, "step_failed", st["id"], role=role, status=step["status"], reason=step["reason"])
        if role == "second-reviewer" and step["status"] == "UNAVAILABLE":
            # not verified, never a pass, and no stop
            st["not_verified"].append(f"step {step['n']}: the second review did not run: {step['reason']}")
            st["review_pass"][role] = None
        elif not st["retried"]:
            st["retried"] = True  # a step that is not OK runs once more
            return
        else:
            why = step["reason"] if step["status"] != "OK" else f"the last line does not fit item {st['id']}: {line}"
            return wait(cfg, tr, st, 2, f"The {role} step ended {step['status']} twice: {why}", folder)
    st["retried"] = False
    verdict = (step["outcome"] or {}).get("verdict")
    if stage == "spec-check":
        if verdict == "GO":
            st["stage"] = "build"
        else:
            wait(cfg, tr, st, 2, f"The spec check asks questions ({line}). Amend the spec, then run"
                                 " wb intake again.", folder, spec_questions=True)
    elif stage == "build":
        if verdict == "READY":
            sha = line[len("BUILD: READY "):]
            why = (f"The build reply names {sha}, but the head of the work folder is {step['head_after']}."
                   if sha != step["head_after"] else deliver(cfg, st, sha))
            if why:
                return wait(cfg, tr, st, 2, why, folder)
            st["stage"] = "review"
        else:
            wait(cfg, tr, st, 2, f"The build stopped: {line}", folder)
    elif stage == "review":
        if step["outcome"]:
            st["review_pass"][role] = step["outcome"]["blocking"]
        if any(r not in st["review_pass"] for r in roles_of(st)):
            return
        blocking = sum(v for v in st["review_pass"].values() if v)
        st["review_pass"] = {}
        if blocking == 0:
            st.update(stage="delivery", status="ready")
            events.append(cfg, "ready", st["id"], tier=st["tier"])
            record_diff(st)
            return
        st["stage"] = "rework"
        if st["rounds_used"] >= st["limits"]["rounds"]:
            wait(cfg, tr, st, 4, f"The review has {blocking} blocking findings after {st['rounds_used']}"
                                 " rework rounds.", folder)
    elif stage == "rework":
        if verdict == "DONE":
            why = commit_rework(cfg, st, step)
            if why:
                return wait(cfg, tr, st, 2, why, folder)
            st["rounds_used"] += 1
            st["stage"] = "review"
        else:
            wait(cfg, tr, st, 2, f"The rework stopped: {line}", folder)


def approved(st: dict, kind: str) -> bool:
    return any(a["kind"] == kind and not a.get("void") for a in st["approvals"])


def refusal(st: dict) -> str | None:
    """Why wb answer refuses an answer to the item, or None."""
    q = st["question"] or {}
    if st["status"] != "waiting":
        return f"item {st['id']} does not wait for the owner (status {st['status']})"
    if q.get("spec_questions"):
        return "the spec check asked questions: amend the spec, then run wb intake again"
    if q.get("budget"):  # an answer cannot raise a limit: only the owner's spec sets them
        return "the budget is spent: raise the limits in the spec, then run wb intake again"
    if q.get("case") == 3:
        return f"case 3 waits for wb approve {st['id']} high"
    return None


def answer(cfg: dict, item_id: str, file: str) -> str | None:
    """Store the owner's answer for the next step and set the item running.
    Returns None, or the reason of a refusal. It holds answer.flock from the load to the save."""
    if not state.exists(cfg, item_id):
        state.load(cfg, item_id)  # the usage error of a missing state, with no lock
    with state.answer_lock(cfg, item_id):
        st = state.load(cfg, item_id)
        why = refusal(st)
        if why:
            return why
        try:
            data = Path(file).read_bytes()
        except OSError as e:
            raise UsageError(f"cannot read the answer {file}: {e}") from e
        store(cfg, st, data)
    return None


def store(cfg: dict, st: dict, data: bytes) -> None:
    """Add data to the answers of the item and set it running."""
    item_id = st["id"]
    dest = state.item_dir(cfg, item_id) / "answers" / f"{len(st['answers']) + 1:03d}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    st["answers"].append(str(dest))
    st["records"].setdefault("answers", []).append(
        {"file": dest.name, "case": (st["question"] or {}).get("case"), "time": _utc()})
    st.update(status="running", question=None)
    state.save(cfg, st)


def last_head(st: dict) -> str | None:
    return next((s["head_after"] for s in reversed(st["steps"]) if s["head_after"]), None)


def work_head(folder: str) -> str | None:
    """The head of the work folder; None when the folder is not the top of a git work tree."""
    try:
        check_work_tree(folder)
    except UsageError:
        return None
    return head(folder)


def approve(cfg: dict, item_id: str, kind: str, head_sha: str | None, stdin, out) -> tuple[str | None, str | None]:
    """The only way an approval enters the state: the owner types the item id
    at a terminal. The head is the last head of the item (merge, live) or the head
    of the work folder (high); a given head_sha must equal it. Returns (None, the
    head), or (the reason of a refusal, None). It does not merge."""
    if not stdin.isatty():
        return "wb approve needs a terminal: the owner types it", None
    st = state.load(cfg, item_id)
    if kind not in APPROVALS:
        raise UsageError(f"unknown approval {kind!r} (known: {', '.join(APPROVALS)})")
    if head_sha is not None and not SHA40.fullmatch(head_sha):
        return "the head must be a full SHA: 40 hex characters", None
    if kind == "high":
        sha, source = work_head(st["work"]["folder"]), "the head of the work folder"
    else:
        sha, source = last_head(st), "the last head of the item"
    if sha is None:
        return "the item has no head yet: wb run-next makes it", None
    if head_sha not in (None, sha):
        return f"the head is not {source} ({sha})", None
    out.write(f"Approve {kind} of item {item_id} at {sha} ({source}). Type the item id: ")
    out.flush()
    if stdin.readline().strip() != item_id:
        return "the typed id is not the item id", None
    st["approvals"].append({"kind": kind, "head": sha, "time": _utc()})
    if kind == "high" and st["status"] == "waiting" and (st["question"] or {}).get("case") == 3:
        st.update(status="running", question=None)
    state.save(cfg, st)
    return None, sha


def run_next(cfg: dict, personas: dict, item_id: str, clock=time.monotonic) -> dict:
    """Run steps until the item waits or is ready; return the state."""
    st = state.load(cfg, item_id)
    tr = tracker.make(cfg)
    if st["status"] == "running":
        check_work_tree(st["work"]["folder"])
        why = remember_remote(cfg, st)
        if why:
            wait(cfg, tr, st, 2, why)
            state.save(cfg, st)
        recover(cfg, tr, st)
    mark = clock()
    while st["status"] == "running":
        if st["stage"] == "delivery":  # an answer after an unpriced last step
            st["status"] = "ready"
            events.append(cfg, "ready", st["id"], tier=st["tier"])
            record_diff(st)
            state.save(cfg, st)
            break
        sha = push_retry(st)
        if sha:  # only the push runs again: no paid step, and before the budget stop
            why = deliver(cfg, st, sha)
            if why:
                wait(cfg, tr, st, 2, why, st["steps"][-1]["run_folder"])
            else:
                st["stage"] = "review"
            state.save(cfg, st)
            continue
        why = budget_stop(cfg, st)
        if why:
            wait(cfg, tr, st, 1, why, budget=True)
            state.save(cfg, st)
            break
        if st["stage"] == "build" and st["tier"] == "High" and not approved(st, "high"):
            wait(cfg, tr, st, 3, f"Item {st['id']} is High tier: the build waits for"
                                 f" python -m wb approve {st['id']} high in the runner clone.")
            state.save(cfg, st)
            break
        role = next_role(st)
        if config.guard(config.runner_config(cfg, config.role_runner(personas, role))):
            why = guard_stop(cfg, st, f"the {role} step")
            if why:
                wait(cfg, tr, st, 2, why)
                state.save(cfg, st)
                break
        step = run_one(cfg, personas, tr, st, role)
        now = clock()
        st["seconds"] += now - mark  # the item time: the wall clock of run-next, without owner waits
        mark = now
        if isinstance(step["usd"], (int, float)):  # None: the runner gave no usage, as when it did not start
            st["usd"] += step["usd"]
        state.save(cfg, st)  # the totals before the push in advance: a death there keeps them
        advance(cfg, tr, st, role, step)
        budget_alerts(cfg, tr, st)
        tr.set_field(st["id"], cfg["tracker"]["subagent_cost_field"],
                     round(sum(s["usd"] for s in st["steps"] if isinstance(s["usd"], (int, float))), 6))
        if step["usd"] == "unpriced" and st["status"] != "waiting":
            wait(cfg, tr, st, 1, f"Step {step['n']} ({role}) is unpriced: cost/prices.json has no price for its"
                                 " model, so the dollars of the item are not known.", step["run_folder"])
        state.save(cfg, st)
    return st
