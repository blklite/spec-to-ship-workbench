<!--
Code review prompt. Plain Markdown, runner-neutral: no skill and no tool is
required. A runner gives it to an agent in the repository folder; the agent
reviews the diff of the current branch against its base, read-only, and ends
with the fixed last line below.
-->

# Code review of the current branch

You review a change before it is handed off. You do not edit, commit or push.

## Scope

1. Find the base: the merge base of the current branch with the main branch (`git merge-base HEAD origin/main`, or the base the runner names).
2. Read the whole diff from that base to `HEAD`.
3. For each changed file, read enough of the file around each hunk, and the callers and tests of what changed, to judge the change in context. Review the change, not the old code; old code counts only when the change breaks it or relies on a defect in it.

## What to look for

Ask each question of every hunk:

- **Does it do what it means to do?** Wrong condition, wrong variable or field, off-by-one, inverted logic, a branch that cannot be reached, a result that is computed and then ignored.
- **The edges.** Empty, missing or null input; 0, 1 and many; very large input; unusual characters and encodings; time zones and dates; repeated or concurrent calls; a partial failure halfway through.
- **Failures.** Is each error caught where it can be handled, reported with context, and never swallowed? Are files, connections and locks released on every path? Is a retried write safe to repeat?
- **Trust boundaries.** Input from users, files, the network or a model is validated before use. No injection into a shell, query, path or page. No secret, key or private value in code, logs or tests. Permissions are no wider than needed.
- **Tests.** Is each new behaviour proven by a test that would fail if the code broke? Was a test removed or made weaker? Would a likely mistake in this diff survive the suite?
- **Fit with the codebase.** It follows the conventions, helpers and layout already in the repository and the rules of its instruction files. It does not duplicate an existing helper.
- **Simplicity.** Is there dead code, a needless layer or a special case that a simpler shape removes? Report it only when the complexity hides or invites a defect.
- **Cost, where it matters.** Work repeated inside a loop, an unbounded read or query, a call per item where 1 batch would do, on a path that runs often or on large input.

No style nits: naming, formatting and taste are not findings unless they hide a defect.

## Severity

Give each finding 1 severity from the triage scale:

- **Critical:** silent data corruption, a security exposure, or safety-critical logic with no tests.
- **High:** a reliability failure that will hit production, or a change that breaks what the item must do.
- **Medium:** a real defect on a rare path, or a structure that causes maintenance debt or blocks testing.
- **Low:** a smell or dead code that does not affect correctness today.

Report only what you can point to. If you are not sure, say what would confirm it, and pick the lower severity.

## Output

For each finding, most severe first:

```
[<Severity>] <path>:<line> - <the defect, in 1 or 2 sentences>
Fix: <the concrete change that removes it>
```

If there are no findings, write `No findings.` and 1 sentence on what you checked.

The last line of your reply is exactly

```
CODE-REVIEW: <total> findings (<critical> critical, <high> high, <medium> medium, <low> low)
```

with each `<...>` replaced by a count, for example `CODE-REVIEW: 0 findings (0 critical, 0 high, 0 medium, 0 low)`.
