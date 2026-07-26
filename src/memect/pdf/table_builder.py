from typing import Any, Final, Mapping, Sequence



from memect.base.bbox import BBox



class Cell:
    def __init__(
        self,
        text: str,
        row_index: int,
        col_index: int,
        row_span: int = 1,
        col_span: int = 1,
    ):
        super().__init__()
        self.text: Final = text
        self.row_index: Final = row_index
        self.col_index: Final = col_index
        self.row_span: Final = row_span
        self.col_span: Final = col_span


class Table:
    def __init__(self, bbox: BBox, row_num: int, col_num: int, cells: Sequence[Cell]):
        super().__init__()
        self.bbox: Final = bbox
        self.row_num: Final = row_num
        self.col_num: Final = col_num
        self.cells: Final = cells

    @classmethod
    def from_dict(cls,bbox:BBox,data: Mapping[str, Any]):
        row_num = data["row_num"]
        col_num = data["col_num"]
        cells: list[Cell] = []
        for cell in data["cells"]:
            c = Cell(
                cell["text"],
                cell["row_index"],
                cell["col_index"],
                row_span=cell.get("row_span", 1),
                col_span=cell.get("col_span", 1),
            )
            cells.append(c)
        return Table(bbox, row_num, col_num, cells)


class Builder:
    def __init__(self):
        super().__init__()

    def build(self,bbox:BBox,data:Mapping[str,Any],*,font_size:float=10):
        """根据表格的结构，计算出大概的单元格的bbox"""

        # 因为不知道单元格的bbox，所以只能够推理，能够让内容合理塞进去即可，
        # 不追求100%还原原文大小，因为没有任何参考信息了

        # 算法
        # 字体大小设置为10
        # 先能够按x轴塞进去
        # 如果溢出y轴，再调整

        table = Table.from_dict(bbox,data)
        row_num: Final = table.row_num
        col_num: Final = table.col_num

        # 计算每列需要的最小宽度（基于文本长度）
        col_min_width = [0.0] * col_num
        row_min_height = [0.0] * row_num

        for cell in table.cells:
            text = cell.text or ""
            if not text:
                continue
            chars = len(text)
            # 单元格宽度：按span平均分配所需字符宽度
            needed_w = chars * font_size / cell.col_span
            needed_h = font_size * 1.5  # 行高
            # 简单按span平均分给每列/行
            for c in range(cell.col_index, cell.col_index + cell.col_span):
                col_min_width[c] = max(col_min_width[c], needed_w / cell.col_span)
            for r in range(cell.row_index, cell.row_index + cell.row_span):
                row_min_height[r] = max(row_min_height[r], needed_h / cell.row_span)

        # 按比例缩放，使总宽/高适配bbox
        total_min_w = sum(col_min_width)
        total_min_h = sum(row_min_height)

        if total_min_w > 0:
            col_widths = [w / total_min_w * bbox.width for w in col_min_width]
        else:
            col_widths = [bbox.width / col_num] * col_num

        if total_min_h > 0:
            row_heights = [h / total_min_h * bbox.height for h in row_min_height]
        else:
            row_heights = [bbox.height / row_num] * row_num

        x_axis = [bbox[0]]
        for w in col_widths[:-1]:
            x_axis.append(x_axis[-1] + w)
        x_axis.append(bbox[2])

        y_axis = [bbox[1]]
        for h in row_heights[:-1]:
            y_axis.append(y_axis[-1] + h)
        y_axis.append(bbox[3])

        y_axis.reverse()
        #print("===>table", table.bbox, (table.row_num, table.col_num))
        #print("x_axis", len(x_axis), x_axis)
        #print("y_axis", len(y_axis), y_axis)

        return x_axis, y_axis
