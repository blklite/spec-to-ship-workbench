"""Run 1 step: a prompt file, in a folder, on a configured runner, with a time limit.

    python -m runner.step --folder F --prompt P --timeout S --runner R [--out D]

The last line of the output is

    STEP: <status> <runner>: <the last line of the step, or the reason>

with status 1 of OK, INVALID, TIMEOUT, UNAVAILABLE, FAILED. The exit code is 0
for each status, and 2 for a usage error (then there is no STEP line and no run
folder). The run folder holds counters.json, stdout.txt and stderr.txt, and
reply.txt when the adapter gives the text of each turn (Reply.report).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import shutil
import sys
import uuid
from pathlib import Path

from runner import ROOT, UsageError, adapters, config, lastline, pricing, proc

STATUSES = ("OK", "INVALID", "TIMEOUT", "UNAVAILABLE", "FAILED")


def _decide(reply, allowed, code, timed_out, seconds, exe_name, exit_fails=False):
    """(status, reason or last line, last line). exit_fails: a non-zero exit is
    FAILED whatever the last line says (the adapter's EXIT_FAILS)."""
    if timed_out:
        return "TIMEOUT", f"stopped at the time limit after {seconds:.1f} s", None
    line = lastline.last_line(reply.final_text)
    if reply.unavailable:
        return "UNAVAILABLE", reply.unavailable, line
    if lastline.matches(line, allowed) and not (exit_fails and code != 0):
        return "OK", line, line
    why = f" ({reply.error})" if reply.error else ""
    if code != 0:
        return "FAILED", f"{exe_name} exited with code {code}{why}", line
    if line is None:
        return "INVALID", "no final text" + why, None
    return "INVALID", f"the last line is not an allowed line: {line}", line


def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _new_run_dir(runner: str) -> Path:
    """A new folder under runs/ of the repo, which git ignores."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return ROOT / "runs" / f"{stamp}-{runner}-{uuid.uuid4().hex[:8]}"


def run_step(folder, prompt_file, timeout_s, runner, out_dir, *, cfg=None, prices_file=None) -> dict:
    """Run 1 step and return its counters (also written to counters.json).

    cfg is the workbench config (a dict, a root folder or a file path); None
    reads this repo's config, the local file first. prices_file defaults to
    cost/prices.json. A usage error raises UsageError before anything is
    written."""
    if not isinstance(cfg, dict):
        cfg = config.load_workbench(cfg)
    table = config.runner_config(cfg, runner)
    adapter = adapters.get(table["adapter"])
    adapter.validate(runner, table)
    wrap = adapters.check_wrap(runner, table)
    folder = Path(folder)
    if not folder.is_dir():
        raise UsageError(f"the work folder does not exist: {folder}")
    prompt_file = Path(prompt_file)
    allowed = lastline.read_allowed(prompt_file)
    prompt = prompt_file.read_bytes()
    if not (isinstance(timeout_s, (int, float)) and math.isfinite(timeout_s) and timeout_s > 0):
        raise UsageError(f"the time limit must be a number of seconds above 0, got {timeout_s}")
    prices = pricing.load(prices_file)
    exe_name = adapter.program(table)
    exe = shutil.which(exe_name)
    base = adapter.argv(table, exe or exe_name, folder.resolve())
    cmd = wrap + base
    forbidden = getattr(adapter, "FORBIDDEN_ARGS", set())
    # the CLI may be a cmd.exe shim; its own path is not 1 of its arguments
    adapters.check_argv([exe or exe_name, *wrap[1:], *base[1:]], exe or exe_name, forbidden)
    if wrap:  # the wrap may be a cmd.exe shim too, and the CLI path is 1 of its arguments
        try:
            adapters.check_argv(cmd, wrap[0], forbidden)
        except UsageError as e:
            raise UsageError(f"runner {runner}: wrap {wrap[0]}: {e}") from None
    # the optional env(table, folder) adds variables to the CLI's environment
    extra_env = adapter.env(table, folder.resolve()) if hasattr(adapter, "env") else {}
    env = {**os.environ, **extra_env}
    # the optional SECRET_ENV names the variables whose values no stored file may hold: proc.run
    # redacts them as the CLI writes, and a run with none keeps the plain files
    secrets = [env[k].encode("utf-8") for k in getattr(adapter, "SECRET_ENV", ()) if env.get(k)]
    refused = None
    if exe is None:
        refused = f"{exe_name} is not on PATH ({os.environ.get('PATH', os.defpath)})"
    elif not wrap and hasattr(adapter, "file_secrets"):  # a wrap has its own home and redacts
        # the optional file_secrets(env, folder): the values of the CLI's credentials file; with no secret
        # at all, the CLI does not start. No path and no text of the file goes to the reason.
        try:
            secrets += [v.encode("utf-8") for v in adapter.file_secrets(env, folder.resolve())]
        except (OSError, ValueError, RecursionError):  # RecursionError: JSON nested too deep
            refused = "the credentials file of the CLI can not be read"
        else:
            if not secrets:
                refused = "no credential to redact"
    redact = {"secrets": secrets} if secrets else {}

    run_dir = Path(out_dir) if out_dir else _new_run_dir(runner)
    run_dir.mkdir(parents=True, exist_ok=True)
    out_path, err_path = run_dir / "stdout.txt", run_dir / "stderr.txt"
    (run_dir / "reply.txt").unlink(missing_ok=True)  # a run folder can be reused, as stdout.txt is overwritten
    counters = {
        "runner": runner,
        "adapter": table["adapter"],
        "model": table["model"],
        "guard": config.guard(table),
        "prompt": str(prompt_file),
        "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
        "start": _utc(),
    }
    if wrap:
        counters["wrap"] = wrap
    if refused:
        out_path.write_bytes(b"")
        err_path.write_bytes(b"")
        status, text, line, code, seconds = "UNAVAILABLE", refused, None, None, 0.0
        reply = adapters.Reply()
    else:
        try:
            code, timed_out, seconds, kill_problem, capture_error = proc.run(
                cmd, folder, prompt, out_path, err_path, timeout_s, env, **redact)
        except OSError as e:  # found on PATH, but it does not start
            code, timed_out, seconds, kill_problem, start_error = None, False, 0.0, None, e
        else:
            start_error = None
        if start_error is not None:
            reply = adapters.Reply()
            status, line = "UNAVAILABLE", None
            starter = Path(wrap[0]).name if wrap else exe_name
            text = f"{starter} does not start: {start_error.strerror or start_error}"
        else:
            # parsed on a timeout too: the usage of the finished turns still counts
            stdout = out_path.read_bytes().decode("utf-8", errors="replace")
            reply = adapter.parse(stdout, table)
            status, text, line = _decide(reply, allowed, code, timed_out, seconds, Path(exe_name).name,
                                         getattr(adapter, "EXIT_FAILS", False))
            if capture_error is not None:  # the output is lost: no OK, no TIMEOUT on what is left
                status = "FAILED"
                text = f"the output capture failed: {getattr(capture_error, 'strerror', None) or capture_error}"
            if kill_problem:  # the status stays: the CLI itself was killed, a child may still run
                text += f"; the process tree kill failed ({kill_problem}), so only the CLI was killed"
    if reply.report:  # bytes: the same file on each OS
        (run_dir / "reply.txt").write_bytes(reply.report.encode("utf-8"))
    usd = pricing.dollars(reply.calls, prices)
    counters.update({
        "end": _utc(),
        "seconds": round(seconds, 3),
        "cli_seconds": reply.cli_seconds,
        **{f"{k}_tokens": v for k, v in reply.tokens.items()},
        **({"commands": reply.commands} if reply.commands is not None else {}),
        "usd": round(usd, 6) if isinstance(usd, float) else usd,
        "status": status,
        "last_line": line,
        "reason": text,
        "exit_code": code,
    })
    (run_dir / "counters.json").write_text(json.dumps(counters, indent=1), encoding="utf-8")
    counters["run_folder"] = str(run_dir)
    return counters


def step_line(counters: dict) -> str:
    text = " ".join(str(counters["reason"]).split())
    return f"STEP: {counters['status']} {counters['runner']}: {text}"


def main(argv: list[str] | None = None, *, cfg=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m runner.step", description=__doc__.split("\n\n")[0])
    ap.add_argument("--folder", required=True, help="the work folder")
    ap.add_argument("--prompt", required=True, help="the prompt file")
    ap.add_argument("--timeout", required=True, type=float, help="the time limit in seconds")
    ap.add_argument("--runner", required=True, help="a [runners.<name>] table of the config")
    ap.add_argument("--out", help="the run folder")
    args = ap.parse_args(argv)
    try:
        counters = run_step(args.folder, args.prompt, args.timeout, args.runner, args.out, cfg=cfg)
    except UsageError as e:
        print(f"runner.step: {e}", file=sys.stderr)
        return 2
    print(f"run folder: {counters['run_folder']}")
    print(step_line(counters))
    return 0


if __name__ == "__main__":
    sys.exit(main())
