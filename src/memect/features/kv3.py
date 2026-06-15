
from memect.pdf.base import KDocument, KTable, KText, KTextline


class Feature:
    def __init__(self):
        super().__init__()
    
    def parse(self,doc:KDocument):
        for page in doc.working_pages:
            texts:list[KText]=[]
            for obj in page.objects:
                if isinstance(obj,KText) and len(obj.objects)>0 and len(obj.objects)==len(obj.chars):
                    #且必须是按顺序从上到下
                    texts.append(obj)
                elif isinstance(obj,KTable):
                    #已经被识别为表格，但是表格解析不一定足够准确，如：
                    #[k][指][v]  => 因为“指”粘连在一起，没有分开
                    pass
                else:
                    pass
        
        #到了这里，定位了区域，然后解析为无边框表格
        #kv： 使用无边框解析
        #k[a]v: a表示“指，：，是”等，有些粘连在一起，还需要切割一下
        cells=[] #使用模型获得cells
        #使用无边框解析即可
    
    def _parse_texts(self,texts:list[KText]):

        def split(line:KTextline):
            chars = line.chars
            #根据间距划分，可以设置得大一些，避免划分错误

        lines:list[KTextline]=[]
        for text in texts:
            lines.extend(text.lines)
        
        for line in lines:
            spans = split(line)
        
        #如果出现这样
        #[span1]----[span2]
        #[span1]----[span2]
        #           [span3]
        #[span1]----[span2]
        #[span1]    [span2]

        #如果是pdf，还可以使用书写顺序，注意：不一定100%按顺序，pdf的制作工具也可能存在bug，或者人为后续修改来pdf，如：
        #[c1,c2,c3]-------[c4,c5]
        #[c6]

        #顺序应该为：[c1,c2,c3,c6]，然后[c4,c5]，c6的书写顺序错误了