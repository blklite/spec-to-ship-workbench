"""`wb pages --out DIR`, read-only HTML pages of the state and
the results."""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import socket
import stat
import sys
import tomllib
from html.parser import HTMLParser
from pathlib import Path

import pytest

from conftest import ROOT

from wb import charts, pages, state, theme
from wb.cli import main
from wb_fakes import make_cfg

BUILT = "2026-11-01T12:00:00Z"
BUILT_AT = f'<time datetime="{BUILT}">Nov 1, 12:00 PM UTC</time>'  # with no pages.time_zone
THEME_ID = "fake.theme_key-1"  # the theme key of pages.theme_key; the real one is private config


def item(cfg, item_id, stage, status, **fields):
    st = state.new(item_id)
    st.update({"stage": stage, "status": status, "tier": "Medium",
               "limits": {"usd": 100.0, "hours": 4.0, "rounds": 2}, **fields})
    state.save(cfg, st)
    return st


@pytest.fixture
def home(tmp_path, monkeypatch):
    """5 item folders: 2 running at build, 1 waiting at spec check, 1 ready at
    delivery, and 1 whose state is not JSON."""
    monkeypatch.setattr(pages, "_utc", lambda: BUILT)
    cfg, personas = make_cfg(tmp_path)
    item(cfg, "3", "build", "running", rounds_used=1, usd=2.5, seconds=1800.0)
    item(cfg, "10", "build", "running", usd=7.25, seconds=3600.0)
    item(cfg, "2", "spec-check", "waiting", question={"case": 2, "text": "Which colour?", "run_folder": None})
    item(cfg, "4", "delivery", "ready", rounds_used=2, usd=40.0, seconds=7200.0)
    bad = state.home(cfg) / "5"
    bad.mkdir()
    (bad / "state.json").write_text("{not json", encoding="utf-8")
    return cfg, personas


def run(cfg, personas, out, key=THEME_ID) -> int:
    return main(["pages", "--out", str(out)], cfg={**cfg, "pages": {"theme_key": key}}, personas=personas)


def read(out, name="index.html") -> str:
    return (out / name).read_text(encoding="utf-8")


def tile(name: str, count: str) -> str:
    """A count tile of the state page."""
    return f"<li><span>{name}</span> {count}</li>"


def badge(text: str) -> str:
    """A Stage or Status cell of a table."""
    return f'<td><span class="badge">{text}</span></td>'


def test_the_state_page_counts_and_links(home, tmp_path):
    out = tmp_path / "pages"
    assert run(*home, out) == 0
    text = read(out)
    assert f"<p>Built at {BUILT_AT}.</p>" in text
    assert "<p>Items: 5</p>" in text
    for line in (tile("running", '<a class="count" href="status-running.html">2</a>'),
                 tile("waiting", '<a class="count" href="status-waiting.html">1</a>'),
                 tile("ready", '<a class="count" href="status-ready.html">1</a>'),
                 tile("unreadable", '<span class="count">1</span>'),
                 tile("spec-check", '<a class="count" href="stage-spec-check.html">1</a>'),
                 tile("build", '<a class="count" href="stage-build.html">2</a>'),
                 tile("review", '<span class="count">0</span>'), tile("rework", '<span class="count">0</span>'),
                 tile("delivery", '<a class="count" href="stage-delivery.html">1</a>')):
        assert line in text, line
    for link in ("status-running", "status-waiting", "status-ready", "stage-spec-check", "stage-build",
                 "stage-delivery"):
        assert (out / f"{link}.html").is_file(), link


def rows(text: str) -> list[str]:
    return [r for r in text.split("\n") if r.startswith("<tr><td>")]


def test_the_breakout_pages_rows(home, tmp_path):
    out = tmp_path / "pages"
    assert run(*home, out) == 0
    assert sorted(p.name for p in out.iterdir()) == [
        pages.MARKER, "index.html", "results.html", "stage-build.html", "stage-delivery.html", "stage-spec-check.html",
        "status-ready.html", "status-running.html", "status-waiting.html"]
    running = [f"<tr><td>10</td>{badge('build')}{badge('running')}<td>0 / 2</td><td>7.25 / 100</td>"
               "<td>1.00 / 4</td></tr>",
               f"<tr><td>3</td>{badge('build')}{badge('running')}<td>1 / 2</td><td>2.50 / 100</td>"
               "<td>0.50 / 4</td></tr>"]
    assert rows(read(out, "status-running.html")) == running
    assert rows(read(out, "stage-build.html")) == running
    waiting = [f"<tr><td>2</td>{badge('spec-check')}{badge('waiting')}<td>0 / 2</td><td>0.00 / 100</td>"
               "<td>0.00 / 4</td></tr>"]
    assert rows(read(out, "status-waiting.html")) == waiting
    assert rows(read(out, "stage-spec-check.html")) == waiting
    ready = [f"<tr><td>4</td>{badge('delivery')}{badge('ready')}<td>2 / 2</td><td>40.00 / 100</td>"
             "<td>2.00 / 4</td></tr>"]
    assert rows(read(out, "status-ready.html")) == ready
    assert rows(read(out, "stage-delivery.html")) == ready


def test_a_limit_with_no_value_shows_no_data(home, tmp_path):
    cfg, personas = home
    st = state.load(cfg, "4")
    st["limits"]["usd"] = None
    del st["rounds_used"]
    state.save(cfg, st)
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    assert rows(read(out, "status-ready.html")) == [
        f"<tr><td>4</td>{badge('delivery')}{badge('ready')}<td>no data / 2</td><td>40.00 / no data</td>"
        "<td>2.00 / 4</td></tr>"]


def test_the_state_page_states_running_and_ready(home, tmp_path):
    out = tmp_path / "pages"
    assert run(*home, out) == 0
    text = read(out)
    assert "<p><code>running</code> means that the next step can start; it does not show a live process.</p>" in text
    assert "<p><code>ready</code> does not show a merge.</p>" in text


def test_a_new_build_replaces_the_old_folder(home, tmp_path):
    cfg, personas = home
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    assert (out / "stage-delivery.html").is_file()
    shutil.rmtree(state.home(cfg) / "4")
    assert run(cfg, personas, out) == 0
    assert not (out / "stage-delivery.html").exists() and (out / "index.html").is_file()
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".pages")) == []


def test_unreadable_item_is_counted_and_the_build_continues(home, tmp_path):
    cfg, personas = home
    for name, text in (("6", None), ("7", '{"version": 99, "stage": "build", "status": "running"}'),
                       ("8", '{"version": 1, "stage": "build"}'), ("9", '{"version": 1, "stage": "../x", '
                                                                         '"status": "running"}')):
        (state.home(cfg) / name).mkdir()
        if text:
            (state.home(cfg) / name / "state.json").write_text(text, encoding="utf-8")
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    text = read(out)
    assert "<p>Items: 9</p>" in text and tile("unreadable", '<span class="count">5</span>') in text
    assert tile("running", '<a class="count" href="status-running.html">2</a>') in text


def test_an_empty_folder_takes_the_pages(home, tmp_path):
    out = tmp_path / "pages"
    out.mkdir()
    assert run(*home, out) == 0
    assert (out / "index.html").is_file()


@pytest.mark.parametrize("bad", ["no home", "no parent", "a file", "inside the home", "holds the home",
                                 "holds other files"])
def test_a_usage_error_exits_2_and_keeps_dir(home, tmp_path, capsys, bad):
    cfg, personas = home
    out = tmp_path / "pages"
    out.mkdir()
    (out / "marker").write_text("keep", encoding="utf-8")
    if bad == "no home":
        cfg["state"]["home"] = str(tmp_path / "missing")
    elif bad == "no parent":
        out = tmp_path / "missing" / "pages"
    elif bad == "a file":
        out = out / "marker"
    elif bad == "inside the home":
        out = state.home(cfg) / "pages"
    elif bad == "holds the home":
        out = tmp_path
    before = sorted(p.name for p in tmp_path.iterdir())
    assert run(cfg, personas, out) == 2
    assert capsys.readouterr().err.startswith("wb: ")
    assert (tmp_path / "pages" / "marker").read_text(encoding="utf-8") == "keep"
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert not (state.home(cfg) / "pages").exists()


def test_a_deleted_page_does_not_stop_the_build(home, tmp_path):
    out = tmp_path / "pages"
    assert run(*home, out) == 0
    (out / "results.html").unlink()
    assert run(*home, out) == 0
    assert (out / "results.html").is_file()


@pytest.mark.parametrize("extra", ["notes.md", "stage-notes.html", "a folder", "a page is a folder"])
def test_what_a_build_did_not_write_stops_the_replace(home, tmp_path, extra):
    out = tmp_path / "pages"
    assert run(*home, out) == 0
    if extra in ("notes.md", "stage-notes.html"):
        kept = out / extra
    else:
        folder = out / "stage-backup.html"
        if extra == "a page is a folder":
            folder = out / "results.html"
            folder.unlink()
        folder.mkdir()
        kept = folder / "notes.txt"
    kept.write_text("keep", encoding="utf-8")
    assert run(*home, out) == 2
    assert kept.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(sys.platform == "win32", reason="a symbolic link needs a privilege on Windows")
def test_a_linked_page_stops_the_replace(home, tmp_path):
    out = tmp_path / "pages"
    assert run(*home, out) == 0
    target = tmp_path / "notes.txt"
    target.write_text("keep", encoding="utf-8")
    (out / "results.html").unlink()
    (out / "results.html").symlink_to(target)
    assert run(*home, out) == 2


@pytest.mark.parametrize("marker", ["no marker", "a folder"])
def test_page_names_with_no_marker_file_are_refused(home, tmp_path, marker):
    out = tmp_path / "pages"
    out.mkdir()
    (out / "index.html").write_text("keep", encoding="utf-8")
    if marker == "a folder":
        (out / pages.MARKER).mkdir()
    assert run(*home, out) == 2
    assert read(out) == "keep"


def test_a_failed_build_leaves_dir_and_no_temp_folder(home, tmp_path, monkeypatch):
    out = tmp_path / "pages"
    out.mkdir()
    (out / pages.MARKER).write_text("index.html", encoding="utf-8")
    (out / "index.html").write_text("keep", encoding="utf-8")

    def broken(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(pages.Path, "write_text", broken)
    with pytest.raises(OSError):
        run(*home, out)
    monkeypatch.undo()
    assert (out / "index.html").read_text(encoding="utf-8") == "keep"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".pages")) == []


REPLY = """I read the spec.

## 3. Notes for the builder

- Use the helper.

## 4. Questions for the owner

1. Which name does the page use?
   - A) The id.
   - B) The title.

SPEC-CHECK w1: QUESTIONS - 1 questions"""
SPEC_WAIT = "The spec check asks questions. Amend the spec, then run wb intake again."
AMEND = "<p>Amend the spec, then run <code>python -m wb intake &lt;spec&gt; --id {}</code> again.</p>"
OFF_PAGE = "<p>This step is done off the page, at the host terminal.</p>"


def run_folder(cfg, item_id, files: dict) -> str:
    folder = state.home(cfg) / item_id / "runs" / "001-spec-checker"
    folder.mkdir(parents=True)
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return str(folder)


@pytest.fixture
def waits(tmp_path, monkeypatch):
    """1 waiting item of each kind."""
    monkeypatch.setattr(pages, "_utc", lambda: BUILT)
    cfg, personas = make_cfg(tmp_path)
    claude = json.loads((ROOT / "tests" / "fixtures" / "runner" / "claude-result.json").read_text(encoding="utf-8"))
    claude["result"] = REPLY
    spec_q = {"case": 2, "text": SPEC_WAIT, "spec_questions": True}
    item(cfg, "w1", "spec-check", "waiting", question={**spec_q, "run_folder": run_folder(cfg, "w1", {
        "counters.json": json.dumps({"adapter": "claude", "model": "claude-opus-5-5"}),
        "stdout.txt": json.dumps(claude)})})
    item(cfg, "w2", "spec-check", "waiting", question={**spec_q, "run_folder": run_folder(cfg, "w2", {
        "reply.txt": "Is the page <public>?\nA) Yes.\nB) No.\n\nSPEC-CHECK w2: QUESTIONS - 1 questions\n"})})
    item(cfg, "w3", "spec-check", "waiting", question={**spec_q, "run_folder": run_folder(cfg, "w3", {})})
    item(cfg, "w4", "build", "waiting", question={"case": 2, "run_folder": None, "text": (
        "Which file name?\n- A) pages.py\nB. html.py\n  C: site.py\nD)no space\n  more text\na) lower\n"
        "See E) here")})
    item(cfg, "w5", "build", "waiting", question={"case": 2, "text": "The build ended FAILED twice.",
                                                  "run_folder": None})
    for n, case in (("w6", 1), ("w7", 3), ("w8", 4), ("w9", 9)):
        item(cfg, n, "review", "waiting", question={"case": case, "text": f"Case {case} stop.", "run_folder": None})
    return cfg, personas


def sections(text: str) -> dict:
    """The <h3> block of each wait in the 4 sections of the owner's work, by id."""
    part = text.split("<h2>Needs your input</h2>\n")[1].split("<h2>Status</h2>")[0]
    return {s.split(":")[0]: "<h3>" + s.split("</section>")[0] for s in part.split("<h3>")[1:]}


TERMINAL = {"wb_host": "h", "wb_command": "cd ~/r && python3 -m wb"}
AT_H = '<p>At the host terminal of <b>h</b>:</p>\n<code class="copy">cd ~/r &amp;&amp; python3 -m wb {}</code>'
AT = '<p>At the host terminal:</p>\n<code class="copy">python -m wb {}</code>'
FILLED = {"approve": "approve {} high", "answer": "answer {} --file &lt;file&gt;"}  # case 3, case 4


@pytest.mark.parametrize("conf,at", [({}, AT), ({"wb_host": "", "wb_command": ""}, AT), (TERMINAL, AT_H)],
                         ids=["no keys", "empty keys", "keys"])
def test_the_waiting_section_for_each_kind(waits, tmp_path, conf, at):
    cfg, personas = waits
    before = sorted(str(p) for p in state.home(cfg).rglob("*"))
    out = tmp_path / "pages"
    assert run_conf(cfg, personas, out, **conf) == 0
    assert sorted(str(p) for p in state.home(cfg).rglob("*")) == before
    s = sections(read(out))
    assert list(s) == [f"w{n}" for n in range(1, 10)]
    assert s["w1"] == ("<h3>w1: case 2</h3>\n<pre>1. Which name does the page use?\n   - A) The id.\n"
                       "   - B) The title.</pre>\n" + AMEND.format("w1") + "\n")
    assert s["w2"] == ("<h3>w2: case 2</h3>\n<pre>Is the page &lt;public&gt;?\nA) Yes.\nB) No.</pre>\n"
                       + AMEND.format("w2") + "\n")
    assert s["w3"] == f"<h3>w3: case 2</h3>\n<pre>{SPEC_WAIT}</pre>\n" + AMEND.format("w3") + "\n"
    for n in ("w1", "w2", "w3"):
        assert "answer" not in s[n].lower()
    assert s["w4"] == ("<h3>w4: case 2</h3>\n<pre>Which file name?\nD)no space\n  more text\na) lower\nSee E) here</pre>\n<ul>\n"
                       "<li>A) pages.py</li>\n<li>B. html.py</li>\n<li>C: site.py</li>\n</ul>\n")
    assert s["w5"] == ("<h3>w5: case 2</h3>\n<pre>The build ended FAILED twice.</pre>\n<p>Answer with the"
                       " <code>answer</code> command: <code>python -m wb answer w5 --file &lt;file&gt;</code>.</p>\n")
    for n, case in (("w6", 1), ("w9", 9)):
        assert s[n] == f"<h3>{n}: case {case}</h3>\n<pre>Case {case} stop.</pre>\n{OFF_PAGE}\n"
    assert s["w7"] == "<h3>w7: case 3</h3>\n<pre>Case 3 stop.</pre>\n" + at.format(FILLED["approve"].format("w7")) + "\n"
    assert s["w8"] == "<h3>w8: case 4</h3>\n<pre>Case 4 stop.</pre>\n" + at.format(FILLED["answer"].format("w8")) + "\n"


def test_a_reply_that_cannot_be_read_shows_the_question(waits, tmp_path):
    cfg, personas = waits
    folder = state.home(cfg) / "w1" / "runs" / "001-spec-checker"
    (folder / "stdout.txt").write_text("{not json", encoding="utf-8")
    (folder / "counters.json").write_text('{"adapter": "claude", "model": "m"}', encoding="utf-8")
    st = state.load(cfg, "w2")
    st["question"]["run_folder"] = str(tmp_path / "gone")
    state.save(cfg, st)
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    s = sections(read(out))
    for n in ("w1", "w2"):
        assert s[n] == f"<h3>{n}: case 2</h3>\n<pre>{SPEC_WAIT}</pre>\n" + AMEND.format(n) + "\n"


def test_a_reply_with_no_batch_heading_shows_the_full_reply():
    assert pages.batch("Intro.\n\n### Notes\n\nA) x\n\nSPEC-CHECK 1: QUESTIONS - 1 questions\n") == \
        "Intro.\n\n### Notes\n\nA) x"
    assert pages.batch("# Questions\nold\n# More Questions\nnew\n") == "new"
    assert pages.batch("#Questions\nx") == "#Questions\nx"


def test_no_waiting_items_says_so(tmp_path, monkeypatch):
    cfg, personas = make_cfg(tmp_path)
    item(cfg, "1", "build", "running")
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    assert "<h2>Needs your input</h2>\n<p>No item needs your input.</p>\n" in read(out)


def steps(cfg, item_id, *runs) -> list[dict]:
    """1 step for each counters dict, with its run folder and counters.json."""
    out = []
    for n, counters in enumerate(runs, 1):
        folder = state.home(cfg) / item_id / "runs" / f"{n:03d}-builder"
        folder.mkdir(parents=True)
        (folder / "counters.json").write_text(json.dumps(counters), encoding="utf-8")
        out.append({"n": n, "run_folder": str(folder)})
    return out


@pytest.fixture
def results_home(tmp_path, monkeypatch):
    monkeypatch.setattr(pages, "_utc", lambda: BUILT)
    cfg, personas = make_cfg(tmp_path)
    item(cfg, "d1", "delivery", "ready", usd=10.0, seconds=3600.0, steps=steps(
        cfg, "d1", {"end": "2026-10-20T10:00:00Z", "usd": 3.0, "seconds": 1000.0},
        {"end": "2026-10-30T10:00:00Z", "usd": "unpriced", "seconds": 800.0},
        {"end": "2026-10-30T11:00:00Z", "usd": 4.5})[::-1])  # the last step is the highest n, not the last entry
    item(cfg, "d2", "delivery", "ready", tier="Low", usd=2.0, seconds=1800.0, rounds_used=2, steps=steps(
        cfg, "d2", {"end": "2026-10-20T10:00:00Z"}))
    item(cfg, "w1", "build", "waiting", usd=5.0, seconds=7200.0, rounds_used=1,
         question={"case": 2, "text": "q", "run_folder": None},
         steps=steps(cfg, "w1", {"end": "2026-11-01T09:00:00Z"}))
    item(cfg, "w2", "spec-check", "waiting", tier=None, usd=1.0, question={"case": 1, "text": "q", "run_folder": None})
    item(cfg, "r1", "build", "running", tier="High", usd=20.5, seconds=3600.0)
    for n in range(1, 9):
        item(cfg, f"x0{n}", "review", "running", tier="Low", usd=1.0)
    return cfg, personas


def measure(text: str, title: str) -> list[list[str]]:
    part = text.split(f"<h3>{title}</h3>\n")[1].split("<h3>")[0]
    if part.startswith("<p>None.</p>"):
        return []
    return [re.sub(r'<span class="badge">(.*?)</span>', r"\1", r).split("</td><td>")  # the text of a badge
            for r in re.findall(r"<tr><td>(.*?)</td></tr>", part)]


def test_results_figures(results_home, tmp_path):
    out = tmp_path / "pages"
    assert run(*results_home, out) == 0
    assert '<a href="results.html">Results</a>' in read(out)
    text = read(out, "results.html")
    assert [g for g in re.findall(r"<h2>(.*?)</h2>", text)] == [
        "Key figures", "Delivered per week", "USD per week by role", "Budget use", "Waits",
        "Throughput", "Cost and time", "Quality", "Waits"]
    assert measure(text, "Items that reached delivery, by ISO week") == [["2026-W43", "1"], ["2026-W44", "1"]]
    assert measure(text, "Items at each stage") == [["spec-check", "1"], ["build", "2"], ["review", "8"],
                                                   ["rework", "0"], ["delivery", "2"]]
    assert measure(text, "USD and hours, by tier") == [
        ["all", "13", "46.50", "3.58", "4.50", "0.35"],
        ["High", "1", "20.50", "20.50", "1.00", "1.00"],
        ["Low", "9", "10.00", "1.11", "0.50", "0.06"],
        ["Medium", "2", "15.00", "7.50", "3.00", "1.50"]]
    assert measure(text, "The 10 items with the highest USD") == [
        ["r1", "20.50"], ["d1", "10.00"], ["w1", "5.00"], ["d2", "2.00"], ["w2", "1.00"],
        ["x01", "1.00"], ["x02", "1.00"], ["x03", "1.00"], ["x04", "1.00"], ["x05", "1.00"]]
    assert measure(text, "Items at each rework round") == [["0", "11"], ["1", "1"], ["2", "1"]]
    assert measure(text, "Items at the round limit") == [["d2", "2 / 2"]]
    assert measure(text, "Waiting items by stop case") == [["1", "1"], ["2", "1"]]
    assert measure(text, "The wait of each waiting item") == [["w1", "2", "3.00"], ["w2", "1", "no data"]]


def test_results_fall_back_to_counters(results_home, tmp_path):
    cfg, personas = results_home
    st = state.load(cfg, "d1")
    del st["usd"], st["seconds"]
    state.save(cfg, st)
    st = state.load(cfg, "w2")  # no steps field: the run folders of the item
    del st["usd"], st["steps"]
    state.save(cfg, st)
    steps(cfg, "w2", {"end": "2026-11-01T11:30:00Z", "usd": 0.75})
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    text = read(out, "results.html")
    top = measure(text, "The 10 items with the highest USD")
    assert top[1] == ["d1", "7.50"]
    tiers = measure(text, "USD and hours, by tier")
    assert tiers[0] == ["all", "13", "43.75", "3.37", "4.00", "0.31"]
    assert tiers[3] == ["Medium", "2", "12.50", "6.25", "2.50", "1.25"]
    assert measure(text, "The wait of each waiting item") == [["w1", "2", "3.00"], ["w2", "1", "0.50"]]
    assert rows(read(out, "status-ready.html"))[0] == (
        f"<tr><td>d1</td>{badge('delivery')}{badge('ready')}<td>0 / 2</td><td>7.50 / 100</td><td>0.50 / 4</td></tr>")


def test_results_no_data(tmp_path, monkeypatch):
    monkeypatch.setattr(pages, "_utc", lambda: BUILT)
    cfg, personas = make_cfg(tmp_path)
    st = item(cfg, "1", "delivery", "waiting", question={"case": 2, "text": "q", "run_folder": None})
    for key in ("usd", "seconds", "rounds_used", "steps", "tier"):
        del st[key]
    st["limits"]["rounds"] = None
    state.save(cfg, st)
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    text = read(out, "results.html")
    assert measure(text, "Items that reached delivery, by ISO week") == [["no data", "1"]]
    assert measure(text, "USD and hours, by tier") == [["all", "1", "no data", "no data", "no data", "no data"]]
    assert measure(text, "The 10 items with the highest USD") == [["no data", "no data"]]
    assert measure(text, "Items at each rework round") == [["no data", "1"]]
    assert measure(text, "Items at the round limit") == [["no data", "no data"]]
    assert measure(text, "The wait of each waiting item") == [["1", "2", "no data"]]


def test_results_of_an_empty_home(tmp_path):
    cfg, personas = make_cfg(tmp_path)
    state.home(cfg).mkdir()
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    text = read(out, "results.html")
    assert measure(text, "Items at each stage") == [[s, "0"] for s in pages.STAGES]
    for title in ("USD and hours, by tier", "The 10 items with the highest USD", "Items at the round limit",
                  "The wait of each waiting item"):
        assert measure(text, title) == [], title


def role_steps(cfg, item_id, *runs) -> list[dict]:
    """steps with a role: each run is (role or None, counters)."""
    return [{**s, **({"role": r} if r else {})} for s, (r, _) in zip(steps(cfg, item_id, *(c for _, c in runs)), runs)]


@pytest.fixture
def dash_home(tmp_path, monkeypatch):
    """The items of the key figures and the graphs: steps at the edges of the windows of 7 and 30
    days before BUILT, of the 12 weeks, and steps of each role."""
    monkeypatch.setattr(pages, "_utc", lambda: BUILT)
    cfg, personas = make_cfg(tmp_path)
    item(cfg, "a1", "delivery", "ready", usd=250.0, seconds=3600.0, steps=role_steps(
        cfg, "a1", ("spec-checker", {"start": "2026-10-31T10:00:00Z", "end": "2026-10-31T12:00:00Z", "usd": 1.25}),
        ("builder", {"end": BUILT, "usd": 2.0})))  # exactly at the build time: in
    item(cfg, "a2", "delivery", "ready", rounds_used=2, steps=role_steps(
        cfg, "a2", ("reviewer", {"start": "2026-10-20T12:00:00Z", "end": "2026-10-25T12:00:00Z", "usd": 4.0})))
    st = item(cfg, "a3", "delivery", "ready", usd=33.7, seconds=7200.0, steps=role_steps(
        cfg, "a3", ("second-reviewer", {"start": "2026-10-28T00:00:00Z", "end": "2026-10-28T06:00:00Z", "usd": 0.5})))
    del st["rounds_used"]
    state.save(cfg, st)
    item(cfg, "a4", "delivery", "ready", steps=role_steps(
        cfg, "a4", ("builder", {"end": "2026-08-09T12:00:00Z", "usd": 64.0}),  # the 13th week back
        ("rework", {"end": "2026-10-02T12:00:00Z", "usd": 8.0})))  # exactly 30 days back: out
    item(cfg, "a5", "build", "running", limits={"usd": 0, "hours": None, "rounds": 2}, steps=role_steps(
        cfg, "a5", ("mystery", {"end": "2026-11-01T11:00:00Z", "usd": 0.25}),
        (None, {"end": "2026-11-01T12:00:01Z", "usd": 32.0})))  # 1 second after the build time: out
    item(cfg, "a7", "delivery", "ready", steps=role_steps(
        cfg, "a7", ("reviewer", {"start": "2026-10-14T12:00:00Z", "end": "2026-10-15T12:00:00Z", "usd": 1.0})))
    item(cfg, "w", "review", "waiting", question={"case": 3, "text": "q", "run_folder": None}, steps=role_steps(
        cfg, "w", ("builder", {"end": "2026-10-25T11:59:59Z", "usd": 16.0}),  # 1 second out of 7 days
        ("builder", {"usd": 50.0})))  # no end
    item(cfg, "w2", "build", "waiting", question={"case": 2, "text": "q", "run_folder": None}, steps=role_steps(
        cfg, "w2", ("builder", {"start": "2026-10-31T00:00:00Z", "end": "2026-10-31T12:00:00Z"})))  # not ready
    st = item(cfg, "w3", "spec-check", "waiting", question={"case": 1, "text": "q", "run_folder": None})
    del st["steps"]  # the run folders of the item, with no role
    state.save(cfg, st)
    steps(cfg, "w3", {"end": "2026-11-01T09:00:00Z", "usd": 0.25})
    return cfg, personas


def key_figures(text: str) -> list[tuple]:
    """The tiles of the Key figures card, which must be the first section in main."""
    main = text.split("<main>\n")[1].split("</h1>\n", 1)[1]
    assert main.startswith('<section class="card">\n<h2>Key figures</h2>\n<ul class="tiles">\n')
    part = main.split("</section>")[0]
    tiles = re.findall(r'<li><span>(.*?)</span> <span class="count">(.*?)</span></li>', part)
    assert len(tiles) == part.count("<li>")
    return tiles


def test_results_kpi_tiles(dash_home, tmp_path):
    out = tmp_path / "pages"
    assert run(*dash_home, out) == 0
    assert key_figures(read(out, "results.html")) == [
        ("Delivered, 7 days", "2"), ("Delivered, 30 days", "4"),  # not a5, not a2 at 7 days, not a4 at 30
        ("USD, 7 days", "4.25"), ("USD, 30 days", "25.25"),
        ("Median hours to ready, 7 days", "16.00"), ("Median hours to ready, 30 days", "25.00"),
        ("Rework-free, 7 days", "100 %"), ("Rework-free, 30 days", "66 %"),  # 2 of 3 known, a3 not known
        ("Waiting now", "3")]


def test_results_kpi_tiles_with_no_input(tmp_path, monkeypatch):
    monkeypatch.setattr(pages, "_utc", lambda: BUILT)
    cfg, personas = make_cfg(tmp_path)
    st = item(cfg, "1", "delivery", "ready", steps=steps(cfg, "1", {"end": BUILT}))  # no start, no usd
    del st["rounds_used"]
    state.save(cfg, st)
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    assert key_figures(read(out, "results.html")) == [
        ("Delivered, 7 days", "1"), ("Delivered, 30 days", "1"), ("USD, 7 days", "0.00"), ("USD, 30 days", "0.00"),
        ("Median hours to ready, 7 days", "no data"), ("Median hours to ready, 30 days", "no data"),
        ("Rework-free, 7 days", "no data"), ("Rework-free, 30 days", "no data"), ("Waiting now", "0")]


GRAPHS = ["Delivered per week", "USD per week by role", "Budget use", "Waits"]
WEEKS = [f"2026-W{w}" for w in range(33, 45)]  # the 12 ISO weeks up to the week of BUILT


def graph(text: str, heading: str) -> tuple[str, list[list[str]]]:
    """(the SVG or "", the table rows) of a graph card: an SVG in its chart box and right below it its table, or None.
    alone."""
    part = text.split(f'<section class="card">\n<h2>{heading}</h2>\n')[1].split("</section>")[0]
    m = re.fullmatch(r'(?:<div class="chart-box">\n(<svg [^>]*>\n.*?</svg>\n)</div>\n)?'
                     r"(<table>\n.*</table>\n|<p>None\.</p>\n)", part, re.S)
    assert m and (m[1] is None) == m[2].startswith("<p>"), part
    return m[1] or "", [r.split("</td><td>") for r in re.findall(r"<tr><td>(.*?)</td></tr>", m[2])]


def svg_rows(svg: str, skip: int = 0) -> list[tuple]:
    """(label, value text) of each bar row, after skip legend texts."""
    t = re.findall(r"<text [^>]*>(.*?)</text>", svg)[skip:]
    return list(zip(t[::2], t[1::2]))


def segments(svg: str) -> list[str]:
    return re.findall(r"<rect [^>]*><title>(.*?)</title></rect>", svg)


def test_results_graphs_and_their_tables(dash_home, tmp_path):
    out = tmp_path / "pages"
    assert run(*dash_home, out) == 0
    text = read(out, "results.html")
    assert re.findall(r'<section class="card">\n<h2>(.*?)</h2>', text)[:5] == ["Key figures", *GRAPHS]
    assert text.count("<svg ") == 4
    svg, table = graph(text, "Delivered per week")
    assert table[-5:] == [["2026-W40", "1", "a4"], ["2026-W41", "0", ""], ["2026-W42", "1", "a7"],
                          ["2026-W43", "1", "a2"], ["2026-W44", "2", "a1, a3"]]  # not a5: not at delivery
    assert svg_rows(svg) == [(w, n) for w, n, _ in table]
    assert segments(svg) == [f"{w}: {n}" for w, n, _ in table if n != "0"]
    svg, table = graph(text, "USD per week by role")
    assert re.findall(r"<text [^>]*>(.*?)</text>", svg)[:6] == list(pages.ROLES)  # the legend
    assert svg_rows(svg, 6) == [(r[0], r[-1]) for r in table]
    assert segments(svg) == [f"{r[0]} {role}: {v}" for r in table for role, v in zip(pages.ROLES, r[1:-1])
                             if v != "0.00"]
    svg, table = graph(text, "Budget use")
    assert table == [["a5", "0.00 / 0", "no data", "0.00 / no data", "no data"],  # a limit of 0 and no limit
                     ["a1", "250.00 / 100", "250 %", "1.00 / 4", "25 %"],
                     ["w3", "0.00 / 100", "0 %", "0.00 / 4", "0 %"], ["w2", "0.00 / 100", "0 %", "0.00 / 4", "0 %"],
                     ["a3", "33.70 / 100", "33 %", "2.00 / 4", "50 %"], ["a2", "0.00 / 100", "0 %", "0.00 / 4", "0 %"],
                     ["a7", "0.00 / 100", "0 %", "0.00 / 4", "0 %"], ["a4", "0.00 / 100", "0 %", "0.00 / 4", "0 %"]]
    assert svg_rows(svg) == [x for r in table for x in ((f"{r[0]} USD", r[2]), (f"{r[0]} hours", r[4]))]
    assert segments(svg) == ["a1 USD: 250 %", "a1 hours: 25 %", "a3 USD: 33 %", "a3 hours: 50 %"]
    widths = re.findall(r'<rect [^>]*width="([0-9.]+)"', svg)
    assert float(widths[0]) == charts.BAR_W and float(widths[1]) == charts.BAR_W / 8  # 250 % at the axis end of 200
    mark = re.findall(r'<line class="mark" x1="([0-9.]+)"', svg)
    assert [float(x) for x in mark] == [charts.BAR_X + charts.BAR_W / 2]
    svg, table = graph(text, "Waits")
    assert table == [["w2", "2", "24.00"], ["w3", "1", "3.00"], ["w", "3", "no data"]]  # longest first
    assert svg_rows(svg) == [(f"{i}: case {c}", h) for i, c, h in table]
    assert segments(svg) == ["w2: case 2: 24.00", "w3: case 1: 3.00"]  # no age: no bar
    assert "<line" not in svg


def test_weekly_graphs_show_12_weeks(dash_home, tmp_path):
    out = tmp_path / "pages"
    assert run(*dash_home, out) == 0
    text = read(out, "results.html")
    svg, table = graph(text, "Delivered per week")
    assert [r[0] for r in table] == WEEKS and [r[1] for r in table[:7]] == ["0"] * 7  # oldest first, empty is 0
    assert "W32" not in svg
    svg, table = graph(text, "USD per week by role")
    zero = ["0.00"] * 7
    assert table == [[w, *zero] for w in WEEKS[:7]] + [
        ["2026-W40", "0.00", "0.00", "0.00", "0.00", "8.00", "0.00", "8.00"], ["2026-W41", *zero],
        ["2026-W42", "0.00", "0.00", "1.00", "0.00", "0.00", "0.00", "1.00"],
        ["2026-W43", "0.00", "16.00", "4.00", "0.00", "0.00", "0.00", "20.00"],
        # an unknown role, no role and the run folders of an item with no steps field are other
        ["2026-W44", "1.25", "2.00", "0.00", "0.50", "0.00", "32.50", "36.25"]]
    assert "W32" not in svg and "64.00" not in svg and "50.00" not in svg  # the 13th week, no end


def test_item_graphs_show_the_last_20(tmp_path, monkeypatch):
    monkeypatch.setattr(pages, "_utc", lambda: BUILT)
    cfg, personas = make_cfg(tmp_path)
    for n in range(21):  # i00 ended first
        item(cfg, f"i{n:02d}", "build", "running", usd=float(n), steps=steps(
            cfg, f"i{n:02d}", {"end": f"2026-10-{n + 1:02d}T00:00:00Z"}))
    item(cfg, "j20", "build", "running", steps=steps(cfg, "j20", {"end": "2026-10-21T00:00:00Z"}))  # a tie
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    svg, table = graph(read(out, "results.html"), "Budget use")
    assert [r[0] for r in table] == ["i20", "j20"] + [f"i{n:02d}" for n in range(19, 1, -1)]  # newest first
    assert len(svg_rows(svg)) == 40 and "i01" not in svg and "i00" not in svg


def test_a_graph_with_no_rows_shows_none(tmp_path, monkeypatch):
    monkeypatch.setattr(pages, "_utc", lambda: BUILT)
    cfg, personas = make_cfg(tmp_path)
    state.home(cfg).mkdir()
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    text = read(out, "results.html")
    for heading in ("Budget use", "Waits"):
        assert graph(text, heading) == ("", []), heading
    for heading in ("Delivered per week", "USD per week by role"):  # always the 12 weeks
        svg, table = graph(text, heading)
        assert svg and [r[0] for r in table] == WEEKS


def test_results_measures_are_cards(results_home, tmp_path):
    out = tmp_path / "pages"
    assert run(*results_home, out) == 0
    text = read(out, "results.html")
    cards = re.findall(r'<section class="card">\n<h2>(.*?)</h2>\n(.*?)</section>\n', text, re.S)
    assert text.count("<section") == len(cards) == 9 and text.split("</section>\n")[-1].startswith("</main>")
    assert [h for h, _ in cards[5:]] == ["Throughput", "Cost and time", "Quality", "Waits"]
    for group, body in cards[5:]:
        titles = [m([], None)[0] for g, m in pages.MEASURES if g == group]  # each measure keeps its h3 and table
        assert re.findall(r"<h3>(.*?)</h3>", body) == titles and len(titles) == 2, group
        assert body.count("<table>") + body.count("<p>None.</p>") == len(titles), group


EVIL = '<script>"x'


@pytest.fixture
def evil(waits):
    """A question, an item id and a reply that hold a script tag, a quote and a URL."""
    cfg, personas = waits
    folder = state.home(cfg) / "e1"
    folder.mkdir()
    st = state.new("e1")
    st.update(id=EVIL, stage="build", status="waiting",
              question={"case": 2, "text": f"{EVIL} see http://example.invalid/x", "run_folder": None})
    (folder / "state.json").write_text(json.dumps(st), encoding="utf-8")
    run = state.home(cfg) / "w2" / "runs" / "001-spec-checker"
    (run / "reply.txt").write_text(f"# Questions\n{EVIL}\nA) <form action='http://example.invalid'>\n",
                                   encoding="utf-8")
    return cfg, personas


def test_state_text_is_escaped(evil, tmp_path):
    out = tmp_path / "pages"
    assert run(*evil, out) == 0
    text = read(out)
    escaped = "&lt;script&gt;&quot;x"
    assert f"<h3>{escaped}: case 2</h3>\n<pre>{escaped} see http://example.invalid/x</pre>" in text
    assert f"<pre>{escaped}\nA) &lt;form action=&#x27;http://example.invalid&#x27;&gt;</pre>" in text
    assert f"<tr><td>{escaped}</td>{badge('build')}{badge('waiting')}" in read(out, "status-waiting.html")
    assert f"<tr><td>{escaped}</td><td>2</td><td>no data</td></tr>" in read(out, "results.html")


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found = []

    def handle_starttag(self, tag, attrs):
        self.found.append((tag, attrs))


PAGE_OR_PR = (r"[a-z-]+\.html|https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*"
              r"|https://ado\.example/proj/_workitems/edit/[1-9][0-9]*")  # the fake ADO base
SCRIPTS = tuple(f"<script>{s}</script>" for s in theme.scripts(THEME_ID))


def no_theme(text: str) -> str:
    """The page with its 2 theme scripts cut out; each must be there once, byte for byte."""
    for script in SCRIPTS:
        assert text.count(script) == 1
        text = text.replace(script, "")
    return text


@pytest.mark.parametrize("conf", [{}, "rows"])
def test_pages_hold_no_script_form_or_link(evil, tmp_path, conf):
    out = tmp_path / "pages"
    assert run_conf(*evil, out, **(ROWS_CONF if conf == "rows" else conf)) == 0
    files = sorted(f for f in out.iterdir() if f.name != pages.MARKER)
    assert [f.name for f in files] == ["index.html", "results.html", "stage-build.html", "stage-review.html",
                                       "stage-spec-check.html", "status-waiting.html"]
    for f in files:
        text = no_theme(f.read_text(encoding="utf-8"))
        assert "<script" not in text.lower() and "<form" not in text.lower(), f.name
        tags = Tags()
        tags.feed(text)
        assert not {"script", "form", "iframe", "img"} & {t for t, _ in tags.found}, f.name
        links = [v for _, attrs in tags.found for k, v in attrs if k in ("href", "src", "action")]
        assert links and all(re.fullmatch(PAGE_OR_PR, v) for v in links), (f.name, links)


def test_each_page_carries_the_theme_scripts_and_buttons(home, tmp_path):
    out = tmp_path / "pages"
    assert run(*home, out) == 0
    files = sorted(f for f in out.iterdir() if f.name != pages.MARKER)
    assert len(files) == 8
    head, control = SCRIPTS
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert text.index("<head>") < text.index(head) < text.index("<style>") < text.index("</head>"), f.name
        assert text.endswith(f"\n{control}\n</body>\n</html>\n"), f.name
        assert text.count('<div role="group" aria-label="Theme">') == 1, f.name
        assert re.findall(r'<button type="button" data-theme-choice="(\w+)" aria-pressed="(\w+)">(\w+)</button>',
                          text) == [("light", "false", "Light"), ("dark", "false", "Dark"),
                                    ("system", "true", "System")], f.name


def test_each_page_has_1_header_bar(home, tmp_path):
    out = tmp_path / "pages"
    assert run(*home, out) == 0
    for f in sorted(f for f in out.iterdir() if f.name != pages.MARKER):
        text = f.read_text(encoding="utf-8")
        assert text.count("<header") == 1 and text.count("</header>") == 1, f.name
        bar = text.split("<header>\n")[1].split("</header>")[0]
        for part in ('<a href="index.html">State</a>', '<a href="results.html">Results</a>',
                     f"<p>Built at {BUILT_AT}.</p>", theme.CONTROL):
            assert bar.count(part) == 1, (f.name, part)
        assert text.count(theme.CONTROL) == 1 and text.count(f"Built at {BUILT_AT}") == 1, f.name


@pytest.mark.parametrize("conf", ["no table", "empty", "a table with no key"])
def test_with_no_key_a_page_has_no_script_and_no_theme_group(home, tmp_path, conf):
    cfg, personas = home
    cfg = {k: v for k, v in cfg.items() if k != "pages"}
    if conf != "no table":
        cfg["pages"] = {"theme_key": ""} if conf == "empty" else {}
    out = tmp_path / "pages"
    assert main(["pages", "--out", str(out)], cfg=cfg, personas=personas) == 0
    files = sorted(f for f in out.iterdir() if f.name != pages.MARKER)
    assert len(files) == 8
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert "<script" not in text and "data-theme-choice" not in text and 'aria-label="Theme"' not in text.split(
            "<style>")[0] + text.split("</style>")[1], f.name
        assert ('<header>\n<nav><a href="index.html">State</a> <a href="results.html">Results</a></nav>\n'
                f"<p>Built at {BUILT_AT}.</p>\n</header>\n") in text, f.name


@pytest.mark.parametrize("key", ['a"b', "a\\b", "a</script>", "x" * 65, 7, ["a"]])
def test_a_bad_theme_key_is_a_usage_error_and_keeps_dir(home, tmp_path, capsys, key):
    out = tmp_path / "pages"
    out.mkdir()
    (out / "marker").write_text("keep", encoding="utf-8")
    before = sorted(p.name for p in tmp_path.iterdir())
    assert run(*home, out, key) == 2
    err = capsys.readouterr().err
    assert err.startswith("wb: pages.theme_key ") and str(key) not in err  # the key is private: never printed
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert [p.name for p in out.iterdir()] == ["marker"]


TILE =re.compile(r'<li><span>([a-z-]+)</span> (?:<a class="count" href="([a-z-]+\.html)">(\d+)</a>'
                  r'|<span class="count">(\d+)</span>)</li>')


def test_the_state_counts_are_tiles_in_a_grid(home, tmp_path):
    out = tmp_path / "pages"
    assert run(*home, out) == 0
    text = read(out)
    grids = re.findall(r'<ul class="tiles">\n(.*?)\n</ul>', text, re.S)
    assert [[TILE.fullmatch(t).groups() for t in g.split("\n")] for g in grids] == [
        [("running", "status-running.html", "2", None), ("waiting", "status-waiting.html", "1", None),
         ("ready", "status-ready.html", "1", None), ("unreadable", None, None, "1")],
        [("spec-check", "stage-spec-check.html", "1", None), ("build", "stage-build.html", "2", None),
         ("review", None, None, "0"), ("rework", None, None, "0"), ("delivery", "stage-delivery.html", "1", None)]]
    assert re.search(r"\.tiles \{ display: grid; grid-template-columns: repeat\(auto-fill, minmax\(", pages.STYLE)


def test_the_owner_sections_and_the_counts_are_cards(owner, tmp_path):
    out = tmp_path / "pages"
    assert run(*owner, out) == 0
    text = read(out)
    cards = re.findall(r'<section class="card">\n<h2>(.*?)</h2>\n(.*?)</section>\n', text, re.S)
    assert [h for h, _ in cards] == ["Needs your input", "Needs merge", "Steps outside wb", "Queued",
                                     "Status", "Stage"]
    assert text.count("<section") == 6 and text.count("</section>") == 6 and text.count("<h2>") == 6
    assert '<ul class="tiles">' in cards[4][1] and '<ul class="tiles">' in cards[5][1]
    assert text.split("</section>\n")[-1].startswith("<p><code>running</code> means")  # the notes below Stage


def test_the_stage_and_status_cells_are_badges(results_home, tmp_path):
    out = tmp_path / "pages"
    assert run(*results_home, out) == 0
    breakouts = sorted(f.name for f in out.glob("st*-*.html"))
    assert len(breakouts) == 7 and sum(len(rows(read(out, name))) for name in breakouts) == 26
    for name in breakouts:
        for r in rows(read(out, name)):
            cells = re.findall(r"<td>(.*?)</td>", r)
            assert [re.fullmatch(r'<span class="badge">[a-z-]+</span>', c) is not None for c in cells] == [
                False, True, True, False, False, False], (name, r)
    part = read(out, "results.html").split("<h3>Items at each stage</h3>\n")[1].split("</table>")[0]
    assert re.findall(r"<tr><td>(.*?)</td><td>(\d+)</td></tr>", part) == [
        (f'<span class="badge">{s}</span>', n) for s, n in zip(pages.STAGES, ("1", "2", "8", "0", "2"))]
    for title in ("The wait of each waiting item", "The 10 items with the highest USD", "Waiting items by stop case"):
        assert "badge" not in read(out, "results.html").split(f"<h3>{title}</h3>\n")[1].split("</table>")[0], title


def tree_hash(home) -> str:
    h = hashlib.sha256()
    for p in sorted(home.rglob("*")):
        h.update(p.relative_to(home).as_posix().encode() + b"\0")
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def test_the_build_changes_nothing_in_the_state_home(waits, results_home, tmp_path):
    for cfg, personas in (waits, results_home):
        before = tree_hash(state.home(cfg))
        assert run(cfg, personas, tmp_path / "pages") == 0
        assert tree_hash(state.home(cfg)) == before


def test_the_build_calls_no_network_service(waits, tmp_path, monkeypatch):
    calls = []

    def no_network(*args, **kwargs):
        calls.append(args)
        raise OSError("no network in this test")
    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    assert run(*waits, tmp_path / "pages") == 0
    assert calls == []


def test_a_failed_replace_puts_the_old_folder_back(home, tmp_path, monkeypatch):
    out = tmp_path / "pages"
    out.mkdir()
    (out / pages.MARKER).write_text("index.html", encoding="utf-8")
    (out / "index.html").write_text("keep", encoding="utf-8")
    rename = pages.Path.rename

    def broken(self, target):
        if Path(target) == out and self.name.startswith(".pages.") and not self.name.endswith(".old"):
            raise OSError("rename failed")
        return rename(self, target)
    monkeypatch.setattr(pages.Path, "rename", broken)
    with pytest.raises(OSError):
        run(*home, out)
    assert (out / "index.html").read_text(encoding="utf-8") == "keep"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".pages")) == []


def test_a_tally_is_in_numeric_order_with_no_data_last():
    assert pages._tally([pages.NO_DATA, 10, 2, 2]) == [(2, 2), (10, 1), (pages.NO_DATA, 1)]


def test_a_time_with_no_zone_is_utc():
    assert pages._time("2026-11-01T09:00:00") == pages._time("2026-11-01T09:00:00Z")
    assert pages._time("not a time") is None and pages._time(None) is None


def test_the_pages_folder_has_the_mode_of_a_new_folder(home, tmp_path):
    out = tmp_path / "pages"
    assert run(*home, out) == 0
    (tmp_path / "plain").mkdir()
    assert stat.S_IMODE(out.stat().st_mode) == stat.S_IMODE((tmp_path / "plain").stat().st_mode)


@pytest.mark.skipif(sys.platform == "win32", reason="a symbolic link needs a privilege on Windows")
def test_a_symbolic_link_keeps_pointing_at_the_pages(home, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "pages"
    link.symlink_to(real)
    for _ in range(2):
        assert run(*home, link) == 0
    assert link.is_symlink() and (real / "index.html").is_file()
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".")) == []


@pytest.mark.skipif(sys.platform == "win32", reason="a symbolic link needs a privilege on Windows")
@pytest.mark.parametrize("files", [["state.json"], [], [".wb-pages", "index.html"]])
def test_the_target_of_a_linked_item_folder_is_refused(home, tmp_path, capsys, files):
    cfg, personas = home
    target = tmp_path / "item-6"
    target.mkdir()
    for name in files:
        (target / name).write_text("index.html", encoding="utf-8")
    (state.home(cfg) / "6").symlink_to(target)
    assert run(cfg, personas, target) == 2
    assert capsys.readouterr().err.startswith("wb: ")
    assert sorted(p.name for p in target.iterdir()) == files


def test_malformed_fields_do_not_stop_the_build(home, tmp_path):
    cfg, personas = home
    st = state.load(cfg, "2")
    st.update(steps=[{"n": "x", "run_folder": 5}, "not a step"],
              question={"case": [2], "text": "q", "run_folder": 5, "spec_questions": True}, tier=["Low"])
    state.save(cfg, st)
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    assert tile("unreadable", '<span class="count">1</span>') in read(out)
    assert [["2", "no data", "no data"]] == measure(read(out, "results.html"), "The wait of each waiting item")


# the options of a blocked reply, the answer form, and no form elsewhere.

STORE = "I built AC 1 and AC 2.\n\nWhich store?\n- A) Keep the file.\nB) Use a table.\n  C: Ask later.\n\n\n"
THREE = ["A) Keep the file.", "B) Use a table.", "C: Ask later."]


def blocked(cfg, item_id, reply, stage="build", **question) -> dict:
    """A case 2 wait whose run folder holds reply as its reply.txt (bytes as given)."""
    folder = state.home(cfg) / item_id / "runs" / "001-builder"
    folder.mkdir(parents=True)
    (folder / "reply.txt").write_bytes(reply.encode("utf-8"))
    last = reply.strip().splitlines()[-1] if reply.strip() else ""
    return item(cfg, item_id, stage, "waiting", question={"case": 2, "text": f"The build stopped: {last}",
                                                         "run_folder": str(folder), **question})


@pytest.mark.parametrize("reply,expected", [
    (STORE + "BUILD: BLOCKED - the owner picks the store\n", THREE),
    (STORE.replace("\n", "\r\n") + "REWORK: BLOCKED - the owner picks the store", THREE),
    ("A) one\nB) two\n\nThen I read the spec again.\nREWORK: BLOCKED - x\n", []),  # a lettered list earlier
    ("A) one\nB) two\nBUILD: READY " + "ab12" * 10 + "\n", []),
    ("A) one\nB) two\nA) three\nBUILD: BLOCKED - x\n", []),  # a repeated letter
    ("Intro.\nA) one\nBUILD: BLOCKED - x\n", []),  # a run of 1
    ("A) one\nB) two\nBUILD: BLOCKED\n", ["A) one", "B) two"]),
    ("A) one\nB) two\nBUILD: BLOCKED - x\nmore\n", []),
    ("A) one  \n - B) two\t\nBUILD: BLOCKED - x", ["A) one", "B) two"]),  # an option line is stripped
    ("", []),
])
def test_options_from_blocked_reply(tmp_path, reply, expected):
    cfg, _ = make_cfg(tmp_path)
    assert pages.options(blocked(cfg, "b1", reply)["question"]) == expected


def test_options_from_the_stream_of_a_claude_step(tmp_path):
    """The reply reader of the pages gives the final text of the adapter, with its blank lines at the end."""
    cfg, _ = make_cfg(tmp_path)
    q = blocked(cfg, "b1", "")["question"]
    claude = json.loads((ROOT / "tests" / "fixtures" / "runner" / "claude-result.json").read_text(encoding="utf-8"))
    claude["result"] = STORE + "BUILD: BLOCKED - x\n\n\n"
    folder = Path(q["run_folder"])
    (folder / "reply.txt").unlink()
    (folder / "counters.json").write_text(json.dumps({"adapter": "claude", "model": "claude-opus-5-5"}),
                                          encoding="utf-8")
    (folder / "stdout.txt").write_text(json.dumps(claude), encoding="utf-8")
    assert pages.reply_text(q["run_folder"]).endswith("x\n\n\n")
    assert pages.options(q) == THREE


def test_no_options_for_another_wait(tmp_path):
    cfg, _ = make_cfg(tmp_path)
    reply = STORE + "BUILD: BLOCKED - x\n"
    q = blocked(cfg, "b1", reply)["question"]
    assert pages.options(q) == THREE
    for change in ({"spec_questions": True}, {"case": 4}, {"case": "2"}, {"run_folder": None},
                   {"run_folder": str(tmp_path / "gone")}):
        assert pages.options({**q, **change}) == [], change


def test_the_page_shows_1_line_for_each_option_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(pages, "_utc", lambda: BUILT)
    cfg, personas = make_cfg(tmp_path)
    blocked(cfg, "b1", STORE + "BUILD: BLOCKED - the owner picks the store\n")
    blocked(cfg, "b2", "A) one\nB) two\nBUILD: READY " + "ab12" * 10 + "\n")
    before = tree_hash(state.home(cfg))
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    assert tree_hash(state.home(cfg)) == before
    s = sections(read(out))
    assert s["b1"] == ("<h3>b1: case 2</h3>\n<pre>The build stopped: BUILD: BLOCKED - the owner picks the store</pre>\n"
                       "<ul>\n<li>A) Keep the file.</li>\n<li>B) Use a table.</li>\n<li>C: Ask later.</li>\n</ul>\n")
    assert "<ul>" not in s["b2"] and "python -m wb answer b2" in s["b2"]


URL = "https://pages.example.invalid/answer"
STOPPED = "The build stopped: BUILD: BLOCKED - the owner picks the store"


def run_url(cfg, personas, out, url=URL, **conf) -> int:
    return main(["pages", "--out", str(out), "--answer-url", url],
                cfg={**cfg, "pages": {"theme_key": THEME_ID, **conf}}, personas=personas)


def forms(text: str) -> list[list[tuple]]:
    """The start tags inside each form, the form tag first."""
    tags = Tags()
    tags.feed(text)
    out, inside = [], False
    for tag, attrs in tags.found:
        if tag == "form":
            out.append([])
            inside = True
        if inside and tag in ("form", "input", "button", "label"):
            out[-1].append((tag, dict(attrs)))
    return out


def test_form_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(pages, "_utc", lambda: BUILT)
    cfg, personas = make_cfg(tmp_path)
    st = blocked(cfg, "b1", STORE + "BUILD: BLOCKED - the owner picks the store\n")
    out = tmp_path / "pages"
    assert run_url(cfg, personas, out) == 0
    text = read(out)
    expected = hashlib.sha256((STOPPED + "\n" + "\n".join(THREE)).encode("utf-8")).hexdigest()[:16]
    assert expected == "548a4d62fb54d552" == pages.qhash(st["question"], THREE)
    assert forms(text) == [[
        ("form", {"method": "post", "action": URL}),
        ("input", {"type": "hidden", "name": "id", "value": "b1"}),
        ("input", {"type": "hidden", "name": "qhash", "value": expected}),
        ("label", {}),
        ("input", {"type": "password", "name": "key", "required": None, "autocomplete": "current-password"}),
        ("button", {"type": "submit", "name": "option", "value": "A"}),
        ("button", {"type": "submit", "name": "option", "value": "B"}),
        ("button", {"type": "submit", "name": "option", "value": "C"})]]
    s = sections(text)["b1"]
    assert s.index("<li>C: Ask later.</li>") < s.index("<form")  # the option lines stay, with the flag too
    assert '<button type="submit" name="option" value="A">A</button>' in s


def test_the_qhash_is_of_the_text_and_the_option_lines():
    q = {"text": "Q é"}
    assert pages.qhash(q, ["A) x", "B) y"]) == hashlib.sha256("Q é\nA) x\nB) y".encode("utf-8")).hexdigest()[:16]
    assert pages.qhash(q, ["A) x", "B) y"]) != pages.qhash(q, ["A) x", "B) z"])
    assert pages.qhash(q, ["A) x"]) != pages.qhash({"text": "Q é\n"}, ["A) x"])


def test_the_answer_url_and_the_id_are_escaped(tmp_path):
    cfg, personas = make_cfg(tmp_path)
    st = blocked(cfg, "b1", STORE + "BUILD: BLOCKED - x\n")
    st["id"] = EVIL
    state.save(cfg, {**st, "id": "b1"})
    (state.home(cfg) / "b1" / "state.json").write_text(json.dumps(st), encoding="utf-8")
    out = tmp_path / "pages"
    assert run_url(cfg, personas, out, '/answer?a=1&b="x"') == 0
    text = read(out)
    assert '<form method="post" action="/answer?a=1&amp;b=&quot;x&quot;">' in text
    assert '<input type="hidden" name="id" value="&lt;script&gt;&quot;x">' in text


@pytest.mark.parametrize("url", ["", "javascript:alert(1)", "data:text/html,x", "ftp://example.invalid/a"])
def test_an_answer_url_that_is_not_http_is_a_usage_error(tmp_path, capsys, url):
    cfg, personas = make_cfg(tmp_path)
    blocked(cfg, "b1", STORE + "BUILD: BLOCKED - x\n")
    assert run_url(cfg, personas, tmp_path / "pages", url) == 2
    assert capsys.readouterr().err.startswith("wb: --answer-url ")
    assert not (tmp_path / "pages").exists()


@pytest.fixture
def mixed(waits):
    """The waits of each kind, a wait with 3 options, and a spec-check and a case 4 wait whose replies end
    BLOCKED after a lettered run."""
    cfg, personas = waits
    blocked(cfg, "b1", STORE + "BUILD: BLOCKED - x\n")
    blocked(cfg, "b2", STORE + "BUILD: BLOCKED - x\n", stage="spec-check", spec_questions=True)
    blocked(cfg, "b3", STORE + "REWORK: BLOCKED - x\n", stage="rework")
    st = state.load(cfg, "b3")
    st["question"]["case"] = 4
    state.save(cfg, st)
    for n, case in (("b4", 1), ("b5", 3)):
        blocked(cfg, n, STORE + "BUILD: BLOCKED - x\n")
        st = state.load(cfg, n)
        st["question"]["case"] = case
        state.save(cfg, st)
    return cfg, personas


def all_tags(out) -> dict:
    found = {}
    for f in sorted(out.iterdir()):
        if f.name != pages.MARKER:
            tags = Tags()
            tags.feed(no_theme(f.read_text(encoding="utf-8")))
            found[f.name] = tags.found
    return found


def test_no_form_without_the_flag(mixed, tmp_path):
    out = tmp_path / "pages"
    assert run(*mixed, out) == 0
    for name, found in all_tags(out).items():
        assert not {"form", "input", "script"} & {t for t, _ in found}, name
        choices = [dict(a).get("data-theme-choice") for t, a in found if t == "button"]
        assert choices == ["light", "dark", "system"], name  # the only buttons: the theme group, in no form
    assert "<li>A) Keep the file.</li>" in sections(read(out))["b1"]


def test_a_form_only_for_a_case_2_wait_with_options(mixed, tmp_path):
    out = tmp_path / "pages"
    assert run_url(*mixed, out) == 0
    s = sections(read(out))
    with_form = sorted(n for n, text in s.items() if "<form" in text)
    assert with_form == ["b1"]  # not w1-w3 (spec_questions), w4-w5 (case 2, no options), b2-b5, w6-w9
    for n in ("b2", "b3", "b4", "b5", "w4", "w5"):
        assert "<input" not in s[n] and "<button" not in s[n], n


@pytest.mark.parametrize("conf", [{}, "rows"])
def test_the_pages_hold_no_script_and_the_only_form_target_is_url(mixed, tmp_path, conf):
    out = tmp_path / "pages"
    assert run_url(*mixed, out, **(ROWS_CONF if conf == "rows" else conf)) == 0
    for name, found in all_tags(out).items():
        text = no_theme((out / name).read_text(encoding="utf-8")).lower()
        assert "<script" not in text and "javascript:" not in text, name
        assert not [k for _, attrs in found for k, _ in attrs if k.startswith("on")], name
        actions = [v for t, attrs in found for k, v in attrs if k in ("action", "formaction")]
        assert actions == ([URL] if name == "index.html" else []), name
        links = [v for _, attrs in found for k, v in attrs if k in ("href", "src")]
        assert all(re.fullmatch(r"[a-z-]+\.html", v) for v in links), (name, links)


# the owner feed, the 4 sections of the owner's work and their order.

WRITTEN = "2026-11-01T11:00:00Z"
WRITTEN_AT = f'<time datetime="{WRITTEN}">Nov 1, 11:00 AM UTC</time>'  # with no pages.time_zone
GITHUB = {"base_sha": "0" * 40, "url": "https://github.com/o/r.git"}
ANSWER = ("<p>Answer with the <code>answer</code> command: <code>python -m wb answer {} --file &lt;file&gt;</code>."
          "</p>")


def write_feed(cfg, data) -> None:
    raw = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
    (state.home(cfg) / pages.FEED).write_bytes(raw)


def a_feed(**fields) -> dict:
    return {"written": WRITTEN, "done": [], "items": {}, "steps": [], **fields}


def wait_q(case=2, **queued) -> dict:
    q = {"case": case, "text": f"Case {case}?", "run_folder": None}
    return {**q, "queued": queued} if queued else q


def test_a_good_feed_is_read(tmp_path):
    cfg, _ = make_cfg(tmp_path)
    state.home(cfg).mkdir()
    write_feed(cfg, a_feed(done=["1", 2], items={"1": {"priority": 2, "created": "2026-10-01T08:00:00Z", "pr": 7},
                                                  "2": {}, "3": {"priority": 1.5}},
                           steps=[{"id": "s1", "text": "Renew the key."}]))
    assert pages.feed(state.home(cfg)) == {
        "written": WRITTEN, "done": {"1"},
        "items": {"1": {"priority": 2, "created": pages._time("2026-10-01T08:00:00+00:00"), "pr": 7},
                  "2": {"priority": None, "created": None, "pr": None},
                  "3": {"priority": 1.5, "created": None, "pr": None}},
        "steps": [{"id": "s1", "text": "Renew the key."}]}


def test_a_missing_or_bad_feed_gives_none(tmp_path):
    cfg, _ = make_cfg(tmp_path)
    home = state.home(cfg)
    home.mkdir()
    assert pages.feed(home) is None
    for bad in (b"{not json", b"\xff\xfe", b"[]", b"null",
                json.dumps(a_feed(written="not a time")), json.dumps(a_feed(written=1)),
                json.dumps({k: v for k, v in a_feed().items() if k != "steps"}),
                json.dumps(a_feed(done={})), json.dumps(a_feed(items=[])), json.dumps(a_feed(steps={})),
                b"[" * 200000):  # too deep for the JSON reader
        write_feed(cfg, bad.encode("utf-8") if isinstance(bad, str) else bad)
        assert pages.feed(home) is None, bad
    (home / pages.FEED).unlink()
    (home / pages.FEED).mkdir()  # a folder at the feed path; read_items still finds no item there
    assert pages.feed(home) is None


def test_the_feed_size_limit(tmp_path):
    cfg, _ = make_cfg(tmp_path)
    state.home(cfg).mkdir()
    raw = json.dumps(a_feed()).encode("utf-8")
    write_feed(cfg, raw.ljust(1024 * 1024))
    assert pages.feed(state.home(cfg))["written"] == WRITTEN
    write_feed(cfg, raw.ljust(1024 * 1024 + 1))
    assert pages.feed(state.home(cfg)) is None
    write_feed(cfg, raw.ljust(2 * 1024 * 1024))
    assert pages.feed(state.home(cfg)) is None


def test_an_entry_of_a_wrong_type_is_skipped(tmp_path):
    cfg, _ = make_cfg(tmp_path)
    state.home(cfg).mkdir()
    good = {"priority": 1, "created": "2026-10-01T08:00:00", "pr": 3}
    write_feed(cfg, a_feed(
        done=["1", None, ["2"]],
        items={"ok": good, "p": {**good, "priority": "1"}, "b": {**good, "priority": True},
               "c": {**good, "created": "yesterday"}, "c2": {**good, "created": 5}, "r": {**good, "pr": True},
               "r2": {**good, "pr": "3"}, "r3": {**good, "pr": 0}, "r4": {**good, "pr": 2.0}, "n": None, "l": [1],
               "nan": {**good, "priority": float("nan")}, "inf": {**good, "priority": float("inf")}},
        steps=[{"id": "s1", "text": "t"}, {"id": "s2"}, {"text": "t"}, {"id": 3, "text": "t"},
               {"id": "s4", "text": None}, "s5"]))
    fd = pages.feed(state.home(cfg))
    assert fd["done"] == {"1"} and list(fd["items"]) == ["ok"]
    assert fd["items"]["ok"]["created"] == pages._time("2026-10-01T08:00:00+00:00")  # no zone: UTC
    assert fd["steps"] == [{"id": "s1", "text": "t"}]


def test_a_priority_above_the_float_range_does_not_stop_the_build(home, tmp_path):
    cfg, personas = home
    write_feed(cfg, a_feed(items={"4": {"priority": 10 ** 400}, "ok": {"priority": 1}}))  # 4 is a ready item
    fd = pages.feed(state.home(cfg))
    assert fd["items"]["4"]["priority"] == 10 ** 400 and fd["items"]["ok"]["priority"] == 1
    assert sorted(["4", "ok"], key=pages.rank([], fd)) == ["ok", "4"]
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    assert f"Owner feed written at {WRITTEN_AT}.</p>" in read(out)


def test_more_than_50_steps_gives_no_steps(tmp_path):
    cfg, _ = make_cfg(tmp_path)
    state.home(cfg).mkdir()
    steps = [{"id": f"s{n}", "text": "t"} for n in range(51)]
    write_feed(cfg, a_feed(done=["1"], steps=steps))
    fd = pages.feed(state.home(cfg))
    assert fd["steps"] == [] and fd["done"] == {"1"}
    write_feed(cfg, a_feed(steps=steps[:50]))
    assert len(pages.feed(state.home(cfg))["steps"]) == 50


@pytest.mark.parametrize("raw", [None, b"{not json", b" " * (2 * 1024 * 1024), json.dumps(a_feed(done=1)).encode(),
                                 b"[" * 200000], ids=["missing", "not-json", "2-mb", "bad-done", "too-deep"])
def test_a_bad_feed_does_not_stop_the_build(home, tmp_path, raw):
    cfg, personas = home
    if raw is not None:
        write_feed(cfg, raw)
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    text = read(out)
    assert ("<p>Items: 5</p>\n<p>Needs your input: 1. Needs merge: not known. Steps outside wb: 0."
            ' Queued: 0. No owner feed.</p>\n<section class="card">\n<h2>Needs your input</h2>\n') in text
    assert ('<h2>Needs merge</h2>\n<p>No owner feed: merges are not known.</p>\n</section>\n<section class="card">\n'
            "<h2>Steps outside wb</h2>") in text


@pytest.fixture
def owner(tmp_path, monkeypatch):
    """Waits that need the owner now (a1-a3), held waits (a4, a5), ready items (r1-r5) and a feed."""
    monkeypatch.setattr(pages, "_utc", lambda: BUILT)
    cfg, personas = make_cfg(tmp_path)
    item(cfg, "a1", "build", "waiting", question=wait_q())
    item(cfg, "a2", "build", "waiting", question=wait_q(reason="after d1", after="d1"))
    item(cfg, "a3", "review", "waiting", question=wait_q(4, reason="by hand", after=None))
    item(cfg, "a4", "build", "waiting", question=wait_q(reason=EVIL, after="r1"))
    item(cfg, "a5", "build", "waiting", question=wait_q(reason="no after", after=None))
    item(cfg, "r1", "delivery", "ready", remote=GITHUB)
    item(cfg, "r2", "delivery", "ready", remote=GITHUB)
    item(cfg, "r3", "delivery", "ready", remote={**GITHUB, "url": "https://gitlab.example.invalid/o/r.git"})
    item(cfg, "r4", "delivery", "ready", remote=GITHUB)
    item(cfg, "r5", "delivery", "ready")
    write_feed(cfg, a_feed(done=["d1", "r4"], items={"r1": {"pr": 7}, "r3": {"pr": 8}, "r4": {"pr": 9}, "r5": {"pr": 10}},
                           steps=[{"id": "s1", "text": EVIL}]))
    return cfg, personas


def owner_part(text: str, heading: str) -> str:
    return text.split(f"<h2>{heading}</h2>\n")[1].split("</section>")[0]


def ids(part: str) -> list[str]:
    return re.findall(r"<h3>([^:]+):", part) or re.findall(r"<li>([^:]+):", part)


def test_the_4_sections(owner, tmp_path):
    out = tmp_path / "pages"
    assert run(*owner, out) == 0
    text = read(out)
    assert (f"<p>Items: 10</p>\n<p>Needs your input: 3. Needs merge: 4. Steps outside wb: 1. Queued: 2."
            f' Owner feed written at {WRITTEN_AT}.</p>\n<section class="card">\n<h2>Needs your input</h2>\n') in text
    assert text.index("<h2>Queued</h2>") < text.index("<h2>Status</h2>")
    assert ids(owner_part(text, "Needs your input")) == ["a1", "a2", "a3"]
    assert sections(text)["a1"] == f"<h3>a1: case 2</h3>\n<pre>Case 2?</pre>\n{ANSWER.format('a1')}\n"
    assert sections(text)["a3"] == "<h3>a3: case 4</h3>\n<pre>Case 4?</pre>\n" + AT.format(FILLED["answer"].format("a3")) + "\n"
    assert owner_part(text, "Needs merge") == (
        '<ul>\n<li>r1: <a href="https://github.com/o/r/pull/7">PR 7</a></li>\n<li>r2: No recorded PR.</li>\n'
        "<li>r3: No recorded PR.</li>\n<li>r5: No recorded PR.</li>\n</ul>\n")
    assert owner_part(text, "Steps outside wb") == "<ul>\n<li>s1: &lt;script&gt;&quot;x</li>\n</ul>\n"
    assert owner_part(text, "Queued") == (
        "<h3>a4: case 2</h3>\n<p>Queued: &lt;script&gt;&quot;x</p>\n<pre>Case 2?</pre>\n" + ANSWER.format("a4") + "\n"
        "<h3>a5: case 2</h3>\n<p>Queued: no after</p>\n<pre>Case 2?</pre>\n" + ANSWER.format("a5") + "\n")
    assert "<script" not in no_theme(text)


def test_with_no_feed_a_hold_stays_queued_and_merges_are_not_known(owner, tmp_path):
    cfg, personas = owner
    (state.home(cfg) / pages.FEED).unlink()
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    text = read(out)
    assert ("<p>Needs your input: 2. Needs merge: not known. Steps outside wb: 0. Queued: 3."
            " No owner feed.</p>\n") in text
    assert ids(owner_part(text, "Needs your input")) == ["a1", "a3"]
    assert owner_part(text, "Needs merge") == "<p>No owner feed: merges are not known.</p>\n"
    assert owner_part(text, "Steps outside wb") == "<p>No step outside wb.</p>\n"
    assert ids(owner_part(text, "Queued")) == ["a2", "a4", "a5"]
    assert "<p>Queued: after d1</p>" in sections(text)["a2"]


def test_a_queued_case_2_wait_keeps_its_answer_form(tmp_path):
    cfg, personas = make_cfg(tmp_path)
    folder = run_folder(cfg, "q1", {"reply.txt": "Which?\nA) One.\nB) Two.\n\nBUILD: BLOCKED - which\n"})
    item(cfg, "q1", "build", "waiting",
         question={"case": 2, "text": "Which?", "run_folder": folder, "queued": {"reason": "later", "after": "x"}})
    out = tmp_path / "pages"
    assert run_url(cfg, personas, out) == 0
    part = owner_part(read(out), "Queued")
    assert part.startswith("<h3>q1: case 2</h3>\n<p>Queued: later</p>\n<pre>Which?</pre>\n") and "<form" in part


def test_empty_sections_say_so(tmp_path, monkeypatch):
    cfg, personas = make_cfg(tmp_path)
    item(cfg, "1", "build", "running")
    write_feed(cfg, a_feed())
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    text = read(out)
    card = '</section>\n<section class="card">\n'
    assert ("<h2>Needs your input</h2>\n<p>No item needs your input.</p>\n"
            f"{card}<h2>Needs merge</h2>\n<p>No item waits for a merge.</p>\n"
            f"{card}<h2>Steps outside wb</h2>\n<p>No step outside wb.</p>\n"
            f"{card}<h2>Queued</h2>\n<p>No queued wait.</p>\n{card}<h2>Status</h2>\n") in text


def test_the_only_new_link_is_a_pr_link(owner, tmp_path):
    cfg, personas = owner
    st = state.load(cfg, "r2")
    st["remote"]["url"] = "javascript:alert(1)//github.com/o/r"
    state.save(cfg, st)
    st = state.load(cfg, "r3")  # a GitHub prefix with more after it: not a clone URL
    st["remote"]["url"] = "https://github.com/o/r/../../evil/x"
    state.save(cfg, st)
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    for name, found in all_tags(out).items():
        assert not {"script", "form", "iframe", "img"} & {t for t, _ in found}, name
        links = [v for _, attrs in found for k, v in attrs if k in ("href", "src", "action")]
        assert all(re.fullmatch(PAGE_OR_PR, v) for v in links), (name, links)
        pr7, pr9 = "https://github.com/o/r/pull/7", "https://github.com/o/r/pull/9"  # r4 is done: not on index
        assert [v for v in links if not v.endswith(".html")] == {  # the PR next to each id
            "index.html": [pr7], "results.html": [pr7, pr9], "stage-delivery.html": [pr7, pr9],
            "status-ready.html": [pr7, pr9]}.get(name, []), name
    assert "<li>r3: No recorded PR.</li>" in read(out)


def test_a_queued_value_that_is_not_an_object_is_no_hold(owner, tmp_path):
    cfg, personas = owner
    item(cfg, "a6", "build", "waiting", question={**wait_q(), "queued": "later"})
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    assert ids(owner_part(read(out), "Needs your input")) == ["a1", "a2", "a3", "a6"]


def test_section_order(tmp_path):
    """Blocks (most first), then priority (1 first), then created (oldest first), then id; missing values last."""
    cfg, personas = make_cfg(tmp_path)
    old, new = "2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z"
    entries = {"m6": {"priority": 3, "created": new}, "m5": {"priority": 1, "created": "2026-01-09T00:00:00Z"},
               "m2": {"priority": 2, "created": old}, "m1": {"priority": 2, "created": new},
               "m3": {"priority": 2, "created": new}, "m4": {"priority": 2, "created": new}, "m7": {"priority": 2},
               "w2": {"priority": 1}, "s2": {"priority": 1}}
    for n in entries:
        if n.startswith("m"):
            item(cfg, n, "delivery", "ready", remote=GITHUB)
    item(cfg, "m0", "delivery", "ready", remote=GITHUB)  # no feed entry
    item(cfg, "q1", "build", "waiting", question=wait_q(reason="x", after="m6"))
    item(cfg, "q2", "build", "waiting", question=wait_q(reason="x", after="w1"))
    item(cfg, "w1", "build", "waiting", question=wait_q())
    item(cfg, "w2", "build", "waiting", question=wait_q())
    item(cfg, "w3", "build", "waiting", question=wait_q())
    write_feed(cfg, a_feed(items=entries, steps=[{"id": "s1", "text": "a"}, {"id": "s2", "text": "b"},
                                                 {"id": "s0", "text": "c"}]))
    out = tmp_path / "pages"
    assert run(cfg, personas, out) == 0
    text = read(out)
    assert ids(owner_part(text, "Needs merge")) == ["m6", "m5", "m2", "m1", "m3", "m4", "m7", "m0"]
    assert ids(owner_part(text, "Needs your input")) == ["w1", "w2", "w3"]  # blocks, then priority, then id
    assert ids(owner_part(text, "Steps outside wb")) == ["s2", "s0", "s1"]
    assert ids(owner_part(text, "Queued")) == ["q1", "q2"]


# ADO and PR links for each item, and local times.

ADO = "https://ado.example/proj/_workitems/edit/"  # the fake base of pages.ado_url; the real one is private config


def ado(n) -> str:
    return f'<a href="{ADO}{n}">{n}</a>'


def run_conf(cfg, personas, out, **conf) -> int:
    return main(["pages", "--out", str(out)], cfg={**cfg, "pages": {"theme_key": THEME_ID, **conf}}, personas=personas)


@pytest.fixture
def linked(tmp_path, monkeypatch):
    """An item id in each place: a wait (12) at the round limit, a ready item (34), a feed step (56), and 2 ids
    that are no ADO number (x7, 07)."""
    monkeypatch.setattr(pages, "_utc", lambda: BUILT)
    cfg, personas = make_cfg(tmp_path)
    item(cfg, "12", "build", "waiting", usd=3.0, rounds_used=2, question=wait_q(),
         steps=steps(cfg, "12", {"end": "2026-11-01T09:00:00Z"}))
    item(cfg, "34", "delivery", "ready", usd=1.0, remote=GITHUB)
    item(cfg, "x7", "build", "running", usd=2.0, rounds_used=2)
    item(cfg, "07", "build", "running", usd=0.5)
    write_feed(cfg, a_feed(steps=[{"id": "56", "text": "Renew the key."}, {"id": "s1", "text": "t"}]))
    return cfg, personas


def test_ado_link_in_each_id_place(linked, tmp_path):
    out = tmp_path / "pages"
    assert run_conf(*linked, out, ado_url=ADO) == 0
    text = read(out)
    assert f"<h3>{ado(12)}: case 2</h3>" in text
    assert f"<li>{ado(34)}: No recorded PR.</li>" in owner_part(text, "Needs merge")
    assert owner_part(text, "Steps outside wb") == (
        f"<ul>\n<li>{ado(56)}: Renew the key.</li>\n<li>s1: t</li>\n</ul>\n")
    assert "python -m wb answer 12 --file" in text  # a command keeps the plain id
    assert rows(read(out, "status-waiting.html"))[0].startswith(f"<tr><td>{ado(12)}</td>")
    assert rows(read(out, "stage-delivery.html"))[0].startswith(f"<tr><td>{ado(34)}</td>")
    results = read(out, "results.html")
    assert measure(results, "The 10 items with the highest USD")[:2] == [[ado(12), "3.00"], ["x7", "2.00"]]
    assert measure(results, "Items at the round limit") == [[ado(12), "2 / 2"], ["x7", "2 / 2"]]
    assert measure(results, "The wait of each waiting item") == [[ado(12), "2", "3.00"]]


def test_a_letter_id_or_leading_zero_is_plain(linked, tmp_path):
    out = tmp_path / "pages"
    assert run_conf(*linked, out, ado_url=ADO) == 0
    running = read(out, "status-running.html")
    assert [r.split("</td>")[0] for r in rows(running)] == ["<tr><td>07", "<tr><td>x7"]
    for f in out.iterdir():
        assert "edit/x7" not in f.read_text(encoding="utf-8") and "edit/07" not in f.read_text(encoding="utf-8")


@pytest.mark.parametrize("conf", [{}, {"ado_url": ""}])
def test_with_no_ado_url_an_id_is_plain(linked, tmp_path, conf):
    out = tmp_path / "pages"
    assert run_conf(*linked, out, **conf) == 0
    assert "ado.example" not in "".join(f.read_text(encoding="utf-8") for f in out.iterdir())
    assert rows(read(out, "status-waiting.html"))[0].startswith("<tr><td>12</td>")


@pytest.mark.parametrize("url", ["http://ado.example/proj/_workitems/edit/", "https://ado.example/proj/_workitems/edit",
                                 "javascript:alert(1)//", "https:///", "https://ado.example/a b/", 7, ["x"],
                                 "https://ado.example:bad/", "https://ado.example:99999/", "https://ado.example:0/",
                                 "https://h/?q=/", "https://h/a#/", 'https://h/a"b/', "https://h/<b>/"])
def test_a_bad_ado_url_is_a_usage_error_and_keeps_dir(linked, tmp_path, capsys, url):
    out = tmp_path / "pages"
    out.mkdir()
    (out / "marker").write_text("keep", encoding="utf-8")
    before = sorted(p.name for p in tmp_path.iterdir())
    assert run_conf(*linked, out, ado_url=url) == 2
    err = capsys.readouterr().err
    assert err.startswith("wb: pages.ado_url ") and str(url) not in err  # the address is private: never printed
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert [p.name for p in out.iterdir()] == ["marker"]


def test_an_ado_url_with_a_port_links(linked, tmp_path):
    base = "https://ado.example:8443/proj/_workitems/edit/"
    for url in (base, ADO):
        out = tmp_path / "pages"
        assert run_conf(*linked, out, ado_url=url) == 0
        assert rows(read(out, "status-waiting.html"))[0].startswith(f'<tr><td><a href="{url}12">12</a></td>')


def test_the_escapes_of_the_id_link():
    assert pages.item_id("12", "https://h/a&b/") == '<a href="https://h/a&amp;b/12">12</a>'
    assert pages.item_id("12", ADO, '<a href="x">PR 3</a>') == f'{ado(12)} <a href="x">PR 3</a>'  # pr as given


def test_read_items_gives_a_plain_id(home):
    cfg, _ = home
    (state.home(cfg) / "x").mkdir()  # raw: state.save refuses the id, and a Windows file name cannot hold <
    st = {**state.new("x"), "id": "<x>", "stage": "build", "status": "waiting", "question": wait_q()}
    (state.home(cfg) / "x" / "state.json").write_text(json.dumps(st), encoding="utf-8")
    items, _ = pages.read_items(state.home(cfg))  # no build: no linked id
    by_id = {it["id"]: it for it in items}
    assert pages.row(by_id["<x>"])[0] == "&lt;x&gt;" and pages.row(by_id["3"])[0] == "3"
    assert pages.waiting(by_id["<x>"]).startswith("<h3>&lt;x&gt;: case 2</h3>")
    assert "<td>&lt;x&gt;</td>" in pages.results(items, pages._time(BUILT), BUILT_AT)


def test_the_item_cell_is_escaped(evil, tmp_path):
    cfg, personas = evil
    item(cfg, "31", "build", "waiting", question=wait_q(EVIL))  # an other cell: escaped next to the helper output
    out = tmp_path / "pages"
    assert run_conf(cfg, personas, out, ado_url=ADO) == 0
    escaped = "&lt;script&gt;&quot;x"
    assert f"<tr><td>{ado(31)}</td><td>{escaped}</td><td>no data</td></tr>" in read(out, "results.html")
    assert f"<tr><td>{escaped}</td>{badge('build')}" in read(out, "status-waiting.html")
    assert f"<tr><td>{escaped}</td><td>2</td><td>no data</td></tr>" in read(out, "results.html")
    assert f"<h3>{escaped}: case 2</h3>" in read(out)


def pr(repo: str, n: int) -> str:
    return f'<a href="https://github.com/{repo}/pull/{n}">PR {n}</a>'


def record(n) -> dict:
    """The records of an item whose PR record holds the number n."""
    return {"pr": {"number": n, "time": "2026-11-01T10:00:00Z"}}


def test_the_pr_record_wins_over_the_feed(linked, tmp_path):
    cfg, personas = linked
    st = state.load(cfg, "34")
    st["records"] = record(5)
    state.save(cfg, st)
    write_feed(cfg, a_feed(items={"34": {"pr": 9}}, steps=[{"id": "34", "text": "Tag it."}]))
    out = tmp_path / "pages"
    assert run_conf(cfg, personas, out, ado_url=ADO) == 0
    text = read(out)
    assert owner_part(text, "Needs merge") == f"<ul>\n<li>{ado(34)}: {pr('o/r', 5)}</li>\n</ul>\n"  # 1 PR link
    assert owner_part(text, "Steps outside wb") == f"<ul>\n<li>{ado(34)} {pr('o/r', 5)}: Tag it.</li>\n</ul>\n"
    assert rows(read(out, "status-ready.html"))[0].startswith(f"<tr><td>{ado(34)} {pr('o/r', 5)}</td>")
    assert "pull/9" not in "".join(f.read_text(encoding="utf-8") for f in out.iterdir())


def test_a_feed_pr_alone_links(linked, tmp_path):
    cfg, personas = linked
    write_feed(cfg, a_feed(items={"34": {"pr": 9}}))
    out = tmp_path / "pages"
    assert run_conf(cfg, personas, out) == 0  # with no ado_url: the PR link alone
    assert f"<li>34: {pr('o/r', 9)}</li>" in owner_part(read(out), "Needs merge")
    assert rows(read(out, "status-ready.html"))[0].startswith(f"<tr><td>34 {pr('o/r', 9)}</td>")
    assert measure(read(out, "results.html"), "The 10 items with the highest USD")[2] == [f"34 {pr('o/r', 9)}", "1.00"]


def test_an_int_id_gets_the_feed_pr(linked, tmp_path):
    cfg, personas = linked
    (state.home(cfg) / "7").mkdir()  # raw: a hand-edited id can be an int, and state.save refuses it
    st = {**state.new("7"), "id": 7, "stage": "build", "status": "running", "remote": GITHUB}
    (state.home(cfg) / "7" / "state.json").write_text(json.dumps(st), encoding="utf-8")
    write_feed(cfg, a_feed(items={"7": {"pr": 12}}))
    out = tmp_path / "pages"
    assert run_conf(cfg, personas, out) == 0
    assert rows(read(out, "status-running.html"))[1].startswith(f"<tr><td>7 {pr('o/r', 12)}</td>")  # after 07


@pytest.mark.parametrize("records", [record(0), record(True), record("7"), record(1.5), record(-3), record(None),
                                     {"pr": 7}, {"pr": [7]}, [], "x"])
@pytest.mark.parametrize("fed", [True, False])
def test_a_bad_pr_record_falls_back(linked, tmp_path, records, fed):
    cfg, personas = linked
    st = state.load(cfg, "34")
    st["records"] = records
    state.save(cfg, st)
    write_feed(cfg, a_feed(items={"34": {"pr": 9}} if fed else {}))
    out = tmp_path / "pages"
    assert run_conf(cfg, personas, out) == 0
    row = rows(read(out, "status-ready.html"))[0]
    assert row.startswith(f"<tr><td>34 {pr('o/r', 9)}</td>" if fed else "<tr><td>34</td>")
    assert ("No recorded PR." in owner_part(read(out), "Needs merge")) is not fed


def test_2_items_link_to_2_repos(linked, tmp_path):
    cfg, personas = linked
    st = state.load(cfg, "12")
    st.update(remote={**GITHUB, "url": "git" + "@" + "github.com:p/q.git"}, records=record(3))
    state.save(cfg, st)
    write_feed(cfg, a_feed(items={"34": {"pr": 9}}))
    out = tmp_path / "pages"
    assert run_conf(cfg, personas, out, ado_url=ADO) == 0
    assert f"<h3>{ado(12)} {pr('p/q', 3)}: case 2</h3>" in read(out)
    results = read(out, "results.html")
    assert measure(results, "The wait of each waiting item") == [[f"{ado(12)} {pr('p/q', 3)}", "2", "3.00"]]
    assert measure(results, "Items at the round limit")[0] == [f"{ado(12)} {pr('p/q', 3)}", "2 / 2"]
    assert rows(read(out, "status-ready.html"))[0].startswith(f"<tr><td>{ado(34)} {pr('o/r', 9)}</td>")


def test_a_pr_with_no_github_remote_has_no_link(linked, tmp_path):
    cfg, personas = linked
    st = state.load(cfg, "34")
    st.update(remote={**GITHUB, "url": "https://gitlab.example.invalid/o/r.git"}, records=record(5))
    state.save(cfg, st)
    out = tmp_path / "pages"
    assert run_conf(cfg, personas, out) == 0
    assert rows(read(out, "status-ready.html"))[0].startswith("<tr><td>34</td>")
    assert "<li>34: No recorded PR.</li>" in owner_part(read(out), "Needs merge")


def test_an_evil_state_gives_no_other_link(evil, tmp_path):
    cfg, personas = evil
    for folder, id_, fields in (("e2", '12" onclick="x', {"records": record('7"><script>'), "remote": {
            **GITHUB, "url": 'https://github.com/o/r"><img src=x>/y'}}),
                                ("e3", "javascript:alert(1)//", {"records": record(-1), "remote": GITHUB})):
        (state.home(cfg) / folder).mkdir()  # raw: state.save refuses a bad id
        st = {**state.new(folder), "id": id_, "stage": "build", "status": "running", **fields}
        (state.home(cfg) / folder / "state.json").write_text(json.dumps(st), encoding="utf-8")
    item(cfg, "21", "build", "running", records=record(4), remote=GITHUB)  # the 2 allowed forms
    item(cfg, "22", "build", "running", remote={**GITHUB, "url": "javascript:alert(1)//github.com/o/r"})
    write_feed(cfg, a_feed(items={EVIL: {"pr": "<script>"}, "e3": {"pr": 1.5}, "22": {"pr": 6}},
                           steps=[{"id": EVIL, "text": EVIL}, {"id": "javascript:alert(1)", "text": "x"}]))
    out = tmp_path / "pages"
    assert run_conf(cfg, personas, out, ado_url=ADO) == 0
    seen = set()
    for name, found in all_tags(out).items():
        assert not {"script", "form", "iframe", "img"} & {t for t, _ in found}, name
        assert not [k for _, attrs in found for k, _ in attrs if k.startswith("on")], name
        links = [v for _, attrs in found for k, v in attrs if k in ("href", "src", "action")]
        assert all(re.fullmatch(PAGE_OR_PR, v) for v in links), (name, links)
        seen.update(v for v in links if not v.endswith(".html"))
    assert seen == {f"{ADO}21", f"{ADO}22", "https://github.com/o/r/pull/4"}  # 22: no GitHub remote, no PR link


ZONE = "America/New_York"  # the tests need tzdata on Windows: the test extra of pyproject.toml


def shown(utc: str, local: str) -> str:
    return f'<time datetime="{utc}">{local}</time>'


@pytest.mark.parametrize("v,zone,expected", [
    ("2026-11-01T12:00:00Z", ZONE, shown("2026-11-01T12:00:00Z", "Nov 1, 7:00 AM EST")),
    ("2026-07-04T14:05:09Z", ZONE, shown("2026-07-04T14:05:09Z", "Jul 4, 10:05 AM EDT")),
    ("2026-11-02T05:00:00Z", ZONE, shown("2026-11-02T05:00:00Z", "Nov 2, 12:00 AM EST")),  # midnight
    ("2026-11-01T17:00:00Z", ZONE, shown("2026-11-01T17:00:00Z", "Nov 1, 12:00 PM EST")),  # noon
    ("2026-12-31T23:59:59Z", None, shown("2026-12-31T23:59:59Z", "Dec 31, 11:59 PM UTC")),
    ("2026-01-05T06:30:15.5-05:00", None, shown("2026-01-05T11:30:15Z", "Jan 5, 11:30 AM UTC")),  # to the second
    ("2026-02-05T06:30:00", ZONE, shown("2026-02-05T06:30:00Z", "Feb 5, 1:30 AM EST")),  # no zone: UTC
    ("0001-01-01T00:00:00Z", ZONE, "0001-01-01T00:00:00Z"),  # out of range in the zone: the text as it is
])
def test_the_local_time(v, zone, expected):
    from zoneinfo import ZoneInfo
    assert pages.local_time(v, ZoneInfo(zone) if zone else pages.dt.timezone.utc) == expected


def test_the_month_names_are_english():
    assert [pages.local_time(f"2026-{m:02d}-15T00:00:00Z", pages.dt.timezone.utc).split(">")[1][:3]
            for m in range(1, 13)] == ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov",
                                       "Dec"]


@pytest.mark.parametrize("zone,built,written", [
    (ZONE, shown(BUILT, "Nov 1, 7:00 AM EST"), shown(WRITTEN, "Nov 1, 6:00 AM EST")),
    ("", shown(BUILT, "Nov 1, 12:00 PM UTC"), shown(WRITTEN, "Nov 1, 11:00 AM UTC")),
    (None, shown(BUILT, "Nov 1, 12:00 PM UTC"), shown(WRITTEN, "Nov 1, 11:00 AM UTC"))])
def test_built_at_and_the_feed_time_are_local(linked, tmp_path, zone, built, written):
    out = tmp_path / "pages"
    assert run_conf(*linked, out, **({} if zone is None else {"time_zone": zone})) == 0
    for f in sorted(f for f in out.iterdir() if f.name != pages.MARKER):
        assert f"<p>Built at {built}.</p>" in f.read_text(encoding="utf-8"), f.name
    assert f" Owner feed written at {written}.</p>" in read(out)


@pytest.mark.parametrize("zone", ["Not/AZone", "America", "../etc/x", "/etc/localtime", "a\x00b", " UTC", 7, ["UTC"]])
def test_a_bad_time_zone_is_a_usage_error_and_keeps_dir(linked, tmp_path, capsys, zone):
    out = tmp_path / "pages"
    out.mkdir()
    (out / "marker").write_text("keep", encoding="utf-8")
    before = sorted(p.name for p in tmp_path.iterdir())
    assert run_conf(*linked, out, time_zone=zone) == 2
    err = capsys.readouterr().err
    assert err.startswith("wb: pages.time_zone ") and str(zone) not in err
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert [p.name for p in out.iterdir()] == ["marker"]


@pytest.mark.parametrize("zone,week,hours", [(ZONE, "2026-W43", "33.00"), ("", "2026-W44", "33.00")])
def test_delivery_week_is_local(tmp_path, monkeypatch, zone, week, hours):
    monkeypatch.setattr(pages, "_utc", lambda: BUILT)
    cfg, personas = make_cfg(tmp_path)
    item(cfg, "1", "delivery", "ready", steps=steps(cfg, "1", {"end": "2026-10-26T03:00:00Z"}))  # Sunday in the zone
    item(cfg, "2", "build", "waiting", question=wait_q(), steps=steps(cfg, "2", {"end": "2026-10-31T03:00:00Z"}))
    item(cfg, "3", "delivery", "ready", steps=steps(cfg, "3", {"end": "0001-01-01T00:00:00Z"}))  # out of range there
    out = tmp_path / "pages"
    assert run_conf(cfg, personas, out, time_zone=zone) == 0
    text = read(out, "results.html")
    assert measure(text, "Items that reached delivery, by ISO week") == (
        [[week, "1"], ["no data", "1"]] if zone else [["1-W01", "1"], [week, "1"]])
    assert measure(text, "The wait of each waiting item") == [["2", "2", hours]]  # across the end of DST: UTC sums


@pytest.mark.parametrize("built,inside,outside", [
    ("2026-11-05T18:00:00Z", "2026-10-29T18:00:01Z", "2026-10-29T17:30:00Z"),  # 1 PM EST, 7 days back is EDT
    ("2026-03-12T17:00:00Z", "2026-03-05T17:30:00Z", "2026-03-05T17:00:00Z")])  # 1 PM EDT, 7 days back is EST
def test_a_window_is_7_times_24_hours_across_dst(tmp_path, monkeypatch, built, inside, outside):
    """A window of 7 days is 168 hours of UTC, not 7 days of the local clock: 169 or 167 hours across a change."""
    monkeypatch.setattr(pages, "_utc", lambda: built)
    cfg, personas = make_cfg(tmp_path)
    item(cfg, "in", "delivery", "ready", rounds_used=0, steps=steps(
        cfg, "in", {"start": inside, "end": inside, "usd": 1.0}))
    item(cfg, "out", "delivery", "ready", rounds_used=1, steps=steps(
        cfg, "out", {"start": outside, "end": outside, "usd": 2.0}))
    out = tmp_path / "pages"
    assert run_conf(cfg, personas, out, time_zone=ZONE) == 0
    tiles = dict(key_figures(read(out, "results.html")))
    assert (tiles["Delivered, 7 days"], tiles["USD, 7 days"], tiles["Rework-free, 7 days"]) == ("1", "1.00", "100 %")
    assert (tiles["Delivered, 30 days"], tiles["USD, 30 days"]) == ("2", "3.00")


# the Commands card and the filled lines of a case 3 or 4 wait.

MARK = "Zq7"  # in each bad value: a message must not quote any part of it
ROW = {"label": "Run the next step", "host": "example-host", "folder": "~/r", "command": "python3 -m wb run-next 7"}


def bad_row(**fields) -> dict:
    return {k: v for k, v in {**ROW, **fields}.items() if v is not None}


@pytest.mark.parametrize("conf,name", [
    ({"commands": [ROW, bad_row(label=f"{MARK}\nx")]}, "pages.commands row 2"),  # a line break
    ({"commands": [ROW, bad_row(host=f"{MARK}\tx")]}, "pages.commands row 2"),  # a tab
    ({"commands": [ROW, bad_row(command=f"{MARK} é")]}, "pages.commands row 2"),  # not ASCII
    ({"commands": [ROW, bad_row(folder="", command=f"$ {MARK}")]}, "pages.commands row 2"),  # a $ start
    ({"commands": [ROW, bad_row(command=f"{MARK} ")]}, "pages.commands row 2"),  # a trailing space
    ({"commands": [ROW, bad_row(folder="", command=f" {MARK}")]}, "pages.commands row 2"),  # a leading space
    ({"commands": [ROW, bad_row(folder=f"~/{MARK}\n")]}, "pages.commands row 2"),
    ({"commands": [ROW, bad_row(folder="", command=MARK + "x" * 298)]}, "pages.commands row 2"),  # 301
    ({"commands": [ROW, bad_row(folder="~/r", command=MARK + "x" * (298 - len("cd ~/r && ")))]},
     "pages.commands row 2"),  # 301 with the folder
    ({"commands": [ROW, bad_row(label=MARK + "x" * 78)]}, "pages.commands row 2"),  # 81
    ({"commands": [ROW, bad_row(host=MARK + "x" * 78)]}, "pages.commands row 2"),
    ({"commands": [ROW, bad_row(label="")]}, "pages.commands row 2"),
    ({"commands": [ROW, bad_row(host="")]}, "pages.commands row 2"),
    ({"commands": [ROW, bad_row(folder="", command="")]}, "pages.commands row 2"),
    ({"commands": [ROW] * 30 + [bad_row(label=MARK)]}, "pages.commands row 31"),  # 31 rows
    *(({"commands": [ROW, bad_row(**{k: None})]}, "pages.commands row 2") for k in ROW),  # a missing key
    *(({"commands": [ROW, bad_row(**{k: 7})]}, "pages.commands row 2") for k in ROW),  # not a string
    ({"commands": [ROW, bad_row(label=[MARK])]}, "pages.commands row 2"),
    ({"commands": [ROW, MARK]}, "pages.commands row 2"),  # not a table
    ({"commands": ROW}, "pages.commands must be an array"),  # not an array
    ({"commands": MARK}, "pages.commands must be an array"),
    ({"wb_host": f"{MARK}\n"}, "pages.wb_host "),
    ({"wb_host": MARK + "x" * 78}, "pages.wb_host "),
    ({"wb_host": 7}, "pages.wb_host "),
    ({"wb_command": f"$ {MARK}"}, "pages.wb_command "),
    ({"wb_command": f"{MARK} "}, "pages.wb_command "),
    ({"wb_command": f" {MARK}"}, "pages.wb_command "),
    ({"wb_command": f"{MARK}\nx"}, "pages.wb_command "),
    ({"wb_command": f"{MARK}é"}, "pages.wb_command "),
    ({"wb_command": MARK + "x" * 298}, "pages.wb_command "),
    ({"wb_command": 7}, "pages.wb_command "),
])
def test_a_bad_command_value_is_a_usage_error_and_keeps_dir(home, tmp_path, capsys, conf, name):
    out = tmp_path / "pages"
    out.mkdir()
    (out / "marker").write_text("keep", encoding="utf-8")
    before = sorted(p.name for p in tmp_path.iterdir())
    assert run_conf(*home, out, **conf) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"wb: {name}") and MARK not in err and "x" * 20 not in err  # no part of the value
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert [p.name for p in out.iterdir()] == ["marker"]


@pytest.mark.parametrize("conf", [
    {}, {"commands": []}, {"wb_host": "", "wb_command": ""},
    {"commands": [ROW, bad_row(folder="")], "wb_host": "h", "wb_command": "cd ~/r && python3 -m wb"},
    {"commands": [bad_row(label="L" * 80, host="H" * 80, folder="", command="c" * 300)] * 30,
     "wb_host": "h" * 80, "wb_command": "c" * 300},  # each limit
    {"commands": [bad_row(label="~", host=" ", folder="~/r", command="c" * (300 - len("cd ~/r && ")))]},
])
def test_good_command_rows_build(home, tmp_path, conf):
    assert run_conf(*home, tmp_path / "pages", **conf) == 0


ROWS = [ROW, {"label": "Disk use", "host": "other-host", "folder": "", "command": "df -h"}]
EVIL_ROW = {"label": '<x>&"', "host": '<x>&"', "folder": '~/"<x>"', "command": 'echo "<x>" http://example.invalid & ls'}
ROWS_CONF = {"commands": [*ROWS, EVIL_ROW], "wb_host": '<x>&"', "wb_command": 'echo "<x>" & wb'}
NOTES = "<p><code>ready</code> does not show a merge.</p>\n"
CARD = ('<section class="card">\n<h2>Commands</h2>\n<ul>\n'
        '<li><b class="host">example-host</b> Run the next step '
        '<code class="copy">cd ~/r &amp;&amp; python3 -m wb run-next 7</code></li>\n'
        '<li><b class="host">other-host</b> Disk use <code class="copy">df -h</code></li>\n</ul>\n</section>\n')


def after_notes(text: str) -> str:
    """The part of the main block of index.html after the 2 notes below the Stage card."""
    return text.split(NOTES)[1].split("</main>")[0]


def test_the_commands_card(home, tmp_path):
    out = tmp_path / "pages"
    assert run_conf(*home, out, commands=ROWS) == 0
    text = read(out)
    assert after_notes(text) == CARD  # the last card, after the Status and Stage cards and the notes
    assert text.count("<h2>Commands</h2>") == 1
    assert ".host { font-size: 1.125rem; }" in pages.STYLE  # with <b>: the host first, bold, at 1.125rem


def test_the_commands_card_is_escaped(home, tmp_path):
    out = tmp_path / "pages"
    assert run_conf(*home, out, commands=[EVIL_ROW]) == 0
    assert after_notes(read(out)) == (
        '<section class="card">\n<h2>Commands</h2>\n<ul>\n<li><b class="host">&lt;x&gt;&amp;&quot;</b> '
        '&lt;x&gt;&amp;&quot; <code class="copy">cd ~/&quot;&lt;x&gt;&quot; &amp;&amp; echo &quot;&lt;x&gt;&quot; '
        'http://example.invalid &amp; ls</code></li>\n</ul>\n</section>\n')


@pytest.mark.parametrize("conf", [{}, {"commands": []}])
def test_no_rows_no_card(home, tmp_path, conf):
    out = tmp_path / "pages"
    assert run_conf(*home, out, **conf) == 0
    text = read(out)
    assert after_notes(text) == "" and "Commands" not in text


@pytest.mark.parametrize("case,line", [(3, FILLED["approve"]), (4, FILLED["answer"])])
def test_the_filled_line_and_the_host_are_escaped(case, line):
    it = {"id": EVIL, "_id": "e", "question": {"case": case, "text": "t"}}
    assert pages.waiting(it, wb_host=EVIL, wb_command='a "<b>" &') == (
        f"<h3>e: case {case}</h3>\n<pre>t</pre>\n<p>At the host terminal of <b>&lt;script&gt;&quot;x</b>:</p>\n"
        '<code class="copy">a &quot;&lt;b&gt;&quot; &amp; ' + line.format("&lt;script&gt;&quot;x") + "</code>\n")


def test_the_copy_style():
    rule = re.search(r"^\.copy \{([^}]*)\}", pages.STYLE, re.M)
    assert rule and {d.strip() for d in rule.group(1).split(";")} >= {
        "-webkit-user-select: all", "user-select: all", "white-space: pre-wrap", "overflow-wrap: anywhere",
        "display: block"}


@pytest.mark.parametrize("conf", [{"commands": [*ROWS, EVIL_ROW]}, {}, TERMINAL], ids=["rows", "no keys", "keys"])
def test_each_copy_text_is_1_line(waits, tmp_path, conf):
    out = tmp_path / "pages"
    assert run_conf(*waits, out, **conf) == 0
    texts = [html.unescape(t) for t in re.findall(r'<code class="copy">(.*?)</code>', read(out), re.S)]
    assert len(texts) == 2 + len(conf.get("commands", []))  # the case 3 and 4 waits, and the rows
    for t in texts:
        assert re.fullmatch(r"[ -~]{1,300}", t) and not t.startswith("$"), t


@pytest.mark.parametrize("case,line", [(3, " approve 7 high"), (4, " answer 7 --file <file>")])
def test_the_filled_line_is_at_most_300_characters(tmp_path, monkeypatch, capsys, case, line):
    monkeypatch.setattr(pages, "_utc", lambda: BUILT)
    cfg, personas = make_cfg(tmp_path)
    item(cfg, "7", "review", "waiting", question={"case": case, "text": "t", "run_folder": None})
    out = tmp_path / "pages"
    command = MARK + "x" * (300 - len(MARK) - len(line))  # a good wb_command: the filled line is 300
    assert run_conf(cfg, personas, out, wb_command=command) == 0
    texts = [html.unescape(t) for t in re.findall(r'<code class="copy">(.*?)</code>', read(out), re.S)]
    assert texts == [command + line] and len(texts[0]) == 300
    before = sorted(p.name for p in tmp_path.iterdir()), {p.name: p.read_bytes() for p in out.iterdir()}
    capsys.readouterr()
    assert run_conf(cfg, personas, out, wb_command=command + "x") == 2  # 301: the pages of the last build stay
    err = capsys.readouterr().err
    assert err.startswith("wb: pages.wb_command") and MARK not in err and "x" * 20 not in err
    assert (sorted(p.name for p in tmp_path.iterdir()), {p.name: p.read_bytes() for p in out.iterdir()}) == before


def test_the_docs_show_the_commands_and_the_keys():
    with (ROOT / "config" / "workbench.example.toml").open("rb") as f:
        conf = tomllib.load(f)["pages"]
    assert (conf["wb_host"], conf["wb_command"]) == ("", "")  # private: the local file sets them
    assert [h for h, _, _ in pages.commands(conf["commands"])] == ["example-host"]
    text = (ROOT / "wb" / "README.md").read_text(encoding="utf-8")
    for need in ("**Commands.**", "`[[pages.commands]]`", "`pages.wb_host`", "`pages.wb_command`",
                 "`user-select: all`", "At the host terminal of"):
        assert need in text, need
