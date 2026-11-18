"""AC 6: each prompt states its fixed last line, in the form the runner reads."""

from __future__ import annotations

import hashlib
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
    "build.md": [
        r"BUILD: READY <sha>",
        r"BUILD: BLOCKED - <reason>",
    ],
    "review.md": [
        r"REVIEW: CLOSE",
        r"REVIEW: REWORK - <n> blocking",
    ],
    "rework.md": [
        r"REWORK: DONE",
        r"REWORK: BLOCKED - <reason>",
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
    build = re.compile(r"^BUILD: (READY ([0-9a-f]{7,40})|BLOCKED - (.+))$")
    assert build.match("BUILD: READY " + "ab12" * 10).group(2)
    assert build.match("BUILD: BLOCKED - the spec names a file that does not exist").group(3)
    cr = re.compile(r"^CODE-REVIEW: (\d+) findings \((\d+) critical, (\d+) high, (\d+) medium, (\d+) low\)$")
    assert cr.match("CODE-REVIEW: 4 findings (0 critical, 1 high, 2 medium, 1 low)")


def test_code_review_header_is_generic():
    text = (ROOT / "prompts" / "code-review.md").read_text(encoding="utf-8")
    header = text.split("-->", 1)[0]
    assert not re.search(r"Q[0-9]+", header)
    assert "plans/" not in header and ".md (" not in header


BUILD = ROOT / "prompts" / "build.md"
LADDER_START = "## Before you write"
PIN = "9b58c1f"


def test_the_build_style_rules_come_before_the_ladder():
    """The spec and the decision rights win: the guard rules precede the quoted ladder."""
    text = BUILD.read_text(encoding="utf-8")
    style = text.index("## Build style")
    ladder = text.index(LADDER_START)
    guards = text[style:ladder]
    for rule in ("The spec and `process/decision-rights.md` win over the ladder.",
                 "Never remove or weaken a test that is not on the spec's retire list"
                 ' ("Tests that the item can retire").',
                 "Never cut what the spec states.",
                 "Do not change the scope.",
                 '"Deletion beats addition" applies only to the code that this item changes.',
                 "Do not push back on the spec."):
        assert rule in guards, rule
    assert text.index("Never cut:", ladder) < text.index("## Report")


STAGES = ROOT / "process" / "stages.md"


def stage_3_style() -> str:
    """The text from "### Stage 3: the build style" to the next "### " heading."""
    stages = STAGES.read_text(encoding="utf-8")
    return stages.split("### Stage 3: the build style", 1)[1].split("\n### ", 1)[0]


def test_the_test_guard_names_the_spec_list():
    """A test on the spec's retire list may go: no file holds the unqualified guard."""
    build, stages = BUILD.read_text(encoding="utf-8"), STAGES.read_text(encoding="utf-8")
    for text in (build, stages):
        assert "Never remove or weaken a test." not in text
    assert "never removes or weakens a test that is not on the spec's retire list" in stage_3_style()


def test_the_guard_is_anchored_to_the_retire_list():
    """'a test that the spec lists' must not read as 'any test the spec names'."""
    for folder in ("prompts", "process", "roles"):
        for p in sorted((ROOT / folder).rglob("*")):
            if p.is_file():
                assert "that the spec does not list" not in p.read_text(encoding="utf-8"), p
    assert "\n\nNever remove or weaken a test that is not on the spec's retire list. Decide" in the_job()
    text = BUILD.read_text(encoding="utf-8")
    guards = text[text.index("## Build style"):text.index(LADDER_START)]
    assert ("\n- Never remove or weaken a test that is not on the spec's retire list"
            ' ("Tests that the item can retire").\n') in guards
    assert "it never removes or weakens a test that is not on the spec's retire list," in stage_3_style()


def the_job() -> str:
    return BUILD.read_text(encoding="utf-8").split("## The job", 1)[1].split("\n## ", 1)[0]


def test_the_builder_opens_the_pull_request():
    """The build commits and stops; the script pushes and opens the pull request."""
    assert ("5. Commit on the branch and stop. Do not push or open the pull request."
            " Do not merge, deploy or change the state of the work item.") in the_job()


def test_the_self_check_names_the_review_level():
    step_4 = the_job().split("\n4. ", 1)[1].split("\n5. ", 1)[0]
    assert "`config:review.code_review_level`" in step_4


def test_the_build_style_wins_over_the_persona():
    """The line is in the guard list, so a brief that copies "Build style" has it."""
    text = BUILD.read_text(encoding="utf-8")
    guards = text[text.index("## Build style"):text.index(LADDER_START)]
    assert "\n- The build style wins over the builder's persona.\n" in guards


def test_the_quote_ends_at_the_notice_and_stage_3_heads_the_style():
    """The quoted H2 headings stay; the lead-in and a stage 3 heading mark the span."""
    lead_in = ("\nThe ladder (quoted, not changed, up to the Ponytail notice line;"
               " a copy of this section keeps that line):\n\n")
    assert lead_in + LADDER_START in BUILD.read_text(encoding="utf-8")
    stages = STAGES.read_text(encoding="utf-8")
    assert "\n### Stage 3: the build style\n\nThe build style of [prompts/build.md]" in stages
    assert stages.index("### Stage 3: the build style") < stages.index("### Stage 3: the builder's self-check")


def test_the_report_asks_for_the_ladder_line():
    report = BUILD.read_text(encoding="utf-8").split("## Report", 1)[1]
    assert "what the ladder made you leave out or reuse" in report
    assert '"no change"' in report


def test_the_quote_names_its_source_and_the_notice_has_the_licence():
    text = BUILD.read_text(encoding="utf-8")
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert f"commit {PIN}, MIT licence" in text
    assert "THIRD_PARTY_NOTICES.md" in text
    assert PIN in notices and "prompts/build.md" in notices
    assert "Permission is hereby granted, free of charge" in notices
    assert "The above copyright notice and this permission notice shall be included" in notices


def test_the_notice_places_the_pin_after_the_release():
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert ("(4 commits after release v5.1.0; `skills/ponytail/SKILL.md` and `LICENSE`"
            " are the same as at the tag)") in notices
    assert "(release v5.1.0)" not in notices


# sha256 of the upstream text at the pin, LF line ends (checked at intake)
QUOTE_SHA256 = "e12264f254551d1a32e6411183ea77d408a0f8c72938213b548a267dff3800fc"
LICENCE_SHA256 = "5213b04ddaa6cd0d7225a4c0e76dd8edf57db5960360bf27c6e03865d8cece5c"


def _lf(path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def test_the_quote_and_the_licence_are_not_changed():
    text = _lf(BUILD)
    start = text.index(LADDER_START)
    end = text.index("\n", text.index("Never cut:", start))
    assert hashlib.sha256(text[start:end].encode("utf-8")).hexdigest() == QUOTE_SHA256
    licence = re.search(r"```text\n(.*?)\n```", _lf(ROOT / "THIRD_PARTY_NOTICES.md"), re.S)
    assert licence, "no licence block in THIRD_PARTY_NOTICES.md"
    assert hashlib.sha256(licence.group(1).encode("utf-8")).hexdigest() == LICENCE_SHA256


@pytest.mark.parametrize("name", sorted(p.name for p in (ROOT / "prompts").glob("*.md") if p.name != "build.md"))
def test_only_the_builder_prompt_has_the_ladder(name):
    """The spec checker, the reviewer and the other roles get no build-style text."""
    text = (ROOT / "prompts" / name).read_text(encoding="utf-8")
    assert "Ponytail" not in text and LADDER_START not in text


PLAIN = "Write the last line as plain text, not inside a code block."


@pytest.mark.parametrize("name", sorted(p.name for p in (ROOT / "prompts").glob("*.md")))
def test_each_prompt_asks_for_a_plain_last_line(name):
    """The sentence comes before the last-line sentence."""
    text = (ROOT / "prompts" / name).read_text(encoding="utf-8")
    assert 0 <= text.find(PLAIN) < text.index("The last line of your reply is exactly")


# --- the review and rework prompts of the stage machine ---

REVIEW = ROOT / "prompts" / "review.md"
REWORK = ROOT / "prompts" / "rework.md"


def test_the_review_prompt_follows_the_reviewer_role():
    text = REVIEW.read_text(encoding="utf-8")
    for need in ("file:line", "mutation probe", "test-coverage verdict", "Testability, Reliability,"
                 " Scalability and Annoyance Factor", "`process/triage.md`", '"repair now"'):
        assert need in text, need
    for severity in ("**Critical:**", "**High:**", "**Medium:**", "**Low:**"):
        assert severity in text, severity
    assert "You do not edit, commit or push." in text


def test_the_review_last_lines_parse():
    from runner import lastline

    allowed = lastline.read_allowed(REVIEW)
    assert allowed == ["REVIEW: CLOSE", "REVIEW: REWORK - <n> blocking"]
    assert lastline.matches("REVIEW: REWORK - 2 blocking", allowed)
    assert not lastline.matches("REVIEW: REWORK", allowed)


def test_the_rework_prompt_follows_the_rework_role():
    text = REWORK.read_text(encoding="utf-8")
    assert "- Make 1 repair for each finding, with a test that fails without the repair" in text
    assert "- Never remove or weaken a test to make the suite green." in text
    assert "- Do not commit and do not push. The caller commits after `REWORK: DONE`." in text
    assert "fresh-eyes questions first" in text and "learning walkthrough" in text


def test_the_rework_last_lines_parse():
    from runner import lastline

    allowed = lastline.read_allowed(REWORK)
    assert allowed == ["REWORK: DONE", "REWORK: BLOCKED - <reason>"]
    assert not lastline.matches("REWORK: DONE " + "ab12" * 10, allowed)


# --- the rework prompt, adapted for a runner in a sandbox ---

def test_the_rework_prompt_names_its_inputs():
    inputs = REWORK.read_text(encoding="utf-8").split("## Inputs", 1)[1].split("\n## ", 1)[0]
    for need in ("the findings to repair", "the spec", "the work folder"):
        assert need in inputs, need


def test_the_rework_step_runs_with_no_one_to_answer():
    text = REWORK.read_text(encoding="utf-8")
    assert "No one answers during the step" in text
    assert "Edit files in the work folder only." in text
    assert "the caller runs the whole suite again before the commit" in text


# --- the recurrence check and the claims checked ---


def _review_section(start: str, end: str) -> str:
    text = REVIEW.read_text(encoding="utf-8")
    return text[text.index(start):text.index(end)]


def _job_step(number: int) -> str:
    job = _review_section("## The job", "## Blocking findings")
    m = re.search(rf"^{number}\. (.*?)(?=^\d+\. |\Z)", job, re.S | re.M)
    assert m, f"no step {number} in the job"
    return m.group(1)


def test_the_review_looks_for_the_same_defect_after_a_rework_round():
    """AC 1: step 2 widens a repair check to the same defect elsewhere; step 6 reverts each repair."""
    step2 = _job_step(2)
    assert "After a rework round, check each repaired finding at its cite" in step2
    assert "the same defect" in step2 and "a new finding with its own cite" in step2
    step6 = _job_step(6)
    assert "After a rework round, revert each repair" in step6 and "confirm that its test fails" in step6


def test_the_review_output_names_the_claims_checked():
    """AC 2: the claims item comes before the mutation table and makes a review without one incomplete."""
    out = _review_section("## Output", "Write the last line as plain text")
    assert "The claims checked" in out and "A review that names no claim is incomplete" in out
    assert out.index("The claims checked") < out.index("The mutation table")
    items = re.findall(r"^(\d+)\. ", out, re.M)
    assert items == [str(n) for n in range(1, len(items) + 1)] and len(items) == 5
    # the claims item is item 2, not only before the mutation table.
    assert re.search(r"^2\. The claims checked", out, re.M)


def test_the_same_defect_has_a_definition():
    """Step 2 says what the same defect is."""
    step2 = _job_step(2)
    assert "the same defect (the same cause or the same code pattern)" in step2
    # the places to search are in step 2 as well.
    assert "in the rest of the diff" in step2


@pytest.mark.parametrize("name,last", [("build.md", "BUILD: BLOCKED - <reason>"),
                                       ("rework.md", "REWORK: BLOCKED - <reason>")])
def test_a_blocked_choice_is_written_as_lettered_options(name, last):
    """The answer buttons of the state page read these lines; the last lines do not change."""
    text = (ROOT / "prompts" / name).read_text(encoding="utf-8")
    rule = text.index("When you stop with `BLOCKED` because the owner must choose, write the choice as lettered"
                      " options right before the last line of the reply, 1 line for each: `A) ...`, `B) ...`,")
    assert rule < text.index("The last line of your reply is exactly")
    assert last in last_line_block(text) and len(last_line_block(text)) == 2


# --- the lines that wb deliver reads from the replies ---


@pytest.mark.parametrize("name,line", [
    ("build.md", "SELF-CHECK: <k> of <n> mutants killed, <s> survived, by builder"),
    ("rework.md", "SELF-CHECK: <k> of <n> mutants killed, <s> survived, by rework"),
    ("review.md", "MUTANTS: <k>/<n> killed")])
def test_the_prompts_ask_for_the_lines_of_wb_deliver(name, line):
    """The line is in a fenced block before the last-line rule; the last lines do not change."""
    text = (ROOT / "prompts" / name).read_text(encoding="utf-8")
    assert 0 <= text.find(f"```\n{line}\n```") < text.index("The last line of your reply is exactly")
    assert last_line_block(text) == CONTRACTS[name]
