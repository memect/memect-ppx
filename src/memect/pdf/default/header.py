

from memect.base.bbox import BBox

from ..base import KDocument, KObject


class PageHeaderParser:
    def __init__(self):
        super().__init__()
    
    def parse(self,doc:KDocument):
        for page in doc.working_pages:
            if page.number==1:
                #第一页多数情况都没有页眉，如果严格一点，可以和第二页的对比一下？
                #continue
                pass 
            i=0
            header_objects:list[KObject]=[]
            while i<len(page.objects):
                obj = page.objects[i]
                #TODO 如果是研报且为首页，有些正常的内容会被识别为页眉
                if obj.vobject and obj.vobject.is_header():
                    header_objects.append(obj)
                    del page.objects[i]
                else:
                    i+=1
            
            if len(header_objects)>0:
                bbox = BBox.join2(header_objects)
                bbox = page.bbox.adjust(y0=bbox.y0)
                bbox.get(page.objects,ratio=0.5,remove=True)

