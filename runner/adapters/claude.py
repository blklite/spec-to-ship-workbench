"""The Claude Code adapter: `claude -p` with the prompt on stdin.

Runner table:

    adapter = "claude"
    model = "<full model id>"            --model
    allowed_tools = ["Read", ...]        --allowedTools (default: none)
    setting_sources = []                 --setting-sources (default: none)
    permission_mode = "default"          --permission-mode: default, dontAsk or plan
                                         (always passed; default: default)

It always passes --strict-mcp-config, so that no settings file and no MCP
config adds a permission, a hook, a plugin or a server beyond what the
runner table states. It never passes
--dangerously-skip-permissions, --allow-dangerously-skip-permissions or the
bypassPermissions mode; a table that asks for one is a usage error. It also
refuses the modes acceptEdits and auto, which approve tools that
allowed_tools does not name: a runner that must edit files names Edit and
Write in allowed_tools.

It also passes --output-format stream-json --verbose: 1 JSON object on each
line, with a result object for each turn of the reply (a late notification,
of a background command for example, opens 1 more turn). stdout.txt of the
run folder keeps that raw output; parse gives the text of each result, in
order, as Reply.report, which the step writes to reply.txt, the file that
the next step reads. The final text, the last line and the counters come
from the last result only.

The stream holds the tool results too, so a step that prints its environment
or its credentials file would store a token there. The step replaces each
secret with <redacted> as the CLI writes, before a byte reaches stdout.txt or
stderr.txt (runner/proc.py). The secrets are the value of each SECRET_ENV
variable of the CLI's environment and, for a runner with no wrap, each JSON
string value of SECRET_MIN or more characters, at any depth, of CREDENTIALS
in CLAUDE_CONFIG_DIR (a relative one in the work folder), else in .claude
of the home folder (file_secrets); such a value with a character outside
printable ASCII, or with a quote or a backslash, makes a file that can not
be read (PLAIN). An unwrapped step with no secret does not start:
UNAVAILABLE, "no credential to redact", or "the credentials file of the CLI
can not be read". A write or close of a stored file that fails is
FAILED, "the output capture failed".
scripts/wrap-step.sh, the wrap of a Linux host, has its own home and redacts
the token it holds as the step writes, so a wrapped step reads no file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from runner import UsageError
from runner.adapters import Reply, check_keys, check_value, count

KEYS = {"allowed_tools", "setting_sources", "permission_mode"}
FORBIDDEN_KEYS = {
    "dangerously_skip_permissions": "the runner never skips the permission checks",
    "allow_dangerously_skip_permissions": "the runner never skips the permission checks",
    "mcp_config": "the runner config is the only source of permission",
    "settings": "the runner config is the only source of permission",
    "add_dir": "the step works in its folder only",
    "extra_args": "no argument is passed through",
}
FORBIDDEN_ARGS = {
    "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
    "bypassPermissions", "--settings", "--mcp-config", "--add-dir", "--plugin-dir",
}
# allowed_tools is the only source of tool permission: no mode that approves
# more (acceptEdits approves file edits, auto and bypassPermissions approve more)
PERMISSION_MODES = {"default", "dontAsk", "plan"}
DEFAULT_MODE = "default"
# a non-zero exit is FAILED, whatever the last line says: the stream can end
# with an allowed line of an earlier turn, then a crash in a later turn
EXIT_FAILS = True
# the variables of the environment that can hold a credential of the CLI
SECRET_ENV = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
# the file of a CLI that signs in with no variable: in CLAUDE_CONFIG_DIR, else in ~/.claude
CREDENTIALS = ".credentials.json"
# a string value of that file this long or longer is a secret; a shorter one (a scope) is not
SECRET_MIN = 20
# a secret holds printable ASCII only, with no " and no \: no other form of it is redacted
PLAIN = re.compile(r'[ !#-\[\]-~]*')
SETTING_SOURCES = {"user", "project", "local"}
# A tool name, MCP tools included (mcp__server__tool). No rule in brackets: a
# value must not reach the command line with a space, a bracket or a "-".
TOOL = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def _list(name: str, table: dict, key: str) -> list[str]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise UsageError(f"runner {name}: {key} must be a list of strings")
    if len(set(value)) != len(value):
        raise UsageError(f"runner {name}: {key} lists a value twice")
    return value


def validate(name: str, table: dict) -> None:
    check_keys(name, table, KEYS, FORBIDDEN_KEYS)
    check_value(name, "model", table["model"])
    for tool in _list(name, table, "allowed_tools"):
        check_value(name, "allowed_tools", tool, TOOL)
    for source in _list(name, table, "setting_sources"):
        if source not in SETTING_SOURCES:
            raise UsageError(f"runner {name}: setting_sources takes {sorted(SETTING_SOURCES)}, got {source!r}")
    if "permission_mode" in table:
        mode = table["permission_mode"]
        if mode == "bypassPermissions":
            raise UsageError(f"runner {name}: permission_mode bypassPermissions is forbidden")
        if not isinstance(mode, str) or mode not in PERMISSION_MODES:  # the type first: a list is unhashable
            raise UsageError(f"runner {name}: permission_mode takes {sorted(PERMISSION_MODES)}, got {mode!r}")


def file_secrets(env: dict, folder: Path) -> list[str]:
    """The secrets of the CLI's credentials file: each JSON string value of
    SECRET_MIN or more characters, at any depth. [] with no file; OSError or
    ValueError when the file can not be read or parsed, or a secret is not
    PLAIN. folder is the work folder, where the CLI starts: a relative
    CLAUDE_CONFIG_DIR is in it."""
    config = Path(folder) / env["CLAUDE_CONFIG_DIR"] if env.get("CLAUDE_CONFIG_DIR") else Path.home() / ".claude"
    try:
        stack = [json.loads((config / CREDENTIALS).read_text(encoding="utf-8"))]
    except FileNotFoundError:
        return []
    found = []
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack += value.values()
        elif isinstance(value, list):
            stack += value
        elif isinstance(value, str) and len(value) >= SECRET_MIN:
            if not PLAIN.fullmatch(value):  # the CLI may print it escaped, past the redaction
                raise ValueError("a secret that is not plain ASCII")
            found.append(value)
    return found


def program(table: dict) -> str:
    return "claude"


def argv(table: dict, exe: str, folder: Path) -> list[str]:
    cmd = [exe, "-p", "--output-format", "stream-json", "--verbose", "--model", table["model"]]
    tools = table.get("allowed_tools") or []
    if tools:
        cmd += ["--allowedTools", ",".join(tools)]
    # always passed: else a setting source that the table names could set
    # its own permissions.defaultMode
    cmd += ["--permission-mode", table.get("permission_mode", DEFAULT_MODE)]
    cmd += ["--setting-sources", ",".join(table.get("setting_sources") or []), "--strict-mcp-config"]
    return cmd


def _results(stdout: str) -> list[dict]:
    """The result objects, in order: the whole output, the results of a list,
    or each line that is a result object (the stream)."""
    try:
        data = json.loads(stdout)
    except ValueError:
        data = None
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict) and d.get("type") == "result"]
    results = []
    for line in stdout.split("\n"):  # not splitlines: a JSON string may hold U+2028 as it is
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("type") == "result":
            results.append(d)
    return results


def parse(stdout: str, table: dict) -> Reply:
    results = _results(stdout)
    if not results:
        return Reply(error="no JSON result")
    # the counters and the last line come from the last result, the report from each
    data = results[-1]
    text = data.get("result")
    reply = Reply(final_text=text if isinstance(text, str) else None,
                  report="\n\n".join(r["result"] for r in results if isinstance(r.get("result"), str) and r["result"]))
    if data.get("is_error"):
        reply.error = str(data.get("subtype") or "error")
    if isinstance(data.get("duration_ms"), (int, float)):
        reply.cli_seconds = data["duration_ms"] / 1000
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
    by_model = data.get("modelUsage") if isinstance(data.get("modelUsage"), dict) else None
    if usage is None and not by_model:
        return reply
    usage = usage or {}
    cc = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    cw5, cw1 = cc.get("ephemeral_5m_input_tokens"), cc.get("ephemeral_1h_input_tokens")
    if cw5 is None and cw1 is None:
        cw5, cw1 = usage.get("cache_creation_input_tokens"), 0  # no split: the 5-minute TTL
    cw5, cw1 = count(cw5), count(cw1)
    share_1h = cw1 / (cw5 + cw1) if cw5 + cw1 else 0.0
    if by_model:
        # 1 call for each model: Claude Code may use a second model for side tasks
        for model, u in by_model.items():
            u = u if isinstance(u, dict) else {}
            cw = count(u.get("cacheCreationInputTokens"))
            hour = round(cw * share_1h)
            reply.calls.append({"model": model, "in": count(u.get("inputTokens")),
                                "cr": count(u.get("cacheReadInputTokens")),
                                "cw5": cw - hour, "cw1": hour, "out": count(u.get("outputTokens"))})
    else:
        reply.calls.append({"model": table["model"], "in": count(usage.get("input_tokens")),
                            "cr": count(usage.get("cache_read_input_tokens")),
                            "cw5": cw5, "cw1": cw1, "out": count(usage.get("output_tokens"))})
    reply.tokens = {
        "input": sum(c["in"] for c in reply.calls),
        "output": sum(c["out"] for c in reply.calls),
        "cache_read": sum(c["cr"] for c in reply.calls),
        "cache_write": sum(c["cw5"] + c["cw1"] for c in reply.calls),
    }
    return reply
