"""The secret guard: before a guarded step and before each push, the text that
the step or the push would send out goes to `scripts/secret-scan.sh --message`.

The scan file holds the added lines of the diff of the work folder against the
base (untracked files included) and of the patch of each commit since the base,
the diff headers, the commit messages since the base and, for a push, the title
and body of the pull request. A context or removed line is not scanned: it is in
the base commit, which is already on the remote, or it is an added line of an
earlier commit patch. So a hunk header ends at its second @@: the text after it
is a line that git copies from above the hunk. It is written in the item folder,
never in the work folder. A hit gives its class and where it is, never the
matched text.

After the diff, the scan file holds each regular file under the work folder,
outside the top .git, that the temporary index does not hold after add -A (by
.gitignore, info/exclude, a global excludes file or a nested git folder): the
whole file as text, any size and type, no skip by name, at the place
`an ignored file, <path>:<line>`. A link gives its target path as text, 1 place
for each line, and is never followed; a Windows junction counts as a link. A
file or folder that the script can not read waits.

Before the scan (after check_git), the Python caches go: a regular file *.pyc
directly in a folder __pycache__, and in a folder .pytest_cache the regular files
CACHEDIR.TAG, README.md and .gitignore at its top and each regular file under v/;
then each such folder that is empty, with the empty folders in it. No .gitignore
is read. A path in HEAD or in the real index stays, a file outside these forms
stays and is scanned, no link is followed, and no removal enters the top .git or
a nested git folder. A failed removal waits.

check_git refuses a .git/info/exclude that is a link, is not a regular file or
has a line that is not blank and not a comment: a clone does not write one. A
missing file passes.

A path that git lists keeps the bytes of its name, as os.walk does: a name that
is not UTF-8 never matches a different file.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

from runner import ROOT

HIT = re.compile(r"MESSAGE:([0-9]+) (\S+)")
# A build or rework step can write the .git of its work folder: each git call of
# the script there runs with no hook, no fsmonitor and no replace ref (a push
# sends the real objects), and only after check_git.
GIT = ["git", "--no-replace-objects", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false"]
# The keys that a clone and run-next write in the local config; any other key
# (a filter, a textconv, url.*.insteadOf, include.path, core.sshCommand ...)
# could run a command or move a request: the script then runs no git there.
CLONE_KEY = re.compile(r"core\.(repositoryformatversion|filemode|bare|logallrefupdates|symlinks|ignorecase"
                       r"|precomposeunicode)|remote\.origin\.(url|fetch)|branch\..+\.(remote|merge)"
                       r"|user\.(name|email)", re.I)
HUNK = re.compile(r"@@ -[0-9]+(?:,[0-9]+)? \+([0-9]+)")
PYTEST_TOP = [("CACHEDIR.TAG",), ("README.md",), (".gitignore",)]  # the files at the top of .pytest_cache


def _git_for_windows_bash() -> list[Path]:
    found: list[Path] = []
    try:
        out = subprocess.run(
            ["git", "--exec-path"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        out = ""
    if out:
        # <Git>/mingw64/libexec/git-core -> <Git>/bin/bash.exe
        exec_path = Path(out)
        for parent in exec_path.parents:
            candidate = parent / "bin" / "bash.exe"
            if candidate.is_file():
                found.append(candidate)
                break
    found.append(Path("C:/Program Files/Git/bin/bash.exe"))
    return found


def find_bash() -> tuple[str | None, str]:
    """Return (path, reason). path is None when no usable bash exists.

    Never ``shutil.which("bash")``: on Windows that can find the WSL launcher
    stub, which runs a different system. Order: env ``WORKBENCH_BASH``; on
    Windows, Git for Windows ``bin/bash.exe`` (derived from ``git --exec-path``,
    then the default install folder); on other systems ``/bin/bash`` or
    ``/usr/bin/bash``."""
    env = os.environ.get("WORKBENCH_BASH")
    if env:
        if Path(env).is_file():
            return env, "WORKBENCH_BASH"
        return None, f"WORKBENCH_BASH names a missing file: {env}"
    if sys.platform == "win32":
        candidates = _git_for_windows_bash()
    else:
        candidates = [Path("/bin/bash"), Path("/usr/bin/bash")]
    for c in candidates:
        if c.is_file():
            return str(c), "found"
    return None, "no bash found (set WORKBENCH_BASH, or install Git for Windows)"


class GuardError(Exception):
    """The scan could not run: the item waits, as for a hit."""


def _is_link(path) -> bool:
    """Whether path is a link or a Windows junction, which os.walk enters; isjunction is 3.12+, looked up at
    each call."""
    junction = getattr(os.path, "isjunction", None)
    return os.path.islink(path) or bool(junction and junction(path))


def check_git(folder) -> str | None:
    """None, or why the script runs no git command in the work folder."""
    dot = Path(folder) / ".git"
    if _is_link(dot) or not dot.is_dir():
        return "the .git of the work folder is not a folder"
    # commondir, gitdir, config.worktree point git at another config; info/grafts and shallow hide commits
    for name in ("commondir", "gitdir", "config.worktree", "info/grafts", "shallow"):
        if (dot / name).exists():
            return f"the .git of the work folder has a {name} file, which a clone does not write"
    exclude = dot / "info" / "exclude"  # a rule there hides a file from the diff and from git status
    if os.path.lexists(exclude):
        try:  # a comment starts with # in column 1; git strips only trailing spaces (and a CR) from a line
            mode = os.lstat(exclude).st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode) or stat.S_ISDIR(mode)):  # a FIFO blocks a read
                return "the info/exclude of the work folder is not a regular file"
            rule = stat.S_ISLNK(mode) or any(line.rstrip("\r ") and not line.startswith("#")
                                               for line in exclude.read_bytes().decode("utf-8", "replace").split("\n"))
        except OSError as e:
            return f"cannot read the info/exclude of the work folder: {e}"
        if rule:
            return "the .git of the work folder has an info/exclude rule, which a clone does not write"
    r = subprocess.run(["git", "config", "--file", str(dot / "config"), "--name-only", "--list", "--null"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return f"cannot read the git config of the work folder: {r.stderr.strip()}"
    # --null: a subsection can hold a space, so 1 key would split into 2 keys that a clone writes
    bad = sorted({k for k in r.stdout.split("\0")[:-1] if not CLONE_KEY.fullmatch(k)})
    if bad:
        return f"the git config of the work folder has keys that a clone does not write: {', '.join(bad)}"
    return None


def _git_bytes(folder, *args: str, env=None) -> bytes:
    r = subprocess.run([*GIT, *args], cwd=folder, capture_output=True, env=env)
    if r.returncode != 0:
        why = r.stderr.decode("utf-8", errors="replace").strip()
        raise GuardError(f"git {args[0]} failed: {why}")
    return r.stdout


def _git(folder, *args: str, env=None) -> str:
    return _git_bytes(folder, *args, env=env).decode("utf-8", errors="replace")


def _paths(folder, *args: str, env=None) -> set[str]:
    """The paths of a -z list of git, decoded as os.walk decodes a name: a byte that is not UTF-8 stays itself,
    so a path of git never equals a different file of the walk."""
    return {os.fsdecode(p) for p in _git_bytes(folder, *args, env=env).split(b"\0")}


def _walk(diff: list[str]):
    """For each line of a diff: (where it is, whether it is a line of a hunk).

    A line of a hunk starts with a marker: ``+``, ``-`` or a space."""
    path, new, header = None, 0, False
    for k, line in enumerate(diff, 1):
        where, body = f"line {k} of the diff", False
        if line.startswith("diff --git "):
            header, path = True, None
        elif header and line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif HUNK.match(line):
            header, new = False, int(HUNK.match(line).group(1))
        elif not header and line[:1] in ("+", "-", " "):
            body = True
            if path and line[:1] != "-":
                where, new = f"{path}:{new}", new + 1
        yield where, body


def _places(diff: list[str]) -> list[str]:
    """For each line of a diff, the file and line that it shows, or the diff line."""
    return [where for where, _ in _walk(diff)]


def _content(diff: list[str]) -> list[str]:
    """The diff with the text of the added lines only, 1 entry for each diff line.

    An added line loses its marker: the scan reads the text of a file, and the marker
    ``+`` would join the local part of an address, so an added decorator line would be
    an email. A context or removed line is empty: it is in the base commit, which is
    already on the remote, or it is an added line of an earlier commit patch, which
    the scan reads. Headers (with the paths) stay as they are, but a hunk header ends at its second ``@@``:
    git copies the text after it from a line above the hunk, which is in the base or in an earlier patch."""
    return [(line[1:] if line[:1] == "+" else "") if body else _hunk_header(line)
            for line, (_, body) in zip(diff, _walk(diff))]


def _hunk_header(line: str) -> str:
    """A hunk header up to and with its second @@; any other header line as it is."""
    end = line.find("@@", 2)
    return line[:end + 2] if HUNK.match(line) and end > 0 else line


def _raise(e: OSError):
    raise e  # the onerror of os.walk: by default it drops a folder that it cannot read


def _cache_form(parts: tuple[str, ...]) -> bool:
    """Whether the regular file at these path parts is a cache form: a *.pyc directly in __pycache__, or in
    .pytest_cache 1 of the files of its top or a file under v/. Exact names; no .gitignore is read."""
    if len(parts) > 1 and parts[-2] == "__pycache__" and parts[-1].endswith(".pyc"):
        return True
    return any(p == ".pytest_cache" and (parts[i + 1:] in PYTEST_TOP or (len(parts) > i + 2 and parts[i + 1] == "v"))
               for i, p in enumerate(parts[:-1]))


def drop_caches(folder) -> None:
    """Remove each cache form that is not in HEAD or in the real index, then each cache folder that holds
    only empty folders, with them. No link is followed, and no removal enters the top .git or a nested git
    folder. Raises OSError or GuardError: the item waits."""
    keep = _paths(folder, "ls-tree", "-r", "-z", "--name-only", "HEAD") | _paths(folder, "ls-files", "-z")
    caches = []
    for root, dirs, files in os.walk(folder, onerror=_raise):  # os.walk enters a junction, never a link: both pruned
        if root != os.fspath(folder) and os.path.lexists(os.path.join(root, ".git")):
            dirs[:] = []  # a nested git folder
            continue
        dirs[:] = [d for d in dirs if d != ".git" and not _is_link(os.path.join(root, d))]
        if os.path.basename(root) in ("__pycache__", ".pytest_cache"):
            caches.append(root)
        for name in files:
            path = os.path.join(root, name)
            rel = Path(os.path.relpath(path, folder))
            if rel.as_posix() not in keep and stat.S_ISREG(os.lstat(path).st_mode) and _cache_form(rel.parts):
                os.unlink(path)
    for top in sorted(caches, key=len, reverse=True):  # the deepest first: a cache folder in a cache folder
        roots, held = [], False
        for root, dirs, files in os.walk(top, onerror=_raise):  # top-down: a link and a .git are pruned, never entered
            held = held or bool(files) or ".git" in dirs or any(_is_link(os.path.join(root, d)) for d in dirs)
            dirs[:] = [d for d in dirs if d != ".git" and not _is_link(os.path.join(root, d))]
            roots.append(root)
        if not held:
            for root in reversed(roots):  # a folder of a top-down walk comes after its parent
                os.rmdir(root)


def _ignored(folder, held: set[str]) -> tuple[list[str], list[str]]:
    """(the lines, their places) of each regular file outside the top .git that the index does not hold."""
    lines, where = [], []
    for root, dirs, files in os.walk(folder, onerror=_raise):
        if root == os.fspath(folder):
            dirs[:] = [d for d in dirs if d != ".git"]
        links = [d for d in dirs if _is_link(os.path.join(root, d))]  # walk lists a link to a folder in dirs
        dirs[:] = [d for d in dirs if d not in links]
        for name in files + links:
            path = os.path.join(root, name)
            rel = Path(os.path.relpath(path, folder)).as_posix()
            if rel in held:
                continue
            if _is_link(path):  # its target as text, as git holds a link: never followed; a junction is S_ISDIR
                text = os.fsencode(os.readlink(path)).decode("utf-8", errors="replace").split("\n")  # as a file's text
            elif not stat.S_ISREG(os.lstat(path).st_mode):  # a FIFO would block the read
                continue
            else:
                with open(path, "rb") as f:
                    text = f.read().decode("utf-8", errors="replace").split("\n")
            lines += text
            where += [f"an ignored file, {rel}:{k}" for k in range(1, len(text) + 1)]
    return lines, where


def scan_text(folder, base: str, index: Path, extra: list[str]) -> tuple[list[str], list[str]]:
    """(the lines to scan, where each line comes from). base: a commit or a ref."""
    plain = ("--text", "--no-color", "--no-ext-diff", "--no-textconv")
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    index.unlink(missing_ok=True)
    try:  # a temporary index: the diff then holds the untracked files too
        _git(folder, "read-tree", "HEAD", env=env)  # a tracked file that .gitignore matches still shows
        _git(folder, "add", "-A", env=env)
        diff = _git(folder, "diff", "--cached", *plain, base, env=env).split("\n")
        held = _paths(folder, "ls-files", "-z", env=env)
    finally:
        index.unlink(missing_ok=True)
    ignored, ignored_where = _ignored(folder, held)
    # each commit of the push: a secret that a later commit removes still goes out;
    # a merge against each parent, else git log shows no patch for a merge; -m would obey log.diffMerges
    patches = _git(folder, "log", "-p", "--diff-merges=separate", "--format=", *plain, f"{base}..HEAD").split("\n")
    messages = _git(folder, "log", "--format=%B", f"{base}..HEAD").split("\n")
    extra = [line for text in extra for line in text.split("\n")]  # 1 place for each line
    lines = _content(diff) + ignored + _content(patches) + messages + extra
    where = (_places(diff) + ignored_where + [f"a commit, {p.replace('of the diff', 'of the commits')}" for p in _places(patches)]
             + ["a commit message"] * len(messages) + ["the pull request title or body"] * len(extra))
    return lines, where


def scan(st: dict, item_dir: Path, extra: list[str] = ()) -> str | None:
    """None when the scan is clean, else why the item waits."""
    unsafe = check_git(st["work"]["folder"])
    if unsafe:
        return f"the secret guard did not run: {unsafe}"
    base = (st.get("remote") or {}).get("base_sha")
    if not base:
        return "the secret guard did not run: no base commit is recorded"
    try:
        drop_caches(st["work"]["folder"])  # a step that runs the tests leaves them; the push can not send them
        lines, where = scan_text(st["work"]["folder"], base, item_dir / "guard.index", list(extra))
    except (GuardError, OSError) as e:
        return f"the secret guard did not run: {e}"
    file = item_dir / "guard.txt"
    file.write_bytes("\n".join(lines).replace("\0", " ").encode("utf-8"))  # a NUL makes grep skip the file
    bash, why = find_bash()
    if bash is None:
        return f"the secret guard did not run: {why}"
    env = {k: v for k, v in os.environ.items() if k != "WORKBENCH_DENYLIST"}
    try:
        r = subprocess.run([bash, str(ROOT / "scripts" / "secret-scan.sh"), "--message", file.as_posix()],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    except OSError as e:
        return f"the secret guard did not run: {e}"
    if r.returncode == 0:
        return None
    hits = [f"{where[int(n) - 1] if 0 < int(n) <= len(where) else f'line {n}'} {cls}"
            for n, cls in sorted(HIT.findall(r.stdout), key=lambda h: int(h[0]))]
    if r.returncode == 1 and hits:
        return f"the secret guard found {len(hits)} hits: {'; '.join(hits)} (scan file: {file})"
    return f"the secret scan exited with code {r.returncode} (scan file: {file})"
