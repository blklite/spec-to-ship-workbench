"""The repository of an item. The config names the repositories
in [repos]; `wb intake --repo <name>` writes the name and its URL into the
state; an unknown name, or a new URL while the work folder exists, is refused.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import ROOT
from wb import intake, state, tracker
from wb.cli import main
from wb_fakes import SPEC, make_cfg

URL_A = "https://github.com/example/widget.git"
URL_B = "git" + "@github.com:example/other.git"  # built from pieces: the scan reads it as an email


@pytest.fixture
def env(tmp_path):
    cfg, personas = make_cfg(tmp_path)
    cfg["repos"] = {"widget": URL_A, "other_2": URL_B}
    return cfg, personas


# AC 1: the repository list

def test_a_good_repos_table_loads(env, capsys):
    cfg, personas = env
    assert main(["status"], cfg=cfg, personas=personas) == 0
    assert intake.repos(cfg) == {"widget": URL_A, "other_2": URL_B}


def test_a_good_repos_table_loads_from_a_file(tmp_path, capsys):
    text = (ROOT / "config" / "workbench.example.toml").read_text(encoding="utf-8")
    text = text.replace('state/"', f'{(tmp_path / "state").as_posix()}/"')
    path = tmp_path / "workbench.toml"
    path.write_text(text.replace("[repos]\n", f'[repos]\nwidget = "{URL_A}"\n'), encoding="utf-8")
    assert main(["status"], cfg=str(path)) == 0


@pytest.mark.parametrize("name,url,why", [
    ("a b", URL_A, "the config key repos.a b: a bad name"),
    ("a.b", URL_A, "the config key repos.a.b: a bad name"),
    ("-x", URL_A, "the config key repos.-x: a bad name"),
    ("lab", "https://gitlab.com/example/widget.git", "the config key repos.lab: 'https://gitlab.com/"),
    ("local", "/srv/git/widget.git", "the config key repos.local: '/srv/git/widget.git' is not a GitHub"),
    ("num", 7, "the config key repos.num: 7 is not a GitHub clone URL"),
])
def test_a_bad_repos_entry_is_a_usage_error_that_names_the_key(env, capsys, name, url, why):
    cfg, personas = env
    cfg["repos"][name] = url
    assert main(["status"], cfg=cfg, personas=personas) == 2
    assert capsys.readouterr().err.startswith("wb: " + why)


def test_repos_that_is_not_a_table_is_a_usage_error(env, capsys):
    cfg, personas = env
    cfg["repos"] = URL_A
    assert main(["status"], cfg=cfg, personas=personas) == 2
    assert "repos must be a table" in capsys.readouterr().err


def test_the_example_config_has_an_empty_table_with_1_commented_example():
    from wb_fakes import EXAMPLE

    assert EXAMPLE["repos"] == {}
    text = (ROOT / "config" / "workbench.example.toml").read_text(encoding="utf-8")
    block = text.split("[repos]\n", 1)[1].split("\n\n", 1)[0]
    assert block.count('\n# example = "https://github.com/') == 1
    assert "the origin of the runner clone: that is the default" in " ".join(block.replace("#", "").split())


# AC 2: the CLI input

def run(env, capsys, *extra, item="7"):
    cfg, personas = env
    code = main(["intake", str(SPEC), "--id", item, *extra], cfg=cfg, personas=personas)
    return code, capsys.readouterr().out.splitlines()


def wait(cfg, item="7"):
    """The item waits for the owner (case 2): a new intake amends its spec."""
    st = state.load(cfg, item)
    st.update(status="waiting", question={"case": 2, "text": "q", "run_folder": None})
    state.save(cfg, st)


def work(cfg, item="7"):
    w = state.load(cfg, item)["work"]
    return w["repo"], w["url"]


def test_intake_with_repo_writes_the_name_and_the_url(env, capsys):
    assert run(env, capsys, "--repo", "other_2") == (0, ["WB: INTAKE OK 7"])
    assert work(env[0]) == ("other_2", URL_B)


def test_intake_without_repo_writes_null(env, capsys):
    assert run(env, capsys)[0] == 0
    assert work(env[0]) == (None, None)


def test_an_amendment_without_repo_keeps_the_old_repo(env, capsys):
    cfg, _ = env
    assert run(env, capsys, "--repo", "widget")[0] == 0
    wait(cfg)
    assert run(env, capsys)[0] == 0
    assert work(cfg) == ("widget", URL_A)


def test_an_amendment_without_repo_keeps_null(env, capsys):
    cfg, _ = env
    assert run(env, capsys)[0] == 0
    wait(cfg)
    assert run(env, capsys)[0] == 0
    assert work(cfg) == (None, None)


def test_an_amendment_with_repo_sets_the_new_repo(env, capsys):
    cfg, _ = env
    assert run(env, capsys, "--repo", "widget")[0] == 0
    wait(cfg)
    assert run(env, capsys, "--repo", "other_2")[0] == 0
    assert work(cfg) == ("other_2", URL_B)


def test_an_amendment_of_a_state_of_before_repos_keeps_null(env, capsys):
    cfg, _ = env
    assert run(env, capsys)[0] == 0
    st = state.load(cfg, "7")
    del st["work"]["repo"], st["work"]["url"]
    state.save(cfg, st)
    wait(cfg)
    assert run(env, capsys)[0] == 0
    assert work(cfg) == (None, None)


# AC 3: an unknown name

@pytest.fixture
def tracker_calls(monkeypatch):
    calls = []
    real = tracker.make

    class Spy:
        def __init__(self, tr):
            self.tr = tr

        def __getattr__(self, name):
            def method(*a, **k):
                calls.append(name)
                return getattr(self.tr, name)(*a, **k)
            return method

    monkeypatch.setattr(tracker, "make", lambda cfg: Spy(real(cfg)))
    return calls


@pytest.mark.parametrize("amend", [False, True])
def test_an_unknown_repo_is_refused_with_1_line(env, capsys, tracker_calls, amend):
    cfg, _ = env
    path = state.item_dir(cfg, "7") / "state.json"
    if amend:
        assert run(env, capsys, "--repo", "widget")[0] == 0
        wait(cfg)
    before = path.read_bytes() if amend else None
    calls = len(tracker_calls)
    code, out = run(env, capsys, "--repo", "nope")
    assert (code, out) == (1, ["unknown repository 'nope': the config [repos] names widget, other_2",
                               "WB: INTAKE REFUSE 7 - 1 problems"])
    assert path.read_bytes() == before if amend else not path.exists()
    assert len(tracker_calls) == calls


def test_an_unknown_repo_with_no_repos_table_names_none(env, capsys):
    cfg, _ = env
    del cfg["repos"]
    code, out = run(env, capsys, "--repo", "widget")
    assert code == 1 and out[0] == "unknown repository 'widget': the config [repos] names none"
    assert not state.exists(cfg, "7")


# AC 4: no change under an existing work folder

def test_a_new_url_is_refused_while_the_work_folder_exists(env, capsys, tracker_calls):
    cfg, _ = env
    assert run(env, capsys, "--repo", "widget")[0] == 0
    wait(cfg)
    folder = Path(cfg["work"]["home"]) / "7"
    folder.mkdir(parents=True)
    path = state.item_dir(cfg, "7") / "state.json"
    before, calls = path.read_bytes(), len(tracker_calls)
    code, out = run(env, capsys, "--repo", "other_2")
    assert (code, out) == (1, [f"the work folder {folder} exists and clones another repository; the owner deletes"
                               " it by hand before a new repository", "WB: INTAKE REFUSE 7 - 1 problems"])
    assert path.read_bytes() == before and len(tracker_calls) == calls
    folder.rmdir()
    assert run(env, capsys, "--repo", "other_2")[0] == 0
    assert work(cfg) == ("other_2", URL_B)


def test_a_repo_for_an_item_of_the_runner_origin_is_refused_while_the_work_folder_exists(env, capsys):
    cfg, _ = env
    assert run(env, capsys)[0] == 0
    wait(cfg)
    (Path(cfg["work"]["home"]) / "7").mkdir(parents=True)
    code, out = run(env, capsys, "--repo", "widget")
    assert code == 1 and out[0].startswith("the work folder ")
    assert work(cfg) == (None, None)


@pytest.mark.parametrize("extra", [[], ["--repo", "widget"]])
def test_the_same_url_passes_while_the_work_folder_exists(env, capsys, extra):
    cfg, _ = env
    assert run(env, capsys, "--repo", "widget")[0] == 0
    wait(cfg)
    (Path(cfg["work"]["home"]) / "7").mkdir(parents=True)
    assert run(env, capsys, *extra)[0] == 0
    assert work(cfg) == ("widget", URL_A)


def test_a_new_item_with_repo_passes_while_its_work_folder_exists(env, capsys):
    """No old state, so no old URL: the origin check of run-next (AC 5) guards the folder."""
    cfg, _ = env
    (Path(cfg["work"]["home"]) / "7").mkdir(parents=True)
    assert run(env, capsys, "--repo", "widget")[0] == 0
    assert work(cfg) == ("widget", URL_A)



def test_a_new_repository_drops_the_origin_and_the_push_of_the_old_one(env, capsys):
    """Code review: a recorded remote of the old repository would take the push there."""
    cfg, _ = env
    assert run(env, capsys)[0] == 0
    st = state.load(cfg, "7")
    st.update(remote={"url": "https://github.com/example/runner.git", "base_sha": "a" * 40}, pushed="b" * 40)
    state.save(cfg, st)
    wait(cfg)
    assert run(env, capsys)[0] == 0  # the same repository: kept
    st = state.load(cfg, "7")
    assert st["remote"]["url"] == "https://github.com/example/runner.git" and st["pushed"] == "b" * 40
    wait(cfg)
    assert run(env, capsys, "--repo", "widget")[0] == 0
    st = state.load(cfg, "7")
    assert "remote" not in st and "pushed" not in st
