---
description: Show the state of one item, or of all items
argument-hint: [item id]
allowed-tools: mcp__plugin_workbench_workbench__wb_status
---

Call the tool `wb_status` for $ARGUMENTS. With no argument, call it for all items.

Then report in 1 screen: for each item the stage, the status and the budget used, and for an item that waits, its question.

If an item waits at a case 1, 3 or 4 stop (the budget, a High tier item, or the round limit), say that the owner acts at the host terminal, and stop. Do not try another tool for it.

If an item waits at a case 2 stop (a decision, or a blocked step), say that the owner can answer it with the `answer` command of this plugin.

Use no other tool, and do not start a run.
