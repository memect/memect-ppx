import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterator

import pymupdf

from .api import ApiError


@dataclass
class PageInfo:
    number: int
    width: float
    height: float
    rotation: int = 0


_logger: Final = logging.getLogger(f"{__name__}")


def page_count(file: str | Path | bytes) -> int:
    """获得页码"""
    # 需要支持多线程，所以pypdfium就不可以
    # 如果还需要支持free-threaded的？pymupdf不支持,只能够使用pdfminer了，但是慢
    # 或者：pypdf get_num_pages

    def use_pymupdf():
        try:
            import pymupdf  # type: ignore
            return True
        except ImportError:
            return False

    def use_pdf_oxide():
        # 支持多线程，但是不支持free-threaded
        try:
            import pdf_oxide
            return True
        except ImportError:
            return False

    try:
        if isinstance(file, (str, Path)) and use_pdf_oxide():
            # 5000页需要0.005秒
            import pdf_oxide
            return pdf_oxide.PdfDocument(str(file)).page_count()
        elif use_pymupdf():
            # 5000页需要0.2秒
            import pymupdf

            filename = None
            stream = None
            if isinstance(file, (str, Path)):
                # 直接读取为字节
                # stream=Path(file).read_bytes()
                filename = file
            else:
                stream = io.BytesIO(file)
            with pymupdf.Document(
                filename=filename, stream=stream, filetype="pdf"
            ) as doc:
                return doc.page_count
        else:
            # 5000页需要1秒，500页需要0.1秒，也可以接受，毕竟总共只是会调用2-3次
            import pypdf

            if isinstance(file, (str, Path)):
                stream = io.BytesIO(Path(file).read_bytes())
            else:
                stream = io.BytesIO(file)
            with pypdf.PdfReader(stream) as doc:
                return doc.get_num_pages()
    except Exception as e:
        _logger.exception("无法打开PDF文件")
        raise ApiError(ApiError.ANY, "不是有效的PDF文件") from e


def pages(file: str | Path | bytes) -> Iterator[PageInfo]:
    # TODO 或者pdf_oxide
    filename: str | None = None
    stream = None
    if isinstance(file, (str, Path)):
        filename = str(file)
    else:
        stream = io.BytesIO(file)
    with pymupdf.open(filename=filename, stream=stream, filetype="pdf") as doc:
        for i in range(doc.page_count):
            # 5000页大概1.3秒
            # TODO 如果原文的meidabbox,cropbbox不是[0,0,x1,y1]的，需要转换为[0,0]
            # 这个就需要在ctm中同时进行调整，避免出现误差
            # 因为mediabbox,cropbbox是相对用户空间（也就是逻辑空间），99.99%都应该为[0,0,x1,y1]，但是总有个别pdf不是
            # 因为后续计算，是把页面上的对象坐标都是定义在rect的(0,0)，而pdf的中，使用相对用户空间，如果rect也是(0,0)，都是一致的，
            # 如果不是，就需要translate一下。在pdf溯源的时候，需要看pdf工具，使用的是哪个坐标，如果是使用rect，就不需要再转换，如果是使用用户空间，还需要再
            # translate回来即可
            page = doc.load_page(i)  # 不渲染内容，仅加载页面结构
            rect = page.rect  # 已应用 rotation 的视觉宽高
            rotation = page.rotation  # 0 / 90 / 180 / 270
            w, h = rect.width, rect.height
            w=round(w,1)
            h=round(h,1)
            yield PageInfo(i + 1, w, h, rotation=rotation)
