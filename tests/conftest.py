"""Shared test helpers: the repo root and a bash finder for the shell tests.

The shell tests need a real bash. The finder never uses ``shutil.which("bash")``:
on Windows that can find the WSL launcher stub, which runs a different system.
Order: env ``WORKBENCH_BASH``; on Windows, Git for Windows ``bin/bash.exe``
(derived from ``git --exec-path``, then the default install folder); on other
systems ``/bin/bash`` or ``/usr/bin/bash``. If none exists, the shell tests skip
with a reason.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _git_for_windows_bash() -> list[Path]:
    found: list[Path] = []
    try:
        out = subprocess.run(
            ["git", "--exec-path"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        out = ""
    if out:
        # <Git>/mingw64/libexec/git-core -> <Git>/bin/bash.exe
        exec_path = Path(out)
        for parent in exec_path.parents:
            candidate = parent / "bin" / "bash.exe"
            if candidate.is_file():
                found.append(candidate)
                break
    found.append(Path("C:/Program Files/Git/bin/bash.exe"))
    return found


def find_bash() -> tuple[str | None, str]:
    """Return (path, reason). path is None when no usable bash exists."""
    env = os.environ.get("WORKBENCH_BASH")
    if env:
        if Path(env).is_file():
            return env, "WORKBENCH_BASH"
        return None, f"WORKBENCH_BASH names a missing file: {env}"
    if sys.platform == "win32":
        candidates = _git_for_windows_bash()
    else:
        candidates = [Path("/bin/bash"), Path("/usr/bin/bash")]
    for c in candidates:
        if c.is_file():
            return str(c), "found"
    return None, "no bash found (set WORKBENCH_BASH, or install Git for Windows)"


@pytest.fixture(scope="session")
def bash() -> str:
    path, reason = find_bash()
    if path is None:
        pytest.skip(f"shell test skipped: {reason}")
    return path


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


def load_denylist() -> list[tuple[str, str]] | None:
    """The (class, word) pairs of the local list named by WORKBENCH_DENYLIST.

    None when the env var is not set. The words are never printed by a test:
    a failure names the class and the file only.
    """
    name = os.environ.get("WORKBENCH_DENYLIST")
    if not name:
        return None
    pairs = []
    for line in Path(name).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cls, word = line.split(None, 1)  # same rule as the scan: any white space
        pairs.append((cls, word.strip()))
    return pairs


def denylist_hits(text: str, pairs: list[tuple[str, str]]) -> list[str]:
    """The classes of the private words found whole-word, case-insensitive."""
    import re

    hits = []
    for cls, word in pairs:
        if re.search(r"(?<![A-Za-z0-9_])" + re.escape(word) + r"(?![A-Za-z0-9_])", text, re.I):
            hits.append(cls)
    return hits
