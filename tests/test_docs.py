"""AC 8: the docs exist, say what the spec asks, and hold nothing private."""

from __future__ import annotations

import re
import subprocess

import pytest

from conftest import ROOT, denylist_hits, load_denylist

DOCS = ["README.md", "AGENTS.md", "CLAUDE.md", "LICENSE", "docs/ado-setup.md", "docs/specs.md"]


@pytest.mark.parametrize("name", DOCS)
def test_doc_exists(name):
    assert (ROOT / name).is_file()


def test_readme():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "v0.1a" in text
    stages = re.findall(r"^\d\. \*\*([A-Za-z ]+)", text, re.M)
    assert stages == ["Intake", "Spec check", "Build", "Checks", "Fix loop", "Delivery"]
    # no repo URL: the README must not point at the hosting of this repo
    assert not re.search(r"https?://(www\.)?(github|gitlab|dev\.azure)\.", text)


def test_claude_md_points_to_agents_md():
    assert "AGENTS.md" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def test_ado_checklist():
    text = (ROOT / "docs" / "ado-setup.md").read_text(encoding="utf-8")
    for state in ("Draft", "In Review", "Approved", "Executing", "Waiting for Owner"):
        assert f"| {state} |" in text
    assert "Custom.CostUSD" in text and "Custom.MainSessionCostUSD" in text


def test_spec_format():
    text = (ROOT / "docs" / "specs.md").read_text(encoding="utf-8")
    assert "8 KB" in text
    for section in ("## Story", "## Acceptance criteria", "## Non-goals"):
        assert section in text


def test_licence_is_mit():
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")


def test_no_private_word_in_any_text_file():
    pairs = load_denylist()
    if pairs is None:
        pytest.skip("WORKBENCH_DENYLIST is not set")
    # the files of the repo: tracked, and untracked but not ignored (the scan's set)
    out = subprocess.run(
        ["git", "ls-files", "-z", "-c", "-o", "--exclude-standard"],
        cwd=ROOT, capture_output=True, check=True,
    ).stdout.decode("utf-8")
    for name in sorted(n for n in out.split("\0") if n):
        p = ROOT / name
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        hits = denylist_hits(text, pairs)
        assert not hits, f"private words of classes {sorted(set(hits))} in {p.relative_to(ROOT)}"
