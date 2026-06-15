import math
from dataclasses import dataclass
from typing import Any, Literal

import cv2
import numpy as np
from PIL import Image

from memect.base.bbox import BBox


@dataclass(frozen=True)
class ColorBlock:
    bbox: BBox
    color: tuple[int, int, int]
    area: int
    mask_ratio: float

    @property
    def hex(self) -> str:
        return "#{:02X}{:02X}{:02X}".format(*self.color)

    def jsonify(self) -> dict[str, Any]:
        return {
            "bbox": list(self.bbox),
            "color": self.color,
            "hex": self.hex,
            "area": self.area,
            "mask_ratio": self.mask_ratio,
        }


class ColorBlockDetector:
    def __init__(
        self,
        *,
        min_area_ratio: float = 0.001,
        min_color_ratio: float = 0.002,
        min_width: int = 8,
        min_height: int = 6,
        min_width_ratio: float = 0.04,
        min_height_ratio: float = 0.012,
        min_aspect_ratio: float = 1.2,
        min_mask_ratio: float = 0.6,
        max_inner_area_ratio: float = 0.25,
        max_plain_background_area_ratio: float = 0.05,
        plain_background_white_ratio: float = 0.72,
        color_distance: float = 18.0,
        split_color_distance: float = 10.0,
        vertical_merge_color_distance: float = 6.0,
        foreground_dilate: int = 1,
        foreground_inpaint_radius: int = 3,
        max_detect_size: tuple[int, int] | None = (500, 800),
        input_mode: Literal["bgr", "rgb"] = "bgr",
    ):
        super().__init__()
        self.min_area_ratio = min_area_ratio
        self.min_color_ratio = min_color_ratio
        self.min_width = min_width
        self.min_height = min_height
        self.min_width_ratio = min_width_ratio
        self.min_height_ratio = min_height_ratio
        self.min_aspect_ratio = min_aspect_ratio
        self.min_mask_ratio = min_mask_ratio
        self.max_inner_area_ratio = max_inner_area_ratio
        self.max_plain_background_area_ratio = max_plain_background_area_ratio
        self.plain_background_white_ratio = plain_background_white_ratio
        self.color_distance = color_distance
        self.split_color_distance = split_color_distance
        self.vertical_merge_color_distance = vertical_merge_color_distance
        self.foreground_dilate = foreground_dilate
        self.foreground_inpaint_radius = foreground_inpaint_radius
        self.max_detect_size = max_detect_size
        self.input_mode = input_mode

    def detect(self, img: Any, *, debug: bool = False) -> list[ColorBlock]:
        original_rgb = self._to_rgb(img)
        original_h, original_w = original_rgb.shape[:2]
        if original_h == 0 or original_w == 0:
            return []

        rgb, scale_x, scale_y = self._resize_for_detection(original_rgb)
        h, w = rgb.shape[:2]
        if h == 0 or w == 0:
            return []

        background_rgb, foreground_mask = self._remove_foreground(rgb)
        block_rgb = self._smooth_background_for_blocks(background_rgb)
        candidate_mask = self._background_candidate_mask(block_rgb)
        if not np.any(candidate_mask):
            if debug:
                foreground_mask, background_rgb = self._scale_debug_images(
                    foreground_mask,
                    background_rgb,
                    original_w,
                    original_h,
                    scale_x,
                    scale_y,
                )
                self._debug_show(original_rgb, [], foreground_mask, background_rgb)
            return []

        colors = self._dominant_colors(block_rgb, candidate_mask)
        blocks: list[ColorBlock] = []
        for color, raw_mask in self._exclusive_color_masks(
            block_rgb, candidate_mask, colors
        ):
            region_mask = self._clean_mask(raw_mask, w, h)
            blocks.extend(
                self._mask_blocks(block_rgb, raw_mask, region_mask, color)
            )

        blocks = self._dedupe_blocks(blocks)
        blocks = self._merge_blocks(blocks, scale_x=scale_x, scale_y=scale_y)
        blocks = self._remove_inner_blocks(
            blocks,
            scale_x=scale_x,
            scale_y=scale_y,
        )
        blocks = self._remove_plain_background_blocks(block_rgb, blocks)
        blocks = self._merge_same_color_fragments(
            blocks,
            scale_x=scale_x,
            scale_y=scale_y,
        )
        blocks = self._remove_same_color_overlaps(blocks)
        blocks = self._sort_blocks(blocks)
        blocks = self._scale_blocks(
            blocks,
            original_w,
            original_h,
            scale_x,
            scale_y,
        )
        if debug:
            foreground_mask, background_rgb = self._scale_debug_images(
                foreground_mask,
                background_rgb,
                original_w,
                original_h,
                scale_x,
                scale_y,
            )
            self._debug_show(original_rgb, blocks, foreground_mask, background_rgb)
        return blocks

    def _resize_for_detection(
        self, rgb: np.ndarray
    ) -> tuple[np.ndarray, float, float]:
        h, w = rgb.shape[:2]
        if self.max_detect_size is None:
            return rgb, 1.0, 1.0

        max_w, max_h = self.max_detect_size
        if max_w <= 0 or max_h <= 0:
            return rgb, 1.0, 1.0

        scale = min(1.0, max_w / max(1, w), max_h / max(1, h))
        if scale >= 1.0:
            return rgb, 1.0, 1.0

        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        resized = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
        return resized, new_w / w, new_h / h

    def _scale_blocks(
        self,
        blocks: list[ColorBlock],
        width: int,
        height: int,
        scale_x: float,
        scale_y: float,
    ) -> list[ColorBlock]:
        if scale_x == 1.0 and scale_y == 1.0:
            return blocks

        scale_area = max(scale_x * scale_y, 1e-6)
        result: list[ColorBlock] = []
        for block in blocks:
            x0 = max(0, min(width, math.floor(block.bbox.x0 / scale_x)))
            y0 = max(0, min(height, math.floor(block.bbox.y0 / scale_y)))
            x1 = max(0, min(width, math.ceil(block.bbox.x1 / scale_x)))
            y1 = max(0, min(height, math.ceil(block.bbox.y1 / scale_y)))
            if x0 >= x1 or y0 >= y1:
                continue
            result.append(
                ColorBlock(
                    bbox=BBox(x0, y0, x1, y1),
                    color=block.color,
                    area=int(round(block.area / scale_area)),
                    mask_ratio=block.mask_ratio,
                )
            )
        return result

    def _scale_debug_images(
        self,
        foreground_mask: np.ndarray,
        background_rgb: np.ndarray,
        width: int,
        height: int,
        scale_x: float,
        scale_y: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        if scale_x == 1.0 and scale_y == 1.0:
            return foreground_mask, background_rgb

        mask_u8 = foreground_mask.astype(np.uint8) * 255
        mask_u8 = cv2.resize(mask_u8, (width, height), interpolation=cv2.INTER_NEAREST)
        background_rgb = cv2.resize(
            background_rgb,
            (width, height),
            interpolation=cv2.INTER_AREA,
        )
        return mask_u8 > 0, background_rgb

    def _scaled_px(
        self,
        value: float,
        scale: float,
        *,
        minimum: float = 1.0,
    ) -> float:
        return max(minimum, value * scale)

    def _to_rgb(self, img: Any) -> np.ndarray:
        if isinstance(img, Image.Image):
            return np.array(img.convert("RGB"))

        arr = np.asarray(img)
        if arr.ndim == 2:
            return np.stack([arr, arr, arr], axis=2).astype(np.uint8)
        if arr.ndim != 3:
            raise ValueError(f"unsupported image shape: {arr.shape}")

        if arr.shape[2] == 4:
            if self.input_mode == "rgb":
                arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2RGB)
            else:
                arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2RGB)
        elif arr.shape[2] == 3:
            if self.input_mode == "rgb":
                arr = arr.copy()
            else:
                arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
        else:
            raise ValueError(f"unsupported image channels: {arr.shape[2]}")

        if arr.dtype != np.uint8:
            arr = np.clip(arr, 0, 255).astype(np.uint8)
        return arr

    def _smooth_background_for_blocks(self, rgb: np.ndarray) -> np.ndarray:
        h, w = rgb.shape[:2]
        size = min(h, w)
        if size < 80:
            return rgb

        kernel = 5
        if size >= 700:
            kernel = 7
        if size >= 1400:
            kernel = 9

        smoothed = cv2.medianBlur(rgb, kernel)
        return cv2.bilateralFilter(smoothed, d=5, sigmaColor=18, sigmaSpace=9)

    def _remove_foreground(self, rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        foreground_mask = self._foreground_mask(rgb)
        if not np.any(foreground_mask):
            return rgb.copy(), foreground_mask

        mask = foreground_mask.astype(np.uint8) * 255
        background = cv2.inpaint(
            rgb,
            mask,
            self.foreground_inpaint_radius,
            cv2.INPAINT_TELEA,
        )
        return background, foreground_mask

    def _foreground_mask(self, rgb: np.ndarray) -> np.ndarray:
        h, w = rgb.shape[:2]
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        value = hsv[:, :, 2]
        saturation = hsv[:, :, 1]
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

        dark = value <= 135
        edges = cv2.Canny(gray, 60, 160) > 0

        rgb_i = rgb.astype(np.int16)
        red = (
            (rgb_i[:, :, 0] >= 150)
            & (rgb_i[:, :, 0] - rgb_i[:, :, 1] >= 55)
            & (rgb_i[:, :, 0] - rgb_i[:, :, 2] >= 55)
        )
        red = self._small_component_mask(red, h, w, max_area_ratio=0.02)

        light_gray = (
            (saturation <= 28)
            & (value >= 120)
            & (value <= 242)
            & self._plain_white_neighborhood_mask(rgb)
        )
        light_gray = self._small_component_mask(
            light_gray,
            h,
            w,
            max_area_ratio=self.max_plain_background_area_ratio,
            max_width_ratio=0.45,
        )

        mask = dark | edges | red | light_gray
        if self.foreground_dilate > 0:
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            mask_u8 = mask.astype(np.uint8) * 255
            mask_u8 = cv2.dilate(mask_u8, kernel, iterations=self.foreground_dilate)
            mask = mask_u8 > 0
        return mask

    def _small_component_mask(
        self,
        mask: np.ndarray,
        height: int,
        width: int,
        *,
        max_area_ratio: float,
        max_width_ratio: float = 0.35,
        max_height_ratio: float = 0.25,
    ) -> np.ndarray:
        output = np.zeros(mask.shape, dtype=bool)
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
            mask.astype(np.uint8), connectivity=8
        )
        image_area = height * width
        for label in range(1, num_labels):
            x, y, w, h, area = stats[label]
            if area <= 0:
                continue
            if area / image_area > max_area_ratio:
                continue
            if w / width > max_width_ratio or h / height > max_height_ratio:
                continue
            output[labels == label] = True
        return output

    def _plain_white_neighborhood_mask(self, rgb: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        white = ((hsv[:, :, 2] >= 245) & (hsv[:, :, 1] <= 22)).astype(np.uint8)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
        white_around = cv2.dilate(white, kernel, iterations=1) > 0
        return white_around

    def _background_candidate_mask(self, rgb: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        saturation = hsv[:, :, 1]
        value = hsv[:, :, 2]

        rgb_i = rgb.astype(np.int16)
        max_channel = rgb_i.max(axis=2)
        min_channel = rgb_i.min(axis=2)
        white_distance = np.linalg.norm(255 - rgb_i, axis=2)

        near_white = (value >= 245) & (saturation <= 22)
        near_gray_white = (min_channel >= 238) & ((max_channel - min_channel) <= 10)
        dark = value <= 105
        low_contrast_gray = (saturation <= 10) & (white_distance <= 32)

        return ~(near_white | near_gray_white | dark | low_contrast_gray)

    def _dominant_colors(
        self, rgb: np.ndarray, candidate_mask: np.ndarray
    ) -> list[tuple[int, int, int]]:
        pixels = rgb[candidate_mask]
        if len(pixels) == 0:
            return []

        h, w = candidate_mask.shape
        min_pixels = max(20, int(h * w * self.min_color_ratio))
        quantized = (pixels // 8) * 8 + 4
        values, counts = np.unique(quantized, axis=0, return_counts=True)
        order = np.argsort(counts)[::-1]

        colors: list[tuple[int, int, int]] = []
        for index in order:
            count = int(counts[index])
            if count < min_pixels:
                break
            color = tuple(int(v) for v in values[index])
            if any(self._rgb_distance(color, old) < 12 for old in colors):
                continue
            colors.append(color)
            if len(colors) >= 12:
                break
        return colors

    def _color_mask(
        self,
        rgb: np.ndarray,
        candidate_mask: np.ndarray,
        color: tuple[int, int, int],
    ) -> np.ndarray:
        lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
        color_lab = cv2.cvtColor(
            np.array([[color]], dtype=np.uint8), cv2.COLOR_RGB2LAB
        ).astype(np.float32)[0, 0]
        distance = np.linalg.norm(lab - color_lab, axis=2)
        return candidate_mask & (distance <= self.color_distance)

    def _exclusive_color_masks(
        self,
        rgb: np.ndarray,
        candidate_mask: np.ndarray,
        colors: list[tuple[int, int, int]],
    ) -> list[tuple[tuple[int, int, int], np.ndarray]]:
        if not colors:
            return []

        lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
        color_labs = cv2.cvtColor(
            np.array([[color for color in colors]], dtype=np.uint8),
            cv2.COLOR_RGB2LAB,
        ).astype(np.float32)[0]

        distances = np.stack(
            [np.linalg.norm(lab - color_lab, axis=2) for color_lab in color_labs],
            axis=0,
        )
        nearest = np.argmin(distances, axis=0)
        nearest_distance = np.take_along_axis(
            distances,
            nearest[np.newaxis, :, :],
            axis=0,
        )[0]
        assigned = candidate_mask & (nearest_distance <= self.color_distance)

        masks: list[tuple[tuple[int, int, int], np.ndarray]] = []
        for index, color in enumerate(colors):
            mask = assigned & (nearest == index)
            if np.any(mask):
                masks.append((color, mask))
        return masks

    def _clean_mask(self, mask: np.ndarray, width: int, height: int) -> np.ndarray:
        mask_u8 = mask.astype(np.uint8) * 255
        kx = max(3, min(15, width // 80 * 2 + 3))
        ky = max(3, min(9, height // 160 * 2 + 3))
        close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky))
        open_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, close_kernel)
        mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_OPEN, open_kernel)
        return mask_u8 > 0

    def _mask_blocks(
        self,
        rgb: np.ndarray,
        raw_mask: np.ndarray,
        region_mask: np.ndarray,
        expected_color: tuple[int, int, int],
    ) -> list[ColorBlock]:
        h, w = region_mask.shape
        min_area = max(12, int(h * w * self.min_area_ratio))
        min_width = max(self.min_width, int(w * self.min_width_ratio))
        min_height = max(self.min_height, int(h * self.min_height_ratio))
        blocks: list[ColorBlock] = []

        component_mask = self._background_region_mask(region_mask, w, h)
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
            component_mask.astype(np.uint8), connectivity=8
        )
        for label in range(1, num_labels):
            x, y, width, height, area = (int(v) for v in stats[label])
            thin_band = self._is_thin_background_band(width, height, w, h)
            narrow_band = self._is_narrow_background_band(width, height, w, h)
            if area < min_area:
                continue
            if (
                width < min_width or height < min_height
            ) and not thin_band and not narrow_band:
                continue
            if (
                not thin_band
                and not narrow_band
                and not self._is_background_shape(width, height, w, h)
            ):
                continue

            component = labels[y : y + height, x : x + width] == label
            x0, y0, x1, y1 = self._tight_bbox_from_component(component, x, y)
            thin_band = self._is_thin_background_band(x1 - x0, y1 - y0, w, h)
            narrow_band = self._is_narrow_background_band(x1 - x0, y1 - y0, w, h)
            if (
                x1 - x0 < min_width or y1 - y0 < min_height
            ) and not thin_band and not narrow_band:
                continue

            for sx0, sy0, sx1, sy1 in self._split_component_rectangles(
                rgb,
                raw_mask,
                component,
                x,
                y,
                x0,
                y0,
                x1,
                y1,
                min_width,
                min_height,
            ):
                block_mask = raw_mask[sy0:sy1, sx0:sx1]
                component_mask = component[
                    sy0 - y : sy1 - y,
                    sx0 - x : sx1 - x,
                ]
                shape_mask = block_mask & component_mask
                area = int(np.count_nonzero(block_mask))
                shape_area = int(np.count_nonzero(shape_mask))
                bbox_area = (sx1 - sx0) * (sy1 - sy0)
                if area < min_area or shape_area < min_area or bbox_area <= 0:
                    continue

                mask_ratio = shape_area / bbox_area
                if mask_ratio < self.min_mask_ratio:
                    continue

                color = self._block_color(
                    rgb[sy0:sy1, sx0:sx1],
                    shape_mask,
                )
                if self._rgb_distance(color, expected_color) > 45:
                    continue
                blocks.append(
                    ColorBlock(
                        bbox=BBox(sx0, sy0, sx1, sy1),
                        color=color,
                        area=area,
                        mask_ratio=mask_ratio,
                    )
                )
        return blocks

    def _background_region_mask(
        self, mask: np.ndarray, width: int, height: int
    ) -> np.ndarray:
        mask_u8 = mask.astype(np.uint8) * 255
        gap = max(1, min(4, min(width, height) // 180))
        close_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (gap * 2 + 1, gap * 2 + 1)
        )
        mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, close_kernel)

        line_gap = max(3, min(11, width // 180 * 2 + 3))
        row_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (line_gap, 1))
        col_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, line_gap))
        mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, row_kernel)
        mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, col_kernel)

        open_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_OPEN, open_kernel)
        return mask_u8 > 0

    def _tight_bbox_from_component(
        self, component: np.ndarray, x_offset: int, y_offset: int
    ) -> tuple[int, int, int, int]:
        rows = np.flatnonzero(np.any(component, axis=1))
        cols = np.flatnonzero(np.any(component, axis=0))
        if len(rows) == 0 or len(cols) == 0:
            return x_offset, y_offset, x_offset, y_offset
        return (
            x_offset + int(cols[0]),
            y_offset + int(rows[0]),
            x_offset + int(cols[-1]) + 1,
            y_offset + int(rows[-1]) + 1,
        )

    def _split_component_rectangles(
        self,
        rgb: np.ndarray,
        raw_mask: np.ndarray,
        component: np.ndarray,
        component_x: int,
        component_y: int,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        min_width: int,
        min_height: int,
    ) -> list[tuple[int, int, int, int]]:
        local_rgb = rgb[y0:y1, x0:x1]
        local_component = component[
            y0 - component_y : y1 - component_y,
            x0 - component_x : x1 - component_x,
        ]
        local_mask = raw_mask[y0:y1, x0:x1] & local_component
        y_ranges = self._color_change_ranges(
            local_rgb,
            local_mask,
            axis=0,
            min_len=min_height,
        )

        rects: list[tuple[int, int, int, int]] = []
        for ly0, ly1 in y_ranges:
            band_height = ly1 - ly0
            band_mask = local_mask[ly0:ly1, :]
            band_area = max(1, band_mask.shape[0] * band_mask.shape[1])
            band_ratio = np.count_nonzero(band_mask) / band_area
            min_thin_height = self._min_thin_band_height(local_mask.shape[0])
            if band_height < min_height and not (
                band_height >= min_thin_height
                and band_ratio >= self.min_mask_ratio
            ):
                continue
            band_rgb = local_rgb[ly0:ly1, :]
            x_ranges = self._color_change_ranges(
                band_rgb,
                band_mask,
                axis=1,
                min_len=min_width,
            )
            for lx0, lx1 in x_ranges:
                width = lx1 - lx0
                touches_edge = lx0 == 0 or lx1 == band_mask.shape[1]
                min_edge_width = max(3, min_width // 3)
                column = band_mask[:, lx0:lx1]
                column_area = max(1, column.shape[0] * column.shape[1])
                column_ratio = np.count_nonzero(column) / column_area
                min_narrow_width = self._min_narrow_band_width(local_mask.shape[1])
                if width < min_width and not (
                    touches_edge and width >= min_edge_width
                ) and not (
                    width >= min_narrow_width
                    and column_ratio >= self.min_mask_ratio
                ):
                    continue
                rects.append((x0 + lx0, y0 + ly0, x0 + lx1, y0 + ly1))
        if rects:
            return rects
        return [(x0, y0, x1, y1)]

    def _color_change_ranges(
        self,
        rgb: np.ndarray,
        mask: np.ndarray,
        *,
        axis: Literal[0, 1],
        min_len: int,
    ) -> list[tuple[int, int]]:
        length = mask.shape[axis]
        cross = mask.shape[1 - axis]
        cross_ratio = self.min_width_ratio if axis == 0 else self.min_height_ratio
        min_pixels = max(3, int(cross * cross_ratio * 0.5))
        gap_len = max(8, min(32, min_len // 3))

        samples: list[tuple[np.ndarray, float] | None] = []
        for i in range(length):
            line_mask = mask[i, :] if axis == 0 else mask[:, i]
            pixel_count = int(np.count_nonzero(line_mask))
            if pixel_count < min_pixels:
                samples.append(None)
                continue
            if axis == 0:
                line_rgb = rgb[i, :][line_mask]
            else:
                line_rgb = rgb[:, i][line_mask]
            samples.append(
                (np.median(line_rgb, axis=0).astype(float), pixel_count / cross)
            )

        first = next((sample for sample in samples if sample is not None), None)
        if first is None:
            return [(0, length)]

        ranges: list[tuple[int, int, np.ndarray, int, float]] = []
        first_index = next(
            i for i, sample in enumerate(samples) if sample is not None
        )
        start = first_index
        run_color = first[0]
        run_coverage = first[1]
        run_count = 1
        last_valid = first_index
        empty_start: int | None = None
        for i in range(first_index + 1, length):
            sample = samples[i]
            if sample is None:
                if empty_start is None:
                    empty_start = i
                continue
            if empty_start is not None:
                if i - empty_start >= gap_len:
                    ranges.append(
                        (start, empty_start, run_color, run_count, run_coverage)
                    )
                    start = i
                    run_color = sample[0]
                    run_coverage = sample[1]
                    run_count = 1
                    last_valid = i
                    empty_start = None
                    continue
                empty_start = None
            sample_color, sample_coverage = sample
            if (
                self._array_rgb_distance(sample_color, run_color)
                > self.split_color_distance
                or abs(sample_coverage - run_coverage) > 0.28
            ):
                ranges.append((start, i, run_color, run_count, run_coverage))
                start = i
                run_color = sample_color
                run_coverage = sample_coverage
                run_count = 1
                continue
            run_color = (run_color * run_count + sample_color) / (run_count + 1)
            run_coverage = (
                run_coverage * run_count + sample_coverage
            ) / (run_count + 1)
            run_count += 1
            last_valid = i
        ranges.append((start, last_valid + 1, run_color, run_count, run_coverage))

        if axis == 0:
            keep_short_len = self._min_thin_band_height(length)
        else:
            keep_short_len = self._min_narrow_band_width(length)
        ranges = self._merge_short_color_ranges(
            ranges,
            min_len,
            gap_len,
            length,
            keep_short_len=keep_short_len,
        )
        return [(start, end) for start, end, _, _, _ in ranges if start < end]

    def _merge_short_color_ranges(
        self,
        ranges: list[tuple[int, int, np.ndarray, int, float]],
        min_len: int,
        max_merge_gap: int,
        total_len: int,
        *,
        keep_short_len: int | None = None,
    ) -> list[tuple[int, int, np.ndarray, int, float]]:
        min_edge_len = max(3, min_len // 3)
        while len(ranges) > 1:
            short_index = next(
                (
                    i
                    for i, (start, end, _, _, coverage) in enumerate(ranges)
                    if end - start < min_len
                    and not (
                        (start == 0 or end == total_len)
                        and end - start >= min_edge_len
                    )
                    and not (
                        keep_short_len is not None
                        and end - start >= keep_short_len
                        and coverage >= self.min_mask_ratio
                    )
                ),
                None,
            )
            if short_index is None:
                break

            if short_index == 0:
                neighbor_index = 1
            elif short_index == len(ranges) - 1:
                neighbor_index = short_index - 1
            else:
                color = ranges[short_index][2]
                prev_distance = self._array_rgb_distance(
                    color,
                    ranges[short_index - 1][2],
                )
                next_distance = self._array_rgb_distance(
                    color,
                    ranges[short_index + 1][2],
                )
                neighbor_index = (
                    short_index - 1
                    if prev_distance <= next_distance
                    else short_index + 1
                )

            short = ranges[short_index]
            neighbor = ranges[neighbor_index]
            gap = (
                max(short[0], neighbor[0])
                - min(short[1], neighbor[1])
            )
            if gap > max_merge_gap:
                ranges = ranges[:short_index] + ranges[short_index + 1 :]
                continue

            left_index = min(short_index, neighbor_index)
            right_index = max(short_index, neighbor_index)
            left = ranges[left_index]
            right = ranges[right_index]
            total_count = max(1, left[3] + right[3])
            color = (left[2] * left[3] + right[2] * right[3]) / total_count
            coverage = (left[4] * left[3] + right[4] * right[3]) / total_count
            merged = (left[0], right[1], color, total_count, coverage)
            ranges = ranges[:left_index] + [merged] + ranges[right_index + 1 :]
        return ranges

    def _is_background_shape(
        self, width: int, height: int, image_width: int, image_height: int
    ) -> bool:
        short_side = min(width, height)
        long_side = max(width, height)
        line_thickness = max(2, int(min(image_width, image_height) * 0.012))
        if short_side <= line_thickness and long_side / max(1, short_side) >= 12:
            return False

        width_ratio = width / max(1, image_width)
        height_ratio = height / max(1, image_height)
        area_ratio = (width * height) / max(1, image_width * image_height)
        if area_ratio >= self.min_area_ratio * 3:
            return True
        return (
            width_ratio >= self.min_width_ratio * 2.5
            or height_ratio >= self.min_height_ratio * 3
        )

    def _is_thin_background_band(
        self, width: int, height: int, image_width: int, image_height: int
    ) -> bool:
        if height <= 0 or width <= 0:
            return False
        min_height = self._min_thin_band_height(image_height)
        width_ratio = width / max(1, image_width)
        if height < min_height:
            return False
        if height / max(1, image_height) > self.min_height_ratio * 1.5:
            return False
        return width_ratio >= self.min_width_ratio * 3

    def _min_thin_band_height(self, image_height: int) -> int:
        return max(6, int(image_height * self.min_height_ratio * 0.45))

    def _is_narrow_background_band(
        self, width: int, height: int, image_width: int, image_height: int
    ) -> bool:
        if height <= 0 or width <= 0:
            return False
        min_width = self._min_narrow_band_width(image_width)
        height_ratio = height / max(1, image_height)
        if width < min_width:
            return False
        if width / max(1, image_width) > self.min_width_ratio * 1.5:
            return False
        return height_ratio >= self.min_height_ratio * 8

    def _min_narrow_band_width(self, image_width: int) -> int:
        return max(6, int(image_width * self.min_width_ratio * 0.45))

    def _dedupe_blocks(self, blocks: list[ColorBlock]) -> list[ColorBlock]:
        ordered = sorted(
            blocks,
            key=lambda b: (
                -b.bbox.area,
                b.bbox.y0,
                b.bbox.x0,
                b.bbox.y1,
                b.bbox.x1,
            ),
        )
        result: list[ColorBlock] = []
        for block in ordered:
            duplicate = False
            for old in result:
                if self._rgb_distance(block.color, old.color) > 18:
                    continue
                if self._iou(block.bbox, old.bbox) >= 0.86:
                    duplicate = True
                    break
                if self._overlap_ratio(block.bbox, old.bbox) >= 0.82:
                    duplicate = True
                    break
            if not duplicate:
                result.append(block)
        return sorted(
            result,
            key=lambda b: (b.bbox.y0, b.bbox.x0, b.bbox.y1, b.bbox.x1),
        )

    def _block_color(self, rgb: np.ndarray, mask: np.ndarray) -> tuple[int, int, int]:
        h, w = mask.shape
        pad_x = min(4, max(1, w // 20))
        pad_y = min(3, max(1, h // 20))
        y1 = max(pad_y + 1, h - pad_y)
        x1 = max(pad_x + 1, w - pad_x)
        inner = mask[pad_y:y1, pad_x:x1]
        inner_rgb = rgb[pad_y:y1, pad_x:x1]
        pixels = inner_rgb[inner] if np.any(inner) else rgb[mask]
        if len(pixels) == 0:
            pixels = rgb.reshape(-1, 3)
        color = np.median(pixels, axis=0)
        return tuple(int(round(v)) for v in color)

    def _merge_blocks(
        self,
        blocks: list[ColorBlock],
        *,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
    ) -> list[ColorBlock]:
        pending = sorted(
            blocks,
            key=lambda b: (
                b.bbox.y0,
                b.bbox.x0,
                b.bbox.y1,
                b.bbox.x1,
            ),
        )
        merged: list[ColorBlock] = []
        for block in pending:
            for index, old in enumerate(merged):
                if self._can_merge(
                    old,
                    block,
                    scale_x=scale_x,
                    scale_y=scale_y,
                ):
                    merged[index] = self._merge_pair(old, block)
                    break
            else:
                merged.append(block)

        changed = True
        while changed:
            changed = False
            result: list[ColorBlock] = []
            for block in merged:
                for index, old in enumerate(result):
                    if self._can_merge(
                        old,
                        block,
                        scale_x=scale_x,
                        scale_y=scale_y,
                    ):
                        result[index] = self._merge_pair(old, block)
                        changed = True
                        break
                else:
                    result.append(block)
            merged = result
        return sorted(
            merged,
            key=lambda b: (b.bbox.y0, b.bbox.x0, b.bbox.y1, b.bbox.x1),
        )

    def _merge_pair(self, a: ColorBlock, b: ColorBlock) -> ColorBlock:
        bbox = BBox(
            min(a.bbox.x0, b.bbox.x0),
            min(a.bbox.y0, b.bbox.y0),
            max(a.bbox.x1, b.bbox.x1),
            max(a.bbox.y1, b.bbox.y1),
        )
        total_area = a.area + b.area
        color = tuple(
            int(
                round(
                    (a.color[i] * a.area + b.color[i] * b.area)
                    / max(1, total_area)
                )
            )
            for i in range(3)
        )
        return ColorBlock(
            bbox=bbox,
            color=color,
            area=total_area,
            mask_ratio=min(a.mask_ratio, b.mask_ratio),
        )

    def _can_merge(
        self,
        a: ColorBlock,
        b: ColorBlock,
        *,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
    ) -> bool:
        distance = self._rgb_distance(a.color, b.color)
        y_tolerance = self._scaled_px(2.0, scale_y)
        same_row = (
            abs(a.bbox.y0 - b.bbox.y0) <= y_tolerance
            and abs(a.bbox.y1 - b.bbox.y1) <= y_tolerance
        )
        row_height_ratio = min(a.bbox.height, b.bbox.height) / max(
            1.0, max(a.bbox.height, b.bbox.height)
        )
        x_gap = max(b.bbox.x0 - a.bbox.x1, a.bbox.x0 - b.bbox.x1, 0)
        if (
            same_row
            and row_height_ratio >= 0.8
            and x_gap <= self._scaled_px(3.0, scale_x)
            and distance <= self.vertical_merge_color_distance
        ):
            return True
        col_width_ratio = min(a.bbox.width, b.bbox.width) / max(
            1.0, max(a.bbox.width, b.bbox.width)
        )
        same_col = (
            self._axis_overlap_ratio(a.bbox, b.bbox, axis="x") >= 0.88
            and col_width_ratio >= 0.8
        )
        y_gap = max(b.bbox.y0 - a.bbox.y1, a.bbox.y0 - b.bbox.y1, 0)
        original_height = min(a.bbox.height, b.bbox.height) / max(scale_y, 1e-6)
        max_y_gap = max(3.0, min(12.0, original_height * 0.2)) * scale_y
        max_y_gap = max(1.0, max_y_gap)
        return (
            same_col
            and y_gap <= max_y_gap
            and distance <= self.vertical_merge_color_distance
        )

    def _remove_inner_blocks(
        self,
        blocks: list[ColorBlock],
        *,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
    ) -> list[ColorBlock]:
        result: list[ColorBlock] = []
        tolerance = self._scaled_px(2.0, min(scale_x, scale_y))
        for block in blocks:
            block_area = block.bbox.area
            inner = False
            for other in blocks:
                if other is block or other.bbox.area <= block_area:
                    continue
                if not self._contains(other.bbox, block.bbox, tolerance=tolerance):
                    continue
                if block_area / other.bbox.area <= self.max_inner_area_ratio:
                    span_x = block.bbox.width / max(1.0, other.bbox.width)
                    span_y = block.bbox.height / max(1.0, other.bbox.height)
                    if span_x < 0.5 and span_y < 0.5:
                        inner = True
                        break
            if not inner:
                result.append(block)
        return result

    def _remove_same_color_overlaps(self, blocks: list[ColorBlock]) -> list[ColorBlock]:
        ordered = sorted(
            blocks,
            key=lambda b: (
                -b.bbox.area,
                -b.mask_ratio,
                b.bbox.y0,
                b.bbox.x0,
            ),
        )
        result: list[ColorBlock] = []
        for block in ordered:
            duplicate = False
            for old in result:
                if self._rgb_distance(block.color, old.color) > 24:
                    continue
                if self._overlap_ratio(block.bbox, old.bbox) >= 0.5:
                    duplicate = True
                    break
            if not duplicate:
                result.append(block)
        return result

    def _merge_same_color_fragments(
        self,
        blocks: list[ColorBlock],
        *,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
    ) -> list[ColorBlock]:
        merged = blocks[:]
        changed = True
        while changed:
            changed = False
            for i, block in enumerate(merged):
                for j in range(i + 1, len(merged)):
                    other = merged[j]
                    if not self._can_merge_fragments(
                        block,
                        other,
                        scale_x=scale_x,
                        scale_y=scale_y,
                    ):
                        continue
                    new_block = self._merge_fragment_pair(block, other)
                    merged = (
                        merged[:i]
                        + [new_block]
                        + merged[i + 1 : j]
                        + merged[j + 1 :]
                    )
                    changed = True
                    break
                if changed:
                    break
        return merged

    def _can_merge_fragments(
        self,
        a: ColorBlock,
        b: ColorBlock,
        *,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
    ) -> bool:
        distance = self._rgb_distance(a.color, b.color)
        if distance > 18:
            return False

        union = self._union_bbox(a.bbox, b.bbox)
        if union.area <= 0:
            return False

        inter_area = self._intersection_area(a.bbox, b.bbox)
        fill_ratio = (a.bbox.area + b.bbox.area - inter_area) / union.area
        if fill_ratio < 0.72:
            return False

        x_gap = max(b.bbox.x0 - a.bbox.x1, a.bbox.x0 - b.bbox.x1, 0)
        y_gap = max(b.bbox.y0 - a.bbox.y1, a.bbox.y0 - b.bbox.y1, 0)
        x_overlap = self._axis_overlap_ratio(a.bbox, b.bbox, axis="x")
        y_overlap = self._axis_overlap_ratio(a.bbox, b.bbox, axis="y")
        width_ratio = min(a.bbox.width, b.bbox.width) / max(
            1.0, max(a.bbox.width, b.bbox.width)
        )
        height_ratio = min(a.bbox.height, b.bbox.height) / max(
            1.0, max(a.bbox.height, b.bbox.height)
        )
        original_union_width = union.width / max(scale_x, 1e-6)
        original_height = max(a.bbox.height, b.bbox.height) / max(scale_y, 1e-6)
        row_gap_limit = max(
            self._scaled_px(6.0, scale_x),
            min(
                original_union_width * 0.08 * scale_x,
                original_height * 10 * scale_x,
                180.0 * scale_x,
            ),
        )

        same_band = (
            y_overlap >= 0.65
            and height_ratio >= 0.5
            and x_gap <= row_gap_limit
            and distance <= max(8, self.vertical_merge_color_distance)
        )
        stacked_band = (
            x_overlap >= 0.88
            and width_ratio >= 0.8
            and y_gap <= self._scaled_px(3.0, scale_y)
        )
        return same_band or stacked_band

    def _merge_fragment_pair(self, a: ColorBlock, b: ColorBlock) -> ColorBlock:
        bbox = self._union_bbox(a.bbox, b.bbox)
        inter_area = self._intersection_area(a.bbox, b.bbox)
        area = int(min(bbox.area, a.area + b.area))
        bbox_fill_area = a.bbox.area + b.bbox.area - inter_area
        mask_ratio = min(1.0, bbox_fill_area / max(1.0, bbox.area))
        total_area = max(1, a.area + b.area)
        color = tuple(
            int(round((a.color[i] * a.area + b.color[i] * b.area) / total_area))
            for i in range(3)
        )
        return ColorBlock(
            bbox=bbox,
            color=color,
            area=area,
            mask_ratio=mask_ratio,
        )

    def _sort_blocks(self, blocks: list[ColorBlock]) -> list[ColorBlock]:
        return sorted(
            blocks,
            key=lambda b: (
                -b.bbox.area,
                b.bbox.y0,
                b.bbox.x0,
                b.bbox.y1,
                b.bbox.x1,
            ),
        )

    def _contains(self, outer: BBox, inner: BBox, *, tolerance: float = 0) -> bool:
        return (
            outer.x0 <= inner.x0 + tolerance
            and outer.y0 <= inner.y0 + tolerance
            and outer.x1 >= inner.x1 - tolerance
            and outer.y1 >= inner.y1 - tolerance
        )

    def _iou(self, a: BBox, b: BBox) -> float:
        inter = self._intersection_area(a, b)
        union = a.area + b.area - inter
        if union <= 0:
            return 0.0
        return float(inter / union)

    def _intersection_area(self, a: BBox, b: BBox) -> float:
        x0 = max(a.x0, b.x0)
        y0 = max(a.y0, b.y0)
        x1 = min(a.x1, b.x1)
        y1 = min(a.y1, b.y1)
        if x0 >= x1 or y0 >= y1:
            return 0.0
        return float((x1 - x0) * (y1 - y0))

    def _union_bbox(self, a: BBox, b: BBox) -> BBox:
        return BBox(
            min(a.x0, b.x0),
            min(a.y0, b.y0),
            max(a.x1, b.x1),
            max(a.y1, b.y1),
        )

    def _overlap_ratio(self, a: BBox, b: BBox) -> float:
        x0 = max(a.x0, b.x0)
        y0 = max(a.y0, b.y0)
        x1 = min(a.x1, b.x1)
        y1 = min(a.y1, b.y1)
        if x0 >= x1 or y0 >= y1:
            return 0.0
        inter = (x1 - x0) * (y1 - y0)
        return float(inter / max(1.0, min(a.area, b.area)))

    def _axis_overlap_ratio(
        self, a: BBox, b: BBox, *, axis: Literal["x", "y"]
    ) -> float:
        if axis == "x":
            start = max(a.x0, b.x0)
            end = min(a.x1, b.x1)
            min_len = min(a.width, b.width)
        else:
            start = max(a.y0, b.y0)
            end = min(a.y1, b.y1)
            min_len = min(a.height, b.height)
        if start >= end or min_len <= 0:
            return 0.0
        return float((end - start) / min_len)

    def _remove_plain_background_blocks(
        self, rgb: np.ndarray, blocks: list[ColorBlock]
    ) -> list[ColorBlock]:
        image_area = rgb.shape[0] * rgb.shape[1]
        image_h, image_w = rgb.shape[:2]
        result: list[ColorBlock] = []
        for block in blocks:
            width_ratio = block.bbox.width / max(1, image_w)
            height_ratio = block.bbox.height / max(1, image_h)
            if block.bbox.area / image_area > self.max_plain_background_area_ratio:
                result.append(block)
                continue
            if (
                width_ratio >= self.min_width_ratio * 3
                or height_ratio >= self.min_height_ratio * 10
            ):
                result.append(block)
                continue
            if self._is_on_plain_white_background(rgb, block.bbox):
                continue
            result.append(block)
        return result

    def _is_on_plain_white_background(self, rgb: np.ndarray, bbox: BBox) -> bool:
        h, w = rgb.shape[:2]
        x0, y0, x1, y1 = (int(v) for v in bbox)
        pad = max(4, min(12, int(max(x1 - x0, y1 - y0) * 0.12)))
        ex0 = max(0, x0 - pad)
        ey0 = max(0, y0 - pad)
        ex1 = min(w, x1 + pad)
        ey1 = min(h, y1 + pad)
        if ex0 >= ex1 or ey0 >= ey1:
            return False

        ring_mask = np.ones((ey1 - ey0, ex1 - ex0), dtype=bool)
        ix0 = max(0, x0 - ex0)
        iy0 = max(0, y0 - ey0)
        ix1 = min(ex1 - ex0, x1 - ex0)
        iy1 = min(ey1 - ey0, y1 - ey0)
        ring_mask[iy0:iy1, ix0:ix1] = False
        if not np.any(ring_mask):
            return False

        ring = rgb[ey0:ey1, ex0:ex1][ring_mask]
        hsv = cv2.cvtColor(ring.reshape(-1, 1, 3), cv2.COLOR_RGB2HSV).reshape(-1, 3)
        white = (hsv[:, 2] >= 245) & (hsv[:, 1] <= 22)
        return (
            float(np.count_nonzero(white) / len(white))
            >= self.plain_background_white_ratio
        )

    def _debug_show(
        self,
        rgb: np.ndarray,
        blocks: list[ColorBlock],
        foreground_mask: np.ndarray | None = None,
        background_rgb: np.ndarray | None = None,
    ):
        if background_rgb is None:
            background_rgb = rgb
        rendered = np.full_like(rgb, 255)
        for block in blocks:
            x0, y0, x1, y1 = (int(v) for v in block.bbox)
            color = tuple(int(v) for v in block.color)
            rendered[y0:y1, x0:x1] = color
            cv2.rectangle(rendered, (x0, y0), (x1 - 1, y1 - 1), (0, 0, 0), 1)

        cv2.imshow("color.original", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        if foreground_mask is not None:
            cv2.imshow("color.foreground_mask", foreground_mask.astype(np.uint8) * 255)
        cv2.imshow("color.background", cv2.cvtColor(background_rgb, cv2.COLOR_RGB2BGR))
        cv2.imshow("color.blocks", cv2.cvtColor(rendered, cv2.COLOR_RGB2BGR))
        cv2.waitKey(0)

    def _rgb_distance(
        self, c1: tuple[int, int, int], c2: tuple[int, int, int]
    ) -> float:
        return float(
            np.linalg.norm(np.array(c1, dtype=float) - np.array(c2, dtype=float))
        )

    def _array_rgb_distance(self, c1: np.ndarray, c2: np.ndarray) -> float:
        return float(np.linalg.norm(c1.astype(float) - c2.astype(float)))
