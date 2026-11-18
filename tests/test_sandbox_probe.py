"""scripts/sandbox-probe.sh checks the sandbox rules from inside a
wrapper. Each test plants one fault in a temp tree and expects FAIL; the clean
tree gets PASS. A stub `sudo` is first on PATH, `--proc` points at a fake /proc
(the seam of the spec), and a planted value is built at run time and must never
appear in the output.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT

SCRIPT = ROOT / "scripts" / "sandbox-probe.sh"
# Git for Windows adds these variables to every bash it starts.
PLATFORM_ENV = (["MSYSTEM", "SYSTEMROOT", "WINDIR", "EXEPATH", "PLINK_PROTOCOL", "TERM"]
                if sys.platform == "win32" else [])

NET_HEAD = (
    "Inter-|   Receive                                                |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
)
NET_LO = "    lo:       0       0    0    0    0     0          0         0        0       0    0    0    0     0       0          0\n"
NET_ETH = NET_LO.replace("    lo:", "  eth0:")


def planted() -> str:
    """A value that no file of this repo holds: built at run time."""
    return "pl" + "ant-" + secrets.token_hex(8)


class Tree:
    """A clean temp tree: every check of the probe passes, except that the
    parent of the work folder is writable (a temp folder cannot be locked on
    each system; the `ro_parent` fixture locks it)."""

    def __init__(self, base: Path, bash: str):
        self.bash = bash
        self.base = base
        self.parent = base / "parent"
        self.work = self.parent / "work"
        self.home = base / "home"
        self.real_home = base / "real-home"
        self.keys_absent = base / "keys-absent"
        self.keys_empty = base / "keys-empty"
        self.proc = base / "proc"
        self.stubs = base / "stubs"
        for d in (self.work, self.home, self.real_home, self.keys_empty,
                  self.proc / "self", self.proc / "net", self.stubs):
            d.mkdir(parents=True)
        self.status("1")
        self.net_dev(NET_LO)
        self.sudo("exit 1")

    def status(self, value: str | None) -> None:
        f = self.proc / "self" / "status"
        if value is None:
            f.unlink(missing_ok=True)
        else:
            f.write_text(f"Name:\tbash\nNoNewPrivs:\t{value}\nSeccomp:\t0\n", newline="\n")

    def net_dev(self, lines: str | None) -> None:
        f = self.proc / "net" / "dev"
        if lines is None:
            f.unlink(missing_ok=True)
        else:
            f.write_text(NET_HEAD + lines, newline="\n")

    def sudo(self, body: str) -> None:
        f = self.stubs / "sudo"
        f.write_text("#!/bin/sh\n" + body + "\n", newline="\n")
        f.chmod(0o755)

    def args(self, net: str = "off") -> list[str]:
        a = ["--net", net, "--work", self.work.as_posix(), "--real-home", self.real_home.as_posix(),
             "--deny", self.keys_absent.as_posix(), self.keys_empty.as_posix(),
             "--proc", self.proc.as_posix()]
        for name in PLATFORM_ENV:
            a += ["--allow-env", name]
        return a

    def env(self, **extra: str) -> dict[str, str]:
        env = {"PATH": self.stubs.as_posix() + os.pathsep + os.environ["PATH"],
               "HOME": self.home.as_posix(), "LANG": "C"}
        if "SYSTEMROOT" in os.environ:
            env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        env.update(extra)
        return env

    def run(self, args: list[str] | None = None, env: dict[str, str] | None = None):
        p = subprocess.run(
            [self.bash, SCRIPT.as_posix(), *(self.args() if args is None else args)],
            env=self.env() if env is None else env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return p.returncode, p.stdout, p.stderr


@pytest.fixture
def tree(tmp_path, bash) -> Tree:
    return Tree(tmp_path / "tree", bash)


def _lock(path: Path) -> bool:
    """Lock `path` against a new file; False where chmod cannot stop a write."""
    path.chmod(0o555)
    probe = path / "chmod-probe"
    try:
        probe.write_text("x")
    except OSError:
        return True
    probe.unlink()
    path.chmod(0o755)
    return False


@pytest.fixture
def ro_parent(tree):
    if not _lock(tree.parent):
        pytest.skip("chmod cannot stop a write here (Windows, root, or a file system without modes)")
    yield tree
    tree.parent.chmod(0o755)


def fail_lines(out: str) -> list[str]:
    """The lines before the result line; a run with no result line is an error."""
    lines = out.strip().splitlines()
    assert lines and re.fullmatch(r"SANDBOX: (PASS|FAIL - \d+ checks)", lines[-1]), out
    return lines[:-1]


def last(out: str) -> str:
    return out.strip().splitlines()[-1]


def test_script_style():
    raw = SCRIPT.read_bytes()
    assert b"\r" not in raw
    text = raw.decode("utf-8")
    assert text.startswith("#!/usr/bin/env bash\n")
    assert not re.search(r"grep -[A-Za-z]*P|declare -A|mapfile|readarray|local -n", text)


def test_clean_tree_passes(ro_parent):
    rc, out, err = ro_parent.run()
    assert (rc, out) == (0, "SANDBOX: PASS\n"), out + err


def test_clean_tree_fails_only_on_the_writable_parent(tree):
    """The portable twin of the PASS test: every other check is clean."""
    rc, out, _ = tree.run()
    assert (rc, out) == (
        1, "check failed: the parent of the work folder is writable\nSANDBOX: FAIL - 1 checks\n")


def test_clean_tree_passes_when_no_deny_path_exists_and_net_is_on(ro_parent):
    ro_parent.net_dev(NET_LO + NET_ETH)
    rc, out, _ = ro_parent.run(ro_parent.args("on"))
    assert (rc, out) == (0, "SANDBOX: PASS\n")


# --- the sudo check -------------------------------------------------------

def test_sudo_that_succeeds_fails(tree):
    tree.sudo("exit 0")
    rc, out, _ = tree.run()
    assert rc == 1 and any("sudo" in l for l in fail_lines(out)), out


def test_sudo_output_is_not_printed(tree):
    secret = planted()
    tree.sudo(f"echo {secret}; echo {secret} >&2; exit 0")
    rc, out, err = tree.run()
    assert rc == 1 and secret not in out + err


# --- NoNewPrivs -----------------------------------------------------------

@pytest.mark.parametrize("value", ["0", "10", None])
def test_no_new_privs_not_1_fails(tree, value):
    tree.status(value)
    rc, out, _ = tree.run()
    assert rc == 1 and any("NoNewPrivs" in l for l in fail_lines(out)), out


# --- the deny paths -------------------------------------------------------

def test_deny_file_with_content_fails_and_is_not_printed(tree):
    secret = planted()
    (tree.base / "id_key").write_text(secret + "\n")
    args = tree.args()
    args[args.index("--deny") + 1] = (tree.base / "id_key").as_posix()
    rc, out, err = tree.run(args)
    assert rc == 1 and any("deny path 1" in l for l in fail_lines(out)), out
    assert secret not in out + err


def test_deny_folder_with_a_file_fails_and_a_hidden_file_counts(tree):
    secret = planted()
    (tree.keys_empty / ("." + secret)).write_text(secret)
    rc, out, err = tree.run()
    assert rc == 1 and any("deny path 2" in l for l in fail_lines(out)), out
    assert secret not in out + err


def test_each_failed_deny_path_has_its_own_line(tree):
    (tree.keys_empty / "k").write_text("x")
    (tree.base / "k2").write_text("x")
    args = tree.args() + ["--deny", (tree.base / "k2").as_posix()]
    rc, out, _ = tree.run(args)
    assert len([l for l in fail_lines(out) if "deny path" in l]) == 2, out


def test_deny_empty_file_and_dev_null_pass(tree):
    (tree.base / "empty").write_text("")
    args = tree.args() + ["--deny", (tree.base / "empty").as_posix(), "/dev/null"]
    rc, out, _ = tree.run(args)
    assert not any("deny path" in l for l in fail_lines(out)), out


# --- write in the work folder, none in its parent -------------------------

def test_work_folder_that_cannot_be_written_fails(tree):
    tree.work.chmod(0o555)
    try:
        try:
            (tree.work / "x").write_text("x")
        except OSError:
            pass
        else:
            (tree.work / "x").unlink()
            pytest.skip("chmod cannot stop a write here (Windows, root, or a file system without modes)")
        rc, out, _ = tree.run()
        assert rc == 1 and "check failed: cannot write in the work folder" in fail_lines(out), out
    finally:
        tree.work.chmod(0o755)


def test_probe_leaves_no_file_in_the_work_folder(tree):
    fail_lines(tree.run()[1])
    assert list(tree.work.iterdir()) == []


def test_writable_parent_fails_and_leaves_no_file(tree):
    rc, out, _ = tree.run()
    assert rc == 1 and any("parent" in l for l in fail_lines(out)), out
    assert sorted(p.name for p in tree.parent.iterdir()) == ["work"]


def test_read_only_parent_is_not_reported(ro_parent):
    rc, out, _ = ro_parent.run()
    assert not any("parent" in l for l in fail_lines(out)), out


def test_relative_work_folder_checks_the_real_parent(ro_parent):
    args = ro_parent.args()
    args[args.index("--work") + 1] = "."
    p = subprocess.run([ro_parent.bash, SCRIPT.as_posix(), *args], env=ro_parent.env(),
                       cwd=ro_parent.work, capture_output=True, text=True)
    assert (p.returncode, p.stdout) == (0, "SANDBOX: PASS\n"), p.stdout


# --- the network ----------------------------------------------------------

@pytest.mark.parametrize("extra", [
    NET_ETH, NET_LO + NET_ETH, NET_LO + NET_ETH.replace("eth0", "wlan0"),
    NET_ETH.replace("eth0", "wlo1"), NET_ETH.replace("eth0", "lo0"),  # a name that holds "lo" is not lo
])
def test_net_off_with_another_interface_fails(tree, extra):
    tree.net_dev(extra)
    rc, out, _ = tree.run()
    assert rc == 1 and any("network" in l for l in fail_lines(out)), out


def test_net_off_without_a_net_dev_file_fails(tree):
    tree.net_dev(None)
    rc, out, _ = tree.run()
    assert rc == 1 and any("network" in l for l in fail_lines(out)), out


def test_net_off_with_only_lo_or_no_interface_has_no_network_line(tree):
    for lines in (NET_LO, ""):
        tree.net_dev(lines)
        _, out, _ = tree.run()
        assert not any("network" in l for l in fail_lines(out)), out


def test_net_on_does_not_check_the_network(tree):
    tree.net_dev(NET_LO + NET_ETH)
    _, out, _ = tree.run(tree.args("on"))
    assert not any("network" in l for l in fail_lines(out)), out


# --- the environment ------------------------------------------------------

def test_extra_variable_fails_and_its_value_is_not_printed(tree):
    secret = planted()
    name = "PL" + "ANT_" + secrets.token_hex(3).upper()  # built at run time
    rc, out, err = tree.run(env=tree.env(**{name: secret}))
    assert rc == 1 and any(name in l for l in fail_lines(out)), out
    assert secret not in out + err


def test_variable_on_the_fixed_list_or_allowed_passes(tree):
    env = tree.env(TMPDIR=tree.base.as_posix(), XDG_CACHE_HOME="x")
    _, out, _ = tree.run(env=env)
    assert "XDG_CACHE_HOME" in out and "TMPDIR" not in out
    _, out, _ = tree.run(tree.args() + ["--allow-env", "XDG_CACHE_HOME"], env=env)
    assert not any("variable" in l for l in fail_lines(out)), out


def test_exported_variable_with_a_new_line_in_its_value_is_one_name(tree):
    secret = planted()
    _, out, err = tree.run(env=tree.env(PLANT_ML=secret + "\nPATH2=1"))
    assert fail_lines(out) and "PATH2" not in out and secret not in out + err


# --- HOME -----------------------------------------------------------------

def test_home_equal_to_real_home_fails(tree):
    for home in (tree.real_home.as_posix(), tree.real_home.as_posix() + "/"):
        rc, out, _ = tree.run(env=tree.env(HOME=home))
        assert rc == 1 and any("HOME" in l for l in fail_lines(out)), out


@pytest.mark.skipif(sys.platform == "win32", reason="Git for Windows rewrites HOME to a /c/ path")
def test_home_equal_to_a_real_home_that_does_not_exist_fails(tree):
    ghost = (tree.base / "no-such-home").as_posix()
    args = tree.args()
    args[args.index("--real-home") + 1] = ghost
    _, out, _ = tree.run(args, env=tree.env(HOME=ghost))
    assert any("HOME" in l for l in fail_lines(out)), out


def test_home_that_links_to_real_home_fails(tree):
    link = tree.base / "link-home"
    try:
        link.symlink_to(tree.real_home, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("cannot make a symbolic link here")
    _, out, _ = tree.run(env=tree.env(HOME=link.as_posix()))
    assert any("HOME" in l for l in fail_lines(out)), out


@pytest.mark.skipif(sys.platform == "win32", reason="Git for Windows sets HOME itself")
def test_home_that_is_not_set_fails(tree):
    env = tree.env()
    del env["HOME"]
    _, out, _ = tree.run(env=env)
    assert any("HOME" in l for l in fail_lines(out)), out


def test_other_home_has_no_home_line(tree):
    _, out, _ = tree.run()
    assert not any("HOME" in l for l in fail_lines(out)), out


# --- the result lines and the exit codes ----------------------------------

def test_the_final_line_counts_the_failed_checks(tree):
    tree.sudo("exit 0")
    tree.status("0")
    (tree.keys_empty / "k").write_text("x")
    rc, out, _ = tree.run()
    lines = fail_lines(out)
    assert rc == 1
    assert last(out) == f"SANDBOX: FAIL - {len(lines)} checks"
    assert len(lines) >= 3
    assert all(l.startswith("check failed: ") for l in lines)


def test_pass_has_no_other_line(ro_parent):
    _, out, _ = ro_parent.run()
    assert out.splitlines() == ["SANDBOX: PASS"]


USAGE_ERRORS = {
    "no arguments": [],
    "no --net": ["--work", "W", "--real-home", "R", "--deny", "D"],
    "bad --net": ["--net", "maybe", "--work", "W", "--real-home", "R", "--deny", "D"],
    "no --work": ["--net", "off", "--real-home", "R", "--deny", "D"],
    "no --real-home": ["--net", "off", "--work", "W", "--deny", "D"],
    "no --deny": ["--net", "off", "--work", "W", "--real-home", "R"],
    "empty --deny": ["--net", "off", "--work", "W", "--real-home", "R", "--deny", "--allow-env", "X"],
    "unknown option": ["--net", "off", "--work", "W", "--real-home", "R", "--deny", "D", "--bogus"],
    "a value is missing": ["--net", "off", "--work", "W", "--real-home"],
    "bad --allow-env name": ["--net", "off", "--work", "W", "--real-home", "R", "--deny", "D", "--allow-env", "A B"],
    "--allow-env name with a new line": ["--net", "off", "--work", "W", "--real-home", "R", "--deny", "D", "--allow-env", "X" + chr(10) + "Y Z"],
    "--allow-env name that starts with a digit": ["--net", "off", "--work", "W", "--real-home", "R", "--deny", "D", "--allow-env", "1X"],
    "a stray word": ["--net", "off", "--work", "W", "--real-home", "R", "--deny", "D", "--allow-env", "X", "extra"],
}


@pytest.mark.parametrize("name", sorted(USAGE_ERRORS))
def test_usage_error_exits_2(tree, name):
    args = [tree.work.as_posix() if a == "W" else a for a in USAGE_ERRORS[name]]
    rc, out, err = tree.run(args)
    assert rc == 2, (out, err)
    assert "SANDBOX:" not in out and "usage" in err


def test_work_folder_that_does_not_exist_exits_2(tree):
    args = tree.args()
    args[args.index("--work") + 1] = (tree.base / "nope").as_posix()
    rc, out, err = tree.run(args)
    assert rc == 2 and "SANDBOX:" not in out


# --- a real wrapper passes ----------------------------------

def _bwrap_reason() -> str | None:
    """None where bwrap starts a minimal sandbox; else the reason to skip."""
    exe = shutil.which("bwrap")
    if sys.platform == "win32" or exe is None:
        return "bwrap is not installed here"
    try:
        p = subprocess.run([exe, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "true"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"bwrap does not start here: {e}"
    return None if p.returncode == 0 else "bwrap does not start here: " + p.stderr.strip()[:200]


def _host_has_a_network() -> bool:
    try:
        text = Path("/proc/net/dev").read_text()
    except OSError:
        return False
    return any(l.split(":")[0].strip() not in ("", "lo") for l in text.splitlines() if ":" in l)


def bwrap_probe(tree: Tree, *wrapper: str, deny: list[str] | None = None, net: str = "off"):
    """Run the probe in a minimal bwrap: a read-only system, the work folder
    bound, a cleared environment, and the options in `wrapper`."""
    w = tree.work.as_posix()
    cmd = [shutil.which("bwrap"), "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
           "--bind", w, w, "--clearenv", "--setenv", "PATH", os.environ["PATH"],
           "--setenv", "HOME", tree.home.as_posix(), "--die-with-parent", *wrapper, "--",
           tree.bash, SCRIPT.as_posix(), "--net", net, "--work", w,
           "--real-home", tree.real_home.as_posix(),
           "--deny", *(deny or [tree.keys_absent.as_posix()])]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout, p.stderr


@pytest.fixture
def wrapped(tree):
    reason = _bwrap_reason()
    if reason:
        pytest.skip(reason)
    return tree


def test_minimal_bwrap_passes(wrapped):
    rc, out, err = bwrap_probe(wrapped, "--unshare-net")
    assert (rc, out) == (0, "SANDBOX: PASS\n"), out + err


def test_minimal_bwrap_with_the_network_shared_fails(wrapped):
    if not _host_has_a_network():
        pytest.skip("this run has no network interface other than lo (it runs in a sandbox itself)")
    rc, out, _ = bwrap_probe(wrapped, "--share-net")
    assert rc == 1 and any("network" in l for l in fail_lines(out)), out
    rc, out, _ = bwrap_probe(wrapped, "--share-net", net="on")
    assert (rc, out) == (0, "SANDBOX: PASS\n")


def test_bwrap_hides_a_key_folder_with_a_tmpfs(wrapped):
    secret = planted()
    keys = wrapped.base / "keys"
    keys.mkdir()
    (keys / "id_key").write_text(secret)
    rc, out, err = bwrap_probe(wrapped, "--unshare-net", deny=[keys.as_posix()])
    assert rc == 1 and any("deny path 1" in l for l in fail_lines(out)), out
    assert secret not in out + err
    rc, out, err = bwrap_probe(wrapped, "--unshare-net", "--tmpfs", keys.as_posix(), deny=[keys.as_posix()])
    assert (rc, out) == (0, "SANDBOX: PASS\n"), out + err


def test_work_folder_that_cannot_be_entered_exits_2(tree):
    tree.work.chmod(0o000)
    try:
        if os.access(tree.work, os.X_OK):
            pytest.skip("chmod cannot stop an entry here (Windows, root, or a file system without modes)")
        rc, out, _ = tree.run()
        assert rc == 2 and "SANDBOX:" not in out
    finally:
        tree.work.chmod(0o755)
