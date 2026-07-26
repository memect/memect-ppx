
from dataclasses import KW_ONLY, dataclass, field
import logging
from typing import Sequence

from memect.base import lists
from memect.base.debug import XDebugger
from memect.pdf.base import KChar, KObject, KPDFFigure, KTable, VObject


@dataclass
class Result:
    _:KW_ONLY
    pdf_chars:Sequence[KChar]=field(default_factory=tuple)
    """pdf的字符"""
    ocr_chars:Sequence[KChar]=field(default_factory=tuple)
    """ocr识别的字符"""
    chars:Sequence[KChar]=field(default_factory=tuple)
    """pdf+ocr识别的字符，且去掉ocr图片上的字符"""
    removed_chars:Sequence[KChar]=field(default_factory=tuple)
    pdf_figures:Sequence[KPDFFigure]=field(default_factory=tuple)
    removed_pdf_figures:Sequence[KPDFFigure]=field(default_factory=tuple)
    vobjects:Sequence[VObject]=field(default_factory=tuple)
    objects:Sequence[KObject]=field(default_factory=tuple)
    remain_objects:Sequence[KObject|VObject]=field(default_factory=tuple)


class TableFiller:
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger=XDebugger(f'{__module__}.{__qualname__}')
    
    def __init__(self):
        super().__init__()
    
    def _get_objects(self,vobj:VObject,page_pdf_chars:list[KChar],page_pdf_figures:list[KPDFFigure],remove:bool=False)->Result:
        bbox = vobj.bbox
        #page = vobj.page
        pdf_chars = bbox.get(page_pdf_chars,ratio=0.7,remove=remove)
        pdf_figures = bbox.get(page_pdf_figures,ratio=0.7,remove=remove)
        ocr_chars = vobj.ocr_chars
        #TODO 现在这些先全部作为图片处理
        #如果将来需要再进一步解析，可以在这里处理，也可以在外边先批量处理，存储下来再使用
        vobjects = [v for v in vobj.vobjects if v.is_figure() or v.is_formula() or v.is_chart() or v.is_code()]
        chars = pdf_chars+ocr_chars
        #有些图片有文字，可能被ocr识别为文字，去掉
        #但如果图片是一张大图，如：整个表格就是一个图片
        removed_pdf_figures:list[KPDFFigure]=lists.remove2(pdf_figures,lambda i,objs:objs[i].bbox.area/vobj.bbox.area>0.7)
        removed_chars:list[KChar]=[]
        for objs in [pdf_figures,vobjects]:
            for obj in objs:
                removed_chars.extend(obj.bbox.get(chars,ratio=0.7,remove=True))
        
        return Result(
            pdf_chars=pdf_chars,
            ocr_chars=ocr_chars,
            pdf_figures=pdf_figures,
            chars=chars,
            vobjects=vobjects,
            removed_chars=removed_chars,
            removed_pdf_figures=removed_pdf_figures
        )
    
    def get_objects(self,vobjs:Sequence[VObject])->Result:
        assert len(vobjs)>0
        page = vobjs[0].page
        if len(vobjs)==1:
            return self._get_objects(vobjs[0],page.pdf_chars,page.pdf_figures,remove=False)
        
        #为了避免多个对象可能使用了重叠的字符和图片，这里复制然后删除用过的
        pdf_chars=list(page.pdf_chars)
        pdf_figures = list(page.pdf_figures)
        results:list[Result]=[]
        for vobj in vobjs:
            results.append(self._get_objects(vobj,pdf_chars,pdf_figures,remove=True))
        
        return Result(
            pdf_chars=lists.join([r.pdf_chars for r in results]),
            ocr_chars=lists.join([r.ocr_chars for r in results]),
            pdf_figures=lists.join([r.pdf_figures for r in results]),
            chars=lists.join([r.chars for r in results]),
            vobjects=lists.join([r.vobjects for r in results]),
            removed_chars=lists.join([r.removed_chars for r in results]),
            removed_pdf_figures=lists.join([r.removed_pdf_figures for r in results])
        )

    


    def fill(self,table:KTable,result:Result|None=None)->Result:
        if result is None:
            assert table.vobject is not None
            #debugger = self._debugger.bind(page=table.page.number)
            vobj = table.vobject
            #page = table.page
            result = self.get_objects([vobj])
        objs:list[KObject|VObject]=[]
        objs.extend(result.chars)
        if len(result.pdf_figures)>0:
            #如果有pdf图片，就仅仅使用pdf的图片？即使还有其他，如：公式
            objs.extend(result.pdf_figures)
        else:
            objs.extend(result.vobjects)
        table.fill_objects(objs)

        #获得剩余的对象
        result.remain_objects=tuple(objs)
        return result
        