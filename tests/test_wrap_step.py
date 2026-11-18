"""scripts/wrap-step.sh, the wrap of a Claude step. Tests
on the argv that it gives bwrap, with a fake bwrap first on PATH. The probe of
a real step in the wrapper runs on the host (process/sandbox.md).

A wrapped step gets a step folder beside the work folder, with a new
home and the copies of its inputs, and its Inputs block names the copies.
"""

from __future__ import annotations

import os
import select
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

import runner_fakes
from conftest import ROOT
from wb import machine, state
from wb_fakes import start

WRAP = ROOT / "scripts" / "wrap-step.sh"
linux = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="bwrap and stat -c are Linux only")
FAKE_BWRAP = """#!/usr/bin/env bash
out="$FAKE_BWRAP_OUT"
printf '%s\\n' "$@" > "$out/argv"
env > "$out/env"
cat <&3 | tr '\\0' '\\n' > "$out/fd3"
exit 7
"""


@pytest.fixture
def host(tmp_path):
    """A home with a token file, a CLI that links into its package folder, a
    work folder with its step folder, and a fake bwrap first on PATH."""
    home = tmp_path / "home"
    (home / ".config" / "workbench").mkdir(parents=True)
    token = "wbtest" + uuid.uuid4().hex
    tf = home / ".config" / "workbench" / "claude-token"
    tf.write_text(token + "\n", encoding="utf-8")
    os.chmod(tf, 0o600)
    pkg = tmp_path / "pkg" / "versions"
    pkg.mkdir(parents=True)
    (pkg / "1.0.0").write_text("#!/bin/sh\n", encoding="utf-8")
    (home / ".local" / "bin").mkdir(parents=True)
    cli = home / ".local" / "bin" / "claude"
    cli.symlink_to(pkg / "1.0.0")
    work = tmp_path / "work" / "7"
    work.mkdir(parents=True)
    step = tmp_path / "work" / "7.step"
    (step / "home").mkdir(parents=True)
    (step / "inputs").mkdir()
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "bwrap").write_text(FAKE_BWRAP, encoding="utf-8")
    os.chmod(fake / "bwrap", 0o755)
    out = tmp_path / "out"
    out.mkdir()
    env = {"HOME": str(home), "PATH": f"{fake}:/usr/bin:/bin", "FAKE_BWRAP_OUT": str(out), "SECRET_OF_HOST": "x"}
    return {"home": home, "token": token, "tf": tf, "cli": cli, "pkg": pkg, "work": work, "step": step,
            "out": out, "env": env}


def wrap(h, mode="write", *args):
    return subprocess.run(["bash", str(WRAP), mode, *(args or (str(h["cli"]), "-p", "--model", "m"))],
                          cwd=h["work"], env=h["env"], capture_output=True, text=True)


def argv(h) -> list[str]:
    return (h["out"] / "argv").read_text().splitlines()


def pairs(a: list[str], flag: str) -> list[tuple[str, str]]:
    return [(a[i + 1], a[i + 2]) for i, x in enumerate(a) if x == flag]


@linux
def test_write_mode_binds_the_work_folder_writable_and_ends_with_the_cli(host):
    r = wrap(host)
    assert r.returncode == 7, r.stderr  # the exit code of bwrap, that is of the step
    a = argv(host)
    work, step = str(host["work"]), str(host["step"])
    for flag in ("--unshare-all", "--share-net", "--die-with-parent", "--new-session", "--clearenv"):
        assert flag in a[:a.index("--")], flag
    end = a.index("--")  # bwrap reads in order: the token after --clearenv and the last --setenv
    assert a[end - 2:end] == ["--args", "3"] and a.count("--args") == 1
    assert (work, work) in pairs(a, "--bind") and (step + "/home", step + "/home") in pairs(a, "--bind")
    assert (step + "/inputs", step + "/inputs") in pairs(a, "--ro-bind")
    assert a[a.index("--remount-ro") + 1] == "/" and a[a.index("--chdir") + 1] == work
    assert a.index("--remount-ro") > max(i for i, x in enumerate(a) if x in ("--bind", "--ro-bind", "--ro-bind-try"))
    assert a[a.index("--") + 1:] == [str(host["cli"]), "-p", "--model", "m"]


@linux
def test_read_mode_binds_the_work_folder_read_only(host):
    assert wrap(host, "read").returncode == 7
    a = argv(host)
    work = str(host["work"])
    assert (work, work) in pairs(a, "--ro-bind") and (work, work) not in pairs(a, "--bind")


@linux
def test_the_environment_is_cleared_and_the_token_goes_on_a_file_descriptor(host):
    assert wrap(host).returncode == 7
    a = argv(host)
    sets = dict(pairs(a, "--setenv"))
    assert sets == {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(host["step"]) + "/home", "TMPDIR": "/tmp",
                    "LANG": "C.UTF-8", "DISABLE_AUTOUPDATER": "1"}
    assert (host["out"] / "fd3").read_text().splitlines() == ["--setenv", "CLAUDE_CODE_OAUTH_TOKEN", host["token"]]
    assert all(host["token"] not in x for x in a)
    assert host["token"] not in (host["out"] / "env").read_text()


@linux
def test_only_the_named_paths_are_bound(host):
    """No ~/.ssh, ~/.config, ~/.claude, state home, code clone or env file: each bind source is on the list."""
    assert wrap(host).returncode == 7
    a = argv(host)
    work, step = str(host["work"]), str(host["step"])
    etc = {"/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf", "/etc/passwd", "/etc/group", "/etc/localtime",
           "/etc/ssl", "/etc/ca-certificates", "/etc/alternatives"}
    allowed = {"/usr", "/bin", "/sbin", "/lib", "/lib64", str(host["cli"]), str(host["pkg"]), work,
               step + "/home", step + "/inputs"} | etc
    binds = pairs(a, "--bind") + pairs(a, "--ro-bind") + pairs(a, "--ro-bind-try")
    assert binds and all(src == dst and src in allowed for src, dst in binds), binds
    assert {src for src, _ in pairs(a, "--bind")} == {work, step + "/home"}
    home = str(host["home"])
    assert not any(x.startswith((home + "/.ssh", home + "/.config", home + "/.claude")) for x in a)
    assert not any(x.endswith(".env") for x in a)


@linux
@pytest.mark.parametrize("case", ["bad mode", "no cli", "no step folder", "no token", "wide mode", "empty token"])
def test_a_usage_error_exits_2_and_starts_no_bwrap(host, case):
    cmd = ["bash", str(WRAP), "write", str(host["cli"]), "-p"]
    if case == "bad mode":
        cmd[2] = "rw"
    elif case == "no cli":
        cmd = cmd[:3]
    elif case == "no step folder":
        (host["step"] / "inputs").rmdir()
    elif case == "no token":
        host["tf"].unlink()
    elif case == "wide mode":
        os.chmod(host["tf"], 0o644)
    elif case == "empty token":
        host["tf"].write_text("\n", encoding="utf-8")
    r = subprocess.run(cmd, cwd=host["work"], env=host["env"], capture_output=True, text=True)
    assert r.returncode == 2 and r.stderr.startswith("wrap-step: "), (case, r.stderr)
    assert not (host["out"] / "argv").exists()
    assert host["token"] not in r.stderr


def test_the_wrap_is_generic_and_portable():
    text = WRAP.read_text(encoding="utf-8")
    assert "\r" not in text and "grep -P" not in text and "declare -A" not in text
    assert "/home/" not in text


# --- the inputs of a wrapped step ---

def test_a_wrapped_step_gets_a_new_step_folder_and_copies_of_its_inputs(tmp_path, monkeypatch):
    cfg, personas, r = start(tmp_path, monkeypatch)
    cfg["runners"]["fake-builder"]["wrap"] = [runner_fakes.install_wrap(monkeypatch, tmp_path)]
    st = state.load(cfg, "7")
    answer = tmp_path / "answer.md"
    answer.write_text("an answer\n", encoding="utf-8")
    st["answers"] = [str(answer)]
    state.save(cfg, st)
    step_dir = machine.step_dir(st)
    (step_dir / "home").mkdir(parents=True)
    (step_dir / "home" / "old-transcript").write_text("x", encoding="utf-8")  # of an earlier step
    r.add("spec-checker", "SPEC-CHECK 7: GO").add("builder", "BUILD: BLOCKED - stop here")
    st = machine.run_next(cfg, personas, "7")
    assert step_dir == Path(cfg["work"]["home"]) / "7.step"
    assert list((step_dir / "home").iterdir()) == []
    inputs = step_dir / "inputs"
    assert sorted(p.name for p in inputs.iterdir()) == ["answer-001.md", "reply-001-spec-checker.txt", "spec.md"]
    assert (inputs / "spec.md").read_bytes() == Path(st["spec"]["path"]).read_bytes()
    assert (inputs / "reply-001-spec-checker.txt").read_bytes() == Path(machine.reply_path(st["steps"][0])).read_bytes()
    prompt = (Path(st["steps"][1]["run_folder"]) / "prompt.md").read_text(encoding="utf-8")
    assert f"- Spec: {inputs / 'spec.md'}\n" in prompt
    assert f"- Reply of step 1 (spec-checker): {inputs / 'reply-001-spec-checker.txt'}\n" in prompt
    assert f"- Owner answer: {inputs / 'answer-001.md'}\n" in prompt
    assert str(state.home(cfg)) not in prompt[:prompt.index("<!--")]  # no path of the state home
    spec_prompt = (Path(st["steps"][0]["run_folder"]) / "prompt.md").read_text(encoding="utf-8")
    assert f"- Spec: {st['spec']['path']}\n" in spec_prompt  # an unwrapped step reads the state home itself
    assert runner_fakes.wrap_logged(tmp_path)["argv"]  # the step ran in the wrap


def test_the_first_wrapped_step_makes_the_step_folder(tmp_path, monkeypatch):
    """The first build of an item: no <work>.step folder exists yet."""
    cfg, personas, r = start(tmp_path, monkeypatch)
    cfg["runners"]["fake-builder"]["wrap"] = [runner_fakes.install_wrap(monkeypatch, tmp_path)]
    step_dir = machine.step_dir(state.load(cfg, "7"))
    assert not step_dir.exists()
    r.add("spec-checker", "SPEC-CHECK 7: GO").add("builder", "BUILD: BLOCKED - stop here")
    st = machine.run_next(cfg, personas, "7")
    assert [(s["role"], s["status"]) for s in st["steps"]] == [("spec-checker", "OK"), ("builder", "OK")]
    assert list((step_dir / "home").iterdir()) == [] and (step_dir / "inputs" / "spec.md").is_file()
    assert runner_fakes.wrap_logged(tmp_path)["argv"]  # the step ran in the wrap


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="Windows and root ignore the mode of a folder")
def test_a_folder_with_no_write_bit_in_the_old_step_home_does_not_stop_the_next_step(tmp_path, monkeypatch):
    """A step can leave a 0555 folder in its home (a go module cache): the next step still gets a new home."""
    cfg, personas, r = start(tmp_path, monkeypatch)
    cfg["runners"]["fake-builder"]["wrap"] = [runner_fakes.install_wrap(monkeypatch, tmp_path)]
    cache = machine.step_dir(state.load(cfg, "7")) / "home" / "cache"
    cache.mkdir(parents=True)
    (cache / "mod.txt").write_text("x", encoding="utf-8")
    cache.chmod(0o555)
    r.add("spec-checker", "SPEC-CHECK 7: GO").add("builder", "BUILD: BLOCKED - stop here")
    st = machine.run_next(cfg, personas, "7")
    assert [s["role"] for s in st["steps"]] == ["spec-checker", "builder"]
    assert list((cache.parent).iterdir()) == []


def _real_bwrap_runs() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        return subprocess.run(["bwrap", "--ro-bind", "/", "/", "true"], capture_output=True).returncode == 0
    except OSError:
        return False


@pytest.mark.skipif(not _real_bwrap_runs(), reason="needs a bwrap that runs (Linux, user namespaces)")
def test_the_real_wrapper_passes_the_probe_and_a_commit_works(host):
    """The probe of process/sandbox.md inside the real bwrap, with a dummy token: the token file,
    ~/.ssh and the state home are absent, the token is set, and git commits in the work folder."""
    env = {**host["env"], "PATH": "/usr/bin:/bin"}  # the real bwrap, not the fake
    home, work = host["home"], host["work"]
    for d in (".ssh", ".claude", ".local/state/workbench"):
        (home / d).mkdir(parents=True, exist_ok=True)
        (home / d / "secret").write_text("x\n", encoding="utf-8")
    (work / "probe.sh").write_bytes((ROOT / "scripts" / "sandbox-probe.sh").read_bytes())
    probe = subprocess.run(
        ["bash", str(WRAP), "write", "/usr/bin/bash", "probe.sh", "--net", "on", "--work", str(work),
         "--real-home", str(home), "--deny", str(home / ".config"), str(host["tf"]), str(home / ".ssh"),
         str(home / ".claude"), str(home / ".local/state/workbench"), str(ROOT),
         "--allow-env", "CLAUDE_CODE_OAUTH_TOKEN", "--allow-env", "DISABLE_AUTOUPDATER"],
        cwd=work, env=env, capture_output=True, text=True)
    fails = [x for x in probe.stdout.splitlines() if x.startswith("check failed")]
    # a test folder is under /tmp, the private tmpfs of the wrapper, so its parent accepts a write there;
    # a work folder under work.home sits on the read-only root (the probe on the host checks it)
    assert fails in ([], ["check failed: the parent of the work folder is writable"]), probe.stdout + probe.stderr
    assert not fails or str(work).startswith("/tmp/")
    seen = subprocess.run(["bash", str(WRAP), "read", "/usr/bin/env"], cwd=work, env=env, capture_output=True, text=True)
    names = {line.split("=", 1)[0] for line in seen.stdout.splitlines()}
    assert names <= {"PATH", "HOME", "TMPDIR", "LANG", "DISABLE_AUTOUPDATER", "CLAUDE_CODE_OAUTH_TOKEN", "PWD"}, names
    # the whole value is the token: the filter of the output leaves only <redacted>
    assert "CLAUDE_CODE_OAUTH_TOKEN=<redacted>" in seen.stdout.splitlines()
    assert host["token"] not in seen.stdout + seen.stderr
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    commit = subprocess.run(["bash", str(WRAP), "write", "/usr/bin/git", "-c", "user.name=w", "-c",
                             "user.email=w@example.invalid", "commit", "-q", "--allow-empty", "-m", "in the wrapper"],
                            cwd=work, env=env, capture_output=True, text=True)
    assert commit.returncode == 0, commit.stderr
    assert subprocess.run(["git", "log", "-1", "--format=%s"], cwd=work, capture_output=True,
                          text=True).stdout.strip() == "in the wrapper"


# --- an opt-in review token in a build step ---

FAKE_STEP = """#!/usr/bin/env bash
out="$FAKE_BWRAP_OUT"
printf '%s\\n' "$@" > "$out/argv"
env > "$out/env"
cat <&3 | tr '\\0' '\\n' > "$out/fd3"
f="$FAKE_REVIEW_FILE"
[ -f "$f" ] && cat "$f" > "$out/review" && stat -c %a "$f" > "$out/review-mode"
case "$FAKE_STEP" in
    sleep) trap 'kill $!; exit 0' TERM; sleep 30 > /dev/null 2>&1 & wait; exit 0 ;;
    many) seq -f 'line %g of the step' 20000 ;;
    wait) echo 'the first line'; for _ in $(seq 100); do [ -e "$out/go" ] && break; sleep 0.1; done ;;
    print) r=$(cat "$f"); c=$(sed -n 3p "$out/fd3")
           printf 'a%sb\\n%s\\n plain line\\t \\\\ %%s\\r\\377\\nx\\0y%s\\0\\n%s%s.' "$r" "$c" "$c" "$r" "$c"
           printf 'a%sb\\n%s\\n plain line\\t \\\\ %%s\\r\\377\\nx\\0y%s\\0\\n%s%s.' "$r" "$c" "$c" "$r" "$c" >&2 ;;
esac
exit "${FAKE_EXIT:-7}"
"""


def fake_step(h, **env) -> Path:
    """The fake bwrap of a step that records the review token file while it runs, and a python3 that records the
    argv of the output filter; returns the path of the file."""
    fakebin = h["out"].parent / "fakebin"
    (fakebin / "bwrap").write_text(FAKE_STEP, encoding="utf-8")
    (fakebin / "python3").write_text(f'#!/usr/bin/env bash\nprintf \'%s\\n\' "$@" >> "$FAKE_BWRAP_OUT/filter-argv"\n'
                                     f'exec {shutil.which("python3")} "$@"\n', encoding="utf-8")
    os.chmod(fakebin / "python3", 0o755)
    rf = h["step"] / "home" / ".config" / "workbench" / "review-token"
    h["env"].update(FAKE_REVIEW_FILE=str(rf), **env)
    return rf


def review_token(h) -> str:
    """A review token file beside the claude-token, with a fake value made at run time."""
    value = "wbreview" + uuid.uuid4().hex
    rf = h["home"] / ".config" / "workbench" / "review-token"
    rf.write_text(value + "\n", encoding="utf-8")
    os.chmod(rf, 0o600)
    return value


def wrap_review(h, *args):
    return wrap(h, "write", "--review-token", *(args or (str(h["cli"]), "-p", "--model", "m")))


@linux
def test_the_review_token_flag_is_optional_and_leaves_the_bwrap_argv_unchanged(host):
    review_token(host)
    assert wrap(host).returncode == 7
    plain = argv(host)
    r = wrap_review(host)
    assert r.returncode == 7, r.stderr
    assert argv(host) == plain
    assert plain[plain.index("--") + 1:] == [str(host["cli"]), "-p", "--model", "m"]


@linux
def test_the_review_token_flag_in_read_mode_exits_2_and_starts_no_bwrap(host):
    value = review_token(host)
    r = wrap(host, "read", "--review-token", str(host["cli"]), "-p")
    assert r.returncode == 2 and r.stderr.startswith("wrap-step: "), r.stderr
    assert not (host["out"] / "argv").exists()
    assert value not in r.stderr and host["token"] not in r.stderr


@linux
def test_a_review_token_flag_after_the_cli_goes_to_the_cli(host):
    assert wrap(host, "write", str(host["cli"]), "--review-token").returncode == 7
    a = argv(host)
    assert a[a.index("--") + 1:] == [str(host["cli"]), "--review-token"]


@linux
def test_the_review_token_goes_to_a_600_file_in_the_step_home(host):
    value = review_token(host)
    fake_step(host)
    assert wrap_review(host).returncode == 7
    assert (host["out"] / "review").read_text() == value + "\n"
    assert (host["out"] / "review-mode").read_text().strip() == "600"


@linux
def test_the_review_token_is_in_no_argv_and_no_environment_of_the_step(host):
    value = review_token(host)
    fake_step(host)
    assert wrap_review(host).returncode == 7
    assert all(value not in x for x in argv(host))
    assert value not in (host["out"] / "env").read_text()
    filter_argv = (host["out"] / "filter-argv").read_text()  # the filter of stdout and stderr ran
    assert value not in filter_argv and host["token"] not in filter_argv
    assert (host["out"] / "fd3").read_text().splitlines() == ["--setenv", "CLAUDE_CODE_OAUTH_TOKEN", host["token"]]


@linux
@pytest.mark.parametrize("case", ["no review token", "wide review mode", "empty review token"])
def test_a_bad_review_token_file_exits_2_and_starts_no_bwrap(host, case):
    value = review_token(host)
    rf = host["home"] / ".config" / "workbench" / "review-token"
    if case == "no review token":
        rf.unlink()
    elif case == "wide review mode":
        os.chmod(rf, 0o644)
    elif case == "empty review token":
        rf.write_text("\n", encoding="utf-8")
    r = wrap_review(host)
    assert r.returncode == 2 and r.stderr.startswith("wrap-step: "), (case, r.stderr)
    assert not (host["out"] / "argv").exists()
    assert not (host["step"] / "home" / ".config").exists()
    assert value not in r.stderr and host["token"] not in r.stderr


@linux
def test_no_python3_for_the_filter_exits_2_and_starts_no_bwrap(host):
    """With no reader of the output, the step would hang on its fifos."""
    review_token(host)
    host["env"]["PATH"] = str(host["out"].parent / "fakebin")  # the fake bwrap, and no python3
    r = subprocess.run([shutil.which("bash"), str(WRAP), "write", "--review-token", str(host["cli"])],
                       cwd=host["work"], env=host["env"], capture_output=True, text=True)
    assert (r.returncode, r.stderr) == (2, "wrap-step: no python3 for the filter of the step output\n")
    assert not (host["out"] / "argv").exists()


@linux
@pytest.mark.parametrize("code", [0, 3])
def test_the_review_token_file_is_gone_when_the_step_exits(host, code):
    review_token(host)
    rf = fake_step(host, FAKE_EXIT=str(code))
    assert wrap_review(host).returncode == code
    assert (host["out"] / "review").exists() and not rf.exists()


@linux
def test_the_review_token_file_is_gone_after_a_term_to_the_wrapper(host):
    review_token(host)
    rf = fake_step(host, FAKE_STEP="sleep")
    p = subprocess.Popen(["bash", str(WRAP), "write", "--review-token", str(host["cli"])], cwd=host["work"],
                         env=host["env"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(100):
        if (host["out"] / "review-mode").exists():
            break
        time.sleep(0.05)
    assert rf.exists()
    p.terminate()  # the fake step exits 0 on TERM: the wrapper still exits 143
    p.communicate(timeout=10)
    assert p.returncode == 128 + 15 and not rf.exists()


@linux
def test_the_step_gets_the_stdin_of_the_wrapper(host):
    review_token(host)
    fake_step(host)
    (host["out"].parent / "fakebin" / "bwrap").write_text(FAKE_STEP.replace("exit ", "cat > \"$out/stdin\"; exit "),
                                                         encoding="utf-8")
    r = subprocess.run(["bash", str(WRAP), "write", "--review-token", str(host["cli"])], cwd=host["work"],
                       env=host["env"], input="the prompt\n", capture_output=True, text=True)
    assert r.returncode == 7, r.stderr
    assert (host["out"] / "stdin").read_text() == "the prompt\n"


@linux
def test_the_step_output_has_both_tokens_redacted_and_the_rest_byte_for_byte(host):
    """The fake step prints `a<review>b`, the claude-token, a plain line, a line with NUL bytes and both values
    with no last newline."""
    value = review_token(host)
    fake_step(host, FAKE_STEP="print", FAKE_EXIT="3")
    r = subprocess.run(["bash", str(WRAP), "write", "--review-token", str(host["cli"])], cwd=host["work"],
                       env=host["env"], capture_output=True)
    assert r.returncode == 3
    want = b"a<redacted>b\n<redacted>\n plain line\t \\ %s\r\xff\nx\0y<redacted>\0\n<redacted><redacted>."
    assert r.stdout == want and r.stderr == want
    assert value.encode() not in r.stdout + r.stderr and host["token"].encode() not in r.stdout + r.stderr


def test_rule_2_of_the_sandbox_has_the_review_token_exception_and_the_header_names_the_flag():
    rule2 = next(x for x in (ROOT / "process" / "sandbox.md").read_text(encoding="utf-8").splitlines()
                 if x.startswith("2. **Files.**"))
    assert rule2.endswith("logs and live repositories are absent, or empty. Exception: with `--review-token`, a build "
                          "or rework step holds 1 review token in a file of its step home; the wrapper deletes the "
                          "file when the step ends, and the runner deletes it after a timeout or a reboot.")
    header = WRAP.read_text(encoding="utf-8").split("\nset -u\n")[0]
    assert "--review-token" in header


@linux
def test_the_output_is_complete_when_the_wrapper_exits(host, tmp_path):
    """The runner reads the output files right after the exit: the wrapper waits for both filters."""
    review_token(host)
    fake_step(host, FAKE_STEP="many")
    with open(tmp_path / "stdout", "wb") as out:
        code = subprocess.run(["bash", str(WRAP), "write", "--review-token", str(host["cli"])], cwd=host["work"],
                              env=host["env"], stdout=out, stderr=subprocess.DEVNULL).returncode
        size = os.fstat(out.fileno()).st_size
    assert code == 7
    assert size == len("".join(f"line {i} of the step\n" for i in range(1, 20001)))


@linux
def test_the_delete_does_not_follow_a_symlink_that_the_step_made(host, tmp_path):
    """A step that makes its .config/workbench a symlink to a host folder: the host's review-token stays."""
    review_token(host)
    rf = fake_step(host, FAKE_EXIT="0")
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "review-token").write_text("keep\n", encoding="utf-8")
    bwrap = host["out"].parent / "fakebin" / "bwrap"
    bwrap.write_text(FAKE_STEP.replace('exit "', f'rm -r "${{f%/*}}"; ln -s {victim} "${{f%/*}}"; exit "'),
                     encoding="utf-8")
    assert wrap_review(host).returncode == 0
    assert rf.parent.is_symlink() and (victim / "review-token").read_text() == "keep\n"


# --- the filter streams, and the fifo folder goes ---

@linux
def test_the_filter_passes_a_line_before_the_step_exits(host):
    """The fake step prints a line, then waits up to 10 s for the file go, which the test makes once it has read
    the line."""
    review_token(host)
    fake_step(host, FAKE_STEP="wait", FAKE_EXIT="0")
    p = subprocess.Popen(["bash", str(WRAP), "write", "--review-token", str(host["cli"])], cwd=host["work"],
                         env=host["env"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    got, deadline = b"", time.monotonic() + 5
    try:
        while b"\n" not in got and select.select([p.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
            chunk = os.read(p.stdout.fileno(), 4096)
            if not chunk:
                break
            got += chunk
        assert (got, p.poll()) == (b"the first line\n", None)
    finally:
        (host["out"] / "go").touch()
        p.communicate(timeout=20)
    assert p.returncode == 0


@linux
@pytest.mark.parametrize("end", ["exit 0", "exit 3", "TERM"])
def test_the_fifo_folder_is_gone_when_the_wrapper_exits(host, tmp_path, end):
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    host["env"]["TMPDIR"] = str(tmp)
    review_token(host)
    if end != "TERM":
        fake_step(host, FAKE_EXIT=end.split()[1])
        assert wrap_review(host).returncode == int(end.split()[1])
    else:
        fake_step(host, FAKE_STEP="sleep")
        p = subprocess.Popen(["bash", str(WRAP), "write", "--review-token", str(host["cli"])], cwd=host["work"],
                             env=host["env"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(100):
            if (host["out"] / "review-mode").exists():
                break
            time.sleep(0.05)
        assert (host["out"] / "review-mode").exists() and list(tmp.iterdir())  # the step runs, and its fifos exist
        p.terminate()
        p.communicate(timeout=10)
        assert p.returncode == 128 + 15
    assert list(tmp.iterdir()) == []


# --- review: the stream stores tool results, so each mode redacts ---

@linux
@pytest.mark.parametrize("mode", ["write", "read"])
def test_the_step_output_has_the_token_redacted_with_no_review_token(host, mode):
    """With no --review-token, the fake step prints the claude-token (and an empty file for the review token) as in
    test_the_step_output_has_both_tokens_redacted_and_the_rest_byte_for_byte."""
    fake_step(host, FAKE_STEP="print", FAKE_EXIT="3")
    (host["out"] / "no-review").write_bytes(b"")
    host["env"]["FAKE_REVIEW_FILE"] = str(host["out"] / "no-review")
    r = subprocess.run(["bash", str(WRAP), mode, str(host["cli"])], cwd=host["work"], env=host["env"],
                       capture_output=True)
    leaked = host["token"].encode() in r.stdout + r.stderr
    assert not leaked, "the token is in the output"  # first, and with no value: a failure prints no token
    assert r.returncode == 3
    want = b"ab\n<redacted>\n plain line\t \\ %s\r\xff\nx\0y<redacted>\0\n<redacted>."
    assert r.stdout == want and r.stderr == want


@linux
def test_no_python3_for_the_filter_exits_2_with_no_review_token(host):
    host["env"]["PATH"] = str(host["out"].parent / "fakebin")  # the fake bwrap, and no python3
    r = subprocess.run([shutil.which("bash"), str(WRAP), "read", str(host["cli"])],
                       cwd=host["work"], env=host["env"], capture_output=True, text=True)
    assert (r.returncode, r.stderr) == (2, "wrap-step: no python3 for the filter of the step output\n")
    assert not (host["out"] / "argv").exists()
