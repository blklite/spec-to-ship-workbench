"""`wb hold <id>` queues a wait with a reason, and the hold ends with the wait."""

from __future__ import annotations

import json
import os
import sys
import threading

import pytest

import test_wb_pull
from test_wb_approve import Terminal, high  # noqa: F401 (high is a fixture)
from wb import intake, machine, run, state, tracker
from wb.cli import main
from wb_fakes import SPEC, make_cfg, start


def waiting(cfg, item_id="h1", case=2, **question) -> None:
    st = state.new(item_id)
    st.update(stage="build", status="waiting", tier="Medium",
              question={"case": case, "text": "Which store?", "run_folder": None, **question})
    state.save(cfg, st)
    tracker.make(cfg).create(item_id, "t", "In Review")


@pytest.fixture
def cfg(tmp_path):
    cfg, _ = make_cfg(tmp_path)
    waiting(cfg)
    return cfg


def hold(cfg, *args, item_id="h1") -> int:
    return main(["hold", item_id, *args], cfg=cfg, personas={"roles": {}})


def files(cfg, item_id="h1") -> dict:
    folder = state.item_dir(cfg, item_id)
    return {n: (folder / n).read_bytes() for n in ("state.json", "tracker.json")}


def test_hold_sets_queued_and_only_that(cfg, capsys):
    before = files(cfg)
    assert hold(cfg, "--reason", "  waits \t for\n the  store ") == 0
    assert capsys.readouterr().out == "WB: HOLD OK h1\n"
    after = files(cfg)
    assert after["tracker.json"] == before["tracker.json"]
    old, new = json.loads(before["state.json"]), json.loads(after["state.json"])
    assert new["question"].pop("queued") == {"reason": "waits for the store", "after": None}
    assert new == old


def test_hold_after(cfg, capsys):
    assert hold(cfg, "--reason", "x", "--after", "9") == 0
    assert state.load(cfg, "h1")["question"]["queued"] == {"reason": "x", "after": "9"}


def test_a_reason_of_200_characters_and_a_new_hold_replaces_the_old(cfg, capsys):
    assert hold(cfg, "--reason", "a", "--after", "9") == 0
    assert hold(cfg, "--reason", "r" * 200) == 0
    assert state.load(cfg, "h1")["question"]["queued"] == {"reason": "r" * 200, "after": None}


def test_hold_clear(cfg, capsys):
    before = files(cfg)
    assert hold(cfg, "--reason", "x") == 0
    assert hold(cfg, "--clear") == 0
    assert capsys.readouterr().out.splitlines()[-1] == "WB: HOLD OK h1"
    assert files(cfg) == before
    assert hold(cfg, "--clear") == 0  # no hold: nothing changes
    assert files(cfg) == before


def refused(cfg, capsys, *args, why: str) -> None:
    before = files(cfg)
    assert hold(cfg, *args) == 1
    assert capsys.readouterr().out == f"WB: HOLD REFUSE h1 - {why}\n"
    assert files(cfg) == before


def set_state(cfg, **fields) -> None:
    st = state.load(cfg, "h1")
    st.update(fields)
    state.save(cfg, st)


def test_refuses_an_item_that_does_not_wait(cfg, capsys):
    set_state(cfg, status="running", question=None)
    refused(cfg, capsys, "--reason", "x", why="item h1 does not wait for the owner (status running)")
    refused(cfg, capsys, "--clear", why="item h1 does not wait for the owner (status running)")
    set_state(cfg, status="ready", question={"case": 2, "text": "old", "run_folder": None})
    refused(cfg, capsys, "--reason", "x", why="item h1 does not wait for the owner (status ready)")


@pytest.mark.parametrize("case", [3, 4])
def test_refuses_case_3_and_4(cfg, capsys, case):
    set_state(cfg, question={"case": case, "text": "x", "run_folder": None})
    refused(cfg, capsys, "--reason", "x", why=f"a case {case} wait is never queued")


def test_case_1_can_be_held(cfg, capsys):
    set_state(cfg, question={"case": 1, "text": "x", "run_folder": None})
    assert hold(cfg, "--reason", "x") == 0


def test_refuses_a_live_run(cfg, capsys):
    lock = run.lock_path(cfg, "h1")
    lock.write_text(json.dumps({"pid": os.getpid(), "token": run.start_token(os.getpid())}), encoding="utf-8")
    refused(cfg, capsys, "--reason", "x", why=f"a run is going (pid {os.getpid()})")
    lock.write_text(json.dumps({"pid": 0, "token": None}), encoding="utf-8")  # a dead run does not refuse
    assert hold(cfg, "--reason", "x") == 0


def test_hold_waits_for_the_answer_lock(cfg, tmp_path, capsys):
    """An answer that holds answer.flock saves first; the hold then sees no wait and writes nothing over it."""
    results = {}
    with state.answer_lock(cfg, "h1"):
        t = threading.Thread(target=lambda: results.update(code=hold(cfg, "--reason", "x")))
        t.start()
        t.join(0.5)
        assert t.is_alive()  # blocked on the lock
        st = state.load(cfg, "h1")
        machine.store(cfg, st, b"Keep the file.")
    t.join(10)
    assert results == {"code": 1}
    assert state.load(cfg, "h1")["question"] is None


@pytest.mark.parametrize("reason", ["", " \t\n ", "r" * 201])
def test_refuses_a_reason_of_0_or_201_characters(cfg, capsys, reason):
    refused(cfg, capsys, "--reason", reason, why="the reason must have 1 to 200 characters")


@pytest.mark.parametrize("args", [["--reason", "x"], ["--clear"]])
@pytest.mark.parametrize("item_id", ["../h1", "a b", "nope"])
def test_bad_id_is_usage(cfg, capsys, args, item_id):
    before = files(cfg)
    assert hold(cfg, *args, item_id=item_id) == 2
    assert capsys.readouterr().out == ""
    assert files(cfg) == before


@pytest.mark.parametrize("args", [["--reason", "x", "--after", "../9"], ["--clear", "--after", "9"],
                                  ["--clear", "--reason", "x"], [], ["--after", "9"]])
def test_bad_arguments_are_usage(cfg, capsys, args):
    """argparse exits 2 itself; the checks of _hold return 2."""
    before = files(cfg)
    try:
        code = hold(cfg, *args)
    except SystemExit as e:
        code = e.code
    assert code == 2
    assert "HOLD" not in capsys.readouterr().out
    assert files(cfg) == before


# AC 2: the hold ends with the wait


def queued(cfg, item_id) -> None:
    """A hold set by hand: a case 3 wait cannot be held through wb hold."""
    st = state.load(cfg, item_id)
    st["question"]["queued"] = {"reason": "x", "after": None}
    state.save(cfg, st)


def test_an_answer_ends_the_hold(cfg, tmp_path, capsys):
    assert hold(cfg, "--reason", "x") == 0
    answer = tmp_path / "answer.md"
    answer.write_text("Keep the file.", encoding="utf-8")
    assert main(["answer", "h1", "--file", str(answer)], cfg=cfg, personas={"roles": {}}) == 0
    assert state.load(cfg, "h1")["question"] is None


def test_pull_answers_ends_the_hold(tmp_path, capsys):
    cfg, _ = make_cfg(tmp_path)
    test_wb_pull.waiting(cfg, "b1")
    tracker.make(cfg).create("b1", "t", "In Review")
    assert hold(cfg, "--reason", "x", item_id="b1") == 0
    queue = tmp_path / "queue"
    queue.mkdir()
    test_wb_pull.put(queue, "a1.json", test_wb_pull.good())
    assert main(["pull-answers", "--queue", str(queue)], cfg=cfg, personas={"roles": {}}) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "PULL: answered=1 refused=0"
    assert state.load(cfg, "b1")["question"] is None


def test_approve_high_ends_the_hold(high, monkeypatch, capsys):
    cfg, personas, _, _ = high
    queued(cfg, "7")
    monkeypatch.setattr(sys, "stdin", Terminal("7\n"))
    assert main(["approve", "7", "high"], cfg=cfg, personas=personas) == 0
    assert state.load(cfg, "7")["question"] is None


def test_a_new_intake_ends_the_hold(tmp_path, monkeypatch, capsys):
    cfg, personas, _ = start(tmp_path, monkeypatch)
    st = state.load(cfg, "7")
    machine.wait(cfg, tracker.make(cfg), st, 2, "Which store?")
    state.save(cfg, st)
    assert hold(cfg, "--reason", "x", item_id="7") == 0
    assert intake.intake(cfg, str(SPEC), "7") == []
    assert state.load(cfg, "7")["question"] is None


def test_a_new_wait_ends_the_hold(cfg, capsys):
    assert hold(cfg, "--reason", "x", "--after", "9") == 0
    st = state.load(cfg, "h1")
    machine.wait(cfg, tracker.make(cfg), st, 2, "Another question?")
    state.save(cfg, st)
    assert "queued" not in state.load(cfg, "h1")["question"]
