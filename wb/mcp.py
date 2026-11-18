"""The MCP server over the wb command (see wb/README.md).

    python -m wb.mcp

MCP over stdio: JSON-RPC 2.0, 1 message for each line. 4 tools, each a call of
the wb command of the same name: wb_status, wb_intake, wb_answer and
wb_run_next. No tool approves, merges or runs a shell: an approval is given at
the host terminal only (wb approve).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path

from runner import UsageError, config
from wb import cli, intake, state

NAME = "wb"
VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")  # the newest first


def _tool(name: str, description: str, required: list[str], **props: str) -> dict:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "additionalProperties": False, "required": required,
                            "properties": {k: {"type": "string", "description": v} for k, v in props.items()}}}


ID = "the item id: letters, digits, - and _"
TOOLS = [
    _tool("wb_status", "1 line for each item, or the detail of 1 item (wb status).", [],
          id=ID + "; none for all items"),
    _tool("wb_intake", "Check a spec and start the item at the spec check; a new call amends the spec of a"
                       " waiting item (wb intake).", ["id", "spec"], id=ID, spec="the full text of the spec",
          repo="optional: a name of [repos] in the config (wb intake --repo); none for the origin of the"
               " runner clone, or the repository of the old state in an amendment"),
    _tool("wb_answer", "Store the owner's answer to a case 2 stop and set the item running (wb answer).",
          ["id", "answer"], id=ID, answer="the owner's words"),
    _tool("wb_run_next", "Start the detached run of the item and return at once (wb run-next).", ["id"], id=ID),
]
SCHEMAS = {t["name"]: t["inputSchema"] for t in TOOLS}
# An absolute path, POSIX or Windows; "0/2" and "a/b" are not. Right after a quote, all up to the same
# quote, spaces included; with no closing quote, up to white space.
UNQUOTED = re.compile(r"(?<![\w.~])(?:[A-Za-z]:)?[\\/][^\s'\":,;()]*")
PATH = re.compile(r"(?<=(['\"]))(?:[A-Za-z]:)?[\\/](?:(?!\1).)*(?=\1)|" + UNQUOTED.pattern)


class Refused(Exception):
    """A 1-line error result."""


def _run(argv: list[str], cfg: dict, personas: dict) -> tuple[int, str, str]:
    """(exit code, stdout, stderr) of the wb command; nothing reaches the protocol channel."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv, cfg=cfg, personas=personas)
        except SystemExit as e:  # argparse
            code = e.code if isinstance(e.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def _status(cfg, personas, args) -> list[str]:
    return ["status"] if args.get("id") is None else ["status", args["id"]]


# The waits that only the owner clears at the host terminal (wb/README.md)
OWNER_WAITS = {1: "the budget stop", 3: "the High tier wait", 4: "the round limit"}


def _owner_wait(cfg, item_id: str, command: str, cases) -> None:
    """Refuse a waiting item whose case is in cases."""
    if not state.exists(cfg, item_id):
        return
    st = state.load(cfg, item_id)
    case = (st["question"] or {}).get("case") if st["status"] == "waiting" else None
    if case in cases:
        raise Refused(f"WB: {command} REFUSE {item_id} - case {case} ({OWNER_WAITS[case]}) waits for the owner"
                      " at the host terminal")


def _intake(cfg, personas, args) -> list[str]:
    if "repo" in args and not intake.REPO_NAME.fullmatch(args["repo"]):  # before the argv: "-x" is not a flag
        raise Refused(f"wb_intake: bad repo {args['repo']!r}: use letters, digits, - and _ only")
    # An amended spec could lower the tier, raise the limits or clear the round limit: a case 2 wait only
    _owner_wait(cfg, args["id"], "INTAKE", OWNER_WAITS)
    data = args["spec"].encode("utf-8")
    if len(data) > cfg["specs"]["max_kb"] * 1024:  # before the write: a loop of oversized calls fills no disk
        problems = intake.check(data, cfg["specs"]["max_kb"])[0]
        if state.exists(cfg, args["id"]) and (st := state.load(cfg, args["id"]))["status"] != "waiting":
            # the text of wb/intake.py intake()
            problems.append(f"item {args['id']} exists and does not wait for the owner (status {st['status']});"
                            " a new intake replaces the spec of a waiting item only")
        raise Refused(f"WB: INTAKE REFUSE {args['id']} - {len(problems)} problems: " + "; ".join(problems))
    # A new file for each text: a refused amendment keeps the spec that the state names.
    path = state.item_dir(cfg, args["id"]) / "specs" / f"{hashlib.sha256(data).hexdigest()[:16]}.md"
    repo = [f"--repo={args['repo']}"] if "repo" in args else []
    return ["intake", str(path), "--id", args["id"], *repo]  # call() writes the file


def _answer(cfg, personas, args) -> list[str]:
    _owner_wait(cfg, args["id"], "ANSWER", (4,))  # the CLI answers case 4; a front end does not; it refuses 1 and 3
    return ["answer", args["id"], "--file"]


def _run_next(cfg, personas, args) -> list[str]:
    return ["run-next", args["id"]]  # never --foreground: the run is detached


CALLS = {"wb_status": _status, "wb_intake": _intake, "wb_answer": _answer, "wb_run_next": _run_next}


def _one_line(text: str, cfg: dict | None, item_id: str | None) -> str:
    """1 line, with each path outside the item folder replaced by <path>."""
    keep = []
    if cfg is not None and item_id is not None:
        with contextlib.suppress(UsageError):
            folder = state.item_dir(cfg, item_id)
            keep = [str(folder), str(folder.resolve())]  # intake names the spec by its resolved path
    # In a kept quote: a path from the item folder whole, though the folder has a space, then each other path
    inner = re.compile("|".join([*(r"(?<![\w.~])" + re.escape(k) + r"[^\s'\":,;()]*"
                                   for k in sorted(keep, key=len, reverse=True)), UNQUOTED.pattern]))

    def path(m):
        p = m.group(0)
        if not any(p == k or p.startswith(k + os.sep) for k in keep):
            return "<path>"
        return inner.sub(path, p) if m.re is PATH and m.group(1) else p  # a kept quote: each path in it

    return PATH.sub(path, " ".join(text.split()))


def call(name: str, args, cfg=None, personas=None) -> tuple[str, bool]:
    """(the text, is an error) of 1 tool call. cfg and personas: as wb.cli.main."""
    item_id = None
    try:
        if not isinstance(args, dict):
            raise Refused("the arguments must be an object")
        for key in SCHEMAS[name]["required"]:
            if key not in args:
                raise Refused(f"{name}: the argument {key} is missing")
        for key, value in args.items():
            if key not in SCHEMAS[name]["properties"]:
                raise Refused(f"{name}: unknown argument {key}")
            if not isinstance(value, str):
                raise Refused(f"{name}: the argument {key} must be a string")
        if "id" in args:
            item_id = state.check_id(args["id"])  # before the argv: "-x" is not a flag, "../x" not a folder
        cfg = cfg if isinstance(cfg, dict) else config.load_workbench(cfg)  # per call: a config error is a tool error
        personas = personas if isinstance(personas, dict) else config.load_personas(personas)
        argv = CALLS[name](cfg, personas, args)
        if name == "wb_intake":
            path, code = Path(argv[1]), None
            new = not path.exists()  # the same text sent again: its file may be the spec that the state names
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(args["spec"].encode("utf-8"))
                code, out, err = _run(argv, cfg, personas)
            finally:  # a refused intake keeps no data; a state saved before a later error names the file
                if new and code != 0 and not (state.exists(cfg, args["id"]) and state.load(cfg, args["id"])
                                              ["spec"]["path"] == str(path.resolve())):
                    path.unlink(missing_ok=True)
        elif name != "wb_answer":
            code, out, err = _run(argv, cfg, personas)
        else:
            fd, tmp = tempfile.mkstemp(suffix=".md")
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                    f.write(args["answer"])
                code, out, err = _run(argv + [tmp], cfg, personas)
            finally:
                os.unlink(tmp)
    except (Refused, UsageError) as e:
        text = str(e) if isinstance(e, Refused) else f"wb: {e}"
        return _one_line(text, cfg if isinstance(cfg, dict) else None, item_id), True
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return f"wb: {name} failed: an internal error (the detail is on the stderr of the server)", True
    if code == 0:
        return out.rstrip("\n"), False
    lines = (out if code == 1 else err).splitlines() or [f"wb: exit {code}"]
    # a refused intake: its last line, then each problem
    text = lines[-1] + (": " + "; ".join(lines[:-1]) if len(lines) > 1 else "")
    return _one_line(text, cfg, item_id), True


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(msg, cfg=None, personas=None) -> dict | None:
    """The reply to 1 message, or None for a notification."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
    if "id" not in msg:  # JSON-RPC 2.0: a notification gets no reply
        return None
    method, params, msg_id = msg["method"], msg.get("params") or {}, msg["id"]
    if not isinstance(params, dict):
        return _error(msg_id, -32602, "params must be an object")
    if method == "initialize":
        version = params.get("protocolVersion")
        result = {"protocolVersion": version if version in VERSIONS else VERSIONS[0],
                  "capabilities": {"tools": {}}, "serverInfo": {"name": NAME, "version": "0.2.0"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments")
        if not isinstance(name, str) or name not in CALLS:
            return _error(msg_id, -32602, f"unknown tool {name!r}")
        text, is_error = call(name, {} if args is None else args, cfg, personas)
        result = {"content": [{"type": "text", "text": text}], "isError": is_error}
    else:
        return _error(msg_id, -32601, f"method not found: {method}")
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def serve(stdin, stdout, cfg=None, personas=None) -> None:
    """Read 1 message for each line of stdin until its end; write each reply as 1 line."""
    for line in stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = _error(None, -32700, "parse error")
        else:
            reply = handle(msg, cfg, personas)
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve(sys.stdin, sys.stdout)
