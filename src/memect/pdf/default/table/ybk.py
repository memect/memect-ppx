from concurrent.futures import ThreadPoolExecutor
import logging
from typing import Callable, Sequence


from memect.base.bbox import BBox
from memect.base.debug import XDebugger
from memect.pdf.base import KChar, KDocument, KLine, KPage, KTable, VObject
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
        #有时候把几个小表格识别为一个
        #True表示修正一下，False表示不处理
        use_fix=True
        if use_fix:
            tables = self._fix1(page)
        else:
            tables:list[list[VObject]]=[]
            for vobj in page.vobjects:
                if vobj.is_table():
                    tables.append([vobj])
        i=0
        for vobjs in tables:
            self.parse_table(page,vobjs, index=i)
            i += 1

    def parse_table(self, page: KPage,vobjs:list[VObject], index: int = 0):
        debugger = self._debugger.bind(page=page.number)
        bbox = BBox.join2(vobjs)
        # 对于ocr字符，没有
        pdf_chars = bbox.get(page.pdf_chars, ratio=0.7)
        ocr_chars:list[KChar] = []
        for vobj in vobjs:
            ocr_chars.extend(vobj.ocr_chars)
        chars = pdf_chars + ocr_chars
        figures = bbox.get(page.pdf_figures, ratio=0.8)
        bbox = BBox.join2([bbox,*figures,*chars])
        #获得来自pdf或者图片的线
        source,h_lines,v_lines = self._parse_lines(page,bbox.expand(dx=2,dy=3,bound=page.bbox))
        raw_lines = h_lines+v_lines
        #再清理一下，避免误差，更好的解析表格，然后还可以再次
        table_lines = Liner().parse([line.bbox for line in raw_lines],page=page)
        table_bbox=bbox
        if table_lines:
            #因为没有包括线的宽度，所以这里大一点
            bbox2 = BBox.join(table_lines)
            if bbox2.expand(dx=5,dy=5).contains(bbox):
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

        


        if len(vobjs)>1:
            #如果是有多个表格组成一个，可能包含了其他对象，需要去掉
            table_bbox.get(page.objects,ratio=1,remove=True)
            #但是如果是先处理表格后再处理文本的，怎么知道文本被表格给用掉了？
            #创建一个新的VObject替代？

        grid = Grid(table_lines,chars)
        page.objects.append(KTable.from_grid(page,grid))

        if debugger.allow("draw"):
            grid.draw(image=page.image.copy(),scale=page.image.width/page.width,show=True).show()
            page.draw(
                ("page", None),
                ("pdf_chars", [*vobjs,*pdf_chars]),
                ("ocr_chars", [*vobjs,*ocr_chars]),
                (f"{source}_lines", [*vobjs,*raw_lines]),
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


    def _fix1(self,page:KPage):
        lines = page.pdf_lines
        h_lines,v_lines = KLine.split(lines)

        def has_v_lines(bbox:BBox)->bool:
            n=0
            for line in v_lines:
                if line.bbox.align('y',bbox,d=10) and bbox.x0<=line.bbox.x0<=bbox.x1:
                    return True
            return False

        def get_vobjects(bbox:BBox)->list[VObject]:
            return bbox.get(page.vobjects,ratio=0.7)
        
        vobjs = list(vobj for vobj in page.vobjects if vobj.is_table())
        vobjs.sort(key=lambda vobj:vobj.bbox.y1,reverse=True)
        tables:list[list[VObject]]=[]
        i=0
        while i<len(vobjs):
            vobj1=vobjs[i]
            vobj2=vobjs[i+1] if i+1 < len(vobjs) else None
            if len(v_lines)>0 and vobj2 and 0<=vobj1.bbox.y0-vobj2.bbox.y1<=20 and vobj1.bbox.width>=100 and vobj1.bbox.height>50 and vobj2.bbox.height>50 and vobj1.bbox.align('x',vobj2.bbox,d=10) and has_v_lines(BBox.join([vobj1.bbox,vobj2.bbox])):
                #连接在一起的表格，被识别为了2个或者多个
                #[table1]
                #[title]
                #[table2]
                tables.append(get_vobjects(BBox.join([vobj1.bbox,vobj2.bbox])))
                self._logger.warning('第%s页，合并识别错误的表格，2个为一个',page.number)
                i+=2
            else:
                tables.append([vobj1])
                i+=1
        
        return tables
        

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