"""The adapters: 1 module for each kind of runner.

Each adapter module has:

    validate(name, table)         check the runner table; UsageError if bad
    program(table) -> str         the CLI to find on PATH
    argv(table, exe, folder)      the command line (the prompt goes on stdin)
    parse(stdout, table) -> Reply the final text and the counters

and may have FORBIDDEN_ARGS, the arguments that check_argv refuses,
SECRET_ENV, the names of the variables whose values the step redacts in the
output as the CLI writes it, EXIT_FAILS, true when a non-zero exit is FAILED whatever the
last line says, and

    env(table, folder) -> dict    variables to add to the environment of the CLI

See runner/README.md for the contract and how to add an adapter.
"""

from __future__ import annotations

import importlib
import re
import sys
from dataclasses import dataclass, field

from runner import UsageError

# A config string that reaches a command line: no shell metacharacter, no
# space, no leading "-" (a value that starts with "-" would become a flag).
SAFE_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")
# cmd.exe acts on these when the CLI is a .cmd or .bat shim.
CMD_META = re.compile(r'[&|<>^%!"\r\n()]')
# The program of a wrap: an absolute path of SAFE_VALUE characters (no ":"),
# or on Windows also with a drive letter.
WRAP_PROGRAM = re.compile((r"(?:[A-Za-z]:)?" if sys.platform == "win32" else "")
                          + r"/[A-Za-z0-9._-][A-Za-z0-9._/-]*")


@dataclass
class Reply:
    final_text: str | None = None
    # the text of each result, in order, joined with 1 empty line; "" when none
    report: str = ""
    # input, output, cache_read, cache_write: an int, or None where the runner
    # does not give the count
    tokens: dict = field(default_factory=lambda: {
        "input": None, "output": None, "cache_read": None, "cache_write": None})
    # 1 entry for each model, in the form of cost.py: model, in, cr, cw5, cw1, out
    calls: list = field(default_factory=list)
    cli_seconds: float | None = None
    error: str | None = None
    # the count of command items, where the runner gives it (codex)
    commands: int | None = None
    # why the run is not a result, whatever its last line says (require_commands)
    unavailable: str | None = None


def count(v) -> int:
    """A token count from CLI output: a non-negative int, else 0."""
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def check_keys(name: str, table: dict, allowed: set[str], forbidden: dict[str, str]) -> None:
    """Only known keys; a key that would widen the permissions is refused by name."""
    for key in table:
        if key in forbidden:
            raise UsageError(f"runner {name}: {key} is forbidden: {forbidden[key]}")
        if key not in allowed | {"adapter", "model", "wrap", "guard"}:
            raise UsageError(f"runner {name}: unknown key {key}")
    for key, value in table.items():
        values = value if isinstance(value, list) else [value]
        for v in values:
            if isinstance(v, str) and "dangerously" in v.lower():
                raise UsageError(f"runner {name}: {key} asks for a dangerous mode: forbidden")


def check_value(name: str, key: str, value, pattern: re.Pattern = SAFE_VALUE) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise UsageError(f"runner {name}: {key} must match {pattern.pattern}, got {value!r}")
    return value


def check_wrap(name: str, table: dict) -> list[str]:
    """The optional wrap command of a runner: the step runs wrap + the adapter argv."""
    wrap = table.get("wrap", [])
    if not isinstance(wrap, list) or not all(isinstance(w, str) for w in wrap):
        raise UsageError(f"runner {name}: wrap must be a list of strings")
    if "wrap" in table and not wrap:
        raise UsageError(f"runner {name}: wrap must not be empty")
    if wrap and not WRAP_PROGRAM.fullmatch(wrap[0]):
        raise UsageError(f"runner {name}: the first element of wrap must be an absolute path"
                         f" matching {WRAP_PROGRAM.pattern}, got {wrap[0]!r}")
    for w in wrap[1:]:
        check_value(name, "wrap", w)
    return wrap


def check_argv(argv: list[str], exe: str, forbidden: set[str]) -> None:
    """The last guard before the CLI starts: no forbidden flag, no "dangerously"
    in any element, and no cmd.exe metacharacter when the CLI is a shim."""
    shim = exe.lower().endswith((".cmd", ".bat"))
    for a in argv[1:]:
        if "dangerously" in a.lower() or any(part in forbidden for part in [a, *a.split("=", 1)]):
            raise UsageError(f"a forbidden argument reached the command line: {a}")
        if shim and CMD_META.search(a):
            raise UsageError(f"an argument holds a character that cmd.exe acts on: {a!r}")


def get(name: str):
    """The adapter module of an adapter name that config.runner_config checked."""
    return importlib.import_module(f"runner.adapters.{name}")
