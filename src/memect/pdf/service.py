import asyncio
import gzip
import logging
import shutil
import uuid
from enum import StrEnum, auto
from pathlib import Path
from typing import Annotated, Any, Final, Mapping, Sequence

import anyio
import PIL
import PIL.Image
from fastapi import Body, FastAPI, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import Field
from starlette.background import BackgroundTask
from starlette.datastructures import UploadFile

from memect.base.api import ApiError, ApiInfo, FileType
from memect.base.config import get_settings
from memect.base.task import Saver, Task, TaskManager, TaskManagerArgs
from memect.base.utils import AutoCleaner, MyBaseModel
from .base import ApiParams, KDocument, ParseParams
from .parser import Parser, ParserArgs


def parse_params(params: Annotated[str | None, Query()] = None) -> ParseParams:
    # 这里的参数的定义对应query，不能够随便使用名字
    if not params:
        params_obj = ParseParams()
    else:
        obj = ApiParams.model_validate_json(params)
        params_obj = ParseParams.model_validate(obj.model_dump())
    params_obj.api = True
    return params_obj


class KeepFilePolicy(StrEnum):
    all = auto()
    """保存全部文件"""
    error = auto()
    """仅仅保存错误的文件"""
    no = auto()
    """不保存文件"""


class ImageSettings(MyBaseModel):
    exts: Sequence[str] = ("png", "jpg", "jpeg", "bmp", "webp")
    max_file_size: int = 100 * 1024 * 1024
    max_image_size: tuple[int, int] = (10000, 10000)


class PdfSettings(MyBaseModel):
    exts: Sequence[str] = "pdf"
    max_file_size: int = 1024 * 1024 * 1024
    max_page_count: int = 10000

    priorities: Sequence[int] = tuple([2000, 1000, 500, 0])
    """设置级别的划分，如：[2000,1000,500,0]，表示>=20000页为1级，>=1000页为2级,>=500页为3级"""

    def get_priority(self, page_count: int) -> int:
        for i, threshold in enumerate(self.priorities):
            if page_count >= threshold:
                return i + 1
        return len(self.priorities) + 1
    


class PdfServiceArgs(MyBaseModel):
    name: str = "parse"
    data_dir: Path = Path("./data/pdf")
    keep_file_policy:KeepFilePolicy=KeepFilePolicy.error
    image: ImageSettings = Field(default_factory=ImageSettings)
    pdf: PdfSettings = Field(default_factory=PdfSettings)
    task_manager: TaskManagerArgs = Field(default_factory=TaskManagerArgs)


def format_size(size: int) -> str:
    remain_size: float = size
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if remain_size < 1024:
            return f"{remain_size:.2f} {unit}"
        remain_size /= 1024
    return f"{remain_size:.2f} PB"


def decompress(
    input_filename: str | Path, output_filename: str | Path, max_file_size: int
):
    """解压，在解压的过程中再检查大小"""
    with gzip.GzipFile(input_filename, "rb") as gfp:
        n = 0
        with open(output_filename, "wb") as fp:
            while True:
                # 每次仅仅读取64K，而不是一次读取完成
                data = gfp.read(64 * 1024)
                if not data:
                    break
                fp.write(data)
                n += len(data)
                if n > max_file_size:
                    raise ApiError(
                        ApiError.ANY,
                        f"最大允许:{max_file_size}，该文件使用gzip压缩上传，解压后为超过了",
                    )


class MethodConfig(MyBaseModel):
    name:str
    args:Sequence[Any]=Field(default_factory=tuple)
    kwargs:dict[str,Any]=Field(default_factory=dict)

class PdfService:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self,args: PdfServiceArgs | Mapping[str, Any] | None = None):
        super().__init__()
        if args is None:
            args = get_settings('pdf_service')
        args = PdfServiceArgs.create(args)
        self._settings: Final = args
        self._name: Final = args.name
        self._image_settings: Final = args.image
        self._pdf_settings: Final = args.pdf

        self._task_manager: Final = TaskManager(args.task_manager)
        
        self._data_dir: Final = args.data_dir.resolve()
        self._tasks_dir: Final = self._data_dir / "tasks"
        self._temp_dir: Final = self._data_dir / "temp"
        self._files_dir:Final=self._data_dir/'files'
        self._keep_file_policy:Final=args.keep_file_policy

        #TODO 为了简便，使用默认的设置，否则就需要再传递参数
        self._parser:Parser=Parser()

        def ensure_dir(dir:Path,clean:bool=False):
            if clean and dir.is_dir():
                shutil.rmtree(dir)
            dir.mkdir(parents=True,exist_ok=True)

        # 每次启动，可以清除temp
        ensure_dir(self._temp_dir,clean=True)
        ensure_dir(self._tasks_dir,clean=True)
        ensure_dir(self._files_dir,clean=self._keep_file_policy==KeepFilePolicy.no)

    def setup(self, app: FastAPI):
        """表示需要设置api"""
        self._task_manager.start()
        # 执行解析服务
        app.post("/api/parse")(self._parse)
        # 获得异步解析的结果
        app.get("/api/parse")(self._get_result)

        app.get('/api/parse/state')(self._get_state)

        if False:
            app.post('/api/parse/invoke')(self._invoke_method)
    

    def _get_state(self)->Any:
        return {'data':self._task_manager.state()}
    
    def get_info(self) -> ApiInfo:
        types: list[FileType] = []
        types.append(
            {
                "name": "image",
                "exts": self._image_settings.exts,
                "max_length": self._image_settings.max_file_size,
                "max_size": self._image_settings.max_image_size,
            }
        )
        types.append(
            {
                "name": "pdf",
                "exts": self._pdf_settings.exts,
                "max_length": self._pdf_settings.max_file_size,
                "max_page_count": self._pdf_settings.max_page_count,
            }
        )
        file: dict[str, Any] = {"name": "file", "types": types}

        return {
            "name": self._name,
            "url": f"/api/{self._name}",
            "allow_async": True,  # self._allow_async,
            "allow_timeout": True,  # self._allow_timeout,
            "allow_form": True,  # self._allow_form,
            "allow_task_id": True,  # self._allow_custom_task_id,
            "file": file,
            "schema": ApiParams.model_json_schema(),
            # TODO 这个可以根据schema自动生成
            "defaults": {},
        }

    async def close(self):
        # 如果需要等待所有的任务完成
        wait = True
        if wait:
            await self._task_manager.astop()
        else:
            self._task_manager.stop()
        pass

    async def _parse(
        self,
        request: Request,
        async_: Annotated[bool, Query(alias="async")] = False,
        
    ):
        # 直接获得，无法限制大小
        # data =await request.body()

        def check_file_size(size: int, max_size: int):
            if size > max_size:
                raise ApiError(
                    ApiError.ANY,
                    f"文件最大允许为:{format_size(max_size)}，现在为:{format_size(size)}，超过了:{format_size(max_size - size)}",
                )

        def check_content_length(max_file_size: int):
            """快速检测一下，虽然不可信任"""
            headers = request.headers
            # 如果请求使用了gzip等，这个为压缩后的，也就是接收到的大小
            content_length = headers.get("Content-Length", "0")
            if content_length:
                try:
                    content_length = int(content_length)
                    if content_length > 0:
                        check_file_size(content_length, max_file_size)
                except ValueError:
                    pass

        async def save_file(file: Path, max_file_size: int,upload_file:UploadFile|None=None):
            file.parent.mkdir(exist_ok=True)
            total: int = 0
            async with await anyio.open_file(file, "wb") as fp:
                if upload_file:
                    #一次性读取最大的，或者分批读取也可
                    #buf_size=max_file_size+1
                    buf_size=100*1024*1024
                    while True:
                        content = await upload_file.read(buf_size)
                        if not content:
                            break
                        total+=len(content)
                        check_file_size(total,max_file_size)
                        await fp.write(content)
                else:
                    async for chunk in request.stream():
                        total += len(chunk)
                        check_file_size(total, max_file_size)
                        await fp.write(chunk)

        def check_pdf(doc: KDocument):
            check_file_size(doc.file.stat().st_size, self._pdf_settings.max_file_size)
            if doc.page_count > self._pdf_settings.max_page_count:
                raise ApiError(
                    ApiError.ANY,
                    f"最大允许:{self._pdf_settings.max_page_count}页，现在为:{doc.page_count}",
                )

        def check_image(doc: KDocument):
            check_file_size(doc.file.stat().st_size, self._image_settings.max_file_size)
            try:
                # 耗时：0.00x
                with PIL.Image.open(doc.file) as image:
                    size = image.size
            except Exception as e:
                self._logger.exception("上传的图片无效")
                raise ApiError(ApiError.ANY, "不是有效的图片") from e

            max_width, max_height = self._image_settings.max_image_size
            if size[0] > max_width or size[1] > max_height:
                raise ApiError(
                    ApiError.ANY,
                    f"图片最大允许为:{self._image_settings.max_image_size},现在为:{size}",
                )

        def is_form_request(request: Request) -> bool:
            content_type, _, _ = request.headers.get("content-type", "").partition(";")
            return content_type.strip() in (
                "multipart/form-data",
                #"application/x-www-form-urlencoded",
            )
        content_encoding = request.headers.get("Content-Encoding")
        if content_encoding:  # and content_encoding != 'gzip':
            # 可能使用http_status=400更合适？
            raise ApiError(ApiError.ANY, f"不支持content-encoding={content_encoding}")
        # 不管是图片还是pdf，先使用最大的保存文件
        max_file_size: int = max(
            self._pdf_settings.max_file_size, self._image_settings.max_file_size
        )

        self._tasks_dir.mkdir(parents=True, exist_ok=True)
        self._files_dir.mkdir(parents=True, exist_ok=True)
        self._temp_dir.mkdir(parents=True, exist_ok=True)

        # 先保存到临时目录
        temp_file = self._temp_dir / uuid.uuid4().hex

        use_form=is_form_request(request)
        if not use_form:
            params = ParseParams.create(request.query_params.get('params'))
            # 初步检查文件大小，如果使用form，就不检查了？
            check_content_length(max_file_size)
            # 保存到文件系统
            await save_file(temp_file, max_file_size)
        else:
            form = await request.form()
            upload_file = form.get('file')
            if not isinstance(upload_file,UploadFile):
                raise ApiError(ApiError.ANY,'使用form上传，file必须为文件')
            params_str = form.get('params','')
            if not isinstance(params_str,str):
                raise ApiError(ApiError.ANY,'使用form上传，params必须为字符串')
            params = ParseParams.create(params_str)
            await save_file(temp_file,max_file_size,upload_file)
        
        params.api=True
        # 先获得一个任务的id
        task_id = Task.next_id()
        out_dir = self._tasks_dir / task_id
        out_dir.mkdir(parents=True)

        # filetype,ext = KDocument.get_file_type(temp_file)
        # 不用添加扩展名，因为不重要，目前也不知道
        file = out_dir / "a"
        file = temp_file.replace(file)

        # 轻量级对象
        doc = KDocument(file, out_dir=out_dir, params=params,auto_rename=True)
        if doc.is_pdf():
            check_pdf(doc)
        elif doc.is_image():
            check_image(doc)
        else:
            # TODO 添加了新的类型？
            raise RuntimeError("不可能执行到这里")

        saver:Saver|None=None
        if self._keep_file_policy!=KeepFilePolicy.no:
            save_dir = self._files_dir/task_id
            save_dir.mkdir(parents=True,exist_ok=True)
            if self._keep_file_policy==KeepFilePolicy.all:
                #如果不管成功还是失败都要保存，就先保存，然后通过saver保存错误信息
                saver = Saver(save_dir)
                shutil.copyfile(doc.file,saver.dir/doc.file.name)
            elif self._keep_file_policy==KeepFilePolicy.error:
                #如果仅仅需要保存错误的文件，就先不报错文件，等有错误的时候再保存
                saver = Saver(save_dir,[doc.file])
            else:
                pass

        
        # 在api调用，对象没有被使用了，就可以自动清除
        # doc.set_auto_clean(True)
        # 获得需要解析的页面数
        doc.priority = self._pdf_settings.get_priority(doc.page_count)
        #生命周期
        #doc最短，当解析完毕，doc就别回收了
        #当task回收，task.object就会被回收，所以task.object可以做清理的工作
        #但是因为需要先返回内容，所以两种做法
        #先读取返回的内容，释放task，task.object
        #先引用task 或者 task.object，在返回后再解除引用BackgroundTask
        #如果需要更快一点，可以手动task.object.clean()
        #同样的，parse()为内部函数，也引用了doc，所以在执行后就可以释放了
        task = Task(
            task_id,
            doc.get_auto_cleaner(),
            self._parser.new_runner(doc),
            priority=doc.priority,
            async_=async_,
            saver=saver,
            details={
                'page_count':doc.page_count,
                'priority':doc.priority,
            }
        )
        self._task_manager.add_task(task)
        doc=None
        if not task.async_():
            # 异常已经转换为ApiError
            return await self._return_result(task,timeout=params.timeout)
        else:
            #
            if params.timeout is not None and params.timeout>0:
                asyncio.create_task(self._wait_timeout(task,timeout=params.timeout))
            return {'data':{"id": task.custom_id or task.id}}

    async def _get_result(
        self,
        task_id: Annotated[str, Query(max_length=100)],
        custom: Annotated[bool, Query()] = False,
    ) -> Any:
        task = self._task_manager.get_task(task_id, custom=custom,remove_if_done=True)
        if task is None or not task.async_():
            # 如果不是异步任务，不允许轮训，一样认为任务不存在
            raise ApiError(ApiError.ANY, f"任务不存在:{task_id}")

        if task.done():
            # 成功/失败/被取消，会自动抛出ApiError
            return await self._return_result(task)
        else:
            # 等待中或者执行中
            raise ApiError(ApiError.ANY, "等待或者执行中", status=task.status())
            # 为了兼容老的api接口，code='running'，现在在error.jsonify()做兼容处理
            #raise ApiError('running', "等待或者执行中", status=task.status())

    async def _return_result(self, task: Task,timeout:float|None=None):
        #在这里返回结果后，task被释放，object没有引用了，也会自动被释放，然后会自动清除使用到的目录
        #获得返回的结果，目前不需要使用这个的返回，但是必须要调用
        zip_file:Path = await task.result(timeout=timeout)
        cleaner: AutoCleaner = task.object
        if not zip_file.is_file():
            #执行到这里，可能结果文件已经被删除了？不应该执行到这里
            raise ApiError(ApiError.ANY, "执行失败，无法生成结果")

        use_file = True
        if use_file:
            #必须使用BackgroundTask来释放，因为需要等文件发送完毕
            return FileResponse(
                zip_file,
                headers={"x-api-result": "binary"},
                background=BackgroundTask(cleaner.clean),
            )
        else:
            #读取数据就可以马上释放了，等回收自动释放也可以
            data=zip_file.read_bytes()
            #可以在这里手动，或者不手动也可以，会马上被gc然后自动清除
            cleaner.clean()
            return Response(
                data,
                headers={"x-api-result": "binary"},
                media_type="application/zip",
            )

    async def _wait_timeout(self,task:Task,timeout:float|None=None):
        try:
            #await asyncio.wait_for(task.future,timeout=timeout)
            await task.result(timeout=timeout)
        except BaseException:
            #其他异常，忽略，因为不关心
            pass
    
    async def _invoke_method(self,cfg:MethodConfig=Body(default={})):
        name = cfg.name
        args = cfg.args
        
        pass
