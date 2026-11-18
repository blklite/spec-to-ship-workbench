"""The fixed last line: what a prompt allows, and whether a reply ends with it.

A prompt states its allowed last lines in the first fenced block after the
sentence "The last line of your reply is exactly" (the form that
tests/test_prompts.py checks). In each allowed line, a placeholder `<...>`
stands for 1 or more characters; all other text is literal. A reply is valid
only when its last non-empty line matches 1 allowed line in full. When that line
closes a fenced block, the last non-empty line inside the block counts.
"""

from __future__ import annotations

import re
from pathlib import Path

from runner import UsageError

MARKER = "The last line of your reply is exactly"
# A fence at the start of a line, with any info string; the block may be empty.
FENCE = re.compile(r"^```[^`\n]*\n(.*?)^```", re.S | re.M)
PLACEHOLDER = re.compile(r"<[^<>]+>")
# A line of a reply that opens a fence: 3 backticks and any info string.
OPENER = re.compile(r"```[^`]*")


def _clean(line: str) -> str:
    return line.rstrip("\r").rstrip(" \t")


def allowed_lines(text: str) -> list[str]:
    """The allowed last lines of a prompt text. No block is a usage error."""
    text = text.replace("\r\n", "\n")
    at = text.find(MARKER)
    if at < 0:
        raise UsageError(f'the prompt has no "{MARKER}" sentence')
    m = FENCE.search(text, at)
    if not m:
        raise UsageError("the prompt has no fenced block after the last-line sentence")
    lines = [_clean(x) for x in m.group(1).split("\n")]
    lines = [x for x in lines if x]
    if not lines:
        raise UsageError("the last-line block of the prompt is empty")
    return lines


def read_allowed(prompt_file: str | Path) -> list[str]:
    try:
        text = Path(prompt_file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise UsageError(f"cannot read the prompt file {prompt_file}: {e}") from e
    return allowed_lines(text)


def compile_line(line: str) -> re.Pattern:
    """Each `<...>` becomes `.+`; the rest is literal."""
    parts, pos = [], 0
    for m in PLACEHOLDER.finditer(line):
        parts.append(re.escape(line[pos:m.start()]))
        parts.append(".+")
        pos = m.end()
    parts.append(re.escape(line[pos:]))
    return re.compile("".join(parts))


def last_line(final_text: str | None) -> str | None:
    """The last non-empty line of a reply, without trailing white space. When it
    is a bare fence that closes a block (fences paired from the top), the last
    non-empty line inside the block; an empty block keeps the fence."""
    lines = [_clean(x) for x in (final_text or "").replace("\r\n", "\n").split("\n")]
    lines = [x for x in lines if x.strip()]
    opener = None
    for i, line in enumerate(lines):
        if opener is None:
            if OPENER.fullmatch(line):
                opener = i
        elif line == "```":
            if i == len(lines) - 1 and i - opener > 1:
                return lines[i - 1]
            opener = None
    return lines[-1] if lines else None


def matches(line: str | None, allowed: list[str]) -> bool:
    if line is None:
        return False
    return any(compile_line(a).fullmatch(line) for a in allowed)
