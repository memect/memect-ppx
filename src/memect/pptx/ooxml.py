"""PresentationML serialization."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from lxml import etree

from .model import (
    Deck,
    Line,
    Paragraph,
    Picture,
    Shape,
    Slide,
    Table,
    TableCell,
    TextBox,
    TextStyle,
    normalize_color,
    rect_to_emu,
    resolve_picture_size,
)
from .opc import Package
from .units import to_emu, to_points

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

REL = {
    "app": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties",
    "core": "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
    "image": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
    "office": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
    "slide": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide",
    "slideLayout": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout",
    "slideMaster": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster",
    "theme": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme",
}

CONTENT_TYPES = {
    "app": "application/vnd.openxmlformats-officedocument.extended-properties+xml",
    "core": "application/vnd.openxmlformats-package.core-properties+xml",
    "presentation": "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
    "slide": "application/vnd.openxmlformats-officedocument.presentationml.slide+xml",
    "slideLayout": "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml",
    "slideMaster": "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml",
    "theme": "application/vnd.openxmlformats-officedocument.theme+xml",
}

TABLE_STYLE_ID = "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"


def build_package(deck: Deck) -> bytes:
    serializer = _Serializer(deck)
    return serializer.build()


def _q(prefix: str, tag: str) -> str:
    return f"{{{NS[prefix]}}}{tag}"


def _e(prefix: str, tag: str, attrs: dict[str, Any] | None = None, *, nsmap: dict[str, str] | None = None):
    element = etree.Element(_q(prefix, tag), nsmap=nsmap)
    if attrs:
        _set_attrs(element, attrs)
    return element


def _sub(parent, prefix: str, tag: str, attrs: dict[str, Any] | None = None):
    child = etree.SubElement(parent, _q(prefix, tag))
    if attrs:
        _set_attrs(child, attrs)
    return child


def _set_attrs(element, attrs: dict[str, Any]) -> None:
    for key, value in attrs.items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = "1" if value else "0"
        if ":" in key:
            prefix, local = key.split(":", 1)
            element.set(_q(prefix, local), str(value))
        else:
            element.set(key, str(value))


def _xml(root) -> bytes:
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)


def _text(parent, prefix: str, tag: str, value: str) -> None:
    child = _sub(parent, prefix, tag)
    child.text = value


class _Serializer:
    def __init__(self, deck: Deck) -> None:
        self.deck = deck
        self.package = Package()
        self._media_names: set[str] = set()

    def build(self) -> bytes:
        if not self.deck.slides:
            self.deck.add_slide()

        self.package.add_default("png", "image/png")
        self.package.add_default("jpg", "image/jpeg")
        self.package.add_default("jpeg", "image/jpeg")
        self.package.add_default("gif", "image/gif")

        root_rels = self.package.relationships()
        root_rels.add(REL["office"], "ppt/presentation.xml")
        root_rels.add(REL["core"], "docProps/core.xml")
        root_rels.add(REL["app"], "docProps/app.xml")

        presentation_rels = self.package.relationships("ppt/presentation.xml")
        presentation_rels.add(REL["slideMaster"], "slideMasters/slideMaster1.xml")
        slide_rel_ids: list[str] = []
        for index, _slide in enumerate(self.deck.slides, start=1):
            slide_rel_ids.append(presentation_rels.add(REL["slide"], f"slides/slide{index}.xml"))

        master_rels = self.package.relationships("ppt/slideMasters/slideMaster1.xml")
        master_rels.add(REL["theme"], "../theme/theme1.xml")
        master_rels.add(REL["slideLayout"], "../slideLayouts/slideLayout1.xml")

        layout_rels = self.package.relationships("ppt/slideLayouts/slideLayout1.xml")
        layout_rels.add(REL["slideMaster"], "../slideMasters/slideMaster1.xml")

        self.package.add_part(
            "ppt/presentation.xml",
            CONTENT_TYPES["presentation"],
            self._presentation_xml(slide_rel_ids),
        )
        self.package.add_part(
            "ppt/slideMasters/slideMaster1.xml",
            CONTENT_TYPES["slideMaster"],
            self._slide_master_xml(),
        )
        self.package.add_part(
            "ppt/slideLayouts/slideLayout1.xml",
            CONTENT_TYPES["slideLayout"],
            self._slide_layout_xml(),
        )
        self.package.add_part("ppt/theme/theme1.xml", CONTENT_TYPES["theme"], self._theme_xml())
        self.package.add_part("docProps/core.xml", CONTENT_TYPES["core"], self._core_props_xml())
        self.package.add_part("docProps/app.xml", CONTENT_TYPES["app"], self._app_props_xml())

        for index, slide in enumerate(self.deck.slides, start=1):
            self._add_slide_part(index, slide)

        return self.package.to_bytes()

    def _add_slide_part(self, index: int, slide: Slide) -> None:
        slide_part = f"ppt/slides/slide{index}.xml"
        slide_rels = self.package.relationships(slide_part)
        slide_rels.add(REL["slideLayout"], "../slideLayouts/slideLayout1.xml")
        image_rel_ids: dict[int, str] = {}
        for element in slide.elements:
            if isinstance(element, Picture):
                media_name = self._unique_media_name(element.image.name)
                image_rel_ids[id(element)] = slide_rels.add(REL["image"], f"../media/{media_name}")
                if media_name not in self._media_names:
                    self._media_names.add(media_name)
                    self.package.add_part(f"ppt/media/{media_name}", element.image.content_type, element.image.data)
        self.package.add_part(slide_part, CONTENT_TYPES["slide"], self._slide_xml(slide, image_rel_ids))

    def _unique_media_name(self, name: str) -> str:
        if name not in self._media_names:
            return name
        if "." in name:
            stem, ext = name.rsplit(".", 1)
            suffix = f".{ext}"
        else:
            stem, suffix = name, ""
        index = 2
        candidate = f"{stem}_{index}{suffix}"
        while candidate in self._media_names:
            index += 1
            candidate = f"{stem}_{index}{suffix}"
        return candidate

    def _presentation_xml(self, slide_rel_ids: list[str]) -> bytes:
        root = _e("p", "presentation", nsmap={key: NS[key] for key in ("a", "r", "p")})
        master_ids = _sub(root, "p", "sldMasterIdLst")
        _sub(master_ids, "p", "sldMasterId", {"id": 2147483648, "r:id": "rId1"})
        slide_ids = _sub(root, "p", "sldIdLst")
        for index, rel_id in enumerate(slide_rel_ids, start=1):
            _sub(slide_ids, "p", "sldId", {"id": 255 + index, "r:id": rel_id})
        _sub(root, "p", "sldSz", {"cx": self.deck.width_emu, "cy": self.deck.height_emu, "type": "custom"})
        _sub(root, "p", "notesSz", {"cx": 6858000, "cy": 9144000})
        root.append(self._default_text_style_xml())
        return _xml(root)

    def _slide_xml(self, slide: Slide, image_rel_ids: dict[int, str]) -> bytes:
        root = _e("p", "sld", nsmap={key: NS[key] for key in ("a", "r", "p")})
        common = _sub(root, "p", "cSld")
        if slide.background:
            bg = _sub(common, "p", "bg")
            bg_pr = _sub(bg, "p", "bgPr")
            self._solid_fill(bg_pr, slide.background)
            _sub(bg_pr, "a", "effectLst")
        sp_tree = _sub(common, "p", "spTree")
        self._append_group_shape(sp_tree)
        shape_id = 2
        for element in slide.elements:
            if isinstance(element, TextBox):
                sp_tree.append(self._textbox_xml(element, shape_id))
            elif isinstance(element, Shape):
                sp_tree.append(self._shape_xml(element, shape_id))
            elif isinstance(element, Line):
                sp_tree.append(self._line_xml(element, shape_id))
            elif isinstance(element, Picture):
                sp_tree.append(self._picture_xml(element, shape_id, image_rel_ids[id(element)]))
            elif isinstance(element, Table):
                sp_tree.append(self._table_xml(element, shape_id))
            shape_id += 1
        clr_map = _sub(root, "p", "clrMapOvr")
        _sub(clr_map, "a", "masterClrMapping")
        return _xml(root)

    def _textbox_xml(self, textbox: TextBox, shape_id: int):
        x, y, cx, cy = rect_to_emu(textbox.left, textbox.top, textbox.width, textbox.height)
        shape = _e("p", "sp")
        nv = _sub(shape, "p", "nvSpPr")
        _sub(nv, "p", "cNvPr", {"id": shape_id, "name": f"TextBox {shape_id - 1}"})
        _sub(nv, "p", "cNvSpPr", {"txBox": True})
        nv_pr = _sub(nv, "p", "nvPr")
        if textbox.placeholder:
            _sub(nv_pr, "p", "ph", {"type": textbox.placeholder})
        sp_pr = _sub(shape, "p", "spPr")
        self._xfrm(sp_pr, x, y, cx, cy)
        self._preset_geometry(sp_pr, "rect")
        self._fill_and_line(sp_pr, textbox.fill, textbox.line, None)
        shape.append(self._text_body_xml(textbox.paragraphs, textbox))
        return shape

    def _shape_xml(self, shape_model: Shape, shape_id: int):
        x, y, cx, cy = rect_to_emu(shape_model.left, shape_model.top, shape_model.width, shape_model.height)
        shape = _e("p", "sp")
        nv = _sub(shape, "p", "nvSpPr")
        _sub(nv, "p", "cNvPr", {"id": shape_id, "name": f"Rectangle {shape_id - 1}"})
        _sub(nv, "p", "cNvSpPr")
        _sub(nv, "p", "nvPr")
        sp_pr = _sub(shape, "p", "spPr")
        self._xfrm(sp_pr, x, y, cx, cy)
        self._preset_geometry(sp_pr, "rect")
        self._fill_and_line(sp_pr, shape_model.fill, shape_model.line, shape_model.line_width)
        return shape

    def _line_xml(self, line_model: Line, shape_id: int):
        x1 = to_emu(line_model.x1, default_unit="pt")
        y1 = to_emu(line_model.y1, default_unit="pt")
        x2 = to_emu(line_model.x2, default_unit="pt")
        y2 = to_emu(line_model.y2, default_unit="pt")
        assert x1 is not None and y1 is not None and x2 is not None and y2 is not None
        x = min(x1, x2)
        y = min(y1, y2)
        cx = abs(x2 - x1)
        cy = abs(y2 - y1)

        shape = _e("p", "sp")
        nv = _sub(shape, "p", "nvSpPr")
        _sub(nv, "p", "cNvPr", {"id": shape_id, "name": f"Line {shape_id - 1}"})
        _sub(nv, "p", "cNvSpPr")
        _sub(nv, "p", "nvPr")
        sp_pr = _sub(shape, "p", "spPr")
        xfrm_attrs = {}
        if x2 < x1:
            xfrm_attrs["flipH"] = True
        if y2 < y1:
            xfrm_attrs["flipV"] = True
        self._xfrm(sp_pr, x, y, max(1, cx), max(1, cy), attrs=xfrm_attrs)
        self._preset_geometry(sp_pr, "line")
        self._line(sp_pr, line_model.color, line_model.width)
        return shape

    def _picture_xml(self, picture: Picture, shape_id: int, rel_id: str):
        x = to_emu(picture.left, default_unit="pt")
        y = to_emu(picture.top, default_unit="pt")
        assert x is not None and y is not None
        cx, cy = resolve_picture_size(picture)
        pic = _e("p", "pic")
        nv = _sub(pic, "p", "nvPicPr")
        _sub(
            nv,
            "p",
            "cNvPr",
            {"id": shape_id, "name": picture.image.name, "descr": picture.alt_text},
        )
        c_nv_pic = _sub(nv, "p", "cNvPicPr")
        _sub(c_nv_pic, "a", "picLocks", {"noChangeAspect": True})
        _sub(nv, "p", "nvPr")
        blip_fill = _sub(pic, "p", "blipFill")
        _sub(blip_fill, "a", "blip", {"r:embed": rel_id})
        stretch = _sub(blip_fill, "a", "stretch")
        _sub(stretch, "a", "fillRect")
        sp_pr = _sub(pic, "p", "spPr")
        self._xfrm(sp_pr, x, y, cx, cy)
        self._preset_geometry(sp_pr, "rect")
        return pic

    def _table_xml(self, table: Table, shape_id: int):
        x, y, cx, cy = rect_to_emu(table.left, table.top, table.width, table.height)
        frame = _e("p", "graphicFrame")
        nv = _sub(frame, "p", "nvGraphicFramePr")
        _sub(nv, "p", "cNvPr", {"id": shape_id, "name": f"Table {shape_id - 1}"})
        c_nv = _sub(nv, "p", "cNvGraphicFramePr")
        _sub(c_nv, "a", "graphicFrameLocks", {"noGrp": True})
        _sub(nv, "p", "nvPr")
        xfrm = _sub(frame, "p", "xfrm")
        _sub(xfrm, "a", "off", {"x": x, "y": y})
        _sub(xfrm, "a", "ext", {"cx": cx, "cy": cy})
        graphic = _sub(frame, "a", "graphic")
        graphic_data = _sub(
            graphic,
            "a",
            "graphicData",
            {"uri": "http://schemas.openxmlformats.org/drawingml/2006/table"},
        )
        tbl = _sub(graphic_data, "a", "tbl")
        tbl_pr = _sub(tbl, "a", "tblPr", {"firstRow": False, "bandRow": False})
        _text(tbl_pr, "a", "tableStyleId", TABLE_STYLE_ID)
        grid = _sub(tbl, "a", "tblGrid")
        for width in self._table_column_widths(table, cx):
            _sub(grid, "a", "gridCol", {"w": width})
        row_heights = self._table_row_heights(table, cy)
        for row_index in range(table.rows):
            row = _sub(tbl, "a", "tr", {"h": row_heights[row_index]})
            for col_index in range(table.cols):
                cell = table.cell(row_index, col_index)
                attrs: dict[str, Any] = {}
                if table.is_origin(row_index, col_index):
                    if cell.col_span > 1:
                        attrs["gridSpan"] = cell.col_span
                    if cell.row_span > 1:
                        attrs["rowSpan"] = cell.row_span
                else:
                    origin_row, origin_col = table.origin(row_index, col_index)
                    if col_index > origin_col:
                        attrs["hMerge"] = True
                    if row_index > origin_row:
                        attrs["vMerge"] = True
                tc = _sub(row, "a", "tc", attrs)
                if table.is_origin(row_index, col_index):
                    tc.append(self._table_cell_text_body(cell))
                else:
                    tc.append(self._table_cell_text_body(TableCell(row_index=row_index, col_index=col_index)))
                tc_pr = _sub(tc, "a", "tcPr", {"anchor": _vertical_anchor(cell.vertical_align)})
                if cell.fill:
                    self._solid_fill(tc_pr, cell.fill)
                self._table_cell_borders(tc_pr, table)
        return frame

    def _text_body_xml(self, paragraphs: list[Paragraph], textbox: TextBox | None = None):
        body = _e("p", "txBody") if textbox is not None else _e("a", "txBody")
        body_pr_attrs: dict[str, Any] = {
            "wrap": "square",
            "anchor": _vertical_anchor(textbox.vertical_align if textbox else "middle"),
        }
        if textbox is not None:
            body_pr_attrs.update(
                {
                    "lIns": to_emu(textbox.margin_left, default_unit="pt"),
                    "rIns": to_emu(textbox.margin_right, default_unit="pt"),
                    "tIns": to_emu(textbox.margin_top, default_unit="pt"),
                    "bIns": to_emu(textbox.margin_bottom, default_unit="pt"),
                }
            )
        body_pr = _sub(body, "a", "bodyPr", body_pr_attrs)
        _sub(body_pr, "a", "spAutoFit" if textbox is not None and textbox.auto_fit else "noAutofit")
        _sub(body, "a", "lstStyle")
        used = paragraphs or [Paragraph()]
        for paragraph in used:
            body.append(self._paragraph_xml(paragraph))
        return body

    def _table_cell_text_body(self, cell: TableCell):
        body = _e("a", "txBody")
        body_pr = _sub(body, "a", "bodyPr")
        _sub(body_pr, "a", "noAutofit")
        _sub(body, "a", "lstStyle")
        paragraphs = cell.paragraphs
        if cell.text and not paragraphs:
            paragraph = Paragraph()
            paragraph.add_run(cell.text)
            paragraphs = [paragraph]
        for paragraph in paragraphs or [Paragraph()]:
            body.append(self._paragraph_xml(paragraph))
        return body

    def _paragraph_xml(self, paragraph: Paragraph):
        p = _e("a", "p")
        if paragraph.alignment or paragraph.line_spacing or paragraph.space_before or paragraph.space_after:
            p_pr = _sub(p, "a", "pPr", {"algn": _alignment(paragraph.alignment)})
            self._spacing(p_pr, "lnSpc", paragraph.line_spacing)
            self._spacing(p_pr, "spcBef", paragraph.space_before)
            self._spacing(p_pr, "spcAft", paragraph.space_after)
        for run in paragraph.runs:
            r = _sub(p, "a", "r")
            r_pr = _sub(r, "a", "rPr")
            self._run_properties(r_pr, run.style)
            t = _sub(r, "a", "t")
            t.text = run.text
        end = _sub(p, "a", "endParaRPr")
        self._run_properties(end, TextStyle())
        return p

    def _run_properties(self, element, style: TextStyle) -> None:
        font = style.font or self.deck.defaults.font
        east_asia_font = style.east_asia_font or self.deck.defaults.east_asia_font or font
        size = style.size if style.size is not None else self.deck.defaults.size
        color = style.color if style.color is not None else self.deck.defaults.color
        size_points = to_points(size, default_unit="pt")
        attrs = {"lang": "en-US", "sz": int(round((size_points or 18) * 100))}
        if style.bold is not None:
            attrs["b"] = style.bold
        if style.italic is not None:
            attrs["i"] = style.italic
        if style.underline:
            attrs["u"] = "sng"
        if style.strike:
            attrs["strike"] = "sng"
        _set_attrs(element, attrs)
        if color:
            self._solid_fill(element, color)
        _sub(element, "a", "latin", {"typeface": font})
        _sub(element, "a", "ea", {"typeface": east_asia_font})
        _sub(element, "a", "cs", {"typeface": font})

    def _slide_master_xml(self) -> bytes:
        root = _e("p", "sldMaster", nsmap={key: NS[key] for key in ("a", "r", "p")})
        c_sld = _sub(root, "p", "cSld")
        sp_tree = _sub(c_sld, "p", "spTree")
        self._append_group_shape(sp_tree)
        _sub(
            root,
            "p",
            "clrMap",
            {
                "bg1": "lt1",
                "tx1": "dk1",
                "bg2": "lt2",
                "tx2": "dk2",
                "accent1": "accent1",
                "accent2": "accent2",
                "accent3": "accent3",
                "accent4": "accent4",
                "accent5": "accent5",
                "accent6": "accent6",
                "hlink": "hlink",
                "folHlink": "folHlink",
            },
        )
        layout_ids = _sub(root, "p", "sldLayoutIdLst")
        _sub(layout_ids, "p", "sldLayoutId", {"id": 2147483649, "r:id": "rId2"})
        tx_styles = _sub(root, "p", "txStyles")
        tx_styles.append(self._text_style_list_xml("titleStyle"))
        tx_styles.append(self._text_style_list_xml("bodyStyle"))
        tx_styles.append(self._text_style_list_xml("otherStyle"))
        return _xml(root)

    def _slide_layout_xml(self) -> bytes:
        root = _e(
            "p",
            "sldLayout",
            {"type": "blank", "preserve": True},
            nsmap={key: NS[key] for key in ("a", "r", "p")},
        )
        c_sld = _sub(root, "p", "cSld", {"name": "Blank"})
        sp_tree = _sub(c_sld, "p", "spTree")
        self._append_group_shape(sp_tree)
        clr_map = _sub(root, "p", "clrMapOvr")
        _sub(clr_map, "a", "masterClrMapping")
        return _xml(root)

    def _theme_xml(self) -> bytes:
        root = _e("a", "theme", {"name": "memect"}, nsmap={"a": NS["a"]})
        theme_elements = _sub(root, "a", "themeElements")
        clr_scheme = _sub(theme_elements, "a", "clrScheme", {"name": "memect"})
        self._scheme_color(clr_scheme, "dk1", "000000")
        self._scheme_color(clr_scheme, "lt1", "FFFFFF")
        self._scheme_color(clr_scheme, "dk2", "1F1F1F")
        self._scheme_color(clr_scheme, "lt2", "F2F2F2")
        for name, color in (
            ("accent1", "4472C4"),
            ("accent2", "ED7D31"),
            ("accent3", "A5A5A5"),
            ("accent4", "FFC000"),
            ("accent5", "5B9BD5"),
            ("accent6", "70AD47"),
            ("hlink", "0563C1"),
            ("folHlink", "954F72"),
        ):
            self._scheme_color(clr_scheme, name, color)

        font_scheme = _sub(theme_elements, "a", "fontScheme", {"name": "memect"})
        for tag in ("majorFont", "minorFont"):
            font = _sub(font_scheme, "a", tag)
            _sub(font, "a", "latin", {"typeface": self.deck.defaults.font})
            _sub(font, "a", "ea", {"typeface": self.deck.defaults.east_asia_font})
            _sub(font, "a", "cs", {"typeface": self.deck.defaults.font})
        fmt_scheme = _sub(theme_elements, "a", "fmtScheme", {"name": "memect"})
        fill_style_lst = _sub(fmt_scheme, "a", "fillStyleLst")
        solid = _sub(fill_style_lst, "a", "solidFill")
        _sub(solid, "a", "schemeClr", {"val": "phClr"})
        grad = _sub(fill_style_lst, "a", "gradFill", {"rotWithShape": True})
        gs_lst = _sub(grad, "a", "gsLst")
        for pos in (0, 100000):
            gs = _sub(gs_lst, "a", "gs", {"pos": pos})
            _sub(gs, "a", "schemeClr", {"val": "phClr"})
        _sub(grad, "a", "lin", {"ang": 5400000, "scaled": False})
        _sub(fill_style_lst, "a", "solidFill").append(_e("a", "schemeClr", {"val": "phClr"}))
        ln_style_lst = _sub(fmt_scheme, "a", "lnStyleLst")
        for width in (6350, 12700, 19050):
            ln = _sub(ln_style_lst, "a", "ln", {"w": width, "cap": "flat", "cmpd": "sng", "algn": "ctr"})
            solid = _sub(ln, "a", "solidFill")
            _sub(solid, "a", "schemeClr", {"val": "phClr"})
            _sub(ln, "a", "prstDash", {"val": "solid"})
        effect_style_lst = _sub(fmt_scheme, "a", "effectStyleLst")
        for _ in range(3):
            effect = _sub(effect_style_lst, "a", "effectStyle")
            _sub(effect, "a", "effectLst")
        bg_fill_style_lst = _sub(fmt_scheme, "a", "bgFillStyleLst")
        solid = _sub(bg_fill_style_lst, "a", "solidFill")
        _sub(solid, "a", "schemeClr", {"val": "phClr"})
        solid = _sub(bg_fill_style_lst, "a", "solidFill")
        _sub(solid, "a", "srgbClr", {"val": "FFFFFF"})
        solid = _sub(bg_fill_style_lst, "a", "solidFill")
        _sub(solid, "a", "srgbClr", {"val": "F2F2F2"})
        _sub(root, "a", "objectDefaults")
        _sub(root, "a", "extraClrSchemeLst")
        return _xml(root)

    def _core_props_xml(self) -> bytes:
        props = self.deck.properties
        root = _e(
            "cp",
            "coreProperties",
            nsmap={key: NS[key] for key in ("cp", "dc", "dcterms", "xsi")},
        )
        _text(root, "dc", "title", props.title)
        _text(root, "dc", "creator", props.creator)
        if props.subject:
            _text(root, "dc", "subject", props.subject)
        if props.description:
            _text(root, "dc", "description", props.description)
        if props.keywords:
            _text(root, "cp", "keywords", props.keywords)
        stamp = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        created = _sub(root, "dcterms", "created", {"xsi:type": "dcterms:W3CDTF"})
        created.text = stamp
        modified = _sub(root, "dcterms", "modified", {"xsi:type": "dcterms:W3CDTF"})
        modified.text = stamp
        return _xml(root)

    def _app_props_xml(self) -> bytes:
        root = _e("ep", "Properties", nsmap={None: NS["ep"], "vt": NS["vt"]})
        _text(root, "ep", "Application", "memect.pptx")
        _text(root, "ep", "PresentationFormat", "On-screen Show")
        _text(root, "ep", "Slides", str(len(self.deck.slides)))
        _text(root, "ep", "Notes", "0")
        _text(root, "ep", "HiddenSlides", "0")
        _text(root, "ep", "MMClips", "0")
        _text(root, "ep", "ScaleCrop", "false")
        heading_pairs = _sub(root, "ep", "HeadingPairs")
        vector = _sub(heading_pairs, "vt", "vector", {"size": 2, "baseType": "variant"})
        variant = _sub(vector, "vt", "variant")
        _text(variant, "vt", "lpstr", "Slides")
        variant = _sub(vector, "vt", "variant")
        _text(variant, "vt", "i4", str(len(self.deck.slides)))
        titles = _sub(root, "ep", "TitlesOfParts")
        vector = _sub(titles, "vt", "vector", {"size": len(self.deck.slides), "baseType": "lpstr"})
        for index in range(1, len(self.deck.slides) + 1):
            _text(vector, "vt", "lpstr", f"Slide {index}")
        return _xml(root)

    def _default_text_style_xml(self):
        style = _e("p", "defaultTextStyle")
        style.append(self._text_style_level_xml("defPPr", level=None))
        return style

    def _text_style_list_xml(self, tag: str):
        style = _e("p", tag)
        style.append(self._text_style_level_xml("lvl1pPr", level=0))
        return style

    def _text_style_level_xml(self, tag: str, *, level: int | None):
        attrs = {"algn": "l"}
        if level is not None:
            attrs["marL"] = level * 457200
            attrs["indent"] = 0
        p_pr = _e("a", tag, attrs)
        def_r_pr = _sub(p_pr, "a", "defRPr")
        self._run_properties(def_r_pr, TextStyle())
        return p_pr

    def _append_group_shape(self, parent) -> None:
        nv = _sub(parent, "p", "nvGrpSpPr")
        _sub(nv, "p", "cNvPr", {"id": 1, "name": ""})
        _sub(nv, "p", "cNvGrpSpPr")
        _sub(nv, "p", "nvPr")
        grp_pr = _sub(parent, "p", "grpSpPr")
        xfrm = _sub(grp_pr, "a", "xfrm")
        _sub(xfrm, "a", "off", {"x": 0, "y": 0})
        _sub(xfrm, "a", "ext", {"cx": 0, "cy": 0})
        _sub(xfrm, "a", "chOff", {"x": 0, "y": 0})
        _sub(xfrm, "a", "chExt", {"cx": 0, "cy": 0})

    def _xfrm(self, parent, x: int, y: int, cx: int, cy: int, *, attrs: dict[str, Any] | None = None) -> None:
        xfrm = _sub(parent, "a", "xfrm", attrs)
        _sub(xfrm, "a", "off", {"x": x, "y": y})
        _sub(xfrm, "a", "ext", {"cx": cx, "cy": cy})

    def _preset_geometry(self, parent, preset: str) -> None:
        geom = _sub(parent, "a", "prstGeom", {"prst": preset})
        _sub(geom, "a", "avLst")

    def _fill_and_line(
        self,
        parent,
        fill: str | None,
        line: str | None,
        line_width: Any,
    ) -> None:
        if fill:
            self._solid_fill(parent, fill)
        else:
            _sub(parent, "a", "noFill")
        self._line(parent, line, line_width)

    def _solid_fill(self, parent, color: str) -> None:
        fill = _sub(parent, "a", "solidFill")
        _sub(fill, "a", "srgbClr", {"val": normalize_color(color)})

    def _line(self, parent, color: str | None, width: Any = None, *, tag: str = "ln") -> None:
        attrs: dict[str, Any] = {}
        if width is not None:
            line_width = to_emu(width, default_unit="pt")
            if line_width is not None:
                attrs["w"] = max(1, line_width)
        ln = _sub(parent, "a", tag, attrs)
        if color:
            self._solid_fill(ln, color)
        else:
            _sub(ln, "a", "noFill")

    def _spacing(self, parent, tag: str, value: Any) -> None:
        points = to_points(value, default_unit="pt")
        if points is None:
            return
        node = _sub(parent, "a", tag)
        _sub(node, "a", "spcPts", {"val": int(round(points * 100))})

    def _table_column_widths(self, table: Table, total_width: int) -> list[int]:
        if table.column_widths:
            widths = [to_emu(value, default_unit="pt") for value in table.column_widths]
            if len(widths) != table.cols or any(value is None for value in widths):
                raise ValueError("column_widths length must match table columns")
            return [int(value) for value in widths if value is not None]
        base = total_width // table.cols
        widths = [base for _ in range(table.cols)]
        widths[-1] += total_width - sum(widths)
        return widths

    def _table_row_heights(self, table: Table, total_height: int) -> list[int]:
        if table.row_heights:
            heights = [to_emu(value, default_unit="pt") for value in table.row_heights]
            if len(heights) != table.rows or any(value is None for value in heights):
                raise ValueError("row_heights length must match table rows")
            return [int(value) for value in heights if value is not None]
        base = total_height // table.rows
        heights = [base for _ in range(table.rows)]
        heights[-1] += total_height - sum(heights)
        return heights

    def _table_cell_borders(self, tc_pr, table: Table) -> None:
        if not table.border_color:
            return
        for tag in ("lnL", "lnR", "lnT", "lnB"):
            self._line(tc_pr, table.border_color, table.border_width, tag=tag)

    def _scheme_color(self, parent, tag: str, color: str) -> None:
        node = _sub(parent, "a", tag)
        _sub(node, "a", "srgbClr", {"val": normalize_color(color)})


def _alignment(value: str | None) -> str | None:
    if value is None:
        return None
    return {
        "left": "l",
        "center": "ctr",
        "right": "r",
        "justify": "just",
    }[value]


def _vertical_anchor(value: str) -> str:
    return {
        "top": "t",
        "middle": "ctr",
        "bottom": "b",
    }[value]
