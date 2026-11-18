"""Helpers for the adapter tests: a fake vendor CLI on PATH.

The shim is written at test time: a committed shim would lose its executable
bit on a Windows checkout. On Windows it is a .cmd file, as a real npm-style
CLI often is, so the tests also take the cmd.exe path.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

from conftest import ROOT

FAKE_CLI = ROOT / "tests" / "fixtures" / "runner" / "fake_cli.py"
FAKE_WRAP = ROOT / "tests" / "fixtures" / "runner" / "fake_wrap.py"


def _shim(tmp_path: Path, name: str, script: Path) -> Path:
    """An executable `name` in tmp_path/bin that runs the Python `script`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if sys.platform == "win32":
        shim = bin_dir / f"{name}.cmd"
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        shim = bin_dir / name
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return shim


def install(monkeypatch, tmp_path: Path, name: str) -> Path:
    """Put a fake `name` CLI first on PATH; return the folder of the shim."""
    bin_dir = _shim(tmp_path, name, FAKE_CLI).parent
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    log = tmp_path / "cli-log.json"
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    for var in ("FAKE_CLI_OUTPUT", "FAKE_CLI_MORE", "FAKE_CLI_EXIT", "FAKE_CLI_CHILD"):
        monkeypatch.delenv(var, raising=False)
    return bin_dir


def output(monkeypatch, tmp_path: Path, text: str) -> None:
    f = tmp_path / "cli-output.txt"
    f.write_text(text, encoding="utf-8")
    monkeypatch.setenv("FAKE_CLI_OUTPUT", str(f))


def logged(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "cli-log.json").read_text(encoding="utf-8"))


def install_wrap(monkeypatch, tmp_path: Path) -> str:
    """A fake wrap command (fake_wrap.py); return its absolute path with / separators."""
    monkeypatch.setenv("FAKE_WRAP_LOG", str(tmp_path / "wrap-log.json"))
    monkeypatch.delenv("FAKE_WRAP_SKIP", raising=False)
    return _shim(tmp_path, "wrap", FAKE_WRAP).as_posix()


def wrap_logged(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "wrap-log.json").read_text(encoding="utf-8"))
