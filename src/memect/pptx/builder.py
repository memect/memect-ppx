"""Build PPTX files from parsed PDF document objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Iterable

from PIL import ImageFont

from memect.pdf.fonts import get_font_dir
from memect.pdf.base import (
    KBlock,
    KCell,
    KChar,
    KColor,
    KDocument,
    KFigure,
    KFont,
    KFormula,
    KLine,
    KObject,
    KRect,
    KSpan,
    KTable,
    KText,
)

from .model import Alignment, Paragraph, Slide, TableCell, TextBox, VerticalAlignment
from .presentation import Presentation
from .units import Length, pt


class TextLayoutMode(StrEnum):
    BOX = "box"
    LINES = "lines"


@dataclass(frozen=True)
class FontSpec:
    ppt_name: str
    path: Path
    index: int = 0


@dataclass
class _TextItem:
    text: str
    font: KFont
    color: KColor
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikeout: bool = False
    base_size: float = 10


@dataclass
class _MediaItem:
    obj: KFigure | KFormula


RenderItem = _TextItem | _MediaItem


@dataclass
class _RenderLine:
    items: list[RenderItem]
    bbox: tuple[float, float, float, float]


@dataclass
class RenderedInlineMedia:
    source: KFigure | KFormula
    picture: object | None = None


@dataclass
class TextRenderResult:
    textbox: TextBox | None = None
    cell: TableCell | None = None
    media: list[RenderedInlineMedia] = field(default_factory=list)
    font_scale: float = 1.0


class FontResolver:
    def resolve(self, font: KFont, *, bold: bool = False, italic: bool = False) -> FontSpec:
        font_dir = get_font_dir()
        if font.wingdings:
            if font.name == "wingdings2":
                return FontSpec("Wingdings 2", font_dir / "wingdings2.ttf")
            if font.name == "wingdings3":
                return FontSpec("Wingdings 3", font_dir / "wingdings3.ttf")
            return FontSpec("Wingdings", font_dir / "wingdings.ttf")
        if font.serif:
            return FontSpec(
                "Source Han Serif SC",
                font_dir / f"serif/SourceHanSerif-{'Bold' if bold else 'Regular'}.ttc",
                index=2,
            )
        if font.monospace and font.name != "ocr":
            return FontSpec(
                "Consolas",
                font_dir / f"sans-serif/SourceHanSans-{'Bold' if bold else 'Regular'}.ttc",
                index=2,
            )
        return FontSpec(
            "Source Han Sans SC",
            font_dir / f"sans-serif/SourceHanSans-{'Bold' if bold else 'Regular'}.ttc",
            index=2,
        )


class TextRenderer:
    def __init__(self, builder: Builder):
        self.builder = builder
        self.font_resolver = FontResolver()
        self._font_cache: dict[tuple[str, int, int], ImageFont.FreeTypeFont] = {}

    def render(
        self,
        text: KText,
        *,
        slide: Slide | None = None,
        cell: TableCell | None = None,
        layout: TextLayoutMode = TextLayoutMode.BOX,
        fit: bool = True,
        min_font_size: float = 4,
        max_font_size: float | None = None,
        preserve_inline_media: bool = True,
        append: bool = False,
        alignment: Alignment | None = None,
        vertical_align: VerticalAlignment = "top",
    ) -> TextRenderResult:
        if slide is None and cell is None:
            raise ValueError("slide or cell is required")

        bbox = self.builder._bbox(text)
        x0, y0, x1, y1 = bbox
        width = max(1, x1 - x0)
        height = max(1, y1 - y0)
        lines = self._collect_lines(text)
        scale = self._fit_scale(lines, width, height, layout, fit, min_font_size, max_font_size)
        result = TextRenderResult(cell=cell, font_scale=scale)

        if cell is not None:
            if not append:
                cell.text = ""
                cell.paragraphs.clear()
            cell.vertical_align = "middle" if vertical_align == "middle" else cell.vertical_align
            self._render_to_cell(cell, lines, layout, scale, min_font_size, max_font_size, alignment)
        else:
            assert slide is not None
            textbox = slide.add_textbox(
                "",
                left=pt(x0),
                top=pt(y0),
                width=pt(width),
                height=pt(height),
                alignment=alignment,
                vertical_align=vertical_align,
            )
            textbox.paragraphs.clear()
            self._render_to_textbox(textbox, lines, layout, scale, min_font_size, max_font_size, alignment)
            result.textbox = textbox

        if preserve_inline_media and slide is not None:
            for line in lines:
                for item in line.items:
                    if isinstance(item, _MediaItem):
                        self.builder._render_picture(slide, item.obj)
                        result.media.append(RenderedInlineMedia(item.obj))
        return result

    def _collect_lines(self, text: KText) -> list[_RenderLine]:
        if text.lines:
            lines: list[_RenderLine] = []
            for line in text.lines:
                items: list[RenderItem] = []
                for obj in line.split():
                    item = self._item_from_object(obj)
                    if item is not None:
                        items.append(item)
                if items:
                    lines.append(_RenderLine(items, self.builder._bbox(line)))
            return lines

        raw = text.text or ""
        if not raw:
            return []
        item = _TextItem(
            text=raw,
            font=KFont.SANS_SERIF,
            color=KColor.BLACK,
            base_size=max(1, text.bbox.height * 0.75),
        )
        return [_RenderLine([item], self.builder._bbox(text))]

    def _item_from_object(self, obj: KObject) -> RenderItem | None:
        if isinstance(obj, KSpan):
            return _TextItem(
                text="".join(self._char_text(char) for char in obj.chars),
                font=obj.font,
                color=obj.color,
                bold=obj.bold,
                italic=obj.italic,
                underline=obj.underline,
                strikeout=obj.strikeout,
                base_size=max(1, obj.bbox.height * 0.75),
            )
        if isinstance(obj, KChar):
            return _TextItem(
                text=self._char_text(obj),
                font=obj.font,
                color=obj.color,
                bold=obj.bold,
                italic=obj.italic,
                underline=obj.underline,
                strikeout=obj.strikeout,
                base_size=max(1, obj.bbox.height * 0.75),
            )
        if isinstance(obj, (KFigure, KFormula)):
            return _MediaItem(obj)
        return None

    def _fit_scale(
        self,
        lines: list[_RenderLine],
        width: float,
        height: float,
        layout: TextLayoutMode,
        fit: bool,
        min_font_size: float,
        max_font_size: float | None,
    ) -> float:
        if not fit or not lines:
            return 1.0

        def fits(scale: float) -> bool:
            measured_width, measured_height = self._measure(
                lines,
                width,
                layout,
                scale,
                min_font_size,
                max_font_size,
            )
            return measured_width <= width + 0.5 and measured_height <= height + 0.5

        low = 0.05
        high = 1.0
        if fits(high):
            while high < 4:
                candidate = high * 1.25
                if not fits(candidate):
                    break
                low = high
                high = candidate
            else:
                return high
        elif not fits(low):
            return low

        for _ in range(16):
            mid = (low + high) / 2
            if fits(mid):
                low = mid
            else:
                high = mid
        return low

    def _measure(
        self,
        lines: list[_RenderLine],
        width: float,
        layout: TextLayoutMode,
        scale: float,
        min_font_size: float,
        max_font_size: float | None,
    ) -> tuple[float, float]:
        if layout == TextLayoutMode.BOX:
            items = [item for line in lines for item in line.items if isinstance(item, _TextItem)]
            return self._measure_wrapped_items(items, width, scale, min_font_size, max_font_size)

        max_width = 0.0
        total_height = 0.0
        for line in lines:
            line_width = 0.0
            line_height = 0.0
            for item in line.items:
                if isinstance(item, _TextItem):
                    item_width, item_height = self._measure_text(item, item.text, scale, min_font_size, max_font_size)
                else:
                    x0, y0, x1, y1 = self.builder._bbox(item.obj)
                    item_width, item_height = max(1, x1 - x0), max(1, y1 - y0)
                line_width += item_width
                line_height = max(line_height, item_height)
            max_width = max(max_width, line_width)
            total_height += max(1, line_height) * 1.05
        return max_width, total_height

    def _measure_wrapped_items(
        self,
        items: list[_TextItem],
        width: float,
        scale: float,
        min_font_size: float,
        max_font_size: float | None,
    ) -> tuple[float, float]:
        if not items:
            return 0, 0
        max_width = 0.0
        total_height = 0.0
        line_width = 0.0
        line_height = 0.0

        def flush_line() -> None:
            nonlocal max_width, total_height, line_width, line_height
            max_width = max(max_width, line_width)
            total_height += max(1, line_height) * 1.15
            line_width = 0.0
            line_height = 0.0

        for item in items:
            for char in item.text:
                if char == "\n":
                    flush_line()
                    continue
                char_width, char_height = self._measure_text(item, char, scale, min_font_size, max_font_size)
                if line_width > 0 and line_width + char_width > width:
                    flush_line()
                line_width += char_width
                line_height = max(line_height, char_height)
        if line_width > 0 or total_height == 0:
            flush_line()
        return max_width, total_height

    def _measure_text(
        self,
        item: _TextItem,
        value: str,
        scale: float,
        min_font_size: float,
        max_font_size: float | None,
    ) -> tuple[float, float]:
        size = self._font_size(item, scale, min_font_size, max_font_size)
        font = self._measure_font(item, size)
        if not value:
            return 0, size
        x0, y0, x1, y1 = font.getbbox(value)
        ascent, descent = font.getmetrics()
        return max(0, x1 - x0), max(size, y1 - y0, ascent + descent)

    def _render_to_textbox(
        self,
        textbox: TextBox,
        lines: list[_RenderLine],
        layout: TextLayoutMode,
        scale: float,
        min_font_size: float,
        max_font_size: float | None,
        alignment: Alignment | None,
    ) -> None:
        if layout == TextLayoutMode.BOX:
            paragraph = Paragraph(alignment=alignment)
            self._append_text_items(
                paragraph,
                [item for line in lines for item in line.items],
                scale,
                min_font_size,
                max_font_size,
            )
            textbox.paragraphs.append(paragraph)
            return

        for line in lines:
            paragraph = Paragraph(alignment=alignment)
            self._append_text_items(paragraph, line.items, scale, min_font_size, max_font_size)
            textbox.paragraphs.append(paragraph)

    def _render_to_cell(
        self,
        cell: TableCell,
        lines: list[_RenderLine],
        layout: TextLayoutMode,
        scale: float,
        min_font_size: float,
        max_font_size: float | None,
        alignment: Alignment | None,
    ) -> None:
        if layout == TextLayoutMode.BOX:
            paragraph = Paragraph(alignment=alignment)
            self._append_text_items(
                paragraph,
                [item for line in lines for item in line.items],
                scale,
                min_font_size,
                max_font_size,
            )
            cell.paragraphs.append(paragraph)
            return

        for line in lines:
            paragraph = Paragraph(alignment=alignment)
            self._append_text_items(paragraph, line.items, scale, min_font_size, max_font_size)
            cell.paragraphs.append(paragraph)

    def _append_text_items(
        self,
        paragraph: Paragraph,
        items: list[RenderItem],
        scale: float,
        min_font_size: float,
        max_font_size: float | None,
    ) -> None:
        for item in items:
            if not isinstance(item, _TextItem) or not item.text:
                continue
            spec = self.font_resolver.resolve(item.font, bold=item.bold, italic=item.italic)
            paragraph.add_run(
                item.text,
                font=spec.ppt_name,
                east_asia_font=spec.ppt_name,
                size=pt(self._font_size(item, scale, min_font_size, max_font_size)),
                bold=item.bold,
                italic=item.italic,
                underline=item.underline,
                strike=item.strikeout,
                color=self.builder._color(item.color),
            )

    def _font_size(
        self,
        item: _TextItem,
        scale: float,
        min_font_size: float,
        max_font_size: float | None,
    ) -> float:
        size = max(min_font_size, item.base_size * scale)
        if max_font_size is not None:
            size = min(max_font_size, size)
        return size

    def _measure_font(self, item: _TextItem, size: float) -> ImageFont.FreeTypeFont:
        spec = self.font_resolver.resolve(item.font, bold=item.bold, italic=item.italic)
        int_size = max(1, int(round(size)))
        key = (str(spec.path), spec.index, int_size)
        if key not in self._font_cache:
            self._font_cache[key] = ImageFont.truetype(str(spec.path), size=int_size, index=spec.index)
        return self._font_cache[key]

    def _char_text(self, char: KChar) -> str:
        if char.font.wingdings:
            wingdings_text = getattr(char, "wingdings_text", None)
            if wingdings_text:
                return wingdings_text
        return char.text


class Builder:
    """Render a ``KDocument`` into a PPTX deck."""

    def __init__(self, *, text_layout: TextLayoutMode = TextLayoutMode.BOX) -> None:
        self.text_layout = text_layout
        self.text_renderer = TextRenderer(self)

    def build(self, doc: KDocument) -> bytes:
        pages = list(doc.pages or [])
        width = max((float(page.width or 0) for page in pages), default=960.0)
        height = max((float(page.height or 0) for page in pages), default=540.0)

        deck = Presentation(title="", creator="memect.pptx", width=pt(width), height=pt(height))
        deck.set_default_font(font="Source Han Sans SC", east_asia_font="Source Han Sans SC", size=pt(10.5))
        for page in pages or [None]:
            slide = deck.add_slide()
            if page is not None:
                self._render_objects(slide, page.objects)
        return deck.to_bytes()

    def _render_objects(self, slide: Slide, objects: Iterable[KObject]) -> None:
        for obj in objects:
            if isinstance(obj, KText):
                self._render_text(slide, obj)
            elif isinstance(obj, (KFigure, KFormula)):
                self._render_picture(slide, obj)
            elif isinstance(obj, KTable):
                self._render_table(slide, obj)
            elif isinstance(obj, KRect):
                self._render_rect(slide, obj)
            elif isinstance(obj, KLine):
                self._render_line(slide, obj)
            elif isinstance(obj, KBlock):
                self._render_objects(slide, obj.objects)

    def _render_text(self, slide: Slide, obj: KText) -> None:
        self.text_renderer.render(obj, slide=slide, layout=self.text_layout)

    def _render_picture(self, slide: Slide, obj: KFigure | KFormula) -> None:
        bbox = self._bbox(obj)
        path = obj.fullpath
        x0, y0, x1, y1 = bbox
        if not Path(path).exists():
            return
        slide.add_picture(
            path,
            left=pt(x0),
            top=pt(y0),
            width=pt(max(1, x1 - x0)),
            height=pt(max(1, y1 - y0)),
        )

    def _render_rect(self, slide: Slide, obj: KRect) -> None:
        bbox = self._bbox(obj)
        x0, y0, x1, y1 = bbox
        slide.add_rect(
            left=pt(x0),
            top=pt(y0),
            width=pt(max(1, x1 - x0)),
            height=pt(max(1, y1 - y0)),
            fill=self._color(obj.color),
            line=None,
        )

    def _render_line(self, slide: Slide, obj: KLine) -> None:
        bbox = self._bbox(obj)
        x0, y0, x1, y1 = bbox
        slide.add_line(
            x1=pt(x0),
            y1=pt(y0),
            x2=pt(x1),
            y2=pt(y1),
            color=self._color(obj.color) or "000000",
            width=pt(float(obj.width or 1)),
        )

    def _render_table(self, slide: Slide, obj: KTable) -> None:
        bbox = self._bbox(obj)
        x0, y0, x1, y1 = bbox
        rows = int(obj.row_num or 0)
        cols = int(obj.col_num or 0)
        if rows < 1 or cols < 1:
            return

        column_widths, row_heights = self._table_sizes(obj, rows, cols)
        cells: list[TableCell] = []
        for cell in obj.cells:
            cx0, cy0, cx1, cy1 = self._cell_bbox(cell)
            tc = TableCell(
                row_index=cell.row_index,
                col_index=cell.col_index,
                row_span=cell.row_span,
                col_span=cell.col_span,
                width=pt(max(1, cx1 - cx0)),
                height=pt(max(1, cy1 - cy0)),
            )
            cells.append(tc)
            appended = False
            for child in cell.objects:
                if isinstance(child, KText):
                    self.text_renderer.render(
                        child,
                        slide=slide,
                        cell=tc,
                        layout=TextLayoutMode.BOX,
                        vertical_align="middle",
                        append=appended,
                    )
                    appended = True
                elif isinstance(child, (KFigure, KFormula)):
                    self._render_picture(slide, child)

        slide.add_table(
            rows=rows,
            cols=cols,
            cells=cells,
            left=pt(x0),
            top=pt(y0),
            width=pt(max(1, x1 - x0)),
            height=pt(max(1, y1 - y0)),
            column_widths=column_widths,
            row_heights=row_heights,
        )

    def _bbox(self, obj: KObject) -> tuple[float, float, float, float]:
        return obj.bbox.transform(obj.page.to_lt()).to_tuple()

    def _cell_bbox(self, cell: KCell) -> tuple[float, float, float, float]:
        return cell.bbox.transform(cell.page.to_lt()).to_tuple()

    def _table_sizes(self, table: KTable, rows: int, cols: int) -> tuple[list[Length], list[Length]]:
        x_axis, y_axis = self._table_axes(table, rows, cols)
        column_widths = [
            pt(max(1, x_axis[index + 1] - x_axis[index]))
            for index in range(cols)
        ]
        row_heights = [
            pt(max(1, y_axis[index + 1] - y_axis[index]))
            for index in range(rows)
        ]
        return column_widths, row_heights

    def _table_axes(self, table: KTable, rows: int, cols: int) -> tuple[list[float], list[float]]:
        x0, y0, x1, y1 = self._bbox(table)
        x_end = x0 + max(1, x1 - x0)
        y_end = y0 + max(1, y1 - y0)
        x_candidates: list[list[float]] = [[] for _ in range(cols + 1)]
        y_candidates: list[list[float]] = [[] for _ in range(rows + 1)]

        for cell in table.cells:
            row = int(cell.row_index)
            col = int(cell.col_index)
            row_span = int(cell.row_span)
            col_span = int(cell.col_span)
            if row < 0 or col < 0 or row_span < 1 or col_span < 1:
                continue
            if row + row_span > rows or col + col_span > cols:
                continue

            cx0, cy0, cx1, cy1 = self._cell_bbox(cell)
            if cx1 - cx0 > 0.5:
                x_candidates[col].append(self._clamp(cx0, x0, x_end))
                x_candidates[col + col_span].append(self._clamp(cx1, x0, x_end))
            if cy1 - cy0 > 0.5:
                y_candidates[row].append(self._clamp(cy0, y0, y_end))
                y_candidates[row + row_span].append(self._clamp(cy1, y0, y_end))

        x_axis = [self._median(values) if values else None for values in x_candidates]
        y_axis = [self._median(values) if values else None for values in y_candidates]
        return (
            self._fill_axis(x_axis, start=x0, end=x_end),
            self._fill_axis(y_axis, start=y0, end=y_end),
        )

    def _fill_axis(self, axis: list[float | None], *, start: float, end: float) -> list[float]:
        count = len(axis) - 1
        if count < 1:
            return [start, end]
        if end <= start:
            end = start + count

        axis[0] = start
        axis[-1] = end
        for index in range(1, count):
            value = axis[index]
            if value is None:
                continue
            if value <= start or value >= end:
                axis[index] = None

        known = [index for index, value in enumerate(axis) if value is not None]
        for left_index, right_index in zip(known, known[1:]):
            left = axis[left_index]
            right = axis[right_index]
            if left is None or right is None or right_index == left_index:
                continue
            step = (right - left) / (right_index - left_index)
            for index in range(left_index + 1, right_index):
                axis[index] = left + step * (index - left_index)

        values = [float(value if value is not None else start) for value in axis]
        if any(values[index + 1] <= values[index] for index in range(count)):
            return self._equal_axis(count, start=start, end=end)
        return values

    def _equal_axis(self, count: int, *, start: float, end: float) -> list[float]:
        step = (end - start) / count
        return [start + step * index for index in range(count + 1)]

    def _median(self, values: list[float]) -> float:
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / 2

    def _clamp(self, value: float, low: float, high: float) -> float:
        return min(max(value, low), high)

    def _cell_text(self, cell: KCell) -> str:
        parts: list[str] = []
        for obj in cell.objects:
            if isinstance(obj, KText) and obj.text:
                text = obj.text
                parts.append(str(text))
        return "\n".join(parts)

    def _color(self, color: KColor | None) -> str | None:
        if color is None:
            return None
        return f"{int(color.rgba[0]):02X}{int(color.rgba[1]):02X}{int(color.rgba[2]):02X}"


PPTXBuilder = Builder
