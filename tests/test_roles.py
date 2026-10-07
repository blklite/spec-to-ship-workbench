"""AC 5: each role the process names has a file in roles/, the example config
maps each role to a persona page, the persona pages are generic, and every
relative link in roles/ and personas/ resolves inside the repo, and its
#anchor, if any, names a heading of the target."""

from __future__ import annotations

import re
import tomllib

import pytest

from conftest import ROOT, denylist_hits, load_denylist

ROLES = {
    "builder", "reviewer", "rework", "spec-checker",
    "product-owner", "architect", "agentic-reviewer",
}
PERSONAS = {"marge", "lisa", "milhouse", "ned", "apu", "frink", "martin"}
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def process_roles() -> set[str]:
    names = set()
    for p in (ROOT / "process").glob("*.md"):
        names |= set(re.findall(r"`role:([a-z-]+)`", p.read_text(encoding="utf-8")))
    return names


def test_role_files():
    assert {p.stem for p in (ROOT / "roles").glob("*.md")} == ROLES


def test_every_process_role_has_a_file():
    named = process_roles()
    assert named, "the process files name no role"
    missing = sorted(n for n in named if not (ROOT / "roles" / f"{n}.md").is_file())
    assert not missing, f"roles named in process/ without a file: {missing}"


def test_persona_mapping():
    with (ROOT / "config" / "personas.example.toml").open("rb") as f:
        cfg = tomllib.load(f)
    assert set(cfg["roles"]) == ROLES
    mapped = {v["persona"] for v in cfg["roles"].values()}
    assert mapped == PERSONAS
    for name in mapped:
        assert (ROOT / "personas" / f"{name}.md").is_file()


@pytest.mark.parametrize("name", sorted(PERSONAS))
def test_persona_page_has_origin_and_grades(name):
    text = (ROOT / "personas" / f"{name}.md").read_text(encoding="utf-8")
    assert "\n## Origin\n" in text
    assert "\n## Companion personas\n" in text


def _md_files():
    for folder in ("roles", "personas", "process", "docs", "prompts", "cost"):
        yield from sorted((ROOT / folder).glob("*.md"))
    yield from sorted(ROOT.glob("*.md"))


@pytest.mark.parametrize("path", list(_md_files()), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_relative_links_resolve(path):
    for target in LINK.findall(path.read_text(encoding="utf-8")):
        if re.match(r"[a-z]+:", target):
            continue  # an absolute URL
        assert (path.parent / target).resolve().exists(), f"{target} does not resolve"


# --- the #anchor of a relative Markdown link names a heading of its target ----

ANCHOR_LINK = re.compile(r"\]\(([^)#\s]*)#([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^\s*(```|~~~)")


def unfenced(text: str) -> str:
    """The text without its code fences: an example link there is no link."""
    out, fenced = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            out.append(line)
    return "\n".join(out)


def slug(heading: str) -> str:
    """The anchor GitHub gives a heading: lower case, punctuation dropped
    (except - and _), each space a hyphen."""
    text = re.sub(r"`|\*\*|__", "", heading.strip().lower())
    text = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", text)  # _emphasis_
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # a link keeps its text
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(text: str) -> set[str]:
    """The anchors of the headings of a Markdown text, outside code fences.
    A repeated heading gets -1, -2 and so on, as on GitHub."""
    found: set[str] = set()
    seen: dict[str, int] = {}
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        m = None if fenced else re.match(r"^ {0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
        if not m:
            continue
        base = slug(m[1])
        n = seen.get(base, 0)
        seen[base] = n + 1
        found.add(base if n == 0 else f"{base}-{n}")
    return found


def broken_anchors(path) -> list[str]:
    bad = []
    for target, anchor in ANCHOR_LINK.findall(unfenced(path.read_text(encoding="utf-8"))):
        if re.match(r"[a-z]+:", target):
            continue  # an absolute URL
        dest = (path.parent / target).resolve() if target else path
        if dest.suffix.lower() != ".md" or not dest.is_file():
            continue  # a missing file fails test_relative_links_resolve
        if anchor.lower() not in anchors(dest.read_text(encoding="utf-8")):
            bad.append(f"{target}#{anchor}")
    return bad


@pytest.mark.parametrize("path", list(_md_files()), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_link_anchors_name_a_heading(path):
    bad = broken_anchors(path)
    assert not bad, f"anchors with no heading in the target: {bad}"


def test_slug_and_anchor_rules():
    assert slug("Fix loop") == "fix-loop"
    assert slug("`config:` keys, and the **budget**!") == "config-keys-and-the-budget"
    assert slug("Step 2: the [spec](specs.md)") == "step-2-the-spec"
    assert slug("The _draft_ step of snake_case") == "the-draft-step-of-snake_case"
    text = "# Title\n## Notes\n```\n# not a heading\n```\n## Notes\n"
    assert anchors(text) == {"title", "notes", "notes-1"}


def test_a_broken_anchor_is_found(tmp_path):
    (tmp_path / "b.md").write_text("# B\n\n## Real part\n", encoding="utf-8")
    page = tmp_path / "a.md"
    page.write_text(
        "# A\n\n## Here\n\n[ok](b.md#real-part) [ok](#here) [ok](b.md)\n"
        "[bad](b.md#no-part) [bad](#gone) [bad](b.md#gone-too \"a title\")\n"
        "```\n[an example, not a link](#nowhere)\n```\n",
        encoding="utf-8",
    )
    assert broken_anchors(page) == ["b.md#no-part", "#gone", "b.md#gone-too"]


def test_no_private_word_in_roles_and_personas():
    pairs = load_denylist()
    if pairs is None:
        pytest.skip("WORKBENCH_DENYLIST is not set")
    for folder in ("roles", "personas"):
        for p in (ROOT / folder).glob("*.md"):
            hits = denylist_hits(p.read_text(encoding="utf-8"), pairs)
            assert not hits, f"private words of classes {sorted(set(hits))} in {folder}/{p.name}"
