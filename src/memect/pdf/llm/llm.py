from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any, Final, Iterator, Mapping, Sequence

import PIL
import PIL.Image
from openai import OpenAI
from pydantic import Field

from memect.pdf.base import KDocument, KPage

from memect.base import images
from memect.base.job import Scheduler, SchedulerArgs
from memect.base.utils import MyBaseModel

@dataclass
class RequestArgs:
    file: Path | bytes
    exif:bool=False
    rotation:int=0
    number: int = 1
    """页码，1表示第一页"""
    page:KPage|None=None


class ModelArgs(MyBaseModel):
    name: str
    """内部使用的名字，用来区分"""
    # =======模型相关的设置
    model: str
    base_url: str
    api_key: str
    prompt: str
    temperature: float = 0
    max_tokens: int = 8192
    openai: Mapping[str, Any] | None = None
    extra_body: Mapping[str, Any] | None = None
    params: Mapping[str, Any] | None = None
    """请求的额外参数，可以根据不同的模型设置"""
    # ===========================
    # ========图片相关的设置========
    max_image_size: tuple[int, int]|None = (2000, 2000)
    """如果设置了，表示不超过，image_size设置了就忽略这个"""
    image_size:tuple[int,int]|None=None
    """如果设置了，表示使用固定大小"""
    image_format: str|None = "png"
    """如果为None，表示使用原图的格式"""

    scheduler: SchedulerArgs = Field(default_factory=SchedulerArgs)


class Task:
    def __init__(self, model: "Model", args: RequestArgs):
        super().__init__()
        self.model: Final = model
        self.args: Final = args

        self.number: Final = args.number
        self.file: Final = args.file

        self.max_image_size: Final = model.args.max_image_size
        self.image_format: Final = model.args.image_format
        self.prompt: Final = model.args.prompt

        self.results: list[tuple[Any, Any]] = []
        self.images: list[tuple[PIL.Image.Image,PIL.Image.Image]]=[]

        self._prepare()

    def _prepare(self):
        """子类可以做些操作"""
        pass

    def build_messages(self) -> Iterator[tuple[Any, Any]]:
        image = images.open(self.file,exif=self.args.exif,rotation=self.args.rotation)
        llm_image,url = images.to_url(image,max_size=self.max_image_size,format=self.image_format)
        prompt = self.prompt
        #保存llm使用的图片
        self.images.append((image,llm_image))
        yield (
            "0",
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": url}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )

    def add_result(self, item: Any, resp: Any):
        self.results.append((item, resp))

    def post_process(self) -> Any:
        assert len(self.images)==len(self.results)
        #如果需要转换坐标
        results:dict[Any,Any]={}
        for (image,llm_image),(key,resp) in zip(self.images,self.results):
            results[key]=self._process_one(image,llm_image,resp.choices[0].message.content or "")
        return results
    
    def _process_one(self,image:PIL.Image.Image,llm_image:PIL.Image.Image,text:str)->Any:
        """默认直接返回原始的内容，有些情况子类需要重写，如：有坐标的，且坐标是相对llm_image，就需要计算，如果是归一化坐标，就不需要做处理"""
        return text


class Model:
    """轻量级对象，执行序列化"""

    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self, factory: type[Task], args: ModelArgs):
        super().__init__()
        self.args: Final = args
        self._factory: Final = factory
        self._name: Final = args.name
        self._model: Final = args.model
        self._params: Final = self._create_params(args)
        # 暂时使用openai的sdk
        self._api: Final = self._create_openai(args)
        self._scheduler = Scheduler(
            self._name, self.execute, **args.scheduler.model_dump()
        )

        

    def close(self):
        self._scheduler.close()
    
    def _create_openai(self, args: ModelArgs) -> OpenAI:
        # 实际上都不需要使用这个库，自己使用httpx.post即可，避免依赖第三方，因为就是一个简单的api请求
        # 暂时就还是继续使用OpenAI这个库，这个库太繁琐了
        kwargs: dict[str, Any] = {"base_url": args.base_url, "api_key": args.api_key}
        if args.openai:
            kwargs.update(args.openai)
        return OpenAI(**kwargs)

    def _create_params(self, args: ModelArgs) -> Mapping[str, Any]:
        params: dict[str, Any] = {
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            "extra_body": args.extra_body,
        }
        if args.params:
            params.update(args.params)
        return params

    def parse(self,name:str,doc:KDocument):
        pages:list[KPage]=[]
        items:list[RequestArgs]=[]
        for page in doc.pages:
            if page.skipped:
                continue
            if doc.is_dev() and doc.has_file(f'{name}/{page.number}.json'):
                continue
            pages.append(page)
            items.append(RequestArgs(file=page.file,page=page))
        
        if len(pages)>0:
            for page,result in zip(pages,self.submit(items).wait()):
                page.cache[name]=result
                #如果为开发模式，会保存
                if doc.is_dev():
                    doc.write(f'{name}/{page.number}.json',result)

        
        for page in doc.pages:
            if page.skipped:
                continue

            if page not in pages:
                #已经存在的
                page.cache[name]=doc.read_json(f'{name}/{page.number}.json')

    def submit(self, items: Sequence[RequestArgs]):
        """排队等待"""
        return self._scheduler.submit(items)

    def execute(self, args: RequestArgs) -> Any:
        """直接执行"""
        task = self._factory(self, args)
        # TODO 有些只需要调用一次，有些一个页面截图为多个图片，然后多次调用
        for item, messages in task.build_messages():
            resp = self._api.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
                **self._params,
            )
            task.add_result(item, resp)
        return task.post_process()

class LLMArgs(MyBaseModel):
    default: str = ""
    models: dict[str, dict[str, Any]] = Field(default_factory=dict)


class LLM:
    def __init__(self, args: LLMArgs|Mapping[str,Any]):
        super().__init__()
        args = LLMArgs.create(args)
        self._models: dict[str, Model] = {}
        for k, v in args.models.items():
            self._models[k] = Model.from_dict(k, v)

        if args.default:
            self._models["default"] = self._models[args.default]
        elif self._models:
            default = next(iter(self._models.keys()))
            self._models["default"] = self._models[default]
        else:
            pass

    def submit(self, items: Sequence[RequestArgs], *, name: str = "default"):
        return self.get_model(name).submit(items)

    def execute(self, args: RequestArgs, *, name: str = "default"):
        return self.get_model(name).execute(args)

    def get_model(self, name: str) -> Model:
        return self._models[name]




