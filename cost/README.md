# cost.py: the cost of an item from its transcripts

The budget of an item covers the whole item: the subagents plus the main session that runs it ([process/budget.md](../process/budget.md)). `cost.py` measures both parts from the Claude Code transcripts, at list price, until the runner script records them itself.

## Usage

```sh
python cost/cost.py transcript <file.jsonl>          # 1 agent transcript
python cost/cost.py session <dir>/<id>               # a main session and its subagents
python cost/cost.py session <dir>/<id> --window 2026-10-07T09:00:00Z 2026-10-07T13:00:00Z
python cost/cost.py session <dir>/<id> --json
```

Options come after the mode: `--prices FILE` (default `cost/prices.json`), `--window START END` (ISO 8601; only records in `[START, END)` count), `--json`.

Claude Code keeps the transcripts of a project in its projects folder (`~/.claude/projects/<project>/`). A session `<id>` there has:

- `<id>.jsonl`: the **main share**, the transcript of the session that you talk to;
- `<id>/subagents/agent-*.jsonl`: the **subagent share**, 1 transcript for each subagent, with an `agent-*.meta.json` beside it that holds the subagent's description and type.

## The main-session share

The main session is not free: it reads every subagent's report, keeps the whole conversation in its cache, and often runs on the most expensive model. In the first items of the workbench it cost about as much again as all of the subagents together. A budget that counts only the subagents is therefore off by about half, which is why the limit covers both shares and the tracker records them in 2 fields ([docs/ado-setup.md](../docs/ado-setup.md)).

A main session usually spans several items. Use `--window` to cut the part of the main transcript that belongs to 1 item: from the intake message of the item to its delivery. The subagent transcripts are cut by the same window.

## How a record is priced

- Prices come from `prices.json`: input, output and cache read in USD per million tokens for each model, and the multipliers of the 2 cache-write TTLs (5 minutes: 1.25 times input; 1 hour: 2 times input). There is no price in the code.
- A model id with a date suffix (`<model>-20261001`) takes the price of its listed prefix.
- Streaming writes several records for 1 model call, with the same `message.id`. The call counts once: each usage field keeps its largest value.
- A legacy record that has only `cache_creation_input_tokens` (no split by TTL) is priced as a 5-minute write.
- **Unpriced models:** a call of a model that `prices.json` does not list is not priced at $0. It is listed as `UNPRICED` with its calls and tokens, and it is left out of the totals, so a missing price is visible. Add the model to `prices.json` and run again.
- **Low and high:** the low figure uses `output_tokens` of the usage block. The high figure takes, for each call, the larger of `output_tokens` and an estimate from the text and tool input of the record (`chars_per_token` characters for each token) plus its thinking tokens. Some transcripts record only the first streamed usage block, which under-counts output; the truth lies between the 2 figures. Known limit: the high figure counts thinking only from the thinking-token field of the usage block, and that count is tested on the synthetic fixture only, not against real transcripts; where a transcript lacks the field, the high figure can miss thinking output and is then not an upper bound.

## Tests

`tests/test_cost.py` runs on a synthetic fixture (`tests/fixtures/cost/`) with round prices: a duplicate stream record, a 5-minute and a 1-hour cache write, a legacy record, an unknown model and 1 subagent with its meta file. The expected dollars are exact.
