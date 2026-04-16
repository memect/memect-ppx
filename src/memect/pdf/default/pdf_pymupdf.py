import logging
import math
import re
from typing import Any, Final, Literal, Sequence

import PIL
import PIL.ImageDraw
import pymupdf

from memect.base import images, lists, utils
from memect.base.bbox import BBox, Quad
from memect.base.debug import XDebugger
from memect.base.matrix import Matrix
from memect.pdf.base import (
    CharSource,
    KChar,
    KColor,
    KDocument,
    KFont,
    KLine,
    KPage,
    KPDFFigure,
)


class Parser:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")
    _no_mapping: dict[int, str] = {
        # ms word
        108: "\u25cf",  # ●,
        110: "\u25a0",  # \u25A0,■,\u25fc,◼
        115: "\2B27",  # ⬧
        116: "\u29eb",  # ⧫
        117: "\u25c6",  # ◆
        118: "\u2756",  # ❖
        119: "\u2b25",  # ⬥
        167: "\u25aa",  # ▪
        178: "\u2727",  # ✧
        216: "\u27a2",  # ➢
        251: "\u2718",  # \u2717,✗,\u2718,✘
        252: "\u2714",  # \u2713,✓,\u2714,✔
        253: "\u2612",  # ☒
        254: "\u2611",  # ☑
        # 私有字符空间，不一定100%
        0xF052: "\u2611",
        0xF0FB: "\u2717",
        0xF07D: "\u25ba",  # ,►
        # 正常应该为216
        # Acrobat Distiller|PScript5
        190: "\u27a2",
        # wps
        61630: "\u25fc",
        61548: "\u25cf",
        61550: "\u25fc",
        61557: "\u25c6",
        61558: "\u2725",
        61656: "\u27a2",
        61618: "\u2727",
        61692: "\u2714",
    }

    def __init__(self):
        super().__init__()

    def parse(self, kdoc: KDocument):
        with pymupdf.Document(filename=kdoc.file, filetype="pdf") as doc:
            # 在一个文档中，使用的字体和颜色不会太多，如果相同的使用同一个对象即可
            for kpage in kdoc.working_pages:
                self._parse_page(doc, kpage)

    def _parse_page(self, doc: pymupdf.Document, kpage: KPage):
        self._parse_rawdict(doc, kpage)

    def _parse_rawdict(self, doc: pymupdf.Document, kpage: KPage):
        page: Final = doc[kpage.number - 1]
        debugger: Final = self._debugger.bind(page=kpage.number)
        # get_texttrace() 按原始指令顺序返回，不重排
        # https://pymupdf.readthedocs.io/en/latest/functions.html#Page.get_texttrace
        # https://github.com/pymupdf/PyMuPDF/blob/main/src/__init__.py
        # 看源代码，没有应用page自身的rotation，而是先总是变成0，如果页面自身设置了rotation，就不一致了
        # 因为现在需要的是相对rotation后的解析

        def is_overlapped(b1: Sequence[float], b2: Sequence[float]) -> bool:
            """判断是否完全重叠，也就是一个cid对应多个字符"""
            # [b1][b2] => b2的宽度为0
            return (
                math.isclose(b2[0], b2[2])
                and math.isclose(b2[0], b1[2])
                and math.isclose(b1[3], b2[3])
                and math.isclose(b1[1], b2[1])
            )

        def parse_text(block: Any):
            for line in block["lines"]:
                for span in line["spans"]:
                    parse_span(span)

        def parse_span(span: Any):
            verbose=False
            alpha = span["alpha"]
            # 原点为左上角
            bbox = span["bbox"]
            if alpha == 0:
                self._logger.warning(
                    "第%s页删除透明的span=%s,bbox=%s",
                    kpage.number,
                    [c["c"] for c in span["chars"]],
                    bbox,
                )
                return
            if bbox[3] - bbox[1] <= 3:
                self._logger.warning(
                    "第%s页，删除很小的span=%s,bbox=%s",
                    kpage.number,
                    [c["c"] for c in span["chars"]],
                    bbox,
                )
                return
            # color = pymupdf.sRGB_to_rgb(span['color'])
            # 0xRRGGBB
            color = KColor.from_list(
                pymupdf.sRGB_to_rgb(span["color"]),
                is_float=False,
                alpha=alpha / 255,
                colors=kpage.doc.colors,
            )

            flags = span["flags"]
            char_flags = span["char_flags"]
            is_superscript = bool(flags & pymupdf.TEXT_FONT_SUPERSCRIPT)
            # pymupdf根据字体的信息简单判断，不够准确，不如之前我们自己解析的准确
            is_bold = bool(flags & pymupdf.TEXT_FONT_BOLD)
            # 有些文字是通过渲染的时候倾斜实现的，也就是需要获得字体的matrix，判断倾斜度数
            is_italic = bool(flags & pymupdf.TEXT_FONT_ITALIC)

            is_strikeout = bool(char_flags & 1)
            is_underline = bool(char_flags & 2)
            # https://pymupdf.readthedocs.io/en/latest/textpage.html#TextPage.extractRAWDICT
            # 文档应该是写错了filled=2**3，stroked=2**4，实际上应该是相反
            # is_filled = bool(char_flags & 8)
            # is_stroked = bool(char_flags & 16)
            is_filled = bool(char_flags & 16)
            is_stroked = bool(char_flags & 8)
            is_clipped = bool(char_flags & 32)

            font = self._get_font(span)
            if debugger.allow("info"):
                with debugger.group("span"):
                    print("".join(c["c"] for c in span["chars"]))
                    print(
                        {
                            "bold": is_bold,
                            "italic": is_italic,
                            "underline": is_underline,
                            "strikeout": is_strikeout,
                            "superscript": is_superscript,
                            "filled": is_filled,
                            "stroked": is_stroked,
                            "clipped": is_clipped,
                        }
                    )

            chars = span["chars"]
            i = 0
            while i < len(chars):
                char = chars[i]
                bbox = BBox.from_list(char["bbox"], matrix=matrix)
                origin = char["origin"]
                text = char["c"]
                raw_text=text
                wingdings_text=None
                j = i + 1
                while j < len(chars):
                    char2 = chars[j]
                    # 如果2个bbox重叠，表示对应的是同一个cid
                    if is_overlapped(char["bbox"], char2["bbox"]):
                        j += 1
                    else:
                        break
                if j - i > 1:
                    # 连体字符或者部首+字或者多个兼容字
                    # 如果是连体字符，替换为一个，因为原文显示就是一个连体字符，但是复制为n个
                    # 如果是其他，仅仅选择最合适的一个，因为形似但是unicode不同
                    raw_text = "".join([c["c"] for c in chars[i:j]])
                    text = self._normalize_text(raw_text)
                    self._logger.warning(
                        "cid对应多个字符，现在纠正：page=%s,from=%s,to=%s,unicode=0x%04X",
                        kpage.number,
                        raw_text,
                        text,
                        ord(text),
                    )
                elif font.wingdings:
                    #TODO 如果是ms生成的pdf，通常使用的是0x20-0xff之间的编码，而且是一一对应
                    #如果是wps，通常使用的是0xf020-0xf0ff之间的编码，也是一一对应
                    #如果是其他软件制作的，就不一定了，如果需要准确的，就需要设置force=True，也就是总是通过视觉识别
                    force=True
                    cv2_image = get_cv2_image()
                    h,w = cv2_image.shape[0:2]
                    m = kpage.to_lt((w,h))
                    wingdings_text,text = font.normalize_wingdings(cv2_image,bbox.transform(m).to_int(),text,force=force)
                    self._logger.info('normalize wingdings,page=%s,from=(%s,0x%04X),to=(%s,0x%04X),unicode=(%s,0x%04X)',kpage.number,raw_text,ord(raw_text),wingdings_text,ord(wingdings_text),text,ord(text))
                elif verbose:
                    # 对应单个文本，就不管了，返回什么就是什么
                    # 如果无法找到准确的
                    unicode = ord(text)
                    if unicode == 0xFFFD:
                        # 65533，表示无效的unicode，有下面几种可能
                        # 1.cid没有对应的unicode，在pdf阅读工具上，就是无法复制（需要通过ocr）
                        # 2.cid有对应的unicode，但是为无效的，如："\u0000"
                        # local/cases/test/12-cid没有对应的unicode.pdf 第2页的箭头，就是第一种
                        # local/cases/test/11-有字符返回0000.pdf 第5页，为第二种，返回是“\u0000"
                        # 有些还是可以通过ocr获得
                        self._logger.warning(
                            "无效的字符，page=%s,font=%s,bbox=%s",
                            kpage.number,
                            span["font"],
                            bbox,
                        )

                    elif 0xE000 <= unicode <= 0xF8FF:
                        # pua:private user area，私有的区域
                        # BMP PUA0xE000 – 0xF8FF
                        # 补充PUA-A0xF0000 – 0xFFFFF
                        # 补充PUA-B0x100000 – 0x10FFFF
                        # 去掉这个字符，还是仍然保留
                        self._logger.warning(
                            "pua字符，page=%s,font=%s,unicode=0x%04X,bbox=%s",
                            kpage.number,
                            span["font"],
                            unicode,
                            bbox,
                        )
                    else:
                        pass
                i = j
                # print(c)
                if is_filled or is_stroked:
                    kpage.pdf_chars.append(
                        KChar(
                            kpage,
                            bbox.to_quad(),
                            bold=is_bold,
                            italic=is_italic,
                            underline=is_underline,
                            strikeout=is_strikeout,
                            text=text,
                            font=font,
                            color=color,
                            subtype="superscript" if is_superscript else None,
                            source=CharSource.PDF,
                            raw_text=raw_text,
                            wingdings_text=wingdings_text
                        )
                    )
                else:
                    self._logger.warning(
                        "第%s页，忽略没有fill或者stroke的字符,span=%s,bbox=%s", bbox
                    )
                pass

        def parse_image(block: Any):
            pass


        cv2_image=None
        def get_cv2_image()->Any:
            """在识别wingdings的时候需要"""
            nonlocal cv2_image
            if cv2_image is None:
                cv2_image=images.pil_to_cv2(kpage.image)
            return cv2_image

        use_images = False
        use_paths = True
        ignore_actual_text = False
        # 这是默认的rawdict的flags
        flags:int = (
            pymupdf.TEXT_PRESERVE_LIGATURES
            | pymupdf.TEXT_PRESERVE_WHITESPACE
            | pymupdf.TEXT_MEDIABOX_CLIP
            | pymupdf.TEXT_PRESERVE_IMAGES
            | pymupdf.TEXT_USE_CID_FOR_UNKNOWN_UNICODE
        )
        # TEXT_INHIBIT_SPACES： 表示不要自动根据水平间距生成空格，默认会自动根据水平间距生成
        flags = (
            pymupdf.TEXT_PRESERVE_LIGATURES
            | pymupdf.TEXT_PRESERVE_WHITESPACE
            | pymupdf.TEXT_MEDIABOX_CLIP
        )
        if use_paths:
            flags = flags | pymupdf.TEXT_COLLECT_VECTORS
        if use_images:
            #连内容也返回，比较耗时
            flags = flags | pymupdf.TEXT_PRESERVE_IMAGES
        if ignore_actual_text:
            # 如果不设置这个，当cid对应的unicode为\u0000的时候，就使用ActualText（如果有，当然，不一定和显示的一致）
            # local/cases/test/11-有字符返回0000.pdf --pages 5
            flags = flags | pymupdf.TEXT_IGNORE_ACTUALTEXT

        timer = utils.Timer.start()
        timer.mark('start gettext')
        result: dict[str, Any] = page.get_text("rawdict", flags=flags)
        timer.mark('end gettext')
        width = result["width"]
        height = result["height"]
        matrix: Final = Matrix.lt_to_lb((width, height), kpage.size)
        kpage.pdf_chars.clear()
        #kpage.pdf_paths.clear()
        kpage.pdf_lines.clear()
        kpage.pdf_rects.clear()
        
        paths:list[Any]=[]
        for block in result["blocks"]:
            type_ = block["type"]
            if type_ == 0:
                parse_text(block)
            elif type_ == 1:
                parse_image(block)
            elif type_ == 3:
                paths.append(block)
            else:
                pass
        
        if not kpage.pdf_chars:
            # 没有文字，就按图片解析，不管是什么样了
            self._logger.info("第%s页没有文字，不需要再解析,paths=%s", kpage.number,len(paths))
            return

        # 如果多个图片组成一个大图，一样按图片解析，不管图片是背景还是扫描（可能覆盖文字）
        if self._parse_figures(doc, kpage):
            return
        
        if paths:
            #因为有些文档用很多线来渲染图片，为了减少计算，把图片中的线给去掉
            pass
        
        def clean_paths(paths:list[Any]):
            #先清除无用的线，避免有几十万的时候增加计算量
            rects:list[Any]=[]
            lines:list[Any]=[]
            for vobj in kpage.vobjects:
                if vobj.is_figure() or vobj.is_seal() or vobj.is_chart():
                    vobj.bbox.expand(dx=2,dy=2).get(paths,ratio=0.5,remove=True)
                else:
                    #在文本框和表格区域的保留
                    pass
            #然后就可以转换为线了
            for path in paths:
                bbox = path['bbox']
                if bbox[2]-bbox[0]>=5 and bbox[3]-bbox[1]>=5:
                    if not path['stroked']:
                        rects.append(path)
                else:
                    lines.append(path)
            
            #如果是矩形，可以合并？
            #如果是线，合并

        if use_paths:
            self._parse_paths(kpage,paths)
        else:
            # 如果没有一同返回path，就需要单独解析
            #page.get_cdrawings()
            pass

    def _get_font(self, span: Any) -> KFont:
        #span={'flags':1,'font':'xxxx'}
        flags = span["flags"]
        is_monospace = flags & 8
        is_serif = flags & 4
        return KFont.get(span['font'],monospace=is_monospace,serif=is_serif)


    def _normalize_text(self, text: str) -> str:
        # 仅仅归一化特殊的字符，不归一化标点符号等
        # 没有被归一化，如：ff => ['f','f']
        # 1个glyph对应多个字符，如果直接返回多个字符，就无法做到一个字符对应一个XChar对象了，增加了后续处理的复杂度
        # 所以这里就尽量的归一化了，但是还是没有办法做的，所以就只能够取第一个字符了
        assert len(text) > 1
        from memect.pdf.chars import normalize, to_ligature

        s = to_ligature(text)
        if s != text:
            # 表示为连字符，多个变成1个
            return s
        # 不是连字符，可能是兼容字
        new_text: list[str] = []
        normalized = False
        for c in text:
            # 仅仅支持单个字符，没有处理组合字符
            c2 = normalize(c)
            if c != c2:
                normalized = True
            new_text.append(c2)

        if normalized and len(set(new_text)) == 1:
            # 多个字符归一化为一个字符，如：
            #'⽓气' = '\u2f53\u6c14' => ['\u6c14','\u6c14'] => '气'
            return new_text[0]
        else:
            # 无法归一化，就使用最后一个
            return text[-1]

    def _parse_figures(self, doc: pymupdf.Document, kpage: KPage) -> bool:
        """解析图片，如果返回True，表示整个页面作为图片，不需要再解析"""

        def is_full_page(bboxes: list[Any]):
            # 这里使用的坐标原点为页面左上角
            from shapely.geometry import box
            from shapely.ops import unary_union

            # 如果需要再严格一点，获得
            # 使用page.rect还是page.bound()?
            page_box = box(*page.rect)
            # 去掉重叠区域
            total_area = unary_union(
                [
                    box(x1, y1, x2, y2).intersection(page_box)
                    for x1, y1, x2, y2 in bboxes
                ]
            ).area
            # 不去掉重叠区域
            # total_area = sum((x2 - x1) * (y2 - y1) for x1, y1, x2, y2 in bboxes)
            page_area = page_box.area
            if (ratio := total_area / page_area) > 0.8:
                self._logger.warning(
                    "多个图片组成一个页面，figures=%s,total_area=%.1f,page_area=%.1f,ratio=%.2f",
                    len(bboxes),
                    total_area,
                    page_area,
                    ratio,
                )
                return True
            return False

        debugger: Final = self._debugger.bind(page=kpage.number)
        page = doc[kpage.number - 1]
        # 如果需要获得xref，这里需要通过digest来比较，而不是简单的/Im1 do => /Im1 1 0  => xref=1
        # 因为底层的实现没有返回渲染图片指令的name，无法根据name再获得xref，就只能够通过digest比较了
        images: list[Any] = page.get_image_info()  # type: ignore
        if is_full_page([image["bbox"] for image in images]):
            return True
        
        def has_chars(bbox:BBox)->bool:
            chars = bbox.get(kpage.pdf_chars,ratio=0.8)
            return len(chars)>0
        figures: list[KPDFFigure] = []
        m = Matrix.lt_to_lb(kpage.size)
        small_images: list[Any] = []
        transparent_images: list[Any] = []
        no_chars_images:list[Any]=[]
        for image in images:
            # number = image["number"]
            # xref = image.get('xref',0)
            transparent = image["has-mask"]
            #bbox = image["bbox"]
            bbox=BBox.from_list(image["bbox"], matrix=m)
            if bbox.width <= 2 and bbox.height <= 2:
                small_images.append(image)
            elif transparent:
                # TODO 不去掉也可以的
                # 如果是透明的图片，也不要了，因为可能是普通的图，也可能是背景，
                # 当然也可能是ocr文字（概率比较低，不应该去掉）
                # 如果是背景，应该去掉，否则会认为是ocr文字，使用ocr处理，虽然也没有错，但是没有必要
                if has_chars(bbox):
                    # 这个例子使用带虚线的透明背景，应该去掉，因为不需要使用ocr
                    # local/cases/test/16-线由小图片组成-解析非常耗时.pdf 52页
                    transparent_images.append(image)
                    #但是，如果有些表格有图片的，这些图片就需要保留
                else:
                    # 这个例子多数字符都是使用图片，就需要使用ocr
                    # local/cases/test/文字都是小图片.pdf
                    no_chars_images.append(image)
            else:
                # TODO 如果希望使用原始图片的，可以获得width/height，transform（用来计算是否左右旋转图片了，或者翻转了，或者直接就保留这个）
                figure = KPDFFigure(
                    kpage,
                    BBox.from_list(image["bbox"], matrix=m).to_quad(),
                    transparent=transparent,
                )
                figures.append(figure)



        if len(small_images) > 0 or len(transparent_images) > 0:
            self._logger.warning(
                "第%s页,小图片=%s,有文字透明图片=%s",
                kpage.number,
                len(small_images),
                len(transparent_images),
            )
        
        #保留pdf图片的目的，只是为了通过ocr补充需要，所以，如果可能为文字的，就保留
        kpage.pdf_figures.clear()
        kpage.pdf_figures.extend(figures)
        kpage.pdf_figures.extend(no_chars_images)
        self._logger.warning('第%s页，图片=%s,没有文字遮挡的透明图片=%s',kpage.number,len(figures),len(no_chars_images))
        return False

    def _parse_paths(self,page:KPage,paths:Sequence[Any]):
        debugger = self._debugger.bind(page=page.number)
        raw_paths:Final=paths

        def filter1(paths:Sequence[Any])->list[Any]:
            new_paths:list[Any]=[]
            for path in paths:
                # TODO 有些线使用无数个点组成，还需要吗？
                # 现在不解析，原样保留，在处理表格的时候，需要再解析
                # 如：有些地图或者其他，有十几万条线的
                if path['isrect'] and path['alpha']>0:
                    #re通过fill来填充矩形，如果是很小的矩形，就是线，而边框很少用来作为表格线
                    #ml和ll等，通过stroke来画线
                    #kpage.pdf_paths.append(block)
                    #先处理了特殊的矩形合并？
                    new_paths.append(path)
                else:
                    #曲线或者斜线，去掉
                    pass
            return new_paths
        
        def filter2(paths:Sequence[Any]):
            #需要考虑特殊的文档有几十万条线的情况，避免解析太慢
            paths = list(paths)
            for vobj in page.vobjects:
                if vobj.is_any_text() or vobj.is_table():
                    #在文本框和表格区域的保留，或者页面页脚区域的（这两个也不重要）
                    #文本区域是考虑删除线或者下划线？
                    pass
                else:
                    vobj.bbox.expand(dx=2,dy=2).get(paths,ratio=0.5,remove=True)

            return paths
        
        def split(paths:list[Any]):
            line_paths:list[Any]=[]
            rect_paths:list[Any]=[]
            for path in paths:
                bbox = path['bbox']
                if bbox[2]-bbox[0]>=5 and bbox[3]-bbox[1]>=5:
                    if not path['stroked']:
                        rect_paths.append(path)
                else:
                    line_paths.append(path)
            return line_paths,rect_paths

        timer=utils.Timer.start()
        timer.mark('start filter')
        paths = filter1(paths)
        paths = filter2(paths)
        line_paths,rect_paths = split(paths)
        timer.mark('end filter')
        timer.mark('start merge')
        h_lines,v_lines = LineParser().parse(page,line_paths)
        page.pdf_lines.clear()
        page.pdf_lines.extend(h_lines)
        page.pdf_lines.extend(v_lines)

        #矩形的合并，目的是为了能够还原背景，但是这些是否非常复杂的计算
        page.pdf_rects.clear()
        page.pdf_rects.extend([])
        timer.mark('end merge')

        if debugger.allow('info'):
            debugger.print(f'第{page.number}页,耗时={timer.elapsed()}，原始路径={len(raw_paths)},line_paths={len(line_paths)},rect_paths={len(rect_paths)}，合并后=({len(h_lines)},{len(v_lines)})')
        
        if debugger.allow('draw'):
            #TODO 为了渲染这些线，还需要转换一下
            def draw_paths(paths:Sequence[Any]):
                img = page.image.copy()
                draw = PIL.ImageDraw.Draw(img)
                m = Matrix().scale(img.width / page.width, img.height / page.height)
                for path in paths:
                    x0, y0, x1, y1 = BBox.from_list(path["bbox"], matrix=m)
                    # color = path['color']
                    if path["stroked"]:
                        draw.line((x0, y0, x1, y1), fill=(0, 0, 255), width=2)
                    else:
                        draw.rectangle((x0, y0, x1, y1), fill=(255, 255, 0))
                return img
            lines=h_lines+v_lines
            page.draw(('page',None),('vobjects',page.vobjects),(f'raw paths={len(raw_paths)}',draw_paths(raw_paths)),(f'rect paths={len(rect_paths)}',draw_paths(rect_paths)),('rects',[]),(f'line paths={len(line_paths)}',draw_paths(line_paths)),(f'lines={len(h_lines)},{len(v_lines)}',lines),dir='debug/default/pymupdf')
        



class _Parser2:
    def _parse_texttrace(self, doc: pymupdf.Document, kpage: KPage):
        # get_texttrace() 按原始指令顺序返回，不重排
        # https://pymupdf.readthedocs.io/en/latest/functions.html#Page.get_texttrace
        # https://github.com/pymupdf/PyMuPDF/blob/main/src/__init__.py
        # 看源代码，没有应用page自身的rotation，而是先总是变成0，如果页面自身设置了rotation，就不一致了
        # 因为现在需要的是相对rotation后的解析

        # 来自原始的指令，没有做任何的处理
        # 1.如果一个cid对应多个unicode（连体字使用多个字符表示），返回多个chars
        # 2.没有考虑clip的影响
        # 3.没有考虑文字被覆盖
        # 4.没有考虑文字溢出区域（crop bbox）
        # 5.没有考虑文字归一化，如：部首文字
        # 6.

        debugger: Final = self._debugger.bind(page=kpage.number)

        def is_visible(span: Any) -> bool:
            if span["opacity"] == 0:
                # 0<=x<=1
                return False

            if span["type"] > 1:
                # 不可见，对应render=3
                return False

            return True

        def normalize_text(text: str):
            # 仅仅归一化特殊的字符，不归一化标点符号等
            # 没有被归一化，如：ff => ['f','f']
            # 1个glyph对应多个字符，如果直接返回多个字符，就无法做到一个字符对应一个XChar对象了，增加了后续处理的复杂度
            # 所以这里就尽量的归一化了，但是还是没有办法做的，所以就只能够取第一个字符了
            assert len(text) > 1
            from memect.pdf.chars import normalize, to_ligature

            s = to_ligature(text)
            if s != text:
                # 表示为连字符，多个变成1个
                return s
            # 不是连字符，可能是兼容字
            new_text: list[str] = []
            normalized = False
            for c in text:
                # 仅仅支持单个字符，没有处理组合字符
                c2 = normalize(c)
                if c != c2:
                    normalized = True
                new_text.append(c2)

            if normalized and len(set(new_text)) == 1:
                # 多个字符归一化为一个字符，如：
                #'⽓气' = '\u2f53\u6c14' => ['\u6c14','\u6c14'] => '气'
                return new_text[0]
            else:
                # 无法归一化，就使用第一个
                return text[0]

        def is_wingdings(font: str) -> bool:
            # wingdings,wingdings2,wingdings3
            return re.search(r"wingdings", font.lower()) is not None

        def is_symbol(font: str) -> bool:
            return re.search(r"symbol", font.lower()) is not None

        def handle_span(span: Any) -> list[KChar]:
            kchars: list[KChar] = []
            use_quad = False

            wingdings = is_wingdings(span["font"])
            # symbol = is_symbol(span['font'])
            flags = span["flags"]

            if wingdings:
                # 如果是这种字体，可能有几种情况：
                # cid没有对应的unicode
                # cid有对应的unicode，但是获得的unicode（然后在不同的字体上显示）和使用当前字体的glyph显示不一致
                # 因为这种字体是微软设计的，使用的unicode在0x20-0xff之间，如果直接使用其他的字体，显示不一致
                # 使用cid还是unicode，都不一定能够100%还原
                # 之前的做法是根据获得的unicode转化为普通字体的unicode（标准），也就是使用普通字体来获得相近的显示
                font = KFont.WINGDINGS
            elif flags & 8:
                font = KFont.MONOSPACE
            elif flags & 4:
                font = KFont.SERIF
            else:
                font = KFont.SANS_SERIF

            is_bold = bool(flags & 16)
            is_italic = bool(flags & 2)
            is_superscript = bool(flags & 1)

            color = KColor.from_list(
                span["color"],
                alpha=span["opacity"],
                is_float=True,
                colors=kpage.doc.colors,
            )

            # (1,0) or (0,1)
            dir_ = span["dir"]
            wmode = span["wmode"]

            # 暂时假设旋转都一样，页面使用的size都一样
            m = Matrix.lt_to_lb(kpage.size)
            chars = span["chars"]
            i = 0
            while i < len(chars):
                char = chars[i]
                unicode = char[0]
                cid = char[1]
                origin = char[2]
                bbox = BBox(*char[3])
                if use_quad:
                    quad = Quad.from_list(pymupdf.recover_char_quad(None, span, char))
                else:
                    quad = BBox(*char[3]).to_quad()

                quad = quad.transform(m)
                j = i + 1
                while j < len(chars):
                    c2 = chars[j]
                    if c2[1] == -1 and c2[3][2] - c2[3][0] == 0:
                        # 表示为连体字，或者部首，也就是一个cid对应多个字符，如：
                        #'\uFB00' => ff，pdf的cid2unicode返回2个字符，这个时候，但是显示的位置只有一个
                        # 要么分成2个字符，每个字符占据一点空间？
                        # 要么还原为一个连体字，仅仅使用一个字符
                        # 解决方案：
                        # 合并几个cid=-1
                        # 或者认为多数情况下cid=unicode，所以，如果cid为连体字符的unicode
                        # font.has_glyph(cid)
                        j += 1
                        # U+4E00 — U+9FFF
                    else:
                        break
                    # 如果原文有空格，就返回空格，没有就不返回，不会自动根据空间补充一个
                if j - i > 1:
                    # 连体字符或者部首+字或者多个兼容字
                    # 如果是连体字符，替换为一个，因为原文显示就是一个连体字符，但是复制为n个
                    # 如果是其他，仅仅选择最合适的一个，因为形似但是unicode不同
                    old_text = "".join([chr(c[0]) for c in chars[i:j]])
                    text = normalize_text(old_text)
                    self._logger.warning(
                        "cid对应多个字符，现在纠正：page=%s,from=%s,to=%s,unicode=0x%04X",
                        kpage.number,
                        old_text,
                        text,
                        ord(text),
                    )
                else:
                    # 对应单个文本，就不管了，返回什么就是什么
                    # 如果无法找到准确的
                    if wingdings:
                        # Windows 符号字体惯例：PUA 子区间 0xF000–0xF0FF
                        # 不还原了，因为也是在pua区间
                        # if 0xF000 <= unicode <= 0xF0FF:
                        # unicode=unicode & 0xff # 还原为原始字节 0x00–0xFF
                        # 0x20-0xff
                        self._logger.warning(
                            "wingdings的字符，page=%s,cid=0x%04x,unicode=0x%04X,bbox=%s",
                            kpage.number,
                            cid,
                            unicode,
                            bbox,
                        )
                    elif unicode == 0xFFFD:
                        # 65533，表示无效的unicode，有下面几种可能
                        # 1.cid没有对应的unicode，在pdf阅读工具上，就是无法复制（需要通过ocr）
                        # 2.cid有对应的unicode，但是为无效的，如："\u0000"
                        # local/cases/test/12-cid没有对应的unicode.pdf 第2页的箭头，就是第一种
                        # local/cases/test/11-有字符返回0000.pdf 第5页，为第二种，返回是“\u0000"
                        # 有些还是可以通过ocr获得
                        self._logger.warning(
                            "无效的字符，page=%s,font=%s,bbox=%s",
                            kpage.number,
                            span["font"],
                            bbox,
                        )
                    elif 0xE000 <= unicode <= 0xF8FF:
                        # pua:private user area，私有的区域
                        # BMP PUA0xE000 – 0xF8FF
                        # 补充PUA-A0xF0000 – 0xFFFFF
                        # 补充PUA-B0x100000 – 0x10FFFF
                        # 去掉这个字符，还是仍然保留
                        self._logger.warning(
                            "pua字符，page=%s,font=%s,cid=0x%04X,unicode=0x%04X,bbox=%s",
                            kpage.number,
                            span["font"],
                            cid,
                            unicode,
                            bbox,
                        )
                    else:
                        pass

                    text = chr(unicode)

                kchar = KChar(
                    kpage,
                    quad,
                    text=text,
                    font=font,
                    color=color,
                    bold=is_bold,
                    italic=is_italic,
                    source=CharSource.PDF,
                )
                if is_superscript:
                    kchar.subtype = "superscript"
                kchar.seqno = span["seqno"]
                kchars.append(kchar)
                i = j
            return kchars

        def eq(b1: Any, b2: Any):
            if b1 == b2:
                # TODO 转化为int比较？
                return True
            else:
                return False

        def filter_spans(spans: list[Any]):
            # 有些粗体字，pdf是使用2个span渲染出来的，这个函数没有做处理，page.get_text(...)做了
            # pymupdf根据渲染顺序，如果后面有图片等覆盖了span，会删除或者切开，如果一个span被分成3段
            # span=[span1,span2,span3] => span2被覆盖，就获得了[span1,span2]，这两个的seq是同一个
            # 在同一个span中，字符是按pdf的书写顺序的（可能合并多个Tj/TJ指令，只要属性一致）
            i = 0
            while i + 1 < len(spans):
                span1 = spans[i]
                span2 = spans[i + 1]
                if (
                    span1["type"] == 0
                    and span2["type"] == 1
                    and len(span1["chars"]) == len(span2["chars"])
                    and eq(span1["chars"][0][3], span2["chars"][0][3])
                    and eq(span1["chars"][-1][3], span2["chars"][-1][3])
                ):
                    # 当使用render=2的时候，fill+stroke，这里会返回2个span，一个是type=0,一个是type=1
                    # 所以，需要过滤掉
                    # render=0 fill  返回type=0
                    # render=1 stroke 返回type=1
                    # render=2 fill+stroke，这里分2个返回type=0,type=1
                    # render=3,invisible，返回type=3
                    # render=4,fill+clip，也就是仅仅保留文字区域，如：在一个背景中，返回type=0
                    # render=5,stroke+clip，返回type=1
                    # render=6，fill+stroke+clip，分2个返回type=0，type=1
                    # render=7，clip
                    if debugger.allow("info"):
                        debugger.print(
                            "删除stroke的span", [chr(c[0]) for c in span2["chars"]]
                        )
                    del spans[i + 1]
                    i += 1
                else:
                    i += 1

            i = 0
            while i < len(spans):
                if not is_visible(spans[i]):
                    if debugger.allow("info"):
                        debugger.print(
                            "删除不可见字符", [chr(c[0]) for c in spans[i]["chars"]]
                        )
                    del spans[i]
                else:
                    i += 1

        page: Final = doc[kpage.number - 1]
        chars: Final[list[KChar]] = []
        spans: Final[list[Any]] = page.get_texttrace()  # type: ignore
        filter_spans(spans)

        # 如果需要考虑旋转，需要使用这个，对所有坐标进行处理
        # 如果还有二次旋转，* pymupdf.Matrix(90)
        # 或者如下
        # page.set_rotation(page.rotation+kpage.rotation)
        mat = page.rotation_matrix
        for span in spans:
            span_chars = handle_span(span)
            print("====>>", [c.text for c in span_chars])
            chars.extend(span_chars)

        kpage.pdf_chars.clear()
        kpage.pdf_chars.extend(chars)

        if not kpage.pdf_chars:
            # 没有文字，就按图片解析，不管是什么样了
            self._logger.info("页面没有文字，不需要再继续,page=%s", kpage.number)
            return
        # 如果多个图片组成一个大图，一样按图片解析，不管图片是背景还是扫描（可能覆盖文字）
        if self._parse_figures(doc, kpage):
            return
        self._parse_paths(doc, kpage)

    def _parse_paths2(
        self,
        doc: pymupdf.Document,
        kpage: KPage,
    ):
        # ── 2. paths（已解析为 lines/rects/curves）────────

        # 有些页面有很多细小的线构造为直线，或者很多线来渲染文字
        # 或者地图这种（图片超大），有文字有无数的线，都不需要解析
        # 如何判断？
        # 1.没有表格，就不需要解析线了，缺点：还原生成docx会丢失页面页脚线，但是不重要，就总是认为有或者没有，另外没有矩形，获得不了某些区域的背景颜色
        # 2.或者如果是图片区域，就不要解析了

        # 3.有些文档的字是使用线条渲染而成（字体轮廓化）

        # 如果有数量太大的，就不解析了，因为可能为整页都是使用线来渲染文字
        # 或者为复杂的图形，即使有有边框表格，也不要这些线了，解析太耗时



        def count_page(page: pymupdf.Page) -> int:
            # 一个页面可能有多个内容流
            n = 0
            for xref in page.get_contents():
                raw = doc.xref_stream(xref)
                if not raw:
                    continue
                n += count_paths(raw)
            return n

        def clean_stream(raw: bytes) -> bytes:
            """清除字符串内容，避免 Tj/TJ 中误匹配"""
            # 去除 (...) 字符串，处理转义 \( \)
            clean = re.sub(rb"\((?:[^\\()]|\\.)*\)", b"()", raw)
            # 去除 [...] TJ 数组
            clean = re.sub(rb"\[(?:[^\[\]]*)\]", b"[]", clean)
            return clean

        def count_paths(stream: bytes):
            clean = clean_stream(stream)
            paint_re = re.compile(rb"(?<![\/\w])(S|s|B\*|B|b\*|b|f\*|f|F)(?![\w])")
            # moveto_re = re.compile(rb'(?<![\/\w])m(?![\w])')
            # rect_re   = re.compile(rb'(?<![\/\w])re(?![\w])')
            # clip_re   = re.compile(rb'(?<![\/\w])(W\*|W)(?![\w])')
            paint_ops = paint_re.findall(clean)
            # movetos   = moveto_re.findall(clean)
            # rects     = rect_re.findall(clean)
            # clips     = clip_re.findall(clean)
            return len(paint_ops)

        # 之所以需要线，只是在：
        # 有边框表格中，或者无边框表格中，或者页眉页脚线
        # 在测试中，即使15万条线，也只需要2.4秒，所以
        # 方案1: 获得所有的线，但是不做任何转换，在解析表格的需要的时候，再解析，页面页脚也是如此，如果线多的，
        # 方案2: 如果线多就抛弃了
        # 目前，为了支持后续可以找到下划线/删除线/表格线/页眉页脚线，就原样保留
        page = doc[kpage.number - 1]

        # 0表示不快速计算，否则表示如果线的数量超过这个值，就都抛弃了
        threshold = 0
        # 在15万条线的下，这个需要0.76秒，另外2个方法需要2.5秒
        total = -1
        if threshold > 0:
            total = count_page(page)
            if total > threshold:
                # 线太多了，就不不要了，因为可能是线渲染的文字，也可能是线渲染的图片
                # 也可能是线渲染的地图
                self._logger.warning(
                    "第%s页的线条数为:%s，超过阀值:%s,抛弃线条",
                    kpage.number,
                    total,
                    threshold,
                )
                return
        # 现在就不考虑clip(extended=True)的影响了，只是获得信息，也需要自己处理
        # drawings = page.get_cdrawings()
        # 这个比上一个需要多一倍的时间，区别就是使用了Point，Rect，Quad对象
        # drawings = page.get_drawings(True)
        # 只是保存，不解析，用到的时候再处理
        #kpage.pdf_drawings = page.get_cdrawings()  # type: ignore
        #print("========>paths", total, len(kpage.pdf_drawings))

    def _use_paths(self, drawings: Sequence[Any]):
        clip_paths = []
        for path in drawings:
            # path['type'] in ('f','s','fs','clip','group')
            # 使用了extended=True，就多了W,W*，也就是clip
            # clip1 (level1) => clip1
            # clip2 (level1) => clip1&clip2
            # clip3 (level1) => clip1&clip2&clip3
            # q
            #   clip4(level2)=> clip1&clip2&clip3&clip4
            #     re(level3)
            #     re(levle3)
            #    group(level3) => 对应一个有"/Group"的form，可以设置一个渲染的属性，然后先统一渲染
            #      re(level4)
            #      re(level4)
            #   如果是没有group的from
            #   re(level3)
            #   re(level3)
            # Q
            # clip5(level1) => clip1&cli2&clip3&clip5
            #   re(level2)
            #   re(level2)

            clip_path = None
            while clip_paths:
                if clip_paths[-1]["level"] < path["level"]:
                    clip_path = clip_paths[-1]
                    break
                del clip_paths[-1]
            if path["type"] == "clip":
                clip_paths.append(path)
            else:
                if True:
                    if clip_path:
                        print(
                            "===============>clippath",
                            clip_path["scissor"],
                            path["rect"],
                            (clip_path["level"], path["level"]),
                        )
                    else:
                        print(
                            "==>rect",
                            path.get("seqno", -1),
                            path["rect"],
                            path["level"],
                        )
                if clip_path:
                    # 使用这个区域裁剪
                    scissor = clip_path["scissor"]

                else:
                    # 没有，不用裁剪
                    pass

type _Rect = tuple[float, float, float, float]
class LineParser:
    """pdf的线可能是由很多个小矩形/小点等组成，这里合并为水平线和垂直线"""
    def __init__(
        self,
        *,
        max_width: float = 2,
        max_height: float = 2,
        h_threshold: float = 1,
        v_threshold: float = 1,
        min_length: float = 5,
        gap_threshold: float = 3,
    ):
        super().__init__()
        self._max_height = max_height  # 水平线的最大宽度
        self._max_width = max_width  # 垂直线的最大宽度
        self._h_threshold = h_threshold  # 水平线y坐标容差
        self._v_threshold = v_threshold  # 垂直线x坐标容差
        self._min_length = min_length  # 最小线长度
        self._gap_threshold = gap_threshold  # 线段间隙阈值，超过此值则分开

    def parse(self, page: KPage,paths:Sequence[Any]) -> tuple[list[KLine], list[KLine]]:
        # 线可能为很多个点，或者很多小段的线组成
        # 而且还包含文字的下划线，删除线
        # 第一步是先解析表格线
        # 然后去掉下划线/删除线？

        # 使用来自pymupdf的对象，还需要转换，为了简化实现，颜色后续再补充

        # https://pymupdf.readthedocs.io/en/latest/textpage.html#TextPage.extractRAWDICT
        # {'type':3,'color':0xffffff,'bbox':[]}
        # 现在采用按需解析，所以，一开始不会解析线，还是保留原始的数据结构

        if not paths:
            return ([], [])
        # TODO 后续去掉这个函数
        from pymupdf import sRGB_to_rgb

        def get_color(bbox: Any):
            # 如果需要计算复杂，获得所有的颜色取平均值？这个意义又不大
            for p in paths:
                if p["bbox"] is bbox:
                    color = p["color"]
                    if isinstance(color, int):
                        rgb = sRGB_to_rgb(color)
                    else:
                        rgb = color
                    return KColor.from_list(rgb, is_float=False, colors=page.doc.colors)
            return KColor.BLACK

        # 这里可以使用左下角或者左上角的坐标，都不影响
        h_groups, v_groups = self._parse([p["bbox"] for p in paths])

        def make_line(
            bboxes: Sequence[Any],
            use_avg: bool = False,
            is_h: bool = True,
            color: KColor = KColor.BLACK,
        ):
            if is_h:
                x0, y0, x1, y1 = 0, 1, 2, 3
            else:
                x0, y0, x1, y1 = 1, 0, 3, 2

            bbox = BBox.join(bboxes)
            if use_avg:
                # 使用平均值的方式
                line_width = sum(b[y1] - b[y0] for b in bboxes) / len(bboxes)
                y = sum((b[y1] + b[y0]) / 2 for b in bboxes) / len(bboxes)
            else:
                # 使用合并的方式
                line_width = bbox[y1] - bbox[y0]
                y = (bbox[y1] + bbox[y0]) / 2
            # 转换为左下角为原点
            bbox = bbox.set((y0, y), (y1, y)).transform(page.to_lb())
            return KLine(page, bbox, color=color, width=line_width)

        def make_lines(groups: list[list[Any]], is_h: bool = True):
            lines: list[KLine] = []
            for group in groups:
                # 认为在同一组都是相同的颜色
                color = get_color(group[0])
                line = make_line(group, is_h=is_h, color=color)
                lines.append(line)
            return lines

        h_lines = make_lines(h_groups, is_h=True)
        v_lines = make_lines(v_groups, is_h=False)
        return (h_lines, v_lines)

    def _parse(
        self, rects: Sequence[_Rect]
    ) -> tuple[list[list[_Rect]], list[list[_Rect]]]:
        """解析，然后返回水平线和垂直线(h_lines,v_lines)，为了后续使用，这里没有对线合并，只是分组"""
        if not rects:
            return [], []

        rects_list = list(rects)
        h_lines = self._merge_lines(rects_list, "h")
        v_lines = self._merge_lines(rects_list, "v")
        return h_lines, v_lines

    def _merge_lines(
        self, rects: list[_Rect], direction: Literal["h", "v"]
    ) -> list[list[_Rect]]:
        """合并线段"""
        filtered_rects: list[_Rect] = []
        if direction == "h":
            x0, y0, x1, y1 = 0, 1, 2, 3
            max_thickness = self._max_height
            coord_threshold = self._h_threshold
        else:
            x0, y0, x1, y1 = 1, 0, 3, 2
            max_thickness = self._max_width
            coord_threshold = self._v_threshold

        for rect in rects:
            if rect[y1] - rect[y0] <= max_thickness:
                filtered_rects.append(rect)
            else:
                # print('===>filter',direction,rect,max_thickness)
                pass

        if not filtered_rects:
            return []

        def cmp(a: _Rect, b: _Rect) -> int:
            cy1 = (a[y0] + a[y1]) / 2
            cy2 = (b[y0] + b[y1]) / 2
            if abs(cy1 - cy2) <= coord_threshold:
                if a[x0] <= b[x0]:
                    return -1
                else:
                    return 1
            elif cy1 < cy2:
                return -1
            else:
                return 1

        # lists.sort(filtered_rects,cmp=cmp)
        filtered_rects.sort(key=lambda r: (r[y0] + r[y1]) / 2)

        groups: list[list[_Rect]] = []
        current_group: list[_Rect] = [filtered_rects[0]]

        for rect in filtered_rects[1:]:
            # 使用第一个避免累积误差
            # 如果遇到的都是点，如：（1*1），当处理水平线的时候，可能就线
            first_rect = current_group[0]
            cy1 = (first_rect[y0] + first_rect[y1]) / 2
            cy2 = (rect[y0] + rect[y1]) / 2

            if abs(cy1 - cy2) <= coord_threshold:
                current_group.append(rect)
            else:
                if current_group:
                    groups.append(current_group)
                current_group = [rect]

        if current_group:
            groups.append(current_group)

        # 合并每组中的线段
        lines: list[list[_Rect]] = []
        for group in groups:
            merged_lines = self._merge_group(group, direction)
            lines.extend(merged_lines)
        return lines

    def _merge_group(
        self, group: list[_Rect], direction: Literal["h", "v"]
    ) -> list[list[_Rect]]:
        """合并同一方向的线段组，处理间隙分割"""
        if not group:
            return []

        # 索引映射：统一处理水平和垂直线
        if direction == "h":
            # 水平线：x0,y0,x1,y1 = 0,1,2,3
            x0, y0, x1, y1 = 0, 1, 2, 3
        else:
            # 垂直线：交换x和y的索引，x0,y0,x1,y1 = 1,0,3,2
            x0, y0, x1, y1 = 1, 0, 3, 2

        # 按主方向坐标排序
        group.sort(key=lambda r: r[x0])

        # 分割成连续的子组
        continuous_groups: list[list[_Rect]] = []
        current_subgroup: list[_Rect] = [group[0]]

        last_position = group[0][x1]
        for rect in group[1:]:
            # 检查间隙：当前线段的起点与前一个线段的终点的距离
            gap = rect[x0] - last_position
            if gap <= self._gap_threshold:
                # 粘连或间隙小，合并
                current_subgroup.append(rect)
                last_position = max(last_position, rect[x1])
            else:
                # 间隙过大，分开
                continuous_groups.append(current_subgroup)
                current_subgroup = [rect]
                last_position = rect[x1]

        continuous_groups.append(current_subgroup)

        # TODO 如果是由点组成的线，每一个点都很小，在水平线的时候，可能存在很短的垂直线
        def is_valid(group: list[_Rect]) -> bool:
            start = min(b[x0] for b in group)
            end = max(b[x1] for b in group)
            return end - start >= self._min_length

        return [g for g in continuous_groups if is_valid(g)]
        # return continuous_groups

class RectParser:
    def __init__(self):
        super().__init__()
    
    def parse(self,page:KPage,rects:Sequence[Any]):
        """合并矩形"""
        #有些使用多个小矩形表示一个大矩形，如：
        #--r1--
        #|     |
        #r2-r3-r4
        #|     |
        #--r5--
        pass