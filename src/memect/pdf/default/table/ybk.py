from concurrent.futures import ThreadPoolExecutor
import logging
from typing import Callable, Sequence


from memect.base.bbox import BBox
from memect.base.debug import XDebugger
from memect.pdf.base import KDocument, KLine, KPage, KTable, VObject
from memect.pdf.grid import Grid
from .line import Liner


class Parser:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")

    def __init__(self):
        super().__init__()
    

    def parse(self,doc:KDocument,*,max_workers:int=0):
        self._do(self.parse_page,doc.working_pages,max_workers=max_workers)

    def parse_page(self,page:KPage):
        """"""
        #TODO 有时候一个大的表格会被识别为多个，需要在这里做些处理吗？

        i=0
        for vobj in page.vobjects:
            if vobj.is_table():
                self.parse_table(page, vobj, index=i)
                i += 1

    def parse_table(self, page: KPage, vobj: VObject, index: int = 0):
        debugger = self._debugger.bind(page=page.number)
        # 对于ocr字符，没有
        pdf_chars = vobj.bbox.get(page.pdf_chars, ratio=0.7)
        ocr_chars = vobj.ocr_chars
        chars = pdf_chars + ocr_chars
        figures = vobj.bbox.get(page.pdf_figures, ratio=0.8)
        bbox = BBox.join2([vobj, *chars, *figures])
        #获得来自pdf或者图片的线
        source,h_lines,v_lines = self._parse_lines(page,bbox.expand(dx=2,dy=3,bound=page.bbox))
        raw_lines = h_lines+v_lines
        #再清理一下，避免误差，更好的解析表格，然后还可以再次
        table_lines = Liner().parse([line.bbox for line in raw_lines],page=page)
        table_bbox=bbox
        if table_lines:
            #因为没有包括线的宽度，所以这里大一点
            bbox2 = BBox.join(table_lines)
            if bbox2.expand(dx=5,dy=5).contains(vobj.bbox):
                table_bbox=bbox2
            else:
                #虽然识别了线，但是可能只是局部的，也抛弃
                table_lines=[]
        
        
        if not table_lines:
            #理解为单行单列的表格，或者截图？
            table_lines=[
                (table_bbox[0],table_bbox[1],table_bbox[2],table_bbox[1]),
                (table_bbox[2],table_bbox[1],table_bbox[2],table_bbox[3]),
                (table_bbox[0],table_bbox[3],table_bbox[2],table_bbox[3]),
                (table_bbox[0],table_bbox[1],table_bbox[0],table_bbox[3])
            ]

        
        grid = Grid(table_lines,chars)
        page.objects.append(KTable.from_grid(page,grid))

        if debugger.allow("draw"):
            grid.draw(image=page.image.copy(),scale=page.image.width/page.width,show=True).show()
            page.draw(
                ("page", None),
                ("pdf_chars", [vobj,*pdf_chars]),
                ("ocr_chars", [vobj,*ocr_chars]),
                (f"{source}_lines", [vobj,*raw_lines]),
                (f'table_lines={len(table_lines)}',[table_bbox.expand(dx=1,dy=1)]+[KLine(page,BBox.from_list(line)) for line in table_lines]),
                dir="debug/default/ybk",
                index=index,
                show_type=False
            )

    def _parse_lines(
        self, page: KPage, bbox: BBox
    ) -> tuple[str, list[KLine], list[KLine]]:
        h_lines, v_lines = self._parse_pdf_lines(page, bbox)
        if h_lines or v_lines:
            return "pdf", h_lines, v_lines
        h_lines, v_lines = self._parse_image_lines(page, bbox)
        return "image", h_lines,v_lines

    def _parse_pdf_lines(
        self, page: KPage, bbox: BBox
    ) -> tuple[list[KLine], list[KLine]]:
        lines = bbox.get(page.pdf_lines,ratio=0.5)
        return KLine.split(lines)
        

    def _parse_image_lines(
        self, page: KPage, bbox: BBox
    ) -> tuple[list[KLine], list[KLine]]:
        return [], []


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