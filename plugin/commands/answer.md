---
description: Answer a waiting item at a case 2 stop
argument-hint: <item id> <the answer>
allowed-tools: mcp__plugin_workbench_workbench__wb_status, mcp__plugin_workbench_workbench__wb_answer
---

Answer the item in $ARGUMENTS.

1. The first word of the arguments is the item id. The rest is the owner's answer. If either is missing, ask the owner.
2. Call the tool `wb_status` for the item. Continue only if the item waits at a case 2 stop (a decision outside the decision rights, or a blocked step).
3. At a case 1, 3 or 4 stop (the budget, a High tier item, or the round limit), do not answer. Say that the owner acts at the host terminal, and stop.
4. Call the tool `wb_answer` with the id and the owner's words. Do not write an answer for the owner, and do not shorten or change the words.
5. Report the result of the tool in 1 line.

Use no other tool.
