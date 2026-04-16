import logging
import os
import platform
from pathlib import Path
from typing import Any, Final


def is_running_in_docker() -> bool:
    # 在dockerfile中设置环境变量，就可以知道在docker运行了
    if os.environ.get("RUNNING_IN_DOCKER"):
        return True

    if os.path.isfile("./dockerenv"):
        return True
    return False


def is_force_cpu() -> bool:
    """表示是否强制使用cpu"""
    # 在gpu环境下，强制使用cpu，目的是为了方便切换而不需要修改配置
    if os.environ.get("FORCE_CPU"):
        return True
    else:
        return False


def is_force_gpu() -> bool:
    if os.environ.get("FORCE_GPU"):
        return True
    else:
        return False


def is_apple_silicon():
    return platform.processor() == "arm" and platform.system() == "Darwin"


_gpus: Final[dict[str, bool]] = {}


def use_gpu(engine: str, vendor: str = "cuda") -> bool:
    key = f"{engine}_{vendor}"
    if key not in _gpus:
        _gpus[key] = _use_gpu(engine, vendor=vendor)
        # from rich import get_console
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
    if is_force_cpu():
        # 即使在gpu环境下，也使用cpu，避免需要修改配置
        return False

    if engine == "onnxruntime":
        try:
            import onnxruntime

            if onnxruntime.get_device() != "GPU":
                return False
            providers = onnxruntime.get_available_providers()
            mappings = {
                "cuda": "CUDAExecutionProvider",
                "amd": "MIGraphXExecutionProvider",
                "cann": "CANNExecutionProvider",
            }
            return mappings[vendor] in providers
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


# 为了支持pypy==3.11，就不使用泛型定义了
def get_value(name: str, default: str | int | float | bool | None) -> Any:
    value = os.environ.get(name.lower()) or os.environ.get(name.upper())
    if not value or default is None:
        return default
    type_ = type(default)
    try:
        return type_(value)
    except ValueError:
        print(f"环境变量设置的值的无法转换为对应的类型，type={type_},{name}={value}")
        return default


def get_ocr_engine() -> str:
    if use_gpu("onnxruntime"):
        return "onnxruntime"
    elif is_apple_silicon():
        return "onnxruntime"
    else:
        # amd/intel,cpu下这个更快
        return "openvino"


def get_cpu_engine():
    # TODO 目前最底层还是都是使用cpu，还没有使用CoreML，这个有很多错误
    if is_apple_silicon():
        return "onnxruntime"
    else:
        return "openvino"


def get_model_path(file: str | Path) -> str | None:
    file = Path(file).absolute()
    if file.exists():
        # 目录或者文件
        return str(file)
    else:
        # 表示不存在，自动下载
        return None


_paddle_layout_v2 = {
    # 粗体或者有背景颜色的文本
    "paragraph_title": "title",
    # 会把有一个大边框包围的文本也识别为图
    "image": "figure",
    "text": "text",
    # 页码
    "number": "footer",
    # 通常表示一个整页的，里面包含了title或者text?
    "abstract": "text",
    # 目录内容
    "content": "toc",
    "figure_title": "title",
    # 'formula': 'formula',
    # v2版本分成2个类型
    "display_formula": "formula",
    "inline_formula": "inline_formula",
    "table": "table",
    "table_title": "title",
    # 通常表示一个整页的，里面包含了小的text，所以可以使用reference类型
    "reference": "text",
    # v2特有的
    "reference_content": "text",
    "doc_title": "title",
    # 这个也是文本，只是多数情况下还是比较准确的，因为有一条水平分割线来标识位置
    # 当然为text
    "footnote": "footnote",
    "header": "header",
    # 算法，论文中出现，有文本，通常可以作为图片处理？
    # 映射为figure，表示作为图片处理，映射为text，表示作为文本处理
    "algorithm": "code",
    "footer": "footer",
    # 圆形的，正方形的多数识别为image
    "seal": "seal",
    "chart_title": "title",
    "chart": "chart",
    # 公式的编号，如：(12.11)
    "formula_number": "text",
    "header_image": "figure",
    # 标记为图片还是footer？因为其他的可能没有这种类型
    "footer_image": "figure",
    # 还是先使用这个名字
    "aside_text": "other_text",
    # v2特有的，获得垂直书写的，如果是英文，通常还顺时针旋转90度
    "vertical_text": "text",
    # 如：来源：xxxxx
    "vision_footnote": "text",
}

_paddle_layout_v3 = _paddle_layout_v2

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
        "executors": {
            "ocr": {
                # 默认为True，False表示不加载
                "enable": True,
                "name": "ocr",
                "max_workers": 2,
                "use_process": True,
                "scheduler": {
                    "policy": "fifo",
                    # >=max_workers，如果大一些，可以减少调度的耗时
                    "max_task_size": 10,
                },
                # or "ocr_server"
                "model": "ocr_mobile",
            },
            "layout": {
                "name": "layout",
                "max_workers": 2,
                "use_process": True,
                "scheduler": {
                    "policy": "fifo",
                    "max_task_size": 10,
                },
                # or layout_v3
                "model": "layout_v2",
            },
            "formula": {
                "name": "formula",
                "max_workers": 2,
                # 如果使用的是llm，没有必要启动进程，但是也不影响
                "use_process": True,
                "scheduler": {
                    "policy": "fifo",
                    "max_task_size": 10,
                },
                # paddle or glm or rapid_formula
                "model": "formula",
            },
            "table_cls": {
                # 表格分类，有边框还是无边框
                "name": "",
                "enable": True,
                # 表示只需要一个即可，不需要通过每一个进程一个或者每个线程一个
                "max_workers": 0,
                "use_process": False,
                "scheduler": {
                    "policy": "fifo",
                    # 因为这里使用单个模型，这个和后台llm的能力匹配即可
                    "max_task_size": 10,
                },
                "model": "table_cls_q",
            },
            "table_det": {
                # 识别表格的单元格
                "name": "",
                "enable": True,
                # 表示只需要一个即可，不需要通过每一个进程一个或者每个线程一个
                "max_workers": 2,
                "use_process": True,
                "scheduler": {
                    "policy": "fifo",
                    # 因为这里使用单个模型，这个和后台llm的能力匹配即可
                    "max_task_size": 10,
                },
                "model": "table_det",
            },
            "table_llm": {
                # 识别表格的单元格
                "name": "",
                "enable": True,
                # 表示只需要一个即可，不需要通过每一个进程一个或者每个线程一个
                "max_workers": 2,
                "use_process": True,
                "scheduler": {
                    "policy": "fifo",
                    # 因为这里使用单个模型，这个和后台llm的能力匹配即可
                    "max_task_size": 10,
                },
                # paddle or glm
                "model": "paddle",
            },
        },
        "models": {
            # 这里的设置对应RapidOCR，然后必须使用具体的枚举类型，但是使用了这些，每次就必须载入RapidOCR这个库
            # 这个又直接载入cv2/numpy，导致在多进程下，有些不需要的，载入就变慢，所以这里还是使用字符串
            # 在这个模型中做转换处理
            "ocr_mobile": {
                "name": "RapidOCRModel",
                "kwargs": {
                    "Global.model_root_dir": get_model_path("./models/ocr"),
                    "Global.text_score": 0.5,
                    # -1表示无论如何都det，否则w/h>width_height_ratio，就不det了，而是直接rec
                    "Global.width_height_ratio": -1,
                    "Det.engine_type": get_ocr_engine(),
                    "Cls.engine_type": get_ocr_engine(),
                    "Rec.engine_type": get_ocr_engine(),
                    "Det.model_type": "mobile",
                    "Cls.model_type": "mobile",
                    "Rec.model_type": "mobile",
                    # 表示下载目录
                    #'Det.model_dir':'./models/ocr',
                    # 表示模型文件
                    #'Det.model_path':'',
                    #'Cls.model_path':'',
                    #'Rec.model_path':'',
                    "Det.ocr_version": f"PP-OCR{get_value('ocr_version', 'v5')}",
                    # 没有v5
                    "Cls.ocr_version": "PP-OCRv4",
                    "Rec.ocr_version": f"PP-OCR{get_value('ocr_version', 'v5')}",
                    "EngineConfig.onnxruntime.use_cuda": use_gpu("onnxruntime"),
                },
            },
            "ocr_server": {
                "name": "RapidOCRModel",
                "kwargs": {
                    "Global.model_root_dir": get_model_path("./models/ocr"),
                    "Global.text_score": 0.5,
                    # -1表示无论如何都det，否则w/h>width_height_ratio，就不det了，而是直接rec
                    "Global.width_height_ratio": -1,
                    "Det.engine_type": get_ocr_engine(),
                    "Cls.engine_type": get_ocr_engine(),
                    "Rec.engine_type": get_ocr_engine(),
                    "Det.model_type": "server",
                    "Cls.model_type": "server",
                    "Rec.model_type": "server",
                    # 表示下载目录
                    #'Det.model_dir':'./models/ocr',
                    # 表示模型文件
                    #'Det.model_path':'',
                    #'Cls.model_path':'',
                    #'Rec.model_path':'',
                    "Det.ocr_version": f"PP-OCR{get_value('ocr_version', 'v5')}",
                    # 没有v5
                    "Cls.ocr_version": "PP-OCRv4",
                    "Rec.ocr_version": f"PP-OCR{get_value('ocr_version', 'v5')}",
                    "EngineConfig.onnxruntime.use_cuda": use_gpu("onnxruntime"),
                },
            },
            "layout_v2": {
                "name": "RapidLayoutModel",
                "kwargs": {
                    "mapping": dict(_paddle_layout_v2),
                    "model_type": "pp_doc_layoutv2",
                    # cpu下，openvino快一些
                    "engine_type": "onnxruntime"
                    if use_gpu("onnxruntime")
                    else get_cpu_engine(),
                    "model_dir_or_path": get_model_path(
                        "./models/layout/pp_doc_layoutv2.onnx"
                    ),
                    "engine_cfg": {"use_cuda": use_gpu("onnxruntime")},
                    "conf_thresh": 0.3,
                    "iou_thresh": 0.5,
                },
            },
            "layout_v3": {
                "name": "RapidLayoutModel",
                "kwargs": {
                    "mapping": dict(_paddle_layout_v3),
                    "model_type": "pp_doc_layoutv3",
                    # or "openvino"
                    "engine_type": "onnxruntime"
                    if use_gpu("onnxruntime")
                    else get_cpu_engine(),
                    "model_dir_or_path": get_model_path(
                        "./models/layout/pp_doc_layoutv3.onnx"
                    ),
                    "engine_cfg": {"use_cuda": use_gpu("onnxruntime")},
                    "conf_thresh": 0.3,
                    "iou_thresh": 0.5,
                },
            },
            "paddle": {
                "name": "LLMModel",
                "kwargs": {
                    "model": "paddleocr-vl",
                    "client": {
                        "base_url": "http://127.0.0.1:4001/v1",
                        "api_key": "",
                    },
                    "params": {
                        # <=后台llmserver的max-token-len - input_tokens
                        "max_tokens": 4000,
                        "temperature": 0,
                    },
                    #"prompt": "Formula Recognition:",
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
                        "base_url": "http://127.0.0.1:4002/v1",
                        "api_key": "",
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
            "table_cls_q": {
                "name": "TableClsModel",
                "kwargs": {
                    "model_type": "q",
                    "model_path": get_model_path("./models/table_cls/q_cls.onnx"),
                    # "use_gpu": use_gpu("onnxruntime"),
                },
            },
            "table_cls_paddle": {
                "name": "TableClsModel",
                "kwargs": {
                    "model_type": "paddle",
                    "model_path": get_model_path("./models/table_cls/paddle_cls.onnx"),
                    # "use_gpu": use_gpu("onnxruntime"),
                },
            },
            "table_cls_yolo": {
                "name": "TableClsModel",
                "kwargs": {
                    "model_type": "yolo",
                    "model_path": get_model_path("./models/table_cls/yolo_cls.onnx"),
                    # "use_gpu": use_gpu("onnxruntime"),
                },
            },
            "table_cls_yolox": {
                "name": "TableClsModel",
                "kwargs": {
                    "model_type": "yolox",
                    "model_path": get_model_path("./models/table_cls/yolo_cls_x.onnx"),
                    # "use_gpu": use_gpu("onnxruntime"),
                },
            },
            "table_det": {
                "name": "TestModel"
            },
            "formula": {"name": "RapidFormulaModel", "kwargs": {}},
        },
    },
    "pdf_parser": {
        "pdf2image": {
            "scheduler": {
                #'policy':'fifo',
                "policy": "balance",
                "max_task_size": 10,
            }
        },
        "deepseek": {
            "model": {
                "base_url": get_value("llm_deepseek_url", "http://127.0.0.1:4000/v1"),
                "scheduler": {
                    # fifo:按顺序执行
                    # balance: 公平执行
                    "policy": "balance",
                    # 可以同时处理10个文件
                    "max_task_size": get_value("llm_deepseek_size", 10),
                },
            }
        },
        "paddle": {
            # layout or layout-v3
            "layout": "layout",
            "model": {
                "base_url": get_value("llm_paddle_url", "http://127.0.0.1:4001/v1"),
                #'model':'paddleocr-vl-1.5',
                "scheduler": {
                    # fifo:按顺序执行
                    # balance: 公平执行
                    "policy": "balance",
                    # 可以同时处理10个文件
                    "max_task_size": get_value("llm_paddle_size", 10),
                },
            },
        },
        "glm": {
            # layout or layout-v3
            "layout": "layout",
            "model": {
                "base_url": get_value("llm_glm_url", "http://127.0.0.1:4002/v1"),
                "scheduler": {
                    "policy": "balance",
                    # 单显卡一般就是10个并发，如果多显卡，可以设置更大
                    "max_task_size": get_value("llm_glm_size", 10),
                },
            },
        },
        "default": {
            "pdf": {
                "provider": "pymupdf"
                # "provider":"pdf_oxide"
            },
            "pdf2": {"provider": "pymupdf"},
            "image": {"ocr": "ocr", "llm": "llm"},
        },
    },
    "pdf_service": {
        # 上传的文件的保存目录
        # {data_dir}/tasks,{data_dir}/errors,{data_dir}/files
        "data_dir": "./data/pdf",
        # all:保留所有文件，放在：data_dir/files
        # error:保留解析错误的，放在 data_dir/files
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
        # True表示启用新的进程执行parser
        # 如果使用进程的方式，可以确保每个任务解析的时间基本固定，即使系统空闲，也不会使用更多的资源
        # 对于满载的情况，True和False没有什么区别
        # 如果设置为True，scheduler.max_task_size*task_manager.max_runnning_size<=系统能力
        # 如果设置为False，scheduler.max_task_size=系统能力，当只有一个任务，可以并发，最快速完成
        "use_process": False,
    },
}
