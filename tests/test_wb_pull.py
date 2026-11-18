"""`wb pull-answers --queue DIR` answers case 2 waits from the files of the answer forms."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from pathlib import Path

import pytest

from wb import state
from wb.cli import main
from wb_fakes import make_cfg

REPLY = "I built AC 1.\n\nWhich store?\n- A) Keep the file.\nB) Use a table.\nC) Ask later.\n\nBUILD: BLOCKED - the store\n"
TEXT = "The build stopped: BUILD: BLOCKED - the store"
QHASH = hashlib.sha256((TEXT + "\nA) Keep the file.\nB) Use a table.\nC) Ask later.").encode("utf-8")).hexdigest()[:16]
# a key value that no file holds: built at run time
KEY = "-".join(["k3y", "529", hashlib.sha256(b"pull").hexdigest()[:12]])


def waiting(cfg, item_id, reply=REPLY, **question) -> None:
    """An item at a case 2 wait after a blocked build whose reply holds 3 options."""
    folder = state.home(cfg) / item_id / "runs" / "001-builder"
    folder.mkdir(parents=True)
    (folder / "reply.txt").write_text(reply, encoding="utf-8")
    st = state.new(item_id)
    st.update(stage="build", status="waiting", tier="Medium", limits={"usd": 100.0, "hours": 4.0, "rounds": 2},
              question={"case": 2, "text": TEXT, "run_folder": str(folder), **question})
    state.save(cfg, st)


@pytest.fixture
def home(tmp_path):
    cfg, personas = make_cfg(tmp_path)
    waiting(cfg, "b1")
    queue = tmp_path / "queue"
    queue.mkdir()
    return cfg, personas, queue


def put(queue: Path, name: str, data) -> Path:
    path = queue / name
    path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode("utf-8"))
    return path


def good(item_id="b1", option="B") -> dict:
    return {"id": item_id, "qhash": QHASH, "option": option}


def pull(cfg, personas, queue) -> int:
    return main(["pull-answers", "--queue", str(queue)], cfg=cfg, personas=personas)


def state_bytes(cfg, item_id="b1") -> bytes:
    return (state.home(cfg) / item_id / "state.json").read_bytes()


def last_line(capsys) -> str:
    return capsys.readouterr().out.splitlines()[-1]


def test_answers_and_moves(home, capsys):
    cfg, personas, queue = home
    put(queue, "a1.json", good())
    (queue / ".a2.json").write_text("{}", encoding="utf-8")
    (queue / "a3.txt").write_text("{}", encoding="utf-8")
    (queue / "sub").mkdir()
    (queue / "sub" / "a4.json").write_text("{}", encoding="utf-8")
    (queue / "folder.json").mkdir()
    assert pull(cfg, personas, queue) == 0
    assert last_line(capsys) == "PULL: answered=1 refused=0"
    st = state.load(cfg, "b1")
    assert st["status"] == "running" and st["question"] is None and st["stage"] == "build"
    assert [Path(a).read_text(encoding="utf-8") for a in st["answers"]] == ["B) Use a table.\n"]
    assert sorted(p.name for p in (queue / "done").iterdir()) == ["a1.json"]
    assert json.loads((queue / "done" / "a1.json").read_text(encoding="utf-8")) == good()
    assert sorted(p.name for p in queue.iterdir()) == [".a2.json", "a3.txt", "done", "folder.json", "refused", "sub"]
    assert list((queue / "refused").iterdir()) == [] and (queue / "sub" / "a4.json").is_file()


def test_a_file_of_4096_bytes_is_read(home, capsys):
    cfg, personas, queue = home
    put(queue, "a1.json", json.dumps(good()).encode("utf-8").ljust(4096))
    assert pull(cfg, personas, queue) == 0
    assert last_line(capsys) == "PULL: answered=1 refused=0"


def test_a_taken_name_in_done_is_kept(home, capsys):
    cfg, personas, queue = home
    (queue / "done").mkdir()
    for name in ("a1.json", "a1.1.json"):
        (queue / "done" / name).write_text("old", encoding="utf-8")
    put(queue, "a1.json", good())
    assert pull(cfg, personas, queue) == 0
    assert [(queue / "done" / n).read_text(encoding="utf-8") for n in ("a1.json", "a1.1.json")] == ["old", "old"]
    assert json.loads((queue / "done" / "a1.2.json").read_text(encoding="utf-8")) == good()


def set_question(cfg, **fields):
    st = state.load(cfg, "b1")
    st.update(fields)
    state.save(cfg, st)


def refuse_case(cfg, queue, tmp_path, case) -> None:
    """Write the file of a refusal case as queue/x.json, and set the state that the case needs."""
    q = state.load(cfg, "b1")["question"]
    if case == "bad JSON":
        put(queue, "x.json", b'{"id": "b1", "qhash": ')
    elif case == "not UTF-8":
        put(queue, "x.json", b'{"id": "b1\xff"}')
    elif case == "above 4 KB":
        put(queue, "x.json", json.dumps(good()).encode("utf-8").ljust(4097))  # JSON with spaces
    elif case == "a list":
        put(queue, "x.json", [good()])
    elif case == "a scalar":
        put(queue, "x.json", "b1")
    elif case == "missing field":
        put(queue, "x.json", {"id": "b1", "qhash": QHASH})
    elif case == "extra field":
        put(queue, "x.json", {**good(), "note": "x"})
    elif case == "not a string":
        put(queue, "x.json", {**good(), "option": 2})
    elif case == "bad id form":
        put(queue, "x.json", good(item_id="../b1"))
    elif case == "unknown id":
        put(queue, "x.json", good(item_id="b9"))
    elif case == "unreadable state":
        (state.home(cfg) / "b8").mkdir()
        (state.home(cfg) / "b8" / "state.json").write_text('{"version": 99}', encoding="utf-8")
        put(queue, "x.json", good(item_id="b8"))
    elif case == "not waiting":
        set_question(cfg, status="running", question=None)
        put(queue, "x.json", good())
    elif case.startswith("case "):
        set_question(cfg, question={**q, "case": int(case[5:])})
        put(queue, "x.json", good())
    elif case == "spec questions":
        set_question(cfg, stage="spec-check", question={**q, "spec_questions": True})
        put(queue, "x.json", good())
    elif case == "budget":
        set_question(cfg, question={**q, "budget": True})
        put(queue, "x.json", good())
    elif case == "no options":
        (Path(q["run_folder"]) / "reply.txt").write_text("BUILD: BLOCKED - x\n", encoding="utf-8")
        put(queue, "x.json", good())
    elif case == "qhash":
        put(queue, "x.json", {**good(), "qhash": "0" * 16})
    elif case in ("letter D", "letter b", "letter B)"):
        put(queue, "x.json", good(option=case[7:]))
    elif case == "a link":
        target = tmp_path / "target.json"
        target.write_text(json.dumps(good()), encoding="utf-8")
        (queue / "x.json").symlink_to(target)
    else:
        raise AssertionError(case)


REASONS = {
    "bad JSON": "the file is not JSON in UTF-8",
    "not UTF-8": "the file is not JSON in UTF-8",
    "above 4 KB": "the file is above 4096 bytes",
    "a list": "the file is not a JSON object",
    "a scalar": "the file is not a JSON object",
    "missing field": "the fields must be exactly id, qhash and option",
    "extra field": "the fields must be exactly id, qhash and option",
    "not a string": "a field is not a string",
    "bad id form": "the id has a bad form",
    "unknown id": "the id is unknown",
    "unreadable state": "the state of the item cannot be read",
    "not waiting": "item b1 does not wait for the owner (status running)",
    "case 1": "the item waits at case 1, which the owner answers at the host terminal",
    "case 3": "the item waits at case 3, which the owner answers at the host terminal",
    "case 4": "the item waits at case 4, which the owner answers at the host terminal",
    "spec questions": "the spec check asked questions: amend the spec, then run wb intake again",
    "budget": "the budget is spent: raise the limits in the spec, then run wb intake again",
    "no options": "the question has no options",
    "qhash": "the qhash does not match the current question",
    "letter D": "the letter is not an option of the question",
    "letter b": "the letter is not an option of the question",
    "letter B)": "the letter is not an option of the question",
    "a link": "the file is not a regular file",
}


@pytest.mark.parametrize("case", list(REASONS))
def test_refused_files(home, tmp_path, capsys, case):
    if case == "a link" and sys.platform == "win32":
        pytest.skip("a symbolic link needs a privilege on Windows")
    cfg, personas, queue = home
    waiting(cfg, "ok")
    put(queue, "y.json", good(item_id="ok"))  # a good file in the same run, after the bad one
    refuse_case(cfg, queue, tmp_path, case)
    before = state_bytes(cfg) if state.exists(cfg, "b1") else None
    assert pull(cfg, personas, queue) == 0
    assert last_line(capsys) == "PULL: answered=1 refused=1"
    assert state_bytes(cfg) == before
    assert not (queue / "x.json").exists() and not (queue / "x.json").is_symlink()
    assert [p.name for p in (queue / "refused").iterdir()] == ["x.json.reason"]
    assert (queue / "refused" / "x.json.reason").read_text(encoding="utf-8") == REASONS[case] + "\n"
    assert [p.name for p in (queue / "done").iterdir()] == ["y.json"]
    assert state.load(cfg, "ok")["status"] == "running"
    if case == "a link":
        assert (tmp_path / "target.json").is_file()  # only the link goes


def test_a_taken_reason_name_is_kept(home, capsys):
    cfg, personas, queue = home
    (queue / "refused").mkdir()
    (queue / "refused" / "x.json.reason").write_text("old\n", encoding="utf-8")
    put(queue, "x.json", b"not json")
    assert pull(cfg, personas, queue) == 0
    assert (queue / "refused" / "x.json.reason").read_text(encoding="utf-8") == "old\n"
    assert (queue / "refused" / "x.json.1.reason").read_text(encoding="utf-8") == "the file is not JSON in UTF-8\n"


def test_a_file_that_another_run_took_is_skipped(home, capsys, monkeypatch):
    cfg, personas, queue = home
    put(queue, "a1.json", good())
    put(queue, "a2.json", b"not json")
    opened = os.open

    def gone(path, *args, **kwargs):
        if Path(path).name == "a1.json":
            Path(path).unlink()
            raise FileNotFoundError(path)
        return opened(path, *args, **kwargs)
    monkeypatch.setattr(os, "open", gone)
    assert pull(cfg, personas, queue) == 0
    assert last_line(capsys) == "PULL: answered=0 refused=1"
    assert state.load(cfg, "b1")["status"] == "waiting"


def test_second_file_refused(home, capsys):
    cfg, personas, queue = home
    put(queue, "a1.json", good(option="A"))
    put(queue, "a2.json", good(option="C"))
    assert pull(cfg, personas, queue) == 0
    assert last_line(capsys) == "PULL: answered=1 refused=1"
    st = state.load(cfg, "b1")
    assert [Path(a).read_text(encoding="utf-8") for a in st["answers"]] == ["A) Keep the file.\n"]
    assert (queue / "refused" / "a2.json.reason").read_text(encoding="utf-8") == \
        "item b1 does not wait for the owner (status running)\n"


def test_a_file_for_a_newer_question_is_refused(home, capsys):
    """The item waits again, at a new question: the qhash of the old question does not match."""
    cfg, personas, queue = home
    put(queue, "a1.json", good())
    assert pull(cfg, personas, queue) == 0
    set_question(cfg, status="waiting", question={"case": 2, "text": TEXT + " again",
                                                  "run_folder": str(state.home(cfg) / "b1" / "runs" / "001-builder")})
    put(queue, "a2.json", good())
    assert pull(cfg, personas, queue) == 0
    assert last_line(capsys) == "PULL: answered=0 refused=1"
    assert len(state.load(cfg, "b1")["answers"]) == 1


def test_report_line(home, capsys):
    cfg, personas, queue = home
    assert pull(cfg, personas, queue) == 0
    assert capsys.readouterr().out == "PULL: answered=0 refused=0\n"
    put(queue, "a1.json", good())
    put(queue, "a2.json", b"[]")
    assert pull(cfg, personas, queue) == 0
    assert capsys.readouterr().out == "PULL: answered=1 refused=1\n"


@pytest.mark.parametrize("bad", ["missing", "a file"])
def test_missing_queue_exit_2(home, capsys, bad):
    cfg, personas, queue = home
    target = queue / "missing"
    if bad == "a file":
        target.write_text("x", encoding="utf-8")
    assert pull(cfg, personas, target) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("wb: --queue is not a folder: ")


def test_key_never_leaks(home, capsys):
    cfg, personas, queue = home
    put(queue, "k1.json", {**good(), "key": KEY})
    put(queue, "k2.json", ('{"id": "b1", "key": "' + KEY + '", ').encode("utf-8"))  # bad JSON too
    put(queue, "k3.json", {**good(), KEY: "x"})  # a key in a field name
    assert pull(cfg, personas, queue) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines()[-1] == "PULL: answered=0 refused=3"
    assert KEY not in captured.out + captured.err
    for p in [*queue.rglob("*"), *state.home(cfg).rglob("*")]:
        assert not p.is_file() or KEY.encode() not in p.read_bytes(), p
    assert sorted(p.name for p in (queue / "refused").iterdir()) == ["k1.json.reason", "k2.json.reason",
                                                                       "k3.json.reason"]
    assert sorted(p.name for p in queue.iterdir()) == ["done", "refused"]
    assert state.load(cfg, "b1")["status"] == "waiting"


@pytest.mark.parametrize("bad", [{"status": None}, {"question": "x"}, {"answers": None}, "no status"])
def test_a_malformed_state_is_refused_and_the_run_goes_on(home, capsys, bad):
    """Code review: a state that wb pages counts as unreadable must not stop the pass."""
    cfg, personas, queue = home
    waiting(cfg, "ok")
    st = state.load(cfg, "b1")
    if bad == "no status":
        del st["status"]
    else:
        st.update(bad)
    state.save(cfg, st)
    put(queue, "a1.json", good())
    put(queue, "a2.json", good(item_id="ok"))
    assert pull(cfg, personas, queue) == 0
    assert last_line(capsys) == "PULL: answered=1 refused=1"
    assert (queue / "refused" / "a1.json.reason").read_text(encoding="utf-8") == "the state of the item cannot be read\n"


@pytest.mark.parametrize("swap", ["a pipe", "a link", "no read right"])
def test_a_file_that_changes_after_the_lstat_is_refused(home, tmp_path, capsys, monkeypatch, swap):
    """Code review: the open follows no link and waits on no pipe; an error of 1 file does not stop the pass."""
    if sys.platform == "win32":
        pytest.skip("a pipe, a link and a read right are POSIX here")
    cfg, personas, queue = home
    waiting(cfg, "ok")
    put(queue, "a1.json", good())
    put(queue, "a2.json", good(item_id="ok"))
    opened = os.open

    def swapped(path, *args, **kwargs):
        if Path(path).name == "a1.json":
            Path(path).unlink()
            if swap == "a pipe":
                os.mkfifo(path)
            elif swap == "a link":
                target = tmp_path / "target.json"
                target.write_text(json.dumps(good()), encoding="utf-8")
                Path(path).symlink_to(target)
            else:
                raise PermissionError(path)
        return opened(path, *args, **kwargs)
    monkeypatch.setattr(os, "open", swapped)
    assert pull(cfg, personas, queue) == 0
    assert last_line(capsys) == "PULL: answered=1 refused=1"
    reason = "the file is not a regular file" if swap == "a pipe" else "the file cannot be read"
    assert (queue / "refused" / "a1.json.reason").read_text(encoding="utf-8") == reason + "\n"
    assert state.load(cfg, "b1")["status"] == "waiting" and not os.path.lexists(queue / "a1.json")


def overlap(cfg, monkeypatch, first, second) -> tuple:
    """First answers A and waits inside machine.store until second loads the state, or for
    1 s. With the lock second cannot load before the save, so the wait times out. (the 2 results)"""
    from wb import machine

    entered, loaded = threading.Event(), threading.Event()
    store, load = machine.store, state.load

    def slow_store(*args):
        if threading.current_thread().name == "first":
            entered.set()
            loaded.wait(1)
        store(*args)

    def watched_load(*args):
        if threading.current_thread().name == "second":
            loaded.set()
        return load(*args)
    monkeypatch.setattr(machine, "store", slow_store)
    monkeypatch.setattr(state, "load", watched_load)
    results = {}
    one = threading.Thread(name="first", target=lambda: results.update(first=first()))
    one.start()
    assert entered.wait(5)
    two = threading.Thread(name="second", target=lambda: results.update(second=second()))
    two.start()
    one.join(10)
    two.join(10)
    answers = sorted((state.home(cfg) / "b1" / "answers").iterdir())
    assert [p.read_text(encoding="utf-8") for p in answers] == ["A) Keep the file.\n"]
    return results["first"], results["second"]


def pull_of(cfg, folder: Path, option: str):
    """A pass over a new queue in folder with 1 file for b1."""
    from wb import pull as pull_mod

    folder.mkdir()
    put(folder, "a.json", good(option=option))
    return lambda: pull_mod.pull(cfg, str(folder))


def answer_of(cfg, folder: Path, line: str):
    from wb import machine

    file = put(folder, f"answer-{line[0]}.md", line.encode("utf-8"))
    return lambda: machine.answer(cfg, "b1", str(file))


RUNNING = "item b1 does not wait for the owner (status running)"


def test_2_pulls_that_overlap_give_1_answer(home, tmp_path, monkeypatch):
    cfg, personas, queue = home
    assert overlap(cfg, monkeypatch, pull_of(cfg, tmp_path / "q1", "A"), pull_of(cfg, tmp_path / "q2", "C")) == \
        ((1, 0), (0, 1))
    assert (tmp_path / "q2" / "refused" / "a.json.reason").read_text(encoding="utf-8") == RUNNING + "\n"


def test_an_answer_that_overlaps_a_pull_is_refused(home, tmp_path, monkeypatch):
    cfg, personas, queue = home
    assert overlap(cfg, monkeypatch, pull_of(cfg, tmp_path / "q1", "A"), answer_of(cfg, tmp_path, "C) Ask later.\n")) \
        == ((1, 0), RUNNING)


def test_a_pull_that_overlaps_an_answer_is_refused(home, tmp_path, monkeypatch):
    """The mutation probe: answer holds the lock until its save, also as the first."""
    cfg, personas, queue = home
    assert overlap(cfg, monkeypatch, answer_of(cfg, tmp_path, "A) Keep the file.\n"),
                   pull_of(cfg, tmp_path / "q2", "C")) == (None, (0, 1))


def test_an_answer_for_an_unknown_id_makes_no_item_folder(home, tmp_path, capsys):
    """The mutation probe: wb answer takes no lock for an unknown id."""
    cfg, personas, queue = home
    assert main(["answer", "b9", "--file", str(put(tmp_path, "answer.md", b"x"))], cfg=cfg, personas=personas) == 2
    assert capsys.readouterr().err.startswith("wb: no state of item b9: ")
    assert not (state.home(cfg) / "b9").exists()


def test_an_unknown_id_makes_no_item_folder(home, capsys):
    cfg, personas, queue = home
    put(queue, "x.json", good(item_id="b9"))
    assert pull(cfg, personas, queue) == 0
    assert last_line(capsys) == "PULL: answered=0 refused=1"
    assert (queue / "refused" / "x.json.reason").read_text(encoding="utf-8") == "the id is unknown\n"
    assert not (state.home(cfg) / "b9").exists()


@pytest.mark.parametrize("command", ["pull-answers", "answer"])
def test_an_error_of_the_lock_exits_2_and_keeps_the_file(home, tmp_path, capsys, command):
    cfg, personas, queue = home
    (state.home(cfg) / "b1" / "answer.flock").mkdir()  # the open of the lock fails
    before = state_bytes(cfg)
    put(queue, "a1.json", good())
    if command == "answer":
        argv = ["answer", "b1", "--file", str(put(tmp_path, "answer.md", b"B) Use a table.\n"))]
    else:
        argv = ["pull-answers", "--queue", str(queue)]
    assert main(argv, cfg=cfg, personas=personas) == 2
    assert capsys.readouterr().err.startswith("wb: cannot lock the answers of item b1: ")
    assert state_bytes(cfg) == before and (queue / "a1.json").is_file()


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="on Windows the full path has a limit of 260 characters")


def long_name(queue: Path, head: str, fill: str = "x") -> str:
    """A queue name of NAME_MAX bytes that starts with head; built at run time."""
    n = os.pathconf(queue, "PC_NAME_MAX")
    return head + fill * (n - len(head.encode("utf-8")) - len(".json")) + ".json"


@posix_only
def test_a_long_bad_name_gets_a_short_reason_name(home, capsys):
    cfg, personas, queue = home
    put(queue, long_name(queue, "a" * 200), b"not json")
    put(queue, "b.json", good())
    assert pull(cfg, personas, queue) == 0
    assert last_line(capsys) == "PULL: answered=1 refused=1"
    assert [p.name for p in (queue / "refused").iterdir()] == ["a" * 200 + ".json.reason"]
    assert (queue / "refused" / ("a" * 200 + ".json.reason")).read_text(encoding="utf-8") == \
        "the file is not JSON in UTF-8\n"


@posix_only
def test_a_long_good_name_whose_cut_name_is_taken_in_done(home, capsys):
    cfg, personas, queue = home
    (queue / "done").mkdir()
    (queue / "done" / ("a" * 200 + ".json")).write_text("old", encoding="utf-8")
    put(queue, long_name(queue, "a" * 200), good())
    assert pull(cfg, personas, queue) == 0
    assert last_line(capsys) == "PULL: answered=1 refused=0"
    assert (queue / "done" / ("a" * 200 + ".json")).read_text(encoding="utf-8") == "old"
    assert json.loads((queue / "done" / ("a" * 200 + ".1.json")).read_text(encoding="utf-8")) == good()


@posix_only
def test_2_long_names_with_the_same_first_200_bytes_give_2_reason_files(home, capsys):
    cfg, personas, queue = home
    put(queue, long_name(queue, "a" * 200, "x"), b"not json")
    put(queue, long_name(queue, "a" * 200, "y"), b"[]")
    put(queue, "a" * 200 + ".json", b"1")  # 200 bytes: the name stays
    assert pull(cfg, personas, queue) == 0
    assert last_line(capsys) == "PULL: answered=0 refused=3"
    reasons = {p.name: p.read_text(encoding="utf-8") for p in (queue / "refused").iterdir()}
    assert reasons == {"a" * 200 + ".json.reason": "the file is not a JSON object\n",
                       "a" * 200 + ".json.1.reason": "the file is not JSON in UTF-8\n",
                       "a" * 200 + ".json.2.reason": "the file is not a JSON object\n"}


@posix_only
@pytest.mark.parametrize("char", ["\u00e9", "\u20ac", "\U0001f600"])
def test_a_character_across_byte_200_is_not_cut(home, capsys, char):
    cfg, personas, queue = home
    keep = 201 - len(char.encode("utf-8"))  # the last byte of the character is byte 200, the first left out
    put(queue, long_name(queue, "a" * keep + char), b"not json")
    assert pull(cfg, personas, queue) == 0
    names = [os.fsencode(p.name) for p in (queue / "refused").iterdir()]
    assert [n.decode("utf-8") for n in names] == ["a" * keep + ".json.reason"]
