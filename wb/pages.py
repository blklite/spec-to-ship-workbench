"""`wb pages --out DIR`: read-only HTML pages of the state (see wb/README.md).

index.html holds the counts and the owner's work in 4 sections (the owner feed of
the state home gives the merges, the outside steps and the order), results.html the measures,
and 1 breakout page for each status and each stage with items lists the items.
The build reads the state home and the run folders with no write, builds in a
temp folder in the parent of DIR, and replaces DIR only after a full build. It
replaces only a DIR that is empty, or that holds only what the MARKER file lists.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import math
import os
import re
import shutil
import statistics
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from runner import UsageError, adapters
from runner.step import _utc
from wb import charts, machine, push, state, theme

STATUSES = ("running", "waiting", "ready")
STAGES = ("spec-check", "build", "review", "rework", "delivery")
NO_DATA = "no data"
ROLES = ("spec-checker", "builder", "reviewer", "second-reviewer", "rework", "other")  # the stack order of USD by role
# A lettered option: after any spaces and at most 1 "- ", a capital, then ")", "." or ":", then a space.
OPTION = re.compile(r" *(- )?[A-Z][).:] ")
BATCH_HEADING = re.compile(r"#{1,6} .*\bQuestions\b")
BLOCKED = ("BUILD: BLOCKED", "REWORK: BLOCKED")  # the last lines of a reply that give options
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")  # not %b: no locale
ADO_ID = re.compile(r"[1-9][0-9]*")  # an ADO work item number: ASCII digits, no leading 0
WB_COMMAND = "python -m wb"  # the default of pages.wb_command
# The colours: light on :root; the dark values written once, for a dark OS with no choice and for the Dark choice.
LIGHT = """
  --bg: #f4f6fa; --surface: #ffffff; --stripe: #f0f3f8; --code-bg: #e9edf3; --border: #d3d9e3;
  --fg: #1a1f2b; --muted: #505c6e; --link: #1a56db;
  --badge-bg: #e3eafb; --badge-fg: #1e3a8a; --accent-bg: #1a56db; --accent-fg: #ffffff;
  --series-1: #1a56db; --series-2: #c2410c; --series-3: #047857; --series-4: #7e22ce; --series-5: #be185d;
  --series-6: #4b5563; --mark: #b91c1c;
  color-scheme: light;
"""
DARK = """
  --bg: #0f1218; --surface: #181c24; --stripe: #1e232e; --code-bg: #232937; --border: #353d4c;
  --fg: #e6e9ef; --muted: #a9b2c1; --link: #9ec5ff;
  --badge-bg: #263552; --badge-fg: #d3e2ff; --accent-bg: #9ec5ff; --accent-fg: #12141a;
  --series-1: #60a5fa; --series-2: #fb923c; --series-3: #34d399; --series-4: #c084fc; --series-5: #f472b6;
  --series-6: #9ca3af; --mark: #f87171;
  color-scheme: dark;
"""
STYLE = (":root {" + LIGHT + "}\n@media (prefers-color-scheme: dark) {\n  html:not([data-theme]) {" + DARK + "}\n}\n"
         'html[data-theme="dark"] {' + DARK + "}\n" + """
body { font-family: system-ui, sans-serif; margin: 0; line-height: 1.4; color: var(--fg); background: var(--bg); }
header { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 1rem; padding: 0.5rem 1rem;
  background: var(--surface); border-bottom: 1px solid var(--border); }
header nav { display: flex; gap: 1rem; font-weight: 600; }
header p { margin: 0; color: var(--muted); font-size: 0.875rem; }
header [role="group"] { margin-left: auto; }
main { max-width: 72rem; margin: 0 auto; padding: 1rem; overflow-wrap: anywhere; }  /* no page scroll on a phone */
h1 { margin: 0.5rem 0 1rem; font-size: 1.5rem; }
a { color: var(--link); }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 0.75rem; padding: 0.75rem 1rem;
  margin: 0 0 1rem; }
.card h2 { margin: 0 0 0.5rem; font-size: 1.125rem; }
.host { font-size: 1.125rem; }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(7.5rem, 1fr)); gap: 0.5rem; list-style: none;
  margin: 0; padding: 0; }
.tiles li { display: flex; flex-direction: column; padding: 0.5rem 0.75rem; background: var(--stripe);
  border: 1px solid var(--border); border-radius: 0.5rem; }
.tiles li > span:first-child { color: var(--muted); font-size: 0.875rem; }
.tiles .count { font-size: 1.75rem; font-weight: 700; line-height: 1.2; }
/* the charts of wb/charts.py: colour only from the tokens */
/* on a phone a chart keeps its label size and scrolls sideways in its own box, as a table does */
.chart-box { overflow-x: auto; max-width: 100%; }
.chart { display: block; width: 100%; min-width: 35rem; max-width: 40rem; height: auto; margin: 0 0 0.5rem; }
.chart text { fill: var(--fg); font-size: 12px; }
.chart .s1 { fill: var(--series-1); } .chart .s2 { fill: var(--series-2); } .chart .s3 { fill: var(--series-3); }
.chart .s4 { fill: var(--series-4); } .chart .s5 { fill: var(--series-5); } .chart .s6 { fill: var(--series-6); }
.chart .mark { stroke: var(--mark); stroke-width: 2; }
.badge { display: inline-block; padding: 0 0.5rem; border-radius: 1rem; background: var(--badge-bg);
  color: var(--badge-fg); font-size: 0.875rem; font-weight: 600; }
/* a long table scrolls in its own box, under its header row */
table { border-collapse: separate; border-spacing: 0; display: block; overflow-x: auto; max-height: 70vh;
  width: max-content; max-width: 100%; box-sizing: border-box;
  background: var(--surface); border: 1px solid var(--border); border-radius: 0.5rem; }
th, td { border-bottom: 1px solid var(--border); padding: 0.375rem 0.75rem; text-align: left; white-space: nowrap; }
th { position: sticky; top: 0; background: var(--surface); }
tr:nth-child(even) td { background: var(--stripe); }
pre { white-space: pre-wrap; overflow-wrap: anywhere; padding: 0.5rem 0.75rem; }
/* not monospace alone: browsers shrink it */
pre, code { font-family: ui-monospace, monospace; background: var(--code-bg); border-radius: 0.25rem; }
/* 1 long press in Safari selects the whole command, with no script; a wrap adds no line break to the copy */
.copy { display: block; white-space: pre-wrap; overflow-wrap: anywhere; -webkit-user-select: all; user-select: all;
  padding: 0.5rem 0.75rem; margin: 0.25rem 0 0.5rem; }
input, button { box-sizing: border-box; font: inherit; padding: 0.5rem; margin: 0.25rem 0.5rem 0.25rem 0; }
button, input[type="password"] { min-height: 44px; }  /* a touch target on a phone */
button { min-width: 44px; }
a:focus-visible, button:focus-visible, input:focus-visible { outline: 2px solid var(--link); outline-offset: 2px; }
[role="group"][aria-label="Theme"] { display: none; }  /* the buttons need the theme scripts */
html[data-js] [role="group"][aria-label="Theme"] { display: flex; gap: 0.25rem; }
[role="group"][aria-label="Theme"] button { margin: 0; color: var(--fg); background: var(--surface);
  border: 1px solid var(--border); border-radius: 0.5rem; }
[role="group"][aria-label="Theme"] button[aria-pressed="true"] { color: var(--accent-fg); background: var(--accent-bg);
  border-color: var(--accent-bg); }
""")


def esc(v) -> str:
    return html.escape(str(v), quote=True)


class Html(str):
    """Markup that table puts in as is: the output of item_id."""


def item_id(id_, ado_url: str, pr: str | None = None) -> Html:
    """The escaped id; an ADO number links to its work item when ado_url is set; then 1 space and pr, the PR link."""
    id_ = str(id_)  # a hand-edited id can be any JSON value
    # the esc of the link text changes nothing while ADO_ID is digits only: kept, as no test can tell it apart
    text = f'<a href="{esc(ado_url + id_)}">{esc(id_)}</a>' if ado_url and ADO_ID.fullmatch(id_) else esc(id_)
    return Html(text + (f" {pr}" if pr else ""))


def _https_folder(v) -> bool:
    """True for an https URL with a host, a port of 1 to 65535 if any, and a final /, that holds no query, fragment,
    space, control character, " or <."""
    try:
        u = urlsplit(v)
        port = u.port  # a ValueError for a port that is not a number or is above 65535
    except ValueError:
        return False
    return (u.scheme == "https" and bool(u.hostname) and port != 0 and v.endswith("/") and v.isprintable()
            and not any(c in v for c in ' ?#"<'))


def _pr(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _json(path: Path):
    """The JSON of a file, or None when it is missing or not JSON."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_items(home: Path) -> tuple[list[dict], int]:
    """(the readable states in folder order, the count of unreadable item folders).

    A stage or a status must be a name, since it becomes a file name."""
    items, bad = [], 0
    for folder in sorted(p for p in home.iterdir() if p.is_dir()):
        st = _json(folder / "state.json")
        if isinstance(st, dict) and st.get("version") == state.VERSION and all(
                isinstance(st.get(k), str) and state.ITEM_ID.fullmatch(st[k]) for k in ("stage", "status")):
            it = {**st, "id": st.get("id") or folder.name, "_folder": folder}
            items.append({**it, "_id": item_id(it["id"], ""), "_counters": counters(it)})  # build links _id
        else:
            bad += 1
    return items, bad


def _steps(item: dict) -> list[dict] | None:
    """The step entries with a run folder in step order; None with no steps field."""
    steps = item.get("steps")
    if not isinstance(steps, list):
        return None
    return sorted((s for s in steps if isinstance(s, dict) and isinstance(s.get("run_folder"), str)),
                  key=lambda s: _number(s.get("n")) or 0)


def counters(item: dict) -> list[dict]:
    """The counters.json of each step in step order, read with no write; {} where
    there is none. With no steps field: each runs/*/counters.json of the item."""
    steps = _steps(item)
    if steps is not None:
        folders = [Path(s["run_folder"]) for s in steps]
    else:
        folders = sorted(p for p in (item["_folder"] / "runs").glob("*") if p.is_dir())
    return [c if isinstance(c := _json(f / "counters.json"), dict) else {} for f in folders]


def roles(item: dict) -> list[str]:
    """The role of each step of counters, in the same order: a role of ROLES, else other."""
    steps = _steps(item)
    if steps is None:
        return ["other"] * len(item["_counters"])
    return [s.get("role") if s.get("role") in ROLES else "other" for s in steps]


def _number(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _fmt(v, spec: str = ".2f") -> str:
    return NO_DATA if v is None else format(v, spec)


def _field(item: dict, key: str):
    """The field of the state, else the sum over the counters of its steps, else None."""
    v = _number(item.get(key))
    if v is None:
        found = [n for c in item["_counters"] if (n := _number(c.get(key))) is not None]
        v = sum(found) if found else None
    return v


def usd_of(item: dict):
    return _field(item, "usd")


def hours_of(item: dict):
    seconds = _field(item, "seconds")
    return None if seconds is None else seconds / 3600


def _time(v) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(v)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def local_time(v: str, tz: dt.tzinfo) -> str:
    """<time> with the UTC time to the second, showing the local time as Nov 5, 9:45 AM UTC; the escaped v when
    the time is out of range in UTC or in the zone."""
    t = _time(v)
    try:
        utc, t = t.astimezone(dt.timezone.utc), t.astimezone(tz)
    except OverflowError:
        return esc(v)
    return (f'<time datetime="{utc.replace(tzinfo=None, microsecond=0).isoformat()}Z">{MONTHS[t.month - 1]} {t.day},'
            f' {t.hour % 12 or 12}:{t.minute:02d} {"AM" if t.hour < 12 else "PM"} {esc(t.tzname())}</time>')


def last_end(item: dict) -> dt.datetime | None:
    """The end of the last step, from its counters.json."""
    return _time(item["_counters"][-1].get("end")) if item["_counters"] else None


def _counts(items: list[dict], key: str, known: tuple) -> dict:
    counts = dict.fromkeys(known, 0)
    for it in items:
        counts[it[key]] = counts.get(it[key], 0) + 1
    return counts


def page_name(kind: str, name: str) -> str:
    return f"{kind}-{name}.html"


def page(title: str, built: str, body: str, key: str | None = None) -> str:
    """built: the build time from local_time; key: the theme key; with no key the page has no script and no theme
    group."""
    head, control, group = "", "", ""
    if key:
        head, control = (f"<script>{s}</script>\n" for s in theme.scripts(key))
        group = theme.CONTROL + "\n"
    return ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{esc(title)}</title>\n{head}<style>{STYLE}</style>\n</head>\n"
            '<body>\n<header>\n<nav><a href="index.html">State</a> <a href="results.html">Results</a></nav>\n'
            f"<p>Built at {built}.</p>\n{group}</header>\n<main>\n<h1>{esc(title)}</h1>\n{body}</main>\n"
            f"{control}</body>\n</html>\n")


def table(head: tuple, rows: list) -> str:
    if not rows:
        return "<p>None.</p>\n"
    out = ["<table>", "<tr>" + "".join(f"<th>{esc(h)}</th>" for h in head) + "</tr>"]
    badge = [h in ("Stage", "Status") for h in head]  # a badge holds its text: colour is never the only mark
    out += ["<tr>" + "".join(f'<td><span class="badge">{esc(c)}</span></td>' if b
                             else f"<td>{c if isinstance(c, Html) else esc(c)}</td>"
                             for b, c in zip(badge, r)) + "</tr>" for r in rows]
    return "\n".join(out) + "\n</table>\n"


ROW_HEAD = ("Item", "Stage", "Status", "Rounds", "USD", "Hours")


def row(item: dict) -> tuple:
    """The id, stage, status, and the rounds, USD and hours against their limits."""
    lim = item.get("limits") if isinstance(item.get("limits"), dict) else {}
    return (item["_id"], item["stage"], item["status"],
            f"{_fmt(_number(item.get('rounds_used')), 'g')} / {_fmt(_number(lim.get('rounds')), 'g')}",
            f"{_fmt(usd_of(item))} / {_fmt(_number(lim.get('usd')), 'g')}",
            f"{_fmt(hours_of(item))} / {_fmt(_number(lim.get('hours')), 'g')}")


def _tile(name: str, n: int | str, href: str | None = None) -> str:
    count = f'<a class="count" href="{href}">{n}</a>' if href else f'<span class="count">{n}</span>'
    return f"<li><span>{esc(name)}</span> {count}</li>"


def _count_list(kind: str, counts: dict, extra: tuple = ()) -> str:
    tiles = [_tile(k, n, page_name(kind, k) if n else None) for k, n in counts.items()]
    return '<ul class="tiles">\n' + "\n".join([*tiles, *extra]) + "\n</ul>\n"


def card(heading: str, body: str) -> str:
    return f'<section class="card">\n<h2>{esc(heading)}</h2>\n{body}</section>\n'


def copy(text: str) -> str:
    """A command that 1 long press selects whole: no white space around the text, since pre-wrap would copy it."""
    return f'<code class="copy">{esc(text)}</code>'


def reply_text(run_folder) -> str | None:
    """The final text of the reply in a run folder, read with no write: the
    stdout.txt through its adapter, else reply.txt; None when neither gives text."""
    if not isinstance(run_folder, str) or not run_folder:
        return None
    c = _json(Path(run_folder) / "counters.json")
    name = c.get("adapter") if isinstance(c, dict) else None
    if isinstance(name, str) and name.isidentifier():
        try:
            text = Path(machine.reply_path({"run_folder": run_folder})).read_text(encoding="utf-8")
            final = adapters.get(name).parse(text, {"model": c.get("model")}).final_text
        except Exception:  # any reply that the adapter cannot read falls back, and the build goes on
            final = None
        if final and final.strip():
            return final
    try:
        return (Path(run_folder) / "reply.txt").read_text(encoding="utf-8").strip() or None
    except (OSError, ValueError):
        return None


def batch(reply: str) -> str:
    """The text after the last # heading with Questions, else the full reply; no SPEC-CHECK last line."""
    lines = reply.strip().splitlines()
    if lines and lines[-1].startswith("SPEC-CHECK"):
        lines.pop()
    heads = [i for i, line in enumerate(lines) if BATCH_HEADING.match(line)]
    return "\n".join(lines[heads[-1] + 1:] if heads else lines).strip()


def option(line: str) -> str:
    return line.lstrip(" ").removeprefix("- ")


def options(q: dict) -> list[str]:
    """The options of a case 2 wait with no spec_questions: the unbroken run of 2 or more lettered lines
    right before the BLOCKED last line of its reply, blank lines between not counted; [] for a run with a
    repeated letter, or for any other wait or reply. The page and wb pull-answers both read this."""
    if q.get("case") != 2 or q.get("spec_questions"):
        return []
    lines = (reply_text(q.get("run_folder")) or "").splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or not lines.pop().startswith(BLOCKED):
        return []
    while lines and not lines[-1].strip():
        lines.pop()
    run = []
    while lines and OPTION.match(lines[-1]):
        run.insert(0, option(lines.pop()).strip())
    letters = {o[0] for o in run}
    return run if len(run) >= 2 and len(letters) == len(run) else []


def qhash(q: dict, opts: list[str]) -> str:
    """The first 16 hex characters of the SHA-256 of the question text, 1 LF, then the option lines."""
    return hashlib.sha256((str(q.get("text") or "") + "\n" + "\n".join(opts)).encode("utf-8")).hexdigest()[:16]


def form(item: dict, q: dict, opts: list[str], url: str) -> str:
    """The answer form of a wait with options: it posts the id, the qhash, the key and the letter to url."""
    return (f'<form method="post" action="{esc(url)}">\n'
            f'<input type="hidden" name="id" value="{esc(item["id"])}">\n'
            f'<input type="hidden" name="qhash" value="{qhash(q, opts)}">\n'
            '<label>Key <input type="password" name="key" required autocomplete="current-password"></label>\n'
            + "".join(f'<button type="submit" name="option" value="{esc(o[0])}">{esc(o[0])}</button>\n'
                      for o in opts) + "</form>\n")


def _list(opts: list[str]) -> str:
    return "<ul>\n" + "".join(f"<li>{esc(o)}</li>\n" for o in opts) + "</ul>\n"


def filled(item: dict, wb_command: str = WB_COMMAND) -> str | None:
    """The copy text of a case 3 or 4 wait, else None."""
    q = item.get("question") if isinstance(item.get("question"), dict) else {}
    if q.get("case") == 3:
        return f"{wb_command} approve {item['id']} high"
    if q.get("case") == 4:
        return f"{wb_command} answer {item['id']} --file <file>"
    return None


def waiting(item: dict, answer_url: str | None = None, reason=None, wb_host: str = "",
            wb_command: str = WB_COMMAND) -> str:
    """wb_host and wb_command fill the line of a case 3 or 4 wait, to copy at the host terminal."""
    q = item.get("question") if isinstance(item.get("question"), dict) else {}
    case, text, id_ = q.get("case"), str(q.get("text") or ""), esc(item["id"])
    out = f"<h3>{item['_id']}: case {esc(case)}</h3>\n" + ("" if reason is None else f"<p>Queued: {esc(reason)}</p>\n")
    if case == 2 and q.get("spec_questions"):
        reply = reply_text(q.get("run_folder"))
        shown = batch(reply) if reply else ""
        return (out + f"<pre>{esc(shown or text)}</pre>\n"
                f"<p>Amend the spec, then run <code>python -m wb intake &lt;spec&gt; --id {id_}</code> again.</p>\n")
    if case == 2:
        opts = options(q)
        if opts:
            return (out + f"<pre>{esc(text)}</pre>\n" + _list(opts)
                    + (form(item, q, opts, answer_url) if answer_url else ""))
        lines = text.splitlines()
        listed = [option(line) for line in lines if OPTION.match(line)]
        if listed:
            question = "\n".join(line for line in lines if not OPTION.match(line))
            return out + f"<pre>{esc(question)}</pre>\n" + _list(listed)
        return (out + f"<pre>{esc(text)}</pre>\n<p>Answer with the <code>answer</code> command:"
                f" <code>python -m wb answer {id_} --file &lt;file&gt;</code>.</p>\n")
    if case in (3, 4):
        at = f" of <b>{esc(wb_host)}</b>" if wb_host else ""
        return out + f"<pre>{esc(text)}</pre>\n<p>At the host terminal{at}:</p>\n{copy(filled(item, wb_command))}\n"
    return out + f"<pre>{esc(text)}</pre>\n<p>This step is done off the page, at the host terminal.</p>\n"


FEED = "owner-feed.json"  # optional, in the state home: any program of yours may write it (temp file, then rename)
FEED_MAX = 1024 * 1024
FEED_STEPS = 50


def _entry(v) -> dict | None:
    """An entry of the feed's items with each key it gives of the right type, else None."""
    if not isinstance(v, dict):
        return None
    pr, created = v.get("pr"), v.get("created")
    priority = _number(v.get("priority"))
    # a float NaN breaks the sort; an int is always finite, and isfinite of an int above the float range raises
    if ("priority" in v and (priority is None or isinstance(priority, float) and not math.isfinite(priority))
            or "created" in v and _time(created) is None
            or "pr" in v and not _pr(pr)):
        return None
    return {"priority": priority, "created": _time(created), "pr": pr}


def feed(home: Path) -> dict | None:
    """The owner feed: {written, done, items, steps}; None when it is missing, above FEED_MAX bytes, not JSON, or
    has a bad top-level field. A bad entry is skipped; a steps list above FEED_STEPS gives no steps."""
    path = home / FEED
    try:
        if not path.is_file():  # never open a FIFO: the build would hang
            return None
        with path.open("rb") as f:
            raw = f.read(FEED_MAX + 1)
        data = json.loads(raw) if len(raw) <= FEED_MAX else None
    except (OSError, ValueError, RecursionError):  # RecursionError: JSON nested too deep
        return None
    if not (isinstance(data, dict) and isinstance(data.get("written"), str) and _time(data["written"])
            and isinstance(data.get("done"), list) and isinstance(data.get("items"), dict)
            and isinstance(data.get("steps"), list)):
        return None
    steps = data["steps"] if len(data["steps"]) <= FEED_STEPS else []
    return {"written": data["written"], "done": {d for d in data["done"] if isinstance(d, str)},
            "items": {k: e for k, v in data["items"].items() if (e := _entry(v)) is not None},
            "steps": [s for s in steps if isinstance(s, dict) and isinstance(s.get("id"), str)
                      and isinstance(s.get("text"), str)]}


def _queued(item: dict) -> dict | None:
    q = item.get("question")
    held = q.get("queued") if isinstance(q, dict) else None
    return held if isinstance(held, dict) else None


def rank(waits: list[dict], fd: dict | None):
    """The sort key of an id: the held waits that it blocks (most first), then the feed's priority (1 first),
    then its created time (oldest first), then the id; a missing value sorts after the known values of its key."""
    afters = [h.get("after") for it in waits if (h := _queued(it))]
    known = fd["items"] if fd else {}

    def key(id_):
        id_ = str(id_)  # a hand-edited id can be any JSON value
        e = known.get(id_, {})
        p, c = e.get("priority"), e.get("created")
        return -afters.count(id_), p is None, p or 0, c is None, c or dt.datetime.min, id_
    return key


def pr_link(item: dict, fd: dict | None) -> str | None:
    """The link PR <n> of the item, else None: n is the number of its PR record, else the feed's pr, and the
    repository is its remote.url on GitHub."""
    rec = item.get("records")
    rec = rec.get("pr") if isinstance(rec, dict) else None
    pr = rec.get("number") if isinstance(rec, dict) else None
    if not _pr(pr):
        pr = fd["items"].get(str(item["id"]), {}).get("pr") if fd else None
    url = (item.get("remote") if isinstance(item.get("remote"), dict) else {}).get("url")
    m = push.ORIGIN.fullmatch(url) if isinstance(url, str) else None
    if pr is None or m is None:
        return None
    return f'<a href="{esc(f"https://github.com/{m.group(1)}/{m.group(2)}/pull/{pr}")}">PR {pr}</a>'


def owner(items: list[dict], fd: dict | None, answer_url: str | None, ado_url: str,
          tz: dt.tzinfo, wb_host: str = "", wb_command: str = WB_COMMAND) -> tuple[str, str]:
    """(the count line, the 4 sections of the owner's work)."""
    by_id = {str(it["id"]): it for it in items}
    waits = [it for it in items if it["status"] == "waiting"]
    done = fd["done"] if fd else set()
    key = rank(waits, fd)

    def held(it):  # a case 3 or 4 wait, or a hold after a done item, needs the owner now
        h = _queued(it)
        after = h.get("after") if h else None
        return (h is not None and it["question"].get("case") not in (3, 4)
                and not (isinstance(after, str) and after in done))

    now = sorted((it for it in waits if not held(it)), key=lambda it: key(it["id"]))
    later = sorted((it for it in waits if held(it)), key=lambda it: key(it["id"]))
    merges = sorted((it for it in items if it["status"] == "ready" and it["id"] not in done),
                    key=lambda it: key(it["id"]))
    steps = sorted(fd["steps"], key=lambda s: key(s["id"])) if fd else []
    if fd is None:
        merge = "<p>No owner feed: merges are not known.</p>\n"
    elif merges:
        merge = "<ul>\n" + "".join(f"<li>{item_id(it['id'], ado_url)}: {pr_link(it, fd) or 'No recorded PR.'}</li>\n"
                                    for it in merges) + "</ul>\n"  # the id with no PR: the PR link comes after it
    else:
        merge = "<p>No item waits for a merge.</p>\n"
    out = [card("Needs your input",
                "".join(waiting(it, answer_url, None, wb_host, wb_command) for it in now)
                or "<p>No item needs your input.</p>\n"),
           card("Needs merge", merge),
           card("Steps outside wb",
                "<ul>\n" + "".join(f"<li>{by_id[s['id']]['_id'] if s['id'] in by_id else item_id(s['id'], ado_url)}: "
                                       f"{esc(s['text'])}</li>\n" for s in steps) + "</ul>\n"
                if steps else "<p>No step outside wb.</p>\n"),
           card("Queued", "".join(waiting(it, answer_url, _queued(it).get("reason") or "", wb_host, wb_command)
                                for it in later)
                or "<p>No queued wait.</p>\n")]
    tail = f"Owner feed written at {local_time(fd['written'], tz)}." if fd else "No owner feed."
    line = (f"<p>Needs your input: {len(now)}. Needs merge: {len(merges) if fd else 'not known'}."
            f" Steps outside wb: {len(steps)}. Queued: {len(later)}. {tail}</p>\n")
    return line, "".join(out)


def index(items: list[dict], bad: int, built: str, answer_url: str | None = None, fd: dict | None = None,
          key: str | None = None, ado_url: str = "", tz: dt.tzinfo = dt.timezone.utc, rows: list = (),
          wb_host: str = "", wb_command: str = WB_COMMAND) -> str:
    """rows: the (host, label, copy text) of pages.commands; with none, no Commands card."""
    line, sections = owner(items, fd, answer_url, ado_url, tz, wb_host, wb_command)
    body = (f"<p>Items: {len(items) + bad}</p>\n" + line + sections
            + card("Status", _count_list("status", _counts(items, "status", STATUSES), (_tile("unreadable", bad),)))
            + card("Stage", _count_list("stage", _counts(items, "stage", STAGES)))
            + "<p><code>running</code> means that the next step can start; it does not show a live process.</p>\n"
              "<p><code>ready</code> does not show a merge.</p>\n"
            + (card("Commands", "<ul>\n" + "".join(f'<li><b class="host">{esc(h)}</b> {esc(label)} {copy(c)}</li>\n'
                                                     for h, label, c in rows) + "</ul>\n") if rows else ""))
    return page("Workbench state", built, body, key)


def _tally(values) -> list[tuple]:
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    # numbers in numeric order, no data last
    return sorted(counts.items(), key=lambda kv: (kv[0] == NO_DATA, len(str(kv[0])), str(kv[0])))


def _mean(values: list):
    return sum(values) / len(values) if values else None


def _waiting(items: list[dict]) -> list[dict]:
    return [it for it in items if it["status"] == "waiting"]


def _case(item: dict):
    q = item.get("question")
    case = q.get("case") if isinstance(q, dict) else None
    return case if isinstance(case, (int, str)) else NO_DATA


# Each measure: (items, the build time) -> (title, the column heads, the rows).


def _week(e: dt.datetime | None, tz: dt.tzinfo) -> str:
    """The ISO week of the local date of e."""
    try:
        return NO_DATA if e is None else "%d-W%02d" % e.astimezone(tz).isocalendar()[:2]
    except OverflowError:  # out of range in the zone
        return NO_DATA


def delivered_by_week(items, now):
    weeks = [_week(last_end(it), now.tzinfo) for it in items if it["stage"] == "delivery"]
    return "Items that reached delivery, by ISO week", ("Week", "Items"), _tally(weeks)


def stage_counts(items, now):
    return "Items at each stage", ("Stage", "Items"), list(_counts(items, "stage", STAGES).items())


def cost_by_tier(items, now):
    tiers = sorted({it["tier"] for it in items if isinstance(it.get("tier"), str)})
    rows = []
    for name, group in [("all", items)] + [(t, [it for it in items if it.get("tier") == t]) for t in tiers]:
        usd = [v for it in group if (v := usd_of(it)) is not None]
        hours = [v for it in group if (v := hours_of(it)) is not None]
        rows.append((name, len(group), _fmt(sum(usd) if usd else None), _fmt(_mean(usd)),
                     _fmt(sum(hours) if hours else None), _fmt(_mean(hours))))
    return ("USD and hours, by tier", ("Tier", "Items", "USD total", "USD mean", "Hours total", "Hours mean"),
            rows if items else [])


def top_usd(items, now):
    known = sorted(((v, it) for it in items if (v := usd_of(it)) is not None),
                   key=lambda p: (-p[0], p[1]["_folder"].name))
    rows = [(it["_id"], _fmt(v)) for v, it in known[:10]]
    return "The 10 items with the highest USD", ("Item", "USD"), rows or ([(NO_DATA, NO_DATA)] if items else [])


def rounds_count(items, now):
    return "Items at each rework round", ("Round", "Items"), _tally(
        _fmt(_number(it.get("rounds_used")), "g") for it in items)


def at_round_limit(items, now):
    rows, known = [], False
    for it in items:
        lim = it.get("limits") if isinstance(it.get("limits"), dict) else {}
        used, limit = _number(it.get("rounds_used")), _number(lim.get("rounds"))
        if used is not None and limit is not None:
            known = True
            if used >= limit:
                rows.append((it["_id"], f"{used:g} / {limit:g}"))
    return "Items at the round limit", ("Item", "Rounds"), rows if known or not items else [(NO_DATA, NO_DATA)]


def waits_by_case(items, now):
    return "Waiting items by stop case", ("Case", "Items"), _tally(_case(it) for it in _waiting(items))


def wait_times(items, now):
    rows = [(it["_id"], _case(it), _fmt(None if (end := last_end(it)) is None else (now - end).total_seconds() / 3600))
            for it in _waiting(items)]
    return "The wait of each waiting item", ("Item", "Case", "Hours"), rows


# A new measure is 1 function here and 1 test.
MEASURES = (("Throughput", delivered_by_week), ("Throughput", stage_counts),
            ("Cost and time", cost_by_tier), ("Cost and time", top_usd),
            ("Quality", rounds_count), ("Quality", at_round_limit),
            ("Waits", waits_by_case), ("Waits", wait_times))


def _within(t: dt.datetime | None, now: dt.datetime, days: int) -> bool:
    """True when t is in the window of the last days: (now - days, now], in UTC arithmetic, so that a change of
    the local clock does not make a window 1 hour longer or shorter."""
    now = now.astimezone(dt.timezone.utc)
    return t is not None and now - dt.timedelta(days=days) < t <= now


def _hours_to_ready(item: dict):
    """The hours from the start of step 1 to the end of the last step; None with no start."""
    start = _time(item["_counters"][0].get("start"))
    return None if start is None else (last_end(item) - start).total_seconds() / 3600


def key_figures(items: list[dict], now: dt.datetime) -> str:
    """The 9 tiles of the results page."""
    def delivered(days):
        return [it for it in items if it["stage"] == "delivery" and _within(last_end(it), now, days)]

    def usd(days):
        return _fmt(sum(v for it in items for c in it["_counters"]
                        if _within(_time(c.get("end")), now, days) and (v := _number(c.get("usd"))) is not None))

    def median(days):
        hours = [h for it in items if it["status"] == "ready" and _within(last_end(it), now, days)
                 and (h := _hours_to_ready(it)) is not None]
        return _fmt(statistics.median(hours) if hours else None)

    def rework_free(days):
        rounds = [r for it in delivered(days) if (r := _number(it.get("rounds_used"))) is not None]
        return f"{rounds.count(0) * 100 // len(rounds)} %" if rounds else NO_DATA

    tiles = [_tile(f"{name}, {days} days", figure(days)) for name, figure in (
        ("Delivered", lambda d: len(delivered(d))), ("USD", usd), ("Median hours to ready", median),
        ("Rework-free", rework_free)) for days in (7, 30)]
    tiles.append(_tile("Waiting now", len(_waiting(items))))
    return '<ul class="tiles">\n' + "\n".join(tiles) + "\n</ul>\n"


# Each graph: (items, the build time) -> its SVG in its box, then its table; a graph with no rows shows only None.


def _graph(svg: str, head: tuple, rows: list) -> str:
    return (f'<div class="chart-box">\n{svg}</div>\n' if rows else "") + table(head, rows)


def _weeks(now: dt.datetime) -> list[str]:
    """The 12 ISO weeks up to the week of now, oldest first."""
    return [_week(now - dt.timedelta(weeks=k), now.tzinfo) for k in range(11, -1, -1)]


def delivered_per_week(items, now):
    ids = {w: [] for w in _weeks(now)}
    for it in items:
        if it["stage"] == "delivery" and (w := _week(last_end(it), now.tzinfo)) in ids:
            ids[w].append(it["_id"])
    svg = charts.bars("Delivered per week", "Items that reached delivery in each of the last 12 ISO weeks.",
                      [(w, len(v), str(len(v))) for w, v in ids.items()])
    return _graph(svg, ("Week", "Items", "Item ids"), [(w, len(v), Html(", ".join(v))) for w, v in ids.items()])


def usd_per_week_by_role(items, now):
    sums = {w: dict.fromkeys(ROLES, 0.0) for w in _weeks(now)}
    for it in items:
        for c, role in zip(it["_counters"], roles(it)):
            w = _week(_time(c.get("end")), now.tzinfo)  # a step with no end is no data, not a week
            if w in sums and (v := _number(c.get("usd"))) is not None:
                sums[w][role] += v
    rows = [(w, *(_fmt(v) for v in by_role.values()), _fmt(sum(by_role.values()))) for w, by_role in sums.items()]
    svg = charts.stacked("USD per week by role", "The USD of the steps that ended in each of the last 12 ISO weeks,"
                         " stacked by role in the order of the legend.", list(ROLES),
                         [(r[0], [(v, t) for v, t in zip(sums[r[0]].values(), r[1:-1])], r[-1]) for r in rows])
    return _graph(svg, ("Week", *ROLES, "Total"), rows)


def _use(used, limit) -> tuple:
    """(used / limit, the whole percent rounded down) or (None, no data) for a missing or 0 limit."""
    pct = None if used is None or limit is None or limit <= 0 else used * 100 / limit
    return (None, NO_DATA) if pct is None or not math.isfinite(pct) else (pct / 100, f"{math.floor(pct)} %")


def budget_use(items, now):
    ended = sorted(sorted(((e, it) for it in items if (e := last_end(it)) is not None),
                          key=lambda p: p[1]["_folder"].name), key=lambda p: p[0], reverse=True)[:20]
    rows, bars = [], []
    for _, it in ended:
        lim = it.get("limits") if isinstance(it.get("limits"), dict) else {}
        used = {"USD": (usd_of(it), _number(lim.get("usd"))), "hours": (hours_of(it), _number(lim.get("hours")))}
        use = {k: _use(*v) for k, v in used.items()}
        rows.append((it["_id"], *(x for k, (u, n) in used.items() for x in (f"{_fmt(u)} / {_fmt(n, 'g')}", use[k][1]))))
        bars += [(f"{it['id']} {k}", *use[k]) for k in used]
    svg = charts.hbars("Budget use", "USD and hours used against the limits of the 20 items with the latest step end,"
                       " newest first. The line marks 100 %; a bar stops at 200 %.", bars, end=2.0)
    return _graph(svg, ("Item", "USD used / limit", "USD %", "Hours used / limit", "Hours %"), rows)


def waits(items, now):
    ages = [(None if (end := last_end(it)) is None else (now - end).total_seconds() / 3600, it)
            for it in _waiting(items)]
    ages.sort(key=lambda p: (p[0] is None, -(p[0] or 0), p[1]["_folder"].name))
    svg = charts.bars("Waits", "The hours since the last step of each waiting item, longest first.",
                      [(f"{it['id']}: case {_case(it)}", h, _fmt(h)) for h, it in ages])
    return _graph(svg, ("Item", "Case", "Hours"), [(it["_id"], _case(it), _fmt(h)) for h, it in ages])


GRAPHS = (("Delivered per week", delivered_per_week), ("USD per week by role", usd_per_week_by_role),
          ("Budget use", budget_use), ("Waits", waits))


def results(items: list[dict], now: dt.datetime, built: str, key: str | None = None) -> str:
    """now: the build time in the local zone; built: the build time from local_time."""
    groups = {}  # each group of MEASURES is 1 card, in order
    for g, measure in MEASURES:
        title, head, rows = measure(items, now)
        groups[g] = groups.get(g, "") + f"<h3>{esc(title)}</h3>\n" + table(head, rows)
    body = [card("Key figures", key_figures(items, now)), *(card(h, graph(items, now)) for h, graph in GRAPHS),
            *(card(g, part) for g, part in groups.items())]
    return page("Workbench results", built, "".join(body), key)


MARKER = ".wb-pages"  # each build lists its pages in this file


def ours(folder: Path) -> bool:
    """True when a replace of folder can lose only what a build wrote."""
    entries = list(folder.iterdir())
    if not entries:
        return True
    try:
        listed = (folder / MARKER).read_text(encoding="utf-8").split("\n")
    except (OSError, ValueError):
        return False
    return ({p.name for p in entries} <= {MARKER, *listed}
            and all(p.is_file() and not p.is_symlink() for p in entries))


def zone(cfg: dict) -> dt.tzinfo:
    """The zone of pages.time_zone: empty or missing is UTC; a name that zoneinfo cannot load is a usage error."""
    conf = cfg.get("pages", {})
    name = conf.get("time_zone", "") if isinstance(conf, dict) else None
    try:
        tz = dt.timezone.utc if name == "" else ZoneInfo(name) if isinstance(name, str) else None
    except (ValueError, KeyError, OSError):  # KeyError: ZoneInfoNotFoundError
        tz = None
    if tz is None:
        raise UsageError("pages.time_zone must be empty or an IANA time zone name")  # never the value
    return tz


COMMANDS_MAX = 30
COPY_RULE = "1 line of 1 to 300 printable ASCII characters, with no leading $ and no space at either end"


def _ascii(v, most: int) -> bool:
    """v is a string of 1 to most printable ASCII characters (0x20 to 0x7E): no line break, no tab."""
    return isinstance(v, str) and 0 < len(v) <= most and v.isascii() and v.isprintable()


def _copy_text(v) -> bool:
    return _ascii(v, 300) and not v.startswith("$") and v == v.strip()


def commands(rows) -> list[tuple[str, str, str]]:
    """The rows of pages.commands as (host, label, copy text); a bad row is a usage error that names its number,
    never its text (a host name is private)."""
    if not isinstance(rows, list):
        raise UsageError("pages.commands must be an array of tables")
    if len(rows) > COMMANDS_MAX:
        raise UsageError(f"pages.commands row {COMMANDS_MAX + 1}: at most {COMMANDS_MAX} rows")
    out = []
    for n, r in enumerate(rows, 1):
        fields = [r.get(k) for k in ("label", "host", "folder", "command")] if isinstance(r, dict) else [None]
        if not all(isinstance(f, str) for f in fields):
            raise UsageError(f"pages.commands row {n} must be a table of the strings label, host, folder and command")
        label, host, folder, command = fields
        if not (_ascii(label, 80) and _ascii(host, 80)):
            raise UsageError(f"pages.commands row {n}: label and host must be 1 to 80 printable ASCII characters")
        copy = f"cd {folder} && {command}" if folder else command
        if not _copy_text(copy):
            raise UsageError(f"pages.commands row {n}: the copy text (cd <folder> && <command>) must be {COPY_RULE}")
        out.append((host, label, copy))
    return out


def build(cfg: dict, out: str, answer_url: str | None = None) -> None:
    """Write the pages to out; a usage error leaves out as it was. answer_url: the target of the answer forms."""
    if answer_url is not None and (not answer_url or urlsplit(answer_url).scheme not in ("", "http", "https")):
        raise UsageError(f"--answer-url must be an http, https or relative URL: {answer_url!r}")  # never javascript:
    conf = cfg.get("pages", {})  # a missing table or an empty key: no key, so no script
    theme_key = conf.get("theme_key", "") if isinstance(conf, dict) else None
    if theme_key != "" and not (isinstance(theme_key, str) and theme.KEY.fullmatch(theme_key)):
        raise UsageError("pages.theme_key must be empty or 1 to 64 letters, digits, '.', '_' or '-'")  # never the value
    theme_key = theme_key or None
    ado_url = conf.get("ado_url", "") if isinstance(conf, dict) else None
    if ado_url != "" and not (isinstance(ado_url, str) and _https_folder(ado_url)):
        raise UsageError("pages.ado_url must be empty or an https URL that ends with /")  # private: never the value
    tz = zone(cfg)
    rows = commands(conf.get("commands", []))
    wb_host, wb_command = conf.get("wb_host", ""), conf.get("wb_command", "")
    if wb_host != "" and not _ascii(wb_host, 80):
        raise UsageError("pages.wb_host must be empty or 1 to 80 printable ASCII characters")  # never the value
    if wb_command != "" and not _copy_text(wb_command):
        raise UsageError(f"pages.wb_command must be empty or {COPY_RULE}")
    wb_command = wb_command or WB_COMMAND
    home = state.home(cfg)
    dest = Path(out).resolve()  # a symbolic link keeps pointing at the new pages
    if not home.is_dir():
        raise UsageError(f"no state home: {home}")
    if not dest.parent.is_dir():
        raise UsageError(f"the parent of --out is not a folder: {dest.parent}")
    if dest.exists() and not dest.is_dir():
        raise UsageError(f"--out is not a folder: {dest}")
    # the replace of out must never touch the state, and an item folder can be a link out of the home
    roots = [home.resolve(), *(p.resolve() for p in home.iterdir() if p.is_dir())]
    if any(dest.is_relative_to(r) or r.is_relative_to(dest) for r in roots):
        raise UsageError(f"--out must be outside the state home and its item folders and must not hold them: {dest}")
    if dest.exists() and not ours(dest):
        raise UsageError(f"--out holds files that wb pages did not write: {dest}")
    built = _utc()
    now, shown = _time(built).astimezone(tz), local_time(built, tz)
    items, bad = read_items(home)
    if any(line is not None and not _copy_text(line)
           for line in (filled(it, wb_command) for it in items if it["status"] == "waiting")):
        raise UsageError(f"pages.wb_command: the filled line of a case 3 or 4 wait must be {COPY_RULE}")
    fd = feed(home)
    for it in items:
        it["_id"] = item_id(it["id"], ado_url, pr_link(it, fd))
    pages = {"index.html": index(items, bad, shown, answer_url, fd, theme_key, ado_url, tz, rows, wb_host,
                                 wb_command),
             "results.html": results(items, now, shown, theme_key)}
    for kind, key, known in (("status", "status", STATUSES), ("stage", "stage", STAGES)):
        for name, n in _counts(items, key, known).items():
            if n:
                rows = [row(it) for it in items if it[key] == name]
                pages[page_name(kind, name)] = page(f"{kind.capitalize()}: {name}", shown, table(ROW_HEAD, rows),
                                                    theme_key)
    pages[MARKER] = "\n".join(sorted(pages))
    tmp = Path(tempfile.mkdtemp(dir=dest.parent, prefix=f".{dest.name}."))
    old = tmp.with_name(tmp.name + ".old")
    try:
        umask = os.umask(0)
        os.umask(umask)
        tmp.chmod(0o777 & ~umask)  # mkdtemp gives 0700; the site viewer must read the pages
        for name, text in pages.items():
            (tmp / name).write_text(text, encoding="utf-8")
        if dest.exists():
            dest.rename(old)
        tmp.rename(dest)
    except BaseException:
        if old.exists() and not dest.exists():
            old.rename(dest)
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    shutil.rmtree(old, ignore_errors=True)
