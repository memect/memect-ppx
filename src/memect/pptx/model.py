"""Presentation object model used before PPTX serialization."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .media import LoadedImage, load_image
from .units import Length, ensure_length, inch, pt, to_emu, to_points

Alignment = Literal["left", "center", "right", "justify"]
VerticalAlignment = Literal["top", "middle", "bottom"]
ShapeKind = Literal["rect"]
PlaceholderKind = Literal["title", "body", "centerTitle", "subTitle"]


@dataclass
class TextStyle:
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
    style: TextStyle = field(default_factory=TextStyle)


@dataclass
class Paragraph:
    runs: list[Run] = field(default_factory=list)
    alignment: Alignment | None = None
    line_spacing: Length | int | float | None = None
    space_before: Length | int | float | None = None
    space_after: Length | int | float | None = None

    def add_run(
        self,
        text: str = "",
        *,
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
            style=TextStyle(
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
        self.runs.append(run)
        return run

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)


@dataclass
class TextBox:
    left: Length | int | float
    top: Length | int | float
    width: Length | int | float
    height: Length | int | float
    paragraphs: list[Paragraph] = field(default_factory=list)
    fill: str | None = None
    line: str | None = None
    vertical_align: VerticalAlignment = "top"
    margin_left: Length | int | float = field(default_factory=lambda: pt(3.6))
    margin_right: Length | int | float = field(default_factory=lambda: pt(3.6))
    margin_top: Length | int | float = field(default_factory=lambda: pt(3.6))
    margin_bottom: Length | int | float = field(default_factory=lambda: pt(3.6))
    auto_fit: bool = False
    placeholder: PlaceholderKind | None = None

    def add_paragraph(self, text: str = "", *, alignment: Alignment | None = None) -> Paragraph:
        paragraph = Paragraph(alignment=alignment)
        if text:
            paragraph.add_run(text)
        self.paragraphs.append(paragraph)
        return paragraph


@dataclass
class Shape:
    kind: ShapeKind
    left: Length | int | float
    top: Length | int | float
    width: Length | int | float
    height: Length | int | float
    fill: str | None = None
    line: str | None = None
    line_width: Length | int | float | None = None


@dataclass
class Line:
    x1: Length | int | float
    y1: Length | int | float
    x2: Length | int | float
    y2: Length | int | float
    color: str | None = "000000"
    width: Length | int | float = field(default_factory=lambda: pt(1))


@dataclass
class Picture:
    left: Length | int | float
    top: Length | int | float
    width: Length | int | float | None
    height: Length | int | float | None
    image: LoadedImage
    alt_text: str = ""


@dataclass
class TableCell:
    row_index: int
    col_index: int
    text: str = ""
    paragraphs: list[Paragraph] = field(default_factory=list)
    fill: str | None = None
    vertical_align: VerticalAlignment = "middle"
    row_span: int = 1
    col_span: int = 1
    width: Length | int | float | None = None
    height: Length | int | float | None = None

    def add_paragraph(self, text: str = "", *, alignment: Alignment | None = None) -> Paragraph:
        paragraph = Paragraph(alignment=alignment)
        if text:
            paragraph.add_run(text)
        self.paragraphs.append(paragraph)
        return paragraph

    def has_content(self) -> bool:
        return bool(self.text or self.paragraphs)


@dataclass
class Table:
    rows: int
    cols: int
    left: Length | int | float
    top: Length | int | float
    width: Length | int | float
    height: Length | int | float
    grid: list[list[TableCell]]
    cells: list[TableCell]
    column_widths: list[Length | int | float] | None = None
    row_heights: list[Length | int | float] | None = None
    border_color: str | None = "808080"
    border_width: Length | int | float = field(default_factory=lambda: pt(0.75))

    def cell(self, row: int, col: int) -> TableCell:
        return self.grid[row][col]

    def is_origin(self, row: int, col: int) -> bool:
        cell = self.cell(row, col)
        return cell.row_index == row and cell.col_index == col

    def origin(self, row: int, col: int) -> tuple[int, int]:
        cell = self.cell(row, col)
        return cell.row_index, cell.col_index

    def merge(self, row: int, col: int, *, row_span: int = 1, col_span: int = 1) -> TableCell:
        if row_span < 1 or col_span < 1:
            raise ValueError("row_span and col_span must be positive")
        if row + row_span > self.rows or col + col_span > self.cols:
            raise ValueError("merged cell exceeds table bounds")
        origin = self.cell(row, col)
        if origin.row_index != row or origin.col_index != col:
            raise ValueError("merge must start from an origin cell")
        if origin.row_span > 1 or origin.col_span > 1:
            if origin.row_span == row_span and origin.col_span == col_span:
                return origin
            raise ValueError("cannot resize an already merged cell")

        covered: list[TableCell] = []
        for row_index in range(row, row + row_span):
            for col_index in range(col, col + col_span):
                cell = self.cell(row_index, col_index)
                if not any(cell is item for item in covered):
                    covered.append(cell)

        for cell in covered:
            if cell is origin:
                continue
            if cell.row_span > 1 or cell.col_span > 1:
                raise ValueError("cannot merge over an already merged cell")
            if cell.has_content():
                raise ValueError("cannot merge over non-empty cells")

        for cell in covered:
            if cell is origin:
                continue
            self.cells = [item for item in self.cells if item is not cell]

        origin.row_index = row
        origin.col_index = col
        origin.row_span = row_span
        origin.col_span = col_span
        for row_index in range(row, row + row_span):
            for col_index in range(col, col + col_span):
                self.grid[row_index][col_index] = origin
        return origin


SlideElement = TextBox | Shape | Line | Picture | Table


@dataclass
class Slide:
    elements: list[SlideElement] = field(default_factory=list)
    background: str | None = None
    speaker_notes: str | None = None
    deck: Any | None = field(default=None, repr=False, compare=False)

    def add_textbox(
        self,
        text: str = "",
        *,
        left: Length | int | float,
        top: Length | int | float,
        width: Length | int | float,
        height: Length | int | float,
        font: str | None = None,
        east_asia_font: str | None = None,
        size: Length | int | float | None = None,
        bold: bool | None = None,
        italic: bool | None = None,
        underline: bool | None = None,
        strike: bool | None = None,
        color: str | None = None,
        alignment: Alignment | None = None,
        fill: str | None = None,
        line: str | None = None,
        vertical_align: VerticalAlignment = "top",
        auto_fit: bool = False,
        placeholder: PlaceholderKind | None = None,
    ) -> TextBox:
        textbox = TextBox(
            left=left,
            top=top,
            width=width,
            height=height,
            fill=fill,
            line=line,
            vertical_align=vertical_align,
            auto_fit=auto_fit,
            placeholder=placeholder,
        )
        if text:
            paragraph = Paragraph(alignment=alignment)
            paragraph.add_run(
                text,
                font=font,
                east_asia_font=east_asia_font,
                size=size,
                bold=bold,
                italic=italic,
                underline=underline,
                strike=strike,
                color=color,
            )
            textbox.paragraphs.append(paragraph)
        self.elements.append(textbox)
        return textbox

    def add_rect(
        self,
        *,
        left: Length | int | float,
        top: Length | int | float,
        width: Length | int | float,
        height: Length | int | float,
        fill: str | None = None,
        line: str | None = None,
        line_width: Length | int | float | None = None,
    ) -> Shape:
        shape = Shape(
            kind="rect",
            left=left,
            top=top,
            width=width,
            height=height,
            fill=fill,
            line=line,
            line_width=line_width,
        )
        self.elements.append(shape)
        return shape

    def add_line(
        self,
        *,
        x1: Length | int | float,
        y1: Length | int | float,
        x2: Length | int | float,
        y2: Length | int | float,
        color: str | None = "000000",
        width: Length | int | float | None = None,
    ) -> Line:
        line = Line(x1=x1, y1=y1, x2=x2, y2=y2, color=color, width=pt(1) if width is None else width)
        self.elements.append(line)
        return line

    def add_picture(
        self,
        image: LoadedImage | str | Path | bytes,
        *,
        left: Length | int | float,
        top: Length | int | float,
        width: Length | int | float | None = None,
        height: Length | int | float | None = None,
        alt_text: str = "",
    ) -> Picture:
        if not isinstance(image, LoadedImage):
            if self.deck is None:
                raise ValueError("Slide is not attached to a presentation")
            image = load_image(image, image_id=self.deck.next_image_id())
        picture = Picture(left=left, top=top, width=width, height=height, image=image, alt_text=alt_text)
        self.elements.append(picture)
        return picture

    def add_table(
        self,
        *,
        rows: int | None = None,
        cols: int | None = None,
        data: list[list[Any]] | None = None,
        cells: list[TableCell] | None = None,
        left: Length | int | float,
        top: Length | int | float,
        width: Length | int | float,
        height: Length | int | float,
        column_widths: list[Length | int | float] | None = None,
        row_heights: list[Length | int | float] | None = None,
        border_color: str | None = "808080",
        border_width: Length | int | float | None = None,
    ) -> Table:
        if data is not None and cells is not None:
            raise ValueError("data and cells cannot be used together")
        if data is not None:
            inferred_rows = len(data)
            inferred_cols = max((len(row) for row in data), default=0)
            rows = inferred_rows if rows is None else rows
            cols = inferred_cols if cols is None else cols
        if cells is not None:
            if rows is None:
                rows = max((cell.row_index + cell.row_span for cell in cells), default=0)
            if cols is None:
                cols = max((cell.col_index + cell.col_span for cell in cells), default=0)
        if rows is None or cols is None:
            raise ValueError("rows and cols are required when data and cells are not provided")
        if rows < 1 or cols < 1:
            raise ValueError("rows and cols must be positive")
        if cells is None:
            grid, table_cells = _create_table_grid(rows, cols)
        else:
            grid, table_cells = _materialize_table_cells(rows, cols, cells)
        if data is not None:
            for row_index, values in enumerate(data[:rows]):
                for col_index, value in enumerate(values[:cols]):
                    grid[row_index][col_index].text = str(value)
        if cells is not None:
            if column_widths is None:
                column_widths = _infer_axis_sizes_from_cells(
                    cols,
                    table_cells,
                    total=width,
                    index_attr="col_index",
                    span_attr="col_span",
                    size_attr="width",
                )
            if row_heights is None:
                row_heights = _infer_axis_sizes_from_cells(
                    rows,
                    table_cells,
                    total=height,
                    index_attr="row_index",
                    span_attr="row_span",
                    size_attr="height",
                )
        table = Table(
            rows=rows,
            cols=cols,
            left=left,
            top=top,
            width=width,
            height=height,
            grid=grid,
            cells=table_cells,
            column_widths=column_widths,
            row_heights=row_heights,
            border_color=border_color,
            border_width=pt(0.75) if border_width is None else border_width,
        )
        self.elements.append(table)
        return table


def _create_table_grid(rows: int, cols: int) -> tuple[list[list[TableCell]], list[TableCell]]:
    grid: list[list[TableCell]] = []
    cells: list[TableCell] = []
    for row_index in range(rows):
        row_cells: list[TableCell] = []
        for col_index in range(cols):
            cell = TableCell(row_index=row_index, col_index=col_index)
            cells.append(cell)
            row_cells.append(cell)
        grid.append(row_cells)
    return grid, cells


def _materialize_table_cells(
    rows: int,
    cols: int,
    cells: list[TableCell],
) -> tuple[list[list[TableCell]], list[TableCell]]:
    grid: list[list[TableCell | None]] = [
        [None for _ in range(cols)]
        for _ in range(rows)
    ]
    unique_cells: list[TableCell] = []
    for cell in cells:
        if any(cell is item for item in unique_cells):
            continue
        if cell.row_index < 0 or cell.col_index < 0:
            raise ValueError("cell indexes must be non-negative")
        if cell.row_span < 1 or cell.col_span < 1:
            raise ValueError("cell spans must be positive")
        if cell.row_index + cell.row_span > rows or cell.col_index + cell.col_span > cols:
            raise ValueError("cell span exceeds table bounds")
        unique_cells.append(cell)
        for row_index in range(cell.row_index, cell.row_index + cell.row_span):
            for col_index in range(cell.col_index, cell.col_index + cell.col_span):
                if grid[row_index][col_index] is not None:
                    raise ValueError(f"overlapping table cell at {(row_index, col_index)}")
                grid[row_index][col_index] = cell

    for row_index in range(rows):
        for col_index in range(cols):
            if grid[row_index][col_index] is None:
                cell = TableCell(row_index=row_index, col_index=col_index)
                unique_cells.append(cell)
                grid[row_index][col_index] = cell

    return [[cell for cell in row if cell is not None] for row in grid], unique_cells


def _infer_axis_sizes_from_cells(
    count: int,
    cells: list[TableCell],
    *,
    total: Length | int | float,
    index_attr: str,
    span_attr: str,
    size_attr: str,
) -> list[Length] | None:
    explicit: list[tuple[int, int, float]] = []
    single_candidates: list[list[float]] = [[] for _ in range(count)]

    for cell in cells:
        start = int(getattr(cell, index_attr))
        span = int(getattr(cell, span_attr))
        size_points = to_points(getattr(cell, size_attr), default_unit="pt")
        if size_points is None or size_points <= 0:
            continue
        if start < 0 or span < 1 or start + span > count:
            continue
        explicit.append((start, span, size_points))
        if span == 1:
            single_candidates[start].append(size_points)

    if not explicit:
        return None

    sizes: list[float | None] = [None for _ in range(count)]
    for index, values in enumerate(single_candidates):
        if values:
            sizes[index] = _median(values)

    for start, span, size_points in explicit:
        if span == 1:
            continue
        indexes = list(range(start, start + span))
        known = sum(sizes[index] or 0 for index in indexes)
        missing = [index for index in indexes if sizes[index] is None]
        if not missing:
            continue
        remaining = size_points - known
        if remaining <= 0:
            continue
        value = remaining / len(missing)
        for index in missing:
            sizes[index] = value

    total_points = to_points(total, default_unit="pt")
    known = sum(size or 0 for size in sizes)
    missing = [index for index, size in enumerate(sizes) if size is None]
    if missing:
        if total_points is not None and total_points > known:
            fallback = (total_points - known) / len(missing)
        elif total_points is not None and total_points > 0:
            fallback = total_points / count
        else:
            return None
        for index in missing:
            sizes[index] = fallback

    values = [max(1.0, float(size or 0)) for size in sizes]
    if total_points is not None and total_points > 0:
        current = sum(values)
        if current > 0:
            scale = total_points / current
            values = [max(1.0, value * scale) for value in values]
    return [pt(value) for value in values]


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


@dataclass
class ThemeDefaults:
    font: str = "Calibri"
    east_asia_font: str = "Microsoft YaHei"
    size: Length | int | float = field(default_factory=lambda: pt(18))
    color: str = "000000"


@dataclass
class PresentationProperties:
    title: str = ""
    creator: str = "memect.pptx"
    subject: str = ""
    keywords: str = ""
    description: str = ""


@dataclass
class Deck:
    slides: list[Slide] = field(default_factory=list)
    width: Length | int | float = field(default_factory=lambda: inch(13.3333333333))
    height: Length | int | float = field(default_factory=lambda: inch(7.5))
    defaults: ThemeDefaults = field(default_factory=ThemeDefaults)
    properties: PresentationProperties = field(default_factory=PresentationProperties)
    _next_image_id: int = 1

    def add_slide(self, *, background: str | None = None) -> Slide:
        slide = Slide(background=background, deck=self)
        self.slides.append(slide)
        return slide

    @property
    def width_emu(self) -> int:
        width = to_emu(self.width, default_unit="pt")
        assert width is not None
        return width

    @property
    def height_emu(self) -> int:
        height = to_emu(self.height, default_unit="pt")
        assert height is not None
        return height

    def next_image_id(self) -> int:
        image_id = self._next_image_id
        self._next_image_id += 1
        return image_id


def normalize_color(color: str | None) -> str | None:
    if color is None:
        return None
    value = color.strip()
    if value.startswith("#"):
        value = value[1:]
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        raise ValueError(f"Invalid RGB color: {color}")
    int(value, 16)
    return value.upper()


def rect_to_emu(
    left: Length | int | float,
    top: Length | int | float,
    width: Length | int | float,
    height: Length | int | float,
) -> tuple[int, int, int, int]:
    values = (
        to_emu(left, default_unit="pt"),
        to_emu(top, default_unit="pt"),
        to_emu(width, default_unit="pt"),
        to_emu(height, default_unit="pt"),
    )
    if any(value is None for value in values):
        raise ValueError("rectangle coordinates cannot be None")
    x, y, cx, cy = values
    assert x is not None and y is not None and cx is not None and cy is not None
    if cx < 0 or cy < 0:
        raise ValueError("width and height cannot be negative")
    return x, y, cx, cy


def resolve_picture_size(
    picture: Picture,
) -> tuple[int, int]:
    width_len = ensure_length(picture.width, default_unit="pt")
    height_len = ensure_length(picture.height, default_unit="pt")
    if width_len is None and height_len is None:
        return picture.image.width_emu, picture.image.height_emu
    if width_len is None:
        assert height_len is not None
        width = int(round(height_len.emu() * picture.image.width_emu / picture.image.height_emu))
        return width, height_len.emu()
    if height_len is None:
        height = int(round(width_len.emu() * picture.image.height_emu / picture.image.width_emu))
        return width_len.emu(), height
    return width_len.emu(), height_len.emu()


def path_name(value: str | Path) -> str:
    return Path(value).name
