"""The Claude Code plugin over the wb MCP server. The tests read the
plugin files; none starts ssh or reaches a host."""

from __future__ import annotations

import json
import re

import pytest

from conftest import ROOT

PLUGIN = ROOT / "plugin"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
COMMANDS = ("intake", "status", "answer", "run-next")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def plugin_files() -> set[str]:
    return {p.relative_to(PLUGIN).as_posix() for p in PLUGIN.rglob("*") if p.is_file()}


# AC 1: the plugin files

def test_plugin_json_has_the_pinned_keys():
    meta = load(PLUGIN / ".claude-plugin" / "plugin.json")
    assert sorted(meta) == ["description", "license", "name", "version"]
    assert meta["name"] == "workbench" and meta["license"] == "MIT"
    assert re.fullmatch(r"\d+\.\d+\.\d+", meta["version"])
    assert meta["description"].strip()


def servers() -> dict:
    cfg = load(PLUGIN / ".mcp.json")
    # the top-level form is not verified (spec AC 1): accept both
    return cfg["mcpServers"] if "mcpServers" in cfg else cfg


def test_mcp_json_has_1_server_with_the_exact_argv():
    found = servers()
    assert list(found) == ["workbench"]
    assert found["workbench"] == {
        "command": "ssh",
        "args": ["-T", "-o", "BatchMode=yes", "workbench-host", "~/.local/bin/wb-mcp"],
    }


def test_marketplace_names_1_plugin_at_the_plugin_folder():
    market = load(MARKETPLACE)
    assert market["name"] == "workbench"
    assert [(p["name"], p["source"]) for p in market["plugins"]] == [("workbench", "./plugin")]
    assert (ROOT / market["plugins"][0]["source"] / ".claude-plugin" / "plugin.json").is_file()



# AC 2: the commands

TOOLS = {
    "intake": ["wb_intake"],
    "status": ["wb_status"],
    "answer": ["wb_status", "wb_answer"],
    "run-next": ["wb_run_next"],
}
# the plugin form of an MCP tool name; the form is not verified (spec AC 2, AC 4)
TOOL_NAME = re.compile(r"mcp__plugin_workbench_workbench__(wb_[a-z_]+)")


def command(name) -> str:
    return (PLUGIN / "commands" / f"{name}.md").read_text(encoding="utf-8")


def front_matter(name) -> dict[str, str]:
    head, sep, _ = command(name).removeprefix("---\n").partition("\n---\n")
    assert sep and command(name).startswith("---\n"), name
    pairs = [line.split(":", 1) for line in head.splitlines()]
    return {k.strip(): v.strip() for k, v in pairs}


def body(name) -> str:
    return command(name).split("\n---\n", 1)[1]


def test_the_4_command_files_exist():
    assert sorted(p.name for p in (PLUGIN / "commands").iterdir()) == sorted(f"{c}.md" for c in COMMANDS)


@pytest.mark.parametrize("name", COMMANDS)
def test_front_matter_has_the_3_keys(name):
    meta = front_matter(name)
    assert list(meta) == ["description", "argument-hint", "allowed-tools"]
    assert all(meta.values()), meta


@pytest.mark.parametrize("name", COMMANDS)
def test_allowed_tools_name_only_the_planned_wb_tools(name):
    entries = [e.strip() for e in front_matter(name)["allowed-tools"].split(",")]
    names = []
    for entry in entries:
        match = TOOL_NAME.fullmatch(entry)
        assert match, f"{name}: {entry}"
        names.append(match.group(1))
    assert names == TOOLS[name]


def spec_sections() -> list[str]:
    text = (ROOT / "docs" / "specs.md").read_text(encoding="utf-8")
    block = text.split("```markdown\n", 1)[1].split("\n```", 1)[0]
    return re.findall(r"^## .+$", block, re.M)


def test_the_intake_lists_the_headings_of_the_spec_format():
    sections = spec_sections()
    assert len(sections) == 6
    assert re.findall(r"^- `(## .+)`$", body("intake"), re.M) == sections


def test_the_commands_call_their_tools_and_stop_where_the_spec_says():
    assert body("intake").count("wb_intake") >= 1
    assert re.search(r"amendment.*new `wb_intake`", body("intake"), re.S)
    assert "`wb_status`" in body("status") and "1 screen" in body("status")
    assert "case 1, 3 or 4 stop" in body("status") and "case 1, 3 or 4 stop" in body("answer")
    assert "host terminal" in body("status")
    assert "`wb_answer`" in body("answer") and "case 2" in body("answer")
    assert "`wb_run_next`" in body("run-next")
    run_next = body("run-next").lower()
    assert "stop" in run_next and re.search(r"do not wait.*do not poll", run_next, re.S)


@pytest.mark.parametrize("name", COMMANDS)
def test_no_command_approves_merges_or_runs_a_shell(name):
    text = command(name)
    assert not re.search(r"approv|merge", text, re.I)
    assert "wb_approve" not in text and "wb approve" not in text
    assert not re.search(r"\bBash\b|\bEdit\b|\bWrite\b", front_matter(name)["allowed-tools"])
    assert "!`" not in text  # a backtick after ! runs a shell command in a command file


# AC 3: the install text

def install_readme() -> str:
    return (PLUGIN / "README.md").read_text(encoding="utf-8")


def test_the_install_text_pins_the_3_steps_and_the_4_commands():
    text = install_readme()
    steps = re.findall(r"^(\d)\. (.+)$", text, re.M)
    assert [n for n, _ in steps][:3] == ["1", "2", "3"]
    assert "ssh config" in steps[0][1]
    assert steps[1][1].startswith("In Claude Code, run `/plugin marketplace add <path of the clone>`")
    assert steps[2][1].startswith("Run `/plugin install workbench@workbench`")
    block = re.search(r"```\n\s*Host workbench-host\n((?:\s+\w+ .+\n)+)\s*```", text)
    assert block, "the Host block"
    assert re.findall(r"^\s+(\w+) ", block.group(1), re.M) == ["HostName", "User", "IdentityFile"]
    for name in COMMANDS:
        assert f"`/workbench:{name}" in text, name


def test_the_plugin_folder_holds_only_the_planned_files():
    # no hook, skill or agent: a step never loads the plugin, and none of these runs code
    expected = {".claude-plugin/plugin.json", ".mcp.json", "README.md"} | {f"commands/{c}.md" for c in COMMANDS}
    assert plugin_files() == expected


@pytest.mark.parametrize("path", [MARKETPLACE] + sorted(p for p in PLUGIN.rglob("*") if p.is_file()),
                         ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_user_path_or_address_in_the_plugin_files(path):
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"[A-Za-z]:[\\/]Users|/home/|/Users/|\b\d{1,3}(\.\d{1,3}){3}\b", text), path


# AC 5: the root README

def root_readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_root_readme_names_the_plugin_in_the_status_and_the_table():
    text = root_readme()
    status = next(l for l in text.splitlines() if l.startswith("**Status:"))
    assert "Claude Code plugin" in status and "(plugin/README.md)" in status
    assert "the plugin and the slash commands" not in status
    assert re.search(r"^\| \[plugin/\]\(plugin/\) \|.*\bplugin\b", text, re.M)
    assert re.search(r"^\| \[\.claude-plugin/marketplace\.json\]\(\.claude-plugin/marketplace\.json\) \|", text, re.M)


def test_the_root_readme_links_to_the_plugin_resolve():
    for target in ("plugin/", "plugin/README.md", ".claude-plugin/marketplace.json"):
        assert f"]({target})" in root_readme() and (ROOT / target).exists(), target
