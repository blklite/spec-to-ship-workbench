# Sandbox

A step runs on a host inside a wrapper: a small program that puts the step in a sandbox of the operating system before the step starts. The rules below hold for each wrapper, whatever the runner is. A probe, [scripts/sandbox-probe.sh](../scripts/sandbox-probe.sh), checks them from inside the wrapper.

## Rules

1. **No privilege gain.** The step cannot get more rights than the wrapper gave it. The wrapper sets no-new-privileges, and that counts as a user with no sudo. A separate user with no sudo rule is the stronger form; it is a decision for the host owner.
2. **Files.** The system is read-only. Only the work folder, the run folder, a step-only home and a private `/tmp` accept a write. Keys, configuration, tool homes, the state home, logs and live repositories are absent, or empty. Exception: with `--review-token`, a build or rework step holds 1 review token in a file of its step home; the wrapper deletes the file when the step ends, and the runner deletes it after a timeout or a reboot.
3. **Network.** Off for tests. Shared only when the CLI calls a model API. There is no allow-list: a firewall change is a High tier action.
4. **Environment.** The wrapper clears the environment, then sets only named variables. No environment file is bound.
5. **Temporary places.** `HOME`, `TMPDIR` and the `XDG_*` folders point inside the wrapper before tests run.

## The probe

Run the probe as the first command inside the wrapper, before the first step:

```sh
scripts/sandbox-probe.sh --net <off|on> --work <dir> --real-home <dir> --deny <path>... [--allow-env <name>]...
```

- `--net` is `off` for tests and `on` for a step that calls a model API.
- `--work` is the work folder. `--real-home` is the home of the host user. `--deny` lists each place of rule 2 that must be absent or empty. `--allow-env` names a variable that the wrapper sets on purpose, such as `XDG_CONFIG_HOME`.
- The probe checks: `sudo -n true` fails and `NoNewPrivs` is 1; each deny path is absent or empty; a write in the work folder works and a write in its parent fails; with `--net off`, only the interface `lo` exists; no variable is set outside `PATH`, `HOME`, `TMPDIR`, `LANG`, `PWD`, `SHLVL`, `_` and the `--allow-env` names; `HOME` is not the real home.
- It makes no network call and prints no value and no file content. It prints 1 line for each failed check, then `SANDBOX: PASS` (exit 0) or `SANDBOX: FAIL - <n> checks` (exit 1). A usage error exits 2.
- `--proc <dir>` replaces `/proc` for the files `self/status` and `net/dev`. It is a seam for tests. A wrapper check never uses it.

A new wrapper passes the probe before its first step. A wrapper that gives `FAIL` is repaired, or the record keeps the `FAIL` and a follow-up item opens; the probe never changes a wrapper. Known limits: the probe reads `/proc`, so it runs on Linux only; it does not see an exported shell function or an environment entry whose name is not a shell identifier; it does not check the value of `TMPDIR` or the folder `/tmp`; it does not check the network when `--net` is `on`.

## Local

Each installation keeps its own wrapper arguments, users, firewall and deny list, outside this repository. The wrapper texts stay where they are; each must pass the probe.
