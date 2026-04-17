import abc
import importlib
import logging
import multiprocessing as mp
import os
import subprocess
import time
import weakref
from concurrent.futures import (
    Executor,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
)
from enum import StrEnum, auto
from logging import Logger
from pathlib import Path
from typing import (
    Annotated,
    Any,
    Final,
    Literal,
    Mapping,
    Self,
    Sequence,
    cast,
    override,
)

from pydantic import Field

from memect.base.config import MPInit
from memect.base.job import Scheduler
from memect.base.utils import MyBaseModel, SafeExecutor

from .base import KDocument


class DrawArgs(MyBaseModel):
    file: Path
    out_dir: Path
    max_scale: float = 2
    max_size: tuple[int, int] | None = (2000, 2000)
    pagenos: Sequence[int] | None = None
    chunk_size: Annotated[int, Field(description="表示多少页为一批")] = 10
    skip_exists: bool = False


class Provider(StrEnum):
    pymupdf = auto()
    """gil下线程安全，不支持free-threaded"""
    pdfium = auto()
    """git下线程不安全！！！，不支持free-threaded"""
    unknown = auto()

    def is_available(self)->bool:
        providers={
            Provider.pymupdf:'pymupdf',
            Provider.pdfium:'pypdfium2'
        }
        if self not in providers:
            return False
        else:
            try:
                importlib.import_module(providers[self])
                return True
            except ModuleNotFoundError:
                return False
    
    @classmethod
    def get_available_provider(cls,provider:Self|None)->'Provider':
        if not provider:
            for p in [Provider.pymupdf,Provider.pdfium]:
                if p.is_available():
                    return p
            return Provider.unknown
        elif provider.is_available():
            return provider
        else:
            return Provider.unknown

class Drawer(abc.ABC):
    @abc.abstractmethod
    def draw(self, args: DrawArgs):
        pass

    def _get_scale(
        self,
        size: tuple[int, int],
        max_scale: float,
        max_size: tuple[int, int] | None = None,
    ) -> float:
        if max_size is None:
            return max_scale
        return min(max_scale, max_size[0] / size[0], max_size[1] / size[1])


class _Drawer1(Drawer):
    """轻量级对象，支持进程序列化"""
    _logger: Logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self):
        super().__init__()

    @override
    def draw(self, args: DrawArgs):
        #多线程安全，但不支持free-threaded
        import pymupdf

        file = args.file
        out_dir = args.out_dir
        max_scale = args.max_scale
        max_size = args.max_size
        pagenos = args.pagenos
        skip_exists = args.skip_exists
        out_dir.mkdir(parents=True, exist_ok=True)
        with pymupdf.Document(file, filetype="pdf") as doc:
            page_count: Final[int] = cast(int, doc.page_count)  # type: ignore
            draw_count: int = 0
            exist_count: int = 0
            self._logger.info(
                "start pdf2image,file=%s,page_count=%s,max_scale=%s,max_size=%s,skip_exists=%s",
                file.name,
                page_count,
                max_scale,
                max_size,
                skip_exists,
            )
            t1 = time.monotonic()
            for i in range(page_count):
                pageno = i + 1
                if pagenos and pageno not in pagenos:
                    # 如果不需要执行
                    continue
                image_file = out_dir.joinpath(f"{pageno}.png")
                if skip_exists and image_file.is_file():
                    # 已经存在
                    exist_count += 1
                    continue
                draw_count += 1
                self._draw(
                    doc, pageno, image_file, max_scale=max_scale, max_size=max_size
                )
            t2 = time.monotonic()
            self._logger.info(
                "end pdf2image,file=%s,page_count=%s,exist_count=%s,draw_count=%s,elapsed=%.3f",
                file.name,
                page_count,
                exist_count,
                draw_count,
                t2 - t1,
            )

    def _draw(
        self,
        doc: Any,
        pageno: int,
        image_file: Path,
        max_scale: float = 2,
        max_size: tuple[int, int] | None = None,
    ):
        import pymupdf

        self._logger.debug("start draw page=%s", pageno)
        page: pymupdf.Page = doc[pageno - 1]
        alpha: Final = False
        matrix = pymupdf.Identity

        scale = self._get_scale(
            (int(page.rect.width), int(page.rect.height)), max_scale, max_size=max_size
        )
        if scale == 1:
            matrix = pymupdf.Identity
        else:
            matrix = pymupdf.Matrix(scale, scale)

        pix: pymupdf.Pixmap = page.get_pixmap(  # type: ignore
            matrix=matrix, alpha=alpha, annots=False
        )
        # pix:pymupdf.Pixmap = doc.get_page_pixmap(pageno - 1, matrix=matrix, alpha=alpha, dpi=dpi)
        # 这个如果指定其他格式，总是为png
        # https://pymupdf.readthedocs.io/en/latest/pixmap.html#pixmapoutput
        image_file.parent.mkdir(parents=True, exist_ok=True)
        pix.save(image_file, "png")  # type: ignore

class _Drawer2(Drawer):
    """轻量级对象，支持进程序列化"""
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    @override
    def draw(self, args: DrawArgs):
        #这个库不支持多线程，即使在gil下的多线程
        import pypdfium2 as pdfium

        file = args.file
        out_dir = args.out_dir
        max_scale = args.max_scale
        max_size = args.max_size
        pagenos = args.pagenos
        skip_exists = args.skip_exists
        out_dir.mkdir(parents=True, exist_ok=True)
        with pdfium.PdfDocument(file) as doc:
            page_count = len(doc)
            draw_count: int = 0
            exist_count: int = 0
            self._logger.info(
                "start pdf2image,file=%s,page_count=%s,max_scale=%s,max_size=%s,skip_exists=%s",
                file.name,
                page_count,
                max_scale,
                max_size,
                skip_exists,
            )
            t1 = time.monotonic()
            for i in range(page_count):
                pageno = i + 1
                if pagenos and pageno not in pagenos:
                    # 如果不需要执行
                    continue
                image_file = out_dir.joinpath(f"{pageno}.png")
                if skip_exists and image_file.is_file():
                    # 已经存在
                    exist_count += 1
                    continue
                draw_count += 1
                self._draw(doc, pageno, image_file, max_scale=max_scale, max_size=max_size)
            t2 = time.monotonic()
            self._logger.info(
                "end pdf2image,file=%s,page_count=%s,exist_count=%s,draw_count=%s,elapsed=%.3f",
                file.name,
                page_count,
                exist_count,
                draw_count,
                t2 - t1,
            )

    def _draw(
        self,
        doc: Any,
        pageno: int,
        image_file: Path,
        max_scale: float = 2,
        max_size: tuple[int, int] | None = None,
    ):
        self._logger.debug("start draw page=%s", pageno)
        # pypdfium2不是线程安全的，在多线程下（即使gil开启），一页出现问题，典型的就是doc[1]这样的时候失败
        page = doc[pageno - 1]
        width, height = page.get_width(), page.get_height()
        # 计算 scale，确保图片不超过 2000x2000
        scale = self._get_scale((width, height), max_scale, max_size=max_size)
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil()
        image.save(image_file)

class _Drawer3(Drawer):
    def __init__(self, impl: Drawer):
        super().__init__()
        self._impl = impl

    @override
    def draw(self, args: DrawArgs):
        ctx = mp.get_context("spawn")
        use_mp_init = True
        if use_mp_init:
            # 执行相同的初始化然后执行该操作
            mp_init = MPInit()
            mp_init.set_fn(self._impl.draw, args)
            # 每次使用一个新的进程执行
            p = ctx.Process(target=mp_init, daemon=False)
        else:
            p = ctx.Process(
                target=self._impl.draw, args=(args,), kwargs={}, daemon=False
            )
        p.start()
        p.join()
        if p.exitcode is None or p.exitcode != 0:
            raise Exception(f"进程执行失败，返回:{p.exitcode}")
        return


class _Drawer4(Drawer):
    def __init__(self, provider: Provider | None = None):
        super().__init__()
        self._provider: Final = provider

    @override
    def draw(self, args: DrawArgs):
        # mode可以随便，因为用不到
        settings = Pdf2ImageArgs(provider=self._provider, mode="thread")
        cmd: list[str] = []
        cmd.append("env")
        cmd.append("PYTHONPATH=src")
        cmd.append("python/bin/python")
        if True:
            cmd.append('-c')
            cmd.append('from memect.pdf2image import main;main()')
        else:
            #这种方式不好，如果其他代码又import memect.pdf2image，会导致这个库被import 2次
            #一次是__main__,一次是memect.pdf2image
            #所以，如果需要使用这种方式，建议创建一个单独的文件，避免2次import
            cmd.append("-m")
            cmd.append("memect.pdf2image")
        cmd.append(settings.model_dump_json())
        cmd.append(args.model_dump_json())
        cwd = os.path.abspath(".")
        p = subprocess.run(cmd, cwd=cwd)
        p.check_returncode()


class Pdf2ImageArgs(MyBaseModel):
    provider: Annotated[Provider | None, Field(description="")] = None
    mode: Annotated[Literal["process", "cmd", "thread","auto"], Field(description="")] = (
        "auto"
    )
    max_idle_timeout:Annotated[float|None,Field(description='表示超过这个空闲时间，就释放进程池/线程池')]=120
    # ide很笨，必须这么才知道有默认值，否则会提示这个值必须设置
    scheduler: Annotated[dict[str, Any], Field(description="")] = Field(
        default_factory=dict
    )

class Pdf2Image:
    """支持多线程使用"""

    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self, args: Pdf2ImageArgs|Mapping[str,Any] | None = None,*,only_execute:bool=False):
        super().__init__()
        args = Pdf2ImageArgs.create(args)
        self._args: Final = args
        self._max_idle_timeout:Final = args.max_idle_timeout
        #TODO 两种模式，自动检查模式：provider=None and mode='auto'
        #其他按指定的来，即使不支持，就报错，方便测试
        if args.provider and not args.provider.is_available() and args.mode not in ('auto','cmd'):
            raise RuntimeError(f'当前环境不支持,provider={args.provider},mode={args.mode}')

        self._provider: Final = Provider.get_available_provider(args.provider)
        if args.mode=='auto':
            if self._provider==Provider.unknown:
                mode='cmd'
            else:
                mode='process'
        else:
            mode=args.mode
        self._mode: Final = mode
        if not only_execute:
            self._scheduler = Scheduler(
                "pdf2image", self._execute, **args.scheduler
            )
            self._max_workers = self._scheduler.max_task_size
            # TODO 如果是线程池，空闲会自动释放线程，也可以，max_idle_timeout=120 if self._mode=='process' else 0
            self._executor = SafeExecutor(self._new_executor, max_idle_timeout=self._max_idle_timeout)
        else:
            self._scheduler = None
            self._max_workers =4
            self._executor = None

        self._finalizer:Final=weakref.finalize(self, self._close,self._executor,self._scheduler)

    @classmethod
    def _close(cls,executor:Executor|None,scheduler:Scheduler[Any,Any]|None,wait:bool=True):
        cls._logger.info('start close pdf2image')
        if scheduler:
            scheduler.close()
        if executor:
            executor.shutdown(cancel_futures=True, wait=wait)
        cls._logger.info('end close pdf2image')
    
    def close(self):
        if self._finalizer.alive:
            self._finalizer()
        del self._executor
        del self._scheduler

            
    def __del__(self):
        self._logger.debug('gc %s',self)

    def get_page_count(self, file: str | Path) -> int:
        if self._provider==Provider.pymupdf:
            import pymupdf
            with pymupdf.Document(filename=file, filetype="pdf") as doc:
                return cast(int, doc.page_count)  # type: ignore
        elif self._provider==Provider.pdfium:
            # 如果仅仅是获得页码，pypdfium2更快
            import pypdfium2 as pdfium
            with pdfium.PdfDocument(file) as doc:
                return len(doc)
        elif self._provider==Provider.unknown:
            import pypdf
            return pypdf.PdfReader(Path(file)).get_num_pages()
        else:
            #使用pdf_oxide?获得，也不支持free-threaded
            #使用pdfminer获得，太慢
            raise ValueError(f'不支持的provider={self._provider}')

    def get_pagenos(
        self,
        out_dir: str | Path,
        page_count: int,
        pagenos: Sequence[int] | None = None,
        skip_exists: bool = False,
    ) -> tuple[Sequence[int], Sequence[int]]:
        out_dir = Path(out_dir)
        all_pagenos = list(range(1, page_count + 1))
        if not pagenos:
            pagenos = all_pagenos
        else:
            pagenos = list(set(pagenos) & set(all_pagenos))
            pagenos.sort()

        pending_pagenos: list[int] = []
        exist_pagenos: list[int] = []
        if skip_exists:
            for pageno in pagenos:
                file = out_dir / f"{pageno}.png"
                if file.is_file():
                    exist_pagenos.append(pageno)
                else:
                    pending_pagenos.append(pageno)
        else:
            pending_pagenos.extend(pagenos)

        return exist_pagenos, pending_pagenos
    

    def parse(self,doc:KDocument):

        def on_progress(total_items:Any,successed_items:Any):
            print('onprogress',len(total_items),len(successed_items))
            #assert doc.task is not None
            #doc.task.set_progress('pdf2image')
            pass

        pagenos:list[int]=[]
        for page in doc.pages:
            if page.skipped:
                continue

            if doc.is_dev() and page.file.is_file():
                continue
            pagenos.append(page.number)
        
        if pagenos:
            draw_args=DrawArgs(file=doc.file,out_dir=doc.pages_dir,pagenos=pagenos)
            pdf2image_job=self.submit(draw_args)
            #可以同时显示进度，如果需要
            pdf2image_job.add_progress_listener(on_progress)
            pdf2image_job.wait()
        else:
            #没有需要执行的页码，忽略，否则pdf2image默认没有页码表示为全部
            pass

    def submit(self, args: DrawArgs):
        """排队执行"""
        assert self._scheduler is not None
        file = args.file
        chunk_size = args.chunk_size
        page_count = self.get_page_count(file)
        _, pagenos = self.get_pagenos(
            args.out_dir, page_count, args.pagenos, skip_exists=args.skip_exists
        )
        items: list[Any] = []
        for i in range(0, len(pagenos), chunk_size):
            # 如：有100页，10页一批
            item = args.model_copy()
            item.pagenos = pagenos[i : i + chunk_size]
            items.append(item)
        return self._scheduler.submit(items)

    def _execute(self, args: DrawArgs) -> Any:
        assert self._executor is not None
        drawer = self._new_drawer()
        # 因为现在还不支持free-threaded，所以需要使用executor来使用多核心
        future = self._executor.submit(drawer.draw, args)
        return future.result()

    def execute(self, args: DrawArgs, *, max_workers: int = 4):
        """直接执行，不排队"""
        file = args.file
        chunk_size = args.chunk_size
        page_count = self.get_page_count(file)
        _, pagenos = self.get_pagenos(
            args.out_dir, page_count, args.pagenos, skip_exists=args.skip_exists
        )
        if not pagenos:
            self._logger.info("没有页面需要执行")
            return

        drawer = self._new_drawer()
        if max_workers == 0:
            # 表示在当前进程执行，方便调试
            args = args.model_copy()
            args.pagenos = pagenos
            drawer.draw(args)
        else:
            # 每次创建一个新的Executor来执行
            def get_items(chunk_size: int = 1):
                # 可以按页，也可以按chuank_size
                for i in range(0, len(pagenos), chunk_size):
                    yield args.model_copy(
                        update={"pagenos": pagenos[i : i + chunk_size]}
                    )

            with self._new_executor(max_workers) as executor:
                list(executor.map(drawer.draw, get_items(chunk_size)))

    def _new_executor(self, max_workers: int | None = None) -> Executor:
        max_workers = max_workers or self._max_workers
        if self._mode == "process":
            # 这个不会释放进程，进程常驻
            mp_init = MPInit()
            # mp_init.set_fn()
            return ProcessPoolExecutor(
                max_workers, mp_context=mp.get_context("spawn"), initializer=mp_init
            )
        elif self._mode == "thread":
            # 如果将来pymupdf或者pdfium支持了free-threaded，选择这个
            return ThreadPoolExecutor(max_workers, thread_name_prefix="pdf2image")
        elif self._mode == "cmd":
            # 如果需要快速释放进程，使用这个
            return ThreadPoolExecutor(max_workers, thread_name_prefix="pdf2image")
        else:
            raise ValueError("")

    def _new_drawer(self) -> Drawer:
        drawer: Drawer
        if self._provider == Provider.pymupdf:
            drawer = _Drawer1()
        elif self._provider == Provider.pdfium:
            drawer = _Drawer2()
        else:
            # unknown?
            raise ValueError(f"不支持的provider={self._provider}")

        if self._mode == "cmd":
            # TODO 使用命令的方式执行，可以使用subprocess（可以使用不同的python）
            # 或者使用子进程
            if self._provider == Provider.unknown:
                # 当前的环境（如：free-threaded）不支持，需要使用命令行（使用不同的python）执行
                drawer = _Drawer4(provider=self._args.provider)
            else:
                # 使用子进程就可以，因为当前的环境支持pymupdf或者pdfium
                drawer = _Drawer3(drawer)
        return drawer

    

    
def main():
    # 提供命令行使用
    import sys

    from memect.x2x.config import setup
    setup()
    settings = Pdf2ImageArgs.model_validate_json(sys.argv[1])
    args = DrawArgs.model_validate_json(sys.argv[2])
    Pdf2Image(settings,only_execute=True).execute(args, max_workers=0)


if __name__ == "__main__":
    main()
