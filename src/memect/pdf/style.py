

from typing import Any

from .base import KCell, KColor, KDocument, KRect, KTable


class TableStyleParser:
    def __init__(self):
        super().__init__()
    
    def parse(self,doc:KDocument):
        for page in doc.working_pages:
            for table in page.objects:
                if isinstance(table,KTable):
                    #如果是layout，查找单元格是否包含表格
                    #不再层层递归下去
                    if table.chart_layout is not None:
                        pass
                    elif table.is_layout():
                        for cell in table.cells:
                            for obj in cell.objects:
                                if isinstance(obj,KTable):
                                    self._parse_table(obj)
                    else:
                        self._parse_table(table)
        pass
    def _parse_table(self,table:KTable):
        
        #根据pdf的填充矩形来判断
        #使用图片视觉的方式来判断
        #可以精确到单元格（无边框表格不一定准确，因为单元格的计算可能偏大偏小）

        #判断表格原来是图片还是pdf
        #简单的就是根据页面类型判断，复杂的就是根据当前表格的区域（可能是pdf页面中的一个表格图片）
        def is_ocr(table:KTable)->bool:
            if table.page.is_pdf():
                #表示为纯PDF
                return False
            elif table.page.is_image():
                return True
            elif table.page.is_hybrid():
                #局部图片
                figures = table.bbox.get(table.page.pdf_figures,ratio=0.8)
                if len(figures)==1:
                    return True
                return False
            else:
                return False
        
        if is_ocr(table):
            detector = _CellStyleDetector()
            for cell in table.cells:
                img = cell.page.crop(cell.bbox)
                assert img is not None
                result = detector.detect(img)
                if result['confidence']>0.5:
                    cell.color=result['color']
                    cell.font_color = result['font_color']
        else:
            for cell in table.cells:
                self._parse_cell(cell)
    
    def _parse_cell(self,cell:KCell):
        #rects  = cell.bbox.get(cell.page.pdf_rects,ratio=0.5)
        rects:list[KRect]=[]
        for rect in cell.page.pdf_rects:
            xb = cell.bbox.intersect(rect.bbox)
            #因为背景矩形可能比cell大
            area=min(cell.bbox.area,rect.bbox.area)
            if xb and xb.area/area>=0.5:
                rects.append(rect)

        if not rects:
            return

        from shapely.geometry import Polygon
        from shapely.ops import unary_union

        #简单的就是获得总的面积，根据颜色划分？
        colors:dict[KColor,list[KRect]]={}
        for rect in rects:
            group = colors.setdefault(rect.color,[])
            group.append(rect)
        
        areas:list[tuple[Any,float]]=[]
        for color,group in colors.items():
            #这是简单的算法，不考虑重叠
            polys:list[Polygon]=[]
            for rect in group:
                bbox = rect.bbox
                poly = Polygon(((bbox.x0,bbox.y0),(bbox.x1,bbox.y0),(bbox.x1,bbox.y1),(bbox.x0,bbox.y1)))
                polys.append(poly)
            u = unary_union(polys)
            areas.append((color,u.area))
        #排序
        areas.sort(key=lambda item:item[1],reverse=True)
        color,area = areas[0]
        if area/cell.bbox.area>=0.5:
            cell.color = color
        else:
            pass

    
    

class _CellStyleDetector:
    def __init__(
        self,
        *,
        input_mode: str = "bgr",
        max_detect_size: tuple[int, int] | None = (480, 480),
        quantize_step: int = 8,
        background_distance: float = 22.0,
        min_foreground_ratio: float = 0.001,
    ):
        super().__init__()
        self.input_mode = input_mode
        self.max_detect_size = max_detect_size
        self.quantize_step = max(1, quantize_step)
        self.background_distance = background_distance
        self.min_foreground_ratio = min_foreground_ratio
    
    def detect(self,img:Any)->dict[str,Any]:
        rgb = self._to_rgb(img)
        if rgb.size == 0:
            return self._empty_result()

        rgb = self._resize_for_detection(rgb)
        if rgb.size == 0:
            return self._empty_result()

        bg_rgb, bg_ratio, confidence = self._detect_background(rgb)
        font_rgb, font_ratio = self._detect_font_color(rgb, bg_rgb)
        border_rgb, border_ratio = self._detect_border_color(
            rgb,
            bg_rgb,
            font_rgb,
        )

        color = self._to_kcolor(bg_rgb)
        font_color = self._to_kcolor(font_rgb) if font_rgb is not None else None
        border_color = self._to_kcolor(border_rgb) if border_rgb is not None else None
        return {
            "color": color,
            "background_color": color,
            "font_color": font_color,
            "text_color": font_color,
            "border_color": border_color,
            "background_ratio": round(bg_ratio, 4),
            "font_ratio": round(font_ratio, 4),
            "border_ratio": round(border_ratio, 4),
            "confidence": round(confidence, 4),
        }

    def _empty_result(self)->dict[str,Any]:
        return {
            "color": None,
            "background_color": None,
            "font_color": None,
            "text_color": None,
            "border_color": None,
            "background_ratio": 0.0,
            "font_ratio": 0.0,
            "border_ratio": 0.0,
            "confidence": 0.0,
        }

    def _to_rgb(self,img:Any)->Any:
        import os
        from io import BytesIO

        import cv2
        import numpy as np
        from PIL import Image

        if isinstance(img, Image.Image):
            return self._pil_to_rgb(img)

        if isinstance(img, bytes):
            with Image.open(BytesIO(img)) as image:
                return self._pil_to_rgb(image)

        if isinstance(img, (str, os.PathLike)):
            with Image.open(img) as image:
                return self._pil_to_rgb(image)

        arr = self._as_uint8(np.asarray(img))
        if arr.ndim == 2:
            return np.stack((arr, arr, arr), axis=2)
        if arr.ndim != 3:
            raise ValueError(f"unsupported image shape: {arr.shape}")

        channels = arr.shape[2]
        if channels == 1:
            gray = arr[:, :, 0]
            return np.stack((gray, gray, gray), axis=2)
        if channels == 3:
            if self.input_mode == "rgb":
                return arr.copy()
            return cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
        if channels == 4:
            if self.input_mode == "rgb":
                rgb = arr[:, :, :3]
            else:
                rgb = cv2.cvtColor(arr, cv2.COLOR_BGRA2RGB)
            alpha = arr[:, :, 3:4].astype(np.float32) / 255.0
            return np.clip(rgb * alpha + 255 * (1 - alpha), 0, 255).astype(np.uint8)

        raise ValueError(f"unsupported image channels: {channels}")

    def _pil_to_rgb(self,image:Any)->Any:
        import numpy as np
        from PIL import Image

        if "A" not in image.getbands():
            return np.array(image.convert("RGB"))

        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return np.array(Image.alpha_composite(background, rgba).convert("RGB"))

    def _as_uint8(self,arr:Any)->Any:
        import numpy as np

        if arr.dtype == np.uint8:
            return arr

        if np.issubdtype(arr.dtype, np.floating):
            arr = np.nan_to_num(arr, nan=0, posinf=255, neginf=0)
            if arr.size and float(arr.max()) <= 1.0:
                arr = arr * 255

        return np.clip(arr, 0, 255).astype(np.uint8)

    def _resize_for_detection(self,rgb:Any)->Any:
        import cv2

        if self.max_detect_size is None:
            return rgb

        h, w = rgb.shape[:2]
        max_w, max_h = self.max_detect_size
        if h == 0 or w == 0 or max_w <= 0 or max_h <= 0:
            return rgb

        scale = min(1.0, max_w / w, max_h / h)
        if scale >= 1.0:
            return rgb

        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        return cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)

    def _detect_background(self,rgb:Any)->tuple[tuple[int,int,int],float,float]:
        import numpy as np

        h, w = rgb.shape[:2]
        if h == 0 or w == 0:
            return (255, 255, 255), 0.0, 0.0

        quantized = self._quantize(rgb)
        flat = quantized.reshape(-1, 3)
        colors, counts = np.unique(flat, axis=0, return_counts=True)
        if len(colors) == 0:
            return (255, 255, 255), 0.0, 0.0

        edge_mask = self._edge_mask(h, w)
        edge_colors = quantized[edge_mask]
        edge_codes, edge_counts = np.unique(
            self._color_codes(edge_colors),
            return_counts=True,
        )
        edge_map = {
            int(code): int(count)
            for code, count in zip(edge_codes, edge_counts, strict=False)
        }

        total = max(1, h * w)
        edge_total = max(1, int(edge_mask.sum()))
        best_index = int(np.argmax(counts))
        best_score = -1.0
        best_edge_ratio = 0.0
        order = np.argsort(counts)[::-1]

        for index in order[: min(24, len(order))]:
            index = int(index)
            color = colors[index]
            global_ratio = float(counts[index]) / total
            edge_ratio = edge_map.get(self._color_code(color), 0) / edge_total
            score = global_ratio + edge_ratio * 0.6
            if score > best_score:
                best_score = score
                best_index = index
                best_edge_ratio = edge_ratio

        center = colors[best_index]
        center_mask = np.all(quantized == center, axis=2)
        pixels = rgb[center_mask]
        if len(pixels) == 0:
            bg_rgb = tuple(int(v) for v in center)
        else:
            bg_rgb = tuple(int(v) for v in np.median(pixels, axis=0))

        bg_mask = self._color_distance(rgb, bg_rgb) <= self.background_distance
        bg_ratio = float(bg_mask.mean())
        confidence = min(1.0, bg_ratio + best_edge_ratio * 0.35 + 0.15)
        return bg_rgb, bg_ratio, confidence

    def _detect_font_color(
        self,
        rgb:Any,
        bg_rgb:tuple[int,int,int],
    )->tuple[tuple[int,int,int]|None,float]:
        import numpy as np

        h, w = rgb.shape[:2]
        total = max(1, h * w)
        fg_mask = self._color_distance(rgb, bg_rgb) > self.background_distance
        if not np.any(fg_mask):
            return None, 0.0

        inner_mask = fg_mask.copy()
        band = max(1, min(h, w) // 25)
        if h > band * 2 and w > band * 2:
            inner_mask[:band, :] = False
            inner_mask[-band:, :] = False
            inner_mask[:, :band] = False
            inner_mask[:, -band:] = False
            if int(inner_mask.sum()) >= max(2, int(total * self.min_foreground_ratio)):
                fg_mask = inner_mask

        fg_mask = self._clean_mask(
            fg_mask,
            min_area=max(2, int(total * 0.0003)),
        )
        color, local_ratio = self._dominant_color(
            rgb,
            fg_mask,
            ignore_colors=(bg_rgb,),
        )
        if color is None:
            return None, 0.0
        if self._rgb_distance(color, bg_rgb) <= self.background_distance:
            return None, 0.0

        ratio = local_ratio * (float(fg_mask.sum()) / total)
        if ratio < self.min_foreground_ratio:
            return None, 0.0
        return color, ratio

    def _detect_border_color(
        self,
        rgb:Any,
        bg_rgb:tuple[int,int,int],
        font_rgb:tuple[int,int,int]|None,
    )->tuple[tuple[int,int,int]|None,float]:
        import numpy as np

        h, w = rgb.shape[:2]
        total = max(1, h * w)
        edge_mask = self._edge_mask(h, w)
        mask = edge_mask & (
            self._color_distance(rgb, bg_rgb) > self.background_distance
        )

        if font_rgb is not None:
            non_font = mask & (
                self._color_distance(rgb, font_rgb) > self.background_distance
            )
            if int(non_font.sum()) >= max(2, int(total * 0.0005)):
                mask = non_font

        edge_pixels = max(1, int(edge_mask.sum()))
        if int(mask.sum()) / edge_pixels < 0.03:
            return None, 0.0

        color, local_ratio = self._dominant_color(
            rgb,
            mask,
            ignore_colors=(bg_rgb,),
        )
        if color is None:
            return None, 0.0

        ratio = local_ratio * (float(mask.sum()) / total)
        return color, ratio

    def _dominant_color(
        self,
        rgb:Any,
        mask:Any,
        *,
        ignore_colors:tuple[tuple[int,int,int],...] = (),
    )->tuple[tuple[int,int,int]|None,float]:
        import numpy as np

        pixels = rgb[mask]
        if len(pixels) == 0:
            return None, 0.0

        quantized = self._quantize(pixels)
        colors, counts = np.unique(quantized, axis=0, return_counts=True)
        if len(colors) == 0:
            return None, 0.0

        order = np.argsort(counts)[::-1]
        for index in order:
            index = int(index)
            center = colors[index]
            group_mask = np.all(quantized == center, axis=1)
            group = pixels[group_mask]
            if len(group) == 0:
                continue
            color = tuple(int(v) for v in np.median(group, axis=0))
            if any(
                self._rgb_distance(color, ignored) <= self.background_distance
                for ignored in ignore_colors
            ):
                continue
            return color, float(counts[index]) / len(pixels)

        return None, 0.0

    def _clean_mask(self,mask:Any,*,min_area:int)->Any:
        import cv2
        import numpy as np

        mask_u8 = mask.astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(
            mask_u8,
            connectivity=8,
        )
        if count <= 1:
            return mask

        cleaned = np.zeros_like(mask, dtype=bool)
        for index in range(1, count):
            area = int(stats[index, cv2.CC_STAT_AREA])
            if area >= min_area:
                cleaned[labels == index] = True

        return cleaned if np.any(cleaned) else mask

    def _edge_mask(self,h:int,w:int)->Any:
        import numpy as np

        band = max(1, min(h, w) // 30)
        band = min(band, h, w)
        mask = np.zeros((h, w), dtype=bool)
        mask[:band, :] = True
        mask[-band:, :] = True
        mask[:, :band] = True
        mask[:, -band:] = True
        return mask

    def _quantize(self,colors:Any)->Any:
        import numpy as np

        step = self.quantize_step
        data = colors.astype(np.uint16)
        data = data // step * step + step // 2
        return np.clip(data, 0, 255).astype(np.uint8)

    def _color_distance(self,rgb:Any,color:tuple[int,int,int])->Any:
        import cv2
        import numpy as np

        lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
        color_lab = cv2.cvtColor(
            np.array([[color]], dtype=np.uint8),
            cv2.COLOR_RGB2LAB,
        ).astype(np.float32)[0, 0]
        return np.linalg.norm(lab - color_lab, axis=2)

    def _rgb_distance(
        self,
        color1:tuple[int,int,int],
        color2:tuple[int,int,int],
    )->float:
        return sum((a - b) ** 2 for a, b in zip(color1, color2, strict=False)) ** 0.5

    def _color_code(self,color:Any)->int:
        return int(color[0]) << 16 | int(color[1]) << 8 | int(color[2])

    def _color_codes(self,colors:Any)->Any:
        import numpy as np

        values = colors.astype(np.uint32)
        return (values[:, 0] << 16) | (values[:, 1] << 8) | values[:, 2]

    def _to_kcolor(self,rgb:tuple[int,int,int])->KColor:
        return KColor(tuple(max(0, min(255, int(v))) for v in rgb))
