from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import PIL.Image

from memect.base import images as image_utils

type ImageInput = str | Path | bytes | np.ndarray | PIL.Image.Image


def load_image_bgr(image: ImageInput) -> np.ndarray:
    if isinstance(image, np.ndarray):
        if image.ndim == 2:
            return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        if image.ndim == 3 and image.shape[2] == 4:
            return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError(f"unsupported ndarray image shape: {image.shape}")
        return image.copy()

    if isinstance(image, PIL.Image.Image):
        return image_utils.pil_to_cv2(image.convert("RGB"))

    return image_utils.open_cv2(image)


def order_points_clockwise(points: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float32).reshape(4, 2)
    ordered = np.zeros((4, 2), dtype=np.float32)
    sum_ = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).reshape(-1)
    ordered[0] = pts[np.argmin(sum_)]
    ordered[2] = pts[np.argmax(sum_)]
    ordered[1] = pts[np.argmin(diff)]
    ordered[3] = pts[np.argmax(diff)]
    return ordered


def clip_points(points: np.ndarray, height: int, width: int) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float32).copy()
    pts[:, 0] = np.clip(pts[:, 0], 0, max(width - 1, 0))
    pts[:, 1] = np.clip(pts[:, 1], 0, max(height - 1, 0))
    return pts


def get_rotate_crop_image(image: np.ndarray, points: np.ndarray) -> np.ndarray:
    pts = order_points_clockwise(points).astype(np.float32)
    img_crop_width = int(
        max(
            np.linalg.norm(pts[0] - pts[1]),
            np.linalg.norm(pts[2] - pts[3]),
        )
    )
    img_crop_height = int(
        max(
            np.linalg.norm(pts[0] - pts[3]),
            np.linalg.norm(pts[1] - pts[2]),
        )
    )

    img_crop_width = max(img_crop_width, 1)
    img_crop_height = max(img_crop_height, 1)

    pts_std = np.array(
        [
            [0, 0],
            [img_crop_width, 0],
            [img_crop_width, img_crop_height],
            [0, img_crop_height],
        ],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(pts, pts_std)
    dst_img = cv2.warpPerspective(
        image,
        matrix,
        (img_crop_width, img_crop_height),
        borderMode=cv2.BORDER_REPLICATE,
        flags=cv2.INTER_CUBIC,
    )
    dst_h, dst_w = dst_img.shape[:2]
    if dst_w > 0 and dst_h / float(dst_w) >= 1.5:
        dst_img = np.rot90(dst_img)
    return dst_img


def sorted_boxes(boxes: list[np.ndarray]) -> list[np.ndarray]:
    if len(boxes) <= 1:
        return boxes

    sorted_result = sorted(boxes, key=lambda box: (box[0][1], box[0][0]))
    for i in range(len(sorted_result) - 1):
        j = i
        while (
            j >= 0
            and abs(sorted_result[j + 1][0][1] - sorted_result[j][0][1]) < 10
            and sorted_result[j + 1][0][0] < sorted_result[j][0][0]
        ):
            sorted_result[j], sorted_result[j + 1] = (
                sorted_result[j + 1],
                sorted_result[j],
            )
            j -= 1
    return sorted_result
