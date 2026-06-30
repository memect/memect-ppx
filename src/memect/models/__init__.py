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

    #TODO 这两个公式模型后续需要去掉
    "mfr": {"huggingface": "breezedeus/pix2text-mfr-1.5", "verified": False},
    "PP-FormulaNet_plus-M_infer": {
        "modelscope": "Memect/PP-FormulaNet_plus-M_infer",
        "verified": False,
    },
    "pp_layoutv2.onnx": {
        "url": "https://www.modelscope.cn/models/RapidAI/RapidLayout/resolve/v1.2.0/onnx/pp_doc_layout/pp_doc_layoutv2.onnx",
        "sha256": "0bd2ea0997fe0789f0300292291f8bbf897d890b44a9a3bd5be72afd6198aa90",
        "verified": False,
    },
    "pp_layoutv3.onnx": {
        "url": "https://www.modelscope.cn/models/RapidAI/RapidLayout/resolve/v1.2.0/onnx/pp_doc_layout/pp_doc_layoutv3.onnx",
        "sha256": "250dbad1dfb9e4983fab75e1bf5085cd56ec3f41d5c7d0f8623ec74856e7aa67",
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
}


def get_model_path(name: str):
    logger = logging.getLogger(f"{__name__}")
    path = Path(__file__).parent.joinpath(name)
    cfg = _models[name]

    def check_model():
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
        if check_model():
            return path

    # 模型不存在，支持多个进程同时执行的情况
    with _download_lock:
        if check_model():
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

            # 国外用户export HF_ENDPOINT=https://huggingface.co
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


def get_ocr_path(model: str, size: str):
    return get_model_path(f"PP-OCRv6_{size}_{model}_onnx") / "inference.onnx"


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


def download_mfr():
    get_model_path("mfr")


def download_ocr():
    get_model_path("PP-OCRv6_tiny_rec_onnx")
    get_model_path("PP-OCRv6_tiny_det_onnx")

    get_model_path("PP-OCRv6_small_rec_onnx")
    get_model_path("PP-OCRv6_small_det_onnx")

    get_model_path("PP-OCRv6_medium_rec_onnx")
    get_model_path("PP-OCRv6_medium_det_onnx")


def download_layout():
    get_model_path("pp_layoutv2.onnx")
    get_model_path("pp_layoutv3.onnx")


def download_table():
    get_model_path("table_det.onnx")


def download_formula():
    get_model_path("PP-FormulaNet_plus-M_infer")
    # get_model_path('PP-FormulaNet_plus-S_infer')
    get_model_path("mfr")


def download_all():
    # 因为第三方库需要的下载模型，但是下载并不支持多进程也不执行多线程，也就是如果同时启动多个进程或者多个线程
    # 执行就会冲突，所以先下载好
    download_ocr()
    download_layout()
    download_table()
    download_formula()
