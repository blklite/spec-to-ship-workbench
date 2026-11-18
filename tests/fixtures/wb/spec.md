# A greeting on the start page

**Status:** Checked. **Work item:** an example link.
**Tier:** Medium (a page that a reader sees). **Size:** Small, $40, 2 hours, 2 rework rounds.
**Tests that the item can retire:** none.

## Story

As a visitor, I want a greeting on the start page, so that I know the site works.

## Rulings

The greeting is plain text.

## Acceptance criteria

1. **Greeting.** The start page shows "Hello". *Proof:* a page test finds the text.
2. **No change elsewhere.** The other pages
   stay the same. *Proof:* the existing page tests pass.

## Non-goals

A greeting in other languages.

## Checks (Medium tier)

The tests and the review. A marker in code is no open question: `TODO` here is fine.

```text
TODO in a fenced block is fine too.
## Open questions
```

## Delivery

1 pull request; the owner merges.
