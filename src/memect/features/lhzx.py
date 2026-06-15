import re
from dataclasses import dataclass, field
from typing import Sequence

from memect.base import lists
from memect.base.bbox import BBox
from memect.pdf.base import KCell, KDocument, KObject, KPage, KTable, KText, TableIntent



@dataclass
class _Profile:
    page: KPage
    split_x: float
    left_objects: list[KObject] = field(default_factory=list)
    right_objects: list[KObject] = field(default_factory=list)


class Feature:
    def __init__(self):
        super().__init__()

    def parse(self, doc: KDocument) -> list[KTable]:
        pages = list(doc.working_pages)
        for i in range(len(pages)):
            group = self._build_group(pages, i)
            if group is None:
                continue
            x0, sx, x1 = self._bounds(group)
            tables:list[KTable] = []
            for p in group:
                table = self._make_table(p, x0=x0, sx=sx, x1=x1)
                self._replace(p.page, p.left_objects + p.right_objects, table)
                tables.append(table)
            return tables
        return []

    def _build_group(self, pages: list[KPage], i: int) -> list[_Profile] | None:
        # 第1页：去掉顶部标题后推断 gap，验证关键字
        p0 = self._profile_first(pages[i])
        if p0 is None:
            return None

        group: list[_Profile] = [p0]

        # 第2页：用同一 split_x，左右都有内容
        if i + 1 < len(pages) and pages[i + 1].number == pages[i].number + 1:
            p1 = self._profile(pages[i + 1], split_x=p0.split_x)
            if p1 and p1.left_objects and p1.right_objects:
                group.append(p1)
                # 第3页：左空右有
                if i + 2 < len(pages) and pages[i + 2].number == pages[i + 1].number + 1:
                    p2 = self._profile(pages[i + 2], split_x=p0.split_x)
                    if p2 and not p2.left_objects and p2.right_objects:
                        group.append(p2)

        return group if len(group) >= 2 else None

    def _profile_first(self, page: KPage) -> _Profile | None:
        objects = self._candidates(page)
        if not objects:
            return None

        # 最顶部的对象作为标题（y1 最大），从 body 里排除
        top_y1 = max(o.bbox.y1 for o in objects)
        title_ids = {id(o) for o in objects if o.bbox.y1 >= top_y1 - 5}
        body = [o for o in objects if id(o) not in title_ids]

        if len(body) < 2:
            return None

        split_x = self._infer_split(page, body)
        if split_x is None:
            return None

        left, right = self._split(page, body, split_x)
        if not left or not right:
            return None

        def has(objs: list[KObject], pattern: str) -> bool:
            return any(isinstance(o, KText) and re.search(pattern, o.text) for o in objs)

        if not has(left, r"评级结果[：:]?") or not has(right, r"评级观点[：:]?"):
            return None

        return _Profile(page=page, split_x=split_x, left_objects=left, right_objects=right)

    def _profile(self, page: KPage, *, split_x: float) -> _Profile | None:
        objects = self._candidates(page)
        if not objects:
            return None
        left, right = self._split(page, objects, split_x)
        return _Profile(page=page, split_x=split_x, left_objects=left, right_objects=right)

    def _candidates(self, page: KPage) -> list[KObject]:
        return [o for o in page.objects
                if not (isinstance(o, KTable) and o.intent == TableIntent.LAYOUT)]

    def _infer_split(self, page: KPage, objects: Sequence[KObject]) -> float | None:
        # 找所有对象在 x 轴的投影间隙，取最大 gap 作为分栏线
        usable = [o for o in objects if o.bbox.width < page.width * 0.8]
        if len(usable) < 2:
            return None
        edges = sorted((o.bbox.x0, o.bbox.x1) for o in usable)
        # 合并重叠区间，找最大间隙
        merged: list[list[float]] = []
        for x0, x1 in edges:
            if merged and x0 <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], x1)
            else:
                merged.append([x0, x1])
        if len(merged) < 2:
            return None
        gaps = [(merged[i + 1][0] - merged[i][1], merged[i][1], merged[i + 1][0])
                for i in range(len(merged) - 1)]
        gap_size, lx, rx = max(gaps)
        if gap_size < 6:
            return None
        return (lx + rx) / 2

    def _split(self, page: KPage, objects: Sequence[KObject], split_x: float)->tuple[list[KObject],list[KObject]]:
        lr = BBox(page.bbox.x0, page.bbox.y0, split_x, page.bbox.y1)
        rr = BBox(split_x, page.bbox.y0, page.bbox.x1, page.bbox.y1)
        left:list[KObject]=[]
        right:list[KObject] = []
        for o in objects:
            la = (lr.intersect(o.bbox) or BBox(0, 0, 0, 0)).area2
            ra = (rr.intersect(o.bbox) or BBox(0, 0, 0, 0)).area2
            (left if la >= ra else right).append(o)
        return left, right

    def _bounds(self, group: list[_Profile]) -> tuple[float, float, float]:
        all_objs = [o for p in group for o in p.left_objects + p.right_objects]
        b = BBox.join2(all_objs, strict=False)
        page = group[0].page
        x0 = max(page.bbox.x0, b.x0 - 2) if b else page.bbox.x0
        x1 = min(page.bbox.x1, b.x1 + 2) if b else page.bbox.x1
        sx = sum(p.split_x for p in group) / len(group)
        return x0, min(max(sx, x0 + 1), x1 - 1), x1

    def _make_table(self, p: _Profile, *, x0: float, sx: float, x1: float) -> KTable:
        page = p.page
        bb = BBox.join2(p.left_objects + p.right_objects, strict=False) or page.bbox
        cells = [
            KCell(page, BBox(x0, bb.y0, sx, bb.y1),
                  row_index=0, col_index=0, objects=p.left_objects),
            KCell(page, BBox(sx, bb.y0, x1, bb.y1),
                  row_index=0, col_index=1, objects=p.right_objects),
        ]
        t = KTable(page, BBox(x0, bb.y0, x1, bb.y1), cells=cells, subtype="wbk")
        t.intent = TableIntent.LAYOUT
        return t

    def _replace(self, page: KPage, old: Sequence[KObject], table: KTable):
        old_ids = {id(o) for o in old}
        new:list[KObject]=[]
        insert:bool=False
        for o in page.objects:
            if id(o) in old_ids:
                if not insert:
                    new.append(table)
                    insert=True
            else:
                new.append(o)
        
        if not insert:
            new.append(table)
        page.objects.clear()
        page.objects.extend(new)
