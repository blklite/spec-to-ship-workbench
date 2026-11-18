# AGENTS.md

How an agent works in this repo. `CLAUDE.md` points here; this file is the canonical instruction file.

## What this repo is

The workbench process ([README.md](README.md)): rules in `process/`, roles in `roles/`, example personas in `personas/`, prompts in `prompts/`, example config in `config/`, the cost script in `cost/`, and the secret scan in `scripts/`. When you work on an item of this repo, follow the process it describes: [process/stages.md](process/stages.md).

## Rules

1. **Nothing private.** No person's name, host name, user name, private domain, organization or project name, user path, key or token goes into a file or a commit message. Write generic examples. The 1 exception is the attribution and the source of quoted third-party text, as its licence requires, kept in `THIRD_PARTY_NOTICES.md` and at the quote. Before you hand off, run the scan on the tree and the whole history:

   ```sh
   WORKBENCH_DENYLIST=<path to your local list> scripts/secret-scan.sh .
   ```

   It must exit 0. A hit in the message of a commit that `scripts/known-message-hits.txt` lists does not count; a new entry in that file needs the owner's approval. Never print, quote or commit the content of the local list.
2. **No values in the process.** Process files refer to a configured value by key, in the form `` `config:<table>.<key>` ``, and to a role in the form `` `role:<name>` ``. A test checks that each key exists in `config/workbench.example.toml` and each role has a file in `roles/`.
3. **No ruling or item numbers in `process/`.** A process rule states the rule, not where it was decided. A test fails on a `Q` followed by digits or a `#` followed by digits there.
4. **No prices in code.** `cost/cost.py` reads every price from `cost/prices.json`.
5. **Prompts end with a fixed last line**, stated in a fenced block after "The last line of your reply is exactly". A test checks the form.
6. **Tests.** Run `python -m pytest` at the repo root and report its counts. Never pipe the test runner through `tail` or `head`: the pipe hides the exit code. A test value that looks like a secret or a private name is built at run time from pieces, so that no file holds a real hit.
7. **Commits.** 1 commit for each AC where practical. Do not merge, push or change the tracker unless the item's approval covers it. Merge each pull request with a written subject and body, never the default message of the platform. Before the merge, run `WORKBENCH_DENYLIST=<path to your local list> scripts/secret-scan.sh --message <file>` on that text; it must exit 0.
8. **Shell scripts** use LF line ends (`.gitattributes`), `grep -E` and no `grep -P`, and run on bash 3.2 (no associative arrays).
9. **Decision rights.** Decide alone only what [process/decision-rights.md](process/decision-rights.md) allows; record it under "Decided for you". Otherwise ask.
