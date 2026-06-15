import hashlib
import logging
import threading
from pathlib import Path
from typing import Any, Final

import httpx
from filelock import FileLock

# 这个只是支持多线程下
_lock: Final = threading.Lock()

# 支持多进程，因为可能同时启动多个进程
_download_lock = FileLock(Path(__file__).parent.joinpath("download.lock"))

# 如果模型更新了，上传新的模型，使用新的版本
# 用户需要更新代码才能够使用新的模型
_models: dict[str, Any] = {
    "table_det.onnx": {
        "url": "https://modelscope.cn/models/Memect/memect-table-det/resolve/v1.0.0/table_det.onnx",
        "sha256": "c267cafe004067be73c44cc3aa7990f34e1026c467464372fa6843500f5da1c2",
        "verified": False,
    },
    "mfr": {"huggingface": "breezedeus/pix2text-mfr-1.5", "verified": False},
    "PP-FormulaNet_plus-M_infer": {
        "modelscope": "Memect/PP-FormulaNet_plus-M_infer",
        "verified": False,
    },
    "PP-OCRv6_tiny_rec_onnx": {
        "modelscope": "PaddlePaddle/PP-OCRv6_tiny_rec_onnx",
        "verified": False,
    },
    "PP-OCRv6_tiny_det_onnx": {
        "modelscope": "PaddlePaddle/PP-OCRv6_tiny_det_onnx",
        "verified": False,
    },
    "PP-OCRv6_small_rec_onnx": {
        "modelscope": "PaddlePaddle/PP-OCRv6_small_rec_onnx",
        "verified": False,
    },
    "PP-OCRv6_small_det_onnx": {
        "modelscope": "PaddlePaddle/PP-OCRv6_small_det_onnx",
        "verified": False,
    },
    "PP-OCRv6_medium_rec_onnx": {
        "modelscope": "PaddlePaddle/PP-OCRv6_medium_rec_onnx",
        "verified": False,
    },
    "PP-OCRv6_medium_det_onnx": {
        "modelscope": "PaddlePaddle/PP-OCRv6_medium_det_onnx",
        "verified": False,
    },
    # ===这些将被遗弃====
    "formula/encoder.onnx": {
        "url": "https://modelscope.cn/models/Memect/rapid_latex_ocr/resolve/v1.0.0/encoder.onnx",
        "sha256": "01bf5dc25539ca0cd5b1bd29296ea495977a6ba5f629dc4178277809d26e5e7d",
        "verified": False,
    },
    "formula/decoder.onnx": {
        "url": "https://modelscope.cn/models/Memect/rapid_latex_ocr/resolve/v1.0.0/decoder.onnx",
        "sha256": "bd695497bf1b22279b7626f5916c79226e1e244c84355f8da7edfd2d921d0072",
        "verified": False,
    },
    "formula/image_resizer.onnx": {
        "url": "https://modelscope.cn/models/Memect/rapid_latex_ocr/resolve/v1.0.0/image_resizer.onnx",
        "sha256": "e0b075c39700f64d50400f39c8fc186bbb3b5d84d31864008313f376603aca9d",
        "verified": False,
    },
    "formula/tokenizer.json": {
        "url": "https://modelscope.cn/models/Memect/rapid_latex_ocr/resolve/v1.0.0/tokenizer.json",
        "sha256": "1dc27b18d6a518d0d5ff3f4bb7bd98521fe80ad39e5b2a246d4109f1bb9d5019",
        "verified": False,
    },
}


def get_model_path(name: str):
    logger = logging.getLogger(f"{__name__}")
    path = Path(__file__).parent.joinpath(name)
    cfg = _models[name]

    def has_model():
        if cfg["verified"]:
            return True

        if path.is_file():
            hash = hashlib.sha256(path.read_bytes()).digest().hex()
            if hash == cfg["sha256"]:
                logger.info("模型已经存在:%s", name)
                cfg["verified"] = True
                return True
            else:
                logger.warning("模型已经存在但是不完整:%s", name)
                return False
        elif path.is_dir() and path.joinpath("_done.txt").is_file():
            logger.info("模型已经存在:%s", name)
            cfg["verified"] = True
            return True

        return False

    # 除了第一次，其他模型已经存在了，所以只需要在本地多线程下判断即可
    with _lock:
        if has_model():
            return path

    # 模型不存在，支持多个进程同时执行的情况
    with _download_lock:
        if has_model():
            return path

        logger.info("模型不存在，开始下载模型:%s", name)
        if cfg.get("modelscope"):
            from modelscope import snapshot_download

            # TODO 还需要endpoint吗？
            snapshot_download(cfg.get("modelscope"), local_dir=path)
            path.joinpath("_done.txt").write_text("ok")
            cfg["verified"] = True
        elif cfg.get("huggingface"):
            from huggingface_hub import snapshot_download
            import os

            # 国外用户可以如下取消：export HF_ENDPOINT=
            endpoint = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")
            if not endpoint:
                endpoint = None
            snapshot_download(cfg.get("huggingface"), local_dir=path, endpoint=endpoint)
            path.joinpath("_done.txt").write_text("ok")
            cfg["verified"] = True
        else:
            download(cfg["url"], path)
            hash = hashlib.sha256(path.read_bytes()).digest().hex()
            if hash != cfg["sha256"]:
                # 模型更新了？代码没有更新
                raise RuntimeError("下载的模型不完整")
            cfg["verified"] = True
        return path


def get_ocr_path(model:str,size:str):
    return get_model_path(f'PP-OCRv6_{size}_{model}_onnx')/'inference.onnx'

def download(url: str, file: Path):
    file.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, follow_redirects=True) as r:
        total = int(r.headers.get("content-length", 0))
        from rich.progress import Progress

        with Progress() as progress:
            task = progress.add_task(f"[cyan]{file.name}", total=total or None)
            with file.open("wb") as f:
                for chunk in r.iter_bytes(chunk_size=1024 * 64):
                    f.write(chunk)
                    progress.advance(task, len(chunk))


def download_all():
    # 因为第三方库需要的下载模型，但是下载并不支持多进程也不执行多线程，也就是如果同时启动多个进程或者多个线程
    # 执行就会冲突，所以先下载好
    # rapid_ocr
    download_ocr()
    # rapid_layout
    download_layout()
    # table_det
    # download_table_cls()
    # download_formula()
    get_model_path("PP-FormulaNet_plus-M_infer")
    # get_model_path('PP-FormulaNet_plus-S_infer')
    get_model_path("mfr")
    get_model_path("table_det.onnx")

    get_model_path("PP-OCRv6_tiny_rec_onnx")
    get_model_path("PP-OCRv6_tiny_det_onnx")

    get_model_path("PP-OCRv6_small_rec_onnx")
    get_model_path("PP-OCRv6_small_det_onnx")

    get_model_path("PP-OCRv6_medium_rec_onnx")
    get_model_path("PP-OCRv6_medium_det_onnx")


def download_formula():
    get_model_path("formula/encoder.onnx")
    get_model_path("formula/decoder.onnx")
    get_model_path("formula/image_resizer.onnx")
    get_model_path("formula/tokenizer.json")


def download_mfr():
    get_model_path("mfr")


def download_ocr():
    from rapidocr import ModelType, OCRVersion, RapidOCR

    for version in [OCRVersion.PPOCRV5, OCRVersion.PPOCRV4]:
        for model_type in [ModelType.MOBILE, ModelType.SERVER]:
            params = {
                "Det.ocr_version": version,
                "Rec.ocr_version": version,
                # 目前没有配置v5的，必须使用v4的
                "Cls.ocr_version": version,  # OCRVersion.PPOCRV4,
                # server or mobile
                "Det.model_type": model_type,
                "Rec.model_type": model_type,
                # v4仅仅有mobile
                "Cls.model_type": ModelType.MOBILE
                if version == OCRVersion.PPOCRV4
                else model_type,
            }
            RapidOCR(params=params)


def download_layout():
    from rapid_layout import ModelType
    from rapid_layout.model_handler import ModelProcessor

    ModelProcessor.get_model_path(ModelType.PP_DOC_LAYOUTV2)
    ModelProcessor.get_model_path(ModelType.PP_DOC_LAYOUTV3)


# ===抛弃===
def download_table_cls():
    try:
        from table_cls import TableCls
        from table_cls.main import ModelType

        TableCls.get_model_path(ModelType.YOLO_CLS_X.value, None)
        TableCls.get_model_path(ModelType.YOLO_CLS.value, None)
        TableCls.get_model_path(ModelType.PADDLE_CLS.value, None)
        TableCls.get_model_path(ModelType.Q_CLS.value, None)
    except ImportError:
        pass


def download_latex():
    from pathlib import Path

    from rapid_latex_ocr import LatexOCR, utils
    from rapid_latex_ocr.utils import DownloadModel

    def patch_url():
        # old_init=DownloadModel.__init__
        def new_init(self) -> None:
            # 这个太慢
            # self.url = "https://github.com/RapidAI/RapidLaTeXOCR/releases/download/v0.0.0"
            self.url = (
                "https://modelscope.cn/models/Memect/rapid_latex_ocr/resolve/v1.0.0"
            )
            self.cur_dir = Path(utils.__file__).resolve().parent

        DownloadModel.__init__ = new_init

    patch_url()

    LatexOCR()
