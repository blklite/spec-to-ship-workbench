"""The prompt of a step is an Inputs block, then the stage
prompt byte for byte; replies pass by path."""

from __future__ import annotations

import json

import pytest

from conftest import ROOT
from runner import lastline
from wb import machine, state


def item(steps=(), answers=()) -> dict:
    st = state.new("7")
    st["spec"] = {"path": "/specs/7.md", "sha256": "0" * 64}
    st["work"] = {"folder": "/work/7", "branch": "feature/7", "base": "main"}
    st["steps"] = [{"n": i, "stage": stage, "role": role, "run_folder": f"/runs/{i}", "outcome": outcome}
                   for i, (stage, role, outcome) in enumerate(steps, 1)]
    st["answers"] = list(answers)
    return st


def written(tmp_path, st, role) -> tuple[list[str], bytes]:
    data = machine.write_prompt(st, role, tmp_path).read_bytes()
    stage = (ROOT / "prompts" / machine.PROMPTS[role]).read_bytes()
    assert data.endswith(stage)
    head = data[:-len(stage)].decode("utf-8")
    assert head.endswith("\n\n")
    return head.rstrip("\n").splitlines(), data


def replies(lines):
    return [x for x in lines if x.startswith("- Reply of step ")]


@pytest.mark.parametrize("role", sorted(machine.PROMPTS))
def test_each_prompt_keeps_the_allowed_last_lines(tmp_path, role):
    lines, _ = written(tmp_path, item(), role)
    assert lines[:7] == ["# Inputs", "", "- Item: 7", "- Spec: /specs/7.md", "- Work folder: /work/7",
                         "- Branch: feature/7", "- Base: main"]
    stage = lastline.read_allowed(ROOT / "prompts" / machine.PROMPTS[role])
    assert lastline.read_allowed(tmp_path / "prompt.md") == stage


def test_spec_check_inputs(tmp_path):
    lines, _ = written(tmp_path, item(answers=["/answers/001.md"]), "spec-checker")
    assert replies(lines) == []
    assert lines[-1] == "- Owner answer: /answers/001.md"


def test_build_inputs(tmp_path):
    st = item([("spec-check", "spec-checker", None), ("spec-check", "spec-checker", {"verdict": "GO"})])
    lines, _ = written(tmp_path, st, "builder")
    assert replies(lines) == [f"- Reply of step 2 (spec-checker): {machine.reply_path(st['steps'][1])}"]


@pytest.mark.parametrize("role", ["reviewer", "second-reviewer"])
def test_review_inputs(tmp_path, role):
    go, ready, rework, done = ({"verdict": v} for v in ("GO", "READY", "REWORK", "DONE"))
    st = item([("spec-check", "spec-checker", go), ("build", "builder", ready)])
    assert replies(written(tmp_path, st, role)[0]) == [f"- Reply of step 2 (builder): {machine.reply_path(st['steps'][1])}"]
    st = item([("spec-check", "spec-checker", go), ("build", "builder", ready), ("review", "reviewer", rework),
               ("rework", "rework", done)])
    assert [x.split(":")[0] for x in replies(written(tmp_path, st, role)[0])] == [
        "- Reply of step 2 (builder)", "- Reply of step 4 (rework)"]


def test_rework_inputs_are_the_last_review_pass(tmp_path):
    ok = {"verdict": "REWORK"}
    st = item([("build", "builder", {"verdict": "READY"}), ("review", "reviewer", ok),
               ("review", "second-reviewer", ok), ("rework", "rework", {"verdict": "DONE"}),
               ("review", "reviewer", None), ("review", "reviewer", ok), ("review", "second-reviewer", None)])
    assert [x.split(":")[0] for x in replies(written(tmp_path, st, "rework")[0])] == [
        "- Reply of step 6 (reviewer)"]


def test_the_stage_prompt_is_copied_byte_for_byte(tmp_path):
    _, data = written(tmp_path, item(), "builder")
    assert data.endswith((ROOT / "prompts" / "build.md").read_bytes())


def test_a_blocked_step_is_no_input(tmp_path):
    """Code review: a rework after an answer still gets the review replies."""
    st = item([("spec-check", "spec-checker", {"verdict": "GO"}), ("build", "builder", {"verdict": "READY"}),
               ("review", "reviewer", {"verdict": "REWORK", "blocking": 1}),
               ("rework", "rework", {"verdict": "BLOCKED"})], answers=["/answers/001.md"])
    lines, _ = written(tmp_path, st, "rework")
    assert [x.split(":")[0] for x in replies(lines)] == ["- Reply of step 3 (reviewer)"]
    assert lines[-1] == "- Owner answer: /answers/001.md"
    st = item([("spec-check", "spec-checker", {"verdict": "GO"}), ("build", "builder", {"verdict": "BLOCKED"})])
    assert [x.split(":")[0] for x in replies(written(tmp_path, st, "builder")[0])] == [
        "- Reply of step 1 (spec-checker)"]
    assert replies(written(tmp_path, st, "reviewer")[0]) == []


# --- the next step reads reply.txt when the run has one ---

def built(tmp_path, stdout: str, reply: str | None) -> dict:
    """A state whose step 2 is a READY build with a real run folder."""
    run = tmp_path / "runs" / "002-builder"
    run.mkdir(parents=True)
    (run / "stdout.txt").write_text(stdout, encoding="utf-8")
    if reply is not None:
        (run / "reply.txt").write_text(reply, encoding="utf-8")
    st = item([("spec-check", "spec-checker", {"verdict": "GO"}), ("build", "builder", {"verdict": "READY"})])
    st["steps"][1]["run_folder"] = str(run)
    return st


def test_reply_path_gives_reply_txt_when_it_exists(tmp_path):
    st = built(tmp_path, "{}", "the report")
    assert machine.reply_path(st["steps"][1]) == str(tmp_path / "runs" / "002-builder" / "reply.txt")
    assert replies(written(tmp_path, st, "reviewer")[0]) == [
        f"- Reply of step 2 (builder): {tmp_path / 'runs' / '002-builder' / 'reply.txt'}"]


def test_a_run_folder_from_before_reply_txt_gives_stdout_txt(tmp_path):
    st = built(tmp_path, "{}", None)
    assert machine.reply_path(st["steps"][1]) == str(tmp_path / "runs" / "002-builder" / "stdout.txt")


def test_the_copy_for_a_wrapped_step_holds_the_first_turn(tmp_path):
    from runner.adapters import claude
    first, last = "I built it.\n\nBUILD: READY " + "a" * 40, "The report above stands.\n\nBUILD: READY " + "a" * 40
    out = "".join(json.dumps(x) + "\n" for x in (
        {"type": "system"}, {"type": "result", "result": first}, {"type": "result", "result": last}))
    st = built(tmp_path, out, claude.parse(out, {"model": "m"}).report)
    st["spec"]["path"] = str(tmp_path / "spec.md")
    (tmp_path / "spec.md").write_text("spec", encoding="utf-8")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    machine.write_prompt(st, "reviewer", tmp_path, inputs)
    copy = (inputs / "reply-002-builder.txt").read_text(encoding="utf-8")
    assert copy.startswith(first) and copy.endswith(last)
