"""Each run writes counters.json, stdout.txt and stderr.txt in its
run folder; the dollars come from prices.json through cost.py, or "unpriced".

Fixture prices (tests/fixtures/cost/prices.json), per million tokens:
test-model-a input $1, output $10, cache read $0.10, 5-minute write 1.25x.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from conftest import ROOT
from runner import UsageError, step
from runner.step import run_step

PROMPT = ROOT / "prompts" / "spec-check.md"
PRICES = ROOT / "tests" / "fixtures" / "cost" / "prices.json"
GOOD = "SPEC-CHECK 7: GO"
KEYS = {
    "runner", "adapter", "model", "guard", "prompt", "prompt_sha256", "start", "end", "seconds",
    "cli_seconds", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "usd", "status", "last_line", "reason", "exit_code",
}


def fake_cfg(tmp_path, **reply) -> dict:
    script = tmp_path / "reply.json"
    script.write_text(json.dumps(reply), encoding="utf-8")
    return {"runners": {"fake": {"adapter": "fake", "model": "fake-model", "script": str(script)}}}


@pytest.fixture
def work(tmp_path):
    (tmp_path / "work").mkdir()
    return tmp_path


def run(work, cfg, out="run", **kw):
    return run_step(work / "work", PROMPT, kw.pop("timeout", 30), "fake",
                    work / out if out else None, cfg=cfg, prices_file=PRICES, **kw)


def written(work, out="run") -> dict:
    return json.loads((work / out / "counters.json").read_text(encoding="utf-8"))


def test_counters_file_has_every_field(work):
    c = run(work, fake_cfg(work, final_text=GOOD, model="test-model-a",
                           counters={"input": 5, "output": 6, "cache_read": 7, "cache_write": 8}))
    data = written(work)
    assert set(data) == KEYS
    assert data["runner"] == "fake" and data["adapter"] == "fake" and data["model"] == "fake-model"
    assert data["prompt"] == str(PROMPT)
    assert data["prompt_sha256"] == hashlib.sha256(PROMPT.read_bytes()).hexdigest()
    assert (data["input_tokens"], data["output_tokens"], data["cache_read_tokens"],
            data["cache_write_tokens"]) == (5, 6, 7, 8)
    assert (data["status"], data["last_line"], data["exit_code"]) == ("OK", GOOD, 0)
    assert c["run_folder"] == str(work / "run")


def test_times_are_utc(work):
    run(work, fake_cfg(work, final_text=GOOD, delay=1))
    data = written(work)
    for key in ("start", "end"):
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", data[key]), data[key]
    start = dt.datetime.fromisoformat(data["start"].replace("Z", "+00:00"))
    end = dt.datetime.fromisoformat(data["end"].replace("Z", "+00:00"))
    assert (end - start).total_seconds() >= 1  # the reply took at least its 1 s delay
    assert data["seconds"] >= 1


def test_raw_stdout_and_stderr_beside_the_counters(work):
    run(work, fake_cfg(work, final_text=GOOD, stderr="a warning\n"))
    assert json.loads((work / "run" / "stdout.txt").read_text(encoding="utf-8"))["final_text"] == GOOD
    assert (work / "run" / "stderr.txt").read_text(encoding="utf-8") == "a warning\n"


def test_dollars_from_prices_json(work):
    run(work, fake_cfg(work, final_text=GOOD, model="test-model-a", counters={
        "input": 1_000_000, "output": 100_000, "cache_read": 2_000_000, "cache_write": 1_000_000}))
    # 1.00 input + 1.00 output + 0.20 cache read + 1.25 cache write (5 min)
    assert written(work)["usd"] == pytest.approx(3.45)


def test_dated_model_takes_its_prefix_price(work):
    run(work, fake_cfg(work, final_text=GOOD, model="test-model-a-20260101",
                       counters={"input": 1_000_000}))
    assert written(work)["usd"] == pytest.approx(1.0)


def test_missing_model_is_unpriced_not_zero(work):
    run(work, fake_cfg(work, final_text=GOOD, counters={"input": 1000, "output": 10}))
    assert written(work)["usd"] == "unpriced"


def test_missing_model_with_zero_tokens_is_still_unpriced(work):
    run(work, fake_cfg(work, final_text=GOOD, counters={"input": 0, "output": 0}))
    assert written(work)["usd"] == "unpriced"


def test_priced_model_with_zero_tokens_is_zero(work):
    run(work, fake_cfg(work, final_text=GOOD, model="test-model-a", counters={"input": 0, "output": 0}))
    assert written(work)["usd"] == 0


def test_no_counters_give_no_dollars(work):
    run(work, fake_cfg(work, final_text=GOOD, model="test-model-a"))
    data = written(work)
    assert (data["usd"], data["input_tokens"]) == (None, None)


def test_a_zero_token_side_model_does_not_make_the_step_unpriced():
    from runner import pricing

    prices = pricing.load(PRICES)
    calls = [{"model": "test-model-a", "in": 1_000_000, "cr": 0, "cw5": 0, "cw1": 0, "out": 0},
             {"model": "side-model-not-listed", "in": 0, "cr": 0, "cw5": 0, "cw1": 0, "out": 0}]
    assert pricing.dollars(calls, prices) == pytest.approx(1.0)
    calls[1]["out"] = 1
    assert pricing.dollars(calls, prices) == "unpriced"


def test_unavailable_and_timeout_give_null_counters(work):
    cfg = fake_cfg(work, final_text=GOOD)
    cfg["runners"]["fake"]["command"] = "no-such-cli-for-the-workbench"
    run(work, cfg)
    data = written(work)
    assert (data["status"], data["exit_code"], data["usd"], data["input_tokens"]) == (
        "UNAVAILABLE", None, None, None)
    run(work, fake_cfg(work, final_text=GOOD, delay=30), out="run2", timeout=1)
    data = written(work, "run2")
    assert (data["status"], data["exit_code"], data["usd"], data["input_tokens"]) == (
        "TIMEOUT", None, None, None)


def test_step_writes_nothing_in_the_work_folder(work):
    (work / "work" / "keep.txt").write_text("x", encoding="utf-8")
    before = sorted(p.relative_to(work / "work") for p in (work / "work").rglob("*"))
    run(work, fake_cfg(work, final_text=GOOD))
    after = sorted(p.relative_to(work / "work") for p in (work / "work").rglob("*"))
    assert before == after


def test_default_run_folder_is_new_under_runs(work, monkeypatch):
    monkeypatch.setattr(step, "ROOT", work)
    a = run(work, fake_cfg(work, final_text=GOOD), out=None)
    b = run(work, fake_cfg(work, final_text=GOOD), out=None)
    assert a["run_folder"] != b["run_folder"]
    for c in (a, b):
        folder = Path(c["run_folder"])
        assert folder.parent == work / "runs"
        assert (folder / "counters.json").is_file()


def test_runs_folder_is_ignored_by_git():
    r = subprocess.run(["git", "check-ignore", "-q", "runs/x/counters.json"], cwd=ROOT)
    assert r.returncode == 0


def test_bad_prices_file_is_a_usage_error(work):
    bad = work / "p.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(UsageError, match="prices"):
        run_step(work / "work", PROMPT, 30, "fake", work / "run",
                 cfg=fake_cfg(work, final_text=GOOD), prices_file=bad)
    assert not (work / "run").exists()


def test_shipped_prices_are_the_default(work):
    run_step(work / "work", PROMPT, 30, "fake", work / "run",
             cfg=fake_cfg(work, final_text=GOOD, model="test-model-a", counters={"input": 1}))
    assert written(work)["usd"] == "unpriced"  # the test model is not in cost/prices.json


# --- no price in the runner code ---------------------------------------------

def runner_sources():
    return sorted((ROOT / "runner").rglob("*.py"))


def test_no_price_or_model_name_in_runner_code():
    assert runner_sources()
    for path in runner_sources():
        src = path.read_text(encoding="utf-8")
        name = path.relative_to(ROOT).as_posix()
        assert not re.search(r"claude-[a-z]+-\d|gpt-\d|\bo\d-", src), f"a model name is in {name}"
        assert not re.search(r"1e6|1_000_000|1000000|per million|MTok", src), f"a price unit is in {name}"
        assert "load_prices" not in src or name == "runner/pricing.py", f"{name} reads prices"


def test_runner_prices_through_cost_py():
    src = (ROOT / "runner" / "pricing.py").read_text(encoding="utf-8")
    for fn in ("load_prices", "model_price", "price_calls"):
        assert f".{fn}(" in src
    assert "json.load" not in src


# --- the counters record the guard of the runner ---

def test_the_counters_record_the_guard(work):
    cfg = fake_cfg(work, final_text=GOOD)
    run(work, cfg)
    assert written(work)["guard"] is False  # fake: off by default
    cfg["runners"]["fake"]["guard"] = True
    run(work, cfg, out="run2")
    assert written(work, "run2")["guard"] is True
