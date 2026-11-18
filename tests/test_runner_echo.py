"""The echo prompt of the real run of each adapter.

The real runs (a claude runner on Haiku and the codex runner, on Windows) are a
step of the main session, not of this suite: they call a paid model. This file
checks the prompt they use.
"""

from __future__ import annotations

from conftest import ROOT
from runner.lastline import read_allowed
from runner.step import run_step

ECHO = ROOT / "tests" / "fixtures" / "runner" / "echo.md"


def test_echo_prompt_asks_for_echo_ok():
    assert read_allowed(ECHO) == ["ECHO: OK"]


def test_echo_prompt_runs_on_the_fake_runner(tmp_path):
    (tmp_path / "work").mkdir()
    c = run_step(tmp_path / "work", ECHO, 30, "fake", tmp_path / "run",
                 cfg=ROOT / "config" / "workbench.example.toml")
    assert (c["status"], c["last_line"]) == ("OK", "ECHO: OK")


def test_echo_prompt_asks_for_a_plain_last_line():
    """The plain last line comes before the last-line sentence."""
    text = ECHO.read_text(encoding="utf-8")
    assert 0 <= text.find("Write the last line as plain text, not inside a code block.") < text.find(
        "The last line of your reply is exactly")
