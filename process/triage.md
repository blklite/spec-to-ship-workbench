# Triage policy for findings

Each finding of a check (the builder's code review, the review, the acceptance) gets 1 action:

| Finding | Action |
|---|---|
| Critical or High, in the code of the item | Repair now, inside the budget. After the allowed rework rounds (`config:budget.small.rework_rounds`, `config:budget.medium.rework_rounds`) with the checks still red: stop and ask |
| A test gap that a different check of the item already covers | Record. No hold |
| Medium, in the code of the item | Repair in a rework round before the merge, when a round is left. Medium findings alone start a single round. When no round is left: the follow-up list |
| Low, in the code of the item | The Lows list of its area (below) |
| Outside the scope of the item | The follow-up list |
| A defect in production that the work shows, for example a red main branch | Alert immediately. No repair without a go from the owner |

Severity follows the reviewer's framework ([roles/reviewer.md](../roles/reviewer.md)): Critical is silent data corruption, a security exposure or untested safety-critical logic; High is a reliability failure that will hit production or a broken AC; Medium is a real defect on a rare path or a maintenance problem; Low is a smell or a style issue.

**A different severity.** When the session that controls the item gives a finding a different severity than the check that found it, the record of the finding keeps both severities and the reason, for example `High (review), Low (session): <reason>`. The follow-up list and the report show that record. The higher of the 2 severities sets the action of the table. To act on the lower severity, the session asks the owner.

## The follow-up list

Each item has 1 follow-up list. Its home is the tracker work item of the item, with a link from the pull request, so each item gets a work item at intake. No finding makes a new item or a new run, and no session files an item for a finding. The owner reads the list in the report and promotes what they want. Only a promotion by the owner makes an item of a finding.

## The Lows lists

The Low findings go to 1 Lows list for each area of the code, for example 1 for each top folder. Each list is 1 work item in the tracker. An intake adds to its spec at most `config:triage.lows_per_spec` Lows of the files that the spec already changes, and removes them from the list. A Lows list becomes an item of its own only when the owner promotes it.
