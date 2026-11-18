"""wb status on a fixture state, and the subagent cost
field of the tracker after each step."""

from __future__ import annotations

import json

import pytest

from wb import machine, state, tracker
from wb.cli import main
from wb_fakes import ONE_DOLLAR, make_cfg, reply, start


def step(n, stage, role, usd, tokens, seconds, status="OK", last="X: OK"):
    return {"n": n, "stage": stage, "role": role, "runner": f"fake-{role}", "run_folder": f"/runs/{n}",
            "status": status, "last_line": last if status == "OK" else None, "reason": "why",
            "usd": usd, "seconds": seconds, "tokens": dict(zip(("input", "cache_read", "cache_write", "output"),
                                                                tokens)),
            "head_before": None, "head_after": None, "outcome": None}


@pytest.fixture
def fixture_state(tmp_path):
    cfg, personas = make_cfg(tmp_path)
    st = state.new("7")
    st.update(tier="Medium", size="Small", limits={"usd": 40.0, "hours": 2.0, "rounds": 2}, stage="rework",
              status="waiting", rounds_used=2, usd=3.5, seconds=1800.0,
              question={"case": 4, "text": "The review has 1 blocking\nfindings.", "run_folder": "/runs/5"},
              not_verified=["step 4: the second review did not run: no CLI"],
              approvals=[{"kind": "merge", "head": "a" * 40, "time": "T1", "void": "T2"}])
    st["steps"] = [step(1, "spec-check", "spec-checker", 0.5, (10, 20, 30, 40), 10.0),
                   step(2, "build", "builder", 2.0, (100, 200, 300, 400), 100.0),
                   step(3, "build", "builder", "unpriced", (1, None, None, 1), 5.0, status="INVALID"),
                   step(4, "review", "second-reviewer", None, (None, None, None, None), 0.0, status="UNAVAILABLE"),
                   step(5, "review", "reviewer", 1.0, (1, 2, 3, 4), 20.0)]
    state.save(cfg, st)
    other = state.new("8")
    other.update(limits={"usd": 100.0, "hours": 4.0, "rounds": 2}, status="ready", stage="delivery")
    state.save(cfg, other)
    return cfg, personas


def out(capsys):
    return capsys.readouterr().out.splitlines()


def test_1_line_for_each_item(fixture_state, capsys):
    cfg, personas = fixture_state
    assert main(["status"], cfg=cfg, personas=personas) == 0
    assert out(capsys) == [
        "7 stage=rework status=waiting rounds=2/2 usd=3.50/40 hours=0.50/2",
        "8 stage=delivery status=ready rounds=0/2 usd=0.00/100 hours=0.00/4",
    ]


def test_the_detail_of_1_item(fixture_state, capsys):
    cfg, personas = fixture_state
    assert main(["status", "7"], cfg=cfg, personas=personas) == 0
    lines = out(capsys)
    assert lines[0] == "7 stage=rework status=waiting rounds=2/2 usd=3.50/40 hours=0.50/2"
    assert lines[1] == "  step 1: spec-check spec-checker on fake-spec-checker: OK usd=0.5000 seconds=10.0" \
                       " last line: X: OK (run folder: /runs/1)"
    assert lines[3].startswith("  step 3: build builder on fake-builder: INVALID usd=unpriced ")
    assert lines[3].endswith(" last line: why (run folder: /runs/3)")
    assert lines[6] == "  question: case 4: The review has 1 blocking findings. (run folder: /runs/5)"
    assert lines[7] == "  not verified: step 4: the second review did not run: no CLI"
    assert lines[8] == f"  approval: merge at {'a' * 40} on T1 (void)"


def test_json_by_stage_and_by_role(fixture_state, capsys):
    cfg, personas = fixture_state
    assert main(["status", "7", "--json"], cfg=cfg, personas=personas) == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["id"], data["status"], data["item_seconds"]) == ("7", "waiting", 1800.0)
    assert data["by_stage"]["build"] == {"usd": 2.0, "unpriced_steps": 1, "input": 101, "cache_read": 200,
                                         "cache_write": 300, "output": 401, "seconds": 105.0}
    assert data["by_role"]["second-reviewer"] == {"usd": 0.0, "unpriced_steps": 0, "input": 0, "cache_read": 0,
                                                  "cache_write": 0, "output": 0, "seconds": 0.0}
    assert data["by_stage"]["review"]["usd"] == 1.0 and data["by_role"]["reviewer"]["output"] == 4
    assert data["total"] == {"usd": 3.5, "unpriced_steps": 1, "input": 112, "cache_read": 222, "cache_write": 333,
                             "output": 445, "seconds": 135.0}


def test_json_needs_an_id(fixture_state, capsys):
    cfg, personas = fixture_state
    assert main(["status", "--json"], cfg=cfg, personas=personas) == 2


def test_no_items(tmp_path, capsys):
    cfg, personas = make_cfg(tmp_path)
    assert main(["status"], cfg=cfg, personas=personas) == 0
    assert out(capsys) == []


def test_the_cost_field_is_the_sum_of_the_steps(tmp_path, monkeypatch):
    cfg, personas, r = start(tmp_path, monkeypatch)
    r.add("spec-checker", reply("no last line", ONE_DOLLAR), reply("SPEC-CHECK 7: GO", ONE_DOLLAR))
    r.add("builder", reply("BUILD: BLOCKED - x", {**ONE_DOLLAR, "input": 125000}))
    machine.run_next(cfg, personas, "7")
    fields = tracker.make(cfg).get("7")["fields"]
    assert fields == {cfg["tracker"]["subagent_cost_field"]: 2.5}


# the repository of an item

def test_the_detail_shows_the_repo_when_it_is_set(fixture_state, capsys):
    cfg, personas = fixture_state
    main(["status", "7"], cfg=cfg, personas=personas)
    plain = out(capsys)
    st = state.load(cfg, "7")
    st["work"].update(repo="widget", url="https://github.com/example/widget.git")
    state.save(cfg, st)
    assert main(["status", "7"], cfg=cfg, personas=personas) == 0
    assert out(capsys) == [plain[0] + " repo=widget", *plain[1:]]
    assert main(["status"], cfg=cfg, personas=personas) == 0
    assert "repo=" not in "\n".join(out(capsys))  # the line of each item stays as it was


@pytest.mark.parametrize("old", [False, True])
def test_the_detail_has_nothing_extra_when_the_repo_is_null(fixture_state, capsys, old):
    cfg, personas = fixture_state
    if old:  # a state of before [repos]
        st = state.load(cfg, "7")
        del st["work"]["repo"], st["work"]["url"]
        state.save(cfg, st)
    assert main(["status", "7"], cfg=cfg, personas=personas) == 0
    lines = out(capsys)
    assert lines[0] == "7 stage=rework status=waiting rounds=2/2 usd=3.50/40 hours=0.50/2"
    assert "repo=" not in "\n".join(lines)
