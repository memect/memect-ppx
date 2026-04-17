import logging
from abc import ABC, abstractmethod
from dataclasses import KW_ONLY, dataclass
from logging import Logger
from pathlib import Path
from typing import Any, BinaryIO, Final, Protocol, Self, Sequence, TextIO, overload, override


from PIL import Image, ImageDraw

from memect.base import lists
from memect.base.bbox import BBox
from memect.base.debug import XDebugger

from .sort import Sorter

# 声明为Sequence可以支持tuple/list，但是声明为tuple[T,T,T,T]会更加严格，限制为4个
# 但是使用的时候，就不能够使用list而必须使用tuple
type _Line = tuple[float, float, float, float]


class Cell:
    def __init__(
        self,
        *,
        bbox: BBox,
        row_index: int,
        col_index: int,
        row_span: int,
        col_span: int,
        objects: Sequence[Any] | None = None,
    ):
        super().__init__()
        self.bbox = bbox
        self.row_index = row_index
        self.col_index = col_index
        self.row_span = row_span
        self.col_span = col_span
        self.objects: list[Any] = list(objects) if objects else []

    def jsonify(self) -> dict[str, Any]:
        return {
            "bbox": self.bbox.jsonify(),
            "row_index": self.row_index,
            "col_index": self.col_index,
            "row_span": self.row_span,
            "col_span": self.col_span,
        }


class _Item(Protocol):
    #bbox: BBox
    @property
    def bbox(self)->BBox:
        ...


class BaseGrid(ABC):
    _logger: Logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")

    def __init__(self, lines: Sequence[_Line], items: Sequence[_Item] | None = None):
        super().__init__()
        self.lines = lines
        self.col_num: int = 0
        self.row_num: int = 0
        self.bbox = BBox.join(lines)
        self.cells: list[Cell] = []
        self._parse(lines)

        if items:
            self._fill(items)

    @abstractmethod
    def _parse(self, lines: Sequence[_Line]) -> None:
        raise NotImplementedError

    @overload
    def __getitem__(self, key: tuple[int, int]) -> Cell: ...

    @overload
    def __getitem__(self, key: tuple[int, slice]) -> list[Cell]: ...

    @overload
    def __getitem__(self, key: tuple[slice, int]) -> list[Cell]: ...

    @overload
    def __getitem__(self, key: tuple[slice, slice]) -> list[list[Cell]]: ...

    def __getitem__(
        self, key: tuple[int | slice, int | slice]
    ) -> Cell | list[Cell] | list[list[Cell]]:

        def get_cell(row_index: int, col_index: int) -> Cell:
            for cell in self.cells:
                if (
                    cell.row_index <= row_index < cell.row_index + cell.row_span
                    and cell.col_index <= col_index < cell.col_index + cell.col_span
                ):
                    return cell
            raise ValueError(f"不存在的cell:[{row_index},{col_index}]")

        def distinct[T](objs: list[T]) -> list[T]:
            new_objs: list[T] = []
            for obj in objs:
                if obj not in new_objs:
                    new_objs.append(obj)
            return new_objs

        row_slice, col_slice = key
        if isinstance(row_slice, int):
            row_slice = slice(row_slice, row_slice + 1)
        if isinstance(col_slice, int):
            col_slice = slice(col_slice, col_slice + 1)

        rows: list[list[Cell]] = []
        for i in range(self.row_num)[row_slice]:
            row: list[Cell] = []
            for j in range(self.col_num)[col_slice]:
                row.append(get_cell(i, j))
            rows.append(row)

        if isinstance(key[0], slice) and isinstance(key[1], slice):
            return rows
        elif isinstance(key[0], int) and isinstance(key[1], int):
            return rows[0][0]
        elif isinstance(key[0], int) and isinstance(key[1], slice):
            # 返回行，把跨列的只返回一个
            return distinct(rows[0])

        elif isinstance(key[0], slice) and isinstance(key[1], int):
            # 返回列，把跨行的只返回一个
            return distinct([row[0] for row in rows])
        else:
            raise ValueError(f"不支持的key:{key}")

    def html(self, fp: str | Path | TextIO | None = None, full: bool = True) -> str:
        buf: list[str] = []
        if full:
            buf.append("<html><head></head><body>")
        buf.append('<table style="border: 1px solid;border-collapse: collapse;">')
        # 如果需要显示复杂的表格，如下设置可以让跨列跨行的显示更加明确
        buf.append("<colgroup>")
        for i in range(self.col_num):
            buf.append('<col colspan=1 style="width:100px;"></col>')
        buf.append("</colgroup>")
        buf.append("<tbody>")
        for i in range(self.row_num):
            tr: list[str] = []
            tr.append('<tr style="border:1px solid;">')
            for j in range(self.col_num):
                cell = self[i, j]
                if cell.row_index == i and cell.col_index == j:
                    tr.append(
                        f'<td colspan="{cell.col_span}" rowspan="{cell.row_span}" style="border:1px solid;">'
                    )
                    tr.append(
                        f"[{cell.row_index},{cell.col_index}],[{cell.row_span},{cell.col_span}]"
                    )
                    tr.append("</td>")
            tr.append("</tr>")
            buf.append("".join(tr))
        buf.append("</tbody>")

        buf.append("</table>")
        if full:
            buf.append("</body></html>")

        html = "".join(buf)
        if isinstance(fp, (str, Path)):
            Path(fp).write_text(html, encoding="utf-8")
        elif isinstance(fp, TextIO):
            fp.write(html)
        else:
            pass
        return html

    def draw(
        self,
        *,
        image: Image.Image | None = None,
        scale: float = 1,
        fp: str | Path | BinaryIO | None = None,
        size: tuple[int, int] | None = None,
        show: bool = False,
    ) -> Image.Image:
        _image: Image.Image
        _overlay_image:Image.Image|None=None
        if image is None and size is not None:
            _image = Image.new("RGBA", size, "#ffffff")
        elif image is not None:
            _image = image.copy()
            _overlay_image = Image.new('RGBA',image.size,(0,0,0,0))
        else:
            raise ValueError("image或者size必须指定")
        try:
            draw = ImageDraw.Draw(_overlay_image or _image)
            _, height = _image.size

            for i, cell in enumerate(self.cells):
                if i % 2 == 0:
                    fill = "#cccccc30"
                else:
                    fill = "#ffff0030"

                # 左下角为原点转换为左上角为原点
                xy = list([v * scale for v in cell.bbox])
                xy[1], xy[3] = height - xy[3], height - xy[1]
                draw.rectangle(tuple(xy), fill=fill, width=2, outline="#ff0000")
            
            if _overlay_image:
                _image.paste(_overlay_image,(0,0),_overlay_image)
            if fp is not None:
                _image.save(fp)
        finally:
            if not show and _image is not image:
                _image.close()

        return _image

    def validate(self, data: dict[str, Any]):
        cells = data["cells"]
        if data["col_num"] != self.col_num or data["row_num"] != self.row_num:
            raise ValueError(
                f"行列不一致:({data['row_num']},{data['col_num']}),({self.row_num},{self.col_num})"
            )
        if len(cells) != len(self.cells):
            raise ValueError(f"cells的数量不一致:{len(cells)},{len(self.cells)}")
        for c1, c2 in zip(cells, self.cells):
            if c1["row_index"] != c2.row_index:
                raise ValueError("cell不一致")
            if c1["col_index"] != c2.col_index:
                raise ValueError("cell不一致")

            if c1["row_span"] != c2.row_span:
                raise ValueError("cell不一致")

            if c1["col_span"] != c2.col_span:
                raise ValueError("cell不一致")

            # 严格的还可以比较bbox

    def jsonify(self) -> dict[str, Any]:
        return {
            "row_num": self.row_num,
            "col_num": self.col_num,
            "cells": [c.jsonify() for c in self.cells],
        }

    def _fill[T: _Item](self, items: Sequence[T]):
        """填充对象到单元格"""

        #
        debugger = self._debugger.bind()

        def get_objects2(
            bbox: BBox, items: list[T], *, remove_used: bool = True
        ) -> list[T]:
            i = 0
            x0, y0, x1, y1 = bbox.to_int()
            used_items: list[Any] = []
            while i < len(items):
                item = items[i]
                # TODO 必须有一个bbox
                b = item.bbox.to_int()
                if b[3] < y0:
                    # 不需要再继续了
                    break
                # 先比较y更快，因为先排除            
                if b[1] >= y0 and b[3] <= y1 and b[0] >= x0 and b[2] <= x1:
                    used_items.append(item)
                    if remove_used:
                        del items[i]
                    else:
                        i += 1
                else:
                    i += 1
            
            
            # 简单的排序？
            Sorter.sort(used_items)
            return used_items
        
        def get_objects(
            bbox: BBox, items: list[T], *, remove_used: bool = True,ratio:float=0.7
        ) -> list[T]:
            i = 0
            x0, y0, x1, y1 = bbox.to_int()
            used_items: list[Any] = []
            while i < len(items):
                item = items[i]
                # TODO 必须有一个bbox
                b = item.bbox.to_int()
                if b[3] < y0:
                    # 不需要再继续了
                    break
                # 先比较y更快，因为先排除
                x_bbox = bbox.intersect(b)
                if x_bbox is not None and x_bbox.area2/b.area2>=ratio:            
                #if b[1] >= y0 and b[3] <= y1 and b[0] >= x0 and b[2] <= x1:
                    used_items.append(item)
                    if remove_used:
                        del items[i]
                    else:
                        i += 1
                else:
                    i += 1
            
            
            # 简单的排序？
            Sorter.sort(used_items)
            return used_items
        items = sorted(items, key=lambda item: item.bbox.y1, reverse=True)
        total = len(items)
        for ratio in [0.7,0.6,0.5]:
            if not items:
                break
            for cell in self.cells:
                # TODO 如果是来自pdf解析的，可能会容易一些
                # 如果是来自ocr的，获得的字符串可能跨单元格的，就需要切开
                # TODO 对于和边界线重叠的，还需要仔细调整
                # 如果是几十页的，单元格很多的，这一步速度很慢,2000*2000多个需要7秒的，如果是20个表格，还需要每次累加
                # 如果不使用remove而是get，2000*2000=4百万，耗时18秒，每4.5秒执行100万次
                # cell.objects = cell.bbox.adjust(dx=2,dy=2).remove(items)
                if items:
                    cell.objects.extend(get_objects(cell.bbox.expand(dx=1, dy=1), items,ratio=ratio))
                else:
                    break
        

        
        if len(items) > 0:
            # 如果是跨页表格合并，不应该执行到这里，因为使用的是cell的bbox，已经调整了bbox
            # 所以，这里处理的都是单页的表格，单元格的数量不会太多，速度很快
            #TODO 有些可能是靠近边界的空格，可以去掉的，怎么知道是空格？
            from .base import KChar
            space_items:list[Any]=[]
            for item in items:
                if isinstance(item,KChar) and item.text.isspace():
                    space_items.append(item)

            self._logger.warning(
                "有items没有被填充到表格，total=%s,remain=%s,spaces=%s", total, len(items),len(space_items)
            )

            lists.remove(items,space_items,use_is=True)

            if debugger.allow("info"):
                with debugger.group("items"):
                    for i, item in enumerate(items):
                        debugger.console.print(i, item.bbox)
            
            # 一般就1-2个会溢出
            for item in items:
                # 主要考虑左右溢出的情况，如：
                # ---|-  =>溢出了一些，需要判断是在左边还是在右边
                overlapped_cells: list[tuple[float, Cell]] = []
                b1 = item.bbox
                for cell in self.cells:
                    b2 = cell.bbox
                    if b1.y0 >= b2.y0 and b1.y1 <= b2.y1:
                        dx0 = b1.x0 - b2.x0
                        dx1 = b1.x1 - b2.x1
                        if dx0 <= 0 and dx1 >= 0:
                            # -|--|-
                            overlapped_cells.append((b2.width, cell))
                        elif dx0 <= 0 and b1.x1 > b2.x0:
                            # -|-- |
                            overlapped_cells.append((b1.x1 - b2.x0, cell))
                        elif dx1 >= 0 and b1.x0 < b2.x1:
                            # |  --|-
                            overlapped_cells.append((b2.x1 - b1.x0, cell))
                        else:
                            pass

                        if len(overlapped_cells) > 1:
                            # 正常情况下只会跨2个，超过就不需要再计算了，当然也可以去掉判断
                            break
                    pass

                if len(overlapped_cells) > 0:
                    overlapped_cells.sort(key=lambda obj: obj[0], reverse=True)
                    _, cell = overlapped_cells[0]
                    # 需要只是有一半距离才算？否则抛弃掉？
                    cell.objects.append(item)
                    self._logger.warning(
                        "overflow item ,cell.bbox=%s,item.bbox=%s",
                        cell.bbox,
                        item.bbox,
                    )
                else:
                    pass


@dataclass
class Point:
    _: KW_ONLY
    x: float
    y: float
    left: Self | None = None
    right: Self | None = None
    top: Self | None = None
    bottom: Self | None = None
    x_index: int = 0
    y_index: int = 0
    through_left: bool = False
    through_right: bool = False
    through_top: bool = False
    through_bottom: bool = False


class Grid(BaseGrid):
    """解析表格的cells，输入的线必须没有误差，且需要包括4条边界线"""

    @override
    def _parse(self, lines: Sequence[_Line]):
        if len(lines) < 4:
            raise ValueError(f"至少4条水平线或者垂直线:{lines}")

        def eq(a: float, b: float, d: float = 1) -> bool:
            # d:T=0不允许
            if d == 0:
                return a == b
            else:
                return abs(a - b) <= d

        def between(a: float, start: float, end: float, d: int | float = 0) -> bool:
            return start - d <= a <= end + d

        def get_points(edge: str, start: Point) -> list[Point]:
            result: list[Point] = [start]
            if edge == "top":
                # [p1->p2->p3]
                #         | 如果有，结束
                point = start.right
                while point is not None:
                    result.append(point)
                    if point.through_bottom:
                        break
                    point = point.right
            elif edge == "left":
                point = start.bottom
                while point is not None:
                    result.append(point)
                    if point.through_right:
                        break
                    point = point.bottom

            else:
                raise ValueError(f"不支持的edge:{edge}")
            return result

        h_lines: list[_Line] = []
        v_lines: list[_Line] = []
        for line in lines:
            if eq(line[0], line[2]):
                v_lines.append(line)
            elif eq(line[1], line[3]):
                h_lines.append(line)
            else:
                # ??
                raise ValueError(f"不支持斜线:{line}")

        # 排序只是为了方便debug，并不是必须的
        debug: bool = True
        if debug:
            # 从上到下
            h_lines.sort(key=lambda line: line[1], reverse=True)
            # 从左到右
            v_lines.sort(key=lambda line: line[0])

        bbox: Final[list[float]] = [
            min([line[0] for line in lines]),
            min([line[1] for line in lines]),
            max([line[2] for line in lines]),
            max([line[3] for line in lines]),
        ]

        x_axis: list[float] = []
        y_axis: list[float] = []
        x_axis.append(bbox[0])
        x_axis.append(bbox[2])
        y_axis.append(bbox[1])
        y_axis.append(bbox[3])
        for line in h_lines:
            y = line[1]
            if y not in y_axis:
                y_axis.append(y)

        for line in v_lines:
            x = line[0]
            if x not in x_axis:
                x_axis.append(x)

        # 从左到右
        x_axis.sort()
        # 从上到下
        y_axis.sort(reverse=True)
        points: dict[tuple[float, float], Point] = {}
        rows: list[list[Point]] = []
        # n*m的表格，记录每一个点
        for i, y in enumerate(y_axis):
            rows.append([])
            for j, x in enumerate(x_axis):
                point = Point(x=x, y=y, x_index=j, y_index=i)
                points[(x, y)] = point
                rows[-1].append(point)

        # 为了方便，先计算出来
        for i in range(len(y_axis)):
            for j in range(len(x_axis)):
                point = rows[i][j]
                if j + 1 < len(x_axis):
                    point.right = rows[i][j + 1]
                if j - 1 >= 0:
                    point.left = rows[i][j - 1]
                if i + 1 < len(y_axis):
                    point.bottom = rows[i + 1][j]
                if i - 1 >= 0:
                    point.top = rows[i - 1][j]

        for h_line in h_lines:
            for v_line in v_lines:
                if between(v_line[0], h_line[0], h_line[2]) and between(
                    h_line[1], v_line[1], v_line[3]
                ):
                    #     |
                    # -----|------ y=h_line[1]
                    #     | x=v_line[0]
                    x = v_line[0]
                    y = h_line[1]
                    y0 = v_line[1]
                    y1 = v_line[3]
                    x0 = h_line[0]
                    x1 = h_line[2]
                    point = points[(x, y)]
                    if y0 < y < y1:
                        point.through_top = True
                        point.through_bottom = True
                    elif y0 == y:
                        point.through_top = True
                    elif y1 == y:
                        point.through_bottom = True
                    else:
                        # 不可能到这里
                        pass

                    if x0 < x < x1:
                        point.through_left = True
                        point.through_right = True
                    elif x0 == x:
                        point.through_right = True
                    elif x1 == x:
                        point.through_left = True
                    else:
                        # 不可能执行到这里
                        pass

        # 开始计算cells
        cells: list[Cell] = []

        for row in rows:
            for point in row:
                if not point.through_bottom or not point.through_right:
                    continue
                top_points = get_points("top", point)
                left_points = get_points("left", point)

                p1 = left_points[-1]
                p2 = top_points[-1]

                cells.append(
                    Cell(
                        # 原点为左下角
                        bbox=BBox(p1.x, p1.y, p2.x, p2.y),
                        row_index=point.y_index,
                        col_index=point.x_index,
                        row_span=p1.y_index - point.y_index,
                        col_span=p2.x_index - point.x_index,
                    )
                )
        self.col_num: int = len(rows[0]) - 1
        self.row_num: int = len(rows) - 1
        self.cells = cells


def usecase():
    lines: list[tuple[int, int, int, int]] = [(0, 0, 0, 0)]
    g = Grid(lines)
    g.col_num
    g.row_num
    g.cells
    cell = g[0, 0]
    # 跨列的只返回一个
    row = g[0, :]
    # 跨行的只返回一个
    column = g[:, 0]
    # n*m
    cells = g[:, :]
    print(cell, row, column, cells)
