"""The reference items, their results table and their docs. This repo ships no
item (reference/README.md says how to add your own); each format test loops over
the item folders that exist, so it guards the items a user adds."""

from __future__ import annotations

import subprocess
import tomllib

import pytest

from conftest import ROOT
from runner import reference

REF = ROOT / "reference"


def _folders() -> list:
    return sorted(p for p in REF.iterdir() if p.is_dir())


def _max_bytes() -> int:
    with (ROOT / "config" / "workbench.example.toml").open("rb") as f:
        return tomllib.load(f)["specs"]["max_kb"] * 1024


def _item(folder) -> dict:
    with (folder / "item.toml").open("rb") as f:
        return tomllib.load(f)


def test_the_items_are_the_folders():
    assert {i["id"] for i in reference.items(REF)} == {f.name for f in _folders()}


def test_each_item_has_the_3_files():
    for folder in _folders():
        for name in ("item.toml", "spec.md", "expected.md"):
            assert (folder / name).is_file(), f"{folder.name}: {name}"
        item = _item(folder)
        assert (item["id"], item["step"]) == (folder.name, "spec-check"), folder.name
        assert isinstance(item["reconstructed"], bool), folder.name


def test_each_spec_is_at_most_the_size_limit():
    for folder in _folders():
        assert 0 < len((folder / "spec.md").read_bytes()) <= _max_bytes(), folder.name


def test_each_base_is_in_the_history():
    folders = _folders()
    if not folders:
        return
    git = ["git", "-C", str(ROOT)]
    shallow = subprocess.run([*git, "rev-parse", "--is-shallow-repository"], capture_output=True, text=True)
    if shallow.stdout.strip() == "true":
        pytest.skip("a shallow clone has no full history")
    for folder in folders:
        base = _item(folder)["base"]
        done = subprocess.run([*git, "merge-base", "--is-ancestor", base, "HEAD"], capture_output=True, text=True)
        assert done.returncode == 0, f"{folder.name}: {base} is not in the history of HEAD"


def test_each_expected_holds_the_verdict_and_the_findings():
    for folder in _folders():
        text = (folder / "expected.md").read_text(encoding="utf-8")
        assert "**Verdict:**" in text, folder.name
        assert "- **F1 " in text, folder.name


def test_the_results_have_the_columns_and_a_claude_row_with_a_source_for_each_item():
    lines = (REF / "results.md").read_text(encoding="utf-8").splitlines()
    header = next(line for line in lines if line.startswith("| date |"))
    assert [c.strip() for c in header.strip("|").split("|")] == list(reference.COLUMNS)
    rows = [[c.strip() for c in line.strip("|").split("|")] for line in lines
            if line.startswith("| 20")]
    for folder in _folders():
        claude = [r for r in rows if r[1] == folder.name and r[2] == "claude"]
        assert claude and claude[0][-1], folder.name


def test_the_docs_name_the_files_the_command_and_the_rule():
    text = (REF / "README.md").read_text(encoding="utf-8")
    for part in ("`item.toml`", "`spec.md`", "`expected.md`", "`results.md`",
                 "python -m runner.reference --runner R [--item ID] [--timeout S]", "## The rule",
                 "## Add your own item"):
        assert part in text, part
    contract = (ROOT / "process" / "runner-contract.md").read_text(encoding="utf-8")
    assert "A new runner runs them before a role maps to it" in contract
    assert "](../reference/README.md)" in contract
    runner_readme = (ROOT / "runner" / "README.md").read_text(encoding="utf-8")
    assert "| `opencode` |" in runner_readme and "`env(table, folder)`" in runner_readme
    assert "opencode, not aider" in runner_readme


def test_no_role_maps_to_the_local_runner():
    from runner import config

    personas = config.load_personas(ROOT / "config" / "personas.example.toml")
    assert "opencode-local" not in {config.role_runner(personas, r) for r in personas["roles"]}
