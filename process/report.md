# The report

1 page for each item. It holds:

- what was built;
- the evidence from each check, with counts;
- **"Decided for you":** each decision an agent took alone, with its reason ([decision-rights.md](decision-rights.md));
- the follow-up list ([triage.md](triage.md));
- the cost (subagents and main session), the time and the rounds against the budget ([budget.md](budget.md));
- what is not verified;
- the measure of the workbench: the owner's touchpoints and minutes for the item, and later the defects found after delivery.

The full report goes on the tracker work item of the item.

## Messages to the owner

Each message to the owner is at most 1 screen, about `config:messages.max_chars` characters. It gives the state, the decision that is needed with a recommendation, and the link to the tracker comment or the page that holds the detail. Cause: reading long messages, not making decisions, takes most of the owner's time in an item.
