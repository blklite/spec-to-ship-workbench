# Runner contract

A plain script runs the stages. No model is inside the script. The script knows 1 operation, "run a step":

- **Input:** a folder, a prompt file, a time limit, the name of a runner.
- **Output:** a fixed last line, the files and commits in the folder, and counters for tokens, seconds and dollars.

Each runner (the Claude Code CLI, the CLI of a different vendor, a local model behind an open agent program) gets a small adapter. Rules:

- Prompts are plain Markdown files in the repository ([prompts/](../prompts/)). A run needs no skill and no MCP tool. A step may use a skill on 1 runner only when a prompt file does the same job for the other runners: the builder's code review is `/code-review high` on Claude Code and [prompts/code-review.md](../prompts/code-review.md) elsewhere.
- A role names a capability (`role:builder`, `role:reviewer`). The configuration maps a role to a runner, and to a persona ([config/personas.example.toml](../config/personas.example.toml)).
- The sandbox is the operating system: a user with no sudo, file permissions, the firewall. Before an agent runs tests on a host, the write paths, state folders, logs and env files point at temporary places.
- The script reads only the fixed last line and the counters.
- A second adapter is added early, for 1 cheap step. 2 or 3 completed items are kept as reference items, and each new runner runs them first.
- The main session runs on `config:models.main_session`, as the subagents do, and its cost is recorded for each item ([budget.md](budget.md)). The owner selects the model of the session before the intake of an item.

Until the script exists, a session runs the same stages by hand from [stages.md](stages.md).
