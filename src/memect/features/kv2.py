from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final, Sequence

from memect.base.bbox import BBox
from memect.pdf.base import (
    KCell,
    KDocument,
    KObject,
    KPage,
    KTable,
    KText,
    KTextline,
    TableIntent,
)


@dataclass
class _Line:
    page: KPage
    bbox: BBox
    text: str
    source: KText
    textline: KTextline | None = None
    parts: list["_Line"] = field(default_factory=list)


@dataclass
class _KVStart:
    line: _Line
    key: str
    value: str
    key_x: float
    colon_x: float
    value_x: float


@dataclass
class KVRegion:
    page: KPage
    bbox: BBox
    lines: list[_Line]
    kv_lines: list[_KVStart]
    score: float
    reason: str
    key_x: float
    colon_x: float
    value_x: float

    def jsonify(self) -> dict[str, object]:
        return {
            "bbox": self.bbox.jsonify(),
            "score": round(self.score, 3),
            "reason": self.reason,
            "key_x": round(self.key_x, 1),
            "colon_x": round(self.colon_x, 1),
            "value_x": round(self.value_x, 1),
            "lines": [line.text for line in self.lines],
        }


@dataclass
class _KVRow:
    key: str
    value: str
    lines: list[_Line] = field(default_factory=list)

    @property
    def bbox(self) -> BBox:
        return BBox.join([line.bbox for line in self.lines])


class Feature:
    _CACHE_KEY: Final = "kv2_regions"
    _MIN_KV_LINES: Final = 3
    _TITLE_PATTERN: Final = re.compile(
        r"(本次)?发行(的)?有关机构|本次发行.*机构|发行有关机构|有关机构"
    )
    _MAJOR_TITLE_PATTERN: Final = re.compile(
        r"^[一二三四五六七八九十]+[、.．].+|^第[一二三四五六七八九十0-9]+[章节].+"
    )
    _GROUP_PREFIX_PATTERN: Final = re.compile(r"^[(（][一二三四五六七八九十0-9]+[)）]\s*")
    _START_KEY_PATTERN: Final = re.compile(
        r"发行人|法定代表人|注册地址|联系电话|联系人|保荐人|律师事务所|会计师事务所"
    )

    def __init__(self):
        super().__init__()

    def parse(self, doc: KDocument) -> list[KTable]:
        tables: list[KTable] = []
        for page in doc.working_pages:
            page_regions = self._parse_page(page)
            page.cache[self._CACHE_KEY] = [region.jsonify() for region in page_regions]
            for i,region in enumerate(page_regions):
                #table = self._make_table(region)
                #self._replace_region(region, table)
                table = page.make_table(region.bbox,use_vobj=True,add=True,clear=True,name='kv',index=i)
                tables.append(table)
        return tables

    def _parse_page(self, page: KPage) -> list[KVRegion]:
        lines = self._get_lines(page)
        if not lines:
            return []

        regions: list[KVRegion] = []
        used_line_ids: set[int] = set()

        for index, line in enumerate(lines):
            if id(line) in used_line_ids:
                continue
            if not self._is_anchor_title(line):
                continue
            region = self._parse_after_anchor(page, lines, index + 1)
            if region is None:
                continue
            regions.append(region)
            used_line_ids.update(id(line) for line in region.lines)

        for region in self._parse_without_anchor(page, lines, used_line_ids):
            regions.append(region)
            used_line_ids.update(id(line) for line in region.lines)

        return regions

    def _get_lines(self, page: KPage) -> list[_Line]:
        lines: list[_Line] = []
        for obj in page.objects:
            if not isinstance(obj, KText):
                continue
            if self._is_ignored_text(page, obj):
                continue
            if obj.lines:
                for textline in obj.lines:
                    text = textline.text.strip()
                    if text:
                        lines.append(
                            _Line(page, textline.bbox, text, obj, textline=textline)
                        )
            else:
                text = obj.text.strip()
                if text:
                    lines.append(_Line(page, obj.bbox, text, obj))

        lines.sort(key=lambda line: (-line.bbox.y1, line.bbox.x0))
        return self._merge_visual_rows(lines)

    def _merge_visual_rows(self, lines: Sequence[_Line]) -> list[_Line]:
        groups: list[list[_Line]] = []
        for line in lines:
            group = self._find_row_group(groups, line)
            if group is None:
                groups.append([line])
            else:
                group.append(line)

        merged: list[_Line] = []
        for group in groups:
            group.sort(key=lambda item: item.bbox.x0)
            if len(group) == 1:
                merged.append(group[0])
                continue

            key_line = self._key_line(group)
            bbox = BBox.join([line.bbox for line in group])
            text = " ".join(line.text for line in group if line.text.strip())
            merged.append(
                _Line(
                    key_line.page,
                    bbox,
                    text,
                    key_line.source,
                    textline=key_line.textline,
                    parts=list(group),
                )
            )

        merged.sort(key=lambda line: (-line.bbox.y1, line.bbox.x0))
        return merged

    def _find_row_group(
        self, groups: Sequence[list[_Line]], line: _Line
    ) -> list[_Line] | None:
        for group in groups:
            bbox = BBox.join([item.bbox for item in group])
            height = max(min(bbox.height, line.bbox.height), 1)
            if bbox.over("y", line.bbox, d=height * 0.55):
                return group
        return None

    def _key_line(self, lines: Sequence[_Line]) -> _Line:
        for line in lines:
            if self._find_colon_index(self._normalize_text(line.text)) is not None:
                return line
        return lines[0]

    def _is_ignored_text(self, page: KPage, text: KText) -> bool:
        bbox = text.bbox
        if not bbox.is_valid():
            return True
        vobject = getattr(text, "vobject", None)
        if vobject is not None and (vobject.is_header() or vobject.is_footer()):
            return True
        if bbox.y1 <= page.bbox.y0 + page.height * 0.04:
            return True
        if bbox.y0 >= page.bbox.y1 - page.height * 0.04:
            return True
        return False

    def _parse_after_anchor(
        self, page: KPage, lines: Sequence[_Line], start_index: int
    ) -> KVRegion | None:
        region_lines: list[_Line] = []
        kv_lines: list[_KVStart] = []
        started = False
        noise_count = 0
        last_y0: float | None = None

        for line in lines[start_index:]:
            if self._is_major_title(line) and started:
                break
            if started and last_y0 is not None and last_y0 - line.bbox.y1 > page.height * 0.08:
                break

            kv = self._parse_kv_start(line)
            if kv is not None:
                region_lines.append(line)
                kv_lines.append(kv)
                started = True
                noise_count = 0
                last_y0 = line.bbox.y0
                continue

            if not started:
                if self._is_major_title(line):
                    break
                continue

            if self._is_continuation(line, kv_lines):
                region_lines.append(line)
                noise_count = 0
                last_y0 = line.bbox.y0
                continue

            noise_count += 1
            if noise_count >= 2:
                break

        return self._make_region(page, region_lines, kv_lines, reason="anchor")

    def _parse_without_anchor(
        self, page: KPage, lines: Sequence[_Line], used_line_ids: set[int]
    ) -> list[KVRegion]:
        regions: list[KVRegion] = []
        i = 0
        while i < len(lines):
            if id(lines[i]) in used_line_ids or self._is_major_title(lines[i]):
                i += 1
                continue
            start_kv = self._parse_kv_start(lines[i])
            if start_kv is None or not self._is_start_key(start_kv.key):
                i += 1
                continue

            region_lines: list[_Line] = []
            kv_lines: list[_KVStart] = []
            noise_count = 0
            j = i
            last_y0: float | None = None
            while j < len(lines):
                line = lines[j]
                if id(line) in used_line_ids:
                    break
                if self._is_major_title(line) and region_lines:
                    break
                if (
                    region_lines
                    and last_y0 is not None
                    and last_y0 - line.bbox.y1 > page.height * 0.08
                ):
                    break

                kv = self._parse_kv_start(line)
                if kv is not None:
                    region_lines.append(line)
                    kv_lines.append(kv)
                    noise_count = 0
                    last_y0 = line.bbox.y0
                elif region_lines and self._is_continuation(line, kv_lines):
                    region_lines.append(line)
                    noise_count = 0
                    last_y0 = line.bbox.y0
                elif region_lines:
                    noise_count += 1
                    if noise_count >= 2:
                        break
                j += 1

            region = self._make_region(page, region_lines, kv_lines, reason="cluster")
            if region is not None:
                regions.append(region)
                i = j
            else:
                i += 1
        return regions

    def _parse_kv_start(self, line: _Line) -> _KVStart | None:
        text = self._normalize_text(line.text)
        colon_index = self._find_colon_index(text)
        if colon_index is None:
            return None

        key = text[:colon_index].strip()
        value = text[colon_index + 1 :].strip()
        key = self._GROUP_PREFIX_PATTERN.sub("", key).strip()
        if not self._is_valid_key(key):
            return None

        key_x = self._key_x(line)
        colon_x = self._colon_x(line, colon_index)
        if colon_x is None:
            return None
        value_x = self._value_x(line, colon_index, colon_x)
        if value_x <= colon_x:
            value_x = colon_x + max(8.0, line.bbox.height * 0.6)
        return _KVStart(
            line=line,
            key=key,
            value=value,
            key_x=key_x,
            colon_x=colon_x,
            value_x=value_x,
        )

    def _make_region(
        self,
        page: KPage,
        region_lines: Sequence[_Line],
        kv_lines: Sequence[_KVStart],
        *,
        reason: str,
    ) -> KVRegion | None:
        if len(kv_lines) < self._MIN_KV_LINES:
            return None
        if not self._is_aligned_kv_block(page, kv_lines):
            return None

        bbox = BBox.join([line.bbox for line in region_lines])
        dx = max(4.0, page.width * 0.006)
        dy = max(3.0, page.height * 0.004)
        bbox = bbox.expand(dx=dx, dy=dy, bound=page.bbox)

        key_x = self._median([kv.key_x for kv in kv_lines])
        colon_x = self._median([kv.colon_x for kv in kv_lines])
        value_x = self._median([kv.value_x for kv in kv_lines])
        score = self._score_region(page, region_lines, kv_lines, key_x, colon_x, value_x)
        return KVRegion(
            page=page,
            bbox=bbox,
            lines=list(region_lines),
            kv_lines=list(kv_lines),
            score=score,
            reason=reason,
            key_x=key_x,
            colon_x=colon_x,
            value_x=value_x,
        )

    def _make_table(self, region: KVRegion) -> KTable:
        rows = self._make_rows(region)
        page = region.page
        split_x = min(max(region.value_x, region.bbox.x0 + 1), region.bbox.x1 - 1)
        cells: list[KCell] = []
        for row_index, row in enumerate(rows):
            row_bbox = row.bbox
            key_bbox = BBox(region.bbox.x0, row_bbox.y0, split_x, row_bbox.y1)
            value_bbox = BBox(split_x, row_bbox.y0, region.bbox.x1, row_bbox.y1)
            cells.append(
                KCell(
                    page,
                    key_bbox,
                    row_index=row_index,
                    col_index=0,
                    objects=[KText(page, key_bbox, text=row.key)] if row.key else [],
                )
            )
            cells.append(
                KCell(
                    page,
                    value_bbox,
                    row_index=row_index,
                    col_index=1,
                    objects=[KText(page, value_bbox, text=row.value)] if row.value else [],
                )
            )

        table = KTable(page, region.bbox, cells=cells, subtype="kv2")
        table.intent = TableIntent.DATA
        table.cache["kv2"] = region.jsonify()
        return table

    def _make_rows(self, region: KVRegion) -> list[_KVRow]:
        rows: list[_KVRow] = []
        for line in region.lines:
            kv = self._parse_kv_start(line)
            if kv is not None:
                rows.append(_KVRow(key=kv.key, value=kv.value, lines=[line]))
            elif rows:
                rows[-1].lines.append(line)
                value = line.text.strip()
                if value:
                    if rows[-1].value:
                        rows[-1].value = f"{rows[-1].value} {value}"
                    else:
                        rows[-1].value = value

        return rows

    def _replace_region(self, region: KVRegion, table: KTable):
        consumed_sources = {id(line.source) for line in self._iter_original_lines(region)}
        consumed_textlines = {
            id(line.textline)
            for line in self._iter_original_lines(region)
            if line.textline is not None
        }
        page = region.page
        new_objects: list[KObject] = []
        inserted = False
        for obj in page.objects:
            if id(obj) not in consumed_sources:
                new_objects.append(obj)
                continue

            if not inserted:
                new_objects.append(table)
                inserted = True

            if isinstance(obj, KText) and obj.lines:
                remaining = [
                    textline
                    for textline in obj.lines
                    if id(textline) not in consumed_textlines
                ]
                if remaining:
                    bbox = BBox.join2(remaining)
                    new_objects.append(KText(page, bbox, lines=remaining))

        if not inserted:
            new_objects.append(table)

        page.objects.clear()
        page.objects.extend(new_objects)

    def _iter_original_lines(self, region: KVRegion) -> list[_Line]:
        lines: list[_Line] = []
        for line in region.lines:
            if line.parts:
                lines.extend(line.parts)
            else:
                lines.append(line)
        return lines

    def _is_aligned_kv_block(self, page: KPage, kv_lines: Sequence[_KVStart]) -> bool:
        key_xs = [kv.key_x for kv in kv_lines]
        value_xs = [kv.value_x for kv in kv_lines]
        tolerance = max(18.0, page.width * 0.025)
        key_ok = self._spread_around_median(key_xs) <= max(110.0, page.width * 0.18)
        value_ok = self._spread_around_median(value_xs) <= max(42.0, page.width * 0.07)
        geometry_ok = self._median(value_xs) > self._median(key_xs) + page.width * 0.08
        return key_ok and value_ok and geometry_ok

    def _score_region(
        self,
        page: KPage,
        region_lines: Sequence[_Line],
        kv_lines: Sequence[_KVStart],
        key_x: float,
        colon_x: float,
        value_x: float,
    ) -> float:
        line_ratio = len(kv_lines) / max(len(region_lines), 1)
        count_score = min(len(kv_lines) / 8, 1)
        align_spread = (
            self._spread_around_median([kv.key_x for kv in kv_lines])
            + self._spread_around_median([kv.value_x for kv in kv_lines])
        )
        align_score = max(0.0, 1 - align_spread / max(page.width * 0.25, 1))
        geometry_score = 1.0 if key_x < colon_x < value_x else 0.4
        return count_score * 0.35 + line_ratio * 0.25 + align_score * 0.25 + geometry_score * 0.15

    def _is_continuation(self, line: _Line, kv_lines: Sequence[_KVStart]) -> bool:
        if not kv_lines:
            return False
        text = self._normalize_text(line.text)
        if not text or self._find_colon_index(text) is not None:
            return False
        if self._is_major_title(line):
            return False

        value_x = self._median([kv.value_x for kv in kv_lines])
        colon_x = self._median([kv.colon_x for kv in kv_lines])
        if line.bbox.x0 >= value_x - 18:
            return True
        if line.bbox.x0 > colon_x + 8:
            return True
        return False

    def _is_anchor_title(self, line: _Line) -> bool:
        return self._TITLE_PATTERN.search(self._normalize_text(line.text)) is not None

    def _is_major_title(self, line: _Line) -> bool:
        text = self._normalize_text(line.text)
        if self._GROUP_PREFIX_PATTERN.match(text):
            return False
        return self._MAJOR_TITLE_PATTERN.match(text) is not None

    def _is_valid_key(self, key: str) -> bool:
        if not key:
            return False
        if len(key) > 24:
            return False
        if any(ch in key for ch in "，,。；;"):
            return False
        return True

    def _is_start_key(self, key: str) -> bool:
        return self._START_KEY_PATTERN.search(key) is not None

    def _find_colon_index(self, text: str) -> int | None:
        indexes = [index for index in (text.find("："), text.find(":")) if index >= 0]
        if not indexes:
            return None
        return min(indexes)

    def _colon_x(self, line: _Line, colon_index: int) -> float | None:
        chars = line.textline.chars if line.textline is not None else ()
        for char in chars:
            if char.text in {":", "："}:
                return char.bbox.cx
        source_text = line.source.text.strip()
        if not source_text:
            source_text = line.text
        if not source_text:
            return None
        source_colon_index = self._find_colon_index(self._normalize_text(source_text))
        if source_colon_index is None:
            source_colon_index = colon_index
        ratio = max(0.0, min(1.0, source_colon_index / max(len(source_text), 1)))
        return line.source.bbox.x0 + line.source.bbox.width * ratio

    def _value_x(self, line: _Line, colon_index: int, colon_x: float) -> float:
        right_parts = [
            part
            for part in line.parts
            if part.source is not line.source and part.bbox.x0 > line.source.bbox.x1
        ]
        if right_parts:
            return min(part.bbox.x0 for part in right_parts)

        chars = line.textline.chars if line.textline is not None else ()
        if chars:
            start = 0
            for index, char in enumerate(chars):
                if char.text in {":", "："}:
                    start = index + 1
                    break
            for char in chars[start:]:
                if not char.text.isspace():
                    return char.bbox.x0
        if line.bbox.x1 > line.source.bbox.x1 + 4:
            return line.source.bbox.x1 + max(8.0, line.bbox.height * 0.6)
        return colon_x + max(8.0, line.bbox.height * 0.6)

    def _key_x(self, line: _Line) -> float:
        text = line.source.text.strip()
        chars = line.textline.chars if line.textline is not None else ()
        match = self._GROUP_PREFIX_PATTERN.match(text)
        if match is None:
            return line.source.bbox.x0

        start = match.end()
        if chars:
            for char in chars[start:]:
                if not char.text.isspace():
                    return char.bbox.x0
        ratio = max(0.0, min(1.0, start / max(len(text), 1)))
        return line.source.bbox.x0 + line.source.bbox.width * ratio

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", "", text.strip())

    def _median(self, values: Sequence[float]) -> float:
        assert values
        values = sorted(values)
        mid = len(values) // 2
        if len(values) % 2 == 1:
            return values[mid]
        return (values[mid - 1] + values[mid]) / 2

    def _spread_around_median(self, values: Sequence[float]) -> float:
        median = self._median(values)
        return max(abs(value - median) for value in values)
