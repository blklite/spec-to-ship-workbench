# Workbench

A spec goes in, checked work comes out.

The workbench is a process for running software work with coding agents: you write a short spec, agents build and check it, and you give as little input as the risk allows. This repo holds the rules, the roles, the example personas, the prompts and a cost script. It does not depend on 1 model vendor: a role names a capability, and the configuration maps it to a runner.

**Status: v0.1a.** The process, the roles and personas, the prompts and `cost/cost.py` are here. The plugin, the slash commands, the tracker and runner adapters and the sandbox come in v0.1b. Until the runner script exists, a session runs the stages by hand from [process/stages.md](process/stages.md).

## The 6 stages

1. **Intake.** The owner and the intake session write a spec of at most 8 KB: the story, the ACs, the non-goals, the tier, the budget and the tests that the item may retire ([docs/specs.md](docs/specs.md)).
2. **Spec check.** An independent reader names the test that proves each AC and what each AC leaves open, and asks the owner 1 batch of questions only if the decision rights do not answer them ([prompts/spec-check.md](prompts/spec-check.md)).
3. **Build.** 1 builder, 1 commit for each AC, then a self-check: a mutation probe on its own tests, acceptance screenshots, and a code review of its own diff ([prompts/code-review.md](prompts/code-review.md)).
4. **Checks** by tier: a separate reviewer with its own mutation probe, an acceptance by running the change, and more for higher tiers ([process/checks-by-tier.md](process/checks-by-tier.md)).
5. **Fix loop.** The triage policy sorts the findings; a different agent than the builder does the rework, within a fixed number of rounds ([process/triage.md](process/triage.md)).
6. **Delivery.** 1 short report and 1 approval; a script performs the approved steps ([process/report.md](process/report.md)).

The workbench stops and asks the owner in 4 cases only: the budget is not sufficient, a decision is outside the decision rights, the action is High tier, or the checks stay red after the allowed rounds.

## What is where

| Folder | What |
|---|---|
| [process/](process/) | The rules: stages, decision rights, triage, budget and stop rule, checks by tier, the report, the runner contract |
| [roles/](roles/) | The 7 roles by duty, input, output and rubric |
| [personas/](personas/) | 7 example personas and the rubric matrix ([personas/rubrics.md](personas/rubrics.md)) |
| [prompts/](prompts/) | Plain Markdown prompts for any runner, each with a fixed last line |
| [config/](config/) | Example configuration: the owner, the sizes and limits, the High tier actions, the role-to-persona map |
| [cost/](cost/) | `cost.py`: the cost of an item from its transcripts ([cost/README.md](cost/README.md)) |
| [scripts/](scripts/) | `secret-scan.sh`: the scan for secrets and private names |
| [docs/](docs/) | The tracker setup checklist, the spec format, the rule map |
| [specs/](specs/) | Your specs, 1 file for each item |

## Getting started

1. Copy `config/workbench.example.toml` to `config/workbench.toml` and set the owner, the sizes and the High tier actions.
2. Set up the tracker with [docs/ado-setup.md](docs/ado-setup.md).
3. Copy `scripts/denylist.example.txt` outside the repo, fill in your private words, and set `WORKBENCH_DENYLIST` to its path before you run `scripts/secret-scan.sh`.
4. Write a spec under `specs/` and start at stage 1.

## Requirements and tests

Python 3.11 or later and pytest. The shell script needs bash and GNU grep: Linux and macOS, and Windows through WSL or Git for Windows.

```sh
python -m pytest
```

The shell tests find bash through `WORKBENCH_BASH`, then Git for Windows on Windows, then `/bin/bash`; where none exists they skip with a reason.

## Licence

MIT. See [LICENSE](LICENSE).
