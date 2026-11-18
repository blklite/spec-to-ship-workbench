"""wb intake refuses a spec that breaks docs/specs.md, 1 line
for each problem; a pass creates the tracker item and the state at stage 2."""

from __future__ import annotations

import hashlib
import subprocess
import sys

import pytest

from conftest import ROOT
from wb import state, tracker
from wb.cli import main
from wb_fakes import SPEC, make_cfg

GOOD = SPEC.read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.fixture
def env(tmp_path):
    return make_cfg(tmp_path)


def run(env, capsys, spec, item="7", *extra):
    cfg, personas = env
    code = main(["intake", str(spec), "--id", item, *extra], cfg=cfg, personas=personas)
    return code, capsys.readouterr().out.splitlines()


def write(tmp_path, text, name="spec.md"):
    p = tmp_path / name
    p.write_bytes(text.encode("utf-8"))
    return p


def test_the_generic_fixture_passes(env, capsys, tmp_path):
    cfg, _ = env
    code, out = run(env, capsys, SPEC)
    assert (code, out) == (0, ["WB: INTAKE OK 7"])
    st = state.load(cfg, "7")
    assert (st["stage"], st["status"], st["tier"], st["size"]) == ("spec-check", "running", "Medium", "Small")
    assert st["limits"] == {"usd": 40.0, "hours": 2.0, "rounds": 2}
    assert st["spec"] == {"path": str(SPEC.resolve()), "sha256": hashlib.sha256(SPEC.read_bytes()).hexdigest()}
    assert st["work"] == {"folder": str(tmp_path / "work" / "7"), "branch": "feature/7", "base": "main",
                          "repo": None, "url": None}
    item = tracker.make(cfg).get("7")
    assert (item["title"], item["state"]) == ("A greeting on the start page", "In Review")


def test_branch_option(env, capsys):
    cfg, _ = env
    assert run(env, capsys, SPEC, "7", "--branch", "topic/greeting")[0] == 0
    assert state.load(cfg, "7")["work"]["branch"] == "topic/greeting"


BROKEN = {
    "too large": GOOD + "\n" + "x" * 8 * 1024,
    "no title": GOOD.replace("# A greeting on the start page\n", ""),
    "no status": GOOD.replace("**Status:** Checked. ", ""),
    "no work item": GOOD.replace("**Work item:** an example link.", ""),
    "no tier": GOOD.replace("**Tier:** Medium (a page that a reader sees). ", ""),
    "no size": GOOD.replace("**Size:** Small, $40, 2 hours, 2 rework rounds.", ""),
    "no retire list": GOOD.replace("**Tests that the item can retire:** none.\n", ""),
    "no story": GOOD.replace("## Story", "## The story"),
    "no rulings": GOOD.replace("## Rulings\n", ""),
    "no acceptance criteria": GOOD.replace("## Acceptance criteria", "## ACs"),
    "no non-goals": GOOD.replace("## Non-goals", "## Not in scope"),
    "no checks": GOOD.replace("## Checks (Medium tier)", "## Tests"),
    "no delivery": GOOD.replace("## Delivery", "## Shipping"),
    "bad tier": GOOD.replace("**Tier:** Medium", "**Tier:** Severe"),
    "bad size": GOOD.replace("**Size:** Small, $40", "**Size:** Large, $40"),
    "size with no dollars": GOOD.replace("Small, $40, 2 hours", "Small, 2 hours"),
    "an AC with no proof": GOOD.replace(' *Proof:* a page test finds the text.', ""),
    "a continued AC with no proof": GOOD.replace(" *Proof:* the existing page tests pass.", ""),
    "an open questions heading": GOOD.replace("## Delivery", "## Open questions\n\nNone yet.\n\n## Delivery"),
    "a TBD": GOOD.replace("The greeting is plain text.", "The colour is TBD."),
    "a TODO": GOOD.replace("A greeting in other languages.", "TODO: list them."),
}


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_each_rule_refuses(env, capsys, tmp_path, case):
    cfg, _ = env
    code, out = run(env, capsys, write(tmp_path, BROKEN[case]))
    assert code == 1
    assert out[-1] == "WB: INTAKE REFUSE 7 - 1 problems", out
    assert len(out) == 2
    assert not state.exists(cfg, "7")
    assert not (tmp_path / "state" / "7" / "tracker.json").exists()


def test_problem_lines_name_the_rule(env, capsys, tmp_path):
    text = BROKEN["a TBD"].replace("## Rulings", "## Ruled").replace("*Proof:* a page", "a page")
    code, out = run(env, capsys, write(tmp_path, text))
    assert code == 1
    assert out == ["a section is missing: ## Rulings", "AC 1 has no *Proof:*",
                   "line 13: an open-question mark: TBD", "WB: INTAKE REFUSE 7 - 3 problems"]


@pytest.mark.parametrize("ok", ["a todo list in lower case", "TODOS", "`TBD` in code", "the TBDs"])
def test_only_a_whole_word_outside_code_is_a_mark(env, capsys, tmp_path, ok):
    code, _ = run(env, capsys, write(tmp_path, GOOD.replace("The greeting is plain text.", ok)))
    assert code == 0


def test_a_spec_at_the_limit_passes(env, capsys, tmp_path):
    text = GOOD + "\n" + "x" * (8 * 1024 - len(GOOD.encode("utf-8")) - 1)
    assert len(text.encode("utf-8")) == 8 * 1024
    assert run(env, capsys, write(tmp_path, text))[0] == 0


def test_a_new_intake_of_a_running_item_is_refused(env, capsys):
    assert run(env, capsys, SPEC)[0] == 0
    code, out = run(env, capsys, SPEC)
    assert code == 1
    assert "exists and does not wait" in out[0]


def test_a_new_intake_of_a_waiting_item_restarts_the_spec_check(env, capsys, tmp_path):
    cfg, _ = env
    assert run(env, capsys, SPEC)[0] == 0
    st = state.load(cfg, "7")
    st.update(stage="spec-check", status="waiting", question={"case": 2, "text": "q", "run_folder": None})
    st["usd"] = 3.0
    state.save(cfg, st)
    amended = write(tmp_path, GOOD.replace("Small, $40", "Small, $30"))
    assert run(env, capsys, amended)[0] == 0
    st = state.load(cfg, "7")
    assert (st["stage"], st["status"], st["question"], st["usd"]) == ("spec-check", "running", None, 3.0)
    assert st["limits"]["usd"] == 30.0
    assert st["spec"]["sha256"] == hashlib.sha256(amended.read_bytes()).hexdigest()
    assert tracker.make(cfg).get("7")["state"] == "In Review"


@pytest.mark.parametrize("argv", [["intake", "spec.md", "--id", "../x"],
                                  ["intake", "no-such-spec.md", "--id", "7"],
                                  ["intake", "spec.md", "--id", "7", "--branch", "a;b"]])
def test_usage_errors_exit_2(env, capsys, tmp_path, monkeypatch, argv):
    cfg, personas = env
    write(tmp_path, GOOD)
    monkeypatch.chdir(tmp_path)
    assert main(argv, cfg=cfg, personas=personas) == 2
    assert capsys.readouterr().err.startswith("wb: ")


def test_module_entry_point(tmp_path):
    """python -m wb with the example config (skipped when a local config exists)."""
    if (ROOT / "config" / "workbench.toml").exists():
        pytest.skip("a local config/workbench.toml exists")
    r = subprocess.run([sys.executable, "-m", "wb", "intake", str(write(tmp_path, BROKEN["a TBD"])),
                        "--id", "7"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 1, r.stderr
    assert r.stdout.splitlines()[-1] == "WB: INTAKE REFUSE 7 - 1 problems"


def test_a_new_intake_voids_the_approvals(env, capsys):
    """Code review: an approval was given for the old spec."""
    cfg, _ = env
    assert run(env, capsys, SPEC)[0] == 0
    st = state.load(cfg, "7")
    st.update(status="waiting", question={"case": 2, "text": "q", "run_folder": None},
              approvals=[{"kind": "high", "head": "a" * 40, "time": "T1"},
                         {"kind": "merge", "head": "b" * 40, "time": "T2", "void": "T3"}])
    state.save(cfg, st)
    assert run(env, capsys, SPEC)[0] == 0
    a = state.load(cfg, "7")["approvals"]
    assert a[0]["void"] and a[1]["void"] == "T3"
