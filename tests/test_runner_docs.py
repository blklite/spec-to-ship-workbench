"""The runner docs state the contract, the status line says the
runner core exists, and the runner uses the standard library only."""

from __future__ import annotations

import ast
import re
import sys

import pytest

from conftest import ROOT

RUNNER = ROOT / "runner"


def test_runner_readme_states_the_contract():
    text = (RUNNER / "README.md").read_text(encoding="utf-8")
    for heading in ("## The adapter contract", "## Add an adapter", "## Runners", "## The result"):
        assert heading in text
    for fn in ("validate(name, table)", "program(table)", "argv(table, exe, folder)", "parse(stdout, table)"):
        assert fn in text
    for status in ("OK", "INVALID", "TIMEOUT", "UNAVAILABLE", "FAILED"):
        assert f"| `{status}` |" in text


def test_runner_readme_links_resolve():
    text = (RUNNER / "README.md").read_text(encoding="utf-8")
    links = re.findall(r"\]\(([^)#\s]+)\)", text)
    assert links
    for target in links:
        assert (RUNNER / target).resolve().exists(), target


def test_status_lines_say_the_runner_core_exists():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    status = next(line for line in readme.splitlines() if line.startswith("**Status:"))
    assert "runner core is here" in status and "host part of the stage machine is here" in status
    assert "With no host" in status and "comes with its own item" not in status
    contract = (ROOT / "process" / "runner-contract.md").read_text(encoding="utf-8")
    assert "The runner core exists" in contract and "Its host setup is in [wb/README.md](../wb/README.md)" in contract
    assert "With no host" in contract and "comes next" not in contract and "Until the stage machine" not in contract
    assert "](../runner/README.md)" in contract


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            yield node.module.split(".")[0]


@pytest.mark.parametrize("path", sorted(RUNNER.rglob("*.py")), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_runner_uses_the_standard_library_only(path):
    for name in _imports(path):
        assert name == "runner" or name == "__future__" or name in sys.stdlib_module_names, name


def test_runner_readme_states_the_fence_rule_and_the_usd_key():
    """The fence rule of the last line, and the usd key."""
    text = (RUNNER / "README.md").read_text(encoding="utf-8")
    rule = text.split("**The allowed last lines**", 1)[1].split("\n\n", 1)[0]
    assert "the last non-empty line inside that block is the last line" in rule
    assert "Text after the closing fence, an empty block, a closing fence with no opener" in rule
    assert "dollars (`usd`)" in text


def test_runner_readme_states_the_guard_and_the_host_parts():
    """The guard key, and the host parts of the stage machine."""
    text = (RUNNER / "README.md").read_text(encoding="utf-8")
    assert "**The guard.** Any runner table may have `guard`" in text
    assert "The runner core makes no scan: `python -m runner.step` alone" in text
    for need in ("detached", "pushes the branch", "interrupted step", "scripts/wrap-step.sh"):
        assert need in text, need


def test_runner_readme_states_the_reply_file():
    """The reply file and the stream of the Claude adapter."""
    text = (RUNNER / "README.md").read_text(encoding="utf-8")
    assert "`reply.txt`" in text and "--output-format stream-json --verbose" in text


def test_the_readme_states_the_credentials_file_and_the_capture_failure():
    """The file rule, the closed door and the capture failure."""
    text = (RUNNER / "README.md").read_text(encoding="utf-8")
    for need in (".credentials.json", "CLAUDE_CONFIG_DIR", "20 or more characters", "no credential to redact",
                 "the credentials file of the CLI can not be read", "the output capture failed"):
        assert need in text, need
