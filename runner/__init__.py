"""The runner core: 1 operation, "run a step", on any configured runner.

See runner/README.md for the adapter contract.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class UsageError(Exception):
    """A bad command line, config or prompt file: exit 2, no STEP line, no run folder."""
