import cv2
import numpy as np
from collections import defaultdict


def _bgr_to_hex(bgr: np.ndarray) -> str:
    b, g, r = int(bgr[0]), int(bgr[1]), int(bgr[2])
    return f"#{r:02X}{g:02X}{b:02X}"


def _is_white(bgr: np.ndarray | tuple[int, int, int], sat_thresh: int = 25, val_thresh: int = 215) -> bool:
    hsv = cv2.cvtColor(np.uint8([[bgr]]), cv2.COLOR_BGR2HSV)[0][0]
    return int(hsv[1]) < sat_thresh and int(hsv[2]) > val_thresh


def _quantize(img: np.ndarray, k: int = 6) -> np.ndarray:
    pixels = img.reshape(-1, 3).astype(np.float32)
    _, labels, centers = cv2.kmeans(
        pixels, k, None,
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0),
        5, cv2.KMEANS_PP_CENTERS
    )
    centers = np.uint8(centers)
    return centers[labels.flatten()].reshape(img.shape)


def _dominant(pixels: np.ndarray, total: int, thresh: float = 0.1) -> str | None:
    non_white: list[tuple[int, ...]] = [tuple(p.tolist()) for p in pixels if not _is_white(p)]
    if not non_white:
        return None
    counts: dict[tuple[int, ...], int] = defaultdict(int)
    for p in non_white:
        counts[p] += 1
    best = max(counts, key=counts.__getitem__)
    return _bgr_to_hex(best) if counts[best] / total >= thresh else None


def _merge_runs(colors: list[str | None]) -> list[tuple[str, int, int]]:
    runs: list[tuple[str, int, int]] = []
    i = 0
    while i < len(colors):
        c = colors[i]
        if c is None:
            i += 1
            continue
        j = i + 1
        while j < len(colors) and colors[j] == c:
            j += 1
        runs.append((c, i, j - 1))
        i = j
    return runs


class TableColorDetector:
    def detect(self, img: np.ndarray, debug: bool = False) -> list[tuple[str, tuple[int, int, int, int]]]:
        """返回 [(hex_color, (x, y, w, h)), ...]，仅含有背景色的区域"""
        q = _quantize(img)
        h, w = q.shape[:2]

        row_colors: list[str | None] = [_dominant(q[y].reshape(-1, 3), w) for y in range(h)]
        col_colors: list[str | None] = [_dominant(q[:, x].reshape(-1, 3), h) for x in range(w)]

        row_runs = [(c, y1, y2) for c, y1, y2 in _merge_runs(row_colors) if y2 - y1 >= max(8, h * 0.02)]
        col_runs = [(c, x1, x2) for c, x1, x2 in _merge_runs(col_colors) if x2 - x1 >= max(8, w * 0.02)]

        result: list[tuple[str, tuple[int, int, int, int]]] = []

        for color, y1, y2 in row_runs:
            result.append((color, (0, y1, w, y2 - y1 + 1)))

        row_colors_set = {c for c, _, _ in row_runs}
        for color, x1, x2 in col_runs:
            if color not in row_colors_set:
                result.append((color, (x1, 0, x2 - x1 + 1, h)))

        if debug:
            blank = np.ones((h, w, 3), dtype=np.uint8) * 255
            for hex_color, (x, y, bw, bh) in result:
                r = int(hex_color[1:3], 16)
                g = int(hex_color[3:5], 16)
                b = int(hex_color[5:7], 16)
                cv2.rectangle(blank, (x, y), (x + bw, y + bh), (b, g, r), -1)
                cv2.putText(blank, hex_color, (x + 4, y + 16),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1)
            cv2.imshow("original | result", np.hstack([img, blank]))
            cv2.waitKey(0)
            cv2.destroyAllWindows()

        return result
