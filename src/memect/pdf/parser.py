import logging
import multiprocessing as mp
from threading import Thread
import time
from concurrent.futures import Executor, ProcessPoolExecutor
from types import TracebackType
from typing import (
    Any,
    ClassVar,
    Final,
    Iterable,
    Mapping,
    Self,
    Sized,
    override,
)
import weakref

from pydantic import Field

from memect.base import utils
from memect.base.config import MPInit, get_settings
from memect.base.task import Runner, StoppedError, Task
from memect.base.utils import MyBaseModel, SafeExecutor
from memect.pdf.x.xtree import XTreeParser, XTreeParserArgs

from .base import Backend, KDocument, KDocumentFactory, ParseMode
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
    tree: XTreeParserArgs = Field(default_factory=XTreeParserArgs)


class Parser:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(
        self,
        manager: ModelManager | ModelManagerArgs | Mapping[str, Any] | None = None,
        args: ParserArgs | Mapping[str, Any] | None = None,
    ):
        super().__init__()
        self._close_manager: ModelManager | None = None
        if isinstance(manager, ModelManager):
            self._manager = manager
        else:
            # 如果没有提供，那么就创建一个
            self._manager = ModelManager(manager)
            self._close_manager = self._manager

        if args is None:
            args = get_settings("pdf_parser")
        args = ParserArgs.create(args)
        self._pdf2image = Pdf2Image(args.pdf2image)
        self._deepseek = Deepseek(args.deepseek)
        self._paddle = Paddle(self._manager, args.paddle)
        self._glm = GLM(self._manager, args.glm)
        self._default = DefaultParser(self._manager, args.default)
        self._watermark = Watermark()
        #==============
        self._tree_parser = XTreeParser(args.tree)

    def parse(self, doc: KDocument, *, runner: Runner | None = None):
        #TODO 暂时如此设置
        doc.manager = self._manager
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

            # 如果需要支持去掉水印，需要在这里执行
            check_running("remove_watermark")
            if doc.is_pdf() and doc.params.remove_watermark:
                self._watermark.clean(doc)
            else:
                # 如果图片有水印，需要去掉非常复杂
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
                self._default.parse(doc)
            else:
                raise ValueError(f"不支持的backend={backend}")
            

            if doc.params.mode==ParseMode.TREE:
                self._tree_parser.parse(doc)

            # 解析完毕，按要求输出
            if doc.params.pptx:
                from memect.pptx import PPTXBuilder
                # pptx总是按页渲染，即使要求解析tree
                data = PPTXBuilder().build(doc)
                doc.write("doc.pptx", data)

            if doc.params.docx:
                from memect.docx.builder import DocxBuilder
                data = DocxBuilder().build(doc)
                doc.write("doc.docx", data)

            if doc.params.html:
                from .html import HtmlRenderer
                doc.write("doc.html",HtmlRenderer().render(doc))

            if doc.params.markdown:
                doc.write("doc.md", doc.markdown())
                if doc.tree is not None:
                    doc.write('tree.md',doc.tree.markdown())

            if doc.params.doc_json:
                doc.write("doc.json", doc.jsonify())

            check_running("makezip")
            if doc.params.api:
                # 表示为api调用，需要输出一个zip文件
                # 在这里做比在loop中执行更好一点，特别是大文件压缩，因为解析将来可以在free-threaded中执行
                doc.make_zip()

            doc.write("state.json", doc.state)
            self._logger.info("state=%s", doc.state)
        finally:
            del doc

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ):
        # self._pdf2image.close()
        if self._close_manager:
            self._close_manager.close()
        del self._pdf2image
        del self._deepseek
        del self._glm
        del self._paddle
        del self._default
        del self._close_manager
        del self._manager

    _mp_instance: ClassVar[Self | None] = None

    @classmethod
    def _mp_parse(cls, factory: KDocumentFactory):
        assert cls._mp_instance is not None
        doc = factory()
        cls._mp_instance.parse(doc)

    @classmethod
    def _mp_init(cls, manager: Any, parser: Any, stopped: Any):
        assert cls._mp_instance is None
        if stopped is not None:

            def check():
                while True:
                    if stopped.value:
                        if cls._mp_instance is not None:
                            cls._mp_instance.__exit__(None, None, None)
                            cls._mp_instance = None
                        break
                    else:
                        time.sleep(0.01)

            Thread(target=check, daemon=True).start()
        cls._mp_instance = cls(manager=manager, args=parser)

    @classmethod
    def _mp_exit(cls):
        cls._mp_instance = None

    @classmethod
    def batch(
        cls,
        docs: Iterable[KDocumentFactory],
        *,
        max_workers: int = 0,
        timeout: float | None = None,
    ):
        """
        批量执行，这个方法仅仅合适在命令行下执行，或者在一个新的进程执行
        args:
        docs:
        max_workers:
        timeout: 单个文件的最大解析时间
        """
        timer = utils.Timer.start()
        # 获得当前进程的设置，确保在所有进程的参数一致
        parser_args = get_settings("pdf_parser")
        manager_args = get_settings("model_manager")
        n = 0
        total = None
        if isinstance(docs, Sized):
            total = len(docs)
        if max_workers == 0:
            # 在当前进程执行，方便测试
            with cls(manager=manager_args, args=parser_args) as parser:
                for doc in docs:
                    parser.parse(doc())
                    n += 1
                    cls._logger.info("第[%s/%s]个解析成功", n, total)
                cls._logger.info("结束解析，共解析：%s，耗时:%.3f", n, timer.elapsed())
        else:
            # 不要嵌套使用进程池，很容易导致子进程变成僵尸进程
            # 解决方案
            # 方案1.使用subprocess的方式，虽然也是子进程，但是为完全独立，也就是没有使用resource tracker等
            #   启动的进程可以使用进程池
            # 方案2.当前使用进程池，但是启动的子进程就不能够再使用进程池

            try:
                executor, stopped = cls._new_executor(
                    max_workers, manager_args, parser_args
                )
                with executor:
                    for _ in executor.map(cls._mp_parse, docs):
                        n += 1
                        cls._logger.info("第[%s/%s]个解析成功", n, total)
                    cls._logger.info(
                        "结束解析，共解析：%s，耗时:%.3f", n, timer.elapsed()
                    )
                    stopped.value = True
            finally:
                pass

        cls._logger.info("完成释放所有资源")

    @classmethod
    def _kill_mp_processes(cls, kill: bool, timeout: float | None = 10):
        # 每次返回一个新的
        mp_context = mp.get_context("spawn")
        children = mp_context.active_children()
        cls._logger.info(
            "start %s processes,size=%s", "kill" if kill else "terminate", len(children)
        )
        for p in children:
            cls._logger.info(
                "start %s process=%s", "kill" if kill else "terminate", p.pid
            )
            if kill:
                p.kill()
            else:
                p.terminate()

        start_time = time.monotonic()
        while timeout is None or time.monotonic() - start_time < timeout:
            i = 0
            while i < len(children):
                p = children[i]
                if not p.is_alive():
                    # 如果进程退出后被其他方式读取了wait的状态，这个总是返回True的
                    del children[i]
                else:
                    i += 1
            if not children:
                break
            else:
                time.sleep(0.5)

        cls._logger.info(
            "end %s processes,size=%s",
            "kill" if kill else "terminate",
            len(mp_context.active_children()),
        )

    @classmethod
    def _new_executor(
        cls, max_workers: int, manager_args: Any, parser_args: Any
    ) -> tuple[Executor, Any]:
        mp_context = mp.get_context("spawn")
        stopped = mp_context.Value("b")
        mp_init = MPInit(name='pdfparser')
        mp_init.set_fn(cls._mp_init, manager_args, parser_args, stopped)
        return ProcessPoolExecutor(
            max_workers=max_workers, mp_context=mp_context, initializer=mp_init
        ), stopped


class MPParserArgs(MyBaseModel):
    max_workers: int = 2
    retry_times: int = 1
    max_idle_timeout: float | None = None


class MPParser:
    def __init__(self, args: MPParserArgs | Mapping[str, Any] | None = None):
        super().__init__()
        args = MPParserArgs.create(args)
        self._max_workers: Final = args.max_workers
        self._stopped: Any = None
        self._executor: Final = SafeExecutor(
            self._new_executor,
            retry_times=args.retry_times,
            max_idle_timeout=args.max_idle_timeout,
        )
        self._finalizer = weakref.finalize(self, self._close, self._executor)

    @classmethod
    def _close(cls, executor: Executor):
        executor.shutdown()

    def _new_executor(self):
        parser_args = get_settings("pdf_parser")
        manager_args = get_settings("model_manager")
        if self._stopped is not None:
            self._stopped.value = True
        executor, stopped = Parser._new_executor(
            self._max_workers, manager_args, parser_args
        )
        self._stopped = stopped
        return executor

    def new_runner(self, doc: KDocument):
        return _ParseRunner(self._executor, doc)

    def close(self):
        if self._stopped is not None:
            self._stopped.value = True
        self._executor.shutdown()


class _ParseRunner(Runner):
    def __init__(self, executor: Executor, doc: KDocument):
        super().__init__()
        self._doc = doc
        self._executor = executor

    @override
    def _run(self, task: Task):
        assert self._doc is not None
        try:
            factory = KDocumentFactory(
                self._doc.file, self._doc.params, out_dir=self._doc.out_dir
            )
            future = self._executor.submit(Parser._mp_parse, factory)
            # 可以指定一个timeout
            future.result(timeout=None)
            return self._doc.zip_file
        finally:
            # 解除引用
            self._doc = None
