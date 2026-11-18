"""The fake CLI: replays a reply script, for tests.

    python fake_replay.py <script.json>

The script is a JSON object:

    final_text   the text of the reply (a string, or null for no reply)
    counters     input, output, cache_read, cache_write tokens (each optional)
    model        the model the counters are priced by (default: the config model)
    delay        seconds to sleep before the reply (default 0)
    exit_code    the exit code (default 0)
    stdout       raw text to print in place of the JSON reply (optional)
    stderr       text to print on stderr (optional)

It reads stdin (the prompt) and prints 1 JSON object on stdout:
{"final_text": ..., "counters": ..., "model": ...}.
"""

from __future__ import annotations

import json
import sys
import time


def main(argv: list[str]) -> int:
    with open(argv[1], encoding="utf-8") as f:
        script = json.load(f)
    sys.stdin.buffer.read()
    time.sleep(float(script.get("delay") or 0))
    if script.get("stderr"):
        sys.stderr.write(script["stderr"])
    if "stdout" in script:
        sys.stdout.write(script["stdout"])
    else:
        json.dump({"final_text": script.get("final_text"),
                   "counters": script.get("counters") or {},
                   "model": script.get("model")}, sys.stdout)
    sys.stdout.flush()
    return int(script.get("exit_code") or 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
