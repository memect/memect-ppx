import hashlib
import logging
import shutil
import tarfile
import threading
from tempfile import TemporaryDirectory
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
    "PP-DocLayout-V2":{
        "modelscope":"PaddlePaddle/PP-DocLayoutV2_onnx",
        "verified":False
    },
    "PP-DocLayout-L": {
        #"url": "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-DocLayout-L_infer.tar",
        #"archive": "tar",
        #"required_files": ["inference.onnx", "inference.yml"],
        "modelscope":"Memect/PP-DocLayout-L",
        "verified": False,
    },
    "PP-DocLayout_plus-L": {
        #"url": "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-DocLayout_plus-L_infer.tar",
        #"archive": "tar",
        #"required_files": ["inference.onnx", "inference.yml"],
        "modelscope":"Memect/PP-DocLayout_plus-L",
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

    def has_required_files(directory: Path) -> bool:
        required = cfg.get("required_files")
        if not required:
            return False
        return all(directory.joinpath(file).is_file() for file in required)

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
        elif path.is_dir():
            if path.joinpath("_done.txt").is_file() or has_required_files(path):
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
        elif cfg.get("archive") == "tar":
            download_and_extract_tar(cfg["url"], path, cfg.get("required_files", []))
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


def download_and_extract_tar(url: str, path: Path, required_files: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent) as temp_dir:
        temp_path = Path(temp_dir)
        archive_path = temp_path / "model.tar"
        extract_path = temp_path / "extract"
        extract_path.mkdir()
        download(url, archive_path)
        with tarfile.open(archive_path) as tar:
            for member in tar.getmembers():
                target = extract_path / member.name
                if not target.resolve().is_relative_to(extract_path.resolve()):
                    raise RuntimeError(f"tar contains unsafe path: {member.name}")
            tar.extractall(extract_path)

        source = _find_model_dir(extract_path, required_files)
        if source is None:
            raise RuntimeError("下载的模型包不包含所需文件")
        path.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, path, dirs_exist_ok=True)


def _find_model_dir(root: Path, required_files: list[str]) -> Path | None:
    if not required_files:
        return root
    for candidate in [root, *root.rglob("*")]:
        if candidate.is_dir() and all(candidate.joinpath(file).is_file() for file in required_files):
            return candidate
    return None


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
    get_model_path("PP-DocLayout-V2")
    get_model_path("PP-DocLayout-V3")
    get_model_path("PP-DocLayout-L")
    get_model_path("PP-DocLayout_plus-L")


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
