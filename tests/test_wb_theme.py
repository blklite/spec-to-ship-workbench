"""The look of `wb pages`, its colour tokens, and the light and dark modes."""

from __future__ import annotations

import base64
import hashlib
import re

from wb import pages, theme

DARK_MEDIA = re.compile(r"@media \(prefers-color-scheme: dark\) \{\s*html:not\(\[data-theme\]\) \{([^{}]*)\}\s*\}")
DARK_CHOICE = re.compile(r'html\[data-theme="dark"\] \{([^{}]*)\}')
LIGHT_ROOT = re.compile(r":root \{([^{}]*)\}")


def tokens(block: str) -> dict:
    return dict(re.findall(r"(--[a-z0-9-]+|color-scheme):\s*([^;]+);", block))  # --series-1


def modes() -> tuple[dict, dict]:
    """(the light tokens, the dark tokens) of STYLE."""
    return tokens(LIGHT_ROOT.search(pages.STYLE)[1]), tokens(DARK_CHOICE.search(pages.STYLE)[1])


def test_the_2_dark_rules_hold_the_same_values():
    media, choice = DARK_MEDIA.search(pages.STYLE)[1], DARK_CHOICE.search(pages.STYLE)[1]
    assert media == choice and tokens(media) == tokens(choice)
    light, dark = modes()
    assert set(light) == set(dark) and len(light) > 5
    assert light["color-scheme"] == "light" and dark["color-scheme"] == "dark"


def test_a_light_choice_keeps_light_on_a_dark_os():
    """The dark media rule applies only with no data-theme, so data-theme="light" keeps the :root values."""
    assert len(re.findall(r"@media", pages.STYLE)) == 1
    assert len(DARK_MEDIA.findall(pages.STYLE)) == 1 and len(DARK_CHOICE.findall(pages.STYLE)) == 1
    assert 'data-theme="light"' not in pages.STYLE.split("@media")[1].split("}")[0]


def test_no_colour_literal_outside_the_tokens():
    rest = LIGHT_ROOT.sub("", DARK_CHOICE.sub("", DARK_MEDIA.sub("", pages.STYLE)))
    assert "--" not in rest.replace("var(--", "")  # each token is set in a token rule only
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\b(rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(", rest)
    assert not re.search(r"(?<![\w-])(white|black|red|green|blue|gray|grey|silver|navy|yellow|orange)(?![\w-])",
                         rest, re.I)  # not white-space
    assert "var(--fg)" in rest and "var(--bg)" in rest


def b64_sha256(text: str) -> str:
    return base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")


FAKE_THEME_ID = "fake.theme_key-1"


def test_the_scripts_are_the_shell_scripts_with_the_key():
    """The SHA-256 of each script of the site viewer shell, with FAKE_THEME_ID in place of its key literal: an
    independent copy, computed from the shell text and not from wb/theme.py. The live hashes are a gate check."""
    head, control = theme.scripts(FAKE_THEME_ID)
    assert b64_sha256(head) == "Vav92c3Ta34s/IoEMOJNZKoGYzb52lhdjlk4aWNzrsE="
    assert b64_sha256(control) == "7tZJMJS8mwIJ3OGXANAOe4b/CYeN8CwhZKsR8K6ShHI="
    for script in (theme.HEAD_SCRIPT, theme.CONTROL_SCRIPT):  # the key is config, never a literal of the code
        assert re.findall(r'var KEY = "([^"]*)";', script) == [theme.SLOT] and script.count(theme.SLOT) == 1


def test_a_key_cannot_break_out_of_the_script():
    for key in ("a", "A.b_c-9", "x" * 64):
        assert theme.KEY.fullmatch(key), key
    for key in ("", "x" * 65, 'a"b', "a\\b", "a</script>", "a b", "a\nb", "é"):
        assert not theme.KEY.fullmatch(key), key


def test_the_theme_group_shows_only_with_the_scripts():
    assert '[role="group"][aria-label="Theme"] { display: none; }' in pages.STYLE
    assert re.search(r'html\[data-js\] \[role="group"\]\[aria-label="Theme"\] \{ display: flex;', pages.STYLE)


def test_a_table_scrolls_in_its_box_under_a_sticky_header_row():
    table = re.search(r"\ntable \{([^}]*)\}", pages.STYLE)[1]
    assert "display: block;" in table and "overflow-x: auto;" in table and "max-height: 70vh;" in table
    assert re.search(r"\nth \{[^}]*position: sticky; top: 0; background: var\(--surface\);", pages.STYLE)
    assert re.search(r"\ntr:nth-child\(even\) td \{ background: var\(--stripe\); \}", pages.STYLE)
    assert re.search(r"\n\.badge \{[^}]*background: var\(--badge-bg\);\s*color: var\(--badge-fg\);", pages.STYLE)


def test_a_chart_scrolls_in_its_box():
    """On a phone a chart keeps its label size: it stays 35rem wide and scrolls sideways in its box."""
    box = re.search(r"\n\.chart-box \{([^}]*)\}", pages.STYLE)[1]
    assert "overflow-x: auto;" in box and "max-width: 100%;" in box
    assert "min-width: 35rem;" in re.search(r"\n\.chart \{([^}]*)\}", pages.STYLE)[1]


def test_the_touch_targets_and_the_focus_ring():
    """The option buttons and the theme buttons are each a button; the key field is the password input."""
    rules = dict(re.findall(r"\n([^{}\n]+?) \{([^{}]*)\}", pages.STYLE))
    assert "min-height: 44px;" in rules['button, input[type="password"]']
    assert "min-width: 44px;" in rules["button"]
    for selector, body in rules.items():  # no later rule lowers a button or the key field
        if "button" in selector or "input" in selector:
            assert not re.search(r"(?<![-\w])(min-)?(height|width|max-height|max-width):", body.replace(
                "min-height: 44px;", "").replace("min-width: 44px;", "")), selector
    focus = rules["a:focus-visible, button:focus-visible, input:focus-visible"]
    assert "outline: 2px solid var(--link);" in focus
    assert "overflow-wrap: anywhere;" in rules["main"] and "overflow-wrap: anywhere;" in rules["pre"]


def luminance(hex_colour: str) -> float:
    """The relative luminance of WCAG 2 for a #rrggbb colour."""
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# Each text token and each background it can sit on: the page, a card or the header bar, a stripe or a tile,
# and pre or code; then a badge and a pressed theme button.
PAIRS = ([(fg, bg) for fg in ("--fg", "--muted", "--link") for bg in ("--bg", "--surface", "--stripe", "--code-bg")]
         + [("--badge-fg", "--badge-bg"), ("--accent-fg", "--accent-bg")])


def test_each_text_passes_wcag_aa_in_each_mode():
    for mode in modes():
        assert all(re.fullmatch(r"#[0-9a-f]{6}", v) for k, v in mode.items() if k.startswith("--")), mode
        for fg, bg in PAIRS:
            assert contrast(mode[fg], mode[bg]) >= 4.5, (mode["color-scheme"], fg, bg, contrast(mode[fg], mode[bg]))


SERIES = [f"--series-{k}" for k in range(1, 7)] + ["--mark"]


def test_each_series_passes_3_to_1_in_each_mode():
    """The chart colours of the results page against a card (WCAG 1.4.11); the chart text is a text token."""
    for mode in modes():
        for token in SERIES:
            assert re.fullmatch(r"#[0-9a-f]{6}", mode[token]), (mode["color-scheme"], token)
            assert contrast(mode[token], mode["--surface"]) >= 3, (mode["color-scheme"], token)
    assert re.search(r"\n\.chart text \{ fill: var\(--(fg|muted)\);", pages.STYLE)
    assert not re.search(r"\.chart text[^{]*\{[^}]*fill: var\(--(?!fg|muted)", pages.STYLE)


def test_the_wcag_formula():
    assert round(contrast("#000000", "#ffffff"), 2) == 21.0 and contrast("#777777", "#777777") == 1.0
    assert round(contrast("#767676", "#ffffff"), 2) == 4.54  # the known lightest grey that passes on white


def test_the_style_loads_nothing():
    assert not re.search(r"url\(|@import|@font-face|data:|//", pages.STYLE)
