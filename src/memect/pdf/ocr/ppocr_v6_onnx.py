from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont
import yaml

from memect.base import images as image_utils
from memect.pdf.fonts import get_font_dir

from .ops import (
    build_ctc_decoder,
    build_db_postprocess,
    build_det_preprocess,
    build_rec_resize,
)
from .utils import get_rotate_crop_image, load_image_bgr, sorted_boxes

_logger = logging.getLogger(__name__)


def _default_providers() -> list[str]:
    try:
        import onnxruntime as ort

        providers = ort.get_available_providers()
    except Exception:
        return ["CPUExecutionProvider"]

    preferred = [
        "CUDAExecutionProvider",
        "TensorrtExecutionProvider",
        "DmlExecutionProvider",
        "CANNExecutionProvider",
        "CPUExecutionProvider",
    ]
    return [p for p in preferred if p in providers] or ["CPUExecutionProvider"]


def _load_yaml(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    return yaml.safe_load(Path(path).read_text("utf-8")) or {}


def _resolve_config_path(
    model_path: str | Path,
    config_path: str | Path | None,
) -> Path | None:
    if config_path is not None:
        return Path(config_path)
    model_dir = Path(model_path).parent
    return _find_first_file(model_dir, "inference.yml", "inference.yaml", "*.yml", "*.yaml")


def _get_opencv_or_numpy(image: Any) -> np.ndarray:
    return load_image_bgr(image)


def _get_debug_font(size: int) -> PIL.ImageFont.ImageFont:
    path = get_font_dir() / "sans-serif" / "SourceHanSans-Regular.ttc"
    try:
        return PIL.ImageFont.truetype(path, size=size, index=2)
    except Exception:
        return PIL.ImageFont.load_default()


def _to_pil_image(image: np.ndarray) -> PIL.Image.Image:
    return image_utils.cv2_to_pil(image)


def _draw_debug_overlay(
    image: np.ndarray,
    items: list[dict[str, Any]],
) -> PIL.Image.Image:
    canvas = _to_pil_image(image).convert("RGB")
    draw = PIL.ImageDraw.Draw(canvas)
    font = _get_debug_font(max(14, min(canvas.size) // 40))

    for item in items:
        box = np.asarray(item["box"], dtype=np.float32).reshape(4, 2)
        polygon = [tuple(point.tolist()) for point in box]
        draw.line(polygon + [polygon[0]], fill=(255, 64, 64), width=3)

        text = item.get("text")
        det_score = item.get("det_score")
        rec_score = item.get("rec_score")
        if text is None:
            label = f"{det_score:.3f}" if isinstance(det_score, float) else ""
        else:
            label = text
            if isinstance(rec_score, float):
                label = f"{label} {rec_score:.3f}"

        if not label:
            continue

        x = int(np.min(box[:, 0]))
        y = int(np.min(box[:, 1]))
        bbox = draw.textbbox((x, y), label, font=font)
        pad = 3
        tx0 = max(0, bbox[0] - pad)
        ty0 = max(0, bbox[1] - pad)
        tx1 = min(canvas.width, bbox[2] + pad)
        ty1 = min(canvas.height, bbox[3] + pad)
        draw.rectangle((tx0, ty0, tx1, ty1), fill=(255, 255, 204))
        draw.text((x, y), label, fill=(0, 0, 0), font=font)
    return canvas


def _show_debug_view(image: np.ndarray, items: list[dict[str, Any]]) -> None:
    original = _to_pil_image(image).convert("RGB")
    result = _draw_debug_overlay(image, items)
    merged = image_utils.hmerge(original, result, gap=8)
    merged.show(title="original | result")


def _wrap_debug_text(
    draw: PIL.ImageDraw.ImageDraw,
    text: str,
    font: PIL.ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    if not text:
        return [""]
    if max_width <= 0:
        return [text]

    lines: list[str] = []
    current = ""
    for ch in text:
        candidate = current + ch
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if current and (bbox[2] - bbox[0]) > max_width:
            lines.append(current)
            current = ch
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [text]


def _make_crop_debug_tile(
    crop: np.ndarray,
    index: int,
    text: str | None = None,
    score: float | None = None,
) -> PIL.Image.Image:
    crop_image = _to_pil_image(crop).convert("RGB")
    pad = 8
    font = _get_debug_font(max(14, min(max(crop_image.size), 24)))

    probe = PIL.Image.new("RGB", (1, 1), (255, 255, 255))
    probe_draw = PIL.ImageDraw.Draw(probe)

    lines = [f"#{index} {crop_image.width}x{crop_image.height}"]
    if text is not None:
        wrapped = _wrap_debug_text(probe_draw, text, font, max(crop_image.width, 120))
        lines.extend(wrapped)
    if score is not None:
        lines.append(f"score={score:.3f}")

    line_heights: list[int] = []
    max_text_width = 0
    for line in lines:
        bbox = probe_draw.textbbox((0, 0), line or " ", font=font)
        line_heights.append(bbox[3] - bbox[1])
        max_text_width = max(max_text_width, bbox[2] - bbox[0])

    text_height = sum(line_heights) + max(0, len(lines) - 1) * 4
    tile_width = max(crop_image.width, max_text_width) + pad * 2
    tile_height = crop_image.height + text_height + pad * 3

    tile = PIL.Image.new("RGB", (tile_width, tile_height), (255, 255, 255))
    tile.paste(crop_image, ((tile_width - crop_image.width) // 2, pad))

    draw = PIL.ImageDraw.Draw(tile)
    y = crop_image.height + pad * 2
    for line, line_height in zip(lines, line_heights, strict=False):
        draw.text((pad, y), line, fill=(0, 0, 0), font=font)
        y += line_height + 4
    return tile


def _show_crop_debug_view(
    crops: list[np.ndarray],
    rec_results: list[tuple[str, float]],
) -> None:
    if not crops:
        return

    tiles = []
    for index, crop in enumerate(crops):
        text = None
        score = None
        if index < len(rec_results):
            text, score = rec_results[index]
        tiles.append(_make_crop_debug_tile(crop, index, text=text, score=score))

    if len(tiles) <= 4:
        canvas = image_utils.hmerge(*tiles, gap=8)
    else:
        rows = []
        per_row = min(4, max(2, int(np.ceil(np.sqrt(len(tiles))))))
        for start in range(0, len(tiles), per_row):
            rows.append(image_utils.hmerge(*tiles[start : start + per_row], gap=8))

        total_width = max(row.width for row in rows)
        total_height = sum(row.height for row in rows) + 8 * (len(rows) - 1)
        canvas = PIL.Image.new("RGB", (total_width, total_height), (255, 255, 255))
        y = 0
        for row in rows:
            x = (total_width - row.width) // 2
            canvas.paste(row, (x, y))
            y += row.height + 8
    canvas.show(title="crops")


def _has_openvino() -> bool:
    try:
        import openvino  # type: ignore # noqa: F401
    except Exception:
        return False
    return True


def _validate_accelerator_flags(
    use_cuda: bool,
    use_cann: bool,
    use_dml: bool,
) -> None:
    enabled = sum((use_cuda, use_cann, use_dml))
    if enabled > 1:
        raise ValueError("only one of use_cuda, use_cann, use_dml can be True")


def _resolve_engine(
    engine: str | None,
    use_cuda: bool,
    use_cann: bool,
    use_dml: bool,
) -> str:
    _validate_accelerator_flags(use_cuda, use_cann, use_dml)
    if engine is not None:
        value = engine.lower()
        if value not in ("onnxruntime", "openvino"):
            raise ValueError(f"unsupported engine: {engine}")
        if value == "openvino" and (use_cuda or use_cann or use_dml):
            raise ValueError("openvino engine only supports CPU; do not set use_cuda/use_cann/use_dml")
        return value

    if use_cuda or use_cann or use_dml:
        return "onnxruntime"
    if _has_openvino():
        return "openvino"
    return "onnxruntime"


def _get_ort_providers(
    *,
    use_cuda: bool,
    use_cann: bool,
    use_dml: bool,
    providers: list[str] | None,
) -> list[Any]:
    if providers:
        return providers

    if use_cuda:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    if use_cann:
        return ["CANNExecutionProvider", "CPUExecutionProvider"]
    if use_dml:
        return ["DmlExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


class _InferenceSession:
    def __init__(
        self,
        model_path: str | Path,
        *,
        engine: str | None = None,
        use_cuda: bool = False,
        use_cann: bool = False,
        use_dml: bool = False,
        providers: list[str] | None = None,
    ):
        self.model_path = Path(model_path)
        if not self.model_path.exists():
            raise FileNotFoundError(f"model not found: {self.model_path}")
        self.engine = _resolve_engine(engine, use_cuda, use_cann, use_dml)
        self._session: Any = None
        self._input_name: str = ""
        self._output_names: list[str] = []
        if self.engine == "openvino":
            if providers:
                raise ValueError("providers is only supported when engine='onnxruntime'")
            self._init_openvino()
        else:
            self._init_onnxruntime(
                providers=_get_ort_providers(
                    use_cuda=use_cuda,
                    use_cann=use_cann,
                    use_dml=use_dml,
                    providers=providers,
                )
            )

    def _init_onnxruntime(self, *, providers: list[Any]) -> None:
        import onnxruntime as ort

        sess_opt = ort.SessionOptions()
        sess_opt.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(
            str(self.model_path),
            sess_options=sess_opt,
            providers=providers,
        )
        self._input_name = self._session.get_inputs()[0].name
        self._output_names = [item.name for item in self._session.get_outputs()]

    def _init_openvino(self) -> None:
        from openvino import Core

        core = Core()
        core.set_property(
            "CPU",
            {"INFERENCE_NUM_THREADS": 0, "PERFORMANCE_HINT": "LATENCY"},
        )
        model = core.read_model(str(self.model_path))
        self._session = core.compile_model(model, "CPU")
        self._input_name = list(self._session.inputs[0].names)[0]
        self._output_names = [list(output.names)[0] for output in self._session.outputs]

    @property
    def input_name(self) -> str:
        return self._input_name

    @property
    def output_names(self) -> list[str]:
        return self._output_names

    def run(self, input_array: np.ndarray) -> list[np.ndarray]:
        if self.engine == "openvino":
            result = self._session({self.input_name: input_array})
            return [result[name] for name in self.output_names]
        return self._session.run(None, {self.input_name: input_array})


def _pick_det_output(outputs: list[np.ndarray]) -> np.ndarray:
    for output in outputs:
        if output.ndim == 4:
            return output
    raise RuntimeError("det model output does not contain a 4D tensor")


def _pick_rec_output(outputs: list[np.ndarray]) -> np.ndarray:
    three_dim = [output for output in outputs if output.ndim == 3]
    if not three_dim:
        raise RuntimeError("rec model output does not contain a 3D tensor")
    return three_dim[-1]


def _find_first_file(directory: Path, *patterns: str) -> Path | None:
    for pattern in patterns:
        file = next(directory.glob(pattern), None)
        if file is not None:
            return file
    return None


class PPOCRv6Det:
    def __init__(
        self,
        model_path: str | Path,
        config_path: str | Path | None = None,
        override_config:Mapping[str,Any]|None=None,
        limit_type:str='min',
        limit_side_len:int=32,
        engine: str | None = None,
        use_cuda: bool = False,
        use_cann: bool = False,
        use_dml: bool = False,
        providers: list[str] | None = None,
    ):
        resolved_config = _resolve_config_path(model_path, config_path)
        self.config = _load_yaml(resolved_config)
        if not self.config:
            raise FileNotFoundError(
                f"det config not found for model {model_path}; pass det inference.yml explicitly"
            )
        if override_config:
            from memect.base.config import set_values
            set_values(self.config,override_config)
        
        if True:
            for op in self.config['PreProcess']['transform_ops']:
                if 'DetResizeForTest' in op:
                    value = op['DetResizeForTest']
                    if value:
                        value=dict(value)
                    else:
                        value={}
                    value['limit_type']=limit_type
                    value['limit_side_len']=limit_side_len
                    op['DetResizeForTest']=value

        self.session = _InferenceSession(
            model_path,
            engine=engine,
            use_cuda=use_cuda,
            use_cann=use_cann,
            use_dml=use_dml,
            providers=providers,
        )
        self.preprocess = build_det_preprocess(self.config)
        self.postprocess = build_db_postprocess(self.config)

    def predict(self, image: Any, *, debug: bool = False) -> list[dict[str, Any]]:
        img = _get_opencv_or_numpy(image)
        data = img
        shape_list = None
        for op in self.preprocess:
            if op.__class__.__name__ == "DetResizeForTest":
                data, shape_list = op(data)
            else:
                data = op(data)

        if shape_list is None:
            raise RuntimeError("det preprocess missing shape_list")

        tensor = data.astype(np.float32)[None, ...]
        preds = _pick_det_output(self.session.run(tensor))
        outputs = self.postprocess(preds, np.asarray([shape_list], dtype=np.float32))
        pairs = list(zip(outputs[0]["points"], outputs[0]["scores"], strict=False))
        ordered_boxes = sorted_boxes([pair[0] for pair in pairs])
        score_map = {id(box): score for box, score in pairs}

        result: list[dict[str, Any]] = []
        for box in ordered_boxes:
            result.append(
                {
                    "box": box,
                    "det_score": float(score_map[id(box)]),
                }
            )
        if debug:
            _show_debug_view(img, result)
        return result


class PPOCRv6Rec:
    def __init__(
        self,
        model_path: str | Path,
        config_path: str | Path | None = None,
        override_config:Mapping[str,Any]|None=None,
        engine: str | None = None,
        use_cuda: bool = False,
        use_cann: bool = False,
        use_dml: bool = False,
        providers: list[str] | None = None,
    ):
        resolved_config = _resolve_config_path(model_path, config_path)
        self.config = _load_yaml(resolved_config)
        if not self.config:
            raise FileNotFoundError(
                f"rec config not found for model {model_path}; pass rec inference.yml explicitly"
            )
        if override_config:
            from memect.base.config import set_values
            set_values(self.config,override_config)

        self.config_dir = resolved_config.parent if resolved_config is not None else Path(model_path).parent
        self.session = _InferenceSession(
            model_path,
            engine=engine,
            use_cuda=use_cuda,
            use_cann=use_cann,
            use_dml=use_dml,
            providers=providers,
        )
        self.resize = build_rec_resize(self.config)
        self.decoder = build_ctc_decoder(self.config, base_dir=self.config_dir)
        self.fixed_input_width = self._get_fixed_input_width()

    def _get_fixed_input_width(self) -> int | None:
        session = self.session._session
        try:
            if self.session.engine == "openvino":
                shape = list(session.inputs[0].partial_shape.to_shape())
            else:
                shape = list(session.get_inputs()[0].shape)
        except Exception:
            return None

        if len(shape) < 4:
            return None
        width = shape[3]
        return int(width) if isinstance(width, int) and width > 0 else None

    def predict(self, image: Any) -> tuple[str, float]:
        img = _get_opencv_or_numpy(image)
        wh_ratio = img.shape[1] / float(img.shape[0])
        max_wh_ratio = wh_ratio
        if self.fixed_input_width is not None:
            max_wh_ratio = self.fixed_input_width / float(self.resize.img_h)
        data, _ = self.resize(img, max_wh_ratio=max_wh_ratio)
        tensor = data[None, ...].astype(np.float32)
        preds = _pick_rec_output(self.session.run(tensor))
        text, score = self.decoder(preds)[0]
        return text, score

    def predict_batch(self, images: list[Any]) -> list[tuple[str, float]]:
        cv_images = [_get_opencv_or_numpy(image) for image in images]
        if not cv_images:
            return []

        max_wh_ratio = max(img.shape[1] / float(img.shape[0]) for img in cv_images)
        if self.fixed_input_width is not None:
            max_wh_ratio = self.fixed_input_width / float(self.resize.img_h)
        batches = []
        for img in cv_images:
            data, _ = self.resize(img, max_wh_ratio=max_wh_ratio)
            batches.append(data)
        tensor = np.stack(batches, axis=0).astype(np.float32)
        preds = _pick_rec_output(self.session.run(tensor))
        return self.decoder(preds)


class PPOCRv6OCR:
    def __init__(
        self,
        det_model_path: str | Path,
        rec_model_path: str | Path,
        det_config_path: str | Path | None = None,
        rec_config_path: str | Path | None = None,
        det_override_config:Mapping[str,Any]|None=None,
        rec_override_config:Mapping[str,Any]|None=None,
        det_limit_type:str='min',
        det_limit_side_len:int=32,

        engine: str | None = None,
        use_cuda: bool = False,
        use_cann: bool = False,
        use_dml: bool = False,
        providers: list[str] | None = None,
    ):
        self.det = PPOCRv6Det(
            det_model_path,
            det_config_path,
            override_config=det_override_config,
            limit_type=det_limit_type,
            limit_side_len=det_limit_side_len,
            engine=engine,
            use_cuda=use_cuda,
            use_cann=use_cann,
            use_dml=use_dml,
            providers=providers,
        )
        self.rec = PPOCRv6Rec(
            rec_model_path,
            rec_config_path,
            override_config=rec_override_config,
            engine=engine,
            use_cuda=use_cuda,
            use_cann=use_cann,
            use_dml=use_dml,
            providers=providers,
        )

    def predict(
        self,
        image: Any,
        *,
        debug: bool = False,
        det_score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        img = _get_opencv_or_numpy(image)
        det_boxes = self.det.predict(img)
        if det_score_threshold is not None:
            det_boxes = [
                box
                for box in det_boxes
                if float(box["det_score"]) >= det_score_threshold
            ]
        crops = [get_rotate_crop_image(img, box["box"]) for box in det_boxes]
        rec_results = self.rec.predict_batch(crops) if crops else []

        result: list[dict[str, Any]] = []
        for box_item, (text, score) in zip(det_boxes, rec_results, strict=False):
            result.append(
                {
                    "box": box_item["box"].tolist(),
                    "det_score": box_item["det_score"],
                    "text": text,
                    "rec_score": score,
                }
            )
        if debug:
            _show_crop_debug_view(crops, rec_results)
            _show_debug_view(img, result)
        return result


def load_model_dirs(det_dir: str | Path, rec_dir: str | Path) -> tuple[Path, Path, Path | None, Path | None]:
    det_dir = Path(det_dir)
    rec_dir = Path(rec_dir)
    det_model = _find_first_file(det_dir, "*.onnx")
    rec_model = _find_first_file(rec_dir, "*.onnx")
    if det_model is None:
        raise FileNotFoundError(f"no onnx model found under {det_dir}")
    if rec_model is None:
        raise FileNotFoundError(f"no onnx model found under {rec_dir}")
    det_cfg = _find_first_file(det_dir, "*.yml", "*.yaml")
    rec_cfg = _find_first_file(rec_dir, "*.yml", "*.yaml")
    return det_model, rec_model, det_cfg, rec_cfg
