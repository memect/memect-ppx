import logging
from pathlib import Path
import re
from functools import cached_property
from logging import Logger
from typing import Any, Final, Self, Sequence, TextIO, cast, override

from memect.base import strs
from memect.base.bbox import BBox, Point
from memect.base.matrix import Matrix
from memect.base.pattern import XPattern
from memect.pdf.base import (
    KBlock,
    KCell,
    KChar,
    KColor,
    KDocument,
    KFigure,
    KFormula,
    KObject,
    KPage,
    KTable,
    KText,
)
from memect.pdf.grid import Grid

def _md_escape(text: str) -> str:
    # 在实际使用中"()"不需要转义
    return re.sub(r"[`~*_+\-!{}#.\\]", lambda m: rf"\{m.group()}", text)

class XDest:
    def __init__(self,page_number:int,point:Point|BBox|None=None):
        super().__init__()
        self.page_number:Final=page_number
        self.bbox:Final=point if isinstance(point,BBox) else None
        """目标位置，可以为None"""
        self.point:Final=point if isinstance(point,Point) else None
        """目标位置，可以为None，通常为原文上一点的地方"""

class XNode:
    def __init__(self, object: "XObject"):
        super().__init__()
        self.object: Final = object
        self._children: list[XNode] = []
        self.parent: Self | None = None
        self.id:int=0
    
    def setup_ids(self,start:int=0):
        """从当前节点开始，给每一个节点设置id"""
        self.id=start
        start+=1
        for child in self.children:
            start=child.setup_ids(start)
        return start
    
    def add(self, *objs: "XObject|XNode",index:int|None=None):
        for obj in objs:
            if isinstance(obj, XObject):
                node = obj.node
            else:
                node = obj
            if node not in self._children:
                node.deatch()
                node.parent=self
                if index is None:
                    self._children.append(node)
                else:
                    self._children.insert(index,node)
                    index+=1
            else:
                raise ValueError(f'对象已经存在，重复添加:{obj}')

    def remove(self, *objs: "XObject|XNode"):
        for obj in objs:
            if isinstance(obj, XObject):
                node = obj.node
            else:
                node = obj
            self._children.remove(node)
            node.parent=None

    def up(self, step: int = 1):
        """往上移动多少级"""
        parent = self.parent
        while step > 0 and parent is not None:
            parent = parent.parent
            step -= 1

        if parent is not None:
            parent.add(self)
    
    def deatch(self):
        if self.parent is not None:
            self.parent.remove(self)
            self.parent=None

    def is_ancestor(self, node: "XNode") -> bool:
        """判断当前节点是否为node的祖宗"""
        if node.parent is None:
            return False
        elif self is node.parent:
            return True
        else:
            return self.is_ancestor(node.parent)

    def prev(self) -> "XNode|None":
        """前一个兄弟节点"""
        assert self.parent is not None
        i = self.parent._children.index(self)
        if i > 0:
            return self.parent._children[i - 1]
        else:
            return None

    def next(self) -> "XNode|None":
        """下一个兄弟节点"""
        assert self.parent is not None
        i = self.parent._children.index(self)
        if i + 1 < len(self.parent._children):
            return self.parent._children[i + 1]
        else:
            return None
        
    def is_root(self)->bool:
        return self.parent is None
    
    def is_formula(self)->bool:
        return isinstance(self.object,XFormula)
    
    def is_title(self)->bool:
        return isinstance(self.object,XText) and self.object.is_title()
    
    def is_text(self)->bool:
        return isinstance(self.object,XText)
    
    def is_table(self)->bool:
        return isinstance(self.object,XTable)
    
    def is_figure(self)->bool:
        return isinstance(self.object,XFigure)
    
    def is_block(self)->bool:
        return isinstance(self.object,XBlock)
    
    def lookup(self,obj:'XObject|KObject')->'XNode|None':
        """查找页面对象在那个节点上，从当前节点往下查找"""
        if isinstance(obj,KObject):
            if obj in self.object.objects:
                return self
        else:
            if self.object is obj:
                return self

        for child in self.children:
            node = child.lookup(obj)
            if node is not None:
                return node
        return None
    
    def expand_group(self):
        """如果是XGroup的，去掉XGroup，把组内的节点挂靠"""
        if isinstance(self.object,XGroup):
            assert self.parent is not None
            parent = self.parent
            i=self.index+1
            self.deatch()
            parent.add(*self.object.xobjects,index=i)

        for child in self._children:
            child.expand_group()

    @property
    def type(self)->str:
        return self.object.type

    @property
    def index(self) -> int:
        assert self.parent is not None
        return self.parent._children.index(self)

    @property
    def level(self) -> int:
        """获得当前节点的级别，0表示为根节点"""
        if self.parent is None:
            return 0
        else:
            return self.parent.level + 1

    @property
    def size(self) -> int:
        return len(self._children)

    @property
    def children(self) -> Sequence["XNode"]:
        return self._children
    

    @property
    def text(self)->'XText':
        assert isinstance(self.object,XText)
        return self.object
    
    @property
    def table(self)->'XTable':
        assert isinstance(self.object,XTable)
        return self.object
    
    @property
    def figure(self)->'XFigure':
        assert isinstance(self.object,XFigure)
        return self.object
    
    @property
    def formula(self)->'XFormula':
        assert isinstance(self.object,XFormula)
        return self.object
    
    @property
    def block(self)->'XBlock':
        assert isinstance(self.object,XBlock)
        return self.object
    
    def flat(self)->list['XNode']:
        nodes:list[XNode]=[self]
        for child in self._children:
            nodes.extend(child.flat())
        return nodes
    
    def jsonify(self)->Any:
        data:dict[str,Any]={
            "type":self.object.type,
        }
        data['data']=self.object.jsonify()
        if self.children:
            data['children']=[obj.jsonify() for obj in self.children]
        return data

    def stringify(self, *, indent: int = 0) -> str:
        # 使用纯文本的方式输出，更加容易查看结构

        def get_text() -> str:
            obj = self.object
            if isinstance(obj, XText):
                return obj.text
            elif isinstance(obj, XFigure):
                return '<figure>'
            elif isinstance(obj, XTable):
                return '<table>'
            elif isinstance(obj,XFormula):
                return '<formula>'
            elif isinstance(obj, XBlock):
                return f'<block:{obj.subtype}>'
            else:
                raise ValueError(f'不支持的类型:{type(obj)}')

            return ''
        buf: list[str] = []
        # [0][root][1,10]
        #   [1][chapter][1,10]<封面>
        #      [2][title][1,1]xxxx
        #      [2][title][1,1]xxxxx
        #   [1][chapter]第一章
        page_numbers = self.object.page_numbers
        if page_numbers:
            start=page_numbers[0]
            end = page_numbers[-1]
        else:
            start=-1
            end=-1
        buf.append(f'{" "*indent}[{self.level}][{self.type}][{start},{end}][{get_text()}]')
        for child in self._children:
            buf.append(child.stringify(indent=indent+4))
        return '\n'.join(buf)

    def markdown(self)->str:
        buf:list[str]=[]
        buf.append(self.object.markdown())
        for child in self.children:
            buf.append(child.markdown())
        return '\n\n'.join(buf)
class XTree:
    def __init__(self,doc:KDocument):
        super().__init__()
        self.doc:Final=doc
        self.xobjects:Final[list[XObject]]=[]
        """获得该树的对象，可以添加/删除，因为需要跨页表格，跨页文本合并等"""
        self.root: Final = XText.create_title(doc,'<doc>').node
        """根节点"""
        self.layout_tables:Final[list[Any]]=[]
        """记录用来布局的表格，目的是在生成docx的，需要使用表格的方式来布局"""
        for page in doc.working_pages:
            for obj in page.objects:
                self.xobjects.append(XObject.from_object(obj))
    

    def get_sections(self)->list['XSection']:
        """获得对应docx的节（分栏）"""
        #[c1|c2]=>双栏的，可以左右对齐，或者偏左，偏右
        #[--c3--]=>单栏的
        #[c1|c2|c3]=>三栏的

        #按阅读顺序放平（逻辑顺序）
        #或者按书写顺序放平（从上到下）
        nodes = self.root.flat()
        #TODO 去掉逻辑标题
        #TODO 还需要根据书写顺序排序（虽然99.99%情况下不需要了）
        #TODO 如果首页没有页眉页脚，也独立为一个Section
        xobjects = [node.object for node in nodes if node.object.objects]
        sections:list[XSection]=[]
        for xobj in xobjects:
            if isinstance(xobj,XText) and len(xobj.objects)==0:
                #表示为逻辑上的，没有具体的对象，跳过
                continue
            #获得对象是单栏/双栏/三栏布局的
            if not sections or not sections[-1].join(xobj):
                section=XSection.from_object(xobj)
                sections.append(section)
        
        #最后判断节和节之间是否需要分页，如果间距过大，或者为某些开始？
        i=1
        for i in range(1,len(sections)):
            s1=sections[i-1]
            s2=sections[i]
            #准确的为y0最小的
            xobj1=s1.xobjects[-1]
            xobj2=s2.xobjects[0]
            obj1=xobj1.objects[-1]
            obj2=xobj2.objects[0]
            if obj1.page.number==obj2.page.number:
                #在同一页肯定为continuous
                s2.start='continuous'
            elif obj1.page.number+1==obj2.page.number:
                #判断是否需要分页，如果
                if obj1.bbox.y0-obj1.page.bbox.y0>=200:
                    #如果上一节距离页脚有很大的空间，就表示需要分页
                    s2.start='nextPage'
                else:
                    #如果为章节的标题，可能分页更好？
                    #而且为每一页的第一个对象？
                    #有些文档也是使用“第一章xxx”，“第二章xxx”，也没有分页
                    if isinstance(xobj2,XText) and xobj2.is_title():
                        k=obj2.page.objects.index(obj2)
                        if k==0:
                            pass
                        pass
                    s2.start='nextPage'
            else:
                #跳了几页肯定为分页
                s2.start='nextPage'
                 

        return sections
    

    def markdown(self)->str:
        #不需要输出root，所以不是直接的root.markdown()
        buf:list[str]=[]
        for child in self.root.children:
            buf.append(child.markdown())
        return '\n\n'.join(buf)
    
    def jsonify(self):
        return {
            'root':self.root.jsonify()
        }
        
class LayoutTable:
    def __init__(self):
        super().__init__()
        self.row_num:int=0
        self.col_num:int=0
        self.cells:list[XNode]=[]
    
    def jsonify(self)->Any:
        data:dict[str,Any]={}
        data['row_num']=self.row_num
        data['col_num']=self.col_num
        
        return data
class XColumn:
    def __init__(self):
        super().__init__()
        self.space:int=0
        self.width:int=0

class XSection:
    """对应docx的一节，表示单栏/双栏/三栏等布局"""
    def __init__(self):
        super().__init__()
        self.xobjects:Final[list[XObject]]=[]
        self.equal_width=True
        self.col_num:int=1
        self.space:int=0
        self.columns:list[XColumn]=[]
        self.start:str='nextPage'
    
    def join(self,xobj:'XObject')->bool:
        """判断obj是否属于该节，如果属于，添加到该节且返回True"""
        #页眉页脚相同
        #分栏相同
        #没有分页

        #前面几页没有页眉页脚的作为一栏
        #为了简化，页眉不同也不分节，使用同一个页眉页脚
        obj = xobj.objects[0]
        s1=obj.page.get_section(obj)
        if s1.col_num==self.col_num:
            #TODO 现在简单的判断栏数即可，后续还需要判断栏宽
            self.xobjects.append(xobj)
            return True
        else:
            return False
    

    def is_page_break(self,xobj:'XObject')->bool:
        """判断index对应的对象前面，是否需要插入一个分页符"""
        index = self.xobjects.index(xobj)
        #使用阅读顺序第一个？还是使用y1最高的（双栏布局可能就不正确）
        obj = xobj.objects[0]
        k = obj.page.objects.index(obj)
        if k==0:
            #表示为当前页面的第一个对象，获得上一页的最后一个对象，判断和页面底部的距离
            #如果距离页面底部很大，就分页
            #或者当前对象为章节标题等，也需要分页
            if isinstance(xobj,XText) and xobj.node.level==1:
                return True
            if index-1>=0:
                prev_xobj = self.xobjects[index-1]
                if prev_xobj.objects[-1].bbox.y0>=200:
                    #严格的说是和页眉比较，但是这里直接和页面底部比较即可？
                    pass
        return False

    @classmethod
    def from_object(cls,xobj:'XObject')->Self:
        assert len(xobj.objects)>0
        obj = xobj.objects[0]
        s1=obj.page.get_section(obj)
        sec = cls()
        sec.col_num = s1.col_num
        sec.xobjects.append(xobj)
        #设置列的宽度，目前为相等的
        #sec.columns = []
        return sec


class XObject:
    """逻辑上的对象，包含页面上的0-n个对象"""

    type:str='xobject'
    def __init__(self,doc:KDocument):
        super().__init__()
        self.doc:Final = doc
        self.node = XNode(self)
        self._objects: Final[list[KObject]] = []
        self.subtype:str|None=None
        self.bbox:BBox|None=None
        """逻辑的bbox，如：跨页/跨栏合并后获得一个，width/height是正确的,x0,y0,x1,y1等可以为相对"""

    @property
    def pages(self) -> Sequence[KPage]:
        pages: list[KPage] = []
        for obj in self._objects:
            if obj.page not in pages:
                pages.append(obj.page)
        pages.sort(key=lambda p: p.number)
        return pages
    
    @property
    def page_numbers(self)->Sequence[int]:
        return [p.number for p in self.pages]

    @property
    def objects(self) -> Sequence[KObject]:
        return self._objects

    @property
    def texts(self)->Sequence[KText]:
        assert all(isinstance(obj,KText) for obj in self._objects)
        return cast(Sequence[KText],self._objects)

    @property
    def figures(self)->Sequence[KFigure]:
        assert all(isinstance(obj,KFigure) for obj in self._objects)
        return cast(Sequence[KFigure],self._objects)

    @property
    def formulas(self)->Sequence[KFormula]:
        assert all(isinstance(obj,KFormula) for obj in self._objects)
        return cast(Sequence[KFormula],self._objects)
    
    @property
    def tables(self)->Sequence[KTable]:
        assert all(isinstance(obj,KTable) for obj in self._objects)
        return cast(Sequence[KTable],self._objects)
    
    def add(self, *objs: KObject,index:int|None=None):
        if not objs:
            return
        for obj in objs:
            if obj in self._objects:
                raise ValueError(f'对象重复添加:{obj}')
            
            if index is None:
                self._objects.append(obj)
            else:
                self._objects.insert(index,obj)
                index+=1
        
        self.invalidate()

    def remove(self, *objs: KObject):
        if not objs:
            return
        for obj in objs:
            self._objects.remove(obj)
        self.invalidate()
    
    def replace(self,old_objs:Sequence[KObject],new_objs:Sequence[KObject],*,strict:bool=True):
        """替代对象
        old_objs:[],
        new_objs:[]
        strict: True表示old_objs和new_objs，为一一对应，一个替代一个，False表示先删除old_objs，再插入new_objs，位置为old_objs[0]
        """
        if not old_objs and not new_objs:
            return
        if strict:
            assert len(old_objs)==len(new_objs)
            for obj1,obj2 in zip(old_objs,new_objs):
                assert obj2 not in self._objects
                i = self._objects.index(obj1)
                self._objects[i]=obj2
            self.invalidate()
        else:
            i=self._objects.index(old_objs[0])
            self.remove(*old_objs)
            self.add(*new_objs,index=i)
            self.invalidate()
    

    
    def invalidate(self):
        pass

    def validate(self):
        """检查对象是否有效"""
        pass
    
    def jsonify(self)->Any:
        data = {
            'type':self.type,
            'objects':self._jsonify_objects(self._objects)
        }
        if len(data['objects'])>1:
            data['merged']=True
        else:
            #默认为False，不需要输出
            pass

    
    def _jsonify_objects(self,objs:Sequence[KObject])->Any:
        return [self._jsonify_object(obj) for obj in objs]
    
    def _jsonify_object(self,obj:KObject)->Any:
        data = obj.jsonify()
        data['page_number']=obj.page.number
        return data
    
    def markdown(self)->str:
        return ''
    
    @classmethod
    def from_object(cls,obj:KObject)->Self:
        doc = obj.doc
        if isinstance(obj,KTable):
            xobj = XTable(doc,[XCell.from_cell(c) for c in obj.cells],tables=[obj])
            xobj.subtype=obj.subtype
            m=Matrix().translate(-obj.bbox.x0,-obj.bbox.y0)
            for c1,c2 in zip(xobj.cells,obj.cells):
                c1.bbox = c2.bbox.transform(m)

            xobj.bbox = obj.bbox.transform(m)
        else:
            if isinstance(obj,KText):
                xobj = XText(doc)
            elif isinstance(obj,KFigure):
                xobj = XFigure(doc)
            elif isinstance(obj,KFormula):
                xobj = XFormula(doc)
            elif isinstance(obj,KBlock):
                xobj = XBlock(doc)
            else:
                #后续可能支持KBlock，先作为一个整体，然后再展开
                raise ValueError(f'不支持的类型:{obj}')
            xobj.add(obj)
            xobj.subtype = obj.subtype
        assert isinstance(xobj,cls)
        return xobj

class XCell:
    table:'XTable'
    def __init__(self,row_index:int=0,col_index:int=0,row_span:int=1,col_span:int=1,cells:Sequence[KCell]|None=None):
        super().__init__()
        self.row_index = row_index
        self.col_index = col_index
        self.row_span = row_span
        self.col_span = col_span
        self.cells: Sequence[KCell] = tuple(cells or []) 
        """表示组成这个单元格的原文的cell，注意：这些cell可能来自body table，如果表头重复的"""

        self.bbox:BBox|None=None
        """可以设置逻辑位置，相对表格左下角"""
        # TODO 严格的说，单元格内包含的也是xobjects
        # 如：2个单元格合并，t1+t2，那么，也需要合并为一个xtextbox才更加合理
        # 这样才能够在生成docx更加准确
        #self.xobjects:list[XObject]=[]
    

    @property
    def color(self)->KColor|None:
        """获得单元格的背景颜色"""
        for cell in self.cells:
            #可能来自body cell，在cell.copy()的时候，已经同步了color/font_color
            color=cell.color or KColor.WHITE
            if not color.is_white():
                #使用第一个的颜色，正常应该都是相同的颜色
                return color
        return None
    
    @property
    def font_color(self)->KColor|None:
        """如果是来自图片的表格，字体颜色需要通过ocr的方式获得"""
        for cell in self.cells:
            color = cell.font_color
            if color is not None:
                return color
        return None

    @property
    def merged(self)->bool|None:
        """
        上下相邻表格的最后一行和第一行的单元格的合并状态，
        True表示该单元格上下相邻合并，False表示和上下相邻单元格没有合并，None表示不是上下相邻的单元格
        """
        if len(self.cells)>1:
            return True
        elif len(self.cells)==1:
            cell = self.cells[0]

            if cell.table.main is not None:
                #表示为body table
                k=self.table.tables.index(cell.table.main)
            else:
                k=self.table.tables.index(cell.table)

            if k==0:
                #表示为第一个表格
                if cell.subtype=='header':
                    #如果是表头
                    return None
                if cell.row_index+cell.row_span==cell.table.row_num:
                    #最后一行，没有合并
                    return False
                return None
            else:
                #如果为中间表格，判断首行或者尾行
                #如果是最后一个表格，判断首行
                if cell.table.main is not None:
                    #如果cell使用body table
                    if cell.row_index==0 or (k+1<len(self.table.tables) and cell.row_index+cell.row_span==cell.table.row_num):
                        return False
                    return None
                else:
                    #cell使用table的，需要先去掉表头
                    if cell.table.header is not None:
                        i=cell.table.header.row_num
                    else:
                        i=0
                    
                    if cell.row_index==i or (k+1<len(self.table.tables) and cell.row_index+cell.row_span==cell.table.row_num):
                        return False
                    return None
        else:
            #不应该出现这个，至少有一个
            return None
            
        

    @property
    def text(self)->str:
        return ''.join(c.text for c in self.cells)
    
    @property
    def objects(self) -> Sequence[KObject]:
        objects: list[KObject] = []
        for cell in self.cells:
            objects.extend(cell.objects)
        return objects

    @property
    def pages(self) -> Sequence[KPage]:
        pages: list[KPage] = []
        for cell in self.cells:
            if cell.page not in pages:
                pages.append(cell.page)
        pages.sort(key=lambda p: p.number)
        return pages
    
    def is_header(self)->bool:
        """表示是否为表头单元格"""
        if len(self.cells)>0 and self.cells[0].subtype=='header':
            #这个判断方法对于第一个表格有效，因为使用的原始的表格
            #对于后面的，如果有header，使用的是body，也就是来自body的cell，而不是原始表格的cell
            #对于目前来说，这个实现足够了
            return True
        else:
            return False

    
    def jsonify(self)->Any:
        def jsonify_object(obj:KObject)->Any:
            data=obj.jsonify()
            data['page_number']=obj.page.number
            return data
        data:dict[str,Any]={}
        data['row_index']=self.row_index
        data['col_index']=self.col_index
        data['row_span']=self.row_span
        data['col_span']=self.col_span

        #TODO 再合并，输出xobjects?
        if self.objects:
            data['objects']=[jsonify_object(obj) for obj in self.objects]
            if len(data['objects'])>1:
                data['merged']=True
        return data
    
    @classmethod
    def from_cell(cls,cell:KCell)->Self:
        return cls(row_index=cell.row_index,col_index=cell.col_index,row_span=cell.row_span,col_span=cell.col_span,cells=[cell])


class XItem[T]:
    def __init__(self,bbox:BBox,object:T):
        super().__init__()
        self.bbox=bbox
        self.object=object

class XTable(XObject):
    type='xtable'
    def __init__(self,doc:KDocument,cells:Sequence[XCell],tables:Sequence[KTable]):
        super().__init__(doc)
        assert len(tables)>0
        assert len(cells)>0
        for cell in cells:
            cell.table = self
        self.row_num:int=max(c.row_index+c.row_span for c in cells)
        self.col_num:int=max(c.col_index+c.col_span for c in cells)
        self.cells:Sequence[XCell]=tuple(cells)
        self._grid:list[list[XCell]]|None=None

        #表格合并需要使用到的属性
        self.working_tables:list[KTable]=[]
        """真正用来合并的表格对象，除了第一个，后面的为去掉了表头的"""

        #表示合并的多个表格，和working_tables的区别在于
        #tables=[t1,t2,t3]
        #working_tables=[t1,t2_body,t3_body] 如果t2，t3有重复表头的,t2_body.main is t2,t3_body.main is t3
        self.add(*tables)
    
    @override
    def invalidate(self):
        super().invalidate()
    
    def _get_grid(self)->list[list[XCell]]:
        if self._grid is not None:
            return self._grid

        grid: list[list[KCell]] = []
        for i in range(self.row_num):
            row = [None] * self.col_num
            grid.append(row) # type: ignore

        self._grid = grid    
        for cell in self.cells:
            for i in range(cell.row_index,cell.row_index+cell.row_span):
                self._grid[i][cell.col_index:cell.col_index+cell.col_span]=[cell]*cell.col_span
        return self._grid
    
    def __getitem__(self, key:tuple[int,int]):
        i,j=key
        return self._get_grid()[i][j]

    def get_row(self, row_index: int, *, strict: bool = False) ->list[XCell]:
        if row_index < 0:
            row_index += self.row_num
        row: list[XCell] = []
        for cell in self.cells:
            if cell.row_index <= row_index < cell.row_index+cell.row_span:
                if strict:
                    # 返回的行的长度=col_num
                    row.extend([cell]*cell.col_span)
                else:
                    row.append(cell)
            else:
                pass
        # 和列不同，这里需要排序一下，列就不需要了
        row.sort(key=lambda cell: cell.col_index)
        return row

    def get_column(self, col_index: int, *, strict: bool = False) -> list[XCell]:
        if col_index < 0:
            col_index += self.col_num
        column: list[XCell] = []
        for cell in self.cells:
            if cell.col_index <= col_index < cell.col_index+cell.col_span:
                if strict:
                    # 返回的列的长度==row_num
                    column.extend([cell]*cell.row_span)
                else:
                    column.append(cell)
            else:
                pass

        # 实际上并不需要排序
        column.sort(key=lambda cell: cell.row_index)
        return column
    
    def is_layout(self)->bool:
        """表示表格的意图为布局"""
        return self.tables[0].is_layout()
    
    def is_wbk(self)->bool:
        return self.tables[0].is_wbk()
    
    def is_ybk(self)->bool:
        return self.tables[0].is_ybk()
    
    @override
    def jsonify(self)->Any:
        data:dict[str,Any]={}
        data['objects']=self._jsonify_objects(self._objects)
        if len(self.objects)>1:
            #表示为多个表格合并，返回合并后的
            data['row_num']=self.row_num
            data['col_num']=self.col_num
            data['cells']=[cell.jsonify() for cell in self.cells]
            data['merged']=True
        else:
            #没有合并，直接使用objects[0]即可
            pass

        #跨页合并后，肯能为ybk+ybk，或者wbk+wbk，或者layout+layout，或者ybk+wbk混合？
        data['subtype']=self.subtype
        return data
    
    @override
    def markdown(self) -> str:
        return self.html()

    def html(self) -> str:
        # 没有使用这个就是返回最基本的table的结构，不需要style
        # table.html()
        from html import escape

        def render_objects(objs: Sequence[KObject],allow_chars:bool=False) -> str:
            buf: list[str] = []
            for obj in objs:
                if isinstance(obj, KText):
                    # TODO 如果全部是文字，如果包含有图片
                    if obj.lines:
                        for tl in obj.lines:
                            buf.append("<div>")
                            buf.append(render_objects(tl.objects,allow_chars=True))
                            buf.append("</div>")
                    else:
                        buf.append(escape(obj.text))
                elif isinstance(obj, KFigure):
                    buf.append(f'<img src="{obj.filename}">')
                elif isinstance(obj, KFormula):
                    buf.append(f'<img src="{obj.filename}">')
                elif isinstance(obj, KTable):
                    buf.append(obj.html())
                elif isinstance(obj,KBlock):
                    #KBlock，表示一组对象
                    buf.append(render_objects(obj.objects))
                elif isinstance(obj,KChar) and allow_chars:
                    buf.append(escape(obj.text))
                else:
                    raise ValueError(f'不支持的对象:{obj}')
            return "".join(buf)

        buf: list[str] = []
        buf.append("<table>")
        if True:
            # 如果存在错位的跨列，如果该列没有内容，会导致width=0，显示上看不出来错位
            # 所以这里简单的设置一个宽度
            buf.append("<colgroup>")
            for i in range(self.col_num):
                buf.append('<col colspan=1 style="min-width:20px;"></col>')
            buf.append("</colgroup>")
        i = -1
        tr: list[str] = []
        for cell in self.cells:
            if cell.row_index != i:
                if tr:
                    tr.append("</tr>")
                    buf.extend(tr)
                    tr.clear()
                i = cell.row_index
                tr.append("<tr>")

            if cell.col_span == 1 and cell.row_span == 1:
                # 多数没有
                tr.append("<td>")
            else:
                tr.append(f'<td colspan="{cell.col_span}" rowspan="{cell.row_span}">')
            tr.append(render_objects(cell.objects))
            tr.append("</td>")

        if tr:
            tr.append("</tr>")
            buf.extend(tr)
        buf.append("</table>")
        return "".join(buf)

    def rich_html(self, fp: str | Path | TextIO | None = None, full: bool = True) -> str:
        """生成给测试使用的html，可以显示单元格合并信息"""
        from html import escape
        def render_objects(objs: Sequence[KObject],allow_chars:bool=False) -> str:
            buf: list[str] = []
            for obj in objs:
                if isinstance(obj, KText):
                    # TODO 如果全部是文字，如果包含有图片
                    if obj.lines:
                        for tl in obj.lines:
                            buf.append("<div>")
                            buf.append(render_objects(tl.objects,allow_chars=True))
                            buf.append("</div>")
                    else:
                        buf.append(escape(obj.text))
                elif isinstance(obj, KFigure):
                    buf.append(f'<img src="{obj.filename}">')
                elif isinstance(obj, KFormula):
                    buf.append(f'<img src="{obj.filename}">')
                elif isinstance(obj, KTable):
                    buf.append(obj.html())
                elif isinstance(obj,KBlock):
                    #KBlock，表示一组对象
                    buf.append(render_objects(obj.objects))
                elif isinstance(obj,KChar) and allow_chars:
                    buf.append(escape(obj.text))
                else:
                    raise ValueError(f'不支持的对象:{obj}')
            return "".join(buf)
        
        buf: list[str] = []
        if full:
            buf.append('<html><head></head><body>')
        buf.append('<table style="border: 1px solid;border-collapse: collapse;">')
        # 如果需要显示复杂的表格，如下设置可以让跨列跨行的显示更加明确
        buf.append('<colgroup>')
        for i in range(self.col_num):
            buf.append('<col colspan=1 style="width:100px;"></col>')
        buf.append('</colgroup>')
        buf.append('<tbody>')

        i=-1
        tr:list[str]=[]
        for cell in self.cells:
            if cell.row_index!=i:
                if tr:
                    tr.append('</tr>')
                    buf.extend(tr)
                    tr.clear()
                i=cell.row_index
                #如果使用css，并不需要输出style
                tr.append('<tr style="border:1px solid;">')

            if cell.is_header():
                #重复的表头不存在merged=True或者False的情况
                bgcolor='background-color:blue;'
            elif cell.merged is True:
                #黄色，表示来自多个表格合并
                bgcolor='background-color:yellow;'
            elif cell.merged is False:
                #灰色，上下相邻的单元格，没有合并
                bgcolor='background-color:gray;'
            else:
                bgcolor=''
            tr.append(
                    f'<td colspan="{cell.col_span}" rowspan="{cell.row_span}" style="border:1px solid;{bgcolor}">')
            #tr.append(html.escape(cell.body.text()))
            tr.append(render_objects(cell.objects))               
            tr.append('</td>')
        
        if tr:
            tr.append('</tr>')
            buf.extend(tr)

        buf.append('</tbody>')

        buf.append('</table>')
        if full:
            buf.append('</body></html>')
        s = ''.join(buf)
        if isinstance(fp, (str, Path)):
            Path(fp).write_text(s, encoding='utf-8')
        elif isinstance(fp, TextIO):
            fp.write(s)
        else:
            pass
        return s

    def apply_merged_info(self):
        """把单元格的合并信息设置到组合的table中，对于提取没有什么用，只是为了html显示合并信息"""
        def set_cell(cell:KCell,merged:bool):
            assert cell.original_cell is None
            cell.merged=merged
        #重置
        for table in self.tables:
            for cell in table.cells:
                cell.subtype=None
                #cell.merged=None
        
        if len(self.tables)<=1:
            return
        
        for xcell in self.cells:
            if xcell.merged is True:
                #表示由多个单元格合并，第一个并不需要设置
                for c in xcell.cells:#[1:]:
                    set_cell(c,True)
            elif xcell.merged is False:
                for c in xcell.cells:
                    set_cell(c,False)
            else:
                pass

        for table in self.tables:
            if table.header is not None:
                #表示重复的表头
                j = table.header.row_num
                for cell in table.cells:
                    if cell.row_index<j:
                        cell.subtype='header'
                    else:
                        cell.subtype=None
                pass

    @classmethod
    def from_grid(cls,doc:KDocument,grid:Grid,items:Sequence[XItem[KCell]],*,tables:Sequence[KTable])->Self:
        xcells:list[XCell]=[]
        items = list(items)
        m = Matrix().translate(-grid.bbox.x0,-grid.bbox.y0)
        for cell in grid.cells:
            include_cells = [item.object for item in cell.bbox.get(items,ratio=0.8,remove=True)]
            xcell = XCell(row_index=cell.row_index,col_index=cell.col_index,row_span=cell.row_span,col_span=cell.col_span,cells=include_cells)
            #相对表格左下角
            xcell.bbox=cell.bbox.transform(m)
            xcells.append(xcell)
        xtable = cls(doc,xcells,tables=tables)
        #(0,0,width,height)
        xtable.bbox = grid.bbox.transform(m)
        return xtable

class XText(XObject):
    """文本内容"""
    type='xtext'
    def __init__(self,doc:KDocument,text:str|None=None):
        super().__init__(doc)
        self._text:Final[str|None]=text
        """自定义的文本，如：逻辑标题"""
        self.no:XNo|None=None
        """如果为标题，可以设置序号"""
        self.dest:XDest|None=None
        """如果为标题，且出现在目录中，可以设置目标位置"""
        self._raw_text:str|None=None
    
    def is_title(self)->bool:
        return self.type=='xtitle'
    
    def as_title(self):
        self.type='xtitle'
    
    def as_text(self):
        self.type='xtext'
    
    @property
    def bold(self)->bool:
        """判断该文本是否为粗体"""
        return False
    
    @property
    def text(self)->str:
        if self._text is not None:
            return self._text
        if self._raw_text is None:
            self._raw_text=''.join(obj.text for obj in self.objects if isinstance(obj,KText))
        return self._raw_text
    
    
    @override
    def invalidate(self):
        super().invalidate()
        self._raw_text=None
    
    @override
    def jsonify(self)->Any:
        data:dict[str,Any]={}
        if self._text is not None:
            #如果指定了文本
            data['text']=self._text
        else:
            data['objects']=self._jsonify_objects(self._objects)
            if len(data['objects'])>1:
                data['merged']=True
        return data
    
    @override
    def markdown(self)->str:
        #如果级别太多了怎么办？
        if self.type=='xtitle':
            level = '#'*self.node.level
            return f'{level} {_md_escape(self.text)}'
        else:
            return _md_escape(self.text)
    
    @classmethod
    def create_title(cls,doc:KDocument,text:str)->Self:
        obj = cls(doc,text=text)
        obj.as_title()
        return obj


class XFigure(XObject):
    type='xfigure'
    def __init__(self,doc:KDocument):
        super().__init__(doc)
        self._filename: str = ""
        """表示新的图片文件名，如：几个图片合并后的截图，如果为空，表示没有合并，也就是只有一个图片"""

    

    def invalidate(self):
        super().invalidate()
        if len(self.objects)>1:
            #需要重新生成图片？
            #self.filename=''
            #self.bbox=BBox(0,0,w,h)
            pass


    @property
    def filename(self)->str:
        return self._filename or self.figures[0].filename
        
    @filename.setter
    def filename(self,filename:str):
        self._filename = filename
    
    @property
    def fullpath(self) -> Path:
        return self.doc.out_dir / self.filename

    @override
    def markdown(self) -> str:
        if len(self.objects)>1:
            name = self.fullpath.name
            return f"![{_md_escape(name)}](./images/{_md_escape(name)})"
        else:
            return self.objects[0].markdown()
    
    @override
    def jsonify(self)->Any:
        data:dict[str,Any] = {
        }
        data['objects']=self._jsonify_objects(self.objects)
        if len(self.objects)>1:
            data['filename']=self.filename
            #data['bbox']
            data['merged']=True

        return data


class XFormula(XObject):
    type='xformula'
    def __init__(self,doc:KDocument):
        super().__init__(doc)
        self._latex:str|None = None
        """多个对象合并后的latex"""
        self._filename:str|None = None
        """多个对象合并后的图片文件名"""
    
    def invalidate(self):
        super().invalidate()
        if len(self.objects)>1:
            #更新filename,latex?
            pass

    @property
    def latex(self)->str:
        return self._latex or self.formulas[0].latex
            
    @property
    def filename(self)->str:
        return self._filename or self.formulas[0].filename
    
    @latex.setter
    def latex(self,latex:str):
        self._latex = latex
    
    @filename.setter
    def filename(self,filename:str):
        self._filename = filename
    
    @property
    def inline(self)->bool:
        if len(self.formulas)>1:
            return False
        else:
            return self.formulas[0].inline

    @property
    def fullpath(self) -> Path:
        return self.doc.out_dir / self.filename
    
    @override
    def markdown(self):
        if self.latex:
            if self.inline:
                # $xx$    markdown用
                # \(xxx\) 标准
                return f"${self.latex}$"
            else:
                # $$xx$$ markdown用
                # \[xx\] 标准
                return f"$${self.latex}$$"
                # return rf'\[{self.latex}\]'
        else:
            name =_md_escape(self.fullpath.name)
            return f"![{name}](./images/{name})"

    
    @override
    def jsonify(self)->Any:
        data:dict[str,Any]={}
        data['objects']=self._jsonify_objects(self._objects)
        if len(self._objects)>1:
            #如果有，表示为多个跨页/跨栏合并后的结果
            if self.filename:
                data['filename']=self.filename
            if self.latex:
                data['latex']=self.latex
            
            data['merged']=True
        return data

class XBlock(XObject):
    type='xblock'

    @override
    def markdown(self)->str:
        buf:list[str]=[]
        for obj in self._objects:
            buf.append(obj.markdown())
        return '\n\n'.join(buf)

    
class XGroup(XObject):
    """多个XObject合并为一组，作为一个整体，解析完毕后再展开，如：某些引用的内容，不需要解析层次结构"""
    type="xgroup"
    def __init__(self,doc:KDocument):
        super().__init__(doc)
        self.xobjects:Final[list[XObject]]=[]
        """包含的是XObject"""


class XNo:
    """序号，可以为逻辑上的（页面没有原文），或者来自原文"""
    _logger:Logger = logging.getLogger(f'{__module__}.{__qualname__}')
    _value_pattern: Final = XPattern(
        'search',
        join=False,
        patterns=[
            #这个是最宽松的，支持混合多级序号，如：一.1.a
            #(1.2.) => ['1','2']
            #(1.3)  => ['1','3']
            #(i.ii.iv) => ['i','ii','iv']
            r'([.]?(([0-9]+)|([a-z]+)|([A-Z]+)|([〇一二三四五六七八九十零百千]+)))+',
            #
            # ⑴-⒇
            r'[\u2474-\u2487]',
            # ①-⑳
            r'[\u2460-\u2473]',
            #如果是英文的，还有
            #1st,2nd,3rd,4th----20th
            #21st,22nd,23rd,24th-30th
            #31st,32nd,33rd,34th-40th
        ])
    #因为也是字母，所以就单独
    _roman_pattern: Final = XPattern(
        'fullmatch',
        join=False,
        patterns=[
            r'ivxlcdm',
            r'IVXCLDM'
        ]
    )

    def __init__(self, text: str,*,prefix:str,suffix:str,parts:Sequence[str],value:int,value_type:str,text2:str|None=None, xobject: 'XText|None' = None, roman: bool = False):
        super().__init__()
        self.text: Final = text
        """序号的文本，如：1.，一、，第一章，（一），（一、），一），1），a），a.b.c"""
        self.text2:Final = text2 or text
        """归一化后的文本"""
        self._suffix:Final=suffix
        """后缀"""
        self._prefix:Final=prefix
        """前缀"""
        self._parts:Final=parts
        """序号的每个部分，如：1.1 => ['1','1']"""

        self.level:Final=len(parts)
        """序号有多少级，1表示1级，如：1，2表示2级，如：1.1，3表示3级，如：1.1.1"""

        self._roman = roman
        """True表示为罗马数字"""

        self._value=value
        """序号的整数值，如：1.2 => 2，允许修正"""

        self._value_type=value_type
        """表示值的类型，如：一，1，a,aa，A，i，等"""

        self.xobject:Final = xobject
        """原文对象，如果为None，表示为虚构的"""

        self._raw_value=value
        """记录原始的值"""

        self._fixed:bool=False
        """True表示该序号被修正了，如：原文为2，正确的顺序应该是3"""

    
    @property
    def value(self)->int:
        return self._value
    
    @value.setter
    def value(self,v:int):
        #如：可能存在书写错误，如：
        #1.1  =>1 => 1
        #1.2  =>2 => 2
        #1.2  =>2 => 3  修正为3
        #1.3  =>3 => 4  修正为4
        #1.4  =>4 => 5  修正为5
        self._value = v
        self._fixed=True
    
    @property
    def roman(self)->bool:
        return self._roman
    
    @roman.setter
    def roman(self,b:bool):
        """设置是否为罗马序号"""
        if self._roman!=b:
            self._roman = b
            v=self._parse_value(self._parts[-1],roman=b)
            assert v is not None
            self._value_type = v[0]
            self._value = v[1]
            self._raw_value=v[1]
            self._fixed=False
    
    def reset(self):
        """恢复到最初的值"""
        self._value = self._raw_value
        self._fixed = False

    def to_roman(self)->Self|None:
        """
        把i,ii,iii等转换为罗马序号，因为可能被识别为字母序号
        """
        if self.roman:
            #如果已经标记为罗马标题
            return self
        else:
            #支持多级标题
            #i.ii.i => self._parts[-1]
            if not self._is_valid_roman(self._parts[-1]):
                return None
            return self.parse(self.text,xobject=self.xobject,roman=True)

    def copy(self, value: int,*,strict:bool=False) -> Self:
        """根据当前的序号伪造一个其他值的，如：1.1. xxxx, yyyy  1.3. zzzz

        当解析到“yyyy”的时候，可以给其补充一个序号(1.1.，整形)

        value:序号的整形值
        """
        if strict:
            #需要根据value，转换为字符串
            def to_roman(num:int)->str:
                val = [
                    1000, 900, 500, 400,
                    100, 90, 50, 40,
                    10, 9, 5, 4,
                    1
                ]
                syb = [
                    "M", "CM", "D", "CD",
                    "C", "XC", "L", "XL",
                    "X", "IX", "V", "IV",
                    "I"
                ]
                roman_num = ''
                i = 0
                while num > 0:
                    for _ in range(num // val[i]):
                        roman_num += syb[i]
                        num -= val[i]
                    i += 1
                return roman_num

            def to_chinese(num:int)->str:
                if num < 0 or num > 99999:
                    raise ValueError("Number out of range (0-99999)")
                
                chinese_numerals = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九']
                chinese_units = ['', '十', '百', '千', '万']
                
                if num == 0:
                    return chinese_numerals[0]
                
                digits:list[int] = []
                while num > 0:
                    digits.append(num % 10)
                    num = num // 10
                
                result:list[str] = []
                for i in range(len(digits)):
                    digit = digits[i]
                    if digit != 0:
                        result.append(chinese_units[i])
                        result.append(chinese_numerals[digit])
                    else:
                        # Handle zero cases (avoid multiple zeros and trailing zeros)
                        if i > 0 and digits[i-1] != 0 and i < len(digits):
                            result.append(chinese_numerals[0])
                
                # Special case for numbers like 10-19 where we don't need "一十" just "十"
                if len(result) >= 2 and result[-1] == '一' and result[-2] == '十':
                    result.pop()
                
                return ''.join(reversed(result)).replace('零万', '万').replace('零零', '零').rstrip('零')
            
            if self._value_type=='i':
                value_text=to_roman(value).lower()
            elif self._value_type=='I':
                value_text=to_roman(value).upper()
            elif self._value_type=='一':
                value_text=to_chinese(value)
            elif self._value_type=='1':
                value_text=str(value)
            elif re.search(r'[a-z]',self._value_type):
                #a or aa or aaa
                #值应该在1-26
                n= (value-1)//26
                v = (value-1)%26
                c = chr(ord('a')+v)
                value_text=c*(len(self._value_type)+n)
            elif re.search(r'[A-Z]',self._value_type):
                #A or AA or AAA
                n=(value-1)//26
                v=(value-1)%26
                c = chr(ord('A')+v)
                value_text=c*(len(self._value_type)+n)
            else:
                raise ValueError(f'不支持的value_type={self._value_type}')
            parts=list(self._parts)
            #根据当前的值转换为字符串的表示
            parts[-1]=value_text
            text=f'{self._prefix}{'.'.join(parts)}{self._suffix}'
            no = self.parse(text,roman=self.roman)
            assert no is not None
            return no
        else:
            no = self.parse(self.text,roman=self.roman)
            assert no is not None
            no.value = value
            return no

    def is_ancestor(self,child:Self,level:int=1)->bool:
        """判断当前节点是否为child节点的上级，level=1表示上一级，level=2表示上2级"""
        assert level>0
        a = child.get_ancestor(level=level)
        if a is None:
            return False
        if a.text==child.text or a.text2==child.text2:
            return True
        else:
            return False
        
    def get_ancestor(self,level:int=1)->Self|None:
        """得到往上的序号，level=0表示当前，level=1表示上一级"""
        assert level>0
        no = self
        while level>0:
            no = no._upper
            if no is None:
                break
            level-=1
        return no

    @cached_property
    def _upper(self) -> Self | None:
        """获得上一级序号"""
        # 1.1 => 1
        # 1.2.3 => 1.2
        # a.b => a
        # 第一章 => None
        # 一、   => None
        if len(self._parts)>1:
            #如果是罗马数字，不支持多级，如：ii.ii.i
            text=f'{self._prefix}{''.join(self._parts[0:-1])}{self._suffix}'
            #如果是罗马标题，这里就还是作为字母标题，如：
            #i.ii.iv => 'i.ii' （上级标题，无法确定是罗马还是字母）
            #i.iv.i  => 'i.iv' => 'iv'，在宽松的下，可以判断为罗马序号
            no = self.parse(text)
            assert no is not None
            return no
        else:
            return None

    def alike(self, other: Self) -> bool:
        """判断2个序号的模式是否一致，如：1.2和1.3"""
        # 判断序号是否一致
        if self.text==other.text or self.text2 == other.text2:
            return True
        elif self._prefix == other._prefix and self._suffix == other._suffix and self._parts[0:-1] == other._parts[0:-1] and self._value_type == other._value_type:
            # 前后缀和多级的共同部分必须相同
            # 然后再比较值的模式即可
            # 如果是宽松的模式，如：
            # 一、和（一）可以相等？，因为可能书写错误了
            return True
        else:
            return False

    @classmethod
    def _normalize(cls, text: str) -> str:
        # text = re.sub(r'[．]','.',text)
        # 去掉空格
        text = re.sub(r'\s', '', text)
        buf: list[str] = []
        for s in text:
            b, q = strs.to_bq(s)
            buf.append(b)
        return ''.join(buf)


    @classmethod
    def _parse_value(cls, text: str, *, roman: bool = False) -> tuple[str,int]|None:
        # 中文的数字的unicode并不是顺序递增的，不能够直接比较unicode
        if roman:
            # 如果是罗马数字（因为和a-z等冲突，需要特殊处理）
            if re.search(r'[A-Z]',text):
                value_type = 'I'
            else:
                value_type='i'
            return (value_type,cls._parse_roman_numbers(text))
        elif re.fullmatch(r'[0-9]+', text):
            #"1" => 1
            #"01" => 1
            #"001" => 1
            #现在就简单的使用1，因为无法知道一开始的模式了
            return ('1',int(text))
        elif re.fullmatch(r'[〇一二三四五六七八九十零百千]+', text):
            return ('一',cls._parse_chinese_numbers(text))
        elif re.fullmatch(r'[a-zA-Z]+', text):
            #严格的规则如下：
            #a-z => 1-26  aa-zz => 27-52
            #aa-zz => 1-26 aaa-zzz => 27-52
            #aaa-zzz => 1-26 aaaa-zzzz => 27-52
            #需要先判断是否从a开始，还是aa开始，还是aaa开始
            #通常这太难判断，因为总是存在书写错误/遗漏等，而且，如果序号很多的，很难区分
            #如：一共有52项，就需要a-zz
            #可能作者的意图是：a-z 这26项在一起，aa-zz这26项重新排序
            if re.search(r'[A-Z]',text):
                #使用searc，目的是为了支持书写错误，可能Aa => AA 
                value_type='A'*len(text)
            else:
                value_type='a'*len(text)
            #aa现在理解为a,aa-zz => a-z
            return (value_type,cls._parse_alpha_numbers(text[-1]))
        elif re.fullmatch(r'[\u2460-\u2473]', text):
            # ①-⑳
            return ('\u2460',cls._parse_symbol_numbers(text, 0x2460))
        elif re.fullmatch(r'[\u2474-\u2487]', text):
            # ⑴-⒇
            return ('\u2474',cls._parse_symbol_numbers(text, 0x2474))
        else:
            # 不能够解析的，就全部返回-1?
            # 表示无序的序号？
            return None

    @classmethod
    def _parse_chinese_numbers(cls, text: str) -> int:
        values = {
            '一': 1,
            '二': 2,
            '三': 3,
            '四': 4,
            '五': 5,
            '六': 6,
            '七': 7,
            '八': 8,
            '九': 9,
            #--------
            '十':10,
            '十一':11,
            '十二':12,
            '十三':13,
            '十四':14,
            '十五':15,
            '十六':16,
            '十七':17,
            '十八':18,
            '十九':19
        }

        def parse(s: str) -> int:
            return values[s]

        # 一 => 1
        # 十 => 10
        #十二 => 12
        # 九十九 => 99
        # 一百零一 => 101
        # 一百一十二 => 112
        # 一千零一 => 1001
        # 一千零一十一 => 1011
        # 一千二百三十 => 1230
        # 一千二百零三 => 1203
        text = re.sub(r'[零〇]', '', text)
        m = re.fullmatch(
            r'((?P<a>.+)千)?((?P<b>.+)百)?((?P<c>.+)十)?(?P<d>.+)?', text)
        assert m is not None

        a = m.group('a')
        b = m.group('b')
        c = m.group('c')
        d = m.group('d')
        value = 0
        for i, v in enumerate([d, c, b, a]):
            if v:
                value += parse(v)*(10**i)
        return value

    @classmethod
    def _parse_symbol_numbers(cls, text: str, start: int) -> int:
        # start表示1对应的unicode的值
        return ord(text) - start + 1

    @classmethod
    def _parse_roman_numbers(cls, text: str) -> int:
        # 现在要求先对输入的json进行normalize处理，所以已经不存在特殊的字符
        # 否则：0x2160-0x216f, 0x2170-0x217f 看起来都是罗马数字
        pairs = {'M': 1000, 'D': 500, 'C': 100,
                 'L': 50, 'X': 10, 'V': 5, 'I': 1}
        text = text.upper()

        def get_char_value(c: str) -> int:
            return pairs[c]
        value = 0
        for i in range(len(text)):
            n = get_char_value(text[i])
            # If the next place holds a larger number, this value is negative
            if i + 1 < len(text) and get_char_value(text[i + 1]) > n:
                value -= n
            else:
                value += n
        return value

    @classmethod
    def _parse_alpha_numbers(cls, text: str) -> int:
        # a => 1,z=26
        # aa => 27, zz=52

        # a-z or aa-zz or aaa-zzz

        # TODO 这里转换可以放宽一些了
        m = re.fullmatch(r'(?:.+[.])*(?P<value>(?P<a>[a-z])(?P=a)*)[.]?', text)
        m = re.fullmatch(r'([a-z])\1*', text)
        if m is not None:
            a = ord(text[0])-ord('a')+1
            return (len(text)-1)*26+a

        # A-Z or AA-ZZ or AAA-ZZZ
        m = re.fullmatch(r'([A-Z])\1*', text)
        if m is not None:
            a = ord(text[0])-ord('A')+1
            return (len(text)-1)*26+a

        # 不支持的格式，如：ab
        return -1
        # return 26*(len(text)-1)+(ord(text[0].lower())-ord('a')+1)
    
    @classmethod
    def _is_valid_roman(cls,text:str,*,case_sensitive:bool=False)->bool:
        if not case_sensitive:
            text = text.upper()
        pattern = r'^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$'
        return re.fullmatch(pattern, text) is not None

    @classmethod
    def parse(cls,text:str,xobject:'XText|None'=None,roman:bool=False) -> Self | None:
        #放宽了，支持书写错误，使用归一化的值
        text2 = cls._normalize(text)
        m = cls._value_pattern.search(text2)
        if m is None:
            #完善"_value_pattern"
            cls._logger.warning('不支持的序号:%s',text2)
            return None
        start, end = m.span()
        prefix = text2[0:start]
        suffix = text2[end:]
        parts =  text2[start:end].split('.')
        #只需要最后一部分
        v = cls._parse_value(parts[-1],roman=roman)
        if v is None:
            #完善_parse_value()
            cls._logger.warning('不支持的序号:%s',text2)
            return None
        value_type = v[0]
        value = v[1]
        return cls(text,text2=text2,xobject=xobject,roman=roman,prefix=prefix,suffix=suffix,parts=parts,value=value,value_type=value_type)


    





class MarkdownBuilder:
    def __init__(self):
        super().__init__()
    
    def build(self,node:XNode):
        buf:list[str]=[]

    
    def build_node(self,node:XNode):
        buf:list[str]=[]
        if node.is_title():
            a='#'*node.level
            buf.append(f'{a} {node.title.text}')
        elif node.is_text():
            pass
        elif node.is_figure():
            if node.figure.filename:
                #表示为合并后的图片
                pass
            else:
                #没有合并，直接使用第一个对象
                #或者使用多个图片输出？
                buf.append(node.figure.objects[0].markdown())
            buf.append('')
        elif node.is_formula():
            if node.formula.filename:
                #表示为合并后的
                pass
            else:
                pass

            #或者输出latex
            for obj in node.formula.objects:
                #可以输出图片，或者输出latex?
                buf.append(obj.markdown())
            pass
        elif node.is_table():
            #输出html的结构
            buf.append(node.table.html())
            pass
        else:
            pass