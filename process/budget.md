# Budget and stop rule

The owner sets 3 limits at intake: dollars, wall-clock time and rework rounds. An item has 1 of 2 sizes:

| Size | What | Dollars, whole item | Time | Rework rounds |
|---|---|---|---|---|
| Small | `config:budget.small.scope` | `config:budget.small.usd` | `config:budget.small.hours` hours | `config:budget.small.rework_rounds` |
| Medium | `config:budget.medium.scope` | `config:budget.medium.usd` | `config:budget.medium.hours` hours | `config:budget.medium.rework_rounds` |

The example config ([config/workbench.example.toml](../config/workbench.example.toml)) sets Small to $40, 2 hours and 2 rounds, and Medium to $100, 4 hours and 2 rounds.

**The dollar limit covers the whole item:** the subagents plus the main session that runs the item, at list price. The main session costs about as much again as its subagents, so a limit on the subagents alone misleads. The main session measures both parts at each stage boundary from the transcripts, with [cost/cost.py](../cost/cost.py) (see [cost/README.md](../cost/README.md)), until the runner script exists. The tracker records the 2 parts in 2 fields: `config:tracker.subagent_cost_field` and `config:tracker.main_session_cost_field` ([docs/ado-setup.md](../docs/ado-setup.md)).

A larger item is split at intake. An alert goes out at `config:budget.alert_percent` percent of a limit. At `config:budget.stop_percent` percent the workbench stops and asks. A local model costs $0 for each token, so the time limit and the round limit must stand alone.
