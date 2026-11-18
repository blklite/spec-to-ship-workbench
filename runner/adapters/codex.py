"""The Codex adapter: `codex exec --json` with the prompt on stdin.

Runner table:

    adapter = "codex"
    model = "<model>"                   -m
    sandbox = "read-only"               -s: read-only or workspace-write
    require_commands = true             optional: a run with no command is
                                        UNAVAILABLE (see parse)

It always passes -C <folder>, --ephemeral, --ignore-user-config and
--ignore-rules, so that no user config, profile or rules file adds a
permission. The command line is built from these keys only. It never passes a
--dangerously-* flag, --search, --add-dir, --oss, -c/--config, -p/--profile,
--enable, --approve-for-me, --skip-git-repo-check or -s danger-full-access; a
table that asks for one is a usage error.
"""

from __future__ import annotations

import json
from pathlib import Path

from runner import UsageError
from runner.adapters import Reply, check_keys, check_value, count

KEYS = {"sandbox", "require_commands"}
SANDBOXES = {"read-only", "workspace-write"}
_WIDER = "the runner config is the only source of permission"
FORBIDDEN_KEYS = {
    "dangerously_bypass_approvals_and_sandbox": "the runner never bypasses the sandbox",
    "dangerously_bypass_hook_trust": "the runner never bypasses the hook trust",
    "search": "no web search",
    "add_dir": "the step writes in its folder only",
    "oss": "the codex adapter runs the configured model only",
    "local_provider": "the codex adapter runs the configured model only",
    "config": _WIDER,
    "profile": _WIDER,
    "enable": _WIDER,
    "approve_for_me": _WIDER,
    "skip_git_repo_check": "codex runs in a git repo only",
    "extra_args": "no argument is passed through",
}
FORBIDDEN_ARGS = {
    "--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust",
    "--search", "--add-dir", "--oss", "--local-provider", "-c", "--config", "-p", "--profile",
    "--enable", "--approve-for-me", "--skip-git-repo-check", "danger-full-access",
}
MESSAGE_TYPES = ("agent_message", "assistant_message")
# codex retries a stream that drops and gives an error item for each retry; the
# run goes on. Only an error item whose message starts with 1 of these texts is
# not fatal; any other, also "failed to reconnect ...", is.
RETRY_TEXTS = ("Reconnecting... ", "stream disconnected before completion: ",
               "stream disconnected - retrying sampling request (")


def validate(name: str, table: dict) -> None:
    check_keys(name, table, KEYS, FORBIDDEN_KEYS)
    check_value(name, "model", table["model"])
    sandbox = table.get("sandbox")
    if sandbox == "danger-full-access":
        raise UsageError(f"runner {name}: sandbox danger-full-access is forbidden")
    if not isinstance(sandbox, str) or sandbox not in SANDBOXES:  # the type first: a list is unhashable
        raise UsageError(f"runner {name}: sandbox takes {sorted(SANDBOXES)}, got {sandbox!r}")
    if not isinstance(table.get("require_commands", False), bool):
        raise UsageError(f"runner {name}: require_commands must be true or false")


def program(table: dict) -> str:
    return "codex"


def argv(table: dict, exe: str, folder: Path) -> list[str]:
    return [exe, "exec", "--json", "-C", str(folder), "--ephemeral", "--ignore-user-config",
            "--ignore-rules", "-s", table["sandbox"], "-m", table["model"], "-"]


def parse(stdout: str, table: dict) -> Reply:
    reply = Reply()
    used = {"input": 0, "cached": 0, "cache_write": 0, "output": 0}
    turns = 0
    has_cache_write = False  # an older codex has no cache_write_input_tokens
    commands, succeeded, errors = set(), set(), {}  # by item id
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        item = ev.get("item")
        if str(kind).startswith("item.") and isinstance(item, dict):
            # shortcut: an item with no id counts as 1 item for each line, which is
            # enough for 0 or more than 0; pair its events by text if a codex omits ids
            key = item["id"] if isinstance(item.get("id"), str) else line
            if item.get("type") == "command_execution":
                commands.add(key)
                if kind == "item.completed" and type(item.get("exit_code")) is int and item["exit_code"] == 0:
                    succeeded.add(key)
                elif kind == "item.completed":  # its last end counts
                    succeeded.discard(key)
            elif item.get("type") == "error":
                errors[key] = item.get("message")
        if kind == "item.completed" and isinstance(item, dict):
            if (item.get("type") or item.get("item_type")) in MESSAGE_TYPES and isinstance(item.get("text"), str):
                reply.final_text = item["text"]  # the last message is the final one
        elif kind == "turn.completed" and isinstance(ev.get("usage"), dict):
            u = ev["usage"]
            turns += 1
            used["input"] += count(u.get("input_tokens"))
            used["cached"] += count(u.get("cached_input_tokens"))
            if "cache_write_input_tokens" in u:
                has_cache_write = True
                used["cache_write"] += count(u.get("cache_write_input_tokens"))
            used["output"] += count(u.get("output_tokens"))
        elif kind == "turn.failed":
            err = ev.get("error")
            reply.error = str(err.get("message") if isinstance(err, dict) else err)
        elif kind == "error":
            reply.error = str(ev.get("message"))
    reply.commands = len(commands)
    if table.get("require_commands"):
        if not commands:
            reply.unavailable = "no command ran"
        elif not succeeded:
            reply.unavailable = "no command ran (no command ended with exit code 0)"
        elif any(not (isinstance(m, str) and m.startswith(RETRY_TEXTS)) for m in errors.values()):
            reply.unavailable = "no command ran (an error item that is not a stream retry)"
    if turns:
        # input_tokens includes the cached tokens and the cache writes (codex
        # passes on input_tokens and its details from the API usage, whose cost
        # guide prices each input token at 1 rate: uncached, cached or cache
        # write). The counters keep the raw values; the price takes the rest as
        # input, and a cache write at the 5-minute multiplier (1.25 x input).
        cached = min(used["cached"], used["input"])
        written = min(used["cache_write"], used["input"] - cached)
        reply.tokens = {"input": used["input"], "output": used["output"], "cache_read": used["cached"],
                        "cache_write": used["cache_write"] if has_cache_write else None}
        reply.calls = [{"model": table["model"], "in": used["input"] - cached - written, "cr": cached,
                        "cw5": written, "cw1": 0, "out": used["output"]}]
    return reply
