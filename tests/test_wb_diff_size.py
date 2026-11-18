"""At ready, records.diff holds the files and lines of base...pushed."""

from __future__ import annotations

import pytest

from wb import machine, state
from wb_fakes import git, start
from test_wb_machine import happy


@pytest.fixture
def item(tmp_path, monkeypatch):
    """An item whose work folder has 1 commit after the base: 2 text files and 1 binary file."""
    cfg, personas, r = start(tmp_path, monkeypatch)
    folder = state.load(cfg, "7")["work"]["folder"]
    (tmp_path / "work" / "7" / "a.txt").write_text("1\n2\n3\n", encoding="utf-8")
    (tmp_path / "work" / "7" / "b.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "work" / "7" / "c.bin").write_bytes(b"\x00\x01\x02" * 100)
    git(tmp_path / "work" / "7", "add", "-A")
    git(tmp_path / "work" / "7", "commit", "-q", "-m", "files")
    return cfg, personas, happy(r), folder


def is_utc(text) -> bool:
    return isinstance(text, str) and len(text) == 20 and text.endswith("Z") and text[10] == "T"


def test_ready_records_the_files_and_lines(item):
    cfg, personas, r, folder = item
    st = machine.run_next(cfg, personas, "7")
    assert (st["stage"], st["status"]) == ("delivery", "ready")
    diff = dict(st["records"]["diff"])
    assert is_utc(diff.pop("time"))
    assert diff == {"base": st["remote"]["base_sha"], "head": st["pushed"], "files": 3, "added": 4, "deleted": 0}
    assert state.load(cfg, "7") == st


def test_the_ready_of_run_next_records_again_and_the_last_wins(item, tmp_path):
    cfg, personas, r, folder = item
    st = machine.run_next(cfg, personas, "7")
    work = tmp_path / "work" / "7"
    (work / "a.txt").write_text("1\n0\n", encoding="utf-8")
    git(work, "commit", "-q", "-am", "less")
    # an answer after an unpriced last step; the base is now the commit of the files
    st.update(status="running", pushed=git(work, "rev-parse", "HEAD"))
    st["remote"]["base_sha"] = git(work, "rev-parse", "HEAD~1")
    state.save(cfg, st)
    st = machine.run_next(cfg, personas, "7")
    assert st["status"] == "ready"
    diff = state.load(cfg, "7")["records"]["diff"]
    assert (diff["base"], diff["head"]) == (st["remote"]["base_sha"], st["pushed"])
    assert (diff["files"], diff["added"], diff["deleted"]) == (1, 1, 2)


def test_a_missing_base_sha_records_the_error_and_never_waits(item):
    cfg, personas, r, folder = item
    st = machine.run_next(cfg, personas, "7")
    # the guard scans from the base before the push: the base goes missing after it
    st.update(status="running")
    st["remote"]["base_sha"] = "0" * 40  # not in the repository
    state.save(cfg, st)
    st = machine.run_next(cfg, personas, "7")
    assert (st["stage"], st["status"], st["question"]) == ("delivery", "ready", None)
    diff = st["records"]["diff"]
    assert set(diff) == {"base", "head", "error", "time"} and diff["base"] == "0" * 40
    assert diff["error"] and len(diff["error"]) <= 200 and "\n" not in diff["error"]


@pytest.mark.parametrize("remote,pushed", [(None, "a" * 40), ({"base_sha": "b" * 40}, None)])
def test_no_base_or_no_push_records_the_error(remote, pushed):
    st = {"remote": remote, "pushed": pushed, "work": {"folder": "."}, "records": {}}
    machine.record_diff(st)
    assert st["records"]["diff"]["error"] == "no base sha or no pushed head"


def test_the_diff_is_from_the_merge_base(tmp_path):
    """The base is not an ancestor of pushed: base..pushed would also count d.txt of the side commit as deleted."""
    git(tmp_path, "init", "-q")
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "root")
    (tmp_path / "d.txt").write_text("d\n", encoding="utf-8")
    git(tmp_path, "add", "d.txt")
    git(tmp_path, "commit", "-q", "-m", "side")
    side = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "checkout", "-q", "HEAD~1")
    (tmp_path / "a.txt").write_text("1\n2\n", encoding="utf-8")
    git(tmp_path, "add", "a.txt")
    git(tmp_path, "commit", "-q", "-m", "pushed")
    st = {"remote": {"base_sha": side}, "pushed": git(tmp_path, "rev-parse", "HEAD"), "work": {"folder": tmp_path},
          "records": {}}
    machine.record_diff(st)
    diff = st["records"]["diff"]
    assert (diff["files"], diff["added"], diff["deleted"]) == (1, 2, 0)


def test_git_that_cannot_start_records_the_error(monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("no git\n" + "x" * 300)

    monkeypatch.setattr(machine, "_git", broken)
    st = {"remote": {"base_sha": "b" * 40}, "pushed": "a" * 40, "work": {"folder": "."}, "records": {}}
    machine.record_diff(st)
    assert st["records"]["diff"]["error"] == ("no git " + "x" * 300)[:200]


def test_no_command_writes_the_diff():
    from wb.cli import parser

    assert parser().parse_args(["record", "7", "pr", "--number", "1"]).kind == "pr"
    with pytest.raises(SystemExit):
        parser().parse_args(["record", "7", "diff"])
