# runner: run 1 step on any configured runner

The runner core knows 1 operation, "run a step" ([process/runner-contract.md](../process/runner-contract.md)): a prompt file, in a work folder, on a runner named in config, with a time limit. It gives the same result form for each runner, so that the stage machine and any front end need no code that knows the vendor.

```sh
python -m runner.step --folder F --prompt P --timeout S --runner R [--out D]
```

```python
from runner.step import run_step
counters = run_step(folder, prompt_file, timeout_s, runner, out_dir)
```

## The result

The last line of the command is

```
STEP: <status> <runner>: <the last line of the step, or the reason>
```

| Status | When |
|---|---|
| `OK` | The last non-empty line of the reply matches 1 allowed last line of the prompt. A non-zero exit code is still `OK`; the code is in the counters. Not for the `claude` adapter: its stream can end with an allowed line of an earlier turn, then a crash, so a non-zero exit is `FAILED` |
| `INVALID` | The CLI exited 0, but the reply has no final text, or its last line is not an allowed line |
| `TIMEOUT` | The time limit passed. The step stopped the CLI and every child process (the process tree and its job on Windows, the process group on POSIX). The usage that the CLI printed before the limit still counts. If the tree kill fails, the step still kills the CLI itself, the status stays `TIMEOUT`, and the reason says that the tree kill failed |
| `UNAVAILABLE` | The CLI is not on PATH, or it is there but does not start. Or a `claude` runner with no `wrap` has no credential to redact (below), so the CLI does not start. Or the runner has `require_commands = true` and the run is not a result: no command item ran, no command ended with exit code 0, or an error item is not a stream retry, whatever the last line says (the reason starts with `no command ran`) |
| `FAILED` | A non-zero exit with no allowed last line; for the `claude` adapter, each non-zero exit. Or the output capture failed (below), whatever the last line says, also at the time limit |

The exit code is 0 for each status. A usage error (a bad argument, an unknown runner or adapter, a bad runner table, a prompt with no last-line block, a missing work folder) prints a message on stderr and exits 2, with no STEP line and no run folder.

**The allowed last lines** are the lines of the first fenced block after "The last line of your reply is exactly" in the prompt file. In each line, `<...>` stands for 1 or more characters; all other text is literal. The match is on the whole line. When the last non-empty line of the reply is a closing fence (`` ``` `` alone, trailing white space allowed) and an earlier line opens that fence (`` ``` `` with any info string; fences pair from the top), the last non-empty line inside that block is the last line. Text after the closing fence, an empty block, a closing fence with no opener and a line in inline backticks do not match. CRLF replies work the same.

**The run folder** is `--out`, else a new folder under `runs/` of this repo, which git ignores. It holds:

- `counters.json`: runner, adapter, model, the guard of the runner (`guard`, below), the prompt path and the SHA-256 of its bytes, start and end time (UTC), seconds, the CLI's own seconds where it gives them, input, output, cache-read and cache-write tokens, dollars (`usd`), status, last line, reason and the exit code of the CLI. A counter that the runner does not give is `null`. The token counts are the raw values of the CLI: Claude gives input without the cache reads, Codex gives input with the cached tokens and the cache writes included (`cached_input_tokens` and `cache_write_input_tokens`; the dollars price only the rest as input, and a cache write at the 5-minute multiplier of `prices.json`). A codex that does not print `cache_write_input_tokens` gives a cache-write count of `null`. A runner with a `wrap` adds `wrap`, the wrap command. The codex adapter adds `commands`, the count of the command items of the run (`command_execution`, 1 for each item id).
- `stdout.txt` and `stderr.txt`: the raw output of the CLI. For the `claude` adapter, `stdout.txt` is the stream of the whole run, 1 JSON object on each line, tool results included. So that no token is stored there, the step replaces the value of each credential variable of the CLI's environment (`CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`) with `<redacted>` as the CLI writes, before a byte reaches either file: the output then goes through 2 pipes, so no token is on the disk during the run or after the runner dies. A CLI can also sign in through its credentials file, with no variable: so for a runner with no `wrap`, the step reads `.credentials.json` in `CLAUDE_CONFIG_DIR` of the CLI's environment (a relative one in the work folder, where the CLI starts), else in `.claude` of the home folder, before the start, and each JSON string value of 20 or more characters, at any depth, is a secret too. Such a value must be printable ASCII with no `"` and no `\`, since the CLI could print another form of it; else the file counts as one that can not be parsed. With no secret at all the CLI does not start: the step is `UNAVAILABLE`, `no credential to redact`, or `the credentials file of the CLI can not be read` for a file that exists but can not be read or parsed; the reason holds no path and no text of the file. A wrapped runner reads no file: the wrap has its own home, and the wrap of a Linux host also redacts the token that it holds as the step writes. A child that keeps a pipe open after the CLI ends gets 5 seconds, then its output is dropped. When a write or the close of either file fails (a full disk, for example), the output is lost: the step stops the CLI and every child at once, drops the rest, and is `FAILED`, with a reason that starts with `the output capture failed:` and the error.
- `reply.txt`: the text of each turn of the reply, in order, joined with 1 empty line, in UTF-8; only when the adapter gives it and the run has a result text (the `claude` adapter: 1 turn for each `result` object; a late notification opens 1 more). Another adapter writes none, so the reader of a reply takes `reply.txt` when it exists, else `stdout.txt`. The status, the last line and the counters come from the last result only.

The step writes nothing in the work folder; the CLI may.

**Dollars** come from `cost/prices.json` through the price code of `cost/cost.py`, at list price. A model that `prices.json` does not list gives `"unpriced"`, never 0. No price is in the runner code. The opencode adapter reports each run as 1 call of the model `local`, which `prices.json` lists at $0, so its `usd` is 0.0; only a runner with the adapter `opencode` may name the model `local`.

## Runners

A runner is a `[runners.<name>]` table of `config/workbench.toml` (the local file, which git ignores), else of `config/workbench.example.toml`. The role map in `config/personas.toml` (else the example) names the runner of each role.

```toml
[runners.claude]
adapter = "claude"
model = "claude-opus-5-5"
allowed_tools = ["Read", "Grep", "Glob", "Edit", "Write"]
setting_sources = []

[runners.codex]
adapter = "codex"
model = "gpt-6.1-sol"
sandbox = "read-only"
```

Each adapter accepts only its own keys. An unknown key is a usage error; no key is passed through to the CLI as it is.

| Adapter | Command | Keys | Refused |
|---|---|---|---|
| `claude` | `claude -p --output-format stream-json --verbose --model M [--allowedTools T] --permission-mode P --setting-sources S --strict-mcp-config`, prompt on stdin, in the work folder | `allowed_tools` (tool names only), `setting_sources` (`user`, `project`, `local`; default none; a named source hands its allow rules and hooks to the step), `permission_mode` (`default`, `dontAsk`, `plan`; always passed, default `default`, so that a setting source cannot set the mode; a runner that must edit files names `Edit` and `Write` in `allowed_tools`) | any `dangerously` flag, `bypassPermissions`, `acceptEdits`, `auto` and every other mode, `--settings`, `--mcp-config`, `--add-dir` |
| `codex` | `codex exec --json -C <folder> --ephemeral --ignore-user-config --ignore-rules -s <sandbox> -m M -`, prompt on stdin | `sandbox` (`read-only` or `workspace-write`), `require_commands` (`true` or `false`, default `false`; `true` makes a run with no command `UNAVAILABLE`) | any `--dangerously-*` flag, `danger-full-access`, `--search`, `--add-dir`, `--oss`, `-c`/`--config`, `-p`/`--profile`, `--enable`, `--approve-for-me`, `--skip-git-repo-check` |
| `opencode` | `opencode run --format json -m local/<model>`, prompt on stdin, in the work folder; the config of the step in the env (`OPENCODE_CONFIG_CONTENT`, see [opencode](#opencode)) | `base_url` (`http` or `https`, a host, an optional port, then `/v1`), `edit` and `bash` (`allow` or `deny`) | the keys `webfetch`, `websearch`, `mcp`, `plugin`, `share`, `provider`, `agent`, `api_key`, `extra_args`; the value `ask` (the step has no terminal: it would hang); `--auto`, `--yolo`, `--attach`, `--port`, `--share`, `--agent`, `--dir`, `--file`, `--server` |
| `fake` | the Python that runs the step, on `runner/adapters/fake_replay.py <script>` | `script` (a reply script: final text, counters, delay, exit code; relative to the repo root), `command` (optional) | none: it calls no model |

Config strings that reach a command line must match a strict pattern (no space, no shell metacharacter, no leading `-`). Before the CLI starts, a last guard refuses any argument that holds `dangerously` or a forbidden flag, and, when the CLI or the wrap is a `.cmd` or `.bat` shim, any character that cmd.exe acts on. A `.cmd` or `.bat` wrap also gets this check on the CLI path, because the CLI path is an argument of the wrap.

**The wrap.** Any runner table may have `wrap`, a list of strings: the step then runs `wrap` + the command of the adapter, in the work folder, so that the CLI runs inside a wrapper of the host (for example a script that starts a sandbox and execs the rest of its arguments). The first element is an absolute path (a leading `/`, or a drive letter on Windows) of the characters `A-Z a-z 0-9 . _ - /`; each other element matches the strict pattern above. An empty list, a wrong type or a bad value is a usage error. The counters record the wrap. A wrap that does not start gives `UNAVAILABLE` and names the wrap. For a Claude step on a Linux host, [scripts/wrap-step.sh](../scripts/wrap-step.sh) is a bubblewrap wrap ([wb/README.md](../wb/README.md#the-host)).

**The guard.** Any runner table may have `guard`, `true` or `false`; another value is a usage error. It is on by default for each adapter except `fake`, and the counters record it. The runner core makes no scan: `python -m runner.step` alone sends the work folder to the CLI as it is. The stage machine runs the secret scan before each step of a guarded runner ([wb/README.md](../wb/README.md#the-secret-guard)). On a host the stage machine also starts each run detached from the terminal, pushes the branch after a build and after a rework (the step itself never pushes), and ends an interrupted step after a reboot ([wb/README.md](../wb/README.md)).

## opencode

The adapter for a local model is opencode, not aider. opencode has read, search, edit and shell tools (a spec check runs the test suite), prints JSON events with the token counts of each step, is 1 linux-arm64 binary (it needs no node), and calls `/v1/chat/completions` of an OpenAI-compatible API. aider has no tool loop, commits by itself and prints its tokens as text.

The adapter writes the whole config of the step into `OPENCODE_CONFIG_CONTENT`: 1 provider `local` (OpenAI-compatible, `base_url`, no API key) and no other, the model, `edit` and `bash` from the runner table, `webfetch` denied, no MCP server, no plugin, share and autoupdate off. opencode also reads a project config. So `opencode.json`, `opencode.jsonc` or `.opencode/` in the work folder or in a parent up to the git top level (every parent when there is no git top level) is a usage error, and so is an inherited `OPENCODE_*` variable other than `OPENCODE_CONFIG_CONTENT`, which the adapter sets. The global config (`~/.config/opencode/`) and the auth file of opencode are in the home folder: the adapter does not check them. Run each opencode step in a wrapper with an empty step home ([process/sandbox.md](../process/sandbox.md)), so that no global config, key or plugin adds to the step. A `wrap` of an opencode runner must pass the environment on to the CLI: a wrap that clears it drops the config of the step.

## The adapter contract

An adapter is 1 module in `runner/adapters/` with 4 functions and 1 optional function:

| Function | Does |
|---|---|
| `validate(name, table)` | Checks the runner table: known keys only (`check_keys`), each value against a strict pattern (`check_value`). Raises `UsageError` for anything else, and by name for a setting that would widen the permissions |
| `program(table)` | The name of the CLI; the step finds it with `shutil.which` (which honours PATHEXT on Windows). Not found means `UNAVAILABLE` |
| `argv(table, exe, folder)` | The command line, built from known keys only. The prompt goes on stdin |
| `env(table, folder)` | Optional. The variables that the step adds to the environment of the CLI; the rest is inherited. Raises `UsageError` for a setting outside the table that would change the permissions |
| `parse(stdout, table)` | The output, parsed into a `Reply`: the final text, the token counters (`input`, `output`, `cache_read`, `cache_write`, each an int or `None`), the CLI's seconds, and 1 call for each model in the form of `cost.py` (`model`, `in`, `cr`, `cw5`, `cw1`, `out`) for the dollars |

It may set `FORBIDDEN_ARGS`, the arguments that the last guard refuses.

The step does the rest for every adapter: the time limit and the kill of the process tree, the stdout and stderr files, the last-line match, the status, the dollars and `counters.json`.

## Add an adapter

1. Write `runner/adapters/<name>.py` with the 4 functions. Accept only the keys you need; refuse by name each setting that would widen the permissions; set `FORBIDDEN_ARGS`.
2. Add the name to `ADAPTER_NAMES` in `runner/config.py`, and 1 example `[runners.<name>]` table to `config/workbench.example.toml`.
3. Add the model of the example to `cost/prices.json` at the vendor's list price, with the source.
4. Write the tests with a fake CLI on PATH (`tests/runner_fakes.py`): the exact argv, the folder and stdin, the parse of a synthetic output, and 1 test for each forbidden setting.
5. Run `tests/fixtures/runner/echo.md` once on the real CLI and check that it gives `STEP: OK` and more than 0 input tokens.
