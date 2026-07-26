from __future__ import annotations

from collections.abc import Mapping as MappingABC, Sequence as SequenceABC
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Final, Literal, Mapping, NotRequired, Sequence, TypedDict

import cv2
import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFont, ImageOps


PP_DOC_LAYOUT_V2_URL = (
    "https://www.modelscope.cn/models/RapidAI/RapidLayout/resolve/v1.2.0/"
    "onnx/pp_doc_layout/pp_doc_layoutv2.onnx"
)
PP_DOC_LAYOUT_V3_URL = (
    "https://www.modelscope.cn/models/RapidAI/RapidLayout/resolve/v1.2.0/"
    "onnx/pp_doc_layout/pp_doc_layoutv3.onnx"
)
PP_DOC_LAYOUT_L_MODEL_DIR = "PP-DocLayout-L"
PP_DOC_LAYOUT_PLUS_L_MODEL_DIR = "PP-DocLayout_plus-L"

PP_DOC_LAYOUT_LABELS = [
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

ImageInput = str | Path | bytes | Image.Image | np.ndarray
LayoutVersion = Literal["auto", "v2", "v3", "l", "plus_l"]
EngineType = Literal["onnxruntime", "openvino"]
LayoutShapeMode = Literal["rect", "quad", "poly", "auto"]


class LayoutObject(TypedDict):
    type: str
    score: float
    rect: NotRequired[list[float]]
    quad: NotRequired[list[list[float]]]
    polygon: NotRequired[list[list[float]]]


class LayoutResult(TypedDict):
    width: int
    height: int
    version: NotRequired[LayoutVersion]
    objects: list[LayoutObject]


@dataclass(frozen=True)
class _PreprocessedImage:
    original: np.ndarray
    image: np.ndarray
    im_shape: np.ndarray
    scale_factor: np.ndarray
    width: int
    height: int


@dataclass(frozen=True)
class _LayoutPreprocessConfig:
    input_size: tuple[int, int] = (800, 800)
    use_rgb: bool = False
    scale: float = 1.0 / 255.0
    mean: tuple[float, float, float] = (0.0, 0.0, 0.0)
    std: tuple[float, float, float] = (1.0, 1.0, 1.0)
    interpolation: int = cv2.INTER_CUBIC
    keep_ratio: bool = False


@dataclass(frozen=True)
class _ResolvedModel:
    model_path: Path
    config_path: Path | None
    config: Mapping[str, Any]


def _load_image_bgr(image: ImageInput) -> np.ndarray:
    if isinstance(image, (str, Path)):
        pil_image = Image.open(image)
        return _pil_to_bgr(pil_image)
    if isinstance(image, bytes):
        return _pil_to_bgr(Image.open(BytesIO(image)))
    if isinstance(image, Image.Image):
        return _pil_to_bgr(image)
    if isinstance(image, np.ndarray):
        return _ndarray_to_bgr(image)
    raise TypeError(f"unsupported image type: {type(image)!r}")


def _pil_to_bgr(image: Image.Image) -> np.ndarray:
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA"):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        image = background.convert("RGB")
    else:
        image = image.convert("RGB")
    rgb = np.asarray(image)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _ndarray_to_bgr(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.ndim != 3:
        raise ValueError(f"unsupported image ndim: {image.ndim}")

    channels = image.shape[2]
    if channels == 1:
        return cv2.cvtColor(image[:, :, 0], cv2.COLOR_GRAY2BGR)
    if channels == 3:
        return np.ascontiguousarray(image)
    if channels == 4:
        bgr = image[:, :, :3].astype(np.float32)
        alpha = image[:, :, 3:4].astype(np.float32) / 255.0
        blended = bgr * alpha + 255.0 * (1.0 - alpha)
        return np.ascontiguousarray(blended.astype(np.uint8))
    raise ValueError(f"unsupported image channels: {channels}")


def _find_first_file(directory: Path, *patterns: str) -> Path | None:
    for pattern in patterns:
        exact = directory / pattern
        if exact.is_file():
            return exact
        matches = sorted(directory.glob(pattern))
        if matches:
            return matches[0]
    return None


def _load_config(path: Path | None) -> Mapping[str, Any]:
    if path is None:
        return {}
    data = yaml.safe_load(path.read_text("utf-8")) or {}
    if not isinstance(data, MappingABC):
        return {}
    return data


def _resolve_model(model_path: str | Path) -> _ResolvedModel:
    path = Path(model_path)
    if path.is_dir():
        resolved_model = _find_first_file(path, "inference.onnx", "*.onnx")
        if resolved_model is None:
            raise FileNotFoundError(f"model directory does not contain an ONNX file: {path}")
        config_path = _find_first_file(path, "inference.yml", "inference.yaml", "*.yml", "*.yaml")
        return _ResolvedModel(
            model_path=resolved_model,
            config_path=config_path,
            config=_load_config(config_path),
        )
    if not path.is_file():
        raise FileNotFoundError(f"model not found: {path}")

    config_path = _find_first_file(
        path.parent,
        f"{path.stem}.yml",
        f"{path.stem}.yaml",
        "inference.yml",
        "inference.yaml",
        "*.yml",
        "*.yaml",
    )
    return _ResolvedModel(
        model_path=path,
        config_path=config_path,
        config=_load_config(config_path),
    )


def _version_from_name(name: str | None) -> LayoutVersion | None:
    if not name:
        return None
    value = name.lower().replace("_", "-")
    if value in (
        "v2",
        "pp-doclayoutv2",
        "pp-doclayout-v2",
        "pp-doc-layout-v2",
        "pp-layoutv2",
        "pp-layout-v2",
    ):
        return "v2"
    if value in (
        "v3",
        "pp-doclayoutv3",
        "pp-doclayout-v3",
        "pp-doc-layout-v3",
        "pp-layoutv3",
        "pp-layout-v3",
    ):
        return "v3"
    if value in ("l", "pp-doclayout-l", "pp-doc-layout-l"):
        return "l"
    if value in (
        "plus-l",
        "pp-doclayout-plus-l",
        "pp-doc-layout-plus-l",
    ):
        return "plus_l"
    return None


def _version_from_config(config: Mapping[str, Any]) -> LayoutVersion | None:
    global_cfg = config.get("Global")
    if isinstance(global_cfg, MappingABC):
        version = _version_from_name(str(global_cfg.get("model_name", "")))
        if version is not None:
            return version
    return _version_from_name(str(config.get("model_name", "")))


def _version_from_path(path: Path) -> LayoutVersion | None:
    for part in (path.name, path.stem, path.parent.name):
        version = _version_from_name(part)
        if version is not None:
            return version
    return None


def _normalize_tuple3(values: Any, default: tuple[float, float, float]) -> tuple[float, float, float]:
    if isinstance(values, SequenceABC) and not isinstance(values, (str, bytes)) and len(values) == 3:
        return (float(values[0]), float(values[1]), float(values[2]))
    return default


def _preprocess_config_from_model(
    config: Mapping[str, Any],
    version: LayoutVersion,
) -> _LayoutPreprocessConfig:
    input_size = (640, 640) if version == "l" else (800, 800)
    use_rgb = version in ("l", "plus_l")
    scale = 1.0 / 255.0
    mean = (0.0, 0.0, 0.0)
    std = (1.0, 1.0, 1.0)
    interpolation = cv2.INTER_CUBIC
    keep_ratio = False

    if not config:
        return _LayoutPreprocessConfig(input_size=input_size, use_rgb=use_rgb)

    preprocess = config.get("Preprocess")
    if isinstance(preprocess, SequenceABC) and not isinstance(preprocess, (str, bytes)):
        for op in preprocess:
            if not isinstance(op, MappingABC):
                continue
            op_type = str(op.get("type", ""))
            if op_type == "Resize":
                target_size = op.get("target_size")
                if (
                    isinstance(target_size, SequenceABC)
                    and not isinstance(target_size, (str, bytes))
                    and len(target_size) == 2
                ):
                    input_size = (int(target_size[0]), int(target_size[1]))
                keep_ratio = bool(op.get("keep_ratio", False))
                interp = op.get("interp", 2)
                if isinstance(interp, int):
                    interpolation = {
                        0: cv2.INTER_NEAREST,
                        1: cv2.INTER_LINEAR,
                        2: cv2.INTER_CUBIC,
                        3: cv2.INTER_AREA,
                        4: cv2.INTER_LANCZOS4,
                    }.get(interp, cv2.INTER_CUBIC)
            elif op_type == "NormalizeImage":
                is_scale = bool(op.get("is_scale", True))
                scale = 1.0 / 255.0 if is_scale else 1.0
                norm_type = op.get("norm_type")
                if norm_type and norm_type != "none" and norm_type != "mean_std":
                    mean = (0.0, 0.0, 0.0)
                    std = (1.0, 1.0, 1.0)
                else:
                    mean = _normalize_tuple3(op.get("mean"), mean)
                    std = _normalize_tuple3(op.get("std"), std)

    return _LayoutPreprocessConfig(
        input_size=input_size,
        use_rgb=use_rgb,
        scale=scale,
        mean=mean,
        std=std,
        interpolation=interpolation,
        keep_ratio=keep_ratio,
    )


def _preprocess(image: ImageInput, config: _LayoutPreprocessConfig) -> _PreprocessedImage:
    bgr = _load_image_bgr(image)
    height, width = bgr.shape[:2]
    input_h, input_w = config.input_size

    source = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB) if config.use_rgb else bgr

    if config.keep_ratio:
        img_size = (width, height)
        target_size = (input_w, input_h)
        scale_ratio = min(
            max(target_size) / max(img_size),
            min(target_size) / min(img_size),
        )
        resized_w = max(1, int(round(width * scale_ratio)))
        resized_h = max(1, int(round(height * scale_ratio)))
        resized = cv2.resize(source, (resized_w, resized_h), interpolation=config.interpolation)
        input_h, input_w = resized_h, resized_w
    else:
        resized = cv2.resize(source, (input_w, input_h), interpolation=config.interpolation)

    tensor = resized.astype(np.float32) * config.scale
    mean = np.asarray(config.mean, dtype=np.float32).reshape(1, 1, 3)
    std = np.asarray(config.std, dtype=np.float32).reshape(1, 1, 3)
    tensor = ((tensor - mean) / std).transpose(2, 0, 1)[None, ...]
    im_shape = np.array([[input_h, input_w]], dtype=np.float32)
    scale_factor = np.array(
        [[input_h / float(height), input_w / float(width)]],
        dtype=np.float32,
    )
    return _PreprocessedImage(
        original=bgr,
        image=np.ascontiguousarray(tensor),
        im_shape=im_shape,
        scale_factor=scale_factor,
        width=width,
        height=height,
    )


def _build_input_feed(input_names: Sequence[str], data: _PreprocessedImage) -> dict[str, np.ndarray]:
    exact = {
        "image": data.image,
        "im_shape": data.im_shape,
        "scale_factor": data.scale_factor,
    }
    feed: dict[str, np.ndarray] = {}
    image_used = False
    for name in input_names:
        key = name.lower()
        if key in exact:
            feed[name] = exact[key]
        elif "shape" in key or "size" in key:
            feed[name] = data.im_shape
        elif "scale" in key:
            feed[name] = data.scale_factor
        elif "image" in key or "img" in key or not image_used:
            feed[name] = data.image
            image_used = True
        else:
            raise ValueError(f"cannot bind model input: {name}")
    return feed


def _labels_from_metadata(metadata: dict[str, str] | None) -> list[str] | None:
    if not metadata:
        return None
    labels = metadata.get("character")
    if not labels:
        return None
    parsed = [line.strip() for line in labels.splitlines() if line.strip()]
    return parsed or None


def _normalize_version(version: str) -> LayoutVersion:
    value = version.lower().replace("_", "-")
    if value == "auto":
        return "auto"
    normalized = _version_from_name(value)
    if normalized is not None:
        return normalized
    if value in ("v2", "v3"):
        return value  # type: ignore[return-value]
    raise ValueError(f"unsupported layout version: {version}")


def _normalize_engine(engine: str) -> EngineType:
    value = engine.lower()
    if value in ("onnxruntime", "openvino"):
        return value  # type: ignore[return-value]
    raise ValueError(f"unsupported layout engine: {engine}")


def _validate_accelerator_flags(
    *,
    use_cuda: bool,
    use_dml: bool,
    use_cann: bool,
) -> None:
    if sum((use_cuda, use_dml, use_cann)) > 1:
        raise ValueError("only one of use_cuda, use_dml, use_cann can be True")


def _onnxruntime_providers(
    *,
    use_cuda: bool,
    use_cpu: bool,
    use_dml: bool,
    use_cann: bool,
    providers: Sequence[str] | None,
) -> list[str]:
    if providers is not None:
        return list(providers)

    _validate_accelerator_flags(use_cuda=use_cuda, use_dml=use_dml, use_cann=use_cann)
    resolved: list[str] = []
    if use_cuda:
        resolved.append("CUDAExecutionProvider")
    elif use_dml:
        resolved.append("DmlExecutionProvider")
    elif use_cann:
        resolved.append("CANNExecutionProvider")

    if not use_cpu and not resolved:
        raise ValueError("at least one execution provider must be enabled")
    if use_cpu or not resolved:
        resolved.append("CPUExecutionProvider")
    return resolved


def _detect_version(outputs: Sequence[np.ndarray]) -> LayoutVersion:
    boxes = _pick_boxes_output(outputs)
    if boxes is None:
        return "auto"
    if boxes.shape[-1] == 8:
        return "v2"
    if boxes.shape[-1] == 7:
        return "v3"
    if boxes.shape[-1] == 6:
        return "auto"
    return "auto"


def _pick_boxes_output(outputs: Sequence[np.ndarray]) -> np.ndarray | None:
    for output in outputs:
        array = np.asarray(output)
        if array.ndim == 2 and array.shape[-1] >= 6 and np.issubdtype(array.dtype, np.floating):
            return array
    return None


def _pick_boxes_num_output(outputs: Sequence[np.ndarray]) -> int | None:
    for output in outputs:
        array = np.asarray(output)
        if array.ndim == 1 and array.size and np.issubdtype(array.dtype, np.integer):
            return int(array.reshape(-1)[0])
    return None


def _pick_masks_output(outputs: Sequence[np.ndarray]) -> np.ndarray | None:
    for output in outputs:
        array = np.asarray(output)
        if array.ndim == 3 and array.shape[-1] > 1 and array.shape[-2] > 1:
            return array
    return None


def _iou(box1: np.ndarray, box2: np.ndarray) -> float:
    x0 = max(float(box1[0]), float(box2[0]))
    y0 = max(float(box1[1]), float(box2[1]))
    x1 = min(float(box1[2]), float(box2[2]))
    y1 = min(float(box1[3]), float(box2[3]))
    inter_w = max(0.0, x1 - x0)
    inter_h = max(0.0, y1 - y0)
    inter = inter_w * inter_h
    area1 = max(0.0, float(box1[2] - box1[0])) * max(0.0, float(box1[3] - box1[1]))
    area2 = max(0.0, float(box2[2] - box2[0])) * max(0.0, float(box2[3] - box2[1]))
    denom = area1 + area2 - inter
    return inter / denom if denom > 0 else 0.0


def _overlap_ratio(box1: Sequence[float], box2: Sequence[float], mode: str = "union") -> float:
    x0 = max(float(box1[0]), float(box2[0]))
    y0 = max(float(box1[1]), float(box2[1]))
    x1 = min(float(box1[2]), float(box2[2]))
    y1 = min(float(box1[3]), float(box2[3]))
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area1 = max(0.0, float(box1[2]) - float(box1[0])) * max(0.0, float(box1[3]) - float(box1[1]))
    area2 = max(0.0, float(box2[2]) - float(box2[0])) * max(0.0, float(box2[3]) - float(box2[1]))
    if mode == "small":
        denom = min(area1, area2)
    elif mode == "large":
        denom = max(area1, area2)
    else:
        denom = area1 + area2 - inter
    return inter / denom if denom > 0 else 0.0


def _nms_indices(boxes: np.ndarray, iou_same: float = 0.6, iou_diff: float = 0.98) -> list[int]:
    if boxes.size == 0:
        return []

    order = np.argsort(boxes[:, 1])[::-1].tolist()
    keep: list[int] = []
    while order:
        current = order.pop(0)
        keep.append(current)
        remaining: list[int] = []
        for idx in order:
            threshold = iou_same if int(boxes[current, 0]) == int(boxes[idx, 0]) else iou_diff
            if _iou(boxes[current, 2:6], boxes[idx, 2:6]) < threshold:
                remaining.append(idx)
        order = remaining
    return keep


def _order_indices(boxes: np.ndarray) -> np.ndarray:
    if boxes.size == 0:
        return np.array([], dtype=np.int64)
    if boxes.shape[1] == 8:
        return np.lexsort((-boxes[:, 7], boxes[:, 6]))
    if boxes.shape[1] == 7:
        return np.argsort(boxes[:, 6])
    return np.arange(len(boxes))


def _rect_quad(x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    return np.array(
        [
            [x0, y0],
            [x1, y0],
            [x1, y1],
            [x0, y1],
        ],
        dtype=np.float32,
    )


def _order_quad_points(points: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    sums = points[:, 0] + points[:, 1]
    diffs = points[:, 0] - points[:, 1]
    ordered = np.empty((4, 2), dtype=np.float32)
    ordered[0] = points[np.argmin(sums)]
    ordered[1] = points[np.argmax(diffs)]
    ordered[2] = points[np.argmax(sums)]
    ordered[3] = points[np.argmin(diffs)]
    return ordered


def _convert_polygon_to_quad(polygon: np.ndarray | None) -> np.ndarray | None:
    if polygon is None or len(polygon) < 3:
        return None
    points = np.asarray(polygon, dtype=np.float32).reshape(-1, 2)
    rect = cv2.minAreaRect(points)
    quad = cv2.boxPoints(rect)
    return _order_quad_points(quad)


def _mask_to_shape(
    mask: np.ndarray,
    box: np.ndarray,
    width: int,
    height: int,
    *,
    max_box_width: float,
    layout_shape_mode: LayoutShapeMode,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    x0, y0, x1, y1 = [float(v) for v in box]
    box_w = max(1, int(round(x1 - x0)))
    box_h = max(1, int(round(y1 - y0)))
    mask_h, mask_w = mask.shape[:2]

    scale_w = mask_w / float(width)
    scale_h = mask_h / float(height)
    mx0 = int(np.clip(round(x0 * scale_w), 0, mask_w))
    mx1 = int(np.clip(round(x1 * scale_w), 0, mask_w))
    my0 = int(np.clip(round(y0 * scale_h), 0, mask_h))
    my1 = int(np.clip(round(y1 * scale_h), 0, mask_h))
    if mx1 <= mx0 or my1 <= my0:
        return None, None

    rect_quad = _rect_quad(x0, y0, x1, y1)
    if layout_shape_mode == "rect":
        return rect_quad, None

    cropped = mask[my0:my1, mx0:mx1]
    if cropped.size == 0 or not np.any(cropped):
        return None, None

    resized = cv2.resize(
        (cropped > 0).astype(np.uint8),
        (box_w, box_h),
        interpolation=cv2.INTER_NEAREST,
    )
    contours, _ = cv2.findContours(resized, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, None

    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) <= 0:
        return None, None

    epsilon = 0.004 * cv2.arcLength(contour, True)
    polygon = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2).astype(np.float32)
    if len(polygon) < 4:
        return rect_quad, None
    polygon[:, 0] += x0
    polygon[:, 1] += y0

    quad = _convert_polygon_to_quad(polygon)
    if quad is None:
        return rect_quad, polygon
    if layout_shape_mode == "poly":
        return quad, polygon
    if layout_shape_mode == "quad":
        return quad, None

    # Match RapidLayout's auto mode: prefer rect if quad is almost the same,
    # otherwise use quad when it is a good approximation of the polygon.
    quad_box = [quad[:, 0].min(), quad[:, 1].min(), quad[:, 0].max(), quad[:, 1].max()]
    rect_box = [x0, y0, x1, y1]
    if _overlap_ratio(rect_box, quad_box, mode="union") >= 0.95:
        return rect_quad, None
    if box_w > max_box_width * 0.6:
        return rect_quad, polygon
    return quad, None


def _clip_quad(quad: np.ndarray, width: int, height: int) -> np.ndarray:
    clipped = np.asarray(quad, dtype=np.float32).copy()
    clipped[:, 0] = np.clip(clipped[:, 0], 0, width)
    clipped[:, 1] = np.clip(clipped[:, 1], 0, height)
    return clipped


def _clip_polygon(polygon: np.ndarray, width: int, height: int) -> np.ndarray:
    clipped = np.asarray(polygon, dtype=np.float32).reshape(-1, 2).copy()
    clipped[:, 0] = np.clip(clipped[:, 0], 0, width)
    clipped[:, 1] = np.clip(clipped[:, 1], 0, height)
    return clipped


def _quad_to_list(quad: np.ndarray) -> list[list[float]]:
    return [[round(float(x), 2), round(float(y), 2)] for x, y in quad]


def _polygon_to_list(polygon: np.ndarray) -> list[list[float]]:
    return [[round(float(x), 2), round(float(y), 2)] for x, y in polygon.reshape(-1, 2)]


def _rect_to_list(x0: float, y0: float, x1: float, y1: float) -> list[float]:
    return [round(float(x0), 2), round(float(y0), 2), round(float(x1), 2), round(float(y1), 2)]


def _object_points(obj: LayoutObject) -> np.ndarray:
    if "polygon" in obj:
        return np.asarray(obj["polygon"], dtype=np.float32).reshape(-1, 2)
    if "quad" in obj:
        return np.asarray(obj["quad"], dtype=np.float32).reshape(-1, 2)
    if "rect" in obj:
        x0, y0, x1, y1 = obj["rect"]
        return _rect_quad(x0, y0, x1, y1)
    raise ValueError("layout object must contain rect, quad, or polygon")


def _draw_debug_result(image_bgr: np.ndarray, result: LayoutResult) -> Image.Image:
    canvas = Image.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)).convert("RGB")
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

    for index, obj in enumerate(result["objects"]):
        color = palette[index % len(palette)]
        polygon = _object_points(obj)
        points = [(float(x), float(y)) for x, y in polygon]
        draw.polygon(points, fill=(*color, 30))
        draw.line(points + [points[0]], fill=(*color, 255), width=3)

        label = f"{obj['type']} {obj['score']:.2f}"
        x0 = max(0, int(np.min(polygon[:, 0])))
        y0 = max(0, int(np.min(polygon[:, 1])))
        text_bbox = draw.textbbox((x0, y0), label, font=font)
        pad = 3
        bg = (
            max(0, text_bbox[0] - pad),
            max(0, text_bbox[1] - pad),
            min(canvas.width, text_bbox[2] + pad),
            min(canvas.height, text_bbox[3] + pad),
        )
        draw.rectangle(bg, fill=(255, 255, 255, 220))
        draw.text((x0, y0), label, fill=(*color, 255), font=font)

    return canvas


def _show_debug_result(image_bgr: np.ndarray, result: LayoutResult) -> None:
    _draw_debug_result(image_bgr, result).show(title="layout result")


def _filter_large_image_boxes(
    boxes: np.ndarray,
    labels: Sequence[str],
    width: int,
    height: int,
) -> np.ndarray:
    if len(boxes) <= 1 or "image" not in labels:
        return np.arange(len(boxes))

    image_class = labels.index("image")
    page_area = float(width * height)
    area_threshold = 0.82 if width > height else 0.93
    keep: list[int] = []
    for index, box in enumerate(boxes):
        if int(box[0]) != image_class:
            keep.append(index)
            continue

        x0, y0, x1, y1 = box[2:6]
        x0 = max(0.0, min(float(width), float(x0)))
        x1 = max(0.0, min(float(width), float(x1)))
        y0 = max(0.0, min(float(height), float(y0)))
        y1 = max(0.0, min(float(height), float(y1)))
        area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        if area <= area_threshold * page_area:
            keep.append(index)
    return np.asarray(keep or list(range(len(boxes))), dtype=np.int64)


def _bbox_from_object(obj: LayoutObject) -> list[float]:
    if "rect" in obj:
        return [float(v) for v in obj["rect"]]
    points = _object_points(obj)
    return [
        float(points[:, 0].min()),
        float(points[:, 1].min()),
        float(points[:, 0].max()),
        float(points[:, 1].max()),
    ]


def _filter_overlap_objects(objects: list[LayoutObject]) -> list[LayoutObject]:
    filtered = [obj for obj in objects if obj["type"] != "reference"]
    dropped: set[int] = set()

    for i, obj_i in enumerate(filtered):
        if i in dropped:
            continue
        box_i = _bbox_from_object(obj_i)
        width_i = box_i[2] - box_i[0]
        height_i = box_i[3] - box_i[1]
        if width_i < 6 or height_i < 6:
            dropped.add(i)
            continue

        for j in range(i + 1, len(filtered)):
            if j in dropped:
                continue
            obj_j = filtered[j]
            box_j = _bbox_from_object(obj_j)
            overlap = _overlap_ratio(box_i, box_j, mode="small")

            if obj_i["type"] == "inline_formula" or obj_j["type"] == "inline_formula":
                if overlap > 0.5:
                    if obj_i["type"] == "inline_formula":
                        dropped.add(i)
                    if obj_j["type"] == "inline_formula":
                        dropped.add(j)
                    continue

            if overlap > 0.7:
                if (obj_i["type"] == "image" or obj_j["type"] == "image") and obj_i["type"] != obj_j["type"]:
                    continue
                area_i = max(0.0, width_i) * max(0.0, height_i)
                width_j = box_j[2] - box_j[0]
                height_j = box_j[3] - box_j[1]
                area_j = max(0.0, width_j) * max(0.0, height_j)
                if area_i >= area_j:
                    dropped.add(j)
                else:
                    dropped.add(i)
                    break

    return [obj for idx, obj in enumerate(filtered) if idx not in dropped]


class _PPDocLayoutBase:
    _INPUT_SIZE = (800, 800)

    def __init__(
        self,
        model_path: str | Path,
        *,
        version: LayoutVersion | str = "auto",
        score_threshold: float = 0.5,
        labels: Sequence[str] | None = None,
        layout_nms: bool = True,
        use_masks: bool = True,
        layout_shape_mode: LayoutShapeMode = "rect",
        filter_large_image: bool = True,
        filter_overlap_boxes: bool = True,
    ):
        resolved_model = _resolve_model(model_path)
        self.model_path = resolved_model.model_path
        self.config_path = resolved_model.config_path
        self.config = resolved_model.config

        requested_version = _normalize_version(version)
        config_version = _version_from_config(self.config)
        path_version = _version_from_path(self.model_path)
        self.version = requested_version
        if self.version == "auto":
            self.version = config_version or path_version or "auto"
        elif config_version is not None and config_version != self.version:
            raise ValueError(
                f"model config looks like {config_version}, but version={self.version}"
            )

        self.score_threshold = float(score_threshold)
        config_labels = self.config.get("label_list")
        self._labels_from_config = False
        if labels is not None:
            self.labels = list(labels)
        elif isinstance(config_labels, SequenceABC) and not isinstance(config_labels, (str, bytes)):
            self.labels = [str(label) for label in config_labels]
            self._labels_from_config = True
        else:
            self.labels = list(PP_DOC_LAYOUT_LABELS)
        self.preprocess_config = _preprocess_config_from_model(self.config, self.version)
        self.layout_nms = layout_nms
        self.use_masks = use_masks
        self.layout_shape_mode = layout_shape_mode
        self.filter_large_image = filter_large_image
        self.filter_overlap_boxes = filter_overlap_boxes

    def __call__(self, image: ImageInput, *, debug: bool = False) -> LayoutResult:
        return self.predict(image, debug=debug)

    def predict(self, image: ImageInput, *, debug: bool = False) -> LayoutResult:
        data = _preprocess(image, self.preprocess_config)
        feed = _build_input_feed(self.input_names, data)
        outputs = [np.asarray(output) for output in self._run(feed)]
        result = self._postprocess(outputs, data.width, data.height)
        result["version"] = self.version
        if debug:
            _show_debug_result(data.original, result)
        return result

    @property
    def input_names(self) -> list[str]:
        raise NotImplementedError

    @property
    def output_names(self) -> list[str]:
        raise NotImplementedError

    def _run(self, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        raise NotImplementedError

    def _set_labels_from_metadata(self, metadata: dict[str, str] | None, explicit_labels: bool) -> None:
        if explicit_labels or self._labels_from_config:
            return
        labels = _labels_from_metadata(metadata)
        if labels:
            self.labels = labels

    def _validate_version(self, outputs: Sequence[np.ndarray]) -> None:
        detected = _detect_version(outputs)
        if self.version == "auto":
            if detected == "auto":
                raise ValueError(
                    "cannot detect layout model version from output; pass "
                    "version='l' or version='plus_l', or keep inference.yml next to the model"
                )
            self.version = detected
            self.preprocess_config = _preprocess_config_from_model(self.config, self.version)
            return
        if detected != "auto" and detected != self.version:
            raise ValueError(
                f"model output looks like {detected}, but version={self.version}"
            )

    def _postprocess(self, outputs: Sequence[np.ndarray], width: int, height: int) -> LayoutResult:
        self._validate_version(outputs)
        boxes = _pick_boxes_output(outputs)
        if boxes is None:
            raise RuntimeError("layout model output does not contain a boxes tensor")

        boxes_num = _pick_boxes_num_output(outputs)
        if boxes_num is not None:
            boxes = boxes[: min(max(boxes_num, 0), len(boxes))]

        masks = _pick_masks_output(outputs) if self.use_masks and self.layout_shape_mode != "rect" else None
        if masks is not None:
            masks = masks[: len(boxes)]

        if boxes.size == 0:
            return {"width": width, "height": height, "objects": []}

        valid = (boxes[:, 1] > self.score_threshold) & (boxes[:, 0] > -1)
        boxes = boxes[valid]
        if masks is not None:
            masks = masks[valid]

        if boxes.size == 0:
            return {"width": width, "height": height, "objects": []}

        if self.layout_nms:
            indices = np.asarray(_nms_indices(boxes[:, :6]), dtype=np.int64)
            boxes = boxes[indices]
            if masks is not None:
                masks = masks[indices]

        if self.filter_large_image:
            indices = _filter_large_image_boxes(boxes, self.labels, width, height)
            boxes = boxes[indices]
            if masks is not None:
                masks = masks[indices]

        order = _order_indices(boxes)
        boxes = boxes[order]
        if masks is not None:
            masks = masks[order]

        max_box_width = float(np.max(boxes[:, 4] - boxes[:, 2])) if len(boxes) else 0.0
        objects: list[LayoutObject] = []
        for index, box in enumerate(boxes):
            cls_id = int(box[0])
            if cls_id < 0 or cls_id >= len(self.labels):
                label = str(cls_id)
            else:
                label = self.labels[cls_id]

            x0, y0, x1, y1 = [float(v) for v in box[2:6]]
            x0 = max(0.0, min(float(width), x0))
            x1 = max(0.0, min(float(width), x1))
            y0 = max(0.0, min(float(height), y0))
            y1 = max(0.0, min(float(height), y1))
            if x1 <= x0 or y1 <= y0:
                continue

            rect = _rect_to_list(x0, y0, x1, y1)
            quad = None
            polygon = None
            if self.layout_shape_mode != "rect" and masks is not None and index < len(masks):
                quad, polygon = _mask_to_shape(
                    masks[index],
                    np.asarray([x0, y0, x1, y1]),
                    width,
                    height,
                    max_box_width=max_box_width,
                    layout_shape_mode=self.layout_shape_mode,
                )
            obj: LayoutObject = {
                "type": label,
                "score": round(float(box[1]), 6),
                "rect": rect,
            }
            if self.layout_shape_mode != "rect":
                if quad is None:
                    quad = _rect_quad(x0, y0, x1, y1)
                obj["quad"] = _quad_to_list(_clip_quad(quad, width, height))
                if polygon is not None and self.layout_shape_mode in ("poly", "auto"):
                    obj["polygon"] = _polygon_to_list(_clip_polygon(polygon, width, height))
            objects.append(obj)

        if self.filter_overlap_boxes:
            objects = _filter_overlap_objects(objects)
        return {"width": width, "height": height, "objects": objects}


class PPDocLayoutONNXRuntime(_PPDocLayoutBase):
    def __init__(
        self,
        model_path: str | Path,
        *,
        version: LayoutVersion | str = "auto",
        score_threshold: float = 0.5,
        labels: Sequence[str] | None = None,
        layout_nms: bool = True,
        use_masks: bool = True,
        layout_shape_mode: LayoutShapeMode = "poly",
        filter_large_image: bool = True,
        filter_overlap_boxes: bool = True,
        providers: Sequence[str] | None = None,
    ):
        explicit_labels = labels is not None
        super().__init__(
            model_path,
            version=version,
            score_threshold=score_threshold,
            labels=labels,
            layout_nms=layout_nms,
            use_masks=use_masks,
            layout_shape_mode=layout_shape_mode,
            filter_large_image=filter_large_image,
            filter_overlap_boxes=filter_overlap_boxes,
        )

        import onnxruntime as ort

        session_options = ort.SessionOptions()
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(
            str(self.model_path),
            sess_options=session_options,
            providers=list(providers) if providers else ["CPUExecutionProvider"],
        )
        self._input_names = [item.name for item in self._session.get_inputs()]
        self._output_names = [item.name for item in self._session.get_outputs()]
        self._set_labels_from_metadata(
            self._session.get_modelmeta().custom_metadata_map,
            explicit_labels,
        )

    @property
    def input_names(self) -> list[str]:
        return self._input_names

    @property
    def output_names(self) -> list[str]:
        return self._output_names

    def _run(self, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        return self._session.run(self.output_names, feed)


class PPDocLayoutOpenVINO(_PPDocLayoutBase):
    def __init__(
        self,
        model_path: str | Path,
        *,
        version: LayoutVersion | str = "auto",
        score_threshold: float = 0.5,
        labels: Sequence[str] | None = None,
        layout_nms: bool = True,
        use_masks: bool = True,
        layout_shape_mode: LayoutShapeMode = "poly",
        filter_large_image: bool = True,
        filter_overlap_boxes: bool = True,
        device: str = "CPU",
        config: dict[str, Any] | None = None,
    ):
        explicit_labels = labels is not None
        super().__init__(
            model_path,
            version=version,
            score_threshold=score_threshold,
            labels=labels,
            layout_nms=layout_nms,
            use_masks=use_masks,
            layout_shape_mode=layout_shape_mode,
            filter_large_image=filter_large_image,
            filter_overlap_boxes=filter_overlap_boxes,
        )

        from openvino import Core

        self._core = Core()
        self._model = self._core.read_model(str(self.model_path))
        self._compiled_model = self._core.compile_model(
            self._model,
            device_name=device,
            config=config or {},
        )
        self._input_ports = list(self._compiled_model.inputs)
        self._output_ports = list(self._compiled_model.outputs)
        self._input_names = [port.get_any_name() for port in self._input_ports]
        self._output_names = [port.get_any_name() for port in self._output_ports]
        self._set_labels_from_metadata(self._read_metadata(), explicit_labels)

    @property
    def input_names(self) -> list[str]:
        return self._input_names

    @property
    def output_names(self) -> list[str]:
        return self._output_names

    def _run(self, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        results = self._compiled_model(feed)
        return [np.asarray(results[port]) for port in self._output_ports]

    def _read_metadata(self) -> dict[str, str] | None:
        try:
            rt_info = self._model.get_rt_info()
            framework = rt_info.get("framework")
            if not framework:
                return None
            character = framework.get("character")
            value = getattr(character, "value", None)
            if value:
                return {"character": value}
        except Exception:
            return None
        return None


class LayoutDetector:
    def __init__(
        self,
        model_path: str | Path,
        *,
        version: LayoutVersion | str = "auto",
        engine: EngineType | str = "onnxruntime",
        score_threshold: float = 0.5,
        labels: Sequence[str] | None = None,
        layout_nms: bool = True,
        use_masks: bool = True,
        layout_shape_mode: LayoutShapeMode = "rect",
        filter_large_image: bool = True,
        filter_overlap_boxes: bool = True,
        use_cuda: bool = False,
        use_cpu: bool = True,
        use_dml: bool = False,
        use_cann: bool = False,
        providers: Sequence[str] | None = None,
        openvino_device: str = "CPU",
        openvino_config: dict[str, Any] | None = None,
    ):
        resolved_engine = _normalize_engine(engine)
        _validate_accelerator_flags(
            use_cuda=use_cuda,
            use_dml=use_dml,
            use_cann=use_cann,
        )

        if resolved_engine == "openvino":
            if providers is not None:
                raise ValueError("providers is only supported with engine='onnxruntime'")
            if use_cuda or use_dml or use_cann:
                raise ValueError("use_cuda/use_dml/use_cann are only supported with engine='onnxruntime'")
            if not use_cpu and openvino_device.upper() == "CPU":
                raise ValueError("openvino CPU device requires use_cpu=True")
            self._model: _PPDocLayoutBase = PPDocLayoutOpenVINO(
                model_path,
                version=version,
                score_threshold=score_threshold,
                labels=labels,
                layout_nms=layout_nms,
                use_masks=use_masks,
                layout_shape_mode=layout_shape_mode,
                filter_large_image=filter_large_image,
                filter_overlap_boxes=filter_overlap_boxes,
                device=openvino_device,
                config=openvino_config,
            )
        else:
            self._model = PPDocLayoutONNXRuntime(
                model_path,
                version=version,
                score_threshold=score_threshold,
                labels=labels,
                layout_nms=layout_nms,
                use_masks=use_masks,
                layout_shape_mode=layout_shape_mode,
                filter_large_image=filter_large_image,
                filter_overlap_boxes=filter_overlap_boxes,
                providers=_onnxruntime_providers(
                    use_cuda=use_cuda,
                    use_cpu=use_cpu,
                    use_dml=use_dml,
                    use_cann=use_cann,
                    providers=providers,
                ),
            )

    def __call__(self, image: ImageInput, *, debug: bool = False) -> LayoutResult:
        return self.predict(image, debug=debug)

    def predict(self, image: ImageInput, *, debug: bool = False) -> LayoutResult:
        return self._model.predict(image, debug=debug)

    @property
    def backend(self) -> _PPDocLayoutBase:
        return self._model


_paddle_layout_v2:Final = {
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

_paddle_layout_v3:Final = _paddle_layout_v2

_paddle_layout_l: Final = {
    "paragraph_title": "title",
    "image": "figure",
    "text": "text",
    "number": "footer",
    "abstract": "text",
    "content": "toc",
    "figure_title": "figure",
    "formula": "formula",
    "table": "table",
    "table_title": "title",
    "reference": "text",
    "doc_title": "title",
    "footnote": "footnote",
    "header": "header",
    "algorithm": "code",
    "footer": "footer",
    "seal": "seal",
    "chart_title": "title",
    "chart": "chart",
    "formula_number": "text",
    "header_image": "figure",
    "footer_image": "figure",
    "aside_text": "other_text",
}
_paddle_layout_plus_l: Final = {
    "paragraph_title": "text",
    "image": "figure",
    "text": "text",
    "number": "footer",
    "abstract": "text",
    "content": "toc",
    "figure_title": "title",
    "formula": "formula",
    "table": "table",
    "reference": "text",
    "doc_title": "title",
    "footnote": "footnote",
    "header": "header",
    "algorithm": "code",
    "footer": "footer",
    "seal": "seal",
    "chart": "chart",
    "formula_number": "text",
    "aside_text": "other_text",
    "reference_content": "text",
}


def get_mapping(version: str) -> Mapping[str, str]:
    normalized = _normalize_version(version)
    if normalized == "v2":
        return _paddle_layout_v2
    elif normalized == "v3":
        return _paddle_layout_v3
    elif normalized == "l":
        return _paddle_layout_l
    elif normalized == "plus_l":
        return _paddle_layout_plus_l
    else:
        raise ValueError(f"不支持的版本:{version}")

__all__ = [
    "EngineType",
    "ImageInput",
    "LayoutDetector",
    "LayoutShapeMode",
    "LayoutObject",
    "LayoutResult",
    "PP_DOC_LAYOUT_LABELS",
    "PP_DOC_LAYOUT_V2_URL",
    "PP_DOC_LAYOUT_V3_URL",
    "PPDocLayoutONNXRuntime",
    "PPDocLayoutOpenVINO",
]
