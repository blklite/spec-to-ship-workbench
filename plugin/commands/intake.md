---
description: Draft a spec with the owner, then send it to the workbench
argument-hint: <item id> [what the item should do]
allowed-tools: mcp__plugin_workbench_workbench__wb_intake
---

Draft a spec for the item $ARGUMENTS with the owner, then send it with the tool `wb_intake`.

1. The first word of the arguments is the item id. If it is missing, ask the owner for it.
2. Ask the owner short questions until each section below has content. Write the spec as 1 Markdown file of at most 8 KB. The title is a `# <Title>` line, then the header lines of status, work item, tier, size and the tests that the item can retire, then these sections, in this order:

- `## Story`
- `## Rulings`
- `## Acceptance criteria`
- `## Non-goals`
- `## Checks (<tier> tier)`
- `## Delivery`

3. Give each acceptance criterion a name, what must be true, and a *Proof:* (the test or check that fails when the criterion is not met). Leave no open question, and do not write TBD or TODO.
4. Show the owner the finished spec. When the owner agrees, call `wb_intake` with the id and the full spec text. If the owner names the repository of the item, also pass `repo`: a name from `[repos]` in the config of the workbench. With no `repo`, the item uses the origin of the runner clone, and an amendment keeps the repository of before. The tool refuses an unknown name, and a new repository while the work folder of the item exists: the owner deletes a wrong work folder by hand.
5. Report the result of the tool in 1 line. If the tool refuses the spec, show each problem it names and repair the spec with the owner.

A change to a spec that the workbench already holds (an amendment) is a new `wb_intake` call with the same id and the amended text. Use no other tool.
