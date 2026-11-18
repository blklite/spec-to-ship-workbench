"""Each answer records its file, the case of the wait and the time."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import test_wb_pull
from wb import state
from wb.cli import main
from wb_fakes import make_cfg


def waiting(cfg, case: int) -> None:
    st = state.load(cfg, "a1") if state.exists(cfg, "a1") else state.new("a1")
    st.update(stage="build", status="waiting", tier="Medium",
              question={"case": case, "text": "Which store?", "run_folder": None})
    state.save(cfg, st)


@pytest.fixture
def cfg(tmp_path):
    cfg, _ = make_cfg(tmp_path)
    return cfg


def answer(cfg, tmp_path, text="Keep the file.") -> int:
    path = tmp_path / "answer.md"
    path.write_text(text, encoding="utf-8")
    return main(["answer", "a1", "--file", str(path)], cfg=cfg, personas={"roles": {}})


def is_utc(text) -> bool:
    return isinstance(text, str) and len(text) == 20 and text.endswith("Z") and text[10] == "T"


def test_a_case_2_and_a_case_4_answer_record_their_case_and_time(cfg, tmp_path, capsys):
    waiting(cfg, 2)
    assert answer(cfg, tmp_path) == 0
    waiting(cfg, 4)
    assert answer(cfg, tmp_path) == 0
    st = state.load(cfg, "a1")
    rec = st["records"]["answers"]
    assert [(r["file"], r["case"]) for r in rec] == [("001.md", 2), ("002.md", 4)]
    assert all(is_utc(r["time"]) and set(r) == {"file", "case", "time"} for r in rec)
    # answers stays a list of path strings, and the prompt reads them
    assert [Path(a).name for a in st["answers"]] == ["001.md", "002.md"]
    assert all(isinstance(a, str) for a in st["answers"])


def test_a_pull_answers_answer_is_recorded(tmp_path, capsys):
    cfg, personas = make_cfg(tmp_path)
    test_wb_pull.waiting(cfg, "b1")
    queue = tmp_path / "queue"
    queue.mkdir()
    test_wb_pull.put(queue, "a1.json", test_wb_pull.good())
    assert test_wb_pull.pull(cfg, personas, queue) == 0
    (rec,) = state.load(cfg, "b1")["records"]["answers"]
    assert (rec["file"], rec["case"]) == ("001.md", 2) and is_utc(rec["time"])


def test_an_old_state_with_2_answers_takes_a_third_as_003(cfg, tmp_path, capsys):
    waiting(cfg, 2)
    path = state.item_dir(cfg, "a1") / "state.json"
    old = json.loads(path.read_text(encoding="utf-8"))
    del old["records"]
    old["answers"] = [str(state.item_dir(cfg, "a1") / "answers" / f"00{n}.md") for n in (1, 2)]
    path.write_text(json.dumps(old), encoding="utf-8")
    assert answer(cfg, tmp_path, "third") == 0
    st = state.load(cfg, "a1")
    assert [Path(a).name for a in st["answers"]] == ["001.md", "002.md", "003.md"]
    assert all(isinstance(a, str) for a in st["answers"])
    assert Path(st["answers"][-1]).read_text(encoding="utf-8") == "third"
    assert [r["file"] for r in st["records"]["answers"]] == ["003.md"]
