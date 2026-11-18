"""AC 3: process/ holds 1 file for each topic, no ruling or item numbers, no
private word, and docs/rule-map.md maps each rule of the process to a file."""

from __future__ import annotations

import re

import pytest

from conftest import ROOT, denylist_hits, load_denylist

PROCESS = ROOT / "process"
EXPECTED = {
    "stages.md", "decision-rights.md", "triage.md", "budget.md",
    "checks-by-tier.md", "report.md", "runner-contract.md", "sandbox.md",
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


def test_rule_map_covers_every_topic():
    rows = _rule_rows()
    assert [r[0] for r in rows] == list(range(1, len(rows) + 1))
    assert {r[1] for r in rows} == {"Goal", "Stages", "Decision rights", "Triage", "Budget", "Checks by tier",
                                    "Runner", "After delivery", "Report", "Review rule"}
    assert len(rows) >= 60


def test_rule_map_targets_exist():
    for num, _src, _rule, where in _rule_rows():
        if where.startswith("Left out:"):
            continue
        paths = re.findall(r"[a-z-]+/[A-Za-z0-9_./-]*", where)
        assert paths, f"row {num} names no file and no reason"
        for p in paths:
            assert (ROOT / p.rstrip(".")).exists(), f"row {num}: {p} does not exist"


REPORT_ITEMS = ("red first", "the test counts on the base and at the head", "the diff stat")


def test_stage_3_names_the_report_items():
    text = (PROCESS / "stages.md").read_text(encoding="utf-8")
    stage_3 = text[text.index("### Stage 3"):text.index("### Stage 5")]
    for item in REPORT_ITEMS:
        assert item in stage_3, item
    style = re.search(r"The builder's report states in 1 line [^.]*\.", stage_3)
    assert style and style.group(0).endswith('or "no change".'), style


def test_stage_6_names_the_message_check():
    text = (PROCESS / "stages.md").read_text(encoding="utf-8")
    stage_6 = text[text.index("### Stage 6"):text.index("## When the workbench stops")]
    assert "secret-scan.sh --message" in stage_6


SANDBOX_RULES = ["No privilege gain", "Files", "Network", "Environment", "Temporary places"]


def test_sandbox_pattern_pins_the_five_rules():
    text = (PROCESS / "sandbox.md").read_text(encoding="utf-8")
    assert re.findall(r"^\d\. \*\*([^*]+?)\.\*\*", text, re.M) == SANDBOX_RULES


def test_sandbox_pattern_names_the_probe():
    text = (PROCESS / "sandbox.md").read_text(encoding="utf-8")
    assert "scripts/sandbox-probe.sh" in text


def test_runner_contract_points_to_the_sandbox_pattern():
    text = (PROCESS / "runner-contract.md").read_text(encoding="utf-8")
    assert "(sandbox.md)" in text


def test_rule_map_row_49_points_to_the_sandbox_pattern():
    row = next(r for r in _rule_rows() if r[0] == 49)
    assert "process/sandbox.md" in row[3]


def _different_severity_paragraph() -> str:
    """The paragraph of triage.md that starts with "A different severity", to the next blank line."""
    text = (PROCESS / "triage.md").read_text(encoding="utf-8")
    start = text.index("**A different severity.**")
    end = re.search(r"\n[ \t]*\n", text[start:])
    return text[start:start + end.start()] if end else text[start:]


def test_triage_keeps_both_severities_of_a_finding_rated_again():
    """The session's own rating does not replace the check's rating."""
    text = (PROCESS / "triage.md").read_text(encoding="utf-8")
    paragraph = _different_severity_paragraph()
    assert "**A different severity.**" in text
    assert "keeps both severities and the reason" in paragraph
    assert text.index("Severity follows the reviewer") < text.index("keeps both severities") < text.index("## The follow-up list")
    # the record is shown, and the session that controls the item is the one that rates again.
    assert "The follow-up list and the report show that record." in paragraph
    assert "the session that controls the item" in paragraph


def test_triage_the_higher_severity_sets_the_action():
    """Of 2 severities the higher sets the action; the lower needs the owner."""
    paragraph = _different_severity_paragraph()
    higher = paragraph.index("The higher of the 2 severities sets the action of the table.")
    lower = paragraph.index("To act on the lower severity, the session asks the owner.")
    record = paragraph.index("The follow-up list and the report show that record.")
    assert paragraph.index("keeps both severities") < record < higher < lower


def test_the_lower_of_2_severities_is_on_the_must_ask_list():
    """An agent that reads only the must-ask list sees the case of the lower severity."""
    text = (PROCESS / "decision-rights.md").read_text(encoding="utf-8")
    alone = text[text.index("## An agent decides alone"):text.index("## An agent must ask the owner")]
    ask = text[text.index("## An agent must ask the owner"):text.index("## The default for a doubt")]
    bullets = [b for b in ask.splitlines() if b.startswith("- **The lower of 2 severities.**")]
    assert len(bullets) == 1, "the must-ask list needs 1 bullet named 'The lower of 2 severities'"
    assert "the lower of 2 severities" in bullets[0] and "](triage.md)" in bullets[0]
    assert "lower of 2 severities" not in alone.lower()
