import logging
import re
from typing import Any, Final, Literal, Sequence, cast

from memect.base import lists

from memect.base.bbox import BBox
from memect.base.pattern import XPattern
from memect.base.debug import XDebugger
from memect.pdf.base import (
    Group,
    KBlock,
    KCell,
    KChar,
    KDocument,
    KFigure,
    KObject,
    KPage,
    KTable,
    KText,
)
from memect.pdf.sort import Sorter


class Part:
    """表示页面的一个区域，有标题和来源"""

    def __init__(
        self,
        page: KPage,
        bbox: BBox,
        type: str,
        *,
        titles: Sequence[KObject] | None = None,
        sources: Sequence[KObject] | None = None,
        objects: Sequence[KObject] | None = None,
    ):
        """可以为空，表示只有标题或者来源"""
        # title只有一个，但是可能还有其他文本，如：
        # [title]
        # [text]
        # [table|figure]
        # [source]
        self.page: Final = page
        self.bbox: Final = bbox
        self.type: Final = type
        self.titles: Final[Sequence[KObject]] = titles or ()
        self.sources: Final[Sequence[KObject]] = sources or ()
        self.objects: Final[Sequence[KObject]] = objects or ()

    def make_block(self) -> KBlock:
        objects = [*self.titles, *self.objects, *self.sources]
        Sorter.sort(objects)
        return KBlock(self.page, self.bbox, objects=objects)


class BlockParser:
    """为了分栏建立阅读顺序，先做一些常见的预处理，如：

    有些简单的情况并不需要分栏，但是，可能会有多个表格/图片并排，如：

    [title1] [title2]
    [table1] [table2]
    [source1][source2]

    对于这种，只需要先把[title1,table1,source1],[title2,table2,source2] 关联起来，就合理了
    后续也不需要分栏处理

    目的：
    1.为了阅读顺序需要，因为需要作为一个整体
    2.为了跨页/跨列合并需要，因为有些本身就是使用表格来布局的
    3.如果是title/figure/source（1-n个并排），可以使用表格表示，这样就可以支持title/figure/source，跨列或者跨页的情况
    4.为了避免子表格，如果是：title/table/source，在阅读顺序建立后，会再还原，如果合并为一个表格，就存在子表格，
      而且有些情况下，title可能为有层次的标题


    """

    _logger: logging.Logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")
    # 多语言如何处理？
    _title_pattern: Final = XPattern(
        "fullmatch",
        join=False,
        patterns=[
            r"(表|图|图表)[0-9]+.+",
            r"(表|图|图表)[一二三四五六七八九十]+.+",
            r"(表|图|图表)[:].+",

            #2017年我国地下水水质情况
            r'[0-9]{4}年.+情况',
        ],
    )
    _title_pattern2: Final = XPattern(
        "fullmatch",
        join=False,
        patterns=[
            # 常见标题，研报首页
            r"财务数据及盈利预测",
            r".*走势图",
            r".*涨跌图",
            r"主要财务数据",
            r"主要数据",
        ],
    )

    # 可能是普通的标题，不是表格标题
    _invalid_title_pattern: Final = XPattern(
        "fullmatch",
        join=False,
        patterns=[
            # 1.xxxx 或者 1、xxxx 或者 1 xxxxx
            r"[0-9]+[.、]?.+",
            # (1)xxxx 或者 1)
            r"[(]?[0-9]+[)].+",
            # 一xxx 或者 一、或者（一） 或者 （一、）
            r"[(]?[一二三四五六七八九十]+[、][)]?",
        ],
    )

    _unit_pattern: Final = XPattern(
        "fullmatch",
        join=False,
        patterns=[
            r"单位[:].+",
            r".*[(]单位[:].+[)]",
        ],
    )
    _source_pattern: Final = XPattern(
        "fullmatch",
        join=False,
        patterns=[
            r"数据来源[:]?.+",
            r"资料来源[:]?.+",
            r"来源[:]?.+",
            r"Source[:].+",
            r"备注[:].+",
            r"注[:].+",
        ],
    )

    _no_pattern: Final = XPattern(
        op="fullmatch",
        join=False,
        patterns=[
            # 1. or 1、
            r"(?P<no>[0-9]+)[.、].+",
            # ⑴-⒇
            r"(?P<no>[\u2474-\u2487]).+",
            # ①-⑳
            r"(?P<no>[\u2460-\u2473]).+",
        ],
    )

    def __init__(self):
        super().__init__()

    def parse(self, doc: KDocument):
        # 考虑到跨页/跨栏，需要多页处理
        for page in doc.working_pages:
            # 先按页处理
            self._parse_page(page)

        # 再处理跨页的情况，可能需要参考前后页
        for page in doc.working_pages:
            self._parse_page2(page)
            pass

    def parse2(self, doc: KDocument):
        """在分栏后，再次查找"""
        # 如：
        # [xxxxx]|[table]
        # [title]|
        # 或者
        # [xxxxx]|[source]
        # [table]|

        # 下面这两种在self._parse_page2()中已经处理了？
        # 或者
        # [xxxxx]|[title]
        # ----------------分页
        # [table]|[xxxxxx]
        # 或者
        # [xxxxxx]|[table]
        # ----------------分页
        # [source]|[xxx]

        def is_title(text: KText) -> bool:
            if self._title_pattern.fullmatch(text.text2) is not None:
                return True
            else:
                return False

        def is_source(text: Text) -> bool:
            # TODO 如果只是部分内容？
            if self._source_pattern.fullmatch(text.text2) is not None:
                return True
            else:
                return False

        def get_table(page: Page, index: int) -> Table | None:
            if len(page.body.objects) == 0:
                return None

            if index < 0:
                index += len(page.body.objects)

            if index >= len(page.body.objects):
                return None
            obj = page.body.objects[index]
            if not isinstance(obj, Table):
                return None
            # TODO 严格的可以多判断table.extras['part']，表示为来自part的表格
            return cast(Table[Any], obj)

        def find_table(
            page: Page, text: Text, dir: Literal["next", "prev"]
        ) -> Table | None:
            # TODO 严格的需要先判断页面大小是否一致？对于奇葩的表格，也可能不一致

            i = page.body.objects.index(text)
            if dir == "next":
                # 如果同一页还有对象
                if i + 1 < len(page.body.objects):
                    return get_table(page, i + 1)
                elif page.next_page is not None:
                    # 下一页的第一个
                    return get_table(page.next_page, 0)
                else:
                    return None
            else:
                # 前一个
                if i - 1 >= 0:
                    # 同一页
                    return get_table(page, i - 1)
                elif page.prev_page is not None:
                    # 上一页的最后一个
                    return get_table(page.prev_page, -1)
                else:
                    return None

        def handle_object(page: Page, index: int, objects: Sequence[TObject]):
            obj = objects[index]
            if not isinstance(obj, Text):
                return
            if is_title(obj):
                return handle_title(page, index, objects)
            elif is_source(obj):
                return handle_source(page, index, objects)
            else:
                return None

        def handle_title(page: Page, index: int, objects: Sequence[TObject]):
            title = cast(Text, objects[index])
            table = find_table(page, title, "next")
            if table is None or table.col_num != 1:
                # 严格的可以再判断该table是否已经有title了？
                # table.extras.get('title') => 临时使用的额外的设置
                return

            # print('=======>>',title.text,title.bbox,table.bbox)
            if (table.page == page and title.bbox.x1 <= table.bbox.x0) or (
                table.page != page and table.bbox.x1 < title.bbox.x0
            ):
                # 同页跨栏
                # [xxxxx]|[table]
                # [title]|[xxxxxx]
                # 跨页
                # [xxxxx]|[xxxxx]
                # [xxxxx]|[title]
                # -------------------跨页跨栏
                # [table]|[xxxx]
                part = Part(page, "title", titles=[title])
                page.body.replace([title], [self._make_table(page, [part])])

        def handle_source(page: Page, index: int, objects: Sequence[TObject]):
            source = cast(Text, objects[index])
            table = find_table(page, source, "prev")
            if table is None or table.col_num != 1:
                # 严格的可以再判断该table是否已经有title了？
                # table.extras.get('source') => 临时使用的额外的设置
                return

            if (table.page == page and table.bbox.x1 <= source.bbox.x0) or (
                table.page != page and source.bbox.x1 < table.bbox.x0
            ):
                # 同页跨栏
                # [xxxxx]|[source]
                # [table]|[xxxxxx]
                # 跨页
                # [xxxxx]|[xxxxx]
                # [xxxxx]|[table]
                # -------------------跨页跨栏
                # [source]|[xxxx]
                part = Part(page, "source", sources=[source])
                page.body.replace([source], [self._make_table(page, [part])])

        def parse_page(page: Page):
            debugger: Final = self._debugger.bind(page=page.number)
            objects = tuple(page.body.objects)
            for i in range(len(objects)):
                handle_object(page, i, objects)
            # page.body.sort()
            if debugger.allow("gui"):
                page.show("final body", objects=page.body.objects)

        for page in doc.pages:
            parse_page(page)

    def _parse_page(self, page: KPage):
        """做一些简单的预处理，方便更好的建立阅读顺序，如：表格标题和表格关联，图片标题和图片关联"""
        """
        为了方便分栏和阅读，先把小范围内的对象关联起来为一个整体，因为作者在设计的时候，这些内容就是一个整体的。
        常见的形式有：
        [title]
        [figure/table/chart/flowchart/diagram]
        [source]

        而且存在跨页，跨列的情况。

        解析的原则如下：
        先合并为一个part，如果有>=2个并排，使用表格表示，如下：
        [title]         [title]       => 可能只有一个标题，或者没有，可能还有单位或者其他文本
        [figure/table]  [figure/table] => 可能包含有表格           
        [source]        [source]      =>可能只有一个来源

        如果只有一个，合并为一个part，暂时不使用表格，因为可能为跨页/跨列表格，这样就需要子表格，增加了提取的难度
        [tite]
        [figure]
        [source]
        """
        debugger: Final = self._debugger.bind(page=page.number)

        # 会删除用掉的对象，所以需要复制一个
        raw_objects = tuple(page.objects)
        remain_objects = list(page.objects)
        remain_objects.sort(key=lambda obj: obj.bbox.y1, reverse=True)
        parts: list[Part] = []
        for obj in remain_objects[:]:
            if obj not in remain_objects:
                # 被用掉了
                continue
            if isinstance(obj, KFigure | KTable):
                self._handle_object(obj, remain_objects, parts)
        # 会删除用掉的objs和parts
        tables = self._handle_parts(page, parts, remain_objects)
        blocks: list[KBlock] = []
        for part in parts:
            blocks.append(part.make_block())
        page.objects.clear()
        page.objects.extend(remain_objects)
        page.objects.extend(tables)
        page.objects.extend(blocks)

        if debugger.allow("draw"):
            page.draw(
                ("page", None),
                (f"objects={len(raw_objects)}", raw_objects),
                (f"remain_objects={len(remain_objects)}", remain_objects),
                (f"tables={len(tables)}", tables),
                (f"blocks={len(blocks)}", blocks),
                (f"objects={len(page.objects)}", page.objects),
                show_type=True,
                dir="debug/default/block",
            )

        return remain_objects

    def _parse_page2(self, page: KPage):

        # ----------------
        # 或者顶部，找到
        # [source] [source]  =>需要理解为表格

        # 如果有分栏，问题又来了
        # [text]   | [table1]    =>title1和table1应该关联
        # [title1] | [title2]   =>title1和title2不需要关联
        # ---------------------
        # [table2] |[source3]
        # [text]   |
        # [title3] |
        # [table3] |

        debugger: Final = self._debugger.bind(page=page.number)

        def is_title(text: KText) -> bool:
            if self._title_pattern.fullmatch(text.text2) is not None:
                return True
            else:
                return False

        def is_source(text: KText) -> bool:
            # TODO 如果只是部分内容？
            if self._source_pattern.fullmatch(text.text2) is not None:
                return True
            else:
                return False


        def get_table(page:KPage,index:int)->KTable|None:
            objects = sorted(page.objects,key=lambda obj:obj.bbox.y1,reverse=True)
            if not objects:
                return None
            obj = objects[index]
            if isinstance(obj,KTable):
                #TODO 还需要判断是否为图表布局表格
                return obj
            else:
                return None
        def find_table(page: KPage, dir: Literal["next", "prev"]) -> KTable | None:
            # TODO 严格的需要先判断页面大小是否一致？对于奇葩的表格，也可能不一致
            next_page = page.doc.get_working_page(page.number+1)
            prev_page = page.doc.get_working_page(page.number-1)
            
            if dir == "next" and next_page is not None and abs(page.width-next_page.width)<=2:
                # TODO 可以判断是否为title/figure/source表格
                return get_table(next_page,0)
            elif dir == "prev" and prev_page is not None and abs(page.width-prev_page.width)<=2:
                return get_table(prev_page,-1)
            else:
                return None

        def find_titles(page: KPage, strict: bool = True):
            # 如果没有分栏，如下查找是正确的
            # 在页面的底部，找到如下：没有图片的
            # [title][title]     =>需要理解为表格
            # ------------------分页
            # [figure][figure]

            # 跨页的情况
            # [xxxxx]|[figure]
            # [title]|

            lines = Sorter.get_lines(page.objects)
            if len(lines) < 1:
                return

            # 取最后一行，且应该为Text
            line = lines[-1]
            if not all(isinstance(obj, KText) for obj in line):
                return

            line = cast(list[KText], line)
            parts: list[Part] = []
            for text in line:
                # 判断是否为标题
                if is_title(text):
                    part = Part(page,text.bbox, "title", titles=[text])
                    parts.append(part)
                else:
                    break

            if len(parts) != len(line):
                return

            next_table = find_table(page, "next")
            if strict:
                # 如果没有下一个表格，都不作为表格
                if next_table is None:
                    return

                # 如果有下一个表格，但是可能因为line被识别为一个Text，尝试修正
                # 这是因为layout的对象识别无法100%
                if next_table.col_num == 2 and len(parts) == 1 and len(line) == 1:
                    # 可能原文为
                    # 图1xxxxx  图2xxxxx =>['图1xxxx  图2xxxxx']
                    # 需要修正为
                    # =>['图1xxxx','图2xxxxxx']
                    text = line[0]
                    # 找到最大间距的对象，切开为2个
                    # 或者根据模式找到n个标题？
                    i = get_max_distance_index(text)
                    if i > 0:
                        t1=KText.from_objects(text.objects[0:i])
                        t2=KText.from_objects(text.objects[i:])
                        assert t1 is not None
                        assert t2 is not None
                        if is_title(t1) and is_title(t2):
                            self._logger.warning(
                                "cut title,page=%s,title1=%s,title2=%s",
                                page.number,
                                t1.text,
                                t2.text,
                            )
                            parts = [
                                Part(page,t1.bbox, "title", titles=[t1]),
                                Part(page,t2.bbox, "title", titles=[t2]),
                            ]

                if next_table.col_num != len(parts):
                    return

            # json2doc/report/13.pdf 5-6页,45-46 单列
            # json2doc/report/13.pdf 4-5页，7-8,36-38,42-43 双列
            # 如果有2个标题的，还需要判断下一页吗？
            # 可以根据这2个对齐？暂时不需要了，跨页的时候再处理？
            table = self._make_table(page, parts)
            if next_table is not None and next_table.bbox.over('x',table.bbox,d=20):
                #TODO 需要考虑在没有分栏的情况下，所以限制为2
                x0=min(table.bbox.x0,next_table.bbox.x0)
                x1=max(table.bbox.x1,next_table.bbox.x1)
                table.adjust(x0=x0,x1=x1)
                next_table.adjust(x0=x0,x1=x1)

            k=page.objects.index(line[0])
            page.objects.insert(k,table)
            lists.remove(page.objects,line)
            
            if debugger.allow("gui"):
                pass

        def find_sources(page: KPage, strict: bool = True):
            lines = Sorter.get_lines(page.objects)
            if len(lines) < 1:
                return

            line = lines[0]
            if not all(isinstance(t, KText) for t in line):
                return

            parts: list[Part] = []
            for text in line:
                if is_source(text):
                    part = Part(page,text.bbox, "source", sources=[text])
                    parts.append(part)
                else:
                    break

            if len(parts) != len(line):
                return

            prev_table = find_table(page, "prev")
            if strict and (prev_table is None or prev_table.col_num != len(parts)):
                # 如果没有下一个表格，都不作为表格
                return

            # json2doc/report/13.pdf 27-28
            table = self._make_table(page, parts)
            if prev_table is not None and prev_table.bbox.over('x',table.bbox,d=20):
                #TODO 需要考虑在没有分栏的情况下，所以限制为2
                x0=min(table.bbox.x0,prev_table.bbox.x0)
                x1=max(table.bbox.x1,prev_table.bbox.x1)
                table.adjust(x0=x0,x1=x1)
                prev_table.adjust(x0=x0,x1=x1)

            k = page.objects.index(line[0])
            page.objects.insert(k,table)
            lists.remove(page.objects,line)

            if debugger.allow("gui"):
                pass

        def get_max_distance_index(text: KText) -> int:
            values: list[tuple[int, float]] = []
            for i in range(1, len(text.objects)):
                obj1 = text.objects[i - 1]
                obj2 = text.objects[i]
                d = obj2.bbox.x0 - obj1.bbox.x1
                values.append((i, d))
            if len(values) == 0:
                return -1

            i, v = max(values, key=lambda a: a[1])
            if v >= 10:
                return i
            else:
                return -1

        find_titles(page)
        find_sources(page)
        # page.body.sort()
        if debugger.allow("draw"):
            # page.show('final body',objects=page.body.objects)
            pass

    def _find_texts(
        self,
        main_bbox: BBox,
        objs: Sequence[KObject],
        types: Literal["title", "source", "all"] = "all",
    ) -> dict[str, Any]:
        titles: list[KObject] = []
        units: list[KObject] = []
        sources: list[KObject] = []

        # 先排序，因为是按顺序的
        objs = sorted(objs, key=lambda obj: obj.bbox.y1, reverse=True)
        checked_objs: list[KObject] = []
        for obj in objs:
            if not isinstance(obj, KText):
                continue
            # [title][unit]
            #    [unit]
            # [main]
            # [source]
            bbox = main_bbox.union_all(sources)
            if bbox.y0 - obj.bbox.y1 >= 50:
                # [main]
                #
                # [obj] => 距离很远了，就不需要再继续下去了，虽然可能还有
                break

            # TODO 有些单位和标题在一起
            checked_objs.append(obj)
            # print('====>>',obj.text,obj.bbox,bbox,is_title(obj,bbox))
            if types in ("all", "title") and self._is_title(obj, bbox):
                titles.append(obj)
            elif types in ("all", "title") and self._is_unit(obj, bbox):
                units.append(obj)
            elif types in ("all", "source") and self._is_source(obj, bbox, sources):
                sources.append(obj)
            else:
                pass

        # TODO 下面的只是极少的情况，也就是需要先获得sources，再回来判断titles
        if types in ("all", "title") and len(titles) == 0 and len(sources) == 1:
            # 如果没有标题，但是有来源，可以再查找一次标题，因为可能如下
            # [title]   =>表格和figure没有水平相交，开始的时候就被忽略了
            #          [figure]
            # [source------------]
            k = checked_objs.index(sources[0])
            title = checked_objs[k - 1]
            if (
                k - 1 >= 0
                and isinstance(title, KText)
                and self._is_title(title, main_bbox.union_all(sources))
            ):
                self._logger.warning(
                    "first find source,second find title,page=%s,title=%s",
                    title.page.number,
                    title.text,
                )
                titles.append(title)

        # 把单位也放在标题中
        if titles:
            titles.extend(units)
        else:
            # 没有标题，仅仅有单位，忽略单位？
            # 两种可能
            # [单位]
            # [表格] => 如果被错误的识别为图片，会使用表格表示，这个时候有单位就不合适了
            #      => 被识别为表格，也不影响
            # ----------
            # [单位]
            # [图片] => 为真正的图片，
            pass
        return {
            "titles": titles,
            #'units':units,
            "sources": sources,
        }

    def _handle_object(
        self, main: KObject, working_objects: list[KObject], parts: list[Part]
    ):
        """
        main: 图片或者表格
        working_objects: 会删除用掉的对象
        """
        texts = self._find_texts(main.bbox, working_objects)
        titles = texts["titles"]
        sources = texts["sources"]

        def make(
            used_objects: Sequence[KObject],
            titles: Sequence[KObject] | None = None,
            sources: Sequence[KObject] | None = None,
        ) -> bool:
            # 测试title和source
            # 获得一个新的区域，判断是否仅仅包含这些对象，且和其他对象没有相交
            if not used_objects:
                return False
            bbox = main.bbox.union_all(used_objects)
            other_objs = working_objects[:]
            other_objs.remove(main)
            lists.remove(other_objs, used_objects, use_is=True)
            # TODO 已经去掉了多余的水平线？
            if not bbox.intersect_any(other_objs + parts, ratio=0.2):
                if isinstance(main, KTable):
                    type_ = "table"
                else:
                    # figure/chart/flowchart/diagram
                    type_ = "figure"
                # TODO 对象识别的截图可能不一定很准确，可以根据
                # [title]
                # --------------h_line
                # [figure]
                # -------------h_line      =>可以根据这2条水平线，再调整一下figure，这样可以更加准确一些
                # [source]
                part = Part(
                    main.page,
                    bbox,
                    type_,
                    titles=titles,
                    objects=[main],
                    sources=sources,
                )
                lists.remove(working_objects, [main, *used_objects])
                parts.append(part)
                return True
            else:
                # print('===>has other objs',other_objs)
                return False

        if (
            titles
            and sources
            and make([*titles, *sources], titles=titles, sources=sources)
        ):
            # title和source一起
            return

        if titles and make(titles, titles=titles, sources=None):
            # titles，sources可能共享
            return

        if sources and make(sources, titles=None, sources=sources):
            # sources，titles可能共享
            return

    def _handle_parts(
        self, page: KPage, parts: list[Part], objects: list[KObject]
    ) -> list[KTable]:
        """
        对parts再进行处理，如果能够理解为表格的，转换为表格
        parts:会删除用掉的part
        objects:会删除用掉的对象
        """
        debugger: Final = self._debugger.bind(page=page.number)

        if page.number == 1:
            # 研报首页不处理？或者说先分栏，再合并
            # local/cases/json2doc/report/5.pdf 第1页，3个并排
            # 如果后续考虑了左右分栏等，就可以去掉这个判断了
            return []

        # 信托计划等也不需要，因为可能就是章节或者段落的标题，如：
        # 1.xxxx情况   =>这些需要为标题，而不是表格的标题（caption）
        # [table]
        # 2.xxxx情况   =>
        # [table]

        lines: list[list[Part]] = Sorter.get_lines(parts)
        # json2doc/report/6.pdf 11页等
        tables: list[KTable] = []

        def is_continue(i:int,j:int)->bool:
            if i+1==j:
                return True
            elif j-i<=0:
                return False
            else:
                chars = page.pdf_chars[i+1:j]
                #print('chars=',[c.text for c in chars])
                if all(c.text.isspace() for c in chars):
                    return True
                else:
                    return False
                
        def get_below_object(part:Part):
            for obj in sorted(objects,key=lambda obj:obj.bbox.y1,reverse=True):
                if obj.bbox.y1<=min(part.bbox.y0+10,part.bbox.y1) and obj.bbox.over('x',part.bbox,d=20):
                    #[-part-]|[-part-]
                    #[-obj-] |[-obj-]
                    return obj
            return None
        
        def get_last_char(part:Part)->KChar|None:
            block = part.make_block()
            if block.objects and isinstance(block.objects[-1],KText):
                text = block.objects[-1]
                if text.objects and isinstance(text.objects[-1],KChar):
                    return text.objects[-1]
            return None
        
        def case1(line:list[Part])->bool:
            #local/cases/json2doc/layout/footnote/3-1.pdf 第14页
            #复杂的情况，存在分栏，情况就复杂了，可能为
            #text1|text2
            #part1|part2 =>为一个整体
            #或者
            #text1|text2
            #part1|part2  => [text1,part1] -> [text2,part2]

            #如何判断？
            #text1|text3
            #part1|part2
            #text2|text4   => 如果有text2且text2后到text3，part1和part2就是分开的，对于这种，目前仅仅支持pdf解析
            #              => 如果有text2且text2后到text4，part1和part2就是合并的

            if len(line)!=2:
                return False
            
            part1 = line[0]
            part2 = line[1]
            obj1 = get_below_object(part1)
            obj2 = get_below_object(part2)
            if obj1 is obj2:
                #[part1]|[part2]  =>可以合并
                #---obj1--------
                return False
            
            if isinstance(obj1,KText) and isinstance(obj2,KText) and len(obj1.objects)>0 and len(obj2.objects)>0: 
                #[obj1]|[obj2]
                #简单的判断书写顺序
                c1=obj1.objects[0]
                c2=obj2.objects[0]

                if isinstance(c1,KChar) and isinstance(c2,KChar):
                    k1 = c1.index
                    k2 = c2.index
                    
                    if k1!=-1 and k2!=-1:
                        c3 = get_last_char(part1)
                        c4 = get_last_char(part2)
                        k3 = c3.index if c3 else -1
                        k4 = c4.index if c4 else -1
                        
                        if k3!=-1 and k4!=-1 and is_continue(k3,k1) and is_continue(k4,k2):
                            parse_lines([[part] for part in line])
                            return True
            
            return False
            
            

        def parse_lines(lines: list[list[Part]]):
            # 如果是左右分栏，可以在这里就把part给切开了
            # 可以使用获得的初步分栏信息避免分栏的问题
            # 可能为上下左右，获得左右block，然后在左右block内的part，才合并？
            # 这样就不需要跳过第一页了
            # page.get_vobjects('column',types=['block'],scores=0.5)
            for line in lines:
                # TODO 可能不一定全部都是part，可能就是n个并排的图片/表格/block，需要区别如下情况
                # 下面这种为左右分栏更合适
                # [text1]|[text3]  =>前面的文本也刚好分栏
                # [part1]|[part2]  =>这两个刚好并排,需要很难才能够理解，可能需要合并在一起，也可能不需要
                # [text2]|[text4]
                # 列外：local/cases/json2doc/report/5.pdf 第1页，就出现3个并排
                bbox = BBox.join2(line)
                if len(line) == 1 and line[0].type == "table":
                    # 如果是这种，保留为Part即可，如果使用表格，就需要子表格，增加了后续提取的复杂度，所以就分开不作为表格
                    # [title]
                    # [table]
                    # [source]
                    continue

                if case1(line):
                    continue

                    # 如果是图表，单个或者多个，或者图+表格一边一个，都合并在一起，而不是分开
                if not bbox.intersect_any(objects, ratio=0.2):
                    # 如果当前line没有和其他对象相交
                    # 严格的还根据title，判断序号？
                    # 合并为一个表格，如果都没有title，需要判断是否有共同的header
                    titles: list[KObject] = []
                    sources: list[KObject] = []
                    common_titles: list[KObject] = []
                    common_sources: list[KObject] = []
                    for part in line:
                        titles.extend(part.titles)
                        sources.extend(part.sources)

                    if len(line) > 1:
                        if not titles:
                            # 判断是否有共同的标题
                            # json2doc/report/11.pdf 第5页
                            common_titles = self._find_texts(
                                bbox, objects, types="title"
                            )["titles"]

                        if not sources:
                            # 判断是否有共同的来源
                            # json2doc/report/10.pdf 第12页等
                            common_sources = self._find_texts(
                                bbox, objects, types="source"
                            )["sources"]

                    # TODO 如果该part的标题也是章节树中的标题之一，就需要分开了，如：
                    # [1.2.1 xxxx图表]
                    # [--chart-----]
                    # [source:xxxxx]

                    # TODO 严格的做法需要先判断行是否对齐，否则遇到不对齐的就解析不合理了
                    # [title1][title2]      => 需要对齐在一行，
                    # [figure1][figure2]    => 对齐在一行
                    # [source1][source2]    => 对齐在一行
                    # 如果没有对齐，每个part都作为一个独立的表格


                    table = self._make_table(
                        page,
                        line,
                        common_titles=common_titles,
                        common_sources=common_sources,
                    )
                    tables.append(table)
                    lists.remove(parts, line)
                    lists.remove(objects, common_titles, common_sources)

                elif len(line) > 1:
                    # 特殊的情况，part2很大，跨了3个，这个时候，line=[part1,part2]
                    # [part1] [part2]
                    # [part3] [part2]
                    # [part4] [part2]
                    parse_lines([[part] for part in line])
                else:
                    # TODO 只有一个，不处理？就留到解析的最后了
                    pass

        parse_lines(lines)
        return tables

    def _make_table(
        self,
        page: KPage,
        parts: list[Part],
        common_titles: Sequence[KObject] = (),
        common_sources: Sequence[KObject] = (),
    ):

        # 有如下几种
        # 第一种，标准格式
        # [title]    [title]
        # [body]     [body]
        # [source]   [source]
        # 第二种
        # [title]   [title]
        # [body]    [body]
        # ------------------- 跨页
        # [source]  [source]
        # 第三种
        # [title] [title]
        # -------------------- 跨页
        # [body]  [body]
        # [source] [source]
        # 第四种
        # [title]                =>自动变成跨n列
        # [body]    [body]       =>共用title和source，但是title和source并不跨列
        # [source]               =>自动变成跨n列
        # 第五种
        # [-----title----]  只有一个标题且跨列，需要单独提供titles
        # [body][body]
        # [---source-----]  只有一个来源且跨列，需要单独提供sources
        # json2doc/report/10.pdf 23页

        debugger: Final = self._debugger.bind(page=page.number)

        # 如果titles只有1个，那么就是
        table_bbox = BBox.join2([*parts, *common_titles, *common_sources])

        table_bbox = table_bbox.expand(dx=2, bound=page.bbox)

        row_num: int = 0
        col_num: int = len(parts)

        titles: Group[KObject] = Group()
        sources: Group[KObject] = Group()
        objects: Group[KObject] = Group()

        # 表示有多少个cell有自己的标题
        title_num: int = 0
        source_num: int = 0
        object_num: int = 0
        for part in parts:
            if part.titles:
                title_num += 1
            if part.sources:
                source_num += 1
            if part.objects:
                object_num += 1
            titles.extend(part.titles)
            sources.extend(part.sources)
            objects.extend(part.objects)

        titles.invalidate()
        sources.invalidate()
        objects.invalidate()

        if common_titles:
            row_num += 1
        if common_sources:
            row_num += 1

        if titles:
            # 如果有common_titles，不应该有这些了，但是现在也支持有，万一有更复杂的情况？
            row_num += 1
        if sources:
            # 如果有common_sources，不应该有这些了，但是现在也支持有，万一有更复杂的情况？
            row_num += 1
        if objects:
            row_num += 1

        title_row: list[KCell] = []
        object_row: list[KCell] = []
        source_row: list[KCell] = []
        cells: list[KCell] = []
        common_title_cell: KCell | None = None
        common_source_cell: KCell | None = None
        if common_titles:
            b = BBox.join2(common_titles)
            common_title_cell = KCell(
                page,
                b,
                row_index=0,
                col_index=0,
                col_span=col_num,
                objects=common_titles,
            )
        if common_sources:
            b = BBox.join2(common_sources)
            common_source_cell = KCell(
                page,
                b,
                row_index=row_num - 1,
                col_index=0,
                col_span=col_num,
                objects=common_sources,
            )

        for col_index, part in enumerate(parts):
            row_index = 1 if common_titles else 0
            assert part.bbox is not None
            if titles:
                # 有各自的标题
                # 严格的方式，这里不应该使用part.bbox，而是part.titles.bbox，因为这样，即使标题跨列，也不影响objects的计算，后面会调整
                if part.titles:
                    b2 = BBox.join2(part.titles)
                else:
                    b2 = part.bbox
                b = titles.bbox.adjust(x0=b2.x0, x1=b2.x1)
                title_row.append(
                    KCell(
                        page,
                        b,
                        row_index=row_index,
                        col_index=col_index,
                        col_span=1,
                        objects=part.titles,
                    )
                )
                row_index += 1
            else:
                # 没有标题
                pass

            if objects:
                if part.objects:
                    b2 = BBox.join2(part.objects)
                else:
                    b2 = part.bbox
                b = objects.bbox.adjust(x0=b2.x0, x1=b2.x1)
                object_row.append(
                    KCell(
                        page,
                        b,
                        row_index=row_index,
                        col_index=col_index,
                        col_span=1,
                        objects=part.objects,
                    )
                )
                row_index += 1

            if sources:
                if part.sources:
                    b2 = BBox.join2(part.sources)
                else:
                    b2 = part.bbox
                b = sources.bbox.adjust(x0=b2.x0, x1=b2.x1)
                source_row.append(
                    KCell(
                        page,
                        b,
                        row_index=row_index,
                        col_index=col_index,
                        col_span=1,
                        objects=part.sources,
                    )
                )
                row_index += 1

        # 对于titles只有的，设置为跨列吗？
        strict: bool = True
        if strict:

            def adjust_row(row: list[KCell]):
                i = 0
                while i < len(row):
                    cell = row[i]
                    if cell.objects:
                        # 只有一个有objects
                        # cell.col_index=0
                        # cell.col_span=col_num
                        # row[i]=cell.copy(col_index=0,col_span=col_num)
                        cell.col_index = 0
                        cell.col_span = col_num
                        i += 1
                    else:
                        del row[i]

                # 必须为1
                assert len(row) == 1

            if title_num == 1:
                adjust_row(title_row)
            if source_num == 1:
                adjust_row(source_row)

        if common_title_cell is not None:
            cells.append(common_title_cell)
        cells.extend(title_row)
        cells.extend(object_row)
        cells.extend(source_row)
        if common_source_cell is not None:
            cells.append(common_source_cell)
        table = KTable(page, table_bbox,cells=cells,subtype='wbk')
        if debugger.allow("info"):
            with debugger.group("cells"):
                for c in table.cells:
                    debugger.console.print(
                        (c.row_index, c.col_index, c.row_span, c.col_span)
                    )

        # [title1]        [title2]
        # [table1/figure1][table2/figure2]  =>table1和table2都是子表格，然后使用图片表示
        # TODO 如果有子表格，有3种解决方案
        # 1. 子表格作为图片
        # 2. 解析子表格
        # 3. 不要使用一个无边框表格表示n个part，part单独处理
        # 目前选择方案1
        for cell in table.cells:
            # 实际上只应该有一个对象，但是目前放宽了，只有包含一个table
            if len(cell.objects)==1 and isinstance(cell.objects[0],KTable) and cell.objects[0].row_num==1 and cell.objects[0].col_num==1:
                #可能为一个正常的子表格
                #也可能是一个柱状图等，但是被错误的识别为表格了
                #local/cases/test/layout-1.pdf --pages 12
                figure = page.make_figure(cell.objects[0].quad)
                assert figure is not None
                cell.objects.clear()
                cell.objects.append(figure)
                self._logger.warning('第%s页，把表格转换为图片',page.number)
            pass

        table.adjust()

        return table

    def _is_title(self, text: KText, bbox: BBox) -> bool:
        """判断是否为标题"""
        # 判断规则
        # 1.位置关系
        # 2.正则表达式
        # 3.模糊匹配，支持错别字等，rapidfuzz

        # TODO 有些图片可能又部分和标题重叠，如：
        # --title--
        # --figure-
        if bbox.height > 40:
            dy = -6
        else:
            dy = -2
        if not (text.bbox.over("x", bbox, d=20) and dy <= text.bbox.y0 - bbox.y1 <= 50):
            return False

        if self._title_pattern.fullmatch(text.text2):
            # 这个置信度最高，在这个之上的都可以忽略？
            return True
        elif (
            len(text.lines) == 1
            and self._title_pattern2.fullmatch(text.text2)
            and text.page.number == 1
        ):
            # 限制为1行？或者最多2行？
            # 必须为第一页？其他页可能包含了序号，正常的标题
            return True
        elif self._invalid_title_pattern.fullmatch(text.text2):
            return False
        #elif len(text.lines) == 1 and text.is_bold():
            # 如果是粗体的，也认为是？
            #return True
        else:
            return False

    def _is_unit(self, text: KText, bbox: BBox) -> bool:
        if not (text.bbox.over("x", bbox, d=20) and -2 <= text.bbox.y0 - bbox.y1 <= 30):
            return False
        if self._unit_pattern.fullmatch(text.text2):
            return True
        else:
            return False

    def _is_source(
        self, text: KText, bbox: BBox, sources: Sequence[KObject]
    ) -> bool:
        # 可能有多个source（也就是备注）
        # 第一种
        # 数据来源：xxxx
        # ------------
        # 第二种：
        # 注:xxx
        # 数据来源:xxx
        # ---------
        # 第三种
        # 注:xxx  数据来源
        # -------
        # 第四种
        # 数据来源：xxxx   注：xxx

        def has_no(text: KText) -> bool:
            s = text.text2
            m = re.match(r"注[:]?(?P<text>.+)", s)
            if m is not None:
                s = m.group("text")
            if self._no_pattern.fullmatch(s) is not None:
                return True
            else:
                return False

        if sources:
            bbox = bbox.union(BBox.join2(sources))
        if not (text.bbox.over("x", bbox, d=20) and -2 <= bbox.y0 - text.bbox.y1 <= 50):
            return False

        if self._source_pattern.fullmatch(text.text2):
            return True
        elif (
            len(sources) > 0
            and sources[-1].bbox.y0 - text.bbox.y1 <= 10
            and isinstance(sources[0], KText)
            and has_no(sources[0])
            and has_no(text)
        ):
            # 如果都是有序号，如：
            # 注：1.xxxx
            # 2.xxxx
            return True
        else:
            return False



