from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from memect.base.bbox import BBox
from memect.pdf.base import KCell, KChar, KDocument, KObject, KPage, KTable, KText


_CONNECTOR = {"指", "系", "即", "是"}
_HAS_COLON = re.compile(r"[：:]")


@dataclass
class _Line:
    page: KPage
    bbox: BBox
    text: str
    chars: Sequence[KChar]
    source: KText


@dataclass
class _Entry2:
    row_bbox: BBox
    col0_text: str
    col1_texts: list[str] = field(default_factory=list)
    col1_bboxes: list[BBox] = field(default_factory=list)
    sources: list[KText] = field(default_factory=list)

    def full_bbox(self) -> BBox:
        return BBox.join([self.row_bbox] + self.col1_bboxes)


@dataclass
class _Entry3:
    row_bbox: BBox
    col0_text: str
    col1_text: str
    col2_texts: list[str] = field(default_factory=list)
    col2_bboxes: list[BBox] = field(default_factory=list)
    sources: list[KText] = field(default_factory=list)

    def full_bbox(self) -> BBox:
        return BBox.join([self.row_bbox] + self.col2_bboxes)


def _get_lines(page: KPage) -> list[_Line]:
    lines: list[_Line] = []
    for obj in page.objects:
        if not isinstance(obj, KText):
            continue
        if obj.lines:
            for tl in obj.lines:
                text = tl.text.strip()
                if text:
                    lines.append(_Line(page, tl.bbox, text, tl.chars, obj))
        else:
            text = obj.text.strip()
            if text:
                lines.append(_Line(page, obj.bbox, text, (), obj))
    lines.sort(key=lambda l: (-l.bbox.y1, l.bbox.x0))
    return lines


def _split_blocks(lines: list[_Line]) -> list[list[_Line]]:
    if not lines:
        return []
    blocks: list[list[_Line]] = [[lines[0]]]
    for prev, line in zip(lines, lines[1:]):
        gap = prev.bbox.y0 - line.bbox.y1
        h = prev.bbox.y1 - prev.bbox.y0
        if gap > h * 1.5:
            blocks.append([line])
        else:
            blocks[-1].append(line)
    return blocks


def _cluster(vals: list[float], tol: float = 8) -> list[list[float]]:
    clusters: list[list[float]] = []
    for x in sorted(vals):
        if clusters and x - clusters[-1][0] <= tol:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    return clusters


def _detect_connector_col(lines: list[_Line]) -> float | None:
    hits: list[float] = []
    for line in lines:
        for ch in line.chars:
            if ch.text.strip() in _CONNECTOR:
                hits.append(ch.bbox.cx)
    if len(hits) < max(3, len(lines) // 2):
        return None
    best = max(_cluster(hits), key=len)
    if len(best) < max(3, len(lines) // 2):
        return None
    return sum(best) / len(best)


def _detect_two_col(lines: list[_Line]) -> float | None:
    """返回值续行的 x0（用作列分隔线），否则 None。
    信号：>=2 行无冒号且 x0 比含冒号行均值大 60pt 以上并互相对齐。
    """
    if len(lines) < 3:
        return None
    label_x0s = [l.bbox.x0 for l in lines if _HAS_COLON.search(l.text)]
    if not label_x0s:
        return None
    label_col_x = sum(label_x0s) / len(label_x0s)
    cont_x0s = [
        l.bbox.x0 for l in lines
        if not _HAS_COLON.search(l.text) and l.bbox.x0 > label_col_x + 60
    ]
    if len(cont_x0s) < 2:
        return None
    best = max(_cluster(cont_x0s), key=len)
    if len(best) < 2:
        return None
    return sum(best) / len(best)


def _replace(page: KPage, old: Sequence[KObject], table: KTable) -> None:
    old_ids = {id(o) for o in old}
    new: list[KObject] = []
    inserted = False
    for o in page.objects:
        if id(o) in old_ids:
            if not inserted:
                new.append(table)
                inserted = True
        else:
            new.append(o)
    if not inserted:
        new.append(table)
    page.objects.clear()
    page.objects.extend(new)


def _parse_two_col(lines: list[_Line], value_col_x: float) -> list[_Entry2]:
    tol = 10
    entries: list[_Entry2] = []
    cur: _Entry2 | None = None

    for line in lines:
        # 续行：无冒号且 x0 明显偏右
        if not _HAS_COLON.search(line.text) and line.bbox.x0 >= value_col_x - tol:
            if cur is not None:
                cur.col1_texts.append(line.text)
                cur.col1_bboxes.append(line.bbox)
                cur.sources.append(line.source)
            continue
        m = _HAS_COLON.search(line.text)
        if m:
            value = line.text[m.end():].strip()
            cur = _Entry2(
                row_bbox=line.bbox,
                col0_text=line.text[:m.end()].strip(),
                col1_texts=[value] if value else [],
                col1_bboxes=[line.bbox] if value else [],
                sources=[line.source],
            )
            entries.append(cur)
        elif cur is not None:
            # 无冒号、x0 不偏右：标签续行
            cur.col0_text += line.text
            cur.row_bbox = BBox.join([cur.row_bbox, line.bbox])
            cur.sources.append(line.source)

    return [e for e in entries if e.col0_text]


def _parse_three_col(lines: list[_Line], anchor_x: float) -> list[_Entry3]:
    tol = 12
    entries: list[_Entry3] = []
    cur: _Entry3 | None = None

    for line in lines:
        anchor_char = next(
            (ch for ch in line.chars
             if ch.text.strip() in _CONNECTOR and abs(ch.bbox.cx - anchor_x) <= tol),
            None,
        )
        if anchor_char is not None:
            left = "".join(c.text for c in line.chars if c.bbox.x1 <= anchor_char.bbox.x0).strip()
            right = "".join(c.text for c in line.chars if c.bbox.x0 >= anchor_char.bbox.x1).strip()
            if not left:
                continue
            cur = _Entry3(
                row_bbox=line.bbox,
                col0_text=left,
                col1_text=anchor_char.text,
                col2_texts=[right] if right else [],
                col2_bboxes=[line.bbox] if right else [],
                sources=[line.source],
            )
            entries.append(cur)
        elif cur is not None:
            if line.bbox.x0 > anchor_x + tol:
                cur.col2_texts.append(line.text)
                cur.col2_bboxes.append(line.bbox)
            else:
                cur.col0_text += line.text
                cur.row_bbox = BBox.join([cur.row_bbox, line.bbox])
            cur.sources.append(line.source)

    return [e for e in entries if e.col0_text and e.col2_texts]


def _make_two_col_table(page: KPage, entries: list[_Entry2], split_x: float) -> KTable:
    tb = BBox.join([e.full_bbox() for e in entries])
    x0, x1 = tb.x0, tb.x1
    cells: list[KCell] = []
    for row_i, e in enumerate(entries):
        fb = e.full_bbox()
        y0, y1 = fb.y0, fb.y1
        cells.append(KCell(page, BBox(x0, y0, split_x, y1), row_index=row_i, col_index=0,
                           objects=[KText(page, BBox(x0, y0, split_x, y1), text=e.col0_text)]))
        cells.append(KCell(page, BBox(split_x, y0, x1, y1), row_index=row_i, col_index=1,
                           objects=[KText(page, BBox(split_x, y0, x1, y1),
                                          text="".join(e.col1_texts))]))
    return KTable(page, tb, cells=cells, subtype="kv")


def _make_three_col_table(page: KPage, entries: list[_Entry3], anchor_x: float) -> KTable:
    tb = BBox.join([e.full_bbox() for e in entries])
    x0, x1 = tb.x0, tb.x1
    half = 8.0
    cells: list[KCell] = []
    for row_i, e in enumerate(entries):
        fb = e.full_bbox()
        y0, y1 = fb.y0, fb.y1
        for col_i, (bx0, bx1, text) in enumerate([
            (x0, anchor_x - half, e.col0_text),
            (anchor_x - half, anchor_x + half, e.col1_text),
            (anchor_x + half, x1, "".join(e.col2_texts)),
        ]):
            cells.append(KCell(page, BBox(bx0, y0, bx1, y1), row_index=row_i, col_index=col_i,
                               objects=[KText(page, BBox(bx0, y0, bx1, y1), text=text)]))
    return KTable(page, tb, cells=cells, subtype="kv")


class Feature:
    def __init__(self):
        super().__init__()

    def parse(self, doc: KDocument) -> list[KTable]:
        tables: list[KTable] = []
        for page in doc.working_pages:
            lines = _get_lines(page)
            blocks = _split_blocks(lines)
            for block in blocks:
                anchor_x = _detect_connector_col(block)
                if anchor_x is not None:
                    entries3 = _parse_three_col(block, anchor_x)
                    if len(entries3) < 2:
                        continue
                    table = _make_three_col_table(page, entries3, anchor_x)
                    sources = list({id(s): s for e in entries3 for s in e.sources}.values())
                else:
                    value_x = _detect_two_col(block)
                    if value_x is None:
                        continue
                    entries2 = _parse_two_col(block, value_x)
                    if len(entries2) < 2:
                        continue
                    table = _make_two_col_table(page, entries2, value_x)
                    sources = list({id(s): s for e in entries2 for s in e.sources}.values())
                _replace(page, sources, table)
                tables.append(table)
        return tables
