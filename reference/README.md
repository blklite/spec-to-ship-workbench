# Reference items

A reference item is a completed item with a known result. Each new runner runs the reference items first, before a role maps to it, so that runners can be compared on the same input ([process/runner-contract.md](../process/runner-contract.md)). The result of a new runner gates nothing: the Claude result counts.

## Files

Each item has 1 folder, `reference/<id>/`:

| File | Holds |
|---|---|
| `item.toml` | `id`, `step` (the step that runs: `spec-check`), `base` (the commit of this repo that the step read) and `reconstructed` (true when no copy of the text that the step read exists, and the text was rebuilt) |
| `spec.md` | The text that the spec check read, with names made neutral; at most `config:specs.max_kb` |
| `expected.md` | The verdict and each known finding, F1 to Fn, from the Claude result on the work item |

`results.md` holds 1 row for each run: the recorded Claude row (its source is the work item) and 1 row for each run of a new runner (its source is the run folder).

## The command

```sh
python -m runner.reference --runner R [--item ID] [--timeout S]
```

For each item (or the 1 that `--item` names), the command makes a throwaway clone of this repo at the item's base, adds `spec.md` at the root of the clone, and writes a prompt: the item id and the spec path, then [prompts/spec-check.md](../prompts/spec-check.md) byte for byte. It calls `run_step` ([runner/README.md](../runner/README.md)) with the clone as the work folder, deletes the clone, and prints 1 row for `results.md`:

```
| date | item | runner | model | status | last line | tokens (in/out/cache read/cache write) | seconds | dollars | found | missed | source |
```

`found` and `missed` are empty: the main session fills them from `expected.md` (for example `F1, F3`). The time limit `--timeout` is in seconds, default 1800. The clone is in the temporary folder (`TMPDIR`); the run folder is under `runs/`, as for each step. A usage error (an unknown runner or item, a base that is not in the history) exits 2.

## Add your own item

This repo ships no reference item: the items are completed items of your own work. To add 1:

1. Pick a completed item whose spec check has a known result, and make the folder `reference/<id>/`.
2. Write `item.toml`, for example:

   ```toml
   id = "<id>"
   step = "spec-check"
   base = "<commit id, 7 to 40 hex digits>"
   reconstructed = false
   ```

   `base` is a commit of this repo's history, the one that the spec check read.
3. Copy the spec that the check read to `spec.md`, with private names made neutral, at most `config:specs.max_kb`.
4. Write `expected.md`: the verdict line (`**Verdict:**`) and each known finding, `- **F1 ...`, from the recorded result.
5. Add the recorded row to `results.md`, with the work item as its source.

Then run `python -m runner.reference --runner R --item <id>` for each new runner.

## The rule

A new runner runs each reference item before a role maps to it. Its rows go in `results.md` with the run folder. A missed finding is a fact for the comparison, not a failure.
