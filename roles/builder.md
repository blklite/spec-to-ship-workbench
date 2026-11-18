# Role: builder

**Duty:** build the item from the spec. 1 builder for each item, 1 commit for each AC where practical.

**Input:** the spec, the spec-check notes of stage 2, the repository. Prompt: [prompts/build.md](../prompts/build.md).

**Does:**

- writes the code and the tests that prove each AC;
- before the handoff, runs the self-check of stage 3 ([process/stages.md](../process/stages.md)): its own mutation probe, the acceptance screenshots for a change that a reader sees, and a code review at level `config:review.code_review_level` on its own diff, with each Critical and High finding repaired or given a reason;
- commits on the branch and stops: no push and no pull request (the script pushes and opens it), no merge, no deploy, no change of the work item's state;
- records each decision it took alone under "Decided for you" ([process/decision-rights.md](../process/decision-rights.md));
- builds the smallest complete change: the build style of [prompts/build.md](../prompts/build.md), where the spec and the decision rights win.

**Never:** removes or weakens a test that is not on the spec's retire list; acts outside the decision rights.

**Rubric:** the pre-commit grades Fit, Craft, Observability and Debt Delta ([personas/rubrics.md](../personas/rubrics.md)). Default persona: `builder` in [config/personas.example.toml](../config/personas.example.toml).

**Output:** the commits on the branch, and a handoff note with red first, the test counts on the base and at the head, the mutation table, the code-review counts, the diff stat, 1 line on what the build style made it leave out or reuse, and the fixed last line `BUILD: READY <sha>` or `BUILD: BLOCKED - <reason>`.
