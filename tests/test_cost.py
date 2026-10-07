"""AC 7: cost/cost.py prices a synthetic session exactly, from prices.json.

Fixture (tests/fixtures/cost/), prices per million tokens: test-model-a input
$1, output $10, cache read $0.10; cache writes 1.25x (5 min) and 2x (1 hour).

main  msg_01  2 stream records of 1 call: 1M input, 1M 5-min write, 100k out
              = 1.00 + 1.25 + 1.00                              = 3.25
      msg_02  1M 1-hour write, 2M cache read, 100k thinking
              = 2.00 + 0.20 (low); + 1.00 thinking estimate (high)
      msg_03  legacy: 1M cache_creation_input_tokens only (5 min) = 1.25
      msg_04  unknown-model-x, 500k input: unpriced, not in the totals
      msg_05  1k out; a text block of 6,400 chars (2k tokens at 3.2 chars per
              token) and a tool_use input of 3,200 chars (1k tokens)
              = 0.01 (low); 3k estimated out = 0.03 (high)
      msg_06  <synthetic>, all usage 0: skipped (neither priced nor unpriced)
      main low 6.71, high 7.73
sub   agent-a1 (meta: "fixture builder"), dated model id test-model-a-20260101:
      msg_s1  2M input + 50k out = 2.00 + 0.50                  = 2.50
      msg_s2  1k out; text 3,200 chars + tool_use 3,200 chars (2k tokens) plus
              1k thinking = 0.01 (low); 3k estimated out = 0.03 (high)
      sub low 2.51, high 2.53
total low 9.22, high 10.26
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "cost"))
import cost  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "cost"
SESSION = FIX / "session" / "sess-0001"
MAIN = FIX / "session" / "sess-0001.jsonl"


@pytest.fixture(scope="module")
def prices():
    return cost.load_prices(FIX / "prices.json")


def r2(x):
    return round(x, 2)


def test_session_shares_and_total(prices):
    s = cost.session_cost(SESSION, prices)
    assert (r2(s["main"]["low"]), r2(s["main"]["high"])) == (6.71, 7.73)
    assert (r2(s["subagent_low"]), r2(s["subagent_high"])) == (2.51, 2.53)
    assert (r2(s["total_low"]), r2(s["total_high"])) == (9.22, 10.26)
    [sub] = s["subagents"]
    assert (sub["id"], sub["description"], sub["agent_type"]) == ("a1", "fixture builder", "builder")


def test_unknown_model_is_listed_not_priced_at_zero(prices):
    s = cost.session_cost(SESSION, prices)
    assert s["unpriced"] == {"unknown-model-x": {"calls": 1, "tokens": 500_000}}
    assert s["main"]["calls"] == 4  # the unknown call is not among the priced calls


def test_zero_usage_record_is_skipped(prices):
    # real transcripts hold <synthetic> records with all usage 0: no UNPRICED noise
    calls = cost.read_calls(MAIN)
    assert calls["msg_06"]["model"] == "<synthetic>"
    r = cost.price_calls({"m": calls["msg_06"]}, prices)
    assert (r["calls"], r["unpriced"], r["low"], r["high"]) == (0, {}, 0, 0)


def test_duplicate_stream_record_counts_once(prices):
    calls = cost.read_calls(MAIN)
    assert calls["msg_01"]["out"] == 100_000
    assert calls["msg_01"]["in"] == 1_000_000


def test_cache_write_ttls(prices):
    calls = cost.read_calls(MAIN)
    assert (calls["msg_01"]["cw5"], calls["msg_01"]["cw1"]) == (1_000_000, 0)
    assert (calls["msg_02"]["cw5"], calls["msg_02"]["cw1"]) == (0, 1_000_000)
    # legacy record: only the total, priced as a 5-minute write
    assert (calls["msg_03"]["cw5"], calls["msg_03"]["cw1"]) == (1_000_000, 0)
    one = {k: calls[k] for k in ("msg_02",)}
    assert r2(cost.price_calls(one, prices)["low"]) == 2.20  # 2.00 for the 1-hour write


def test_high_estimate_counts_text_tool_use_and_thinking(prices):
    calls = cost.read_calls(MAIN)
    assert (calls["msg_05"]["out"], calls["msg_05"]["chars"]) == (1_000, 9_600)
    one = cost.price_calls({"m": calls["msg_05"]}, prices)
    assert (r2(one["low"]), r2(one["high"])) == (0.01, 0.03)
    sub = cost.read_calls(SESSION / "subagents" / "agent-a1.jsonl")["msg_s2"]
    assert (sub["out"], sub["chars"], sub["think"]) == (1_000, 6_400, 1_000)
    one = cost.price_calls({"m": sub}, prices)
    assert (r2(one["low"]), r2(one["high"])) == (0.01, 0.03)


def test_window_end_is_exclusive(prices):
    # msg_03 sits at exactly 10:10:00, the end of the window: it is left out
    r = cost.transcript_cost(MAIN, prices, ("2026-01-01T10:04:00Z", "2026-01-01T10:10:00Z"))
    assert (r2(r["low"]), r2(r["high"])) == (2.20, 3.20)
    assert r["calls"] == 1


def test_window_cuts_records(prices):
    r = cost.transcript_cost(MAIN, prices, ("2026-01-01T10:04:00Z", "2026-01-01T10:12:00Z"))
    assert (r2(r["low"]), r2(r["high"])) == (3.45, 4.45)
    s = cost.session_cost(SESSION, prices, ("2026-01-01T10:04:00+00:00", "2026-01-01T10:12:00+00:00"))
    assert s["subagents"][0]["low"] == 0
    assert s["unpriced"] == {}


def test_dated_model_takes_prefix_price(prices):
    assert cost.model_price(prices, "test-model-a-20260101") == prices["models"]["test-model-a"]
    assert cost.model_price(prices, "test-model-ab") is None
    assert cost.model_price(prices, None) is None


def test_no_price_in_code():
    src = (ROOT / "cost" / "cost.py").read_text(encoding="utf-8")
    assert not re.search(r"claude-[a-z]+-\d", src), "a model name (and its price) is in the code"
    assert not re.findall(r"\d+\.\d+", src), "a decimal number (a price?) is in the code"


def test_shipped_prices_load():
    p = cost.load_prices(ROOT / "cost" / "prices.json")
    assert p["models"]


def test_bad_prices_file_is_rejected(tmp_path):
    bad = tmp_path / "p.json"
    bad.write_text(json.dumps({"cache_write_multiplier": {"5m": 1.25}, "chars_per_token": 3.2, "models": {}}))
    with pytest.raises(ValueError):
        cost.load_prices(bad)


def test_cli_session_text_and_json():
    run = lambda *a: subprocess.run(  # noqa: E731
        [sys.executable, str(ROOT / "cost" / "cost.py"), *a], capture_output=True, text=True)
    r = run("session", str(SESSION), "--prices", str(FIX / "prices.json"))
    assert r.returncode == 0, r.stderr
    assert "total:         $9.22 to $10.26" in r.stdout
    assert "UNPRICED (not in the totals): unknown-model-x: 1 calls, 500,000 tokens" in r.stdout
    r = run("session", str(SESSION), "--prices", str(FIX / "prices.json"), "--json")
    assert round(json.loads(r.stdout)["total_low"], 2) == 9.22
    r = run("session", str(FIX / "no-such"), "--prices", str(FIX / "prices.json"))
    assert r.returncode == 2


def test_new_version_does_not_borrow_an_older_price(prices):
    assert cost.model_price(prices, "test-model-a-b") is None
    assert cost.model_price(prices, "test-model-a-2026") is None


def test_cli_errors_exit_2_without_traceback(tmp_path):
    run = lambda *a: subprocess.run(  # noqa: E731
        [sys.executable, str(ROOT / "cost" / "cost.py"), *a], capture_output=True, text=True)
    for args in (
        ("transcript", str(tmp_path / "missing.jsonl")),
        ("transcript", str(MAIN), "--window", "2026-10-07", "9am"),
        ("transcript", str(MAIN), "--prices", str(tmp_path / "none.json")),
    ):
        r = run(*args, "--prices", str(FIX / "prices.json")) if "--prices" not in args else run(*args)
        assert r.returncode == 2, (args, r.stderr)
        assert "Traceback" not in r.stderr


def test_meta_that_is_not_an_object_is_ignored(tmp_path, prices):
    import shutil
    d = tmp_path / "s"
    shutil.copytree(FIX / "session", d)
    (d / "sess-0001" / "subagents" / "agent-a1.meta.json").write_text("[]")
    s = cost.session_cost(d / "sess-0001", prices)
    assert s["subagents"][0]["description"] is None
    assert round(s["total_low"], 2) == 9.22
