# Stages

A spec goes in, checked work comes out.

## Goal

The owner (`config:owner.name`) runs a spec-driven session on a requirement. The work is then built with reasonable checks, problems are handled when they occur, and the owner gives minimal input. The workbench does not depend on 1 model vendor.

**The measure:** the owner's touchpoints and minutes for each item. The target for a Low tier item is 2 touchpoints: the intake and the report. The report also records the cost against the budget and the defects found after delivery (see [report.md](report.md)).

## The 6 stages

| # | Stage | Who | The owner's input |
|---|---|---|---|
| 1 | **Intake.** The story, the ACs, the non-goals, the tier, the budget and the tests that the item may retire, as a spec of at most `config:specs.max_kb` KB under `config:specs.home` (format: [docs/specs.md](../docs/specs.md)), and a work item in the tracker | The owner and the intake session; the intake session writes the spec | Yes. This is where the owner's time has the most value |
| 2 | **Spec check.** An independent reader states, for each AC, the test that proves it and what the AC leaves open. A script runs the base test suite against a throwaway change to find pinned values. Prompt: [prompts/spec-check.md](../prompts/spec-check.md) | `role:spec-checker`, script | 1 batch of product questions, only if the decision rights do not answer them |
| 3 | **Build.** 1 builder, 1 commit for each AC, in the build style of the smallest complete change. Then the self-check before the handoff (below). Prompt: [prompts/build.md](../prompts/build.md) | `role:builder` | None |
| 4 | **Checks,** by tier: [checks-by-tier.md](checks-by-tier.md) | `role:reviewer`, `role:second-reviewer`, `role:product-owner`, script | None |
| 5 | **Fix loop.** The triage policy ([triage.md](triage.md)) sorts each finding. A different agent than the builder does the rework | `role:rework`, `role:reviewer` | None |
| 6 | **Delivery.** 1 report ([report.md](report.md)), then 1 approval. The script, not an agent, performs the approved steps | Script | 1 approval |

### Stage 3: the build style

The build style of [prompts/build.md](../prompts/build.md) applies to every build: take the smallest change that fully works, and reuse what the codebase already has. The spec and the decision rights win over the style: it never removes or weakens a test that is not on the spec's retire list, never cuts what the spec states and never changes the scope. The builder's report states in 1 line what the style made it leave out or reuse, or "no change". Only the builder gets the style: the spec checker, the reviewer, the rework agent and the intake do not.

### Stage 3: the builder's self-check

Before the handoff the `role:builder`:

1. runs a mutation probe on its own tests, in the format of the review: break the code on purpose, confirm that a test fails, and give each surviving mutant a test or a reason in its report;
2. takes the acceptance screenshots, for a change that a reader sees, and checks each AC against them;
3. runs a code review at level `config:review.code_review_level` on its own diff (on Claude Code `/code-review high`; on any other runner the prompt [prompts/code-review.md](../prompts/code-review.md)), and repairs each Critical and High finding or gives a reason in its report. Medium and Low findings go to the report and then to the triage policy.

The report of the `role:builder` also states red first (each new test fails on the base before the change), the test counts on the base and at the head, the count of the mutation probe (killed, total and survived), and the diff stat.

The build agent commits on the branch and stops. It does not push or open the pull request, and it does not merge, deploy or change the state of the work item. The script checks that the SHA of the build reply is the head of the work folder, pushes the branch with the token of `config:delivery.push_token_file`, and opens 1 pull request. The rework agent does not commit: after its step the script commits the work folder and pushes again.

### Stage 5: the fix loop

1. If the `role:reviewer` has a finding that the triage policy says to repair now, a `role:rework` agent, not the builder, applies the fixes on the same branch: questions first, then the fixes, then a short walkthrough of what it learned.
2. The `role:reviewer` reviews again. Steps 1 and 2 repeat until the reviewer has no blocking finding, for at most `config:budget.medium.rework_rounds` rounds for a Medium item and `config:budget.small.rework_rounds` for a Small item. When the checks stay red after the allowed rounds, the workbench stops and asks.

### Stage 6: delivery

- **Low tier:** 1 approval covers the merge and the live write. After `config:delivery.low_tier_auto_after` Low items in a row with no defect found after delivery, both become automatic, with an alert.
- **Other tiers:** 1 approval.
- **Before each merge, the delivery check.** Each check of [checks-by-tier.md](checks-by-tier.md) records the full SHA (40 hex characters) of the head commit that it ran on. The session that controls the item records it from the output of a run that it saw, never from a builder's log or a line made by hand. Before each merge, a script compares the SHA of each required check with the head of the branch. A record names the commit that the run read, never a newer head. A required check that ran on an older commit stops the merge: run it again at the head. The script also judges 5 checks by a fixed form of the result (`tests`, `self-check`, `reviewer-review`, `code-review` and `second-review`): a required row with a result outside its form stops the merge. The `self-check` row comes from the report of the builder: `SELF-CHECK: <k> of <n> mutants killed, <s> survived, by <who>`, or `SELF-CHECK: no mutants, by <who>: <reason>`, where `<who>` is `builder`, `rework`, `reviewer` or `main session`. A row by `reviewer` or `main session` prints a `NOT VERIFIED:` line and does not stop the merge. The other checks stay free text, and the script shows them as `not judged`. An error of the script stops the merge too. When each required check is current, the script prints the merge command with the head SHA (on GitHub, `gh pr merge --match-head-commit <sha>`), and the merge uses that command: the forge then refuses the merge when the branch has a newer commit. The report holds the table of the script ([report.md](report.md)).
- **The merge message.** The merge commit has a written subject and body, never the default message of the forge: a default message can hold a private name, and a commit message stays in the history. Before the merge, `scripts/secret-scan.sh --message <file>` scans that text with the local list of private words set, and it must exit 0.
- **Carry forward.** A check stays current when the net change after its SHA (for example merges of the main branch) touches no file that the branch changes against the main branch, on the branch side and on the main side. The script applies this rule. The check SHA must be an ancestor of the head and must not be on the main branch, and the check SHA and the head must each have exactly 1 merge base with the main branch. The branch files are the changes of the branch against the main branch at the check SHA and at the head, together. The script marks such a check as carried forward and prints a line that names it, and the check does not stop the merge. The report holds those lines. The tests, and a host test where the installation has one, never carry forward: they always run at the head.
- Only then is the change reported as ready to merge; the owner merges and gives each deploy its go, unless the approval above covers it.
- The script performs each approved step with its own credential. Until the script exists, the session that controls the item performs them.
- Each message to the owner is at most 1 screen ([report.md](report.md)).

## The stage machine

A script with no model runs stages 2 to 5 of an item: `python -m wb` ([wb/README.md](../wb/README.md)). `wb intake` checks the spec against [docs/specs.md](../docs/specs.md) and starts the item at stage 2. `wb run-next` runs 1 step at a time through the runner core until the item waits or is ready. Each step gets an Inputs block and the prompt of its stage; the script reads only the fixed last line and the counters of the step.

- At Medium and High tier, the review also runs [prompts/code-review.md](../prompts/code-review.md) on `role:second-reviewer`. When its runner is not available, the second review is not verified, never a pass.
- A step that does not end with an allowed last line runs once more; then the item waits.
- Before each step the script checks the budget (`config:budget.stop_percent`), and a step gets at most `config:steps.max_seconds`.
- The item waits only in the 4 cases below. The owner answers with `wb answer`, approves with `wb approve` at a terminal, and after a spec-check question amends the spec and runs `wb intake` again.
- The state of each item is a folder under `config:state.home`; the work folder is under `config:work.home`; the tracker is `config:tracker.kind`.

## When the workbench stops and asks

In 4 cases only:

1. the budget is not sufficient ([budget.md](budget.md));
2. a decision is outside the decision rights ([decision-rights.md](decision-rights.md));
3. the action is a High tier action (`config:tiers.high.actions`);
4. the checks stay red after the allowed rework rounds.

The state of the work item while it waits is `config:owner.waiting_state`.

## After delivery

- The script runs the whole test suite on the main branch after the merge, and again after each live write and its sync. A red main branch sends an alert.
- A test that asserts a state that is true only before delivery carries a marker that names the event that ends it. The delivery step retires the test at that event. Without the marker, the main branch turns red after delivery and nobody is alerted.
