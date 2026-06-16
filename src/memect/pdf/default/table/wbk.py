import logging
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from typing import Any, Callable, Final, Literal, Sequence

from memect.base import lists
from memect.base.bbox import BBox
from memect.base.debug import XDebugger
from memect.base.matrix import Matrix
from memect.pdf.base import (
    KBlock,
    KCell,
    KChar,
    KDocument,
    KLine,
    KObject,
    KPage,
    KTable,
    KText,
    VObject,
)
from memect.pdf.model import ModelManager

from .filler import Result, TableFiller
from .ybk import YBKMode


class _Cell:
    """目的是为了方便调整bbox，而且知道原对象"""

    def __init__(self, source: Any, *, generated: bool = False):
        super().__init__()

        self.bbox: BBox = source.bbox.large if hasattr(source, "bbox") else source
        self.original_bbox: Final = self.bbox
        """模型原始bbox，用于后续判断边界被吸附前的错位关系"""
        self.content_bbox:BBox|None=None
        """如果设置了，表示为单元格的内容bbox"""
        self.source: Final = source
        self.generated: Final = generated
        """表示这个cell是Builder补出来的空格，不是模型直接检测出来的格子"""



class WBKMode(StrEnum):
    ALL = "all"
    """所有的表格都使用无边框解析"""
    AUTO = "auto"
    """如果有pdf的线，且结构接近，就使用有边框的结构"""


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
                ybk_ok=0
                ok=0
                for i in range(row_num):
                    row1 = ybk.get_row(i)
                    row2 = wbk.get_row(i)
                    if len(row1)==len(row2):
                        ok+=1
                    elif len(row1)==1 and len(row2)==col_num:
                        wbk_ok+=1
                    elif len(row2)==1 and len(row1)==col_num:
                        ybk_ok+=1
                
                if wbk_ok-ybk_ok>=2:
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
            table = table.strip()
            
            pass

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
        cells = Builder().build(cells)
        self._adjust_items(cells)
        if cells:
            bbox = BBox.join2(cells)
        table = Builder().make_table(page,bbox,cells)
        table.vobject = vobject
        table.subtype = "wbk"
        table.cache["result"] = result

        if steps is not None:
            steps.extend(
                [
                    ("page", None),
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
                            print('=====>KK',c1.bbox,c2.bbox)
                            c1.bbox = c1.bbox.adjust(y0=c2.bbox.y1 + 1)
                            print('=====>k2',c1.bbox,c2.bbox)
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


class Builder:
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
        
        if not cells:
            return KTable(page, table_bbox,cells=[KCell(page, table_bbox, row_index=0, col_index=0)])

        x_lines = self._axis_lines(cells, axis="x")
        y_lines = self._axis_lines(cells, axis="y")
        if len(x_lines) < 2 or len(y_lines) < 2:
            bbox = BBox.join2(cells)
            return KTable(page, bbox,cells=[KCell(page, bbox, row_index=0, col_index=0)])

        col_num = len(x_lines) - 1
        row_num = len(y_lines) - 1
        

        kcells: list[KCell] = []
        for cell in cells:
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

            row_index = row_num - y1
            row_span = y1 - y0
            col_span = col1 - col0
            bbox = BBox(x_lines[col0], y_lines[y0], x_lines[col1], y_lines[y1])
            kcells.append(
                KCell(
                    page,
                    bbox,
                    row_index=row_index,
                    col_index=col0,
                    row_span=row_span,
                    col_span=col_span,
                )
            )

        kcells.sort(key=lambda cell: (cell.row_index, cell.col_index))
        return KTable(page, table_bbox,cells=kcells)


    def _snap_outlier_edges(self, cells: list[_Cell], *, axis: Literal["x", "y"]):
        """把孤立边界吸附到相邻稳定边界，避免形成很窄的伪行/列。"""
        lo_attr, hi_attr = self._axis_attrs(axis)
        tol = self._edge_tolerance(cells, axis)
        clusters = self._cluster_edges(
            [(getattr(cell.bbox, lo_attr), cell, lo_attr) for cell in cells]
            + [(getattr(cell.bbox, hi_attr), cell, hi_attr) for cell in cells],
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
            for _, cell, attr in members:
                self._adjust_edge(cell, attr, target)

    def _snap_to_grid(self, cells: list[_Cell], *, axis: Literal["x", "y"]):
        """把所有边界吸附到聚类后的网格线。"""
        lo_attr, hi_attr = self._axis_attrs(axis)
        tol = self._edge_tolerance(cells, axis)
        clusters = self._cluster_edges(
            [(getattr(cell.bbox, lo_attr), cell, lo_attr) for cell in cells]
            + [(getattr(cell.bbox, hi_attr), cell, hi_attr) for cell in cells],
            tol,
        )
        if len(clusters) < 2:
            return

        lines = [value for value, _ in clusters]
        for cell in cells:
            lo = getattr(cell.bbox, lo_attr)
            hi = getattr(cell.bbox, hi_attr)
            new_lo = min(lines, key=lambda line: abs(line - lo))
            new_hi = min(lines, key=lambda line: abs(line - hi))
            self._adjust_edge(cell, lo_attr, new_lo)
            self._adjust_edge(cell, hi_attr, new_hi)

    def _align_neighbor_y_edges(self, cells: list[_Cell]):
        """对齐相邻列中应属于同一行的上下边界。"""
        if len(cells) < 2:
            return

        tolerance = self._neighbor_y_tolerance(cells)
        x_tolerance = self._edge_tolerance(cells, "x")
        for attr in ("y0", "y1"):
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
                        abs(getattr(c1.bbox, attr) - getattr(c2.bbox, attr))
                        > tolerance
                    ):
                        continue
                    add_pair(i, j)

            for group in groups:
                if len(group) < 2:
                    continue
                target = self._best_y_edge_target(cells, group, attr, tolerance)
                old_bboxes = [(cells[i], cells[i].bbox) for i in group]
                for i in group:
                    self._adjust_edge(cells[i], attr, target)
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
        attr: str,
        tolerance: float,
    ) -> float:
        candidates = [getattr(cells[i].bbox, attr) for i in group]

        def score(value: float) -> tuple[int, float]:
            supporters = [
                cell
                for cell in cells
                if abs(getattr(cell.bbox, attr) - value) <= tolerance
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
    ) -> tuple[_Cell, str, float] | None:
        lo_attr, hi_attr, cross_lo_attr, cross_hi_attr = self._stagger_axis_attrs(
            axis
        )
        tolerance = self._edge_tolerance(cells, axis)
        candidates: list[tuple[float, _Cell, str, float]] = []

        for cell in cells:
            if cell is gap or cell.generated:
                continue
            if (
                self._axis_overlap_ratio(
                    cell.bbox, gap.bbox, cross_lo_attr, cross_hi_attr
                )
                < 0.8
            ):
                continue

            if (
                abs(getattr(cell.bbox, lo_attr) - getattr(gap.bbox, hi_attr))
                <= tolerance
            ):
                boundary = getattr(cell.original_bbox, lo_attr)
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
                            lo_attr,
                            getattr(gap.bbox, lo_attr),
                        )
                    )
            if (
                abs(getattr(cell.bbox, hi_attr) - getattr(gap.bbox, lo_attr))
                <= tolerance
            ):
                boundary = getattr(cell.original_bbox, hi_attr)
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
                            hi_attr,
                            getattr(gap.bbox, hi_attr),
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
        lo_attr, hi_attr, cross_lo_attr, cross_hi_attr = self._stagger_axis_attrs(axis)
        tolerance = self._edge_tolerance(cells, axis)
        cross_tolerance = self._edge_tolerance(cells, "x" if axis == "y" else "y")

        for cell in cells:
            if cell is gap or cell is ignore:
                continue
            if not (
                getattr(cell.bbox, lo_attr) < boundary - tolerance
                and getattr(cell.bbox, hi_attr) > boundary + tolerance
            ):
                continue
            if (
                self._axis_overlap_ratio(
                    cell.bbox, gap.bbox, cross_lo_attr, cross_hi_attr
                )
                > 0.15
            ):
                continue
            cross_gap = max(
                getattr(gap.bbox, cross_lo_attr) - getattr(cell.bbox, cross_hi_attr),
                getattr(cell.bbox, cross_lo_attr) - getattr(gap.bbox, cross_hi_attr),
                0,
            )
            if cross_gap <= cross_tolerance:
                return True
        return False

    def _stagger_merge_score(
        self, cell: _Cell, gap: _Cell, axis: Literal["x", "y"]
    ) -> float:
        lo_attr, hi_attr, _, _ = self._stagger_axis_attrs(axis)
        cell_size = getattr(cell.bbox, hi_attr) - getattr(cell.bbox, lo_attr)
        gap_size = getattr(gap.bbox, hi_attr) - getattr(gap.bbox, lo_attr)
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
        lo_attr: str,
        hi_attr: str,
    ) -> float:
        overlap = min(getattr(b1, hi_attr), getattr(b2, hi_attr)) - max(
            getattr(b1, lo_attr), getattr(b2, lo_attr)
        )
        if overlap <= 0:
            return 0.0
        size = min(
            getattr(b1, hi_attr) - getattr(b1, lo_attr),
            getattr(b2, hi_attr) - getattr(b2, lo_attr),
        )
        return float(overlap / size) if size > 0 else 0.0

    def _stagger_axis_attrs(
        self, axis: Literal["x", "y"]
    ) -> tuple[str, str, str, str]:
        if axis == "y":
            return "y0", "y1", "x0", "x1"
        return "x0", "x1", "y0", "y1"

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
                self._alignment_plan(
                    cells, x_lines, y_lines, r0, c0, r1, c1, "y1"
                ),
                self._alignment_plan(
                    cells, x_lines, y_lines, r0, c0, r1, c1, "y0"
                ),
                self._alignment_plan(
                    cells, x_lines, y_lines, r0, c0, r1, c1, "x1"
                ),
                self._alignment_plan(
                    cells, x_lines, y_lines, r0, c0, r1, c1, "x0"
                ),
            )
            if plan is not None
        ]
        if not plans:
            return False

        for _, adjustments in sorted(plans, key=lambda item: item[0]):
            old_bboxes = [(cell, cell.bbox) for cell, _, _ in adjustments]
            for cell, attr, target in adjustments:
                self._adjust_edge(cell, attr, target)
            attr = adjustments[0][1]
            if self._removed_gap_line(cells, x_lines, y_lines, attr, r0, c0, r1, c1):
                for rr in range(r0, r1):
                    for cc in range(c0, c1):
                        covered[rr][cc] = True
                return True
            for cell, old_bbox in old_bboxes:
                cell.bbox = old_bbox
        return False

    def _alignment_plan(
        self,
        cells: list[_Cell],
        x_lines: list[float],
        y_lines: list[float],
        r0: int,
        c0: int,
        r1: int,
        c1: int,
        attr: Literal["x0", "x1", "y0", "y1"],
    ) -> tuple[float, list[tuple[_Cell, str, float]]] | None:
        if attr == "y0":
            target = y_lines[r0]
            intervals = self._edge_intervals(cells, x_lines, y_lines, "y0", r1)
            adjustments = self._covering_adjustments(intervals, c0, c1, attr, target)
            if adjustments is None:
                return None
            return y_lines[r1] - y_lines[r0], adjustments
        if attr == "y1":
            target = y_lines[r1]
            intervals = self._edge_intervals(cells, x_lines, y_lines, "y1", r0)
            adjustments = self._covering_adjustments(intervals, c0, c1, attr, target)
            if adjustments is None:
                return None
            return y_lines[r1] - y_lines[r0], adjustments
        if attr == "x0":
            target = x_lines[c0]
            intervals = self._edge_intervals(cells, x_lines, y_lines, "x0", c1)
            adjustments = self._covering_adjustments(intervals, r0, r1, attr, target)
            if adjustments is None:
                return None
            return x_lines[c1] - x_lines[c0], adjustments

        target = x_lines[c1]
        intervals = self._edge_intervals(cells, x_lines, y_lines, "x1", c0)
        adjustments = self._covering_adjustments(intervals, r0, r1, attr, target)
        if adjustments is None:
            return None
        return x_lines[c1] - x_lines[c0], adjustments

    def _edge_intervals(
        self,
        cells: list[_Cell],
        x_lines: list[float],
        y_lines: list[float],
        attr: Literal["x0", "x1", "y0", "y1"],
        line_index: int,
    ) -> list[tuple[int, int, _Cell]]:
        intervals: list[tuple[int, int, _Cell]] = []
        for cell in cells:
            c0 = self._nearest_index(cell.bbox.x0, x_lines)
            c1 = self._nearest_index(cell.bbox.x1, x_lines)
            r0 = self._nearest_index(cell.bbox.y0, y_lines)
            r1 = self._nearest_index(cell.bbox.y1, y_lines)
            if attr == "y0" and r0 == line_index:
                intervals.append((c0, c1, cell))
            elif attr == "y1" and r1 == line_index:
                intervals.append((c0, c1, cell))
            elif attr == "x0" and c0 == line_index:
                intervals.append((r0, r1, cell))
            elif attr == "x1" and c1 == line_index:
                intervals.append((r0, r1, cell))
        return intervals

    def _covering_adjustments(
        self,
        intervals: list[tuple[int, int, _Cell]],
        start: int,
        end: int,
        attr: str,
        target: float,
    ) -> list[tuple[_Cell, str, float]] | None:
        adjustments: list[tuple[_Cell, str, float]] = []
        cursor = start
        for i0, i1, cell in sorted(intervals, key=lambda item: item[0]):
            if i1 <= cursor:
                continue
            if i0 > cursor:
                break
            adjustments.append((cell, attr, target))
            cursor = max(cursor, i1)
            if cursor >= end:
                return adjustments
        return None

    def _removed_gap_line(
        self,
        cells: list[_Cell],
        x_lines: list[float],
        y_lines: list[float],
        attr: str,
        r0: int,
        c0: int,
        r1: int,
        c1: int,
    ) -> bool:
        if attr == "y0":
            return not self._line_exists(
                y_lines[r1], self._axis_lines(cells, axis="y")
            )
        if attr == "y1":
            return not self._line_exists(
                y_lines[r0], self._axis_lines(cells, axis="y")
            )
        if attr == "x0":
            return not self._line_exists(
                x_lines[c1], self._axis_lines(cells, axis="x")
            )
        if attr == "x1":
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

    def _axis_attrs(self, axis: Literal["x", "y"]) -> tuple[str, str]:
        if axis == "x":
            return "x0", "x1"
        return "y0", "y1"

    def _axis_lines(
        self, cells: list[_Cell], *, axis: Literal["x", "y"]
    ) -> list[float]:
        lo_attr, hi_attr = self._axis_attrs(axis)
        clusters = self._cluster_edges(
            [(getattr(cell.bbox, lo_attr), cell, lo_attr) for cell in cells]
            + [(getattr(cell.bbox, hi_attr), cell, hi_attr) for cell in cells],
            self._edge_tolerance(cells, axis),
        )
        return [value for value, _ in clusters]

    def _adjust_edge(self, cell: _Cell, attr: str, target: float):
        if cell.content_bbox is not None:
            if attr in ("x0", "y0"):
                target = min(target, getattr(cell.content_bbox, attr))
            else:
                target = max(target, getattr(cell.content_bbox, attr))
        bbox = cell.bbox.adjust(**{attr: target})
        if bbox.width > 0 and bbox.height > 0:
            cell.bbox = bbox

    def _edge_tolerance(self, cells: list[_Cell], axis: Literal["x", "y"]) -> float:
        lo_attr, hi_attr = self._axis_attrs(axis)
        sizes = sorted(
            getattr(cell.bbox, hi_attr) - getattr(cell.bbox, lo_attr)
            for cell in cells
            if getattr(cell.bbox, hi_attr) > getattr(cell.bbox, lo_attr)
        )
        if not sizes:
            return 2.0
        base = sizes[len(sizes) // 4]
        median = sizes[len(sizes) // 2]
        return max(2.0, min(base * 0.25, median * 0.12))

    def _cluster_edges(
        self,
        values: list[tuple[float, _Cell, str]],
        tolerance: float,
    ) -> list[tuple[float, list[tuple[float, _Cell, str]]]]:
        if not values:
            return []
        values = sorted(values, key=lambda item: item[0])
        clusters: list[list[tuple[float, _Cell, str]]] = [[values[0]]]
        for item in values[1:]:
            if item[0] - clusters[-1][-1][0] <= tolerance:
                clusters[-1].append(item)
            else:
                clusters.append([item])

        result: list[tuple[float, list[tuple[float, _Cell, str]]]] = []
        for cluster in clusters:
            cluster_values = [item[0] for item in cluster]
            value = sum(cluster_values) / len(cluster_values)
            result.append((value, cluster))
        return result

    def _nearest_index(self, value: float, lines: list[float]) -> int:
        return min(range(len(lines)), key=lambda i: abs(lines[i] - value))
