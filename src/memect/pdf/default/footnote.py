import re
import unicodedata
from statistics import median
from typing import Final, Sequence, TypeGuard

from memect.base.bbox import BBox

from ..base import KDocument, KLine, KObject, KPage, KPageFootnote, KText


class PageFootnoteParser:
    _bottom_ratio: Final = 0.28
    _marked_expand_x: Final = 8
    _marked_expand_y: Final = 6
    _line_expand_y: Final = 4
    _line_text_gap: Final = 45
    _region_hit_ratio: Final = 0.45
    _small_font_ratio: Final = 0.88
    _footnote_start_pattern: Final = re.compile(
        r"^\s*(?:(?:\d{1,3}(?!\.\d)[\s、.．）)]*)|[\[\(（【]\d{1,3}[\]\)）】]|[①②③④⑤⑥⑦⑧⑨⑩]|[*＊†‡])"
    )
    _section_title_pattern: Final = re.compile(r"^\s*\d+(?:\.\d+)+\.?\s+")
    _source_pattern: Final = re.compile(
        r"^\s*(?:(?:资料|数据|信息|图表|图片|表格|本文|报告|素材|案例|参考)?来源|"
        r"来源(?:资料|数据|信息)?|"
        r"source|data\s+source)\s*[:：]",
        flags=re.IGNORECASE,
    )
    _circled_note_numbers: Final = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
    _circled_note_number_values: Final = {
        char: value for value, char in enumerate(_circled_note_numbers, start=1)
    }

    def __init__(self):
        super().__init__()

    def parse(self, doc: KDocument) -> None:
        # Word 6.x/95/97 默认脚注是跟随分栏的，如：
        # -----------|-----------
        # -----------|-footnote-     => 如果没有空间，到下一页的分栏后
        # -----跨页-----------
        # -----------|---------
        # --footnote-|

        # 之后的脚注（分栏）
        # ------------|-----------
        # -footnote---|-footnote--   => 跨栏
        # --------跨页-------------

        # 不分栏
        # ------------------
        # ---footnote------- 如果没有足够的空间，到下一页
        # ----跨页-----------
        # ------------------
        # ---footnote-------
        prev_has_footnote = False
        prev_page: KPage | None = None
        for page in doc.working_pages:
            self._parse_page(page, prev_page=prev_page, prev_has_footnote=prev_has_footnote)
            prev_has_footnote = len(page.footnotes) > 0
            prev_page = page
        
        #TODO 然后汇总所有的脚注，建立一个全局的footnotes，然后变成这样：
        #1.xxxxx       =>在引用页面找到“1”字符，建立关联KFootnoteRef()
        #2.xxxxx
        #3.xxxxx

        #有些脚注开启了list模式，变成
        #(i)  1xxxx    =>1才是引用序号
        #(ii) 2xxx


    def _parse_page(
        self,
        page: KPage,
        *,
        prev_page: KPage | None = None,
        prev_has_footnote: bool = False,
    ) -> None:
        page.footnotes.clear()

        marked = self._collect_marked(page.objects)
        regions: list[BBox] = []
        if marked:
            regions.extend(self._regions_from_marked(page, marked))

        line_regions = self._regions_from_lines(page, prev_has_footnote=prev_has_footnote)
        regions.extend(line_regions)
        regions.extend(self._case1(prev_page, page))

        regions = self._merge_regions(regions)
        if not regions:
            return

        footnotes = self._take_objects(page, regions, marked)
        page.footnotes.extend(footnotes)
        
    def _collect_marked(self, objects: Sequence[KObject]) -> list[KObject]:
        return [
            obj
            for obj in objects
            if obj.vobject is not None and obj.vobject.is_footnote()
        ]

    def _regions_from_marked(self, page: KPage, marked: Sequence[KObject]) -> list[BBox]:
        regions: list[BBox] = []
        if page.sections:
            for section in page.sections:
                for column in section.columns:
                    objs = [obj for obj in marked if self._center_in(obj.bbox, column)]
                    if objs:
                        regions.append(self._expand_marked_region(page, BBox.join2(objs), column=column))
        else:
            regions.append(self._expand_marked_region(page, BBox.join2(marked)))
        return regions

    def _regions_from_lines(
        self,
        page: KPage,
        *,
        prev_has_footnote: bool = False,
    ) -> list[BBox]:
        regions: list[BBox] = []
        body_font_size = self._body_font_size(page.objects)
        for line in self._candidate_footnote_lines(page):
            line_bbox = line.bbox
            column = self._find_column(page, line_bbox)
            region = self._region_below_line(page, line_bbox, column=column)
            text_candidates = self._near_line_texts(page.objects, line_bbox, region)
            if not text_candidates:
                continue
            has_footnote_start = self._has_footnote_start(text_candidates)
            if body_font_size is not None:
                sizes = [s for obj in text_candidates if (s := self._font_size(obj)) is not None]
                if sizes and median(sizes) > body_font_size * self._small_font_ratio:
                    continue
            if not prev_has_footnote and not has_footnote_start:
                continue
            text_bbox = BBox.join2(text_candidates)
            regions.append(self._expand_to_footnote_region(page, text_bbox, column=column))
        return regions

    def _candidate_footnote_lines(self, page: KPage) -> list[KLine]:
        bottom = self._bottom_bbox(page)
        lines: list[KLine] = []
        for line in page.pdf_lines:
            if not line.is_h():
                continue
            bbox = line.bbox
            if not bottom.contains(bbox.center):
                continue
            if bbox.width < 20:
                continue
            if bbox.width > page.bbox.width * 0.75:
                continue
            if self._is_table_line(page, bbox):
                continue
            # 脚注分隔线下方需要有内容，否则更可能是页脚装饰线。
            below = self._region_below_line(page, bbox, column=self._find_column(page, bbox))
            if self._near_line_texts(page.objects, bbox, below):
                lines.append(line)
        return lines

    def _region_below_line(self, page: KPage, line: BBox, *, column: BBox | None = None) -> BBox:
        x0 = column.x0 if column is not None else max(page.bbox.x0, line.x0 - self._marked_expand_x)
        x1 = column.x1 if column is not None else min(page.bbox.x1, max(line.x1 + self._marked_expand_x, line.x0 + page.bbox.width * 0.35))
        y1 = max(page.bbox.y0, line.y0 - self._line_expand_y)
        y0 = page.bbox.y0
        footer_bbox = page.footer.content_bbox
        if footer_bbox is not None and footer_bbox.y1 < y1:
            y0 = max(y0, footer_bbox.y1 + 1)
        if y1 <= y0:
            y0 = page.bbox.y0
        return BBox(x0, y0, x1, y1)

    def _expand_to_footnote_region(self, page: KPage, bbox: BBox, *, column: BBox | None = None) -> BBox:
        x0 = column.x0 if column is not None else max(page.bbox.x0, bbox.x0 - self._marked_expand_x)
        x1 = column.x1 if column is not None else min(page.bbox.x1, bbox.x1 + self._marked_expand_x)
        y0 = max(page.bbox.y0, bbox.y0 - self._marked_expand_y)
        y1 = min(page.bbox.y1, bbox.y1 + self._marked_expand_y)
        footer_bbox = page.footer.content_bbox
        if footer_bbox is not None and footer_bbox.y1 < y1:
            y0 = max(y0, footer_bbox.y1 + 1)
        return BBox(x0, y0, x1, y1)

    def _expand_marked_region(self, page: KPage, bbox: BBox, *, column: BBox | None = None) -> BBox:
        x0 = column.x0 if column is not None else max(page.bbox.x0, bbox.x0 - self._marked_expand_x)
        x1 = column.x1 if column is not None else min(page.bbox.x1, bbox.x1 + self._marked_expand_x)
        y0 = page.bbox.y0
        y1 = min(page.bbox.y1, bbox.y1 + self._marked_expand_y)
        footer_bbox = page.footer.content_bbox
        if footer_bbox is not None and footer_bbox.y1 < y1:
            y0 = max(y0, footer_bbox.y1 + 1)
        return BBox(x0, y0, x1, y1)

    def _bottom_bbox(self, page: KPage) -> BBox:
        return BBox(
            page.bbox.x0,
            page.bbox.y0,
            page.bbox.x1,
            page.bbox.y0 + page.bbox.height * self._bottom_ratio,
        )

    def _take_objects(
        self, page: KPage, regions: Sequence[BBox], marked: Sequence[KObject]
    ) -> list[KPageFootnote]:
        marked_ids = {id(obj) for obj in marked}
        groups: list[list[KObject]] = [[] for _ in regions]

        i = 0
        while i < len(page.objects):
            obj = page.objects[i]
            region_index = self._match_region(obj, regions, force=id(obj) in marked_ids)
            if region_index is None:
                i += 1
                continue
            groups[region_index].append(obj)
            del page.objects[i]

        footnotes: list[KPageFootnote] = []
        for region, objects in zip(regions, groups):
            if not objects:
                continue
            bbox = BBox.join2(objects)
            footnote = KPageFootnote(page, bbox.to_quad())
            footnote.objects.extend(sorted(objects, key=lambda obj: (-obj.bbox.y1, obj.bbox.x0)))
            footnotes.append(footnote)
        return footnotes

    def _match_region(
        self, obj: KObject, regions: Sequence[BBox], *, force: bool = False
    ) -> int | None:
        if self._is_ignored(obj):
            return None
        best_index: int | None = None
        best_ratio = 0.0
        for i, region in enumerate(regions):
            ratio = self._intersect_ratio(region, obj.bbox)
            if ratio > best_ratio:
                best_ratio = ratio
                best_index = i
            if region.contains(obj.bbox.center):
                best_ratio = max(best_ratio, self._region_hit_ratio)
                best_index = i
        if force and best_index is not None:
            return best_index
        if best_ratio >= self._region_hit_ratio:
            return best_index
        return None

    def _merge_regions(self, regions: Sequence[BBox]) -> list[BBox]:
        merged: list[BBox] = []
        for region in regions:
            if region.area2 <= 0:
                continue
            for i, existing in enumerate(merged):
                if existing.intersect(region) is not None or abs(existing.y0 - region.y0) <= 3 and abs(existing.y1 - region.y1) <= 3:
                    merged[i] = BBox.join([existing, region])
                    break
            else:
                merged.append(region)
        return merged

    def _objects_in_region(
        self, objects: Sequence[KObject], region: BBox, *, min_ratio: float
    ) -> list[KObject]:
        return [
            obj
            for obj in objects
            if not self._is_ignored(obj)
            and (
                self._intersect_ratio(region, obj.bbox) >= min_ratio
                or region.contains(obj.bbox.center)
            )
        ]

    def _near_line_texts(
        self, objects: Sequence[KObject], line: BBox, region: BBox
    ) -> list[KText]:
        texts: list[KText] = []
        for obj in self._objects_in_region(objects, region, min_ratio=0.2):
            if not self._is_text(obj):
                continue
            gap = line.y0 - obj.bbox.y1
            if 0 <= gap <= self._line_text_gap:
                texts.append(obj)
        return texts

    def _find_column(self, page: KPage, bbox: BBox) -> BBox | None:
        for section in page.sections:
            for column in section.columns:
                if column.contains(bbox.center):
                    return column
        return None

    def _body_font_size(self, objects: Sequence[KObject]) -> float | None:
        sizes = [
            size
            for obj in objects
            if self._is_text(obj)
            and (obj.vobject is None or not obj.vobject.is_footnote())
            and (size := self._font_size(obj)) is not None
        ]
        if not sizes:
            return None
        return float(median(sizes))

    def _font_size(self, obj: KObject) -> float | None:
        if isinstance(obj, KText):
            sizes = [char.bbox.height for char in obj.chars]
            if sizes:
                return float(median(sizes))
        return None

    def _text(self, obj: KObject) -> str:
        if isinstance(obj,KText):
            return obj.text
        return ''

    def _note_number_value(self, token: str) -> int | None:
        if token.isdecimal():
            return int(token)
        number = self._circled_note_number_values.get(token)
        if number is not None:
            return number
        if len(token) == 1:
            try:
                return unicodedata.digit(token)
            except (TypeError, ValueError):
                pass
        return None

    def _looks_like_footnote_start(self, obj: KObject) -> bool:
        text = self._text(obj)
        if self._section_title_pattern.match(text):
            return False
        return bool(self._footnote_start_pattern.match(text))

    def _has_footnote_start(self, objects: Sequence[KText]) -> bool:
        sorted_objects = sorted(objects, key=lambda obj: (-obj.bbox.y1, obj.bbox.x0))
        texts = [self._text(obj).strip() for obj in sorted_objects if self._text(obj).strip()]
        if not texts:
            return False
        for text in texts:
            if self._section_title_pattern.match(text):
                continue
            if self._footnote_start_pattern.match(text):
                return True
        joined = " ".join(texts[:3])
        if self._section_title_pattern.match(joined):
            return False
        return bool(self._footnote_start_pattern.match(joined))

    def _is_text(self, obj: KObject) -> TypeGuard[KText]:
        return isinstance(obj,KText)

    def _is_ignored(self, obj: KObject) -> bool:
        if obj.vobject is not None and (obj.vobject.is_header() or obj.vobject.is_footer()):
            return True
        text = self._text(obj).strip()
        if self._section_title_pattern.match(text):
            return True
        if re.match(r"^(?:https?://|www\.)", text, flags=re.IGNORECASE):
            return True
        if self._source_pattern.match(text):
            return True
        return obj.type in {"table", "figure", "formula", "pageheader", "pagefooter"}

    def _bottom_text_cluster(self, objects: Sequence[KText]) -> list[KText]:
        sorted_objects = sorted(objects, key=lambda obj: obj.bbox.y0)
        if not sorted_objects:
            return []
        cluster = [sorted_objects[0]]
        last = sorted_objects[0]
        for obj in sorted_objects[1:]:
            if obj.bbox.y0 - last.bbox.y1 > self._line_text_gap:
                break
            cluster.append(obj)
            last = obj
        return cluster

    def _case1(self, prev_page: KPage | None, page: KPage) -> list[BBox]:
        """
        特例：异常跨页续表脚注。
        条件收窄为 table1 + table2 + line + text，且 line 下方脚注序号存在 a > b。
        所有判断封装在本方法内，避免影响通用脚注识别。
        """

        def tables(p: KPage) -> list[KObject]:
            return [obj for obj in p.objects if obj.type == "table"]

        def last_table(p: KPage) -> KObject | None:
            ts = tables(p)
            if not ts:
                return None
            return min(ts, key=lambda obj: obj.bbox.y0)

        def first_table(p: KPage) -> KObject | None:
            ts = tables(p)
            if not ts:
                return None
            return max(ts, key=lambda obj: obj.bbox.y1)

        def is_continued_table(table1: KObject, table2: KObject) -> bool:
            b1 = table1.bbox
            b2 = table2.bbox
            width = max(b1.width, b2.width, 1)
            x_alike = abs(b1.x0 - b2.x0) <= 20 and abs(b1.x1 - b2.x1) <= 20
            width_alike = abs(b1.width - b2.width) / width <= 0.15
            table1_near_bottom = b1.y0 <= table1.page.bbox.y0 + table1.page.bbox.height * 0.35
            table2_near_top = b2.y1 >= table2.page.bbox.y1 - table2.page.bbox.height * 0.35
            return x_alike and width_alike and table1_near_bottom and table2_near_top

        def find_line_below_table(p: KPage, table: KObject) -> KLine | None:
            table_bbox = table.bbox
            candidates: list[KLine] = []
            for line in p.pdf_lines:
                if not line.is_h():
                    continue
                line_bbox = line.bbox
                if line_bbox.y1 > table_bbox.y0 + 8:
                    continue
                if table_bbox.y0 - line_bbox.y1 > 80:
                    continue
                overlap = max(0.0, min(line_bbox.x1, table_bbox.x1) - max(line_bbox.x0, table_bbox.x0))
                if line_bbox.width <= 0 or overlap / line_bbox.width < 0.6:
                    continue
                if line_bbox.width < table_bbox.width * 0.6:
                    continue
                candidates.append(line)
            if not candidates:
                return None
            return max(candidates, key=lambda line: line.bbox.y1)

        def texts_below_line(p: KPage, line: KLine) -> list[KText]:
            region = BBox(
                max(p.bbox.x0, line.bbox.x0 - 12),
                p.bbox.y0,
                min(p.bbox.x1, line.bbox.x1 + 12),
                max(p.bbox.y0, line.bbox.y0 - 4),
            )
            texts: list[KText] = []
            for obj in p.objects:
                if not isinstance(obj, KText):
                    continue
                if self._is_ignored(obj):
                    continue
                if self._intersect_ratio(region, obj.bbox) < 0.2 and not region.contains(obj.bbox.center):
                    continue
                gap = line.bbox.y0 - obj.bbox.y1
                if 0 <= gap <= 120:
                    texts.append(obj)
            if not texts:
                return []
            texts = sorted(texts, key=lambda obj: (line.bbox.y0 - obj.bbox.y1, -obj.bbox.y1, obj.bbox.x0))
            nearest_gap = line.bbox.y0 - texts[0].bbox.y1
            cluster = [obj for obj in texts if line.bbox.y0 - obj.bbox.y1 <= nearest_gap + 45]
            return sorted(cluster, key=lambda obj: (-obj.bbox.y1, obj.bbox.x0))

        def chars_in_bbox(p: KPage, bbox: BBox):
            return [char for char in p.pdf_chars if bbox.contains(char.bbox.center)]

        def is_ref_char(text: str) -> bool:
            return self._note_number_value(text) is not None

        def merge_digit_tokens(chars) -> list[str]:
            sorted_chars = sorted(chars, key=lambda char: (-char.bbox.cy, char.bbox.x0))
            tokens: list[str] = []
            current = []

            def flush() -> None:
                if current:
                    tokens.append("".join(char.text for char in current))
                    current.clear()

            for char in sorted_chars:
                if not is_ref_char(char.text):
                    flush()
                    continue
                if not current:
                    current.append(char)
                    continue
                last = current[-1]
                if last.text.isdecimal() and char.text.isdecimal():
                    max_height = max(last.bbox.height, char.bbox.height, 1)
                    max_width = max(last.bbox.width, char.bbox.width, 1)
                    same_line = abs(last.bbox.cy - char.bbox.cy) <= max_height * 0.6
                    close_x = 0 <= char.bbox.x0 - last.bbox.x1 <= max_width * 1.8
                    if same_line and close_x:
                        current.append(char)
                        continue
                flush()
                current.append(char)
            flush()
            return tokens

        def note_numbers(p: KPage, line: KLine, texts: Sequence[KText]) -> list[int]:
            bbox = BBox.join2(texts).expand(dx=10, dy=6, bound=p.bbox)
            tokens = merge_digit_tokens(chars_in_bbox(p, bbox))
            numbers: list[int] = []
            for token in tokens:
                number = self._note_number_value(token)
                if number is not None:
                    numbers.append(number)
            if numbers:
                return numbers

            text = " ".join(self._text(obj).strip() for obj in texts)
            numbers.extend(int(v) for v in re.findall(r"(?<![\d.])(\d{1,3})(?!\.\d)", text))
            numbers.extend(
                number
                for char in text
                if not char.isdecimal() and (number := self._note_number_value(char)) is not None
            )
            return numbers

        def is_disordered(numbers: Sequence[int]) -> bool:
            return any(a > b for a, b in zip(numbers, numbers[1:]))

        def superscript_numbers_in_bbox(p: KPage, bbox: BBox) -> set[str]:
            chars = [
                char
                for char in chars_in_bbox(p, bbox)
                if char.is_superscript() and is_ref_char(char.text)
            ]
            return set(merge_digit_tokens(chars))

        if prev_page is None:
            return []

        table1 = last_table(prev_page)
        table2 = first_table(page)
        if table1 is None or table2 is None:
            return []
        if not is_continued_table(table1, table2):
            return []

        line = find_line_below_table(page, table2)
        if line is None:
            return []

        texts = texts_below_line(page, line)
        if not texts:
            return []

        numbers = note_numbers(page, line, texts)
        if len(numbers) < 2 or not is_disordered(numbers):
            return []

        refs = superscript_numbers_in_bbox(prev_page, table1.bbox) | superscript_numbers_in_bbox(page, table2.bbox)
        ref_numbers = {
            number
            for ref in refs
            if (number := self._note_number_value(ref)) is not None
        }
        if not (set(numbers) & ref_numbers):
            return []

        return [BBox.join2(texts)]

    def _is_table_line(self, page: KPage, line: BBox) -> bool:
        for obj in page.objects:
            if obj.type != "table":
                continue
            table = obj.bbox
            table_area = BBox(table.x0 - 2, table.y0 - 3, table.x1 + 2, table.y1 + 3)
            if not table_area.contains(line.center):
                continue
            overlap = max(0.0, min(line.x1, table.x1) - max(line.x0, table.x0))
            if line.width <= 0 or overlap / line.width >= 0.5:
                return True
        return False

    def _intersect_ratio(self, region: BBox, bbox: BBox) -> float:
        inter = region.intersect(bbox)
        if inter is None:
            return 0.0
        return inter.area2 / bbox.area2

    def _center_in(self, bbox: BBox, region: BBox) -> bool:
        return region.contains(bbox.center)
