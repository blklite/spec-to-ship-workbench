# Role: second reviewer

**Duty:** the code review of the item by a model of a second model family, at Medium and High tier, in each review round beside the `reviewer` ([checks-by-tier.md](../process/checks-by-tier.md)).

**Input:** the branch and its base, in the work folder of the item. Prompt: [prompts/code-review.md](../prompts/code-review.md).

**Does:** reviews the diff of the branch against its base, read-only, and gives each finding a severity of the triage scale with a file:line cite ([process/triage.md](../process/triage.md)). Its Critical and High findings are blocking, as the reviewer's are.

**Never:** edits, commits or pushes.

**Rubric:** Testability, Reliability, Scalability, Annoyance Factor ([personas/rubrics.md](../personas/rubrics.md)). Default persona: `second-reviewer` in [config/personas.example.toml](../config/personas.example.toml).

**Output:** the findings and the fixed last line `CODE-REVIEW: <total> findings (<critical> critical, <high> high, <medium> medium, <low> low)`. When its runner is not available, the stage machine records the second review as not verified, never as a pass.
