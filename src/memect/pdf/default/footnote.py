

from ..base import KDocument


class PageFootnoteParser:
    def __init__(self):
        super().__init__()
    
    def parse(self,doc:KDocument):
        for page in doc.working_pages:
            i=0
            while i<len(page.objects):
                obj = page.objects[i]
                if obj.vobject and obj.vobject.is_footnote():
                    del page.objects[i]
                else:
                    i+=1
        pass
