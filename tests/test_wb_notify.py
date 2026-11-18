"""The event lines of wb (events.py) and `wb notify`, the pushes to the owner's phone."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from zoneinfo import ZoneInfo

import pytest

from conftest import ROOT
from wb import events, machine, notify, state
from wb.cli import main
from wb_fakes import HEAD, ONE_DOLLAR, SPEC, Replies, make_cfg, make_work_tree, reply

SHA = "0123456789abcdef0123456789abcdef01234567"
CLEAN = "CODE-REVIEW: 0 findings (0 critical, 0 high, 0 medium, 0 low)"


def lines(cfg) -> list[dict]:
    path = events.state.home(cfg) / events.FILE
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


# AC 1: the line

APPENDER = """
import sys
from wb import events
cfg = {"state": {"home": sys.argv[1]}}
for n in range(400):
    events.append(cfg, "fyi", sys.argv[2], n=n, a="a" * 200, b="b" * 200, c="c" * 200, d="d" * 200, e="e" * 100)
"""


def test_2_processes_append_whole_lines(tmp_path):
    cfg, _ = make_cfg(tmp_path)
    home = events.state.home(cfg)
    home.mkdir()
    procs = [subprocess.Popen([sys.executable, "-c", APPENDER, str(home), who], cwd=ROOT) for who in ("p1", "p2")]
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    raw = (home / events.FILE).read_bytes()
    assert raw.count(b"\n") == 800 and raw.endswith(b"\n")
    got = lines(cfg)
    assert all(len(json.dumps(x)) > 1000 for x in got)  # 1 KB each
    for who in ("p1", "p2"):
        assert [x["n"] for x in got if x["item"] == who] == list(range(400))
    assert {(x["v"], x["kind"]) for x in got} == {(1, "fyi")} and all(x["time"].endswith("Z") for x in got)
    if sys.platform != "win32":
        assert (home / events.FILE).stat().st_mode & 0o777 == 0o600


def test_each_text_is_cut_at_200_characters(tmp_path):
    cfg, _ = make_cfg(tmp_path)
    events.state.home(cfg).mkdir()
    events.append(cfg, "wait", "7", case=2, text="x" * 300, key=events.key(2, "x" * 300))
    (line,) = lines(cfg)
    assert line["text"] == "x" * 200 and line["case"] == 2
    assert line["key"] == hashlib.sha256(("2\n" + "x" * 300).encode()).hexdigest()[:16]  # of the whole text
    assert list(line)[:4] == ["v", "time", "kind", "item"]


def test_append_error_warns(tmp_path, capsys):
    cfg, _ = make_cfg(tmp_path)  # no state home yet: the open fails
    events.append(cfg, "intake", "7")
    out, err = capsys.readouterr()
    assert out == "" and err.count("\n") == 1 and err.startswith("wb: the intake event of item 7 was not written")


# AC 2: the sites


def wb(cfg, personas, *argv) -> int:
    return main(list(argv), cfg=cfg, personas=personas)


def flow_start(tmp_path, monkeypatch):
    """An intake through the command line, with the work tree: (cfg, personas, replies)."""
    cfg, personas = make_cfg(tmp_path)
    make_work_tree(cfg, "7")
    r = Replies(monkeypatch, cfg)
    assert wb(cfg, personas, "intake", str(SPEC), "--id", "7") == 0
    return cfg, personas, r


def answer(cfg, personas, tmp_path) -> None:
    (tmp_path / "answer.md").write_text("go on", encoding="utf-8")
    assert wb(cfg, personas, "answer", "7", "--file", str(tmp_path / "answer.md")) == 0


def test_each_site_writes_1_line(tmp_path, monkeypatch):
    cfg, personas, r = flow_start(tmp_path, monkeypatch)
    st = state.load(cfg, "7")
    st["usd"] = 32.0  # 80 percent of $40: the dollar alert after the first step
    state.save(cfg, st)
    r.add("spec-checker", "SPEC-CHECK 7: GO").add("builder", "not ok", "BUILD: BLOCKED - x")
    st = machine.run_next(cfg, personas, "7")
    answer(cfg, personas, tmp_path)
    r.add("builder", "BUILD: READY " + HEAD).add("reviewer", "REVIEW: CLOSE").add("second-reviewer", CLEAN)
    machine.run_next(cfg, personas, "7")
    assert wb(cfg, personas, "record", "7", "merge", "--sha", SHA, "--by", "owner") == 0
    got = lines(cfg)
    assert [x["kind"] for x in got] == ["intake", "budget_alert", "build", "step_failed", "build", "wait", "build",
                                        "ready", "merge"]
    assert {x["item"] for x in got} == {"7"}
    strip = [{k: v for k, v in x.items() if k not in ("v", "time", "kind", "item")} for x in got]
    q = st["question"]
    failed = {"role": "builder", "status": "INVALID", "reason": "the last line is not an allowed line: not ok"}
    assert strip == [{}, {"limit": "usd"}, {}, failed, {},
                     {"case": 2, "text": q["text"], "key": events.key(2, q["text"])}, {}, {"tier": "Medium"},
                     {"sha": SHA, "by": "owner"}]


def test_an_answer_after_an_unpriced_last_step_writes_ready(tmp_path, monkeypatch):
    cfg, personas, r = flow_start(tmp_path, monkeypatch)
    r.add("spec-checker", "SPEC-CHECK 7: GO").add("builder", "BUILD: READY " + HEAD).add("reviewer", "REVIEW: CLOSE")
    r.add("second-reviewer", reply(CLEAN, ONE_DOLLAR, model="fake-model"))
    machine.run_next(cfg, personas, "7")
    answer(cfg, personas, tmp_path)
    assert machine.run_next(cfg, personas, "7")["status"] == "ready"
    assert [x["kind"] for x in lines(cfg)] == ["intake", "build", "ready", "wait", "ready"]


def test_a_record_that_is_not_a_merge_writes_no_line(tmp_path, monkeypatch):
    cfg, personas, _ = flow_start(tmp_path, monkeypatch)
    assert wb(cfg, personas, "record", "7", "pr", "--number", "5") == 0
    assert [x["kind"] for x in lines(cfg)] == ["intake"]


# AC 3 to 6: wb notify. The clock is a parameter of notify.run; the zone is not UTC, so a UTC clock fails.

ZONE = ZoneInfo("America/New_York")
NOTIFY = {"command": [], "page_url": "", "quiet": "22:00-06:30", "digests": ["07:00", "19:00"], "fyi_minutes": 30,
          "remind_hours": 4}
PAGE = "https://pages.example.invalid/wb/"


def at(hour: int, minute: int = 0, day: int = 16) -> dt.datetime:
    """A Monday in November: EST, 5 hours behind UTC. In UTC, as the command line passes it: a run that leaves out
    the zone fails."""
    return dt.datetime(2026, 11, day, hour, minute, tzinfo=ZONE).astimezone(dt.timezone.utc)


def stamp(t: dt.datetime) -> str:
    """The "time" of an event line at t, as events.append writes it."""
    return t.isoformat(timespec="seconds").replace("+00:00", "Z")


@pytest.fixture
def nf(tmp_path, monkeypatch):
    """(cfg, the pushes of a fake send) with the first run done at 08:00. An event line gets the time 08:00 unless
    the test gives its time: out of the quiet hours, whatever the hour of the suite."""
    monkeypatch.setattr(events, "_utc", lambda: stamp(at(8)))
    cfg, _ = make_cfg(tmp_path)
    cfg["pages"]["time_zone"] = "America/New_York"
    cfg["notify"] = {**NOTIFY, "command": ["unused"], "page_url": PAGE}
    state.home(cfg).mkdir()
    pushes = []
    monkeypatch.setattr(notify, "send", lambda command, push: pushes.append(push))
    assert notify.run(cfg, at(8)) == (0, 0, 0)
    return cfg, pushes


def item(cfg, item_id="7", status="waiting", case=2, text="Which option?", **extra) -> None:
    st = state.new(item_id)
    st.update(status=status, tier=extra.pop("tier", "Medium"), **extra)
    if status == "waiting":
        st["question"] = {"case": case, "text": text, "run_folder": None}
    state.save(cfg, st)


def wait_line(cfg, item_id="7", case=2, text="Which option?") -> None:
    events.append(cfg, "wait", item_id, case=case, text=text, key=events.key(case, text))


def titles(pushes) -> list[str]:
    return [p["title"] for p in pushes]


def test_needs_you_once_per_item(nf):
    cfg, pushes = nf
    item(cfg)
    wait_line(cfg)
    wait_line(cfg)
    assert notify.run(cfg, at(9)) == (1, 0, 0)
    wait_line(cfg)
    assert notify.run(cfg, at(9, 2)) == (0, 0, 0)
    assert pushes == [{"title": "#7 waits: case 2", "body": "Which option?", "priority": "high", "tags": "warning",
                       "click": PAGE}]
    wait_line(cfg, text="Another question")  # another key of the item
    wait_line(cfg, "8")
    events.append(cfg, "stop", "7")
    events.append(cfg, "stop", "7")
    events.append(cfg, "ready", "7", tier="High")
    notify.run(cfg, at(9, 4))
    assert titles(pushes[1:]) == ["#7 waits: case 2", "#8 waits: case 2", "#7 stopped", "#7 ready for your merge"]
    assert [p["body"] for p in pushes[1:]] == ["Another question", "Which option?", "#7 stopped",
                                               "#7 ready for your merge"]
    assert {(p["priority"], p["tags"]) for p in pushes} == {("high", "warning")}


def test_a_ready_below_high_tier_is_fyi(nf):
    cfg, pushes = nf
    events.append(cfg, "ready", "7", tier="Medium")
    notify.run(cfg, at(9))
    assert [(p["title"], p["body"], p["priority"]) for p in pushes] == [("Workbench: 1 updates", "#7 ready", "low")]


def test_reminder_after_4_hours(nf):
    cfg, pushes = nf
    item(cfg)
    wait_line(cfg)
    notify.run(cfg, at(9))
    for t, n in ((at(12, 59), 1), (at(13), 2), (at(13, 2), 2), (at(17), 2)):
        notify.run(cfg, t)
        assert len(pushes) == n, t
    assert pushes[1] == {**pushes[0], "title": "#7 still waits"}


def test_no_reminder_once_answered_or_merged(nf):
    cfg, pushes = nf
    item(cfg)
    item(cfg, "8", status="ready", tier="High")
    item(cfg, "9", status="ready", tier="High")
    item(cfg, "10")
    wait_line(cfg)
    wait_line(cfg, "10")
    events.append(cfg, "ready", "8", tier="High")
    events.append(cfg, "ready", "9", tier="High")
    notify.run(cfg, at(9))
    st = state.load(cfg, "7")
    st.update(status="running", question=None)  # what wb answer does
    state.save(cfg, st)
    st = state.load(cfg, "8")
    st["records"]["merge"] = {"sha": SHA, "time": "2026-11-16T16:00:00Z", "by": "owner"}
    state.save(cfg, st)
    item(cfg, "10", text="A new question")  # another wait of the item: the first one was answered
    notify.run(cfg, at(13))
    assert titles(pushes) == ["#7 waits: case 2", "#10 waits: case 2", "#8 ready for your merge",
                              "#9 ready for your merge", "#9 still waits"]


def test_a_reminder_due_in_the_quiet_hours_is_dropped(nf):
    cfg, pushes = nf
    item(cfg)
    notify.run(cfg, at(19))  # the 19:00 digest
    wait_line(cfg)
    notify.run(cfg, at(20))
    notify.run(cfg, at(0, 0, day=17))
    notify.run(cfg, at(6, 40, day=17))
    assert titles(pushes) == ["Workbench digest 19:00", "#7 waits: case 2"]


def test_the_quiet_check_is_on_the_due_time_not_the_run_time(nf):
    cfg, pushes = nf
    item(cfg)
    notify.run(cfg, at(19))  # the 19:00 digest
    wait_line(cfg)
    notify.run(cfg, at(20))
    notify.run(cfg, at(6, 40, day=17))  # the first run after the quiet hours: the reminder was due at 00:00
    assert titles(pushes) == ["Workbench digest 19:00", "#7 waits: case 2"]
    item(cfg, "8")
    wait_line(cfg, "8")
    notify.run(cfg, at(13, day=17))
    notify.run(cfg, at(17, day=17))  # due at 17:00, out of the quiet hours
    assert titles(pushes[2:]) == ["#8 waits: case 2", "Workbench digest 07:00", "#8 still waits"]


def test_a_reminder_due_out_of_the_quiet_hours_waits_for_their_end(nf):
    cfg, pushes = nf
    item(cfg)
    wait_line(cfg)
    notify.run(cfg, at(17))  # the push: the reminder is due at 21:00
    notify.run(cfg, at(19))  # the 19:00 digest
    assert notify.run(cfg, at(23)) == (0, 0, 0)  # a late run in the quiet hours sends nothing
    notify.run(cfg, at(6, 30, day=17))
    assert titles(pushes) == ["#7 waits: case 2", "Workbench digest 19:00", "#7 still waits"]


def notify_state(cfg) -> dict:
    return json.loads((state.home(cfg) / notify.STATE).read_text(encoding="utf-8"))


def test_a_queued_fyi_waits_for_the_end_of_the_quiet_hours(nf):
    cfg, pushes = nf
    notify.run(cfg, at(19))  # the 19:00 digest
    events.append(cfg, "build", "7")
    assert notify.run(cfg, at(23)) == (0, 0, 0)
    assert notify_state(cfg)["fyi"] == ["#7 build"]  # still pending
    notify.run(cfg, at(6, 30, day=17))
    assert titles(pushes) == ["Workbench digest 19:00", "Workbench: 1 updates"]
    assert pushes[-1]["body"] == "#7 build"


def test_a_missed_digest_waits_for_the_end_of_the_quiet_hours(nf):
    cfg, pushes = nf
    assert notify.run(cfg, at(23)) == (0, 0, 0)  # the 19:00 slot passed with no run
    assert notify_state(cfg)["digest_last"] == at(7).astimezone(ZONE).isoformat()  # still pending
    notify.run(cfg, at(6, 30, day=17))
    notify.run(cfg, at(6, 32, day=17))
    assert titles(pushes) == ["Workbench digest 19:00"]


def test_every_send_path_is_quiet_at_23_00(nf):
    """Each path that sends is due at 23:00: a wait, a stop, a High tier ready, a reminder, an FYI, a digest. A new
    path that skips the quiet check of push fails here."""
    cfg, pushes = nf
    item(cfg)
    wait_line(cfg)
    notify.run(cfg, at(17))  # the push of #7: its reminder is due at 21:00; no run at 19:00
    events.append(cfg, "build", "11")
    for kind, item_id, extra in (("wait", "8", {"case": 1, "text": "Go?", "key": "k"}), ("stop", "9", {}),
                                 ("ready", "10", {"tier": "High"})):
        events.append(cfg, kind, item_id, time=stamp(at(21)), **extra)  # out of the quiet hours: the run time holds
    assert titles(pushes) == ["#7 waits: case 2"]
    assert notify.run(cfg, at(23)) == (0, 3, 0)
    assert titles(pushes) == ["#7 waits: case 2"]
    notify.run(cfg, at(6, 30, day=17))  # each deferred send goes out once the quiet hours end
    assert titles(pushes[1:]) == ["#7 still waits", "Workbench: 1 updates", "Workbench digest 19:00"]
    assert "Waiting for you: 4 (#10, #7, #8, #9)" in pushes[-1]["body"]


def test_fyi_at_most_1_each_30_minutes(nf):
    cfg, pushes = nf
    for t, kind in ((at(9), "intake"), (at(9, 10), "build"), (at(9, 29), "merge")):
        events.append(cfg, kind, "7")
        notify.run(cfg, t)
    assert len(pushes) == 1
    notify.run(cfg, at(9, 30))
    assert pushes == [{"title": "Workbench: 1 updates", "body": "#7 intake", "priority": "low", "tags": "workbench",
                       "click": PAGE},
                      {"title": "Workbench: 2 updates", "body": "#7 build\n#7 merge", "priority": "low",
                       "tags": "workbench", "click": PAGE}]


def test_fyi_lists_20_lines_then_the_rest_as_a_count(nf):
    cfg, pushes = nf
    for n in range(25):
        events.append(cfg, "build", f"i{n}")
    notify.run(cfg, at(9))
    (p,) = pushes
    assert p["title"] == "Workbench: 25 updates"
    assert p["body"].split("\n") == [f"#i{n} build" for n in range(20)] + ["and 5 more"]


def test_quiet_hours_hold_until_the_digest(nf):
    cfg, pushes = nf
    notify.run(cfg, at(19))  # the 19:00 digest
    item(cfg, "7")
    item(cfg, "8")
    item(cfg, "9")
    wait_line(cfg, "7")
    notify.run(cfg, at(21, 59))  # 02:59 UTC: in the quiet hours by a UTC clock
    wait_line(cfg, "8")
    assert notify.run(cfg, at(22)) == (0, 1, 0)
    wait_line(cfg, "9")
    assert notify.run(cfg, at(23)) == (0, 1, 0)
    assert notify.run(cfg, at(2, 0, day=17)) == (0, 0, 0)  # the reminder of #7, due in the quiet hours, is dropped
    assert notify.run(cfg, at(6, 59, day=17)) == (0, 0, 0)  # 11:59 UTC: after a 07:00 slot by a UTC clock
    assert notify.run(cfg, at(7, 0, day=17)) == (1, 0, 0)
    for minute in (2, 4):
        notify.run(cfg, at(7, minute, day=17))
        wait_line(cfg, "9")
    assert titles(pushes) == ["Workbench digest 19:00", "#7 waits: case 2", "Workbench digest 07:00"]
    assert "Waiting for you: 3 (#7, #8, #9)" in pushes[-1]["body"]


def test_the_hold_is_on_the_event_time_not_the_run_time(nf):
    cfg, pushes = nf
    notify.run(cfg, at(19))  # the 19:00 digest
    events.append(cfg, "wait", "7", case=2, text="Which option?", key="k", time=stamp(at(23)))
    events.append(cfg, "stop", "9", time="0001-01-01T00:00:00+05:00")  # out of range: the run time decides
    assert notify.run(cfg, at(6, 40, day=17)) == (1, 1, 0)  # the first run after the quiet hours
    notify.run(cfg, at(7, 0, day=17))
    assert titles(pushes) == ["Workbench digest 19:00", "#9 stopped", "Workbench digest 07:00"]
    assert "Waiting for you: 1 (#7)" in pushes[-1]["body"]


def test_the_quiet_hours_end_at_06_30(nf):
    cfg, pushes = nf
    wait_line(cfg, "7")
    assert notify.run(cfg, at(6, 29, day=17)) == (0, 1, 0)  # the 19:00 digest of the 16th waits too
    wait_line(cfg, "8")
    assert notify.run(cfg, at(6, 30, day=17)) == (2, 0, 0)
    assert titles(pushes) == ["#8 waits: case 2", "Workbench digest 19:00"]


def test_a_quiet_window_inside_1_day(nf):
    cfg, pushes = nf
    cfg["notify"]["quiet"] = "09:00-10:00"
    for minute, item_id in ((8 * 60 + 59, "7"), (9 * 60, "8"), (9 * 60 + 59, "9"), (10 * 60, "10")):
        wait_line(cfg, item_id)
        notify.run(cfg, at(minute // 60, minute % 60))
    assert titles(pushes) == ["#7 waits: case 2", "#10 waits: case 2"]


def test_a_held_item_that_no_longer_waits_is_still_named(nf):
    cfg, pushes = nf
    notify.run(cfg, at(19))
    events.append(cfg, "ready", "8", tier="High")
    assert notify.run(cfg, at(23)) == (0, 1, 0)
    notify.run(cfg, at(7, 0, day=17))
    assert "Waiting for you: 1 (#8)" in pushes[-1]["body"]
    notify.run(cfg, at(19, 0, day=17))  # the digest marked it sent
    assert "Waiting for you: 0\n" in pushes[-1]["body"]


def counters(cfg, item_id: str, steps: list[tuple]) -> None:
    """Steps with a counters.json each: (end, usd)."""
    st = state.load(cfg, item_id)
    for n, (end, usd) in enumerate(steps, 1):
        folder = state.item_dir(cfg, item_id) / "runs" / f"{n:03d}"
        folder.mkdir(parents=True)
        (folder / "counters.json").write_text(json.dumps({"end": end, "usd": usd}), encoding="utf-8")
        st["steps"].append({"n": n, "run_folder": str(folder)})
    state.save(cfg, st)


def test_digest_counts(nf):
    cfg, pushes = nf
    item(cfg, "a", status="running")
    item(cfg, "b", status="running")
    item(cfg, "c")
    item(cfg, "d", case=1)
    item(cfg, "e", status="ready")
    # today in the zone: 05:00Z of the 16th is 00:00 EST; 04:59Z is the 15th
    counters(cfg, "a", [("2026-11-16T05:00:00Z", 1.25), ("2026-11-16T04:59:59Z", 100.0)])
    counters(cfg, "e", [("2026-11-17T04:59:59Z", 2.5), (None, 9.0), ("2026-11-16T12:00:00Z", "unpriced")])
    for n in range(2):
        events.append(cfg, "merge", "e", sha=SHA, by="owner")
    notify.run(cfg, at(18, 59))
    notify.run(cfg, at(19))
    assert pushes[-1] == {"title": "Workbench digest 19:00", "priority": "default", "tags": "workbench",
                          "click": PAGE,
                          "body": "Building: 2\nWaiting for you: 2 (#c, #d)\nMerged since the last digest: 2\n"
                                  "Merged but not live: not known\nStep spend today: $3.75"}
    notify.run(cfg, at(7, 0, day=17))
    assert "Merged since the last digest: 0" in pushes[-1]["body"]


def test_digest_with_no_events(nf):
    cfg, pushes = nf
    assert notify.run(cfg, at(18, 59)) == (0, 0, 0)
    assert notify.run(cfg, at(19)) == (1, 0, 0)
    assert notify.run(cfg, at(19, 2)) == (0, 0, 0)
    assert pushes == [{"title": "Workbench digest 19:00", "priority": "default", "tags": "workbench", "click": PAGE,
                       "body": "Building: 0\nWaiting for you: 0\nMerged since the last digest: 0\n"
                               "Merged but not live: not known\nStep spend today: $0.00"}]


def test_a_missed_slot_goes_once_late(nf):
    cfg, pushes = nf
    notify.run(cfg, at(8, 0, day=17))  # the 19:00 and the 07:00 slots passed with no run
    notify.run(cfg, at(8, 2, day=17))
    assert titles(pushes) == ["Workbench digest 07:00"]


def test_the_first_run_starts_at_the_end(tmp_path, monkeypatch):
    monkeypatch.setattr(events, "_utc", lambda: stamp(at(19, 31)))
    cfg, _ = make_cfg(tmp_path)
    cfg["pages"]["time_zone"] = "America/New_York"
    cfg["notify"] = {**NOTIFY, "command": ["unused"]}
    state.home(cfg).mkdir()
    pushes = []
    monkeypatch.setattr(notify, "send", lambda command, push: pushes.append(push))
    wait_line(cfg)
    assert notify.run(cfg, at(19, 30)) == (0, 0, 0)  # the 19:00 slot passed before the first run
    events.append(cfg, "stop", "7")
    notify.run(cfg, at(19, 32))
    assert pushes == [{"title": "#7 stopped", "body": "#7 stopped", "priority": "high", "tags": "warning",
                       "click": None}]


CHILD = """
import json, sys
push = json.loads(sys.stdin.read())
if "#8" in push["title"] and len(sys.argv) > 1:
    sys.exit(3)
with open(sys.argv[0] + ".out", "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "push": push}) + "\\n")
"""


@pytest.fixture
def real(tmp_path, monkeypatch):
    """(cfg, the child script, its records): notify.command runs a real child. The clock of each run is 09:00, out
    of the quiet hours and between the digest slots, whatever the hour of the suite, and so is each event line."""
    monkeypatch.setattr(notify, "clock", lambda: at(9))
    monkeypatch.setattr(events, "_utc", lambda: stamp(at(9)))
    cfg, _ = make_cfg(tmp_path)
    cfg["pages"]["time_zone"] = "America/New_York"
    child = tmp_path / "child.py"
    child.write_text(CHILD, encoding="utf-8")
    cfg["notify"] = {**NOTIFY, "command": [sys.executable, str(child)]}
    state.home(cfg).mkdir()
    assert notify.run(cfg) == (0, 0, 0)

    def records():
        out = tmp_path / "child.py.out"
        return [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()] if out.exists() else []
    return cfg, child, records


def wb_notify(cfg, capsys) -> tuple[int, list[str]]:
    code = main(["notify"], cfg=cfg, personas={"roles": {}})
    return code, capsys.readouterr().out.splitlines()


def test_send_failure_keeps_the_cursor(real, capsys):
    cfg, child, records = real
    cfg["notify"]["command"] = [sys.executable, str(child), "fail on #8"]
    for item_id in ("7", "8", "9"):
        wait_line(cfg, item_id)
    assert wb_notify(cfg, capsys) == (1, ["WB: NOTIFY FAIL the command exited 3"])
    assert [r["push"]["title"] for r in records()] == ["#7 waits: case 2"]
    cfg["notify"]["command"] = [sys.executable, str(child)]
    assert wb_notify(cfg, capsys) == (0, ["WB: NOTIFY sent=2 held=0 skipped=0"])
    assert [r["push"]["title"] for r in records()] == ["#7 waits: case 2", "#8 waits: case 2", "#9 waits: case 2"]


def test_a_command_that_does_not_start_fails(real, capsys, tmp_path):
    cfg, _, _ = real
    cfg["notify"]["command"] = [str(tmp_path / "no-such-command")]
    wait_line(cfg)
    assert wb_notify(cfg, capsys) == (1, ["WB: NOTIFY FAIL the command did not start (FileNotFoundError)"])


def test_half_line_waits(real, capsys):
    cfg, _, records = real
    log = state.home(cfg) / events.FILE
    wait_line(cfg)
    whole = log.read_bytes()
    log.write_bytes(whole[:-10])
    assert wb_notify(cfg, capsys) == (0, ["WB: NOTIFY sent=0 held=0 skipped=0"])
    log.write_bytes(whole)
    assert wb_notify(cfg, capsys) == (0, ["WB: NOTIFY sent=1 held=0 skipped=0"])
    assert len(records()) == 1


def test_a_bad_line_is_skipped_and_counted(real, capsys):
    cfg, _, records = real
    with (state.home(cfg) / events.FILE).open("ab") as f:
        f.write(b'not json\n["a list"]\n{"kind": "wait"}\n')
    wait_line(cfg)
    assert wb_notify(cfg, capsys) == (0, ["WB: NOTIFY sent=1 held=0 skipped=3"])
    assert wb_notify(cfg, capsys) == (0, ["WB: NOTIFY sent=0 held=0 skipped=0"])


def test_busy(real, capsys):
    cfg, _, records = real
    wait_line(cfg)
    fd = os.open(state.home(cfg) / "notify.flock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        state.lock_fd(fd)
        assert wb_notify(cfg, capsys) == (0, ["WB: NOTIFY BUSY"])
        assert records() == []
    finally:
        state.unlock_fd(fd)
        os.close(fd)
    assert wb_notify(cfg, capsys) == (0, ["WB: NOTIFY sent=1 held=0 skipped=0"])


def test_stdin_not_argv(real):
    cfg, child, records = real
    events.append(cfg, "wait", "7", case=2, text="a 'quoted' $(text)", key="k")
    assert notify.run(cfg) == (1, 0, 0)
    assert records() == [{"argv": [], "push": {"title": "#7 waits: case 2", "body": "a 'quoted' $(text)",
                                               "priority": "high", "tags": "warning", "click": None}}]


def test_the_command_line_holds_a_wait_in_the_quiet_hours(real, capsys, monkeypatch):
    cfg, _, records = real
    monkeypatch.setattr(notify, "clock", lambda: at(23))
    wait_line(cfg)
    assert wb_notify(cfg, capsys) == (0, ["WB: NOTIFY sent=0 held=1 skipped=0"])
    assert records() == []  # the 19:00 slot passed since the first run at 09:00: it waits for the end of the quiet hours
    monkeypatch.setattr(notify, "clock", lambda: at(6, 30, day=17))
    assert wb_notify(cfg, capsys) == (0, ["WB: NOTIFY sent=1 held=0 skipped=0"])
    (digest,) = records()  # the wait has no push of its own
    assert digest["push"]["title"] == "Workbench digest 19:00" and "Waiting for you: 1 (#7)" in digest["push"]["body"]


# AC 7: the config

PRIVATE = "https://" + "private-pages" + ".example.invalid"  # pieces: the value must never show


@pytest.mark.parametrize("key,value", [
    ("command", []), ("command", "a string"), ("command", [""]), ("command", None),
    ("page_url", PRIVATE + "/wb"), ("page_url", PRIVATE.replace("https", "http") + "/wb/"), ("page_url", None),
    ("page_url", PRIVATE + "/wb/?q=1"),
    ("quiet", "22:00"), ("quiet", "24:00-06:30"), ("digests", []), ("digests", ["7:00"]), ("digests", "07:00"),
    ("fyi_minutes", 0), ("fyi_minutes", "30"), ("fyi_minutes", True), ("remind_hours", -1),
    ("remind_hours", float("inf"))])
def test_bad_config_exits_2(tmp_path, capsys, key, value):
    cfg, _ = make_cfg(tmp_path)
    cfg["notify"] = {**NOTIFY, "command": ["unused"], "page_url": PRIVATE + "/wb/", key: value}
    assert main(["notify"], cfg=cfg, personas={"roles": {}}) == 2
    out, err = capsys.readouterr()
    assert out == "" and err.startswith(f"wb: notify.{key} must be") and "private-pages" not in err
    assert not state.home(cfg).exists()  # no lock and no state before the check


def test_no_notify_table_exits_2(tmp_path, capsys):
    cfg, _ = make_cfg(tmp_path)
    cfg.pop("notify", None)
    assert main(["notify"], cfg=cfg, personas={"roles": {}}) == 2
    assert capsys.readouterr().err == "wb: notify.command must be a list of 1 or more strings\n"


def test_the_example_config_is_a_usage_error_only_for_notify(tmp_path, capsys):
    cfg, _ = make_cfg(tmp_path)
    assert cfg["notify"]["command"] == []
    assert main(["status"], cfg=cfg, personas={"roles": {}}) == 0
    assert main(["notify"], cfg=cfg, personas={"roles": {}}) == 2
