
from math import e

from memect.base.bbox import BBox

from ..base import KDocument, KObject


class PageFooterParser:
    def __init__(self):
        super().__init__()
    
    def parse(self,doc:KDocument):
        for page in doc.working_pages:
            i=0
            footer_objects:list[KObject]=[]
            while i<len(page.objects):
                obj = page.objects[i]
                if obj.vobject and obj.vobject.is_footer():
                    #TODO 有时候会把页眉对象识别为页脚
                    if obj.bbox.y1<=min(200,page.bbox.cy):
                        footer_objects.append(obj)
                    else:
                        #表示为错误的页脚对象
                        pass
                    del page.objects[i]
                else:
                    i+=1
            if len(footer_objects)>0:
                bbox = BBox.join2(footer_objects)
                bbox = page.bbox.adjust(y1=bbox.y1)
                bbox.get(page.objects,ratio=0.5,remove=True)
        pass
