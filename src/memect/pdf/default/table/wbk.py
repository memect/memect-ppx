import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Any, Callable, Final, Literal, Self, Sequence

import PIL
import PIL.Image
import PIL.ImageDraw

from memect.base import images, lists
from memect.base.bbox import BBox
from memect.base.debug import XDebugger
from memect.base.matrix import Matrix
from memect.pdf.base import (
    KCell,
    KChar,
    KDocument,
    KLine,
    KObject,
    KPage,
    KPDFFigure,
    KTable,
    KText,
    VObject,
)
from memect.pdf.model import ModelManager

from .filler import Result, TableFiller
from .ybk import YBKMode


class _Cell:
    """目的是为了方便调整bbox，而且知道原对象"""

    def __init__(self, source: BBox, *, generated: bool = False):
        super().__init__()

        self.bbox: BBox = source.large #if hasattr(source, "bbox") else source
        self.original_bbox: Final = self.bbox
        """模型原始bbox，用于后续判断边界被吸附前的错位关系"""
        self.content_bbox:BBox|None=None
        """如果设置了，表示为单元格的内容bbox"""
        self.source: Final = source
        self.generated: Final = generated
        """表示这个cell是Builder补出来的空格，不是模型直接检测出来的格子"""
    
    def copy(self)->Self:
        obj = self.__class__(self.source,generated=self.generated)
        obj.content_bbox=self.content_bbox
        return obj
        



class WBKMode(StrEnum):
    ALL = "all"
    """所有的表格都使用无边框解析"""
    AUTO = "auto"
    """如果有pdf的线，且结构接近，就使用有边框的结构"""


@dataclass
class _CellSlot:
    cell: _Cell
    row0: int
    row1: int
    col0: int
    col1: int


type _EdgeIndex = Literal[0, 1, 2, 3]

X0: Final[_EdgeIndex] = 0
Y0: Final[_EdgeIndex] = 1
X1: Final[_EdgeIndex] = 2
Y1: Final[_EdgeIndex] = 3


class Parser:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")

    def __init__(self, manager: ModelManager):
        super().__init__()
        from .ybk import Parser

        self._table: Final = manager.get("table")
        self._table_key: Final = "cache/default/wbk/table"
        self._ybk_parser: Final = Parser()

    def parse(
        self, doc: KDocument, *, max_workers: int = 0, mode: WBKMode = WBKMode.ALL
    ):
        def get_tables(page: KPage):
            tables: list[Any] = []
            for vobj in page.vobjects:
                if vobj.is_table():
                    # TODO 如果是有边框表格，可以考虑稍微大一点，包含表格线？
                    img = page.crop(vobj.bbox)
                    if img:
                        tables.append((img, vobj.cache))
                    else:
                        # bbox是无效的？
                        pass

            return tables

        def parse_page(page: KPage):
            return self._parse_page(page, mode=mode)

        self._table.parse(doc, self._table_key, handler=get_tables)        
        self._do(parse_page, doc.working_pages, max_workers=max_workers)
        #按页解析完毕后，再考虑跨页/跨栏表格的对齐
        #不能够在这里执行，因为还需要先建立阅读顺序
        


    def parse_one(self,page:KPage,bbox:BBox,*,use_vobj:bool=False,use_char:bool=True,add:bool=False,clear:bool=False,name:str='custom',index:int=0)->KTable:
        """把页面的指定区域解析为无边框表格
        page:
        bbox:
        use_vobj: True表示使用vobjects，False使用page.objects
        use_char:True 表示使用字符对象
        add:True 表示添加到page.objects
        clear:True 表示清除bbox区域的对象
        name: 调试的图片的文件名
        index: 调试的图片的序号
        """
        debugger = self._debugger.bind(page=page.number)
        steps: list[Any]|None = None
        if debugger.allow('draw'):
            steps=[]

        img = page.crop(bbox)
        assert img is not None
        model_result = self._table.execute([img])[0]
        if use_vobj:
            #如果没有figures/formulas，两种做法没有什么不同
            #如果有，只是多创建了图片文件等，不影响其他
            vobjs = bbox.get(page.vobjects,ratio=0.8)
            result = TableFiller().get_objects(vobjs)
            table = self._parse_wbk(page,bbox,model_result,result=result)
            if clear:
                bbox.get(page.objects,ratio=0.7,remove=True)
            if add:
                page.objects.append(table)
        else:
            objects:list[KObject]=[]
            chars:list[KChar]=[]
            used_objects:list[KObject]=[]
            for obj in bbox.get(page.objects,ratio=0.8):
                used_objects.append(obj)
                if use_char and isinstance(obj,KText):
                    if len(obj.objects)==len(obj.chars):
                        #多数情况下都是文本，除非有行内图片或者公式
                        chars.extend(obj.chars)
                    else:
                        for a in obj.objects:
                            if isinstance(a,KChar):
                                chars.append(a)
                            else:
                                objects.append(a)
                else:
                    objects.append(obj)
            
            result = Result(chars=chars,objects=objects)
            table = self._parse_wbk(page,bbox,model_result,result=result)
            if len(used_objects)>0:
                if add:
                    i=page.objects.index(used_objects[0])
                    page.objects.insert(i,table)
                if clear:
                    lists.remove(page.objects,used_objects,use_is=True)

        result = table.cache.pop("result", None)
        result = TableFiller().fill(table, result)
        if steps is not None:
            steps.append((f"remain_objects={len(result.remain_objects)}",result.remain_objects))
            steps.append((f"{table.subtype}", table.get_lines2()))
            #TODO 如何获得文件名？
            page.draw(
                *steps,
                index=index,
                dir=f"debug/default/wbk/{name}",
                show_type=False,
                line_width=4,
            )

        return table

        

    def _parse_page(self, page: KPage, mode: WBKMode):
        i = 0
        for vobj in page.vobjects:
            if vobj.is_table():
                table = self._parse_table(page, i, vobj, mode=mode)
                page.objects.append(table)
                i += 1

    def _parse_table(self, page: KPage, index: int, vobj: VObject, mode: WBKMode)->KTable:
        debugger = self._debugger.bind(page=page.number)

        def use_ybk(ybk: KTable, wbk: KTable,use_stripped:bool=False) -> bool:
            if use_stripped:
                row_num,col_num = wbk.get_stripped_size()
            else:
                row_num,col_num = wbk.row_num,wbk.col_num
            
            if ybk.row_num==row_num and ybk.col_num==col_num:
                #TODO 即使结构一致，可能还是需要理解为有边框，特别是彩色表格，如：
                #有边框识别如下：2*3，4个单元格
                #xx  xx  xx   => 被识别为1列
                #xx| xx| xx 

                #无边框识别为如下：也是2*3，6个单元格
                #xx|xx|xx
                #xx|xx|xx

                #TODO 再比较单元格数量？
                if len(ybk.cells)>=len(wbk.cells):
                    return True
                
                if col_num<=2:
                    return True
                #如果ybk列span多的，就信任无边框？
                wbk_ok=0
                for i in range(row_num):
                    row1 = ybk.get_row(i)
                    row2 = wbk.get_row(i)
                    if len(row1)<len(row2):
                        wbk_ok+=1
                    else:
                        pass
                min_threshold=min(2,row_num-1)
                if wbk_ok>=min_threshold:
                    return False
                
            
            if ybk.row_num >= row_num and ybk.col_num >= col_num:
                return True
            # 如果结构不一致，使用哪一个呢？
            # ybk有两种可能：完整有边框，局部有边框
            # 如果是完整有边框，wbk解析出来的行列应该是基本一致的（当然个别表格存在解析错误）
            # 如果是局部有边框，wbk解析出来的行列会多一些
            return False

        steps: list[Any]|None = None
        if debugger.allow('draw'):
            steps=[]
        wbk_table = self._parse_wbk(page,vobj.bbox,vobj.cache.pop(self._table_key,None),vobject=vobj,steps=steps)
        table = wbk_table
        beautify = True
        ybk_table:KTable|None=None
        if mode == WBKMode.AUTO:
            ybk_table = self._parse_ybk(page, index, vobj)
            if ybk_table is not None:
                if steps is not None:
                    steps.append(
                        (
                            f"ybk=({ybk_table.row_num},{ybk_table.col_num})",
                            ybk_table.get_lines2(),
                        )
                    )
                if use_ybk(ybk_table, wbk_table):
                    table = ybk_table
                    beautify = False

        # 如果之前已经获得对象了（因为各种需要）
        # 之所以在这里再填充对象，只是避免表格内的图片/公式等多次解析，不影响什么
        result = table.cache.pop("result", None)
        result = TableFiller().fill(table, result)

        strict=True
        if strict and ybk_table is not None and table is wbk_table and use_ybk(ybk_table,table,use_stripped=True):
            #去掉前后空白行再比较，因为可能对象识别的bbox过大，把外部的文本包含了部分（如：一半）
            #这个时候就可以再次切换回来使用有边框
            self._logger.warning('第%s页，去掉空白行/列，从wbk切换回ybk,bbox=%s',table.page.number,table.bbox)
            result=TableFiller().fill(ybk_table,ybk_table.cache.pop('result'))
            table=ybk_table
            beautify=False
        elif table is wbk_table:
            #仅仅去掉前后空白行
            #如果表格为完全空白，就构造一个1*1的空白表格
            #原因为原文为图片表格，但是太模糊，ocr无法识别出文字，就是一个完全空白的表格
            table = table.strip()
            table.cache['cells']=wbk_table.cache.pop('cells')
            table.cache['result']=result

        if beautify:
            self._beautify(table)

        # TODO 如何显示
        if steps is not None:
            steps.append((f"remain_objects={len(result.remain_objects)}",result.remain_objects))
            steps.append((f"{table.subtype}", table.get_lines2()))
            page.draw(
                *steps,
                index=index,
                dir="debug/default/wbk/table",
                show_type=False,
                line_width=4,
            )
        return table

    def _parse_ybk(self, page: KPage, index: int, vobj: VObject) -> KTable|None:
        # 仅仅当有PDF的线的时候，按有边框解析才有很高的准确度，如果是使用图片的线，就不如直接无边框解析
        #还可以更加快速的判断，如果一条垂直线都没有的？
        lines = vobj.bbox.get(page.pdf_lines,ratio=0.7)
        h_lines,v_lines = KLine.split(lines)
        if not v_lines:
            return None
        return self._ybk_parser.parse_table(
            page, index, vobj, fill=False, mode=YBKMode.PDF
        )

    
    def _parse_wbk(
        self, page: KPage,bbox:BBox,model_result:Any,*,result:Result|None=None,vobject: VObject|None=None, steps: list[Any]|None=None
    ) -> KTable:
        #debugger = self._debugger.bind(page=page.number)
        
        cells = self._convert_cells(page,bbox,model_result)
        raw_cells: Final = [c.bbox for c in cells]
        raw_bbox:Final = bbox
        # 避免重叠
        cells = self._adjust_cells(cells)
        adjusted_cells:Final=[c.bbox for c in cells]
        if result is None:
            #表示根据vobject创建，这个时候，该区域还没有其他对象(page.objects)
            assert vobject is not None
            result = TableFiller().get_objects([vobject])
        chars=list(result.chars[:])
        self._expand_cells(cells, chars, [0.7, 0.5])
        self._expand_cells(cells, list(result.pdf_figures), [0.7, 0.5])
        self._expand_cells(cells, list(result.vobjects), [0.7, 0.5])
        self._expand_cells(cells,list(result.objects),[0.8])
        #有时候识别的区域过小，丢失一部分字符，这里再次纠正
        #self._expand_cells(cells,chars,[0.7],dx=10,dy=0)
        expanded_cells:Final=[c.bbox for c in cells]
        self._adjust_items(cells)
        adjusted2_cells:Final=[c.bbox for c in cells]
        #TODO 如果识别的单元格少了左边或者右边的列，怎么补充？
        #可以根据当前表格区域包含的字符/图片，使用规则先做一个初步的表格解析
        #然后再补充？这样又增加了算法的复杂度

        #记录当前的cells，在跨页/跨栏的表格合并中，需要再计算一次列
        #不使用_Builder().build(cells)后的
        cache_cells:Final = [c.copy() for c in cells]
        cells = _Builder().build(cells)
        #或者记录这些cells
        #没有必要再执行一次了
        #self._adjust_items(cells)
        if cells:
            #TODO 如果识别的单元格少了，如：少了左边/右边的列，bbox就会变小
            bbox = BBox.join2(cells)
        table = _Builder().make_table(page,bbox,cells)
        table.vobject = vobject
        table.subtype = "wbk"
        table.cache["result"] = result
        table.cache['cells']=cache_cells

        if steps is not None:
            steps.extend(
                [
                    ("page", None),
                    ("raw_table_bbox",[raw_bbox]),
                    ("table", [bbox]),
                    (f"pdf_chars={len(result.pdf_chars)}", result.chars),
                    (f"ocr_chars={len(result.ocr_chars)}", result.ocr_chars),
                    (f"removed_chars={len(result.removed_chars)}",result.removed_chars),
                    (f"pdf_figures={len(result.pdf_figures)}", result.pdf_figures),
                    (f"removed_pdf_figures={len(result.removed_pdf_figures)}", result.removed_pdf_figures),
                    (f"vobjects={len(result.vobjects)}", result.vobjects, True),
                    (f"raw_cells={len(raw_cells)}", raw_cells),
                    (f"adjusted_cells={len(adjusted_cells)}", adjusted_cells),
                    (f'expanded_cells={len(expanded_cells)}',expanded_cells),
                    (f'adjusted2_cells={len(adjusted2_cells)}',adjusted2_cells),
                    (f"cells={len(cells)}", cells),
                    (f"wbk=({table.row_num},{table.col_num})",table.get_lines2())
                ]
            )
        return table

    def _expand_cells(
        self, cells: list[_Cell], objs: list[Any], ratios: Sequence[float],dx:float=0,dy:float=0
    ):
        """确保cell能够塞入对象"""
        for ratio in ratios:
            if not objs:
                break
            for cell in cells:
                cell_bbox = cell.bbox.expand(dx=dx,dy=dy)
                cell_objs = cell_bbox.get(objs, ratio=ratio, remove=True)
                if cell_objs:
                    cb = BBox.join2(cell_objs)
                    cell.bbox = cell.bbox.union(cb)
                    if cell.content_bbox is None:
                        cell.content_bbox=cb
                    else:
                        cell.content_bbox = cell.content_bbox.union(cb)

                if not objs:
                    break


    def _convert_cells(self,page:KPage,bbox:BBox,result:Any):
        # 获得的结果是相对截图的，还需要进行转化
        if not result:
            # 表示无法截图？
            return [_Cell(bbox)]
        
        width = result["width"]
        height = result["height"]
        # 转化为相对页面的坐标，
        sw = bbox.width / width
        sh = bbox.height / height
        tx = bbox.x0
        ty = bbox.y0
        m = Matrix().lt_to_lb((width, height)).scale(sw, sh).translate(tx, ty)
        cells: list[_Cell] = []
        for cell_bbox in result["cells"]:
            xb = bbox.intersect(BBox.from_list(cell_bbox, matrix=m))
            if xb is not None: 
                cells.append(_Cell(xb))
        return cells

    def _adjust_cells(self, cells: Sequence[_Cell]) -> list[_Cell]:
        # 先删除完全包含的？

        def get_overlapped_cell(c1:_Cell,cells:Sequence[_Cell],start:int)->tuple[_Cell,BBox]|None:
            overlapped_cells:list[tuple[_Cell,BBox]]=[]
            for i in range(start,len(cells)):
                c2 = cells[i]
                if c2.bbox.y1<=c1.bbox.y0:
                    break
                xa = c1.bbox.intersect(c2.bbox)
                if xa and xa.area>0:
                    overlapped_cells.append((c2,xa))
            
            if not overlapped_cells:
                return None
            
            overlapped_cells.sort(key=lambda item:item[1].area,reverse=True)
            return overlapped_cells[0]
        
        def clean_cells(cells: Sequence[_Cell]) -> list[_Cell]:
            cells = list(cells)
            cells.sort(key=lambda cell: cell.bbox.y1, reverse=True)
            i = 0
            while i < len(cells):
                c1 = cells[i].bbox
                j = i + 1
                c1_removed = False
                while j < len(cells):
                    c2 = cells[j].bbox
                    if c2.y1 <= c1.y0:
                        break
                    xb = c1.intersect(c2)
                    #简单的部分重叠，删除小的，保留大的
                    #如果大的又和其他的部分重叠，这个时候，删除大的更好
                    if xb and xb.area / min(c1.area, c2.area) >= 0.7:
                        if c1.area > c2.area:
                            item1 = get_overlapped_cell(cells[i],cells,j+1)
                            item2 = get_overlapped_cell(cells[j],cells,j+1)
                            if item1 and (not item2 or item1[1].area>item2[1].area):
                                #如果大的还有其他重叠，小的没有
                                del cells[i]
                                c1_removed=True
                                break
                            else:
                                #删除小的
                                del cells[j]
                        else:
                            del cells[i]
                            c1_removed = True
                            break
                    else:
                        j += 1
                if not c1_removed:
                    i += 1
            return cells

        cells = clean_cells(cells)
        self._adjust_items(cells)
        return cells




    def _adjust_items2(self, cells: Sequence[_Cell]):
        # debugger=self._debugger.bind(page=self.table.pages[0].number)
        strict = False

        def adjust(cells: Sequence[_Cell]):
            cells = sorted(cells, key=lambda cell: cell.bbox.y1, reverse=True)
            for i in range(len(cells)):
                c1 = cells[i]
                for j in range(i + 1, len(cells)):
                    c2 = cells[j]
                    # 不允许刚好重叠的，就设置为
                    dy = c2.bbox.y1 - c1.bbox.y0
                    # 如果dy==0，也就是c1,c2粘连在一起，在这种情况，也需要调整上下，否则无法画线？
                    # [c1]
                    # [c2]
                    if dy <= 0:
                        # 不需要再继续了，也可以使用"<"
                        break

                    # 如果完全包含
                    if c1.bbox.expand(dx=3, dy=3).contains(c2.bbox) or c2.bbox.expand(
                        dx=3, dy=3
                    ).contains(c1.bbox):
                        continue
                    area = c1.bbox.intersect(c2.bbox)
                    if area is None:
                        continue

                    if area.width >= area.height:
                        # [--c1--]
                        #  [--c2--]
                        # 水平重叠的多，调整y
                        if c1.bbox.height > c2.bbox.height:
                            c1.bbox = c1.bbox.adjust(y0=c2.bbox.y1 + 1)
                        else:
                            c2.bbox = c2.bbox.adjust(y1=c1.bbox.y0 - 1)
                    else:
                        #      [--c3--]
                        # [--c4--]
                        # 垂直重叠的多，调整x即可
                        if c1.bbox.x1 < c2.bbox.x1:
                            c3, c4 = c2, c1
                        else:
                            c3, c4 = c1, c2
                        if c3.bbox.width > c4.bbox.width:
                            c3.bbox = c3.bbox.adjust(x0=c4.bbox.x1 + 1)
                        else:
                            c4.bbox = c4.bbox.adjust(x1=c3.bbox.x0 - 1)

                    # 不能够break，可能还和其他的重叠
                    if strict and (c1.bbox.area == 0 or c2.bbox.area == 0):
                        raise RuntimeError("程序写错了")

        adjust(cells)


    def _adjust_items(self,cells:Sequence[_Cell]):
        def has_overlap(b1: BBox, b2: BBox) -> bool:
            xb = b1.intersect(b2)
            return xb is not None and xb.width > 0 and xb.height > 0

        def valid_bbox(bbox: BBox) -> bool:
            return bbox.width > 0 and bbox.height > 0

        def apply_plan(plan: list[tuple[_Cell, BBox]]):
            for cell, bbox in plan:
                cell.bbox = bbox

        def plan_cost(plan: list[tuple[_Cell, BBox]]) -> tuple[int, float, float, int]:
            changed = [(cell, bbox) for cell, bbox in plan if cell.bbox != bbox]
            total_loss = sum(max(cell.bbox.area - bbox.area, 0) for cell, bbox in changed)
            relative_loss = sum(
                max(cell.bbox.area - bbox.area, 0) / max(cell.bbox.area, 1)
                for cell, bbox in changed
            )
            real_changed = sum(0 if cell.generated else 1 for cell, _ in changed)
            return real_changed, relative_loss, total_loss, len(changed)

        def add_plan(
            plans: list[list[tuple[_Cell, BBox]]],
            c1: _Cell,
            c2: _Cell,
            changes: list[tuple[_Cell, BBox]],
        ):
            b1 = c1.bbox
            b2 = c2.bbox
            for cell, bbox in changes:
                if not valid_bbox(bbox):
                    return
                if cell is c1:
                    b1 = bbox
                elif cell is c2:
                    b2 = bbox
            if not has_overlap(b1, b2):
                plans.append(changes)

        def axis_plans(c1: _Cell, c2: _Cell, axis: Literal["x", "y"]):
            plans: list[list[tuple[_Cell, BBox]]] = []
            if axis == "y":
                upper, lower = (c1, c2) if c1.bbox.cy >= c2.bbox.cy else (c2, c1)
                add_plan(
                    plans,
                    c1,
                    c2,
                    [(upper, upper.bbox.adjust(y0=lower.bbox.y1))],
                )
                add_plan(
                    plans,
                    c1,
                    c2,
                    [(lower, lower.bbox.adjust(y1=upper.bbox.y0))],
                )
                cut = (upper.bbox.y0 + lower.bbox.y1) / 2
                add_plan(
                    plans,
                    c1,
                    c2,
                    [
                        (upper, upper.bbox.adjust(y0=cut)),
                        (lower, lower.bbox.adjust(y1=cut)),
                    ],
                )
            else:
                left, right = (c1, c2) if c1.bbox.cx <= c2.bbox.cx else (c2, c1)
                add_plan(
                    plans,
                    c1,
                    c2,
                    [(left, left.bbox.adjust(x1=right.bbox.x0))],
                )
                add_plan(
                    plans,
                    c1,
                    c2,
                    [(right, right.bbox.adjust(x0=left.bbox.x1))],
                )
                cut = (left.bbox.x1 + right.bbox.x0) / 2
                add_plan(
                    plans,
                    c1,
                    c2,
                    [
                        (left, left.bbox.adjust(x1=cut)),
                        (right, right.bbox.adjust(x0=cut)),
                    ],
                )
            return plans

        def best_plan(c1: _Cell, c2: _Cell, area: BBox):
            min_width = min(c1.bbox.width, c2.bbox.width)
            min_height = min(c1.bbox.height, c2.bbox.height)
            if min_width <= 0 or min_height <= 0:
                return None
            x_ratio = area.width / min_width
            y_ratio = area.height / min_height
            axes: tuple[Literal["x", "y"], Literal["x", "y"]]
            axes = ("y", "x") if x_ratio >= y_ratio else ("x", "y")

            plans: list[list[tuple[_Cell, BBox]]] = []
            for axis in axes:
                plans.extend(axis_plans(c1, c2, axis))
            if not plans:
                return None
            return min(plans, key=plan_cost)

        cells = sorted(cells, key=lambda cell: cell.bbox.y1, reverse=True)
        for i, c1 in enumerate(cells):
            if not valid_bbox(c1.bbox):
                continue
            for c2 in cells[i + 1 :]:
                if c2.bbox.y1 <= c1.bbox.y0:
                    break
                if not valid_bbox(c2.bbox):
                    continue
                area = c1.bbox.intersect(c2.bbox)
                if area is None or area.width <= 0 or area.height <= 0:
                    continue
                plan = best_plan(c1, c2, area)
                if plan is not None:
                    apply_plan(plan)

    def _beautify(self, table: KTable):
        pass

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


class _Builder:
    def __init__(self):
        super().__init__()

    def build(self, cells: list[_Cell]) -> list[_Cell]:
        """把模型检测出的cell规整到一个矩形表格网格中。

        处理目标：
        1. 轻微漂移的边界吸附到全局网格线。
        2. 孤立的错误边界如果落在两条稳定网格线之间，吸附到前/后稳定线。
        3. 保留跨行跨列cell，因为它们自然覆盖多个网格区间。
        4. 对网格中没有被任何cell覆盖的位置补空cell。
        """
        cells = [
            cell for cell in cells if cell.bbox.width > 0 and cell.bbox.height > 0
        ]
        if len(cells) < 2:
            return cells

        self._snap_outlier_edges(cells, axis="x")
        self._snap_outlier_edges(cells, axis="y")
        self._snap_to_grid(cells, axis="x")
        self._snap_to_grid(cells, axis="y")
        self._align_neighbor_y_edges(cells)
        self._snap_to_grid(cells, axis="y")
        self._fill_missing(cells)
        self._repair_staggered_generated_cells(cells, axis="y")
        self._repair_staggered_generated_cells(cells, axis="x")
        self._absorb_staggered_y_bands(cells)
        self._snap_to_grid(cells, axis="x")
        self._snap_to_grid(cells, axis="y")
        return cells

    def make_table(
        self,
        page: KPage,
        table_bbox: BBox,
        cells: Sequence[_Cell],
    ) -> KTable:
        cells = [cell for cell in cells if cell.bbox.width > 0 and cell.bbox.height > 0]
        self._absorb_staggered_y_bands(cells)
        
        if not cells:
            return KTable(page, table_bbox,cells=[KCell(page, table_bbox, row_index=0, col_index=0)])

        x_lines = self._axis_lines(cells, axis="x")
        y_lines = self._axis_lines(cells, axis="y")
        if len(x_lines) < 2 or len(y_lines) < 2:
            bbox = BBox.join2(cells)
            return KTable(page, bbox,cells=[KCell(page, bbox, row_index=0, col_index=0)])

        col_num = len(x_lines) - 1
        row_num = len(y_lines) - 1
        slots = [
            slot
            for cell in cells
            if (slot := self._make_cell_slot(cell, x_lines, y_lines)) is not None
        ]
        slots = self._resolve_slot_overlaps(slots, row_num, col_num)
        slots = self._ensure_slot_start_indexes(slots, row_num, col_num)
        slots = self._fill_slot_holes(slots, row_num, col_num, x_lines, y_lines)
        kcells = self._slots_to_kcells(page, slots, row_num, x_lines, y_lines)

        kcells.sort(key=lambda cell: (cell.row_index, cell.col_index))
        return KTable(page, table_bbox,cells=kcells)

    def _make_cell_slot(
        self, cell: _Cell, x_lines: list[float], y_lines: list[float]
    ) -> _CellSlot | None:
        col_num = len(x_lines) - 1
        row_num = len(y_lines) - 1
        if col_num <= 0 or row_num <= 0:
            return None

        col0 = self._nearest_index(cell.bbox.x0, x_lines)
        col1 = self._nearest_index(cell.bbox.x1, x_lines)
        y0 = self._nearest_index(cell.bbox.y0, y_lines)
        y1 = self._nearest_index(cell.bbox.y1, y_lines)
        if col1 <= col0:
            col1 = col0 + 1
        if y1 <= y0:
            y1 = y0 + 1
        col0 = max(0, min(col0, col_num - 1))
        col1 = max(col0 + 1, min(col1, col_num))
        y0 = max(0, min(y0, row_num - 1))
        y1 = max(y0 + 1, min(y1, row_num))

        return _CellSlot(
            cell=cell,
            row0=row_num - y1,
            row1=row_num - y0,
            col0=col0,
            col1=col1,
        )

    def _resolve_slot_overlaps(
        self, slots: list[_CellSlot], row_num: int, col_num: int
    ) -> list[_CellSlot]:
        real_slots = [slot for slot in slots if not slot.cell.generated]
        if not real_slots:
            real_slots = slots

        real_slots = self._dedupe_slot_anchors(real_slots)
        real_slots = self._avoid_real_anchor_conflicts(real_slots)

        grid: list[list[_CellSlot | None]] = [
            [None] * col_num for _ in range(row_num)
        ]
        accepted: list[_CellSlot] = []
        for slot in sorted(real_slots, key=self._slot_order_key):
            if self._slot_is_empty(slot):
                continue
            placed = slot
            if not self._slot_rect_is_empty(grid, slot):
                adjusted = self._largest_empty_anchor_slot(grid, slot)
                if adjusted is None:
                    continue
                placed = adjusted
            self._place_slot(grid, placed)
            accepted.append(placed)
        return accepted

    def _dedupe_slot_anchors(self, slots: list[_CellSlot]) -> list[_CellSlot]:
        anchors: dict[tuple[int, int], _CellSlot] = {}
        for slot in slots:
            key = (slot.row0, slot.col0)
            old = anchors.get(key)
            if old is None or self._slot_priority(slot) > self._slot_priority(old):
                anchors[key] = slot
        return list(anchors.values())

    def _avoid_real_anchor_conflicts(self, slots: list[_CellSlot]) -> list[_CellSlot]:
        anchors = [
            (slot.row0, slot.col0, slot) for slot in slots if not slot.cell.generated
        ]
        result: list[_CellSlot] = []
        for slot in slots:
            if slot.cell.generated:
                result.append(slot)
                continue
            forbidden = [
                (row, col)
                for row, col, other in anchors
                if other is not slot
                and slot.row0 <= row < slot.row1
                and slot.col0 <= col < slot.col1
            ]
            if not forbidden:
                result.append(slot)
                continue
            adjusted = self._largest_anchor_slot_without_points(slot, forbidden)
            if adjusted is not None:
                result.append(adjusted)
        return result

    def _largest_anchor_slot_without_points(
        self, slot: _CellSlot, forbidden: list[tuple[int, int]]
    ) -> _CellSlot | None:
        best: _CellSlot | None = None
        for row1 in range(slot.row0 + 1, slot.row1 + 1):
            for col1 in range(slot.col0 + 1, slot.col1 + 1):
                if any(
                    slot.row0 <= row < row1 and slot.col0 <= col < col1
                    for row, col in forbidden
                ):
                    continue
                candidate = _CellSlot(slot.cell, slot.row0, row1, slot.col0, col1)
                if best is None or self._slot_area(candidate) > self._slot_area(best):
                    best = candidate
        return best

    def _largest_empty_anchor_slot(
        self, grid: list[list[_CellSlot | None]], slot: _CellSlot
    ) -> _CellSlot | None:
        if grid[slot.row0][slot.col0] is not None:
            return None

        best: _CellSlot | None = None
        for row1 in range(slot.row0 + 1, slot.row1 + 1):
            for col1 in range(slot.col0 + 1, slot.col1 + 1):
                candidate = _CellSlot(slot.cell, slot.row0, row1, slot.col0, col1)
                if not self._slot_rect_is_empty(grid, candidate):
                    continue
                if best is None or self._slot_area(candidate) > self._slot_area(best):
                    best = candidate
        return best

    def _ensure_slot_start_indexes(
        self, slots: list[_CellSlot], row_num: int, col_num: int
    ) -> list[_CellSlot]:
        slots = list(slots)
        changed = True
        while changed:
            changed = False
            row_starts = {slot.row0 for slot in slots}
            for row in range(row_num):
                if row in row_starts:
                    continue
                candidates = [slot for slot in slots if slot.row0 < row < slot.row1]
                if not candidates:
                    continue
                slot = min(candidates, key=self._slot_shrink_cost)
                slot.row1 = row
                changed = True
                break
            if changed:
                continue

            col_starts = {slot.col0 for slot in slots}
            for col in range(col_num):
                if col in col_starts:
                    continue
                candidates = [slot for slot in slots if slot.col0 < col < slot.col1]
                if not candidates:
                    continue
                slot = min(candidates, key=self._slot_shrink_cost)
                slot.col1 = col
                changed = True
                break

        return [slot for slot in slots if not self._slot_is_empty(slot)]

    def _fill_slot_holes(
        self,
        slots: list[_CellSlot],
        row_num: int,
        col_num: int,
        x_lines: list[float],
        y_lines: list[float],
    ) -> list[_CellSlot]:
        grid = self._slot_grid(slots, row_num, col_num)
        result = list(slots)
        for row in range(row_num):
            for col in range(col_num):
                if grid[row][col] is not None:
                    continue
                y0 = row_num - row - 1
                y1 = row_num - row
                cell = _Cell(
                    BBox(x_lines[col], y_lines[y0], x_lines[col + 1], y_lines[y1]),
                    generated=True,
                )
                slot = _CellSlot(cell, row, row + 1, col, col + 1)
                result.append(slot)
                grid[row][col] = slot
        return result

    def _slots_to_kcells(
        self,
        page: KPage,
        slots: list[_CellSlot],
        row_num: int,
        x_lines: list[float],
        y_lines: list[float],
    ) -> list[KCell]:
        kcells: list[KCell] = []
        for slot in slots:
            y0 = row_num - slot.row1
            y1 = row_num - slot.row0
            kcells.append(
                KCell(
                    page,
                    BBox(
                        x_lines[slot.col0],
                        y_lines[y0],
                        x_lines[slot.col1],
                        y_lines[y1],
                    ),
                    row_index=slot.row0,
                    col_index=slot.col0,
                    row_span=slot.row1 - slot.row0,
                    col_span=slot.col1 - slot.col0,
                )
            )
        return kcells

    def _slot_grid(
        self, slots: list[_CellSlot], row_num: int, col_num: int
    ) -> list[list[_CellSlot | None]]:
        grid: list[list[_CellSlot | None]] = [
            [None] * col_num for _ in range(row_num)
        ]
        for slot in slots:
            self._place_slot(grid, slot)
        return grid

    def _place_slot(
        self, grid: list[list[_CellSlot | None]], slot: _CellSlot
    ):
        for row in range(slot.row0, slot.row1):
            for col in range(slot.col0, slot.col1):
                grid[row][col] = slot

    def _slot_rect_is_empty(
        self, grid: list[list[_CellSlot | None]], slot: _CellSlot
    ) -> bool:
        for row in range(slot.row0, slot.row1):
            for col in range(slot.col0, slot.col1):
                if grid[row][col] is not None:
                    return False
        return True

    def _slot_order_key(self, slot: _CellSlot):
        return (
            slot.row0,
            slot.col0,
            slot.cell.generated,
            slot.cell.content_bbox is None,
            -self._slot_area(slot),
            -slot.cell.bbox.area,
        )

    def _slot_priority(self, slot: _CellSlot):
        return (
            not slot.cell.generated,
            slot.cell.content_bbox is not None,
            self._slot_area(slot),
            slot.cell.bbox.area,
        )

    def _slot_shrink_cost(self, slot: _CellSlot):
        return (
            slot.cell.content_bbox is not None,
            self._slot_area(slot),
            slot.cell.bbox.area,
        )

    def _slot_area(self, slot: _CellSlot) -> int:
        return (slot.row1 - slot.row0) * (slot.col1 - slot.col0)

    def _slot_is_empty(self, slot: _CellSlot) -> bool:
        return slot.row1 <= slot.row0 or slot.col1 <= slot.col0

    def _absorb_staggered_y_bands(self, cells: list[_Cell]):
        """把上下边界轻微错位造成的薄伪行并入相邻行。"""
        if len(cells) < 2:
            return

        changed = True
        while changed:
            changed = False
            y_lines = self._axis_lines(cells, axis="y")
            if len(y_lines) < 3:
                return

            row_heights = [
                y_lines[i + 1] - y_lines[i]
                for i in range(len(y_lines) - 1)
                if y_lines[i + 1] > y_lines[i]
            ]
            if not row_heights:
                return
            row_heights.sort()
            median_height = row_heights[len(row_heights) // 2]
            max_band_height = max(
                self._edge_tolerance(cells, "y") * 2,
                min(median_height * 0.35, 14.0),
            )

            for line_index in range(1, len(y_lines) - 2):
                band_height = y_lines[line_index + 1] - y_lines[line_index]
                if band_height <= 0 or band_height > max_band_height:
                    continue
                if not self._looks_like_staggered_y_band(
                    cells, y_lines, line_index, median_height
                ):
                    continue
                if self._merge_staggered_y_band(cells, y_lines, line_index):
                    changed = True
                    break

    def _looks_like_staggered_y_band(
        self,
        cells: list[_Cell],
        y_lines: list[float],
        line_index: int,
        median_height: float,
    ) -> bool:
        y0 = y_lines[line_index]
        y1 = y_lines[line_index + 1]
        full_band_cells = [
            cell
            for cell in cells
            if abs(cell.bbox.y0 - y0) < 0.5 and abs(cell.bbox.y1 - y1) < 0.5
        ]
        real_full_band_cells = [cell for cell in full_band_cells if not cell.generated]
        if real_full_band_cells:
            return False

        crossing_cells = [
            cell
            for cell in cells
            if cell.bbox.y0 < y0 - 0.5 and cell.bbox.y1 > y1 + 0.5
        ]
        adjacent_cells = [
            cell
            for cell in cells
            if abs(cell.bbox.y1 - y0) < 0.5 or abs(cell.bbox.y0 - y1) < 0.5
        ]
        if crossing_cells and adjacent_cells:
            return True

        lower_supporters = self._y_line_real_supporters(cells, y0)
        upper_supporters = self._y_line_real_supporters(cells, y1)
        if lower_supporters and upper_supporters:
            return True

        band_content = [
            cell
            for cell in full_band_cells
            if cell.content_bbox is not None
            and cell.content_bbox.height >= median_height * 0.35
        ]
        return not band_content and bool(full_band_cells)

    def _y_line_real_supporters(self, cells: list[_Cell], line: float) -> list[_Cell]:
        return [
            cell
            for cell in cells
            if not cell.generated
            and (abs(cell.bbox.y0 - line) < 0.5 or abs(cell.bbox.y1 - line) < 0.5)
        ]

    def _merge_staggered_y_band(
        self, cells: list[_Cell], y_lines: list[float], line_index: int
    ) -> bool:
        lower_line = y_lines[line_index]
        upper_line = y_lines[line_index + 1]

        lower_score = self._y_line_score(cells, lower_line)
        upper_score = self._y_line_score(cells, upper_line)
        target = lower_line if lower_score >= upper_score else upper_line
        source = upper_line if target == lower_line else lower_line

        old_bboxes = [(cell, cell.bbox) for cell in cells]
        changed = False
        for cell in cells:
            if abs(cell.bbox.y0 - source) < 0.5:
                before = cell.bbox
                self._adjust_edge(cell, Y0, target)
                changed = changed or cell.bbox != before
            if abs(cell.bbox.y1 - source) < 0.5:
                before = cell.bbox
                self._adjust_edge(cell, Y1, target)
                changed = changed or cell.bbox != before

        if not changed or any(cell.bbox.height <= 0 for cell, _ in old_bboxes):
            for cell, bbox in old_bboxes:
                cell.bbox = bbox
            return False
        return True

    def _y_line_score(self, cells: list[_Cell], line: float) -> tuple[int, float]:
        supporters = [
            cell
            for cell in cells
            if abs(cell.bbox.y0 - line) < 0.5 or abs(cell.bbox.y1 - line) < 0.5
        ]
        real_count = sum(0 if cell.generated else 1 for cell in supporters)
        intervals = sorted((cell.bbox.x0, cell.bbox.x1) for cell in supporters)
        coverage = 0.0
        end: float | None = None
        for x0, x1 in intervals:
            if end is None or x0 > end:
                coverage += x1 - x0
                end = x1
            elif x1 > end:
                coverage += x1 - end
                end = x1
        return real_count, coverage


    def _snap_outlier_edges(self, cells: list[_Cell], *, axis: Literal["x", "y"]):
        """把孤立边界吸附到相邻稳定边界，避免形成很窄的伪行/列。"""
        lo_idx, hi_idx = self._axis_indexes(axis)
        tol = self._edge_tolerance(cells, axis)
        clusters = self._cluster_edges(
            [(cell.bbox[lo_idx], cell, lo_idx) for cell in cells]
            + [(cell.bbox[hi_idx], cell, hi_idx) for cell in cells],
            tol,
        )
        if len(clusters) < 3:
            return

        stable = [
            i
            for i, cluster in enumerate(clusters)
            if i == 0 or i == len(clusters) - 1 or len(cluster[1]) >= 2
        ]
        # 如果票数不足以判断稳定线，就不要猜。
        if len(stable) < 2:
            return

        stable_set = set(stable)
        for i, (value, members) in enumerate(clusters):
            if i in stable_set:
                continue
            prev_indexes = [j for j in stable if j < i]
            next_indexes = [j for j in stable if j > i]
            if not prev_indexes or not next_indexes:
                continue

            prev_value = clusters[prev_indexes[-1]][0]
            next_value = clusters[next_indexes[0]][0]
            if next_value <= prev_value:
                continue

            ratio = (value - prev_value) / (next_value - prev_value)
            target = next_value if ratio > 0.5 else prev_value
            for _, cell, edge_idx in members:
                self._adjust_edge(cell, edge_idx, target)

    def _snap_to_grid(self, cells: list[_Cell], *, axis: Literal["x", "y"]):
        """把所有边界吸附到聚类后的网格线。"""
        lo_idx, hi_idx = self._axis_indexes(axis)
        tol = self._edge_tolerance(cells, axis)
        clusters = self._cluster_edges(
            [(cell.bbox[lo_idx], cell, lo_idx) for cell in cells]
            + [(cell.bbox[hi_idx], cell, hi_idx) for cell in cells],
            tol,
        )
        if len(clusters) < 2:
            return

        lines = [value for value, _ in clusters]
        for cell in cells:
            lo = cell.bbox[lo_idx]
            hi = cell.bbox[hi_idx]
            new_lo = min(lines, key=lambda line: abs(line - lo))
            new_hi = min(lines, key=lambda line: abs(line - hi))
            self._adjust_edge(cell, lo_idx, new_lo)
            self._adjust_edge(cell, hi_idx, new_hi)

    def _align_neighbor_y_edges(self, cells: list[_Cell]):
        """对齐相邻列中应属于同一行的上下边界。"""
        if len(cells) < 2:
            return

        tolerance = self._neighbor_y_tolerance(cells)
        x_tolerance = self._edge_tolerance(cells, "x")
        for edge_idx in (Y0, Y1):
            groups: list[set[int]] = []
            edge_to_group: dict[int, int] = {}

            def add_pair(i: int, j: int):
                gi = edge_to_group.get(i)
                gj = edge_to_group.get(j)
                if gi is None and gj is None:
                    edge_to_group[i] = edge_to_group[j] = len(groups)
                    groups.append({i, j})
                elif gi is None and gj is not None:
                    groups[gj].add(i)
                    edge_to_group[i] = gj
                elif gi is not None and gj is None:
                    groups[gi].add(j)
                    edge_to_group[j] = gi
                elif gi is not None and gj is not None and gi != gj:
                    keep, drop = (gi, gj) if gi < gj else (gj, gi)
                    groups[keep].update(groups[drop])
                    for edge_index in groups[drop]:
                        edge_to_group[edge_index] = keep
                    groups[drop].clear()

            for i, c1 in enumerate(cells):
                for j in range(i + 1, len(cells)):
                    c2 = cells[j]
                    if not self._x_neighbors(c1.bbox, c2.bbox, x_tolerance):
                        continue
                    if not self._same_row_band(c1.bbox, c2.bbox):
                        continue
                    if (
                        abs(c1.bbox[edge_idx] - c2.bbox[edge_idx])
                        > tolerance
                    ):
                        continue
                    add_pair(i, j)

            for group in groups:
                if len(group) < 2:
                    continue
                target = self._best_y_edge_target(cells, group, edge_idx, tolerance)
                old_bboxes = [(cells[i], cells[i].bbox) for i in group]
                for i in group:
                    self._adjust_edge(cells[i], edge_idx, target)
                if any(cell.bbox.height <= 0 for cell, _ in old_bboxes):
                    for cell, bbox in old_bboxes:
                        cell.bbox = bbox

    def _neighbor_y_tolerance(self, cells: list[_Cell]) -> float:
        heights = sorted(cell.bbox.height for cell in cells if cell.bbox.height > 0)
        if not heights:
            return 4.0
        q1 = heights[len(heights) // 4]
        median = heights[len(heights) // 2]
        return max(
            self._edge_tolerance(cells, "y") * 2,
            min(q1 * 0.35, median * 0.18, 16.0),
        )

    def _x_neighbors(self, b1: BBox, b2: BBox, tolerance: float) -> bool:
        overlap = min(b1.x1, b2.x1) - max(b1.x0, b2.x0)
        if overlap > min(b1.width, b2.width) * 0.15:
            return True
        gap = max(b1.x0 - b2.x1, b2.x0 - b1.x1, 0)
        return gap <= tolerance

    def _same_row_band(self, b1: BBox, b2: BBox) -> bool:
        overlap = min(b1.y1, b2.y1) - max(b1.y0, b2.y0)
        if overlap <= 0:
            return False
        return overlap / min(b1.height, b2.height) >= 0.65

    def _best_y_edge_target(
        self,
        cells: list[_Cell],
        group: set[int],
        edge_idx: _EdgeIndex,
        tolerance: float,
    ) -> float:
        candidates = [cells[i].bbox[edge_idx] for i in group]

        def score(value: float) -> tuple[int, float]:
            supporters = [
                cell
                for cell in cells
                if abs(cell.bbox[edge_idx] - value) <= tolerance
            ]
            intervals = sorted((cell.bbox.x0, cell.bbox.x1) for cell in supporters)
            coverage = 0.0
            end: float | None = None
            for x0, x1 in intervals:
                if end is None or x0 > end:
                    coverage += x1 - x0
                    end = x1
                elif x1 > end:
                    coverage += x1 - end
                    end = x1
            return len(supporters), coverage

        return max(candidates, key=score)

    def _fill_missing(self, cells: list[_Cell]):
        x_lines = self._axis_lines(cells, axis="x")
        y_lines = self._axis_lines(cells, axis="y")
        if len(x_lines) < 2 or len(y_lines) < 2:
            return

        rows = len(y_lines) - 1
        cols = len(x_lines) - 1
        covered = [[False] * cols for _ in range(rows)]

        for cell in cells:
            c0 = self._nearest_index(cell.bbox.x0, x_lines)
            c1 = self._nearest_index(cell.bbox.x1, x_lines)
            r0 = self._nearest_index(cell.bbox.y0, y_lines)
            r1 = self._nearest_index(cell.bbox.y1, y_lines)
            if c1 <= c0:
                c1 = c0 + 1
            if r1 <= r0:
                r1 = r0 + 1
            c0 = max(0, min(c0, cols - 1))
            c1 = max(c0 + 1, min(c1, cols))
            r0 = max(0, min(r0, rows - 1))
            r1 = max(r0 + 1, min(r1, rows))
            cell.bbox = BBox(x_lines[c0], y_lines[r0], x_lines[c1], y_lines[r1])
            for r in range(r0, r1):
                for c in range(c0, c1):
                    covered[r][c] = True

        visited = [[False] * cols for _ in range(rows)]
        for r in range(rows):
            for c in range(cols):
                if covered[r][c] or visited[r][c]:
                    continue
                c_end = c
                while (
                    c_end < cols
                    and not covered[r][c_end]
                    and not visited[r][c_end]
                ):
                    c_end += 1
                r_end = r + 1
                while r_end < rows:
                    if any(
                        covered[r_end][cc] or visited[r_end][cc]
                        for cc in range(c, c_end)
                    ):
                        break
                    r_end += 1

                for rr in range(r, r_end):
                    for cc in range(c, c_end):
                        visited[rr][cc] = True
                if self._align_missing(
                    cells, covered, x_lines, y_lines, r, c, r_end, c_end
                ):
                    continue
                if self._absorb_missing_gap(
                    cells, covered, x_lines, y_lines, r, c, r_end, c_end
                ):
                    continue
                cells.append(
                    _Cell(
                        BBox(x_lines[c], y_lines[r], x_lines[c_end], y_lines[r_end]),
                        generated=True,
                    )
                )

    def _repair_staggered_generated_cells(
        self, cells: list[_Cell], *, axis: Literal["x", "y"]
    ):
        """消除交叉跨行/跨列造成的伪补格。"""
        changed = True
        while changed:
            changed = False
            for gap in list(cells):
                if (
                    gap not in cells
                    or not gap.generated
                    or gap.content_bbox is not None
                ):
                    continue
                plan = self._staggered_generated_cell_plan(cells, gap, axis=axis)
                if plan is None:
                    continue

                cell, attr, target = plan
                old_bbox = cell.bbox
                self._adjust_edge(cell, attr, target)
                if not self._bbox_contains(cell.bbox, gap.bbox, tolerance=0.5):
                    cell.bbox = old_bbox
                    continue
                if self._generated_gap_has_conflict(cells, gap, cell):
                    cell.bbox = old_bbox
                    continue

                cells.remove(gap)
                changed = True
                break

    def _staggered_generated_cell_plan(
        self,
        cells: list[_Cell],
        gap: _Cell,
        *,
        axis: Literal["x", "y"],
    ) -> tuple[_Cell, _EdgeIndex, float] | None:
        lo_idx, hi_idx, cross_lo_idx, cross_hi_idx = self._stagger_axis_indexes(
            axis
        )
        tolerance = self._edge_tolerance(cells, axis)
        candidates: list[tuple[float, _Cell, _EdgeIndex, float]] = []

        for cell in cells:
            if cell is gap or cell.generated:
                continue
            if (
                self._axis_overlap_ratio(
                    cell.bbox, gap.bbox, cross_lo_idx, cross_hi_idx
                )
                < 0.8
            ):
                continue

            if (
                abs(cell.bbox[lo_idx] - gap.bbox[hi_idx])
                <= tolerance
            ):
                boundary = cell.original_bbox[lo_idx]
                if self._has_stagger_cross_evidence(
                    cells,
                    gap,
                    boundary,
                    axis=axis,
                    ignore=cell,
                ):
                    candidates.append(
                        (
                            self._stagger_merge_score(cell, gap, axis),
                            cell,
                            lo_idx,
                            gap.bbox[lo_idx],
                        )
                    )
            if (
                abs(cell.bbox[hi_idx] - gap.bbox[lo_idx])
                <= tolerance
            ):
                boundary = cell.original_bbox[hi_idx]
                if self._has_stagger_cross_evidence(
                    cells,
                    gap,
                    boundary,
                    axis=axis,
                    ignore=cell,
                ):
                    candidates.append(
                        (
                            self._stagger_merge_score(cell, gap, axis),
                            cell,
                            hi_idx,
                            gap.bbox[hi_idx],
                        )
                    )

        if not candidates:
            return None
        _, cell, attr, target = max(candidates, key=lambda item: item[0])
        return cell, attr, target

    def _has_stagger_cross_evidence(
        self,
        cells: list[_Cell],
        gap: _Cell,
        boundary: float,
        *,
        axis: Literal["x", "y"],
        ignore: _Cell,
    ) -> bool:
        lo_idx, hi_idx, cross_lo_idx, cross_hi_idx = self._stagger_axis_indexes(axis)
        tolerance = self._edge_tolerance(cells, axis)
        cross_tolerance = self._edge_tolerance(cells, "x" if axis == "y" else "y")

        for cell in cells:
            if cell is gap or cell is ignore:
                continue
            if not (
                cell.bbox[lo_idx] < boundary - tolerance
                and cell.bbox[hi_idx] > boundary + tolerance
            ):
                continue
            if (
                self._axis_overlap_ratio(
                    cell.bbox, gap.bbox, cross_lo_idx, cross_hi_idx
                )
                > 0.15
            ):
                continue
            cross_gap = max(
                gap.bbox[cross_lo_idx] - cell.bbox[cross_hi_idx],
                cell.bbox[cross_lo_idx] - gap.bbox[cross_hi_idx],
                0,
            )
            if cross_gap <= cross_tolerance:
                return True
        return False

    def _stagger_merge_score(
        self, cell: _Cell, gap: _Cell, axis: Literal["x", "y"]
    ) -> float:
        lo_idx, hi_idx, _, _ = self._stagger_axis_indexes(axis)
        cell_size = cell.bbox[hi_idx] - cell.bbox[lo_idx]
        gap_size = gap.bbox[hi_idx] - gap.bbox[lo_idx]
        content_bonus = 1.0 if cell.content_bbox is not None else 0.0
        return cell_size - gap_size + content_bonus

    def _generated_gap_has_conflict(
        self, cells: list[_Cell], gap: _Cell, merged_cell: _Cell
    ) -> bool:
        for cell in cells:
            if cell is gap or cell is merged_cell:
                continue
            overlap = cell.bbox.intersect(gap.bbox)
            if overlap is None:
                continue
            if overlap.area / min(cell.bbox.area, gap.bbox.area) > 0.1:
                return True
        return False

    def _bbox_contains(
        self, outer: BBox, inner: BBox, *, tolerance: float = 0.0
    ) -> bool:
        return (
            outer.x0 <= inner.x0 + tolerance
            and outer.y0 <= inner.y0 + tolerance
            and outer.x1 >= inner.x1 - tolerance
            and outer.y1 >= inner.y1 - tolerance
        )

    def _axis_overlap_ratio(
        self,
        b1: BBox,
        b2: BBox,
        lo_idx: _EdgeIndex,
        hi_idx: _EdgeIndex,
    ) -> float:
        overlap = min(b1[hi_idx], b2[hi_idx]) - max(b1[lo_idx], b2[lo_idx])
        if overlap <= 0:
            return 0.0
        size = min(
            b1[hi_idx] - b1[lo_idx],
            b2[hi_idx] - b2[lo_idx],
        )
        return float(overlap / size) if size > 0 else 0.0

    def _stagger_axis_indexes(
        self, axis: Literal["x", "y"]
    ) -> tuple[_EdgeIndex, _EdgeIndex, _EdgeIndex, _EdgeIndex]:
        if axis == "y":
            return Y0, Y1, X0, X1
        return X0, X1, Y0, Y1

    def _align_missing(
        self,
        cells: list[_Cell],
        covered: list[list[bool]],
        x_lines: list[float],
        y_lines: list[float],
        r0: int,
        c0: int,
        r1: int,
        c1: int,
    ) -> bool:
        """把边界错位造成的空洞并入相邻cell，而不是创建伪空cell。"""
        bbox = BBox(x_lines[c0], y_lines[r0], x_lines[c1], y_lines[r1])
        if not self._looks_like_alignment_gap(
            bbox, x_lines, y_lines, r0, c0, r1, c1
        ):
            return False

        plans = [
            plan
            for plan in (
                self._alignment_plan(cells, x_lines, y_lines, r0, c0, r1, c1, Y1),
                self._alignment_plan(cells, x_lines, y_lines, r0, c0, r1, c1, Y0),
                self._alignment_plan(cells, x_lines, y_lines, r0, c0, r1, c1, X1),
                self._alignment_plan(cells, x_lines, y_lines, r0, c0, r1, c1, X0),
            )
            if plan is not None
        ]
        if not plans:
            return False

        for _, adjustments in sorted(plans, key=lambda item: item[0]):
            old_bboxes = [(cell, cell.bbox) for cell, _, _ in adjustments]
            for cell, edge_idx, target in adjustments:
                self._adjust_edge(cell, edge_idx, target)
            edge_idx = adjustments[0][1]
            if self._removed_gap_line(
                cells, x_lines, y_lines, edge_idx, r0, c0, r1, c1
            ):
                for rr in range(r0, r1):
                    for cc in range(c0, c1):
                        covered[rr][cc] = True
                return True
            for cell, old_bbox in old_bboxes:
                cell.bbox = old_bbox
        return False

    def _absorb_missing_gap(
        self,
        cells: list[_Cell],
        covered: list[list[bool]],
        x_lines: list[float],
        y_lines: list[float],
        r0: int,
        c0: int,
        r1: int,
        c1: int,
    ) -> bool:
        bbox = BBox(x_lines[c0], y_lines[r0], x_lines[c1], y_lines[r1])
        cols = len(x_lines) - 1
        rows = len(y_lines) - 1
        if not self._is_thin_missing_gap(bbox, x_lines, y_lines):
            return False

        if bbox.height <= bbox.width:
            plans = [
                self._absorb_horizontal_gap_plan(
                    cells, x_lines, y_lines, r0, c0, r1, c1, "upper"
                ),
                self._absorb_horizontal_gap_plan(
                    cells, x_lines, y_lines, r0, c0, r1, c1, "lower"
                ),
            ]
        else:
            plans = [
                self._absorb_vertical_gap_plan(
                    cells, x_lines, y_lines, r0, c0, r1, c1, "left"
                ),
                self._absorb_vertical_gap_plan(
                    cells, x_lines, y_lines, r0, c0, r1, c1, "right"
                ),
            ]
        plans = [plan for plan in plans if plan is not None]
        if not plans:
            return False

        _, adjustments = min(plans, key=lambda item: item[0])
        old_bboxes = [(cell, cell.bbox) for cell, _, _ in adjustments]
        for cell, edge_idx, target in adjustments:
            self._adjust_edge(cell, edge_idx, target)
        if any(cell.bbox.width <= 0 or cell.bbox.height <= 0 for cell, _ in old_bboxes):
            for cell, old_bbox in old_bboxes:
                cell.bbox = old_bbox
            return False

        r1 = min(r1, rows)
        c1 = min(c1, cols)
        for rr in range(r0, r1):
            for cc in range(c0, c1):
                covered[rr][cc] = True
        return True

    def _is_thin_missing_gap(
        self, bbox: BBox, x_lines: list[float], y_lines: list[float]
    ) -> bool:
        cols = len(x_lines) - 1
        rows = len(y_lines) - 1
        if cols <= 0 or rows <= 0:
            return False
        col_widths = sorted(
            x_lines[i + 1] - x_lines[i]
            for i in range(cols)
            if x_lines[i + 1] > x_lines[i]
        )
        row_heights = sorted(
            y_lines[i + 1] - y_lines[i]
            for i in range(rows)
            if y_lines[i + 1] > y_lines[i]
        )
        if not col_widths or not row_heights:
            return False
        median_col_width = col_widths[len(col_widths) // 2]
        median_row_height = row_heights[len(row_heights) // 2]
        return (
            bbox.height <= max(3.0, min(median_row_height * 0.25, 12.0))
            or bbox.width <= max(3.0, min(median_col_width * 0.18, 12.0))
        )

    def _absorb_horizontal_gap_plan(
        self,
        cells: list[_Cell],
        x_lines: list[float],
        y_lines: list[float],
        r0: int,
        c0: int,
        r1: int,
        c1: int,
        side: Literal["upper", "lower"],
    ) -> tuple[float, list[tuple[_Cell, _EdgeIndex, float]]] | None:
        if side == "upper":
            if r1 >= len(y_lines) - 1:
                return None
            line_index = r1
            edge_idx = Y0
            target = y_lines[r0]
        else:
            if r0 <= 0:
                return None
            line_index = r0
            edge_idx = Y1
            target = y_lines[r1]
        intervals = self._edge_intervals(cells, x_lines, y_lines, edge_idx, line_index)
        adjustments = self._covering_adjustments(intervals, c0, c1, edge_idx, target)
        if adjustments is None:
            return None
        return self._absorb_adjustments_cost(adjustments), adjustments

    def _absorb_vertical_gap_plan(
        self,
        cells: list[_Cell],
        x_lines: list[float],
        y_lines: list[float],
        r0: int,
        c0: int,
        r1: int,
        c1: int,
        side: Literal["left", "right"],
    ) -> tuple[float, list[tuple[_Cell, _EdgeIndex, float]]] | None:
        if side == "left":
            if c0 <= 0:
                return None
            line_index = c0
            edge_idx = X1
            target = x_lines[c1]
        else:
            if c1 >= len(x_lines) - 1:
                return None
            line_index = c1
            edge_idx = X0
            target = x_lines[c0]
        intervals = self._edge_intervals(cells, x_lines, y_lines, edge_idx, line_index)
        adjustments = self._covering_adjustments(intervals, r0, r1, edge_idx, target)
        if adjustments is None:
            return None
        return self._absorb_adjustments_cost(adjustments), adjustments

    def _absorb_adjustments_cost(
        self, adjustments: list[tuple[_Cell, _EdgeIndex, float]]
    ) -> float:
        cost = 0.0
        for cell, edge_idx, target in adjustments:
            old_value = cell.bbox[edge_idx]
            generated_discount = 0.25 if cell.generated else 1.0
            content_penalty = 2.0 if cell.content_bbox is not None else 1.0
            cost += abs(target - old_value) * generated_discount * content_penalty
        return cost

    def _alignment_plan(
        self,
        cells: list[_Cell],
        x_lines: list[float],
        y_lines: list[float],
        r0: int,
        c0: int,
        r1: int,
        c1: int,
        edge_idx: _EdgeIndex,
    ) -> tuple[float, list[tuple[_Cell, _EdgeIndex, float]]] | None:
        if edge_idx == Y0:
            target = y_lines[r0]
            intervals = self._edge_intervals(cells, x_lines, y_lines, Y0, r1)
            adjustments = self._covering_adjustments(
                intervals, c0, c1, edge_idx, target
            )
            if adjustments is None:
                return None
            return y_lines[r1] - y_lines[r0], adjustments
        if edge_idx == Y1:
            target = y_lines[r1]
            intervals = self._edge_intervals(cells, x_lines, y_lines, Y1, r0)
            adjustments = self._covering_adjustments(
                intervals, c0, c1, edge_idx, target
            )
            if adjustments is None:
                return None
            return y_lines[r1] - y_lines[r0], adjustments
        if edge_idx == X0:
            target = x_lines[c0]
            intervals = self._edge_intervals(cells, x_lines, y_lines, X0, c1)
            adjustments = self._covering_adjustments(
                intervals, r0, r1, edge_idx, target
            )
            if adjustments is None:
                return None
            return x_lines[c1] - x_lines[c0], adjustments

        target = x_lines[c1]
        intervals = self._edge_intervals(cells, x_lines, y_lines, X1, c0)
        adjustments = self._covering_adjustments(intervals, r0, r1, edge_idx, target)
        if adjustments is None:
            return None
        return x_lines[c1] - x_lines[c0], adjustments

    def _edge_intervals(
        self,
        cells: list[_Cell],
        x_lines: list[float],
        y_lines: list[float],
        edge_idx: _EdgeIndex,
        line_index: int,
    ) -> list[tuple[int, int, _Cell]]:
        intervals: list[tuple[int, int, _Cell]] = []
        for cell in cells:
            c0 = self._nearest_index(cell.bbox[X0], x_lines)
            c1 = self._nearest_index(cell.bbox[X1], x_lines)
            r0 = self._nearest_index(cell.bbox[Y0], y_lines)
            r1 = self._nearest_index(cell.bbox[Y1], y_lines)
            if edge_idx == Y0 and r0 == line_index:
                intervals.append((c0, c1, cell))
            elif edge_idx == Y1 and r1 == line_index:
                intervals.append((c0, c1, cell))
            elif edge_idx == X0 and c0 == line_index:
                intervals.append((r0, r1, cell))
            elif edge_idx == X1 and c1 == line_index:
                intervals.append((r0, r1, cell))
        return intervals

    def _covering_adjustments(
        self,
        intervals: list[tuple[int, int, _Cell]],
        start: int,
        end: int,
        edge_idx: _EdgeIndex,
        target: float,
    ) -> list[tuple[_Cell, _EdgeIndex, float]] | None:
        adjustments: list[tuple[_Cell, _EdgeIndex, float]] = []
        cursor = start
        for i0, i1, cell in sorted(intervals, key=lambda item: item[0]):
            if i1 <= cursor:
                continue
            if i0 > cursor:
                break
            adjustments.append((cell, edge_idx, target))
            cursor = max(cursor, i1)
            if cursor >= end:
                return adjustments
        return None

    def _removed_gap_line(
        self,
        cells: list[_Cell],
        x_lines: list[float],
        y_lines: list[float],
        edge_idx: _EdgeIndex,
        r0: int,
        c0: int,
        r1: int,
        c1: int,
    ) -> bool:
        if edge_idx == Y0:
            return not self._line_exists(
                y_lines[r1], self._axis_lines(cells, axis="y")
            )
        if edge_idx == Y1:
            return not self._line_exists(
                y_lines[r0], self._axis_lines(cells, axis="y")
            )
        if edge_idx == X0:
            return not self._line_exists(
                x_lines[c1], self._axis_lines(cells, axis="x")
            )
        if edge_idx == X1:
            return not self._line_exists(
                x_lines[c0], self._axis_lines(cells, axis="x")
            )
        return False

    def _line_exists(self, line: float, lines: list[float]) -> bool:
        return any(abs(value - line) < 0.5 for value in lines)

    def _looks_like_alignment_gap(
        self,
        bbox: BBox,
        x_lines: list[float],
        y_lines: list[float],
        r0: int,
        c0: int,
        r1: int,
        c1: int,
    ) -> bool:
        rows = len(y_lines) - 1
        cols = len(x_lines) - 1
        col_widths = [x_lines[i + 1] - x_lines[i] for i in range(cols)]
        row_heights = [y_lines[i + 1] - y_lines[i] for i in range(rows)]
        col_widths.sort()
        row_heights.sort()
        median_col_width = col_widths[len(col_widths) // 2]
        median_row_height = row_heights[len(row_heights) // 2]
        narrow = (
            bbox.width <= median_col_width * 0.25
            or bbox.height <= median_row_height * 0.25
        )
        if narrow:
            return True

        on_edge = r0 == 0 or r1 == rows or c0 == 0 or c1 == cols
        if not on_edge:
            return False
        return (
            bbox.width <= median_col_width * 0.5
            or bbox.height <= median_row_height * 0.5
        )

    def _axis_indexes(self, axis: Literal["x", "y"]) -> tuple[_EdgeIndex, _EdgeIndex]:
        if axis == "x":
            return X0, X1
        return Y0, Y1

    def _axis_lines(
        self, cells: list[_Cell], *, axis: Literal["x", "y"]
    ) -> list[float]:
        lo_idx, hi_idx = self._axis_indexes(axis)
        clusters = self._cluster_edges(
            [(cell.bbox[lo_idx], cell, lo_idx) for cell in cells]
            + [(cell.bbox[hi_idx], cell, hi_idx) for cell in cells],
            self._edge_tolerance(cells, axis),
        )
        return [value for value, _ in clusters]

    def _adjust_edge(self, cell: _Cell, edge_idx: _EdgeIndex, target: float):
        if cell.content_bbox is not None:
            if edge_idx in (X0, Y0):
                target = min(target, cell.content_bbox[edge_idx])
            else:
                target = max(target, cell.content_bbox[edge_idx])
        values = list(cell.bbox)
        values[edge_idx] = target
        bbox = BBox(*values)
        if bbox.width > 0 and bbox.height > 0:
            cell.bbox = bbox

    def _edge_tolerance(self, cells: list[_Cell], axis: Literal["x", "y"]) -> float:
        lo_idx, hi_idx = self._axis_indexes(axis)
        sizes = sorted(
            cell.bbox[hi_idx] - cell.bbox[lo_idx]
            for cell in cells
            if cell.bbox[hi_idx] > cell.bbox[lo_idx]
        )
        if not sizes:
            return 2.0
        base = sizes[len(sizes) // 4]
        median = sizes[len(sizes) // 2]
        return max(2.0, min(base * 0.25, median * 0.12))

    def _cluster_edges(
        self,
        values: list[tuple[float, _Cell, _EdgeIndex]],
        tolerance: float,
    ) -> list[tuple[float, list[tuple[float, _Cell, _EdgeIndex]]]]:
        if not values:
            return []
        values = sorted(values, key=lambda item: item[0])
        clusters: list[list[tuple[float, _Cell, _EdgeIndex]]] = [[values[0]]]
        for item in values[1:]:
            if item[0] - clusters[-1][-1][0] <= tolerance:
                clusters[-1].append(item)
            else:
                clusters.append([item])

        result: list[tuple[float, list[tuple[float, _Cell, _EdgeIndex]]]] = []
        for cluster in clusters:
            cluster_values = [item[0] for item in cluster]
            value = sum(cluster_values) / len(cluster_values)
            result.append((value, cluster))
        return result

    def _nearest_index(self, value: float, lines: list[float]) -> int:
        return min(range(len(lines)), key=lambda i: abs(lines[i] - value))


class _JoinMode(StrEnum):
    NO=auto()
    """不能够合并"""

    PAGE=auto()
    """跨页"""
    COLUMN=auto()
    """跨栏"""
    PAGE_COLUMN=auto()
    """跨页且跨栏"""
class XParser:
    """处理跨页/跨栏合并"""
    _logger:Final=logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger:Final=XDebugger(f'{__module__}.{__qualname__}')
    def __init__(self,manager:ModelManager):
        super().__init__()
    
    def parse(self,doc:KDocument):

        def check_chart_layout(t1:KTable,t2:KTable):
            c1=t1.chart_layout
            c2=t2.chart_layout
            if c1 is None and c2 is None:
                #普通表格，返回False表示按普通处理
                return False
            elif c1 is None and c2 is not None:
                return True
            elif c1 is not None and c2 is None:
                return True
            elif c1 is not None and c2 is not None:
                #两个都是图表
                if c1.is_title() and c2.no_title():
                    self._align(t1,t2)
                    return True
                elif c1.no_source() and c2.is_source():
                    self._align(t1,t2)
                    return True
                else:
                    #不需要执行对齐
                    return True
            else:
                return False
        objects:list[KObject]=[]
        for page in doc.working_pages:
            objects.extend(page.objects)
        
        seqs:dict[int,int]={}
        for i in range(1,len(objects)):
            #TODO 2个解析还是一次性获得全部？
            t1 = objects[i-1]
            t2 = objects[i]
            if isinstance(t1,KTable) and isinstance(t2,KTable):
                mode=self._join(t1,t2)
                if t1.is_layout() and t2.is_layout() and t1.col_num==t2.col_num and mode==_JoinMode.PAGE:
                    #如果是整页使用表格布局，只需要支持跨页
                    #如果是局部，如：图表等，需要支持跨栏/跨页/跨页跨栏
                    self._align(t1,t2)
                elif t1.is_wbk() and t2.is_wbk() and mode==_JoinMode.PAGE:
                    #如果都是无边框，且可以合并，先快速对齐，如果没有，重新解析
                    if check_chart_layout(t1,t2):
                        pass
                    elif not self._align(t1,t2):
                        self._parse([t1,t2],seqs)
                elif t1.is_ybk() and t2.is_wbk() and mode==_JoinMode.PAGE:
                    #t1和t2有一个有边框，有一个使用无边框，如：彩色表格会出现这种
                    #TODO 如果无法对齐，合并在一起解析？
                    self._align(t1,t2)
                elif t1.is_wbk() and t2.is_ybk() and mode==_JoinMode.PAGE:
                    #TODO 如果无法对齐，合并在一起解析？
                    self._align(t1,t2)
                else:
                    #跨栏/跨页跨栏等，暂时不处理
                    pass
        pass

    
    def _join(self,t1:KTable,t2:KTable)->_JoinMode:
        """判断2个表格是否可以合并"""
        if abs(t1.bbox.width - t2.bbox.width) >= 10:
            #宽度差别过大
            return _JoinMode.NO

        if t1.page.number == t2.page.number:
            # 同页分栏
            #    |[t2]
            # [t1]|
            # 极端情况
            # [t1]|[t2]
            s1 = t1.page.get_section(t1)
            s2 = t2.page.get_section(t2)
            if s1 is not s2:
                return _JoinMode.NO

            c1 = s1.get_column(t1.bbox)
            c2 = s2.get_column(t2.bbox)
            if c1 is None or c2 is None:
                return _JoinMode.NO

            if c1.index + 1 != c2.index:
                return _JoinMode.NO

            if c2.bbox.y1 <= c1.bbox.y1 - 30:
                return _JoinMode.NO

            return _JoinMode.COLUMN

        elif t1.page.number + 1 == t2.page.number:
            # 所在的页面必须一致
            if abs(t1.page.width - t2.page.width) >= 5:
                return _JoinMode.NO

            # local/宁波核查文档解析-问题排查文件/pdfs/2023-03-13_国网国际融资租赁有限公司2023年度第二期超短期融资券募集说明书.pdf 54-55
            # 差距比较大

            # 跨页不分栏
            # 跨页分栏

            s1 = t1.page.get_section(t1)
            s2 = t2.page.get_section(t2)
            if not s1.alike(s2):
                return _JoinMode.NO

            c1 = s1.get_column(t1.bbox)
            c2 = s2.get_column(t2.bbox)
            if c1 is None or c2 is None:
                return _JoinMode.NO

            # t1比较和页面底部，t2比较和页面顶部的距离？

            # 跨页不分栏
            if c1.index == c2.index and c1.index == 0:
                # --t1--
                # -------
                # --t2--
                return _JoinMode.PAGE

            elif c1.index + 1 == s1.col_num and c2.index == 0:
                # 跨页分栏
                # ---|t1
                # ---------跨页
                # -t2|--
                return _JoinMode.PAGE_COLUMN

            else:
                return _JoinMode.NO
        else:
            return _JoinMode.NO


    def _align(self,t1:KTable,t2:KTable)->bool:
        """快速的对齐，合适列数一致的表格"""

        if not t1.bbox.align('x',t2.bbox,d=5):
            #不允许跨栏，必须对齐
            return False

        #可能误差会大一些，特别是无边框表格
        if abs(t1.bbox.width-t2.bbox.width)>=10:
            return False
        
        if t1.col_num!=t2.col_num:
            return False
        
        axis:list[float]=[]
        axis.append(min(t1.bbox.x0,t2.bbox.x0))
        for i in range(t1.col_num):
            col1 = [c for c in t1.cells if c.col_index==i and c.col_span==1]
            col2 = [c for c in t2.cells if c.col_index==i and c.col_span==1]
            assert len(col1)>0
            assert len(col2)>0
            #判断这两列是否可以对齐
            #如果有垂直线限制的，多数都对齐了，如果没有
            b1=BBox.join2(col1)
            b2=BBox.join2(col2)
            if b1.over('x',b2,d=5,min_len=5):
                #TODO 总是使用t1的为主
                if t1.is_ybk():
                    #如果t1为有边框，就是总是使用他
                    x1=b1.x1
                elif t2.is_ybk():
                    x1=b2.x1
                else:
                    #x0=min(b1.x0,b2.x0)
                    x1=max(b1.x1,b2.x1)
                if axis[-1]<x1:
                    #TODO 严格的判断是否还符合content_bbox的限制
                    axis.append(x1)
                else:
                    break
            else:
                break
        
        if len(axis)!=t1.col_num+1:
            return False
        
        changed=False
        for t in [t1,t2]:
            if t.is_wbk():
                #TODO 仅仅改变无边框的
                changed=t.align(x_axis=axis) or changed
        return changed
        

     
    def _parse(self,tables:Sequence[KTable],seqs:dict[int,int]):
        #目前仅仅仅仅支持跨页且不跨栏的

        doc:Final= tables[0].page.doc
        first_page:Final = tables[0].page
        debugger=self._debugger.bind()
        

        #多个表格合并在一个，使用一个逻辑坐标，左下角为原点
        #[--t1--]
        #[--t2--]
        table_bboxes:list[BBox]=[]
        table_cells:list[list[_Cell]]=[]

        y0=0
        for table in reversed(tables):
            m=Matrix().translate(0,-table.bbox.y0+y0)
            tb=table.bbox.adjust(y0=y0,y1=y0+table.bbox.height)
            #如果是来自纯无边框解析的，有这个存在，如果是来自手动无边框的，就没有
            #必须有cells=[_Cell(),_Cell()]
            cells:list[_Cell]=table.cache.get('cells') or [_Cell(c.bbox) for c in table.cells]
            new_cells:list[_Cell]=[]
            for cell in cells:
                cell = cell.copy()
                cell.bbox=cell.bbox.transform(m)
                if cell.content_bbox:
                    cell.content_bbox = cell.content_bbox.transform(m)
                new_cells.append(cell)
            
            y0=tb.y1
            table_bboxes.insert(0,tb)
            table_cells.insert(0,new_cells)
        
        
        page_width:Final = max(table.page.width for table in tables)
        #合并后的表格的bbox
        table_bbox:Final= BBox.join(table_bboxes)
        imgs:list[PIL.Image.Image]=[]
        for table,cells in zip(tables,table_cells):
            bbox = table.page.bbox.adjust(y0=table.bbox.y0,y1=table.bbox.y1)
            img = table.page.crop(bbox)
            assert img is not None
            imgs.append(img)
        
        
        #之前的单元格
        debug_imgs:list[PIL.Image.Image]=[]
        cells = lists.flat(table_cells)
        if debugger.allow('draw'):
            debug_imgs.append(self._draw(page_width,table_bbox.height,imgs,cells))
        #调整后的单元格
        cells=_Builder().build(cells)
        if debugger.allow('draw'):
            debug_imgs.append(self._draw(page_width,table_bbox.height,imgs,cells))
        
        def rebuild_table(table:KTable,bbox:BBox,cells:Sequence[_Cell]):
            #获得这个表格对应的cells
            #[--t1--] =>table_bbox为在合并后的逻辑表格中的位置
            #---------
            #[--t2--]

            #列坐标不需要变换，行坐标需要重新计算
            #但是目前仅仅是为了列对齐，所以只需要列坐标
            m=Matrix().translate(0,-bbox.y0+table.bbox.y0)
            new_table_bbox = bbox.transform(m)
            new_cells:list[_Cell]=[]
            for cell in cells:
                new_cell = _Cell(cell.bbox.transform(m))
                new_cells.append(new_cell)
            new_table=_Builder().make_table(table.page,new_table_bbox,new_cells)
            #两个表格的列对比？或者直接使用新的new_table，然后替换之前的对象？
            #new_table.page.draw(('page',None),('table',new_table.get_lines2()),line_width=4).show()
            #对齐到这个表格？
            new_table.subtype='ybk'
            changed=self._align(new_table,table)
            if changed:
                self._logger.warning('第%s页，对齐表格成功',new_table.page.number)

        #然后分成
        for table,tb in zip(tables,table_bboxes):
            rebuild_table(table,tb,tb.get(cells,ratio=0.8,remove=True))


        if first_page.number not in seqs:
            seqs[first_page.number]=0
        else:
            seqs[first_page.number]+=seqs[first_page.number]
        idx=seqs[first_page.number]
        if debug_imgs:
            #合并多个图片为一个，可能在一个页面中，有多个跨栏合并
            images.hmerge(*debug_imgs,gap=10,file=doc.debug_dir/'default/wbk/xparse'/f'{first_page.number}-{idx}.png')
        
    
    def _draw(self,width:float,height:float,imgs:Sequence[PIL.Image.Image],cells:Sequence[_Cell]):
        img=images.vmerge(*imgs,gap=1,bg_color=(255,255,0))
        draw = PIL.ImageDraw.Draw(img)
        m=Matrix().lb_to_lt((width,height),img.size)
        for cell in cells:
            draw.rectangle(cell.bbox.transform(m).to_tuple(),outline=(0,0,255))

        return img
        

