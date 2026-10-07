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
| 3 | **Build.** 1 builder, 1 commit for each AC. Then the self-check before the handoff (below) | `role:builder` | None |
| 4 | **Checks,** by tier: [checks-by-tier.md](checks-by-tier.md) | `role:reviewer`, `role:product-owner`, script | None |
| 5 | **Fix loop.** The triage policy ([triage.md](triage.md)) sorts each finding. A different agent than the builder does the rework | `role:rework`, `role:reviewer` | None |
| 6 | **Delivery.** 1 report ([report.md](report.md)), then 1 approval. The script, not an agent, performs the approved steps | Script | 1 approval |

### Stage 3: the builder's self-check

Before the handoff the `role:builder`:

1. runs a mutation probe on its own tests, in the format of the review: break the code on purpose, confirm that a test fails, and give each surviving mutant a test or a reason in its report;
2. takes the acceptance screenshots, for a change that a reader sees, and checks each AC against them;
3. runs a code review at level `config:review.code_review_level` on its own diff (on Claude Code `/code-review high`; on any other runner the prompt [prompts/code-review.md](../prompts/code-review.md)), and repairs each Critical and High finding or gives a reason in its report. Medium and Low findings go to the report and then to the triage policy.

The build agent commits on a branch, opens the pull request and stops. It does not merge, deploy or change the state of the work item.

### Stage 5: the fix loop

1. If the `role:reviewer` has a finding that the triage policy says to repair now, a `role:rework` agent, not the builder, applies the fixes on the same branch: questions first, then the fixes, then a short walkthrough of what it learned.
2. The `role:reviewer` reviews again. Steps 1 and 2 repeat until the reviewer has no blocking finding, for at most `config:budget.medium.rework_rounds` rounds for a Medium item and `config:budget.small.rework_rounds` for a Small item. When the checks stay red after the allowed rounds, the workbench stops and asks.

### Stage 6: delivery

- **Low tier:** 1 approval covers the merge and the live write. After `config:delivery.low_tier_auto_after` Low items in a row with no defect found after delivery, both become automatic, with an alert.
- **Other tiers:** 1 approval.
- Only then is the change reported as ready to merge; the owner merges and gives each deploy its go, unless the approval above covers it.
- The script performs each approved step with its own credential. Until the script exists, the session that controls the item performs them.
- Each message to the owner is at most 1 screen ([report.md](report.md)).

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
