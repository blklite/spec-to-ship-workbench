"""The stops. A wait sets the status, the waiting state of
the tracker and 1 comment of at most messages.max_chars characters: the case,
the question and the run folder. 1 test for each case."""

from __future__ import annotations

import pytest

from wb import machine, state, tracker
from wb.cli import main
from wb_fakes import GOOD_SPEC_TEXT, HEAD, start

GO = "SPEC-CHECK 7: GO"
QUESTIONS = "SPEC-CHECK 7: QUESTIONS - 1 questions"
READY = "BUILD: READY " + HEAD
CLOSE = "REVIEW: CLOSE"
REWORK = "REVIEW: REWORK - 1 blocking"
CR_CLEAN = "CODE-REVIEW: 0 findings (0 critical, 0 high, 0 medium, 0 low)"


@pytest.fixture
def item(tmp_path, monkeypatch):
    return start(tmp_path, monkeypatch)


def run(item):
    cfg, personas, _ = item
    return machine.run_next(cfg, personas, "7")


def comments(cfg):
    return [c["text"] for c in tracker.make(cfg).get("7")["comments"]]


def assert_waits(cfg, st, case):
    assert (st["status"], st["question"]["case"]) == ("waiting", case)
    assert tracker.make(cfg).get("7")["state"] == cfg["owner"]["waiting_state"]
    texts = [c for c in comments(cfg) if c.startswith("Waiting for the owner")]
    assert len(texts) == 1
    assert texts[0].startswith(f"Waiting for the owner, case {case} ({machine.CASES[case]}): ")
    assert " ".join(st["question"]["text"].split()) in texts[0]
    if st["question"]["run_folder"]:
        assert texts[0].endswith(f" Run folder: {st['question']['run_folder']}")
    assert len(texts[0]) <= cfg["messages"]["max_chars"]


def test_case_1_the_budget(item):
    cfg, _, r = item
    st = state.load(cfg, "7")
    st["usd"] = 40.0
    state.save(cfg, st)
    assert_waits(cfg, run(item), 1)
    assert r.calls == []


def test_case_2_a_decision(item):
    cfg, _, r = item
    r.add("spec-checker", GO).add("builder", "BUILD: BLOCKED - which colour?")
    st = run(item)
    assert_waits(cfg, st, 2)
    assert st["question"]["run_folder"] == st["steps"][-1]["run_folder"]


def test_case_3_a_high_tier_item_waits_before_the_build(tmp_path, monkeypatch):
    cfg, personas, r = start(tmp_path, monkeypatch, spec_text=GOOD_SPEC_TEXT.replace(
        "**Tier:** Medium", "**Tier:** High"))
    r.add("spec-checker", GO).add("builder", READY).add("reviewer", CLOSE).add("second-reviewer", CR_CLEAN)
    st = machine.run_next(cfg, personas, "7")
    assert_waits(cfg, st, 3)
    assert st["stage"] == "build" and r.roles() == ["spec-checker"]
    answer = tmp_path / "a.md"
    answer.write_text("approved, go on", encoding="utf-8")
    assert machine.answer(cfg, "7", str(answer)) == "case 3 waits for wb approve 7 high"
    st = machine.run_next(cfg, personas, "7")
    assert st["status"] == "waiting" and r.roles() == ["spec-checker"]
    st["approvals"].append({"kind": "high", "head": "a" * 40, "time": "t"})  # what wb approve 7 high records
    st.update(status="running", question=None)
    state.save(cfg, st)
    assert machine.run_next(cfg, personas, "7")["status"] == "ready"
    assert r.roles()[1] == "builder"


def test_the_case_3_question_asks_for_no_sha(tmp_path, monkeypatch):
    """The owner types the command as the question gives it, with no SHA."""
    cfg, personas, r = start(tmp_path, monkeypatch, spec_text=GOOD_SPEC_TEXT.replace(
        "**Tier:** Medium", "**Tier:** High"))
    r.add("spec-checker", GO)
    text = machine.run_next(cfg, personas, "7")["question"]["text"]
    assert "python -m wb approve 7 high" in text and "--head" not in text and "<sha>" not in text


def test_a_void_high_approval_does_not_pass_case_3(tmp_path, monkeypatch):
    cfg, personas, r = start(tmp_path, monkeypatch, spec_text=GOOD_SPEC_TEXT.replace(
        "**Tier:** Medium", "**Tier:** High"))
    st = state.load(cfg, "7")
    st.update(stage="build", approvals=[{"kind": "high", "head": "a" * 40, "time": "t", "void": "t2"}])
    state.save(cfg, st)
    assert machine.run_next(cfg, personas, "7")["question"]["case"] == 3


def test_case_4_the_round_limit(item):
    cfg, _, r = item
    r.add("spec-checker", GO).add("builder", READY).add("reviewer", REWORK).add("second-reviewer", CR_CLEAN)
    r.add("rework", "REWORK: DONE")
    assert_waits(cfg, run(item), 4)


def test_the_comment_fits_max_chars_and_keeps_the_run_folder(item):
    cfg, _, r = item
    cfg["messages"]["max_chars"] = 200
    r.add("spec-checker", GO).add("builder", "BUILD: BLOCKED - " + "why " * 300)
    st = run(item)
    text = comments(cfg)[-1]
    assert len(text) == 200
    assert text.endswith(f"... Run folder: {st['question']['run_folder']}")
    assert text.startswith("Waiting for the owner, case 2 ")


def test_an_answer_resumes_the_item_and_goes_into_the_next_inputs(item, tmp_path):
    cfg, personas, r = item
    r.add("spec-checker", GO).add("builder", "BUILD: BLOCKED - which colour?", READY)
    r.add("reviewer", CLOSE).add("second-reviewer", CR_CLEAN)
    run(item)
    answer = tmp_path / "answer.md"
    answer.write_text("Blue.\n", encoding="utf-8")
    assert main(["answer", "7", "--file", str(answer)], cfg=cfg, personas=personas) == 0
    st = state.load(cfg, "7")
    assert (st["status"], st["question"]) == ("running", None)
    stored = st["answers"][0]
    assert open(stored, encoding="utf-8").read() == "Blue.\n"
    st = run(item)
    assert st["status"] == "ready"
    prompt = (tmp_path / "state" / "7" / "runs" / "003-builder" / "prompt.md").read_text(encoding="utf-8")
    assert f"- Owner answer: {stored}\n" in prompt


def test_an_answer_to_a_running_item_is_refused(item, tmp_path, capsys):
    cfg, personas, _ = item
    answer = tmp_path / "answer.md"
    answer.write_text("x", encoding="utf-8")
    assert main(["answer", "7", "--file", str(answer)], cfg=cfg, personas=personas) == 1
    assert capsys.readouterr().out.startswith("WB: ANSWER REFUSE 7 - item 7 does not wait")


def test_after_spec_questions_only_a_new_intake_resumes(item, tmp_path, capsys):
    cfg, personas, r = item
    r.add("spec-checker", QUESTIONS, GO).add("builder", "BUILD: BLOCKED - x")
    run(item)
    answer = tmp_path / "answer.md"
    answer.write_text("x", encoding="utf-8")
    assert main(["answer", "7", "--file", str(answer)], cfg=cfg, personas=personas) == 1
    assert "amend the spec, then run wb intake again" in capsys.readouterr().out
    assert state.load(cfg, "7")["status"] == "waiting"
    amended = tmp_path / "amended.md"
    amended.write_text(GOOD_SPEC_TEXT.replace("The greeting is plain text.", "Plain text, black."),
                       encoding="utf-8")
    assert main(["intake", str(amended), "--id", "7"], cfg=cfg, personas=personas) == 0
    st = run(item)
    assert r.roles() == ["spec-checker", "spec-checker", "builder"]
    assert st["question"]["case"] == 2 and st["stage"] == "build"


def test_an_answer_cannot_clear_a_budget_stop(item, tmp_path, capsys):
    """Code review: only the owner's spec sets the limits."""
    cfg, personas, r = item
    st = state.load(cfg, "7")
    st["usd"] = 40.0
    state.save(cfg, st)
    run(item)
    answer = tmp_path / "answer.md"
    answer.write_text("go on", encoding="utf-8")
    assert main(["answer", "7", "--file", str(answer)], cfg=cfg, personas=personas) == 1
    assert "raise the limits in the spec, then run wb intake again" in capsys.readouterr().out
    assert state.load(cfg, "7")["status"] == "waiting"
    amended = tmp_path / "amended.md"
    amended.write_text(GOOD_SPEC_TEXT.replace("Small, $40", "Small, $60"), encoding="utf-8")
    assert main(["intake", str(amended), "--id", "7"], cfg=cfg, personas=personas) == 0
    r.add("spec-checker", QUESTIONS)
    st = run(item)
    assert r.roles() == ["spec-checker"] and st["usd"] == 40.0 and st["question"]["case"] == 2


def test_an_answer_clears_an_unpriced_wait(item, tmp_path):
    from wb_fakes import ONE_DOLLAR, reply

    cfg, personas, r = item
    r.add("spec-checker", reply(GO, ONE_DOLLAR, model="fake-model")).add("builder", "BUILD: BLOCKED - x")
    assert run(item)["question"]["case"] == 1
    answer = tmp_path / "answer.md"
    answer.write_text("the price is known", encoding="utf-8")
    assert machine.answer(cfg, "7", str(answer)) is None
    assert run(item)["steps"][-1]["role"] == "builder"
