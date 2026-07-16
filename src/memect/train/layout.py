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

LayoutVersion = Literal["v2", "v3"]
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

DEFAULT_LAYOUT_LABELS: Final = [
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
]

DEFAULT_PADDLEX_MODELS: Final[dict[LayoutVersion, str]] = {
    "v2": "PP-DocLayoutV2",
    "v3": "PP-DocLayout-L",
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


def _read_labels(path: Path) -> list[str]:
    if not path.is_file():
        return list(DEFAULT_LAYOUT_LABELS)
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


def _ensure_dataset_dirs(paths: dict[str, Path]) -> None:
    paths["root"].mkdir(parents=True, exist_ok=True)
    paths["images"].mkdir(parents=True, exist_ok=True)
    paths["labelme"].mkdir(parents=True, exist_ok=True)
    paths["annotations"].mkdir(parents=True, exist_ok=True)
    paths["previews"].mkdir(parents=True, exist_ok=True)
    if not paths["labels"].exists():
        _write_labels(paths["labels"], DEFAULT_LAYOUT_LABELS)


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


def _init_dataset(paths: dict[str, Path]) -> None:
    _ensure_dataset_dirs(paths)
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

    return Path(get_model_path(f"pp_layout{version}.onnx"))


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
    _ensure_dataset_dirs(paths)
    images = _image_files(paths["images"])
    if not images:
        raise typer.BadParameter(f"images目录下没有图片: {paths['images']}")

    labels = _read_labels(paths["labels"])
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
    val_ratio: float,
    seed: int,
    append_labels: bool,
) -> dict[str, Any]:
    labelme_files = _labelme_files(paths["labelme"])
    if not labelme_files:
        raise typer.BadParameter(f"没有找到LabelMe标注: {paths['labelme']}")

    labels = _read_labels(paths["labels"])
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

    if labels != _read_labels(paths["labels"]):
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

    if check_dataset:
        cmd = _paddlex_command(
            python_cmd=python_cmd,
            paddlex_root=root,
            config=config_path,
            mode="check_dataset",
            dataset_dir=paths["root"],
            output_dir=output_path,
            device=device,
            epochs=None,
            dy2st=False,
            overrides=overrides,
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
            device=device,
            epochs=epochs,
            dy2st=dy2st,
            overrides=overrides,
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
        _init_dataset(paths)

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
        )

    if has_action:
        return

    _init_dataset(paths)
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
        dy2st=dy2st,
        overrides=overrides,
        dry_run=dry_run,
        check_dataset=not skip_check,
        train=True,
    )


def layout(
    version: Annotated[LayoutVersion, typer.Argument(help="预标注模型版本，可选v2或v3")],
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
    """基于pp_layoutv2或pp_layoutv3预标注，并基于LabelMe标注训练版面检测模型。"""
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
