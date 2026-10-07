# Checks by tier

The owner owns the tier table: which actions are Low, Medium and High tier. The High tier actions of the example config are `config:tiers.high.actions`.

| Check | Low | Medium | High |
|---|---|---|---|
| Existing tests, and new tests from the test plan of stage 2 | Yes | Yes | Yes |
| The builder's self-check: its own mutation probe and acceptance screenshots | Yes | Yes | Yes |
| The builder's code review at level `config:review.code_review_level` on its own diff, with each Critical and High finding repaired or given a reason | Yes | Yes | Yes |
| The review by a separate `role:reviewer` agent, with a mutation probe | Yes | Yes | Yes |
| Before and after screenshots, and an acceptance verdict by the `role:product-owner` | For a change that a reader sees | For a change that a reader sees | For a change that a reader sees |
| A reviewer from a second model family | Optional | Yes, when a runner exists | Yes, when a runner exists |
| The blind suite of an isolated test author, written from the spec before the code is seen | No | The owner's choice at intake | The owner's choice at intake |

Deterministic checks are the floor: tests, the contrast scan, the screenshot comparison and the mutation probe give the same result for each model.

## The review rule for agent builds

Every build agent's output gets a review by a separate agent in the `role:reviewer` role. Any rework that the review requires is done by a separate agent in the `role:rework` role. Apply this by default to every agent build; do not ask first.

The reviewer reviews the pull request in its format ([roles/reviewer.md](../roles/reviewer.md)): findings ranked Critical, High, Medium and Low with file:line cites, a **mutation probe** against the tests (break the code on purpose and confirm that a test fails), and its 4 grades. The loop of review and rework is in [stages.md](stages.md), stage 5.

**Test runs:** never pipe the test runner through `tail` or `head`: the pipe hides the exit code of the runner. A green suite can also hide a contract edge, which is why the review exists.
