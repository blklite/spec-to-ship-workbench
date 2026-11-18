"""The fixed last line is read from the prompt file and matched in full."""

from __future__ import annotations

import pytest

from conftest import ROOT
from runner import UsageError
from runner.lastline import allowed_lines, compile_line, last_line, matches, read_allowed

CODE_REVIEW = ROOT / "prompts" / "code-review.md"
SPEC_CHECK = ROOT / "prompts" / "spec-check.md"


def ok(reply: str, prompt) -> bool:
    return matches(last_line(reply), read_allowed(prompt))


def test_both_prompts_give_their_lines():
    assert read_allowed(CODE_REVIEW) == [
        "CODE-REVIEW: <total> findings (<critical> critical, <high> high, <medium> medium, <low> low)"
    ]
    assert read_allowed(SPEC_CHECK) == [
        "SPEC-CHECK <id>: GO",
        "SPEC-CHECK <id>: QUESTIONS - <n> questions",
    ]


def test_crlf_prompt_reads_the_same(tmp_path):
    p = tmp_path / "p.md"
    p.write_bytes(SPEC_CHECK.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert read_allowed(p) == read_allowed(SPEC_CHECK)


@pytest.mark.parametrize("reply", [
    "Findings below.\n\nCODE-REVIEW: 3 findings (0 critical, 1 high, 1 medium, 1 low)",
    "CODE-REVIEW: 0 findings (0 critical, 0 high, 0 medium, 0 low)\n\n  \n",
    "text\r\nCODE-REVIEW: 1 findings (1 critical, 0 high, 0 medium, 0 low)  \r\n",
])
def test_code_review_match(reply):
    assert ok(reply, CODE_REVIEW)


@pytest.mark.parametrize("reply", [
    "SPEC-CHECK 180: GO",
    "notes\nSPEC-CHECK 180: QUESTIONS - 2 questions",
])
def test_spec_check_match(reply):
    assert ok(reply, SPEC_CHECK)


@pytest.mark.parametrize("reply", [
    # near misses
    "CODE-REVIEW: 3 findings (0 critical, 1 high, 1 medium)",
    "CODE-REVIEW: 3 findings [0 critical, 1 high, 1 medium, 1 low]",
    "CODE-REVIEW 3 findings (0 critical, 1 high, 1 medium, 1 low)",
    "code-review: 3 findings (0 critical, 1 high, 1 medium, 1 low)",
    "CODE-REVIEW:  findings (0 critical, 1 high, 1 medium, 1 low)",
    "`CODE-REVIEW: 3 findings (0 critical, 1 high, 1 medium, 1 low)`",
    "CODE-REVIEW: 3 findings (0 critical, 1 high, 1 medium, 1 low).",
    "Result: CODE-REVIEW: 3 findings (0 critical, 1 high, 1 medium, 1 low)",
    # prose after the line
    "CODE-REVIEW: 3 findings (0 critical, 1 high, 1 medium, 1 low)\nThat is all.",
    # nothing
    "",
    "\n \n",
])
def test_code_review_no_match(reply):
    assert not ok(reply, CODE_REVIEW)


@pytest.mark.parametrize("reply", [
    "SPEC-CHECK 180: GO!",
    "SPEC-CHECK 180: go",
    "SPEC-CHECK: GO",
    "SPEC-CHECK 180: QUESTIONS - 2 question",
    "SPEC-CHECK 180: GO\nThanks.",
    "SPEC-CHECK 180: GO SPEC-CHECK 181: GO extra",
])
def test_spec_check_no_match(reply):
    assert not ok(reply, SPEC_CHECK)


def test_placeholder_is_1_or_more_characters():
    pat = compile_line("A <x> B")
    assert pat.fullmatch("A 1 B") and pat.fullmatch("A many words B")
    assert not pat.fullmatch("A  B")


def test_other_text_is_literal():
    pat = compile_line("X (a) [b] *.+? $1")
    assert pat.fullmatch("X (a) [b] *.+? $1")
    assert not pat.fullmatch("X a [b] *.+? $1")
    assert not pat.fullmatch("X (a) b *.+? $1")


def test_last_line_none_for_no_text():
    assert last_line(None) is None
    assert last_line("  \n\t\n") is None
    assert not matches(None, ["X"])


@pytest.mark.parametrize("text", [
    "# A prompt\n\nNo last-line sentence here.\n",
    "The last line of your reply is exactly\n\nbut no fence\n",
    "The last line of your reply is exactly\n\n```\n\n```\n",
    "```\nX: <y>\n```\nThe last line of your reply is exactly what you like.\n",
])
def test_prompt_without_block_is_a_usage_error(text):
    with pytest.raises(UsageError):
        allowed_lines(text)


@pytest.mark.parametrize("fence", ["```", "```text", "```text-plain", "```Text"])
def test_any_info_string_opens_the_block(fence):
    text = (f"The last line of your reply is exactly\n\n{fence}\nDONE <n>\n```\n\n"
            "Prose after.\n\n```\nLATER\n```\n")
    assert allowed_lines(text) == ["DONE <n>"]


def test_an_empty_block_is_a_usage_error_not_the_prose_after_it():
    text = "The last line of your reply is exactly\n\n```\n```\n\nProse.\n\n```\nLATER\n```\n"
    with pytest.raises(UsageError, match="empty"):
        allowed_lines(text)


def test_first_block_after_the_sentence_counts():
    text = ("```\nNOT: this\n```\nThe last line of your reply is exactly\n\n"
            "```text\nDONE <n>\n```\n\n```\nLATER: no\n```\n")
    assert allowed_lines(text) == ["DONE <n>"]


def test_missing_prompt_file_is_a_usage_error(tmp_path):
    with pytest.raises(UsageError):
        read_allowed(tmp_path / "none.md")


# --- a fenced last line --------------------------------

ECHO = ROOT / "tests" / "fixtures" / "runner" / "echo.md"
F = "`" * 3  # a fence, built at run time so that no line of this file opens one


@pytest.mark.parametrize("reply,prompt,line", [
    # the echo reply of the live runs
    (f"I received this prompt.\n\n{F}\nECHO: OK\n{F}", ECHO, "ECHO: OK"),
    (f"Received.\n\n{F}text\nECHO: OK\n{F}\n", ECHO, "ECHO: OK"),
    (f"Received.\r\n\r\n{F}\r\nECHO: OK  \r\n{F}  \r\n\r\n", ECHO, "ECHO: OK"),
    (f"Findings.\n\n{F}\nCODE-REVIEW: 1 findings (0 critical, 1 high, 0 medium, 0 low)\n{F}",
     CODE_REVIEW, "CODE-REVIEW: 1 findings (0 critical, 1 high, 0 medium, 0 low)"),
    # more than 1 line in the fence: the last non-empty one counts
    (f"Notes.\n{F}\nSPEC-CHECK 180: QUESTIONS - 9 questions\n\nSPEC-CHECK 180: GO\n\n{F}",
     SPEC_CHECK, "SPEC-CHECK 180: GO"),
    # an earlier closed block does not change the pair of the last one
    (f"{F}py\nx = 1\n{F}\nThen:\n{F}\nECHO: OK\n{F}", ECHO, "ECHO: OK"),
])
def test_fenced_last_line_match(reply, prompt, line):
    assert last_line(reply) == line
    assert ok(reply, prompt)


@pytest.mark.parametrize("reply", [
    # text after the fence
    f"{F}\nECHO: OK\n{F}\nThat is all.",
    # an empty fence
    f"Received.\n{F}\n{F}",
    f"Received.\n{F}\n\n  \n{F}",
    # a closing fence with no opener
    f"Received.\nECHO: OK\n{F}",
    f"{F}\nnotes\n{F}\nECHO: OK\n{F}",
    # a closer with an info string is not a closer
    f"{F}\nECHO: OK\n{F}text",
    # inline backticks
    "`ECHO: OK`",
    f"{F}ECHO: OK{F}",
    # a near miss inside the fence, and the allowed line not last in it
    f"Received.\n{F}\nECHO: OK.\n{F}",
    f"Received.\n{F}\nECHO: OK\nmore\n{F}",
    # an indented fence (out of scope: the rule is unchanged)
    "Received.\n  " + F + "\n  ECHO: OK\n  " + F,
    "Received.\n  " + F + "\nECHO: OK\n  " + F,
])
def test_fenced_last_line_no_match(reply):
    assert not ok(reply, ECHO)


def test_an_empty_fence_keeps_the_closing_fence_as_the_last_line():
    assert last_line(f"Received.\n{F}text\n{F}") == F
