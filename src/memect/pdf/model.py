import logging
import multiprocessing as mp
import os
import threading
import time
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, ClassVar, Final, Mapping, Sequence, final, override
import weakref

import cv2
import httpx
import numpy as np
import PIL
import PIL.Image
from openai import OpenAI
from pydantic import BaseModel, Field

from memect.base import lists
from memect.base.api import ApiError
from memect.base.config import MPInit, get_settings
from memect.base.debug import XDebugger
from memect.base.job import Scheduler, SchedulerArgs
from memect.base.sdk import Api
from memect.base.task import Runner, Task
from memect.base.utils import MyBaseModel, SafeExecutor

from .base import KDocument, KPage, KTable
from .commons import FileInfo

type _Image = str | Path | bytes | FileInfo | PIL.Image.Image | cv2.typing.MatLike


class Model:
    _use_lock: bool = True
    """True表示需要通过lock来实现线程安全，False表示本身就支持了，如：通过api调用的，默认为False"""

    def __init__(self):
        super().__init__()
        self._lock: Final = threading.RLock()

    @final
    def execute(
        self, files: Sequence[_Image], *, params: Mapping[str, Any] | None = None
    ) -> list[Any]:
        new_files: list[FileInfo] = []
        for file in files:
            if isinstance(file, (str, Path, bytes, np.ndarray, PIL.Image.Image)):
                new_files.append(FileInfo(file=file, params=params))
            else:
                new_files.append(file)
        if self._use_lock:
            with self._lock:
                return self._execute(new_files)
        else:
            return self._execute(new_files)

    def _execute(self, files: Sequence[FileInfo]) -> list[Any]:
        """子类实现，可以顺序执行，也可以批处理"""
        return []

    @classmethod
    def create(
        cls,
        class_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> "Model":
        """
        通过类名查找子类并创建实例。

        Args:
            class_name: 子类名称
            *args:      传给子类构造函数的位置参数
            **kwargs:   传给子类构造函数的关键字参数

        Raises:
            ValueError: 找不到对应的子类

        Returns:
            子类实例
        """
        subclass = cls._find_subclass(class_name)
        if subclass is None:
            available = [c.__name__ for c in cls._all_subclasses()]
            raise ValueError(f"找不到子类 '{class_name}'，可用的子类: {available}")
        return subclass(*args, **kwargs)

    @classmethod
    def _find_subclass(
        cls,
        class_name: str,
    ) -> type["Model"] | None:
        """递归查找所有子类中名称匹配的类"""
        for subclass in cls._all_subclasses():
            if subclass.__name__ == class_name:
                return subclass
        return None

    @classmethod
    def _all_subclasses(cls) -> list[type["Model"]]:
        """递归获取所有子类（包括子类的子类）"""
        result: list[type["Model"]] = []
        for subclass in cls.__subclasses__():
            result.append(subclass)
            result.extend(subclass._all_subclasses())
        return result


class ModelRunner(Runner):
    def __init__(self, model: Model, files: Sequence[FileInfo]):
        super().__init__()
        self._model = model
        self._files = files

    @override
    def _run(self, task: Task):
        return self._model.execute(self._files)


class ModelArgs(BaseModel):
    name: str
    args: Sequence[Any] = Field(default_factory=tuple)
    kwargs: Mapping[str, Any] = Field(default_factory=dict)


class ModelExecutorArgs(MyBaseModel):
    enable: bool = True
    name: str
    chunk_size: int = 1
    """在submit的时候，多少个一批，如：有些模型的批处理为5，一次性输入5个可以获得最好的性能，就可以设置为5"""
    max_workers: int = 2
    """表示创建多少个模型，0表示仅仅创建1个，且不使用多线程和多进程，方便测试"""
    use_process: bool = False
    """True表示使用多进程，False表示使用多线程"""
    max_idle_timeout: float | None = None
    """表示进程或者线程空闲了多长时间就释放，None表示不会释放"""
    scheduler: SchedulerArgs = Field(default_factory=SchedulerArgs)
    model: ModelArgs | str
    """如果为字符串，表示使用settings中的设置"""
    #settings: Mapping[str, ModelArgs] = Field(default_factory=dict)
    use_api: bool = False
    port: int = 9527



# [xx,cache,filename]
type _Item = tuple[_Image, dict[str, Any]]
type _Item2 = tuple[_Image, dict[str, Any], str]


class ModelExecutor:
    """内部使用了scheduler进行调度，支持fifo和balance算法"""

    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")

    def __init__(self, args: ModelExecutorArgs | Mapping[str, Any]):
        super().__init__()
        args = ModelExecutorArgs.create(args)
        assert not isinstance(args.model,str)
        # self._args:Final = args
        self._use_api: Final = args.use_api
        self._chunk_size: Final = args.chunk_size
        self._use_process: Final = args.use_process
        self._model_cfg: Final = args.model
        # 如果是本地执行，建议：scheduler.max_task_size>=args.max_workers
        self._max_workers: Final = args.max_workers
        self._model: Model | None = None
        self._executor: Executor | None = None
        self._thread_local: Final = threading.local()

        # 如果是简单的调度模式，直接使用进程或者线程Executor即可
        # 但是现在需要更大的调度算法，所以，如果是线程
        if self._use_api:
            # 表示通过api的方式调用，也就是先独立启动了服务，如：./app start --xxx --
            self._model = ApiModel()
        elif self._max_workers > 0:
            self._executor = SafeExecutor(
                self._new_executor, max_idle_timeout=args.max_idle_timeout
            )
        else:
            # 表示只需要一个，方便测试，或者一个就足够了，如：通过api调用的
            # 模型需要支持多线程
            self._model = self._create_model(self._model_cfg)

        self._scheduler = Scheduler(
            args.name, self.execute, **args.scheduler.model_dump()
        )

        self._finalizer=weakref.finalize(self,self._close,self._executor,self._scheduler)
    
    @classmethod
    def _close(cls,executor:Executor|None,scheduler:Scheduler[Any,Any]|None):
        if executor:
            executor.shutdown(True,cancel_futures=True)
        if scheduler:
            scheduler.close()
    
    def close(self):
        if self._finalizer.alive:
            self._finalizer()
        self._executor=None
        self._scheduler=None
        self._model=None


    def parse(
        self,
        doc: KDocument,
        cache_name: str,
        handler: Callable[[KPage], Sequence[_Item] | None] | None = None,
        multi: bool = True,
        timeout: float | None = None,
        batch_size: int = 100,
    ):
        """
        执行解析操作

        doc:
        cache_name: 返回的结果存在cache的name

        handler: 如果没有指定，默认使用页面图片，且缓存的文件前缀使用cache_name

        timeout:

        batch_size: 表示分批处理，避免一次性处理，需要太大内存
        """

        end_clock = None if timeout is None else time.monotonic() + timeout

        if handler is None:

            def default_handler(page: KPage):
                return [(page.file, page.cache)]

            handler = default_handler
            multi = False

        def batch(items: Sequence[_Item2]):
            if end_clock is not None:
                timeout = end_clock - time.monotonic()
            else:
                timeout = None
            job = self.submit([item[0] for item in items])
            for item, result in zip(items, lists.flat(job.wait(timeout=timeout))):
                item[1][cache_name] = result
                if doc.is_dev():
                    doc.write(item[2], result)

        items: list[_Item2] = []
        cache_items: list[_Item2] = []
        for page in doc.working_pages:
            # 补充文件名
            page_items: list[_Item2] = []
            raw_items = handler(page) or []
            # 当设置multi=False，只能够返回0-1个
            assert multi or len(raw_items) <= 1
            for i, item in enumerate(raw_items):
                if multi:
                    # 表示会返回多个
                    filename = f"{cache_name}/{page.number}-{i + 1}.json"
                else:
                    # 表示返回0-1个，所以就不需要
                    filename = f"{cache_name}/{page.number}.json"

                page_items.append((item[0], item[1], filename))

            if doc.is_dev():
                # 如果为开发模式，存在的就跳过
                for item in page_items:
                    if doc.has_file(item[2]):
                        cache_items.append(item)
                    else:
                        items.append(item)
            else:
                items.extend(page_items)

            while len(items) >= batch_size:
                batch(items[0:batch_size])
                del items[0:batch_size]

        if len(items) > 0:
            batch(items)

        for item in cache_items:
            # 使用存在的文件，可能为json，也可能为text?
            name = item[2]
            item[1][cache_name] = (
                doc.read_json(name) if name.endswith(".json") else doc.read_text(name)
            )

    def submit(self, files: Sequence[_Image], *, chunk_size: int | None = None):
        items: list[Sequence[_Image]] = []
        chunk_size = self._chunk_size if chunk_size is None else chunk_size
        for i in range(0, len(files), chunk_size):
            items.append(files[i : i + chunk_size])
        return self._scheduler.submit(items)

    def execute(self, files: Sequence[_Image]) -> list[Any]:
        """直接执行，不需要调度，通过api提供服务，应该使用这个方法"""
        if self._executor:
            # 如果是启动快，计算耗时的，每次都使用一个新的进程（或者命令行）执行，也是可以
            # 因为现在是启动慢，执行快，所以使用进程池
            if self._use_process:
                future = self._executor.submit(self._execute_on_process, files)
            else:
                future = self._executor.submit(self._execute_on_thread, files)
            return future.result()
        elif self._model:
            # 轻量级且支持多线程的
            return self._model.execute(files)
        else:
            raise RuntimeError("不可能执行到这里")

    def _new_executor(self) -> Executor:
        if self._use_process:
            mp_init = MPInit()
            mp_init.set_fn(self._init_process, self._model_cfg)
            return ProcessPoolExecutor(
                self._max_workers,
                mp_context=mp.get_context("spawn"),
                initializer=mp_init,
            )
        else:
            return ThreadPoolExecutor(self._max_workers,thread_name_prefix=f'{self._model_cfg.name}_', initializer=self._init_thread)

    def _init_thread(self):
        if hasattr(self._thread_local, "model"):
            # 出现这个错误，是线程池重复初始化同一个线程了
            raise RuntimeError("编程错误，该线程已经初始化了")
        self._thread_local.model = self._create_model(self._model_cfg)
        # 如果需要释放
        # weakref.ref(model)

    def _execute_on_thread(self, files: Sequence[_Image]) -> list[Any]:
        model: Model = self._thread_local.model
        return model.execute(files)

    # 如果不同的文档需要使用不同的模型，且都部署在同一个服务器，那么，一个进程使用多个模型资源分配更合理
    # 如：进程1创建了M1和M2模型，100个M1请求，可以处理，100个M2请求可以，100(M1+M2)，每个50个
    # 如果分开，进程1创建M1，进程2创建M2，如果同时来200个请求，就需要处理200个，如果配置为50+50，M1或者M2就会空闲
    _mp_model: ClassVar[Model | None] = None

    @classmethod
    def _init_process(cls, cfg: ModelArgs):
        assert cls._mp_model is None
        cls._mp_model = cls._create_model(cfg)

    @classmethod
    def _execute_on_process(cls, files: Sequence[_Image]) -> list[Any]:
        assert cls._mp_model is not None
        return cls._mp_model.execute(files)

    @classmethod
    def _create_model(cls, cfg: ModelArgs) -> Model:
        return Model.create(cfg.name, *cfg.args, **cfg.kwargs)


class ModelManagerArgs(MyBaseModel):
    executors: dict[str, ModelExecutorArgs] = Field(default_factory=dict)
    models: dict[str,ModelArgs] = Field(default_factory=dict)


class ModelManager:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self, args: ModelManagerArgs | Mapping[str, Any]|None=None):
        super().__init__()
        if args is None:
            args = get_settings('model_manager')
        args = ModelManagerArgs.create(args)
        self._executors: dict[str, ModelExecutor] = {}
        self._models:dict[str,ModelArgs]={}

        #alias_mapping: dict[str, str] = {}
        models: dict[str, ModelArgs] = {}
        for name, value in args.models.items():
            models[name] = value

        for name, executor_args in args.executors.items():
            if executor_args.enable:
                executor_args.name = name
                if isinstance(executor_args.model,str):
                    self._logger.info("create model executor name=%s,model=%s", name,executor_args.model)
                    executor_args.model = models[executor_args.model]
                else:
                    self._logger.info("create model executor name=%s", name)

                self._executors[name] = ModelExecutor(executor_args)
            else:
                self._logger.info("disable model executor name=%s", name)
        
        self._finalizer = weakref.finalize(self,self._close,self._executors)
    
    @classmethod
    def _close(cls,executors:dict[str,ModelExecutor]):
        cls._logger.info('start close modelmanager')
        executors2 = dict(executors)
        executors.clear()
        for name,executor in executors2.items():
            cls._logger.info('close executor name=%s',name)
            executor.close()
        cls._logger.info('end close modelmanager')

    def close(self):
        if self._finalizer.alive:
            self._finalizer()

    def get(self, name: str) -> ModelExecutor:
        return self._executors[name]
    



class ApiModel(Model):
    """标准的api，和本地执行的输出一致"""

    def __init__(self, port: int = 9527):
        super().__init__()
        self._api = Api()

        self._client = None
        self._use_local = False
        self._local_token: Final = ""
        self._local_url = f"http://127.0.0.1:{port}"
        if self._use_local:
            self._client = httpx.Client()
        else:
            pass

    @override
    def _execute(self, files: Sequence[FileInfo]) -> list[Any]:
        items: list[Any] = []
        if self._use_local:
            # 如果是本地的，就使用简单的方式
            client = httpx.Client()
            params = {"token": "", "files": files}
            # 为了避免token在url中，显示在日志中，可以要求token在参数中，或者header中
            resp = client.post("", json=params)
            if resp.status_code == 200:
                result = resp.json()
                if result.get("error"):
                    raise ApiError.from_dict(result.get("error"))
                else:
                    return result["data"]
            else:
                # 返回错误的代码，这里不应该使用ApiError，而是ModelError更好
                raise ApiError(ApiError.ANY, "")
        else:
            for i, file in enumerate(files):
                # TODO 现在还是传数据，后续可以简化为直接传递路径
                # 问题就是需要考虑安全问题
                # 另外一个就是如果有旋转，就需要对图片先进行处理
                items.append((str(i), file.file, file.params))
            results: list[Any] = []
            for _, _, result in self._api.batch(items):
                results.append(result)
            return results

    def _invoke_local(self, files: Sequence[FileInfo]):
        # 如果是本地的，就使用简单的方式
        assert self._client is not None
        client = self._client
        params = {"token": "", "files": files}
        # 为了避免token在url中，显示在日志中，可以要求token在参数中，或者header中
        resp = client.post("", json=params)
        if resp.status_code == 200:
            result = resp.json()
            if result.get("error"):
                raise ApiError.from_dict(result.get("error"))
            else:
                return result["data"]
        else:
            # 返回错误的代码，这里不应该使用ApiError，而是ModelError更好
            raise ApiError(ApiError.ANY, "")


class OnnxModel(Model):
    pass


class YOLOClassifyModel(Model):
    def __init__(self, **kwargs: Any):
        super().__init__()
        #from yolo_classify import YOLOClassifier
        #self._model: Final = YOLOClassifier(**kwargs)

    @override
    def _execute(self, files: Sequence[FileInfo]):
        # 在输出onnx模型的时候，默认为batch=1，也就是一次只能够输入一个图片
        # 如果batch=4，必须输入4个，如果不够4个，需要使用补充够
        # 如果batch=0，可以为任意数量

        # 有两种设计
        # batch=1，然后5个session，假设每个模型需要1G，那么，就需要5G的显存+5张图片，最大，适合处理单张图片
        # 因为每个独立的请求都只有1张图片
        # batch=5，只需要一个session，1G+5张图片
        # batch=5，5个session，需要5G+25张图片，适合批量处理解析，如：每个pdf都是100页的
        # 可以一次性100张图片过来，然后批处理
        # 折中，就是使用batch=0，5个session，每个session可以批处理1-10张图片，只有1张的时候，也不需要补齐为5张，减少显存和
        # 算力的占用

        imgs: list[cv2.typing.MatLike] = []
        for file in files:
            imgs.append(file.cv2_image)

        results: list[Any] = []
        for scores, classes in self._model.classify(imgs):
            objs: list[Any] = []
            for score, type_ in zip(scores, classes):
                obj = {"type": type_, "score": score}
                objs.append(obj)
            results.append({"objects": objs})

        return results


class YOLODetectModel(Model):
    def __init__(self, **kwargs: Any):
        super().__init__()
        # 本地启动模型，然后执行
        # onnx模型可以把预处理和后处理封装在模型中，所以就不需要再写特别的代码
        #from yolo_detect import YOLODetector

        #self._model: Final = YOLODetector(**kwargs)

    @override
    def _execute(self, files: Sequence[FileInfo]):
        # 在输出onnx模型的时候，默认为batch=1，也就是一次只能够输入一个图片
        # 如果batch=4，必须输入4个，如果不够4个，需要使用补充够
        # 如果batch=0，可以为任意数量

        # 有两种设计
        # batch=1，然后5个session，假设每个模型需要1G，那么，就需要5G的显存+5张图片，最大，适合处理单张图片
        # 因为每个独立的请求都只有1张图片
        # batch=5，只需要一个session，1G+5张图片
        # batch=5，5个session，需要5G+25张图片，适合批量处理解析，如：每个pdf都是100页的
        # 可以一次性100张图片过来，然后批处理
        # 折中，就是使用batch=0，5个session，每个session可以批处理1-10张图片，只有1张的时候，也不需要补齐为5张，减少显存和
        # 算力的占用

        imgs: list[cv2.typing.MatLike] = []
        for file in files:
            imgs.append(file.cv2_image)

        results: list[Any] = []
        for result in self._model.detect(imgs):
            objs: list[Any] = []
            for score, bbox, type_ in result:
                obj = {"type": type_, "score": score, "bbox": bbox}
                objs.append(obj)
            results.append({"objects": objs})

        return results


class PaddleModel(Model):
    pass


class AutoLayoutModel(Model):
    def __init__(self):
        super().__init__()
        # 必须从hh下载，modelscope没有
        # export HF_ENDPOINT=https://hf-mirror.com
        # huggingface-cli download mymodel --local-dir mymodel
        # modelscope download --model mymodel --local_dir mymodel
        # modelscope download --model mymodel --cache_dir ./hub   => 会根据模型的id创建路径

        # huggingface-cli download PaddlePaddle/PP-DocLayoutV3_safetensors --local-dir models/PaddlePaddle/PP-DocLayoutV3_safetensors
        # PaddlePaddle/PP-DocLayoutV3_safetensors
        # PaddlePaddle/PP-DocLayoutV2_safetensors
        # 有多种实现方式
        # 1.pipeline，处理类预处理+推理+后处理
        # 2. auto，从config.json中获得类的信息
        # 3. 指定使用哪些类
        from transformers import pipeline

        self._pipeline = pipeline(
            "object-detection", model="./models/PaddlePaddle/PP-DocLayoutV3_safetensors"
        )

    @override
    def _execute(self, files: Sequence[FileInfo]) -> list[Any]:
        for file in files:
            results = self._pipeline(file.pil_image)
            for idx, res in enumerate(results):
                print(f"Order {idx + 1}: {res}")
        return [{}]


class RapidLayoutModel(Model):
    def __init__(self, mapping: Mapping[str, str] | None = None, **kwargs: Any):
        super().__init__()
        from rapid_layout import RapidLayout

        self._mapping: Final = mapping or {}
        self._model = RapidLayout(**kwargs)

    @override
    def _execute(self, files: Sequence[FileInfo]):
        results: list[Any] = []
        for file in files:
            output = self._model(file.file)
            objs: list[Any] = []
            size = file.size
            height = size[1]
            if output.boxes and output.scores and output.class_names:
                for box, score, class_name in zip(
                    output.boxes, output.scores, output.class_names
                ):
                    # box的坐标为原点为左上角
                    x0, y0, x1, y1 = box
                    # 如果需要转换为左下角，现在不需要了
                    # y0, y1 = height - y1, height - y0
                    obj = {
                        "type": self._mapping.get(class_name) or class_name,
                        "bbox": (x0, y0, x1, y1),
                        "score": round(score, 2),
                        "raw_type": class_name,
                    }
                    objs.append(obj)
            else:
                pass
            results.append({"objects": objs, "width": size[0], "height": size[1]})
        return results


class RapidOCRModel(Model):
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self, **kwargs: Any):
        super().__init__()
        from rapidocr import RapidOCR

        kwargs = self._normalize_kwargs(kwargs)
        self._model: Final = RapidOCR(params=kwargs)

    def _normalize_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        from rapidocr import (
            EngineType,
            LangCls,
            LangDet,
            LangRec,
            ModelType,
            OCRVersion,
        )

        def str2enum(name: str, type_: type[Any], full: bool = False):
            for k, v in kwargs.items():
                if (
                    full and k == name or not full and k.split(".")[1] == name
                ) and isinstance(v, str):
                    kwargs[k] = type_(v)

        kwargs = dict(kwargs)
        str2enum("model_type", ModelType)
        str2enum("engine_type", EngineType)
        str2enum("ocr_version", OCRVersion)
        str2enum("Det.lang_type", LangDet, full=True)
        str2enum("Cls.lang_type", LangCls, full=True)
        str2enum("Rec.lang_type", LangRec, full=True)

        return kwargs

    @override
    def _execute(self, files: Sequence[FileInfo]):
        from rapidocr.utils.output import RapidOCROutput

        def to_point(p: Any) -> Any:
            return (float(p[0]), float(p[1]))

        def to_quad(box: Any) -> Any:
            p1, p2, p3, p4 = box
            return (to_point(p1), to_point(p2), to_point(p3), to_point(p4))

        results: list[Any] = []
        for file in files:
            # 这个模型的其他参数，None表示不设置，使用配置的值
            # 但是，如果设置了一次，就会直接改变配置的值，所以，要么都不设置，要么每次都设置

            # 代码是支持PIL.Image.Image，但是接口的类型注释没有
            output: RapidOCROutput = self._model(file.file)
            objs: list[Any] = []
            size = file.size
            # height = size[1]
            if (
                output.boxes is not None
                and output.scores is not None
                and output.txts is not None
            ):
                for box, score, text in zip(output.boxes, output.scores, output.txts):
                    obj = {
                        "text": text,
                        # 原点为左上角
                        "quad": to_quad(box),
                        "score": round(score, 2),
                    }
                    objs.append(obj)
            else:
                pass
            results.append({"spans": objs, "width": size[0], "height": size[1]})
        return results

    def unclip(self, box: np.ndarray) -> np.ndarray:
        # 标准的实现是
        # det(识别文本区域，然后unclip_ratio扩展quad，截图为长方形（通过透视的方式），如果height/width>1.5，旋转90度，变成水平)
        # cls(识别文本方向，通过det可以知道，垂直的变成了水平，但是可能倒过来，这个主要识别0，180度，然后变成正确的方向)
        # rec（识别出文本）
        # 现在这里，跳过det/cls，只需要rec，所以只需要正确的截图即可，可以应该unclip+截图
        # 代码可以参考
        #
        pass


class RapidFormulaModel(Model):
    def __init__(self, **kwargs: Any):
        super().__init__()
        #这个把pix2tex转化为onnx了
        #需要从github上下载文件，会比较慢
        #https://github.com/RapidAI/RapidLaTeXOCR/releases
        from rapid_latex_ocr import LatexOCR

        #kwargs = self._normalize_kwargs(kwargs)
        self._model: Final = LatexOCR(**kwargs)

    @override
    def _execute(self, files: Sequence[FileInfo]):
        results:list[Any]=[]
        for file in files:
            res,elapsed = self._model(file.cv2_image)
            results.append({
                'latex':res,
                'elapsed':elapsed
            })
        return results


class TableClsModel(Model):
    def __init__(self, **kwargs: Any):
        super().__init__()
        from table_cls import TableCls

        # TODO 也可以使用多个模型
        self._model: Final = TableCls(**kwargs)

    @override
    def _execute(self, files: Sequence[FileInfo]):
        results: list[str] = []
        for file in files:
            label, _ = self._model(file.file)
            results.append(label)
        return results



class LLMModel(Model):
    # 通过api调用，不需要lock，一个模型就足够了
    _use_lock = False

    def __init__(
        self,
        *,
        model: str,
        prompt: str,
        client: Mapping[str, Any],
        params: Mapping[str, Any],
        prompts: Mapping[str, str] | None = None,
        image_format: str | None = None,
        image_size: tuple[int, int] | None = None,
        image_max_size: tuple[int, int] | None = None,
        **kwargs: Any,
    ):
        super().__init__()
        self._model: str = model
        self._prompt: str = prompt
        self._prompts: Mapping[str, str] = prompts or {}
        """如果支持多个任务，如:{"formula":"xxx","table":"","text":""}"""
        self._params: Mapping[str, Any] = params
        self._image_size: tuple[int, int] | None = image_size
        """如果设置了，表示图片使用固定的size"""
        self._image_max_size: tuple[int, int] | None = image_max_size
        """如果设置了，表示图片的最大size"""
        self._image_format: str | None = image_format
        """如果设置了，表示总是使用该格式，如：png"""
        self._client = OpenAI(**client)

    @override
    def _execute(self, files: Sequence[FileInfo]):
        results: list[Any] = []
        # 目前不支持一次性提交多个文件
        for file in files:
            messages = self._build_messages(file)
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
                **self._params,
            )
            results.append(self._build_result(resp))
        return results

    def _build_messages(self, file: FileInfo) -> Any:
        img, url = file.to_url(
            format=self._image_format,
            size=self._image_size,
            max_size=self._image_max_size,
        )
        prompt = self._get_prompt(file)
        return [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ]

    def _build_result(self, resp: Any) -> Any:
        text = resp.choices[0].message.content or ""
        return {"text": text}

    def _get_prompt(self, file: FileInfo) -> str:
        if file.params and file.params.get("task", "") and self._prompts:
            # 如果指定了任务，可以使用任务对应的提示词
            return self._prompts.get(file.params["task"], self._prompt)
        else:
            return self._prompt


class LLMTableModel(LLMModel):
    @override
    def _build_result(self, resp: Any) -> Any:
        # TODO 直接返回文本就可以，让使用者自行解析？
        text = resp.choices[0].message.content or ""
        text = text.strip()
        result: dict[str, Any] = {"row_num": 0, "col_num": 0, "cells": []}

        if not text:
            return result

        if text.startswith("<table"):
            # html的表格解析
            return KTable.parse_html(text)
        else:
            return KTable.parse_otsl(text)


class TestModel(Model):
    def __init__(self):
        super().__init__()
        self._use_lock = False

    @override
    def _execute(self, files: Sequence[FileInfo]):
        results: list[Any] = []
        for i, file in enumerate(files):
            print(f"execute file={file.file}")
            results.append({})
        return results


class LocalService:
    pass


def test():
    args: dict[str, Any] = {
        "scheduler": {
            "policy": "fifo",
            "max_task_size": 2,
        },
        # 本地启动
        # 但是，也可以启动为一个服务，然后调用api就可以
        # 这个时候，为了简化配置
        "model": {"name": "TestModel", "args": [], "kwargs": {}},
        "api": {"url": "http://"},
    }
    executor = ModelExecutor(args)
    executor.execute(["1.png", "2.png", "3.png"])

    # 如果是内部使用，执行这个，自动调度
    executor.submit([])

    # 如果是提供api，使用，task_manager执行
    executor.execute([])
