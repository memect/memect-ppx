import logging
import multiprocessing as mp
import time
from concurrent.futures import ProcessPoolExecutor
from types import TracebackType
from typing import Any, ClassVar, Iterable, Mapping, Self, Sized, override

from pydantic import Field

from memect.base import utils
from memect.base.config import MPInit, get_settings
from memect.base.task import Runner, StoppedError, Task
from memect.base.utils import MyBaseModel, safe_shutdown

from .base import Backend, KDocument, KDocumentFactory
from .default.parser import DefaultParser, DefaultParserArgs
from .llm.deepseek import Deepseek, DeepseekArgs
from .llm.glm import GLM, GLMArgs
from .llm.paddle import Paddle, PaddleArgs
from .model import ModelManager, ModelManagerArgs
from .pdf2image import Pdf2Image, Pdf2ImageArgs
from .watermark import Watermark


class ParserArgs(MyBaseModel):
    pdf2image: Pdf2ImageArgs = Field(default_factory=Pdf2ImageArgs)
    deepseek: DeepseekArgs = Field(default_factory=DeepseekArgs)
    paddle: PaddleArgs = Field(default_factory=PaddleArgs)
    glm: GLMArgs = Field(default_factory=GLMArgs)
    default: DefaultParserArgs = Field(default_factory=DefaultParserArgs)


class Parser:
    """多数情况下，全局只使用一个即可"""

    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self,manager:ModelManager|ModelManagerArgs|Mapping[str,Any]|None=None,args: ParserArgs | Mapping[str, Any] | None = None):
        super().__init__()
        self._close_manager:ModelManager|None=None
        if isinstance(manager,ModelManager):
            self._manager = manager
        else:
            #如果没有提供，那么就创建一个
            self._manager = ModelManager(manager)
            self._close_manager=self._manager

        if args is None:
            args = get_settings('pdf_parser')
        args = ParserArgs.create(args)

        self._pdf2image = Pdf2Image(args.pdf2image)
        self._deepseek = Deepseek(args.deepseek)
        self._paddle = Paddle(self._manager,args.paddle)
        self._glm = GLM(self._manager,args.glm)
        self._default = DefaultParser(self._manager,args.default)
        self._watermark = Watermark()

    def parse(self, doc: KDocument, *, runner: Runner | None = None):
        try:

            def check_running(name: str):
                if runner is not None and runner.is_stopped():
                    raise StoppedError(f"任务已经被停止，不再执行:{name}")

            if not doc.pagenos:
                # 没有任何需要执行的页码，就不需要执行操作了？
                if doc.params.api:
                    # 表示为api调用，需要输出一个zip文件
                    # 在这里做比在loop中执行更好一点，特别是大文件压缩，因为解析将来可以在free-threaded中执行
                    doc.make_zip()
                return
            
            #如果需要支持去掉水印，需要在这里执行
            check_running('remove_watermark')
            if doc.is_pdf() and doc.params.remove_watermark:
                self._watermark.clean(doc)
            else:
                #如果图片有水印，需要去掉非常复杂
                pass
            check_running("pdf2image")
            if doc.is_pdf():
                self._pdf2image.parse(doc)
            else:
                # 图片，忽略
                pass

            check_running("parse")
            backend = doc.params.backend
            if backend == Backend.DEEPSEEK:
                self._deepseek.parse(doc)
            elif backend == Backend.PADDLE:
                self._paddle.parse(doc)
            elif backend == Backend.GLM:
                self._glm.parse(doc)
            elif backend == Backend.DEFAULT:
                # 先判断页面使用什么方式解析
                self._default.parse(doc)
            else:
                raise ValueError(f"不支持的backend={backend}")

            #解析完毕，按要求输出
            if doc.params.pptx:
                from .pptx import PptxBuilder
                #pptx总是按页渲染，即使要求解析tree
                data=PptxBuilder().build(doc)
                doc.write('doc.pptx',data)
            
            if doc.params.docx:
                from .docx import DocxBuilder
                data=DocxBuilder().build(doc)
                doc.write('doc.docx',data)

            if doc.params.html:
                #一个是给开发用的
                #一个是给用户可以直接看的，纯静态的
                #TODO 
                doc.write('doc.html','<html></html>')

            if doc.params.markdown:
                doc.write("doc.md", doc.markdown())
                
            if doc.params.doc_json:
                doc.write("doc.json", doc.jsonify())

            check_running("makezip")
            if doc.params.api:
                # 表示为api调用，需要输出一个zip文件
                # 在这里做比在loop中执行更好一点，特别是大文件压缩，因为解析将来可以在free-threaded中执行
                doc.make_zip()
        finally:
            del doc

    def new_runner(self, doc: KDocument) -> Runner:
        return _ParseRunner(self, doc)

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ):
        self._pdf2image.close()
        if self._close_manager:
            self._close_manager.close()
        del self._pdf2image
        del self._deepseek
        del self._glm
        del self._paddle
        del self._default
        del self._close_manager
        del self._manager


    _mp_instance:ClassVar[Self|None]=None

    @classmethod
    def _mp_parse(cls,factory:KDocumentFactory):
        assert cls._mp_instance is not None
        doc = factory()
        cls._mp_instance.parse(doc)
        

    @classmethod
    def _mp_init(cls,manager:Any,parser:Any):
        assert cls._mp_instance is None
        cls._mp_instance = cls(manager=manager,args=parser)
        
    @classmethod
    def _mp_exit(cls):
        cls._mp_instance=None


    @classmethod
    def batch(cls,docs:Iterable[KDocumentFactory],*,max_workers:int=0,timeout:float|None=None):
        """
        批量执行，这个方法仅仅合适在命令行下执行，或者在一个新的进程执行
        args:
        docs:
        max_workers:
        timeout: 单个文件的最大解析时间
        """
        timer = utils.Timer.start()
        #获得当前进程的设置，确保在所有进程的参数一致
        parser_args = get_settings('pdf_parser')
        manager_args = get_settings('model_manager')
        n=0
        total=None
        if isinstance(docs,Sized):
            total=len(docs)
        if max_workers==0:
            #在当前进程执行，方便测试
            with cls(manager=manager_args,args=parser_args) as parser:
                for doc in docs:
                    parser.parse(doc())
                    n+=1
                    cls._logger.info('第[%s/%s]个解析成功',n,total)
                cls._logger.info('结束解析，共解析：%s，耗时:%.3f',n,timer.elapsed())
        else:
            mp_context = mp.get_context('spawn')
            mp_init = MPInit()
            mp_init.set_fn(cls._mp_init,manager_args,parser_args)
            try:
                with ProcessPoolExecutor(max_workers=max_workers,mp_context=mp_context,initializer=mp_init) as executor:
                    for _ in executor.map(cls._mp_parse,docs):
                        n+=1
                        cls._logger.info('第[%s/%s]个解析成功',n,total)
                    cls._logger.info('结束解析，共解析：%s，耗时:%.3f',n,timer.elapsed())
                    safe_shutdown(executor)
            finally:
                pass
        
        cls._logger.info('完成释放所有资源')
        

    
    @classmethod
    def _kill_mp_processes(cls,kill:bool,timeout:float|None=10):
        #每次返回一个新的
        mp_context = mp.get_context('spawn')
        children = mp_context.active_children()
        cls._logger.info('start %s processes,size=%s','kill' if kill else 'terminate',len(children))
        for p in children:
            cls._logger.info('start %s process=%s','kill' if kill else 'terminate',p.pid)
            if kill:
                p.kill()
            else:
                p.terminate()
        
        start_time = time.monotonic()
        while timeout is None or time.monotonic()-start_time<timeout:
            i=0
            while i<len(children):
                p = children[i]
                if not p.is_alive():
                    #如果进程退出后被其他方式读取了wait的状态，这个总是返回True的
                    del children[i]
                else:
                    i+=1
            if not children:
                break
            else:
                time.sleep(0.5)
        
        cls._logger.info('end %s processes,size=%s','kill' if kill else 'terminate',len(mp_context.active_children()))
    

class _ParseRunner(Runner):
    def __init__(self, parser: Parser, doc: KDocument):
        super().__init__()
        self._parser = parser
        self._doc = doc

    @override
    def _run(self, task: Task):
        assert self._doc is not None
        assert self._parser is not None
        try:
            self._parser.parse(self._doc, runner=self)
            return self._doc.zip_file
        finally:
            # 解除引用
            self._doc = None
            self._parser = None

