"""A fake wrap command for the wrap tests; a shim named wrap runs it.

    wrap [<FAKE_WRAP_SKIP wrap arguments>] <command...>

It writes its arguments to FAKE_WRAP_LOG as JSON, then runs the command after
the first FAKE_WRAP_SKIP (default 0) arguments, with the same stdin, stdout and
stderr, and exits with its exit code.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


def main(argv: list[str]) -> int:
    with open(os.environ["FAKE_WRAP_LOG"], "w", encoding="utf-8") as f:
        json.dump({"argv": argv, "cwd": os.getcwd()}, f)
    return subprocess.run(argv[int(os.environ.get("FAKE_WRAP_SKIP") or 0):]).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
