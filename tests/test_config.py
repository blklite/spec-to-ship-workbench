"""AC 4: every `config:<key>` that a process file names exists in the example
config, and the example holds the values the process needs."""

from __future__ import annotations

import re
import tomllib

import pytest

from conftest import ROOT

EXAMPLE = ROOT / "config" / "workbench.example.toml"
REF = re.compile(r"`config:([A-Za-z0-9_.]+)`")


def load():
    with EXAMPLE.open("rb") as f:
        return tomllib.load(f)


def resolve(cfg: dict, key: str):
    node = cfg
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(key)
        node = node[part]
    return node


def referenced_keys() -> set[str]:
    keys = set()
    for path in sorted((ROOT / "process").glob("*.md")):
        keys |= set(REF.findall(path.read_text(encoding="utf-8")))
    return keys


def test_process_files_refer_to_config():
    assert len(referenced_keys()) >= 10


@pytest.mark.parametrize("key", sorted(referenced_keys()))
def test_each_referenced_key_exists(key):
    resolve(load(), key)


def test_resolve_rejects_a_missing_key():
    with pytest.raises(KeyError):
        resolve(load(), "budget.medium.no_such_key")
    with pytest.raises(KeyError):
        resolve(load(), "budget.medium.usd.deeper")


def test_example_values():
    cfg = load()
    assert cfg["owner"]["name"]
    assert cfg["owner"]["waiting_state"] == "Waiting for Owner"
    for size, usd, hours in (("small", 40, 2), ("medium", 100, 4)):
        b = cfg["budget"][size]
        assert (b["usd"], b["hours"], b["rework_rounds"]) == (usd, hours, 2)
    assert (cfg["budget"]["alert_percent"], cfg["budget"]["stop_percent"]) == (80, 100)
    assert cfg["tiers"]["high"]["actions"]
    assert (cfg["specs"]["home"], cfg["specs"]["max_kb"]) == ("specs/", 8)


# A sentence that states the example config's values on purpose: it starts a
# line or follows a full stop, starts with "the example config", and ends on
# the same line at a full stop followed by white space.
EXAMPLE_SENTENCE = re.compile(r"(?im)(?:^|(?<=\.\s))[ \t]*the example config\b[^\n]*?\.(?=\s|$)")
# A budget value typed in place of a `config:` key: dollars (other than $0, the
# token price of a local model), hours, rounds, and alert or stop percentages,
# in digits or as a spelled-out number ("four hours", "two rounds").
SPELLED = ("one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
           "|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred")
TYPED_VALUE = re.compile(
    r"\$ ?[0-9][0-9.,]*"
    r"|\b(?:[0-9]+(?:\.[0-9]+)?|(?:" + SPELLED + r")\b)"
    r"[ -]*(?:hours?|hrs?|h\b|(?:[a-z]+[ -])?rounds?\b|percent\b|%)",
    re.I,
)
ZERO_DOLLARS = re.compile(r"\$ ?0[.,]?")


def typed_values(text: str) -> list[str]:
    found = TYPED_VALUE.findall(EXAMPLE_SENTENCE.sub(" ", text))
    return [v for v in found if not ZERO_DOLLARS.fullmatch(v)]


def test_typed_value_finder():
    for bad in ("costs $100 at most", "stop after 4 hours", "for 2 rounds",
                "at most 2 rework rounds", "at most 2 review rounds",
                "an alert at 80 percent", "stop at 100% of a limit",
                "a limit of 3h", "a 4-hour limit", "costs $0.50 a call",
                "## The example config\n\nStop after 4 hours. Next.",
                "- The example config: see below\n- stop after 4 hours.",
                "The example config: see below\nstop after 4 hours.",
                "Next to the example config, stop after 4 hours.",
                "The example config sets 4 hours. Stop after 4 hours.",
                "The example config is short. Stop at 100% of a limit here.",
                "stop after four hours", "at most two rounds",
                "at most two rework rounds", "an alert at eighty percent",
                "a twelve-hour limit"):
        assert typed_values(bad), bad
    for ok in ("An alert at `config:budget.alert_percent` percent.",
               "A local model costs $0 for each token.",
               "The example config sets Small to $40, 2 hours and 2 rounds. Next.",
               "Intro. The example config sets 4 hours. Next.",
               "The example config sets four hours and two rounds. Next.",
               "Someone reviews it. Then the second round starts."):
        assert not typed_values(ok), ok


@pytest.mark.parametrize("name", sorted(p.name for p in (ROOT / "process").glob("*.md")))
def test_process_files_name_no_budget_value(name):
    """The process refers to dollars, hours, rounds and the alert and stop
    shares by key, not by value."""
    found = typed_values((ROOT / "process" / name).read_text(encoding="utf-8"))
    assert not found, f"a value in place of a config: key in {name}: {found}"
