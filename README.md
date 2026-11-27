# Workbench

A spec goes in, checked work comes out.

The workbench is a process for running software work with coding agents: you write a short spec, agents build and check it, and you give as little input as the risk allows. This repo holds the rules, the roles, the example personas, the prompts and a cost script. It does not depend on 1 model vendor: a role names a capability, and the configuration maps it to a runner.

**Status: v0.2.** v0.1a gave the process, the roles and personas, the prompts and `cost/cost.py`. v0.2 adds the parts that run them. The runner core is here: `python -m runner.step` runs 1 step on 3 adapters, Claude Code, Codex and opencode, or on a fake runner, with the same result form for each ([runner/README.md](runner/README.md)); `python -m runner.reference` runs your own reference items on a new runner ([reference/README.md](reference/README.md)). The stage machine is here: `python -m wb` runs stages 2 to 5 of an item through the runner core, with a local-file tracker and the stops of the process ([wb/README.md](wb/README.md)). The host part of the stage machine is here: the detached run, the push and the secret guard ([wb/README.md](wb/README.md)). `wb pages` writes read-only HTML pages of the state, `wb record` keeps the records of an item (its pull request, its checks and its merge), `wb deliver` runs the chain after `ready` with no model, and `wb notify` sends the event lines to the owner's phone. The `wb` MCP server is here, and the Claude Code plugin: 4 slash commands over that server ([plugin/README.md](plugin/README.md)). The sandbox pattern and its probe are here ([process/sandbox.md](process/sandbox.md)). A tracker adapter for Azure DevOps comes next. With no host, a session runs the stages by hand from [process/stages.md](process/stages.md).

## The 6 stages

1. **Intake.** The owner and the intake session write a spec of at most 8 KB: the story, the ACs, the non-goals, the tier, the budget and the tests that the item may retire ([docs/specs.md](docs/specs.md)).
2. **Spec check.** An independent reader names the test that proves each AC and what each AC leaves open, and asks the owner 1 batch of questions only if the decision rights do not answer them ([prompts/spec-check.md](prompts/spec-check.md)).
3. **Build.** 1 builder, 1 commit for each AC, in the style of the smallest complete change ([prompts/build.md](prompts/build.md)), then a self-check: a mutation probe on its own tests, acceptance screenshots, and a code review of its own diff ([prompts/code-review.md](prompts/code-review.md)).
4. **Checks** by tier: a separate reviewer with its own mutation probe, an acceptance by running the change, and more for higher tiers ([process/checks-by-tier.md](process/checks-by-tier.md)).
5. **Fix loop.** The triage policy sorts the findings; a different agent than the builder does the rework, within a fixed number of rounds ([process/triage.md](process/triage.md)).
6. **Delivery.** 1 short report and 1 approval; a script performs the approved steps ([process/report.md](process/report.md)).

The workbench stops and asks the owner in 4 cases only: the budget is not sufficient, a decision is outside the decision rights, the action is High tier, or the checks stay red after the allowed rounds.

## Names

The **main session** is the session that writes the specs, answers the spec checks, runs the gates and merges for 1 batch of items. Its nickname is the **Mr Meeseeks session**: it exists for 1 batch only, and a fresh session takes the next batch. A long session reads more context at each turn, so it costs more. The process text and the commands (for example `wb record merge --by main`) keep the name "main session".

## What is where

| Folder | What |
|---|---|
| [process/](process/) | The rules: stages, decision rights, triage, budget and stop rule, checks by tier, the report, the runner contract |
| [process/sandbox.md](process/sandbox.md) | The sandbox pattern: 5 rules for a step on a host |
| [roles/](roles/) | The 8 roles by duty, input, output and rubric |
| [personas/](personas/) | 7 example personas and the rubric matrix ([personas/rubrics.md](personas/rubrics.md)) |
| [prompts/](prompts/) | Plain Markdown prompts for any runner, each with a fixed last line |
| [config/](config/) | Example configuration: the owner, the sizes and limits, the High tier actions, the role-to-persona map |
| [wb/](wb/) | The stage machine: `python -m wb` runs stages 2 to 5 of an item and stops at the 4 cases; the pages, the records, `deliver`, `notify` and the MCP server ([wb/README.md](wb/README.md)) |
| [plugin/](plugin/) | The Claude Code plugin: 4 slash commands (`intake`, `status`, `answer`, `run-next`) over the `wb` MCP server ([plugin/README.md](plugin/README.md)) |
| [.claude-plugin/marketplace.json](.claude-plugin/marketplace.json) | The marketplace file that lists the plugin, for `/plugin marketplace add` |
| [runner/](runner/) | The runner core: `run_step`, the Claude Code, Codex and opencode adapters and a fake one ([runner/README.md](runner/README.md)) |
| [reference/](reference/) | Reference items, to compare runners on the same input: none ship; add your own ([reference/README.md](reference/README.md)) |
| [cost/](cost/) | `cost.py`: the cost of an item from its transcripts ([cost/README.md](cost/README.md)) |
| [scripts/](scripts/) | `secret-scan.sh`: the scan for secrets and private names; `secret-scan.sh --message <file>` scans 1 text, such as a merge message; `wrap-step.sh`: the wrap of a Claude step on a host |
| [scripts/sandbox-probe.sh](scripts/sandbox-probe.sh) | The probe that a wrapper passes before its first step (Linux) |
| [docs/](docs/) | The tracker setup checklist, the spec format, the rule map, the prompt cache |
| [specs/](specs/) | Your specs, 1 file for each item |
| [tests/](tests/) | The pytest suite: `python -m pytest` |

## Getting started

1. Copy `config/workbench.example.toml` to `config/workbench.toml` and set the owner, the sizes and the High tier actions.
2. Set up the tracker with [docs/ado-setup.md](docs/ado-setup.md).
3. Copy `scripts/denylist.example.txt` outside the repo, fill in your private words, and set `WORKBENCH_DENYLIST` to its path before you run `scripts/secret-scan.sh`.
4. Write a spec under `specs/` and start at stage 1.

## Requirements and tests

Python 3.11 or later and pytest; the runner and the cost script use the standard library only. The shell script needs bash and GNU grep: Linux and macOS, and Windows through WSL or Git for Windows.

```sh
python -m pytest
```

The shell tests find bash through `WORKBENCH_BASH`, then Git for Windows on Windows, then `/bin/bash`; where none exists they skip with a reason.

## Licence

MIT. See [LICENSE](LICENSE).
