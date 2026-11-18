"""1 folder for each item, atomic writes, a known version."""

from __future__ import annotations

import json
import subprocess

import pytest

from conftest import ROOT
from runner import UsageError
from wb import state

FIELDS = {"version", "id", "spec", "work", "tier", "size", "limits", "stage", "status", "rounds_used",
          "usd", "seconds", "question", "steps"}


@pytest.fixture
def cfg(tmp_path):
    return {"state": {"home": str(tmp_path / "state")}}


def test_the_state_holds_the_fields_of_the_spec(cfg):
    st = state.new("243")
    assert FIELDS <= set(st)
    assert set(st["spec"]) == {"path", "sha256"}
    assert set(st["work"]) == {"folder", "branch", "base", "repo", "url"}
    assert set(st["limits"]) == {"usd", "hours", "rounds"}
    state.save(cfg, st)
    assert state.load(cfg, "243") == st
    assert (state.home(cfg) / "243" / "state.json").is_file()


def test_a_failed_rename_leaves_the_old_file(cfg, monkeypatch):
    st = state.new("243")
    state.save(cfg, st)
    path = state.home(cfg) / "243" / "state.json"
    old = path.read_bytes()

    def broken(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(state.os, "replace", broken)
    st["stage"] = "build"
    with pytest.raises(OSError):
        state.save(cfg, st)
    assert path.read_bytes() == old
    assert [p.name for p in path.parent.iterdir()] == ["state.json"]


def test_a_failed_write_leaves_the_old_file(cfg):
    st = state.new("243")
    state.save(cfg, st)
    path = state.home(cfg) / "243" / "state.json"
    old = path.read_bytes()
    st["question"] = object()  # not JSON: the write fails before the rename
    with pytest.raises(TypeError):
        state.save(cfg, st)
    assert path.read_bytes() == old
    assert [p.name for p in path.parent.iterdir()] == ["state.json"]


@pytest.mark.parametrize("version", [0, 2, "1", None])
def test_an_unknown_version_is_an_error(cfg, version):
    st = state.new("243")
    st["version"] = version
    path = state.home(cfg) / "243" / "state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(st), encoding="utf-8")
    with pytest.raises(UsageError, match="unknown state version"):
        state.load(cfg, "243")


def test_a_missing_or_broken_state_is_an_error(cfg):
    with pytest.raises(UsageError, match="no state"):
        state.load(cfg, "243")
    path = state.home(cfg) / "243" / "state.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(UsageError, match="cannot read"):
        state.load(cfg, "243")


@pytest.mark.parametrize("bad", ["", "..", "../x", "a/b", "a\\b", "-x", "a b", "a.b"])
def test_a_bad_item_id_is_refused(cfg, bad):
    with pytest.raises(UsageError, match="bad item id"):
        state.item_dir(cfg, bad)


def test_git_ignores_the_default_state_home():
    import tomllib

    with (ROOT / "config" / "workbench.example.toml").open("rb") as f:
        home = tomllib.load(f)["state"]["home"]
    r = subprocess.run(["git", "check-ignore", "-q", f"{home.rstrip('/')}/243/state.json"], cwd=ROOT)
    assert r.returncode == 0
