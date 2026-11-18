# The workbench plugin for Claude Code

4 slash commands over the `wb` MCP server. The server runs on the host that holds the workbench; the plugin starts it through ssh, so no port opens and the ssh key is the only credential. The plugin has no hook, skill or agent, and no command approves, merges or runs a shell: an approval is given at the host terminal only.

## Install

1. Add this block to your ssh config once. `workbench-host` is the alias that `.mcp.json` names; replace the 3 values with your own. The host must have the `wb` MCP server installed as `~/.local/bin/wb-mcp`, a symbolic link to `scripts/wb-mcp` of the code clone on the host ([wb/README.md](../wb/README.md)); the plugin does not install it, and without it all 4 commands fail. `scripts/wb-mcp` puts `~/.local/bin` first on `PATH`, so the steps find `claude` there. On a host where `claude` is not in `~/.local/bin`, set `PATH` in the ssh command: the last argument in `.mcp.json` becomes `PATH=<folder of claude>:$PATH ~/.local/bin/wb-mcp`.

   ```
   Host workbench-host
       HostName <host name or address>
       User <user name>
       IdentityFile <path of your private key>
   ```

2. In Claude Code, run `/plugin marketplace add <path of the clone>`, where the clone is your copy of this repo.
3. Run `/plugin install workbench@workbench`.

## The commands

| Command | Does |
|---|---|
| `/workbench:intake <id>` | Drafts the spec with you in the sections of [docs/specs.md](../docs/specs.md), then sends it with `wb_intake`. An amendment is a new call |
| `/workbench:status [<id>]` | Calls `wb_status` and reports 1 screen. At a stop that needs the host terminal, it says so |
| `/workbench:answer <id> <answer>` | Calls `wb_answer`, at a case 2 stop only |
| `/workbench:run-next <id>` | Calls `wb_run_next` and stops. It does not wait and does not poll; ask for the status later |
