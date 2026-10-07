"""AC 6: each prompt states its fixed last line, in the form the runner reads."""

from __future__ import annotations

import re

import pytest

from conftest import ROOT

CONTRACTS = {
    "code-review.md": [
        r"CODE-REVIEW: <total> findings \(<critical> critical, <high> high, <medium> medium, <low> low\)",
    ],
    "spec-check.md": [
        r"SPEC-CHECK <id>: GO",
        r"SPEC-CHECK <id>: QUESTIONS - <n> questions",
    ],
}


def last_line_block(text: str) -> list[str]:
    """The lines of the first fenced block after 'The last line of your reply'."""
    marker = text.index("The last line of your reply is exactly")
    m = re.search(r"```[a-z]*\n(.*?)\n```", text[marker:], re.S)
    assert m, "no fenced block after the last-line instruction"
    return m.group(1).splitlines()


@pytest.mark.parametrize("name", sorted(CONTRACTS))
def test_fixed_last_line(name):
    text = (ROOT / "prompts" / name).read_text(encoding="utf-8")
    lines = last_line_block(text)
    assert len(lines) == len(CONTRACTS[name])
    for line, pattern in zip(lines, CONTRACTS[name]):
        assert re.fullmatch(pattern, line), line


def test_contract_examples_parse():
    """A runner can tell the outcomes apart from the last line alone."""
    go = re.compile(r"^SPEC-CHECK (\S+): (GO|QUESTIONS - (\d+) questions)$")
    assert go.match("SPEC-CHECK 12: GO").group(2) == "GO"
    assert go.match("SPEC-CHECK 12: QUESTIONS - 3 questions").group(3) == "3"
    cr = re.compile(r"^CODE-REVIEW: (\d+) findings \((\d+) critical, (\d+) high, (\d+) medium, (\d+) low\)$")
    assert cr.match("CODE-REVIEW: 4 findings (0 critical, 1 high, 2 medium, 1 low)")


def test_code_review_header_is_generic():
    text = (ROOT / "prompts" / "code-review.md").read_text(encoding="utf-8")
    header = text.split("-->", 1)[0]
    assert not re.search(r"Q[0-9]+", header)
    assert "plans/" not in header and ".md (" not in header
