"""AC 9: the shell tests find a real bash, or skip with a reason."""

from conftest import find_bash


def test_env_override_wins(tmp_path, monkeypatch):
    fake = tmp_path / "bash.exe"
    fake.write_text("")
    monkeypatch.setenv("WORKBENCH_BASH", str(fake))
    assert find_bash() == (str(fake), "WORKBENCH_BASH")


def test_env_override_missing_gives_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_BASH", str(tmp_path / "nope"))
    path, reason = find_bash()
    assert path is None
    assert "missing" in reason


def test_finder_never_uses_path_lookup(monkeypatch):
    import shutil

    def boom(*a, **k):  # the WSL stub trap: a PATH lookup must not happen
        raise AssertionError("find_bash must not use shutil.which")

    monkeypatch.delenv("WORKBENCH_BASH", raising=False)
    monkeypatch.setattr(shutil, "which", boom)
    find_bash()
