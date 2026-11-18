"""The tracker interface, and the local-file tracker.

`config:tracker.kind` picks the tracker; an unknown kind is a usage error. The
local tracker keeps each work item in tracker.json of the item folder, with the
states of docs/ado-setup.md (the waiting state is `config:owner.waiting_state`).
"""

from __future__ import annotations

from pathlib import Path

from runner import UsageError
from runner.step import _utc
from wb import state

# docs/ado-setup.md: the added states, then the 2 default states it keeps
STATES = ("Draft", "In Review", "Approved", "Executing", "Closed", "Removed")


class Tracker:
    """1 work item for each item: its state, its comments and its fields."""

    def create(self, item_id: str, title: str, item_state: str) -> None:
        raise NotImplementedError

    def get(self, item_id: str) -> dict:
        """{"id", "title", "state", "comments": [{"time", "text"}], "fields": {name: value}}"""
        raise NotImplementedError

    def set_state(self, item_id: str, item_state: str) -> None:
        raise NotImplementedError

    def comment(self, item_id: str, text: str) -> None:
        raise NotImplementedError

    def set_field(self, item_id: str, name: str, value) -> None:
        raise NotImplementedError


class LocalTracker(Tracker):
    def __init__(self, home: Path, waiting_state: str):
        self.home = Path(home)
        self.states = STATES + (waiting_state,)

    def _path(self, item_id: str) -> Path:
        return self.home / state.check_id(item_id) / "tracker.json"

    def _state(self, item_state: str) -> str:
        if item_state not in self.states:
            raise UsageError(f"unknown tracker state {item_state!r} (known: {', '.join(self.states)})")
        return item_state

    def create(self, item_id, title, item_state):
        if self._path(item_id).exists():
            raise UsageError(f"the tracker already has item {item_id}")
        state.write_json(self._path(item_id), {"id": item_id, "title": title, "state": self._state(item_state),
                                               "comments": [], "fields": {}})

    def get(self, item_id):
        return state.read_json(self._path(item_id), f"tracker item {item_id}")

    def _change(self, item_id, fn) -> None:
        item = self.get(item_id)
        fn(item)
        state.write_json(self._path(item_id), item)

    def set_state(self, item_id, item_state):
        self._state(item_state)
        self._change(item_id, lambda item: item.update(state=item_state))

    def comment(self, item_id, text):
        self._change(item_id, lambda item: item["comments"].append({"time": _utc(), "text": text}))

    def set_field(self, item_id, name, value):
        self._change(item_id, lambda item: item["fields"].update({name: value}))


def make(cfg: dict) -> Tracker:
    kind = (cfg.get("tracker") or {}).get("kind")
    if kind != "local":
        raise UsageError(f"unknown tracker kind {kind!r} (known: local)")
    return LocalTracker(state.home(cfg), cfg["owner"]["waiting_state"])
