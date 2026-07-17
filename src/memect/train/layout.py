from __future__ import annotations

import json
import os
import platform
import random
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Sequence

import typer
from PIL import Image, ImageDraw, ImageFont

from memect.base.utils import console

LayoutVersion = Literal["v2", "v3", "l", "plus_l"]
LayoutEngine = Literal["auto", "onnxruntime", "openvino"]

IMAGE_SUFFIXES: Final = {
    ".bmp",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}

LAYOUT_V2_LABELS: Final = (
    "abstract",
    "algorithm",
    "aside_text",
    "chart",
    "content",
    "display_formula",
    "doc_title",
    "figure_title",
    "footer",
    "footer_image",
    "footnote",
    "formula_number",
    "header",
    "header_image",
    "image",
    "inline_formula",
    "number",
    "paragraph_title",
    "reference",
    "reference_content",
    "seal",
    "table",
    "text",
    "vertical_text",
    "vision_footnote",
)

LAYOUT_V3_LABELS: Final = LAYOUT_V2_LABELS

LAYOUT_L_LABELS: Final = (
    "paragraph_title",
    "image",
    "text",
    "number",
    "abstract",
    "content",
    "figure_title",
    "formula",
    "table",
    "table_title",
    "reference",
    "doc_title",
    "footnote",
    "header",
    "algorithm",
    "footer",
    "seal",
    "chart_title",
    "chart",
    "formula_number",
    "header_image",
    "footer_image",
    "aside_text",
)

LAYOUT_PLUS_L_LABELS: Final = (
    "paragraph_title",
    "image",
    "text",
    "number",
    "abstract",
    "content",
    "figure_title",
    "formula",
    "table",
    "reference",
    "doc_title",
    "footnote",
    "header",
    "algorithm",
    "footer",
    "seal",
    "chart",
    "formula_number",
    "aside_text",
    "reference_content",
)

DEFAULT_LAYOUT_LABELS_BY_VERSION: Final[dict[LayoutVersion, tuple[str, ...]]] = {
    "v2": LAYOUT_V2_LABELS,
    "v3": LAYOUT_V3_LABELS,
    "l": LAYOUT_L_LABELS,
    "plus_l": LAYOUT_PLUS_L_LABELS,
}

DEFAULT_PADDLEX_MODELS: Final[dict[LayoutVersion, str]] = {
    "v2": "PP-DocLayoutV2",
    "v3": "PP-DocLayoutV3",
    "l": "PP-DocLayout-L",
    "plus_l": "PP-DocLayout_plus-L",
}


def _dataset_paths(
    root: Path,
    *,
    images_name: str,
    labelme_name: str,
    annotations_name: str,
    previews_name: str,
    labels_name: str,
) -> dict[str, Path]:
    return {
        "root": root,
        "images": root / images_name,
        "labelme": root / labelme_name,
        "annotations": root / annotations_name,
        "previews": root / previews_name,
        "labels": root / labels_name,
    }


def _default_labels(version: LayoutVersion) -> list[str]:
    try:
        return list(DEFAULT_LAYOUT_LABELS_BY_VERSION[version])
    except KeyError:
        raise ValueError(f"不支持的layout版本:{version}") from None


def _read_labels(path: Path, *, default_labels: Sequence[str]) -> list[str]:
    if not path.is_file():
        return list(default_labels)
    labels = [line.strip() for line in path.read_text("utf-8").splitlines()]
    return [label for label in labels if label and not label.startswith("#")]


def _write_labels(path: Path, labels: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(labels) + "\n", "utf-8")


def _relative_path(path: Path, start: Path) -> str:
    return Path(os.path.relpath(path, start=start)).as_posix()


def _image_files(images_dir: Path) -> list[Path]:
    if not images_dir.exists():
        return []
    return sorted(
        path
        for path in images_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def _labelme_files(labelme_dir: Path) -> list[Path]:
    if not labelme_dir.exists():
        return []
    return sorted(
        path
        for path in labelme_dir.rglob("*.json")
        if path.is_file() and not path.name.startswith(".")
    )


def _builtin_label_version(labels: Sequence[str]) -> LayoutVersion | None:
    label_list = list(labels)
    for version, default_labels in DEFAULT_LAYOUT_LABELS_BY_VERSION.items():
        if label_list == list(default_labels):
            return version
    return None


def _ensure_dataset_dirs(paths: dict[str, Path], *, version: LayoutVersion) -> None:
    paths["root"].mkdir(parents=True, exist_ok=True)
    paths["images"].mkdir(parents=True, exist_ok=True)
    paths["labelme"].mkdir(parents=True, exist_ok=True)
    paths["annotations"].mkdir(parents=True, exist_ok=True)
    paths["previews"].mkdir(parents=True, exist_ok=True)
    default_labels = _default_labels(version)
    if not paths["labels"].exists():
        _write_labels(paths["labels"], default_labels)
        return

    labels = _read_labels(paths["labels"], default_labels=default_labels)
    if labels == default_labels:
        return
    if not labels:
        _write_labels(paths["labels"], default_labels)
        return

    builtin_version = _builtin_label_version(labels)
    if builtin_version is not None:
        _write_labels(paths["labels"], default_labels)
        console.print(
            f"label.txt使用{builtin_version}默认标签，已切换为{version}默认标签"
        )


def _labelme_args(paths: dict[str, Path]) -> str:
    return (
        f"images --labels {paths['labels'].name} --output {paths['labelme'].name}"
    )


def _labelme_command(paths: dict[str, Path]) -> str:
    return f"cd {paths['root']} && labelme {_labelme_args(paths)}"


def _stable_labelme_command(paths: dict[str, Path]) -> str:
    return (
        f"cd {paths['root']} && "
        f"uvx --python 3.12 --with 'numpy<2' labelme {_labelme_args(paths)}"
    )


def _print_labelme_commands(paths: dict[str, Path]) -> None:
    console.print(f"labelme command: {_labelme_command(paths)}")
    console.print(f"stable labelme command: {_stable_labelme_command(paths)}")


def _init_dataset(paths: dict[str, Path], *, version: LayoutVersion) -> None:
    _ensure_dataset_dirs(paths, version=version)
    console.print(f"dataset: {paths['root']}")
    console.print(f"images: {paths['images']}")
    console.print(f"labelme: {paths['labelme']}")
    console.print(f"labels: {paths['labels']}")
    _print_labelme_commands(paths)


def _detect_auto_engine(use_cuda: bool, use_dml: bool, use_cann: bool) -> str:
    if use_cuda or use_dml or use_cann:
        return "onnxruntime"
    machine = platform.machine().lower()
    try:
        import openvino  # noqa: F401

        if sys.platform == "darwin" or machine in ("x86_64", "amd64"):
            return "openvino"
    except ImportError:
        pass
    return "onnxruntime"


def _default_model_path(version: LayoutVersion) -> Path:
    from memect.models import get_model_path
    if version == "v2":
        return Path(get_model_path("PP-DocLayout-V2") / "inference.onnx")
    elif version == "v3":
        return Path(get_model_path("PP-DocLayout-V3") / "inference.onnx")
    elif version == "l":
        return Path(get_model_path("PP-DocLayout-L") / "inference.onnx")
    elif version == "plus_l":
        return Path(get_model_path("PP-DocLayout_plus-L") / "inference.onnx")
    else:
        raise ValueError(f"不支持的layout版本:{version}")


def _create_detector(
    version: LayoutVersion,
    *,
    model_path: Path | None,
    labels: Sequence[str],
    engine: LayoutEngine,
    score_threshold: float,
    use_cuda: bool,
    use_dml: bool,
    use_cann: bool,
) -> Any:
    from memect.pdf.layout import LayoutDetector

    resolved_engine = _detect_auto_engine(use_cuda, use_dml, use_cann) if engine == "auto" else engine
    resolved_model_path = model_path or _default_model_path(version)
    return LayoutDetector(
        resolved_model_path,
        version=version,
        labels=labels,
        engine=resolved_engine,
        score_threshold=score_threshold,
        use_cuda=use_cuda,
        use_dml=use_dml,
        use_cann=use_cann,
        layout_shape_mode="rect",
    )


def _shape_from_object(obj: dict[str, Any]) -> dict[str, Any] | None:
    rect = obj.get("rect")
    if not rect or len(rect) != 4:
        return None
    x0, y0, x1, y1 = [float(value) for value in rect]
    if x1 <= x0 or y1 <= y0:
        return None
    return {
        "label": str(obj["type"]),
        "points": [[round(x0, 2), round(y0, 2)], [round(x1, 2), round(y1, 2)]],
        "group_id": None,
        "description": "",
        "shape_type": "rectangle",
        "flags": {},
        "mask": None,
    }


def _write_labelme_json(json_path: Path, image_path: Path, width: int, height: int, shapes: Sequence[dict[str, Any]]) -> None:
    data = {
        "version": "5.5.0",
        "flags": {},
        "shapes": list(shapes),
        "imagePath": _relative_path(image_path, json_path.parent),
        "imageData": None,
        "imageHeight": height,
        "imageWidth": width,
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        "utf-8",
    )


def _draw_preview(image_path: Path, shapes: Sequence[dict[str, Any]], out_path: Path) -> None:
    with Image.open(image_path) as image:
        canvas = image.convert("RGB")
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = ImageFont.load_default()
    palette = [
        (232, 67, 67),
        (30, 144, 255),
        (46, 204, 113),
        (245, 166, 35),
        (155, 89, 182),
        (0, 180, 180),
        (255, 99, 132),
        (99, 110, 114),
    ]

    for index, shape in enumerate(shapes):
        points = shape.get("points") or []
        if len(points) < 2:
            continue
        x_values = [float(point[0]) for point in points]
        y_values = [float(point[1]) for point in points]
        x0, x1 = min(x_values), max(x_values)
        y0, y1 = min(y_values), max(y_values)
        color = palette[index % len(palette)]
        draw.rectangle((x0, y0, x1, y1), outline=(*color, 255), width=3)
        label = str(shape.get("label", ""))
        if label:
            bbox = draw.textbbox((x0, y0), label, font=font)
            draw.rectangle(
                (bbox[0] - 3, bbox[1] - 3, bbox[2] + 3, bbox[3] + 3),
                fill=(255, 255, 255, 220),
            )
            draw.text((x0, y0), label, fill=(*color, 255), font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)


def _prelabel_dataset(
    version: LayoutVersion,
    paths: dict[str, Path],
    *,
    model_path: Path | None,
    engine: LayoutEngine,
    score_threshold: float,
    use_cuda: bool,
    use_dml: bool,
    use_cann: bool,
    overwrite: bool,
    preview: bool,
) -> dict[str, int]:
    _ensure_dataset_dirs(paths, version=version)
    images = _image_files(paths["images"])
    if not images:
        raise typer.BadParameter(f"images目录下没有图片: {paths['images']}")

    labels = _read_labels(paths["labels"], default_labels=_default_labels(version))
    detector = _create_detector(
        version,
        model_path=model_path,
        labels=labels,
        engine=engine,
        score_threshold=score_threshold,
        use_cuda=use_cuda,
        use_dml=use_dml,
        use_cann=use_cann,
    )

    stats = {"images": len(images), "created": 0, "skipped": 0, "objects": 0}
    for index, image_path in enumerate(images, 1):
        relative = image_path.relative_to(paths["images"])
        json_path = paths["labelme"] / relative.with_suffix(".json")
        if json_path.exists() and not overwrite:
            stats["skipped"] += 1
            continue

        result = detector.predict(image_path)
        shapes = [
            shape
            for obj in result.get("objects", [])
            if (shape := _shape_from_object(obj)) is not None
        ]
        width = int(result["width"])
        height = int(result["height"])
        _write_labelme_json(
            json_path,
            image_path,
            width,
            height,
            shapes,
        )
        if preview:
            _draw_preview(
                image_path,
                shapes,
                paths["previews"] / relative.with_suffix(".jpg"),
            )
        stats["created"] += 1
        stats["objects"] += len(shapes)
        console.log(f"prelabel {index}/{len(images)} {image_path.name}: {len(shapes)}")

    return stats


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text("utf-8"))


def _resolve_labelme_image(json_path: Path, images_dir: Path, data: dict[str, Any]) -> Path:
    image_path_text = data.get("imagePath")
    candidates: list[Path] = []
    if isinstance(image_path_text, str) and image_path_text:
        raw = Path(image_path_text)
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
    raise FileNotFoundError(f"找不到LabelMe对应图片: {json_path}")


def _image_size(image_path: Path, data: dict[str, Any]) -> tuple[int, int]:
    width = data.get("imageWidth")
    height = data.get("imageHeight")
    if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
        return width, height
    with Image.open(image_path) as image:
        return image.size


def _shape_bbox(shape: dict[str, Any], width: int, height: int) -> list[float] | None:
    points = shape.get("points")
    if not isinstance(points, list) or len(points) < 2:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for point in points:
        if not isinstance(point, list | tuple) or len(point) < 2:
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
        return shuffled, []
    val_count = round(len(shuffled) * val_ratio)
    val_count = max(1, min(len(shuffled) - 1, val_count))
    val = sorted(shuffled[:val_count])
    train = sorted(shuffled[val_count:])
    return train, val


def _build_coco(
    labelme_files: Sequence[Path],
    *,
    images_dir: Path,
    labels: Sequence[str],
    append_labels: bool,
) -> tuple[dict[str, Any], list[str], dict[str, int]]:
    categories = list(labels)
    category_ids = {label: index + 1 for index, label in enumerate(categories)}
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
            label = str(shape.get("label", "")).strip()
            if not label:
                stats["skipped_shapes"] += 1
                continue
            if label not in category_ids:
                if not append_labels:
                    raise typer.BadParameter(
                        f"标签不在label.txt中: {label} ({json_path})；"
                        "请加入label.txt或使用--append-labels"
                    )
                categories.append(label)
                category_ids[label] = len(categories)

            bbox = _shape_bbox(shape, width, height)
            if bbox is None:
                stats["skipped_shapes"] += 1
                continue

            annotations.append(
                {
                    "id": len(annotations) + 1,
                    "image_id": image_id,
                    "category_id": category_ids[label],
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
        "categories": [
            {"id": index + 1, "name": label, "supercategory": "layout"}
            for index, label in enumerate(categories)
        ],
    }
    return coco, categories, stats


def _write_coco(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")


def _convert_labelme_to_coco(
    paths: dict[str, Path],
    *,
    version: LayoutVersion,
    val_ratio: float,
    seed: int,
    append_labels: bool,
) -> dict[str, Any]:
    _ensure_dataset_dirs(paths, version=version)
    labelme_files = _labelme_files(paths["labelme"])
    if not labelme_files:
        raise typer.BadParameter(f"没有找到LabelMe标注: {paths['labelme']}")

    labels = _read_labels(paths["labels"], default_labels=_default_labels(version))
    train_items, val_items = _split_items(labelme_files, val_ratio, seed)
    train_coco, train_labels, train_stats = _build_coco(
        train_items,
        images_dir=paths["images"],
        labels=labels,
        append_labels=append_labels,
    )
    val_coco, val_labels, val_stats = _build_coco(
        val_items,
        images_dir=paths["images"],
        labels=train_labels,
        append_labels=True,
    )
    labels = train_labels
    for label in val_labels:
        if label not in labels:
            labels.append(label)

    if labels != _read_labels(
        paths["labels"],
        default_labels=_default_labels(version),
    ):
        _write_labels(paths["labels"], labels)
        train_coco, _, train_stats = _build_coco(
            train_items,
            images_dir=paths["images"],
            labels=labels,
            append_labels=False,
        )
        val_coco, _, val_stats = _build_coco(
            val_items,
            images_dir=paths["images"],
            labels=labels,
            append_labels=False,
        )

    _write_coco(paths["annotations"] / "instance_train.json", train_coco)
    _write_coco(paths["annotations"] / "instance_val.json", val_coco)
    return {
        "train": train_stats,
        "val": val_stats,
        "categories": len(labels),
        "train_file": paths["annotations"] / "instance_train.json",
        "val_file": paths["annotations"] / "instance_val.json",
    }


def _require_train_annotations(stats: dict[str, Any]) -> None:
    train_stats = stats.get("train", {})
    if int(train_stats.get("annotations", 0)) <= 0:
        raise typer.BadParameter("训练集没有任何标注框，请先用LabelMe补充标注")


def _is_paddlex_root(path: Path) -> bool:
    return (path / "main.py").is_file() and (path / "paddlex").is_dir()


def _find_paddlex_root(root: Path | None) -> Path:
    if root is not None:
        candidate = root.expanduser().resolve()
        if _is_paddlex_root(candidate):
            return candidate
        raise typer.BadParameter(f"PaddleX源码目录无效: {candidate}")

    candidates = [Path("./PaddleX"), Path("../PaddleX")]
    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        if _is_paddlex_root(candidate):
            return candidate
    raise typer.BadParameter("找不到PaddleX源码目录，默认查找./PaddleX和../PaddleX，请设置--paddlex-root")


def _resolve_config(
    paddlex_root: Path,
    *,
    config: Path | None,
    model_name: str,
) -> Path:
    if config is None:
        path = paddlex_root / "paddlex" / "configs" / "modules" / "layout_detection" / f"{model_name}.yaml"
    elif config.is_absolute():
        path = config
    else:
        path = paddlex_root / config
    path = path.resolve()
    if not path.is_file():
        raise typer.BadParameter(f"PaddleX配置文件不存在: {path}")
    return path


def _is_predict_only_layout_config(config: Path, model_name: str) -> bool:
    if model_name == "PP-DocLayoutV2":
        return True
    return config.stem == "PP-DocLayoutV2"


def _config_arg(path: Path, paddlex_root: Path) -> str:
    try:
        return path.relative_to(paddlex_root).as_posix()
    except ValueError:
        return str(path)


def _resolve_python(python: Path | None, paddlex_root: Path) -> str:
    if python is None:
        default = paddlex_root / ".venv" / "bin" / "python3"
        if default.is_file():
            return str(default)
        return sys.executable
    path = python.expanduser().resolve()
    if not path.is_file():
        raise typer.BadParameter(f"Python解释器不存在: {path}")
    return str(path)


def _resolve_paddlex_device(
    *,
    device: str | None,
    python_cmd: str,
    paddlex_root: Path,
) -> str:
    if device:
        return device

    script = """
try:
    from paddlex.utils.device import get_default_device
    print(get_default_device())
except Exception:
    raise SystemExit(1)
"""
    try:
        result = subprocess.run(
            [python_cmd, "-c", script],
            cwd=paddlex_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=20,
        )
    except Exception:
        return "cpu"
    if result.returncode != 0:
        return "cpu"
    resolved = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    return resolved or "cpu"


def _paddlex_model_registration_status(
    *,
    python_cmd: str,
    paddlex_root: Path,
    model_name: str,
) -> bool | None:
    script = """
import sys
try:
    import paddlex.repo_apis.PaddleDetection_api.object_det.register  # noqa: F401
    from paddlex.repo_apis.base.register import get_registered_model_info
    get_registered_model_info(sys.argv[1])
except KeyError:
    raise SystemExit(1)
except Exception:
    raise SystemExit(2)
raise SystemExit(0)
"""
    try:
        result = subprocess.run(
            [python_cmd, "-c", script, model_name],
            cwd=paddlex_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=20,
        )
    except Exception:
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _paddlex_basic_config(paddlex_root: Path, model_name: str) -> Path:
    return (
        paddlex_root
        / "paddlex"
        / "repo_apis"
        / "PaddleDetection_api"
        / "configs"
        / f"{model_name}.yaml"
    )


def _compatibility_overrides(
    *,
    python_cmd: str,
    paddlex_root: Path,
    model_name: str,
) -> list[str]:
    registration_status = _paddlex_model_registration_status(
        python_cmd=python_cmd,
        paddlex_root=paddlex_root,
        model_name=model_name,
    )
    if registration_status is True:
        return []
    if registration_status is None:
        return []

    if model_name not in ("PP-DocLayout-L", "PP-DocLayout_plus-L"):
        raise typer.BadParameter(
            f"PaddleX当前环境未注册模型:{model_name}；"
            "请升级PaddleX或通过--paddlex-model指定已注册模型"
        )

    basic_config = _paddlex_basic_config(paddlex_root, model_name)
    if not basic_config.is_file():
        raise typer.BadParameter(
            f"PaddleX当前环境未注册模型:{model_name}，且找不到底层检测配置:{basic_config}；"
            "请升级PaddleX到包含该PP-DocLayout配置的版本"
        )

    proxy_candidates = ["PP-DocLayout-L", "RT-DETR-L"]
    for proxy_model in proxy_candidates:
        if proxy_model == model_name:
            continue
        if _paddlex_model_registration_status(
            python_cmd=python_cmd,
            paddlex_root=paddlex_root,
            model_name=proxy_model,
        ) is True:
            console.print(
                f"PaddleX未注册{model_name}，使用{proxy_model}作为训练入口，"
                f"并加载{basic_config.name}"
            )
            return [
                f"Global.model={proxy_model}",
                f"Train.basic_config_path={_config_arg(basic_config, paddlex_root)}",
            ]

    raise typer.BadParameter(
        f"PaddleX当前环境未注册模型:{model_name}，且找不到可用的PP-DocLayout-L/RT-DETR-L代理模型；"
        "请升级PaddleX"
    )


def _paddlex_command(
    *,
    python_cmd: str,
    paddlex_root: Path,
    config: Path,
    mode: Literal["check_dataset", "train", "evaluate"],
    dataset_dir: Path,
    output_dir: Path | None,
    device: str | None,
    epochs: int | None,
    num_classes: int | None,
    dy2st: bool,
    overrides: Sequence[str] | None,
) -> list[str]:
    cmd = [
        python_cmd,
        "main.py",
        "-c",
        _config_arg(config, paddlex_root),
        "-o",
        f"Global.mode={mode}",
        "-o",
        f"Global.dataset_dir={dataset_dir.resolve()}",
    ]
    if output_dir is not None:
        cmd.extend(["-o", f"Global.output={output_dir.resolve()}"])
    if device:
        cmd.extend(["-o", f"Global.device={device}"])
    if epochs is not None and mode == "train":
        cmd.extend(["-o", f"Train.epochs_iters={epochs}"])
    if num_classes is not None and mode == "train":
        cmd.extend(["-o", f"Train.num_classes={num_classes}"])
    if dy2st and mode == "train":
        cmd.extend(["-o", "Train.dy2st=True"])
    for override in overrides or []:
        cmd.extend(["-o", override])
    return cmd


def _run_subprocess(cmd: Sequence[str], *, cwd: Path, dry_run: bool) -> None:
    console.print(" ".join(str(part) for part in cmd))
    if dry_run:
        return
    subprocess.check_call(list(cmd), cwd=cwd)


def _run_paddlex(
    version: LayoutVersion,
    paths: dict[str, Path],
    *,
    python: Path | None,
    paddlex_root: Path | None,
    config: Path | None,
    paddlex_model: str | None,
    output_dir: Path | None,
    device: str | None,
    epochs: int | None,
    num_classes: int | None,
    dy2st: bool,
    overrides: Sequence[str] | None,
    dry_run: bool,
    check_dataset: bool,
    train: bool,
) -> None:
    root = _find_paddlex_root(paddlex_root)
    python_cmd = _resolve_python(python, root)
    model_name = paddlex_model or DEFAULT_PADDLEX_MODELS[version]
    config_path = _resolve_config(root, config=config, model_name=model_name)
    output_path = output_dir or paths["root"] / "output" / f"layout-{version}"
    resolved_device = _resolve_paddlex_device(
        device=device,
        python_cmd=python_cmd,
        paddlex_root=root,
    )
    compatibility_overrides = _compatibility_overrides(
        python_cmd=python_cmd,
        paddlex_root=root,
        model_name=model_name,
    )
    effective_overrides = [*compatibility_overrides, *(overrides or [])]

    if _is_predict_only_layout_config(config_path, model_name):
        raise typer.BadParameter(
            "PP-DocLayoutV2 仅支持predict，不支持check_dataset/train；"
            "请改用PP-DocLayout-L或其它可训练的layout_detection配置"
        )

    if check_dataset:
        cmd = _paddlex_command(
            python_cmd=python_cmd,
            paddlex_root=root,
            config=config_path,
            mode="check_dataset",
            dataset_dir=paths["root"],
            output_dir=output_path,
            device=resolved_device,
            epochs=None,
            num_classes=None,
            dy2st=False,
            overrides=effective_overrides,
        )
        _run_subprocess(cmd, cwd=root, dry_run=dry_run)

    if train:
        cmd = _paddlex_command(
            python_cmd=python_cmd,
            paddlex_root=root,
            config=config_path,
            mode="train",
            dataset_dir=paths["root"],
            output_dir=output_path,
            device=resolved_device,
            epochs=epochs,
            num_classes=num_classes,
            dy2st=dy2st,
            overrides=effective_overrides,
        )
        _run_subprocess(cmd, cwd=root, dry_run=dry_run)


def _run_layout(
    version: LayoutVersion,
    *,
    dir_: Path,
    init: bool,
    prelabel: bool,
    prepare: bool,
    check: bool,
    train: bool,
    images_name: str,
    labelme_name: str,
    annotations_name: str,
    previews_name: str,
    labels_name: str,
    model_path: Path | None,
    engine: LayoutEngine,
    score_threshold: float,
    cuda: bool,
    dml: bool,
    cann: bool,
    overwrite: bool,
    preview: bool,
    val_ratio: float,
    seed: int,
    append_labels: bool,
    python: Path | None,
    paddlex_root: Path | None,
    config: Path | None,
    paddlex_model: str | None,
    output_dir: Path | None,
    device: str | None,
    epochs: int | None,
    dy2st: bool,
    overrides: Sequence[str] | None,
    skip_check: bool,
    dry_run: bool,
) -> None:
    paths = _dataset_paths(
        dir_,
        images_name=images_name,
        labelme_name=labelme_name,
        annotations_name=annotations_name,
        previews_name=previews_name,
        labels_name=labels_name,
    )
    has_action = init or prelabel or prepare or check or train

    if init:
        _init_dataset(paths, version=version)

    if prelabel:
        stats = _prelabel_dataset(
            version,
            paths,
            model_path=model_path,
            engine=engine,
            score_threshold=score_threshold,
            use_cuda=cuda,
            use_dml=dml,
            use_cann=cann,
            overwrite=overwrite,
            preview=preview,
        )
        console.print(stats)
        _print_labelme_commands(paths)

    if prepare or check or train:
        stats = _convert_labelme_to_coco(
            paths,
            version=version,
            val_ratio=val_ratio,
            seed=seed,
            append_labels=append_labels,
        )
        console.print(stats)
        if check or train:
            _require_train_annotations(stats)

    if check or train:
        _run_paddlex(
            version,
            paths,
            python=python,
            paddlex_root=paddlex_root,
            config=config,
            paddlex_model=paddlex_model,
            output_dir=output_dir,
            device=device,
            epochs=epochs,
            dy2st=dy2st,
            overrides=overrides,
            dry_run=dry_run,
            check_dataset=check or (train and not skip_check),
            train=train,
            num_classes=stats["categories"] if (prepare or check or train) else None,
        )

    if has_action:
        return

    _init_dataset(paths, version=version)
    if not _labelme_files(paths["labelme"]):
        stats = _prelabel_dataset(
            version,
            paths,
            model_path=model_path,
            engine=engine,
            score_threshold=score_threshold,
            use_cuda=cuda,
            use_dml=dml,
            use_cann=cann,
            overwrite=overwrite,
            preview=preview,
        )
        console.print(stats)
        console.print("预标注已完成，请用LabelMe调整后再执行同一命令开始训练。")
        _print_labelme_commands(paths)
        return

    stats = _convert_labelme_to_coco(
        paths,
        version=version,
        val_ratio=val_ratio,
        seed=seed,
        append_labels=append_labels,
    )
    console.print(stats)
    _require_train_annotations(stats)
    _run_paddlex(
        version,
        paths,
        python=python,
        paddlex_root=paddlex_root,
        config=config,
        paddlex_model=paddlex_model,
        output_dir=output_dir,
        device=device,
        epochs=epochs,
        num_classes=stats["categories"],
        dy2st=dy2st,
        overrides=overrides,
        dry_run=dry_run,
        check_dataset=not skip_check,
        train=True,
    )


def layout(
    version: Annotated[LayoutVersion, typer.Argument(help="预标注/训练模型版本，可选v2、v3、l或plus_l")],
    dir_: Annotated[Path, typer.Option("--dir","-d", help="数据集根目录")] = Path("."),
    init: Annotated[bool, typer.Option(help="只初始化目录和label.txt")] = False,
    prelabel: Annotated[bool, typer.Option(help="使用指定版本生成LabelMe预标注")] = False,
    prepare: Annotated[bool, typer.Option(help="只将LabelMe标注转换为COCO")] = False,
    check: Annotated[bool, typer.Option(help="转换COCO后执行PaddleX数据校验")] = False,
    train: Annotated[bool, typer.Option(help="转换COCO后执行PaddleX训练")] = False,
    images_name: Annotated[str, typer.Option("--images", help="图片目录名")] = "images",
    labelme_name: Annotated[str, typer.Option("--labelme", help="LabelMe标注目录名")] = "labelme",
    annotations_name: Annotated[str, typer.Option("--annotations", help="COCO标注目录名")] = "annotations",
    previews_name: Annotated[str, typer.Option("--previews", help="预标注可视化目录名")] = "previews",
    labels_name: Annotated[str, typer.Option("--labels", help="类别文件名")] = "label.txt",
    model_path: Annotated[Path | None, typer.Option(help="预标注ONNX模型路径")] = None,
    engine: Annotated[LayoutEngine, typer.Option(help="预标注推理后端")] = "auto",
    score_threshold: Annotated[float, typer.Option(help="预标注置信度阈值")] = 0.5,
    cuda: Annotated[bool, typer.Option(help="预标注使用ONNXRuntime CUDA")] = False,
    dml: Annotated[bool, typer.Option(help="预标注使用ONNXRuntime DirectML")] = False,
    cann: Annotated[bool, typer.Option(help="预标注使用ONNXRuntime CANN")] = False,
    overwrite: Annotated[bool, typer.Option(help="覆盖已存在的LabelMe JSON")] = False,
    preview: Annotated[bool, typer.Option(help="保存预标注可视化图片")] = True,
    val_ratio: Annotated[float, typer.Option(help="验证集比例")] = 0.1,
    seed: Annotated[int, typer.Option(help="训练/验证划分随机种子")] = 2026,
    append_labels: Annotated[bool, typer.Option(help="转换时自动追加label.txt中没有的新标签")] = False,
    python: Annotated[Path | None, typer.Option(help="执行PaddleX main.py的Python解释器，默认使用PaddleX/.venv/bin/python3，否则使用当前sys.executable")] = None,
    paddlex_root: Annotated[Path | None, typer.Option(help="PaddleX源码目录，默认查找./PaddleX和../PaddleX")] = None,
    config: Annotated[Path | None, typer.Option(help="PaddleX训练配置，相对PaddleX根目录或绝对路径")] = None,
    paddlex_model: Annotated[str | None, typer.Option(help="PaddleX layout_detection配置模型名")] = None,
    output_dir: Annotated[Path | None, typer.Option("--output", help="训练输出目录")] = None,
    device: Annotated[str | None, typer.Option(help="PaddleX训练设备，如cpu或gpu:0")] = None,
    epochs: Annotated[int | None, typer.Option(help="训练轮数，写入Train.epochs_iters")] = None,
    dy2st: Annotated[bool, typer.Option(help="PaddleX训练时开启dy2st")] = False,
    overrides: Annotated[list[str] | None, typer.Option("--set", help="额外PaddleX -o参数，如Train.epochs_iters=10")] = None,
    skip_check: Annotated[bool, typer.Option(help="训练前跳过PaddleX数据校验")] = False,
    dry_run: Annotated[bool, typer.Option(help="只打印PaddleX命令，不执行")] = False,
) -> None:
    """基于PP-DocLayout预标注，并基于LabelMe标注训练版面检测模型。"""
    _run_layout(
        version,
        dir_=dir_,
        init=init,
        prelabel=prelabel,
        prepare=prepare,
        check=check,
        train=train,
        images_name=images_name,
        labelme_name=labelme_name,
        annotations_name=annotations_name,
        previews_name=previews_name,
        labels_name=labels_name,
        model_path=model_path,
        engine=engine,
        score_threshold=score_threshold,
        cuda=cuda,
        dml=dml,
        cann=cann,
        overwrite=overwrite,
        preview=preview,
        val_ratio=val_ratio,
        seed=seed,
        append_labels=append_labels,
        python=python,
        paddlex_root=paddlex_root,
        config=config,
        paddlex_model=paddlex_model,
        output_dir=output_dir,
        device=device,
        epochs=epochs,
        dy2st=dy2st,
        overrides=overrides,
        skip_check=skip_check,
        dry_run=dry_run,
    )
