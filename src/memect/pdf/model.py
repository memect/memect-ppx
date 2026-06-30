import logging
import multiprocessing as mp
import threading
import time
import weakref
from concurrent.futures import Executor, Future, ProcessPoolExecutor, ThreadPoolExecutor
from enum import StrEnum, auto
from pathlib import Path
from typing import Any, Callable, ClassVar, Final, Mapping, Sequence, final, override

import cv2
import numpy as np
import PIL
import PIL.Image
from openai import OpenAI
from pydantic import BaseModel, Field

from memect.base import lists, utils
from memect.base.config import MPInit, get_settings
from memect.base.debug import XDebugger
from memect.base.job import Scheduler, SchedulerArgs
from memect.base.task import Runner, Task
from memect.base.utils import MyBaseModel, SafeExecutor, Timer

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
    chunk_size: int = 10
    """在submit的时候，多少个一批，如：有些模型的批处理为5，一次性输入5个可以获得最好的性能，就可以设置为5"""
    max_workers: int = 0
    """表示创建多少个模型，0表示仅仅创建1个，且不使用多线程和多进程，方便测试"""
    use_process: bool = False
    """True表示使用多进程，False表示使用多线程"""
    max_idle_timeout: float | None = None
    """表示进程或者线程空闲了多长时间就释放，None表示不会释放"""
    scheduler: SchedulerArgs = Field(default_factory=SchedulerArgs)
    model: ModelArgs | str
    """如果为字符串，表示使用settings中的设置"""
    # settings: Mapping[str, ModelArgs] = Field(default_factory=dict)
    use_scheduler: bool = False
    """True表示使用scheduler"""
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
        assert not isinstance(args.model, str)
        # self._args:Final = args
        self._name: Final = args.name
        self._use_scheduler: Final = args.use_scheduler
        self._chunk_size: Final = args.chunk_size
        self._use_process: Final = args.use_process
        self._model_cfg: Final = args.model
        # 如果是本地执行，建议：scheduler.max_task_size>=args.max_workers
        self._max_workers: Final = args.max_workers
        self._model: Model | None = None
        self._executor: Executor | None = None
        self._thread_local: Final = threading.local()

        if self._max_workers > 0:
            self._executor = SafeExecutor(
                self._new_executor, max_idle_timeout=args.max_idle_timeout
            )
        else:
            # 表示只需要一个，方便测试，或者一个就足够了，如：通过api调用的
            # 模型需要支持多线程
            self._model = self._create_model(self._model_cfg)

        self._scheduler = Scheduler(
            args.name, self._execute, **args.scheduler.model_dump()
        )

        self._finalizer = weakref.finalize(
            self, self._close, self._executor, self._scheduler
        )

    @classmethod
    def _close(cls, executor: Executor | None, scheduler: Scheduler[Any, Any] | None):
        if executor:
            executor.shutdown(True, cancel_futures=True)
        if scheduler:
            scheduler.close()

    def close(self):
        if self._finalizer.alive:
            self._finalizer()
        del self._executor
        del self._scheduler
        del self._model

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
            # TODO 如果不考虑timeout，现在使用execute更好
            if self._use_scheduler:
                job = self.submit([item[0] for item in items])
                results = lists.flat(job.wait(timeout=timeout))
            else:
                results = self.execute([item[0] for item in items])

            for item, result in zip(items, results):
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

    def execute(self, files: Sequence[_Image]):
        if self._executor:
            if self._use_process:
                fn = self._execute_on_process
            else:
                fn = self._execute_on_thread
            futures: list[Future[Any]] = []
            for i in range(0, len(files), self._chunk_size):
                futures.append(
                    self._executor.submit(fn, files[i : i + self._chunk_size])
                )
            return lists.flat([f.result() for f in futures])
        elif self._model:
            # 轻量级且支持多线程的
            return self._model.execute(files)
        else:
            raise RuntimeError("不可能执行到这里")

    def _execute(self, files: Sequence[_Image]) -> list[Any]:
        """直接执行"""
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
            mp_init = MPInit(name=f"{self._name}_executor")
            mp_init.set_fn(self._init_process, self._model_cfg)
            return ProcessPoolExecutor(
                self._max_workers,
                mp_context=mp.get_context("spawn"),
                initializer=mp_init,
            )
        else:
            return ThreadPoolExecutor(
                self._max_workers,
                thread_name_prefix=f"{self._model_cfg.name}",
                initializer=self._init_thread,
                initargs=(self._model_cfg,),
            )

    def _init_thread(self, cfg: ModelArgs):
        if hasattr(self._thread_local, "model"):
            # 出现这个错误，是线程池重复初始化同一个线程了
            raise RuntimeError("编程错误，该线程已经初始化了")
        self._thread_local.model = self._create_model(cfg)
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


class ModelMode(StrEnum):
    SERVER = auto()
    """表示在服务模式，模型通常在独立的进程"""
    COMMAND = auto()


class ModelManagerArgs(MyBaseModel):
    executors: dict[str, ModelExecutorArgs] = Field(default_factory=dict)
    models: dict[str, ModelArgs] = Field(default_factory=dict)


class ModelManager:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self, args: ModelManagerArgs | Mapping[str, Any] | None = None):
        super().__init__()
        if args is None:
            args = get_settings("model_manager")
        args = ModelManagerArgs.create(args)
        self._executors: dict[str, ModelExecutor] = {}
        self._models: dict[str, ModelArgs] = {}

        # alias_mapping: dict[str, str] = {}
        models: dict[str, ModelArgs] = {}
        for name, value in args.models.items():
            models[name] = value

        for name, executor_args in args.executors.items():
            if executor_args.enable:
                executor_args.name = name
                if isinstance(executor_args.model, str):
                    self._logger.info(
                        "create model executor name=%s,model=%s",
                        name,
                        executor_args.model,
                    )
                    executor_args.model = models[executor_args.model]
                else:
                    self._logger.info("create model executor name=%s", name)

                self._executors[name] = ModelExecutor(executor_args)
            else:
                self._logger.info("disable model executor name=%s", name)

        self._finalizer = weakref.finalize(self, self._close, self._executors)

    @classmethod
    def _close(cls, executors: dict[str, ModelExecutor]):
        cls._logger.info("start close modelmanager")
        executors2 = dict(executors)
        executors.clear()
        for name, executor in executors2.items():
            cls._logger.info("close executor name=%s", name)
            executor.close()
        cls._logger.info("end close modelmanager")

    def close(self):
        if self._finalizer.alive:
            self._finalizer()

    def get(self, name: str) -> ModelExecutor:
        return self._executors[name]


class LayoutModel(Model):
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _use_lock = False

    def __init__(self, **kwargs: Any):
        super().__init__()
        self._model: Any = None
        self._model_kwargs = kwargs

    @override
    def _execute(self, files: Sequence[FileInfo]):
        from memect.pdf.layout import get_mapping

        with self._lock:
            if self._model is None:
                from memect.pdf.layout import LayoutDetector
                from memect.models import get_model_path
                timer = Timer.start()
                params:dict[str,Any]={}
                params.update(self._model_kwargs)
                model_path = params.get('model_path')
                version = params.get('version')
                if not model_path:
                    if version=='v2':
                        model_path=get_model_path('pp_layoutv2.onnx')
                    elif version=='v3':
                        model_path=get_model_path('pp_layoutv3.onnx')
                    elif version=='auto':
                        model_path=get_model_path('pp_layoutv3.onnx')
                    else:
                        raise ValueError(f'不支持的version:{version}')
                    params['model_path']=model_path
                self._model = LayoutDetector(**params)
                self._logger.info("load layout model,elapsed=%.3f", timer.elapsed())

        results: list[Any] = []
        for file in files:
            output = self._model(file.file)
            size = file.size
            new_objs: list[Any] = []
            mapping = get_mapping(output["version"])
            for obj in output["objects"]:
                new_obj = {}
                new_obj["type"] = mapping.get(obj["type"]) or obj["type"]
                new_obj["bbox"] = obj["rect"]
                new_obj["score"] = obj["score"]
                new_obj["raw_type"] = obj["type"]
                new_objs.append(new_obj)

            results.append({"objects": new_objs, "width": size[0], "height": size[1]})
        return results


class OCRModel(Model):
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self, **kwargs: Any):
        super().__init__()
        self._model_kwargs = kwargs
        self._model: Any = None

    def _execute(self, files: Sequence[FileInfo]):
        from .ocr import PPOCRv6OCR
        from memect.models import get_ocr_path

        det_score_threshold = self._model_kwargs.get("det_score_threshold")
        with self._lock:
            if self._model is None:
                timer = Timer.start()
                # tiny,small,medium
                model_size = self._model_kwargs.get("model", "small")
                params: dict[str, Any] = {
                    "det_model_path": self._model_kwargs.get("det_model_path")
                    or get_ocr_path("det", model_size),
                    "rec_model_path": self._model_kwargs.get("rec_model_path")
                    or get_ocr_path("rec", model_size),
                    "use_cuda": self._model_kwargs.get("use_cuda"),
                    "use_cann": self._model_kwargs.get("use_cann"),
                    "use_dml": self._model_kwargs.get("use_dml"),
                    "engine": self._model_kwargs.get("engine"),
                }
                self._model = PPOCRv6OCR(**params)
                self._logger.info("load ocr model,elapsed=%.3f", timer.elapsed())

        def to_point(p: Any) -> Any:
            return (float(p[0]), float(p[1]))

        def to_quad(box: Any) -> Any:
            p1, p2, p3, p4 = box
            return (to_point(p1), to_point(p2), to_point(p3), to_point(p4))

        def adjust_boxes(
            boxes: list[Any], x_overlap_ratio: float = 0.7, min_overlap_y: float = 1
        ) -> Any:
            if len(boxes) < 2:
                return boxes
            result = [b.copy() for b in boxes]
            for i in range(len(result)):
                for j in range(i + 1, len(result)):
                    a, b = result[i], result[j]
                    # 确定上下关系
                    if a[:, 1].mean() > b[:, 1].mean():
                        a, b = b, a
                    # x 相交比例
                    x_inter = min(a[:, 0].max(), b[:, 0].max()) - max(
                        a[:, 0].min(), b[:, 0].min()
                    )
                    min_w = min(
                        a[:, 0].max() - a[:, 0].min(), b[:, 0].max() - b[:, 0].min()
                    )
                    if min_w <= 0 or x_inter / min_w < x_overlap_ratio:
                        continue
                    # y 方向相交
                    a_bottom, b_top = a[:, 1].max(), b[:, 1].min()
                    if a_bottom - b_top < min_overlap_y:
                        continue
                    mid = (a_bottom + b_top) / 2
                    if mid - 1 <= a[:, 1].min() or mid + 1 >= b[:, 1].max():
                        continue
                    a[np.argsort(a[:, 1])[-2:], 1] = mid - 1
                    b[np.argsort(b[:, 1])[:2], 1] = mid + 1
            return result

        model: PPOCRv6OCR = self._model
        use_preferred_bbox = True
        results: list[Any] = []
        for file in files:
            # 这个模型的其他参数，None表示不设置，使用配置的值
            # 但是，如果设置了一次，就会直接改变配置的值，所以，要么都不设置，要么每次都设置

            # 代码是支持PIL.Image.Image，但是接口的类型注释没有

            # TODO 对于超长或者超宽的图片，需要分成多个图片进行识别
            cv2_img = file.cv2_image
            output = model.predict(cv2_img, det_score_threshold=det_score_threshold)
            objs: list[Any] = []
            size = file.size
            # height = size[1]
            if output:
                boxes: list[Any] = [np.array(item["box"]) for item in output]
                txts: list[Any] = [item["text"] for item in output]
                scores: list[Any] = [item["rec_score"] for item in output]
                if use_preferred_bbox:
                    boxes = adjust_boxes(boxes)
                else:
                    pass
                for box, score, text in zip(boxes, scores, txts):
                    # 返回的box是unclip后的结果，扩大了一些
                    # to_quad(box)
                    # to_quad2(box) 稍微内收了一点
                    if use_preferred_bbox:
                        box = self._shrink_bbox_any_bg(cv2_img, box)
                    # TODO 有些情况，会把2行文字识别为一个box，而且没有识别全部字
                    # 这种情况下，如下：被识别为一个box，而且仅仅返回“AB”，或者“ABC”，或者“ABCD”，导致字符的宽度/高度计算错误
                    # AB
                    # CD

                    if text and score > 0:
                        obj = {
                            "text": text,
                            # 原点为左上角
                            "quad": to_quad(box),
                            "score": round(score, 2),
                        }
                        objs.append(obj)
                    else:
                        pass
            else:
                pass
            results.append({"spans": objs, "width": size[0], "height": size[1]})
        return results

    def _shrink_bbox_any_bg(
        self,
        image: np.ndarray,
        bbox: np.ndarray,
        padding: int = 1,
    ) -> np.ndarray:

        # bbox的区域可能大了一点，包含了单元格的边界线，导致认为也是笔画，无法计算更精确的字符区域，导致该字符的区域过大
        x, y, w, h = cv2.boundingRect(bbox.astype(np.float32))
        roi = image[y : y + h, x : x + w]

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi

        # 用边缘像素中位数估计背景色，适配任意背景颜色
        border = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
        bg_value = int(np.median(border))
        diff = cv2.absdiff(gray, np.full_like(gray, bg_value))

        # 检测并遮盖表格线（细长的水平/垂直连通区域），避免其梯度干扰文字bbox
        _, line_bin = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        line_mask = np.zeros_like(line_bin)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(line_bin, connectivity=8)
        for i in range(1, n):
            cw, ch = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
            if cw > 0 and ch > 0 and (cw / ch > 8 or ch / cw > 8):
                line_mask[labels == i] = 255
        gray_masked = gray.copy()
        gray_masked[line_mask > 0] = bg_value
        # 消除紧贴边缘的竖线/横线梯度
        gray_masked[:, :2] = bg_value
        gray_masked[:, -2:] = bg_value
        gray_masked[:2, :] = bg_value
        gray_masked[-2:, :] = bg_value

        grad_x = cv2.Sobel(gray_masked, cv2.CV_32F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(gray_masked, cv2.CV_32F, 0, 1, ksize=3)
        grad = cv2.magnitude(grad_x, grad_y)
        grad = cv2.normalize(grad, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)

        _, binary = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

        # 清除binary边缘残留（Sobel+morphology会让边缘梯度向内扩散）
        edge = 3
        binary[:edge, :] = 0
        binary[-edge:, :] = 0
        binary[:, :edge] = 0
        binary[:, -edge:] = 0

        coords = cv2.findNonZero(binary)

        # DEBUG
        if False:
            import hashlib
            import os

            dbg_dir = "./local/shrink_debug"
            os.makedirs(dbg_dir, exist_ok=True)
            key = hashlib.md5(bbox.tobytes()).hexdigest()[:6]
            cv2.imwrite(f"{dbg_dir}/{key}_roi.png", roi)
            cv2.imwrite(f"{dbg_dir}/{key}_gray_masked.png", gray_masked)
            cv2.imwrite(f"{dbg_dir}/{key}_binary.png", binary)

        if coords is None:
            return bbox

        rx, ry, rw, rh = cv2.boundingRect(coords)
        rx = max(rx - padding, 0)
        ry = max(ry - padding, 0)
        rw = min(rw + 2 * padding, w - rx)
        rh = min(rh + 2 * padding, h - ry)

        x1, y1 = x + rx, y + ry
        x2, y2 = x + rx + rw, y + ry + rh
        return np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float32)


class FormulaPPModel(Model):
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _use_lock = False

    def __init__(self, **kwargs: Any):
        super().__init__()
        self._model = None
        self._model_kwargs = kwargs

    @override
    def _execute(self, files: Sequence[FileInfo]):
        if self._model is None:
            with self._lock:
                from memect.pdf.formula_pp import Parser

                if self._model is None:
                    timer = utils.Timer.start()
                    self._model = Parser(**self._model_kwargs)
                    self._logger.info(
                        "load fromula model,elapsed=%.3f", timer.elapsed()
                    )

        results: list[Any] = []
        for file in files:
            t1 = time.monotonic()
            res = self._model.parse(file.cv2_image)
            results.append({"latex": res, "elapsed": time.monotonic() - t1})
        return results


class FormulaMfrModel(Model):
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _use_lock = False

    def __init__(self, **kwargs: Any):
        super().__init__()
        # from memect.pdf.mfr import Parser
        self._model = None  # Parser(**kwargs)
        self._model_kwargs = kwargs

    @override
    def _execute(self, files: Sequence[FileInfo]):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from memect.pdf.formula_mfr import Parser

                    timer = utils.Timer.start()
                    self._model = Parser(**self._model_kwargs)
                    self._logger.info("load formula elapsed=%.3f", timer.elapsed())

        results: list[Any] = []
        for file in files:
            t1 = time.monotonic()
            res = self._model.parse(file.cv2_image)
            results.append({"latex": res, "elapsed": time.monotonic() - t1})
        return results


class TableModel(Model):
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _use_lock = False

    def __init__(self, *, model_path: str | Path | None = None, **kwargs: Any):
        super().__init__()
        self._model_path = model_path
        self._model_kwargs = kwargs
        self._model: Any = None

    @override
    def _execute(self, files: Sequence[FileInfo]):
        with self._lock:
            if self._model is None:
                from .table_det import RTDETRTableCellDet
                from memect.models import get_model_path
                if not self._model_path:
                    model_path = get_model_path("table_det.onnx")
                else:
                    model_path = Path(self._model_path)
                timer = utils.Timer.start()
                self._model = RTDETRTableCellDet(model_path, **self._model_kwargs)
                self._logger.info("load table model,elapsed=%.3f", timer.elapsed())

        results: list[Any] = []
        for file in files:
            result = self._model(file.cv2_image, show_gui=False)
            results.append(result)
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
        # TODO 因为这里没有并行执行，所以，ModelExecutor的max_workers可以设置为n个
        results: list[Any] = []
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


class MockModel(Model):
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
