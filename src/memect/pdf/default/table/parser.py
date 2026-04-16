from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Final, Sequence

from memect.base.bbox import BBox
from memect.pdf.base import KDocument, KPage, KTable, TableMode, VObject
from memect.pdf.model import ModelExecutor


class TableParser:
    def __init__(self):
        super().__init__()
        self._table_cls: Final = ModelExecutor.get("table_cls")
        self._table_cls_key: Final = "cache/default/table_cls"
        self._table_det: Final = ModelExecutor.get("table_det")
        self._table_det_key: Final = "cache/default/table_det"
        self._table_llm = ModelExecutor.get("table_llm")
        self._table_llm_key: Final = "cache/default/table_llm"

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
        if doc.params.table == TableMode.NO:
            # 不用解析表格，全部作为图片
            self._parse_as_figures(doc, max_workers=max_workers)
        elif doc.params.table == TableMode.LLM:
            self._parse_llm(doc, max_workers=max_workers)
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

    def _parse_as_figures(self, doc: KDocument, *, max_workers: int = 0):
        def adjust_bbox(page: KPage, vobj: VObject) -> BBox:
            bbox = vobj.bbox.large
            return bbox.expand(dx=2, dy=2).intersect(page.bbox) or bbox

        def parse_page(page: KPage):
            for vobj in page.vobjects:
                if vobj.is_table():
                    figure = page.make_figure(
                        adjust_bbox(page, vobj).to_quad(), add=True
                    )
                    if figure is not None:
                        figure.vobject = vobj
                        figure.subtype = str(vobj.type)

        self._do(parse_page, doc.working_pages, max_workers=max_workers)

    def _parse_llm(self, doc: KDocument, *, max_workers: int = 0):
        """使用llm来解析表格"""
        from .llm import Parser
        Parser().parse(doc,max_workers=max_workers)

    def _parse_ybk(self, doc: KDocument, *, max_workers: int = 0):
        """全部按有边框来解析，如果没有线，就是1*1的表格
        一般来说，这个模式合适在开发中测试ybk的解析，或者需要处理的文档都是有边框表格，使用这个可以获得最好的解析。
        """
        from .ybk import Parser        
        Parser().parse(doc,max_workers=max_workers)

    def _parse_wbk(self, doc: KDocument, *, max_workers: int = 0):
        """全部按无边框解析，表格的线仅仅用来参考"""
        from .wbk import Parser        
        Parser().parse(doc,max_workers=max_workers)

    def _parse_auto(self, doc: KDocument, *, max_workers: int = 0):
        """先判断是无边框表格还是有边框表格"""
        #from .auto import Parser

        #赶着提交，就先使用有边框的解析
        from .ybk import Parser 
        Parser().parse(doc,max_workers=max_workers)

