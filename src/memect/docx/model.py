"""Document object model used before OOXML serialization."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from string import Formatter
from typing import Any, Literal

from .units import Length, ensure_length, inch

Alignment = Literal["left", "center", "right", "both", "justify"]
VerticalAlignment = Literal["top", "center", "bottom"]
PageFieldKind = Literal["PAGE", "NUMPAGES", "SECTIONPAGES"]
PageNumberFormat = Literal["decimal", "upperRoman", "lowerRoman", "upperLetter", "lowerLetter"]
ShadingPair = tuple[str | None, str | None]

_PAGE_NUMBER_PLACEHOLDERS: dict[str, PageFieldKind] = {
    "page": "PAGE",
    "total": "NUMPAGES",
    "total_pages": "NUMPAGES",
    "section_total": "SECTIONPAGES",
}


@dataclass
class DocumentDefaults:
    font: str = "Times New Roman"
    east_asia_font: str = "SimSun"
    size: Length | int | float = 11
    color: str | None = None


@dataclass
class RunStyle:
    style: str | None = None
    font: str | None = None
    east_asia_font: str | None = None
    size: Length | int | float | None = None
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    strike: bool | None = None
    color: str | None = None


@dataclass
class Run:
    text: str = ""
    style: RunStyle = field(default_factory=RunStyle)
    break_type: Literal["line", "page"] | None = None
    tab: bool = False
    paragraph: Paragraph | None = field(default=None, repr=False, compare=False)

    def add_footnote_ref(self, footnote: Footnote | int) -> FootnoteReference:
        footnote_id = footnote if isinstance(footnote, int) else footnote.footnote_id
        ref = FootnoteReference(footnote_id=footnote_id)
        if self.paragraph is None:
            raise ValueError("Run is not attached to a paragraph")
        index = self.paragraph.inlines.index(self)
        self.paragraph.inlines.insert(index + 1, ref)
        return ref

    def add_footnote(self, text: str = "") -> Footnote:
        if self.paragraph is None or self.paragraph.document is None:
            raise ValueError("Run is not attached to a document")
        footnote = self.paragraph.document.create_footnote(text)
        self.add_footnote_ref(footnote)
        return footnote


@dataclass
class Picture:
    name: str
    content_type: str
    data: bytes
    width_emu: int
    height_emu: int
    alt_text: str = ""
    image_id: int = 0


@dataclass
class _PendingPicture:
    source: str | Path | bytes
    width: Length | int | float | None = None
    height: Length | int | float | None = None
    alt_text: str = ""


@dataclass
class FootnoteReference:
    footnote_id: int


@dataclass
class PageField:
    kind: PageFieldKind = "PAGE"


Inline = Run | Picture | _PendingPicture | FootnoteReference | PageField


@dataclass
class ParagraphFormat:
    alignment: Alignment | None = None
    line_spacing: float | None = None
    space_before: Length | int | float | None = None
    space_after: Length | int | float | None = None
    left_indent: Length | int | float | None = None
    right_indent: Length | int | float | None = None
    first_line_indent: Length | int | float | None = None
    keep_with_next: bool | None = None
    keep_together: bool | None = None


@dataclass
class Paragraph:
    style: str | None = None
    format: ParagraphFormat = field(default_factory=ParagraphFormat)
    inlines: list[Inline] = field(default_factory=list)
    list_kind: Literal["bullet", "decimal"] | None = None
    list_level: int = 0
    document: Document | None = field(default=None, repr=False, compare=False)

    def add_run(
        self,
        text: str = "",
        *,
        style: str | None = None,
        font: str | None = None,
        east_asia_font: str | None = None,
        size: Length | int | float | None = None,
        bold: bool | None = None,
        italic: bool | None = None,
        underline: bool | None = None,
        strike: bool | None = None,
        color: str | None = None,
    ) -> Run:
        run = Run(
            text=str(text),
            style=RunStyle(
                style=style,
                font=font,
                east_asia_font=east_asia_font,
                size=size,
                bold=bold,
                italic=italic,
                underline=underline,
                strike=strike,
                color=color,
            ),
        )
        run.paragraph = self
        self.inlines.append(run)
        return run

    def add_break(self) -> Run:
        run = Run(break_type="line")
        run.paragraph = self
        self.inlines.append(run)
        return run

    def add_page_break(self) -> Run:
        run = Run(break_type="page")
        run.paragraph = self
        self.inlines.append(run)
        return run

    def add_tab(self) -> Run:
        run = Run(tab=True)
        run.paragraph = self
        self.inlines.append(run)
        return run

    def add_picture(self, picture: Picture) -> Picture:
        self.inlines.append(picture)
        return picture

    def add_footnote_ref(self, footnote: Footnote | int) -> FootnoteReference:
        footnote_id = footnote if isinstance(footnote, int) else footnote.footnote_id
        ref = FootnoteReference(footnote_id=footnote_id)
        self.inlines.append(ref)
        return ref

    def add_footnote(self, text: str = "") -> Footnote:
        if self.document is None:
            raise ValueError("Paragraph is not attached to a document")
        footnote = self.document.create_footnote(text)
        self.add_footnote_ref(footnote)
        return footnote

    def add_field(self, kind: PageFieldKind) -> PageField:
        field = PageField(kind=kind)
        self.inlines.append(field)
        return field

    @property
    def text(self) -> str:
        parts: list[str] = []
        for inline in self.inlines:
            if isinstance(inline, Run):
                if inline.tab:
                    parts.append("\t")
                elif inline.break_type:
                    parts.append("\n")
                else:
                    parts.append(inline.text)
        return "".join(parts)


def _append_page_number_text(paragraph: Paragraph, template: str) -> Paragraph:
    for literal, field_name, format_spec, conversion in Formatter().parse(str(template)):
        if literal:
            paragraph.add_run(literal)
        if field_name is None:
            continue
        if format_spec or conversion:
            raise ValueError("page number placeholders do not support format specifiers")
        field_kind = _PAGE_NUMBER_PLACEHOLDERS.get(field_name)
        if field_kind is None:
            raise ValueError(f"Unsupported page number placeholder: {field_name}")
        paragraph.add_field(field_kind)
    return paragraph


@dataclass
class TableCell:
    blocks: list[Any] = field(default_factory=list)
    width: Length | int | float | None = None
    col_span: int = 1
    row_span: int = 1
    row_index: int | None = None
    col_index: int | None = None
    shading: str | None = None
    vertical_align: VerticalAlignment | None = None
    document: Document | None = field(default=None, repr=False, compare=False)

    def add_paragraph(
        self,
        text: str = "",
        *,
        style: str | None = None,
        alignment: Alignment | None = None,
    ) -> Paragraph:
        paragraph = Paragraph(style=style, format=ParagraphFormat(alignment=alignment))
        if text:
            paragraph.add_run(text)
        paragraph.document = self.document
        self.blocks.append(paragraph)
        return paragraph

    def set_text(self, text: str, *, style: str | None = None) -> Paragraph:
        self.blocks.clear()
        return self.add_paragraph(text, style=style)

    def set_shading(self, color: str | None) -> TableCell:
        self.shading = color
        return self

    def add_table(
        self,
        rows: int | None = None,
        cols: int | None = None,
        *,
        data: list[list[Any]] | None = None,
        cells: list[TableCell] | None = None,
        style: str | None = "TableGrid",
        alignment: Alignment | None = None,
        borders: bool = True,
    ) -> Table:
        table = _create_table(
            rows=rows,
            cols=cols,
            data=data,
            cells=cells,
            style=style,
            alignment=alignment,
            borders=borders,
            document=self.document,
        )
        self.blocks.append(table)
        return table

    def add_picture(
        self,
        source: str | Path | bytes,
        *,
        width: Length | int | float | None = None,
        height: Length | int | float | None = None,
        alt_text: str = "",
        alignment: Alignment | None = None,
    ) -> Paragraph:
        paragraph = self.add_paragraph(alignment=alignment)
        if self.document is None:
            paragraph.inlines.append(
                _PendingPicture(
                    source=source,
                    width=width,
                    height=height,
                    alt_text=alt_text,
                )
            )
        else:
            picture = self.document.create_picture(
                source,
                width=width,
                height=height,
                alt_text=alt_text,
            )
            paragraph.add_picture(picture)
        return paragraph

    def add_caption(
        self,
        text: str,
        *,
        label: str = "Figure",
        sequence: str = "Figure",
        separator: str = " ",
        style: str | None = "Caption",
        numbering_format: str = "ARABIC",
        dirty: bool = True,
    ) -> Caption:
        caption = _create_caption(
            text=text,
            label=label,
            sequence=sequence,
            separator=separator,
            style=style,
            numbering_format=numbering_format,
            dirty=dirty,
        )
        self.blocks.append(caption)
        return caption




@dataclass
class TableRow:
    cells: list[TableCell]
    height: Length | int | float | None = None
    allow_break_across_pages: bool | None = None


@dataclass
class TableLook:
    first_row: bool = False
    last_row: bool = False
    first_column: bool = False
    last_column: bool = False
    banded_rows: bool = False
    banded_columns: bool = False


@dataclass
class Table:
    rows: list[TableRow]
    style: str | None = "TableGrid"
    width: Length | int | float | None = None
    alignment: Alignment | None = None
    borders: bool = True
    cells: list[TableCell] = field(default_factory=list)
    row_count: int = 0
    col_count: int = 0
    repeat_header_rows: int = 0
    allow_row_break_across_pages: bool | None = None
    header_shading: str | None = None
    banded_row_shading: ShadingPair | None = None
    banded_column_shading: ShadingPair | None = None
    look: TableLook = field(default_factory=TableLook)
    document: Document | None = field(default=None, repr=False, compare=False)

    @classmethod
    def create(
        cls,
        rows: int,
        cols: int,
        *,
        data: list[list[Any]] | None = None,
        cells: list[TableCell] | None = None,
        style: str | None = "TableGrid",
    ) -> Table:
        table_rows = [
            TableRow([TableCell() for _ in range(cols)])
            for _ in range(rows)
        ]
        table = cls(table_rows, style=style, row_count=rows, col_count=cols)
        if cells is not None:
            table.cells = list(cells)
            table.rows = _materialize_explicit_table_rows(
                row_count=rows,
                col_count=cols,
                cells=table.cells,
                existing_rows=table.rows,
            )
            return table
        if data is not None:
            for row_index, values in enumerate(data[:rows]):
                for col_index, value in enumerate(values[:cols]):
                    table.cell(row_index, col_index).set_text(str(value))
        return table

    def cell(self, row: int, col: int) -> TableCell:
        return self.rows[row].cells[col]

    def set_repeat_header_rows(self, count: int = 1) -> Table:
        if count < 0:
            raise ValueError("repeat header row count cannot be negative")
        if count > len(self.rows):
            raise ValueError("repeat header row count exceeds table row count")
        self.repeat_header_rows = count
        if count:
            self.look.first_row = True
        return self

    def set_allow_row_break_across_pages(self, allow: bool | None) -> Table:
        self.allow_row_break_across_pages = allow
        return self

    def set_header_shading(self, color: str | None) -> Table:
        self.header_shading = color
        if color is not None:
            self.look.first_row = True
        return self

    def set_banded_rows(self, first: str | None, second: str | None) -> Table:
        self.banded_row_shading = (first, second)
        self.look.banded_rows = first is not None or second is not None
        return self

    def set_banded_columns(self, first: str | None, second: str | None) -> Table:
        self.banded_column_shading = (first, second)
        self.look.banded_columns = first is not None or second is not None
        return self

    def set_table_look(
        self,
        *,
        first_row: bool | None = None,
        last_row: bool | None = None,
        first_column: bool | None = None,
        last_column: bool | None = None,
        banded_rows: bool | None = None,
        banded_columns: bool | None = None,
    ) -> Table:
        if first_row is not None:
            self.look.first_row = first_row
        if last_row is not None:
            self.look.last_row = last_row
        if first_column is not None:
            self.look.first_column = first_column
        if last_column is not None:
            self.look.last_column = last_column
        if banded_rows is not None:
            self.look.banded_rows = banded_rows
        if banded_columns is not None:
            self.look.banded_columns = banded_columns
        return self

    def add_cell(
        self,
        *,
        row_index: int,
        col_index: int,
        row_span: int = 1,
        col_span: int = 1,
        width: Length | int | float | None = None,
        shading: str | None = None,
        vertical_align: VerticalAlignment | None = None,
    ) -> TableCell:
        cell = TableCell(
            row_index=row_index,
            col_index=col_index,
            row_span=row_span,
            col_span=col_span,
            width=width,
            shading=shading,
            vertical_align=vertical_align,
        )
        cell.document = self.document
        self.cells.append(cell)
        self.rows = _materialize_explicit_table_rows(
            row_count=self.row_count or len(self.rows),
            col_count=self.col_count or max((len(row.cells) for row in self.rows), default=0),
            cells=self.cells,
            existing_rows=self.rows,
        )
        _attach_table_document(self, self.document)
        return cell


def _materialize_explicit_table_rows(
    *,
    row_count: int,
    col_count: int,
    cells: list[TableCell],
    existing_rows: list[TableRow] | None = None,
) -> list[TableRow]:
    if row_count < 1 or col_count < 1:
        raise ValueError("rows and cols must be positive")

    grid: list[list[TableCell | None]] = [
        [None for _ in range(col_count)]
        for _ in range(row_count)
    ]

    for cell in cells:
        if cell.row_index is None or cell.col_index is None:
            raise ValueError("Explicit table cells require row_index and col_index")
        if cell.row_span < 1 or cell.col_span < 1:
            raise ValueError("Table cell spans must be positive integers")
        if cell.row_index < 0 or cell.col_index < 0:
            raise ValueError("Table cell indexes must be non-negative")
        if cell.row_index + cell.row_span > row_count or cell.col_index + cell.col_span > col_count:
            raise ValueError("Table cell span exceeds the declared table size")

        for row_index in range(cell.row_index, cell.row_index + cell.row_span):
            for col_index in range(cell.col_index, cell.col_index + cell.col_span):
                if grid[row_index][col_index] is not None:
                    raise ValueError(f"Overlapping table cell at {(row_index, col_index)}")
                grid[row_index][col_index] = cell

    rows: list[TableRow] = []
    for row_index in range(row_count):
        row_cells: list[TableCell] = []
        row_height = existing_rows[row_index].height if existing_rows is not None and row_index < len(existing_rows) else None
        for col_index in range(col_count):
            cell = grid[row_index][col_index]
            if cell is None:
                fallback = None
                if existing_rows is not None and row_index < len(existing_rows):
                    row = existing_rows[row_index]
                    if col_index < len(row.cells):
                        fallback = row.cells[col_index]
                if fallback is None:
                    fallback = TableCell(row_index=row_index, col_index=col_index)
                cell = fallback
            row_cells.append(cell)
        rows.append(TableRow(row_cells, height=row_height))
    return rows


def _create_table(
    *,
    rows: int | None,
    cols: int | None,
    data: list[list[Any]] | None,
    cells: list[TableCell] | None,
    style: str | None,
    alignment: Alignment | None,
    borders: bool,
    document: Document | None,
) -> Table:
    if data is not None and cells is not None:
        raise ValueError("data and cells cannot be used together")
    if cells is not None and (rows is None or cols is None):
        raise ValueError("rows and cols are required when cells are provided")
    if data is not None:
        inferred_rows = len(data)
        inferred_cols = max((len(row) for row in data), default=0)
        rows = inferred_rows if rows is None else rows
        cols = inferred_cols if cols is None else cols
    if rows is None or cols is None:
        raise ValueError("rows and cols are required when data is not provided")
    if rows < 1 or cols < 1:
        raise ValueError("rows and cols must be positive")
    table = Table.create(rows, cols, data=data, cells=cells, style=style)
    table.alignment = alignment
    table.borders = borders
    _attach_table_document(table, document)
    return table


def _attach_table_document(table: Table, document: Document | None) -> None:
    table.document = document
    seen: set[int] = set()
    for row in table.rows:
        for cell in row.cells:
            identity = id(cell)
            if identity in seen:
                continue
            seen.add(identity)
            cell.document = document
            for block in cell.blocks:
                _attach_block_document(block, document)
    for cell in table.cells:
        identity = id(cell)
        if identity in seen:
            continue
        seen.add(identity)
        cell.document = document
        for block in cell.blocks:
            _attach_block_document(block, document)


def _attach_block_document(block: Any, document: Document | None) -> None:
    if isinstance(block, Paragraph):
        block.document = document
        for index, inline in enumerate(block.inlines):
            if isinstance(inline, Run):
                inline.paragraph = block
            elif isinstance(inline, _PendingPicture) and document is not None:
                block.inlines[index] = document.create_picture(
                    inline.source,
                    width=inline.width,
                    height=inline.height,
                    alt_text=inline.alt_text,
                )
    elif isinstance(block, Table):
        _attach_table_document(block, document)


@dataclass
class SectionMargins:
    top: Length | int | float = field(default_factory=lambda: inch(1))
    right: Length | int | float = field(default_factory=lambda: inch(1))
    bottom: Length | int | float = field(default_factory=lambda: inch(1))
    left: Length | int | float = field(default_factory=lambda: inch(1))
    header: Length | int | float = field(default_factory=lambda: inch(0.5))
    footer: Length | int | float = field(default_factory=lambda: inch(0.5))
    gutter: Length | int | float = 0


@dataclass
class HeaderFooter:
    blocks: list[Any] = field(default_factory=list)
    document: Document | None = field(default=None, repr=False, compare=False)

    def add_paragraph(
        self,
        text: str = "",
        *,
        style: str | None = None,
        alignment: Alignment | None = None,
    ) -> Paragraph:
        paragraph = Paragraph(style=style, format=ParagraphFormat(alignment=alignment))
        if text:
            paragraph.add_run(text)
        paragraph.document = self.document
        self.blocks.append(paragraph)
        return paragraph

    def add_text(
        self,
        text: str,
        page_number_format: str | None = None,
        page_number_position: Literal["left", "center", "right"] = "right",
        *,
        style: str | None = None,
        width: Length | int | float | None = None,
    ) -> Paragraph | Table:
        if page_number_format is None:
            return self.add_paragraph(text, style=style, alignment="left")

        if page_number_position == "right":
            return self.add_parts(
                left=text,
                right=page_number_format,
                style=style,
                width=width,
            )
        if page_number_position == "center":
            return self.add_parts(
                left=text,
                center=page_number_format,
                style=style,
                width=width,
            )
        if page_number_position == "left":
            return self.add_parts(
                left=page_number_format,
                right=text,
                style=style,
                width=width,
            )
        raise ValueError("page_number_position must be 'left', 'center', or 'right'")

    def add_parts(
        self,
        *,
        left: Any | None = None,
        center: Any | None = None,
        right: Any | None = None,
        style: str | None = None,
        width: Length | int | float | None = None,
        picture_width: Length | int | float | None = None,
        picture_height: Length | int | float | None = None,
        picture_alt_text: str = "",
    ) -> Table:
        parts = self._layout_parts(left=left, center=center, right=right)
        table_width = width if width is not None else inch(6.5)
        cell_widths = self._part_widths(table_width, parts, proportional=center is None)
        table = self.add_table(1, len(parts), style=None, borders=False)
        table.width = table_width

        for index, (content, alignment) in enumerate(parts):
            cell = table.cell(0, index)
            cell.width = cell_widths[index]
            paragraph = cell.add_paragraph(style=style, alignment=alignment)
            self._append_part(
                paragraph,
                content,
                picture_width=picture_width,
                picture_height=picture_height,
                picture_alt_text=picture_alt_text,
            )

        return table

    def _layout_parts(
        self,
        *,
        left: Any | None,
        center: Any | None,
        right: Any | None,
    ) -> list[tuple[Any | None, Alignment]]:
        if center is not None:
            return [
                (left, "left"),
                (center, "center"),
                (right, "right"),
            ]
        if left is not None and right is not None:
            return [
                (left, "left"),
                (right, "right"),
            ]
        if left is not None:
            return [(left, "left")]
        if right is not None:
            return [(right, "right")]
        return [(None, "left")]

    def _append_part(
        self,
        paragraph: Paragraph,
        content: Any,
        *,
        picture_width: Length | int | float | None,
        picture_height: Length | int | float | None,
        picture_alt_text: str,
    ) -> None:
        if content is None:
            return
        if isinstance(content, Picture):
            paragraph.add_picture(content)
            return
        if isinstance(content, _PendingPicture):
            self._append_pending_picture(paragraph, content)
            return
        if isinstance(content, Path | bytes):
            self._append_picture_source(
                paragraph,
                content,
                width=picture_width,
                height=picture_height,
                alt_text=picture_alt_text,
            )
            return
        if isinstance(content, PageField):
            paragraph.inlines.append(content)
            return
        if isinstance(content, Run):
            content.paragraph = paragraph
            paragraph.inlines.append(content)
            return
        _append_page_number_text(paragraph, str(content))

    def _append_picture_source(
        self,
        paragraph: Paragraph,
        source: Path | bytes,
        *,
        width: Length | int | float | None,
        height: Length | int | float | None,
        alt_text: str,
    ) -> None:
        pending = _PendingPicture(
            source=source,
            width=width,
            height=height,
            alt_text=alt_text,
        )
        self._append_pending_picture(paragraph, pending)

    def _append_pending_picture(
        self,
        paragraph: Paragraph,
        picture: _PendingPicture,
    ) -> None:
        if self.document is None:
            paragraph.inlines.append(picture)
            return
        paragraph.add_picture(
            self.document.create_picture(
                picture.source,
                width=picture.width,
                height=picture.height,
                alt_text=picture.alt_text,
            )
        )

    def _part_widths(
        self,
        width: Length | int | float | None,
        parts: list[tuple[Any | None, Alignment]],
        *,
        proportional: bool,
    ) -> list[Length | None]:
        length = ensure_length(width, default_unit="in")
        if length is None:
            return [None for _ in parts]
        if len(parts) == 1:
            return [length]

        if proportional and len(parts) == 2:
            weights = [self._content_width_hint(content) for content, _ in parts]
            total_weight = sum(weights)
            if total_weight <= 0:
                ratios = [0.5, 0.5]
            else:
                first_ratio = weights[0] / total_weight
                first_ratio = min(0.8, max(0.2, first_ratio))
                ratios = [first_ratio, 1.0 - first_ratio]
            return [inch(length.inches() * ratio) for ratio in ratios]

        part_width = inch(length.inches() / len(parts))
        return [part_width for _ in parts]

    def _content_width_hint(self, content: Any | None) -> float:
        if content is None:
            return 0.0
        if isinstance(content, Picture):
            return self._length_width_hint(Length(content.width_emu, "emu"))
        if isinstance(content, _PendingPicture):
            if content.width is not None:
                return self._length_width_hint(content.width)
            if content.height is not None:
                return self._length_width_hint(content.height)
            return 8.0
        if isinstance(content, Path | bytes):
            return 8.0
        if isinstance(content, PageField):
            return 3.0
        if isinstance(content, Run):
            if content.break_type:
                return 0.0
            if content.tab:
                return 1.5
            return self._text_width_hint(content.text)
        if isinstance(content, str):
            return self._text_width_hint(content)
        return self._text_width_hint(str(content))

    def _length_width_hint(self, value: Length | int | float | None) -> float:
        length = ensure_length(value, default_unit="in")
        if length is None:
            return 0.0
        return max(2.0, length.inches() * 8.0)

    def _text_width_hint(self, text: str) -> float:
        total = 0.0
        try:
            for literal, field_name, _, _ in Formatter().parse(text):
                total += self._plain_text_width_hint(literal)
                if field_name is not None:
                    total += self._page_field_width_hint(field_name)
        except ValueError:
            total += self._plain_text_width_hint(text)
        return max(1.0, total)

    def _plain_text_width_hint(self, text: str) -> float:
        import unicodedata

        total = 0.0
        for char in text:
            if char.isspace():
                total += 0.35
                continue
            if unicodedata.east_asian_width(char) in ("F", "W"):
                total += 2.0
            else:
                total += 1.0
        return total

    def _page_field_width_hint(self, field_name: str) -> float:
        if field_name in _PAGE_NUMBER_PLACEHOLDERS:
            return 3.0
        return self._plain_text_width_hint("{" + field_name + "}")

    def add_picture(
        self,
        source: str | Path | bytes,
        *,
        width: Length | int | float | None = None,
        height: Length | int | float | None = None,
        alt_text: str = "",
        alignment: Alignment | None = None,
    ) -> Paragraph:
        picture = self.document.create_picture(
            source,
            width=width,
            height=height,
            alt_text=alt_text,
        )
        paragraph = self.add_paragraph(alignment=alignment)
        paragraph.add_picture(picture)
        return paragraph

    def has_content(self) -> bool:
        return bool(self.blocks)

    def add_table(
        self,
        rows: int,
        cols: int,
        *,
        data: list[list[Any]] | None = None,
        cells: list[TableCell] | None = None,
        style: str | None = "TableGrid",
        alignment: Alignment | None = None,
        borders: bool = True,
    ) -> Table:
        table = _create_table(
            rows=rows,
            cols=cols,
            data=data,
            cells=cells,
            style=style,
            alignment=alignment,
            borders=borders,
            document=self.document,
        )
        self.blocks.append(table)
        return table


@dataclass
class Footnote:
    footnote_id: int
    blocks: list[Any] = field(default_factory=list)
    document: Document | None = field(default=None, repr=False, compare=False)

    def add_paragraph(
        self,
        text: str = "",
        *,
        style: str | None = None,
        alignment: Alignment | None = None,
        line_spacing: float | None = None,
        space_before: Length | int | float | None = None,
        space_after: Length | int | float | None = None,
        left_indent: Length | int | float | None = None,
        right_indent: Length | int | float | None = None,
        first_line_indent: Length | int | float | None = None,
        keep_with_next: bool | None = None,
        keep_together: bool | None = None,
    ) -> Paragraph:
        paragraph = Paragraph(
            style=style,
            format=ParagraphFormat(
                alignment=alignment,
                line_spacing=line_spacing,
                space_before=space_before,
                space_after=space_after,
                left_indent=left_indent,
                right_indent=right_indent,
                first_line_indent=first_line_indent,
                keep_with_next=keep_with_next,
                keep_together=keep_together,
            ),
        )
        if text:
            paragraph.add_run(text)
        paragraph.document = self.document
        self.blocks.append(paragraph)
        return paragraph

    def add_table(
        self,
        rows: int,
        cols: int,
        *,
        data: list[list[Any]] | None = None,
        cells: list[TableCell] | None = None,
        style: str | None = "TableGrid",
        alignment: Alignment | None = None,
        borders: bool = True,
    ) -> Table:
        table = _create_table(
            rows=rows,
            cols=cols,
            data=data,
            cells=cells,
            style=style,
            alignment=alignment,
            borders=borders,
            document=self.document,
        )
        self.blocks.append(table)
        return table


@dataclass
class PageNumbering:
    start: int | None = None
    format: PageNumberFormat | None = None


@dataclass
class TableOfContents:
    title: str | None = "Contents"
    levels: tuple[int, int] = (1, 3)
    hyperlink: bool = True
    use_outline_levels: bool = True
    hide_page_numbers_in_web: bool = True
    dirty: bool = True
    title_style: str | None = "TOCHeading"


@dataclass
class Caption:
    text: str
    label: str = "Figure"
    sequence: str = "Figure"
    separator: str = " "
    style: str | None = "Caption"
    numbering_format: str = "ARABIC"
    dirty: bool = True


@dataclass
class TableOfFigures:
    title: str | None = "Table of Figures"
    sequence: str = "Figure"
    hyperlink: bool = True
    hide_page_numbers_in_web: bool = True
    dirty: bool = True
    title_style: str | None = "TOCHeading"


@dataclass
class Section:
    start: Literal["nextPage", "continuous", "evenPage", "oddPage", "nextColumn"] = "nextPage"
    columns: int = 1
    column_space: Length | int | float = field(default_factory=lambda: inch(0.5))
    column_widths: list[Length | int | float] | None = None
    equal_width: bool = True
    page_width: Length | int | float = field(default_factory=lambda: inch(8.5))
    page_height: Length | int | float = field(default_factory=lambda: inch(11))
    margins: SectionMargins = field(default_factory=SectionMargins)
    orientation: Literal["portrait", "landscape"] = "portrait"
    blocks: list[Any] = field(default_factory=list)
    header: HeaderFooter = field(default_factory=HeaderFooter)
    footer: HeaderFooter = field(default_factory=HeaderFooter)
    first_header: HeaderFooter = field(default_factory=HeaderFooter)
    first_footer: HeaderFooter = field(default_factory=HeaderFooter)
    even_header: HeaderFooter = field(default_factory=HeaderFooter)
    even_footer: HeaderFooter = field(default_factory=HeaderFooter)
    first_page_different: bool = False
    odd_even_different: bool = False
    page_numbering: PageNumbering = field(default_factory=PageNumbering)
    document: Document | None = field(default=None, repr=False, compare=False)

    @property
    def odd_header(self) -> HeaderFooter:
        return self.header

    @property
    def odd_footer(self) -> HeaderFooter:
        return self.footer

    def add_paragraph(
        self,
        text: str = "",
        *,
        style: str | None = None,
        alignment: Alignment | None = None,
        line_spacing: float | None = None,
        space_before: Length | int | float | None = None,
        space_after: Length | int | float | None = None,
        left_indent: Length | int | float | None = None,
        right_indent: Length | int | float | None = None,
        first_line_indent: Length | int | float | None = None,
        keep_with_next: bool | None = None,
        keep_together: bool | None = None,
    ) -> Paragraph:
        paragraph = Paragraph(
            style=style,
            format=ParagraphFormat(
                alignment=alignment,
                line_spacing=line_spacing,
                space_before=space_before,
                space_after=space_after,
                left_indent=left_indent,
                right_indent=right_indent,
                first_line_indent=first_line_indent,
                keep_with_next=keep_with_next,
                keep_together=keep_together,
            ),
        )
        if text:
            paragraph.add_run(text)
        paragraph.document = self.document
        self.blocks.append(paragraph)
        return paragraph

    def add_heading(self, text: str, *, level: int = 1) -> Paragraph:
        if level < 1 or level > 3:
            raise ValueError("Heading level must be between 1 and 3")
        return self.add_paragraph(text, style=f"Heading{level}")

    def add_list_item(
        self,
        text: str,
        *,
        ordered: bool = False,
        level: int = 0,
    ) -> Paragraph:
        if level < 0 or level > 8:
            raise ValueError("List level must be between 0 and 8")
        paragraph = self.add_paragraph(text)
        paragraph.style = "ListParagraph"
        paragraph.list_kind = "decimal" if ordered else "bullet"
        paragraph.list_level = level
        return paragraph

    def add_toc(
        self,
        *,
        title: str | None = "Contents",
        levels: tuple[int, int] = (1, 3),
        hyperlink: bool = True,
        use_outline_levels: bool = True,
        hide_page_numbers_in_web: bool = True,
        dirty: bool = True,
        title_style: str | None = "TOCHeading",
    ) -> TableOfContents:
        if len(levels) != 2:
            raise ValueError("toc levels must contain exactly two values")
        start_level, end_level = levels
        if start_level < 1 or end_level < 1 or start_level > 9 or end_level > 9:
            raise ValueError("toc levels must be between 1 and 9")
        if start_level > end_level:
            raise ValueError("toc start level cannot be greater than end level")
        toc = TableOfContents(
            title=title,
            levels=(start_level, end_level),
            hyperlink=hyperlink,
            use_outline_levels=use_outline_levels,
            hide_page_numbers_in_web=hide_page_numbers_in_web,
            dirty=dirty,
            title_style=title_style,
        )
        self.blocks.append(toc)
        return toc

    def add_caption(
        self,
        text: str,
        *,
        label: str = "Figure",
        sequence: str = "Figure",
        separator: str = " ",
        style: str | None = "Caption",
        numbering_format: str = "ARABIC",
        dirty: bool = True,
    ) -> Caption:
        caption = _create_caption(
            text=text,
            label=label,
            sequence=sequence,
            separator=separator,
            style=style,
            numbering_format=numbering_format,
            dirty=dirty,
        )
        self.blocks.append(caption)
        return caption

    def add_table_of_figures(
        self,
        *,
        title: str | None = "Table of Figures",
        sequence: str = "Figure",
        hyperlink: bool = True,
        hide_page_numbers_in_web: bool = True,
        dirty: bool = True,
        title_style: str | None = "TOCHeading",
    ) -> TableOfFigures:
        sequence = _validate_field_identifier(sequence, name="table of figures sequence")
        table = TableOfFigures(
            title=title,
            sequence=sequence,
            hyperlink=hyperlink,
            hide_page_numbers_in_web=hide_page_numbers_in_web,
            dirty=dirty,
            title_style=title_style,
        )
        self.blocks.append(table)
        return table

    def add_table(
        self,
        rows: int | None = None,
        cols: int | None = None,
        *,
        data: list[list[Any]] | None = None,
        cells: list[TableCell] | None = None,
        style: str | None = "TableGrid",
        alignment: Alignment | None = None,
        borders: bool = True,
    ) -> Table:
        if data is not None and cells is not None:
            raise ValueError("data and cells cannot be used together")
        if cells is not None and (rows is None or cols is None):
            raise ValueError("rows and cols are required when cells are provided")
        if data is not None:
            inferred_rows = len(data)
            inferred_cols = max((len(row) for row in data), default=0)
            rows = inferred_rows if rows is None else rows
            cols = inferred_cols if cols is None else cols
        table = _create_table(
            rows=rows,
            cols=cols,
            data=data,
            cells=cells,
            style=style,
            alignment=alignment,
            borders=borders,
            document=self.document,
        )
        self.blocks.append(table)
        return table

    def add_picture(
        self,
        source: str | Path | bytes,
        *,
        width: Length | int | float | None = None,
        height: Length | int | float | None = None,
        alt_text: str = "",
        alignment: Alignment | None = None,
    ) -> Paragraph:
        if self.document is None:
            raise ValueError("Section is not attached to a document")
        picture = self.document.create_picture(
            source,
            width=width,
            height=height,
            alt_text=alt_text,
        )
        paragraph = self.add_paragraph(alignment=alignment)
        paragraph.add_picture(picture)
        return paragraph

    def add_page_break(self) -> Paragraph:
        paragraph = self.add_paragraph()
        paragraph.add_page_break()
        return paragraph

    def set_page_size(
        self,
        *,
        width: Length | int | float,
        height: Length | int | float,
        orientation: Literal["portrait", "landscape"] = "portrait",
    ) -> Section:
        if orientation not in ("portrait", "landscape"):
            raise ValueError("orientation must be 'portrait' or 'landscape'")
        self.page_width = width
        self.page_height = height
        self.orientation = orientation
        _validate_section_columns(
            columns=self.columns,
            column_space=self.column_space,
            column_widths=self.column_widths,
            page_width=self.page_width,
            margins=self.margins,
        )
        return self

    def set_margins(
        self,
        *,
        top: Length | int | float | None = None,
        right: Length | int | float | None = None,
        bottom: Length | int | float | None = None,
        left: Length | int | float | None = None,
        header: Length | int | float | None = None,
        footer: Length | int | float | None = None,
        gutter: Length | int | float | None = None,
    ) -> Section:
        margins = self.margins
        self.margins = SectionMargins(
            top=margins.top if top is None else top,
            right=margins.right if right is None else right,
            bottom=margins.bottom if bottom is None else bottom,
            left=margins.left if left is None else left,
            header=margins.header if header is None else header,
            footer=margins.footer if footer is None else footer,
            gutter=margins.gutter if gutter is None else gutter,
        )
        _validate_section_columns(
            columns=self.columns,
            column_space=self.column_space,
            column_widths=self.column_widths,
            page_width=self.page_width,
            margins=self.margins,
        )
        return self

    def set_page_numbering(
        self,
        *,
        start: int | None = None,
        format: PageNumberFormat | None = None,
    ) -> Section:
        if start is not None and start < 1:
            raise ValueError("page numbering start must be positive")
        if format is not None and format not in ("decimal", "upperRoman", "lowerRoman", "upperLetter", "lowerLetter"):
            raise ValueError("invalid page numbering format")
        self.page_numbering.start = start
        self.page_numbering.format = format
        return self

    def add_page_number(
        self,
        *,
        position: Literal["header", "footer"] = "footer",
        variant: Literal["default", "first", "even"] = "default",
        template: str = "{page}",
        alignment: Alignment | None = "center",
        style: str | None = None,
    ) -> Paragraph:
        target = self._page_number_target(position=position, variant=variant)
        paragraph = target.add_paragraph(style=style, alignment=alignment)
        _append_page_number_text(paragraph, template)
        return paragraph

    def _page_number_target(
        self,
        *,
        position: Literal["header", "footer"],
        variant: Literal["default", "first", "even"],
    ) -> HeaderFooter:
        if position == "header":
            if variant == "default":
                return self.header
            if variant == "first":
                self.first_page_different = True
                return self.first_header
            if variant == "even":
                self.odd_even_different = True
                return self.even_header
        elif position == "footer":
            if variant == "default":
                return self.footer
            if variant == "first":
                self.first_page_different = True
                return self.first_footer
            if variant == "even":
                self.odd_even_different = True
                return self.even_footer
        else:
            raise ValueError("position must be 'header' or 'footer'")
        raise ValueError("variant must be 'default', 'first', or 'even'")


def _validate_field_identifier(value: str, *, name: str) -> str:
    identifier = str(value).strip()
    if not identifier:
        raise ValueError(f"{name} cannot be empty")
    if any(char.isspace() or char in {'"', "\\"} for char in identifier):
        raise ValueError(f"{name} cannot contain spaces, quotes, or backslashes")
    return identifier


def _create_caption(
    *,
    text: str,
    label: str,
    sequence: str,
    separator: str,
    style: str | None,
    numbering_format: str,
    dirty: bool,
) -> Caption:
    sequence = _validate_field_identifier(sequence, name="caption sequence")
    numbering_format = _validate_field_identifier(numbering_format, name="caption numbering format")
    return Caption(
        text=str(text),
        label=str(label),
        sequence=sequence,
        separator=str(separator),
        style=style,
        numbering_format=numbering_format,
        dirty=dirty,
    )


def _validate_section_columns(
    *,
    columns: int,
    column_space: Length | int | float,
    column_widths: list[Length | int | float] | None,
    page_width: Length | int | float,
    margins: SectionMargins,
) -> None:
    if column_widths is None:
        return
    page_width_len = ensure_length(page_width, default_unit="in")
    left = ensure_length(margins.left, default_unit="in")
    right = ensure_length(margins.right, default_unit="in")
    gutter = ensure_length(margins.gutter, default_unit="in")
    gap = ensure_length(column_space, default_unit="pt")
    assert page_width_len is not None and left is not None and right is not None and gutter is not None and gap is not None
    available = page_width_len.twips() - left.twips() - right.twips() - gutter.twips()
    total = sum(
        ensure_length(width, default_unit="pt").twips()
        for width in column_widths
    )
    total += (columns - 1) * gap.twips()
    if total > available:
        raise ValueError("column widths exceed available page width for the section")


@dataclass
class ParagraphStyle:
    style_id: str
    name: str | None = None
    based_on: str = "Normal"
    next_style: str | None = None
    font: str | None = None
    east_asia_font: str | None = None
    size: Length | int | float | None = None
    bold: bool | None = None
    italic: bool | None = None
    color: str | None = None
    alignment: Alignment | None = None
    space_before: Length | int | float | None = None
    space_after: Length | int | float | None = None
    outline_level: int | None = None


@dataclass
class TableStyle:
    style_id: str
    name: str | None = None
    based_on: str = "TableNormal"
    header_shading: str | None = None
    banded_row_shading: ShadingPair | None = None
    banded_column_shading: ShadingPair | None = None
    first_column_shading: str | None = None
    last_column_shading: str | None = None


@dataclass
class DocumentProperties:
    title: str = ""
    subject: str = ""
    creator: str = "memect.docx"
    keywords: str = ""
    description: str = ""
    created: datetime = field(default_factory=lambda: datetime.now(UTC))
    modified: datetime = field(default_factory=lambda: datetime.now(UTC))


class Document:
    """Create a DOCX document from a structured Python object model."""

    def __init__(self, *, title: str = "", creator: str = "memect.docx") -> None:
        self.sections: list[Section] = [Section(start="nextPage")]
        self.section = self.sections[0]
        self._attach_document_refs(self.section)
        self.defaults = DocumentDefaults()
        self.properties = DocumentProperties(title=title, creator=creator)
        self.paragraph_styles: dict[str, ParagraphStyle] = {}
        self.styles = self.paragraph_styles
        self.table_styles: dict[str, TableStyle] = {}
        self._next_image_id = 1
        self.footnotes: list[Footnote] = []
        self._next_footnote_id = 1

    def add_section(
        self,
        *,
        start: str = "nextPage",
        columns: int = 1,
        column_space: Length | int | float = 36,
        column_widths: list[Length | int | float] | None = None,
        equal_width: bool = True,
        page_width: Length | int | float | None = None,
        page_height: Length | int | float | None = None,
        orientation: str | None = None,
        margins: SectionMargins | None = None,
    ) -> Section:
        if start not in ("nextPage", "continuous", "evenPage", "oddPage", "nextColumn"):
            raise ValueError("Invalid section start type")
        if columns < 1:
            raise ValueError("columns must be positive")
        if orientation is not None and orientation not in ("portrait", "landscape"):
            raise ValueError("orientation must be 'portrait' or 'landscape'")
        if column_widths is not None and len(column_widths) != columns:
            raise ValueError("column_widths length must match columns")
        if column_widths is not None and columns == 1:
            raise ValueError("column_widths requires columns > 1")
        base = self.section
        _validate_section_columns(
            columns=columns,
            column_space=column_space,
            column_widths=column_widths,
            page_width=base.page_width if page_width is None else page_width,
            margins=base.margins if margins is None else margins,
        )
        section = Section(
            start=start,
            columns=columns,
            column_space=column_space,
            column_widths=list(column_widths) if column_widths is not None else None,
            equal_width=equal_width if column_widths is None else False,
            page_width=base.page_width if page_width is None else page_width,
            page_height=base.page_height if page_height is None else page_height,
            margins=base.margins if margins is None else margins,
            orientation=base.orientation if orientation is None else orientation,
        )
        self._attach_document_refs(section)
        self.sections.append(section)
        self.section = section
        return section

    def create_footnote(self, text: str = "") -> Footnote:
        footnote = Footnote(footnote_id=self._next_footnote_id)
        footnote.document = self
        self._next_footnote_id += 1
        self.footnotes.append(footnote)
        if text:
            footnote.add_paragraph(text)
        return footnote

    def create_picture(
        self,
        source: str | Path | bytes,
        *,
        width: Length | int | float | None = None,
        height: Length | int | float | None = None,
        alt_text: str = "",
    ) -> Picture:
        from .media import load_picture

        picture = load_picture(
            source,
            image_id=self._next_image_id,
            width=width,
            height=height,
            alt_text=alt_text,
        )
        self._next_image_id += 1
        return picture

    def set_default_font(
        self,
        *,
        font: str | None = None,
        east_asia_font: str | None = None,
        size: Length | int | float | None = None,
        color: str | None = None,
    ) -> Document:
        if font is not None:
            self.defaults.font = font
        if east_asia_font is not None:
            self.defaults.east_asia_font = east_asia_font
        if size is not None:
            self.defaults.size = size
        if color is not None:
            self.defaults.color = color
        return self

    def add_paragraph_style(
        self,
        style_id: str,
        *,
        name: str | None = None,
        based_on: str = "Normal",
        next_style: str | None = None,
        font: str | None = None,
        east_asia_font: str | None = None,
        size: Length | int | float | None = None,
        bold: bool | None = None,
        italic: bool | None = None,
        color: str | None = None,
        alignment: Alignment | None = None,
        space_before: Length | int | float | None = None,
        space_after: Length | int | float | None = None,
        outline_level: int | None = None,
    ) -> ParagraphStyle:
        if outline_level is not None and (outline_level < 0 or outline_level > 8):
            raise ValueError("outline_level must be between 0 and 8")
        style = ParagraphStyle(
            style_id=style_id,
            name=name,
            based_on=based_on,
            next_style=next_style,
            font=font,
            east_asia_font=east_asia_font,
            size=size,
            bold=bold,
            italic=italic,
            color=color,
            alignment=alignment,
            space_before=space_before,
            space_after=space_after,
            outline_level=outline_level,
        )
        self.paragraph_styles[style_id] = style
        return style

    def add_table_style(
        self,
        style_id: str,
        *,
        name: str | None = None,
        based_on: str = "TableNormal",
        header_shading: str | None = None,
        banded_rows: ShadingPair | None = None,
        banded_columns: ShadingPair | None = None,
        first_column_shading: str | None = None,
        last_column_shading: str | None = None,
    ) -> TableStyle:
        style = TableStyle(
            style_id=style_id,
            name=name,
            based_on=based_on,
            header_shading=header_shading,
            banded_row_shading=banded_rows,
            banded_column_shading=banded_columns,
            first_column_shading=first_column_shading,
            last_column_shading=last_column_shading,
        )
        self.table_styles[style_id] = style
        return style

    def to_bytes(self) -> bytes:
        from .ooxml import build_package

        return build_package(self)

    def save(self, path: str | Path) -> None:
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(self.to_bytes())

    def _attach_document_refs(self, section: Section) -> None:
        section.document = self
        section.header.document = self
        section.footer.document = self
        section.first_header.document = self
        section.first_footer.document = self
        section.even_header.document = self
        section.even_footer.document = self
