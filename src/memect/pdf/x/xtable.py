import logging
import re
import threading
from logging import Logger
from pathlib import Path
from typing import Any, ClassVar, Final, Literal, Self, Sequence, cast

from memect.base import strs, utils
from memect.base.bbox import BBox
from memect.base.debug import XDebugger
from memect.base.pattern import XPattern
from memect.pdf.base import KCell, KFigure, KLine, KRect, KTable, KText

from .xbase import XItem, XObject, XTable, XText, XTree


class XTableParser:
    """跨页/跨栏表格合并"""
    _logger: Logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")

    def __init__(self):
        super().__init__()

    def parse(self, xtree: XTree):
        """
        合并跨页/跨栏表格，直接对objects进行修改

        """

        #1.判断是否需要合并
        #2.判断是否有共同的表头
        #3.判断单元格是否需要合并
        debugger = self._debugger.bind()
        objects = xtree.xobjects
        i = 0
        start = -1
        while i < len(objects):
            obj = objects[i]
            if start >= 0:
                k = self._join(start, i, objects)
                if k == -1:
                    # 不能够join
                    self._build(start, i, objects)
                    i = start + 1
                    start = -1
                else:
                    # 表示下一个的index
                    i = k
            elif isinstance(obj, XTable):
                start = i
                i += 1
            else:
                i += 1

        if start >= 0 and len(objects) - start > 1:
            self._build(start, len(objects), objects)

        def show_debug():
            if debugger.allow("save"):
                timer = utils.Timer.start()
                #debug_dir = ctx.debug_dir.joinpath("doc/xtable/_htmls")
                #ctx.remove([debug_dir])
                #debug_dir.mkdir(parents=True, exist_ok=True)
                seqs: dict[int, int] = {}
                total = 0
                for obj in objects:
                    if isinstance(obj, XTable) and debugger.allow(
                        "save", page=obj.page_numbers[0]
                    ):
                        total += 1
                        i = obj.pages[0].number
                        j = obj.pages[-1].number
                        n = seqs.setdefault(i, 1)
                        if i == j:
                            name = f"{i}-{n}.html"
                        else:
                            name = f"{i}-{n}-{j}.html"
                        seqs[i] = n + 1
                        xtree.doc.write(f'debug/xtables/{name}',obj.rich_html())

                debugger.print(
                    "save xtable html", {"total": total, "elapsed": timer.elapsed()}
                )

        show_debug()

    def _join(
        self, start: int, index: int, objects: list[XObject], *, method: int = 1
    ) -> int:
        """根据对齐判断表格是否需要合并，返回-1表示无法合并，否则返回下一个index
        start: 开始的index
        index： 目前的index
        objects： 对象集合
        """

        # 表格对齐了，还需要根据表格的内容判断是否需要合并
        # 简单的规则，就是全部合并了，不需要考虑结构是否一致，因为表格的设计是千变万化无厘头的
        # 可能就是多个不同的“子表格”组成（如：使用word设计一个非常复杂的表格，前面几行使用A结构，中间使用B结构，又使用C结构等等）
        # 如果严格的，可以考虑是否表头重复，如果重复，后续的就必须全部重复，如果不重复，后续的就不需要重复

        # 有时候，可能需要先2个表格合并，然后再判断和下一个表格是否合并，有时候又需要获得了所有的表格再判断
        # 就需要反复迭代计算，运算量大，实现也复杂，而且也提高不了太多的准确度
        # 可以使用折中的方案，如果超过

        # 继续判断是否需要跨页合并

        k, score = self._join1(start, index, objects)
        if k == -1:
            # 表示无法合并
            return -1

        # k表示新的下一个，因为可能跳过一些文本等，k>=index+1
        if score < 1 and k < len(objects):
            # 可以合并，但是再判断和下一个的
            k2, score2 = self._join1(k - 1, k, objects, test=True)
            if k2 != -1 and score2 > score:
                # 如果分数值更高（如：重复的表头）
                # 表示xt1和xt2不合并，xt2和xt3更加合适
                return -1

        # 表示需要合并
        if method == 1:
            # 获得所有后再一次性合并
            return k
        else:
            # 先合并2个，再继续，假设一个跨100页的跨页表格，就需要合并99次，第1次2个表格合并，第2次3个，第99次100个
            self._build(start, k, objects)
            return start + 1

    def _join1(
        self, start: int, index: int, objects: list[XObject], *, test: bool = False
    ) -> tuple[int, float]:
        """判断表格是否需要合并，返回[index,score]，如果index=-1，表示无法合并
        start: 开始的index
        index： 目前的index
        objects： 对象集合
        test: True表示为测试是否可以合并，不做具体操作
        """
        # TODO 暂时不支持
        skip_text: bool = True
        obj = objects[index]

        if (
            skip_text
            and isinstance(obj, XText)
            and isinstance(objects[index - 1], XTable)
            and index + 1 < len(objects)
            and re.fullmatch(r"续表|注[:].+",_normalize_text(obj.text))
        ):
            # TODO
            # 如：【续表】
            # [t1]
            # ---------
            #   [续表]   =>只允许一个文本
            # [t2]
            # 或者
            # 判断最后一个表格后面是否紧跟着内容是否为注释
            # cases/pdfs/1-债券募集说明书.pdf 84页
            # [--------t1--------]
            # [注:xxxxxx]    =>需要跳过这一行
            # 还是继续按正常流程，而不能够简单的认为下一个表格一定可以合并
            # return index+1

            return self._join1(start, index + 1, objects, test=test)

        #是否宽度对齐，列对齐
        if not self._is_align(start, index, objects):
            return (-1, 0)

        # 表格对齐了，还需要根据表格的内容判断是否需要合并
        # 简单的规则，就是全部合并了，不需要考虑结构是否一致，因为表格的设计是千变万化无厘头的
        # 可能就是多个不同的“子表格”组成（如：使用word设计一个非常复杂的表格，前面几行使用A结构，中间使用B结构，又使用C结构等等）
        # 如果严格的，可以考虑是否表头重复，如果重复，后续的就必须全部重复，如果不重复，后续的就不需要重复

        # 有时候，可能需要先2个表格合并，然后再判断和下一个表格是否合并，有时候又需要获得了所有的表格再判断
        # 就需要反复迭代计算，运算量大，实现也复杂，而且也提高不了太多的准确度
        # 可以使用折中的方案，如果超过

        # 继续判断是否需要跨页合并
        def get_xtables() -> list[XTable]:
            tables: list[XTable] = []
            i = index - 1
            while i >= start:
                obj = objects[i]
                if isinstance(obj, XTable):
                    tables.insert(0, obj)
                # 跳过文本等
                i -= 1
            return tables

        xtables = get_xtables()
        xt2 = cast(XTable, objects[index])
        has_header = False
        if len(xtables) == 1 or xtables[-1].tables[-1].header is not None:
            # 如果不是test模式，就表示需要设置识别的header
            has_header = self._find_headers([xtables[-1], xt2], set_header=not test)
        else:
            # 表示合并的多个表格没有重复的表头
            pass

        if has_header:
            # 有共同的表头，满分
            return (index + 1, 1)
        else:
            # 没有，只能够的0.5
            return (index + 1, 0.5)

    def _is_align(self, start: int, index: int, objects: list[XObject]) -> bool:

        # TODO 如果是来自ocr解析的，因为拍照，图片旋转等问题，可能需要放宽条件

        obj = objects[index]

        def get_last() -> XTable:
            i = index - 1
            while i >= start:
                obj = objects[i]
                if isinstance(obj, XTable):
                    return obj
                i -= 1
            raise ValueError("没有表格")

        if not isinstance(obj, XTable):
            return False

        # 现在需要往前查找到table，因为中间可能允许跳过的文本/图片等
        xt1 = get_last()
        xt2 = obj
        # if len(xt1.tables)!=1 or len(xt2.tables)!=1:
        # 实际上不会出现这种情况，只是为了支持后续更复杂的解析修改
        # return False

        # 必须还没有合并的，如果合并了，就是程序写错了
        assert len(xt2.tables) == 1

        # 可以为已经合并的
        t1 = xt1.tables[-1]
        t2 = xt2.tables[0]



        def check_width(t1:KTable,t2:KTable)->bool:
            if abs(t1.bbox.width - t2.bbox.width) >= 10:
                return False

            if t1.page.number == t2.page.number:
                # 同页分栏
                #    |[t2]
                # [t1]|
                # 极端情况
                # [t1]|[t2]
                s1 = t1.page.get_section(t1)
                s2 = t2.page.get_section(t2)
                if s1 is not s2:
                    return False

                c1 = s1.get_column(t1.bbox)
                c2 = s2.get_column(t2.bbox)
                if c1 is None or c2 is None:
                    return False

                if c1.index + 1 != c2.index:
                    return False

                if c2.bbox.y1 <= c1.bbox.y1 - 30:
                    return False

                return True

            elif t1.page.number + 1 == t2.page.number:
                # 所在的页面必须一致
                if abs(t1.page.width - t2.page.width) >= 5:
                    return False

                # local/宁波核查文档解析-问题排查文件/pdfs/2023-03-13_国网国际融资租赁有限公司2023年度第二期超短期融资券募集说明书.pdf 54-55
                # 差距比较大

                # 跨页不分栏
                # 跨页分栏

                s1 = t1.page.get_section(t1)
                s2 = t2.page.get_section(t2)
                if not s1.alike(s2):
                    return False

                c1 = s1.get_column(t1.bbox)
                c2 = s2.get_column(t2.bbox)
                if c1 is None or c2 is None:
                    return False

                # t1比较和页面底部，t2比较和页面顶部的距离？

                # 跨页不分栏
                if c1.index == c2.index and c1.index == 0:
                    # --t1--
                    # -------
                    # --t2--
                    return True

                elif c1.index + 1 == s1.col_num and c2.index == 0:
                    # 跨页分栏
                    # ---|t1
                    # ---------跨页
                    # -t2|--
                    return True

                else:
                    return False
            else:
                return False
        
        def check_ybk(t1:KTable,t2:KTable)->bool:
            #允许复杂的结构，t1和t2的列数不需要一致，也不需要对齐
            return True
        
        def check_wbk(t1:KTable,t2:KTable)->bool:
            #如果没有列对齐就合并，会出现很复杂的结构
            #
            return True
        
        def check_layout(t1:KTable,t2:KTable)->bool:
            if t1.col_num!=t2.col_num:
                return False
            return True
        
        def check_hybrid(t1:KTable,t2:KTable)->bool:
            """混合模式"""
            #目前总是不支持
            return False
        
        if not check_width(t1,t2):
            #宽度不一致
            return False

        mode='all'
        if t1.is_ybk() and t2.is_ybk() and mode in ('ybk','all'):
            return check_ybk(t1,t2)
        elif t1.is_wbk() and t2.is_wbk() and mode in ('wbk','all'):
            return check_wbk(t1,t2)
        elif t1.is_layout() and t2.is_layout() and mode in ('layout','all'):
            return check_layout(t1,t2)
        elif (t1.is_ybk() and t2.is_wbk() or t1.is_wbk() and t2.is_ybk()) and mode in ('hybrid','all'):
            #允许混合类型的，如：t1.is_ybk() and t2.is_wbk() or t1.is_wbk() and t2.is_ybk()
            #因为可能存在上一页使用ybk解析，下一页使用wbk解析，特别是对于彩色表格，部分有边框表格
            return check_hybrid(t1,t2)
        else:
            return False

        

    def _find_headers(self, xtables: Sequence[XTable], *, set_header: bool = False):
        """
        找到是否有共同的表头
        xtables:

        set_header:True表示找到后设置为header/body，False表示只是判断，不设置
        """

        def is_header(header: KTable) -> bool:
            # 如果一个表格有多行内容都是一样的，就很难识别是否为重复的表头了，如：
            # [xx][xx][xx][xx]   =>
            # [xx][xx][xx][xx]
            # -------------------
            # [xx][xx][xx][xx]  => 很难判断是否为重复的表头，作者可能设计为表头，也可能没有
            n = 0
            for cell in header.cells:
                # 必须有文本？有图片可以吗？
                if cell.text.strip():
                    # if cell.body.objects:
                    # 判断是否有文本或者有对象就可以？
                    n += 1

            # 至少需要多个才认为是？
            # 考虑到在表格的设计中，通常如下：
            # 有表头，然后跨页没有重复，跨页显示的是内容，和表头一致的可能性很低
            # 有表头，跨页重复
            # 没有表头，跨页不重复
            # 没有表头，跨页重复

            # 在正常的文档中，表格有表头的概率很高，所以就认为总是有了
            if n > 0:
                return True
            else:
                return False

        def match(t1: KTable, t2: KTable, strict: bool = False) -> bool:
            if t1.row_num != t2.row_num:
                # 必须一致
                return False

            # TODO 如果是无边框的，就无法如下这么严格了
            # 如果刚好t1只是表头，那么列数是可能不一致
            if strict:
                if t1.col_num != t2.col_num:
                    # 必须一致
                    return False

                if len(t1.cells) != len(t2.cells):
                    return False

                for c1, c2 in zip(t1.cells, t2.cells):
                    if (
                        c1.col_index != c2.col_index
                        or c1.row_index != c2.row_index
                        or c1.col_span != c2.col_span
                        or c1.row_span != c2.row_span
                    ):
                        return False
            else:
                if len(t1.cells) != len(t2.cells):
                    return False

            # 考虑到表格结构可能不一致，不比较列数，而是直接比较单元格
            # TODO 如果是无边框的，单元格的数量还不一定相等，如：
            # [  ][c1][ ][c2]    =>header=['',c1,'',c2]
            # [xx][xx][x][xx]
            # ----------------分页
            #    [c1][ ][c2]    =>header=[c1,c2]
            #    [xx][ ][xx]

            align_cells: list[tuple[KCell, KCell]] = []

            if len(t1.cells) != len(t2.cells):
                return False
            for c1, c2 in zip(t1.cells, t2.cells):
                # 因为t1和t2可能为跨栏
                b1 = c1.bbox.adjust(x0=c1.bbox.x0 - t1.bbox.x0)
                b2 = c2.bbox.adjust(x0=c2.bbox.x0 - t2.bbox.x0)
                if b1.align("x", b2, d=5):
                    align_cells.append((c1, c2))
                else:
                    # 不需要再继续了
                    return False

            if not align_cells:
                return False

            for c1, c2 in align_cells:
                # 简单的就是根据文本判断，后续复杂的，支持包含图片等？
                if c1.row_span != c2.row_span or _normalize_text(c1.text)!=_normalize_text(c2.text):
                    return False

            return True

        def find_header_end_row_index() -> int:
            t1 = xtables[0].tables[0]
            t2 = xtables[1].tables[0]
            row1 = t1.get_row(0)
            row2 = t2.get_row(0)
            # 多数情况只需要一行（自动拓展几行）
            # 第一种: pingan-bug/pdfs/19.pdf 115,116,117页，需要注意120页特殊情况
            # [xx        ] header start，且必须第一行就需要
            # [xx][xx][xx] header end
            if (
                len(row1) == len(row2) == 1
                and row1[0].row_span == 1
                and row2[0].row_span == 1
                and t1.col_num != 1
                and t1.col_num == t2.col_num
                and t1.row_num >= 2
                and t2.row_num >= 3
                and row1[0].text == row2[0].text
            ):
                # [---------------]
                # [xx][xx][xx]
                # 可以尝试找2行
                h1 = t1.select(0, 2)
                h2 = t2.select(0, 2)
                if match(h1, h2):
                    return h1.row_num
                pass
            return 1

        assert len(xtables) > 1

        if xtables[0].tables[0].is_layout() or xtables[1].tables[0].is_layout():
            #如果是用来布局的表格，没有表头
            return False

        xt1 = xtables[0]
        header = xt1.tables[-1].header
        header_end_row_index = -1
        if header is None:
            header_end_row_index = find_header_end_row_index()
            header = xt1.tables[0].select(0, header_end_row_index)
        else:
            # 表示已经找到表头了
            header_end_row_index = header.row_num

        ok = True
        for xt2 in xtables[1:]:
            # 避免编程错误
            assert len(xt2.tables) == 1
            assert xt2.tables[0].header is None
            header2 = xt2.tables[0].select(0, header_end_row_index)

            if header2.row_num == xt2.tables[0].row_num:
                # 如果去掉表头没有行了，表示不是重复的表头（因为如果出现重复的表头，至少有一行内容需要写到下一页）
                ok = False
                break
            if not match(header, header2):
                ok = False
                break
        if not ok:
            return False

        # 如果第一行都匹配，还需要判断一下是否为表头（可能都是内容，刚好完全匹配）
        if not is_header(header):
            return False

        # 如果有，标记出来
        if set_header:
            for xtable in xtables:
                # 0-1行为header，1-end为body
                for t in xtable.tables:
                    if t.header is None:
                        # 去掉这个判断也可以，只是多计算一次
                        t.set_body(header.row_num)
                    else:
                        pass

        return True

    def _build(self, start: int, end: int, objects: list[XObject]):
        """
        合并start到end之间的表格（可以包含其他不需要的对象），然后直接替换到start到位置，且删除被用掉的对象

        start: 开始的位置

        end：结束的位置，不包括

        objects：相对这个集合，且对该集合进行修改
        """

        tables: list[KTable] = []
        others: list[XObject] = []
        for obj in objects[start:end]:
            if isinstance(obj, XTable):
                tables.extend(obj.tables)
            else:
                others.append(obj)

        if len(tables) == 1 and len(others) == 0:
            # 不需要做改变
            return

        # TODO 在这里考虑如何处理单元格的合并？
        # self._parse_cells(tables)

        xtable = _Builder().build(tables)
        objects[start] = xtable
        del objects[start + 1 : end]
        for obj in others:
            # 中间的对象给抛弃了，如：
            # 续表：
            self._logger.warning('第%s页表格合并，去掉中间的对象：%s',obj.page_numbers,obj)


def _normalize_text(s:str)->str:
    return strs.NText.get(s,mode='q2b',space='remove').text

class _TableState:
    def __init__(self, table: KTable):
        super().__init__()
        self.table: KTable = table
        """表示用来合并的表格内容，如：去掉了重复页头的部分"""
        self.align_cells: Sequence[tuple[KCell, KCell]] = ()
        """上下表格相邻的行对齐的单元格"""


class _CellState:
    def __init__(self, cell: KCell):
        super().__init__()
        self.cell: Final = cell
        self.top_line: bool = True
        self.bottom_line: bool = True
        self.left_line: bool = True
        self.right_line: bool = True
        self.merged: bool | None = None
        """True表示该单元格是否和上一行对应的单元格合并,None表示还没有决定"""
        self.reason: str = ""
        """记录合并/不合并的理由，方便debug"""

        self.case_name: Any = None
        """记录名字，方便查看"""

        self.text = cell.text
        self.text2 = _normalize_text(cell.text)
        """去掉前后空格或者归一化？"""


def T(table: KTable) -> _TableState:
    """获得表格的工作状态"""
    return table.working_state


def C(cell: KCell) -> _CellState:
    """获得单元格的工作状态"""
    return cell.working_state


class _Builder:
    """跨页/跨栏的有边框表格合并"""

    _logger: Logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")

    # 常见类型
    _type_pattern = XPattern(
        "fullmatch",
        join=False,
        patterns=[
            # 数值
            # 复杂的在前面，而且，后面的可以兼容前面的
            # 12,345.12  两位小数
            r"-?\d{1,3}(,\d{3})*(\.\d{2})?",
            # 12.3   一位小数
            r"-?\d{1,3}(,\d{3})*(\.\d{1})?",
            # 1,234   整数
            r"-?\d{1,3}(,\d{3})*",
            # 12345678 整数，
            r"-?\d+",
            # 12.34%
            r"-?[0-9.]+%",
            # 日期
            # 2019-03-04 or 2019-03
            # 2022年04月03日，2022年/04月/03日，2022年-04月-03日
            r"[0-9]{4}年[-/]?[0-9]{1,2}月[-/]?[0-9]{1,2}日",
            r"[0-9]{4}年[-/]?[0-9]{1,2}月",
            # 2022/03/04 or 2022-03-04
            r"[0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2}",
            r"[0-9]{4}[-/][0-9]{1,2}",
            # 03/04/2019
            r"[0-9]{2}/[0-9]{2}/[0-9]{4}",
            r"[0-9]{2}/[0-9]{4}",
            # 阶段
            r"[0-9]{4}-[0-9]{4}年度",
            r"[0-9]{4}年度",
            r"[0-9]{4}年",
        ],
    )

    def __init__(self):
        super().__init__()

    def build(self, tables: Sequence[KTable]) -> XTable:
        """
        把多个跨页/跨栏的表格合并在一起处理
        """

        #tables为
        #[t1,t2,t3,t4] => t1是完整的表格，t2，t3，如果有重复表头的，

        assert len(tables) > 1

        def set_states(tables: Sequence[KTable], clear: bool = False):
            for i, table in enumerate(tables):
                if clear:
                    for cell in T(table).table.cells:
                        cell.working_state = None
                    table.working_state = None
                else:
                    if i == 0:
                        # 第一个总是使用完整的
                        table.working_state = _TableState(table)
                    else:
                        if table.header is not None:
                            # 必须存在，对于只有表头的不合并，因为不认为是跨页
                            assert table.body is not None
                            table.working_state = _TableState(table.body)
                        else:
                            table.working_state = _TableState(table)

                    for cell in T(table).table.cells:
                        cell.working_state = _CellState(cell)

        def setup_merge_info(xtable: XTable):
            # 对应跨行合并的单元格，记录状态
            for i, t in enumerate(xtable.tables):
                if i > 0:
                    # 第一个表格不需要设置，后续表格也仅仅需要设置第一行的
                    for cell in T(t).table.get_row(0):
                        cell.merged = C(cell).merged
                        if cell.original_cell is not None:
                            # 去掉了header，为body（就需要original_cell)
                            cell.original_cell.merged = C(cell).merged

                # 表示跨页/跨栏合并了
                t.merged = True
                # 记录合并的信息，方便debug/或者html显示等
                t.merge_info = {
                    "index": i,
                    "total": len(tables),
                    "row_num": xtable.row_num,
                    "col_num": xtable.col_num,
                }

        # 在处理合并的时候，就已经找到了表头是否重复了
        # working_tables = get_working_tables(tables)

        if tables[0].header is None:
            # 表示没有重复的表头，但是为了能够对表格进行合并进行打分，还是需要判断是否有表头的
            # 对于复杂的表格，可能有多个“逻辑表格”，也就是虽然只是一个表格，但是内部结构根据逻辑分成多个不同的部分，每个部分都是独立的
            # 这种表格就很难了
            pass

        set_states(tables)
        # 逐步合并的方式
        xtable: XTable = XTable.from_object(tables[0])
        xtable.working_tables = [tables[0]]
        # 0表示不使用，1表示仅仅测试一次，2表示每次都测试
        # 如果需要最完善的支持，使用2，可以支持部分表头分离
        batch_mode = 1
        if batch_mode == 1 and (a := self._batch_merge(xtable, tables[1:])) is not None:
            xtable = a
        else:
            i = 1
            while i < len(tables):
                # 这里也可以继续使用批量处理，因为如果是非常非常非常特殊的文档，可能表头就已经跨页了，所以就需要按正常的方式处理
                # 然后后续再批处理
                if (
                    batch_mode == 2
                    and (a := self._batch_merge(xtable, tables[i:])) is not None
                ):
                    xtable = a
                    break
                xtable = self._merge(xtable, tables[i])
                i += 1
        
        #有时候，在全部合并后，再全局参考哪些还需合并的，因为仅仅看相邻的表格，可能不够
        #需要多看几个表格

        # 记录由哪些表格组成，注意：不是working_tables(去掉看重复的表头)
        # xtable.tables=tuple(tables)

        setup_merge_info(xtable)
        # 清除状态
        set_states(tables, clear=True)

        return xtable

    def _get_align_cells(
        self, row1: Sequence[KCell], row2: Sequence[KCell]
    ) -> list[tuple[KCell, KCell]]:
        # 判断上下相邻的cell是否需要合并
        def is_align(c1: KCell, c2: KCell) -> bool:
            b1 = c1.bbox.adjust(x0=c1.bbox.x0-c1.table.bbox.x0)
            b2 = c2.bbox.adjust(x0=c2.bbox.x0-c2.table.bbox.x0)
            return b1.align("x", b2, d=5)


        align_cells: list[tuple[KCell, KCell]] = []
        i = 0
        for c1 in row1:
            for j in range(i, len(row2)):
                c2 = row2[j]
                if is_align(c1, c2):
                    align_cells.append((c1, c2))
                    i = j + 1
                    break
        return align_cells

    def _get_align_cells2(
        self, row1: Sequence[KCell], row2: Sequence[KCell]
    ) -> list[tuple[KCell, Sequence[KCell]]]:
        # 判断上下相邻的cell是否需要合并

        def get_cells(c1: KCell, index: int, row: Sequence[KCell]) -> list[KCell]:
            cells: list[KCell] = []
            #b1 = c1.bbox.translate(-c1.table.bbox.x0, 0).adjust(dx=5, dy=5)
            b1 = c1.bbox.adjust(x0=c1.bbox.x0-c1.table.bbox.x0).expand(dx=5,dy=5)
            bboxes: list[BBox] = []
            for i in range(index, len(row)):
                c2 = row[i]
                #b2 = c2.bbox.translate(-c2.table.bbox.x0, 0)
                b2 = c2.bbox.adjust(x0=c2.bbox.x0-c2.table.bbox.x0)
                if b2.x0 >= b1.x0 and b2.x1 <= b1.x1:
                    cells.append(c2)
                    bboxes.append(b2)
                    i += 1
                else:
                    break

            if not cells:
                return []

            b = BBox.join(bboxes)
    
            if abs(b.x0 - b1.x0) <= 5 and abs(b.x1 - b1.x1) <= 5:
                return cells
            else:
                return []

        align_cells: list[tuple[KCell, Sequence[KCell]]] = []
        i = 0
        for c1 in row1:
            a = get_cells(c1, i, row2)
            if a:
                # 如果有一个不对齐，就不执行了
                i += len(a)
                align_cells.append((c1, a))
            else:
                break

        if i != len(row2) or len(align_cells) != len(row1):
            # 必须完全匹配
            return []
        return align_cells

    def _batch_merge(self, xtable: XTable, tables: Sequence[KTable]) -> XTable | None:
        # local/广发2/pdfs/中南建设2022年度汇报.pdf 11-25

        no_pattern = XPattern(
            "fullmatch", join=False, patterns=[r"[0-9]+", r"第[0-9]+项"]
        )

        def use_batch() -> bool:
            #if not tables[0].is_ybk():
                #return False

            if re.fullmatch(r"序号",_normalize_text(xtable[0, 0].text)) is None:
                return False

            # 可能为表头（极端情况表头也会跨页，所以不一定完整）
            first_row = xtable.get_row(0)
            max_row_index = max(c.row_index + c.row_span for c in first_row)
            for c in xtable.cells:
                # 除了表头（可能重复或者不重复）
                if c.row_index >= max_row_index and c.row_span != 1:
                    return False

            for i in range(len(tables)):
                if i == 0:
                    t1 = T(xtable.tables[-1]).table
                else:
                    t1 = T(tables[i - 1]).table
                t2 = T(tables[i]).table
                if t1.col_num != t2.col_num:
                    return False

                #if not t2.is_ybk():
                    #return False

                # 没有跨行（可以有跨栏，因为表头）
                for c in t2.cells:
                    if c.row_span != 1:
                        return False

                # 虽然经过这么严格的判断，还是无法100%的，如：
                # [序号] [xx][xxx]
                # [1]   [aa][cc]
                # -----------------
                # [ ]   [bb][dd]   =>有可能aa+bb需要合并的，或者cc+dd合并，这种概率比较少

            return True

        def is_no(s: str) -> bool:
            return no_pattern.fullmatch(s) is not None

        def is_blank(c: KCell) -> bool:
            return len(c.objects) == 0

        def is_continue(noes:Sequence[int])->bool:
            for k in range(1,len(noes)):
                if noes[k-1]+1!=noes[k]:
                    return False
            return True
        def has_no_and_blank(t:KTable,col_index:int)->bool:
            blank_count=0
            noes:list[int]=[]
            for j in range(t.row_num):
                s=t[j,col_index].text
                if not s:
                    blank_count+=1
                else:
                    try:
                        k=int(s)
                        noes.append(k)
                    except ValueError:
                        return False
            if blank_count>0 and len(noes)>0 and is_continue(noes):
                #严格的必须为连续的
                return True
            else:
                return False

        if not use_batch():
            return None

        timer = utils.Timer.start()
        ok = True
        i = 0
        while i < len(tables):
            # 如果有序号，且有重复表头
            if i == 0:
                t1 = xtable.tables[-1]
            else:
                t1 = tables[i - 1]
            t2 = tables[i]

            wt1 = T(t1).table
            wt2 = T(t2).table
            align_cells = self._get_align_cells(wt1.get_row(-1), wt2.get_row(0))
            T(t2).align_cells = align_cells
            # 必须完全匹配

            if len(align_cells) == 0 and len(wt2.get_row(0)) == 1:
                # json2doc/表格合并/ybk/百页表格.pdf  367-388
                # [xx][xx][xxx] wt1.get_row(-1)
                # ------------------------
                # [xxxxxxxxxxx] wt2.get_row(0)，可能为标题，允许这种
                C(wt2.get_row(0)[0]).merged = False
                C(wt2.get_row(0)[0]).reason = "没有对齐"
                break

            if len(align_cells) != t2.col_num:
                ok = False
                break

            c1, c2 = align_cells[0]
            if is_no(C(c1).text2) and is_no(C(c2).text2):
                # 如果为连续的序号，就表示不需要合并
                for c1, c2 in align_cells:
                    C(c2).merged = False
                    C(c2).reason = "c1和c2都是序号"
            elif is_no(C(c1).text2) and is_blank(c2):
                # 如果为空格，就是需要合并
                #TODO 这个有例外的，如：
                #[1][xx]
                #---------------
                #[ ][xx] =>不合并，从逻辑上，合并也没有错误，但是因为为有边框表格，且后面有不合并的案例，不合并更符合原意图
                #[2][xx]
                #[ ][xx]
                #[3][xx]
                #[4][xx]

                if has_no_and_blank(c2.table,c2.col_index):
                    if True:
                        ok=False
                        break
                    else:
                        for c1, c2 in align_cells:
                            #TODO 不能够简单的判断为合并，还需要通过其他列判断
                            C(c2).merged = False
                            C(c2).reason = "c1为序号，c2为空，但是该列存在其他空白"
                else:
                    for c1, c2 in align_cells:
                        C(c2).merged = True
                        C(c2).reason = "c1为序号，c2为空"
            elif (
                not is_blank(c1)
                and not is_blank(c2)
                and is_no(C(c1).text2 + C(c2).text2)
            ):
                for c1, c2 in align_cells:
                    C(c2).merged = True
                    C(c2).reason = "c1+c2为序号"
            else:
                # 无法判断，返回
                # print('==>f2', C(c1).text2, C(c2).text2, is_no(C(c1).text2), is_no(C(c2).text2))
                ok = False
                break

            i += 1

        if not ok:
            # 还原状态
            for t in tables:
                for c1, c2 in T(t).align_cells:
                    C(c2).merged = None

                T(t).align_cells = ()
            return None

        for t in tables:
            for c1, c2 in T(t).align_cells:
                if C(c2).merged:
                    C(c1).bottom_line = False
                    C(c2).top_line = False

            # 如果后续允许更复杂的情况
            for c2 in T(t).table.get_row(0):
                if C(c2).merged is None:
                    C(c2).merged = False
                    C(c2).reason = "c1,c2没有对齐"

            T(t).align_cells = ()

        # 一次性搞定
        # print(f'========>use batch,times={timer.elapsed():.3f}')
        return self._build_xtable(xtable, tables)

    def _merge(self, xtable: XTable, next_table: KTable) -> XTable:
        # 获得对齐的单元格，判断是否需要合并

        # debug = utils.Debug({'info': True, 'gui': True})
        # debug.enable = True

        debugger = self._debugger.bind()

        # 单元格合并使用当页的表格结构，而不是前几个表格合并后的新的表格
        # 可以简化合并，不需要看太长的信息，但是也导致一些合并错误
        # 有些复杂的表格可能需要几十页一起考虑单元格的合并，典型的就是一些包含介绍文本的内容
        # 可能有一列全部都是写文，非常长，跨几十页
        working_tables: list[KTable] = []
        working_tables.extend(T(t).table for t in xtable.tables)
        working_tables.append(T(next_table).table)

        def setup_merge_status(
            table: KTable, align_cells: Sequence[tuple[KCell, KCell]]
        ):
            row = table.get_row(0)
            for c in row:
                for c1, c2 in align_cells:
                    if c is c2:
                        break
                else:
                    # 如果不是上下对齐的，就总是为False
                    C(c).merged = False
                    C(c).reason = "no align"

        def get_fraction(s: str) -> int | None:
            # TODO 这个可以放宽的
            a_pattern = re.compile(r"-?\d{1,3}(,\d{3})*(?P<fraction>\.\d+)?")
            m = a_pattern.fullmatch(s)
            if m is None:
                return None
            s = m.group("fraction")
            if not s:
                return 0
            else:
                return len(s) - 1

        def is_decimal(s: str, digit: int = 2) -> bool:
            a_pattern = re.compile(r"-?\d{1,3}(,\d{3})*(\.(?P<fraction>\d+))?")
            # a_pattern = re.compile(r'-?\d+(?P<fraction>\.\d+)?')
            m = a_pattern.fullmatch(s)
            if m is None:
                return False
            fraction = m.group("fraction") or ""
            return len(fraction) == digit

        no_pattern = XPattern(
            "fullmatch", join=False, patterns=[r"[0-9]+", r"第[0-9]+项"]
        )

        def is_no(s: str) -> bool:
            return no_pattern.fullmatch(s) is not None

        def is_percent(s: str) -> bool:
            return re.fullmatch(r"-?[0-9.]+%", s) is not None

        def is_blank(cell: KCell) -> bool:
            # TODO 忽略线，矩形等的影响，仅仅考虑char/figure/formula？
            for obj in cell.objects:
                if not isinstance(obj, (KLine, KRect)):
                    return False
            return True

        def has_figures(cell: KCell) -> bool:
            # 如果仅仅只有图片
            return len(cell.objects) > 0 and all(
                isinstance(obj, KFigure) for obj in cell.objects
            )

        def get_column_texts(cell: KCell) -> list[str]:
            table = cell.table
            column = table.get_column(cell.col_index)
            texts: list[str] = []
            # 如果是第一个表格，可以去掉表头，但是这里不去掉，也可以，不怎么影响
            # 而且有些表格也是混合多个结构的表格
            for c in column:
                if c is cell:
                    continue
                if (
                    c.col_index == cell.col_index
                    and c.col_span == cell.col_span
                    and C(c).text2
                ):
                    texts.append(C(c).text2)
            return list(set(texts))

        def get_column_patterns(cell: KCell) -> list[re.Pattern[str]]:
            texts = get_column_texts(cell)

            for t in texts:
                m = self._type_pattern.fullmatch(t)
                if m is not None:
                    p = m.re

                pass
            return []

        def get_type_pattern(s: str) -> re.Pattern[str] | None:
            m = self._type_pattern.fullmatch(s)
            if m is None:
                return None
            else:
                # 也可能为regex.Pattern，但是目前不使用这个
                assert isinstance(m.re, re.Pattern)
                return m.re

        def is_finish() -> bool:
            for c1, c2 in align_cells:
                if C(c2).merged is None:
                    return False
            return True

        def get_column_indexes(c1: KCell, pattern: re.Pattern[str]) -> list[int]:
            header: KTable | None = None
            if c1.table.header is not None:
                # 第一个表格，且有表头
                # header和c1.table的列数一致
                header = c1.table.header
            elif c1.table.main is not None:
                # 有重复表头的，c1.table为body,c1.main为完整的table
                # header和main的列数一致
                header = c1.table.main.header
            else:
                # 没有重复的表头
                header = c1.table

            col_indexes: list[int] = []
            if header is not None:
                for c in header.get_row(0):
                    # TODO 可能需要使用归一化后且去掉空格的？
                    if pattern.fullmatch(c.text.strip()):
                        col_indexes.append(c.col_index)
            return col_indexes

        def has_blank_cells(cell: KCell) -> bool:
            # TODO 可以考虑去掉表头
            # TODO 如果单元格已经
            column = cell.table.get_column(cell.col_index)

            for c in column:
                if c is cell:
                    continue
                # 如果已经被标记为合并，还需要判断合并后是否为空吗？
                if C(c).merged:
                    continue
                if is_blank(c):
                    return True
            return False

        def handle_header():
            # 去掉判断也可以
            # json2doc/表格合并/ybk/1-债券募集说明书.pdf 175,176
            # local/表格合并/pdfs/2021-06-08[300266]2020年年度报告（更新后）.PDF  123-134 多行表头
            if len(working_tables) != 2:
                # 必须为第一个表格+后续表格的合并
                return False

            wt1 = working_tables[0]
            wt2 = working_tables[1]

            row1: list[KCell] = wt1.get_row(-1)
            row2: list[KCell] = wt2.get_row(0)

            def is_header(wt: KTable, strict: bool = False) -> bool:
                if strict:
                    for c in wt.get_row(-1):
                        if c.subtype == "header":
                            return True
                    return False
                else:
                    # 因为整个表格就是重复表头，使用同一个
                    return wt is wt.header

            if is_header(wt1, strict=True):
                # 表示为表头重复，且只有表头
                # 如果需要严谨的
                for cell in row2:
                    C(cell).merged = False
                    C(cell).reason = "t1为表头"
                return True

            if not (
                min(c.row_index for c in row1) == 0
                and max(c.row_index + c.row_span for c in row1) == wt1.row_num
            ):
                return False
            # 如果是完全对齐？可能合并
            if len(row1) >= len(row2):
                return False
            align_cells2 = self._get_align_cells2(row1, row2)
            if not align_cells2:
                return False
            # 肯定存在col_span
            for c1, c2s in align_cells2:
                for c2 in c2s:
                    if len(c2s) > 1:
                        C(c2).merged = False
                        C(c2).reason = "t1为表头，c1跨列"
                    else:
                        C(c2).merged = True
                        C(c2).reason = "t1为表头，c1和c2对齐"
            return True

        def is_continue(noes:Sequence[int])->bool:
            for k in range(1,len(noes)):
                if noes[k-1]+1!=noes[k]:
                    return False
            return True
        def has_no_and_blank(t:KTable,col_index:int)->bool:
            blank_count=0
            noes:list[int]=[]
            for j in range(t.row_num):
                s=t[j,col_index].text
                if not s:
                    blank_count+=1
                else:
                    try:
                        k=int(s)
                        noes.append(k)
                    except ValueError:
                        return False
            if blank_count>0 and len(noes)>0 and is_continue(noes):
                #严格的必须为连续的
                return True
            else:
                return False

        def case0(i: int):
            """单行"""
            # 案例1:
            # [--][--][--]
            # [xx        ](t1)
            # [xx        ](t2)
            # [--][--][--]
            # TODO 也有可能不合并
            # local/表格合并/pdfs/2021-03-23[600731]2020年年度报告.PDF   183,184页
            c1, c2 = align_cells[i]
            if (
                c1.col_span == c1.table.col_num
                and c2.col_span == c2.table.col_num
                and C(c1).text2
                and C(c2).text2
            ):
                # 获得内容的区域
                b1 = c1.content_bbox
                b2 = c2.content_bbox
                if b1 is None or b2 is None:
                    return

                if c1.bbox.x1 - b1.x1 >= 20 and c2.bbox.x1 - b2.x1 >= 20:
                    C(c2).merged = False
                    C(c2).reason = "c1和c2占据整行，且右边有空间剩余，不合并"
                pass

        def case1(i: int):
            """处理序号"""

            c1, c2 = align_cells[i]
            # 可能有多个序号列
            col_indexes: list[int] = get_column_indexes(c1, re.compile(r"序号"))
            # 限制为前面2列？
            s1 = C(c1).text2
            s2 = C(c2).text2

            if col_indexes and c1.col_index in col_indexes or c1.col_index < 2:
                if is_no(s1) and is_no(s2):
                    # TODO 需要检查连续吗？可能书写错误，不一定连续
                    # [19]c1
                    # [20]c2

                    # 也可能空白
                    # []c1
                    # []c2

                    # 特殊的：
                    # local/cases/pdfs/a.pdf 56-57
                    # [序号][xxx][xxx][xxx]
                    # [1  ][xxx][xxx][xxx]
                    # ----------------------
                    # [2  ][   ][xxx][xxx]
                    # [3  ][   ][xxx][xxx]
                    # [4  ][xxx][xxx][xxx]
                    C(c2).merged = False
                    C(c2).reason = "c1为序号，c2为序号"
                elif is_no(s1) and is_blank(c2):
                    # [xxx]c1
                    # [   ]c2

                    #TODO 这个有例外的，如：
                    #[1][xx]
                    #---------------
                    #[ ][xx] =>不合并，从逻辑上，合并也没有错误，但是因为为有边框表格，且后面有不合并的案例，不合并更符合原意图
                    #[2][xx]
                    #[ ][xx]
                    #[3][xx]
                    #[4][xx]
                    if has_no_and_blank(c2.table,c2.col_index):
                        #待定？
                        #C(c2).merged = False
                        #C(c2).reason = "c1为序号，c2空白，但是该列允许序号为空"
                        pass
                    else:
                        C(c2).merged = True
                        C(c2).reason = "c1为序号，c2空白"
                elif not is_blank(c1) and not is_blank(c2) and is_no(s1 + s2):
                    C(c2).merged = True
                    C(c2).reason = "c1+c2为序号"
                else:
                    # c1和c2都空白，没有或者丢失了序号
                    pass

        def case2(i: int):
            """内容相同的"""
            c1, c2 = align_cells[i]
            if c1.row_span == c2.row_span:
                # TODO 需要吗？
                pass
            s1 = C(c1).text2
            s2 = C(c2).text2
            if s1 == s2 and len(s2) > 0:
                C(c2).merged = False
                C(c2).reason = "c1和c2的内容相同"

        def case3(i: int):
            """处理数值，因为最常见，而且有时候完整的数值也跨页"""
            c1, c2 = align_cells[i]
            # 前面的案例已经比较过相同的，如，没有数值，可能使用下面3种方式：
            # [-]  [/]   [0]
            # [-]  [/]   [0]
            s1 = C(c1).text2
            s2 = C(c2).text2
            s = s1 + s2
            if not s1 or not s2:
                return

            if is_decimal(s, 2):
                # 有些会分成2行写，跨页分行，所以这里合并检查且必须为2位小数
                # 如果如下，是很难分辨是否需要合并的
                # [12,34]c1
                # [3.06]c2
                if re.fullmatch(r"[-]", s1):
                    C(c2).merged = False
                    # 也有可能是合并为一个负数，但是多数情况下都是使用“-”表示没有数值
                    C(c2).reason = 'c1为"-"，c2为小数'
                else:
                    C(c2).merged = True
                    C(c2).reason = "c1+c2为数值，且为2位小数"
            elif is_percent(s1) and is_percent(s2):
                C(c2).merged = False
                C(c2).reason = "c1,c2为百分比"
            elif not s1 and (
                re.fullmatch(r"(-?[0-9.]+%)|(-?[0-9.,]+)|(-)|(--)|(/)", s2) is not None
            ):
                # [  ]c1
                # ---------
                # [12.3]c2
                C(c2).merged = False
                C(c2).reason = "c1为空，c2位数值/百分比"
            else:
                # 数值在没有的时候，可能使用"-,--,/,0"等表示
                fraction1 = get_fraction(s1)
                fraction2 = get_fraction(s2)
                if fraction1 is None and fraction2 is None:
                    # 没有数值
                    return

                if (
                    fraction1 is not None
                    and fraction2 is not None
                    and fraction1 > 0
                    and fraction2 > 0
                ):
                    C(c2).merged = False
                    C(c2).reason = "c1和c2都带小数"
                elif (
                    fraction1 is not None
                    and s2 in ("-", "--", "/")
                    or fraction2 is not None
                    and s1 in ("-", "--", "/")
                ):
                    C(c2).merged = False
                    C(c2).reason = "c1和c2都是数值+无值"
                elif fraction1 is not None and fraction2 is not None:
                    # 如果不带小数的，也认为不需要合并？
                    C(c2).merged = False
                    C(c2).reason = "c1和c2都是整数"
                else:
                    pass

        def case4(i: int):
            """处理重复出现的内容，如：是/否，男/女等"""
            c1, c2 = align_cells[i]
            if c1.row_span == c2.row_span:
                pass

            s1 = C(c1).text2
            s2 = C(c2).text2
            s = s1 + s2
            if s1 and s2:
                s = s1 + s2
                texts = get_column_texts(c1) + get_column_texts(c2)
                if s in texts:
                    C(c2).merged = True
                    C(c2).reason = "c1+c2的内容在该列重复出现"
                elif s2 in texts:
                    C(c2).merged = False
                    C(c2).reason = "c2的内容在该列重复出现"
                elif s1 in texts:
                    C(c2).merged = False
                    C(c2).reason = "c1的内容在该列重复出现"
                else:
                    pass

        def case5(i: int):
            """根据该列的内容类型判断，如：数值，日期等"""
            c1, c2 = align_cells[i]
            s1 = C(c1).text2
            s2 = C(c2).text2
            if not s1 or not s2:
                return

            p1 = get_type_pattern(s1)
            p2 = get_type_pattern(s2)
            p = get_type_pattern(s1 + s2)
            if p1 is not None and p1 == p2:
                C(c2).merged = False
                C(c2).reason = "c1和c2的内容相似"
            elif p is not None:
                C(c2).merged = True
                C(c2).reason = "c1+c2为常见类型"
            elif p1 != p2:
                # 类型不同？大概率为表头？不合并？
                C(c2).merged = False
                C(c2).reason = "c1和c2的内容类型不同"
                pass

        def case6(i: int):
            """判断是否为常见词汇"""
            c1, c2 = align_cells[i]

            s1 = C(c1).text2
            s2 = C(c2).text2

            col_indexes = get_column_indexes(c1, re.compile(r"项目"))
            if s1 and s2 and c1.col_index in col_indexes:
                # 必须在指定列中，这样可以提高准确度，其他列的文本太随意
                # 可以选择根据字典/或者模型等来计算
                a1, a2, a3 = _TableAI.parse_phrases(s1, s2)
                if a3:
                    # 合并
                    C(c2).merged = True
                    C(c2).reason = "c1+c2为常见词汇"
                elif a1 and a2:
                    C(c2).merged = False
                    C(c2).reason = "c1和c2为常见词汇"

            else:
                # 普通的文本太难判断了
                pass

        def case7(i: int):
            """处理包含文本+图片的"""
            c1, c2 = align_cells[i]
            s1 = C(c1).text2
            s2 = C(c2).text2
            if s1 and not has_figures(c1) and not s2 and has_figures(c2):
                # local/广发/v3.0与v4.0比对召回测试标准集/pdfs/23-溯联股份招股书申报稿.pdf --feature guangfa  108-109
                # [text]c1
                # --------------不合并，因为c1可能就是表头
                # [figure]c2
                C(c2).merged = False
                C(c2).reason = "c1有文字，c2有图片"
            elif has_figures(c1) and has_figures(c2):
                # 在多数情况下都是不需要合并的，但是例外：
                # local/广发/v3.0与v4.0比对召回测试标准集/pdfs/23-溯联股份招股书申报稿.pdf --feature guangfa  109-110  图片需要合并
                # 所以说目前还不能够判断
                pass
            else:
                pass

        def case8(i: int):
            """根据跨行判断"""
            c1, c2 = align_cells[i]
            # 反列，如下就不能够合并，这种太难理解
            # ocal/cases/pdfs/奇怪的表格-万德斯招 股说明书-V1.pdf 58-59

            if (
                (c1.row_span > 1 and c2.row_span > 1)
                and not is_blank(c1)
                and not is_blank(c2)
            ):
                # 如果两个都跨行且都有内容，不合并的可能性大
                # TODO 这个还是需要通过ai来完成
                # 包括了矩形/线等
                # cb2 = c2.body.content_bbox
                #cb2 = BBox.x_union(c2.body.texts())
                cb2 = BBox.join2([obj for obj in c2.objects if isinstance(obj,KText)],strict=False)
                if cb2 is not None and cb2.y0 - c2.bbox.y0 >= c2.bbox.y1 - cb2.y1 + 10:
                    C(c2).merged = True
                    C(c2).reason = "c1和c2都跨行且有内容，但是c2剩余空间多"
                else:
                    C(c2).merged = False
                    C(c2).reason = "c1和c2都跨行且都有内容"
                pass

        def case9(i: int):
            """处理有空白的"""
            c1, c2 = align_cells[i]
            s1 = C(c1).text2
            s2 = C(c2).text2
            if s1 and not s2 and (c1.row_span > 1 or c2.row_span > 1):
                # cases/json2doc/表格合并/ybk/1-债券募集说明书.pdf 86-87，72-73，79-80
                # [c1][xx]
                # [c1][yy]
                # --------
                # [c2][zz]

                # 因为c1存在跨行，然后c2空白，那么c1和c2合并的可能性很大
                C(c2).merged = True
                C(c2).reason = "c1跨行或者c2跨行，c2空白"

            elif (
                not is_blank(c1)
                and is_blank(c2)
                and not has_blank_cells(c1)
                and not has_blank_cells(c2)
            ):
                # [xx]c1
                # [  ]c2
                C(c2).merged = True
                C(c2).reason = "c2为空白，但是该列不允许空白"
            elif c1.table.row_num == 1 and is_blank(c1) and is_blank(c2):
                # 前一个表格只有一行，占据了整页
                C(c2).merged = True
                C(c2).reason = "c1为空白，c2为空白，c1占据了整页"
                pass

        def case10(i: int):
            """处理包含图片的"""
            c1, c2 = align_cells[i]
            s1 = C(c1).text2
            s2 = C(c2).text2
            if has_figures(c1) and has_figures(c2):
                # 在多数情况下都是不需要合并的，但是例外：
                # local/广发/v3.0与v4.0比对召回测试标准集/pdfs/23-溯联股份招股书申报稿.pdf --feature guangfa  109-110  图片需要合并（已经通过其他方式推理了）
                # 但是执行到这里，表示已经无法通过其他方式来判断了，所以执行到这里，就总是不合并
                C(c2).merged = False
                C(c2).reason = "c1和c2都是图片"
                pass
            else:
                pass

        def case11(i: int):
            """根据剩余空间判断"""
            c1, c2 = align_cells[i]
            s1 = C(c1).text2
            s2 = C(c2).text2
            cb1 = c1.content_bbox
            cb2 = c2.content_bbox
            if s1 and s2 and cb1 is not None and cb2 is not None:
                # 因为需要支持跨栏，只能够使用相对的，而不能够使用c1.bbox.x0==c2.bbox.x0这种方式
                if c1.row_span == 1 and c1.bbox.x1 - cb1.x1 >= 20:
                    # [xx   ]c1
                    # ------------
                    # [xxxx]c2
                    C(c2).merged = False
                    C(c2).reason = "c1右边有空间"
                elif (
                    c2.row_span == 1
                    and c2.bbox.y1 - cb2.y1 >= 10
                    and cb2.y0 - c2.bbox.y0 >= 10
                ):
                    C(c2).merged = False
                    C(c2).reason = "c2上下有空间"
                else:
                    pass
        
        def case12(i:int):
            """根据空间判断"""
            #[c1]
            #----------------
            #[c2]  =>
            #[c3]  =>如果c3为垂直居中对齐，而c1占据全部空间，c2内容出现在顶部，且有大量空间声音，就可以认为c1和c2需要合并，否则c2应该居中
            #      =>有时候某个单元格内容太多，导致一页只有一个单元格，无法判断，就需要考虑多页？
            #[c4]
            c1, c2 = align_cells[i]
            s1 = C(c1).text2
            s2 = C(c2).text2
            cb1 = c1.content_bbox
            cb2 = c2.content_bbox
            #TODO 这里关键是需要先确定该列的内容是否居中书写
            if s1 and s2 and cb1 and cb2 and cb1.expand(dx=6,dy=6).contains(c1.bbox) and c2.bbox.y1-cb2.y1<=5 and cb2.y0-c2.bbox.y0>=20:
                #严格的判断最后一行也写满
                C(c2).merged=True
                C(c2).reason='该列的内容为居中书写，c2现在为顶部，合并'
                pass

        def do_final():
            """对于没有办法知道是否合并的，最终都给一个决定"""
            merged_cells: list[KCell] = []
            no_merged_cells: list[KCell] = []
            pending_merged_cells: list[KCell] = []
            for c1, c2 in align_cells:
                merged = C(c2).merged
                if merged is None:
                    pending_merged_cells.append(c2)
                elif merged:
                    merged_cells.append(c2)
                else:
                    no_merged_cells.append(c2)

            if not pending_merged_cells:
                return

            for c2 in pending_merged_cells:
                # 对于其他未知的
                merged = len(merged_cells) >= 1
                C(c2).merged = merged
                if merged:
                    C(c2).reason = "无法判断，但是有合并的，所以合并"
                else:
                    C(c2).reason = "无法判断，但是没有合并的，所以不合并"

        def is_row_align(*cells: KCell) -> bool:
            c1 = cells[0]
            for c2 in cells[1:]:
                if c1.row_span != c2.row_span or c1.row_index != c2.row_index:
                    return False
            return True

        def is_column_align(*cells: KCell) -> bool:
            c1 = cells[0]
            for c2 in cells[1:]:
                if c1.col_span != c2.col_span or c1.col_index != c2.col_index:
                    return False
            return True

        def infer_by(
            row1: list[KCell],
            index: int,
            row2: list[KCell],
            dir_: Literal["left", "right"],
        ) -> bool:
            # [   ][c1]       [c1]
            # [ref][c2]  或者  [c2][ref]

            c2 = row2[index]
            # c1, c2 = align_cells[index]
            if C(c2).merged is not None:
                return True

            # align_index=-1
            c1: KCell | None = None
            for i, (a, b) in enumerate(align_cells):
                if c2 is b:
                    c1 = a
                    # align_index = i
                    break
            pass
            ref1: KCell | None = None
            ref2: KCell | None = None
            if dir_ == "left" and index - 1 >= 0:
                # ref1, ref2 = align_cells[index-1]
                ref2 = row2[index - 1]
            elif dir_ == "right" and index + 1 < len(row2):
                # ref1, ref2 = align_cells[index+1]
                ref2 = row2[index + 1]
            else:
                return False

            if c1 is not None:
                # 表示c1和c2是对齐的，所以也可以使用c1的参考
                i = row1.index(c1)
                if dir_ == "left" and i - 1 >= 0:
                    ref1 = row1[i - 1]
                elif dir_ == "right" and i + 1 < len(row1):
                    ref1 = row1[i + 1]
                else:
                    pass

            if C(ref2).merged is None:
                return False

            # 必须在同一行且行对齐
            if not is_row_align(c2, ref2):
                # [ref2][c2] or [c2][ref2]
                return False

            # 必须为左相邻或者右相邻
            if not (
                c2.col_index + c2.col_span == ref2.col_index
                or ref2.col_index + ref2.col_span == c2.col_index
            ):
                return False

            # 往下看
            n = 0
            for i in range(c2.row_index + c2.row_span, c2.table.row_num):
                x1 = c2.table[i, c2.col_index]
                x2 = c2.table[i, ref2.col_index]
                if (
                    c2.row_span == x1.row_span
                    and is_column_align(c2, x1)
                    and is_column_align(ref2, x2)
                    and is_row_align(x1, x2)
                ):
                    # [x2]  [x1] or [x1][x2]
                    # [ref1][c2] or [c2][ref1]
                    n += 1
                else:
                    break

            # 往上看
            if c1 is not None and ref1 is not None:
                for i in range(c1.row_index - 1, -1, -1):
                    x1 = c1.table[i, c1.col_index]
                    x2 = c1.table[i, ref1.col_index]
                    if (
                        c1.row_span == x1.row_span
                        and is_column_align(c1, x1)
                        and is_column_align(ref1, x2)
                        and is_row_align(x1, x2)
                    ):
                        # [x2]  [x1] or [x1][x2]
                        # [ref1][c1] or [c1][ref1]
                        n += 1
                    else:
                        break

            if n > 0:
                C(c2).merged = C(ref2).merged
                C(c2).reason = f"infer by {dir_}"
                return True
            else:
                return False

        def infer_others():
            # 有些没有对齐的，也可以参考
            row1: list[KCell] = working_tables[-2].get_row(-1)
            # row2:list[_Cell] = working_tables[-1].get_row(0)
            # 暂时使用这个更好
            row2 = [a[1] for a in align_cells]
            for i in range(len(row2)):
                # 根据左右相邻的推测
                _ = infer_by(row1, i, row2, "left") or infer_by(row1, i, row2, "right")

        # TODO 如果是第一个表格，使用完整的表格，但是可能只有表头
        if (
            len(xtable.working_tables) == 1
            and xtable.working_tables[0].header is not None
            and xtable.working_tables[0].body is None
        ):
            # local/广发2/pdfs/寒武纪上会稿.pdf 304-305
            align_cells = []
        else:
            align_cells = self._get_align_cells(
                xtable.working_tables[-1].get_row(-1),
                T(next_table).table.get_row(0),
            )

        if not handle_header():
            setup_merge_status(T(next_table).table, align_cells)

            # 模拟人类的思维，在大脑中拥有了相关的知识，然后扫描相邻的上下行，判断是否需要合并
            # 大脑会优先最可能的方案先判断
            cases = [
                case0,
                case1,
                case2,
                case3,
                case4,
                case5,
                case6,
                case7,
                case8,
                case9,
                case10,
                case11,
                #不够严谨，暂时先禁用
                case12,
            ]
            # 先执行？如果有不对齐的，就已经可以推理多数了？
            # 也可以注释掉
            # infer_others()
            while not is_finish() and cases:
                case = cases.pop(0)
                for i in range(len(align_cells)):
                    if C(align_cells[i][1]).merged is not None:
                        continue
                    case(i)
                    if C(align_cells[i][1]).merged is not None:
                        C(align_cells[i][1]).case_name = case.__name__
                    # 也可以在这里执行
                    # infer_others()
                infer_others()

            do_final()

            if not is_finish():
                raise RuntimeError("编程错误，没有判断完全单元格是否需要合并")

        # if self.ctx.is_debug('/doc/xtable?log',pagenos=[working_tables[-1].page.number]):
        if debugger.allow("info", page=working_tables[-1].page.number):
            with debugger.group("merge cells"):
                for cell in working_tables[-1].get_row(0):
                    debugger.console.print(
                        (
                            cell.col_index,
                            C(cell).merged,
                            C(cell).case_name,
                            C(cell).reason,
                            C(cell).text2[0:10],
                        )
                    )

        for c1, c2 in align_cells:
            if C(c2).merged:
                C(c1).bottom_line = False
                C(c2).top_line = False

        return self._build_xtable(xtable, [next_table])

    def _build_xtable(self, xtable: XTable, new_tables: Sequence[KTable]) -> XTable:
        def is_continue_pages() -> bool:
            for i in range(1, len(working_tables)):
                t1 = working_tables[i - 1]
                t2 = working_tables[i]
                if t1.page.number + 1 != t2.page.number:
                    # 表示为跨栏的，忽略
                    return False
                # 而且x0和x1的误差不能够太大，目的是为了支持调整
                if (
                    abs(t1.bbox.x0 - t2.bbox.x0) > 10
                    or abs(t1.bbox.x1 - t2.bbox.x1) > 10
                ):
                    return False
            return True

        def adjust_lines(
            x0: float | None, x1: float | None, table: KTable
        ) -> tuple[BBox, Sequence[BBox], Sequence[BBox]]:
            bbox = table.bbox
            h_lines,v_lines = table.get_lines()
            h_lines = [line.bbox for line in h_lines]
            v_lines = [line.bbox for line in v_lines]
            if (
                x0 is None
                or x1 is None
                or (abs(table.bbox.x0 - x0) <= 3 and abs(table.bbox.x1 - x1) <= 3)
            ):
                return (bbox, h_lines, v_lines)

            # 表示为另外一个页面的，就需要调整了

            # 误差太大，调整一下
            h_lines = list(h_lines)
            v_lines = list(v_lines)

            for j, line in enumerate(h_lines):
                if abs(line.x0 - table.bbox.x0) <= 1:
                    line = line.adjust(x0=x0)
                    h_lines[j] = line

                if abs(line.x1 - table.bbox.x1) <= 1:
                    h_lines[j] = line.adjust(x1=x1)

            for j, line in enumerate(v_lines):
                if abs(line.x0 - table.bbox.x0) <= 1:
                    line = line.adjust(x0=x0, x1=x0)
                    v_lines[j] = line

                if abs(line.x1 - table.bbox.x1) <= 1:
                    v_lines[j] = line.adjust(x0=x1, x1=x1)
            bbox = bbox.adjust(x0=x0, x1=x1)
            self._logger.warning(
                "跨页表格误差大，需要进行调整,page=%s,table_bbox=%s,new_bbox=%s",
                table.page.number,
                table.bbox,
                bbox,
            )
            return (bbox, h_lines, v_lines)

        def adjust_cell(
            x0: float | None, x1: float | None, table: KTable, bbox: BBox
        ) -> BBox:
            if x0 is None or x1 is None:
                return bbox

            if abs(bbox.x0 - table.bbox.x0) <= 1:
                bbox = bbox.adjust(x0=x0)

            if abs(bbox.x1 - table.bbox.x1) <= 1:
                bbox = bbox.adjust(x1=x1)

            return bbox

        def translate(bbox: BBox, tx: float, ty: float) -> BBox:
            # 对于5万个单元格的，下面这个慢了0.5秒左右
            # bbox.translate(tx,ty)
            x0, y0, x1, y1 = bbox
            return BBox(x0 + tx, y0 + ty, x1 + tx, y1 + ty)
    
        items: list[XItem[KCell]] = []
        # 因为坐标原点使用左下角
        offset_y = 0

        # 单元格的边界线
        working_tables: list[KTable] = []
        working_tables.extend(xtable.working_tables)
        for t in new_tables:
            working_tables.append(T(t).table)

        lines: list[BBox] = []
        # 找到宽度最大的表格为参考？其他表格自动调整两边？
        # 有些奇葩的表格错位严重，需要先调整，否则表格结构奇葩
        enable_adjust_lines = True
        if enable_adjust_lines and is_continue_pages():
            x0 = min(t.bbox.x0 for t in working_tables)
            x1 = max(t.bbox.x1 for t in working_tables)
        else:
            x0 = None
            x1 = None

        for i in range(len(working_tables) - 1, -1, -1):
            table = working_tables[i]
            bbox, h_lines, v_lines = adjust_lines(x0, x1, table)
            tx = -bbox.x0
            ty = -bbox.y0 + offset_y
            # 如果是水平线，顶部的线
            for line in h_lines[1:-1]:
                # 去掉前后的线，因为需要连接
                # 最后一个表格底部的线没有不影响，当然也可以添加
                # 第一个表格顶部的线没有也不影响
                lines.append(translate(line, tx, ty))
            for line in v_lines:
                lines.append(translate(line, tx, ty))

            for cell in table.get_row(0):
                if not C(cell).merged:
                    # 如果没有被合并，添加一条线
                    lines.append(
                        translate(adjust_cell(x0, x1, table, cell.bbox).top, tx, ty)
                    )
                else:
                    pass

            for cell in table.cells:
                # TODO 如果需要速度更快，可以直接使用cell.body即可
                # 然后在make_ybk_table()中再展开
                # 直接使用cell.bbox，填充更加准确
                #TODO 可以考虑使用 cell.original_cell or cell
                #这样，可以使用原始table的cell而不是body table的cell
                if cell.original_cell is not None:
                    if cell.original_cell.table is not cell.table.main:
                        #cell不是header或者body中的，而是另外的表格，通常来自strip，copy等
                        self._logger.warning('第%s页的表格，original_cell指向其他',table.page.number)
                    else:
                        cell = cell.original_cell
                items.insert(0, XItem(translate(cell.bbox, tx, ty),cell))

            offset_y += table.bbox.height

        # 可以添加top/bottom，不添加也行
        # lines.append()
        # lines.append()

        new_xtable = self._make_ybk_xtable(lines, items, [*xtable.tables, *new_tables])
        #new_xtable.last_xtable = xtable
        new_xtable.working_tables = working_tables
        return new_xtable

    def _make_ybk_xtable(
        self, lines: list[BBox], items: list[XItem[KCell]], tables: Sequence[KTable]
    ) -> XTable:
        from memect.base.utils import Timer
        from memect.pdf.default.table.line import Liner
        from memect.pdf.grid import Grid

        doc:Final = tables[0].doc
        
        timer = Timer.start()

        with timer.watch('liner'):
            #暂时使用这个类，实际上可以使用一个更加简便的
            new_lines = Liner().parse(lines)
        
        with timer.watch('grid'):
            grid = Grid(new_lines)
        
        with timer.watch('fill'):
            xtable = XTable.from_grid(doc,grid,items,tables=tables)

        #为了更好的显示合并状态
        xtable.apply_merged_info()
        # xtable.subtype = tables[0].subtype

        return xtable



class _TableAI:
    _lock:Final=threading.RLock()
    _prefix: Final = XPattern(
        "match",
        join=False,
        patterns=[
            # (1)
            r"[(（][0-9]+[)）]",
            # 1. or 1、
            r"[0-9]+[.．、]",
            # (一)
            r"[(（][一二三四五六七八九十]+[）)]",
            # 一、or 一
            r"[一二三四五六七八九十]+[、]?",
            r"(加|减)[:：]",
        ],
    )
    _suffix: Final = XPattern(
        "search",
        join=False,
        patterns=[
            r"[：:]$",
            # 把后面的单位去掉
            r"[（(]元|万元|%[)）]$",
        ],
    )

    def __init__(self):
        super().__init__()
        self._do_init()

    def _do_init(self):
        from memect.conf import get_conf_path
        data_dir = get_conf_path().joinpath('table_dicts')
        custom_dir = Path('./conf/table_dicts')
        patterns: list[str] = []
        texts: list[str] = []
        for dir_ in [custom_dir,data_dir]:
            for p in dir_.joinpath("patterns").glob("*.txt"):
                for line in p.read_text("utf-8").splitlines():
                    if line and line not in patterns:
                        patterns.append(line)
        for dir_ in [custom_dir,data_dir]:
            for p in dir_.joinpath("txts").glob("*.txt"):
                for line in p.read_text("utf-8").splitlines():
                    line = self._filter(line)
                    if line and line not in texts:
                        texts.append(line)
        self._pattern = XPattern(
            "fullmatch", join=False, patterns=patterns, texts=texts
        )

    def _filter(self, line: str) -> str:
        if not line:
            return line

        # 归一化，使用半角且去掉空格
        line = strs.NText.get(line, mode="q2b", space="remove").text
        # 去掉前缀，如：序号等
        m = self._prefix.match(line)
        if m:
            line = line[m.end() :]

        # 去掉单位等？
        m = self._suffix.search(line)
        if m:
            line = line[: m.start()]

        return line

    def is_phrase(self, line: str) -> bool:
        line = self._filter(line)
        if not line:
            return False
        return self._pattern.fullmatch(line) is not None

    _instance: ClassVar[Self | None] = None

    @classmethod
    def parse_phrases(
        cls, s1: str, s2: str, *, mode: str = "dict"
    ) -> tuple[bool, bool, bool]:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()

        a = cls._instance.is_phrase(s1)
        b = cls._instance.is_phrase(s2)
        c = cls._instance.is_phrase(s1 + s2)
        return (a, b, c)
