# Rule map

Where each rule of the source process went. The sources are the 2 pages that this repo was extracted from: the workbench process page (blocks B to J; its blocks of rulings history are not rules and are not mapped) and the rubric page (block N, the standing review rule). Each row is 1 list item, table row or paragraph rule of a source block. "Left out" names the reason.

| # | Source | Rule | Where |
|---|---|---|---|
| 1 | process B | Goal: a spec-driven session, reasonable checks, minimal owner input, no single model vendor | process/stages.md, Goal |
| 2 | process B | The measure: owner touchpoints and minutes; Low tier target of 2; cost and defects after delivery | process/stages.md, Goal; process/report.md |
| 3 | process C | Stage 1, intake: the spec of at most 8 KB and the work item | process/stages.md; docs/specs.md; config `specs` |
| 4 | process C | Stage 2, spec check, and the pin-finding script run | process/stages.md; prompts/spec-check.md |
| 5 | process C | Stage 3, build: 1 builder, 1 commit for each AC, self-check (mutation probe, screenshots, code review) | process/stages.md, stage 3 |
| 6 | process C | Stage 4, checks by tier | process/stages.md; process/checks-by-tier.md |
| 7 | process C | Stage 5, fix loop by a different agent than the builder | process/stages.md, stage 5; process/triage.md |
| 8 | process C | Stage 6, delivery: 1 report, approvals, the script performs approved steps, 1-screen messages | process/stages.md, stage 6 |
| 9 | process C | Stop and ask in 4 cases only | process/stages.md, "When the workbench stops and asks" |
| 10 | process D | The test: reversible, inside the spec, no change a user sees | process/decision-rights.md |
| 11 | process D001 | Decide alone: names, layout, test structure, helper reuse | process/decision-rights.md |
| 12 | process D002 | Decide alone: a value bounded by a floor or rule | process/decision-rights.md (example made generic) |
| 13 | process D003 | Decide alone: a wording defect that does not change behaviour | process/decision-rights.md |
| 14 | process D004 | Decide alone: repair of a Critical or High finding inside budget | process/decision-rights.md |
| 15 | process D005 | Decide alone: update of a pinned test value under 3 conditions, separate commit | process/decision-rights.md |
| 16 | process D006 | Ask: a change a user sees that the spec does not state | process/decision-rights.md |
| 17 | process D007 | Ask: a change of scope | process/decision-rights.md |
| 18 | process D008 | Ask: a test removal or weakening; retirement list at intake; marker rule | process/decision-rights.md |
| 19 | process D009 | Ask: each High tier action | process/decision-rights.md; config `tiers.high.actions` (the list made generic) |
| 20 | process D010 | Ask: a merge or live write; Low tier single approval; automatic after 3 clean items | process/decision-rights.md; config `delivery.low_tier_auto_after` |
| 21 | process D011 | Ask: more budget | process/decision-rights.md |
| 22 | process D | Default for a doubt: least change if reversible, else ask | process/decision-rights.md |
| 23 | process E | Critical or High in the item: repair now; stop after the allowed rounds | process/triage.md |
| 24 | process E | A test gap covered by a different check: record, no hold | process/triage.md |
| 25 | process E | Medium: repair only in a round a Critical or High made necessary | process/triage.md |
| 26 | process E | Low or out of scope: the follow-up list | process/triage.md |
| 27 | process E | A production defect: alert, no repair without a go | process/triage.md |
| 28 | process E | The follow-up list: 1 per item, on the tracker work item | process/triage.md |
| 29 | process F | 3 limits set at intake: dollars, time, rework rounds | process/budget.md |
| 30 | process F | Small: 1 change in 1 or 2 files, $40, 2 hours, 2 rounds | process/budget.md; config `budget.small` |
| 31 | process F | Medium: several files or pages, $100, 4 hours, 2 rounds | process/budget.md; config `budget.medium` |
| 32 | process F | The dollar limit covers subagents plus main session; measured with cost.py; 2 tracker fields | process/budget.md; cost/; docs/ado-setup.md (the history of the older subagent-only limit is left out) |
| 33 | process F | Split a larger item; alert at 80%; stop at 100%; local models need time and round limits | process/budget.md; config `budget.alert_percent`, `budget.stop_percent` |
| 34 | process G | The owner owns the tier table | process/checks-by-tier.md (the link to the source tier page is left out: not in this repo) |
| 35 | process G | Check: existing tests and new tests from the stage 2 plan | process/checks-by-tier.md |
| 36 | process G | Check: the builder's mutation probe and acceptance screenshots | process/checks-by-tier.md |
| 37 | process G | Check: the builder's code review at level high | process/checks-by-tier.md |
| 38 | process G | Check: review with a mutation probe | process/checks-by-tier.md; roles/reviewer.md |
| 39 | process G | Check: before and after screenshots and an acceptance verdict, for a visible change | process/checks-by-tier.md; roles/product-owner.md |
| 40 | process G | Check: a reviewer from a second model family | process/checks-by-tier.md |
| 41 | process G | Check: the blind suite as the owner's choice for Medium and High | process/checks-by-tier.md |
| 42 | process G | Deterministic checks are the floor | process/checks-by-tier.md |
| 43 | process H | A plain script with 1 operation, "run a step" | process/runner-contract.md |
| 44 | process H001 | Step input: folder, prompt file, time limit, runner | process/runner-contract.md |
| 45 | process H002 | Step output: fixed last line, files and commits, counters | process/runner-contract.md |
| 46 | process H | 1 small adapter for each runner | process/runner-contract.md (the adapters themselves are v0.1b) |
| 47 | process H003 | Prompts are Markdown files; no skill or MCP tool needed, except with a prompt-file twin | process/runner-contract.md; prompts/ |
| 48 | process H004 | A role names a capability; config maps role to runner | process/runner-contract.md; roles/; config/personas.example.toml |
| 49 | process H005 | The sandbox is the operating system | process/runner-contract.md (the reference to a private tracker item is left out; the sandbox is v0.1b) |
| 50 | process H006 | The script reads only the last line and counters | process/runner-contract.md |
| 51 | process H007 | A second adapter early; 2 or 3 reference items | process/runner-contract.md |
| 52 | process H008 | The main session runs on the same model as the subagents; its cost is recorded | process/runner-contract.md; config `models.main_session` (the dated history of the earlier model is left out) |
| 53 | process I001 | Run the whole suite on main after merge and after each live write; alert on red | process/stages.md, After delivery |
| 54 | process I002 | A pre-delivery test carries a marker with its end event; delivery retires it | process/stages.md, After delivery |
| 55 | process J | The report: what, evidence, Decided for you, follow-ups, cost, not verified; full report on the work item | process/report.md |
| 56 | process J | Messages to the owner: at most 1 screen, about 1,500 characters | process/report.md; config `messages.max_chars` (the eval figures behind the cause are left out: private data) |
| 57 | rubrics N | Every build gets a review by a separate reviewer agent; rework by a separate rework agent; do not ask first | process/checks-by-tier.md, "The review rule for agent builds" |
| 58 | rubrics N | Exception for the runs of the blind review loop experiment | Left out: the experiment is closed; its useful part is the optional blind suite (row 41) |
| 59 | rubrics N001 | The build agent opens the PR and stops: no merge, deploy or state change | process/stages.md, stage 3 |
| 60 | rubrics N002 | The reviewer's format: ranked findings with cites, mutation probe, 4 grades | process/checks-by-tier.md; roles/reviewer.md |
| 61 | rubrics N003 | A rework agent applies the fixes: questions first, walkthrough at the end | process/stages.md, stage 5; roles/rework.md |
| 62 | rubrics N004 | Re-review; repeat until no blocking finding | process/stages.md, stage 5 (bounded by the rework rounds of the budget) |
| 63 | rubrics N005 | Only then report ready to merge; the owner merges and gives each deploy go | process/stages.md, stage 6 |
| 64 | rubrics N006 | An extra environment check before ready-to-merge for some folders | Left out: specific to 1 project; a per-folder extra check is a v0.1b non-goal |
| 65 | rubrics N | Why: never pipe the test runner through tail or head (hides the exit code) | process/checks-by-tier.md, "Test runs" (the dated incident is left out) |

Not mapped, by design: the source's status line and ratification history, the rulings blocks, the relation to an older loop tool, the evidence block and the open-questions block. They are history of 1 installation, not rules of the process.
