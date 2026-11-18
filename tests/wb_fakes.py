"""Helpers for the wb tests: a config with 1 fake runner for each role, a git
work tree, and an intake of the generic spec fixture.

Each role has its own reply script, so a test sets what each role replies. A
queue of replies for 1 role is written into its script just before each of its
steps; the fake adapter then runs every step as a real subprocess. In a reply,
HEAD stands for the head of the work folder at the start of the step, so that
`BUILD: READY <sha>` names the real head. The push after a build or a rework is
a fake that records the SHA (Replies.pushes); tests/test_wb_push.py tests the
real push.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import tomllib
from pathlib import Path

from conftest import ROOT

SPEC = ROOT / "tests" / "fixtures" / "wb" / "spec.md"
GOOD_SPEC_TEXT = SPEC.read_text(encoding="utf-8").replace("\r\n", "\n")
ROLES = ("spec-checker", "builder", "reviewer", "second-reviewer", "rework")
GIT_ENV = {"GIT_AUTHOR_NAME": "wb test", "GIT_AUTHOR_EMAIL": "wb@example.invalid",
           "GIT_COMMITTER_NAME": "wb test", "GIT_COMMITTER_EMAIL": "wb@example.invalid"}
# A priced model: 250000 input tokens of claude-opus-5-5 cost $1.00 at list price.
PRICED = "claude-opus-5-5"
ONE_DOLLAR = {"input": 250000, "output": 0, "cache_read": 0, "cache_write": 0}
NO_COST = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
HEAD = "<head of the work folder>"
ORIGIN = "https://github.com/example/widget.git"

with (ROOT / "config" / "workbench.example.toml").open("rb") as _f:
    EXAMPLE = tomllib.load(_f)


def make_cfg(tmp_path: Path) -> tuple[dict, dict]:
    """(the workbench config, the role map) for a test; never the owner's local file."""
    cfg = copy.deepcopy(EXAMPLE)
    cfg["state"]["home"] = str(tmp_path / "state")
    cfg["work"]["home"] = str(tmp_path / "work")
    cfg["runners"] = {}
    personas = {"roles": {}}
    replies = tmp_path / "replies"
    replies.mkdir(exist_ok=True)
    for role in ROLES:
        script = replies / f"{role}.json"
        script.write_text(json.dumps({"final_text": "nothing yet"}), encoding="utf-8")
        cfg["runners"][f"fake-{role}"] = {"adapter": "fake", "model": "fake-model", "script": str(script)}
        personas["roles"][role] = {"persona": "p", "runner": f"fake-{role}"}
    return cfg, personas


def git(folder: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=folder, capture_output=True, text=True, check=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


def make_work_tree(cfg: dict, item_id: str) -> Path:
    """A work tree as run-next makes it: a git identity, an origin on GitHub
    and origin/main at the base commit."""
    folder = Path(cfg["work"]["home"]) / item_id
    folder.mkdir(parents=True)
    git(folder, "init", "-q")
    git(folder, "config", "user.name", GIT_ENV["GIT_AUTHOR_NAME"])
    git(folder, "config", "user.email", GIT_ENV["GIT_AUTHOR_EMAIL"])
    git(folder, "commit", "-q", "--allow-empty", "-m", "base")
    git(folder, "remote", "add", "origin", ORIGIN)
    git(folder, "update-ref", "refs/remotes/origin/main", "HEAD")
    return folder


def commit(folder: Path, message: str = "change") -> str:
    git(folder, "commit", "-q", "--allow-empty", "-m", message)
    return git(folder, "rev-parse", "HEAD")


def reply(text: str, counters: dict | None = None, model: str = PRICED, **extra) -> dict:
    return {"final_text": text, "counters": NO_COST if counters is None else counters, "model": model, **extra}


class Replies:
    """Queues of replies, 1 for each role. Before each step of a role, its next
    reply (if any) goes into the role's reply script; then the real run_step,
    with the fake adapter, runs the step. An empty queue repeats the last reply."""

    def __init__(self, monkeypatch, cfg: dict):
        from wb import machine, push

        self.cfg = cfg
        self.queues: dict[str, list] = {role: [] for role in ROLES}
        self.last: dict[str, dict] = {}
        self.calls: list[tuple[str, float]] = []
        self.pushes: list[str] = []
        real = machine.run_step

        def step(folder, prompt, timeout, runner, out, *, cfg=None):
            role = runner.removeprefix("fake-")
            if self.queues[role]:
                self.last[role] = self.queues[role].pop(0)
            if role in self.last:
                self.set(role, self.last[role], machine.head(folder))
            self.calls.append((role, timeout))
            return real(folder, prompt, timeout, runner, out, cfg=cfg)

        monkeypatch.setattr(machine, "run_step", step)
        monkeypatch.setattr(push, "publish", lambda cfg, st, sha, title, body: self.pushes.append(sha))

    def set(self, role: str, reply_: dict, head: str | None = None) -> None:
        if head and isinstance(reply_.get("final_text"), str):
            reply_ = {**reply_, "final_text": reply_["final_text"].replace(HEAD, head)}
        Path(self.cfg["runners"][f"fake-{role}"]["script"]).write_text(json.dumps(reply_), encoding="utf-8")

    def add(self, role: str, *texts) -> "Replies":
        self.queues[role] += [reply(t) if isinstance(t, str) else t for t in texts]
        return self

    def roles(self) -> list[str]:
        return [role for role, _ in self.calls]


def start(tmp_path: Path, monkeypatch, item_id: str = "7", spec_text: str | None = None):
    """An item after intake, with its work tree: (cfg, personas, replies)."""
    from wb import intake

    cfg, personas = make_cfg(tmp_path)
    make_work_tree(cfg, item_id)
    spec = SPEC
    if spec_text is not None:
        spec = tmp_path / "spec.md"
        spec.write_text(spec_text, encoding="utf-8")
    assert intake.intake(cfg, str(spec), item_id) == []
    return cfg, personas, Replies(monkeypatch, cfg)
