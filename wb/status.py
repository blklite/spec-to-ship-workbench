"""`wb status [<id>] [--json]`: 1 line for each item, or the detail of 1 item."""

from __future__ import annotations

from wb import state

TOKENS = ("input", "cache_read", "cache_write", "output")


def line(st: dict) -> str:
    lim = st["limits"]
    return (f"{st['id']} stage={st['stage']} status={st['status']}"
            f" rounds={st['rounds_used']}/{lim['rounds']}"
            f" usd={st['usd']:.2f}/{lim['usd']:g} hours={st['seconds'] / 3600:.2f}/{lim['hours']:g}")


def _usd(v) -> str:
    return f"{v:.4f}" if isinstance(v, (int, float)) else str(v)


def detail(st: dict) -> list[str]:
    repo = st["work"].get("repo")  # a state of before [repos] has no key
    out = [line(st) + (f" repo={repo}" if repo else "")]
    for s in st["steps"]:
        out.append(f"  step {s['n']}: {s['stage']} {s['role']} on {s['runner']}: {s['status']}"
                   f" usd={_usd(s['usd'])} seconds={s['seconds']:.1f}"
                   f" last line: {s['last_line'] or s['reason']} (run folder: {s['run_folder']})")
    q = st["question"]
    if q:
        where = f" (run folder: {q['run_folder']})" if q.get("run_folder") else ""
        out.append(f"  question: case {q['case']}: {' '.join(q['text'].split())}{where}")
    out += [f"  not verified: {x}" for x in st["not_verified"]]
    out += [f"  approval: {a['kind']} at {a['head']} on {a['time']}" + (" (void)" if a.get("void") else "")
            for a in st["approvals"]]
    rec = st.get("records")
    if rec:
        out.append("  records: " + records(rec))
    return out


def records(rec: dict) -> str:
    """1 short value for each record that the item has."""
    parts = []
    if "answers" in rec:
        parts.append(f"answers={len(rec['answers'])}")
    if "pr" in rec:
        parts.append(f"pr={rec['pr']['number']}")
    if "gates" in rec:
        parts.append(f"gates={len(rec['gates'])}")
    if "merge" in rec:
        parts.append(f"merge={rec['merge']['sha']}")
    if "follow_ups" in rec:
        parts.append(f"follow_ups={','.join(rec['follow_ups'])}")
    if "diff" in rec:
        d = rec["diff"]
        parts.append("diff=error" if "error" in d else f"diff={d['files']} files +{d['added']} -{d['deleted']}")
    return " ".join(parts)


def costs(st: dict) -> dict:
    """Dollars, tokens and seconds of the steps by stage and by role. An
    unpriced step adds no dollars; it is counted in unpriced_steps."""
    def empty():
        return {"usd": 0.0, "unpriced_steps": 0, **{k: 0 for k in TOKENS}, "seconds": 0.0}

    by_stage, by_role, total = {}, {}, empty()
    for s in st["steps"]:
        for group in (by_stage.setdefault(s["stage"], empty()), by_role.setdefault(s["role"], empty()), total):
            if isinstance(s["usd"], (int, float)):
                group["usd"] += s["usd"]
            elif s["usd"] == "unpriced":
                group["unpriced_steps"] += 1
            for k in TOKENS:
                group[k] += s["tokens"].get(k) or 0
            group["seconds"] += s["seconds"] or 0
    return {"id": st["id"], "stage": st["stage"], "status": st["status"], "item_seconds": st["seconds"],
            "by_stage": by_stage, "by_role": by_role, "total": total}


def items(cfg: dict) -> list[dict]:
    home = state.home(cfg)
    names = sorted(p.parent.name for p in home.glob("*/state.json")) if home.is_dir() else []
    return [state.load(cfg, n) for n in names]
