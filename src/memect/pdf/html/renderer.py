import html
import importlib
import importlib.resources
import json
import re
from collections.abc import Mapping
from typing import Any, Final, Iterable, Sequence

from memect.base.bbox import BBox
from memect.pdf.base import (
    KBlock,
    KCell,
    KDocument,
    KFigure,
    KFormula,
    KObject,
    KPage,
    KPageFootnote,
    KSpan,
    KTable,
    KText,
    KTextline,
)
from memect.pdf.x.xbase import XNode

type _BBox = Sequence[float]  # tuple[float,float,float,float,float,float]
type _Offset = tuple[float, float]
type _Object = dict[str, Any]


class _Html:
    def __init__(self, html: str):
        super().__init__()
        self._html = html

    def __str__(self) -> str:
        return self._html



class _Tag:
    def __init__(self, name: str):
        super().__init__()
        self.name = name
        self.style: dict[str, Any] = {}
        self.classes: list[str] = []
        self.attrs: dict[str, Any] = {}
        self.children: list[Any] = []
        self.data: dict[str, Any] = {}

    @classmethod
    def escape(cls, s: str, linefeed: bool = False, space: bool = False):
        if not s:
            return ""
        s = html.escape(s)
        if space:
            s = re.sub(" ", "&nbsp;", s)
        if linefeed:
            s = s.replace("\n", "&#10;")
        return s

    @classmethod
    def calc(cls, v: float, scale: float | None = None, unit: str = "px") -> str:
        if scale is None:
            # 如果为None，表示使用变量
            return f"calc(var(--scale) * {v:.1f}{unit})"
        else:
            return f"{v * scale:.1f}{unit}"

    def set_pos(
        self, bbox: BBox, offset: _Offset | None = None, scale: float | None = None
    ):
        """坐标原点都是左上角"""
        offset = offset or (0, 0)
        left = bbox[0] - offset[0]
        top = bbox[1] - offset[1]
        self.style["left"] = self.calc(left, scale=scale)
        self.style["top"] = self.calc(top, scale=scale)

    def set_size(self, bbox: BBox, scale: float | None = None):
        self.style["width"] = self.calc(bbox[2] - bbox[0], scale=scale)
        self.style["height"] = self.calc(bbox[3] - bbox[1], scale=scale)

    def set_bbox(
        self, bbox: BBox, offset: _Offset | None = None, scale: float | None = None
    ):
        self.set_pos(bbox, offset=offset, scale=scale)
        self.set_size(bbox, scale=scale)

    def set_index(self, index: Any):
        if isinstance(index, int):
            self.style["z-index"] = index
        elif isinstance(index, dict) and isinstance(index.get("index"), int):
            # 添加一个统一的偏移量
            self.style["z-index"] = index.get("index") + 100
        else:
            # raise ValueError(f'不支持的index类型:{index}')
            pass

    def set_data(self, obj: Mapping[str, Any], keys: Iterable[str]|None=None):
        keys = keys or obj.keys()
        for key in keys:
            value = obj.get(key, None)
            if value is not None:
                if isinstance(value, bool):
                    value = "true" if value else "false"
                elif isinstance(value,float):
                    value=f'{value:.1f}'
                else:
                    pass
                self.data[key] = value

    def set_title(self, obj: Mapping[str, Any], keys: Sequence[str]):
        buf: list[str] = []
        for key in keys:
            value = obj.get(key)
            if value is not None:
                # 需要转换为json吗？不需要
                buf.append(f"{key}={value}")

        title = "\n".join(buf)
        self.attrs["title"] = title

    def set_class_by_level(self,base: str,level:int):
        """对于有层次结构的，可以通过奇偶级别设置不同的class，交替呈现"""
        if level % 2 == 0:
            self.classes.append(f"{base}-even")
        else:
            self.classes.append(f"{base}-odd")

    def set_color(self, name: str, color: Any):
        if isinstance(color, str):
            self.style[name] = color
        elif len(color) == 3:
            self.style[name] = f"rgb({color[0]},{color[1]},{color[2]})"
        elif len(color) == 4:
            self.style[name] = f"rgba({color[0]},{color[1]},{color[2]},{color[3]})"
        else:
            # 不知道的颜色
            pass

    def __str__(self) -> str:
        # <a style="" class="a,b,c" ></a>
        buf: list[str] = []
        buf.append("<")
        buf.append(self.name)

        if self.style:
            buf.append(' style="')
            for k, v in self.style.items():
                buf.append(f"{k}:{v};")
            buf.append('"')

        if self.classes:
            buf.append(' class="')
            buf.append(" ".join(self.classes))
            buf.append('"')

        def quote(v: Any):
            if v is None:
                return '""'
            v = str(v)
            return f'"{self.escape(v, linefeed=True)}"'

        if self.attrs:
            for k, v in self.attrs.items():
                buf.append(" ")
                buf.append(k)
                if v != "" and v is not None:
                    buf.append("=")
                    buf.append(quote(v))
                

        if self.data:
            for k, v in self.data.items():
                buf.append(" ")
                buf.append(f"data-{k}")
                buf.append("=")
                buf.append(quote(v))

        buf.append(">")

        if self.children:
            for child in self.children:
                # TODO 如果是字符串的，需要先转义吗？
                if isinstance(child, str):
                    buf.append(self.escape(child, linefeed=True, space=True))
                else:
                    buf.append(str(child))

        buf.append("</")
        buf.append(self.name)
        buf.append(">")
        return "".join(buf)



class HtmlRenderer:
    _template = (
        importlib.resources.files(__package__).joinpath("doc.html").read_text("utf-8")
    )
    _js = (
        importlib.resources.files(__package__).joinpath("doc.js").read_text("utf-8")
    )
    def __init__(self):
        super().__init__()
        self._scale: float | None = None
        
    
    def render(self,doc:KDocument,scale:float|None=None)->str:
        data={
            'scale':1,
            'total':doc.page_count,
            'currentNumber':doc.working_pages[0].number if doc.working_pages else 1,
            'bgColor':'#ffffff',
            'tree': self._build_tree(doc)
        }
        values={
            '"{{doc_data}}"':json.dumps(data),
            '"{{doc_js}}"': self._js,
            '"{{doc_html}}"':self._render_doc(doc,scale=scale)
        }
        return self._render_template(self._template,values)
    
    def _build_tree(self,doc:KDocument):
        if doc.tree is not None:
            def build_node(node:XNode)->Any:
                data:dict[str,Any]={}
                page_numbers = node.object.page_numbers
                if not page_numbers:
                    page_numbers=[-1,-1]
                if node.is_title():
                    #data['text']=node.text.text
                    data['text']=f'[{page_numbers[0]},{page_numbers[-1]},{len(node.object.objects)}]{node.text.text}'
                else:
                    #[页码1,页码2][1][type]
                    page_numbers = node.object.page_numbers
                    data['text']=f'[{page_numbers[0]},{page_numbers[-1]},{len(node.object.objects)}][{node.type}]'
                
                if node.size>0:
                    data['children']=[build_node(child) for child in node.children]
                
                #xid表示节点的id
                #ids表示在页面渲染的对象的id，目的是点击后可以滚动到目标节点
                data['xid']=node.id
                data['ids']=[]
                data['type']=node.type
                return data
            tree={
                'root':build_node(doc.tree.root)
            }
        else:
            tree={
                'root':{
                        'type':'page-root',
                        'text':'页面',
                        'number':1,
                        'children':[
                            {
                                'type':'page',
                                'text':f'第{page.number}页',
                                'number':page.number
                            } for page in doc.pages
                        ]
                    }
            }
        return tree

    def _render_template(self,template: str, values: dict[str, Any]) -> str:
        replaces: list[dict[str, Any]] = []
        for k, v in values.items():
            i = template.index(k)
            j = i + len(k)
            replaces.append({"start": i, "end": j, "value": v})

        html_buf: list[str] = []
        replaces.sort(key=lambda r: r["start"])
        start = 0
        for r in replaces:
            html_buf.append(template[start : r["start"]])
            html_buf.append(r["value"])
            start = r["end"]

        html_buf.append(template[start:])
        return "".join(html_buf)
    def _render_doc(self, doc: KDocument,scale:float|None=None):
        
        tag = _Tag("div")
        tag.classes.append("doc")
        tag.attrs['v-pre']=''
        #tag.set_data({'show-layout':''})
        #tag.set_data({'show-page-number':''})
        if False:
            if scale is not None:
                tag.style['--scale']=scale
                scale=None
            else:
                tag.style['--scale']=1
        else:
            scale=1
        
        self._scale = scale
        for page in doc.pages:
            tag.children.extend(self._render_page(page))

        return str(tag)

    def _render_page(self, page: KPage) -> list[_Tag]:
        # <div class="page"></div>

        def render_header(tag:_Tag):
            if not page.header.objects:
                return
            m = page.to_lt()
            bbox = page.header.bbox.transform(m)
            header = _Tag("div")
            header.classes.append("header")
            header.set_bbox(bbox,scale=scale)
            header.set_data({'type':'header'})
            header.children.extend(
                self._render_objects(page.header.objects, offset=(bbox[0], bbox[1]))
            )
            #这里的就不显示阅读顺序了
            tag.children.append(header)

        def render_footer(tag:_Tag):
            if not page.footer.objects:
                return
            m = page.to_lt()
            bbox = page.footer.bbox.transform(m)
            footer = _Tag("div")
            footer.classes.append("footer")
            footer.set_bbox(bbox,scale=scale)
            footer.set_data({'type':'footer'})
            footer.children.extend(
                self._render_objects(page.footer.objects, offset=(bbox[0], bbox[1]))
            )
            #这里的就不显示阅读顺序了
            tag.children.append(footer)

        def render_footnotes(tag:_Tag):
            if not page.footnotes:
                return
            for footnote in page.footnotes:
                render_footnote(tag,footnote)

        def render_footnote(tag:_Tag,footnote:KPageFootnote):
            m = page.to_lt()
            bbox = footnote.bbox.transform(m)
            footnote_tag = _Tag("div")
            footnote_tag.classes.append("footnote")
            footnote_tag.set_bbox(bbox,scale=scale)
            footnote_tag.set_data({'type':'footnote'})
            footnote_tag.children.extend(
                self._render_objects(footnote.objects, offset=(bbox[0], bbox[1]))
            )
            #这里的就不显示阅读顺序了
            tag.children.append(footnote_tag)

        def render_read_order():
            m=page.to_lt()
            tags:list[_Tag]=[]
            use_left=False
            for i,obj in enumerate(page.objects):
                tag = _Tag('div')
                tag.classes.append('read-order')
                bbox = obj.bbox.transform(m)
                top = bbox.y0
                #显示后的大小(padding_left+padding_right+fontsize/2*n)
                width = 2+2+len(str(i+1))*4+1
                if use_left:
                    #正常
                    left = max(0,bbox.x0-width)
                    tag.style['left']=tag.calc(left,scale=scale)
                else:
                    #因为当前body设置了absolute:position，但是没有设置width/height，所以width=height=0
                    #所以right就等同于相对页面的左边
                    right =-bbox.x0+5
                    if width+right>0:
                        #如果溢出了页面
                        right = -width
                    tag.style["right"] = tag.calc(right, scale=scale)
                tag.style["top"] = tag.calc(top, scale=scale)
                tag.children.append(str(i+1))
                tags.append(tag)
            return tags
        
        def render_body(tag:_Tag):
            body = _Tag("div")
            body.classes.append("body")
            for obj in page.objects:
                tags = self._render_object(obj)
                for t in tags:
                    self._set_xid(t,obj)
                body.children.extend(tags)
            #body.children.extend(self._render_objects(page.objects, offset=None))
            body.children.extend(render_sections())
            body.children.extend(render_read_order())
            
            tag.children.append(body)

        def render_sections():
            m=page.to_lt()
            tags:list[_Tag]=[]
            for section in page.sections:
                bbox = section.bbox.transform(m)
                section_tag = _Tag('div')
                section_tag.classes.append('section')
                section_tag.set_bbox(bbox,scale=self._scale)
                if len(section.columns)>1:
                    for column in section.columns:
                        column_tag = _Tag('div')
                        column_tag.classes.append('column')
                        column_tag.set_bbox(column.transform(m),scale=self._scale,offset=(bbox[0],bbox[1]))
                        section_tag.children.append(column_tag)
                tags.append(section_tag)
            return tags
                


        scale = self._scale
        m = page.to_lt()
        tag = _Tag("div")
        tag.classes.append("page")
        tag.set_size(page.bbox.transform(m), scale=scale)
        tag.set_data({'number':page.number,'type':str(page.type)})

        render_header(tag)
        render_body(tag)
        render_footnotes(tag)
        render_footer(tag)

        for pos in ['top','bottom']:
            page_number = _Tag('div')
            page_number.classes.append('page-number')
            page_number.set_data({'position':pos})
            page_number.children.append(str(page.number))
            tag.children.append(page_number)
        

        return [tag]

    def _render_figure(
        self, figure: KFigure, offset: _Offset | None = None
    ) -> list[_Tag]:
        # <img src="">
        m = figure.page.to_lt()
        tag = _Tag("img")
        tag.classes.append("figure")
        tag.set_bbox(figure.bbox.transform(m), offset=offset, scale=self._scale)
        tag.set_data({'type':figure.type})
        tag.attrs["src"] = figure.filename
        return [tag]

    def _render_formula(
        self, formula: KFormula, offset: _Offset | None = None
    ) -> list[_Tag]:
        m = formula.page.to_lt()
        tag = _Tag('div')
        tag.classes.append('formula')
        tag.set_bbox(formula.bbox.transform(m), offset=offset, scale=self._scale)
        tag.set_data({'type':formula.type,'latex':formula.latex,'image':formula.filename})
        use_img = False
        if use_img:
            img = _Tag('img')
            img.attrs['src']=formula.filename
            tag.children.append(img)

        return [tag]

    def _render_table(self, table: KTable, offset: _Offset | None = None):
        m: Final = table.page.to_lt()
        bbox: Final = table.bbox.transform(m)
        tag = _Tag("table")
        tag.classes.append("table")
        tag.set_bbox(bbox, offset=offset, scale=self._scale)
        tag.set_data({'type':table.type,'subtype':table.subtype or 'wbk'})
        #if table.merged:
            #tag.set_data({'merged':'true'})

        # <table classe="table" style=""></table>

        # 设置列的默认width，这样当存在错位的列，也能够100%还原

        def get_x_axis() -> list[float]:
            axis = [None] * table.col_num
            for cell in table.cells:
                i = cell.col_index
                # j = cell.col_index+cell.col_span
                if axis[i] is None:
                    assert cell.bbox is not None
                    axis[i] = cell.bbox[0]
            axis.append(table.bbox[2])
            return axis

        def render_cell(cell: KCell,offset:_Offset|None=None) -> list[_Tag]:
            td = _Tag("td")
            #td.classes.append('cell')
            if cell.col_span>1:
                td.attrs["colspan"] = cell.col_span
            if cell.row_span>1:
                td.attrs["rowspan"] = cell.row_span

            td.style["height"] = td.calc(cell.bbox.height, scale=self._scale)
            td.children.extend(self._render_objects(cell.objects, offset=offset))

            if cell.subtype=='header':
                td.set_data({'subtype':cell.subtype})

            if cell.merged is True:
                td.set_data({'cell-merged':'true'})
            
            show_bg_color=True
            if show_bg_color and not table.is_layout():
                #显示了背景颜色，字体的颜色也需要更新，因为默认使用白底黑字
                #如果背景是黑色的
                if cell.color and not cell.color.is_white():
                    #显示了背景颜色，需要同时使用对应的字体颜色
                    #td.style['background-color']=f'#{cell.color.rgba[0]:02x}{cell.color.rgba[1]:02x}{cell.color.rgba[2]:02x}'
                    #td.style['color']=f'#{cell.font_color.rgba[0]:02x}{cell.font_color.rgba[1]:02x}{cell.font_color.rgba[2]:02x}'
                    td.set_data({
                        'color':f'#{cell.color.rgba[0]:02x}{cell.color.rgba[1]:02x}{cell.color.rgba[2]:02x}'
                    })

            return [td]

        x_axis = get_x_axis()

        colgroup = _Tag("colgroup")
        for i in range(table.col_num):
            width = x_axis[i + 1] - x_axis[i]
            col = _Tag("col")
            # col.style['width']=f'{width:.2f}px'
            col.style["width"] = tag.calc(width, scale=self._scale)
            colgroup.children.append(col)

        tag.children.append(colgroup)
        # =========
        tbody = _Tag("tbody")
        trs: list[_Tag] = []
        td_offset = (bbox[0],bbox[1])
        for cell in table.cells:
            if cell.row_index >= len(trs):
                tr = _Tag("tr")
                trs.append(tr)
            tr = trs[-1]  # rows[cell.row_index]
            tr.children.extend(render_cell(cell,td_offset))

        tbody.children.extend(trs)
        tag.children.append(tbody)



        return [tag]



    def _render_textline(
        self, tl: KTextline, offset: _Offset | None = None
    ) -> list[_Tag]:
        m = tl.page.to_lt()
        bbox = tl.bbox.transform(m)
        tag = _Tag("div")
        tag.classes.append("textline")
        tag.set_bbox(bbox, offset=offset, scale=self._scale)
        tag.set_data({'type':tl.type})
        tag.children.extend(self._render_objects(tl.split(), offset=(bbox[0], bbox[1])))
        return [tag]

    def _render_span(self, span: KSpan, offset: _Offset | None = None) -> list[_Tag]:
        def set_text_style(tag: _Tag, span: KSpan, *, scale: float | None = None):
            style = tag.style
            # size:float = span['fontsize']*scale
            # 在html显示中，使用这个更加精确
            # TODO 如果是垂直的书写的，字体的大小不是这么计算

            vertical = False
            bold: bool = span.bold
            italic: bool = span.italic
            underline: bool = span.underline
            # 删除线
            strikeout: bool = span.strikeout

            color = span.color
            font = span.font
            bbox = span.bbox

            size: float
            if vertical:
                # 如果是垂直书写的
                # size就是
                size = (bbox[3] - bbox[1]) / len(span.text)
                style["writing-mode"] = "vertical-lr"
            else:
                size = bbox[3] - bbox[1]
            # 在这里设置的字体还无法100%准确的，因为浏览器具体使用哪些字体还是未知的
            # 当然，可以指定网络字体，然后再这里先计算好
            # 否则就需要在浏览器渲染后，再计算transform-scale
            style["font-size"] = tag.calc(size, scale=scale)
            # style['transform-origin']='left center'
            # style['transform']='scaleX()'.format()
            # style['line-height']='{:.2f}px'.format(h_size)
            if bold:
                style["font-weight"] = "bold"
            if italic:
                style["font-style"] = "italic"

            if color.is_black():
                # 多数都是黑色，就不设置了，减少文件的体积
                pass
            elif color.is_white():
                #白色也不设置（使用默认黑色），因为默认为白色背景
                pass
            elif color.rgba[3] == 1:
                style["color"] = f"rgb({color.rgba[0]},{color.rgba[1]},{color.rgba[2]})"
            else:
                style["color"] = (
                    f"rgba({color.rgba[0]},{color.rgba[1]},{color.rgba[2]},{color.rgba[3]})"
                )

            if underline and strikeout:
                style["text-decoration"] = "underline line-through"
            elif underline:
                style["text-decoration"] = "underline"
            elif strikeout:
                style["text-decoration"] = "line-through"
            else:
                pass

            # 使用泛字体名字即可，浏览器自动选择合适的，因为指定某个字体浏览器不一定有，或者仅仅为英文字体，中文也会选择其他的
            # 严格的说，因为在span的时候，为了方便计算，可能已经按照宽度相同划分了字符串，所以仅仅需要使用等宽字体即可

            #style["font-family"] = font.name
            if font.wingdings:
                # 表示为微软的符号字体，用户的环境不一定安装有，而且使用私有unicode
                # 但是可以在css中指定使用来自google font的字体
                # style['font-family']
                tag.classes.append("span-monospace")
            elif font.monospace:
                tag.classes.append("span-monospace")
            elif font.serif:
                tag.classes.append("span-serif")
            elif font.sans_serif:
                tag.classes.append("span-sans")
            else:
                tag.classes.append("span-monospace")

        m = span.page.to_lt()
        tag = _Tag("span")
        tag.classes.append("span")
        tag.set_pos(span.bbox.transform(m), offset=offset, scale=self._scale)
        #最多设置height，不要设置width，因为需要根据最终使用的字体来计算
        #tag.set_bbox(span.bbox.transform(m), offset=offset, scale=self._scale)
        set_text_style(tag, span, scale=self._scale)

        tag.set_data({'width':span.bbox.width,'height':span.bbox.height})
        if span.font.wingdings:
            tag.set_data({'wingdings':'true'})
        tag.children.append(span.text)
        return [tag]


    def _render_text(self, text: KText, offset: _Offset | None = None) -> list[_Tag]:
        m = text.page.to_lt()
        bbox = text.bbox.transform(m)
        tag = _Tag("div")
        
        tag.set_bbox(bbox, offset=offset, scale=self._scale)
        tag.set_data({'type':text.type})
        if text.lines:
            #使用另外的class名字
            tag.classes.append("textbox")
            tag.children.extend(self._render_objects(text.lines, offset=(bbox[0], bbox[1])))
        else:
            tag.classes.append("text")
            tag.children.append(text.text)
        return [tag]
    
    def _render_block(self,block:KBlock,offset:_Offset|None=None):
        m = block.page.to_lt()
        bbox = block.bbox.transform(m)
        tag=_Tag('div')
        tag.classes.append('block')
        tag.set_data({'type':block.type})
        tag.set_bbox(bbox,offset=offset,scale=self._scale)
        tag.children.extend(self._render_objects(block.objects,offset=(bbox[0],bbox[1])))
        return [tag]

    def _render_unknown(
        self, obj: KObject, offset: _Offset | None = None
    ) -> list[_Tag]:
        # <div class="unknown"></div>
        m = obj.page.to_lt()
        tag = _Tag("div")
        tag.classes.append("unknown")
        tag.set_bbox(obj.bbox.transform(m), offset=offset, scale=self._scale)
        return [tag]

    def _render_object(self, obj: KObject, offset: _Offset | None = None) -> list[_Tag]:
        if isinstance(obj, KSpan):
            return self._render_span(obj, offset)
        elif isinstance(obj, KTextline):
            return self._render_textline(obj, offset)
        elif isinstance(obj, KFigure):
            return self._render_figure(obj, offset)
        elif isinstance(obj, KFormula):
            return self._render_formula(obj, offset)
        elif isinstance(obj, KTable):
            return self._render_table(obj, offset)
        elif isinstance(obj, KText):
            return self._render_text(obj, offset)
        elif isinstance(obj,KBlock):
            return self._render_block(obj,offset)
        else:
            return self._render_unknown(obj, offset)


    def _render_objects(
        self, objs: Sequence[KObject], offset: _Offset | None = None
    ) -> list[_Tag]:
        tags: list[_Tag] = []
        for obj in objs:
            tags.extend(self._render_object(obj, offset=offset))
        return tags
    


    def _set_xid(self,tag:_Tag,obj:KObject):
        if obj.doc.tree is not None:
            node = obj.doc.tree.root.lookup(obj)
            if node is not None:
                tag.set_data({'xid':node.id})
                if len(node.object.objects)>1:
                    tag.set_data({'merged':''})
