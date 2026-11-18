"""The runner config: the `[runners.<name>]` tables and the role-to-runner map.

The loader reads the local file (`config/workbench.toml`, `config/personas.toml`)
when it exists, else the example beside it. It takes a repo root or the path of
1 file, so that a test can use its own config and never the owner's local one.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from runner import ROOT, UsageError

ADAPTER_NAMES = ("claude", "codex", "fake", "opencode")


def _pick(source: str | Path | None, stem: str) -> Path:
    """The file to read: `source` itself when it is a file, else the local
    `<root>/config/<stem>.toml`, else its example."""
    if source is not None and Path(source).is_file():
        return Path(source)
    root = Path(source) if source is not None else ROOT
    local = root / "config" / f"{stem}.toml"
    if local.is_file():
        return local
    example = root / "config" / f"{stem}.example.toml"
    if example.is_file():
        return example
    raise UsageError(f"no config found: neither {local} nor {example}")


def _load(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise UsageError(f"cannot read config {path}: {e}") from e


def load_workbench(source: str | Path | None = None) -> dict:
    """The workbench config: a root folder, a file path, or None for this repo."""
    return _load(_pick(source, "workbench"))


def load_personas(source: str | Path | None = None) -> dict:
    """The role map: a root folder, a file path, or None for this repo."""
    return _load(_pick(source, "personas"))


def runner_config(cfg: dict, name: str) -> dict:
    """The table of runner `name`, with its adapter name checked. The adapter
    checks its own keys (runner/adapters/)."""
    runners = cfg.get("runners")
    if not isinstance(runners, dict) or name not in runners:
        if isinstance(runners, dict) and runners:
            raise UsageError(f"unknown runner: {name} (configured: {', '.join(sorted(runners))})")
        raise UsageError(f"unknown runner: {name} (the config has no [runners.<name>] table;"
                         " copy them from config/workbench.example.toml)")
    table = runners[name]
    if not isinstance(table, dict):
        raise UsageError(f"runner {name}: not a table")
    for key in ("adapter", "model"):
        if key not in table:
            raise UsageError(f"runner {name}: the required key {key} is missing")
    if table["adapter"] not in ADAPTER_NAMES:
        raise UsageError(f"runner {name}: unknown adapter: {table['adapter']}")
    if table["model"] == "local" and table["adapter"] != "opencode":
        raise UsageError(f"runner {name}: the model local is the $0 price of the opencode adapter only")
    if not isinstance(table.get("guard", False), bool):
        raise UsageError(f"runner {name}: guard must be true or false")
    return dict(table)


def guard(table: dict) -> bool:
    """The secret guard of a runner (wb scans before its steps): on by default
    for each adapter except fake."""
    return table.get("guard", table["adapter"] != "fake")


def role_runner(personas: dict, role: str) -> str:
    """The runner name that the role map gives a role."""
    entry = (personas.get("roles") or {}).get(role)
    if not isinstance(entry, dict) or "runner" not in entry:
        raise UsageError(f"role {role}: no runner in the role map")
    return entry["runner"]
