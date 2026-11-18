"""wb/charts.py, the inline SVG charts of the results page."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from wb import charts, pages

SVG = "{http://www.w3.org/2000/svg}"


def tree(svg: str) -> ET.Element:
    """The parsed chart; the page puts it inline in HTML, so it holds no xmlns."""
    return ET.fromstring(svg.replace("<svg ", f'<svg xmlns="{SVG[1:-1]}" ', 1))


def tag(e: ET.Element) -> str:
    return e.tag.removeprefix(SVG)


def texts(svg: str) -> list[str]:
    return [e.text for e in tree(svg).iter(f"{SVG}text")]


def bars_of(svg: str) -> list[dict]:
    return [{**r.attrib, "title": r.find(f"{SVG}title").text} for r in tree(svg).iter(f"{SVG}rect")
            if r.find(f"{SVG}title") is not None]


ROWS = [("2026-W43", 1, "1"), ("2026-W44", 3, "3"), ("<b>&", 0, "0")]


def test_bars_shape():
    svg = charts.bars("Delivered per week", "Items per ISO week", ROWS)
    root = tree(svg)
    assert tag(root) == "svg" and root.get("role") == "img" and root.get("class") == "chart"
    assert "width" not in root.attrib and "height" not in root.attrib
    assert [tag(e) for e in root][:2] == ["title", "desc"]
    assert root.find(f"{SVG}title").text == "Delivered per week"
    assert root.find(f"{SVG}desc").text == "Items per ISO week"
    x, y, w, h = root.get("viewBox").split()
    assert (x, y, w) == ("0", "0", "640")
    taller = charts.bars("t", "d", ROWS + [("x", 2, "2")])
    assert int(tree(taller).get("viewBox").split()[3]) > int(h) > 0  # the height grows with the rows
    assert texts(svg) == ["2026-W43", "1", "2026-W44", "3", "<b>&", "0"]  # each label and value in text
    assert "&lt;b&gt;&amp;" in svg  # escaped
    rects = bars_of(svg)
    assert [r["title"] for r in rects] == ["2026-W43: 1", "2026-W44: 3"]  # no bar for 0
    assert float(rects[1]["width"]) == pytest.approx(3 * float(rects[0]["width"]), abs=0.02)
    none = charts.bars("t", "d", [("a", None, "no data"), ("b", 2.0, "2.00")])
    assert texts(none) == ["a", "no data", "b", "2.00"] and len(bars_of(none)) == 1
    odd = charts.stacked("t", "d", ["x", "y"], [("w", [(float("inf"), "inf"), (float("nan"), "nan")], "inf"),
                                               ("v", [(2.0, "2.00"), (0.0, "0.00")], "2.00")])
    assert "nan" not in odd.replace(">nan<", "") and "inf\"" not in odd  # no bar for a value that is not finite
    assert [r["title"] for r in bars_of(odd)] == ["v x: 2.00"] and float(bars_of(odd)[0]["width"]) > 0


SERIES = ["spec-checker", "builder", "reviewer"]


def test_stacked_order_and_legend():
    rows = [("2026-W44", [(1.0, "1.00"), (2.0, "2.00"), (0.5, "0.50")], "3.50"),
            ("2026-W43", [(0.0, "0.00"), (4.0, "4.00"), (0.0, "0.00")], "4.00")]
    svg = charts.stacked("USD per week by role", "d", SERIES, rows)
    root = tree(svg)
    assert texts(svg)[:3] == SERIES  # the legend, in the stack order, left to right
    legend = [r for r in root.iter(f"{SVG}rect") if r.find(f"{SVG}title") is None]
    assert [r.get("class") for r in legend] == ["s1", "s2", "s3"]
    assert [float(r.get("x")) for r in legend] == sorted(float(r.get("x")) for r in legend)
    assert texts(svg)[3:] == ["2026-W44", "3.50", "2026-W43", "4.00"]
    segs = bars_of(svg)
    assert [(s["class"], s["title"]) for s in segs] == [
        ("s1", "2026-W44 spec-checker: 1.00"), ("s2", "2026-W44 builder: 2.00"), ("s3", "2026-W44 reviewer: 0.50"),
        ("s2", "2026-W43 builder: 4.00")]
    first = segs[:3]
    assert len({s["x"] for s in first}) == 1 and float(segs[3]["x"]) > float(first[0]["x"])  # 1 column a row
    for s in (first[0], segs[3]):  # the first series that has a value stands on the base line
        assert float(s["y"]) + float(s["height"]) == pytest.approx(charts.BASE)
    for a, b in zip(first, first[1:]):  # then the next series above it, with a gap between: bottom first
        assert float(a["y"]) - (float(b["y"]) + float(b["height"])) == pytest.approx(charts.GAP)
    assert float(segs[1]["height"]) == pytest.approx(2 * float(segs[0]["height"]), abs=0.02)
    assert len({s["width"] for s in segs}) == 1
    height = float(root.get("viewBox").split()[3])
    assert all(float(t.get("y")) <= height for t in root.iter(f"{SVG}text"))  # each label inside the chart


def inside(svg: str) -> float:
    """The viewBox height, after a check that each rect and each text anchor lies inside the viewBox."""
    root = tree(svg)
    height = float(root.get("viewBox").split()[3])
    for r in root.iter(f"{SVG}rect"):
        x, y, w, h = (float(r.get(k)) for k in ("x", "y", "width", "height"))
        assert 0 <= x and x + w <= charts.WIDTH and 0 <= y and y + h <= height, r.attrib
    for t in root.iter(f"{SVG}text"):
        assert 0 <= float(t.get("x")) <= charts.WIDTH and 0 < float(t.get("y")) <= height, t.attrib
    return height


def test_stacked_height_is_fixed():
    def rows(n):
        return [(f"2026-W{k:02}", [(1.0 + k, "x"), (2.0, "y"), (0.5, "z")], "t") for k in range(n)]
    one, twelve = (charts.stacked("t", "d", SERIES, rows(n)) for n in (1, 12))
    assert inside(one) == inside(twelve) == charts.BASE + 36  # its rows are columns side by side


def test_stacked_columns_stay_in_the_chart():
    """A negative or inf segment, which a step usd can give, draws nothing and does not move the axis end."""
    inf = float("inf")
    for b, tallest in ((5.0, "a"), (30.0, "b")):  # 30: the column with inf is the tallest by its drawn segments
        rows = [("a", [(10.0, "10"), (10.0, "10"), (-19.0, "-19")], "1"),
                ("b", [(inf, "inf"), (b, "b"), (b, "b")], "inf"), ("c", [(3.0, "3"), (0.0, "0"), (0.0, "0")], "3")]
        svg = charts.stacked("t", "d", SERIES, rows)
        inside(svg)
        tops = {}
        for s in bars_of(svg):
            y, h = float(s["y"]), float(s["height"])
            assert y >= charts.BASE - charts.COL_H and y + h <= charts.BASE, s
            tops[s["title"][0]] = min(y, tops.get(s["title"][0], charts.BASE))
        assert min(tops, key=tops.get) == tallest
        totals = [t for t in tree(svg).iter(f"{SVG}text") if t.text in ("1", "inf", "3")]
        assert len(totals) == 3 and all(float(t.get("y")) > 0 for t in totals)


def test_hbars_mark_at_100():
    rows = [("1 USD", 2.5, "250 %"), ("1 hours", 0.5, "50 %"), ("2 USD", None, "no data")]
    svg = charts.hbars("Budget use", "d", rows, end=2.0)
    root = tree(svg)
    assert texts(svg) == ["1 USD", "250 %", "1 hours", "50 %", "2 USD", "no data"]
    marks = [e for e in root.iter(f"{SVG}line") if e.get("class") == "mark"]
    assert len(marks) == 1
    x = float(marks[0].get("x1"))
    assert x == float(marks[0].get("x2")) == charts.BAR_X + charts.BAR_W / 2  # 1.0 of an axis that ends at 2.0
    rects = bars_of(svg)
    assert float(rects[0]["width"]) == charts.BAR_W  # 250 % is drawn at the end of the axis
    assert charts.BAR_X + float(rects[1]["width"]) == charts.BAR_X + charts.BAR_W / 4
    plain = tree(charts.hbars("t", "d", [("a", 4.0, "4")]))
    line = plain.find(f"{SVG}line")
    assert float(line.get("x1")) == charts.BAR_X + charts.BAR_W / 4  # the axis ends at the largest value


COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(|"
                    r"(?<![\w-])(white|black|red|green|blue|gray|grey|silver|navy|yellow|orange|purple|pink)(?![\w-])",
                    re.I)


def test_charts_hold_no_colour():
    rows = [("a", 1.0, "1"), ("b", 2.0, "2")]
    for svg in (charts.bars("t", "d", rows), charts.hbars("t", "d", rows),
                charts.stacked("t", "d", ["builder", "other"], [("w", [(1.0, "1"), (2.0, "2")], "3")])):
        for e in tree(svg).iter():
            assert tag(e) != "style", svg
            assert not {"fill", "stroke", "style", "href", "color"} & {k.split("}")[-1] for k in e.attrib}, e.attrib
            for v in [*e.attrib.values(), e.text or ""]:
                assert not COLOUR.search(v), v
        assert "href" not in svg and "style" not in svg
        classes = {e.get("class") for e in tree(svg).iter() if e.get("class")}
        assert classes <= {"chart", "mark", "s1", "s2", "s3", "s4", "s5", "s6"}, classes
    for k in range(1, 7):  # each class takes its colour from its token in STYLE
        assert f".chart .s{k} {{ fill: var(--series-{k}); }}" in pages.STYLE
    assert ".chart .mark { stroke: var(--mark);" in pages.STYLE
