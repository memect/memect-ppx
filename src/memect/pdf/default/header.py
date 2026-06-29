

from memect.base.bbox import BBox

from ..base import KDocument, KObject


class PageHeaderParser:
    def __init__(self):
        super().__init__()
    
    def parse(self,doc:KDocument):
        for page in doc.working_pages:
            i=0
            header_objects:list[KObject]=[]
            while i<len(page.objects):
                obj = page.objects[i]
                if obj.vobject and obj.vobject.is_header():
                    header_objects.append(obj)
                    del page.objects[i]
                else:
                    i+=1
            
            if len(header_objects)>0:
                bbox = BBox.join2(header_objects)
                bbox = page.bbox.adjust(y0=bbox.y0)
                bbox.get(page.objects,ratio=0.5,remove=True)

