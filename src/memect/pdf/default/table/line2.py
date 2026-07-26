from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
from PIL import Image, ImageDraw


Orientation = Literal["h", "v"]
Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Segment:
    """A high-confidence image-space table line segment.

    Coordinates use image convention: origin at top-left, x rightwards, y downwards.
    For horizontal segments y0 == y1; for vertical segments x0 == x1.  The detected
    stroke thickness is kept separately so callers can decide whether to expand it.
    """

    x0: int
    y0: int
    x1: int
    y1: int
    orientation: Orientation
    score: float = 1.0
    thickness: int = 1

    @property
    def length(self) -> int:
        if self.orientation == "h":
            return abs(self.x1 - self.x0)
        return abs(self.y1 - self.y0)

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.x0, self.y0, self.x1, self.y1


@dataclass
class LineDetectionOptions:
    """Conservative defaults: false positives are worse than missed lines."""

    min_horizontal_length: int | None = None
    min_vertical_length: int | None = None
    min_horizontal_length_ratio: float = 0.12
    min_vertical_length_ratio: float = 0.15
    min_aspect_ratio: float = 12.0
    min_density: float = 0.72
    max_line_thickness: int | None = None
    max_line_thickness_ratio: float = 0.008
    max_merged_line_thickness: int | None = None
    max_merged_line_thickness_ratio: float = 0.025
    horizontal_kernel_ratio: float = 0.025
    vertical_kernel_ratio: float = 0.025
    min_kernel_size: int = 9
    merge_coord_tolerance: int | None = None
    max_bridge_gap: int = 2
    light_value_min: int = 190
    light_saturation_max: int = 75
    max_light_component_area_ratio: float = 0.08
    max_border_light_component_area_ratio: float = 0.02
    dark_value_max: int = 95
    use_edge_mask: bool = False
    edge_threshold: int = 55
    debug_show: bool = True
    debug_panel_max_width: int = 1800
    text_bbox_shrink: int = 1
    max_join_gap: int | None = None
    max_join_gap_ratio: float = 0.006
    parallel_axis_tolerance: int | None = None
    parallel_axis_tolerance_ratio: float = 0.03
    parallel_axis_tolerance_max: int = 12
    parallel_min_overlap_ratio: float = 0.65


@dataclass
class LineDetectionResult:
    width: int
    height: int
    h_lines: list[Segment]
    v_lines: list[Segment]
    debug: dict[str, Any] = field(default_factory=dict)

    @property
    def horizontal(self) -> list[tuple[int, int, int, int]]:
        return [line.as_tuple() for line in self.h_lines]

    @property
    def vertical(self) -> list[tuple[int, int, int, int]]:
        return [line.as_tuple() for line in self.v_lines]


class ImageLineDetector:
    """Detect table horizontal and vertical lines from an image.

    This detector is intentionally conservative.  It only returns line segments
    supported by actual pixels after morphology and strict component filtering.
    It does not infer missing borders, extend lines to a grid, or join large gaps.
    """

    def __init__(self, options: LineDetectionOptions | None = None):
        self.options = options or LineDetectionOptions()

    def detect(
        self,
        image: Image.Image | np.ndarray | str | Path,
        *,
        content_bboxes: list[Box] | None = None,
        debug: bool = False,
    ) -> LineDetectionResult:
        bgr = self._to_bgr(image)
        height, width = bgr.shape[:2]
        options = self.options
        prepared = self._remove_text_regions(bgr, content_bboxes or [])

        candidate_mask = self._candidate_mask(prepared)
        h_mask = self._orientation_mask(candidate_mask, "h", width, height)
        v_mask = self._orientation_mask(candidate_mask, "v", width, height)

        if options.use_edge_mask:
            h_edges, v_edges = self._edge_masks(prepared)
            h_mask = cv2.bitwise_or(
                h_mask, self._orientation_mask(h_edges, "h", width, height)
            )
            v_mask = cv2.bitwise_or(
                v_mask, self._orientation_mask(v_edges, "v", width, height)
            )

        h_lines = self._merge_segments(
            self._segments_from_mask(h_mask, "h", width, height), width, height
        )
        v_lines = self._merge_segments(
            self._segments_from_mask(v_mask, "v", width, height), width, height
        )

        debug_data: dict[str, Any] = {}
        if debug:
            debug_images = self._debug_images(
                bgr,
                prepared,
                candidate_mask,
                h_mask,
                v_mask,
                h_lines,
                v_lines,
                content_bboxes or [],
            )
            debug_data = {
                "image": bgr,
                "prepared": prepared,
                "candidate_mask": candidate_mask,
                "h_mask": h_mask,
                "v_mask": v_mask,
                "content_bboxes": content_bboxes or [],
                "images": debug_images,
            }
            if options.debug_show:
                debug_images["panel"].show()
        return LineDetectionResult(width, height, h_lines, v_lines, debug_data)

    def _remove_text_regions(self, bgr: np.ndarray, boxes: list[Box]) -> np.ndarray:
        if not boxes:
            return bgr
        out = bgr.copy()
        height, width = bgr.shape[:2]
        shrink = self.options.text_bbox_shrink
        for x0, y0, x1, y1 in boxes:
            ix0 = max(0, min(width, int(round(x0)) + shrink))
            iy0 = max(0, min(height, int(round(y0)) + shrink))
            ix1 = max(0, min(width, int(round(x1)) - shrink))
            iy1 = max(0, min(height, int(round(y1)) - shrink))
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            fill = self._local_background(out, ix0, iy0, ix1, iy1)
            cv2.rectangle(out, (ix0, iy0), (ix1, iy1), fill, -1)
        return out

    def _local_background(
        self,
        image: np.ndarray,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        *,
        border: int = 4,
    ) -> tuple[int, int, int]:
        height, width = image.shape[:2]
        bx0 = max(0, x0 - border)
        by0 = max(0, y0 - border)
        bx1 = min(width, x1 + border)
        by1 = min(height, y1 + border)
        region = image[by0:by1, bx0:bx1]
        mask = np.ones(region.shape[:2], dtype=bool)
        mask[y0 - by0 : y1 - by0, x0 - bx0 : x1 - bx0] = False
        pixels = region[mask]
        if len(pixels) == 0:
            return (255, 255, 255)
        median = np.median(pixels, axis=0).astype(int)
        return int(median[0]), int(median[1]), int(median[2])

    def _to_bgr(self, image: Image.Image | np.ndarray | str | Path) -> np.ndarray:
        if isinstance(image, str | Path):
            with Image.open(image) as pil_image:
                return self._to_bgr(pil_image)

        if isinstance(image, Image.Image):
            if image.mode == "RGBA":
                background = Image.new("RGBA", image.size, (255, 255, 255, 255))
                image = Image.alpha_composite(background, image)
            rgb = image.convert("RGB")
            return cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)

        if not isinstance(image, np.ndarray):
            raise TypeError(f"Unsupported image type: {type(image)!r}")

        array = image
        if array.ndim == 2:
            return cv2.cvtColor(array, cv2.COLOR_GRAY2BGR)
        if array.ndim != 3:
            raise ValueError(f"Unsupported image shape: {array.shape!r}")
        if array.shape[2] == 4:
            return cv2.cvtColor(array, cv2.COLOR_BGRA2BGR)
        if array.shape[2] == 3:
            return array.copy()
        raise ValueError(f"Unsupported image shape: {array.shape!r}")

    def _candidate_mask(self, bgr: np.ndarray) -> np.ndarray:
        options = self.options
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        _, saturation, value = cv2.split(hsv)

        light = (
            (value >= options.light_value_min)
            & (saturation <= options.light_saturation_max)
        )
        dark = value <= options.dark_value_max
        light_mask = self._light_stroke_mask(light)
        mask = (light_mask > 0) | dark

        return mask.astype(np.uint8) * 255

    def _light_stroke_mask(self, light: np.ndarray) -> np.ndarray:
        non_light = (~light).astype(np.uint8) * 255
        near_non_light = cv2.dilate(
            non_light,
            cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
            iterations=1,
        )
        return (light & (near_non_light > 0)).astype(np.uint8) * 255

    def _edge_masks(self, bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        options = self.options
        blurred = cv2.GaussianBlur(bgr, (3, 3), 0)
        h_edges = np.zeros(blurred.shape[:2], dtype=np.float32)
        v_edges = np.zeros(blurred.shape[:2], dtype=np.float32)
        for channel in cv2.split(blurred):
            sy = cv2.Sobel(channel, cv2.CV_32F, 0, 1, ksize=3)
            sx = cv2.Sobel(channel, cv2.CV_32F, 1, 0, ksize=3)
            np.maximum(h_edges, np.abs(sy), out=h_edges)
            np.maximum(v_edges, np.abs(sx), out=v_edges)

        h_mask = (h_edges >= options.edge_threshold).astype(np.uint8) * 255
        v_mask = (v_edges >= options.edge_threshold).astype(np.uint8) * 255
        return h_mask, v_mask

    def _orientation_mask(
        self,
        mask: np.ndarray,
        orientation: Orientation,
        width: int,
        height: int,
    ) -> np.ndarray:
        options = self.options
        if orientation == "h":
            kernel_len = self._kernel_size(width, options.horizontal_kernel_ratio)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_len, 1))
            close_kernel = cv2.getStructuringElement(
                cv2.MORPH_RECT, (max(1, options.max_bridge_gap + 1), 1)
            )
        else:
            kernel_len = self._kernel_size(height, options.vertical_kernel_ratio)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, kernel_len))
            close_kernel = cv2.getStructuringElement(
                cv2.MORPH_RECT, (1, max(1, options.max_bridge_gap + 1))
            )

        out = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        if options.max_bridge_gap > 0:
            out = cv2.morphologyEx(out, cv2.MORPH_CLOSE, close_kernel)
        return out

    def _segments_from_mask(
        self,
        mask: np.ndarray,
        orientation: Orientation,
        width: int,
        height: int,
    ) -> list[Segment]:
        options = self.options
        min_length = self._min_length(orientation, width, height)
        max_thickness = self._max_thickness(width, height)
        segments: list[Segment] = []

        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        for label in range(1, count):
            x, y, w, h, area = (int(v) for v in stats[label])
            if w <= 0 or h <= 0 or area <= 0:
                continue

            length = w if orientation == "h" else h
            thickness = h if orientation == "h" else w
            ratio = length / max(thickness, 1)
            density = area / float(w * h)

            if length < min_length:
                continue
            if thickness > max_thickness:
                continue
            if ratio < options.min_aspect_ratio:
                continue
            if density < options.min_density:
                continue

            component_mask = labels[y : y + h, x : x + w] == label
            straightness = self._straightness(component_mask, orientation)
            if straightness < 0.82:
                continue

            score = min(1.0, density) * min(1.0, ratio / options.min_aspect_ratio)
            score *= straightness
            if orientation == "h":
                cy = int(round(y + (h - 1) / 2))
                segments.append(Segment(x, cy, x + w - 1, cy, "h", score, h))
            else:
                cx = int(round(x + (w - 1) / 2))
                segments.append(Segment(cx, y, cx, y + h - 1, "v", score, w))
        return segments

    def _straightness(self, component_mask: np.ndarray, orientation: Orientation) -> float:
        if orientation == "h":
            projection = component_mask.sum(axis=1)
            ideal_length = component_mask.shape[1]
        else:
            projection = component_mask.sum(axis=0)
            ideal_length = component_mask.shape[0]
        if ideal_length <= 0:
            return 0.0
        return float(projection.max() / ideal_length)

    def _merge_segments(
        self,
        segments: list[Segment],
        width: int,
        height: int,
    ) -> list[Segment]:
        if not segments:
            return []

        orientation = segments[0].orientation
        coord_tol = self._merge_coord_tolerance(width, height)
        gap_tol = self.options.max_bridge_gap

        if orientation == "h":
            key = lambda line: (line.y0, line.x0)
            coord = lambda line: line.y0
            start = lambda line: min(line.x0, line.x1)
            end = lambda line: max(line.x0, line.x1)
        else:
            key = lambda line: (line.x0, line.y0)
            coord = lambda line: line.x0
            start = lambda line: min(line.y0, line.y1)
            end = lambda line: max(line.y0, line.y1)

        merged: list[Segment] = []
        for line in sorted(segments, key=key):
            if not merged:
                merged.append(line)
                continue

            prev = merged[-1]
            same_axis = abs(coord(line) - coord(prev)) <= coord_tol
            close_span = start(line) <= end(prev) + gap_tol + 1
            if not same_axis or not close_span:
                merged.append(line)
                continue

            prev_len = max(prev.length, 1)
            line_len = max(line.length, 1)
            new_coord = int(
                round(
                    (coord(prev) * prev_len + coord(line) * line_len)
                    / (prev_len + line_len)
                )
            )
            new_start = min(start(prev), start(line))
            new_end = max(end(prev), end(line))
            new_score = min(prev.score, line.score)
            new_thickness = max(prev.thickness, line.thickness)
            if orientation == "h":
                merged[-1] = Segment(
                    new_start, new_coord, new_end, new_coord, "h", new_score, new_thickness
                )
            else:
                merged[-1] = Segment(
                    new_coord,
                    new_start,
                    new_coord,
                    new_end,
                    "v",
                    new_score,
                    new_thickness,
                )

        merged = self._join_nearly_connected_segments(merged, width, height)
        merged = self._merge_parallel_overlapping_segments(merged, width, height)
        merged = self._drop_contained_parallel_segments(merged, width, height)
        return self._filter_merged_segments(merged, width, height)

    def _join_nearly_connected_segments(
        self,
        segments: list[Segment],
        width: int,
        height: int,
    ) -> list[Segment]:
        if not segments:
            return []

        orientation = segments[0].orientation
        coord_tol = self._merge_coord_tolerance(width, height)
        join_gap = self._max_join_gap(width, height, orientation)

        if orientation == "h":
            key = lambda line: (line.y0, line.x0)
            coord = lambda line: line.y0
            start = lambda line: min(line.x0, line.x1)
            end = lambda line: max(line.x0, line.x1)

            def make_line(
                axis: int, span_start: int, span_end: int, score: float, thickness: int
            ) -> Segment:
                return Segment(span_start, axis, span_end, axis, "h", score, thickness)

        else:
            key = lambda line: (line.x0, line.y0)
            coord = lambda line: line.x0
            start = lambda line: min(line.y0, line.y1)
            end = lambda line: max(line.y0, line.y1)

            def make_line(
                axis: int, span_start: int, span_end: int, score: float, thickness: int
            ) -> Segment:
                return Segment(axis, span_start, axis, span_end, "v", score, thickness)

        changed = True
        result = sorted(segments, key=key)
        while changed:
            changed = False
            joined: list[Segment] = []
            for line in result:
                if not joined:
                    joined.append(line)
                    continue

                prev = joined[-1]
                same_axis = abs(coord(line) - coord(prev)) <= coord_tol
                gap = start(line) - end(prev)
                if not same_axis or gap > join_gap:
                    joined.append(line)
                    continue

                prev_len = max(prev.length, 1)
                line_len = max(line.length, 1)
                new_coord = int(
                    round(
                        (coord(prev) * prev_len + coord(line) * line_len)
                        / (prev_len + line_len)
                    )
                )
                joined[-1] = make_line(
                    new_coord,
                    min(start(prev), start(line)),
                    max(end(prev), end(line)),
                    min(prev.score, line.score),
                    max(prev.thickness, line.thickness),
                )
                changed = True
            result = joined
        return result

    def _merge_parallel_overlapping_segments(
        self,
        segments: list[Segment],
        width: int,
        height: int,
    ) -> list[Segment]:
        if not segments:
            return []

        orientation = segments[0].orientation
        axis_tol = self._parallel_axis_tolerance(width, height, orientation)
        min_overlap_ratio = self.options.parallel_min_overlap_ratio

        if orientation == "h":
            key = lambda line: (line.y0, line.x0)
            coord = lambda line: line.y0
            start = lambda line: min(line.x0, line.x1)
            end = lambda line: max(line.x0, line.x1)

            def make_line(
                axis: int, span_start: int, span_end: int, score: float, thickness: int
            ) -> Segment:
                return Segment(span_start, axis, span_end, axis, "h", score, thickness)

        else:
            key = lambda line: (line.x0, line.y0)
            coord = lambda line: line.x0
            start = lambda line: min(line.y0, line.y1)
            end = lambda line: max(line.y0, line.y1)

            def make_line(
                axis: int, span_start: int, span_end: int, score: float, thickness: int
            ) -> Segment:
                return Segment(axis, span_start, axis, span_end, "v", score, thickness)

        def axis_bounds(line: Segment) -> tuple[float, float]:
            half = max(line.thickness - 1, 0) / 2.0
            axis = coord(line)
            return axis - half, axis + half

        merged: list[Segment] = []
        for line in sorted(segments, key=lambda item: item.length, reverse=True):
            line_start = start(line)
            line_end = end(line)
            line_axis0, line_axis1 = axis_bounds(line)
            target_index: int | None = None
            for i, other in enumerate(merged):
                other_axis0, other_axis1 = axis_bounds(other)
                axis_gap = max(line_axis0 - other_axis1, other_axis0 - line_axis1, 0)
                if axis_gap > axis_tol:
                    continue
                overlap = min(line_end, end(other)) - max(line_start, start(other))
                min_length = max(1, min(line.length, other.length))
                if overlap / min_length >= min_overlap_ratio:
                    target_index = i
                    break

            if target_index is None:
                merged.append(line)
                continue

            other = merged[target_index]
            other_axis0, other_axis1 = axis_bounds(other)
            axis0 = min(line_axis0, other_axis0)
            axis1 = max(line_axis1, other_axis1)
            new_coord = int((axis0 + axis1) / 2.0 + 0.5)
            new_thickness = max(
                line.thickness,
                other.thickness,
                int(axis1 - axis0 + 1.5),
            )
            merged[target_index] = make_line(
                new_coord,
                min(line_start, start(other)),
                max(line_end, end(other)),
                min(line.score, other.score),
                new_thickness,
            )
        return sorted(merged, key=key)

    def _drop_contained_parallel_segments(
        self,
        segments: list[Segment],
        width: int,
        height: int,
    ) -> list[Segment]:
        if not segments:
            return []

        orientation = segments[0].orientation
        coord_tol = self._merge_coord_tolerance(width, height)

        if orientation == "h":
            key = lambda line: (line.y0, line.x0)
            coord = lambda line: line.y0
            start = lambda line: min(line.x0, line.x1)
            end = lambda line: max(line.x0, line.x1)
        else:
            key = lambda line: (line.x0, line.y0)
            coord = lambda line: line.x0
            start = lambda line: min(line.y0, line.y1)
            end = lambda line: max(line.y0, line.y1)

        kept: list[Segment] = []
        for line in sorted(segments, key=lambda item: item.length, reverse=True):
            line_start = start(line)
            line_end = end(line)
            drop = False
            for other in kept:
                if abs(coord(line) - coord(other)) > coord_tol:
                    continue
                overlap = min(line_end, end(other)) - max(line_start, start(other))
                if overlap >= line.length * 0.85:
                    drop = True
                    break
            if not drop:
                kept.append(line)
        return sorted(kept, key=key)

    def _filter_merged_segments(
        self,
        segments: list[Segment],
        width: int,
        height: int,
    ) -> list[Segment]:
        if not segments:
            return []

        result: list[Segment] = []
        for line in segments:
            if line.length < self._min_length(line.orientation, width, height):
                continue
            if line.thickness > self._max_merged_thickness(width, height):
                continue
            result.append(line)
        return result

    def _kernel_size(self, size: int, ratio: float) -> int:
        value = max(self.options.min_kernel_size, int(round(size * ratio)))
        return max(1, value)

    def _min_length(self, orientation: Orientation, width: int, height: int) -> int:
        options = self.options
        if orientation == "h":
            if options.min_horizontal_length is not None:
                return options.min_horizontal_length
            return max(12, int(round(width * options.min_horizontal_length_ratio)))

        if options.min_vertical_length is not None:
            return options.min_vertical_length
        return max(12, int(round(height * options.min_vertical_length_ratio)))

    def _max_thickness(self, width: int, height: int) -> int:
        options = self.options
        if options.max_line_thickness is not None:
            return options.max_line_thickness
        size = min(width, height)
        return max(2, min(10, int(round(size * options.max_line_thickness_ratio))))

    def _max_merged_thickness(self, width: int, height: int) -> int:
        options = self.options
        if options.max_merged_line_thickness is not None:
            return options.max_merged_line_thickness
        size = min(width, height)
        return max(
            self._max_thickness(width, height),
            min(18, int(round(size * options.max_merged_line_thickness_ratio))),
        )

    def _merge_coord_tolerance(self, width: int, height: int) -> int:
        if self.options.merge_coord_tolerance is not None:
            return self.options.merge_coord_tolerance
        return 3

    def _max_join_gap(self, width: int, height: int, orientation: Orientation) -> int:
        if self.options.max_join_gap is not None:
            return self.options.max_join_gap
        size = width if orientation == "h" else height
        return max(
            self.options.max_bridge_gap,
            int(round(size * self.options.max_join_gap_ratio)),
        )

    def _parallel_axis_tolerance(
        self, width: int, height: int, orientation: Orientation
    ) -> int:
        if self.options.parallel_axis_tolerance is not None:
            return self.options.parallel_axis_tolerance
        size = height if orientation == "h" else width
        return max(
            self._merge_coord_tolerance(width, height),
            min(
                self.options.parallel_axis_tolerance_max,
                int(round(size * self.options.parallel_axis_tolerance_ratio)),
            ),
        )

    def _debug_images(
        self,
        bgr: np.ndarray,
        prepared: np.ndarray,
        candidate_mask: np.ndarray,
        h_mask: np.ndarray,
        v_mask: np.ndarray,
        h_lines: list[Segment],
        v_lines: list[Segment],
        content_bboxes: list[Box],
    ) -> dict[str, Image.Image]:
        original = self._bgr_to_pil(bgr)
        prepared_image = self._bgr_to_pil(prepared)
        content_bboxes_image = self._draw_boxes(bgr, content_bboxes)
        candidate = self._mask_to_pil(candidate_mask)
        h_mask_image = self._mask_to_pil(h_mask)
        v_mask_image = self._mask_to_pil(v_mask)
        h_result = self._overlay_lines(bgr, h_lines, [])
        v_result = self._overlay_lines(bgr, [], v_lines)
        result = self._overlay_lines(bgr, h_lines, v_lines)

        images = {
            "original": original,
            "content_bboxes": content_bboxes_image,
            "prepared": prepared_image,
            "candidate_mask": candidate,
            "h_mask": h_mask_image,
            "v_mask": v_mask_image,
            "h_result": h_result,
            "v_result": v_result,
            "result": result,
        }
        images["panel"] = self._make_debug_panel(images)
        return images

    def _overlay_lines(
        self,
        bgr: np.ndarray,
        h_lines: list[Segment],
        v_lines: list[Segment],
    ) -> Image.Image:
        canvas = bgr.copy()
        for line in h_lines:
            cv2.line(
                canvas,
                (line.x0, line.y0),
                (line.x1, line.y1),
                (0, 220, 0),
                max(2, line.thickness),
            )
        for line in v_lines:
            cv2.line(
                canvas,
                (line.x0, line.y0),
                (line.x1, line.y1),
                (0, 0, 255),
                max(2, line.thickness),
            )
        return self._bgr_to_pil(canvas)

    def _draw_boxes(self, bgr: np.ndarray, boxes: list[Box]) -> Image.Image:
        canvas = bgr.copy()
        for x0, y0, x1, y1 in boxes:
            cv2.rectangle(
                canvas,
                (int(round(x0)), int(round(y0))),
                (int(round(x1)), int(round(y1))),
                (0, 165, 255),
                1,
            )
        return self._bgr_to_pil(canvas)

    def _make_debug_panel(self, images: dict[str, Image.Image]) -> Image.Image:
        items = [
            ("original", images["original"]),
            ("content bboxes", images["content_bboxes"]),
            ("text removed", images["prepared"]),
            ("candidate mask", images["candidate_mask"]),
            ("horizontal mask", images["h_mask"]),
            ("vertical mask", images["v_mask"]),
            ("horizontal result", images["h_result"]),
            ("vertical result", images["v_result"]),
            ("final result", images["result"]),
        ]
        thumb_width = max(260, min(520, self.options.debug_panel_max_width // 3))
        label_height = 28
        gap = 12
        thumbs: list[tuple[str, Image.Image]] = []
        for label, image in items:
            thumb = image.copy()
            ratio = thumb_width / max(thumb.width, 1)
            thumb = thumb.resize(
                (thumb_width, max(1, int(round(thumb.height * ratio)))),
                Image.Resampling.BILINEAR,
            )
            thumbs.append((label, thumb))

        cols = 3
        rows = (len(thumbs) + cols - 1) // cols
        cell_height = max(thumb.height for _, thumb in thumbs) + label_height
        panel_width = cols * thumb_width + (cols + 1) * gap
        panel_height = rows * cell_height + (rows + 1) * gap
        panel = Image.new("RGB", (panel_width, panel_height), (245, 245, 245))
        draw = ImageDraw.Draw(panel)

        for index, (label, thumb) in enumerate(thumbs):
            row = index // cols
            col = index % cols
            x = gap + col * (thumb_width + gap)
            y = gap + row * (cell_height + gap)
            draw.text((x, y), label, fill=(20, 20, 20))
            panel.paste(thumb, (x, y + label_height))

        if panel.width > self.options.debug_panel_max_width:
            ratio = self.options.debug_panel_max_width / panel.width
            panel = panel.resize(
                (
                    self.options.debug_panel_max_width,
                    max(1, int(round(panel.height * ratio))),
                ),
                Image.Resampling.BILINEAR,
            )
        return panel

    @staticmethod
    def _bgr_to_pil(bgr: np.ndarray) -> Image.Image:
        return Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))

    @staticmethod
    def _mask_to_pil(mask: np.ndarray) -> Image.Image:
        if mask.ndim != 2:
            raise ValueError(f"Expected 2D mask, got shape {mask.shape!r}")
        return Image.fromarray(mask).convert("RGB")


def detect_lines(
    image: Image.Image | np.ndarray | str | Path,
    *,
    options: LineDetectionOptions | None = None,
    content_bboxes: list[Box] | None = None,
    debug: bool = False,
) -> LineDetectionResult:
    return ImageLineDetector(options).detect(
        image,
        content_bboxes=content_bboxes,
        debug=debug,
    )
