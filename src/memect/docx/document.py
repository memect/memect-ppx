"""Public SDK entry point."""

from __future__ import annotations

from pathlib import Path

from .media import load_picture
from .model import (
    Alignment,
    DocumentDefaults,
    DocumentProperties,
    Footnote,
    ParagraphStyle,
    Picture,
    Section,
    SectionMargins,
    ShadingPair,
    TableStyle,
    _validate_section_columns,
)
from .units import Length


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
