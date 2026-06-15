"""WordprocessingML serialization."""

from __future__ import annotations

import re
from datetime import UTC
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

from .model import (
    DocumentProperties,
    Footnote,
    FootnoteReference,
    HeaderFooter,
    Paragraph,
    ParagraphFormat,
    PageField,
    ParagraphStyle,
    Picture,
    Run,
    RunStyle,
    Section,
    Table,
    TableCell,
)
from .opc import Package
from .errors import ValidationError
from .units import ensure_length, to_half_points, to_twips

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

XML_NS = "http://www.w3.org/XML/1998/namespace"

REL = {
    "app": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties",
    "core": "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
    "document": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
    "footer": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer",
    "footnotes": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes",
    "header": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header",
    "image": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
    "numbering": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering",
    "settings": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings",
    "styles": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles",
}

CONTENT_TYPES = {
    "app": "application/vnd.openxmlformats-officedocument.extended-properties+xml",
    "core": "application/vnd.openxmlformats-package.core-properties+xml",
    "document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "footer": "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml",
    "footnotes": "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
    "header": "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml",
    "numbering": "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml",
    "settings": "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml",
    "styles": "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml",
}

_INVALID_XML_10 = re.compile(
    r"[^\u0009\u000A\u000D\u0020-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]"
)


def build_package(document: Any) -> bytes:
    serializer = _Serializer(document)
    return serializer.build()


def _q(prefix: str, tag: str) -> str:
    return f"{{{NS[prefix]}}}{tag}"


def _w(tag: str, attrs: dict[str, Any] | None = None, *, nsmap: dict[str, str] | None = None) -> etree._Element:
    element = etree.Element(_q("w", tag), nsmap=nsmap)
    if attrs:
        _set_attrs(element, attrs)
    return element


def _sub(parent: etree._Element, tag: str, attrs: dict[str, Any] | None = None) -> etree._Element:
    child = etree.SubElement(parent, _q("w", tag))
    if attrs:
        _set_attrs(child, attrs)
    return child


def _set_attrs(element: etree._Element, attrs: dict[str, Any]) -> None:
    for key, value in attrs.items():
        if value is None:
            continue
        if ":" in key:
            prefix, local = key.split(":", 1)
            element.set(_q(prefix, local), str(value))
        else:
            element.set(key, str(value))


def _xml(root: etree._Element) -> bytes:
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)


class _Serializer:
    def __init__(self, document: Any) -> None:
        self.document = document
        self.package = Package()
        self.document_part = "word/document.xml"
        self._added_media: set[str] = set()
        self._use_even_odd_headers = any(
            section.odd_even_different
            or section.even_header.has_content()
            or section.even_footer.has_content()
            for section in document.sections
        )

    def build(self) -> bytes:
        root_rels = self.package.relationships()
        root_rels.add(REL["document"], "word/document.xml")
        root_rels.add(REL["core"], "docProps/core.xml")
        root_rels.add(REL["app"], "docProps/app.xml")

        doc_rels = self.package.relationships(self.document_part)
        doc_rels.add(REL["styles"], "styles.xml")
        doc_rels.add(REL["settings"], "settings.xml")
        doc_rels.add(REL["numbering"], "numbering.xml")
        if self.document.footnotes:
            doc_rels.add(REL["footnotes"], "footnotes.xml")

        self.package.add_part(
            self.document_part,
            CONTENT_TYPES["document"],
            self._document_xml(),
        )
        if self.document.footnotes:
            self.package.add_part("word/footnotes.xml", CONTENT_TYPES["footnotes"], self._footnotes_xml())
        self.package.add_part("word/styles.xml", CONTENT_TYPES["styles"], self._styles_xml())
        self.package.add_part("word/settings.xml", CONTENT_TYPES["settings"], self._settings_xml())
        self.package.add_part("word/numbering.xml", CONTENT_TYPES["numbering"], self._numbering_xml())
        self.package.add_part("docProps/core.xml", CONTENT_TYPES["core"], self._core_props_xml())
        self.package.add_part("docProps/app.xml", CONTENT_TYPES["app"], self._app_props_xml())
        return self.package.to_bytes()

    def _document_xml(self) -> bytes:
        root = _w("document", nsmap={key: NS[key] for key in ("w", "r", "wp", "a", "pic")})
        body = _sub(root, "body")
        sections = self.document.sections
        for index, section in enumerate(sections):
            for block in section.blocks:
                body.append(self._block_xml(block, self.document_part))
            is_last = index == len(sections) - 1
            header_rel_ids, footer_rel_ids = self._section_header_footer_rel_ids(section, index)
            include_type = (not is_last) or section.start != "nextPage"
            sect_pr = self._section_xml(section, header_rel_ids, footer_rel_ids, include_type=include_type)
            if is_last:
                body.append(sect_pr)
            else:
                self._attach_section_break(body, sect_pr)
        return _xml(root)

    def _header_footer_xml(self, tag: str, header_footer: HeaderFooter, source_part: str) -> bytes:
        root = _w(tag, nsmap={key: NS[key] for key in ("w", "r", "wp", "a", "pic")})
        blocks = header_footer.blocks or [Paragraph()]
        for block in blocks:
            root.append(self._block_xml(block, source_part))
        return _xml(root)

    def _attach_section_break(self, body: etree._Element, sect_pr: etree._Element) -> None:
        if len(body):
            last = body[-1]
            if last.tag == _q("w", "p"):
                p_pr = last.find(_q("w", "pPr"))
                if p_pr is None:
                    p_pr = _w("pPr")
                    last.insert(0, p_pr)
                p_pr.append(sect_pr)
                return
        paragraph = _w("p")
        p_pr = _sub(paragraph, "pPr")
        p_pr.append(sect_pr)
        body.append(paragraph)

    def _section_header_footer_rel_ids(
        self,
        section: Section,
        index: int,
    ) -> tuple[dict[str, str], dict[str, str]]:
        doc_rels = self.package.relationships(self.document_part)
        header_rel_ids: dict[str, str] = {}
        footer_rel_ids: dict[str, str] = {}

        def add_variant(
            rel_ids: dict[str, str],
            rel_kind: str,
            ref_type: str,
            content: HeaderFooter,
            part_suffix: str,
        ) -> None:
            if not content.has_content():
                return
            part_name = f"word/{rel_kind}{index + 1}_{part_suffix}.xml"
            rel_type = REL["header"] if rel_kind == "header" else REL["footer"]
            content_type = CONTENT_TYPES["header"] if rel_kind == "header" else CONTENT_TYPES["footer"]
            root_tag = "hdr" if rel_kind == "header" else "ftr"
            rel_ids[ref_type] = doc_rels.add(rel_type, part_name.split("/", 1)[1])
            self.package.add_part(
                part_name,
                content_type,
                self._header_footer_xml(root_tag, content, part_name),
            )

        add_variant(header_rel_ids, "header", "default", section.header, "default")
        add_variant(footer_rel_ids, "footer", "default", section.footer, "default")

        if section.first_page_different:
            add_variant(header_rel_ids, "header", "first", section.first_header, "first")
            add_variant(footer_rel_ids, "footer", "first", section.first_footer, "first")

        if self._use_even_odd_headers:
            add_variant(header_rel_ids, "header", "even", section.even_header, "even")
            add_variant(footer_rel_ids, "footer", "even", section.even_footer, "even")

        return header_rel_ids, footer_rel_ids

    def _block_xml(self, block: Any, source_part: str) -> etree._Element:
        if isinstance(block, Paragraph):
            return self._paragraph_xml(block, source_part)
        if isinstance(block, Table):
            return self._table_xml(block, source_part)
        raise TypeError(f"Unsupported document block: {type(block)!r}")

    def _paragraph_xml(self, paragraph: Paragraph, source_part: str) -> etree._Element:
        p = _w("p")
        p_pr = self._paragraph_properties_xml(paragraph)
        if p_pr is not None:
            p.append(p_pr)
        for inline in paragraph.inlines:
            if isinstance(inline, Run):
                run_xml = self._run_xml(inline)
                if run_xml is not None:
                    p.append(run_xml)
            elif isinstance(inline, Picture):
                p.append(self._picture_run_xml(inline, source_part))
            elif isinstance(inline, FootnoteReference):
                p.append(self._footnote_reference_xml(inline))
            elif isinstance(inline, PageField):
                p.append(self._page_field_xml(inline))
            else:
                raise TypeError(f"Unsupported inline object: {type(inline)!r}")
        return p

    def _paragraph_properties_xml(self, paragraph: Paragraph) -> etree._Element | None:
        p_pr = _w("pPr")
        if paragraph.style:
            _sub(p_pr, "pStyle", {"w:val": paragraph.style})

        if paragraph.list_kind is not None:
            num_id = "2" if paragraph.list_kind == "decimal" else "1"
            num_pr = _sub(p_pr, "numPr")
            _sub(num_pr, "ilvl", {"w:val": paragraph.list_level})
            _sub(num_pr, "numId", {"w:val": num_id})

        fmt = paragraph.format
        self._append_paragraph_format(p_pr, fmt)
        return p_pr if len(p_pr) else None

    def _append_paragraph_format(self, p_pr: etree._Element, fmt: ParagraphFormat) -> None:
        if fmt.alignment:
            value = "both" if fmt.alignment == "justify" else fmt.alignment
            _sub(p_pr, "jc", {"w:val": value})
        spacing_attrs: dict[str, Any] = {}
        before = to_twips(fmt.space_before, default_unit="pt")
        after = to_twips(fmt.space_after, default_unit="pt")
        if before is not None:
            spacing_attrs["w:before"] = before
        if after is not None:
            spacing_attrs["w:after"] = after
        if fmt.line_spacing is not None:
            spacing_attrs["w:line"] = int(round(fmt.line_spacing * 240))
            spacing_attrs["w:lineRule"] = "auto"
        if spacing_attrs:
            _sub(p_pr, "spacing", spacing_attrs)

        indent_attrs: dict[str, Any] = {}
        left = to_twips(fmt.left_indent, default_unit="pt")
        right = to_twips(fmt.right_indent, default_unit="pt")
        first_line = to_twips(fmt.first_line_indent, default_unit="pt")
        if left is not None:
            indent_attrs["w:left"] = left
        if right is not None:
            indent_attrs["w:right"] = right
        if first_line is not None:
            if first_line < 0:
                indent_attrs["w:hanging"] = abs(first_line)
            else:
                indent_attrs["w:firstLine"] = first_line
        if indent_attrs:
            _sub(p_pr, "ind", indent_attrs)
        if fmt.keep_with_next:
            _sub(p_pr, "keepNext")
        if fmt.keep_together:
            _sub(p_pr, "keepLines")

    def _run_xml(self, run: Run) -> etree._Element | None:
        r = _w("r")
        r_pr = self._run_properties_xml(run.style)
        if r_pr is not None:
            r.append(r_pr)
        if run.tab:
            _sub(r, "tab")
        if run.break_type:
            attrs = {"w:type": "page"} if run.break_type == "page" else None
            _sub(r, "br", attrs)
        if run.text:
            self._append_text(r, run.text)
        return r if len(r) else None

    def _run_properties_xml(self, style: RunStyle) -> etree._Element | None:
        r_pr = _w("rPr")
        if style.style:
            _sub(r_pr, "rStyle", {"w:val": style.style})
        if style.font or style.east_asia_font:
            attrs = {}
            if style.font:
                attrs["w:ascii"] = style.font
                attrs["w:hAnsi"] = style.font
            if style.east_asia_font:
                attrs["w:eastAsia"] = style.east_asia_font
            _sub(r_pr, "rFonts", attrs)
        if style.bold is not None:
            _sub(r_pr, "b", {"w:val": "1" if style.bold else "0"})
        if style.italic is not None:
            _sub(r_pr, "i", {"w:val": "1" if style.italic else "0"})
        if style.underline is not None:
            _sub(r_pr, "u", {"w:val": "single" if style.underline else "none"})
        if style.strike is not None:
            _sub(r_pr, "strike", {"w:val": "1" if style.strike else "0"})
        if style.color:
            _sub(r_pr, "color", {"w:val": _color(style.color)})
        size = to_half_points(style.size)
        if size is not None:
            _sub(r_pr, "sz", {"w:val": size})
            _sub(r_pr, "szCs", {"w:val": size})
        return r_pr if len(r_pr) else None

    def _footnote_reference_xml(self, footnote_ref: FootnoteReference) -> etree._Element:
        run = _w("r")
        r_pr = _sub(run, "rPr")
        _sub(r_pr, "rStyle", {"w:val": "FootnoteReference"})
        _sub(run, "footnoteReference", {"w:id": footnote_ref.footnote_id})
        return run

    def _page_field_xml(self, page_field: PageField) -> etree._Element:
        field = _w("fldSimple", {"w:instr": page_field.kind})
        run = _sub(field, "r")
        text = _sub(run, "t")
        text.text = "1"
        return field

    def _append_text(self, run: etree._Element, text: str) -> None:
        buffer: list[str] = []

        def flush() -> None:
            if not buffer:
                return
            node = _sub(run, "t")
            node.set(f"{{{XML_NS}}}space", "preserve")
            node.text = _clean_text("".join(buffer))
            buffer.clear()

        for char in text:
            if char == "\t":
                flush()
                _sub(run, "tab")
            elif char == "\n":
                flush()
                _sub(run, "br")
            elif char == "\r":
                continue
            else:
                buffer.append(char)
        flush()

    def _picture_run_xml(self, picture: Picture, source_part: str) -> etree._Element:
        rel_id = self._image_relationship(source_part, picture)
        r = _w("r")
        drawing = _sub(r, "drawing")
        inline = etree.SubElement(
            drawing,
            _q("wp", "inline"),
            {"distT": "0", "distB": "0", "distL": "0", "distR": "0"},
        )
        etree.SubElement(
            inline,
            _q("wp", "extent"),
            {"cx": str(picture.width_emu), "cy": str(picture.height_emu)},
        )
        etree.SubElement(inline, _q("wp", "effectExtent"), {"l": "0", "t": "0", "r": "0", "b": "0"})
        etree.SubElement(
            inline,
            _q("wp", "docPr"),
            {"id": str(picture.image_id), "name": picture.alt_text or picture.name},
        )
        frame_pr = etree.SubElement(inline, _q("wp", "cNvGraphicFramePr"))
        etree.SubElement(frame_pr, _q("a", "graphicFrameLocks"), {"noChangeAspect": "1"})
        graphic = etree.SubElement(inline, _q("a", "graphic"))
        graphic_data = etree.SubElement(
            graphic,
            _q("a", "graphicData"),
            {"uri": "http://schemas.openxmlformats.org/drawingml/2006/picture"},
        )
        pic = etree.SubElement(graphic_data, _q("pic", "pic"))
        nv_pic_pr = etree.SubElement(pic, _q("pic", "nvPicPr"))
        etree.SubElement(
            nv_pic_pr,
            _q("pic", "cNvPr"),
            {"id": str(picture.image_id), "name": picture.name},
        )
        etree.SubElement(nv_pic_pr, _q("pic", "cNvPicPr"))
        blip_fill = etree.SubElement(pic, _q("pic", "blipFill"))
        etree.SubElement(blip_fill, _q("a", "blip"), {_q("r", "embed"): rel_id})
        stretch = etree.SubElement(blip_fill, _q("a", "stretch"))
        etree.SubElement(stretch, _q("a", "fillRect"))
        sp_pr = etree.SubElement(pic, _q("pic", "spPr"))
        xfrm = etree.SubElement(sp_pr, _q("a", "xfrm"))
        etree.SubElement(xfrm, _q("a", "off"), {"x": "0", "y": "0"})
        etree.SubElement(
            xfrm,
            _q("a", "ext"),
            {"cx": str(picture.width_emu), "cy": str(picture.height_emu)},
        )
        geom = etree.SubElement(sp_pr, _q("a", "prstGeom"), {"prst": "rect"})
        etree.SubElement(geom, _q("a", "avLst"))
        return r

    def _image_relationship(self, source_part: str, picture: Picture) -> str:
        target = f"media/{picture.name}"
        rel_id = self.package.relationships(source_part).add(REL["image"], target)
        media_part = f"word/{target}"
        if media_part not in self._added_media:
            self.package.add_part(media_part, picture.content_type, picture.data)
            self._added_media.add(media_part)
        return rel_id

    def _table_xml(self, table: Table, source_part: str) -> etree._Element:
        tbl = _w("tbl")
        tbl_pr = _sub(tbl, "tblPr")
        if table.style:
            _sub(tbl_pr, "tblStyle", {"w:val": table.style})
        width = to_twips(table.width, default_unit="in")
        if width is None:
            _sub(tbl_pr, "tblW", {"w:w": "0", "w:type": "auto"})
        else:
            _sub(tbl_pr, "tblW", {"w:w": width, "w:type": "dxa"})
        if table.alignment:
            value = "both" if table.alignment == "justify" else table.alignment
            _sub(tbl_pr, "jc", {"w:val": value})
        if table.borders:
            tbl_pr.append(_borders_xml())

        layout = _resolve_table_layout(table)
        grid = _sub(tbl, "tblGrid")
        for width in _grid_col_widths(table, layout):
            _sub(grid, "gridCol", {"w:w": str(width)})

        for layout_row in layout.rows:
            tr = _sub(tbl, "tr")
            if layout_row.height is not None:
                tr_pr = _sub(tr, "trPr")
                _sub(tr_pr, "trHeight", {"w:val": to_twips(layout_row.height, default_unit="pt")})
            for entry in layout_row.entries:
                tr.append(self._cell_xml(entry, source_part))
        return tbl

    def _cell_xml(self, cell: _LayoutCell, source_part: str) -> etree._Element:
        tc = _w("tc")
        tc_pr = _sub(tc, "tcPr")
        width = to_twips(cell.source.width, default_unit="in")
        if width is not None:
            _sub(tc_pr, "tcW", {"w:w": width, "w:type": "dxa"})
        if cell.col_span > 1:
            _sub(tc_pr, "gridSpan", {"w:val": cell.col_span})
        if cell.v_merge is not None:
            attrs = None if cell.v_merge == "continue" else {"w:val": "restart"}
            _sub(tc_pr, "vMerge", attrs)
        if cell.source.shading:
            _sub(tc_pr, "shd", {"w:fill": _color(cell.source.shading)})
        if cell.source.vertical_align:
            _sub(tc_pr, "vAlign", {"w:val": cell.source.vertical_align})

        blocks = cell.blocks or [Paragraph()]
        for block in blocks:
            tc.append(self._block_xml(block, source_part))
        return tc

    def _section_xml(
        self,
        section: Section,
        header_rel_ids: dict[str, str],
        footer_rel_ids: dict[str, str],
        *,
        include_type: bool,
    ) -> etree._Element:
        sect_pr = _w("sectPr")
        for ref_type, rel_id in header_rel_ids.items():
            _sub(sect_pr, "headerReference", {"w:type": ref_type, "r:id": rel_id})
        for ref_type, rel_id in footer_rel_ids.items():
            _sub(sect_pr, "footerReference", {"w:type": ref_type, "r:id": rel_id})
        if include_type:
            _sub(sect_pr, "type", {"w:val": section.start})
        if section.first_page_different:
            _sub(sect_pr, "titlePg")
        width = ensure_length(section.page_width, default_unit="in")
        height = ensure_length(section.page_height, default_unit="in")
        assert width is not None and height is not None
        attrs: dict[str, Any] = {"w:w": width.twips(), "w:h": height.twips()}
        if section.orientation == "landscape":
            attrs["w:orient"] = "landscape"
        _sub(sect_pr, "pgSz", attrs)
        margins = section.margins
        _sub(
            sect_pr,
            "pgMar",
            {
                "w:top": to_twips(margins.top, default_unit="in"),
                "w:right": to_twips(margins.right, default_unit="in"),
                "w:bottom": to_twips(margins.bottom, default_unit="in"),
                "w:left": to_twips(margins.left, default_unit="in"),
                "w:header": to_twips(margins.header, default_unit="in"),
                "w:footer": to_twips(margins.footer, default_unit="in"),
                "w:gutter": to_twips(margins.gutter, default_unit="in"),
            },
        )
        if section.page_numbering.start is not None or section.page_numbering.format is not None:
            attrs: dict[str, Any] = {}
            if section.page_numbering.start is not None:
                attrs["w:start"] = section.page_numbering.start
            if section.page_numbering.format is not None:
                attrs["w:fmt"] = section.page_numbering.format
            _sub(sect_pr, "pgNumType", attrs)
        sect_pr.append(_columns_xml(section))
        _sub(sect_pr, "docGrid", {"w:linePitch": "360"})
        return sect_pr

    def _styles_xml(self) -> bytes:
        root = _w("styles", nsmap={"w": NS["w"]})
        root.append(_doc_defaults_xml())
        for style in _builtin_styles():
            root.append(self._style_xml(style))
        root.append(_table_normal_style_xml())
        root.append(_table_grid_style_xml())
        for style_id in sorted(self.document.styles):
            root.append(self._style_xml(self.document.styles[style_id]))
        return _xml(root)

    def _style_xml(self, style: ParagraphStyle) -> etree._Element:
        style_type = "character" if style.style_id == "FootnoteReference" else "paragraph"
        node = _w("style", {"w:type": style_type, "w:styleId": style.style_id})
        if style.style_id == "Normal":
            node.set(_q("w", "default"), "1")
        _sub(node, "name", {"w:val": style.name or style.style_id})
        if style.based_on and style.style_id != "Normal":
            _sub(node, "basedOn", {"w:val": style.based_on})
        if style.next_style:
            _sub(node, "next", {"w:val": style.next_style})
        _sub(node, "qFormat")

        if style_type == "paragraph":
            p_pr = _w("pPr")
            fmt = ParagraphFormat(
                alignment=style.alignment,
                space_before=style.space_before,
                space_after=style.space_after,
            )
            self._append_paragraph_format(p_pr, fmt)
            if len(p_pr):
                node.append(p_pr)

        r_pr = self._run_properties_xml(
            RunStyle(
                font=style.font,
                east_asia_font=style.east_asia_font,
                size=style.size,
                bold=style.bold,
                italic=style.italic,
                color=style.color,
            )
        )
        if r_pr is not None:
            if style.style_id == "FootnoteReference":
                _sub(r_pr, "vertAlign", {"w:val": "superscript"})
            node.append(r_pr)
        return node

    def _footnotes_xml(self) -> bytes:
        root = _w("footnotes", nsmap={"w": NS["w"]})
        root.append(self._special_footnote_xml(-1, "separator"))
        root.append(self._special_footnote_xml(0, "continuationSeparator"))
        for footnote in self.document.footnotes:
            root.append(self._footnote_xml(footnote))
        return _xml(root)

    def _special_footnote_xml(self, footnote_id: int, footnote_type: str) -> etree._Element:
        node = _w("footnote", {"w:id": footnote_id, "w:type": footnote_type})
        paragraph = _sub(node, "p")
        run = _sub(paragraph, "r")
        separator_tag = "separator" if footnote_type == "separator" else "continuationSeparator"
        _sub(run, separator_tag)
        return node

    def _footnote_xml(self, footnote: Footnote) -> etree._Element:
        node = _w("footnote", {"w:id": footnote.footnote_id})
        blocks = footnote.blocks or [Paragraph()]
        for index, block in enumerate(blocks):
            block_xml = self._block_xml(block, "word/footnotes.xml")
            if index == 0 and isinstance(block, Paragraph):
                p_pr = block_xml.find(_q("w", "pPr"))
                if p_pr is None:
                    p_pr = _w("pPr")
                    block_xml.insert(0, p_pr)
                if block.style is None:
                    _sub(p_pr, "pStyle", {"w:val": "FootnoteText"})
                ref_run = _w("r")
                ref_rpr = _sub(ref_run, "rPr")
                _sub(ref_rpr, "rStyle", {"w:val": "FootnoteReference"})
                _sub(ref_run, "footnoteRef")
                ref_text = _w("r")
                self._append_text(ref_text, " ")
                insert_at = 1 if p_pr is not None else 0
                block_xml.insert(insert_at, ref_run)
                block_xml.insert(insert_at + 1, ref_text)
            node.append(block_xml)
        return node

    def _settings_xml(self) -> bytes:
        root = _w("settings", nsmap={"w": NS["w"]})
        _sub(root, "zoom", {"w:percent": "100"})
        _sub(root, "defaultTabStop", {"w:val": "720"})
        if self._use_even_odd_headers:
            _sub(root, "evenAndOddHeaders")
        compat = _sub(root, "compat")
        _sub(
            compat,
            "compatSetting",
            {
                "w:name": "compatibilityMode",
                "w:uri": "http://schemas.microsoft.com/office/word",
                "w:val": "15",
            },
        )
        return _xml(root)

    def _numbering_xml(self) -> bytes:
        root = _w("numbering", nsmap={"w": NS["w"]})
        root.append(_abstract_numbering_xml("1", "bullet"))
        root.append(_abstract_numbering_xml("2", "decimal"))
        num_bullet = _sub(root, "num", {"w:numId": "1"})
        _sub(num_bullet, "abstractNumId", {"w:val": "1"})
        num_decimal = _sub(root, "num", {"w:numId": "2"})
        _sub(num_decimal, "abstractNumId", {"w:val": "2"})
        return _xml(root)

    def _core_props_xml(self) -> bytes:
        props: DocumentProperties = self.document.properties
        root = etree.Element(
            _q("cp", "coreProperties"),
            nsmap={key: NS[key] for key in ("cp", "dc", "dcterms", "xsi")},
        )
        _text(root, "dc", "title", props.title)
        _text(root, "dc", "subject", props.subject)
        _text(root, "dc", "creator", props.creator)
        _text(root, "cp", "keywords", props.keywords)
        _text(root, "dc", "description", props.description)
        _text(root, "cp", "lastModifiedBy", props.creator)
        created = etree.SubElement(root, _q("dcterms", "created"))
        created.set(_q("xsi", "type"), "dcterms:W3CDTF")
        created.text = props.created.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        modified = etree.SubElement(root, _q("dcterms", "modified"))
        modified.set(_q("xsi", "type"), "dcterms:W3CDTF")
        modified.text = props.modified.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return _xml(root)

    def _app_props_xml(self) -> bytes:
        root = etree.Element(_q("ep", "Properties"), nsmap={"ep": NS["ep"], "vt": NS["vt"]})
        _text(root, "ep", "Application", "memect.docx")
        _text(root, "ep", "DocSecurity", "0")
        _text(root, "ep", "ScaleCrop", "false")
        _text(root, "ep", "Company", "")
        _text(root, "ep", "LinksUpToDate", "false")
        _text(root, "ep", "SharedDoc", "false")
        _text(root, "ep", "HyperlinksChanged", "false")
        _text(root, "ep", "AppVersion", "16.0000")
        return _xml(root)


def _doc_defaults_xml() -> etree._Element:
    doc_defaults = _w("docDefaults")
    r_pr_default = _sub(doc_defaults, "rPrDefault")
    r_pr = _sub(r_pr_default, "rPr")
    _sub(
        r_pr,
        "rFonts",
        {
            "w:ascii": "Times New Roman",
            "w:hAnsi": "Times New Roman",
            "w:eastAsia": "SimSun",
        },
    )
    _sub(r_pr, "sz", {"w:val": "22"})
    _sub(r_pr, "szCs", {"w:val": "22"})
    p_pr_default = _sub(doc_defaults, "pPrDefault")
    p_pr = _sub(p_pr_default, "pPr")
    _sub(p_pr, "spacing", {"w:after": "160", "w:line": "259", "w:lineRule": "auto"})
    return doc_defaults


def _builtin_styles() -> list[ParagraphStyle]:
    return [
        ParagraphStyle("Normal", "Normal"),
        ParagraphStyle("Title", "Title", size=26, bold=True, alignment="center", space_after=12),
        ParagraphStyle("Subtitle", "Subtitle", size=15, color="666666", alignment="center", space_after=12),
        ParagraphStyle("Heading1", "heading 1", size=20, bold=True, space_before=12, space_after=6),
        ParagraphStyle("Heading2", "heading 2", size=16, bold=True, space_before=10, space_after=4),
        ParagraphStyle("Heading3", "heading 3", size=14, bold=True, space_before=8, space_after=4),
        ParagraphStyle("Quote", "Quote", italic=True, color="666666", space_before=6, space_after=6),
        ParagraphStyle("Code", "Code", font="Consolas", east_asia_font="Consolas", size=10, space_after=4),
        ParagraphStyle("FootnoteText", "footnote text", size=10, space_after=0),
        ParagraphStyle("FootnoteReference", "footnote reference", size=10),
        ParagraphStyle("ListParagraph", "List Paragraph"),
    ]


def _table_normal_style_xml() -> etree._Element:
    style = _w("style", {"w:type": "table", "w:default": "1", "w:styleId": "TableNormal"})
    _sub(style, "name", {"w:val": "Normal Table"})
    _sub(style, "uiPriority", {"w:val": "99"})
    _sub(style, "semiHidden")
    _sub(style, "unhideWhenUsed")
    tbl_pr = _sub(style, "tblPr")
    _sub(tbl_pr, "tblInd", {"w:w": "0", "w:type": "dxa"})
    _sub(tbl_pr, "tblCellMar")
    return style


def _table_grid_style_xml() -> etree._Element:
    style = _w("style", {"w:type": "table", "w:styleId": "TableGrid"})
    _sub(style, "name", {"w:val": "Table Grid"})
    _sub(style, "basedOn", {"w:val": "TableNormal"})
    _sub(style, "uiPriority", {"w:val": "59"})
    _sub(style, "qFormat")
    tbl_pr = _sub(style, "tblPr")
    tbl_pr.append(_borders_xml())
    return style


def _borders_xml() -> etree._Element:
    borders = _w("tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        _sub(borders, edge, {"w:val": "single", "w:sz": "4", "w:space": "0", "w:color": "auto"})
    return borders


def _abstract_numbering_xml(abstract_id: str, kind: str) -> etree._Element:
    abstract = _w("abstractNum", {"w:abstractNumId": abstract_id})
    _sub(abstract, "multiLevelType", {"w:val": "hybridMultilevel"})
    for level in range(9):
        lvl = _sub(abstract, "lvl", {"w:ilvl": str(level)})
        _sub(lvl, "start", {"w:val": "1"})
        if kind == "bullet":
            _sub(lvl, "numFmt", {"w:val": "bullet"})
            _sub(lvl, "lvlText", {"w:val": "\u2022"})
        else:
            _sub(lvl, "numFmt", {"w:val": "decimal"})
            _sub(lvl, "lvlText", {"w:val": f"%{level + 1}."})
        _sub(lvl, "lvlJc", {"w:val": "left"})
        p_pr = _sub(lvl, "pPr")
        _sub(p_pr, "ind", {"w:left": str(720 * (level + 1)), "w:hanging": "360"})
    return abstract


def _text(parent: etree._Element, prefix: str, tag: str, value: str) -> etree._Element:
    node = etree.SubElement(parent, _q(prefix, tag))
    node.text = _clean_text(str(value))
    return node


def _clean_text(value: str) -> str:
    return _INVALID_XML_10.sub("", value)


def _color(value: str) -> str:
    color = value.strip().lstrip("#").upper()
    if len(color) != 6 or any(char not in "0123456789ABCDEF" for char in color):
        raise ValueError(f"Invalid RGB color: {value!r}")
    return color


def _columns_xml(section: Section) -> etree._Element:
    cols = _w("cols")
    if section.columns <= 1:
        cols.set(_q("w", "space"), str(to_twips(section.column_space, default_unit="pt") or 720))
        return cols
    if section.column_widths is None:
        cols.set(_q("w", "num"), str(section.columns))
        cols.set(_q("w", "space"), str(to_twips(section.column_space, default_unit="pt") or 720))
        if not section.equal_width:
            cols.set(_q("w", "equalWidth"), "0")
        return cols
    cols.set(_q("w", "num"), str(section.columns))
    cols.set(_q("w", "equalWidth"), "0")
    for index, width in enumerate(section.column_widths):
        attrs = {_q("w", "w"): str(to_twips(width, default_unit="pt"))}
        if index < len(section.column_widths) - 1:
            attrs[_q("w", "space")] = str(to_twips(section.column_space, default_unit="pt") or 720)
        etree.SubElement(cols, _q("w", "col"), attrs)
    return cols


@dataclass
class _LayoutCell:
    source: TableCell
    col_span: int = 1
    v_merge: str | None = None
    blocks: list[Any] = field(default_factory=list)


@dataclass
class _LayoutRow:
    entries: list[_LayoutCell]
    height: Length | int | float | None = None


@dataclass
class _TableLayout:
    rows: list[_LayoutRow]
    col_count: int


def _grid_col_widths(table: Table, layout: _TableLayout) -> list[int]:
    if table.cells:
        return _grid_col_widths_from_axis(table, layout.col_count)
    return _grid_col_widths_from_layout(table, layout)


def _grid_col_widths_from_axis(table: Table, col_count: int) -> list[int]:
    if col_count <= 0:
        return []

    axis: list[int | None] = [None] * (col_count + 1)
    axis[0] = 0

    for cell in table.cells:
        if cell.col_index is None:
            continue
        width = to_twips(cell.width, default_unit="in")
        if width is None:
            continue
        boundary = cell.col_index + cell.col_span
        if boundary < 0 or boundary > col_count:
            continue
        if axis[boundary] is not None:
            continue
        left = axis[cell.col_index]
        if left is None:
            raise ValidationError("table.cells must be ordered left-to-right with continuous column boundaries")
        axis[boundary] = left + width

    total_width = to_twips(table.width, default_unit="in")
    if axis[col_count] is None:
        last_boundary = next((value for value in reversed(axis[:-1]) if value is not None), 0)
        axis[col_count] = total_width if total_width is not None else last_boundary

    last_known = 0
    for index in range(1, col_count + 1):
        if axis[index] is None:
            continue
        left = axis[last_known] or 0
        right = axis[index] or left
        if right < left:
            right = left
        span = index - last_known
        base, remainder = divmod(right - left, span)
        position = left
        for offset in range(1, span + 1):
            position += base + (1 if offset <= remainder else 0)
            axis[last_known + offset] = position
        last_known = index

    return [max((axis[index + 1] or 0) - (axis[index] or 0), 0) for index in range(col_count)]


def _grid_col_widths_from_layout(table: Table, layout: _TableLayout) -> list[int]:
    widths: list[int | None] = [None] * layout.col_count

    for layout_row in layout.rows:
        col_index = 0
        for entry in layout_row.entries:
            width = to_twips(entry.source.width, default_unit="in")
            if width is not None and entry.col_span > 0:
                base, remainder = divmod(width, entry.col_span)
                for offset in range(entry.col_span):
                    value = base + (1 if offset < remainder else 0)
                    index = col_index + offset
                    current = widths[index]
                    widths[index] = value if current is None else max(current, value)
            col_index += entry.col_span

    unresolved = [index for index, width in enumerate(widths) if width is None]
    total_width = to_twips(table.width, default_unit="in")
    if unresolved and total_width is not None and layout.col_count > 0:
        known_width = sum(width or 0 for width in widths)
        remaining = max(total_width - known_width, 0)
        base, remainder = divmod(remaining, len(unresolved))
        for offset, index in enumerate(unresolved):
            widths[index] = base + (1 if offset < remainder else 0)

    return [width or 0 for width in widths]


def _resolve_table_layout(table: Table) -> _TableLayout:
    if table.cells:
        return _resolve_explicit_cells_layout(table)
    return _resolve_matrix_layout(table)


def _resolve_matrix_layout(table: Table) -> _TableLayout:
    rows: list[_LayoutRow] = []
    col_count = 0
    merge_open_until: dict[int, int] = {}

    for row_index, row in enumerate(table.rows):
        entries: list[_LayoutCell] = []
        col_index = 0
        for cell in row.cells:
            if cell.col_span < 1 or cell.row_span < 1:
                raise ValidationError("Table cell spans must be positive integers")
            while merge_open_until.get(col_index, -1) >= row_index:
                entries.append(
                    _LayoutCell(
                        source=TableCell(),
                        v_merge="continue",
                        blocks=[Paragraph()],
                    )
                )
                col_index += 1
            entries.append(
                _LayoutCell(
                    source=cell,
                    col_span=cell.col_span,
                    v_merge="restart" if cell.row_span > 1 else None,
                    blocks=cell.blocks,
                )
            )
            if cell.row_span > 1:
                for offset in range(cell.col_span):
                    merge_open_until[col_index + offset] = row_index + cell.row_span - 1
            col_index += cell.col_span
        while merge_open_until.get(col_index, -1) >= row_index:
            entries.append(
                _LayoutCell(
                    source=TableCell(),
                    v_merge="continue",
                    blocks=[Paragraph()],
                )
            )
            col_index += 1
        col_count = max(col_count, col_index)
        rows.append(_LayoutRow(entries, height=row.height))
    return _TableLayout(rows, col_count)


def _resolve_explicit_cells_layout(table: Table) -> _TableLayout:
    row_count = table.row_count or len(table.rows)
    if row_count == 0:
        raise ValidationError("rows are required when explicit cells are used")
    col_count = table.col_count or max((len(row.cells) for row in table.rows), default=0)
    if col_count == 0:
        raise ValidationError("cols are required when explicit cells are used")

    anchors: dict[tuple[int, int], TableCell] = {}
    occupied: dict[tuple[int, int], TableCell] = {}

    for cell in table.cells:
        if cell.row_span < 1 or cell.col_span < 1:
            raise ValidationError("Table cell spans must be positive integers")
        if cell.row_index is None or cell.col_index is None:
            raise ValidationError("Explicit table cells require row_index and col_index")
        if cell.row_index < 0 or cell.col_index < 0:
            raise ValidationError("Table cell indexes must be non-negative")
        if cell.row_index + cell.row_span > row_count or cell.col_index + cell.col_span > col_count:
            raise ValidationError("Table cell span exceeds the declared table size")
        key = (cell.row_index, cell.col_index)
        if key in anchors:
            raise ValidationError(f"Duplicate table cell anchor at {key}")
        anchors[key] = cell
        for row_index in range(cell.row_index, cell.row_index + cell.row_span):
            for col_index in range(cell.col_index, cell.col_index + cell.col_span):
                occupied_key = (row_index, col_index)
                if occupied_key in occupied:
                    raise ValidationError(f"Overlapping table cell at {occupied_key}")
                occupied[occupied_key] = cell

    rows: list[_LayoutRow] = []
    for row_index, row in enumerate(table.rows):
        entries: list[_LayoutCell] = []
        col_index = 0
        while col_index < col_count:
            key = (row_index, col_index)
            anchor = anchors.get(key)
            if anchor is not None:
                entries.append(
                    _LayoutCell(
                        source=anchor,
                        col_span=anchor.col_span,
                        v_merge="restart" if anchor.row_span > 1 else None,
                        blocks=anchor.blocks,
                    )
                )
                col_index += anchor.col_span
                continue

            owner = occupied.get(key)
            if owner is not None:
                entries.append(
                    _LayoutCell(
                        source=owner,
                        col_span=owner.col_span,
                        v_merge="continue",
                        blocks=[Paragraph()],
                    )
                )
                col_index += owner.col_span
                continue

            matrix_cell = row.cells[col_index] if col_index < len(row.cells) else TableCell()
            entries.append(_LayoutCell(source=matrix_cell, blocks=matrix_cell.blocks))
            col_index += 1
        rows.append(_LayoutRow(entries, height=row.height))
    return _TableLayout(rows, col_count)
