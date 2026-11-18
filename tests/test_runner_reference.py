"""`python -m runner.reference` runs a reference item on a
runner: a throwaway clone at the item's base with spec.md added, the prompt
(the item id and the spec path, then prompts/spec-check.md byte for byte), 1
step through run_step, no clone after the run, and 1 row for results.md.

The runner is the fake adapter (a reply script); the repo is a small git repo
made at test time, so the test needs no history of this repo.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from conftest import ROOT
from runner import UsageError, reference

SPEC_CHECK = ROOT / "prompts" / "spec-check.md"


def git(folder: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(folder), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "a.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("later\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "later")
    ref = tmp_path / "reference"
    item = ref / "77"
    item.mkdir(parents=True)
    (item / "item.toml").write_text(f'id = "77"\nstep = "spec-check"\nbase = "{base}"\n', encoding="utf-8")
    (item / "spec.md").write_text("# A spec\n\n1. **An AC.** A proof.\n", encoding="utf-8")
    (item / "expected.md").write_text("SPEC-CHECK 77: GO\n", encoding="utf-8")
    script = tmp_path / "reply.json"
    script.write_text(json.dumps({"final_text": "The table.\n\nSPEC-CHECK 77: GO",
                                  "counters": {"input": 900, "output": 40}}), encoding="utf-8")
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(reference.tempfile, "tempdir", str(tmp))
    seen = {}
    real = reference.run_step

    def spy(folder, prompt_file, timeout_s, runner, out_dir, **kw):
        folder = Path(folder)
        seen.update(head=git(folder, "rev-parse", "HEAD"), a=(folder / "a.txt").read_text(encoding="utf-8"),
                    spec=(folder / "spec.md").read_bytes(), prompt=Path(prompt_file).read_bytes(),
                    timeout=timeout_s, runner=runner)
        return real(folder, prompt_file, timeout_s, runner, tmp_path / "run", **kw)

    monkeypatch.setattr(reference, "run_step", spy)
    cfg = {"runners": {"fake-ref": {"adapter": "fake", "model": "fake-model", "script": str(script)}}}
    return {"repo": repo, "base": base, "ref": ref, "item": item, "script": script, "tmp": tmp,
            "seen": seen, "cfg": cfg}


def run(s, **kw):
    item = reference.items(s["ref"])[0]
    return reference.run_item(item, "fake-ref", kw.pop("timeout", 30), cfg=s["cfg"], repo=s["repo"], **kw)


def test_the_clone_is_at_the_base_with_the_spec(setup):
    run(setup)
    seen = setup["seen"]
    assert seen["head"] == setup["base"]
    assert seen["a"].replace("\r\n", "\n") == "base\n"
    assert seen["spec"] == (setup["item"] / "spec.md").read_bytes()


def test_the_prompt_bytes(setup):
    run(setup)
    head = b"The id of the item: 77\nThe path of the spec: spec.md\n\n"
    assert setup["seen"]["prompt"] == head + SPEC_CHECK.read_bytes()


def test_the_row(setup):
    line = run(setup)
    cells = [c.strip() for c in line.strip("|").split("|")]
    assert len(cells) == len(reference.COLUMNS) == 12
    date, item, runner, model, status, last, tokens, seconds, dollars, found, missed, source = cells
    assert (item, runner, model, status, last) == ("77", "fake-ref", "fake-model", "OK", "SPEC-CHECK 77: GO")
    assert len(date) == 10 and date[4] == "-"
    assert tokens == "900/40/-/-"
    assert float(seconds) >= 0
    assert dollars == "unpriced"  # fake-model is not in cost/prices.json
    assert (found, missed) == ("", "")
    assert source == Path(os.path.relpath(setup["tmp"].parent / "run", ROOT)).as_posix()


def test_no_clone_after_ok(setup):
    assert "| OK |" in run(setup)
    assert list(setup["tmp"].iterdir()) == []


def test_no_clone_after_timeout(setup):
    setup["script"].write_text(json.dumps({"final_text": "SPEC-CHECK 77: GO", "delay": 30}), encoding="utf-8")
    assert "| TIMEOUT |" in run(setup, timeout=1)
    assert list(setup["tmp"].iterdir()) == []


def test_no_clone_after_a_usage_error(setup):
    with pytest.raises(UsageError, match="unknown runner"):
        reference.run_item(reference.items(setup["ref"])[0], "nope", 30, cfg=setup["cfg"], repo=setup["repo"])
    assert list(setup["tmp"].iterdir()) == []


def test_a_base_that_is_not_in_the_history_is_a_usage_error(setup):
    (setup["item"] / "item.toml").write_text('id = "77"\nstep = "spec-check"\nbase = "0123456"\n',
                                             encoding="utf-8")
    with pytest.raises(UsageError, match="git checkout failed"):
        run(setup)
    assert list(setup["tmp"].iterdir()) == []


@pytest.mark.parametrize("base", ["--detach", "main", "HEAD~1", ""])
def test_a_base_that_is_not_a_commit_id_is_a_usage_error(setup, base):
    (setup["item"] / "item.toml").write_text(f'id = "77"\nstep = "spec-check"\nbase = "{base}"\n',
                                             encoding="utf-8")
    with pytest.raises(UsageError, match="base"):
        reference.items(setup["ref"])


def test_the_command_prints_1_row_for_each_item(setup, capsys):
    code = reference.main(["--runner", "fake-ref", "--item", "77"], cfg=setup["cfg"], ref_dir=setup["ref"],
                          repo=setup["repo"])
    out, _ = capsys.readouterr()
    assert code == 0
    assert out.count("\n") == 1 and out.startswith("| ") and "| 77 | fake-ref |" in out


def test_an_unknown_item_is_a_usage_error(setup, capsys):
    code = reference.main(["--runner", "fake-ref", "--item", "78"], cfg=setup["cfg"], ref_dir=setup["ref"],
                          repo=setup["repo"])
    assert code == 2 and "no reference item 78" in capsys.readouterr().err


def test_the_default_time_limit_is_1800():
    assert reference.DEFAULT_TIMEOUT == 1800


def test_row_source_is_relative_with_portable_separators(tmp_path, monkeypatch):
    monkeypatch.setattr(reference, "ROOT", tmp_path)
    counters = {"start": "2026-01-01", "runner": "fake", "model": "fake-model",
                "status": "OK", "run_folder": str(tmp_path / "runs" / "sample")}
    source = reference.row("77", counters).strip("|").split("|")[-1].strip()
    assert source == "runs/sample"
    assert not Path(source).is_absolute()
    assert "\\" not in source

