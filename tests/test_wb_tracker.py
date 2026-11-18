"""1 tracker interface; 1 contract suite, run on the local tracker."""

from __future__ import annotations

import pytest

from runner import UsageError
from wb import tracker

WAITING = "Waiting for Owner"


def local(tmp_path):
    return tracker.make({"tracker": {"kind": "local"}, "state": {"home": str(tmp_path)},
                         "owner": {"waiting_state": WAITING}})


# Each tracker kind runs the same contract suite.
KINDS = [local]


@pytest.fixture(params=KINDS, ids=lambda f: f.__name__)
def tr(request, tmp_path):
    t = request.param(tmp_path)
    assert isinstance(t, tracker.Tracker)
    return t


def test_create_and_get(tr):
    tr.create("7", "A title", "In Review")
    item = tr.get("7")
    assert (item["id"], item["title"], item["state"]) == ("7", "A title", "In Review")
    assert item["comments"] == [] and item["fields"] == {}


def test_create_twice_is_an_error(tr):
    tr.create("7", "A title", "In Review")
    with pytest.raises(UsageError, match="already"):
        tr.create("7", "Other", "In Review")
    assert tr.get("7")["title"] == "A title"


def test_get_of_an_unknown_item_is_an_error(tr):
    with pytest.raises(UsageError):
        tr.get("8")


@pytest.mark.parametrize("name", ["Draft", "In Review", "Approved", "Executing", WAITING, "Closed", "Removed"])
def test_set_state_takes_the_states_of_the_setup(tr, name):
    tr.create("7", "A title", "Draft")
    tr.set_state("7", name)
    assert tr.get("7")["state"] == name


@pytest.mark.parametrize("name", ["Done", "waiting for owner", ""])
def test_an_unknown_state_is_an_error(tr, name):
    tr.create("7", "A title", "Draft")
    with pytest.raises(UsageError, match="unknown tracker state"):
        tr.set_state("7", name)
    assert tr.get("7")["state"] == "Draft"
    with pytest.raises(UsageError, match="unknown tracker state"):
        tr.create("9", "B", name)


def test_comments_keep_their_order(tr):
    tr.create("7", "A title", "Draft")
    tr.comment("7", "first")
    tr.comment("7", "second")
    texts = [c["text"] for c in tr.get("7")["comments"]]
    assert texts == ["first", "second"]
    assert all(c["time"].endswith("Z") for c in tr.get("7")["comments"])


def test_set_field_replaces_the_value(tr):
    tr.create("7", "A title", "Draft")
    tr.set_field("7", "Custom.CostUSD", 1.5)
    tr.set_field("7", "Custom.CostUSD", 2.25)
    assert tr.get("7")["fields"] == {"Custom.CostUSD": 2.25}


def test_the_local_tracker_keeps_the_item_in_the_item_folder(tmp_path):
    local(tmp_path).create("7", "A title", "Draft")
    assert (tmp_path / "7" / "tracker.json").is_file()


@pytest.mark.parametrize("kind", [None, "ado", "Local", ""])
def test_an_unknown_kind_is_a_usage_error(tmp_path, kind):
    cfg = {"tracker": {"kind": kind}, "state": {"home": str(tmp_path)}, "owner": {"waiting_state": WAITING}}
    with pytest.raises(UsageError, match="unknown tracker kind"):
        tracker.make(cfg)


def test_the_example_config_picks_the_local_tracker():
    from runner import config

    assert config.load_workbench(config.ROOT / "config" / "workbench.example.toml")["tracker"]["kind"] == "local"
