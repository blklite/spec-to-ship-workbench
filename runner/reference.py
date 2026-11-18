"""Run the reference items on a runner (reference/README.md).

    python -m runner.reference --runner R [--item ID] [--timeout S]

For each item of reference/ (or the 1 that --item names): a throwaway clone of
this repo at the item's base, with the item's spec.md added at its root; a
prompt of the item id and the spec path, then prompts/spec-check.md byte for
byte; 1 step through run_step; then the clone is deleted. It prints 1 row for
reference/results.md for each item. A usage error exits 2.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from runner import ROOT, UsageError
from runner.step import run_step

REFERENCE = ROOT / "reference"
SPEC_CHECK = ROOT / "prompts" / "spec-check.md"
SPEC_NAME = "spec.md"
DEFAULT_TIMEOUT = 1800
COLUMNS = ("date", "item", "runner", "model", "status", "last line", "tokens (in/out/cache read/cache write)",
           "seconds", "dollars", "found", "missed", "source")


def items(ref_dir: Path = REFERENCE) -> list[dict]:
    """The items: the keys of each item.toml, with "folder"."""
    found = []
    for folder in sorted(p for p in Path(ref_dir).iterdir() if (p / "item.toml").is_file()):
        try:
            with (folder / "item.toml").open("rb") as f:
                item = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise UsageError(f"cannot read {folder / 'item.toml'}: {e}") from e
        for key in ("id", "step", "base"):
            if not isinstance(item.get(key), str) or not item[key]:
                raise UsageError(f"{folder / 'item.toml'}: the key {key} is missing")
        if not re.fullmatch(r"[0-9a-f]{7,40}", item["base"]):
            raise UsageError(f"{folder / 'item.toml'}: base must be a commit id, got {item['base']!r}")
        found.append({**item, "folder": folder})
    return found


def prompt_bytes(item_id: str, spec_check: Path = SPEC_CHECK) -> bytes:
    head = f"The id of the item: {item_id}\nThe path of the spec: {SPEC_NAME}\n\n"
    return head.encode("utf-8") + Path(spec_check).read_bytes()


def _git(what: str, *args: str) -> None:
    done = subprocess.run(["git", *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise UsageError(f"git {what} failed: {done.stderr.strip()}")


def _remove(path: Path) -> None:
    def writable(func, p, _exc):  # git makes its object files read-only (Windows)
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=writable)
    else:
        shutil.rmtree(path, onerror=writable)


def _cell(v) -> str:
    return "" if v is None else " ".join(str(v).split()).replace("|", "\\|")


def row(item_id: str, counters: dict) -> str:
    tokens = "/".join("-" if counters.get(f"{k}_tokens") is None else str(counters[f"{k}_tokens"])
                      for k in ("input", "output", "cache_read", "cache_write"))
    usd = counters.get("usd")
    dollars = f"{usd:.2f}" if isinstance(usd, float) else ("-" if usd is None else usd)
    cells = (counters["start"][:10], item_id, counters["runner"], counters["model"], counters["status"],
             counters.get("last_line"), tokens, counters.get("seconds"), dollars, "", "",
             Path(os.path.relpath(counters["run_folder"], ROOT)).as_posix())
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def run_item(item: dict, runner: str, timeout_s: float, *, cfg=None, repo: Path = ROOT,
             spec_check: Path = SPEC_CHECK, prices_file=None) -> str:
    """1 reference run; returns its row. The clone is deleted on each status."""
    tmp = Path(tempfile.mkdtemp(prefix=f"reference-{item['id']}-"))
    try:
        clone = tmp / "clone"
        # --shared: the clone reads the objects of repo, so any base in its history checks out
        _git("clone", "clone", "-q", "--shared", "--no-checkout", str(repo), str(clone))
        _git("checkout", "-C", str(clone), "checkout", "-q", "--detach", f"{item['base']}^{{commit}}")
        shutil.copyfile(item["folder"] / SPEC_NAME, clone / SPEC_NAME)
        prompt = tmp / "prompt.md"
        prompt.write_bytes(prompt_bytes(item["id"], spec_check))
        counters = run_step(clone, prompt, timeout_s, runner, None, cfg=cfg, prices_file=prices_file)
    finally:
        _remove(tmp)
    return row(item["id"], counters)


def main(argv: list[str] | None = None, *, cfg=None, ref_dir: Path = REFERENCE, repo: Path = ROOT) -> int:
    ap = argparse.ArgumentParser(prog="python -m runner.reference", description=__doc__.split("\n\n")[0])
    ap.add_argument("--runner", required=True, help="a [runners.<name>] table of the config")
    ap.add_argument("--item", help="the id of 1 item (default: each item)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="the time limit of 1 run, in seconds")
    args = ap.parse_args(argv)
    try:
        chosen = [i for i in items(ref_dir) if args.item in (None, i["id"])]
        if not chosen:
            raise UsageError(f"no reference item {args.item}" if args.item else "no reference item")
        for item in chosen:
            print(run_item(item, args.runner, args.timeout, cfg=cfg, repo=repo), flush=True)
    except UsageError as e:
        print(f"runner.reference: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
