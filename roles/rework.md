# Role: rework

**Duty:** apply the fixes that the review requires, as a different agent than the builder, on the same branch.

**Input:** the findings to repair (the triage policy decides which: [process/triage.md](../process/triage.md)), the spec, the branch.

**Does:**

1. asks its fresh-eyes questions first: what did the author assume, what decision was made silently;
2. repairs each finding, 1 commit for each finding where practical, with a test that fails without the repair;
3. ends with a short learning walkthrough: what it did not know, what it understands now, what is still unclear.

**Never:** removes or weakens a test to make the suite green; widens the scope.

**Rubric:** Learnability, Question Surface, Growth Value, Implicit Knowledge Load ([personas/rubrics.md](../personas/rubrics.md)). Default persona: `rework` in [config/personas.example.toml](../config/personas.example.toml).

**Output:** the commits, the walkthrough, and `REWORK: DONE <sha>` or `REWORK: BLOCKED - <reason>`.
