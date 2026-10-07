# Triage policy for findings

Each finding of a check (the builder's code review, the review, the acceptance) gets 1 action:

| Finding | Action |
|---|---|
| Critical or High, in the code of the item | Repair now, inside the budget. After the allowed rework rounds (`config:budget.small.rework_rounds`, `config:budget.medium.rework_rounds`) with the checks still red: stop and ask |
| A test gap that a different check of the item already covers | Record. No hold |
| Medium, in the code of the item | Repair only in a rework round that a Critical or High finding already made necessary. No round starts for Medium findings alone. If no such round exists: the follow-up list |
| Low, or outside the scope of the item | The follow-up list |
| A defect in production that the work shows, for example a red main branch | Alert immediately. No repair without a go from the owner |

Severity follows the reviewer's framework ([roles/reviewer.md](../roles/reviewer.md)): Critical is silent data corruption, a security exposure or untested safety-critical logic; High is a reliability failure that will hit production or a broken AC; Medium is a real defect on a rare path or a maintenance problem; Low is a smell or a style issue.

## The follow-up list

Each item has 1 follow-up list. Its home is the tracker work item of the item, with a link from the pull request, so each item gets a work item at intake. No finding makes a new item or a new run. The owner reads the list in the report and promotes what they want.
