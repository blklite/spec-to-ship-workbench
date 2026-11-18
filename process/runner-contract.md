# Runner contract

A plain script runs the stages. No model is inside the script. The script knows 1 operation, "run a step":

- **Input:** a folder, a prompt file, a time limit, the name of a runner.
- **Output:** a fixed last line, the files and commits in the folder, and counters for tokens, seconds and dollars.

The runner core exists ([runner/](../runner/README.md)): `python -m runner.step` runs 1 step on a runner that the configuration names, and writes the fixed last line and the counters. The stage machine calls it for each step: `python -m wb` ([wb/README.md](../wb/README.md)) runs stages 2 to 5 of an item and reads only the fixed last line and the counters of each step. Its host setup is in [wb/README.md](../wb/README.md).

Each runner (the Claude Code CLI, the CLI of a different vendor, a local model behind an open agent program) gets a small adapter. Rules:

- Prompts are plain Markdown files in the repository ([prompts/](../prompts/)). A run needs no skill and no MCP tool. A step may use a skill on 1 runner only when a prompt file does the same job for the other runners: the builder's code review is `/code-review high` on Claude Code and [prompts/code-review.md](../prompts/code-review.md) elsewhere.
- A role names a capability (`role:builder`, `role:reviewer`). The configuration maps a role to a runner, and to a persona ([config/personas.example.toml](../config/personas.example.toml)).
- The sandbox is the operating system: a user with no sudo, file permissions, the firewall. Before an agent runs tests on a host, the write paths, state folders, logs and env files point at temporary places. The 5 rules and the probe that checks them are in [sandbox.md](sandbox.md).
- The script reads only the fixed last line and the counters.
- A second adapter is added early, for 1 cheap step. 2 or 3 completed items are kept as reference items ([reference/](../reference/README.md)). A new runner runs them before a role maps to it: `python -m runner.reference --runner <name>`.
- The repository of an item is named at intake: a name from `config:repos`, whose value is a GitHub clone URL. The state of the item holds the name and the URL; the work folder clones that URL, and the push and the base of the scan follow the origin of that folder. With no name, the item uses the origin of the runner clone. An unknown name is refused. A new repository for an item is refused while its work folder (`config:work.home`) exists, and a work folder whose origin is not the URL of the item stops the run before any step: the owner deletes a wrong work folder by hand. The git identity of a work folder stays that of the runner clone.
- The main session runs on `config:models.main_session`, as the subagents do, and its cost is recorded for each item ([budget.md](budget.md)). The owner selects the model of the session before the intake of an item.

With no host, a session runs the same stages by hand from [stages.md](stages.md).
