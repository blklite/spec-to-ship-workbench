"""AC 2: scripts/secret-scan.sh finds each class in the tree, in history and in
commit messages, prints only `commit:path:line class`, and exits 0, 1 or 2.

Every planted value is built at run time from pieces, so no file of this repo
holds a real pattern hit (the scan of this repo itself stays clean).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from conftest import ROOT

SCRIPT = (ROOT / "scripts" / "secret-scan.sh").as_posix()
GIT_ID = ["-c", "user.name=test", "-c", "user.email=test@example.invalid"]

# class -> a value of that class, assembled at run time
GENERIC = {
    "user-path": "/ho" + "me/" + "plantuser",
    "email": "plant.person" + "@" + "mail-host" + ".net",
    "tailscale-ip": "100" + ".101.7.9",
    "tailscale-host": "box" + ".tail1234" + ".ts" + ".net",
    "supabase-host": "abcdefghijklmnopqrst" + ".supa" + "base.co",
    "supabase-key": "sb_" + "secret_" + "A" * 20,
    "jwt": "ey" + "J" + "a" * 10 + ".ey" + "J" + "b" * 10 + "." + "c" * 10,
    "github-token": "gh" + "p_" + "A" * 36,
    "anthropic-key": "sk-" + "ant-" + "x" * 24,
    "openrouter-key": "sk-" + "or-v1-" + "a" * 40,
    "ado-token": "q" * 52,
}

# private classes come from a temp denylist made by the test
PRIVATE = {"person": "Zorb" + "laxa", "host": "quux" + "server9"}


def _patterns_classes() -> set[str]:
    out = set()
    for line in (ROOT / "scripts" / "patterns.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            cls = line.split(" ", 1)[0]
            if cls != "allow":
                out.add(cls)
    return out


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *GIT_ID, *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "README.md").write_text("clean start\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "start")
    return repo


def denylist_file(tmp_path: Path) -> Path:
    p = tmp_path / "denylist.txt"
    p.write_text("# test list\n" + "".join(f"{c} {w}\n" for c, w in PRIVATE.items()))
    return p


def scan(bash: str, repo: Path, denylist: Path | None = None):
    env = {k: v for k, v in os.environ.items() if k != "WORKBENCH_DENYLIST"}
    if denylist is not None:
        env["WORKBENCH_DENYLIST"] = str(denylist)
    r = subprocess.run(
        [bash, SCRIPT, repo.as_posix()], capture_output=True, text=True, env=env
    )
    return r.returncode, r.stdout, r.stderr


def test_every_shipped_class_is_tested():
    assert _patterns_classes() == set(GENERIC)


CASES = [(c, v, None) for c, v in GENERIC.items()] + [
    (f"private:{c}", v, "deny") for c, v in PRIVATE.items()
]


@pytest.mark.parametrize("cls,value,deny", CASES, ids=[c[0] for c in CASES])
def test_class_found_in_tree_history_and_message(bash, tmp_path, cls, value, deny):
    repo = make_repo(tmp_path)
    # an older commit holds the value; a later commit deletes it again
    (repo / "old.txt").write_text(f"line one\nsome text {value} here\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "add old")
    old = git(repo, "rev-parse", "HEAD")
    git(repo, "rm", "-q", "old.txt")
    git(repo, "commit", "-q", "-m", f"tidy\n\nnote {value}")
    msg = git(repo, "rev-parse", "HEAD")
    # and the working tree holds it in an untracked file
    (repo / "new.txt").write_text(f"{value}\n")

    rc, out, err = scan(bash, repo, denylist_file(tmp_path) if deny else None)
    assert rc == 1, (out, err)
    lines = set(out.splitlines())
    assert f"{old}:old.txt:2 {cls}" in lines
    assert f"WORKTREE:new.txt:1 {cls}" in lines
    assert f"{msg}:COMMIT_MSG:3 {cls}" in lines
    # the matched text is never printed
    assert value not in out and value not in err


def test_clean_repo_exits_0(bash, tmp_path):
    repo = make_repo(tmp_path)
    rc, out, err = scan(bash, repo, denylist_file(tmp_path))
    assert (rc, out) == (0, ""), err


def test_missing_denylist_exits_2(bash, tmp_path):
    repo = make_repo(tmp_path)
    rc, out, err = scan(bash, repo, tmp_path / "no-such-list.txt")
    assert rc == 2
    assert out == ""


def test_private_words_are_whole_word_and_case_insensitive(bash, tmp_path):
    repo = make_repo(tmp_path)
    word = PRIVATE["person"]
    (repo / "a.txt").write_text(f"{word}ian is a longer word\n")
    rc, out, _ = scan(bash, repo, denylist_file(tmp_path))
    assert rc == 0, out
    (repo / "b.txt").write_text(f"x {word.upper()}.\n")
    rc, out, _ = scan(bash, repo, denylist_file(tmp_path))
    assert rc == 1 and "WORKTREE:b.txt:1 private:person" in out


def test_placeholders_and_trailer_are_allowed(bash, tmp_path):
    repo = make_repo(tmp_path)
    (repo / "doc.md").write_text("mail someone" + "@" + "example.com\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "doc\n\nCo-Authored-By: Bot <noreply" + "@" + "anthropic.com>")
    rc, out, err = scan(bash, repo)
    assert (rc, out) == (0, ""), err


def test_allow_rule_does_not_hide_a_real_hit_on_the_same_line(bash, tmp_path):
    repo = make_repo(tmp_path)
    (repo / "doc.md").write_text(
        "a" + "@" + "example.com and " + GENERIC["email"] + "\n"
    )
    rc, out, _ = scan(bash, repo)
    assert rc == 1 and "WORKTREE:doc.md:1 email" in out


def test_author_fields_are_not_scanned(bash, tmp_path):
    repo = make_repo(tmp_path)
    (repo / "x.txt").write_text("plain\n")
    subprocess.run(
        ["git", "-c", "user.name=" + PRIVATE["person"], "-c",
         "user.email=" + GENERIC["email"], "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=" + PRIVATE["person"], "-c",
         "user.email=" + GENERIC["email"], "commit", "-q", "-m", "plain"],
        cwd=repo, check=True)
    rc, out, err = scan(bash, repo, denylist_file(tmp_path))
    assert (rc, out) == (0, ""), err


# --- repairs of the builder's code review ------------------------------------

def test_relative_list_and_pattern_paths_are_read_from_the_callers_folder(bash, tmp_path):
    repo = make_repo(tmp_path)
    (repo / "x.txt").write_text(f"{PRIVATE['host']} {GENERIC['anthropic-key']}\n")
    denylist_file(tmp_path)
    (tmp_path / "p.txt").write_text((ROOT / "scripts" / "patterns.txt").read_text())
    env = {k: v for k, v in os.environ.items() if k != "WORKBENCH_DENYLIST"}
    env.update(WORKBENCH_DENYLIST="denylist.txt", WORKBENCH_PATTERNS="p.txt")
    r = subprocess.run([bash, SCRIPT, "repo"], cwd=tmp_path, capture_output=True, text=True, env=env)
    assert r.returncode == 1, r.stderr
    assert "WORKTREE:x.txt:1 private:host" in r.stdout
    assert "WORKTREE:x.txt:1 anthropic-key" in r.stdout


def test_a_pattern_file_with_no_class_is_an_error(bash, tmp_path):
    repo = make_repo(tmp_path)
    empty = tmp_path / "p.txt"
    empty.write_text("# nothing\n")
    env = {k: v for k, v in os.environ.items() if k != "WORKBENCH_DENYLIST"}
    env["WORKBENCH_PATTERNS"] = str(empty)
    r = subprocess.run([bash, SCRIPT, repo.as_posix()], capture_output=True, text=True, env=env)
    assert r.returncode == 3


def test_example_domain_allow_rule_has_a_boundary(bash, tmp_path):
    repo = make_repo(tmp_path)
    # a real domain that only starts like the example domain
    (repo / "a.txt").write_text("joe" + "@" + "example.company.io\n")
    # a private word in front of the example domain is still found
    (repo / "b.txt").write_text(PRIVATE["person"] + "@" + "example.com\n")
    rc, out, _ = scan(bash, repo, denylist_file(tmp_path))
    assert rc == 1
    assert "WORKTREE:a.txt:1 email" in out
    assert "WORKTREE:b.txt:1 private:person" in out


def test_denylist_tab_separator_and_bad_line(bash, tmp_path):
    repo = make_repo(tmp_path)
    (repo / "x.txt").write_text(PRIVATE["host"] + "\n")
    tabbed = tmp_path / "tab.txt"
    tabbed.write_text("  host\t" + PRIVATE["host"] + "  \n")
    rc, out, _ = scan(bash, repo, tabbed)
    assert rc == 1 and "WORKTREE:x.txt:1 private:host" in out
    bad = tmp_path / "bad.txt"
    bad.write_text("onlyoneword\n")
    rc, out, err = scan(bash, repo, bad)
    assert rc == 3 and "line 1" in err and "onlyoneword" not in err


# --- every commit of `git rev-list --all`, not only the commits of HEAD -----

def _plant_off_head(repo: Path, branch: str, name: str, value: str) -> str:
    """A commit on a new branch that HEAD never reaches; HEAD goes back."""
    git(repo, "checkout", "-q", "-b", branch)
    (repo / name).write_text(f"x {value}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "off head")
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-")
    assert not (repo / name).exists()
    return sha


def test_a_value_only_on_another_branch_is_found(bash, tmp_path):
    repo = make_repo(tmp_path)
    sha = _plant_off_head(repo, "side", "side.txt", GENERIC["anthropic-key"])
    rc, out, err = scan(bash, repo)
    assert rc == 1, (out, err)
    assert f"{sha}:side.txt:1 anthropic-key" in out.splitlines()


@pytest.mark.parametrize("annotated", [False, True], ids=["light-tag", "annotated-tag"])
def test_a_value_only_on_a_tag_is_found(bash, tmp_path, annotated):
    repo = make_repo(tmp_path)
    sha = _plant_off_head(repo, "tmp", "tagged.txt", GENERIC["github-token"])
    if annotated:
        git(repo, "tag", "-a", "-m", "a release", "v1", sha)
    else:
        git(repo, "tag", "v1", sha)
    git(repo, "branch", "-q", "-D", "tmp")  # only the tag reaches the commit now
    rc, out, err = scan(bash, repo)
    assert rc == 1, (out, err)
    assert f"{sha}:tagged.txt:1 github-token" in out.splitlines()


def test_tailscale_ip_keeps_to_the_cgnat_range(bash, tmp_path):
    repo = make_repo(tmp_path)
    inside = ["100" + ".64.0.1", "100" + ".127.255.255"]
    outside = ["100" + ".63.255.255", "100" + ".128.0.1", "100" + ".1.2.3"]
    (repo / "in.txt").write_text("\n".join(inside) + "\n")
    (repo / "out.txt").write_text("\n".join(outside) + "\n")
    rc, out, err = scan(bash, repo)
    assert rc == 1, (out, err)
    lines = set(out.splitlines())
    assert {"WORKTREE:in.txt:1 tailscale-ip", "WORKTREE:in.txt:2 tailscale-ip"} <= lines
    assert not [x for x in lines if x.startswith("WORKTREE:out.txt")], lines


# --- the message of an annotated tag ------------------------------------------

def _tag_label(repo: Path, name: str) -> str:
    """The label of a hit in a tag message: the tag object id, never the name."""
    return "tag-object:" + git(repo, "rev-parse", f"refs/tags/{name}")[:12]


def test_a_value_only_in_an_annotated_tag_message_is_found(bash, tmp_path):
    repo = make_repo(tmp_path)
    value = GENERIC["anthropic-key"]
    git(repo, "tag", "-a", "-m", f"release one\n\nnote {value}", "rel/v1")
    rc, out, err = scan(bash, repo, denylist_file(tmp_path))
    assert rc == 1, (out, err)
    assert out.splitlines() == [f"{_tag_label(repo, 'rel/v1')}:3 anthropic-key"]
    assert value not in out and value not in err


def test_a_private_word_in_a_tag_message_is_found(bash, tmp_path):
    repo = make_repo(tmp_path)
    git(repo, "tag", "-a", "-m", "for " + PRIVATE["host"], "v2")
    rc, out, _ = scan(bash, repo, denylist_file(tmp_path))
    assert rc == 1 and out.splitlines() == [f"{_tag_label(repo, 'v2')}:1 private:host"]


def test_a_light_tag_and_the_tagger_field_are_not_scanned(bash, tmp_path):
    repo = make_repo(tmp_path)
    git(repo, "tag", "light")
    subprocess.run(
        ["git", "-c", "user.name=" + PRIVATE["person"], "-c",
         "user.email=" + GENERIC["email"], "tag", "-a", "-m", "clean note", "v3"],
        cwd=repo, check=True)
    rc, out, err = scan(bash, repo, denylist_file(tmp_path))
    assert (rc, out) == (0, ""), err


def test_an_inner_tag_of_a_tag_of_a_tag_is_read(bash, tmp_path):
    repo = make_repo(tmp_path)
    git(repo, "tag", "-a", "-m", "note " + GENERIC["anthropic-key"], "inner")
    git(repo, "-c", "advice.nestedTag=false", "tag", "-a", "-m", "clean", "outer", "inner")
    inner = git(repo, "rev-parse", "refs/tags/inner")
    git(repo, "tag", "-d", "inner")  # only the outer tag reaches the inner one now
    rc, out, err = scan(bash, repo)
    assert rc == 1, (out, err)
    assert out.splitlines() == [f"tag-object:{inner[:12]}:1 anthropic-key"]


def _signed_tag(repo: Path, name: str, message: str, signature_line: str,
                kind: str = "PGP", after: str = "") -> None:
    head = git(repo, "rev-parse", "HEAD")
    body = (f"object {head}\ntype commit\ntag {name}\n"
            "tagger test <test@example.invalid> 0 +0000\n\n"
            f"{message}\n-----BEGIN {kind} SIGNATURE-----\n{signature_line}\n"
            f"-----END {kind} SIGNATURE-----\n{after}")
    # bytes, not text: text mode on Windows would send CRLF line ends
    sha = subprocess.run(["git", "mktag"], cwd=repo, input=body.encode(), check=True,
                         capture_output=True).stdout.decode().strip()
    git(repo, "update-ref", f"refs/tags/{name}", sha)


def test_the_signature_block_of_a_tag_is_not_scanned(bash, tmp_path):
    repo = make_repo(tmp_path)
    _signed_tag(repo, "s1", "signed release", "q" * 52)
    rc, out, err = scan(bash, repo)
    assert (rc, out) == (0, ""), err
    _signed_tag(repo, "s2", "x " + GENERIC["anthropic-key"], "q" * 52)
    rc, out, _ = scan(bash, repo)
    assert rc == 1 and out.splitlines() == [f"{_tag_label(repo, 's2')}:1 anthropic-key"]


def test_an_ssh_signature_block_of_a_tag_is_not_scanned(bash, tmp_path):
    repo = make_repo(tmp_path)
    _signed_tag(repo, "h1", "signed release", "q" * 52, kind="SSH")
    rc, out, err = scan(bash, repo)
    assert (rc, out) == (0, ""), err


def test_text_after_a_signature_block_is_still_scanned(bash, tmp_path):
    repo = make_repo(tmp_path)
    key = GENERIC["anthropic-key"]
    _signed_tag(repo, "p1", "release", "sig", after=f"tail {key}\n")
    rc, out, err = scan(bash, repo)
    assert rc == 1, (out, err)
    assert out.splitlines() == [f"{_tag_label(repo, 'p1')}:5 anthropic-key"]
    # a fake END in the middle does not hide what follows the real block
    _signed_tag(repo, "p2", "-----END SSH SIGNATURE-----\nnote " + key, "sig",
                kind="SSH")
    rc, out, _ = scan(bash, repo)
    assert f"{_tag_label(repo, 'p2')}:2 anthropic-key" in out.splitlines()


def test_a_private_word_in_a_tag_name_is_found_and_not_printed(bash, tmp_path):
    repo = make_repo(tmp_path)
    git(repo, "tag", "a-light-tag")
    git(repo, "tag", "rel-" + PRIVATE["host"])
    rc, out, err = scan(bash, repo, denylist_file(tmp_path))
    assert rc == 1, (out, err)
    assert out.splitlines() == ["TAG_NAMES:2 private:host"]
    assert PRIVATE["host"] not in out + err


def test_a_tag_message_hit_never_prints_the_tag_name(bash, tmp_path):
    repo = make_repo(tmp_path)
    word = PRIVATE["host"]
    name = "rel-" + word
    git(repo, "tag", "-a", "-m", "built on " + word, name)
    git(repo, "-c", "advice.nestedTag=false", "tag", "-a", "-m", "for " + word,
        "outer", name)
    inner = git(repo, "rev-parse", f"refs/tags/{name}")
    rc, out, err = scan(bash, repo, denylist_file(tmp_path))
    assert rc == 1, (out, err)
    assert word.lower() not in (out + err).lower()
    lines = out.splitlines()
    # both message hits, each by its tag object id; the name hit as TAG_NAMES
    assert f"tag-object:{inner[:12]}:1 private:host" in lines
    assert f"{_tag_label(repo, 'outer')}:1 private:host" in lines
    assert "TAG_NAMES:2 private:host" in lines  # the ref name (2nd of 2, sorted)
    git(repo, "tag", "-d", name)  # the inner name now lives only in its tag object
    rc, out, err = scan(bash, repo, denylist_file(tmp_path))
    assert rc == 1 and word.lower() not in (out + err).lower()
    assert [x for x in out.splitlines() if x.startswith("TAG_NAMES:")], out


# --- a tag that points straight at a blob or a tree ----------------------------

def _hash_blob(repo: Path, text: str) -> str:
    return subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo,
                          input=text.encode(), check=True,
                          capture_output=True).stdout.decode().strip()


def test_a_blob_that_only_a_tag_reaches_is_scanned(bash, tmp_path):
    repo = make_repo(tmp_path)
    word = PRIVATE["host"]
    blob = _hash_blob(repo, "first\nkey " + GENERIC["anthropic-key"] + "\n")
    git(repo, "tag", "-a", "-m", "a bare blob", "rel-" + word, blob)
    rc, out, err = scan(bash, repo, denylist_file(tmp_path))
    assert rc == 1, (out, err)
    label = _tag_label(repo, "rel-" + word)
    assert f"{label}:BLOB:{blob[:12]}:2 anthropic-key" in out.splitlines()
    assert word.lower() not in (out + err).lower()
    assert GENERIC["anthropic-key"] not in out + err
    # a light tag at the same blob: labelled by the blob id
    git(repo, "tag", "-d", "rel-" + word)
    git(repo, "tag", "bare", blob)
    rc, out, _ = scan(bash, repo)
    assert out.splitlines() == [f"tag-target:{blob[:12]}:BLOB:{blob[:12]}:2 anthropic-key"]


def test_every_blob_of_a_tree_that_only_a_tag_reaches_is_scanned(bash, tmp_path):
    repo = make_repo(tmp_path)
    word = PRIVATE["person"]
    one = _hash_blob(repo, "clean\n")
    two = _hash_blob(repo, "x\ny\nfor " + word + "\n")
    sub = subprocess.run(["git", "mktree"], cwd=repo, check=True, capture_output=True,
                         input=f"100644 blob {two}\t{word}.txt\n".encode()
                         ).stdout.decode().strip()
    tree = subprocess.run(["git", "mktree"], cwd=repo, check=True, capture_output=True,
                          input=(f"100644 blob {one}\ta.txt\n"
                                 f"040000 tree {sub}\tdir\n").encode()
                          ).stdout.decode().strip()
    git(repo, "tag", "-a", "-m", "a bare tree", "t1", tree)
    rc, out, err = scan(bash, repo, denylist_file(tmp_path))
    assert rc == 1, (out, err)
    assert out.splitlines() == [f"{_tag_label(repo, 't1')}:TREE:{two[:12]}:3 private:person"]
    assert word.lower() not in (out + err).lower()  # no path of the tree is printed


def test_the_tag_names_item_holds_the_names_only(bash, tmp_path):
    repo = make_repo(tmp_path)
    git(repo, "tag", "v1")
    git(repo, "tag", "-a", "-m", "clean", "v2")
    deny = tmp_path / "deny-words.txt"
    # words that the for-each-ref line holds next to the name: type and path
    deny.write_text("kind commit\nkind tag\nkind refs\n")
    rc, out, err = scan(bash, repo, deny)
    assert (rc, out) == (0, ""), err
