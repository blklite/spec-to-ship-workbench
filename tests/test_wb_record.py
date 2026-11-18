"""`wb record <id> <pr|gate|merge|follow-up>` and the records of the state."""

from __future__ import annotations

import json
import os
import re

import pytest

from wb import run, state
from wb.cli import main
from wb_fakes import make_cfg

SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def cfg(tmp_path):
    cfg, _ = make_cfg(tmp_path)
    st = state.new("r1")
    st.update(stage="delivery", status="ready", limits={"usd": 100.0, "hours": 4.0, "rounds": 2})
    state.save(cfg, st)
    return cfg


def record(cfg, *args, item_id="r1") -> int:
    """The exit code: argparse exits 2 itself."""
    try:
        return main(["record", item_id, *args], cfg=cfg, personas={"roles": {}})
    except SystemExit as e:
        return e.code


def state_bytes(cfg, item_id="r1") -> bytes:
    return (state.item_dir(cfg, item_id) / "state.json").read_bytes()


def records(cfg) -> dict:
    return state.load(cfg, "r1")["records"]


def is_utc(text) -> bool:
    return isinstance(text, str) and len(text) == 20 and text.endswith("Z") and text[10] == "T"


def usage(cfg, capsys, *args) -> None:
    before = state_bytes(cfg)
    assert record(cfg, *args) == 2, args
    out, err = capsys.readouterr()
    assert "RECORD" not in out
    # the error of record or of its kind (argparse names an option of another kind at the top), not of an
    # unknown command
    assert re.search(r"^(python -m wb record[ a-z-]*: error: |wb: record |python -m wb: error: unrecognized arg)",
                     err, re.M), err
    assert state_bytes(cfg) == before


# AC 1: the records and the command frame


def test_a_new_state_has_empty_records_and_the_version_stays_1():
    assert state.new("r1")["records"] == {}
    assert state.VERSION == 1


def test_an_old_state_with_no_records_reads_as_empty(cfg):
    path = state.item_dir(cfg, "r1") / "state.json"
    old = json.loads(path.read_text(encoding="utf-8"))
    del old["records"]
    path.write_text(json.dumps(old), encoding="utf-8")
    assert state.load(cfg, "r1")["records"] == {}


def test_record_ok_saves_through_state_save(cfg, capsys, monkeypatch):
    saved = []
    real = state.save
    monkeypatch.setattr(state, "save", lambda c, st: (saved.append(st["id"]), real(c, st)))
    assert record(cfg, "pr", "--number", "42") == 0
    assert capsys.readouterr().out.splitlines()[-1] == "WB: RECORD OK r1 pr"
    assert saved == ["r1"]


def test_refuses_a_live_run(cfg, capsys):
    lock = run.lock_path(cfg, "r1")
    lock.write_text(json.dumps({"pid": os.getpid(), "token": run.start_token(os.getpid())}), encoding="utf-8")
    before = state_bytes(cfg)
    assert record(cfg, "pr", "--number", "42") == 1
    assert capsys.readouterr().out.splitlines()[-1] == f"WB: RECORD REFUSE r1 - a run is going (pid {os.getpid()})"
    assert state_bytes(cfg) == before
    lock.write_text(json.dumps({"pid": 0, "token": None}), encoding="utf-8")  # a dead run does not refuse
    assert record(cfg, "pr", "--number", "42") == 0


def test_record_holds_the_answer_lock(cfg, capsys, monkeypatch):
    held = []
    real = state.answer_lock

    def lock(c, item_id):
        held.append(item_id)
        return real(c, item_id)

    monkeypatch.setattr(state, "answer_lock", lock)
    assert record(cfg, "pr", "--number", "42") == 0
    assert held == ["r1"]


@pytest.mark.parametrize("args", [[], ["bot"], ["pr"], ["pr", "--number"], ["pr", "--number", "4", "--sha", SHA],
                                  ["pr", "--number", "4", "--item", "9"]])
def test_a_missing_kind_or_option_or_an_option_of_another_kind_is_usage(cfg, capsys, args):
    usage(cfg, capsys, *args)


def test_an_unknown_item_is_usage(cfg, capsys):
    assert record(cfg, "pr", "--number", "4", item_id="nope") == 2
    assert "wb: no state of item nope" in capsys.readouterr().err
    assert record(cfg, "pr", "--number", "4", item_id="../r1") == 2
    assert "wb: bad item id '../r1'" in capsys.readouterr().err
    assert not state.item_dir(cfg, "nope").exists()


def test_status_adds_the_records_line_only_with_records(cfg, capsys):
    assert main(["status", "r1"], cfg=cfg, personas={"roles": {}}) == 0
    before = capsys.readouterr().out.splitlines()
    assert not any(x.startswith("  records:") for x in before)
    assert record(cfg, "pr", "--number", "42") == 0
    capsys.readouterr()
    assert main(["status", "r1"], cfg=cfg, personas={"roles": {}}) == 0
    after = capsys.readouterr().out.splitlines()
    assert after == before + ["  records: pr=42"]
    assert main(["status"], cfg=cfg, personas={"roles": {}}) == 0
    assert capsys.readouterr().out.splitlines() == [before[0]]  # the line of each item stays


def test_the_records_line_names_each_record():
    from wb import status

    rec = {"answers": [{}, {}], "pr": {"number": 7}, "gates": [{}], "merge": {"sha": SHA},
           "follow_ups": ["600", "601"], "diff": {"files": 3, "added": 10, "deleted": 2}}
    assert status.records(rec) == f"answers=2 pr=7 gates=1 merge={SHA} follow_ups=600,601 diff=3 files +10 -2"
    assert status.records({"diff": {"error": "x"}}) == "diff=error"


# AC 3: the pull request record


def test_a_pr_record_and_a_new_one_replaces_it(cfg, capsys):
    assert record(cfg, "pr", "--number", "42") == 0
    pr = records(cfg)["pr"]
    assert pr["number"] == 42 and type(pr["number"]) is int and is_utc(pr["time"]) and set(pr) == {"number", "time"}
    assert record(cfg, "pr", "--number", "999999999") == 0
    assert records(cfg)["pr"]["number"] == 999999999


@pytest.mark.parametrize("number", ["0", "-1", "01", "x", "1000000000", "+1", "1_0", "١", " 1"])
def test_a_bad_pr_number_is_usage(cfg, capsys, number):
    usage(cfg, capsys, "pr", "--number", number)


# AC 4: the gate record

REVIEW = "CODE-REVIEW: 3 findings (0 critical, 1 high, 1 medium, 1 low)"
SECOND = "CODE-REVIEW: 1 findings (1 critical, 0 high, 0 medium, 0 low)"
GATE = ["gate", "--host-passed", "1676", "--host-failed", "0", "--review", REVIEW, "--reviewer", "CLOSE",
        "--mutants", "7/8"]


def with_option(args, option, value):
    out = list(args)
    out[out.index(option) + 1] = value
    return out


def test_a_good_gate_and_2_gates_keep_2_entries(cfg, capsys):
    st = state.load(cfg, "r1")
    st["pushed"] = SHA
    state.save(cfg, st)
    assert record(cfg, *GATE) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "WB: RECORD OK r1 gate"
    assert record(cfg, *with_option(GATE, "--reviewer", "REWORK"), "--second", SECOND, "--second-seconds", "61.5") == 0
    first, second = records(cfg)["gates"]
    assert is_utc(first.pop("time")) and is_utc(second.pop("time"))
    assert first == {"head": SHA, "host": {"passed": 1676, "failed": 0},
                     "review": {"findings": 3, "critical": 0, "high": 1, "medium": 1, "low": 1},
                     "reviewer": "CLOSE", "mutants": {"killed": 7, "total": 8}, "second": None}
    assert second["reviewer"] == "REWORK"
    assert second["second"] == {"findings": 1, "critical": 1, "high": 0, "medium": 0, "low": 0, "seconds": 61.5}


def test_0_of_1_mutants_is_a_good_gate(cfg, capsys):
    assert record(cfg, *with_option(GATE, "--mutants", "0/1")) == 0
    assert records(cfg)["gates"][-1]["mutants"] == {"killed": 0, "total": 1}


def test_a_gate_before_any_push_has_no_head(cfg, capsys):
    assert record(cfg, *with_option(GATE, "--mutants", "8/8"), "--second", SECOND, "--second-seconds", "0") == 0
    (gate,) = records(cfg)["gates"]
    assert gate["head"] is None and gate["mutants"] == {"killed": 8, "total": 8} and gate["second"]["seconds"] == 0


@pytest.mark.parametrize("option,value", [
    ("--host-passed", "-1"), ("--host-passed", "x"), ("--host-passed", "+1"), ("--host-passed", "1_0"),
    ("--host-passed", "١"), ("--host-passed", "9" * 5000), ("--host-failed", "-1"), ("--host-failed", ""),
    ("--mutants", "9/8"), ("--mutants", "0/0"), ("--mutants", "1/0"), ("--mutants", "-1/8"), ("--mutants", "7"),
    ("--mutants", "7/8/9"), ("--reviewer", "X"), ("--reviewer", "close"),
    ("--review", "CODE-REVIEW: 3 findings (0 critical, 1 high, 1 medium, 0 low)"),
    ("--review", "CODE-REVIEW: 3 findings"), ("--review", "x " + REVIEW), ("--review", REVIEW + " x"),
    ("--review", "CODE-REVIEW: -1 findings (0 critical, 0 high, 0 medium, -1 low)")])
def test_a_bad_gate_value_is_usage(cfg, capsys, option, value):
    usage(cfg, capsys, *with_option(GATE, option, value))


@pytest.mark.parametrize("extra", [
    ["--second", SECOND], ["--second-seconds", "5"],
    ["--second", "CODE-REVIEW: 2 findings (1 critical, 0 high, 0 medium, 0 low)", "--second-seconds", "5"],
    *[["--second", SECOND, "--second-seconds", s]
      for s in ("nan", "inf", "-1", "+1", "1_0", " 5", "x", "", "1e400", "9" * 400, "e3", ".", "1e", "١")]])
def test_a_bad_second_review_is_usage(cfg, capsys, extra):
    usage(cfg, capsys, *GATE, *extra)


@pytest.mark.parametrize("text,seconds", [("1e3", 1000.0), (".5", 0.5), ("5.", 5.0), ("0", 0.0), ("5.e3", 5000.0),
                                          ("1E+3", 1000.0), ("2.5e-1", 0.25), ("1e-400", 0.0)])
def test_a_second_review_takes_any_finite_number_of_0_or_more(cfg, capsys, text, seconds):
    assert record(cfg, *GATE, "--second", SECOND, "--second-seconds", text) == 0
    stored = records(cfg)["gates"][-1]["second"]["seconds"]
    assert stored == seconds and type(stored) is float


@pytest.mark.parametrize("missing", ["--host-passed", "--host-failed", "--review", "--reviewer", "--mutants"])
def test_a_missing_gate_option_is_usage(cfg, capsys, missing):
    args = list(GATE)
    del args[args.index(missing):args.index(missing) + 2]
    usage(cfg, capsys, *args)


# AC 5: the merge and the follow-ups


def test_a_merge_record_and_a_new_one_replaces_it(cfg, capsys):
    assert record(cfg, "merge", "--sha", SHA, "--by", "owner") == 0
    merge = records(cfg)["merge"]
    assert is_utc(merge.pop("time")) and merge == {"sha": SHA, "by": "owner"}
    assert record(cfg, "merge", "--sha", "f" * 40, "--time", "2026-02-28T23:59:59Z", "--by", "main") == 0
    assert capsys.readouterr().out.splitlines()[-1] == "WB: RECORD OK r1 merge"
    assert records(cfg)["merge"] == {"sha": "f" * 40, "time": "2026-02-28T23:59:59Z", "by": "main"}


@pytest.mark.parametrize("sha", [SHA.upper(), SHA[:20].upper() + SHA[20:]])
def test_an_upper_or_mixed_case_sha_is_stored_lower(cfg, capsys, sha):
    assert record(cfg, "merge", "--sha", sha, "--by", "main") == 0
    assert records(cfg)["merge"]["sha"] == SHA


@pytest.mark.parametrize("args", [
    ["--sha", SHA[:-1], "--by", "main"], ["--sha", SHA + "0", "--by", "main"], ["--sha", "g" * 40, "--by", "main"],
    ["--sha", SHA.upper()[:-1], "--by", "main"], ["--sha", SHA.upper() + "A", "--by", "main"],
    ["--sha", "G" * 40, "--by", "main"], ["--sha", SHA, "--by", "bot"], ["--sha", SHA],
    ["--by", "main"], ["--sha", SHA, "--by", "main", "--time", "2026-11-05 10:00:00Z"],
    ["--sha", SHA, "--by", "main", "--time", "2026-13-01T00:00:00Z"],
    ["--sha", SHA, "--by", "main", "--time", "2026-02-30T00:00:00Z"],
    ["--sha", SHA, "--by", "main", "--time", "2026-11-05T10:00:00"],
    ["--sha", SHA, "--by", "main", "--time", "2026-11-05T10:00:00+00:00"]])
def test_a_bad_merge_value_is_usage(cfg, capsys, args):
    usage(cfg, capsys, "merge", *args)


def test_a_follow_up_and_a_known_one_changes_nothing(cfg, capsys):
    assert record(cfg, "follow-up", "--item", "600") == 0
    assert record(cfg, "follow-up", "--item", "a-6_1") == 0
    before = state_bytes(cfg)
    assert record(cfg, "follow-up", "--item", "600") == 0
    assert capsys.readouterr().out.splitlines()[-1] == "WB: RECORD OK r1 follow-up"
    assert state_bytes(cfg) == before
    assert records(cfg)["follow_ups"] == ["600", "a-6_1"]


@pytest.mark.parametrize("item", ["../6", "-6", "", "6/7", "a" * 65, "6 7"])
def test_a_bad_follow_up_is_usage(cfg, capsys, item):
    usage(cfg, capsys, "follow-up", "--item", item)
