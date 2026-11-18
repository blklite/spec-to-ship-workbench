"""The fake adapter: replays a reply script (fake_replay.py) as a real subprocess,
so that the time limit, the exit code and the kill take the real path.

Runner table: adapter = "fake", model, script (the reply script; a relative
path is relative to the repo root), command (optional: the program that runs
the replay script; default the Python that runs the step).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from runner import ROOT, UsageError
from runner.adapters import Reply, check_keys, check_value, count

REPLAY = Path(__file__).with_name("fake_replay.py")
KEYS = {"script", "command"}
TOKENS = ("input", "output", "cache_read", "cache_write")


def _script(table: dict) -> Path:
    p = Path(table["script"])
    return p if p.is_absolute() else ROOT / p


def validate(name: str, table: dict) -> None:
    check_keys(name, table, KEYS, {})
    check_value(name, "model", table["model"])
    if not isinstance(table.get("script"), str):
        raise UsageError(f"runner {name}: the required key script is missing")
    if not _script(table).is_file():
        raise UsageError(f"runner {name}: the reply script does not exist: {table['script']}")
    if "command" in table and not isinstance(table["command"], str):
        raise UsageError(f"runner {name}: command must be a string")


def program(table: dict) -> str:
    return table.get("command") or sys.executable


def argv(table: dict, exe: str, folder: Path) -> list[str]:
    return [exe, str(REPLAY), str(_script(table))]


def parse(stdout: str, table: dict) -> Reply:
    try:
        data = json.loads(stdout)
    except ValueError:
        return Reply(error="the fake reply is not JSON")
    if not isinstance(data, dict):
        return Reply(error="the fake reply is not a JSON object")
    counters = data.get("counters") if isinstance(data.get("counters"), dict) else {}
    tokens = {k: counters.get(k) for k in TOKENS}
    text = data.get("final_text")
    reply = Reply(final_text=text if isinstance(text, str) else None, tokens=tokens)
    if any(v is not None for v in tokens.values()):  # no counter at all: no usage, no dollars
        n = {k: count(tokens[k]) for k in TOKENS}
        reply.calls = [{"model": data.get("model") or table["model"], "in": n["input"],
                        "cr": n["cache_read"], "cw5": n["cache_write"], "cw1": 0, "out": n["output"]}]
    return reply
