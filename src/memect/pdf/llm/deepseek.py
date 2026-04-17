import json
import logging
import re
from typing import Any, Final, Mapping, Sequence
import weakref

from pydantic import Field

from memect.base.bbox import BBox
from memect.base.debug import XDebugger
from memect.base.utils import MyBaseModel
from memect.pdf.base import KDocument, KMarkdown, KPage, KTable
from .llm import Model, ModelArgs, Task


def create_model_args(args: Mapping[str, Any] | ModelArgs) -> ModelArgs:
    default = ModelArgs(
        name="deepseek",
        base_url="http://127.0.0.1:9654/v1",
        api_key="",
        model="deepseek-ocr-2",
        max_tokens=8192 - 1200,
        temperature=0.0,
        prompt="<|grounding|>Convert the document to markdown.",
        extra_body={
            "skip_special_tokens": False,
            "vllm_xargs": {
                # 官网写20
                "ngram_size": 20,
                "window_size": 90,
                "whitelist_token_ids": [128821, 128822],
            },
        },
        max_image_size=(2000, 2000),
        image_format="png",
    )
    # exclude_none=True??
    if isinstance(args, ModelArgs):
        return default.model_copy(update=args.model_dump())
    else:
        a = default.model_dump()
        a.update(args)
        return ModelArgs.model_validate(a)
    

def create_model(args: Mapping[str, Any] | ModelArgs) -> Model:
    return Model(DeepseekTask,create_model_args(args))

class DeepseekArgs(MyBaseModel):
    name:str='deepseek'
    model:ModelArgs|Mapping[str,Any]=Field(default_factory=create_model_args)


class DeepseekTask(Task):
    #以后使用free-threaded，可以把parse_page()的操作放在这里，速度更快，因为是使用多核心
    pass


"""
<|ref|>text<|/ref|><|det|>[[40, 734, 333, 760]]<|/det|>
2021-08-06 发布

<|ref|>text<|/ref|><|det|>[[632, 734, 928, 760]]<|/det|>
2021-11-06 实施
"""

class Deepseek:
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger = XDebugger(f'{__module__}.{__qualname__}')
    def __init__(self,args:DeepseekArgs|Mapping[str,Any]|None=None):
        super().__init__()
        args = DeepseekArgs.create(args)
        self._name:Final=args.name
        self._model:Final= create_model(args.model)
        self._llm_key=f'cache/{self._name}'
        self._finalizer = weakref.finalize(self,self._close,self._model)
    
    @classmethod
    def _close(cls,model:Model):
        model.close()
    
    def close(self):
        if self._finalizer.alive:
            self._finalizer()

    def parse(self, doc: KDocument):
        doc.all_as_images()
        self._model.parse(self._llm_key,doc)
        for page in doc.working_pages:
            self._parse_page(page)

    def _parse_page(self,page:KPage):
        debugger:Final=self._debugger.bind(page=page.number)
        #必须存在，如果不存在，不应该执行到这里
        text:str= page.cache[self._llm_key]["0"]
        #获得后就可以释放了
        del page.cache[self._llm_key]
        if debugger.allow('info'):
            with debugger.group('llm'):
                print(text)

        def parse_bboxes(s: str) -> list[BBox]:
            # 解析是归一化的，需要转换为相对当前图片的
            try:
                #模型遇到需要旋转的页面，一样可以正常识别，返回的bbox是相对输入的页面，所以溯源显示是正确的
                #只是如果能够告知该页面需要旋转
                #这里返回的bbox都是相对输入的图片(应用了page.rotation后的)，而page.bbox已经为应用page.rotation的
                #所以不需要做任何特别的处理
                width = int(page.width)
                height = int(page.height)
                bboxes: list[BBox] = []
                objs = json.loads(s)
                #objs = ast.literal_eval(s)
                
                for obj in objs:
                    x0, y0, x1, y1 = obj
                    x0 = int(x0 / 999 * width)
                    x1 = int(x1 / 999 * width)
                    y0 = int(y0 / 999 * height)
                    y1 = int(y1 / 999 * height)
                    #原点从左上角转换为左下角
                    b=BBox(x0,height-y1,x1,height-y0)
                    x_bbox = page.bbox.intersect(b)
                    if b.is_valid() and x_bbox is not None and x_bbox.is_valid():
                        #确保在页面范围内
                        bboxes.append(x_bbox)
                    else:
                        self._logger.warning(
                            "获得错误的bbox,page=%s,text=%s,bbox=%s",page.number, obj, (x0, y0, x1, y1)
                        )
                return bboxes
            except Exception:
                #使用warning就可以了？
                self._logger.warning("解析bbox失败,page=%s,text=%s",page.number,s,exc_info=True)
                return []

        def normalize_type(label: str) -> str:
            if label in ("image",):
                return "figure"
            else:
                return label

        def parse(text:str):
            #官方代码就有这一行，需要先替换
            text = text.replace("\\coloneqq", ":=").replace("\\eqqcolon", "=:")
            pattern = r'(<\|ref\|>(.*?)<\|/ref\|><\|det\|>(\s*\[\s*\[\s*[0-9]+\s*,\s*[0-9]+\s*,\s*[0-9]+\s*,\s*[0-9]+\s*\](?:\s*,\s*\[\s*[0-9]+\s*,\s*[0-9]+\s*,\s*[0-9]+\s*,\s*[0-9]+\s*\])*?\s*\]\s*)?<\|/det\|>)'
            last_match:re.Match[str]|None=None
            for m in re.finditer(pattern,text):
                if last_match is None:
                    start=0
                else:
                    start = last_match.end()
                #m=('','<><>','image','[[0,0,0,0],[0,0,0,0]]')
                parse_object(last_match,text[start:m.start()])
                last_match = m
            
            parse_object(last_match,text[last_match.end() if last_match else 0:])
        

        def adjust_bbox(bbox:BBox)->BBox:
            #TODO 严格的就是再判断是否和其他区域有相交，没有相交的就可以
            #现在简单的变大一点，如果图片很大的，可以变大更多，如果图片很小的，就不要了？
            if bbox.width<20:
                dx=1
            else:
                dx=2
            if bbox.height<20:
                dy=1
            else:
                dy=2
            bbox = bbox.large
            return bbox.expand(dx=dx,dy=dy).intersect(page.bbox) or bbox

        def parse_object(m:re.Match[str]|None,text:str):
            text=text.strip()
            if not m:
                if text:
                    #使用一个无效的BBox，仍然保留文本
                    page.objects.append(KMarkdown(page,BBox(0,0,0,0).to_quad(),text=text))
                return
            
            type_ = normalize_type(m.group(2))
            bboxes = parse_bboxes(m.group(3))           
            if len(bboxes)==0:
                self._logger.warning('第%s页，没有获得bbox:%s',page.number,m.group())
                page.objects.append(KMarkdown(page,BBox(0,0,0,0).to_quad(),text=text))
            else:
                if len(bboxes)>1:
                    self._logger.warning('第%s页，返回的bboxes有多个:%s',page.number,bboxes)
                if type_ == "figure":
                    #有些BBox可能小了一点，如：没有边界，可以稍微大一点
                    page.make_figure(adjust_bbox(bboxes[0]).to_quad(),add=True)
                elif type_ == "table":
                    #获得的是html，然后可以解析为cells
                    table = KTable(page,adjust_bbox(bboxes[0]).to_quad())
                    try:
                        table.fill_html(text)
                    except Exception:
                        self._logger.exception('解析表格出现异常')
                    if table.row_num==0 or table.col_num==0:
                        #如果无法生成表格，返回markdown？使用图片表示
                        page.make_figure(table.quad,add=True)
                    else:
                        page.objects.append(table)
                else:
                    #TODO 这个只是暂时处理，模型升级就可以去掉了
                    cases = [case1]
                    for case in cases:
                        if case(text,bboxes):
                            break
                    else:
                        #可能返回多个BBox，如：
                        #---b1-------
                        #--b2--   <图片> 
                        #所以使用合并后的bbox
                        md = KMarkdown(page,BBox.join(bboxes).to_quad(),text=text)
                        page.objects.append(md)


        def case1(text:str,bboxes:Sequence[BBox])->bool:
            if len(bboxes)!=2:
                return False
            #可能有多个bboxes，如：
            #-------b1------
            #--b2--      有图片等
            b1 = bboxes[0]
            b2 = bboxes[1]
            if b2.x0<b1.x1:
                return False
            
            #local/cases/pptx/Reducto_企业级AI文档基础设施投资机遇.pdf 第12页
            #----b1---      --b2--   两段间距过大的文本
            lines = text.splitlines(True)
            if len(lines)<2:
                return False
            
            t1 = ''.join(lines[0:-1]).strip()
            t2 = lines[-1].strip()

            self._logger.warning('第%s页，一个文本返回2个bbox，所以切成2个返回:t1=%s,t2=%s',page.number,t1,t2)
            page.objects.append(KMarkdown(page,b1.to_quad(),text=t1))
            page.objects.append(KMarkdown(page,b2.to_quad(),text=t2))
            return True


        
        parse(text)

        if debugger.allow('draw'):
            page.draw(('objects',page.objects),dir=f'debug/{self._name}')

