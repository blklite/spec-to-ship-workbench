"""The docs of the stage machine, its config keys, and the
standard library only."""

from __future__ import annotations

import ast
import re
import sys
import tomllib

import pytest

from conftest import ROOT

WB = ROOT / "wb"


def readme() -> str:
    return (WB / "README.md").read_text(encoding="utf-8")


def test_the_wb_readme_states_the_commands_transitions_and_stops():
    text = readme()
    for heading in ("## Commands", "## Transitions", "## Stops and the budget", "## The item folder"):
        assert f"\n{heading}\n" in text, heading
    for command in ("intake <spec> --id <id>", "run-next <id>", "answer <id> --file <file>", "hold <id>",
                    "approve <id> <merge|live|high> [--head <sha40>]", "status [<id>] [--json]",
                    "pages --out <dir> [--answer-url <url>]", "pull-answers --queue <dir>",
                    "record <id> <pr|gate|merge|follow-up> [options]"):
        assert f"python -m wb {command}" in text, command
    for line in ("SPEC-CHECK <id>: GO", "SPEC-CHECK <id>: QUESTIONS - <n> questions", "BUILD: READY <sha>",
                 "BUILD: BLOCKED - <reason>", "REVIEW: CLOSE", "REVIEW: REWORK - <n> blocking", "REWORK: DONE",
                 "REWORK: BLOCKED - <reason>"):
        assert f"`{line}`" in text, line
    for case in range(1, 5):
        assert f"\n| {case} | " in text


def test_the_wb_readme_links_resolve():
    links = re.findall(r"\]\(([^)#\s]+)\)", readme())
    assert links
    for target in links:
        assert (WB / target).resolve().exists(), target


@pytest.mark.parametrize("name,link", [("README.md", "](wb/README.md)"),
                                       ("process/stages.md", "](../wb/README.md)"),
                                       ("process/runner-contract.md", "](../wb/README.md)")])
def test_the_docs_name_the_stage_machine(name, link):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "`python -m wb`" in text and link in text and "stage machine" in text


def test_the_new_config_keys_exist():
    with (ROOT / "config" / "workbench.example.toml").open("rb") as f:
        cfg = tomllib.load(f)
    assert cfg["state"]["home"] and cfg["work"]["home"]
    assert cfg["work"]["base"] == "main"
    assert cfg["tracker"]["kind"] == "local"
    assert cfg["steps"]["max_seconds"] > 0
    with (ROOT / "config" / "personas.example.toml").open("rb") as f:
        assert f.read().count(b"[roles.second-reviewer]") == 1


def _imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            yield node.module.split(".")[0]


@pytest.mark.parametrize("path", sorted(WB.rglob("*.py")), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_wb_uses_the_standard_library_only(path):
    for name in _imports(path):
        assert name in ("wb", "runner", "__future__") or name in sys.stdlib_module_names, name


def test_the_wb_readme_states_the_host_parts():
    """The guard, the push, the detached run and the interrupted step."""
    text = readme()
    for heading in ("## The push", "## The secret guard", "## The host"):
        assert f"\n{heading}\n" in text, heading
    for need in ("python -m wb run-next <id> [--foreground]", "`WB: STARTED <id> - log <path>`",
                 "`WB: RUN REFUSE <id> - a run is going (pid <n>)`", "**The interrupted step.**",
                 "`delivery.push_token_file`", "`guard`", "never the matched text", "Nothing starts at boot."):
        assert need in text, need


def test_the_example_config_has_the_push_token_file_and_the_guard():
    text = (ROOT / "config" / "workbench.example.toml").read_text(encoding="utf-8")
    with (ROOT / "config" / "workbench.example.toml").open("rb") as f:
        assert tomllib.load(f)["delivery"]["push_token_file"].startswith("/")
    assert "# guard = true" in text


def test_the_wb_readme_states_the_mcp_server():
    """The 4 tools, the install and no approve tool."""
    text = readme()
    assert "\n## The MCP server\n" in text
    for need in ("`wb_status`", "`wb_intake`", "`wb_answer`", "`wb_run_next`", "`~/.local/bin/wb-mcp`",
                 "There is no approve tool", "`python -m wb.mcp`"):
        assert need in text, need


@pytest.mark.parametrize("doc", ["wb/README.md", "plugin/README.md"])
def test_the_docs_state_the_path_of_the_mcp_server(doc):
    """The script sets the PATH, the install is a link, and the fix for another host."""
    text = (ROOT / doc).read_text(encoding="utf-8")
    for need in ("puts `~/.local/bin` first on `PATH`", "a symbolic link to", "set `PATH` in the ssh command"):
        assert need in text, need


def test_the_wb_readme_states_the_reply_file():
    """The next step reads reply.txt, else stdout.txt."""
    assert "`reply.txt`" in readme()


def test_the_wb_readme_and_docstrings_state_the_guard_of_ignored_files():
    """The ignored files, the caches, the hidden change and info/exclude."""
    from wb import guard, machine

    sections = {h: readme().split(f"\n## {h}\n", 1)[1].split("\n## ", 1)[0] for h in ("The secret guard", "The push")}
    for pin in ("an ignored file,", "info/exclude", "__pycache__"):
        assert pin in sections["The secret guard"] and pin in guard.__doc__, pin
    assert "info/exclude" in sections["The push"] and "--skip-worktree" in sections["The push"]
    assert "read-tree HEAD" in machine.uncommitted.__doc__ and "--assume-unchanged" in machine.uncommitted.__doc__


def test_the_wb_readme_and_the_pull_docstring_state_the_answer_lock_and_the_name_rule():
    """answer.flock in the answer and pull-answers rows and the item folder, and 200 bytes."""
    from wb import pull

    rows = {c: next(line for line in readme().splitlines() if line.startswith(f"| `{c}` |"))
            for c in ("answer", "pull-answers")}
    folder = readme().split("\n## The item folder\n", 1)[1].split("\n## ", 1)[0]
    for text in (rows["answer"], rows["pull-answers"], folder, pull.__doc__):
        assert "answer.flock" in text, text[:60]
    for text in (rows["pull-answers"], pull.__doc__):
        assert "200 bytes" in text


def test_the_wb_readme_and_the_guard_docstring_state_the_junction_and_the_hunk_header():
    """A junction counts as a link; the hunk-header context is not scanned."""
    from wb import guard

    section = readme().split("\n## The secret guard\n", 1)[1].split("\n## ", 1)[0]
    for pin in ("junction", "hunk header"):
        assert pin in section and pin in guard.__doc__, pin


def test_the_wb_readme_states_the_refusal_with_no_pid_and_the_change_list():
    """The refusal with no pid, the removal of uncommitted.txt and the quoted form."""
    row = next(line for line in readme().splitlines() if line.startswith("| `run-next` |"))
    folder = readme().split("\n## The item folder\n", 1)[1].split("\n## ", 1)[0]
    push_ = readme().split("\n## The push\n", 1)[1].split("\n## ", 1)[0]
    assert "`WB: RUN REFUSE <id> - a run is going` when" in row
    assert "`uncommitted.txt`, the paths of the last wait" in folder and "no such change removes it" in folder
    assert "`uncommitted.txt` in the item folder" in push_ and "finds no change removes the file" in push_
    assert "`json.dumps(path, ensure_ascii=False)`" in push_ and "1 line for each path" in push_


def test_the_example_config_readme_and_docstring_state_notify():
    """The example [notify] table; the command, the kinds and the keys in the docs."""
    from wb import cli

    with (ROOT / "config" / "workbench.example.toml").open("rb") as f:
        assert tomllib.load(f)["notify"] == {"command": [], "page_url": "", "quiet": "22:00-07:00",
                                             "digests": ["08:00"], "fyi_minutes": 30, "remind_hours": 4}
    row = next(line for line in readme().splitlines() if line.startswith("| `notify` |"))
    config = readme().split("\n## Config\n", 1)[1].split("\n## ", 1)[0]
    assert "python -m wb notify" in readme() and "python -m wb notify" in cli.__doc__
    for kind in ("wait", "step_failed", "budget_alert", "ready", "build", "intake", "merge"):
        assert f"`{kind}`" in row and kind in cli.__doc__, kind
    for key in ("case", "text", "key", "role", "status", "reason", "limit", "tier", "sha", "by"):
        assert f"`{key}`" in row, key
    for key in ("command", "page_url", "quiet", "digests", "fyi_minutes", "remind_hours"):
        assert f"`notify.{key}`" in config and f"notify.{key}" in cli.__doc__, key
    for need in ("`events.jsonl`", "`notify-state.json`", "`notify.flock`", '`{"title", "body", "priority", "tags", '
                 '"click"}`', "`WB: NOTIFY sent=<n> held=<n> skipped=<n>`", "`WB: NOTIFY BUSY`",
                 "`WB: NOTIFY FAIL <reason>`", "`Merged but not live: not known`", "with no shell"):
        assert need in row, need


def test_the_deliver_keys_exist():
    """The keys of wb deliver, with no host, and their docs."""
    with (ROOT / "config" / "workbench.example.toml").open("rb") as f:
        table = tomllib.load(f)["deliver"]
    assert (table["retry_seconds"], table["slot_seconds"], table["command_seconds"]) == (300, 14400, 7200)
    assert table["review_state"] == "In Review" and table["gate_source"] == [] and table["ado"] == []
    assert table["default"] == {"host_test": [], "code_review": [], "second_review": []}
    text = readme()
    assert "python -m wb deliver <id> [--foreground]" in text and text.count("\n| `deliver` | ") == 1
    config = text.split("\n## Config\n", 1)[1].split("\n## ", 1)[0]
    for key in ("retry_seconds", "slot_seconds", "command_seconds", "review_state", "gate_source", "ado"):
        assert f"`deliver.{key}`" in config, key
    for key in ("host_test", "code_review", "second_review", "[deliver.default]", "[deliver.<repos name>]"):
        assert f"`{key}`" in config, key
