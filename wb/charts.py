"""Inline SVG charts for `wb pages`: pure functions, colour only by class.

Each chart is `<svg role="img" class="chart">` with a viewBox 640 wide and no width or height, so that the page CSS
scales it. bars and hbars draw 1 bar per row and take their height from the rows; stacked draws 1 column per row,
its series stacked bottom first; the columns stand side by side, so its height is the fixed BASE + 36 for any row
count. A series class `s1` to `s6` and `mark` get their colour from STYLE of wb/pages.py; the SVG itself holds no
colour, no style and no link. Each text is escaped.
"""

from __future__ import annotations

import html
import math

WIDTH = 640
LABEL_X, BAR_X, BAR_W = 0, 190, 380  # the label column, then the bars
ROW, BAR_H, TOP = 24, 16, 4
GAP = 2  # between 2 segments of a stack
COL_H = 160  # the height of the tallest column of a stack
SIDE = 8  # the margin left and right of the columns, so that the outer labels are not cut
BASE = TOP + 2 * ROW + COL_H  # the base line of the columns, below the legend and the total of the tallest column


def esc(v) -> str:
    return html.escape(str(v), quote=True)


def _svg(title: str, desc: str, height: int, parts: list[str]) -> str:
    return (f'<svg role="img" class="chart" viewBox="0 0 {WIDTH} {height}">\n<title>{esc(title)}</title>\n'
            f"<desc>{esc(desc)}</desc>\n" + "".join(parts) + "</svg>\n")


def _end(values, end) -> float:
    """The value at the right end of the axis: end, else the largest value, else 1."""
    return end or max([v for v in values if v is not None and math.isfinite(v)] + [0]) or 1


def _x(value: float, end: float, span: float = BAR_W) -> float:
    """The bar length of value on an axis of span; 0 for inf or nan, which JSON can give."""
    return round(min(max(value, 0), end) / end * span, 2) if math.isfinite(value) else 0


def _text(x: float, y: float, text) -> str:
    return f'<text x="{x}" y="{y + BAR_H - 4}">{esc(text)}</text>\n'


def _rows(rows: list[tuple], end: float, top: int) -> list[str]:
    parts = []
    for i, (label, value, text) in enumerate(rows):
        y = top + i * ROW
        w = 0 if value is None else _x(value, end)
        parts.append(_text(LABEL_X, y, label))
        if w:
            parts.append(f'<rect class="s1" x="{BAR_X}" y="{y}" width="{w}" height="{BAR_H}">'
                         f"<title>{esc(label)}: {esc(text)}</title></rect>\n")
        parts.append(_text(BAR_X + w + 6, y, text))
    return parts


def bars(title: str, desc: str, rows: list[tuple], end: float | None = None) -> str:
    """rows: (label, value or None, the value as text); a None value has no bar. end: the axis end."""
    return _svg(title, desc, TOP * 2 + ROW * len(rows), _rows(rows, _end((r[1] for r in rows), end), TOP))


def hbars(title: str, desc: str, rows: list[tuple], end: float | None = None) -> str:
    """bars with the mark line at the value 1.0; a value past end is drawn at end, and its text stays true."""
    end = _end([r[1] for r in rows] + [1.0], end)
    height = TOP * 2 + ROW * len(rows)
    x = BAR_X + _x(1.0, end)
    mark = f'<line class="mark" x1="{x}" y1="0" x2="{x}" y2="{height}"><title>100 %</title></line>\n'
    return _svg(title, desc, height, _rows(rows, end, TOP) + [mark])


def stacked(title: str, desc: str, series: list[str], rows: list[tuple]) -> str:
    """rows: (label, [(value, text) for each series], the total as text): 1 column for each row, left to right. The
    series stack in the given order, the first on the base line, with the legend above in the same order. The total
    stands above its column and the label below it; when the columns are narrow, every second label is 1 line lower."""
    parts, x = [], LABEL_X
    for k, name in enumerate(series, 1):
        parts.append(f'<rect class="s{k}" x="{x}" y="{TOP + 2}" width="12" height="12"></rect>\n'
                     + _text(x + 16, TOP, name))
        x += 24 + 7 * len(name)
    end = _end([sum(v for v, _ in segs if math.isfinite(v) and v > 0) for _, segs, _ in rows], None)  # drawn only
    span = COL_H - GAP * (len(series) - 1)  # room for the gaps
    slot = (WIDTH - 2 * SIDE) / max(len(rows), 1)
    width = round(slot * 0.6, 2)
    for i, (label, segs, total) in enumerate(rows):
        x, mid, y = round(SIDE + i * slot + (slot - width) / 2, 2), round(SIDE + (i + 0.5) * slot, 2), BASE
        parts.append(f'<text x="{mid}" y="{BASE + 16 + (14 if slot < 64 and i % 2 else 0)}" text-anchor="middle">'
                     f"{esc(label)}</text>\n")
        for k, ((value, text), name) in enumerate(zip(segs, series), 1):
            h = _x(value, end, span)
            if h:
                y = round(y - h, 2)
                parts.append(f'<rect class="s{k}" x="{x}" y="{y}" width="{width}" height="{h}">'
                             f"<title>{esc(label)} {esc(name)}: {esc(text)}</title></rect>\n")
                y -= GAP
        parts.append(f'<text x="{mid}" y="{round(y - 4, 2)}" text-anchor="middle">{esc(total)}</text>\n')
    return _svg(title, desc, BASE + 36, parts)
