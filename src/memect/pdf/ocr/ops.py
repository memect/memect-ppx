from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .utils import clip_points, order_points_clockwise


class NormalizeImage:
    def __init__(
        self,
        scale: float | str | None = None,
        mean: list[float] | tuple[float, ...] | None = None,
        std: list[float] | tuple[float, ...] | None = None,
        order: str = "hwc",
    ):
        if isinstance(scale, str):
            scale = float(eval(scale, {"__builtins__": {}}, {}))
        self.scale = float(scale if scale is not None else 1.0 / 255.0)
        self.mean = np.array(mean or [0.485, 0.456, 0.406], dtype=np.float32)
        self.std = np.array(std or [0.229, 0.224, 0.225], dtype=np.float32)
        self.order = order.lower()

    def __call__(self, img: np.ndarray) -> np.ndarray:
        image = img.astype(np.float32) * self.scale
        if self.order == "chw":
            mean = self.mean[:, None, None]
            std = self.std[:, None, None]
        else:
            mean = self.mean[None, None, :]
            std = self.std[None, None, :]
        return (image - mean) / std


class ToCHWImage:
    def __call__(self, img: np.ndarray) -> np.ndarray:
        return np.transpose(img, (2, 0, 1))


class DetResizeForTest:
    def __init__(self, **kwargs: Any):
        self.resize_long = kwargs.get("resize_long")
        self.limit_side_len = int(kwargs.get("limit_side_len", 960))
        self.limit_type = str(kwargs.get("limit_type", "max"))
        self.image_shape = kwargs.get("image_shape")

    def __call__(self, img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        src_h, src_w = img.shape[:2]

        if self.image_shape is not None:
            if len(self.image_shape) == 2:
                resize_h, resize_w = map(int, self.image_shape)
            elif len(self.image_shape) == 3:
                _, resize_h, resize_w = map(int, self.image_shape)
            else:
                raise ValueError(f"invalid image_shape: {self.image_shape}")
        elif self.resize_long is not None:
            resize_h, resize_w = self._resize_type0(src_h, src_w, int(self.resize_long))
        else:
            resize_h, resize_w = self._resize_type1(src_h, src_w)

        if resize_h <= 0 or resize_w <= 0:
            raise ValueError(
                f"invalid det resize result: src=({src_h},{src_w}) dst=({resize_h},{resize_w})"
            )

        resized = cv2.resize(img, (resize_w, resize_h))
        ratio_h = resize_h / float(src_h)
        ratio_w = resize_w / float(src_w)
        shape = np.array([src_h, src_w, ratio_h, ratio_w], dtype=np.float32)
        return resized, shape

    def _resize_type0(self, src_h: int, src_w: int, resize_long: int) -> tuple[int, int]:
        ratio = resize_long / float(max(src_h, src_w))
        resize_h = int(round(src_h * ratio / 32) * 32)
        resize_w = int(round(src_w * ratio / 32) * 32)
        resize_h = max(resize_h, 32)
        resize_w = max(resize_w, 32)
        return resize_h, resize_w

    def _resize_type1(self, src_h: int, src_w: int) -> tuple[int, int]:
        if self.limit_type == "max":
            ratio = 1.0
            if max(src_h, src_w) > self.limit_side_len:
                ratio = self.limit_side_len / float(max(src_h, src_w))
        elif self.limit_type == "min":
            ratio = 1.0
            if min(src_h, src_w) < self.limit_side_len:
                ratio = self.limit_side_len / float(min(src_h, src_w))
        elif self.limit_type == "resize_long":
            ratio = self.limit_side_len / float(max(src_h, src_w))
        else:
            raise ValueError(f"unsupported limit_type: {self.limit_type}")

        resize_h = int(round(src_h * ratio))
        resize_w = int(round(src_w * ratio))
        resize_h = max(int(round(resize_h / 32) * 32), 32)
        resize_w = max(int(round(resize_w / 32) * 32), 32)
        return resize_h, resize_w


class RecResizeImg:
    def __init__(self, image_shape: list[int] | tuple[int, ...], padding: bool = True):
        if len(image_shape) != 3:
            raise ValueError(f"invalid rec image_shape: {image_shape}")
        self.img_c, self.img_h, self.img_w = map(int, image_shape)
        self.padding = padding

    def __call__(
        self,
        img: np.ndarray,
        *,
        max_wh_ratio: float | None = None,
    ) -> tuple[np.ndarray, float]:
        h, w = img.shape[:2]
        if h <= 0 or w <= 0:
            raise ValueError(f"invalid rec image size: {(h, w)}")

        if max_wh_ratio is None:
            max_wh_ratio = self.img_w / float(self.img_h)
        max_wh_ratio = max(float(max_wh_ratio), self.img_w / float(self.img_h))

        ratio = w / float(h)
        if self.padding:
            target_w = int(math.ceil(self.img_h * max_wh_ratio))
            resized_w = min(target_w, int(math.ceil(self.img_h * ratio)))
        else:
            target_w = self.img_w
            resized_w = target_w
        resized_w = max(resized_w, 1)

        resized = cv2.resize(img, (resized_w, self.img_h))
        resized = resized.astype(np.float32) / 255.0
        resized = resized.transpose((2, 0, 1))
        resized = (resized - 0.5) / 0.5

        padding_im = np.zeros((self.img_c, self.img_h, target_w), dtype=np.float32)
        padding_im[:, :, :resized_w] = resized[:, :, :resized_w]
        valid_ratio = min(1.0, resized_w / float(target_w))
        return padding_im, valid_ratio


class CTCLabelDecode:
    def __init__(
        self,
        character_dict: list[str],
        use_space_char: bool = False,
    ):
        self.character = ["blank"] + list(character_dict)
        if use_space_char and " " not in self.character:
            self.character.append(" ")

    def __call__(self, preds: np.ndarray) -> list[tuple[str, float]]:
        preds_idx = preds.argmax(axis=2)
        preds_prob = preds.max(axis=2)
        result: list[tuple[str, float]] = []
        for indices, probs in zip(preds_idx, preds_prob, strict=False):
            text_chars: list[str] = []
            conf_list: list[float] = []
            last_idx = None
            for idx, prob in zip(indices.tolist(), probs.tolist(), strict=False):
                if idx == 0:
                    last_idx = idx
                    continue
                if idx == last_idx:
                    continue
                if idx >= len(self.character):
                    last_idx = idx
                    continue
                text_chars.append(self.character[idx])
                conf_list.append(float(prob))
                last_idx = idx
            score = float(sum(conf_list) / len(conf_list)) if conf_list else 0.0
            result.append(("".join(text_chars), score))
        return result


class DBPostProcess:
    def __init__(
        self,
        thresh: float = 0.3,
        box_thresh: float = 0.6,
        max_candidates: int = 1000,
        unclip_ratio: float = 1.5,
        score_mode: str = "fast",
        min_size: int = 3,
    ):
        self.thresh = float(thresh)
        self.box_thresh = float(box_thresh)
        self.max_candidates = int(max_candidates)
        self.unclip_ratio = float(unclip_ratio)
        self.score_mode = score_mode
        self.min_size = int(min_size)

    def __call__(self, pred: np.ndarray, shape_list: np.ndarray) -> list[dict[str, Any]]:
        if pred.ndim == 4:
            pred = pred[:, 0, :, :]
        segmentation = pred > self.thresh
        result: list[dict[str, Any]] = []
        for batch_index in range(pred.shape[0]):
            src_h, src_w, ratio_h, ratio_w = shape_list[batch_index].tolist()
            boxes, scores = self.boxes_from_bitmap(
                pred[batch_index],
                segmentation[batch_index],
                int(round(src_w)),
                int(round(src_h)),
            )
            result.append({"points": boxes, "scores": scores})
        return result

    def boxes_from_bitmap(
        self,
        pred: np.ndarray,
        bitmap: np.ndarray,
        dest_width: int,
        dest_height: int,
    ) -> tuple[list[np.ndarray], list[float]]:
        contours, _ = cv2.findContours(
            (bitmap.astype(np.uint8) * 255),
            cv2.RETR_LIST,
            cv2.CHAIN_APPROX_SIMPLE,
        )

        num_contours = min(len(contours), self.max_candidates)
        boxes: list[np.ndarray] = []
        scores: list[float] = []
        for contour in contours[:num_contours]:
            points, sside = self.get_mini_boxes(contour)
            if sside < self.min_size:
                continue

            score = self.box_score_fast(pred, points)
            if score < self.box_thresh:
                continue

            expanded = self.unclip(points)
            expanded_points, expanded_sside = self.get_mini_boxes(expanded)
            if expanded_sside < self.min_size + 2:
                continue

            box = expanded_points.copy()
            box[:, 0] = np.round(box[:, 0] / pred.shape[1] * dest_width)
            box[:, 1] = np.round(box[:, 1] / pred.shape[0] * dest_height)
            box = clip_points(box, dest_height, dest_width)
            boxes.append(box.astype(np.float32))
            scores.append(float(score))
        return boxes, scores

    def get_mini_boxes(self, contour: np.ndarray) -> tuple[np.ndarray, float]:
        box = cv2.boxPoints(cv2.minAreaRect(contour))
        box = order_points_clockwise(box)
        side = min(
            np.linalg.norm(box[0] - box[1]),
            np.linalg.norm(box[1] - box[2]),
        )
        return box.astype(np.float32), float(side)

    def box_score_fast(self, bitmap: np.ndarray, box: np.ndarray) -> float:
        h, w = bitmap.shape[:2]
        box = box.copy()
        xmin = int(np.clip(np.floor(box[:, 0].min()).astype(np.int32), 0, w - 1))
        xmax = int(np.clip(np.ceil(box[:, 0].max()).astype(np.int32), 0, w - 1))
        ymin = int(np.clip(np.floor(box[:, 1].min()).astype(np.int32), 0, h - 1))
        ymax = int(np.clip(np.ceil(box[:, 1].max()).astype(np.int32), 0, h - 1))

        if xmax <= xmin or ymax <= ymin:
            return 0.0

        mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
        shifted = box.copy()
        shifted[:, 0] -= xmin
        shifted[:, 1] -= ymin
        cv2.fillPoly(mask, [shifted.astype(np.int32)], 1)
        return float(cv2.mean(bitmap[ymin : ymax + 1, xmin : xmax + 1], mask)[0])

    def unclip(self, box: np.ndarray) -> np.ndarray:
        polygon = np.asarray(box, dtype=np.float32).reshape(-1, 2)
        if polygon.shape[0] < 3:
            return polygon

        area = abs(self._polygon_signed_area(polygon))
        perimeter = self._polygon_perimeter(polygon)
        if area <= 1e-6 or perimeter <= 1e-6:
            return polygon

        distance = area * self.unclip_ratio / perimeter
        expanded = self._offset_polygon(polygon, distance)
        if expanded is None or expanded.shape[0] < 3:
            return polygon
        return expanded.astype(np.float32)

    @staticmethod
    def _polygon_signed_area(polygon: np.ndarray) -> float:
        x = polygon[:, 0]
        y = polygon[:, 1]
        return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))

    @staticmethod
    def _polygon_perimeter(polygon: np.ndarray) -> float:
        shifted = np.roll(polygon, -1, axis=0)
        return float(np.linalg.norm(shifted - polygon, axis=1).sum())

    @classmethod
    def _offset_polygon(cls, polygon: np.ndarray, distance: float) -> np.ndarray | None:
        count = polygon.shape[0]
        signed_area = cls._polygon_signed_area(polygon)
        if abs(signed_area) <= 1e-6:
            return None

        is_ccw = signed_area > 0
        shifted_lines: list[tuple[np.ndarray, np.ndarray]] = []
        for index in range(count):
            p0 = polygon[index]
            p1 = polygon[(index + 1) % count]
            edge = p1 - p0
            edge_len = float(np.linalg.norm(edge))
            if edge_len <= 1e-6:
                return None
            if is_ccw:
                normal = np.array([edge[1], -edge[0]], dtype=np.float32) / edge_len
            else:
                normal = np.array([-edge[1], edge[0]], dtype=np.float32) / edge_len
            offset = normal * distance
            shifted_lines.append((p0 + offset, p1 + offset))

        expanded: list[np.ndarray] = []
        for index in range(count):
            prev_line = shifted_lines[index - 1]
            curr_line = shifted_lines[index]
            point = cls._line_intersection(
                prev_line[0],
                prev_line[1],
                curr_line[0],
                curr_line[1],
            )
            if point is None:
                point = curr_line[0]
            expanded.append(point)
        return np.asarray(expanded, dtype=np.float32)

    @staticmethod
    def _line_intersection(
        p1: np.ndarray,
        p2: np.ndarray,
        p3: np.ndarray,
        p4: np.ndarray,
    ) -> np.ndarray | None:
        x1, y1 = p1
        x2, y2 = p2
        x3, y3 = p3
        x4, y4 = p4
        denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(float(denom)) <= 1e-6:
            return None
        det1 = x1 * y2 - y1 * x2
        det2 = x3 * y4 - y3 * x4
        px = (det1 * (x3 - x4) - (x1 - x2) * det2) / denom
        py = (det1 * (y3 - y4) - (y1 - y2) * det2) / denom
        return np.array([px, py], dtype=np.float32)


def build_det_preprocess(config: dict[str, Any]) -> list[Any]:
    ops: list[Any] = []
    for item in config["PreProcess"]["transform_ops"]:
        name, params = next(iter(item.items()))
        params = params or {}
        if name == "DecodeImage":
            continue
        if name.endswith("LabelEncode"):
            continue
        if name == "DetResizeForTest":
            ops.append(DetResizeForTest(**params))
            continue
        if name == "NormalizeImage":
            ops.append(NormalizeImage(**params))
            continue
        if name == "ToCHWImage":
            ops.append(ToCHWImage())
            continue
        if name == "KeepKeys":
            continue
        raise ValueError(f"unsupported det preprocess op: {name}")
    return ops


def build_rec_resize(config: dict[str, Any]) -> RecResizeImg:
    for item in config["PreProcess"]["transform_ops"]:
        name, params = next(iter(item.items()))
        params = params or {}
        if name == "RecResizeImg":
            return RecResizeImg(
                image_shape=params["image_shape"],
                padding=bool(params.get("padding", True)),
            )
    raise ValueError("RecResizeImg not found in rec config")


def _load_rec_characters(
    post_cfg: dict[str, Any],
    *,
    base_dir: Path | None = None,
) -> list[str]:
    chars = post_cfg.get("character_dict")
    if isinstance(chars, list) and chars:
        return [str(char) for char in chars]

    dict_path = post_cfg.get("character_dict_path")
    if dict_path:
        path = Path(dict_path)
        if not path.is_absolute() and base_dir is not None:
            path = base_dir / path
        lines = path.read_text("utf-8").splitlines()
        return [line.strip("\n").strip("\r") for line in lines if line.strip("\n").strip("\r")]

    return list("0123456789abcdefghijklmnopqrstuvwxyz")


def build_ctc_decoder(
    config: dict[str, Any],
    *,
    base_dir: Path | None = None,
) -> CTCLabelDecode:
    post_cfg = config["PostProcess"]
    if post_cfg["name"] != "CTCLabelDecode":
        raise ValueError(f"unsupported rec postprocess: {post_cfg['name']}")
    chars = _load_rec_characters(post_cfg, base_dir=base_dir)
    use_space_char = bool(post_cfg.get("use_space_char", False))
    return CTCLabelDecode(chars, use_space_char=use_space_char)


def build_db_postprocess(config: dict[str, Any]) -> DBPostProcess:
    post_cfg = config["PostProcess"]
    if post_cfg["name"] != "DBPostProcess":
        raise ValueError(f"unsupported det postprocess: {post_cfg['name']}")
    return DBPostProcess(
        thresh=post_cfg.get("thresh", 0.3),
        box_thresh=post_cfg.get("box_thresh", 0.6),
        max_candidates=post_cfg.get("max_candidates", 1000),
        unclip_ratio=post_cfg.get("unclip_ratio", 1.5),
        score_mode=post_cfg.get("score_mode", "fast"),
    )
