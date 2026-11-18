# Rule map

Where each rule of the process lives. Each row is 1 rule; "Where" names the file that holds it, with its section or config key.

| # | Topic | Rule | Where |
|---|---|---|---|
| 1 | Goal | Goal: a spec-driven session, reasonable checks, minimal owner input, no single model vendor | process/stages.md, Goal |
| 2 | Goal | The measure: owner touchpoints and minutes; Low tier target of 2; cost and defects after delivery | process/stages.md, Goal; process/report.md |
| 3 | Stages | Stage 1, intake: the spec of at most 8 KB and the work item | process/stages.md; docs/specs.md; config `specs` |
| 4 | Stages | Stage 2, spec check, and the pin-finding script run | process/stages.md; prompts/spec-check.md |
| 5 | Stages | Stage 3, build: 1 builder, 1 commit for each AC, self-check (mutation probe, screenshots, code review) | process/stages.md, stage 3 |
| 6 | Stages | Stage 4, checks by tier | process/stages.md; process/checks-by-tier.md |
| 7 | Stages | Stage 5, fix loop by a different agent than the builder | process/stages.md, stage 5; process/triage.md |
| 8 | Stages | Stage 6, delivery: 1 report, approvals, the script performs approved steps, 1-screen messages | process/stages.md, stage 6 |
| 9 | Stages | Stop and ask in 4 cases only | process/stages.md, "When the workbench stops and asks" |
| 10 | Decision rights | The test: reversible, inside the spec, no change a user sees | process/decision-rights.md |
| 11 | Decision rights | Decide alone: names, layout, test structure, helper reuse | process/decision-rights.md |
| 12 | Decision rights | Decide alone: a value bounded by a floor or rule | process/decision-rights.md |
| 13 | Decision rights | Decide alone: a wording defect that does not change behaviour | process/decision-rights.md |
| 14 | Decision rights | Decide alone: repair of a Critical or High finding inside budget | process/decision-rights.md |
| 15 | Decision rights | Decide alone: update of a pinned test value under 3 conditions, separate commit | process/decision-rights.md |
| 16 | Decision rights | Ask: a change a user sees that the spec does not state | process/decision-rights.md |
| 17 | Decision rights | Ask: a change of scope | process/decision-rights.md |
| 18 | Decision rights | Ask: a test removal or weakening; retirement list at intake; marker rule | process/decision-rights.md |
| 19 | Decision rights | Ask: each High tier action | process/decision-rights.md; config `tiers.high.actions` |
| 20 | Decision rights | Ask: a merge or live write; Low tier single approval; automatic after 3 clean items | process/decision-rights.md; config `delivery.low_tier_auto_after` |
| 21 | Decision rights | Ask: more budget | process/decision-rights.md |
| 22 | Decision rights | Default for a doubt: least change if reversible, else ask | process/decision-rights.md |
| 23 | Triage | Critical or High in the item: repair now; stop after the allowed rounds | process/triage.md |
| 24 | Triage | A test gap covered by a different check: record, no hold | process/triage.md |
| 25 | Triage | Medium: repair only in a round a Critical or High made necessary | process/triage.md |
| 26 | Triage | Low or out of scope: the follow-up list | process/triage.md |
| 27 | Triage | A production defect: alert, no repair without a go | process/triage.md |
| 28 | Triage | The follow-up list: 1 per item, on the tracker work item | process/triage.md |
| 29 | Budget | 3 limits set at intake: dollars, time, rework rounds | process/budget.md |
| 30 | Budget | Small: 1 change in 1 or 2 files, $40, 2 hours, 2 rounds | process/budget.md; config `budget.small` |
| 31 | Budget | Medium: several files or pages, $100, 4 hours, 2 rounds | process/budget.md; config `budget.medium` |
| 32 | Budget | The dollar limit covers subagents plus main session; measured with cost.py; 2 tracker fields | process/budget.md; cost/; docs/ado-setup.md |
| 33 | Budget | Split a larger item; alert at 80%; stop at 100%; local models need time and round limits | process/budget.md; config `budget.alert_percent`, `budget.stop_percent` |
| 34 | Checks by tier | The owner owns the tier table | process/checks-by-tier.md |
| 35 | Checks by tier | Check: existing tests and new tests from the stage 2 plan | process/checks-by-tier.md |
| 36 | Checks by tier | Check: the builder's mutation probe and acceptance screenshots | process/checks-by-tier.md |
| 37 | Checks by tier | Check: the builder's code review at level high | process/checks-by-tier.md |
| 38 | Checks by tier | Check: review with a mutation probe | process/checks-by-tier.md; roles/reviewer.md |
| 39 | Checks by tier | Check: before and after screenshots and an acceptance verdict, for a visible change | process/checks-by-tier.md; roles/product-owner.md |
| 40 | Checks by tier | Check: a reviewer from a second model family | process/checks-by-tier.md |
| 41 | Checks by tier | Check: the blind suite as the owner's choice for Medium and High | process/checks-by-tier.md |
| 42 | Checks by tier | Deterministic checks are the floor | process/checks-by-tier.md |
| 43 | Runner | A plain script with 1 operation, "run a step" | process/runner-contract.md |
| 44 | Runner | Step input: folder, prompt file, time limit, runner | process/runner-contract.md |
| 45 | Runner | Step output: fixed last line, files and commits, counters | process/runner-contract.md |
| 46 | Runner | 1 small adapter for each runner | process/runner-contract.md; runner/adapters/ |
| 47 | Runner | Prompts are Markdown files; no skill or MCP tool needed, except with a prompt-file twin | process/runner-contract.md; prompts/ |
| 48 | Runner | A role names a capability; config maps role to runner | process/runner-contract.md; roles/; config/personas.example.toml |
| 49 | Runner | The sandbox is the operating system | process/sandbox.md; process/runner-contract.md |
| 50 | Runner | The script reads only the last line and counters | process/runner-contract.md |
| 51 | Runner | A second adapter early; 2 or 3 reference items | process/runner-contract.md; reference/README.md |
| 52 | Runner | The main session runs on the same model as the subagents; its cost is recorded | process/runner-contract.md; config `models.main_session` |
| 53 | After delivery | Run the whole suite on main after merge and after each live write; alert on red | process/stages.md, After delivery |
| 54 | After delivery | A pre-delivery test carries a marker with its end event; delivery retires it | process/stages.md, After delivery |
| 55 | Report | The report: what, evidence, Decided for you, follow-ups, cost, not verified; full report on the work item | process/report.md |
| 56 | Report | Messages to the owner: at most 1 screen, about 1,500 characters | process/report.md; config `messages.max_chars` |
| 57 | Review rule | Every build gets a review by a separate reviewer agent; rework by a separate rework agent; do not ask first | process/checks-by-tier.md, "The review rule for agent builds" |
| 58 | Review rule | The build agent opens the PR and stops: no merge, deploy or state change | process/stages.md, stage 3 |
| 59 | Review rule | The reviewer's format: ranked findings with cites, mutation probe, 4 grades | process/checks-by-tier.md; roles/reviewer.md |
| 60 | Review rule | A rework agent applies the fixes: questions first, walkthrough at the end | process/stages.md, stage 5; roles/rework.md |
| 61 | Review rule | Re-review; repeat until no blocking finding | process/stages.md, stage 5 (bounded by the rework rounds of the budget) |
| 62 | Review rule | Only then report ready to merge; the owner merges and gives each deploy go | process/stages.md, stage 6 |
| 63 | Review rule | Why: never pipe the test runner through tail or head (hides the exit code) | process/checks-by-tier.md, "Test runs" |
