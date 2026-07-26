import logging
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum, auto
from typing import Callable, Sequence

from memect.base.bbox import BBox
from memect.base.debug import XDebugger
from memect.pdf.base import KDocument, KLine, KPage, KTable, VObject
from memect.pdf.default.table.filler import TableFiller

from .line import Liner


class YBKMode(StrEnum):
    AUTO = auto()
    """所有的表格都按有边框解析，自动选择pdf的线还是图片的线"""
    PDF = auto()
    """使用PDF线的解析，如果没有pdf的线，返回1*1的表格"""
    IMAGE = auto()
    """总是使用图片的方式解析线"""


class Parser:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")

    def __init__(self):
        super().__init__()

    def parse(
        self, doc: KDocument, *, max_workers: int = 0, mode: YBKMode = YBKMode.AUTO
    ):
        def parse_page(page: KPage):
            self._parse_page(page, mode)

        self._do(parse_page, doc.working_pages, max_workers=max_workers)

    def _parse_page(self, page: KPage, mode: YBKMode):
        """"""
        i = 0
        for vobj in page.vobjects:
            if vobj.is_table():
                table = self.parse_table(page, i, vobj, mode=mode)
                page.objects.append(table)
                i += 1

    def parse_table(
        self,
        page: KPage,
        index: int,
        vobj: VObject,
        fill: bool = True,
        mode: YBKMode = YBKMode.AUTO,
    ) -> KTable:
        """解析有边框表格
        page:
        index: 表格的index，为了输出图片等方便
        vobj:
        mode：解析方式
        fill：True表示填充对象，False表示先设置到table.cache['objects']中
        """
        debugger = self._debugger.bind(page=page.number)
        bbox = vobj.bbox
        # 获得来自pdf或者图片的线
        source, h_lines, v_lines = self._parse_lines(
            page, bbox, mode
        )
        raw_lines = h_lines + v_lines
        # 再清理一下，避免误差，更好的解析表格
        from .line import Options
        options=Options(y_axis_d=4)
        table_lines = Liner().parse([line.bbox for line in raw_lines], page=page,options=options)
        table_bbox = bbox
        if table_lines:
            # 因为没有包括线的宽度，所以这里大一点
            bbox2 = BBox.join(table_lines)

            #dy需要大一些，是因为对象识别的时候，bbox可能大了一些，包含了部分的外部文本
            #bbox2可能小了，如：彩色表格，可能丢失了前后的水平线，合理的做法是
            #bbox可能小了，没有包含边界线
            #bbox可能大了一点
            if bbox2.expand(dx=10, dy=10).contains(bbox):
                #有下面2种可能，如何处理？
                #线完整，bbox2正确，去掉了部分溢出的文本（因为table_bbox过大）
                #线不完整，bbox2不正确，丢失了部分文本（table_bbox正确）
                table_bbox=bbox2
            else:
                # 虽然识别了线，但是可能只是局部的，也抛弃
                table_lines = []

        if not table_lines:
            # 理解为单行单列的表格，或者截图？
            table_lines = [
                (table_bbox[0], table_bbox[1], table_bbox[2], table_bbox[1]),
                (table_bbox[2], table_bbox[1], table_bbox[2], table_bbox[3]),
                (table_bbox[0], table_bbox[3], table_bbox[2], table_bbox[3]),
                (table_bbox[0], table_bbox[1], table_bbox[0], table_bbox[3]),
            ]

        table = KTable.from_lines(page, table_lines)
        table.subtype='ybk'
        table.vobject = vobj
        filler = TableFiller()
        result = filler.get_objects([vobj])
        if fill:
            filler.fill(table, result)
        else:
            # 暂时不填充
            table.cache["result"] = result

        if debugger.allow("draw"):
            page.draw(
                ("page", None),
                ("table", [vobj]),
                (f"pdf_chars={len(result.pdf_chars)}", result.pdf_chars),
                (f"ocr_chars={len(result.ocr_chars)}", result.ocr_chars),
                (f"pdf_figures={len(result.pdf_figures)}", result.pdf_figures),
                (f"vobjects={len(result.vobjects)}", result.vobjects, True),
                (f"remain_objects={len(result.remain_objects)}", result.remain_objects),
                (f"{source}_lines={len(raw_lines)}", raw_lines),
                (
                    f"table_lines={len(table_lines)}",
                    [table_bbox.expand(dx=1, dy=1)]
                    + [KLine(page, BBox.from_list(line)) for line in table_lines],
                ),
                dir="debug/default/ybk",
                index=index,
                show_type=False,
                line_width=4
            )

        return table

    def _parse_lines(
        self, page: KPage, bbox: BBox, mode: YBKMode
    ) -> tuple[str, list[KLine], list[KLine]]:
        if mode == YBKMode.PDF:
            h_lines, v_lines = self._parse_pdf_lines(page, bbox)
            return "pdf", h_lines, v_lines
        elif mode == YBKMode.IMAGE:
            h_lines, v_lines = self._parse_image_lines(page, bbox)
            return "image", h_lines, v_lines
        elif mode == YBKMode.AUTO:
            h_lines, v_lines = self._parse_pdf_lines(page, bbox)
            if h_lines or v_lines:
                return "pdf", h_lines, v_lines
            h_lines, v_lines = self._parse_image_lines(page, bbox)
            return "image", h_lines, v_lines
        else:
            raise ValueError()

    def _parse_pdf_lines(
        self, page: KPage, bbox: BBox
    ) -> tuple[list[KLine], list[KLine]]:
        
        def has_v_lines(h_line:KLine,v_lines:list[KLine],top:bool)->bool:
            if not v_lines:
                return False
            if top:
                v_lines.sort(key=lambda line:line.bbox.y1,reverse=True)
            else:
                v_lines.sort(key=lambda line:line.bbox.y0)
            
            v_line = v_lines[0]
            if top:
                return v_line.bbox.y0-h_line.bbox.y0>=-1
            else:
                return v_line.bbox.y0-h_line.bbox.y0<=1
            
        def clean1(h_lines:list[KLine],v_lines:list[KLine]):
            #v_lines.sort(key=lambda line:line.bbox.y1,reverse=True)
            #或者和最高的垂直线没有连接的水平线去掉（对于没有左右垂直线的表格，可能会把正确的去掉，但是不影响解析）
            if len(h_lines)>=2 and page.bbox.y1-bbox.y1<=100:
                #可能和页眉线相邻
                h_lines.sort(key=lambda line:line.bbox.y1,reverse=True)
                line1=h_lines[0]
                line2=h_lines[1]
                if line1.bbox.y0-line2.bbox.y0<=5 and not has_v_lines(line1,v_lines,True):
                    #--------line1---
                    #--------line2---
                    del h_lines[0]
                    self._logger.warning('第%s页，删除和表格粘连的页眉线,line1=%s,line2=%s',page.number,line1.bbox,line2.bbox)

            if len(h_lines)>=2 and bbox.y0<=100:
                h_lines.sort(key=lambda line:line.bbox.y0,reverse=False)
                line1=h_lines[0]
                line2=h_lines[1]
                if line2.bbox.y0-line1.bbox.y0<=5 and not has_v_lines(line1,v_lines,False):
                    #--------line2----
                    #--------line1----
                    del h_lines[0]
                    self._logger.warning('第%s页，删除和表格粘连的页脚线,line1=%s,line2=%s',page.number,line1.bbox,line2.bbox)

        def clean2(lines:list[KLine],is_h:bool):
            #清除双层的线，如：
            #------------------line1
            # ---------------line2
            if len(lines)<2:
                return
            
            if is_h:
                x0,y0,x1,y1=0,1,2,3
            else:
                x0,y0,x1,y1=1,0,3,2
            
            lines.sort(key=lambda line:line.bbox[y1],reverse=True)
            i=0
            while i+1<len(lines):
                line1=lines[i].bbox
                line2=lines[i+1].bbox
                #如果线很粗的，可以放大，如：4
                dx=3
                dy=2
                if line1[x1]-line1[x0]>=20 and abs(line1[x0]-line2[x0])<=dx and abs(line1[x1]-line2[x1])<=dx and line1[y1]-line2[y1]<=dy:
                    if line1[x1]-line1[x0]>=line2[x1]-line2[x0]:
                        del lines[i+1]
                    else:
                        del lines[i]
                else:
                    i+=1
            
            pass
        
        def clean3(bbox:BBox,h_lines:list[KLine],v_lines:list[KLine]):
            #----line1------
            # [--table--]
            #----line2------ 
            #因为会影响表格，因为对象识别的原因，表格的区域可能大了一点，包含了line1或者line2，需要去掉line1和line2
            #如果是严格的有边框表格，有左右垂直线，容易判断
            #如果是没有左右垂直线的表格，line1可能也是表格的线
            if not v_lines:
                return
            
            if len(h_lines)<2:
                return
            
            h_lines.sort(key=lambda line:line.bbox.y1,reverse=True)
            v_lines.sort(key=lambda line:line.bbox.y1,reverse=True)

            h_line1 = h_lines[0]
            h_line2 = h_lines[1]
            v_line = v_lines[0]
            #先处理第一种可能，就是粘连在一起了，bbox也无法区分，这种情况，就认为不是没有左右垂直线表格，直接去掉
            #h_line1.bbox.over('x',h_line2.bbox,d=20) and h_line1.bbox.y0-h_line2.bbox.y0<=3
            
            if h_line1.bbox.over('x',h_line2.bbox,d=20) and h_line1.bbox.y0-h_line2.bbox.y0<=3 or h_line1.bbox.y0-bbox.y1>=1 and h_line1.bbox.y0-h_line2.bbox.y1<=8 and h_line1.bbox.y0-v_line.bbox.y1>=3:
                #-------h_line1---- 去掉这一条
                #----bbox.y1-------
                #-------h_line2---- 保留这一条
                self._logger.warning('第%s页，删除错误的表格上线,table=%s,line=%s',page.number,bbox,h_line1.bbox)
                del h_lines[0]
            
            if len(h_lines)<2:
                return
            
            h_line1=h_lines[-1]
            h_line2=h_lines[-2]
            v_line=v_lines[-1]
            
            if h_line1.bbox.over('x',h_line2.bbox,d=20) and h_line2.bbox.y0-h_line1.bbox.y0<=3 or bbox.y0-h_line1.bbox.y1>=1 and h_line2.bbox.y0-h_line1.bbox.y0<=8 and v_line.bbox.y0-h_line1.bbox.y1>=3:
                #----h_line2----- 保留这一条
                #----bbox.y0-----
                #----h_line1----- 删除这一条
                self._logger.warning('第%s页，删除错误的表格底线,table=%s,line=%s',page.number,bbox,h_line1.bbox)
                del h_lines[-1]

        #TODO 还需要去掉下划线，否则可能会把下划线识别为表格线，如：
        # xx____下划线
        #-------表格线
        
        #expand后，可能会把靠得很近的水平线给多包含了，就需要删除
        lines = bbox.expand(dx=2, dy=2, bound=page.bbox).get(page.pdf_lines, ratio=0.5)
        h_lines,v_lines=KLine.split(lines)

        clean2(h_lines,True)
        clean2(v_lines,False)
        #TODO 如果有页眉线页脚线，可能会重叠或者相邻，需要先去掉，避免多识别一列
        clean1(h_lines,v_lines)

        clean3(bbox,h_lines,v_lines)

        return h_lines,v_lines

    def _parse_image_lines(
        self, page: KPage, bbox: BBox
    ) -> tuple[list[KLine], list[KLine]]:
        # TODO 暂时没有实现
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
