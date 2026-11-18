"""The runners come from config, local file first, else the example."""

from __future__ import annotations

import shutil
import tomllib

import pytest

from conftest import ROOT
from runner import UsageError, config

EXAMPLE = ROOT / "config" / "workbench.example.toml"
PERSONAS = ROOT / "config" / "personas.example.toml"


def _example() -> dict:
    with EXAMPLE.open("rb") as f:
        return tomllib.load(f)


def test_example_has_1_runner_for_each_adapter():
    runners = _example()["runners"]
    adapters = {t["adapter"] for t in runners.values()}
    assert adapters == set(config.ADAPTER_NAMES)
    for name, table in runners.items():
        assert config.runner_config({"runners": runners}, name)["model"]


def test_example_models_are_full_ids_not_aliases():
    for table in _example()["runners"].values():
        if table["adapter"] == "claude":
            assert table["model"].count("-") >= 2, table["model"]
            assert table["model"] not in ("opus", "sonnet", "haiku")


def test_each_role_has_a_configured_runner():
    personas = config.load_personas(PERSONAS)
    runners = _example()["runners"]
    assert personas["roles"]
    for role in personas["roles"]:
        assert config.role_runner(personas, role) in runners


def test_fake_runner_script_exists():
    script = _example()["runners"]["fake"]["script"]
    assert (ROOT / script).is_file()


def _root(tmp_path, local: str | None):
    (tmp_path / "config").mkdir()
    shutil.copy(EXAMPLE, tmp_path / "config" / "workbench.example.toml")
    shutil.copy(PERSONAS, tmp_path / "config" / "personas.example.toml")
    if local is not None:
        (tmp_path / "config" / "workbench.toml").write_text(local, encoding="utf-8")
        (tmp_path / "config" / "personas.toml").write_text(
            '[roles.builder]\npersona = "p"\nrunner = "mine"\n', encoding="utf-8")
    return tmp_path


def test_loader_reads_the_example_when_no_local_file(tmp_path):
    root = _root(tmp_path, None)
    assert "claude" in config.load_workbench(root)["runners"]
    assert config.role_runner(config.load_personas(root), "builder") == "claude"


def test_loader_prefers_the_local_file(tmp_path):
    root = _root(tmp_path, '[runners.mine]\nadapter = "fake"\nmodel = "m"\n')
    cfg = config.load_workbench(root)
    assert set(cfg["runners"]) == {"mine"}
    assert config.role_runner(config.load_personas(root), "builder") == "mine"


def test_loader_takes_a_file_path(tmp_path):
    f = tmp_path / "w.toml"
    f.write_text('[runners.x]\nadapter = "codex"\nmodel = "m"\n', encoding="utf-8")
    assert config.runner_config(config.load_workbench(f), "x")["adapter"] == "codex"


def test_no_config_is_a_usage_error(tmp_path):
    with pytest.raises(UsageError):
        config.load_workbench(tmp_path)


def test_bad_toml_is_a_usage_error(tmp_path):
    f = tmp_path / "w.toml"
    f.write_text("[runners.x\n", encoding="utf-8")
    with pytest.raises(UsageError):
        config.load_workbench(f)


def test_unknown_runner_is_a_usage_error():
    with pytest.raises(UsageError, match="unknown runner: nope"):
        config.runner_config(_example(), "nope")
    with pytest.raises(UsageError, match=r"no \[runners.<name>\] table"):
        config.runner_config({"owner": {}}, "claude")


def test_unknown_adapter_is_a_usage_error():
    cfg = {"runners": {"x": {"adapter": "other-vendor", "model": "m"}}}
    with pytest.raises(UsageError, match="unknown adapter: other-vendor"):
        config.runner_config(cfg, "x")


@pytest.mark.parametrize("missing", ["adapter", "model"])
def test_missing_required_key_is_a_usage_error(missing):
    table = {"adapter": "fake", "model": "m"}
    del table[missing]
    with pytest.raises(UsageError, match=f"required key {missing}"):
        config.runner_config({"runners": {"x": table}}, "x")


def test_a_runner_that_is_not_a_table_is_a_usage_error():
    with pytest.raises(UsageError, match="not a table"):
        config.runner_config({"runners": {"x": "claude"}}, "x")


def test_role_without_runner_is_a_usage_error():
    with pytest.raises(UsageError):
        config.role_runner({"roles": {"builder": {"persona": "p"}}}, "builder")
    with pytest.raises(UsageError):
        config.role_runner({}, "builder")


def test_each_example_runner_model_has_a_price():
    """The models of the example runners are in cost/prices.json."""
    from runner import pricing

    prices = pricing.load()
    cost = pricing.cost_module()
    for name, table in _example()["runners"].items():
        if table["adapter"] == "fake":
            continue
        model = "local" if table["adapter"] == "opencode" else table["model"]  # each opencode call is priced as local
        assert cost.model_price(prices, model) is not None, name


# --- the guard key of each runner table ---

@pytest.mark.parametrize("adapter,on", [("claude", True), ("codex", True), ("opencode", True), ("fake", False)])
def test_the_guard_is_on_by_default_except_for_fake(adapter, on):
    assert config.guard({"adapter": adapter, "model": "m"}) is on
    assert config.guard({"adapter": adapter, "model": "m", "guard": not on}) is (not on)


@pytest.mark.parametrize("value", ["true", 1, 0, None, ["yes"]])
def test_the_guard_must_be_a_bool(value):
    with pytest.raises(UsageError, match="guard must be true or false"):
        config.runner_config({"runners": {"x": {"adapter": "fake", "model": "m", "guard": value}}}, "x")


def test_each_adapter_takes_the_guard_key(tmp_path):
    from runner import adapters

    script = tmp_path / "reply.json"
    script.write_text("{}", encoding="utf-8")
    for name, table in _example()["runners"].items():
        table = {**table, "guard": True, **({"script": str(script)} if table["adapter"] == "fake" else {})}
        adapters.get(table["adapter"]).validate(name, table)  # no unknown-key error
