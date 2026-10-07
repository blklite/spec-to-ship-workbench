"""AC 3: process/ holds 1 file for each topic, no ruling or item numbers, no
private word, and docs/rule-map.md maps each source rule to a file or a reason."""

from __future__ import annotations

import re

import pytest

from conftest import ROOT, denylist_hits, load_denylist

PROCESS = ROOT / "process"
EXPECTED = {
    "stages.md", "decision-rights.md", "triage.md", "budget.md",
    "checks-by-tier.md", "report.md", "runner-contract.md",
}


def test_one_file_for_each_topic():
    assert {p.name for p in PROCESS.glob("*.md")} == EXPECTED


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_no_ruling_or_item_numbers(name):
    text = (PROCESS / name).read_text(encoding="utf-8")
    assert not re.findall(r"Q[0-9]+", text), "a Q number (a ruling id) is in " + name
    assert not re.findall(r"#[0-9]+", text), "a # number (an item id) is in " + name


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_no_private_word(name):
    pairs = load_denylist()
    if pairs is None:
        pytest.skip("WORKBENCH_DENYLIST is not set")
    hits = denylist_hits((PROCESS / name).read_text(encoding="utf-8"), pairs)
    assert not hits, f"private words of classes {sorted(set(hits))} in {name}"


def _rule_rows():
    rows = []
    for line in (ROOT / "docs" / "rule-map.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\| (\d+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$", line)
        if m:
            rows.append((int(m[1]), m[2].strip(), m[3].strip(), m[4].strip()))
    return rows


def test_rule_map_covers_every_source_block():
    rows = _rule_rows()
    assert [r[0] for r in rows] == list(range(1, len(rows) + 1))
    sources = {r[1].split()[1][0] for r in rows if r[1].startswith("process")}
    assert sources == set("BCDEFGHIJ")
    assert any(r[1].startswith("rubrics N") for r in rows)
    assert len(rows) >= 60



def test_rule_map_targets_exist():
    for num, _src, _rule, where in _rule_rows():
        if where.startswith("Left out:"):
            continue
        paths = re.findall(r"[a-z-]+/[A-Za-z0-9_./-]*", where)
        assert paths, f"row {num} names no file and no reason"
        for p in paths:
            assert (ROOT / p.rstrip(".")).exists(), f"row {num}: {p} does not exist"
