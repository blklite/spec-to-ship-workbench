#!/usr/bin/env python3
"""Cost of Claude Code transcripts at list prices.

2 modes:

    cost.py transcript <file.jsonl>     1 agent transcript
    cost.py session <dir>/<id>          a session: the main transcript
                                        <dir>/<id>.jsonl plus its subagents
                                        <dir>/<id>/subagents/agent-*.jsonl

Options (after the mode): --prices FILE (default: prices.json next to this script),
--window START END (ISO 8601 times; only records in [START, END) count),
--json (machine-readable output).

Prices live in prices.json, never in this file. Each assistant record is priced
by its model and by the TTL of its cache writes (5 minutes or 1 hour). A record
of a model that prices.json does not list is reported as unpriced, with its
calls and tokens, and is left out of the totals: it is never priced at $0.

The "low" figure uses the output_tokens of the usage block. The "high" figure
also estimates output from the text and tool input of the record plus its
thinking tokens, for transcripts where the usage block under-counts streamed
output. See README.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

DEFAULT_PRICES = Path(__file__).with_name("prices.json")
FIELDS = ("in", "cr", "cw5", "cw1", "out", "think")


# --- prices -----------------------------------------------------------------

def load_prices(path: str | Path = DEFAULT_PRICES) -> dict:
    with open(path, encoding="utf-8") as f:
        prices = json.load(f)
    mult = prices.get("cache_write_multiplier") or {}
    if not {"5m", "1h"} <= set(mult):
        raise ValueError("prices.json: cache_write_multiplier needs 5m and 1h")
    for name, p in (prices.get("models") or {}).items():
        missing = {"input", "output", "cache_read"} - set(p)
        if missing:
            raise ValueError(f"prices.json: model {name} lacks {sorted(missing)}")
    if "chars_per_token" not in prices:
        raise ValueError("prices.json: chars_per_token is missing")
    return prices


def model_price(prices: dict, model: str | None) -> dict | None:
    """The price entry of a model: its exact name, or the listed name plus a
    date suffix (name-20261001 takes the price of name). Any other model is
    unpriced, so that a new version never borrows an older version's price."""
    if not model:
        return None
    models = prices.get("models") or {}
    if model in models:
        return models[model]
    m = re.fullmatch(r"(.+)-\d{8}", model)
    return models.get(m.group(1)) if m else None


# --- reading ----------------------------------------------------------------

def _ts(s: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def read_calls(path: str | Path, window: tuple[str, str] | None = None) -> dict:
    """1 entry for each model call (message id). Streamed records of 1 call
    repeat its usage; each field keeps its largest value, so a call counts once."""
    lo_t = hi_t = None
    if window:
        lo_t, hi_t = _ts(window[0]), _ts(window[1])
    calls: dict = {}
    seen: set = set()
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict) or d.get("type") != "assistant":
                continue
            t = d.get("timestamp")
            if window:
                if not t or not (lo_t <= _ts(t) < hi_t):
                    continue
            m = d.get("message") or {}
            u = m.get("usage") or {}
            mid = m.get("id") or d.get("uuid")
            cur = calls.setdefault(mid, {"model": m.get("model"), "chars": 0, "ts": t,
                                         **{k: 0 for k in FIELDS}})
            cc = u.get("cache_creation") or {}
            cw5 = cc.get("ephemeral_5m_input_tokens")
            cw1 = cc.get("ephemeral_1h_input_tokens")
            if cw5 is None and cw1 is None:
                # a legacy record: only the total, which was the 5-minute TTL
                cw5, cw1 = u.get("cache_creation_input_tokens") or 0, 0
            values = {
                "in": u.get("input_tokens"),
                "cr": u.get("cache_read_input_tokens"),
                "cw5": cw5,
                "cw1": cw1,
                "out": u.get("output_tokens"),
                "think": (u.get("output_tokens_details") or {}).get("thinking_tokens"),
            }
            for k, v in values.items():
                cur[k] = max(cur[k], v or 0)
            for b in m.get("content") or []:
                if not isinstance(b, dict):
                    continue
                key = (mid, b.get("type"), b.get("id") or (b.get("text") or "")[:80])
                if key in seen:
                    continue
                seen.add(key)
                if b.get("type") == "text":
                    cur["chars"] += len(b.get("text") or "")
                elif b.get("type") == "tool_use":
                    cur["chars"] += len(json.dumps(b.get("input"), ensure_ascii=False))
    return calls


# --- pricing ----------------------------------------------------------------

def price_calls(calls: dict, prices: dict) -> dict:
    mult = prices["cache_write_multiplier"]
    cpt = prices["chars_per_token"]
    lo = hi = 0
    unpriced: dict = {}
    tokens = {k: 0 for k in FIELDS}
    priced_calls = 0
    for c in calls.values():
        total = sum(c[k] for k in FIELDS)
        if total == 0:
            continue  # an empty record (no usage) costs nothing at any price
        p = model_price(prices, c["model"])
        if p is None:
            u = unpriced.setdefault(c["model"] or "<no model>", {"calls": 0, "tokens": 0})
            u["calls"] += 1
            u["tokens"] += total
            continue
        priced_calls += 1
        for k in FIELDS:
            tokens[k] += c[k]
        fixed = (c["in"] * p["input"]
                 + c["cw5"] * p["input"] * mult["5m"]
                 + c["cw1"] * p["input"] * mult["1h"]
                 + c["cr"] * p["cache_read"]) / 1e6
        out_est = max(c["out"], round(c["chars"] / cpt) + c["think"])
        lo += fixed + c["out"] * p["output"] / 1e6
        hi += fixed + out_est * p["output"] / 1e6
    return {"calls": priced_calls, "tokens": tokens, "low": lo, "high": hi,
            "unpriced": unpriced}


def _times(calls: dict) -> dict:
    ts = sorted(c["ts"] for c in calls.values() if c["ts"])
    return {"first": ts[0], "last": ts[-1]} if ts else {}


def transcript_cost(path: str | Path, prices: dict, window=None) -> dict:
    calls = read_calls(path, window)
    r = price_calls(calls, prices)
    r.update(_times(calls))
    r["file"] = str(path)
    return r


def _merge_unpriced(*parts: dict) -> dict:
    out: dict = {}
    for part in parts:
        for model, u in part.items():
            o = out.setdefault(model, {"calls": 0, "tokens": 0})
            o["calls"] += u["calls"]
            o["tokens"] += u["tokens"]
    return out


def session_cost(prefix: str | Path, prices: dict, window=None) -> dict:
    """prefix is <dir>/<id>: main share <dir>/<id>.jsonl, subagent share
    <dir>/<id>/subagents/agent-*.jsonl (with agent-*.meta.json beside)."""
    prefix = Path(str(prefix).removesuffix(".jsonl"))
    main_file = prefix.with_name(prefix.name + ".jsonl")
    if not main_file.is_file():
        raise FileNotFoundError(f"main transcript not found: {main_file}")
    main = transcript_cost(main_file, prices, window)
    subs = []
    for p in sorted((prefix / "subagents").glob("agent-*.jsonl")):
        r = transcript_cost(p, prices, window)
        meta_file = p.with_name(p.name.removesuffix(".jsonl") + ".meta.json")
        meta = {}
        if meta_file.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except ValueError:
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
        r["id"] = p.name.removeprefix("agent-").removesuffix(".jsonl")
        r["description"] = meta.get("description")
        r["agent_type"] = meta.get("agentType")
        subs.append(r)
    sub_lo = sum(s["low"] for s in subs)
    sub_hi = sum(s["high"] for s in subs)
    return {
        "main": main,
        "subagents": subs,
        "subagent_low": sub_lo,
        "subagent_high": sub_hi,
        "total_low": main["low"] + sub_lo,
        "total_high": main["high"] + sub_hi,
        "unpriced": _merge_unpriced(main["unpriced"], *(s["unpriced"] for s in subs)),
    }


# --- output -----------------------------------------------------------------

def _money(lo: float, hi: float) -> str:
    return f"${lo:,.2f}" if round(lo, 2) == round(hi, 2) else f"${lo:,.2f} to ${hi:,.2f}"


def _unpriced_lines(unpriced: dict) -> list[str]:
    if not unpriced:
        return ["unpriced: none"]
    return [f"UNPRICED (not in the totals): {m}: {u['calls']} calls, {u['tokens']:,} tokens"
            for m, u in sorted(unpriced.items())]


def format_transcript(r: dict) -> str:
    lines = [f"transcript {r['file']}: {r['calls']} priced calls, {_money(r['low'], r['high'])}"]
    return "\n".join(lines + _unpriced_lines(r["unpriced"]))


def format_session(s: dict) -> str:
    m = s["main"]
    lines = [f"main session:  {_money(m['low'], m['high'])}  ({m['calls']} calls)"]
    for sub in s["subagents"]:
        desc = sub["description"] or "(no description)"
        lines.append(f"  subagent {sub['id'][:12]:12} {desc[:40]:40} {_money(sub['low'], sub['high'])}")
    lines.append(f"subagents:     {_money(s['subagent_low'], s['subagent_high'])}  ({len(s['subagents'])} agents)")
    lines.append(f"total:         {_money(s['total_low'], s['total_high'])}")
    lines.append(f"low/high:      ${s['total_low']:,.2f} / ${s['total_high']:,.2f}")
    return "\n".join(lines + _unpriced_lines(s["unpriced"]))


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--prices", default=str(DEFAULT_PRICES), help="prices.json to use")
    common.add_argument("--window", nargs=2, metavar=("START", "END"),
                        help="ISO 8601 times; only records in [START, END) count")
    common.add_argument("--json", action="store_true", help="print JSON")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    t = sub.add_parser("transcript", parents=[common], help="1 agent transcript")
    t.add_argument("file")
    s = sub.add_parser("session", parents=[common], help="a main transcript and its subagents")
    s.add_argument("prefix", help="<dir>/<id>: the main transcript is <dir>/<id>.jsonl")
    args = ap.parse_args(argv)
    try:
        prices = load_prices(args.prices)
        window = None
        if args.window:
            window = tuple(args.window)
            _ts(window[0]), _ts(window[1])  # reject a bad time before reading
        if args.mode == "transcript":
            r = transcript_cost(args.file, prices, window)
            text = format_transcript(r)
        else:
            r = session_cost(args.prefix, prices, window)
            text = format_session(r)
    except (OSError, ValueError) as e:
        # a missing file, a bad prices.json, or a bad --window time
        print(f"cost.py: {e}", file=sys.stderr)
        return 2
    print(json.dumps(r, indent=1) if args.json else text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
