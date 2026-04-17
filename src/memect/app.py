# coding=utf-8

import asyncio
import datetime
import logging
import time
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Mapping, Protocol, Self, Sequence

# ============================
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from memect.pdf.service import PdfService

from memect.base.api import ApiError, ApiInfo
from memect.base.config import get_settings

# ==========================
from memect.base.utils import Timer


class ServerSettings(BaseModel):
    provider: str = "uvicorn"
    host: str = ""
    port: int = 9527
    cors: Mapping[str, Any] | None = None
    uvicorn: Any = None
    hypercorn: Any = None


class ApiService(Protocol):
    def setup(self, app: FastAPI):
        pass

    def get_info(self) -> ApiInfo: ...


class App:
    _logger: Final = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self, settings: Mapping[str, Any] | None = None):
        super().__init__()

        self._settings: Final = settings or get_settings()
        self._timer = Timer.start()
        self._fastapi: FastAPI | None = None
        self._pdf_service: PdfService | None = None

        self._inited: bool = False

    async def _init(self):
        if self._inited:
            return
        self._inited = True
        self._logger.info("start init app...")
        self._pdf_service = PdfService(self._settings['pdf_service'])
        self._fastapi = self._create_fastapi([self._pdf_service])
        self._logger.info("end init app,elapsed=%.3f", self._timer.elapsed())

    async def _exit(self):
        self._logger.info("start exit app")
        if self._pdf_service:
            await self._pdf_service.close()
        self._logger.info("end exit app uptime=%s", self._timer.uptime())

    def _create_fastapi(self, services: Sequence[ApiService] | None = None):
        self._logger.info("start create fastapi")
        cors_cfg = self._settings["server"].get("cors")

        async def on_startup():
            # 如果需要使用同一个loop，需要在这里初始化
            # await api.init()
            self._logger.info("fastapi startup")

        async def on_shutdown():
            # await api.close()
            self._logger.info("fastapi shutdown")

        app = FastAPI(on_startup=[on_startup], on_shutdown=[on_shutdown])
        if isinstance(cors_cfg, dict):
            self._logger.info("setup cors")
            app.add_middleware(CORSMiddleware, **cors_cfg)

        app.add_middleware(GZipMiddleware, minimum_size=1000)

        error_headers = {"x-api-status": "error"}

        @app.exception_handler(RequestValidationError)
        async def validation_exception_handler(
            request: Request, exc: RequestValidationError
        ):
            # 参数错误使用debug级别的日志就可以，否则可能会很多，如果恶意攻击的
            # 默认不会输出到日志，如果需要，可以在这里记录
            # str(exc) 返回太具体，连错误文件和位置都暴露了
            fields = {err["loc"][-1]: err["msg"] for err in exc.errors()}
            error = ApiError(ApiError.PARAMETER, "参数错误", details=fields)
            return JSONResponse(
                status_code=200,
                headers=error_headers,
                content={"error": error.jsonify()},
            )

        @app.exception_handler(ApiError)
        async def api_exception_handler(request: Request, exc: ApiError):
            # fastapi不会记录日志
            return JSONResponse(
                status_code=200, headers=error_headers, content={"error": exc.jsonify()}
            )

        @app.exception_handler(Exception)
        async def system_exception_handler(request: Request, exc: Exception):
            # 不需要返回具体的信息给客户端，这个异常fastapi会log
            error = ApiError(ApiError.SYSTEM, "系统异常")
            return JSONResponse(
                status_code=200,
                headers=error_headers,
                content={"error": error.jsonify()},
            )

        # ====api====
        api_infos: list[ApiInfo] = []
        if services:
            for service in services:
                service.setup(app)
                api_infos.append(service.get_info())

        @app.get("/admin/gc")
        def gc():
            # TODO 后续可以要求一个token
            import gc

            t1 = time.monotonic()
            gc.collect()
            gc.get_count()
            t2 = time.monotonic()
            return {"elapsed": t2 - t1}

        @app.get("/admin/state.html")
        async def state():
            return FileResponse("./web/state.html")

        @app.get("/apis")
        async def apis():
            return api_infos

        # ===k8s=====
        @app.get("/health")
        async def health():
            # k8s检查，如果没有返回200，就重启容器
            return {
                "status": "ok",
                "datetime": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
            }

        @app.get("/ready")
        async def ready():
            # k8s检查是否准备好了，如：数据库等都连接了
            return {}

        #

        @app.get("/echo")
        def echo():
            return {
                "message": "echo",
                "datetime": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
            }

        @app.get("/version.json")
        async def version():
            return {}

        @app.get("/changelog.md")
        async def changelog():
            return FileResponse("./changelog.md")

        import memect.web
        app.mount(
            "/", StaticFiles(directory=Path(memect.web.__file__).parent.absolute(), html=True), name="web"
        )
        self._logger.info("end create fastapi")
        return app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> Any:
        # 必须在这里初始化，因为需要在异步环境下
        await self._init()
        assert self._fastapi is not None
        # 可以根据asgi自行处理请求，目前使用fastapi
        return await self._fastapi(scope, receive, send)

    async def serve(self):
        server_cfg = self._settings["server"]
        self._logger.info("start httpserver,provider=%s", server_cfg["provider"])
        host = server_cfg.get("host", "0.0.0.0")
        port = server_cfg.get("port", 9527)
        try:
            if server_cfg["provider"] == "uvicorn":
                # uvicorn仅仅支持http1.1，不支持http2
                # 禁用uvicorn的日志设置，使用全局的设置
                # uvicorn的access log的缺点就是，不显示请求返回的内容长度和耗时，因为是请求在返回headers的时候就记录了日志
                # 而不是在请求处理完毕后记录请求的日志
                # 如果需要，可以参考https://github.com/Kludex/asgi-logger
                # 使用uvloop+httptools可以达到3500-3700个请求/秒
                # pypy不支持uvloop，但是也不需要使用pypy来执行io操作，因为比cpython慢
                # 使用对象不支持reload,workers等参数，实际上也不需要
                # 可以通过nginx或者gunicon等来支持
                # ab -n 1000 -c 10
                # pypy 2200/s(asyncio)
                # python3.11 2200/s(asyncio)  3000/s(uvloop)
                import uvicorn

                config = uvicorn.Config(
                    self, **server_cfg.get("uvicorn") or {}, host=host, port=port
                )
                server = uvicorn.Server(config)
                await server.serve()
            elif server_cfg["provider"] == "granian":
                from granian.constants import Interfaces
                from granian.server.embed import Server

                server = Server(
                    self,
                    address=host,
                    port=port,
                    interface=Interfaces.ASGI,
                    **server_cfg.get("granian", {}),
                )
                await server.serve()
            elif server_cfg["provider"] == "hypercorn":
                # 支持http1.1,http2,http3
                # pip install hypercorn
                # ab -n 1000 -c 10
                # pypy: 1200/s
                # python3.11: 1200/s,1400/s(uvloop)
                # 所以，使用默认的asyncio，pypy和python是持平的，uvloop会快10%-20%
                from hypercorn.asyncio import serve  # type:ignore
                from hypercorn.config import Config

                # from hypercorn.statsd import StatsdLogger
                # config.statsd_host=None
                # config.set_statsd_logger_class(StatsdLogger)
                config = Config.from_mapping(
                    server_cfg.get("hypercorn"), bind=[f"{host}:{port}"]
                )
                await serve(self, config)
            else:
                raise ValueError(f"不支持的provider={server_cfg['provider']}")

        finally:
            self._logger.info("exit httpserver uptime=%s", self._timer.uptime())

    # TODO 如果需要异步
    async def __aenter__(self) -> Self:
        await self._init()
        return self

    async def __aexit__(
        self, et: type | None, ev: BaseException | None, tb: TracebackType | None
    ) -> bool | None:
        await self._exit()

    @classmethod
    def run_async(cls, c: Any):
        try:
            import uvloop

            use_uvloop = True
        except ModuleNotFoundError:
            use_uvloop = False
        if use_uvloop:
            cls._logger.info("use uv loop")
            import uvloop

            # 如下执行，不修改全局
            with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
                runner.run(c)
        else:
            # 不在这里install，使用默认的
            # uvloop.install()
            asyncio.run(c)

    @classmethod
    def run(cls):
        cls.run_async(cls.arun())

    @classmethod
    async def arun(cls):
        async with App() as app:
            await app.serve()


async def create_app():
    from memect.base.config import setup
    setup()
    return App()
