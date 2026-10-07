# Tracker setup: Azure DevOps checklist

The workbench keeps 1 work item for each item: the spec link, the follow-up list, the full report and the 2 cost figures live there. This checklist sets up an Azure DevOps project for it. Another tracker works the same way if it has the same states and fields.

## 1. Process and work item types

- [ ] Create an inherited process from Agile (Organization settings → Process → Agile → Create inherited process), and move the project to it.
- [ ] Use **User Story** for a workbench item and **Bug** for a defect found after delivery.
- [ ] Optional: an area path for the workbench items, so that queries can find them.

## 2. States

Add these states to User Story and to Bug, in this order, and map them to the state categories shown:

| State | Category | Meaning |
|---|---|---|
| Draft | Proposed | The intake is writing the spec |
| In Review | In Progress | Stage 2: the spec check |
| Approved | In Progress | The spec is checked; the build may start |
| Executing | In Progress | Stages 3 to 5: build, checks and fix loop |
| Waiting for Owner | In Progress | The workbench stopped and asked (see "When the workbench stops and asks" in [process/stages.md](../process/stages.md)) |

Keep the default Closed (Completed) and Removed (Removed) states. The name of the waiting state is `owner.waiting_state` in [config/workbench.example.toml](../config/workbench.example.toml); if you rename it in the tracker, rename it there too.

## 3. Fields

Add 2 decimal fields to User Story and to Bug, and show both on the form:

| Field | Reference name (example) | Holds |
|---|---|---|
| Cost USD | `Custom.CostUSD` | The subagent share of the item's cost, at list price |
| Main Session Cost USD | `Custom.MainSessionCostUSD` | The main-session share of the item's cost, at list price |

The reference names are `tracker.subagent_cost_field` and `tracker.main_session_cost_field` in the example config. Measure both with `cost/cost.py session` ([cost/README.md](../cost/README.md)).

## 4. Conventions

- [ ] The description of each work item links to its spec under `specs/`.
- [ ] The follow-up list of an item is a section of its work item, linked from the pull request ([process/triage.md](../process/triage.md)).
- [ ] The full report of an item is a comment on its work item; messages to the owner stay at 1 screen and link to it ([process/report.md](../process/report.md)).
- [ ] A personal access token for the scripts has only the work-item read and write scope, and lives in the environment, never in the repo.
