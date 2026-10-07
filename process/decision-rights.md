# Decision rights

**The test:** an agent decides alone when the choice is reversible, is inside the spec, and does not change what a user sees. If 1 of the 3 conditions is not true, the agent asks the owner (`config:owner.name`). The 2 lists below apply the test to the known cases.

## An agent decides alone, and records the decision in the report

- Names, file layout, test structure, reuse of helpers.
- A value that the spec bounds by a floor or a rule, for example a colour contrast above 4.5:1.
- A defect of wording in the spec that does not change behaviour.
- The repair of a Critical or High finding in the code of the item, inside the budget.
- **A pinned value in a test.** The update of a pinned value in a test, which includes the addition of names to a list in a test, when 3 conditions are true: the spec names the pages or files, the change adds only those names or values, and the review confirms that nothing else became weaker. The update goes in a separate commit.

## An agent must ask the owner

- A change of what a user sees or does that the spec does not state.
- A change of scope: more pages, files or services than the spec names.
- **A test removal.** The removal of a test, or a change that makes the logic of a test weaker. The owner approves a removal at intake: the spec lists the tests that the item may retire. A test that carries a marker with the event that ends it is retired by an agent alone at that event; this applies to tests written after the marker rule exists. An agent never removes a different test alone.
- Each High tier action. The list is `config:tiers.high.actions`; the example config names credentials, auth, data migrations, a delete of live data, production access and the network edge.
- **A merge, and a write to live data.** For a Low tier item with all checks green, 1 approval from the owner covers the merge and the live write. After `config:delivery.low_tier_auto_after` Low items with no defect found after delivery, both become automatic, with an alert.
- More budget.

## The default for a doubt that is not on a list

If the choice is reversible, choose the option that changes least and record it under "Decided for you" in the report. If the choice is not reversible, ask.
