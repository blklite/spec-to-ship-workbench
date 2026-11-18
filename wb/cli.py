"""The wb command line (see wb/README.md).

    python -m wb intake <spec> --id <id> [--branch <branch>] [--repo <name>]
    python -m wb run-next <id> [--foreground]
    python -m wb answer <id> --file <file>
    python -m wb hold <id> (--reason <text> [--after <id2>] | --clear)
    python -m wb approve <id> <merge|live|high> [--head <sha40>]
    python -m wb status [<id>] [--json]
    python -m wb pages --out <dir> [--answer-url <url>]
    python -m wb pull-answers --queue <dir>
    python -m wb record <id> <pr|gate|merge|follow-up> [options]
    python -m wb notify

Each change appends 1 line to events.jsonl of the state home (wb/events.py): {"v": 1, "time", "kind", "item",
...}, with the kinds wait (case, text, key), step_failed (role, status, reason), budget_alert (limit), ready
(tier), build, intake and merge (sha, by). notify runs notify.command with each push as JSON on stdin; its keys are
notify.command, notify.page_url, notify.quiet, notify.digests, notify.fyi_minutes and notify.remind_hours.
    python -m wb deliver <id> [--foreground]

A usage error (a bad argument, config or state) prints a message on stderr and
exits 2.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys

from runner import UsageError, config
from runner.step import _utc
from wb import events, intake, machine, notify, pages, pull, run, state, status
from wb import deliver, intake, machine, pages, pull, run, state, status


def _intake(args, cfg, personas) -> int:
    problems = intake.intake(cfg, args.spec, args.id, args.branch, args.repo)
    for p in problems:
        print(p)
    if problems:
        print(f"WB: INTAKE REFUSE {args.id} - {len(problems)} problems")
        return 1
    print(f"WB: INTAKE OK {args.id}")
    events.append(cfg, "intake", args.id)
    return 0


def result_line(st: dict) -> str:
    if st["status"] == "ready":
        return f"WB: READY {st['id']}"
    q = st["question"]
    return f"WB: WAITING {st['id']} - case {q['case']}: {' '.join(q['text'].split())}"


def _run_next(args, cfg, personas) -> int:
    if args.foreground:  # the detached child; also a run at the terminal
        try:
            st = run.run_locked(cfg, personas, args.id)
        except run.Busy as e:
            print(f"WB: RUN REFUSE {args.id} - {e}")
            return 1
        print(result_line(st))
        return 0
    st = state.load(cfg, args.id)  # a bad id or a missing item is a usage error here, not in the log
    if st["status"] != "running":  # nothing to run: no child
        print(result_line(st))
        return 0
    pid = run.holder(run.lock_path(cfg, args.id))
    if pid is not None:
        print(f"WB: RUN REFUSE {args.id} - {run.Busy(pid)}")
        return 1
    log = run.log_path(cfg, args.id)
    # shortcut: 2 run-next within the start time of the child both print STARTED, and the second child
    # refuses in the log; upgrade if a front end starts runs in a loop
    run.spawn([sys.executable, "-m", "wb", "run-next", args.id, "--foreground"], log)
    print(f"WB: STARTED {args.id} - log {log}")
    return 0


def _deliver(args, cfg, personas) -> int:
    if args.foreground:  # the detached child; also a run at the terminal
        print(f"WB: DELIVER {args.id} {deliver.deliver(cfg, args.id)}")
        return 0
    deliver.check_config(cfg, state.load(cfg, args.id))  # a bad id or a bad type is a usage error here, not in the log
    log = state.item_dir(cfg, args.id) / "deliver.log"
    run.spawn([sys.executable, "-m", "wb", "deliver", args.id, "--foreground"], log)
    print(f"WB: DELIVER {args.id} STARTED - log {log}")
    return 0


def _answer(args, cfg, personas) -> int:
    why = machine.answer(cfg, args.id, args.file)
    if why:
        print(f"WB: ANSWER REFUSE {args.id} - {why}")
        return 1
    print(f"WB: ANSWER OK {args.id}")
    return 0


def _hold(args, cfg, personas) -> int:
    """Queue a wait with a reason, or clear the mark: only question.queued changes, and no tracker call."""
    if args.clear and args.after is not None:
        raise UsageError("hold --clear takes no --after")
    if args.after is not None:
        state.check_id(args.after)
    reason = " ".join((args.reason or "").split())
    state.load(cfg, args.id)  # the usage error of a bad id or a missing state, with no lock
    with state.answer_lock(cfg, args.id):  # an answer that overlaps never loses its save
        st = state.load(cfg, args.id)
        q = st["question"] if isinstance(st.get("question"), dict) else None
        pid = run.holder(run.lock_path(cfg, args.id))
        if st["status"] != "waiting" or q is None:
            why = f"item {args.id} does not wait for the owner (status {st['status']})"
        elif q.get("case") in (3, 4):
            why = f"a case {q['case']} wait is never queued"
        elif pid is not None:
            why = str(run.Busy(pid))
        elif not args.clear and not 1 <= len(reason) <= 200:
            why = "the reason must have 1 to 200 characters"
        else:
            why = None
            if args.clear:
                q.pop("queued", None)
            else:
                q["queued"] = {"reason": reason, "after": args.after}
            state.save(cfg, st)
    if why:
        print(f"WB: HOLD REFUSE {args.id} - {why}")
        return 1
    print(f"WB: HOLD OK {args.id}")
    return 0


def _approve(args, cfg, personas) -> int:
    why, sha = machine.approve(cfg, args.id, args.kind, args.head, sys.stdin, sys.stdout)
    if why:
        print(f"WB: APPROVE REFUSE {args.id} - {why}")
        return 1
    print(f"WB: APPROVE OK {args.id} {args.kind} {sha}")
    return 0


def _status(args, cfg, personas) -> int:
    if args.id is None:
        if args.json:
            raise UsageError("status --json needs an item id")
        for st in status.items(cfg):
            print(status.line(st))
        return 0
    st = state.load(cfg, args.id)
    print(json.dumps(status.costs(st), indent=1) if args.json else "\n".join(status.detail(st)))
    return 0


def _pages(args, cfg, personas) -> int:
    pages.build(cfg, args.out, args.answer_url)
    return 0


def _pull_answers(args, cfg, personas) -> int:
    answered, refused = pull.pull(cfg, args.queue)
    print(f"PULL: answered={answered} refused={refused}")
    return 0


def _notify(args, cfg, personas) -> int:
    try:
        sent, held, skipped = notify.run(cfg)
    except notify.Busy:
        print("WB: NOTIFY BUSY")
        return 0
    except notify.Failed as e:
        print(f"WB: NOTIFY FAIL {e}")
        return 1
    print(f"WB: NOTIFY sent={sent} held={held} skipped={skipped}")
    return 0


def _arg(pattern: str, what: str, convert=str):
    """An argparse type: a full match of pattern, then convert; a ValueError of convert is a usage error too."""
    def parse(text: str):
        try:
            if re.fullmatch(pattern, text):
                return convert(text)
        except ValueError:
            pass
        raise argparse.ArgumentTypeError(f"{what}, not {text!r}")
    return parse


def _mutants(text: str) -> dict:
    killed, total = map(int, text.split("/"))
    if total < 1 or killed > total:
        raise ValueError(text)
    return {"killed": killed, "total": total}


def _review(text: str) -> dict:
    """The 5 counts of a full CODE-REVIEW line; the 4 severities add to the findings."""
    findings, *severities = map(int, re.findall(r"[0-9]+", text))
    if findings != sum(severities):
        raise ValueError(text)
    return dict(zip(("findings", "critical", "high", "medium", "low"), (findings, *severities)))


def _seconds(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):  # 400 digits give inf
        raise ValueError(text)
    return value


def _utc_time(text: str) -> str:
    dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")  # a date that cannot exist is a ValueError
    return text


def _record(args, cfg, personas) -> int:
    """Add 1 record of the main session to records: the lock and the run.lock refusal of hold."""
    if args.kind == "gate" and (args.second is None) != (args.second_seconds is None):
        raise UsageError("record gate: --second and --second-seconds come together or not at all")
    state.load(cfg, args.id)  # the usage error of a bad id or a missing state, with no lock
    with state.answer_lock(cfg, args.id):
        st = state.load(cfg, args.id)
        pid = run.holder(run.lock_path(cfg, args.id))
        why = str(run.Busy(pid)) if pid is not None else None
        if why is None:
            _apply(cfg, args, st)
    if why:
        print(f"WB: RECORD REFUSE {args.id} - {why}")
        return 1
    print(f"WB: RECORD OK {args.id} {args.kind}")
    return 0


def _apply(cfg: dict, args, st: dict) -> None:
    """Add the record of args and save the state, then its event: the side effects of 1 record, for `wb record`
    and `wb deliver`. The caller holds the answer lock."""
    if _add(args, st):
        state.save(cfg, st)
        if args.kind == "merge":
            events.append(cfg, "merge", st["id"], sha=args.sha, by=args.by)


def _add(args, st: dict) -> bool:
    """Add the record of args to the state; False when it changes nothing."""
    rec = st["records"]
    if args.kind == "pr":
        rec["pr"] = {"number": args.number, "time": _utc()}
    elif args.kind == "gate":
        rec.setdefault("gates", []).append({
            "head": st.get("pushed"), "time": _utc(), "host": {"passed": args.host_passed, "failed": args.host_failed},
            "review": args.review, "reviewer": args.reviewer, "mutants": args.mutants,
            "second": None if args.second is None else {**args.second, "seconds": args.second_seconds}})
    elif args.kind == "merge":
        rec["merge"] = {"sha": args.sha, "time": args.time or _utc(), "by": args.by}
    elif args.item in rec.setdefault("follow_ups", []):
        return False
    else:
        rec["follow_ups"].append(args.item)
    return True


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m wb", description="The stage machine of the workbench.")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("intake", help="check a spec and start the item at stage 2")
    p.add_argument("spec")
    p.add_argument("--id", required=True)
    p.add_argument("--branch")
    p.add_argument("--repo", help="a name of [repos] in the config; default: the origin of the runner clone")
    p.set_defaults(fn=_intake)
    p = sub.add_parser("run-next", help="start a detached run of steps until the item waits or is ready")
    p.add_argument("id")
    p.add_argument("--foreground", action="store_true", help="run here, not detached (the detached run uses it)")
    p.set_defaults(fn=_run_next)
    p = sub.add_parser("answer", help="store the owner's answer and set the item running")
    p.add_argument("id")
    p.add_argument("--file", required=True)
    p.set_defaults(fn=_answer)
    p = sub.add_parser("hold", help="queue a wait below the owner's work, with a reason; --clear ends it")
    p.add_argument("id")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--reason", help="1 to 200 characters, white space joined")
    g.add_argument("--clear", action="store_true", help="remove the queued mark")
    p.add_argument("--after", help="the id of the item that the wait waits for")
    p.set_defaults(fn=_hold)
    p = sub.add_parser("approve", help="the owner approves a merge, a live write or a High tier item")
    p.add_argument("id")
    p.add_argument("kind", choices=machine.APPROVALS)
    p.add_argument("--head", help="40 hex characters; default: the head of the item, which a given head must equal")
    p.set_defaults(fn=_approve)
    p = sub.add_parser("status", help="1 line for each item, or the detail of 1 item")
    p.add_argument("id", nargs="?")
    p.add_argument("--json", action="store_true", help="dollars, tokens and seconds by stage and by role")
    p.set_defaults(fn=_status)
    p = sub.add_parser("pages", help="write read-only HTML pages of the state and the results")
    p.add_argument("--out", required=True, help="the folder of the pages; empty, or from an earlier build; it is replaced after a full build")
    p.add_argument("--answer-url", help="the target of an answer form on each wait that has options")
    p.set_defaults(fn=_pages)
    p = sub.add_parser("pull-answers", help="answer case 2 waits from the files of the answer forms")
    p.add_argument("--queue", required=True, help="the folder of the answer files")
    p.set_defaults(fn=_pull_answers)
    p = sub.add_parser("record", help="the main session records a pull request, a gate, a merge or a follow-up")
    p.add_argument("id")
    p.set_defaults(fn=_record)
    kinds = p.add_subparsers(dest="kind", required=True, metavar="<pr|gate|merge|follow-up>")
    k = kinds.add_parser("pr", help="the pull request of the item; a new record replaces the old")
    k.add_argument("--number", required=True, type=_arg(r"[1-9][0-9]{0,8}", "1 to 9 digits with no leading 0", int))
    count = _arg(r"[0-9]+", "a count: ASCII digits only", int)
    review = _arg(machine.CODE_REVIEW_LINE.pattern, "a full CODE-REVIEW line whose 4 counts add to the findings",
                  _review)
    k = kinds.add_parser("gate", help="the result of the gate at the pushed head; each gate adds 1 entry")
    k.add_argument("--host-passed", required=True, type=count)
    k.add_argument("--host-failed", required=True, type=count)
    k.add_argument("--review", required=True, type=review, help='"CODE-REVIEW: ..." of the code review')
    k.add_argument("--reviewer", required=True, choices=("CLOSE", "REWORK"))
    k.add_argument("--mutants", required=True, type=_arg(r"[0-9]+/[0-9]+", "K/N with N 1 or more, K at most N",
                                                         _mutants), help="K/N: killed of total")
    k.add_argument("--second", type=review, help='"CODE-REVIEW: ..." of the second review')
    k.add_argument("--second-seconds", type=_arg(r"([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?",
                                                 "seconds: a finite number of 0 or more (digits, an optional fraction,"
                                                 " an optional exponent)", _seconds),
                   help="a finite number of 0 or more (digits, an optional fraction, an optional exponent)")
    k = kinds.add_parser("merge", help="the merge of the pull request; a new record replaces the old")
    k.add_argument("--sha", required=True, type=_arg(r"[0-9A-Fa-f]{40}", "40 hex characters, stored in lower case",
                                                     str.lower), help="40 hex characters, stored in lower case")
    k.add_argument("--time", type=_arg(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
                                       "a time YYYY-MM-DDTHH:MM:SSZ", _utc_time), help="default: now")
    k.add_argument("--by", required=True, choices=("main", "owner"), help="the main session, or the owner")
    k = kinds.add_parser("follow-up", help="an item that follows from this one; a known item changes nothing")
    k.add_argument("--item", required=True, type=_arg(state.ITEM_ID.pattern, "an item id: letters, digits, - and _"))
    p = sub.add_parser("notify", help="send the new event lines to notify.command; a cron job runs it each 2 minutes")
    p.set_defaults(fn=_notify)
    p = sub.add_parser("deliver", help="start a detached run of the chain after ready: the host test, the reviews,"
                                       " the gate, the merge and ADO")
    p.add_argument("id")
    p.add_argument("--foreground", action="store_true", help="run here, not detached (the detached run uses it)")
    p.set_defaults(fn=_deliver)
    return ap


def main(argv: list[str] | None = None, *, cfg=None, personas=None) -> int:
    """cfg and personas: dicts, or None for this repo's config (the local file first)."""
    args = parser().parse_args(argv)
    try:
        cfg = cfg if isinstance(cfg, dict) else config.load_workbench(cfg)
        intake.repos(cfg)
        personas = personas if isinstance(personas, dict) else config.load_personas(personas)
        return args.fn(args, cfg, personas)
    except UsageError as e:
        print(f"wb: {e}", file=sys.stderr)
        return 2
