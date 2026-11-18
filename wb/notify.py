"""`wb notify`: the event lines of events.jsonl (wb/events.py) as pushes to the owner's phone (see wb/README.md).

A cron job runs it every 2 minutes. It holds notify.flock of the state home (a held lock: BUSY), reads the whole
lines after the offset in notify-state.json, and sends each push as 1 JSON object {"title", "body", "priority",
"tags", "click"} on the stdin of notify.command (no shell, 60 s), then saves the state: a failed send keeps the
cursor. Needs you (a wait, a stop, a High tier ready): 1 high push for each item and key (else kind), and 1
reminder after notify.remind_hours while state.json still shows it; a line whose event time or run time is in
the quiet hours is held for the next digest. FYI (each other kind): 1 low push of the lines at most each
notify.fyi_minutes. A digest at each slot of notify.digests, in pages.time_zone. Nothing goes out in the quiet
hours: each send passes the 1 check of push, and a reminder, an FYI or a digest waits for their end. The first run
starts at the end of the file and marks the slots that passed.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import subprocess

from runner import UsageError
from wb import events, pages, state

STATE = "notify-state.json"
HM = r"([01][0-9]|2[0-3]):[0-5][0-9]"
SECONDS = 60
MAX_LINES = 20


class Busy(Exception):
    pass


class Failed(Exception):
    pass


def settings(cfg: dict) -> dict:
    """The [notify] table, checked, and the zone; a bad value is a usage error that never quotes it."""
    conf = cfg.get("notify")
    conf = conf if isinstance(conf, dict) else {}
    command = conf.get("command")
    if not (isinstance(command, list) and command and all(isinstance(a, str) and a for a in command)):
        raise UsageError("notify.command must be a list of 1 or more strings")
    url = conf.get("page_url")
    if url != "" and not (isinstance(url, str) and pages._https_folder(url)):
        raise UsageError("notify.page_url must be empty or an https URL that ends with /")  # private: never the value
    if not (isinstance(conf.get("quiet"), str) and re.fullmatch(f"{HM}-{HM}", conf["quiet"])):
        raise UsageError('notify.quiet must be "HH:MM-HH:MM"')
    digests = conf.get("digests")
    if not (isinstance(digests, list) and digests and all(isinstance(d, str) and re.fullmatch(HM, d) for d in digests)):
        raise UsageError('notify.digests must be a list of 1 or more "HH:MM"')
    for key in ("fyi_minutes", "remind_hours"):
        v = pages._number(conf.get(key))
        if v is None or not 0 < v < math.inf:
            raise UsageError(f"notify.{key} must be a number above 0")
    return {**conf, "zone": pages.zone(cfg)}


def _hm(text: str) -> dt.time:
    return dt.time(int(text[:2]), int(text[3:]))


def quiet(conf: dict, local: dt.datetime) -> bool:
    start, end = (_hm(t) for t in conf["quiet"].split("-"))
    t = local.time()
    return start <= t < end if start <= end else t >= start or t < end


def event_time(ev: dict, zone: dt.tzinfo) -> dt.datetime | None:
    """The time of the event line in zone; None when it has none or it is out of range."""
    t = pages._time(ev.get("time"))
    try:
        return t.astimezone(zone) if t else None
    except (OverflowError, ValueError):
        return None


def last_slot(conf: dict, local: dt.datetime) -> dt.datetime:
    """The latest digest time at or before local, today or yesterday."""
    days = (local.date(), local.date() - dt.timedelta(days=1))
    return max(t for d in days for s in conf["digests"] if (t := dt.datetime.combine(d, _hm(s), local.tzinfo)) <= local)


def send(command: list[str], push: dict) -> str | None:
    """Run command, no shell, with the push as JSON on stdin; None, or why it failed: never its text or output."""
    try:
        r = subprocess.run(command, input=json.dumps(push).encode("utf-8"), stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=SECONDS)
    except subprocess.TimeoutExpired:
        return f"the command timed out after {SECONDS} s"
    except OSError as e:
        return f"the command did not start ({type(e).__name__})"
    return None if r.returncode == 0 else f"the command exited {r.returncode}"


def still_open(cfg: dict, entry: dict) -> bool:
    """state.json still shows the wait of the entry, or its ready with no merge."""
    try:
        st = state.load(cfg, entry["item"])
    except UsageError:
        return False
    if entry["kind"] == "wait":
        q = st.get("question") or {}
        return st["status"] == "waiting" and events.key(q.get("case"), q.get("text")) == entry["key"]
    return entry["kind"] == "ready" and st["status"] == "ready" and not st["records"].get("merge")


def digest(cfg: dict, conf: dict, ns: dict, local: dt.datetime) -> str:
    home = state.home(cfg)
    items = pages.read_items(home)[0] if home.is_dir() else []
    waiting = sorted({it["id"] for it in items if it["status"] == "waiting"} | set(ns["held"]))
    spend = sum(v for it in items for c in it["_counters"]
                if (t := pages._time(c.get("end"))) and t.astimezone(conf["zone"]).date() == local.date()
                and (v := pages._number(c.get("usd"))) is not None)
    ids = f" ({', '.join('#' + i for i in waiting)})" if waiting else ""
    return "\n".join([f"Building: {sum(it['status'] == 'running' for it in items)}",
                      f"Waiting for you: {len(waiting)}{ids}",
                      f"Merged since the last digest: {ns['merged']}",
                      "Merged but not live: not known",
                      f"Step spend today: ${spend:.2f}"])


def clock() -> dt.datetime:
    """The time of a run with no clock given, as the command line makes it; the tests replace it."""
    return dt.datetime.now(dt.timezone.utc)


def run(cfg: dict, now: dt.datetime | None = None) -> tuple[int, int, int]:
    """(sent, held, skipped) of 1 run; Busy when another run holds notify.flock, Failed when a push or the state
    home fails."""
    conf = settings(cfg)
    home = state.home(cfg)
    try:
        home.mkdir(parents=True, exist_ok=True)
        fd = os.open(home / "notify.flock", os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        raise Failed(f"cannot open the lock in the state home ({type(e).__name__})") from e
    try:
        try:
            state.lock_fd(fd, wait=False)
        except (BlockingIOError, PermissionError):
            raise Busy() from None
        except OSError as e:
            raise Failed(f"cannot lock notify.flock ({type(e).__name__})") from e
        try:
            return _run(cfg, conf, now or clock())
        except OSError as e:  # the text of an OSError holds a path at most
            raise Failed(f"the state home: {e}") from e
        finally:
            state.unlock_fd(fd)
    finally:
        os.close(fd)


def _run(cfg: dict, conf: dict, now: dt.datetime) -> tuple[int, int, int]:
    home = state.home(cfg)
    path, log = home / STATE, home / events.FILE
    local = now.astimezone(conf["zone"])
    if not path.exists():  # the first run: the cursor at the end, the slots that passed are done
        state.write_json(path, {"offset": log.stat().st_size if log.exists() else 0, "sent": {}, "held": [],
                                "fyi": [], "fyi_last": None, "merged": 0,
                                "digest_last": last_slot(conf, local).isoformat()})
        return 0, 0, 0
    ns = state.read_json(path, "notify state")
    counts = {"sent": 0, "held": 0, "skipped": 0}
    click = conf["page_url"] or None

    def push(title: str, body: str, priority: str, tags: str, mark=lambda: None) -> bool:
        """The 1 path of each send. In the quiet hours it sends nothing and marks nothing: the send stays pending
        for the first run after their end. Else mark() records it as sent, then the send, then the save."""
        if quiet(conf, local):
            return False
        mark()
        why = send(conf["command"], {"title": title, "body": body, "priority": priority, "tags": tags, "click": click})
        if why:
            raise Failed(why)
        counts["sent"] += 1
        state.write_json(path, ns)  # after the send: a failed send keeps the cursor
        return True

    data = b""
    # shortcut: a file shorter than the offset (a rotation) gives no lines; upgrade with the rotation of events.jsonl
    if log.exists():
        with open(log, "rb") as f:
            f.seek(ns["offset"])
            data = f.read()
    for raw in data[:data.rfind(b"\n") + 1].splitlines(keepends=True):  # a tail with no line feed waits
        ns["offset"] += len(raw)
        try:
            ev = json.loads(raw)
        except ValueError:
            ev = None
        if not (isinstance(ev, dict) and isinstance(ev.get("kind"), str) and isinstance(ev.get("item"), str)):
            counts["skipped"] += 1
            continue
        kind, item = ev["kind"], ev["item"]
        if kind == "merge":
            ns["merged"] += 1
        if not (kind in ("wait", "stop") or kind == "ready" and ev.get("tier") == "High"):
            ns["fyi"].append(f"#{item} {kind}")
            continue
        # shortcut: sent keeps each key for good, so it grows by 1 entry per wait; upgrade with a rotation
        k = f"{item}|{ev.get('key') or kind}"
        if k in ns["sent"]:
            continue
        title = {"wait": f"#{item} waits: case {ev.get('case')}", "stop": f"#{item} stopped",
                 "ready": f"#{item} ready for your merge"}[kind]
        body = ev["text"] if isinstance(ev.get("text"), str) and ev["text"] else title
        t = event_time(ev, conf["zone"])
        entry = {"item": item, "kind": kind, "key": ev.get("key"), "time": now.isoformat(), "body": body,
                 "remind": kind != "stop"}
        # an event in the quiet hours, also read late, or a run in them: held for the next digest
        if t is not None and quiet(conf, t) or not push(title, body, "high", "warning",
                                                         lambda: ns["sent"].update({k: entry})):
            ns["sent"][k] = {**entry, "remind": False}
            ns["held"].append(item)
            counts["held"] += 1
    for e in ns["sent"].values():
        due = dt.datetime.fromisoformat(e["time"]) + dt.timedelta(hours=conf["remind_hours"])
        if e["remind"] and now >= due:  # 1 reminder at most; in the quiet hours push defers it to their end
            if quiet(conf, due.astimezone(conf["zone"])) or not still_open(cfg, e):
                e["remind"] = False  # one due in the quiet hours is dropped, also when the run is late
            else:
                push(f"#{e['item']} still waits", e["body"], "high", "warning", lambda e=e: e.update(remind=False))
    if ns["fyi"] and (ns["fyi_last"] is None or now - dt.datetime.fromisoformat(ns["fyi_last"])
                      >= dt.timedelta(minutes=conf["fyi_minutes"])):
        fyi = ns["fyi"]
        more = [f"and {len(fyi) - MAX_LINES} more"] if len(fyi) > MAX_LINES else []
        push(f"Workbench: {len(fyi)} updates", "\n".join(fyi[:MAX_LINES] + more), "low", "workbench",
             lambda: ns.update(fyi=[], fyi_last=now.isoformat()))
    slot = last_slot(conf, local)
    if dt.datetime.fromisoformat(ns["digest_last"]) < slot:  # a missed slot goes once, late, after the quiet hours
        push(f"Workbench digest {slot:%H:%M}", digest(cfg, conf, ns, local), "default", "workbench",
             lambda: ns.update(digest_last=slot.isoformat(), held=[], merged=0))
    state.write_json(path, ns)
    return counts["sent"], counts["held"], counts["skipped"]
