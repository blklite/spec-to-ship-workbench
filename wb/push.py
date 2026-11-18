"""The push after a build or a rework: the branch of the item to the GitHub
repository of `origin`, over https, then 1 pull request through the REST API.

The token comes from the file `config:delivery.push_token_file` (an absolute
path, mode 0600 on POSIX). It goes only to the git push (in the environment of
that git process) and to the API calls: never into the environment, the
command line or the prompt of a step, and never into an error text.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from wb import guard

# Tests point these at a fake remote and a fake API.
GITHUB = "https://github.com"
API = "https://api.github.com"
ORIGIN = re.compile(r"(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)"
                    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?")


class PushError(Exception):
    """The push or the pull request failed; the text holds no token."""


def read_token(cfg: dict) -> str:
    name = (cfg.get("delivery") or {}).get("push_token_file")
    if not isinstance(name, str) or not Path(name).is_absolute():
        raise PushError("the config has no absolute delivery.push_token_file")
    path = Path(name)
    try:
        if os.name == "posix" and path.stat().st_mode & 0o077:
            raise PushError(f"the push token file must have mode 0600: {path}")
        token = path.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise PushError(f"cannot read the push token file {path}: {e.strerror or e}") from None
    if not token:
        raise PushError(f"the push token file is empty: {path}")
    return token


def repo(folder, url: str | None = None) -> tuple[str, str]:
    """(owner, repository) of url, else of the origin of the work folder."""
    if url is None:
        r = subprocess.run([*guard.GIT, "remote", "get-url", "origin"], cwd=folder, capture_output=True, text=True)
        url = r.stdout.strip() if r.returncode == 0 else ""
    m = ORIGIN.fullmatch(url)
    if not m:
        raise PushError("origin of the work folder is not a GitHub repository")
    return m.group(1), m.group(2)


def pr_text(st: dict) -> tuple[str, str]:
    """The title and body of the pull request: public text, so the guard scans it."""
    return st["title"], f"Item {st['id']}. The stage machine opened this pull request after the build step."


def api(method: str, url: str, token: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


def _access(cfg: dict, st: dict):
    """(owner, repository, token, the environment of a git call that signs in with the token, clean) of the
    recorded origin; clean gives the end of an error text, with no token."""
    token = read_token(cfg)
    secrets = [token, base64.b64encode(f"x-access-token:{token}".encode()).decode()]

    def clean(text) -> str:
        text = str(text)
        for s in secrets:
            text = text.replace(s, "***")
        return " ".join(text.split())[-400:]

    url = (st.get("remote") or {}).get("url")  # the origin of before the first step, never of now
    if not url:
        raise PushError("no origin URL is recorded for the item")
    owner, name = repo(st["work"]["folder"], url)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "2",
           # no stored credential of the host: only this header signs in
           "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": "",
           "GIT_CONFIG_KEY_1": "http.extraHeader", "GIT_CONFIG_VALUE_1": f"Authorization: Basic {secrets[1]}"}
    return owner, name, token, env, clean


def publish(cfg: dict, st: dict, sha: str, title: str, body: str) -> None:
    """Push `sha` to the branch of the item, then open its pull request if it has none."""
    folder, branch, base = st["work"]["folder"], st["work"]["branch"], st["work"]["base"]
    if branch == base:
        raise PushError(f"the branch of the item is the base {base}: no push")
    owner, name, _, env, clean = _access(cfg, st)
    try:
        r = subprocess.run([*guard.GIT, "push", "--no-verify", f"{GITHUB}/{owner}/{name}.git",
                            f"{sha}:refs/heads/{branch}"],
                           cwd=folder, env=env, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PushError(f"git push did not run: {clean(e)}") from None
    if r.returncode != 0:
        raise PushError(f"git push failed: {clean(r.stderr)}")
    pull_request(cfg, st, title, body)


def pull_request(cfg: dict, st: dict, title: str, body: str):
    """The number of the open pull request of the branch to the base; with none, 1 is opened. A pull request
    of the branch to another base is never taken: the gate checks the base."""
    owner, name, token, _, clean = _access(cfg, st)
    branch, base = st["work"]["branch"], st["work"]["base"]
    pulls = f"{API}/repos/{owner}/{name}/pulls"
    try:
        query = urllib.parse.urlencode({"head": f"{owner}:{branch}", "base": base, "state": "open"})
        found = api("GET", f"{pulls}?{query}", token)  # 1 pull request: also after a rework or an interrupted run
        pr = next((p for p in found or [] if isinstance(p, dict) and (p.get("base") or {}).get("ref") == base),
                  None) or api("POST", pulls, token, {"title": title, "head": branch, "base": base, "body": body})
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
        raise PushError(f"the pull request call failed: {clean(e)}") from None
    return pr.get("number") if isinstance(pr, dict) else None


def remote_head(cfg: dict, st: dict, folder) -> str | None:
    """The sha of the branch of the item on origin (git ls-remote), or None when origin has no such branch.
    folder: the cwd; never the work folder, whose local config a step can write."""
    owner, name, _, env, clean = _access(cfg, st)
    ref = f"refs/heads/{st['work']['branch']}"
    try:
        r = subprocess.run([*guard.GIT, "ls-remote", f"{GITHUB}/{owner}/{name}.git", ref], cwd=folder, env=env,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PushError(f"git ls-remote did not run: {clean(e)}") from None
    if r.returncode != 0:
        raise PushError(f"git ls-remote failed: {clean(r.stderr)}")
    return next((line.split("\t")[0] for line in r.stdout.splitlines() if line.endswith(f"\t{ref}")), None)


class Moved(PushError):
    """The forge refused the merge (409): the head of the pull request is not the given sha."""


def merge(cfg: dict, st: dict, number: int, sha: str, title: str, body: str) -> str:
    """Merge the pull request only at `sha` (as gh pr merge --match-head-commit), with this text; the merge sha."""
    owner, name, token, _, clean = _access(cfg, st)
    try:
        r = api("PUT", f"{API}/repos/{owner}/{name}/pulls/{number}/merge", token,
                {"sha": sha, "merge_method": "merge", "commit_title": title, "commit_message": body})
    except urllib.error.HTTPError as e:
        if e.code == 409:
            raise Moved(f"the head moved: the pull request is not at {sha}") from None
        raise PushError(f"the merge call failed: {clean(e)}") from None
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
        raise PushError(f"the merge call failed: {clean(e)}") from None
    merged = r.get("sha") if isinstance(r, dict) else None
    if not isinstance(merged, str) or not re.fullmatch(r"[0-9a-f]{40}", merged):
        raise PushError("the merge reply has no merge sha")
    return merged


def merged_at(cfg: dict, st: dict, number: int, sha: str) -> str | None:
    """The merge sha of the pull request when it is merged and its head is `sha`, else None. After a refused
    merge, it finds the merge of an earlier call whose reply was lost."""
    owner, name, token, _, clean = _access(cfg, st)
    try:
        r = api("GET", f"{API}/repos/{owner}/{name}/pulls/{number}", token)
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
        raise PushError(f"the pull request call failed: {clean(e)}") from None
    if not isinstance(r, dict) or r.get("merged") is not True or (r.get("head") or {}).get("sha") != sha:
        return None
    merged = r.get("merge_commit_sha")
    return merged if isinstance(merged, str) and re.fullmatch(r"[0-9a-f]{40}", merged) else None
