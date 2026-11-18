"""Shared test helpers: the repo root and a bash finder for the shell tests.

The shell tests need a real bash. The finder is the one of the secret guard
(wb/guard.py): it never uses ``shutil.which("bash")``: on Windows that can find
the WSL launcher stub, which runs a different system. Order: env
``WORKBENCH_BASH``; on Windows, Git for Windows ``bin/bash.exe`` (derived from
``git --exec-path``, then the default install folder); on other systems
``/bin/bash`` or ``/usr/bin/bash``. If none exists, the shell tests skip with a
reason.

Each test runs with CLAUDE_CONFIG_DIR in a temporary folder that holds a fake
.credentials.json, so that no test reads the credentials of the host.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from wb.guard import find_bash  # noqa: F401 (the shell tests and test_bash_finder use it)

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def bash() -> str:
    path, reason = find_bash()
    if path is None:
        pytest.skip(f"shell test skipped: {reason}")
    return path


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture(autouse=True)
def fake_claude_credentials(tmp_path_factory, monkeypatch) -> Path:
    """CLAUDE_CONFIG_DIR: a temporary folder with a fake .credentials.json; the value is made at run time."""
    folder = tmp_path_factory.mktemp("claude-config")
    token = {"accessToken": "wbtest" + uuid.uuid4().hex, "expiresAt": 1, "scopes": ["user:inference"]}
    (folder / ".credentials.json").write_text(json.dumps({"claudeAiOauth": token}), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(folder))
    return folder


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
