from concurrent.futures import ThreadPoolExecutor
import logging
from typing import Callable, Final, Sequence

from memect.base import lists, strs
from memect.base.bbox import BBox
from memect.base.pattern import XPattern
from memect.pdf.base import (
    KDocument,
    KLine,
    KPage,
    KText,
    KTextline,
    OCRMode,
    TableMode,
    VObject,
    VObjectType,
)
from memect.pdf.default.table.wbk import WBKMode
from memect.pdf.default.table.ybk import YBKMode
from memect.pdf.model import ModelManager


class TableParser:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self, manager: ModelManager):
        super().__init__()
        self._manager: Final = manager

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

    def parse(self, doc: KDocument, max_workers: int = 0):
        self._fix(doc)
        if doc.params.ocr==OCRMode.NO:
            #如果不使用ocr，仅仅pdf，那么，当表格原文就是图片的时候，一样作为图片
            self._parse_no_ocr(doc,max_workers=max_workers)
        
        if doc.params.table == TableMode.NO:
            # 不用解析表格，全部作为图片
            self._parse_as_figures(doc, max_workers=max_workers)
        # elif doc.params.table == TableMode.LLM:
        # self._parse_llm(doc, max_workers=max_workers)
        elif doc.params.table == TableMode.YBK:
            # 全部按有边框
            self._parse_ybk(doc, max_workers=max_workers)
        elif doc.params.table == TableMode.WBK:
            # 全部按无边框
            self._parse_wbk(doc, max_workers=max_workers)
        elif doc.params.table == TableMode.AUTO:
            self._parse_auto(doc, max_workers=max_workers)
        else:
            raise ValueError(f"不支持的表格mode={doc.params.table}")

    
    def xparse(self,doc:KDocument,max_workers:int=0):
        """跨页/跨列表格合并的预处理"""
        from .wbk import XParser
        XParser(self._manager).parse(doc)
    
    def _parse_as_figures(self, doc: KDocument, *, max_workers: int = 0):
        def parse_page(page: KPage):
            for vobj in page.vobjects:
                if vobj.is_table():
                    figure = vobj.make_figure(dx=2, dy=2)
                    page.objects.append(figure)

        self._do(parse_page, doc.working_pages, max_workers=max_workers)
    
    def _parse_no_ocr(self,doc:KDocument,*,max_workers:int=0):
        def parse_page(page: KPage):
            for i,vobj in enumerate(page.vobjects):
                if vobj.is_table():
                    for figure in page.pdf_figures:
                        xa = vobj.bbox.intersect(figure.bbox)
                        if xa and xa.area/vobj.bbox.area>0.8:
                            self._logger.warning('第%s页的表格，原表格为图片，且当前不使用ocr，不解析该表格,bbox=%s',page.number,figure.bbox)
                            #TODO 使用原图大小？
                            page.objects.append(figure.make_figure())
                            #标记这个对象被处理过了？或者删除这个对象
                            #现在使用新的对象替代，因为作为图片处理了
                            #page.vobjects.remove(vobj)
                            page.vobjects[i]=VObject(vobj.page,VObjectType.FIGURE,vobj.quad,score=vobj.score,raw_type=vobj.raw_type)
                            break

        self._do(parse_page, doc.working_pages, max_workers=max_workers)

    def _parse_llm(self, doc: KDocument, *, max_workers: int = 0):
        """使用llm来解析表格"""
        from .llm import Parser

        Parser(self._manager).parse(doc, max_workers=max_workers)

    def _parse_ybk(self, doc: KDocument, *, max_workers: int = 0):
        """全部按有边框来解析"""
        from .ybk import Parser

        Parser().parse(doc, max_workers=max_workers, mode=YBKMode.AUTO)

    def _parse_wbk(self, doc: KDocument, *, max_workers: int = 0):
        """全部按无边框解析，表格的线仅仅用来参考"""
        from .wbk import Parser

        Parser(self._manager).parse(doc, max_workers=max_workers, mode=WBKMode.ALL)

    def _parse_auto(self, doc: KDocument, *, max_workers: int = 0):
        """自动选择最合适的"""
        from .wbk import Parser

        Parser(self._manager).parse(doc, max_workers=max_workers, mode=WBKMode.AUTO)

    def _fix(self, doc: KDocument):
        for page in doc.working_pages:
            #self._fix1(page)
            pass

    def _fix1(self, page: KPage):
        """
        修正layout识别错误的表格
        """

        def normalize_text(s: str) -> str:
            return strs.NText.get(s, mode="q2b", space="remove").text

        def case1(vobj: VObject):
            # 第一种：多包含内容
            # ----单位-- 可能包含了这些不属于表格的内容
            # --t1-----

            #例外的情况，下面这种就不能够删除了
            #单位：%   xx   xx
            #xxxxx    xx   xx
            page = vobj.page
            a_pattern = XPattern("fullmatch", patterns=[r"[(]?单位[:：].+[)]?"])
            pdf_chars = vobj.bbox.get(vobj.page.pdf_chars, ratio=0.8)
            ocr_chars = vobj.ocr_chars
            chars = pdf_chars + ocr_chars
            lines = KTextline.parse(chars)
            if len(lines) <= 2:
                return

            line = lines[0]
            # 如果第一行为单位，去掉？
            # 如果是有边框表格呢？明确包含的
            if not a_pattern.fullmatch(normalize_text(line.text)):
                return
            self._logger.warning('第%s页，修正表格，去掉单位，table=%s',page.number,vobj.bbox)
            page.objects.append(KText(page, line.quad, lines=[line]))
            # 重新调整这个的bbox，或者需要使用一个新的对象替代？
            vobj.set_bbox(vobj.bbox.adjust(y1=line.bbox.y0 - 1))
            lists.remove(vobj.ocr_chars, line.chars, strict=False)


        vobjs = list(vobj for vobj in page.vobjects if vobj.is_table())
        vobjs.sort(key=lambda vobj: vobj.bbox.y1, reverse=True)

        for vobj in vobjs:
            case1(vobj)
