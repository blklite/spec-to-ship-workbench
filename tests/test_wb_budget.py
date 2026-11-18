"""The budget stop. Tests at 79, 80, 99 and 100 percent of
a limit, and 1 unpriced step. The fixture spec gives $40, 2 hours and 2 rounds;
the example config alerts at 80 percent and stops at 100."""

from __future__ import annotations

import pytest

from wb import machine, state, tracker
from wb_fakes import HEAD, ONE_DOLLAR, reply, start

GO = "SPEC-CHECK 7: GO"
QUESTIONS = "SPEC-CHECK 7: QUESTIONS - 1 questions"
HOURS = 2 * 3600


@pytest.fixture
def item(tmp_path, monkeypatch):
    return start(tmp_path, monkeypatch)


def preset(cfg, **values):
    st = state.load(cfg, "7")
    st.update(values)
    state.save(cfg, st)


def alerts(cfg):
    return [c["text"] for c in tracker.make(cfg).get("7")["comments"] if c["text"].startswith("Budget alert")]


def run(item):
    cfg, personas, _ = item
    return machine.run_next(cfg, personas, "7")


@pytest.mark.parametrize("usd,alerted", [(31.6, False), (32.0, True)])
def test_the_dollar_alert_at_80_percent(item, usd, alerted):
    cfg, _, r = item
    preset(cfg, usd=usd)
    r.add("spec-checker", QUESTIONS)
    run(item)
    assert len(alerts(cfg)) == (1 if alerted else 0)


@pytest.mark.parametrize("seconds,alerted", [(0.79 * HOURS, False), (0.80 * HOURS, True)])
def test_the_time_alert_at_80_percent(item, seconds, alerted):
    cfg, _, r = item
    preset(cfg, seconds=seconds)
    r.add("spec-checker", QUESTIONS)
    run(item)
    assert len(alerts(cfg)) == (1 if alerted else 0)


def test_the_alert_is_1_comment(item):
    cfg, _, r = item
    preset(cfg, usd=31.5)
    r.add("spec-checker", reply("not ok", ONE_DOLLAR), reply(QUESTIONS, ONE_DOLLAR))
    st = run(item)
    assert st["usd"] == pytest.approx(33.5)
    assert len(alerts(cfg)) == 1 and "dollars" in alerts(cfg)[0]


@pytest.mark.parametrize("usd,stops", [(39.6, False), (40.0, True)])
def test_the_dollar_stop_at_100_percent(item, usd, stops):
    cfg, _, r = item
    preset(cfg, usd=usd)
    r.add("spec-checker", QUESTIONS)
    st = run(item)
    assert (len(r.calls), st["question"]["case"]) == ((0, 1) if stops else (1, 2))


@pytest.mark.parametrize("seconds,stops", [(0.99 * HOURS, False), (1.00 * HOURS, True)])
def test_the_time_stop_at_100_percent(item, seconds, stops):
    cfg, _, r = item
    preset(cfg, seconds=seconds)
    r.add("spec-checker", QUESTIONS)
    st = run(item)
    assert (len(r.calls), st["question"]["case"]) == ((0, 1) if stops else (1, 2))


def test_the_stop_is_checked_before_each_step(item):
    cfg, _, r = item
    preset(cfg, usd=39.5)
    r.add("spec-checker", reply(GO, ONE_DOLLAR))
    st = run(item)
    assert len(r.calls) == 1 and st["stage"] == "build"
    assert (st["status"], st["question"]["case"]) == ("waiting", 1)
    assert "$40.50 of its $40" in st["question"]["text"]
    assert tracker.make(cfg).get("7")["state"] == "Waiting for Owner"


def test_the_step_time_limit_is_the_smaller_of_the_config_and_the_time_left(item):
    cfg, _, r = item
    r.add("spec-checker", QUESTIONS)
    run(item)
    assert r.calls[-1][1] == cfg["steps"]["max_seconds"]
    preset(cfg, seconds=HOURS - 100, status="running", question=None)
    run(item)
    assert 99 < r.calls[-1][1] <= 100


def test_an_unpriced_step_waits(item):
    cfg, _, r = item
    r.add("spec-checker", reply(GO, ONE_DOLLAR, model="fake-model"))
    st = run(item)
    assert st["steps"][-1]["usd"] == "unpriced"
    assert (st["stage"], st["status"], st["question"]["case"]) == ("build", "waiting", 1)
    assert "unpriced" in st["question"]["text"] and st["usd"] == 0
    assert len(r.calls) == 1


def test_an_unpriced_last_step_waits_before_ready(item):
    cfg, personas, r = item
    r.add("spec-checker", GO).add("builder", "BUILD: READY " + HEAD).add("reviewer", "REVIEW: CLOSE")
    r.add("second-reviewer", reply("CODE-REVIEW: 0 findings (0 critical, 0 high, 0 medium, 0 low)",
                                   ONE_DOLLAR, model="fake-model"))
    st = run(item)
    assert (st["stage"], st["status"], st["question"]["case"]) == ("delivery", "waiting", 1)
    st.update(status="running", question=None)  # what wb answer does
    state.save(cfg, st)
    assert run(item)["status"] == "ready" and len(r.calls) == 4


def test_the_item_time_is_the_wall_clock_of_run_next(item):
    cfg, personas, r = item
    r.add("spec-checker", GO).add("builder", "BUILD: BLOCKED - x")
    ticks = iter([1000.0, 1100.0, 1350.0])
    st = machine.run_next(cfg, personas, "7", clock=lambda: next(ticks))
    assert st["seconds"] == pytest.approx(350.0)
    assert sum(s["seconds"] for s in st["steps"]) < 350
