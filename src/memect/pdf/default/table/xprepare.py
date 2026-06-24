

from memect.pdf.base import KDocument


class PreProcessor:
    """为了后续的跨页表格合并，对表格进行预处理，也就是考虑跨页/跨栏上下关联的内容，是否需要理解为表格，调整表格结构一致等"""
    def __init__(self):
        super().__init__()
    
    def parse(self,doc:KDocument):
        
        #有边框场景：
        #---t1---  只有一行一列，被识别为普通文本
        #---------- 跨页
        #---t2---  表格，t1需要理解为表格，否则就无法跨页合并2个表格

        #反过来，t1识别为表格，t2为最后一行，没有识别为表格而是普通文本，

        #无边框表格
        #c1|c2|c3    => 被识别为1*3
        #------------跨页
        #  |c4|c5    => 被识别为1*2

        #这两个表格将无法跨页合并，需要先调整t1=1*3，t2=1*3

        #如何调整2个跨页无边框表格
        #
        for page in doc.working_pages:
            #同页跨栏
            #跨页跨栏
            #跨页不跨栏
            objects = page.objects
            
            pass
        pass