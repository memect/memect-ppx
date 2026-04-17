import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Final, Mapping, NotRequired, Sequence, TypedDict

import PIL
import PIL.Image
import PIL.ImageDraw
from pydantic import Field

from memect.base import images, lists
from memect.base.bbox import BBox, Quad
from memect.base.debug import XDebugger
from memect.base.matrix import Matrix
from memect.base.utils import MyBaseModel
from memect.pdf.base import (
    CharSource,
    KChar,
    KColor,
    KDocument,
    KFont,
    KFormula,
    KObject,
    KPage,
    KSpan,
    KTextbox,
    OCRMode,
    PageType,
    ParseMode,
    VObject,
)
from memect.pdf.commons import FileInfo
from memect.pdf.model import ModelExecutor, ModelManager
from memect.pdf.sort import Sorter

from .footer import PageFooterParser
from .footnote import PageFootnoteParser
from .header import PageHeaderParser
from .pdf import PdfParser, PdfParserArgs

from .table.parser import TableParser
from .tree.parser import TreeParser


class _OCRSpan(TypedDict):
    text: str
    bbox: NotRequired[Sequence[float]]
    quad: NotRequired[Sequence[Sequence[float]]]
    score: float


class _OCRResult(TypedDict):
    # number: int
    width: int
    height: int
    spans: list[_OCRSpan]


class DefaultParserArgs(MyBaseModel):
    layout: str = "layout"
    ocr: str = "ocr"
    formula: str = "formula"
    llm: str = "llm"
    # image: ImageParserArgs = Field(default_factory=ImageParserArgs)
    pdf: PdfParserArgs = Field(default_factory=PdfParserArgs)


class DefaultParser:
    """使用layout+pdf+ocr+llm的方式解析文档"""

    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")

    def __init__(self,manager:ModelManager, args: DefaultParserArgs | Mapping[str, Any] | None = None):
        super().__init__()
        self._args: Final = DefaultParserArgs.create(args)
        self._layout_model: Final = manager.get(self._args.layout)
        self._layout_key: Final = "cache/default/layout"
        self._formula_model: Final = manager.get(self._args.formula)
        self._formula_key: Final = "cache/default/formula"
        self._ocr_model: Final = manager.get(self._args.ocr)
        self._ocr_key: Final = "cache/default/ocr"
        # self._llm_model:Final=ModelExecutor.get(self._args.llm)
        # self._llm_key:Final='cache/default/llm'

        self._pdf_parser: Final = PdfParser(self._args.pdf)
        self._table_parser: Final = TableParser(manager)

        self._header_parser: Final = PageHeaderParser()
        self._footer_parser: Final = PageFooterParser()
        self._footnote_parser: Final = PageFootnoteParser()

        self._tree_parser: Final = TreeParser()

    def parse(self, doc: KDocument):
        self._parse_layout(doc)
        # pdf：解析pdf的指令，获得chars/lines/rects/figures，主要是chars
        self._parse_pdf(doc)
        # ocr：使用传统的模型或者llm模型，获得spans，以后可以辅助线？
        self._parse_ocr(doc, method=1)
        # 得到了chars/lines/rects/figures，就开始填充对象
        self._parse_texts(doc)
        self._parse_figures(doc)
        self._parse_formulas(doc)
        self._parse_tables(doc)

        if doc.params.mode == ParseMode.PPT:
            # 如果是按ppt，就不需要解析页面页脚等了
            self._sort_ppt(doc)
        else:
            # 按页解析即可
            self._header_parser.parse(doc)
            self._footer_parser.parse(doc)
            self._footnote_parser.parse(doc)
            self._sort(doc)
            # TODO 如果有些排版是使用大表格的，这里也尝试还原？
            if doc.params.mode == ParseMode.TREE:
                # 按页然后再解析章节树
                self._tree_parser.parse(doc)

    def _parse_layout(self, doc: KDocument):
        """版面分析"""
        debugger = self._debugger.bind()
        self._layout_model.parse(doc, self._layout_key)
        for page in doc.working_pages:
            page.load_layout(page.cache.pop(self._layout_key))
            if debugger.allow("draw", page=page.number):
                # 合并两个图片为一个？left|right
                page.draw(
                    ("raw_vobjects", page.raw_vobjects),
                    ("vobjects", page.vobjects),
                    dir="debug/default/layout",
                )

    def _parse_pdf(self, doc: KDocument):
        # 如果是pdf文件，先使用pdf获得
        # chars,figures,lines,rects
        if not doc.is_pdf():
            # 如果不是pdf文件，不需要使用pdf解析
            return

        if doc.params.ocr == OCRMode.YES:
            # 如果指定强制使用ocr
            return

        # 解析pdf页面，根据指令获得chars/lines/rects/figures
        debugger = self._debugger.bind()
        self._pdf_parser.parse(doc)
        for page in doc.working_pages:
            if debugger.allow("draw", page=page.number):
                page.draw(
                    ("vobjects", page.vobjects),
                    ("chars", page.pdf_chars),
                    (f"lines={len(page.pdf_lines)}",page.pdf_lines),
                    (f"rects={len(page.pdf_rects)}",page.pdf_rects),
                    dir="debug/default/pdf",
                    show_type=False,
                )

    def _parse_ocr(self, doc: KDocument, *, max_workers: int = 0, method: int = 1):
        debugger = self._debugger.bind()

        def get_bbox(objs: Sequence[KObject]) -> BBox:
            return BBox.join([obj.bbox for obj in objs])

        def get_image_objects(
            page: KPage,
        ) -> list[tuple[VObject, Sequence[Quad | BBox]]]:
            """获得纯图片解析下，需要识别文本的对象"""
            objs: list[tuple[VObject, Sequence[Quad | BBox]]] = []
            for vobj in page.vobjects:
                if vobj.is_any_text() or vobj.is_table():
                    objs.append((vobj, [vobj.quad]))
            return objs

        def get_pdf_objects(page: KPage) -> list[tuple[VObject, Sequence[Quad | BBox]]]:
            # pdf解析了部分字符串，这里再判断是否需要，仅仅处理下面2种情况
            # 1.原文都是图片/或者使用线来渲染文字，没有pdf字符，直接使用ocr解析
            # 2.原文有字符，部分字符使用了图片（特殊的文字效果）
            objs: list[tuple[VObject, Sequence[Quad | BBox]]] = []
            for vobj in page.vobjects:
                if vobj.is_any_text() or vobj.is_table():
                    # 可以简单使用bbox，如果需要支持倾斜等，使用vobj.quad()
                    chars = vobj.bbox.get(page.pdf_chars, ratio=0.5)
                    figures = vobj.bbox.get(page.pdf_figures, ratio=0.8)
                    chars = [c for c in chars if not c.text.isspace()]
                    # 目前ocr无法识别出个别无效的字符，特别是图标化的字符，如：无序列表的第一个字符
                    invalid_chars = [c for c in chars if not c.is_valid()]

                    if vobj.is_table():
                        # TODO 如果为表格，有些图片为单元格的图片，有些为文字图片（也就是部分文字使用图片表示）
                        # 如何区分，做个简单的假设，单元格的图片比较大，而文字图片比较小
                        figures = [
                            figure for figure in figures if figure.bbox.height <= 20
                        ]

                    if len(chars) == 0:
                        # 如果没有字符，就使用ocr（也有可能原文是一个logo或者其他，不管了）
                        objs.append((vobj, [vobj.quad]))
                    elif len(figures) > 0 or len(invalid_chars) > 0:
                        # 就使用整个区域？
                        # 包含图片，该图片可能是文字内容（特殊的效果，或者其他）
                        # 如果在表格中，可能为普通的图片，也可能为特殊效果的文字
                        # 有些图片为单个字符，为了避免截图过小，合并连续的多个图片
                        # groups = merge_figures(figures+invalid_chars)
                        groups = merge_bboxes(figures + invalid_chars)
                        objs.append((vobj, [get_bbox(group) for group in groups]))
                        # TODO 可以把无效的pdf字符也删除了？或者后续直接1对1匹配即可？
                        lists.remove(page.pdf_chars, invalid_chars)
                        self._logger.warning(
                            "第%s页，文本块中包含图片/或者无效字符，使用ocr辅助,figures=%s,invalid_chars=%s",
                            page.number,
                            len(figures),
                            len(invalid_chars),
                        )
                    else:
                        # 如果只有部分字符，其他的使用线来渲染，该如何？
                        # 判断有很多曲线吗？
                        pass

            return objs

        def merge_bboxes(objs: Sequence[KObject]):
            # 需要排序吗？不需要，按书写顺序？

            # local/cases/test/8-局部文字为图片.pdf 第47页，"(%)" 这3个字符，两个括号为图片，中间的为pdf字符
            # 为了避免仅仅截图"(",")"，导致ocr识别不准确，合并在一起，这样，当和pdf字符合并的时候，可能就重叠了
            # 要么去掉ocr字符，要么去掉pdf字符，去掉pdf字符更好，因为ocr字符的bbox计算的不够准确
            objs = sorted(objs, key=lambda figure: figure.bbox.x0)
            groups: list[list[KObject]] = []
            i = 0
            while i < len(objs):
                j = i + 1
                groups.append([objs[i]])
                while j < len(objs):
                    f1 = groups[-1][-1]
                    f2 = objs[j]
                    if (
                        -3 <= f2.bbox.x0 - f1.bbox.x1 <= 10
                        and f1.bbox.over("y", f2.bbox, d=5)
                        and abs(f1.bbox.height - f2.bbox.height) <= 10
                    ):
                        groups[-1].append(f2)
                        del objs[j]
                    elif f2.bbox.x0 - f1.bbox.x1 >= 10:
                        break
                    else:
                        j += 1
                i += 1

            return groups

        def handler(page: KPage, method: int = 1):
            if doc.is_image() or doc.params.ocr == OCRMode.YES:
                # 如果强制使用ocr（如：pdf）或者本身就是图片
                objs = get_image_objects(page)
                # 表示该页面使用了ocr解析
                page.type = PageType.IMAGE
            elif doc.is_pdf() and doc.params.ocr == OCRMode.AUTO:
                # 获得需要ocr的区域
                objs = get_pdf_objects(page)
                if len(objs) > 0:
                    # 至少部分文字来自ocr
                    page.type = PageType.IMAGE
                else:
                    # 纯pdf
                    page.type = PageType.PDF
            else:
                page.type = PageType.PDF
                objs = None

            if not objs:
                return None

            # 转换坐标为左上角
            m = page.to_lt(page.image.size)
            items: list[Any] = []
            if method == 1:
                # 1. 合并在一个页面中识别，然后识别完成后，再分离chars
                bboxes: list[BBox] = []
                quads: list[Quad] = []
                for vobj, areas in objs:
                    for area in areas:
                        if isinstance(area, Quad):
                            quads.append(area.transform(m))
                        else:
                            bboxes.append(area.transform(m))
                img = images.make_image(page.image, quads=quads, bboxes=bboxes)
                # 这种做法结果设置到page.cache
                items.append((img, page.cache))
                page.debug["ocr_image"] = img
                page.cache["ocr_vobjects"] = [item[0] for item in objs]
            else:
                # 2. 每个对象单独识别，合适llm，传统的也可以，但是需要大图片，直接小图片det不准确
                for vobj, areas in objs:
                    bboxes: list[BBox] = []
                    quads: list[Quad] = []
                    for area in areas:
                        if isinstance(area, Quad):
                            quads.append(area.transform(m))
                        else:
                            bboxes.append(area.transform(m))
                    # 直接使用小图片，det不准确
                    # img = page.crop(vobj.quad)
                    img = images.make_image(page.image, quads=quads, bboxes=bboxes)
                    items.append((img, vobj.cache))
                    vobj.debug["ocr_image"] = img

            return items

        def make_span(page: KPage, span: Any, m: Matrix) -> KSpan:
            text = span["text"]
            score = span["score"]
            # TODO 分数太低的不要？
            if "quad" in span:
                quad = Quad.from_list(span["quad"], matrix=m)
                # TODO 在ocr识别中，即使为标准的印刷体的文字，识别的quad也经常有轻微的倾斜（旋转）
                # 所以可以旋转回来？
                angle = quad.angle
                if abs(angle) < 5:
                    quad = quad.rotate(-angle)
            else:
                quad = BBox.from_list(span["bbox"], matrix=m).to_quad()

            is_vertical = False
            # 如果是中文*1.5，如果是英文*2?
            if len(text) > 1 and quad.height >= 2 * quad.width:
                is_vertical = True
            chars: list[KChar] = []

            # TODO 间距过大的情况如何处理，如："A    B" => "AB"，就会导致A和B的bbox计算的间距过大
            weights: list[float] = []
            for c in text:
                if c.isascii():
                    weights.append(1)
                else:
                    weights.append(2)

            for c, q in zip(
                text, quad.split("y" if is_vertical else "x", weights=weights)
            ):
                char = KChar(
                    page,
                    quad=q,
                    text=c,
                    font=KFont.OCR,
                    color=KColor.BLACK,
                    source=CharSource.OCR,
                )
                chars.append(char)
            return KSpan(page, quad, chars=chars, score=score)

        def parse_page1(page: KPage):
            # page.ocr_spans.clear()
            ocr_result: _OCRResult = page.cache.pop(self._ocr_key, None)
            ocr_objects: list[VObject] = page.cache.pop("ocr_vobjects", [])
            ocr_image: PIL.Image.Image = page.debug.pop("ocr_image", None)
            # 表示没有使用ocr解析
            if not ocr_result:
                return
            ocr_chars: list[KChar] = []
            ocr_spans: list[KSpan] = []
            m = Matrix.lt_to_lb((ocr_result["width"], ocr_result["height"]), page.size)
            for span_obj in ocr_result["spans"]:
                span = make_span(page, span_obj, m)
                ocr_spans.append(span)
                ocr_chars.extend(span.chars)

            # page.ocr_spans.extend(ocr_spans)

            if debugger.allow("draw", page=page.number):
                page.draw(
                    ("vobjects", page.vobjects),
                    ("ocr_image", ocr_image),
                    ("chars", ocr_chars),
                    show_type=False,
                    dir="debug/default/ocr",
                )

            # 第一次先分配
            for vobj in ocr_objects:
                vobj.ocr_chars.clear()
                if ocr_chars:
                    chars = vobj.bbox.get(ocr_chars, ratio=0.7)
                    vobj.ocr_chars.extend(chars)
                    lists.remove(ocr_chars, chars)

            # 对于少部分在区域内的
            for vobj in ocr_objects:
                if not ocr_chars:
                    break
                chars = vobj.bbox.get(ocr_chars, ratio=0.4)
                vobj.ocr_chars.extend(chars)
                lists.remove(ocr_chars, chars)

            if len(ocr_chars) > 0:
                # 识别的字符无法分配？不应该的
                self._logger.warning(
                    "ocr识别的字符无法完全分配，剩余:%s",
                    [(c.text, c.bbox) for c in ocr_chars],
                )

            remove_overlapped_chars(page, ocr_spans)

        def parse_page2(page: KPage):
            # page.ocr_spans.clear()
            i = 0
            ocr_spans: list[KSpan] = []
            for vobj in page.vobjects:
                ocr_result: _OCRResult = vobj.cache.pop(self._ocr_key, None)
                ocr_image: PIL.Image.Image = vobj.debug.pop("ocr_image", None)
                # 表示没有使用ocr解析
                if not ocr_result:
                    continue

                m = Matrix.lt_to_lb(
                    (ocr_result["width"], ocr_result["height"]), page.size
                )
                ocr_chars: list[KChar] = []

                for span_obj in ocr_result["spans"]:
                    span = make_span(page, span_obj, m)
                    ocr_chars.extend(span.chars)
                    ocr_spans.append(span)

                vobj.ocr_chars.clear()
                vobj.ocr_chars.extend(ocr_chars)
                if debugger.allow("draw", page=page.number):
                    page.draw(
                        ("ocr_image", ocr_image),
                        ("ocr_chars", ocr_chars),
                        show_type=False,
                        file=f"debug/default/ocr/{page.number}-{i + 1}.png",
                    )

                i += 1

            remove_overlapped_chars(page, ocr_spans)

        def remove_overlapped_chars(page: KPage, ocr_spans: Sequence[KSpan]):
            # local/cases/test/8-局部文字为图片.pdf 第47页，"(%)" 这3个字符，两个括号为图片，中间的为pdf字符
            # 为了避免仅仅截图"(",")"，导致ocr识别不准确，合并在一起，这样，当和pdf字符合并的时候，可能就重叠了
            # 要么去掉ocr字符，要么去掉pdf字符，去掉pdf字符更好，因为ocr字符的bbox计算的不够准确

            if not page.pdf_chars:
                return

            for span in ocr_spans:
                chars = span.bbox.get(page.pdf_chars, ratio=0.8)
                lists.remove(page.pdf_chars, chars, use_is=True)
                if len(chars) > 0:
                    self._logger.warning(
                        "page=%s，清除和ocr字符串重叠的pdf字符,ocr_span=%s,pdf_chars=%s",
                        page.number,
                        (span.bbox, span.text),
                        [c.text for c in chars],
                    )

        # 通过模型获得文本
        self._ocr_model.parse(
            doc,
            self._ocr_key,
            multi=method == 2,
            handler=lambda page: handler(page, method),
        )
        if method == 1:
            # 解析为对象
            self._do(parse_page1, doc.working_pages, max_workers)
        else:
            # 解析为对象
            self._do(parse_page2, doc.working_pages, max_workers)

    def _parse_texts(self, doc: KDocument, max_workers: int = 0):
        debugger = self._debugger.bind()
        verbose = True

        def make_textbox(page: KPage, vobj: VObject):
            if not vobj.is_any_text():
                return
            # TODO 补充行内公式/行内图片
            pdf_chars = vobj.bbox.get(page.pdf_chars, ratio=0.6)
            ocr_chars = vobj.ocr_chars
            chars = pdf_chars + ocr_chars
            if not chars:
                return
            # TODO 如果包含有ocr chars，全部或者部分，可以调整一下ocr chars的bbox，对齐和美观（因为ocr识别
            # 即使在同一行，同样的字体，大小等，识别出来的bbox还是不一定一致）
            tb = KTextbox.from_objects(chars)
            tb.vobject = vobj
            # TODO 更新吗？
            tb.bbox = BBox.join([c.bbox for c in chars])
            page.objects.append(tb)

            if verbose and debugger.allow("info", page=page.number):
                with debugger.group("textbox"):
                    for tl in tb.lines:
                        print(tl.text)

        def parse_page(page: KPage):
            for vobj in page.vobjects:
                make_textbox(page, vobj)

        self._do(parse_page, doc.working_pages, max_workers=max_workers)

    def _parse_figures(self, doc: KDocument, max_workers: int = 0):
        def parse_page(page: KPage):
            for vobj in page.vobjects:
                if (
                    vobj.is_figure()
                    or vobj.is_seal()
                    or vobj.is_chart()
                    or vobj.is_code()
                ):
                    figure = page.make_figure(vobj.quad, add=True)
                    if figure is not None:
                        figure.vobject = vobj
                        figure.subtype = str(vobj.type)

        self._do(parse_page, doc.working_pages, max_workers)

    def _parse_formulas(self, doc: KDocument, max_workers: int = 0):

        def parse_page(page: KPage):
            for vobj in page.vobjects:
                if vobj.is_any_formula():
                    formula = page.make_formula(
                        vobj.quad, add=True, inline=vobj.is_inline_formula()
                    )
                    if formula is not None:
                        formula.vobject = vobj

        def parse_latexs():
            if not doc.params.formula:
                return

            def get_formulas(page: KPage):
                formulas: list[Any] = []
                for obj in page.objects:
                    if isinstance(obj, KFormula):
                        # 当使用llm模型，需要传递{"task":"formula"}
                        info = FileInfo(file=obj.fullpath, params={"task": "formula"})
                        formulas.append((info, obj.cache))
                return formulas

            self._formula_model.parse(doc, self._formula_key, handler=get_formulas)
            for page in doc.working_pages:
                for obj in page.objects:
                    if isinstance(obj, KFormula):
                        #来自其他模型，返回的是:{"latex":""}
                        result = obj.cache.pop(self._formula_key)
                        if 'latex' in result:
                            text = result['latex']
                        else:
                            #来自llm的返回的是{"text":""}
                            text = result['text']
                            text=KFormula.normalize(text)
                        obj.latex=text

        # 先获得公式对象+截图
        self._do(parse_page, doc.working_pages, max_workers)
        # 获得latex
        parse_latexs()

    def _parse_tables(self, doc: KDocument, max_workers: int = 0):
        self._table_parser.parse(doc, max_workers=max_workers)

    def _do(
        self, fn: Callable[[KPage], None], pages: Sequence[KPage], max_workers: int = 0
    ):
        if max_workers == 0:
            for page in pages:
                fn(page)
        else:
            # 在free-threaded后才真正使用多核心
            with ThreadPoolExecutor(
                max_workers, thread_name_prefix=fn.__name__
            ) as executor:
                for _ in executor.map(fn, pages):
                    pass

    def _sort(self, doc: KDocument, max_workers: int = 0):

        def parse_page(page: KPage):
            page.objects.sort(key=lambda obj: obj.bbox.y1, reverse=True)

        self._do(parse_page, doc.working_pages, max_workers=max_workers)

    def _sort_ppt(self, doc: KDocument, max_workers: int = 0):
        def parse_page(page: KPage):
            Sorter.sort(page.objects)

        self._do(parse_page, doc.working_pages, max_workers=max_workers)


class ReadOrder:
    def __init__(self):
        super().__init__()

    def sort(self, doc: KDocument):
        pass
