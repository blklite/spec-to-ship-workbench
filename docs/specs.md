# The spec format

A spec is the input of the workbench: 1 Markdown file for each item under `specs/` (the folder is `specs.home` in the config). A wiki home is optional; if you keep specs on a wiki, the work item links to the page and the run copies it into its folder at the start.

## The 8 KB limit

A spec is at most **8 KB** (`specs.max_kb`). The intake step enforces it. Long specs cost more to build: the cost of an item follows the precision of its spec, not its length. A spec holds no run log, no report and no prompt; those go to the work item and the run folder. A spec has no versions: an amendment replaces the text, and git keeps the history.

## Sections

```markdown
# <Title>

**Status:** <Draft | Ready for the spec check | Checked>. **Work item:** <link>.
**Tier:** <Low | Medium | High> (<why>). **Size:** <Small | Medium>, $<dollars>, <hours> hours, <rounds> rework rounds.
**Tests that the item can retire:** <list, or none>.

## Story

As a <role>, I want <capability>, so that <outcome>.

## Rulings

<The owner's answers to the questions of the intake and the spec check, 1 line each.>

## Acceptance criteria

1. **<Name>.** <What must be true.> *Proof:* <the test or check that proves it>.
2. ...

## Non-goals

<What the item does not do, including the obvious neighbours.>

## Checks (<tier> tier)

<The checks of the tier that apply, and any extra check.>

## Delivery

<What happens after the checks: the branch, the pull request, the merge and the live steps, and which approvals cover them.>
```

## Rules

- Each AC has a *Proof*: a test or a check that fails when the AC is not met.
- A spec has no open question: the intake refuses a `## Open questions` heading, and `TBD` or `TODO` as a whole word, outside code.
- The header may name the repository of the item with the word `Repo:` (for example `Repo: the widget repo.`). It is for the reader only: the intake does not require it or read it. The repository that the workbench uses is the `--repo <name>` of `wb intake` (the `repo` of the tool `wb_intake`), a name from `[repos]` in the config; with none, the item uses the origin of the runner clone. An unknown name is refused, a new repository is refused while the work folder of the item exists, and the owner deletes a wrong work folder by hand.
- The tier follows the owner's tier table ([process/checks-by-tier.md](../process/checks-by-tier.md)); the size and its limits follow [process/budget.md](../process/budget.md).
- The list of tests that the item can retire is the only approval to remove a test ([process/decision-rights.md](../process/decision-rights.md)).
- An open question that passes the test of the decision rights goes in an optional section `Decided for you` after Rulings, with the choice and 1 reason line, not under Rulings ([process/decision-rights.md](../process/decision-rights.md), "The open questions of an intake").
- The spec check (stage 2) reads the spec as written; a gap it finds becomes a question for the owner or a note for the builder, and an answer becomes a line under Rulings.
