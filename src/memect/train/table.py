#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import random
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Annotated, Any, Sequence

import numpy as np
import typer
import yaml
from PIL import Image, ImageDraw, ImageFont


app = typer.Typer(no_args_is_help=True,help='表格识别训练')

MODEL_NAME = "PicoDet_layout_1x_table"
LABEL = "Table"
DEFAULT_ROOT = Path("./table-train")
PADDLEX_LEGACY_MODEL_ENV = {
    "FLAGS_json_format_model": "0",
    "FLAGS_enable_pir_api": "0",
}

INFER_URL = (
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/"
    "official_inference_model/paddle3.0.0/PicoDet_layout_1x_table_infer.tar"
)
PRETRAINED_URL = (
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/"
    "official_pretrained_model/PicoDet_layout_1x_table_pretrained.pdparams"
)
CONFIG_URL = (
    "https://raw.githubusercontent.com/PaddlePaddle/PaddleX/develop/"
    "paddlex/configs/modules/layout_detection/PicoDet_layout_1x_table.yaml"
)

IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
LABEL_ALIASES = {"Table", "table", "TABLE", "表格"}
PIL_INTERP_BY_PADDLEX_CODE = {
    # PaddleX maps integer interp as:
    # 0 NEAREST, 1 LINEAR, 2 BICUBIC, 3 AREA, 4 LANCZOS4.
    0: Image.NEAREST,
    1: Image.BILINEAR,
    2: Image.BICUBIC,
    3: Image.BOX,
    4: Image.LANCZOS,
}


def _echo(message: Any) -> None:
    typer.echo(message)


def _repo_model_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "models" / MODEL_NAME


def _weights_model_dir(root: Path) -> Path:
    return root / "weights" / MODEL_NAME


def _infer_dir(root: Path) -> Path:
    return _weights_model_dir(root) / f"{MODEL_NAME}_infer"


def _pretrained_path(root: Path) -> Path:
    return _weights_model_dir(root) / f"{MODEL_NAME}_pretrained.pdparams"


def _config_path(root: Path) -> Path:
    return _weights_model_dir(root) / f"{MODEL_NAME}.yaml"


def _manifest_path(root: Path) -> Path:
    return _weights_model_dir(root) / "manifest.json"


def _image_files(images_dir: Path) -> list[Path]:
    if not images_dir.exists():
        return []
    return sorted(
        path
        for path in images_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def _label_files(labels_dir: Path) -> list[Path]:
    if not labels_dir.exists():
        return []
    return sorted(
        path
        for path in labels_dir.rglob("*.json")
        if path.is_file() and not path.name.startswith(".")
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_file(url: str, path: Path, *, force: bool) -> None:
    if path.is_file() and not force:
        _echo(f"exists: {path}")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    _echo(f"download: {url}")
    try:
        import httpx

        with httpx.stream("GET", url, follow_redirects=True, timeout=None) as response:
            response.raise_for_status()
            with tmp.open("wb") as file:
                for chunk in response.iter_bytes(1024 * 1024):
                    if chunk:
                        file.write(chunk)
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
    _echo(f"saved: {path}")


def _safe_extract_tar(tar_path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    root = target_dir.resolve()
    with tarfile.open(tar_path) as tar:
        for member in tar.getmembers():
            member_path = (root / member.name).resolve()
            if root != member_path and root not in member_path.parents:
                raise typer.BadParameter(f"unsafe tar member: {member.name}")
        tar.extractall(root, filter="data")


def _download(root: Path, *, force: bool) -> dict[str, Any]:
    root = root.resolve()
    model_dir = _weights_model_dir(root)
    model_dir.mkdir(parents=True, exist_ok=True)

    infer_tar = model_dir / f"{MODEL_NAME}_infer.tar"
    pretrained = _pretrained_path(root)
    config = _config_path(root)

    _download_file(INFER_URL, infer_tar, force=force)
    _download_file(PRETRAINED_URL, pretrained, force=force)
    _download_file(CONFIG_URL, config, force=force)

    infer_dir = _infer_dir(root)
    if force and infer_dir.exists():
        shutil.rmtree(infer_dir)
    if not infer_dir.exists():
        _safe_extract_tar(infer_tar, model_dir)

    yml = infer_dir / "inference.yml"
    if yml.is_file():
        shutil.copy2(yml, model_dir / "inference.yml")

    manifest = {
        "model": MODEL_NAME,
        "label": LABEL,
        "downloaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "files": {
            infer_tar.name: {
                "url": INFER_URL,
                "sha256": _sha256(infer_tar),
            },
            pretrained.name: {
                "url": PRETRAINED_URL,
                "sha256": _sha256(pretrained),
            },
            config.name: {
                "url": CONFIG_URL,
                "sha256": _sha256(config),
            },
        },
    }
    _manifest_path(root).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _downloaded(root: Path) -> bool:
    return (
        _pretrained_path(root).is_file()
        and _config_path(root).is_file()
        and (_infer_dir(root) / "inference.yml").is_file()
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_labelme_image(json_path: Path, images_dir: Path, data: dict[str, Any]) -> Path:
    candidates: list[Path] = []
    image_path = data.get("imagePath")
    if isinstance(image_path, str) and image_path:
        raw = Path(image_path)
        if raw.is_absolute():
            candidates.append(raw)
        else:
            candidates.append(json_path.parent / raw)
            candidates.append(images_dir / raw)
            candidates.append(images_dir / raw.name)

    for suffix in IMAGE_SUFFIXES:
        candidates.append(images_dir / f"{json_path.stem}{suffix}")

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise typer.BadParameter(f"找不到LabelMe对应图片: {json_path}")


def _image_size(image_path: Path, data: dict[str, Any]) -> tuple[int, int]:
    width = data.get("imageWidth")
    height = data.get("imageHeight")
    if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
        return width, height
    with Image.open(image_path) as image:
        return image.size


def _shape_bbox(shape: dict[str, Any], width: int, height: int) -> list[float] | None:
    label = str(shape.get("label", "")).strip()
    if label not in LABEL_ALIASES:
        return None

    points = shape.get("points")
    if not isinstance(points, list) or len(points) < 2:
        return None

    xs: list[float] = []
    ys: list[float] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return None
        xs.append(float(point[0]))
        ys.append(float(point[1]))

    x0 = max(0.0, min(float(width), min(xs)))
    x1 = max(0.0, min(float(width), max(xs)))
    y0 = max(0.0, min(float(height), min(ys)))
    y1 = max(0.0, min(float(height), max(ys)))
    box_w = x1 - x0
    box_h = y1 - y0
    if box_w <= 1 or box_h <= 1:
        return None
    return [round(x0, 2), round(y0, 2), round(box_w, 2), round(box_h, 2)]


def _split_items(items: Sequence[Path], val_ratio: float, seed: int) -> tuple[list[Path], list[Path]]:
    if val_ratio < 0 or val_ratio >= 1:
        raise typer.BadParameter("--val-ratio 必须满足 0 <= value < 1")
    shuffled = list(items)
    random.Random(seed).shuffle(shuffled)
    if len(shuffled) <= 1 or val_ratio == 0:
        return sorted(shuffled), []
    val_count = max(1, min(len(shuffled) - 1, round(len(shuffled) * val_ratio)))
    return sorted(shuffled[val_count:]), sorted(shuffled[:val_count])


def _build_coco(labelme_files: Sequence[Path], *, images_dir: Path) -> tuple[dict[str, Any], dict[str, int]]:
    images: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    stats = {"images": 0, "annotations": 0, "skipped_shapes": 0}

    for image_id, json_path in enumerate(labelme_files, 1):
        data = _read_json(json_path)
        image_path = _resolve_labelme_image(json_path, images_dir, data)
        width, height = _image_size(image_path, data)
        try:
            file_name = image_path.relative_to(images_dir.resolve()).as_posix()
        except ValueError:
            file_name = image_path.name

        images.append(
            {
                "id": image_id,
                "file_name": file_name,
                "width": width,
                "height": height,
            }
        )
        stats["images"] += 1

        for shape in data.get("shapes", []):
            bbox = _shape_bbox(shape, width, height)
            if bbox is None:
                stats["skipped_shapes"] += 1
                continue
            annotations.append(
                {
                    "id": len(annotations) + 1,
                    "image_id": image_id,
                    "category_id": 1,
                    "bbox": bbox,
                    "area": round(bbox[2] * bbox[3], 2),
                    "iscrowd": 0,
                    "segmentation": [],
                }
            )
            stats["annotations"] += 1

    coco = {
        "images": images,
        "annotations": annotations,
        "categories": [{"id": 1, "name": LABEL, "supercategory": "layout"}],
    }
    return coco, stats


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ensure_dir_link(link: Path, target: Path) -> None:
    target = target.resolve()
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        if link.resolve() == target:
            return
        link.unlink()
    elif link.exists():
        if link.is_dir():
            return
        raise typer.BadParameter(f"路径已存在且不是目录: {link}")

    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        shutil.copytree(target, link, dirs_exist_ok=True)


def _prepare_dataset(
    root: Path,
    *,
    images_dir: Path,
    labels_dir: Path,
    val_ratio: float,
    seed: int,
) -> tuple[Path, dict[str, Any]]:
    if not images_dir.is_dir():
        raise typer.BadParameter(f"images目录不存在: {images_dir}")
    if not labels_dir.is_dir():
        raise typer.BadParameter(f"labels目录不存在: {labels_dir}")

    label_files = _label_files(labels_dir)
    if not label_files:
        raise typer.BadParameter(f"没有找到LabelMe标注: {labels_dir}")

    train_items, val_items = _split_items(label_files, val_ratio, seed)
    train_coco, train_stats = _build_coco(train_items, images_dir=images_dir)
    val_coco, val_stats = _build_coco(val_items, images_dir=images_dir)

    if train_stats["annotations"] <= 0:
        raise typer.BadParameter("训练集没有任何Table标注框")

    dataset_dir = root / "trains" / "dataset"
    annotations_dir = dataset_dir / "annotations"
    _ensure_dir_link(dataset_dir / "images", images_dir)
    _write_json(annotations_dir / "instance_train.json", train_coco)
    _write_json(annotations_dir / "instance_val.json", val_coco)

    stats = {
        "train": train_stats,
        "val": val_stats,
        "dataset_dir": str(dataset_dir.resolve()),
    }
    _write_json(dataset_dir / "stats.json", stats)
    return dataset_dir, stats


def _is_paddlex_root(path: Path) -> bool:
    return (path / "main.py").is_file() and (path / "paddlex").is_dir()


def _find_paddlex_root(root: Path | None) -> Path:
    if root is not None:
        candidate = root.expanduser().resolve()
        if _is_paddlex_root(candidate):
            _echo(f"PaddleX: {candidate}")
            return candidate
        raise typer.BadParameter(f"PaddleX源码目录无效: {candidate}")

    current = Path.cwd().resolve()
    while True:
        if _is_paddlex_root(current):
            _echo(f"PaddleX: {current}")
            return current

        candidate = current / "PaddleX"
        if _is_paddlex_root(candidate):
            resolved = candidate.resolve()
            _echo(f"PaddleX: {resolved}")
            return resolved

        parent = current.parent
        if parent == current:
            break
        current = parent

    raise typer.BadParameter("找不到PaddleX源码目录，请用--paddlex-root指定")


def _resolve_python(python: Path | None, paddlex_root: Path | None = None) -> str:
    if python is not None:
        path = python.expanduser().resolve()
        if not path.is_file():
            raise typer.BadParameter(f"Python解释器不存在: {path}")
        return str(path)
    if paddlex_root is not None:
        if os.name == "nt":
            candidates = (
                Path(".venv") / "Scripts" / "python.exe",
                Path(".venv") / "Scripts" / "python",
            )
        else:
            candidates = (
                Path(".venv") / "bin" / "python",
                Path(".venv") / "bin" / "python3",
            )
        for relative in candidates:
            if (paddlex_root / relative).is_file():
                return str(relative)
        expected = ".venv\\Scripts\\python.exe" if os.name == "nt" else ".venv/bin/python"
        raise typer.BadParameter(
            f"找不到PaddleX虚拟环境Python: {paddlex_root / expected}；"
            "请先在PaddleX目录创建.venv，或通过--python指定解释器"
        )
    return sys.executable


def _config_arg(config: Path, paddlex_root: Path) -> str:
    try:
        return config.relative_to(paddlex_root).as_posix()
    except ValueError:
        return str(config)


def _resolve_paddlex_config(
    root: Path,
    paddlex_root: Path,
    config: Path | None,
) -> Path:
    if config is not None:
        path = config.expanduser()
        if not path.is_absolute():
            path = paddlex_root / path
        path = path.resolve()
    elif _config_path(root).is_file():
        path = _config_path(root).resolve()
    else:
        path = (
            paddlex_root
            / "paddlex"
            / "configs"
            / "modules"
            / "layout_detection"
            / f"{MODEL_NAME}.yaml"
        ).resolve()
    if not path.is_file():
        raise typer.BadParameter(f"PaddleX配置文件不存在: {path}")
    return path


def _run_subprocess(
    cmd: Sequence[str],
    *,
    cwd: Path,
    dry_run: bool,
    stage: str | None = None,
    env: dict[str, str] | None = None,
) -> None:
    if stage:
        _echo(f"==> {stage} cwd={cwd}")
    if env:
        _echo("env: " + shlex.join(f"{key}={value}" for key, value in env.items()))
    _echo(shlex.join(str(part) for part in cmd))
    if dry_run:
        return
    subprocess_env = None
    if env is not None:
        subprocess_env = os.environ.copy()
        subprocess_env.update(env)
    try:
        subprocess.check_call(list(cmd), cwd=cwd, env=subprocess_env)
    except subprocess.CalledProcessError as exc:
        if stage:
            _echo(f"<== {stage} failed: exit {exc.returncode}")
        raise typer.Exit(exc.returncode) from exc
    if stage:
        _echo(f"<== {stage} done")


def _resolve_executable(command: str) -> str:
    path = Path(command).expanduser()
    if path.is_absolute() or os.sep in command:
        if path.is_file():
            return str(path.resolve())
        raise typer.BadParameter(f"可执行文件不存在: {path}")

    resolved = shutil.which(command)
    if resolved:
        return resolved
    raise typer.BadParameter(f"找不到命令: {command}，请安装LabelMe或用--labelme指定")


def _paddlex_command(
    *,
    python_cmd: str,
    paddlex_root: Path,
    config: Path,
    mode: str,
    dataset_dir: Path | None = None,
    output_dir: Path | None = None,
    device: str | None = None,
    overrides: Sequence[str] = (),
) -> list[str]:
    cmd = [
        python_cmd,
        "main.py",
        "-c",
        _config_arg(config, paddlex_root),
        "-o",
        f"Global.mode={mode}",
    ]
    if dataset_dir is not None:
        cmd.extend(["-o", f"Global.dataset_dir={dataset_dir.resolve()}"])
    if output_dir is not None:
        cmd.extend(["-o", f"Global.output={output_dir.resolve()}"])
    if device:
        cmd.extend(["-o", f"Global.device={device}"])
    for override in overrides:
        cmd.extend(["-o", override])
    return cmd


def _latest_run_file(root: Path) -> Path:
    return root / "trains" / "latest.txt"


def _write_latest_run(root: Path, run_dir: Path) -> None:
    latest = _latest_run_file(root)
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(str(run_dir.resolve()) + "\n", encoding="utf-8")

    link = root / "trains" / "latest"
    if link.exists() or link.is_symlink():
        if link.is_symlink():
            link.unlink()
            try:
                link.symlink_to(run_dir.resolve(), target_is_directory=True)
            except OSError:
                pass
        return
    try:
        link.symlink_to(run_dir.resolve(), target_is_directory=True)
    except OSError:
        pass


def _run_dirs(root: Path) -> list[Path]:
    runs = root / "trains" / "runs"
    if not runs.is_dir():
        return []
    return sorted(
        (path for path in runs.iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
    )


def _resolve_default_model(root: Path) -> Path:
    latest = _latest_run_file(root)
    if latest.is_file():
        path = Path(latest.read_text(encoding="utf-8").strip()).expanduser()
        if path.exists():
            return path.resolve()

    link = root / "trains" / "latest"
    if link.exists():
        return link.resolve()

    runs = _run_dirs(root)
    if runs:
        return runs[-1].resolve()

    infer_dir = _infer_dir(root)
    if infer_dir.is_dir():
        return infer_dir.resolve()

    raise typer.BadParameter("没有找到默认模型，请使用--model指定")


def _candidate_weight_paths(model: Path) -> list[Path]:
    if model.is_file():
        return [model]
    return [
        model / "best_model" / "best_model.pdparams",
        model / "model_final" / "model.pdparams",
        model / "model_final" / "model_final.pdparams",
        model / "model_final" / "best_model.pdparams",
    ]


def _resolve_weight_path(model: Path) -> Path:
    for path in _candidate_weight_paths(model):
        if path.is_file() and path.suffix == ".pdparams":
            return path.resolve()
    raise typer.BadParameter(f"找不到可导出的pdparams权重: {model}")


def _find_inference_dir(path: Path) -> Path | None:
    if path.is_file():
        path = path.parent
    if _is_paddle_inference_dir(path):
        return path
    if not path.is_dir():
        return None
    matches = [
        item
        for item in path.rglob("*")
        if item.is_dir() and _is_paddle_inference_dir(item)
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda item: item.stat().st_mtime)[-1]


def _is_paddle_inference_dir(path: Path) -> bool:
    return (
        (path / "inference.pdiparams").is_file()
        and ((path / "inference.json").is_file() or (path / "inference.pdmodel").is_file())
    )


def _paddle2onnx_model_filename(inference_dir: Path, *, dry_run: bool) -> str:
    if (inference_dir / "inference.pdmodel").is_file():
        return "inference.pdmodel"
    if (inference_dir / "inference.json").is_file():
        raise typer.BadParameter(
            "当前Paddle inference目录只有inference.json(PIR格式)，"
            "此格式在PicoDet_layout_1x_table上可能触发paddle2onnx PIR解析错误；"
            "请用训练输出目录或pdparams checkpoint重新执行export生成inference.pdmodel"
        )
    if dry_run:
        return "inference.pdmodel"
    raise typer.BadParameter(f"找不到inference.pdmodel: {inference_dir}")


def _resolve_paddle2onnx_command(
    python_cmd: str,
    paddle2onnx: Path | None,
    *,
    cwd: Path,
) -> list[str]:
    if paddle2onnx is not None:
        path = paddle2onnx.expanduser().resolve()
        if not path.is_file():
            raise typer.BadParameter(f"paddle2onnx不存在: {path}")
        return [str(path)]

    python_path = Path(python_cmd)
    python_dir = python_path.parent if python_path.is_absolute() else cwd / python_path.parent
    for name in ("paddle2onnx", "paddle2onnx.exe", "paddle2onnx.cmd", "paddle2onnx.bat"):
        candidate = python_dir / name
        if candidate.is_file():
            return [str(candidate)]
    return [python_cmd, "-c", "from paddle2onnx.command import main; main()"]


def _convert_paddle_inference_to_onnx(
    *,
    inference_dir: Path,
    output_dir: Path,
    python_cmd: str,
    cwd: Path,
    paddle2onnx: Path | None,
    onnx_opset: int | None,
    force: bool,
    dry_run: bool,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "inference.onnx"
    if out_file.exists() and not force:
        raise typer.BadParameter(f"{out_file} 已存在，请加--force覆盖")

    model_filename = _paddle2onnx_model_filename(inference_dir, dry_run=dry_run)
    cmd = [
        *_resolve_paddle2onnx_command(python_cmd, paddle2onnx, cwd=cwd),
        "--model_dir",
        str(inference_dir.resolve()),
        "--model_filename",
        model_filename,
        "--params_filename",
        "inference.pdiparams",
        "--save_file",
        str(out_file.resolve()),
    ]
    if onnx_opset is not None:
        cmd.extend(["--opset_version", str(onnx_opset)])
    _run_subprocess(cmd, cwd=cwd, dry_run=dry_run, stage="paddle2onnx")
    _copy_inference_yml(inference_dir, output_dir, dry_run=dry_run)
    return out_file


def _copy_inference_yml(source_dir: Path, output_dir: Path, *, dry_run: bool) -> None:
    candidates = [
        source_dir / "inference.yml",
        source_dir / "inference.yaml",
        _infer_dir(Path(".").resolve()) / "inference.yml",
        _repo_model_dir() / "inference.yml",
    ]
    source = next((path for path in candidates if path.is_file()), None)
    if source is None or dry_run:
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output_dir / "inference.yml")
    shutil.copy2(source, output_dir / "inference.yaml")


def _copy_onnx_model(model: Path, output_dir: Path, *, force: bool, dry_run: bool) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "inference.onnx"
    if out_file.exists() and out_file.resolve() != model.resolve() and not force:
        raise typer.BadParameter(f"{out_file} 已存在，请加--force覆盖")
    if not dry_run and out_file.resolve() != model.resolve():
        shutil.copy2(model, out_file)
    _copy_inference_yml(model.parent, output_dir, dry_run=dry_run)
    return out_file


def _load_inference_config(config_path: Path) -> dict[str, Any]:
    return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}


def _resolve_onnx_model(root: Path, model: Path | None) -> Path:
    candidates: list[Path] = []
    if model is not None:
        if model.is_dir():
            candidates.append(model / "inference.onnx")
        else:
            candidates.append(model)
    else:
        candidates.extend(
            [
                root / "onnx" / "inference.onnx",
                _weights_model_dir(root) / "inference.onnx",
                _repo_model_dir() / "inference.onnx",
            ]
        )

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise typer.BadParameter("找不到ONNX模型，请先执行export或用--model指定")


def _resolve_infer_config(root: Path, model: Path, config: Path | None) -> Path:
    candidates: list[Path] = []
    if config is not None:
        candidates.append(config)
    candidates.extend(
        [
            model.parent / "inference.yml",
            model.parent / "inference.yaml",
            root / "onnx" / "inference.yml",
            root / "onnx" / "inference.yaml",
            _weights_model_dir(root) / "inference.yml",
            _infer_dir(root) / "inference.yml",
            _repo_model_dir() / "inference.yml",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise typer.BadParameter("找不到inference.yml，请用--config指定")


def _preprocess_for_onnx(image: Image.Image, config: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    target_h, target_w = 800, 608
    mean = [0.485, 0.456, 0.406]
    std = [0.229, 0.224, 0.225]
    is_scale = True
    interp = Image.BICUBIC

    for op in config.get("Preprocess", []) or []:
        if not isinstance(op, dict):
            continue
        if op.get("type") == "Resize":
            target_size = op.get("target_size")
            if isinstance(target_size, list) and len(target_size) == 2:
                target_h, target_w = int(target_size[0]), int(target_size[1])
            interp_code = int(op.get("interp", 2))
            try:
                interp = PIL_INTERP_BY_PADDLEX_CODE[interp_code]
            except KeyError:
                raise typer.BadParameter(f"unsupported Resize interp: {interp_code}") from None
        elif op.get("type") == "NormalizeImage":
            mean = [float(v) for v in op.get("mean", mean)]
            std = [float(v) for v in op.get("std", std)]
            is_scale = bool(op.get("is_scale", is_scale))

    orig_w, orig_h = image.size
    resized = image.resize((target_w, target_h), interp)
    arr = np.asarray(resized, dtype=np.float32)
    if is_scale:
        arr = arr / 255.0
    arr = (arr - np.asarray(mean, dtype=np.float32)) / np.asarray(std, dtype=np.float32)
    blob = np.transpose(arr, (2, 0, 1))[None].astype(np.float32)
    scale_factor = np.array(
        [[target_h / float(orig_h), target_w / float(orig_w)]],
        dtype=np.float32,
    )
    return blob, scale_factor


def _postprocess(dets: np.ndarray, num_dets: np.ndarray, image_size: tuple[int, int], threshold: float) -> list[dict[str, Any]]:
    width, height = image_size
    valid_num = int(np.asarray(num_dets).reshape(-1)[0])
    results: list[dict[str, Any]] = []
    for row in np.asarray(dets)[:valid_num]:
        cls_id, score, x1, y1, x2, y2 = row[:6]
        score = float(score)
        if score < threshold:
            continue
        x1 = max(0.0, min(float(width), float(x1)))
        x2 = max(0.0, min(float(width), float(x2)))
        y1 = max(0.0, min(float(height), float(y1)))
        y2 = max(0.0, min(float(height), float(y2)))
        if x2 <= x1 or y2 <= y1:
            continue
        results.append(
            {
                "label": LABEL,
                "class_id": int(cls_id),
                "score": score,
                "rect": [x1, y1, x2, y2],
            }
        )
    return results


def _draw(image: Image.Image, detections: Sequence[dict[str, Any]]) -> Image.Image:
    canvas = image.copy()
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = ImageFont.load_default()
    for det in detections:
        x1, y1, x2, y2 = det["rect"]
        label = f"{det['label']} {det['score']:.2f}"
        draw.rectangle((x1, y1, x2, y2), fill=(255, 0, 0, 30))
        draw.rectangle((x1, y1, x2, y2), outline=(255, 0, 0, 255), width=3)
        text_pos = (x1 + 2, max(0, y1 - 15))
        text_bbox = draw.textbbox(text_pos, label, font=font)
        draw.rectangle(text_bbox, fill=(255, 255, 255, 230))
        draw.text(text_pos, label, fill=(255, 0, 0, 255), font=font)
    return canvas


@app.command()
def download(
    root: Annotated[Path, typer.Option("--root", "-r", help="工作目录")] = DEFAULT_ROOT,
    force: Annotated[bool, typer.Option(help="重新下载并覆盖已有文件")] = False,
) -> None:
    """下载PicoDet_layout_1x_table官方推理包、训练权重和PaddleX配置。"""
    manifest = _download(root, force=force)
    _echo(json.dumps(manifest, ensure_ascii=False, indent=2))


@app.command()
def label(
    root: Annotated[Path, typer.Option("--root", "-r", help="工作目录")] = DEFAULT_ROOT,
    images: Annotated[Path, typer.Option(help="训练图片目录")] = Path("images"),
    labels: Annotated[Path, typer.Option(help="LabelMe标注输出目录")] = Path("labels"),
    labelme: Annotated[str, typer.Option(help="labelme命令或可执行文件路径")] = "labelme",
    dry_run: Annotated[bool, typer.Option(help="只打印命令，不执行")] = False,
) -> None:
    """打开LabelMe标注root/images，并把JSON保存到root/labels。"""
    root = root.resolve()
    images_dir = (root / images).resolve() if not images.is_absolute() else images.resolve()
    labels_dir = (root / labels).resolve() if not labels.is_absolute() else labels.resolve()
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    labelme_cmd = _resolve_executable(labelme)
    cmd = [
        labelme_cmd,
        str(images_dir),
        "--output",
        str(labels_dir),
    ]
    _run_subprocess(cmd, cwd=root, dry_run=dry_run)


@app.command()
def train(
    root: Annotated[Path, typer.Option("--root", "-r", help="工作目录")] = DEFAULT_ROOT,
    device: Annotated[str, typer.Option(help="PaddleX训练设备，如cpu或gpu:0")] = "gpu:0",
    epochs: Annotated[int, typer.Option("--epochs", help="训练轮数")] = 10,
    batch_size: Annotated[int, typer.Option(help="训练batch size")] = 24,
    images: Annotated[Path, typer.Option(help="训练图片目录")] = Path("images"),
    labels: Annotated[Path, typer.Option(help="LabelMe标注目录")] = Path("labels"),
    val_ratio: Annotated[float, typer.Option(help="验证集比例")] = 0.1,
    seed: Annotated[int, typer.Option(help="训练/验证划分随机种子")] = 2026,
    paddlex_root: Annotated[Path | None, typer.Option(help="PaddleX源码目录，默认从当前目录向上查找")] = None,
    python: Annotated[Path | None, typer.Option(help="执行PaddleX的Python解释器")] = None,
    config: Annotated[Path | None, typer.Option(help="PaddleX配置文件")] = None,
    output: Annotated[Path | None, typer.Option(help="训练输出目录，默认trains/runs/<timestamp>")] = None,
    resume: Annotated[Path | None, typer.Option(help="PaddleX Train.resume_path")] = None,
    overrides: Annotated[list[str] | None, typer.Option("--set", help="额外PaddleX -o参数")] = None,
    skip_check: Annotated[bool, typer.Option(help="跳过check_dataset")] = False,
    check_only: Annotated[bool, typer.Option(help="只执行check_dataset，不执行训练")] = False,
    skip_eval: Annotated[bool, typer.Option(help="跳过evaluate")] = False,
    skip_predict: Annotated[bool, typer.Option(help="跳过训练后的predict")] = False,
    dry_run: Annotated[bool, typer.Option(help="只打印命令，不执行")] = False,
) -> None:
    """转换LabelMe标注为COCO，并调用PaddleX训练、推理和验证。"""
    root = root.resolve()
    if not _downloaded(root):
        _echo("weights缺失，自动执行download")
        _download(root, force=False)

    images_dir = (root / images).resolve() if not images.is_absolute() else images.resolve()
    labels_dir = (root / labels).resolve() if not labels.is_absolute() else labels.resolve()
    dataset_dir, stats = _prepare_dataset(
        root,
        images_dir=images_dir,
        labels_dir=labels_dir,
        val_ratio=val_ratio,
        seed=seed,
    )
    _echo(json.dumps(stats, ensure_ascii=False, indent=2))

    paddlex = _find_paddlex_root(paddlex_root)
    python_cmd = _resolve_python(python, paddlex)
    config_path = _resolve_paddlex_config(root, paddlex, config)
    run_dir = (
        output.resolve()
        if output is not None
        else (root / "trains" / "runs" / time.strftime("%Y%m%d_%H%M%S")).resolve()
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    base_overrides = [
        "Global.model=PicoDet_layout_1x_table",
        "Train.num_classes=1",
        f"Train.epochs_iters={epochs}",
        f"Train.batch_size={batch_size}",
        f"Train.pretrain_weight_path={_pretrained_path(root).resolve()}",
    ]
    if resume is not None:
        base_overrides.append(f"Train.resume_path={resume.expanduser().resolve()}")
    base_overrides.extend(overrides or [])

    if not skip_check:
        _run_subprocess(
            _paddlex_command(
                python_cmd=python_cmd,
                paddlex_root=paddlex,
                config=config_path,
                mode="check_dataset",
                dataset_dir=dataset_dir,
                output_dir=run_dir,
                device=device,
                overrides=base_overrides,
            ),
            cwd=paddlex,
            dry_run=dry_run,
            stage="check_dataset",
        )
        if check_only:
            _echo("check_only: skip train/evaluate/predict")
            return

    _echo(
        "note: PaddleX may print "
        f"\"The model({MODEL_NAME}) don't support to update_static_assigner_epochs!\"; "
        "for this model it is an informational message, not a train failure."
    )
    _echo(f"train_log: {run_dir / 'train.log'}")
    _run_subprocess(
        _paddlex_command(
            python_cmd=python_cmd,
            paddlex_root=paddlex,
            config=config_path,
            mode="train",
            dataset_dir=dataset_dir,
            output_dir=run_dir,
            device=device,
            overrides=base_overrides,
        ),
        cwd=paddlex,
        dry_run=dry_run,
        stage="train",
        env=PADDLEX_LEGACY_MODEL_ENV,
    )
    _write_latest_run(root, run_dir)

    best_weight = run_dir / "best_model" / "best_model.pdparams"
    if not skip_eval:
        eval_overrides = [
            *(overrides or []),
            f"Evaluate.weight_path={best_weight.resolve()}",
        ]
        _run_subprocess(
            _paddlex_command(
                python_cmd=python_cmd,
                paddlex_root=paddlex,
                config=config_path,
                mode="evaluate",
                dataset_dir=dataset_dir,
                output_dir=run_dir,
                device=device,
                overrides=eval_overrides,
            ),
            cwd=paddlex,
            dry_run=dry_run,
            stage="evaluate",
        )

    if not skip_predict:
        image_candidates = _image_files(images_dir)
        inference_dir = _find_inference_dir(run_dir)
        if image_candidates and (inference_dir is not None or dry_run):
            predict_overrides = [
                *(overrides or []),
                f"Predict.input={image_candidates[0].resolve()}",
            ]
            if inference_dir is not None:
                predict_overrides.append(f"Predict.model_dir={inference_dir.resolve()}")
            _run_subprocess(
                _paddlex_command(
                    python_cmd=python_cmd,
                    paddlex_root=paddlex,
                    config=config_path,
                    mode="predict",
                    dataset_dir=dataset_dir,
                    output_dir=run_dir,
                    device=device,
                    overrides=predict_overrides,
                ),
                cwd=paddlex,
                dry_run=dry_run,
                stage="predict",
            )
        else:
            _echo("skip predict: 未找到训练图片或Paddle inference目录")

    _echo(f"latest: {run_dir}")


@app.command()
def infer(
    image: Annotated[Path, typer.Argument(help="待推理图片")],
    root: Annotated[Path, typer.Option("--root", "-r", help="工作目录")] = DEFAULT_ROOT,
    model: Annotated[Path | None, typer.Option(help="ONNX模型文件或目录")] = None,
    config: Annotated[Path | None, typer.Option(help="inference.yml路径")] = None,
    threshold: Annotated[float, typer.Option(help="置信度阈值")] = 0.5,
    show: Annotated[bool, typer.Option(help="显示画框后的图片")] = False,
    save: Annotated[Path | None, typer.Option(help="保存画框后的图片")] = None,
) -> None:
    """使用ONNXRuntime执行表格区域推理。"""
    import onnxruntime as ort

    root = root.resolve()
    image_path = image.expanduser().resolve()
    if not image_path.is_file():
        raise typer.BadParameter(f"图片不存在: {image_path}")

    model_path = _resolve_onnx_model(root, model)
    config_path = _resolve_infer_config(root, model_path, config)
    cfg = _load_inference_config(config_path)

    source = Image.open(image_path).convert("RGB")
    blob, scale_factor = _preprocess_for_onnx(source, cfg)
    sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    dets, num_dets = sess.run(None, {"image": blob, "scale_factor": scale_factor})
    detections = _postprocess(dets, num_dets, source.size, threshold)

    for det in detections:
        x1, y1, x2, y2 = det["rect"]
        _echo(
            f"{det['label']} {det['score']:.6f} "
            f"{x1:.2f} {y1:.2f} {x2:.2f} {y2:.2f}"
        )
    if not detections:
        _echo(f"no detections above threshold {threshold}")

    if show or save is not None:
        canvas = _draw(source, detections)
        if save is not None:
            save_path = save.expanduser().resolve()
            save_path.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(save_path)
            _echo(f"saved: {save_path}")
        if show:
            canvas.show()


@app.command(name="export")
def export_model(
    root: Annotated[Path, typer.Option("--root", "-r", help="工作目录")] = DEFAULT_ROOT,
    model: Annotated[Path | None, typer.Option(help="训练输出目录、checkpoint、Paddle inference目录或ONNX文件")] = None,
    output: Annotated[Path, typer.Option(help="ONNX输出目录")] = Path("onnx"),
    paddlex_root: Annotated[Path | None, typer.Option(help="PaddleX源码目录，导出checkpoint时需要")] = None,
    python: Annotated[Path | None, typer.Option(help="执行PaddleX/paddle2onnx的Python解释器")] = None,
    config: Annotated[Path | None, typer.Option(help="PaddleX配置文件，导出checkpoint时需要")] = None,
    paddle2onnx: Annotated[Path | None, typer.Option(help="paddle2onnx可执行文件")] = None,
    onnx_opset: Annotated[int | None, typer.Option(help="paddle2onnx opset_version")] = None,
    device: Annotated[str, typer.Option(help="PaddleX export设备")] = "cpu",
    force: Annotated[bool, typer.Option(help="覆盖已有onnx/inference.onnx")] = False,
    dry_run: Annotated[bool, typer.Option(help="只打印命令，不执行")] = False,
) -> None:
    """导出onnx/inference.onnx和inference.yaml。"""
    root = root.resolve()
    model_path = model.expanduser().resolve() if model is not None else _resolve_default_model(root)
    output_dir = output.resolve() if output.is_absolute() else (root / output).resolve()

    if model_path.is_file() and model_path.suffix == ".onnx":
        out_file = _copy_onnx_model(model_path, output_dir, force=force, dry_run=dry_run)
        _echo(f"onnx: {out_file}")
        return

    paddlex = _find_paddlex_root(paddlex_root)
    python_cmd = _resolve_python(python, paddlex)
    inference_dir = _find_inference_dir(model_path)
    if inference_dir is not None:
        out_file = _convert_paddle_inference_to_onnx(
            inference_dir=inference_dir,
            output_dir=output_dir,
            python_cmd=python_cmd,
            cwd=paddlex,
            paddle2onnx=paddle2onnx,
            onnx_opset=onnx_opset,
            force=force,
            dry_run=dry_run,
        )
        _echo(f"onnx: {out_file}")
        return

    weight_path = _resolve_weight_path(model_path)
    config_path = _resolve_paddlex_config(root, paddlex, config)
    export_dir = root / "trains" / "exports" / time.strftime("%Y%m%d_%H%M%S")
    export_overrides = [
        f"Export.weight_path={weight_path.resolve()}",
    ]
    _run_subprocess(
        _paddlex_command(
            python_cmd=python_cmd,
            paddlex_root=paddlex,
            config=config_path,
            mode="export",
            output_dir=export_dir,
            device=device,
            overrides=export_overrides,
        ),
        cwd=paddlex,
        dry_run=dry_run,
        stage="export",
        env=PADDLEX_LEGACY_MODEL_ENV,
    )

    inference_dir = _find_inference_dir(export_dir)
    if inference_dir is None:
        if dry_run:
            inference_dir = export_dir / "model_final" / "inference"
        else:
            raise typer.BadParameter(f"导出完成但找不到Paddle inference目录: {export_dir}")

    out_file = _convert_paddle_inference_to_onnx(
        inference_dir=inference_dir,
        output_dir=output_dir,
        python_cmd=python_cmd,
        cwd=paddlex,
        paddle2onnx=paddle2onnx,
        onnx_opset=onnx_opset,
        force=force,
        dry_run=dry_run,
    )
    _echo(f"onnx: {out_file}")


if __name__ == "__main__":
    app()
