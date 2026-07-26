import logging
import os
import platform
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from memect.base.utils import console

if sys.platform != "darwin":
    import onnxruntime

    # directory=None 表示先查找pytorc的lib，再查找安装的nvidia包，再系统路径
    # directory=''，nvidia的包，再系统路径
    onnxruntime.preload_dlls(directory=None)
    # onnxruntime.print_debug_info()


def is_running_in_docker() -> bool:
    # 在dockerfile中设置环境变量，就可以知道在docker运行了
    if os.environ.get("RUNNING_IN_DOCKER"):
        return True

    if os.path.isfile("./dockerenv"):
        return True
    return False


@lru_cache()
def get_value[T: str | int | float | bool](name: str, default: T | None) -> T | None:
    # TODO 可以读取当前的".env"文件？获得环境变量？
    value = os.environ.get(name.lower()) or os.environ.get(name.upper())
    console.log(f"env {name}={value}")
    if value is None:
        return default
    type_ = type(default) if default is not None else str
    try:
        return type_(value)  # type: ignore
    except ValueError:
        console.log(
            f"环境变量设置的值的无法转换为对应的类型，type={type_},{name}={value}"
        )
        return default


def is_force_cpu(name: str) -> bool:
    """表示是否强制使用cpu"""
    # 在gpu环境下，强制使用cpu，目的是为了方便切换而不需要修改配置
    cpu = get_value("PPX_CPU", "")
    if not cpu:
        return False
    elif cpu == "all" or cpu == "true" or name in [n.strip() for n in cpu.split(",")]:
        return True
    else:
        return False


def is_apple_silicon():
    return platform.processor() == "arm" and platform.system() == "Darwin"


_gpus: Final[dict[str, bool]] = {}


def use_gpu(model: str, engine: str = "onnxruntime", vendor: str = "cuda") -> bool:
    if is_force_cpu(model):
        # 即使在gpu环境下，也使用cpu，避免需要修改配置
        return False
    key = f"{engine}_{vendor}"
    if key not in _gpus:
        _gpus[key] = _use_gpu(engine, vendor=vendor)
        from memect.base.utils import console

        console.log(f"detect gpu,engine={engine},vendor={vendor},ok={_gpus[key]}")
    return _gpus[key]


def _use_gpu(engine: str, vendor: str = "cuda") -> bool:
    """判断是否gpu可用"""
    # 因为配置文件在多进程下每个进程都会执行一次，所以，这个配置文件必须轻量级，也就是不要执行耗时的操作
    # 所以，判断是否gpu可用（包括cuda/cann等），仅仅使用简单的判断
    # 默认的配置都是自动使用gpu，也就是gpu可用就用，不可用就使用cpu，所以，即使总是返回True也是可以的
    # 只是有些库会显示警告，表示指定使用gpu，但是当前环境不支持
    # 判断原则
    # 安装了支持gpu的库+有显卡
    if engine == "onnxruntime":
        try:
            import onnxruntime

            providers = onnxruntime.get_available_providers()
            mappings = {
                "dml": ("CPU-DML", "DmlExecutionProvider"),
                "cuda": ("GPU", "CUDAExecutionProvider"),
                "amd": ("GPU", "MIGraphXExecutionProvider"),
                "cann": ("GPU", "CANNExecutionProvider"),
            }
            cfg = mappings[vendor]
            if cfg[0] != onnxruntime.get_device():
                # 判断是否有硬件了（如：显卡）
                return False
            if cfg[1] not in providers:
                # 判断是否安装了对应的库
                return False
            return True
        except ModuleNotFoundError:
            return False
    elif engine == "torch":
        try:
            import torch

            if vendor in ("cuda", "amd"):
                # 1. 是否编译了 CUDA 支持（包括amd的）
                if not torch.cuda.is_available():
                    return False
                # 2. 是否有实际设备
                if torch.cuda.device_count() == 0:
                    return False
                return True
            elif vendor == "cann":
                # hasattr(torch,'npu') and torch.npu.is_available()
                return False
            else:
                return False

        except ModuleNotFoundError:
            return False
    else:
        raise ValueError(f"不支持的engine={engine}")


def is_x86():
    machine = platform.machine().lower()
    return machine in ("x86_64", "amd64")


def use_openvino():
    try:
        import openvino  # type: ignore

        # glibc>=35的linux可以安装，但是无法正常运行，所以目前现在限制为x86
        return is_x86()
    except ImportError:
        return False


def get_device(model: str):
    if use_gpu(model, vendor="cuda"):
        return {"engine": "onnxruntime", "use_cuda": True}
    elif use_gpu(model, vendor="cann"):
        return {"engine": "onnxruntime", "use_cann": True}
    elif use_gpu(model, vendor="dml"):
        return {"engine": "onnxruntime", "use_dml": True}
    elif is_apple_silicon():
        # use_coreml:True 总是失败
        # cpu+openvino比cpu+onnxruntime快
        # 2秒/张
        # return {"engine": "onnxruntime", "use_coreml": False}
        # 1秒/张
        return {"engine": "openvino"}
    elif use_openvino():
        return {"engine": "openvino"}
    else:
        return {"engine": "onnxruntime"}


def get_model_path(file: str | Path) -> str | None:
    file = Path(file).absolute()
    if file.exists():
        # 目录或者文件
        return str(file)
    else:
        # 表示不存在，自动下载
        return None


# 4090显卡比cpu快，2080/3090等不一定，windows下可以安装onnxruntime-directml
# 4090显卡0.5秒/张
# 2080/3090显卡1.5-2秒/张
# 如果是windows，cuda+onnxruntime很慢的话，可以使用dml+onnxruntime
# 或者可以考虑设置：Rec.rec_batch_num=6 (默认，很慢)，需要设置为100（正常，1.5秒）
# cpu+openvino，2秒/张，如果是更好的cpu，可以达到1.5秒/张
# cpu+onnxruntime，非常慢
# mac m系列的cpu，默认使用onnxruntime，和openvino对比，没有设备测试
_ocr_device: Final = get_device("ocr")
# 显卡比cpu快,cpu+openvino和cpu+onnxruntime持平
_layout_device: Final = get_device("layout")
# 显卡比cpu快,cpu+openvino和cpu+onnxruntime持平
_table_device: Final = get_device("table")
_formula_device: Final = get_device("formula")
# cpu+onnxruntime比cpu+openvino快，不过复杂一些的模型，都需要50秒
# FormulaPPModel不支持cpu+openvino，必须使用onnxruntime
_formula_device["engine"] = "onnxruntime"

console.log(f"ocr={_ocr_device}")
console.log(f"layout={_layout_device}")
console.log(f"table={_table_device}")
console.log(f"formula={_formula_device}")

settings: dict[str, Any] = {
    "server": {
        "provider": "uvicorn",
        #'provider':'granian',
        # 'provider':'hypercorn',
        # 统一使用这两个值设置地址和端口号
        "host": "0.0.0.0",
        "port": 9527,
        "uvicorn": {
            # 'host': '0.0.0.0',
            # 'port': 3456,
            "log_config": None,
            "server_header": False,
            "lifespan": "on",
            # 如果需要使用ssl
            # 'ssl_keyfile': './conf/server.key',
            # 'ssl_certfile': './conf/server.crt',
        },
        "granian": {"log_access": True, "log_access_format": ""},
        "hypercorn": {
            # https://pgjones.gitlab.io/hypercorn/how_to_guides/configuring.html
            # 支持http2，但是必须使用ssl
            # 'bind':['0.0.0.0:3456'],
            "include_server_header": False,
            "access_log_format": '%(h)s %(l)s %(l)s %(t)s %(L)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"',
            # logging.getLogger('hypercorn.access') or filename,"-"表示stdout/stderr
            "accesslog": logging.getLogger("hypercorn.access"),
            "errorlog": logging.getLogger("hypercorn.error"),
            # 'accesslog':'-',
            # 'errorlog':'-',
            # 'keyfile':None,
            # 'certfile':None
        },
        # None表示不启用跨域支持，如果不需要支持浏览器跨域访问，可以禁用跨域
        "cors": {
            "allow_origins": ["*"],
            # True的时候，必须设置allow_origins
            "allow_credentials": False,
            "allow_methods": ["*"],
            "allow_headers": ["*"],
            "allow_origin_regex": None,
            "expose_headers": [],
            # 单位为秒
            "max_age": 600,
        },
    },
    "model_manager": {
        # 如果为True，表示每一个都是使用api调用，不加载模型
        # "use_api":False,
        # TODO 这里的配置为server模式，在命令行执行模式
        # max_workers=0 or use_process=False
        "executors": {
            "ocr": {
                # 默认为True，False表示不加载
                "enable": True,
                "name": "ocr",
                # 0表示在当前进程执行,>0表示使用启动多个
                "max_workers": 0,
                # True表示每一个都在独立的进程
                "use_process": False,
                "scheduler": {
                    "policy": "fifo",
                    # >=max_workers，如果大一些，可以减少调度的耗时
                    "max_task_size": 10,
                },
                "model": "ocr",
            },
            "layout": {
                "name": "layout",
                "max_workers": 0,
                "use_process": False,
                "scheduler": {
                    "policy": "fifo",
                    "max_task_size": 10,
                },
                # or layout_v3
                "model": "layout",
            },
            "formula": {
                "name": "formula",
                # 如果使用的是llm，可以设置为4
                "max_workers": 0,
                "use_process": False,
                "scheduler": {
                    "policy": "fifo",
                    "max_task_size": 10,
                },
                # paddle or glm or formula-pp or formula-mfr
                "model": get_value("ppx_formula", "formula-pp"),
            },
            "table": {
                # 识别表格的单元格
                "name": "",
                "enable": True,
                "max_workers": 0,
                "use_process": False,
                "scheduler": {
                    "policy": "fifo",
                    # 因为这里使用单个模型，这个和后台llm的能力匹配即可
                    "max_task_size": 10,
                },
                "model": "table",
            },
        },
        "models": {
            "ocr": {
                "name": "OCRModel",
                "kwargs": {
                    # tiny,small,medium
                    "model": "tiny",
                    "det_score_threshold": 0.4,
                    "rec_batch_size": 100,
                    # "det_model_path":None,
                    # "rec_model_path":None,
                    "engine": _ocr_device.get("engine", "openvino"),
                    "use_cuda": _ocr_device.get("use_cuda", False),
                    "use_cann": _ocr_device.get("use_cann", False),
                    "use_dml": _ocr_device.get("use_dml", False),
                },
            },
            "layout": {
                "name": "LayoutModel",
                "kwargs": {
                    # "model_path":get_model_path('./models/pp_layoutv2.onnx'),
                    #"model_path":get_model_path('./models/pp_layoutv3.onnx'),
                    #v2,v3,l,plus_l
                    "version":"v2",
                    "score_threshold":0.5,
                    "engine": _layout_device["engine"],
                    "use_cuda": _layout_device.get("use_cuda", False),
                    "use_cann": _layout_device.get("use_cann", False),
                    "use_dml": _layout_device.get("use_dml", False),
                },
            },
            "table": {
                "name": "TableModel",
                "kwargs": {
                    #"model_path": get_model_path("./models/memect/table_det.onnx"),
                    "score_threshold": 0.5,
                    "engine": _table_device["engine"],
                    "use_cuda": _table_device.get("use_cuda", False),
                    "use_cann": _table_device.get("use_cann", False),
                    "use_dml": _table_device.get("use_dml", False),
                },
            },
            "formula-pp": {
                # 在cpu下快，在gpu下很慢
                "name": "FormulaPPModel",
                "kwargs": {
                    "model_dir": get_model_path("./models/PP-FormulaNet_plus-M_infer"),
                    # 必须使用onnxruntime，不支持openvino
                    "engine": "onnxruntime",
                    "use_cuda": _formula_device.get("use_cuda", False),
                    "use_cann": _formula_device.get("use_cann", False),
                    "use_dml": _formula_device.get("use_dml", False),
                },
            },
            "formula-mfr": {
                # 在cpu下慢，在gpu下快
                "name": "FormulaMfrModel",
                "kwargs": {
                    "model_dir": get_model_path("./models/mfr"),
                    "engine": _formula_device["engine"],
                    "use_cuda": _formula_device.get("use_cuda", False),
                    "use_cann": _formula_device.get("use_cann", False),
                    "use_dml": _formula_device.get("use_dml", False),
                },
            },
            # 下面这2个模型，支持formula，table，ocr
            # 目前主要用来识别公式
            "paddle": {
                "name": "LLMModel",
                "kwargs": {
                    "model": "paddleocr-vl",
                    "client": {
                        "base_url": get_value(
                            "ppx_paddle_url", "http://127.0.0.1:4001/v1"
                        ),
                        "api_key": "x",
                    },
                    "params": {
                        # <=后台llmserver的max-token-len - input_tokens
                        "max_tokens": 4000,
                        "temperature": 0,
                    },
                    "prompt": "Formula Recognition:",
                    "prompts": {
                        "text": "OCR:",
                        "formula": "Formula Recognition:",
                        "table": "Table Recognition:",
                        # "chart":"Chart Recognition:"
                    },
                },
            },
            "glm": {
                "name": "LLMModel",
                "kwargs": {
                    "model": "glmocr",
                    "client": {
                        "base_url": get_value(
                            "ppx_glm_url", "http://127.0.0.1:4002/v1"
                        ),
                        "api_key": "x",
                    },
                    "params": {
                        # <=后台llmserver的max-token-len - input_tokens
                        "max_tokens": 4000,
                        "temperature": 0,
                    },
                    "prompt": "Formula Recognition:",
                    "prompts": {
                        "text": "Text Recognition:",
                        "formula": "Formula Recognition:",
                        "table": "Table Recognition:",
                    },
                },
            },
        },
    },
    "pdf_parser": {
        "pdf2image": {"max_workers": 4, "max_size": (2000, 6000), "max_scale": 2},
        "deepseek": {
            "model": {
                "base_url": get_value("ppx_deepseek_url", "http://127.0.0.1:4000/v1"),
                "api_key": "x",
                "scheduler": {
                    # fifo:按顺序执行
                    # balance: 公平执行
                    "policy": "balance",
                    # 可以同时处理10个文件
                    "max_task_size": get_value("ppx_deepseek_size", 10),
                },
            }
        },
        "paddle": {
            # layout or layout-v3
            "layout": "layout",
            "model": {
                "base_url": get_value("ppx_paddle_url", "http://127.0.0.1:4001/v1"),
                "api_key": "x",
                #'model':'paddleocr-vl-1.5',
                "scheduler": {
                    # fifo:按顺序执行
                    # balance: 公平执行
                    "policy": "balance",
                    # 可以同时处理10个文件
                    "max_task_size": get_value("ppx_paddle_size", 10),
                },
            },
        },
        "glm": {
            # layout or layout-v3
            "layout": "layout",
            "model": {
                "base_url": get_value("ppx_glm_url", "http://127.0.0.1:4002/v1"),
                "api_key": "x",
                "scheduler": {
                    "policy": "balance",
                    # 单显卡一般就是10个并发，如果多显卡，可以设置更大
                    "max_task_size": get_value("ppx_glm_size", 10),
                },
            },
        },
        "baidu": {
            "model": {
                "base_url": get_value("ppx_baidu_url", "http://127.0.0.1:4003/v1"),
            }
        },
        "default": {
            # pdf解析的配置
            "pdf": {
                "provider": "pymupdf"
                # "provider":"pdf_oxide"
            },
            # 图片解析的配置
            "image": {},
            "table": {"ybk": {}, "wbk": {}, "llm": {}},
            "features": {
                # 这个名字空间为默认，也就是不添加到全名中，否则，feature的全名为：{ns}.{filename}
                "default": {
                    # 查找该包下的所有py文件
                    "package": "memect.features",
                    #
                    # "class":"memect.features.feature1.Feature"
                }
            },
        },
        "tree": {
            # 跨页/跨栏文本合并
            "text": {},
            # 跨页/跨栏表格合并
            "table": {},
            # 某些内容需要先分成一个逻辑组，也就是不需要细分层次
            "group": {},
            # 使用llm构建章节树
            "llm": {
                # or anthropic
                "provider": "openai",
                "base_url": "",
                "api_key": "",
                "model": "",
                "tempeature": 0,
                "max_tokens": 2000,
            },
            # 根据pdf的outline构建章节树
            "outline": {},
            # 根据pdf的目录章节构建章节树
            "toc": {},
            # 根据一定的规则构建章节树
            "default": {},
        },
    },
    "pdf_service": {
        # 上传的文件的保存目录
        # {data_dir}/tasks,{data_dir}/errors,{data_dir}/files
        "data_dir": "./data/pdf",
        # all:保留所有文件，放在：data_dir/files
        # error:保留解析错误的，放在 data_dir/errors
        # no:不保留文件
        "keep_file_policy": "error",
        "image": {
            # 允许哪些类型的图片
            "exts": ("png", "webp", "jpg", "jpeg", "bmp"),
            # 100M
            "max_file_size": 100 * 1024 * 1024,
            # 图片(width,height)，载入内存都需要400M了（RGBA）
            "max_image_size": (10000, 10000),
        },
        "pdf": {
            "exts": ("pdf",),
            # 1G
            "max_file_size": 1024 * 1024 * 1024,
            # 2000页
            "max_page_count": 2000,
            "priorities": [2000, 1000, 500, 0],
        },
        "task_manager": {
            # 如果是cpu操作，根据本机的能力划分，如：一个任务使用4个，那么，32个gpu可以同时设置为32/4=8个
            # 如果是llm操作，llm可以同时并发20个，然后每个任务使用5个请求，那么，最大运行就是20/5=4
            # 如果是对象模型操作，算法也同上
            # 这个时候，设置为4就是合理的，cpu资源有空闲，但是gpu资源用满，如果设置太大，就容易出现在llm操作的时候，等待请求返回超时
            # 如果设置为0，根据pdf2image，llm，layout等模型的设置来计算
            "max_running_size": 4,
            "max_waiting_size": 1000,
            "max_done_size": 1000,
            "max_running_timeout": 60 * 60,
            "max_waiting_timeout": None,
            "max_done_timeout": 30 * 60,
            "priorities": {
                # 表示1级最多运行1个，没有定义就是max_running_size
                1: 1,
                2: 2,
            },
        },
    },
}
