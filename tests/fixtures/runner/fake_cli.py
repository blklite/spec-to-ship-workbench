"""A fake vendor CLI for the adapter tests; a shim named claude, codex or opencode runs it.

Env:
    FAKE_CLI_LOG     write {"argv", "cwd", "stdin", "env"} here as JSON; env holds
                     the OPENCODE_* variables only
    FAKE_CLI_OUTPUT  print the content of this file on stdout, first
    FAKE_CLI_MORE    then, after 0.5 s, print this text on stdout (a second write)
    FAKE_CLI_EXIT    the exit code (default 0)
    FAKE_CLI_CHILD   start a child that sleeps, write its pid to this file,
                     and sleep (for the time limit test)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def main() -> int:
    stdin = sys.stdin.buffer.read().decode("utf-8")
    log = os.environ.get("FAKE_CLI_LOG")
    if log:
        with open(log, "w", encoding="utf-8") as f:
            env = {k: v for k, v in os.environ.items() if k.upper().startswith("OPENCODE_")}
            json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(), "stdin": stdin, "env": env}, f)
    out = os.environ.get("FAKE_CLI_OUTPUT")
    if out:
        with open(out, encoding="utf-8") as f:
            sys.stdout.reconfigure(newline="\n")  # Windows: the bytes of the file, as the real CLI
            sys.stdout.write(f.read())
        sys.stdout.flush()
    more = os.environ.get("FAKE_CLI_MORE")
    if more:
        time.sleep(0.5)
        sys.stdout.write(more)
        sys.stdout.flush()
    child_file = os.environ.get("FAKE_CLI_CHILD")
    if child_file:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        with open(child_file, "w", encoding="utf-8") as f:
            f.write(str(child.pid))
        time.sleep(120)
    sys.stderr.write("fake cli stderr\n")
    return int(os.environ.get("FAKE_CLI_EXIT") or 0)


if __name__ == "__main__":
    sys.exit(main())
