"""The opencode adapter: `opencode run --format json` on 1 local model, prompt on stdin.

Runner table:

    adapter = "opencode"
    model = "<model>"                        -m local/<model>
    base_url = "http://localhost:11434/v1"   the OpenAI-compatible API: http or https,
                                             a host, an optional port, then /v1
    edit = "allow"                           allow or deny: the file edit tools
    bash = "allow"                           allow or deny: the shell tool

The config of the step goes in OPENCODE_CONFIG_CONTENT (env): 1 provider
`local` with no API key and no other provider, the model, edit and bash from
the table, webfetch denied, no MCP server, no plugin, share and autoupdate off.
An opencode config in the work folder or a parent up to the git top level
(opencode.json, opencode.jsonc, .opencode/), or an inherited OPENCODE_*
variable, is a usage error: it would add to the permissions. The global config
is the wrapper's job: the step runs with an empty home (runner/README.md).

Each run reports 1 call with the model `local`, which cost/prices.json prices
at $0.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from runner import UsageError
from runner.adapters import Reply, check_keys, check_value

KEYS = {"base_url", "edit", "bash"}
_WIDER = "the runner table is the only source of permission"
FORBIDDEN_KEYS = {
    "webfetch": "no web access",
    "websearch": "no web search",
    "mcp": "no MCP server",
    "plugin": "no plugin",
    "share": "sharing is off",
    "provider": "the adapter writes the 1 local provider itself",
    "agent": _WIDER,
    "api_key": "the local provider has no API key",
    "extra_args": "no argument is passed through",
}
FORBIDDEN_ARGS = {
    "--auto", "--yolo", "--dangerously-skip-permissions", "--attach", "--port", "--share", "--agent",
    "--dir", "--file", "--server",
}
PERMISSIONS = {"allow", "deny"}
BASE_URL = re.compile(r"https?://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::\d{1,5})?/v1")
CONFIG_NAMES = ("opencode.json", "opencode.jsonc", ".opencode")
ENV_NAME = "OPENCODE_CONFIG_CONTENT"
MODEL = "local"  # the model of each call: cost/prices.json prices it at $0
TOKENS = ("input", "output", "cache_read", "cache_write")


def validate(name: str, table: dict) -> None:
    check_keys(name, table, KEYS, FORBIDDEN_KEYS)
    check_value(name, "model", table["model"])
    url = table.get("base_url")
    if not isinstance(url, str) or not BASE_URL.fullmatch(url):
        raise UsageError(f"runner {name}: base_url must be http(s)://<host>[:<port>]/v1, got {url!r}")
    for key in ("edit", "bash"):
        value = table.get(key)
        if value == "ask":
            raise UsageError(f"runner {name}: {key} ask is forbidden: the step has no terminal, it would hang")
        if not isinstance(value, str) or value not in PERMISSIONS:  # the type first: a list is unhashable
            raise UsageError(f"runner {name}: {key} takes {sorted(PERMISSIONS)}, got {value!r}")


def program(table: dict) -> str:
    return "opencode"


def argv(table: dict, exe: str, folder: Path) -> list[str]:
    return [exe, "run", "--format", "json", "-m", f"local/{table['model']}"]


def _config_files(folder: Path) -> list[Path]:
    """The opencode config files that opencode would read from the folder up to
    the git top level (every parent when there is no git top level)."""
    found = []
    for d in (folder, *folder.parents):
        found += [d / n for n in CONFIG_NAMES if os.path.lexists(d / n)]
        if os.path.lexists(d / ".git"):
            break
    return found


def env(table: dict, folder: Path) -> dict[str, str]:
    """The variables that the step adds to the environment of the CLI."""
    inherited = sorted(k for k in os.environ if k.upper().startswith("OPENCODE_") and k.upper() != ENV_NAME)
    if inherited:
        raise UsageError(f"an inherited opencode variable would change the config: {', '.join(inherited)}")
    files = _config_files(Path(folder))
    if files:
        raise UsageError(f"an opencode config would add to the permissions: {', '.join(map(str, files))}")
    model = table["model"]
    content = {
        "provider": {"local": {"npm": "@ai-sdk/openai-compatible", "name": "local",
                               "options": {"baseURL": table["base_url"]}, "models": {model: {"name": model}}}},
        "enabled_providers": ["local"],
        "model": f"local/{model}",
        "permission": {"edit": table["edit"], "bash": table["bash"], "webfetch": "deny"},
        "mcp": {},
        "plugin": [],
        "share": "disabled",
        "autoupdate": False,
    }
    return {ENV_NAME: json.dumps(content)}


def _n(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def parse(stdout: str, table: dict) -> Reply:
    reply = Reply()
    sums: dict = dict.fromkeys(TOKENS)
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        part = ev.get("part") if isinstance(ev.get("part"), dict) else {}
        kind = ev.get("type")
        if kind == "text" and isinstance(part.get("text"), str):
            reply.final_text = part["text"]  # the last text is the final one
        elif kind == "step_finish" and isinstance(part.get("tokens"), dict):
            t = part["tokens"]
            cache = t.get("cache") if isinstance(t.get("cache"), dict) else {}
            for key, v in zip(TOKENS, (t.get("input"), t.get("output"), cache.get("read"), cache.get("write"))):
                if _n(v) is not None:
                    sums[key] = (sums[key] or 0) + v
        elif kind == "error":
            err = ev.get("error")
            data = err.get("data") if isinstance(err, dict) and isinstance(err.get("data"), dict) else {}
            reply.error = str(data.get("message") or (err.get("message") or err.get("name")
                                                      if isinstance(err, dict) else err))
    reply.tokens = sums
    n = {k: sums[k] or 0 for k in TOKENS}
    # always 1 call, also with no counts: usd is 0.0, never "unpriced" or null
    reply.calls = [{"model": MODEL, "in": n["input"], "cr": n["cache_read"], "cw5": n["cache_write"],
                    "cw1": 0, "out": n["output"]}]
    return reply
