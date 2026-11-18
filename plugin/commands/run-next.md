---
description: Start the next steps of an item and return at once
argument-hint: <item id>
allowed-tools: mcp__plugin_workbench_workbench__wb_run_next
---

Call the tool `wb_run_next` once for the item $ARGUMENTS. If the id is missing, ask the owner for it.

Report the result in 1 line, then stop. Do not wait for the run to end, do not poll, and do not call another tool. Tell the owner to use the `status` command later.
