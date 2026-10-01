from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

import numpy as np
from PIL import Image, ImageOps

from memect.base.bbox import BBox


ImageInput = str | Path | bytes | Image.Image | np.ndarray
EngineType = Literal["onnxruntime", "openvino"]
ResizeMode = Literal["letterbox", "stretch"]
ArrayFormat = Literal["rgb", "bgr"]


class YOLOLayoutObject(TypedDict):
    type: str
    bbox: list[float]
    score: float


class YOLOLayoutResult(TypedDict):
    width: int
    height: int
    objects: list[YOLOLayoutObject]


@dataclass(frozen=True)
class _PreprocessInfo:
    width: int
    height: int
    input_width: int
    input_height: int
    resize_mode: ResizeMode
    ratio: float
    pad_x: float
    pad_y: float


class YOLOLayoutDetector:
    """
    YOLOv8 ONNX layout detector.

    The return value is directly serializable and compatible with
    KPage.load_layout:

        {"width": 100, "height": 200,
         "objects": [{"type": "text", "bbox": [...], "score": 0.9}]}
    """

    def __init__(
        self,
        model_path: str | Path,
        *,
        labels: Sequence[str] | Mapping[int | str, str] | None = None,
        score_threshold: float = 0.25,
        iou_threshold: float = 0.45,
        input_size: tuple[int, int] | None = None,
        resize_mode: ResizeMode = "letterbox",
        image_format: ArrayFormat = "bgr",
        engine: EngineType = "openvino",
        use_cuda: bool = False,
        use_cann: bool = False,
        use_dml: bool = False,
        providers: Sequence[str] | None = None,
        agnostic_nms: bool = False,
    ) -> None:
        self.model_path = Path(model_path)
        self.score_threshold = float(score_threshold)
        self.iou_threshold = float(iou_threshold)
        self.resize_mode = _normalize_resize_mode(resize_mode)
        self.image_format = _normalize_image_format(image_format)
        self.engine = _normalize_engine(engine)
        self.agnostic_nms = bool(agnostic_nms)

        _validate_threshold("score_threshold", self.score_threshold)
        _validate_threshold("iou_threshold", self.iou_threshold)
        _validate_accelerator_flags(
            engine=self.engine,
            use_cuda=use_cuda,
            use_cann=use_cann,
            use_dml=use_dml,
        )

        if self.engine == "openvino":
            if providers is not None:
                raise ValueError("providers is only supported with engine='onnxruntime'")
            self._load_openvino()
        else:
            self._load_onnxruntime(
                providers=_onnxruntime_providers(
                    use_cuda=use_cuda,
                    use_cann=use_cann,
                    use_dml=use_dml,
                    providers=providers,
                )
            )

        metadata_input_size = _input_size_from_metadata(self._metadata)
        if input_size is not None:
            self.input_width, self.input_height = _normalize_input_size(input_size)
        elif metadata_input_size is not None:
            self.input_width, self.input_height = metadata_input_size
        else:
            self.input_width, self.input_height = _input_size_from_shape(self._input_shape)

        explicit_labels = _normalize_labels(labels)
        metadata_labels = _labels_from_metadata(self._metadata)
        self.labels = explicit_labels or metadata_labels or []

    def __call__(self, image: ImageInput) -> YOLOLayoutResult:
        return self.predict(image)

    def predict(self, image: ImageInput) -> YOLOLayoutResult:
        tensor, info = self.preprocess(image)
        outputs = self._run(tensor)
        return self.postprocess(outputs, info)

    def preprocess(self, image: ImageInput) -> tuple[np.ndarray, _PreprocessInfo]:
        rgb = _load_image_rgb(image, image_format=self.image_format)
        height, width = rgb.shape[:2]

        if self.resize_mode == "letterbox":
            processed, ratio, pad_x, pad_y = _letterbox(
                rgb,
                target_width=self.input_width,
                target_height=self.input_height,
            )
        else:
            processed = _resize_rgb(rgb, (self.input_width, self.input_height))
            ratio = 1.0
            pad_x = 0.0
            pad_y = 0.0

        tensor = processed.astype(np.float32) / 255.0
        tensor = np.transpose(tensor, (2, 0, 1))[None]
        tensor = np.ascontiguousarray(tensor, dtype=np.float32)

        return tensor, _PreprocessInfo(
            width=width,
            height=height,
            input_width=self.input_width,
            input_height=self.input_height,
            resize_mode=self.resize_mode,
            ratio=ratio,
            pad_x=pad_x,
            pad_y=pad_y,
        )

    def postprocess(
        self,
        outputs: Sequence[np.ndarray],
        info: _PreprocessInfo,
    ) -> YOLOLayoutResult:
        predictions = _yolov8_predictions(outputs)
        if predictions.size == 0:
            return {"width": info.width, "height": info.height, "objects": []}

        if predictions.shape[1] < 5:
            raise RuntimeError(
                "YOLOv8 output must contain box coordinates and class scores"
            )

        boxes_cxywh = predictions[:, :4].astype(np.float32, copy=False)
        scores_by_class = predictions[:, 4:].astype(np.float32, copy=False)
        class_ids = np.argmax(scores_by_class, axis=1)
        scores = scores_by_class[np.arange(len(scores_by_class)), class_ids]

        score_mask = scores >= self.score_threshold
        if not np.any(score_mask):
            return {"width": info.width, "height": info.height, "objects": []}

        boxes_xyxy = _cxywh_to_xyxy(boxes_cxywh[score_mask])
        scores = scores[score_mask]
        class_ids = class_ids[score_mask].astype(np.int64, copy=False)

        boxes_xyxy = _restore_boxes(boxes_xyxy, info)
        valid_mask = _clip_boxes_inplace(boxes_xyxy, info.width, info.height)
        if not np.any(valid_mask):
            return {"width": info.width, "height": info.height, "objects": []}

        boxes_xyxy = boxes_xyxy[valid_mask]
        scores = scores[valid_mask]
        class_ids = class_ids[valid_mask]

        indices = _nms_indices(
            boxes_xyxy,
            scores,
            class_ids,
            self.iou_threshold,
            agnostic=self.agnostic_nms,
        )
        objects = [
            self._object_from_detection(
                boxes_xyxy[index],
                float(scores[index]),
                int(class_ids[index]),
            )
            for index in indices
        ]
        objects.sort(key=lambda obj: (obj["bbox"][1], obj["bbox"][0]))

        return {"width": info.width, "height": info.height, "objects": objects}

    def _object_from_detection(
        self,
        box: np.ndarray,
        score: float,
        class_id: int,
    ) -> YOLOLayoutObject:
        bbox = BBox(float(box[0]), float(box[1]), float(box[2]), float(box[3]))
        return {
            "type": self._label_for_class(class_id),
            "bbox": _bbox_to_list(bbox),
            "score": round(score, 6),
        }

    def _label_for_class(self, class_id: int) -> str:
        if 0 <= class_id < len(self.labels):
            return self.labels[class_id]
        return str(class_id)

    def _load_onnxruntime(self, *, providers: Sequence[str]) -> None:
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError(
                "onnxruntime is required when engine='onnxruntime'"
            ) from exc

        self._session = ort.InferenceSession(
            str(self.model_path),
            providers=list(providers),
        )
        input_meta = self._session.get_inputs()[0]
        self._input_name = input_meta.name
        self._input_shape = list(input_meta.shape)
        self._output_names = [output.name for output in self._session.get_outputs()]
        self._metadata = cast(
            Mapping[str, str],
            self._session.get_modelmeta().custom_metadata_map,
        )

    def _load_openvino(self) -> None:
        try:
            import openvino as ov
        except ImportError as exc:
            raise RuntimeError("openvino is required when engine='openvino'") from exc

        core = ov.Core()
        model = core.read_model(str(self.model_path))
        self._metadata = _openvino_framework_metadata(model)
        self._compiled_model = core.compile_model(model, "CPU")
        self._ov_input = self._compiled_model.input(0)
        self._ov_outputs = list(self._compiled_model.outputs)
        self._input_name = _openvino_name(self._ov_input)
        self._input_shape = _openvino_shape(self._ov_input)

    def _run(self, tensor: np.ndarray) -> list[np.ndarray]:
        if self.engine == "openvino":
            result = self._compiled_model({self._ov_input: tensor})
            return [np.asarray(result[output]) for output in self._ov_outputs]
        return [
            np.asarray(output)
            for output in self._session.run(
                self._output_names,
                {self._input_name: tensor},
            )
        ]


def _normalize_engine(engine: str) -> EngineType:
    value = engine.lower()
    if value in ("onnxruntime", "openvino"):
        return cast(EngineType, value)
    raise ValueError(f"unsupported YOLO layout engine: {engine}")


def _normalize_resize_mode(mode: str) -> ResizeMode:
    value = mode.lower()
    if value in ("letterbox", "stretch"):
        return cast(ResizeMode, value)
    raise ValueError(f"unsupported YOLO resize_mode: {mode}")


def _normalize_image_format(image_format: str) -> ArrayFormat:
    value = image_format.lower()
    if value in ("rgb", "bgr"):
        return cast(ArrayFormat, value)
    raise ValueError(f"unsupported image_format: {image_format}")


def _validate_threshold(name: str, value: float) -> None:
    if value < 0.0 or value > 1.0:
        raise ValueError(f"{name} must be in [0, 1]")


def _validate_accelerator_flags(
    *,
    engine: EngineType,
    use_cuda: bool,
    use_cann: bool,
    use_dml: bool,
) -> None:
    if sum((use_cuda, use_cann, use_dml)) > 1:
        raise ValueError("only one of use_cuda, use_cann, use_dml can be True")
    if engine == "openvino" and (use_cuda or use_cann or use_dml):
        raise ValueError("openvino engine only supports CPU inference")


def _onnxruntime_providers(
    *,
    use_cuda: bool,
    use_cann: bool,
    use_dml: bool,
    providers: Sequence[str] | None,
) -> list[str]:
    if providers is not None:
        return list(providers)
    if use_cuda:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    if use_cann:
        return ["CANNExecutionProvider", "CPUExecutionProvider"]
    if use_dml:
        return ["DmlExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def _normalize_input_size(input_size: tuple[int, int]) -> tuple[int, int]:
    width, height = input_size
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        raise ValueError("input_size must contain positive width and height")
    return width, height


def _shape_dim(value: Any) -> int | None:
    if isinstance(value, int):
        return value if value > 0 else None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _input_size_from_shape(shape: Sequence[Any]) -> tuple[int, int]:
    if len(shape) >= 4:
        height = _shape_dim(shape[-2])
        width = _shape_dim(shape[-1])
        if width is not None and height is not None:
            return width, height
    return 640, 640


def _openvino_name(port: Any) -> str:
    try:
        return str(port.get_any_name())
    except Exception:
        return str(getattr(port, "any_name", "image"))


def _openvino_shape(port: Any) -> list[Any]:
    try:
        return list(port.shape)
    except Exception:
        try:
            return list(port.partial_shape)
        except Exception:
            return []


def _openvino_framework_metadata(model: Any) -> Mapping[str, str]:
    try:
        rt_info = model.get_rt_info()
    except Exception:
        return {}

    if "framework" not in rt_info:
        return {}

    framework = rt_info["framework"]
    result: dict[str, str] = {}
    for key in ("names", "imgsz"):
        try:
            value = framework[key]
        except Exception:
            continue
        result[key] = _openvino_rt_value(value)
    return result


def _openvino_rt_value(value: Any) -> str:
    try:
        return str(value.get())
    except Exception:
        return str(value)


def _normalize_labels(
    labels: Sequence[str] | Mapping[int | str, str] | None,
) -> list[str] | None:
    if labels is None:
        return None
    if isinstance(labels, Mapping):
        return [
            str(value)
            for _, value in sorted(labels.items(), key=lambda item: _label_key(item[0]))
        ]
    return [str(label) for label in labels]


def _label_key(key: int | str) -> tuple[int, str]:
    try:
        return int(key), ""
    except (TypeError, ValueError):
        return 10**9, str(key)


def _labels_from_metadata(metadata: Mapping[str, str] | None) -> list[str] | None:
    if not metadata:
        return None
    raw = metadata.get("names")
    if not raw:
        return None
    labels = _parse_names_value(raw)
    if labels:
        return labels
    return None


def _parse_names_value(raw: str) -> list[str] | None:
    try:
        parsed = ast.literal_eval(raw)
    except Exception:
        return None
    return _labels_from_parsed_value(parsed)


def _labels_from_parsed_value(value: Any) -> list[str] | None:
    if isinstance(value, Mapping):
        return [str(label) for label in value.values()]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(label) for label in value]
    return None


def _input_size_from_metadata(metadata: Mapping[str, str] | None) -> tuple[int, int] | None:
    if not metadata:
        return None
    raw = metadata.get("imgsz")
    if not raw:
        return None
    try:
        parsed = ast.literal_eval(raw)
    except Exception:
        return None
    return _input_size_from_parsed_value(parsed)


def _input_size_from_parsed_value(value: Any) -> tuple[int, int] | None:
    if isinstance(value, int):
        return _normalize_input_size((value, value))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        values = list(value)
        if len(values) == 1:
            return _normalize_input_size((int(values[0]), int(values[0])))
        if len(values) >= 2:
            return _normalize_input_size((int(values[1]), int(values[0])))
    return None


def _load_image_rgb(image: ImageInput, *, image_format: ArrayFormat) -> np.ndarray:
    if isinstance(image, (str, Path)):
        return _pil_to_rgb_array(Image.open(image))
    if isinstance(image, bytes):
        return _pil_to_rgb_array(Image.open(BytesIO(image)))
    if isinstance(image, Image.Image):
        return _pil_to_rgb_array(image)
    if isinstance(image, np.ndarray):
        return _ndarray_to_rgb(image, image_format=image_format)
    raise TypeError(f"unsupported image type: {type(image)!r}")


def _pil_to_rgb_array(image: Image.Image) -> np.ndarray:
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA"):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        image = background.convert("RGB")
    else:
        image = image.convert("RGB")
    return np.ascontiguousarray(np.asarray(image, dtype=np.uint8))


def _ndarray_to_rgb(image: np.ndarray, *, image_format: ArrayFormat) -> np.ndarray:
    if image.ndim == 2:
        array = np.repeat(image[:, :, None], 3, axis=2)
    elif image.ndim == 3 and image.shape[2] == 1:
        array = np.repeat(image[:, :, :1], 3, axis=2)
    elif image.ndim == 3 and image.shape[2] >= 3:
        if image_format == "bgr":
            array = image[:, :, [2, 1, 0]]
        else:
            array = image[:, :, :3]
    else:
        raise ValueError(f"unsupported image shape: {image.shape}")

    if array.dtype == np.uint8:
        return np.ascontiguousarray(array)
    if np.issubdtype(array.dtype, np.floating) and float(np.nanmax(array)) <= 1.0:
        array = array * 255.0
    array = np.nan_to_num(array, nan=0.0, posinf=255.0, neginf=0.0)
    return np.ascontiguousarray(np.clip(array, 0, 255).astype(np.uint8))


def _resize_rgb(image: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    pil_image = Image.fromarray(image, mode="RGB")
    resized = pil_image.resize(size, Image.Resampling.BILINEAR)
    return np.ascontiguousarray(np.asarray(resized, dtype=np.uint8))


def _letterbox(
    image: np.ndarray,
    *,
    target_width: int,
    target_height: int,
) -> tuple[np.ndarray, float, float, float]:
    height, width = image.shape[:2]
    ratio = min(target_width / width, target_height / height)
    new_width = max(1, int(round(width * ratio)))
    new_height = max(1, int(round(height * ratio)))

    resized = _resize_rgb(image, (new_width, new_height))
    pad_x = (target_width - new_width) / 2.0
    pad_y = (target_height - new_height) / 2.0
    left = int(round(pad_x - 0.1))
    top = int(round(pad_y - 0.1))

    output = np.full((target_height, target_width, 3), 114, dtype=np.uint8)
    output[top : top + new_height, left : left + new_width] = resized
    return output, ratio, pad_x, pad_y


def _yolov8_predictions(outputs: Sequence[np.ndarray]) -> np.ndarray:
    if not outputs:
        raise RuntimeError("YOLO model returned no outputs")

    output = np.asarray(outputs[0])
    if output.ndim == 3:
        if output.shape[0] != 1:
            output = output.reshape(-1, output.shape[-1])
        else:
            output = output[0]
    if output.ndim != 2:
        raise RuntimeError(f"unsupported YOLO output shape: {output.shape}")

    if (
        (output.shape[0] < output.shape[1] and output.shape[0] >= 5)
        or (output.shape[1] < 5 and output.shape[0] >= 5)
    ):
        output = output.T
    return np.ascontiguousarray(output, dtype=np.float32)


def _cxywh_to_xyxy(boxes: np.ndarray) -> np.ndarray:
    xyxy = np.empty_like(boxes, dtype=np.float32)
    xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2.0
    xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2.0
    xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2.0
    xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2.0
    return xyxy


def _restore_boxes(boxes: np.ndarray, info: _PreprocessInfo) -> np.ndarray:
    restored = boxes.astype(np.float32, copy=True)
    if info.resize_mode == "letterbox":
        restored[:, [0, 2]] = (restored[:, [0, 2]] - info.pad_x) / info.ratio
        restored[:, [1, 3]] = (restored[:, [1, 3]] - info.pad_y) / info.ratio
    else:
        scale_x = info.width / info.input_width
        scale_y = info.height / info.input_height
        restored[:, [0, 2]] *= scale_x
        restored[:, [1, 3]] *= scale_y
    return restored


def _clip_boxes_inplace(boxes: np.ndarray, width: int, height: int) -> np.ndarray:
    boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0.0, float(width))
    boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0.0, float(height))
    return (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])


def _nms_indices(
    boxes: np.ndarray,
    scores: np.ndarray,
    class_ids: np.ndarray,
    iou_threshold: float,
    *,
    agnostic: bool,
) -> list[int]:
    if boxes.size == 0:
        return []
    if agnostic:
        return _nms_one_class(boxes, scores, iou_threshold)

    keep: list[int] = []
    for class_id in np.unique(class_ids):
        class_indices = np.flatnonzero(class_ids == class_id)
        local_keep = _nms_one_class(
            boxes[class_indices],
            scores[class_indices],
            iou_threshold,
        )
        keep.extend(int(class_indices[index]) for index in local_keep)
    keep.sort(key=lambda index: float(scores[index]), reverse=True)
    return keep


def _nms_one_class(
    boxes: np.ndarray,
    scores: np.ndarray,
    iou_threshold: float,
) -> list[int]:
    order = np.argsort(scores)[::-1]
    keep: list[int] = []

    while order.size > 0:
        index = int(order[0])
        keep.append(index)
        if order.size == 1:
            break
        ious = _iou(boxes[index], boxes[order[1:]])
        order = order[1:][ious < iou_threshold]

    return keep


def _iou(box: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    x0 = np.maximum(box[0], boxes[:, 0])
    y0 = np.maximum(box[1], boxes[:, 1])
    x1 = np.minimum(box[2], boxes[:, 2])
    y1 = np.minimum(box[3], boxes[:, 3])

    inter = np.maximum(0.0, x1 - x0) * np.maximum(0.0, y1 - y0)
    area = max(0.0, float(box[2] - box[0])) * max(0.0, float(box[3] - box[1]))
    areas = np.maximum(0.0, boxes[:, 2] - boxes[:, 0]) * np.maximum(
        0.0,
        boxes[:, 3] - boxes[:, 1],
    )
    union = np.maximum(area + areas - inter, 1e-6)
    return inter / union


def _bbox_to_list(bbox: BBox) -> list[float]:
    return [
        round(float(bbox.x0), 2),
        round(float(bbox.y0), 2),
        round(float(bbox.x1), 2),
        round(float(bbox.y1), 2),
    ]


__all__ = [
    "ArrayFormat",
    "EngineType",
    "ImageInput",
    "ResizeMode",
    "YOLOLayoutDetector",
    "YOLOLayoutObject",
    "YOLOLayoutResult",
]
