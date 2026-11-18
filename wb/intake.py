"""`wb intake <spec> --id <id>`: check a spec against docs/specs.md, then create
the tracker item and the item state at stage 2 (the spec check).

A spec that is too large, lacks a header line or a section, has a bad tier or
size, an AC with no *Proof:*, or an open-question mark is refused, with 1 line
for each problem.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from runner import UsageError
from runner.adapters import SAFE_VALUE
from runner.step import _utc
from wb import push, state, tracker

HEADER = ("**Status:**", "**Work item:**", "**Tier:**", "**Size:**", "**Tests that the item can retire:**")
SECTIONS = ("## Story", "## Rulings", "## Acceptance criteria", "## Non-goals", "## Checks", "## Delivery")
TIER = re.compile(r"\*\*Tier:\*\* (Low|Medium|High)\b")
SIZE = re.compile(r"\*\*Size:\*\* (Small|Medium), \$([0-9]+(?:\.[0-9]+)?), ([0-9]+(?:\.[0-9]+)?) hours?,"
                  r" ([0-9]+) rework rounds?\b")
OPEN_HEADING = re.compile(r"^#{1,6}[ \t]+Open questions[ \t#]*$", re.I)
OPEN_WORD = re.compile(r"\b(TBD|TODO)\b")
FENCE = re.compile(r"^[ \t]{0,3}(```|~~~)")
INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1")
# A repository name goes on a command line: no leading "-".
REPO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def without_code(text: str) -> list[str]:
    """The lines of the text with fenced blocks and inline code blanked; the line numbers stay."""
    lines, fence = [], None
    for line in text.split("\n"):
        m = FENCE.match(line)
        if fence is None and m:
            fence, line = m.group(1), ""
        elif fence is not None:
            if line.strip().startswith(fence):
                fence = None
            line = ""
        lines.append(INLINE_CODE.sub(lambda c: " " * len(c.group(0)), line))
    return lines


def _section(lines: list[str], heading: str) -> list[str]:
    start = next(i for i, x in enumerate(lines) if x.rstrip() == heading)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return lines[start + 1:end]


def check(data: bytes, max_kb) -> tuple[list[str], dict]:
    """(the problems, the fields that a good spec gives)."""
    problems, fields = [], {}
    if len(data) > max_kb * 1024:
        problems.append(f"the spec is {len(data) / 1024:.1f} KB, above the limit of {max_kb} KB")
    try:
        text = data.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        return problems + ["the spec is not UTF-8 text"], fields
    lines = without_code(text)
    first = next((i for i, x in enumerate(lines) if x.startswith("## ")), len(lines))
    head = lines[:first]
    title = next((x[2:].strip() for x in head if x.startswith("# ")), None)
    if not title:
        problems.append("a header line is missing: # <Title>")
    for name in HEADER:
        if not any(name in x for x in head):
            problems.append(f"a header line is missing: {name}")
    for name in SECTIONS:
        if not any(x.rstrip() == name or x.startswith(name + " ") for x in lines):
            problems.append(f"a section is missing: {name}")
    header = "\n".join(head)
    tier, size = TIER.search(header), SIZE.search(header)
    if "**Tier:**" in header and not tier:
        problems.append("a bad tier: the Tier line must start with Low, Medium or High")
    if "**Size:**" in header and not size:
        problems.append("a bad size: the Size line must be <Small | Medium>, $<dollars>, <hours> hours,"
                        " <rounds> rework rounds")
    if "## Acceptance criteria" in [x.rstrip() for x in lines]:
        acs = re.split(r"\n(?=[0-9]+\. )", "\n" + "\n".join(_section(lines, "## Acceptance criteria")))
        acs = [a for a in acs if re.match(r"[0-9]+\. ", a)]
        if not acs:
            problems.append("the section Acceptance criteria has no numbered AC")
        for ac in acs:
            if "*Proof:*" not in ac:
                problems.append(f"AC {ac.split('.', 1)[0]} has no *Proof:*")
    for n, line in enumerate(lines, 1):
        if OPEN_HEADING.match(line):
            problems.append(f"line {n}: an open-question mark: an Open questions heading")
        for word in OPEN_WORD.findall(line):
            problems.append(f"line {n}: an open-question mark: {word}")
    if tier and size:
        fields = {"title": title, "tier": tier.group(1), "size": size.group(1),
                  "limits": {"usd": float(size.group(2)), "hours": float(size.group(3)),
                             "rounds": int(size.group(4))}}
    return problems, fields


def work_home(cfg: dict) -> Path:
    try:
        return state.under_root(cfg["work"]["home"])
    except (KeyError, TypeError):
        raise UsageError("the config has no work.home") from None


def repos(cfg: dict) -> dict:
    """The table [repos] (name: GitHub clone URL), checked; empty when the config has none."""
    table = cfg.get("repos", {})
    if not isinstance(table, dict):
        raise UsageError("the config key repos must be a table: [repos]")
    for name, url in table.items():
        if not REPO_NAME.fullmatch(name):
            raise UsageError(f"the config key repos.{name}: a bad name: use letters, digits, - and _ only")
        if not isinstance(url, str) or not push.ORIGIN.fullmatch(url):
            raise UsageError(f"the config key repos.{name}: {url!r} is not a GitHub clone URL")
    return table


def intake(cfg: dict, spec: str, item_id: str, branch: str | None = None, repo: str | None = None) -> list[str]:
    """The problems; none means the item now waits at stage 2 for run-next."""
    state.check_id(item_id)
    if branch is not None and not SAFE_VALUE.fullmatch(branch):
        raise UsageError(f"bad branch name {branch!r}")
    spec_path = Path(spec).resolve()
    try:
        data = spec_path.read_bytes()
    except OSError as e:
        raise UsageError(f"cannot read the spec {spec}: {e}") from e
    tr = tracker.make(cfg)
    problems, fields = check(data, cfg["specs"]["max_kb"])
    old = state.load(cfg, item_id) if state.exists(cfg, item_id) else None
    if old is not None and old["status"] != "waiting":
        problems.append(f"item {item_id} exists and does not wait for the owner (status {old['status']});"
                        " a new intake replaces the spec of a waiting item only")
    known = repos(cfg)
    if repo is not None and repo not in known:
        problems.append(f"unknown repository {repo!r}: the config [repos] names {', '.join(known) or 'none'}")
    old_work = old["work"] if old else {}  # a state of before [repos] has no repo and no url: null
    url = known[repo] if repo in known else old_work.get("url")
    folder = work_home(cfg) / item_id
    if old is not None and url != old_work.get("url") and folder.exists():
        problems.append(f"the work folder {folder} exists and clones another repository;"
                        " the owner deletes it by hand before a new repository")
    if problems:
        return problems
    st = old or state.new(item_id)
    for a in st["approvals"]:  # an approval was given for the old spec
        a.setdefault("void", _utc())
    st.update(fields)
    st["spec"] = {"path": str(spec_path), "sha256": hashlib.sha256(data).hexdigest()}
    st["work"] = {"folder": str(folder),
                  "branch": branch or (old and old["work"]["branch"]) or f"feature/{item_id}",
                  "base": (cfg.get("work") or {}).get("base") or "main",
                  "repo": repo if repo in known else old_work.get("repo"), "url": url}
    if url != old_work.get("url"):  # the origin, base and push of the old repository: run-next records them again
        st.pop("remote", None)
        st.pop("pushed", None)
    st.update(stage="spec-check", status="running", question=None, retried=False, review_pass={})
    state.save(cfg, st)
    if old is None:
        tr.create(item_id, fields["title"], "In Review")
    else:
        tr.set_state(item_id, "In Review")
    return []
