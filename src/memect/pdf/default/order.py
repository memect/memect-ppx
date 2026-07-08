import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Final, Iterator, Protocol, Self, Sequence

from memect.base import lists
from memect.base.bbox import BBox
from memect.base.debug import XDebugger
from memect.pdf.base import (
    Group,
    KBlock,
    KCell,
    KDocument,
    KFigure,
    KObject,
    KPage,
    KTable,
    KText,
)
from memect.pdf.sort import Sorter


class Item(Protocol):
    @property
    def bbox(self) -> BBox: ...


class ReadingOrder:
    _debugger=XDebugger(f'{__module__}.{__qualname__}')
    def __init__(self):
        super().__init__()

    def parse(self,doc:KDocument,max_workers:int=0):
        self._do(self._parse_page, doc.working_pages, max_workers=max_workers)
        _TableLayout().parse(doc)

    def _parse_page(self,page:KPage):
        #TODO 如果是多页的，还需要考虑表格布局的情况，跨几页
        def expand_objects(objs:Sequence[KObject])->Iterator[KObject]:
            for obj in objs:
                if isinstance(obj,KBlock):
                    yield from expand_objects(obj.objects)
                else:
                    yield obj

        debugger = self._debugger.bind(page=page.number)
        raw_objects = list(page.objects)
        method:str='towcolumn'
        columns = _TowCut().sort(page)
        if len(columns)==1:
            columns = _XYCut().sort(page.objects)
            method='xycut'
        column_bboxes:list[BBox]=[]
        page.objects.clear()
        for column in columns:
            bbox = BBox.join2(column)
            column_bboxes.append(bbox)
            #TODO 需要在这里就展开了吗？应该在章节树解析后
            page.objects.extend(expand_objects(column))

        page.set_blocks(column_bboxes)
        if debugger.allow('draw'):
            page.draw(
                ('page',None),
                (f'columns={method},{len(column_bboxes)}',column_bboxes,'number'),
                ('sections',page.sections,'number'),
                (f'raw_objects={len(raw_objects)}',raw_objects,'number'),
                (f'objects={len(page.objects)}',page.objects,'number'),
                show_type=False,
                dir='debug/default/order',
            )


    

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
class _XYCut:
    def __init__(self):
        super().__init__()

    def sort[T: Item](self, bboxes: Sequence[T]) -> list[Sequence[T]]:
        if not bboxes:
            return []
        return self._regroup([b for a in self._cut(bboxes) for b in a])

    def _regroup[T: Item](self, items: list[T]) -> list[Sequence[T]]:
        if not items:
            return []
        done: list[Sequence[T]] = []
        cur: list[T] = [items[0]]
        cur_bbox = items[0].bbox
        for i, o in enumerate(items[1:], 1):
            candidate = cur_bbox.union(o.bbox)
            #这里为除了当前的
            #remaining = [x.bbox for x in items[i + 1:]]
            other = list(items)
            lists.remove(other,cur,[o],use_is=True)
            remaining = [x.bbox for x in other]
            if o.bbox.y1<=cur_bbox.y0 and all(candidate.intersect(b) is None for b in remaining):
                cur.append(o)
                cur_bbox = candidate
            else:
                done.append(cur)
                cur = [o]
                cur_bbox = o.bbox
        done.append(cur)
        return done

    def _cut[T: Item](self, bboxes: Sequence[T]) -> list[Sequence[T]]:
        if len(bboxes) <= 1:
            return [bboxes]

        sorted_y = sorted(bboxes, key=lambda o: o.bbox.y1, reverse=True)
        y_gap = self._find_gap(sorted_y, axis='y')

        sorted_x = sorted(bboxes, key=lambda o: o.bbox.x0)
        x_gap = self._find_gap(sorted_x, axis='x')

        if x_gap is not None:
            left = [o for o in bboxes if o.bbox.x1 <= x_gap]
            right = [o for o in bboxes if o.bbox.x0 >= x_gap]
            mixed = [o for o in bboxes if o not in left and o not in right]
            if not mixed:
                # 纯双栏，列优先：先左列全部再右列全部
                return self._cut(left) + self._cut(right)

        if y_gap is not None:
            above = [o for o in bboxes if o.bbox.y0 >= y_gap]
            below = [o for o in bboxes if o.bbox.y1 <= y_gap]
            mixed = [o for o in bboxes if o not in above and o not in below]
            above += mixed
            return self._cut(above) + self._cut(below)

        if x_gap is not None:
            left = [o for o in bboxes if o.bbox.x1 <= x_gap]
            right = [o for o in bboxes if o.bbox.x0 >= x_gap]
            mixed = [o for o in bboxes if o not in left and o not in right]
            return self._cut(left) + self._cut(right) + (self._cut(mixed) if mixed else [])

        return [bboxes]

        return [bboxes]

    def _find_gap[T: Item](self, sorted_objs: Sequence[T], axis: str) -> float | None:
        if axis == 'y':
            min_start = sorted_objs[0].bbox.y0
            for o in sorted_objs[1:]:
                if o.bbox.y1 <= min_start:
                    return (o.bbox.y1 + min_start) / 2
                min_start = min(min_start, o.bbox.y0)
        else:
            max_end = sorted_objs[0].bbox.x1
            for o in sorted_objs[1:]:
                if o.bbox.x0 >= max_end:
                    return (o.bbox.x0 + max_end) / 2
                max_end = max(max_end, o.bbox.x1)
        return None


class Column:
    """表示一个分栏"""
    def __init__(self,page:KPage,bbox:BBox,index:int,columns:Sequence[Self]):
        super().__init__()
        self.page:Final=page
        self.bbox:Final=bbox
        self.index:Final=index
        self.columns:Final=columns
    
    @property
    def next(self)->Self|None:
        if self.index+1<len(self.columns):
            return self.columns[self.index+1]
        else:
            return None

    @property
    def prev(self)->Self|None:
        if self.index-1>=0:
            return self.columns[self.index-1]
        else:
            return None

class _TowCut:
    """双栏"""
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger = XDebugger(f'{__module__}.{__qualname__}')
    def __init__(self):
        super().__init__()
    
    def sort(self,page:KPage):
        #典型的页面通常是1栏，有些为2栏，3栏的很少
        #如果是2栏的，可能会插入一个分栏符，也就是如下
        #[col1][col2]
        #-------------分栏符
        #[--table--]   
        #------------------
        #[col3][col4]
        #--------------分页符
        #TODO 研报的首页更加复杂，页面有不少无序的文字（没有什么逻辑性）

        #debugger = self._debugger.bind(page=page.number)

        def cut(column:Group[KObject],ref_obj:KObject)->tuple[Group[KObject],Group[KObject]]:
            #[obj1]
            #[obj2]
            #[-----ref_obj-----]  被这个对象切开了
            #[obj3]
            #[obj4]
            #column已经按y0排序了
            end=len(column)
            for i in range(len(column)):
                obj = column[i]
                if obj.bbox.y0-ref_obj.bbox.y1>=-5:
                    #允许一点重叠
                    end=i
                    break
            
            top = column[end:]
            bottom = column[0:end]
            return Group[KObject](top),Group[KObject](bottom)

        def split_columns(objs:Sequence[KObject])->tuple[list[Group[KObject]],list[KObject]]:
            """快速的左右分栏"""
            #如何处理特别的情况，如：
            #下面这些不一定属于左右分栏，就是有些文本随意放置
            #    [title]
            #           | [span1]
            #   [span2] |
            #   [-----text-------]
            # 或者
            #          |[text]
            #    [text]|
            #   [------text----]
            #    [text]
            #
            bbox = page.bbox
            left_column = Group[KObject]()
            right_column = Group[KObject]()
            other_objs = list[KObject]()
            for obj in objs:
                if obj.bbox.width>100:
                    dx=10
                else:
                    dx=5
                if obj.bbox.x1-bbox.cx<=dx:
                    left_column.append(obj)
                    left_column.invalidate()
                elif obj.bbox.x0-bbox.cx>=-dx:
                    right_column.append(obj)
                    right_column.invalidate()
                else:
                    #包括线，表格，矩形等
                    #如是垂直线，忽略？
                    other_objs.append(obj)
            
            columns:list[Group[KObject]]=[]
            if left_column:
                columns.append(left_column)
            if right_column:
                columns.append(right_column)
            return columns,other_objs

        def adjust_groups(groups:list[Group[KObject]]):
            lists.remove2(groups,lambda i,groups:len(groups[i])==0)
            if len(groups)<=1:
                return 
            
            #按行排序
            lines:list[list[Group[KObject]]] = Sorter.get_lines(groups)
            
            #也可以不执行
            if True:
                for line in lines:
                    #[ text1
                    #              [text3]
                    # text2 
                    #]
                    #对于这种，合并为一个组更好？
                    if len(line)==2:
                        g1=line[0]
                        g2=line[1]
                        b1=g1.bbox.adjust(y0=g2.bbox.y0,y1=g2.bbox.y1)
                        if not b1.get(g1,ratio=0.1):
                            #[text1]
                            #[--blank--] [text3]   =>可以插入这里，然后合并
                            #[text2]
                            #if debugger.allow('gui'):
                                #page.show('before merge groups',objects=[g.bbox for g in line])

                            g1.extend(g2)
                            g1.invalidate()
                            Sorter.sort(g1)
                            lists.remove(groups,[g2],use_is=True)
                            del line[1]

                            #if debugger.allow('gui'):
                                #page.show('after merge groups',objects=g1)

                            pass
                    pass
            i=1
            while i<len(lines):
                #如果有并排
                line1=lines[i-1]
                line2=lines[i]
                if len(line1)==1 and len(line2)==1:
                    #        [group1]
                    #[group2]
                    #合并为一个组？
                    line1[0].extend(line2[0])
                    line1[0].invalidate()
                    del lines[i]
                    lists.remove(groups,line2,use_is=True)
                else:
                    i+=1

            #简单的是左右分栏，复杂的情况为
            #[----top----]      =>这个也需要记录
            #[left]|[right]
            #[----bottom-]      =>这个也需要记录
            #-----------------------分页
            #[left]|[right]
            

        def sort(objs:list[KObject]):
            lines = [Group[KObject](line) for line in Sorter.get_lines(objs)]
            i=0
            new_lines:list[list[KObject]]=[]
            while i<len(lines):
                line = lines[i]
                if len(line)==2 and isinstance(line[0],KText) and line[-1].bbox.height>=50 and isinstance(line[-1],(KBlock,KTable,KFigure)):
                    #TODO 可以不限制高度的
                    #[text] | [table] or [figure]
                    right_object = line[-1]
                    bbox = BBox.join2(line)
                    #assert bbox is not None
                    left_lines:list[Group[KObject]]=[]
                    for j in range(i+1,len(lines)):
                        line2 = lines[j]
                        if line2.bbox.x1<=right_object.bbox.x0 and line2.bbox.y1-bbox.y0>=5:
                            left_lines.append(line2)
                        else:
                            break

                    
                    if len(left_lines)>0:
                        new_lines.append(line[0:-1])
                        new_lines.extend(left_lines)
                        new_lines.append(line[-1:])
                        i+=1+len(left_lines)
                    else:
                        i+=1
                        new_lines.append(line)
                else:
                    i+=1
                    new_lines.append(line)
            
            objs.clear()
            for line in new_lines:
                objs.extend(line)
            return 


        objs = list(page.objects)

        columns,other_objs = split_columns(objs)
        groups:list[Group[KObject]]=[]
        
        if len(columns)<=1:
            #如果只有1列，可能为研报的首页，需要做更加复杂的处理？
            #也就是不一定需要水平居中，可能是7:3，3:7这样划分
            groups.append(Group(objs))
        else:
            #如果能够划分2列，表示为典型的双栏
            for column in columns:
                column.sort(key=lambda obj:obj.bbox.y0)
            
            other_objs.sort(key=lambda obj:obj.bbox.y1,reverse=True)

            row:Group[KObject]|None=None
            for obj in other_objs:
                if columns:
                    new_columns:list[Group[KObject]]=[]
                    for column in columns:
                        top,bottom=cut(column,obj)
                        
                        if top:
                            #表示需要插入一新行
                            row=None
                            groups.append(top)
                            
                        else:
                            pass

                        if bottom:
                            new_columns.append(bottom)
                            
                    
                    columns = new_columns

                if row is None:
                    #没有或者需要插入一个新行
                    row = Group()
                    groups.append(row)
            
                row.append(obj)
                row.invalidate()
            
            groups.extend(columns)
        

        adjust_groups(groups)
        for group in groups:
            #Sorter.sort(group)
            sort(group)
        
        return groups
        

class _TableLayout:
    _logger=logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger=XDebugger(f'{__module__}.{__qualname__}')
    _front_page_limit:Final=4
    def __init__(self):
        super().__init__()
    
    def parse(self,doc:KDocument):
        #对于有些使用表格布局且跨页的，需要在这里转换为表格，仅仅分栏无法还原顺序，如：
        #-----xxx-----
        #xxx|xxxxxxxxx
        #xxx|xxxxxxxxx
        #--------------跨页
        #   |xxxxxxx
        #--------------跨页
        #   |xxxxxx

        #目前没有太好的办法识别连续多个页面是否是使用表格布局的
        #通过语义判断意义也不大，因内容可以为任意
        if not self._is_yb(doc):
            return

        #如果是研究报告，首页的布局比较繁琐，转换为使用表格布局
        #[c1]|[c3]
        #[c2]|[c4]

        #第一页可能为封面，需要判断
        #否则就是仅仅处理第一页

        pages = doc.working_pages
        i=0
        while i<len(pages):
            j=self._parse_once(i,pages)
            if j==-1:
                i+=1
            else:
                i=j

    def _is_yb(self,doc:KDocument):
        """判断是否为研究报告"""
        score = 0
        pages = [
            page for page in doc.working_pages
            if page.number<=self._front_page_limit
        ]
        text = self._get_text(pages)
        if not text:
            return False
        rules:tuple[tuple[str,int],...]=(
            ('证券研究报告',3),
            ('证券研究',2),
            ('公司研究',2),
            ('行业研究',2),
            ('行业周报',2),
            ('深度报告',2),
            ('公司报告',2),
            ('投资评级',2),
            ('分析师',2),
            ('研究员',1),
            ('评级',1),
            ('目标价',1),
            ('投资要点',1),
            ('相对指数表现',1),
            ('相关研究',1),
            ('相关报告',1),
            ('投资建议',1),
            ('风险提示',1),
            ('SAC',1),
            ('请务必阅读',1),
            ('免责声明',1),
        )
        for word,weight in rules:
            if word in text:
                score+=weight
        if '.SH' in text or '.SZ' in text:
            score+=1
        if '证券' in text and ('报告' in text or '研究' in text):
            score+=2
        return score>=4

    def _parse_once(self,index:int,pages:Sequence[KPage])->int:
        # 只处理首页附近的研报版式页，避免把正文双栏页转换成布局表格。
        page = pages[index]
        if index>=self._front_page_limit or page.number>self._front_page_limit:
            return -1
        if self._is_toc_page(page):
            return -1
        if any(isinstance(obj,KTable) and obj.subtype=='layout' for obj in page.objects):
            return -1
        layout = self._find_layout_columns(page)
        if layout is None:
            return -1
        split,bboxes = layout
        tables:list[tuple[KPage,tuple[BBox,BBox]]]=[(page,bboxes)]

        prev_page = page
        for next_page in pages[index+1:]:
            if not self._can_continue_layout_from(prev_page,tables[-1][1]):
                break
            if next_page.number>self._front_page_limit:
                break
            if self._is_toc_page(next_page):
                break
            if next_page.number!=prev_page.number+1:
                break
            if not next_page.bbox.align('x',page.bbox,d=2):
                break
            bboxes2 = self._find_continuation_columns(next_page,bboxes,split)
            if bboxes2 is None:
                break
            tables.append((next_page,bboxes2))
            prev_page = next_page

        #group_id = f'layout-{page.number}-{len(tables)}'
        for table_index,(table_page,table_bboxes) in enumerate(tables):
            table = self._make_table(table_page,table_bboxes)

        return index+len(tables)

    def _find_layout_columns(self,page:KPage)->tuple[float,tuple[BBox,BBox]]|None:
        if self._is_toc_page(page):
            return None
        split = self._find_split(page,page.objects)
        if split is None:
            return None
        left,right,spans = self._split_objects(page,page.objects,split)

        full_width = self._find_full_width_objects(page,page.objects,split)
        left = [obj for obj in left if obj not in full_width]
        right = [obj for obj in right if obj not in full_width]
        left,right = self._trim_above_spanning_title(page,split,full_width,left,right)
        if not self._is_layout(page,left,right):
            return None

        # 如果还有大量跨栏对象夹在两列之间，说明更像普通复杂页面，不包装。
        layout_bbox = BBox.join2([*left,*right],strict=False)
        if layout_bbox is None:
            return None
        if self._split_blockers(page,spans,full_width):
            return None
        return split,(BBox.join2(left),BBox.join2(right))

    def _find_continuation_columns(self,page:KPage,ref_bboxes:tuple[BBox,BBox],split:float)->tuple[BBox,BBox]|None:
        if self._is_toc_page(page):
            return None
        if any(isinstance(obj,KTable) and obj.subtype=='layout' for obj in page.objects):
            return None
        full_width = self._find_full_width_objects(page,page.objects,split)
        objects = [
            obj for obj in page.objects
            if obj not in full_width
            and not self._is_page_margin(page,obj.bbox)
        ]
        if not objects:
            return None
        left,right,spans = self._split_objects(page,objects,split)
        ref_left,ref_right = ref_bboxes
        left = [
            obj for obj in left
            if obj.bbox.over('x',ref_left,d=10)
        ]
        right = [
            obj for obj in right
            if obj.bbox.over('x',ref_right,d=10)
        ]
        if not right:
            return None
        right_bbox = BBox.join2(right)
        if right_bbox.x1<=split:
            return None
        if right_bbox.width<ref_right.width*0.25 and len(right)<2:
            return None
        if self._split_blockers(page,spans,()):
            return None
        content_bbox = BBox.join2([*left,*right],strict=False)
        if content_bbox is None:
            return None

        # 延续页按首页列轴对齐。左列允许为空，右列承接跨页正文/表格。
        left_bbox = BBox(ref_left.x0,content_bbox.y0,ref_left.x1,content_bbox.y1)
        right_bbox = BBox(ref_right.x0,content_bbox.y0,max(ref_right.x1,right_bbox.x1),content_bbox.y1)
        return left_bbox,right_bbox

    def _can_continue_layout_from(self,page:KPage,bboxes:Sequence[BBox])->bool:
        # 真正的跨页 layout 在上一页通常会吃到页底附近；如果上一页下方还有
        # 大块空白，下一页更可能是新的章节/目录，不应继续沿用上一页列轴。
        bbox = BBox.join(bboxes)
        return bbox.y0<=page.bbox.y0+page.bbox.height*0.20

    def _is_toc_page(self,page:KPage)->bool:
        text = self._get_text([page])
        return '内容目录' in text or '图表目录' in text

    def _trim_above_spanning_title(
        self,
        page:KPage,
        split:float,
        full_width:Sequence[KObject],
        left:Sequence[KObject],
        right:Sequence[KObject],
    )->tuple[list[KObject],list[KObject]]:
        page_height = page.bbox.height
        spanning_titles = [
            obj for obj in full_width
            if isinstance(obj,KText)
            and obj.bbox.x0<split
            and obj.bbox.x1>split
            and obj.bbox.y0>page.bbox.y0+page_height*0.35
            and not self._is_page_margin(page,obj.bbox)
        ]
        if not spanning_titles:
            return list(left),list(right)
        top_cut = min(obj.bbox.y0 for obj in spanning_titles)
        return (
            [obj for obj in left if obj.bbox.y1<=top_cut+2],
            [obj for obj in right if obj.bbox.y1<=top_cut+2],
        )

    def _make_table(self,page:KPage,bboxes:Sequence[BBox])->KTable|None:
        debugger = self._debugger.bind(page=page.number)
        cells:list[KCell]=[]
        selected:list[KObject]=[]
        for col_index,cell_bbox in enumerate(bboxes):
            objs = self._get_cell_objects(page,cell_bbox,exclude=selected)
            Sorter.sort(objs)
            selected.extend(objs)
            cell=KCell(page,cell_bbox,row_index=0,col_index=col_index,objects=objs)
            cells.append(cell)
        if not selected:
            return
        insert_index = min(page.objects.index(obj) for obj in selected)
        table = KTable(page,BBox.join(bboxes),cells=cells,subtype='layout')
        table.adjust()
        if self._has_right_cell_left_overflow(page,table):
            return None
        lists.remove(page.objects,selected,use_is=True)
        page.objects.insert(insert_index,table)
        if debugger.allow('draw',page=page.number):
            page.draw(
                ('page',None),
                ('columns',bboxes,'number'),
                ('table',table.get_lines2()),
                show_type=False,
                dir='debug/default/tablelayout'
            )
        return table

    def _has_right_cell_left_overflow(self,page:KPage,table:KTable)->bool:
        hard_limit = max(24,page.bbox.width*0.03)
        soft_limit = max(12,page.bbox.width*0.02)
        soft_count = 0
        for cell in table.cells:
            if cell.col_index!=1 or cell.col_span!=1:
                continue
            for obj in cell.objects:
                if not isinstance(obj,(KText,KBlock)):
                    continue
                overflow = cell.bbox.x0-obj.bbox.x0
                if overflow>=hard_limit:
                    return True
                if overflow>=soft_limit:
                    soft_count+=1
                    if soft_count>=2:
                        return True
        return False

    def _find_split(self,page:KPage,objects:Sequence[KObject])->float|None:
        candidates = [
            obj for obj in objects
            if self._can_use_for_split(page,obj)
        ]
        if len(candidates)<4:
            return None
        candidates.sort(key=lambda obj:obj.bbox.cx)

        best:tuple[float,float]|None=None
        page_width = page.bbox.width
        min_gutter = max(1,page_width*0.002)
        min_center_gap = max(16,page_width*0.025)

        def update_best(split:float,extra_score:float=0):
            nonlocal best
            if split<=page.bbox.x0+page_width*0.15 or split>=page.bbox.x1-page_width*0.15:
                return
            left,right,spans = self._split_objects(page,objects,split)
            full_width = self._find_full_width_objects(page,objects,split)
            left = [obj for obj in left if obj not in full_width]
            right = [obj for obj in right if obj not in full_width]
            if not self._is_layout(page,left,right):
                return
            if self._split_blockers(page,spans,full_width):
                return
            left_bbox = BBox.join2(left)
            right_bbox = BBox.join2(right)
            gutter = right_bbox.x0-left_bbox.x1
            if gutter<min_gutter:
                return
            overlap = self._overlap_ratio(left_bbox,right_bbox,axis='y')
            score = gutter+overlap*30+self._sidebar_score(left,right)*5+self._side_panel_score(page,left_bbox,right_bbox,left,right)+extra_score
            if best is None or score>best[0]:
                best=(score,split)

        for i in range(1,len(candidates)):
            center_gap = candidates[i].bbox.cx-candidates[i-1].bbox.cx
            if center_gap<min_center_gap:
                continue
            left = candidates[:i]
            right = candidates[i:]
            if len(left)<1 or len(right)<1:
                continue
            left_bbox = BBox.join2(left)
            right_bbox = BBox.join2(right)
            gutter = right_bbox.x0-left_bbox.x1
            if gutter<min_gutter:
                continue
            split = (left_bbox.x1+right_bbox.x0)/2
            update_best(split,center_gap*0.3)

        # 有些首页标题横跨左右栏，会干扰按中心排序的切分；再扫描 x 区间空隙，
        # 允许横跨 gutter 的标题作为通栏对象排除。
        for left_obj in candidates:
            for right_obj in candidates:
                gutter = right_obj.bbox.x0-left_obj.bbox.x1
                if gutter>=min_gutter:
                    update_best((left_obj.bbox.x1+right_obj.bbox.x0)/2,gutter)
        if best is None:
            return None
        return best[1]

    def _split_objects(self,page:KPage,objects:Sequence[KObject],split:float)->tuple[list[KObject],list[KObject],list[KObject]]:
        left:list[KObject]=[]
        right:list[KObject]=[]
        spans:list[KObject]=[]
        d=max(2,page.bbox.width*0.005)
        for obj in objects:
            bbox = obj.bbox
            if bbox.x1<=split+d:
                left.append(obj)
            elif bbox.x0>=split-d:
                right.append(obj)
            elif min(split-bbox.x0,bbox.x1-split)<=max(12,page.bbox.width*0.02):
                if bbox.cx<split:
                    left.append(obj)
                else:
                    right.append(obj)
            else:
                spans.append(obj)
        return left,right,spans

    def _find_full_width_objects(self,page:KPage,objects:Sequence[KObject],split:float)->Sequence[KObject]:
        full_width:set[KObject]=set()
        page_width = page.bbox.width
        for obj in objects:
            bbox = obj.bbox
            if self._is_page_spanning(page,bbox) or self._is_cross_column_title(page,bbox,split):
                full_width.add(obj)

        for line in Sorter.get_lines(objects):
            bbox = BBox.join2(line)
            if bbox.x0<split and bbox.x1>split and bbox.width>page_width*0.78 and len(line)>=3:
                full_width.update(line)
        return tuple(full_width)

    def _is_layout(self,page:KPage,left:Sequence[KObject],right:Sequence[KObject])->bool:
        if len(left)<2 or len(right)<2:
            return False
        left_bbox = BBox.join2(left)
        right_bbox = BBox.join2(right)
        if left_bbox is None or right_bbox is None:
            return False
        min_column_width = max(36,page.bbox.width*0.10)
        if min(left_bbox.width,right_bbox.width)<min_column_width:
            return False
        if right_bbox.x0-left_bbox.x1<1:
            return False
        if self._overlap_ratio(left_bbox,right_bbox,axis='y')<0.25:
            return False
        sidebar_score = self._sidebar_score(left,right)
        if sidebar_score<=0:
            return False
        width_ratio = min(left_bbox.width,right_bbox.width)/max(left_bbox.width,right_bbox.width)
        # 版式侧栏通常明显窄一些；宽度接近时也允许，但必须有很强侧栏信号。
        return width_ratio<0.82 or sidebar_score>=3 or self._side_panel_score(page,left_bbox,right_bbox,left,right)>0

    def _sidebar_score(self,left:Sequence[KObject],right:Sequence[KObject])->int:
        return max(self._sidebar_score_of(left),self._sidebar_score_of(right))

    def _sidebar_score_of(self,objs:Sequence[KObject])->int:
        text = self._get_text(objs)
        result = 0
        for word in (
            '分析师','研究员','投资评级','评级','目标价','交易数据','股价','走势图','走势比较',
            '相对指数表现','公司简介','相关报告','相关研究','资料来源','数据来源','请务必阅读',
            '总股本','流通A股','52周','市值','EPS','PE','PB','Wind'
        ):
            if word in text:
                result+=1
        return result

    def _side_panel_score(self,page:KPage,left_bbox:BBox,right_bbox:BBox,left:Sequence[KObject],right:Sequence[KObject])->float:
        page_width = page.bbox.width
        left_width_ratio = left_bbox.width/page_width
        right_width_ratio = right_bbox.width/page_width
        score = 0.0

        # 研报首页常见为“左/右窄信息栏 + 主正文”。窄栏不一定在页面中线附近，
        # 需要在 split 打分时优先选择稳定的侧边栏边界。
        if left_width_ratio<=0.42 and right_bbox.width>=page_width*0.35:
            if left_bbox.x0<=page.bbox.x0+page_width*0.24:
                score+=18+self._sidebar_score_of(left)*4
        if right_width_ratio<=0.42 and left_bbox.width>=page_width*0.35:
            if right_bbox.x1>=page.bbox.x1-page_width*0.12 or right_bbox.x0>=page.bbox.x0+page_width*0.52:
                score+=18+self._sidebar_score_of(right)*4
        return score

    def _can_use_for_split(self,page:KPage,obj:KObject)->bool:
        bbox = obj.bbox
        if bbox.width<=1 or bbox.height<=1:
            return False
        if self._is_page_spanning(page,bbox):
            return False
        if bbox.height>page.bbox.height*0.85:
            return False
        if bbox.area<4:
            return False
        return isinstance(obj,(KText,KTable,KFigure,KBlock))

    def _is_page_spanning(self,page:KPage,bbox:BBox)->bool:
        page_width = page.bbox.width
        return (
            bbox.width>page_width*0.68
            and bbox.x0<page.bbox.x0+page_width*0.22
            and bbox.x1>page.bbox.x1-page_width*0.22
        )

    def _is_cross_column_title(self,page:KPage,bbox:BBox,split:float)->bool:
        page_width = page.bbox.width
        page_height = page.bbox.height
        if (
            bbox.x0<split
            and bbox.x1>split
            and bbox.y0>page.bbox.y0+page_height*0.70
            and bbox.width>page_width*0.30
        ):
            return True
        return (
            bbox.x0<split
            and bbox.x1>split
            and bbox.width>page_width*0.55
            and bbox.x0<page.bbox.x0+page_width*0.24
            and bbox.x1>page.bbox.x1-page_width*0.18
        )

    def _get_cell_objects(self,page:KPage,cell_bbox:BBox,*,exclude:Sequence[KObject])->list[KObject]:
        objs:list[KObject]=[]
        for obj in page.objects:
            if obj in exclude:
                continue
            bbox = obj.bbox
            inter = cell_bbox.intersect(bbox)
            if inter is None:
                continue
            if inter.height/max(1,bbox.height)<0.5:
                continue
            if cell_bbox.x0<=bbox.cx<=cell_bbox.x1:
                objs.append(obj)
                continue
            if inter.width/max(1,min(bbox.width,cell_bbox.width))>=0.55:
                objs.append(obj)
        return objs

    def _is_page_margin(self,page:KPage,bbox:BBox)->bool:
        page_height = page.bbox.height
        return (
            bbox.y0>page.bbox.y1-page_height*0.06
            or bbox.y1<page.bbox.y0+page_height*0.10
        )

    def _split_blockers(self,page:KPage,spans:Sequence[KObject],full_width:Sequence[KObject])->list[KObject]:
        page_width = page.bbox.width
        return [
            obj for obj in spans
            if obj not in full_width
            and obj.bbox.width>page_width*0.08
            and obj.bbox.height>4
        ]

    def _overlap_ratio(self,b1:BBox,b2:BBox,*,axis:str)->float:
        if axis=='x':
            start = max(b1.x0,b2.x0)
            end = min(b1.x1,b2.x1)
            base = min(b1.width,b2.width)
        else:
            start = max(b1.y0,b2.y0)
            end = min(b1.y1,b2.y1)
            base = min(b1.height,b2.height)
        if base<=0:
            return 0
        return max(0,end-start)/base

    def _get_text(self,items:Sequence[KObject]|Sequence[KPage])->str:
        buf:list[str]=[]
        def add_obj(obj:KObject):
            if isinstance(obj,KText):
                buf.append(obj.text)
            elif isinstance(obj,KTable):
                for cell in obj.cells:
                    for child in cell.objects:
                        add_obj(child)
            elif isinstance(obj,KBlock):
                for child in obj.objects:
                    add_obj(child)
        for item in items:
            if isinstance(item,KPage):
                if item.number==1:
                    #如果是首页，研报可能把一些文本也识别为页眉
                    for obj in item.header.objects:
                        add_obj(obj)
                for obj in item.objects:
                    add_obj(obj)
            else:
                add_obj(item)
        return '\n'.join(buf)
