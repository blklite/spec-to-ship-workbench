"""The dollars of a step, through the price code of cost/cost.py.

No price is in the runner: every price comes from cost/prices.json. cost.py is
loaded by its file path under its own module name, because the tests of cost.py
import it as the top-level module `cost`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from runner import ROOT, UsageError

COST_PY = ROOT / "cost" / "cost.py"
PRICES = ROOT / "cost" / "prices.json"
TOKEN_KEYS = ("in", "cr", "cw5", "cw1", "out")
_cost = None


def cost_module():
    global _cost
    if _cost is None:
        spec = importlib.util.spec_from_file_location("workbench_cost", COST_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _cost = module
    return _cost


def load(prices_file: str | Path | None = None) -> dict:
    """prices.json, checked by cost.py; a bad file is a usage error."""
    try:
        return cost_module().load_prices(prices_file or PRICES)
    except (OSError, ValueError) as e:
        raise UsageError(f"cannot read the prices: {e}") from e


def dollars(calls: list[dict], prices: dict):
    """The list-price dollars of the calls, or "unpriced" when a model of a call
    is not in prices.json, or None when the runner gave no usage at all.

    A call is {"model", "in", "cr", "cw5", "cw1", "out"} in tokens."""
    if not calls:
        return None
    cost = cost_module()
    # a model with no tokens (a side model the CLI lists at 0) costs nothing at
    # any price; when every call has 0 tokens, each model still needs a price
    used = [c for c in calls if any(c.get(k) for k in TOKEN_KEYS)] or calls
    if any(cost.model_price(prices, c["model"]) is None for c in used):
        return "unpriced"  # never 0: a missing price must show
    full = {i: {"chars": 0, "think": 0, **c} for i, c in enumerate(calls)}
    return float(cost.price_calls(full, prices)["low"])  # float: calls with 0 tokens give 0.0, not 0
