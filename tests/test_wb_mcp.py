"""The MCP server over the wb command (wb/mcp.py). JSON-RPC 2.0
over stdio, 1 message for each line; 4 tools, each the wb command of the same
name; no approve tool; each error is 1 line with no path outside the item
folder."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

from conftest import ROOT
from wb import machine, mcp, run, state
from wb.cli import main
from wb_fakes import GOOD_SPEC_TEXT, HEAD, start

GO = "SPEC-CHECK 7: GO"
READY = "BUILD: READY " + HEAD
REWORK = "REVIEW: REWORK - 1 blocking"
CR_CLEAN = "CODE-REVIEW: 0 findings (0 critical, 0 high, 0 medium, 0 low)"
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fake", "version": "1"}}}


@pytest.fixture
def item(tmp_path, monkeypatch):
    return start(tmp_path, monkeypatch)


@pytest.fixture
def spawned(monkeypatch):
    calls = []
    monkeypatch.setattr(run, "spawn", lambda argv, log: calls.append((argv, log)))
    return calls


def client(argv: list[str], messages: list, cwd=ROOT) -> list[dict]:
    """A fake client: the messages through the pipes, 1 for each line; the reply lines."""
    lines = "".join((m if isinstance(m, str) else json.dumps(m)) + "\n" for m in messages)
    p = subprocess.run(argv, input=lines, capture_output=True, text=True, cwd=cwd, timeout=60)
    assert p.returncode == 0, p.stderr
    return [json.loads(x) for x in p.stdout.splitlines()]


def tool(cfg, personas, name, **args) -> tuple[str, bool]:
    reply = mcp.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                        "params": {"name": name, "arguments": args}}, cfg, personas)
    assert reply["id"] == 9 and len(reply["result"]["content"]) == 1
    return reply["result"]["content"][0]["text"], reply["result"]["isError"]


def cli(cfg, personas, capsys, *argv) -> str:
    capsys.readouterr()
    main(list(argv), cfg=cfg, personas=personas)
    return capsys.readouterr().out.rstrip("\n")


def snapshot(cfg) -> tuple:
    folder = state.item_dir(cfg, "7")
    return (folder / "state.json").read_bytes(), sorted(p.name for p in (folder / "answers").glob("*"))


def wait_at(item, case):
    cfg, personas, r = item
    if case == 1:
        st = state.load(cfg, "7")
        st["usd"] = 40.0
        state.save(cfg, st)
    else:
        r.add("spec-checker", GO).add("builder", READY).add("reviewer", REWORK).add("second-reviewer", CR_CLEAN)
        r.add("rework", "REWORK: DONE")
    st = machine.run_next(cfg, personas, "7")
    assert (st["status"], st["question"]["case"]) == ("waiting", case)
    return cfg, personas


# AC 1: the server


def test_initialize_gives_name_and_version():
    replies = client([sys.executable, "-m", "wb.mcp"],
                     [INIT, {"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert len(replies) == 1  # a notification gets no reply
    assert replies[0]["id"] == 1 and replies[0]["result"]["serverInfo"]["name"] == "wb"
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in replies[0]["result"]["capabilities"]


def test_unknown_method_gives_jsonrpc_error():
    replies = client([sys.executable, "-m", "wb.mcp"],
                     [{"jsonrpc": "2.0", "id": 2, "method": "foo/bar"}, "{not json", "[]",
                      {"jsonrpc": "2.0", "method": "foo/baz"}, {"jsonrpc": "2.0", "id": 3, "method": "ping"}])
    assert [(r["id"], r["error"]["code"]) for r in replies] == [(2, -32601), (None, -32700), (None, -32600),
                                                                 (3, -32601)]
    assert not any("result" in r for r in replies)


def test_an_unknown_protocol_version_gets_the_newest():
    reply = mcp.handle({**INIT, "params": {"protocolVersion": "1999-01-01"}})
    assert reply["result"]["protocolVersion"] == mcp.VERSIONS[0]


@pytest.mark.parametrize("name", ["wb_approve", ["wb_status"], {"a": 1}, None])
def test_an_unknown_tool_is_a_jsonrpc_error(name):
    reply = mcp.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": name}})
    assert reply["error"]["code"] == -32602 and "result" not in reply


# AC 2: 4 tools


def test_tools_list_is_exactly_the_4_tools():
    tools = mcp.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/list"})["result"]["tools"]
    assert {t["name"]: t["inputSchema"]["required"] for t in tools} == {
        "wb_status": [], "wb_intake": ["id", "spec"], "wb_answer": ["id", "answer"], "wb_run_next": ["id"]}
    for t in tools:
        assert t["inputSchema"]["type"] == "object" and t["description"]
        optional = {"repo"} if t["name"] == "wb_intake" else set()
        assert set(t["inputSchema"]["properties"]) == {"id", *t["inputSchema"]["required"], *optional}


# AC 3: the same behavior as the CLI


def test_wb_status_text_equals_cli(item, capsys):
    cfg, personas, _ = item
    assert tool(cfg, personas, "wb_status") == (cli(cfg, personas, capsys, "status"), False)
    assert tool(cfg, personas, "wb_status", id="7") == (cli(cfg, personas, capsys, "status", "7"), False)


def test_wb_intake_text_equals_cli(tmp_path, monkeypatch, capsys):
    from wb_fakes import make_cfg
    cfg, personas = make_cfg(tmp_path)
    spec = tmp_path / "spec.md"
    spec.write_text(GOOD_SPEC_TEXT, encoding="utf-8", newline="\n")
    assert tool(cfg, personas, "wb_intake", id="8", spec=GOOD_SPEC_TEXT) == ("WB: INTAKE OK 8", False)
    assert cli(cfg, personas, capsys, "intake", str(spec), "--id", "9") == "WB: INTAKE OK 9"
    st8, st9 = state.load(cfg, "8"), state.load(cfg, "9")
    assert st8["spec"]["sha256"] == st9["spec"]["sha256"] and st8["stage"] == st9["stage"] == "spec-check"
    stored = state.item_dir(cfg, "8") / "specs"
    assert st8["spec"]["path"].startswith(str(stored) + os.sep)
    assert open(st8["spec"]["path"], encoding="utf-8").read() == GOOD_SPEC_TEXT


def test_wb_answer_text_equals_cli(item, tmp_path, monkeypatch, capsys):
    cfg, personas, r = item
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    r.add("spec-checker", GO).add("builder", "BUILD: BLOCKED - which colour?")
    machine.run_next(cfg, personas, "7")
    saved = snapshot(cfg)
    assert tool(cfg, personas, "wb_answer", id="7", answer="Blue.\n") == ("WB: ANSWER OK 7", False)
    st = state.load(cfg, "7")
    assert st["status"] == "running" and open(st["answers"][0], encoding="utf-8").read() == "Blue.\n"
    assert list(tmp.iterdir()) == []  # the temp file of the answer is gone
    (state.item_dir(cfg, "7") / "state.json").write_bytes(saved[0])  # the same state again, for the CLI
    answer = tmp_path / "answer.md"
    answer.write_text("Blue.\n", encoding="utf-8")
    assert cli(cfg, personas, capsys, "answer", "7", "--file", str(answer)) == "WB: ANSWER OK 7"


def test_wb_run_next_text_equals_cli(item, spawned, capsys):
    cfg, personas, r = item
    text, is_error = tool(cfg, personas, "wb_run_next", id="7")
    log = run.log_path(cfg, "7")
    assert (text, is_error) == (f"WB: STARTED 7 - log {log}", False)
    assert text == cli(cfg, personas, capsys, "run-next", "7")
    assert [argv for argv, _ in spawned] == [[sys.executable, "-m", "wb", "run-next", "7", "--foreground"]] * 2
    assert r.calls == []  # it returns at once: no step ran here
    st = state.load(cfg, "7")
    st.update(status="ready")
    state.save(cfg, st)
    assert tool(cfg, personas, "wb_run_next", id="7") == ("WB: READY 7", False)
    assert cli(cfg, personas, capsys, "run-next", "7") == "WB: READY 7"
    assert len(spawned) == 2  # not running: no child


def test_cli_output_does_not_reach_the_protocol_channel(item, capsys):
    cfg, personas, _ = item
    out = io.StringIO()
    mcp.serve(io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": "wb_status", "arguments": {"id": "7"}}}) + "\n"),
              out, cfg, personas)
    assert len(out.getvalue().splitlines()) == 1 and capsys.readouterr().out == ""


# AC 4: no approval path


def test_no_tool_name_holds_approve():
    names = [t["name"] for t in mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]]
    assert not [n for n in names if any(w in n for w in ("approve", "merge", "shell", "exec"))]


def test_answer_on_budget_stop_is_error_and_state_unchanged(item):
    cfg, personas = wait_at(item, 1)
    before = snapshot(cfg)
    text, is_error = tool(cfg, personas, "wb_answer", id="7", answer="go on")
    assert is_error and text == ("WB: ANSWER REFUSE 7 - the budget is spent: raise the limits in the spec,"
                                 " then run wb intake again")
    assert snapshot(cfg) == before


def test_answer_on_a_running_item_is_error_and_state_unchanged(item):
    cfg, personas, _ = item
    text, is_error = tool(cfg, personas, "wb_answer", id="7", answer="go on")
    assert is_error and text.startswith("WB: ANSWER REFUSE 7 - item 7 does not wait")
    assert state.load(cfg, "7")["status"] == "running" and not (state.item_dir(cfg, "7") / "answers").exists()


def test_answer_on_a_high_tier_wait_is_error(tmp_path, monkeypatch):
    high = GOOD_SPEC_TEXT.replace("**Tier:** Medium", "**Tier:** High")
    cfg, personas, r = start(tmp_path, monkeypatch, spec_text=high)
    r.add("spec-checker", GO)
    assert machine.run_next(cfg, personas, "7")["question"]["case"] == 3
    before = (state.item_dir(cfg, "7") / "state.json").read_bytes()
    assert tool(cfg, personas, "wb_answer", id="7", answer="approved") == (
        "WB: ANSWER REFUSE 7 - case 3 waits for wb approve 7 high", True)
    assert (state.item_dir(cfg, "7") / "state.json").read_bytes() == before


def test_answer_on_a_round_limit_wait_is_error_and_state_unchanged(item):
    cfg, personas = wait_at(item, 4)
    before = snapshot(cfg)
    text, is_error = tool(cfg, personas, "wb_answer", id="7", answer="merge it anyway")
    assert is_error and text == ("WB: ANSWER REFUSE 7 - case 4 (the round limit) waits for the owner"
                                 " at the host terminal")
    assert snapshot(cfg) == before


def specs(cfg) -> list[str]:
    return sorted(p.name for p in (state.item_dir(cfg, "7") / "specs").glob("*"))


def assert_intake_refused(cfg, personas, spec, case, why):
    before, files = snapshot(cfg), specs(cfg)
    assert tool(cfg, personas, "wb_intake", id="7", spec=spec) == (
        f"WB: INTAKE REFUSE 7 - case {case} ({why}) waits for the owner at the host terminal", True)
    assert snapshot(cfg) == before and specs(cfg) == files


def test_intake_on_budget_stop_is_error_and_state_unchanged(item):
    cfg, personas = wait_at(item, 1)  # a front end must not raise the limits
    assert_intake_refused(cfg, personas, GOOD_SPEC_TEXT.replace("$40", "$4000"), 1, "the budget stop")


def test_intake_on_a_high_tier_wait_is_error_and_state_unchanged(tmp_path, monkeypatch):
    high = GOOD_SPEC_TEXT.replace("**Tier:** Medium", "**Tier:** High")
    cfg, personas, r = start(tmp_path, monkeypatch, spec_text=high)
    r.add("spec-checker", GO)
    assert machine.run_next(cfg, personas, "7")["question"]["case"] == 3
    assert_intake_refused(cfg, personas, GOOD_SPEC_TEXT, 3, "the High tier wait")  # Low or Medium: no approval


def test_intake_on_a_round_limit_wait_is_error_and_state_unchanged(item):
    cfg, personas = wait_at(item, 4)
    assert_intake_refused(cfg, personas, GOOD_SPEC_TEXT, 4, "the round limit")


def test_intake_on_a_case_2_wait_amends_the_spec(item):
    cfg, personas, r = item
    r.add("spec-checker", "SPEC-CHECK 7: QUESTIONS - 1 questions")
    assert machine.run_next(cfg, personas, "7")["question"]["case"] == 2
    amended = GOOD_SPEC_TEXT + "\nThe answer.\n"
    assert tool(cfg, personas, "wb_intake", id="7", spec=amended) == ("WB: INTAKE OK 7", False)
    st = state.load(cfg, "7")
    assert (st["status"], st["stage"]) == ("running", "spec-check")
    assert open(st["spec"]["path"], encoding="utf-8").read() == amended


# AC 5: errors


def assert_one_line(text, cfg, item_id=None):
    assert "\n" not in text and "Traceback" not in text and text
    if item_id:
        text = text.replace(str(state.item_dir(cfg, item_id)), "")
    for outside in (str(ROOT), cfg["state"]["home"], cfg["work"]["home"], os.path.expanduser("~")):
        assert outside not in text


@pytest.mark.parametrize("bad", ["../x", "-x", "a/b", "", "x" * 65])
@pytest.mark.parametrize("name", ["wb_status", "wb_intake", "wb_answer", "wb_run_next"])
def test_bad_id_is_error(item, name, bad):
    cfg, personas, _ = item
    args = {"id": bad, "spec": GOOD_SPEC_TEXT, "answer": "x"}
    args = {k: v for k, v in args.items() if k in mcp.SCHEMAS[name]["properties"]}
    text, is_error = tool(cfg, personas, name, **args)
    assert is_error and text.startswith("wb: bad item id")
    assert_one_line(text, cfg)
    assert sorted(p.name for p in state.home(cfg).iterdir()) == ["7"]


@pytest.mark.parametrize("name,args,why", [
    ("wb_intake", {"id": "8"}, "wb_intake: the argument spec is missing"),
    ("wb_answer", {"answer": "x"}, "wb_answer: the argument id is missing"),
    ("wb_run_next", {}, "wb_run_next: the argument id is missing"),
    ("wb_status", {"id": 7}, "wb_status: the argument id must be a string"),
    ("wb_run_next", {"id": "7", "foreground": "yes"}, "wb_run_next: unknown argument foreground"),
])
def test_missing_key_is_error(item, spawned, name, args, why):
    cfg, personas, _ = item
    before = (state.item_dir(cfg, "7") / "state.json").read_bytes()
    assert tool(cfg, personas, name, **args) == (why, True)
    assert (state.item_dir(cfg, "7") / "state.json").read_bytes() == before and spawned == []


def test_null_arguments_are_no_arguments(item, capsys):
    cfg, personas, _ = item
    reply = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "wb_status", "arguments": None}}, cfg, personas)
    assert reply["result"]["content"][0]["text"] == cli(cfg, personas, capsys, "status")


def test_arguments_that_are_not_an_object_are_an_error(item):
    cfg, personas, _ = item
    reply = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "wb_status", "arguments": ["7"]}}, cfg, personas)
    assert reply["result"] == {"content": [{"type": "text", "text": "the arguments must be an object"}],
                               "isError": True}


def test_refused_intake_is_error(item):
    cfg, personas, _ = item
    bad = GOOD_SPEC_TEXT.replace("## Non-goals", "## Other").replace("**Tier:** Medium", "**Tier:** Huge")
    text, is_error = tool(cfg, personas, "wb_intake", id="8", spec=bad)
    assert is_error and text == ("WB: INTAKE REFUSE 8 - 2 problems: a section is missing: ## Non-goals;"
                                 " a bad tier: the Tier line must start with Low, Medium or High")
    assert not state.exists(cfg, "8")
    assert_one_line(text, cfg)


def test_a_refused_amendment_keeps_the_accepted_spec(item):
    cfg, personas, _ = item  # item 7 is running: a new intake is refused
    path = state.load(cfg, "7")["spec"]["path"]
    text, is_error = tool(cfg, personas, "wb_intake", id="7", spec=GOOD_SPEC_TEXT + "\nmore\n")
    assert is_error and text.startswith("WB: INTAKE REFUSE 7 - 1 problems: item 7 exists and does not wait")
    st = state.load(cfg, "7")
    assert st["spec"]["path"] == path
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == st["spec"]["sha256"]


def test_a_refused_amendment_of_a_waiting_item_keeps_its_spec_file(tmp_path):
    from wb_fakes import make_cfg
    cfg, personas = make_cfg(tmp_path)
    assert tool(cfg, personas, "wb_intake", id="8", spec=GOOD_SPEC_TEXT) == ("WB: INTAKE OK 8", False)
    st = state.load(cfg, "8")
    st.update(status="waiting", question={"case": 2, "text": "x", "run_folder": None})
    state.save(cfg, st)
    text, is_error = tool(cfg, personas, "wb_intake", id="8", spec=GOOD_SPEC_TEXT.replace("## Story", "## Tale"))
    assert is_error and text.startswith("WB: INTAKE REFUSE 8 - 1 problems: a section is missing: ## Story")
    assert open(st["spec"]["path"], encoding="utf-8").read() == GOOD_SPEC_TEXT


def test_a_missing_item_gives_no_path_outside_its_folder(item):
    cfg, personas, _ = item
    text, is_error = tool(cfg, personas, "wb_status", id="9")
    assert is_error and text.startswith("wb: no state of item 9: ")
    assert_one_line(text, cfg, "9")


def test_a_config_error_gives_no_path(tmp_path):
    text, is_error = mcp.call("wb_status", {}, cfg=tmp_path / "nothing.toml", personas={})
    assert is_error and "\n" not in text and str(tmp_path) not in text and "<path>" in text


def test_an_internal_error_gives_no_traceback(item, monkeypatch, capsys):
    cfg, personas, _ = item
    from wb import status
    monkeypatch.setattr(status, "detail", lambda st: 1 / 0)
    text, is_error = tool(cfg, personas, "wb_status", id="7")
    assert is_error and text == "wb: wb_status failed: an internal error (the detail is on the stderr of the server)"
    assert "ZeroDivisionError" in capsys.readouterr().err


@pytest.mark.parametrize("code,stream", [(1, "out"), (2, "err")])
def test_a_command_error_is_1_line_with_no_outside_path(item, monkeypatch, code, stream):
    cfg, personas, _ = item
    outside = os.path.join(cfg["work"]["home"], "x")

    def fail(argv, cfg, personas):
        print(f"cannot read {outside}\nWB: STATUS REFUSE 7", file=getattr(sys, "std" + stream))
        return code
    monkeypatch.setattr(mcp.cli, "main", fail)
    text, is_error = tool(cfg, personas, "wb_status", id="7")
    assert is_error and text == "WB: STATUS REFUSE 7: cannot read <path>"
    assert_one_line(text, cfg, "7")


def test_paths_outside_the_item_folder_are_replaced(item):
    cfg, _, _ = item
    keep = str(state.item_dir(cfg, "7"))
    text = f"cannot read {keep}{os.sep}state.json:\n  [Errno 2] '/etc/x' in {keep}x, rounds 0/2 (D:\\x\\y)"
    assert mcp._one_line(text, cfg, "7") == (f"cannot read {keep}{os.sep}state.json: [Errno 2] '<path>' in <path>,"
                                             " rounds 0/2 (<path>)")


# a quoted path is redacted whole


@pytest.mark.parametrize("quoted", ["'/srv/shared space/private/config.toml'",
                                    '"/srv/shared space/private/config.toml"',
                                    "'C:\\srv\\shared space\\private\\config.toml'"])
def test_a_quoted_path_is_redacted_whole(item, quoted):
    cfg, _, _ = item
    text = mcp._one_line(f"cannot read {quoted}", cfg, "7")
    assert text == f"cannot read {quoted[0]}<path>{quoted[0]}"
    assert not [w for w in ("space", "private", "config.toml") if w in text]


def test_a_quoted_path_in_the_item_folder_is_kept(item):
    cfg, _, _ = item
    spec = str(state.item_dir(cfg, "7") / "specs" / "a b.md")
    assert mcp._one_line(f"cannot read '{spec}'", cfg, "7") == f"cannot read '{spec}'"


def test_a_quote_that_starts_in_the_item_folder_hides_each_other_path(item):
    cfg, _, _ = item
    spec = str(state.item_dir(cfg, "7") / "specs" / "a.md")
    assert mcp._one_line(f"cannot move '{spec} to /srv/private/x'", cfg, "7") == f"cannot move '{spec} to <path>'"


def test_an_unquoted_path_still_ends_at_white_space(item):
    cfg, _, _ = item
    assert mcp._one_line("cannot read /x y and '/z w", cfg, "7") == "cannot read <path> y and '<path> w"


# an item folder path with a space in it


def spaced(item) -> dict:
    """The item's config with the state home in a folder that has a space in its name."""
    cfg, _, _ = item
    return {**cfg, "state": {**cfg["state"], "home": str(state.home(cfg).parent / "shared space" / "state")}}


def test_a_quoted_path_in_an_item_folder_with_a_space_is_kept(item):
    cfg = spaced(item)
    text = f"cannot read '{state.item_dir(cfg, '7') / 'specs' / 'a.md'}'"
    assert " " in str(state.item_dir(cfg, "7")) and mcp._one_line(text, cfg, "7") == text


def test_a_kept_quote_with_a_space_hides_each_other_path(item):
    cfg = spaced(item)
    spec = str(state.item_dir(cfg, "7") / "specs" / "a.md")
    text = mcp._one_line(f"cannot read '{spec} /etc/passwd'", cfg, "7")
    assert text == f"cannot read '{spec} <path>'" and "/etc/passwd" not in text


def test_a_sibling_folder_with_a_space_is_not_kept(item):
    cfg = spaced(item)
    text = mcp._one_line(f"cannot read '{state.home(cfg) / '70' / 'x'}'", cfg, "7")
    assert text == "cannot read '<path>'" and f"state{os.sep}70" not in text


def test_a_sibling_folder_with_a_space_in_a_kept_quote_is_not_kept(item):
    cfg = spaced(item)
    spec, sibling = state.item_dir(cfg, "7") / "specs" / "a.md", state.home(cfg) / "70" / "x"
    text = mcp._one_line(f"cannot move '{spec} to {sibling}'", cfg, "7")
    assert text == f"cannot move '{spec} to <path>'" and f"state{os.sep}70" not in text


# the size limit is checked before the write


def padded(text: str, size: int) -> str:
    """The text with a Story line that makes it size bytes long."""
    return text.replace("## Rulings", "x" * (size - len(text.encode("utf-8")) - 2) + "\n\n## Rulings", 1)


def cli_intake(cfg, personas, capsys, tmp_path, text, item_id) -> str:
    """The text of a refused CLI intake, joined as a tool joins it."""
    spec = tmp_path / "cli-spec.md"
    spec.write_text(text, encoding="utf-8", newline="\n")
    *problems, last = cli(cfg, personas, capsys, "intake", str(spec), "--id", item_id).splitlines()
    return last + ": " + "; ".join(problems)


def test_an_oversized_spec_writes_no_file(tmp_path, capsys):
    from wb_fakes import make_cfg
    cfg, personas = make_cfg(tmp_path)
    big = padded(GOOD_SPEC_TEXT, cfg["specs"]["max_kb"] * 1024 + 1)
    text, is_error = tool(cfg, personas, "wb_intake", id="8", spec=big)
    assert is_error and text == "WB: INTAKE REFUSE 8 - 1 problems: the spec is 8.0 KB, above the limit of 8 KB"
    assert text == cli_intake(cfg, personas, capsys, tmp_path, big, "8")
    assert not (state.item_dir(cfg, "8") / "specs").exists() and not state.exists(cfg, "8")  # never written


def test_an_oversized_spec_gives_each_problem_as_the_cli(item, tmp_path, capsys):
    cfg, personas, _ = item  # item 7 is running: a new intake is refused
    big = padded(GOOD_SPEC_TEXT.replace("## Non-goals", "## Other"), cfg["specs"]["max_kb"] * 1024 + 1)
    files = specs(cfg)
    text, is_error = tool(cfg, personas, "wb_intake", id="7", spec=big)
    assert is_error and text.startswith("WB: INTAKE REFUSE 7 - 3 problems: the spec is 8.0 KB")
    assert text == cli_intake(cfg, personas, capsys, tmp_path, big, "7")
    assert specs(cfg) == files


def test_a_spec_of_exactly_max_kb_reaches_the_check(tmp_path):
    from wb_fakes import make_cfg
    cfg, personas = make_cfg(tmp_path)
    assert tool(cfg, personas, "wb_intake", id="8", spec=padded(GOOD_SPEC_TEXT, cfg["specs"]["max_kb"] * 1024)) == (
        "WB: INTAKE OK 8", False)


# a refused intake leaves no new file


def test_a_refused_intake_removes_its_spec_file(tmp_path):
    from wb_fakes import make_cfg
    cfg, personas = make_cfg(tmp_path)
    text, is_error = tool(cfg, personas, "wb_intake", id="8", spec=GOOD_SPEC_TEXT.replace("## Non-goals", "## Other"))
    assert is_error and text == "WB: INTAKE REFUSE 8 - 1 problems: a section is missing: ## Non-goals"
    assert not list((state.item_dir(cfg, "8") / "specs").glob("*"))


def test_an_intake_that_fails_removes_its_spec_file(tmp_path, monkeypatch, capsys):
    from wb_fakes import make_cfg
    cfg, personas = make_cfg(tmp_path)
    monkeypatch.setattr(mcp.cli, "main", lambda argv, cfg, personas: 1 / 0)
    text, is_error = tool(cfg, personas, "wb_intake", id="8", spec=GOOD_SPEC_TEXT)
    assert is_error and text == "wb: wb_intake failed: an internal error (the detail is on the stderr of the server)"
    assert not list((state.item_dir(cfg, "8") / "specs").glob("*"))


def test_an_error_after_the_state_is_saved_keeps_the_spec_it_names(tmp_path, monkeypatch):
    from wb import tracker
    from wb_fakes import make_cfg
    cfg, personas = make_cfg(tmp_path)

    def fail(self, item_id, title, item_state):
        raise mcp.UsageError(f"the tracker already has item {item_id}")
    monkeypatch.setattr(tracker.LocalTracker, "create", fail)
    text, is_error = tool(cfg, personas, "wb_intake", id="8", spec=GOOD_SPEC_TEXT)
    assert is_error and text == "wb: the tracker already has item 8"
    st = state.load(cfg, "8")
    assert hashlib.sha256(open(st["spec"]["path"], "rb").read()).hexdigest() == st["spec"]["sha256"]


def test_the_same_spec_sent_twice_keeps_its_file(tmp_path):
    from wb_fakes import make_cfg
    cfg, personas = make_cfg(tmp_path)
    assert tool(cfg, personas, "wb_intake", id="8", spec=GOOD_SPEC_TEXT) == ("WB: INTAKE OK 8", False)
    text, is_error = tool(cfg, personas, "wb_intake", id="8", spec=GOOD_SPEC_TEXT)
    assert is_error and text.startswith("WB: INTAKE REFUSE 8 - 1 problems: item 8 exists and does not wait")
    st = state.load(cfg, "8")
    assert hashlib.sha256(open(st["spec"]["path"], "rb").read()).hexdigest() == st["spec"]["sha256"]


def test_an_old_spec_sent_again_keeps_its_file(tmp_path):
    from wb_fakes import make_cfg
    cfg, personas = make_cfg(tmp_path)
    assert tool(cfg, personas, "wb_intake", id="8", spec=GOOD_SPEC_TEXT) == ("WB: INTAKE OK 8", False)
    old = state.load(cfg, "8")["spec"]["path"]
    st = state.load(cfg, "8")
    st.update(status="waiting", question={"case": 2, "text": "x", "run_folder": None})
    state.save(cfg, st)
    assert tool(cfg, personas, "wb_intake", id="8", spec=GOOD_SPEC_TEXT + "\nmore\n") == ("WB: INTAKE OK 8", False)
    text, is_error = tool(cfg, personas, "wb_intake", id="8", spec=GOOD_SPEC_TEXT)  # running now: refused
    assert is_error and state.load(cfg, "8")["spec"]["path"] != old
    assert open(old, encoding="utf-8").read() == GOOD_SPEC_TEXT


@pytest.mark.skipif(os.name != "posix", reason="a symbolic link")
def test_a_resolved_path_in_the_item_folder_is_kept(item):
    cfg, _, _ = item
    linked = state.home(cfg).parent / "linked"
    linked.symlink_to(state.home(cfg))
    cfg = {**cfg, "state": {**cfg["state"], "home": str(linked)}}  # intake names the spec by its resolved path
    resolved = str(state.item_dir(cfg, "7").resolve() / "specs" / "a.md")
    assert resolved != str(state.item_dir(cfg, "7") / "specs" / "a.md")
    assert mcp._one_line(f"x {resolved}", cfg, "7") == f"x {resolved}"


# AC 6: the entry script


@pytest.mark.skipif(os.name != "posix" or shutil.which("python3") is None, reason="a POSIX host with python3")
def test_wb_mcp_script_answers_initialize(tmp_path):
    script = ROOT / "scripts" / "wb-mcp"
    assert len(script.read_bytes().splitlines()) == 3 and b"\r" not in script.read_bytes()
    assert "exec python3 -m wb.mcp" in script.read_text(encoding="utf-8")
    link = tmp_path / "bin" / "wb-mcp"  # as ~/.local/bin/wb-mcp, run from outside the clone
    link.parent.mkdir()
    link.symlink_to(script)
    reply, = client([str(link)], [INIT], cwd=tmp_path)  # ssh runs it so: the shebang
    assert reply["id"] == 1 and reply["result"]["serverInfo"]["name"] == "wb"


@pytest.mark.skipif(os.name != "posix", reason="a POSIX host with bash")
def test_wb_mcp_script_puts_local_bin_first_on_path(tmp_path):
    """ssh gives a PATH without ~/.local/bin, where claude lives."""
    local_bin = tmp_path / "home" / ".local" / "bin"
    local_bin.mkdir(parents=True)
    stub = local_bin / "python3"
    stub.write_bytes(b'#!/bin/sh\necho "$PATH"\ncommand -v claude\npwd\n')
    (local_bin / "claude").write_bytes(b"#!/bin/sh\n")
    for f in (stub, local_bin / "claude"):
        f.chmod(0o755)
    link = tmp_path / "other" / "wb-mcp"  # the installer's form: a link in another folder
    link.parent.mkdir()
    link.symlink_to(ROOT / "scripts" / "wb-mcp")
    p = subprocess.run([str(link)], capture_output=True, text=True, cwd=tmp_path, timeout=60,
                       env={"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"})
    path, claude, cwd = p.stdout.splitlines()
    assert path.split(":") == [str(local_bin), "/usr/bin", "/bin"]
    assert claude == str(local_bin / "claude")
    assert os.path.samefile(cwd, ROOT)


# the repository of an item


def test_the_intake_schema_has_repo_as_optional():
    schema = mcp.SCHEMAS["wb_intake"]
    assert schema["properties"]["repo"]["type"] == "string" and "repo" not in schema["required"]
    assert schema["required"] == ["id", "spec"]


def test_wb_intake_with_repo_gives_the_state_of_the_cli(tmp_path, capsys):
    from wb_fakes import make_cfg
    cfg, personas = make_cfg(tmp_path)
    cfg["repos"] = {"widget": "https://github.com/example/widget.git"}
    spec = tmp_path / "spec.md"
    spec.write_text(GOOD_SPEC_TEXT, encoding="utf-8", newline="\n")
    assert tool(cfg, personas, "wb_intake", id="8", spec=GOOD_SPEC_TEXT, repo="widget") == ("WB: INTAKE OK 8", False)
    assert cli(cfg, personas, capsys, "intake", str(spec), "--id", "9", "--repo", "widget") == "WB: INTAKE OK 9"
    w8, w9 = state.load(cfg, "8")["work"], state.load(cfg, "9")["work"]
    assert (w8["repo"], w8["url"]) == (w9["repo"], w9["url"]) == ("widget", "https://github.com/example/widget.git")
    text, is_error = tool(cfg, personas, "wb_intake", id="10", spec=GOOD_SPEC_TEXT, repo="nope")
    assert is_error and text == ("WB: INTAKE REFUSE 10 - 1 problems: unknown repository 'nope':"
                                 " the config [repos] names widget")
    assert not state.exists(cfg, "10")


@pytest.mark.parametrize("bad", ["-x", "a b", "../x", "", "a;b", "--id=9"])
def test_wb_intake_with_a_bad_repo_is_an_error_with_no_state(tmp_path, bad):
    from wb_fakes import make_cfg
    cfg, personas = make_cfg(tmp_path)
    cfg["repos"] = {"widget": "https://github.com/example/widget.git"}
    text, is_error = tool(cfg, personas, "wb_intake", id="8", spec=GOOD_SPEC_TEXT, repo=bad)
    assert is_error and text.startswith("wb_intake: bad repo ")
    assert not state.item_dir(cfg, "8").exists()
