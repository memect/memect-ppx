import json
import logging
import math
import random
import re
import threading
import typing
import uuid
import weakref
from collections.abc import Sequence
from enum import StrEnum, auto
from functools import cached_property
from pathlib import Path
from typing import (
    Any,
    ClassVar,
    Final,
    Iterator,
    Mapping,
    NotRequired,
    Self,
    TypeGuard,
    TypedDict,
    cast,
    override,
)

import PIL
import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont
from pydantic import ConfigDict, Field

from memect.base import images, lists, pdfs
from memect.base.api import ApiError
from memect.base.bbox import BBox, Point, Quad
from memect.base.matrix import Matrix
from memect.base.strs import NText
from memect.base.utils import AutoCleaner, MyBaseModel, safe_write
from memect.pdf.grid import Grid
from memect.base.zip import Archiver
from memect.pdf.sort import Sorter


if typing.TYPE_CHECKING:
    from .model import ModelManager

class PageParams(MyBaseModel):
    number: int = 1
    """表示页码，1为第一页"""
    rotation: int = 0
    """表示页面需要顺时针旋转多少度"""


class Backend(StrEnum):
    DEFAULT = auto()
    DEEPSEEK = auto()
    PADDLE = auto()
    GLM = auto()
    BAIDU = auto()


class PageType(StrEnum):
    PDF = auto()
    """表示有pdf解析获得的字符，可以包含部分来自ocr的"""
    IMAGE = auto()
    """表示完全作为图片解析，也就是所有字符都来自ocr"""
    UNKNOWN = auto()
    """初始状态"""
    HYBRID=auto()
    """混合解析"""


class CharSource(StrEnum):
    PDF = auto()
    OCR = auto()
    UNKNOWN = auto()


class OCRMode(StrEnum):
    """处理pdf使用"""

    YES = auto()
    """所有页面都使用ocr"""
    NO = auto()
    """所有页面都不使用ocr"""
    AUTO = auto()
    """自动判断"""


class TableMode(StrEnum):
    NO = auto()
    """不解析表格，作为图片"""
    YBK = auto()
    """全部按有边框解析"""
    WBK = auto()
    """全部按无边框解析"""
    AUTO = auto()
    """自动按有边框或者无边框"""
    # LLM = auto()
    # """使用LLM解析"""


class ParseMode(StrEnum):
    PAGE = auto()
    """按页解析即可"""
    TREE = auto()
    """按章节树解析"""
    PPT = auto()
    """按PPT解析，也就是没有页眉页脚注的内容"""


class TreeBackend(StrEnum):
    DEFAULT = auto()
    LLM = auto()
    OUTLINE = auto()
    TOC = auto()


class TreeParams(MyBaseModel):
    backend: TreeBackend = TreeBackend.DEFAULT
    template: Mapping[str, Any] | None = None


class ApiParams(MyBaseModel):
    model_config = ConfigDict(
        title="Parse Params",
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://memect/api/parse/params.schema.json",
        },
    )
    # 表示api可以设置的参数
    use_figures: bool = True
    """True表示是否需要截取页面上的图片"""
    output_files: Sequence[str] | None = None

    timeout: float | None = None
    """设置一个超时，超过该时间就结束任务，主要用来方便测试"""

    # ===pdf相关的======
    pagenos: Sequence[int] | None = None
    """表示仅仅处理哪些页面"""
    pages: Sequence[PageParams] | None = None
    """可以定义页面需要使用的参数"""

    # ===图片相关的======
    exif: bool = True
    """如果是图片的，True表示需要应用exif的旋转处理"""
    rotation: int = 0
    """如果是图片的，表示需要顺时针旋转多少度"""

    # ===输出相关====
    docx: bool = False
    """True表示输出doc.docx"""
    pptx: bool = False
    """True表示输出doc.pptx"""
    html: bool = False
    """True表示输出doc.html"""
    markdown: bool = True
    """True表示输出doc.md"""
    doc_json: bool = True
    """True表示输出doc.json"""

    mode: ParseMode = ParseMode.PAGE
    table: TableMode = TableMode.AUTO
    formula: bool = False
    """True表示需要解析公式，需要部署对应的模型,False只是截图即可"""

    tree: TreeParams = Field(default_factory=TreeParams)

    remove_watermark: bool = False
    """True表示清除水印"""

    backend: Backend = Backend.DEFAULT
    """deepseek,paddle,glm,default"""

    ocr: OCRMode = OCRMode.AUTO

    features:list[str]=Field(default_factory=list)


class ParseParams(ApiParams):
    api: bool = False
    """表示为api请求"""
    dev: bool = False
    """表示为开发模式"""


####!!!
# cached_property: 不支持多线程，如果要在多线程下使用，只能够使用property，然后自行lock和缓存对象
# 当然，这个线程不安全表示的是，可能同时执行几次计算，如果每次计算的结果都一样，不影响，缓存最后一个的，然后就不会了
####!!!


class VObjectType(StrEnum):
    TEXT = auto()
    """普通文本"""
    TITLE = auto()
    """标题"""
    TOC = auto()
    """目录文本"""
    OTHER_TEXT = auto()
    """不重要的文本"""
    CODE = auto()
    """代码"""
    FIGURE = auto()
    """图片"""
    CHART = auto()
    """图表"""
    TABLE = auto()
    """表格"""
    SEAL = auto()
    """印章"""
    FORMULA = auto()
    """独立公式"""
    INLINE_FORMULA = auto()
    """行内公式"""
    HEADER = auto()
    """页眉文本"""
    FOOTER = auto()
    """页脚文本"""
    FOOTNOTE = auto()
    """脚注文本"""

    HEADER_FIGURE=auto()
    """页眉图片"""

    FOOTER_FIGURE=auto()
    """页脚图片"""


class VObject:
    def __init__(
        self, page: "KPage", type: str, quad: Quad, score: float = 1, raw_type: str = ""
    ):
        super().__init__()
        self.page: Final = page
        self.type: Final = VObjectType(type)
        self.bbox: Final = quad.bbox
        self.quad: Final = quad
        self.score: Final = score
        self.raw_type: Final = raw_type
        self.cache: Final[dict[str, Any]] = {}
        self.debug: Final[dict[str, Any]] = {}
        self.ocr_chars: Final[list[KChar]] = []
        """该对象区域ocr识别的字符"""
        self.vobjects: Final[list[VObject]] = []
        """如果是表格，可以继续包含对象，如：图片等"""
        # self.pdf_chars:Final[list[KChar]]=[]
        # self.ocr_spans:Final[list[KSpan]]=[]

    def clear(self):
        """在完成解析后，可以清除引用了"""
        self.cache.clear()
        self.debug.clear()
        self.ocr_chars.clear()
        # self.ocr_spans.clear()
        # self.pdf_chars.clear()

    def is_any_text(self) -> bool:
        """True表示各种文本"""
        # TODO code表示代码，通常有高亮，缩进等，作为图片处理，可能会更好
        # 或者使用一个KCode对象
        return self.type in (
            VObjectType.TEXT,
            VObjectType.TITLE,
            VObjectType.TOC,
            VObjectType.OTHER_TEXT,
            VObjectType.FOOTER,
            VObjectType.HEADER,
            VObjectType.FOOTNOTE,
        )

    def is_text(self) -> bool:
        return self.type == VObjectType.TEXT

    def is_title(self) -> bool:
        return self.type == VObjectType.TITLE

    def is_toc(self) -> bool:
        return self.type == VObjectType.TOC

    def is_other_text(self) -> bool:
        return self.type == VObjectType.OTHER_TEXT

    def is_table(self) -> bool:
        return self.type == VObjectType.TABLE

    def is_figure(self) -> bool:
        return self.type == VObjectType.FIGURE

    def is_chart(self) -> bool:
        return self.type == VObjectType.CHART

    def is_any_formula(self) -> bool:
        return self.type in (VObjectType.FORMULA, VObjectType.INLINE_FORMULA)

    def is_formula(self) -> bool:
        return self.type == VObjectType.FORMULA

    def is_inline_formula(self) -> bool:
        return self.type == VObjectType.INLINE_FORMULA

    def is_seal(self) -> bool:
        return self.type == VObjectType.SEAL

    def is_code(self) -> bool:
        return self.type == VObjectType.CODE

    def is_header(self) -> bool:
        # TODO 如果换了模型，raw_type需要重新映射？
        return self.type == VObjectType.HEADER or self.raw_type == "header_image"

    def is_footer(self) -> bool:
        # TODO 如果换了模型，raw_type需要使用其他名字?
        return self.type == VObjectType.FOOTER or self.raw_type == "footer_image"

    def is_footnote(self) -> bool:
        return self.type == VObjectType.FOOTNOTE

    def is_table_title(self) -> bool:
        return self.raw_type == "table_title"

    def is_figure_title(self) -> bool:
        return self.raw_type == "figure_title"

    def is_source(self) -> bool:
        """判断是否来源等？"""
        return self.raw_type == "vision_footnote"

    def make_figure(self, *, dx: float = 0, dy: float = 0) -> "KFigure":
        if dx != 0 or dy != 0:
            quad = self.bbox.expand(dx=dx, dy=dy).intersect(self.page.bbox).to_quad()
        else:
            quad = self.quad
        figure = self.page.make_figure(quad)
        assert figure is not None
        figure.vobject = self
        figure.subtype = str(self.type)
        return figure
    
    def set_bbox(self,bbox:BBox):
        """在有些情况下可以改变bbox"""
        self.bbox=bbox
        self.quad=bbox.to_quad()


class KDocument:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(
        self,
        file: str | Path,
        *,
        out_dir: str | Path | None = None,
        auto_rename: bool = False,
        params: ParseParams | Mapping[str, Any] | None = None,
    ):
        params = ParseParams.create(params)
        file = Path(file)
        if not out_dir:
            out_dir = Path(f"{file}.out")
        else:
            out_dir = Path(out_dir)

        if file.is_file():
            filetype, suffix = self._get_filetype(file)
        elif file.is_dir():
            filetype = "image"
            suffix = ""
        else:
            raise FileNotFoundError(f"文件/目录不存在:{file}")
        if file.is_file() and auto_rename:
            # 在api的时候，可以自动重命名，调试更友好
            file = file.replace(file.with_suffix(suffix))

        self.raw_file: Final = file
        """原始文件的路径，可以为pdf/image等"""
        self.file = file
        """工作的文件路径，如：清除了水印，会被替换为新的路径"""
        self.out_dir: Final = out_dir
        """输出的目录"""
        self.params: Final = params
        self._filetype: Final = filetype
        """原始的文件类型，pdf/image"""
        self.pages: Final = self._build_pages()
        """所有的页面"""
        self.working_pages: Final[Sequence[KPage]] = tuple(
            p for p in self.pages if not p.skipped
        )
        """仅仅为指定的需要的页面"""
        self.page_count: Final = len(self.pages)
        """总页数"""
        self.priority: int = 1
        """执行的优先级，这个是给api请求的时候设置和任务调度使用，在这里设置和保留是为了方便调试"""

        self.colors: Final[dict[Any, KColor]] = {}
        # self.fonts:Final[dict[str,KFont]]={}

        self._lock: Final = threading.RLock()
        self._auto_cleaner: AutoCleaner | None = None

        self.state: dict[str, Any] = {}
        """执行统计"""

        self.pdf_toc: PDFNode | None = None
        """pdf的toc根节点"""

        #在word中，可以包含文本/图片等任意内容，也可以使用list[KObject]或者XBlock
        self._footnotes:dict[str,str]={}
        """全局的脚注列表，就不记录字体的大小了，纯文本？"""

        from .x.xbase import XTree

        self.tree: XTree | None = None
        """章节树解析的结果"""

        #from .model import ModelManager
        self._model_manager=None
    
    @property
    def model_manager(self)->'ModelManager|None':
        if self._model_manager is not None:
            return self._model_manager()
        else:
            return None
    
    @model_manager.setter
    def manager(self,m:'ModelManager'):
        self._model_manager = weakref.ref(m)
    
    def __del__(self):
        # self._logger.debug("gc %s", self)
        pass

    def get_auto_cleaner(self) -> AutoCleaner:
        """在api请求的时候调用，在其他时候不应该调用，否则会自动清除目录"""
        with self._lock:
            if self._auto_cleaner is None:
                self._auto_cleaner = AutoCleaner([self.out_dir])
            else:
                pass
            return self._auto_cleaner

    def _build_pages(self) -> Sequence["KPage"]:
        def get_page_params(n: int):
            for page_params in params.pages or []:
                if page_params.number == n:
                    return page_params
            return PageParams(number=n)

        def get_bbox(size: tuple[float, float], rotation: int) -> BBox:
            rotation = rotation % 360
            if rotation in (90, 270):
                return BBox(0, 0, size[1], size[0])
            else:
                return BBox(0, 0, size[0], size[1])

        params: Final = self.params
        file: Final = self.file
        pages_dir: Final = self.pages_dir
        pages: list[KPage] = []
        if file.is_file():
            if self.is_pdf():
                for p in pdfs.pages(file):
                    # TODO 在pdf2image的时候，如果没有使用pdf自带的旋转，就需要设置rotation=p.rotation
                    # 如果应用了，就不需要了，p.width,p.height已经为旋转后的
                    page_params = get_page_params(p.number)
                    # 相对pdf页面应用了自身旋转度数后的旋转度数
                    page = KPage(
                        self,
                        p.number,
                        get_bbox((p.width, p.height), page_params.rotation),
                        pages_dir / f"{p.number}.png",
                        rotation=page_params.rotation,
                    )
                    pages.append(page)
            elif self.is_image():
                # copy+mode
                # shutil.copy()
                # copy+mode+meta
                # shutil.copy2()
                # 统一使用“png”作为后缀，即使不是png格式，不影响，目的是方便在任何地方使用，可以根据规则构造图片的路径
                page_file = pages_dir / "1.png"
                # 在前端溯源图片的时候，也同样需要先自行应用exif的旋转等，rotation是相对应用exif后的
                images.copy(file, page_file, exif=params.exif)
                size = images.size(page_file)
                page = KPage(
                    self,
                    1,
                    get_bbox(size, params.rotation),
                    page_file,
                    rotation=params.rotation,
                )
                pages.append(page)
            else:
                raise RuntimeError("不可能执行到这里")
        elif file.is_dir():
            # 如果为目录，表示为1.png,2.png,3.png这样
            for image_file in file.iterdir():
                if (
                    image_file.is_file()
                    and image_file.name[0] != "."
                    and image_file.suffix.lower()
                    in (".png", ".jpg", ".jpeg", ".webp", ".bmp")
                ):
                    number = int(image_file.stem)
                    page_params = get_page_params(number)
                    # 统一使用“png”后缀
                    page_file = pages_dir / f"{number}.png"
                    # 如果是多个图片的，exif=False?
                    images.copy(image_file, page_file, exif=params.exif)
                    size = images.size(page_file)
                    # 这里rotation使用的是每个页面自己的设置
                    page = KPage(
                        self,
                        number,
                        get_bbox(size, page_params.rotation),
                        page_file,
                        rotation=page_params.rotation,
                    )
                    pages.append(page)
            pages.sort(key=lambda page: page.number)
        else:
            raise FileNotFoundError(file)

        pagenos = params.pagenos
        for page in pages:
            if pagenos and page.number not in pagenos:
                page.skipped = True
        return tuple(pages)

    def new_dir(self, name: str) -> Path:
        dir_ = self.out_dir.joinpath(name)
        dir_.mkdir(parents=True, exist_ok=True)
        return dir_

    def is_dev(self) -> bool:
        """判断是否为开发模式"""
        return self.params.dev

    def has_file(self, name: str) -> bool:
        return self.out_dir.joinpath(name).is_file()

    @cached_property
    def images_dir(self) -> Path:
        return self.new_dir("images")

    @cached_property
    def debug_dir(self) -> Path:
        return self.new_dir("debug")

    @cached_property
    def pages_dir(self) -> Path:
        return self.new_dir("pages")

    @cached_property
    def zip_file(self) -> Path:
        return self.out_dir / "out.zip"

    @cached_property
    def html_file(self) -> Path:
        return self.out_dir / "doc.html"

    @cached_property
    def md_file(self) -> Path:
        return self.out_dir / "doc.md"

    @cached_property
    def docx_file(self) -> Path:
        return self.out_dir / "doc.docx"

    @cached_property
    def pptx_file(self) -> Path:
        return self.out_dir / "doc.pptx"

    @cached_property
    def json_file(self) -> Path:
        return self.out_dir / "doc.json"

    @cached_property
    def pagenos(self) -> Sequence[int]:
        """获得仅仅需要执行的页码"""
        return tuple(p.number for p in self.pages if not p.skipped)

    def is_dir(self) -> bool:
        """表示为图片目录"""
        return self.file.is_dir()

    def is_image(self) -> bool:
        return self._filetype == "image"

    def is_pdf(self) -> bool:
        return self._filetype == "pdf"

    def make_zip(self, zip_file: str | Path | None = None):
        if not zip_file:
            zip_file = self.zip_file
        else:
            zip_file = Path(zip_file)
            zip_file.parent.mkdir(parents=True, exist_ok=True)

        temp_file = Path(str(zip_file) + "." + uuid.uuid4().hex)
        # 默认的返回
        names: list[str] = [
            "doc.json",
            "doc.html",
            "doc.md",
            "images",
            # "a.pdf",
            # "pages",
            "doc.pptx",
            "doc.docx",
        ]
        # 可选的，也就是必须明确选择才返回
        optional_names: list[str] = ["pages"]

        files: list[Path] = []
        if self.params.output_files:
            names = list(set(names + optional_names) & set(self.params.output_files))
        else:
            pass

        for name in names:
            f = self.out_dir / name
            if f.exists():
                files.append(f)
        try:
            Archiver().zip(temp_file, files=files)
            temp_file.replace(zip_file)
        finally:
            temp_file.unlink(missing_ok=True)

    def write(self, name: str, data: Any):
        file = self.out_dir.joinpath(name)
        if isinstance(data, str | bytes | PIL.Image.Image):
            safe_write(file, data)
        else:
            # orjson比json快，但是不支持free-threaded
            try:
                import orjson

                safe_write(file, orjson.dumps(data))
            except ModuleNotFoundError:
                safe_write(file, json.dumps(data, ensure_ascii=False))

    def read_json(self, name: str) -> Any | None:
        """载入json对象，如果文件不存在，返回None"""
        file = self.out_dir.joinpath(name)
        if file.is_file():
            try:
                import orjson

                return orjson.loads(file.read_bytes())
            except ModuleNotFoundError:
                return json.loads(file.read_text("utf-8"))
        else:
            return None

    def read_text(self, name: str, *, encoding: str = "utf-8") -> str | None:
        file = self.out_dir.joinpath(name)
        if file.is_file():
            return file.read_text(encoding=encoding)
        else:
            return None

    def read_bytes(self, name: str) -> bytes | None:
        file = self.out_dir.joinpath(name)
        if file.is_file():
            return file.read_bytes()
        else:
            return None

    def all_as_images(self):
        """所有的页面都作为图片"""
        for page in self.pages:
            assert page.type == PageType.UNKNOWN
            page.type = PageType.IMAGE

    def all_as_pdfs(self):
        """所有的页面都作为pdf处理"""
        for page in self.pages:
            assert page.type == PageType.UNKNOWN
            page.type = PageType.PDF

    def get_working_page(self, number: int) -> "KPage|None":
        for p in self.working_pages:
            if p.number == number:
                return p
        return None

    def _get_filetype(self, file: Path) -> tuple[str, str]:
        import filetype

        # 可以根据扩展名判断？
        kind = filetype.guess(file)
        if kind:
            if kind.extension in ("pdf",):
                return ("pdf", ".pdf")
            elif kind.extension in ("png", "jpeg", "jpg", "webp", "bmp"):
                return ("image", f".{kind.extension}")
            else:
                # 可以在这里直接使用ApiError，可以获得具体的信息
                raise ApiError(ApiError.ANY, f"不支持的文档类型:{kind.extension}")
        else:
            suffix = file.suffix.lower()
            if suffix in (".pdf",):
                return ("pdf", ".pdf")
            elif suffix in (".png", ".jpeg", ".jpg", ".webp", ".bmp"):
                return ("image", suffix)
            else:
                raise ApiError(ApiError.ANY, f"不支持的文档类型:{suffix}")

    def jsonify(self, lite: bool = False) -> Any:
        data: dict[str, Any] = {}
        if self.tree is not None:
            # 如果解析了章节树，输出tree和pages(精简版)
            data["pages"] = [page.jsonify(lite=True) for page in self.pages]
            data["tree"] = self.tree.jsonify()
        else:
            data["pages"] = [page.jsonify() for page in self.pages]

        # data['execute']={}
        return data

    def markdown(self) -> str:
        buf: list[str] = []
        for page in self.working_pages:
            buf.append(page.markdown())
        # TODO 需要添加分页符吗？如：----1----
        return "\n\n".join(buf)


class KDocumentFactory:
    def __init__(self, file: str | Path, params: Any, out_dir: Path | None = None):
        super().__init__()
        self.file: Final = file
        self.params: Final = params
        self.out_dir: Final = out_dir

    def __call__(self) -> KDocument:
        return KDocument(self.file, params=self.params, out_dir=self.out_dir)


class _LayoutObject(TypedDict):
    type: str
    bbox: NotRequired[Sequence[float]]
    quad: NotRequired[Sequence[Sequence[float]]]
    score: float
    raw_type: str


class _LayoutResult(TypedDict):
    number: int
    width: int
    height: int
    objects: list[_LayoutObject]


def _md_escape(text: str) -> str:
    # 在实际使用中"()"不需要转义
    return re.sub(r"[`~*_+\-!{}#.\\]", lambda m: rf"\{m.group()}", text)


class KColumn:
    """对应docx中的一个列"""

    def __init__(self, index: int, bbox: BBox):
        super().__init__()
        self.index = index
        self.bbox = bbox

    def has_silibing(self) -> bool:
        """
        [column1]|[column2]|[column3]
        """
        pass

    def is_section(self, *columns: Self) -> bool:
        """
        判断几个列是否为一个section，如：
        [column1]|[column2]|[column3]
        """
        if len(columns) <= 1:
            return True

        for i in range(1, len(columns)):
            c1 = columns[i - 1]
            c2 = columns[i]
            if c1.index + 1 != c2.index:
                return False
            # 如果是严格的，必须顶部对齐的，现在宽松一点


class KSection:
    """对应docx的一个节，在docx中，一个节可以设置分栏的方式，如：单栏，双栏，三栏，在按页解析中，记录该页的节"""

    def __init__(self, page: "KPage", columns: Sequence[BBox]):
        super().__init__()
        assert len(columns) > 0
        self.page: Final = page
        self.columns: Final = tuple(columns)
        self.bbox = BBox.join(columns)

    @property
    def col_num(self) -> int:
        return len(self.columns)

    def get_column(self, bbox: BBox) -> KColumn | None:
        for i, column in enumerate(self.columns):
            xa = column.intersect(bbox)
            if xa and xa.area / bbox.area >= 0.8:
                return KColumn(i, column)
        return None

    def contains(self, bbox: BBox) -> bool:
        column = self.get_column(bbox)
        if column is not None:
            return True
        return False

    def alike(self, b: "KSection", *, strict: bool = True) -> bool:
        """判断是否列对齐"""
        a = self
        if a.col_num != b.col_num:
            return False
        if a.col_num == 1:
            return True

        # a1|a2|a3
        # b1|b2|b3
        # 因为目前仅仅支持双栏且等宽
        return True


class KPage:
    """pdf的一个页面或者一个图片"""

    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(
        self,
        doc: KDocument,
        number: int,
        bbox: BBox,
        file: str | Path,
        *,
        rotation: int = 0,
    ):
        assert number >= 1
        # self.doc: Final = doc
        # 避免循环引用，不能够快速释放对象
        self._doc: Final = weakref.ref(doc)
        self.number: Final = number
        """页码，1表示第一页"""
        self.bbox: Final = bbox

        self.file: Final = Path(file)
        """图片路径"""
        self.rotation: Final = rotation % 360
        """表示需要在原图上进行多少度的旋转，顺时针，如果是pdf，pdf本身可以自己设置一个rotation，
        这个是相对pdf应用了自己的rotation的，如：pdf_page.set_rotation(pdf_page.rotation+rotate)
        """

        self.skipped: bool = False
        """True表示这一页不需要解析，跳过"""

        self.objects: Final[list[KObject]] = []
        """页面上按阅读顺序排序后的对象，没有KChar"""

        self.vobjects: Final[list[VObject]] = []
        """在页面解析过程中，存储筛选后使用的对象"""

        self.raw_vobjects: Final[list[VObject]] = []
        """模型识别的所有页面对象"""

        self.pdf_chars: Final[list[KChar]] = []
        """页面上的所有的原始pdf字符"""
        self.pdf_lines: Final[list[KLine]] = []
        """页面上所有的原始pdf线"""
        self.pdf_rects: Final[list[KRect]] = []
        """页面上所有的原始pdf矩形"""
        self.pdf_figures: Final[list[KPDFFigure]] = []
        """页面上所有的原始的pdf图片"""
        # self.pdf_paths: Final[list[Any]] = []
        # """页面上所有的原始的路径"""

        # self.ocr_spans:Final[list[KSpan]]=[]
        # """ocr识别的字符串，目的是用来去掉和pdf识别的且重叠的"""

        self.pdf_links: Sequence[PDFLink] = tuple()
        """表示页面上的pdf的annots(Subtype=Link)"""

        self.data: Final[dict[str, Any]] = {}

        self.cache: Final[dict[str, Any]] = {}
        """可以缓存任意临时的数据"""
        self.debug: Final[dict[str, Any]] = {}
        """可以存储任意临时调试的信息"""

        self.type: PageType = PageType.UNKNOWN
        """页面的类型，pdf，image，unknown"""

        # self._images:Final[dict[str,PIL.Image.Image]]={}
        # self._raw_image:PIL.Image.Image|None=None

        # 先设置一个大概的区域
        height = self.bbox.height
        self.header: Final = KPageHeader(
            self, self.bbox.adjust(y0=int(height * 0.9)).to_quad()
        )
        self.footer: Final = KPageFooter(
            self, self.bbox.adjust(y1=int(height * 0.1)).to_quad()
        )

        self.footnotes: Final[list[KPageFootnote]] = []
        """在分栏下，会存在多个部分"""

        self._seqs: dict[str, int] = {"figure": 1}

        # TODO 或者使用一个doc.lock，所有的页面都使用同一个
        self._lock: Final = threading.RLock()
        # self._lock:Final = self.doc.lock

        # 如果原文是docx排版的，可以选择生成docx，支持分栏，复杂的情况使用textbox？
        # 如果原文是pptx排版的，可以选择生成pptx
        # 如果原文是杂志，报纸等排版的，建议选择生成html
        # 如果是需要生成docx，只有section（节）的概念
        # 1.上一节和下一节的分栏不同
        # 2.或者需要分页，插入一个分页符，可以共享相同的页眉页脚设置
        #  如：竖向，横向页面的切换
        #  如果只是简单的添加一个空白页，添加一个换行（分页）即可

        self._blocks: Sequence[BBox] | None = None
        """页面的板块区域"""
        self._sections: Sequence[KSection] = tuple()
        """记录当前页面的分栏，生成docx的时候需要"""
        self._default_section: Final = KSection(self, [self.bbox])

    def __del__(self):
        self._logger.debug("gc %s", self)

    @property
    def doc(self) -> KDocument:
        doc = self._doc()
        if doc is None:
            raise RuntimeError("编程错误，doc已经提前解除了引用，必须一直引用")
        return doc

    @property
    def width(self) -> float:
        return self.bbox.width

    @property
    def height(self) -> float:
        return self.bbox.height

    @property
    def size(self) -> tuple[float, float]:
        return (self.bbox.width, self.bbox.height)

    @cached_property
    def image(self) -> PIL.Image.Image:
        """如果是pdf，必须在pdf2image后调用，不然文件不存在"""
        # pil比cv2慢一些，但是目前的场景使用pil更好，因为pil支持free-threaded和pypy
        # 而且耗时都很小，对整体解析影响不大
        with self._lock:
            if not self.file.is_file():
                raise RuntimeError("编程错误，图片还没有存在")
            return images.open(self.file, rotation=self.rotation)

    def is_pdf(self) -> bool:
        """表示使用pdf解析"""
        return self.type == PageType.PDF

    def is_image(self) -> bool:
        """表示使用ocr解析"""
        return self.type == PageType.IMAGE

    def is_unknown(self) -> bool:
        """表示未知，也就是还没有解析"""
        return self.type == PageType.UNKNOWN
    
    def is_hybrid(self)->bool:
        """混合"""
        return self.type==PageType.HYBRID

    def clear(self):
        """解析完毕，清除不必要的内容"""

        # 清除引用，对象不一定能够释放，因为可能还在用，但是有些已经没有用了
        def clear_objects(objs: Sequence[KObject] | Sequence[VObject]):
            for obj in objs:
                obj.clear()

        for objs in [
            self.pdf_chars,
            self.pdf_lines,
            self.pdf_rects,
            self.vobjects,
            self.raw_vobjects,
        ]:
            clear_objects(objs)
            objs.clear()
        self.cache.clear()
        self.debug.clear()

    def to_lt(self, size: tuple[int, int] | None = None) -> Matrix:
        """获得从页面左下角转换为左上角的矩阵，且可以放大缩小为目标大小"""
        return Matrix.lb_to_lt(self.size, size)

    def to_lb(self, size: tuple[int, int] | None = None) -> Matrix:
        """获得从页面左上角转换为左下角的矩阵，且可以放大缩小为目标大小"""
        return Matrix.lt_to_lb(self.size, size)

    def next_figure_name(self) -> str:
        """相对输出目录的图片路径"""
        with self._lock:
            # 实际上不会在多线程使用
            seq = self._seqs["figure"]
            self._seqs["figure"] += 1
            # 使用png
            return f"images/{self.number}-{seq}.png"

    def crop(
        self, quad: Quad | BBox, *, filename: str | None = None
    ) -> PIL.Image.Image | None:
        """相对页面的区域进行裁剪
        filename: 相对输出目录的文件名，如果指定了，就自动保存
        """
        m = Matrix.lb_to_lt(self.size, self.image.size)
        if isinstance(quad, BBox):
            quad = quad.to_quad()
        new_quad = quad.transform(m)
        new_bbox = new_quad.bbox.intersect(
            BBox(0, 0, self.image.width, self.image.height)
        )
        if new_bbox is None or not new_bbox.is_valid():
            return None
        # TODO 后续可以支持把quad外的区域变成透明
        img = self.image.crop(new_bbox)
        if filename:
            fullpath = self.doc.out_dir / filename
            fullpath.parent.mkdir(parents=True, exist_ok=True)
            img.save(fullpath)
        return img

    def make_figure(self, quad: Quad | BBox, *, add: bool = False,clear:bool=False) -> "KFigure|None":
        img = self.crop(quad)
        if img is None:
            return None

        filename = self.next_figure_name()
        figure = KFigure(self, quad, filename=filename)
        figure.fullpath.parent.mkdir(parents=True, exist_ok=True)
        img.save(figure.fullpath)

        if clear:
            figure.bbox.get(self.objects,ratio=0.8,remove=True)

        if add:
            self.objects.append(figure)
        return figure

    def make_formula(
        self, quad: Quad|BBox, *, add: bool = False,clear:bool=False,inline: bool = False, latex: str = ""
    ):
        figure = self.make_figure(quad)
        if figure is None:
            return None
        formula = KFormula(
            self, quad, inline=inline, latex=latex, filename=figure.filename
        )
        if clear:
            formula.bbox.get(self.objects,ratio=0.8,remove=True)
        if add:
            self.objects.append(formula)
        return formula
    
    def make_table(self,quad:Quad|BBox,*,use_vobj:bool=False,add:bool=False,clear:bool=False,name:str='custom',index:int=0):
        """创建一个表格"""
        if isinstance(quad,Quad):
            bbox=quad.bbox
        else:
            bbox=quad
        from memect.pdf.default.table.wbk import Parser
        #TODO 目前还是需要使用模型来获得结构，后续不使用模型了，直接根据规则解析？
        manager = self.doc.model_manager
        assert manager is not None
        return Parser(manager).parse_one(self,bbox,use_vobj=use_vobj,add=add,clear=clear,name=name,index=index)

    def load_layout(self, data: Any, clear: bool = True):
        """
        载入模型，clear=True，表示清除之前的，如果需要合并多个不同的模型，设置为False
        """
        if clear:
            self.raw_vobjects.clear()
        result: _LayoutResult = data
        m = Matrix.lt_to_lb((result["width"], result["height"]), self.size)
        for obj in result["objects"]:
            if "quad" in obj:
                quad = Quad.from_list(obj["quad"])
                quad = quad.transform(m)
                bbox = quad.bbox
            elif "bbox" in obj:
                bbox = BBox(*obj["bbox"])
                # 坐标是相对图片的，需要转换为相对页面
                bbox = bbox.transform(m)
                quad = bbox.to_quad()
            else:
                raise ValueError("错误的对象，quad和bbox必须设置一个")

            # 如果溢出了页面？
            # 或者为无效的bbox?
            if not bbox.is_valid():
                self._logger.warning("去掉无效的vobject=%s", obj)
                continue
            vobj = VObject(
                self,
                type=obj["type"],
                quad=quad,
                score=obj["score"],
                raw_type=obj["raw_type"],
            )
            self.raw_vobjects.append(vobj)

        # 更新对象
        self.vobjects.clear()
        self.vobjects.extend(self._filter_vobjects(self.raw_vobjects))
        # self.vobjects.extend(self.raw_vobjects)
        # 按原始的顺序排序
        self.vobjects.sort(key=lambda obj: self.raw_vobjects.index(obj))

    def _filter_vobjects(
        self, vobjects: Sequence[VObject], min_overlap_ratio: float = 0.3
    ) -> list[VObject]:
        # 按分数排序，分数高的在前面

        # 识别出来的对象，存在下面的问题
        # 1. 丢失个别区域，如：某些文本，图片，表格，不处理，这是模型的锅
        # 2. 区域过大或者过小，不处理，这是模型的锅
        # 3. 区域重叠，如：大文本包含小文本，可能都是错误的，也可能都是正确的

        def fix1(index:int,vobjs:list[VObject]):
            #第一种：1个表格的，被识别为2个
            #--t1--
            #--title-- 这个被识别为普通标题，但是应该为表格内容，同时需要从page.objects中删除
            #--t2--

    
            if index+2>=len(vobjs):
                return False
            
            table1 = vobjs[index]
            title:VObject|None=None
            table2:VObject|None=None
            #不能够这么简单的，因为还需要支持跨栏的情况
            #title = vobjs[index+1]
            #table2 = vobjs[index+2]
            for k in range(index+1,len(vobjs)):
                obj2 = vobjs[k]
                if obj2.bbox.y1<=table1.bbox.y0 and table1.bbox.over('x',obj2.bbox,d=20):
                    title=obj2
                    index=k
                    break
            
            if title is None:
                return False

            for k in range(index+1,len(vobjs)):
                obj2 = vobjs[k]
                if obj2.bbox.y1<=title.bbox.y0 and table1.bbox.over('x',obj2.bbox,d=20):
                    table2=obj2
                    index=k
                    break
            
            if table2 is None:
                return False
            min_width=100
            max_title_height=20
            if not (table1.is_table() and title.is_title() and table2.is_table()):
                return False
            
            if title.bbox.height>max_title_height or table1.bbox.width<min_width:
                return False
            
            if not table1.bbox.align('x',table2.bbox,d=10):
                return False

            if not (-2<=table1.bbox.y0-title.bbox.y1<=5 and -2<=title.bbox.y0-table2.bbox.y1<=5 and abs(title.bbox.cx-table2.bbox.cx)<=10):
                return False
            
            self._logger.warning(
                "第%s页，合并表格，t1=%s,title=%s,t2=%s", self.number,table1.bbox,title.bbox,table2.bbox
            )
            vobjs.remove(title)
            vobjs.remove(table2)
            table1.set_bbox(BBox.join2([table1,title,table2]))
            return True
            
        def fix2(index:int,vobjs:list[VObject])->bool:
            #第二种: 粘连在一起的表格，被识别为2个，可能是因为表头颜色不同？
            #--t1--
            #--------
            #--t2--
            min_width=200
            max_gap=10
            min_score=0.5
            if index+1>=len(vobjs):
                return False
            t1=vobjs[index]
            if not t1.is_table() or t1.score<min_score:
                return False
            
            #查找t2，但是不是简单的vobjs[index+1]，因为可能多栏布局
            t2:VObject|None=None
            for k in range(index+1,len(vobjs)):
                t2=vobjs[k]
                if t2.bbox.y1<=t1.bbox.y0 and t1.bbox.over('x',t2.bbox,d=20):
                    break

            if t2 is None or not t2.is_table() or t2.score<min_score:
                return False
            
            if t1.bbox.y0-t2.bbox.y1>max_gap or t1.bbox.width<min_width:
                #间距过大
                return False
            if not t1.bbox.align('x',t2.bbox,d=10):
                #没有对齐
                return False
            
            self._logger.warning('第%s页，合并表格，t1=%s,t2=%s',self.number,t1.bbox,t2.bbox)
            t1.set_bbox(BBox.join2([t1,t2]))
            vobjs.remove(t2)
            
            return True

        def fix(vobjs:list[VObject]):
            fns=[fix1,fix2]
            vobjs.sort(key=lambda vobj:vobj.bbox.y1,reverse=True)
            i=0
            while i<len(vobjs):
                for fn in fns:
                    if fn(i,vobjs):
                        break
                i+=1
            pass

        raw_vobjects = vobjects
        vobjects = list(vobjects)
        # 可以删除太小的对象，目前仅仅删除为0
        lists.remove2(vobjects, lambda i, vobjs: vobjs[i].bbox.area <= 0)

        # 现在处理区域重叠的问题，不管使用哪种算法，都只能够处理某些情况，所以就简化了
        # 1.找到重叠的区域
        # 2.面积排序，大的留下，小的去掉
        # 3.相互重叠的情况就不考虑了
        vobjects.sort(key=lambda vobj: vobj.bbox.area, reverse=True)
        removed_objects: list[VObject] = []
        i = 0
        while i < len(vobjects):
            vobj = vobjects[i]
            for vobj2 in vobjects[i + 1 :]:
                inter = vobj.bbox.intersect(vobj2.bbox)
                if inter is None or inter.area == 0:
                    continue

                overlap_ratio = inter.area / vobj2.bbox.area
                if overlap_ratio >= min_overlap_ratio:
                    if (vobj.is_table() or vobj.is_chart()) and vobj2.is_any_text() and overlap_ratio<0.5 and vobj2.bbox.cy-vobj.bbox.y1>-2:
                        #[---title--]
                        #[---table--]  =>如果稍微重叠一点，可以调整table的
                        self._logger.warning('第%s页，调整和表格重叠的文本,text=%s,table=%s',self.number,vobj2.bbox,vobj.bbox)
                        vobj.set_bbox(vobj.bbox.adjust(y1=vobj2.bbox.y0-1))
                    else:
                        # 超过一半区域重叠，如果类型相同？合并，如：都是文本类型
                        self._logger.warning(
                            "删除重叠的对象,page=%s,large=%s,small=%s,overlap=%s,ratio=%.3f",
                            self.number,
                            vobj.bbox,
                            vobj2.bbox,
                            inter,
                            overlap_ratio,
                        )
                        vobjects.remove(vobj2)
                        removed_objects.append(vobj2)
                        continue
                # 如果仅仅部分区域重叠，合并为一个？

            i += 1
        
        #如果将来模型完善了，可以去掉这个修正
        fix(vobjects)

        for vobj in vobjects:
            if vobj.is_table():
                # 如果是表格，内部可能还包含有图片或者其他
                vobj.vobjects.extend(
                    vobj.bbox.get(removed_objects, ratio=0.7, remove=True)
                )

        return vobjects

    def draw(
        self,
        *columns: Any,
        file: str | Path | None = None,
        dir: str | Path | None = None,
        use_bbox: bool = False,
        show_type: bool = True,
        index: int | None = None,
        line_width: int | None = None,
    ) -> PIL.Image.Image:
        """
        columns: 需要显示的多列内容，None表示输出原图，或者list，或者PIL.Image.Image
        dir: 如果设置了，表示保存到哪个目录下，相对当前的out_dir，如果是Path，为完整的路径
        file: dir和file只能够设置一个
        """
        assert not (dir and file)

        def get_font(size: float | None = None):
            # font = PIL.ImageFont.truetype('')
            font: Final = PIL.ImageFont.load_default(size=size)
            return font

        def ensure_quad(a: Any) -> Quad:
            if isinstance(a, Quad):
                return a
            elif isinstance(a, BBox):
                return a.to_quad()
            elif isinstance(a, Sequence):
                if len(a) == 8:
                    return Quad.from_flat(a)
                elif isinstance(a[0], Sequence):
                    return Quad.from_list(a)
                else:
                    return BBox.from_list(a).to_quad()
            else:
                raise ValueError(f"不支持的quad:{a}")

        def get_quad_and_type(obj: Any) -> tuple[Quad, str]:
            if isinstance(obj, BBox):
                return (obj.to_quad(), "")
            elif isinstance(obj, Quad):
                return (obj, "")
            elif isinstance(obj, Sequence):
                return (ensure_quad(obj), "")
            elif isinstance(obj, (KObject, VObject)):
                return (obj.quad, obj.type)
            elif isinstance(obj, dict):
                if "bbox" in obj:
                    return (ensure_quad(obj["bbox"]), obj.get("type", ""))  # type: ignore
                else:
                    return (ensure_quad(obj["quad"]), obj.get("type", ""))  # type: ignore
            elif hasattr(obj, "bbox"):
                return (ensure_quad(obj.bbox), getattr(obj, "type", ""))
            elif hasattr(obj, "quad"):
                return (ensure_quad(obj.quad), getattr(obj, "type", ""))
            else:
                raise ValueError(f"不支持的对象:{obj}")

        def draw_objects(objects: Sequence[Any], font: Any, op: str | None = None):
            image = self.image.copy()
            overlay_image = PIL.Image.new("RGBA", image.size, (0, 0, 0, 0))

            draw = PIL.ImageDraw.Draw(image)
            overlay_draw = PIL.ImageDraw.Draw(overlay_image)

            # 把相对页面的坐标转换为相对图片的，原点从左下角到左上角，然后再缩放
            m = Matrix.lb_to_lt(self.size, image.size)
            for k, obj in enumerate(objects):
                # TODO 为页面坐标，还需要转换为相对图片的，因为可能缩放了，但是旋转方向等是一致的
                quad, type_ = get_quad_and_type(obj)
                if op == "number":
                    # 显示序号
                    if type_:
                        type_ = f"{k}-{type_}"
                    else:
                        type_ = str(k)

                quad = quad.transform(m)
                bbox = quad.bbox
                x0, y0, x1, y1 = bbox
                if isinstance(obj, KLine):
                    if line_width is None:
                        w = math.ceil(obj.width)
                    else:
                        w = line_width
                    draw.line(
                        (x0, y0, x1, y1),
                        fill=(255, 255, 0) if obj.is_h() else (0, 0, 255),
                        width=w,
                    )
                    # draw.rectangle(obj.rect_bbox.transform(m),fill=(255,255,0))
                elif isinstance(obj, KRect):
                    # 这个是覆盖原图
                    # draw.rectangle((x0, y0, x1, y1), fill=(255, 0, 0, 20))
                    overlay_draw.rectangle((x0, y0, x1, y1), fill=(255, 0, 0, 20))
                else:
                    # quad = obj.quad.transform(m)
                    # type_ = obj.type
                    color = (
                        random.randint(0, 200),
                        random.randint(0, 200),
                        random.randint(0, 255),
                    )
                    color_a = color + (20,)

                    if use_bbox:
                        draw.rectangle([x0, y0, x1, y1], outline=color, width=4)
                        overlay_draw.rectangle(
                            [x0, y0, x1, y1],
                            fill=color_a,
                            outline=(0, 0, 0, 0),
                            width=1,
                        )
                    else:
                        draw.polygon(quad.points, outline=color, width=4)
                        overlay_draw.polygon(
                            quad.points, fill=color_a, outline=(0, 0, 0, 0), width=1
                        )

                    if type_ and op in ("type", "number"):
                        text_x = x0
                        text_y = max(0, y0 - 15)

                        text_bbox = draw.textbbox((0, 0), type_, font=font)
                        text_width = text_bbox[2] - text_bbox[0]
                        text_height = text_bbox[3] - text_bbox[1]
                        draw.rectangle(
                            [text_x, text_y, text_x + text_width, text_y + text_height],
                            fill=(255, 255, 255, 30),
                        )

                        draw.text((text_x, text_y), type_, font=font, fill=color)

            image.paste(overlay_image, (0, 0), overlay_image)
            return image

        def draw_title(
            title: str, width: int, height: int, font: Any
        ) -> PIL.Image.Image:
            image = PIL.Image.new("RGB", (width, height), (255, 255, 0))
            draw = PIL.ImageDraw.Draw(image)
            text_bbox = draw.textbbox((0, 0), title, font=font, anchor="lt")
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]

            x = int(width / 2 - text_width / 2)
            y = int(height / 2 - text_height / 2)

            x = max(x, 0)
            y = max(y, 0)

            draw.text((x, y), title, font=font, fill=(0, 0, 255), anchor="lt")
            return image

        type_font = get_font()

        images: list[tuple[str, PIL.Image.Image]] = []
        new_columns: list[Any] = []
        for column in columns:
            if len(column) == 2:
                column = [*column, "type" if show_type else None]
            elif len(column) == 3 and isinstance(column[2], bool):
                # ('xx',[],True) => 表示显示类型
                column = [column[0], column[1], "type"]
            else:
                pass
            assert len(column) == 3
            new_columns.append(column)

        for title, obj, op in new_columns:
            if obj is None:
                image = self.image
            elif isinstance(obj, PIL.Image.Image):
                image = obj
            else:
                image = draw_objects(obj, type_font, op)

            images.append((title, image))

        title_font = get_font(30)
        gap_width = 2
        title_height = 40
        new_width = sum(img[1].width for img in images) + gap_width * (
            len(new_columns) - 1
        )
        new_height = max(img[1].height for img in images) + title_height
        # 创建新画布（模式为RGB，背景白色）
        image = PIL.Image.new("RGB", (new_width, new_height), (255, 255, 255))
        gap = PIL.Image.new("RGB", (gap_width, new_height), (255, 255, 0))
        x = 0
        for i, (title, child_img) in enumerate(images):
            # 添加标题？
            title_img = draw_title(title, child_img.width, title_height, title_font)
            image.paste(title_img, (x, 0))
            # 将图片粘贴到新画布上
            image.paste(child_img, (x, title_img.height))  # 左边图片从(0,0)开始
            x += child_img.width
            if i + 1 < len(images):
                image.paste(gap, (x, 0))
                x += gap.width

        if dir:
            if isinstance(dir, str):
                dir = self.doc.out_dir / dir

            file = dir.joinpath(
                f"{self.number}.png" if index is None else f"{self.number}-{index}.png"
            )

        if file:
            if isinstance(file, str):
                file = self.doc.out_dir / file
            file.parent.mkdir(parents=True, exist_ok=True)
            image.save(file)
        return image

    def jsonify(self, lite: bool = False) -> Any:
        data: dict[str, Any] = {
            "number": self.number,
            # "bbox": self.bbox.jsonify(),
            "width": self.width,
            "height": self.height,
            #'header':{},
            #'footer':{},
            #'footnotes':[],
            "objects": [],
        }
        if not lite:
            for obj in self.objects:
                data["objects"].append(obj.jsonify())
        return data

    def markdown(self) -> str:
        buf: list[str] = []
        for obj in self.objects:
            buf.append(obj.markdown())
        return "\n\n".join(buf)

    @property
    def sections(self) -> Sequence[KSection]:
        return self._sections

    def get_section(self, obj: "KObject") -> KSection:
        for section in self._sections:
            if section.contains(obj.bbox):
                return section
        return self._default_section

    def set_blocks(self, blocks: Sequence[BBox]):
        """设置页面按阅读顺序划分的区域"""

        def parse_section(blocks: list[BBox]) -> KSection:
            b1 = blocks.pop(0)
            if not blocks:
                return KSection(self, [b1])

            columns: list[BBox] = [b1]
            while blocks:
                b2 = blocks[0]
                if b2.x0 >= b1.x1:
                    # b1|b2
                    # 考虑到可能有很多回车导致的如下：
                    # b1|
                    #  |b2
                    columns.append(b2)
                    blocks.pop(0)
                else:
                    break
            return KSection(self, columns)

        def check1(sections: Sequence[KSection]) -> bool:
            for i in range(1, len(sections)):
                s1 = sections[i - 1]
                s2 = sections[i]
                # 检查，必须满足格式
                # [--section--]
                # [--section--]
                if s1.bbox.y0 <= s2.bbox.y1 - 5:
                    # 允许一点误差?
                    return False
            return True

        def check2(sections: Sequence[KSection]) -> bool:
            # 检查，是否为单栏/双栏/三栏
            cx: Final = self.bbox.cx
            for section in sections:
                assert len(section.columns) > 0
                if len(section.columns) >= 3:
                    # 现在不支持三栏，后续支持再说
                    return False

                if len(section.columns) == 1:
                    # c1| or
                    # -------
                    # [--c1--]
                    if section.columns[0].x0 > cx:
                        # 允许一点误差
                        return False
                else:
                    # 目前仅仅支持等宽
                    c1 = section.columns[0]
                    c2 = section.columns[1]
                    if c1.x1 > cx + 10 or c2.x0 < cx - 10:
                        return False

            return True

        def cut(section: KSection, x: float) -> list[KSection]:
            # xx
            # ------- 在这里切开
            # xxxxxx
            # xxxxxx
            bbox = section.bbox
            objs = bbox.get(self.objects, ratio=0.8)
            objs.sort(key=lambda obj: obj.bbox.y1, reverse=True)
            idx = 0
            for i, obj in enumerate(objs):
                if obj.bbox.x1 >= x + 5:
                    idx = i
                    break

            if idx == 0:
                return []
            y0 = objs[idx].bbox.y1
            left_bbox = bbox.adjust(x1=x, y0=y0 + 1)
            bottom_bbox = bbox.adjust(y1=y0)
            section1 = KSection(self, [left_bbox])
            section2 = KSection(self, [bottom_bbox])
            return [section1, section2]

        def fix1(sections: list[KSection]):
            # 还需要根据上一页处理几种特别的情况
            # 这种不处理，因为太少见了，使用很多换行来占据空间然后再分栏
            #   |c1
            # -------- 分页
            # c2 |
            # 第二种
            # c1 | c2
            # --------- 分页，因为前一页存在分栏，c2和c3可能跨页合并
            # c3 |      需要把c3也设置为对应的分栏
            # ---c4---
            if not sections:
                return

            if self.number <= 1:
                return

            prev_page = self.doc.get_working_page(self.number - 1)
            if (
                prev_page is None
                or len(prev_page.sections) < 1
                or abs(prev_page.width - self.width) >= 5
            ):
                return

            section = sections[0]
            prev_section = prev_page.sections[-1]

            # TODO 目前仅仅支持双栏的
            if not (
                prev_section.col_num == 2 and section.col_num < prev_section.col_num
            ):
                return

            # a1|a2
            # ---------
            # b1|     => 自动补齐b2

            # 如果是这样
            # a1|a2
            # ----------
            # b1
            # ---b2------  => 需要先把这个给切开

            left_column = prev_section.columns[0]
            right_column = prev_section.columns[1]
            if section.bbox.x1 > self.bbox.cx:
                # 目前仅仅支持双栏且左右相等
                new_sections = cut(section, self.bbox.cx)
                if len(new_sections) != 2:
                    return

                del sections[0]
                sections.insert(0, new_sections[0])
                sections.insert(1, new_sections[1])
                section = sections[0]

            b1 = section.columns[0]
            b2 = b1.adjust(x0=right_column[0], x1=right_column[2])
            sections[0] = KSection(self, [b1, b2])
            self._logger.warning(
                "第%s页，因为上一页为分栏，当前页使用分栏", self.number
            )

        self._blocks = tuple(blocks)
        sections: list[KSection] = []

        new_blocks = list(blocks)
        while new_blocks:
            section = parse_section(new_blocks)
            sections.append(section)

        if len(sections) > 0 and all(fn(sections) for fn in [check1, check2]):
            fix1(sections)
            self._sections = tuple(sections)
        else:
            # 如果不能够分节成功，就仅仅作为一节，不影响阅读理解，章节树分析
            # 只是影响docx的生成
            if blocks:
                default_section = KSection(self, [BBox.join(blocks)])
                self._sections = (default_section,)
            else:
                self._sections = tuple()


class KObject:
    _logging = logging.getLogger(f"{__module__}.{__qualname__}")
    type: str = "object"
    subtype: str | None = None

    # __slots__=['type','subtype','_page','']
    def __init__(self, page: KPage, quad: Quad | BBox, /):
        super().__init__()
        self._page: Final = weakref.ref(page)

        # TODO 如果是为了支持不规则的形状，使用quad可能更好，表示4个点
        # quad=(p1,p2,p3,p4)
        if isinstance(quad, BBox):
            self._bbox = quad
            self._quad = quad.to_quad()
        else:
            self._quad = quad
            self._bbox = quad.bbox

        self.llm_text: str | None = None
        """从llm模型获得的清洗后的结果"""
        self.raw_text: str | None = None
        """从llm模型返回的原始结果，调试使用"""
        self.vobject: VObject | None = None
        """表示来自识别的这个对象"""

        self.cache: Final[dict[str, Any]] = {}
        """临时存储一些数据"""
        self.debug: Final[dict[str, Any]] = {}

    def clear(self):
        self.cache.clear()
        self.debug.clear()

    @property
    def quad(self) -> Quad:
        return self._quad

    @property
    def bbox(self) -> BBox:
        return self._bbox

    @property
    def content_bbox(self) -> BBox | None:
        return self._bbox

    @quad.setter
    def quad(self, quad: Quad):
        self._quad = quad
        self._bbox = quad.bbox

    @bbox.setter
    def bbox(self, bbox: BBox):
        self._quad = bbox.to_quad()
        self._bbox = bbox

    def __del__(self):
        # self._logging.debug("gc %s", self)
        pass

    @property
    def page(self) -> KPage:
        page = self._page()
        if page is None:
            raise RuntimeError("编程错误，page被提前释放")
        return page

    @property
    def doc(self) -> KDocument:
        return self.page.doc

    def jsonify(self) -> Any:
        return {"type": self.type, "bbox": self.bbox.jsonify()}

    def markdown(self) -> str:
        return ""


class KColor:
    BLACK: ClassVar["KColor"]
    WHITE: ClassVar["KColor"]

    def __new__(cls, rgba: Sequence[int], *, alpha: float = 1):
        a: tuple[int, int, int, int]
        if len(rgba) == 1:
            # gray => rgba
            a = (rgba[0], rgba[0], rgba[0], int(alpha * 255))
        elif len(rgba) == 3:
            # rgb => rgba
            a = (rgba[0], rgba[1], rgba[2], int(alpha * 255))
        elif len(rgba) == 4:
            # rgba
            a = tuple(rgba)
        else:
            raise ValueError(f"错误的rgba:{rgba}")

        if hasattr(cls, "BLACK") and a == (0, 0, 0, 255):
            return cls.BLACK
        elif hasattr(cls, "WHITE") and a == (255, 255, 255, 255):
            return cls.WHITE
        else:
            obj = super().__new__(cls)
            obj.rgba = a
            return obj

    rgba: tuple[int, int, int, int]

    def __init__(self, rgba: Sequence[int], *, alpha: float = 1):
        pass

    def is_black(self) -> bool:
        return self is self.BLACK

    def is_white(self) -> bool:
        return self is self.WHITE

    def jsonify(self) -> Any:
        return self.rgba[0:3]
    
    def __eq__(self,other:Any)->bool:
        if other is self:
            return True
        if isinstance(other,KColor):
            return self.rgba==other.rgba
        else:
            return False
    
    def __hash__(self)->int:
        return hash(self.rgba)
    
    def hex(self)->str:
        """返回这种格式ffaabb"""
        r,g,b = self.rgba[0:3]
        return f'{r:02x}{g:02x}{b:02x}'
    
    def hexa(self)->str:
        r,g,b,a = self.rgba
        return f'{r:02x}{g:02x}{b:02x}{a:02x}'

    @classmethod
    def from_list(
        cls,
        color: Sequence[float],
        *,
        is_float: bool = True,
        alpha: float = 1,
        colors: dict[Any, Self] | None = None,
    ):
        if is_float:
            rgba = tuple(int(v * 255) for v in color)
        else:
            rgba = color

        kcolor = cls(rgba, alpha=alpha)
        if colors:
            return colors.setdefault(kcolor.rgba, kcolor)
        else:
            return kcolor


KColor.BLACK = KColor((0, 0, 0, 255))
KColor.WHITE = KColor((255, 255, 255, 255))


class KFont:
    OCR: ClassVar["KFont"]
    # PDF:ClassVar['KFont']
    MONOSPACE: ClassVar["KFont"]
    """等宽字体，如："""
    SERIF: ClassVar["KFont"]
    """衬线字体字体，如：宋体，Times New Roman，Calibri"""
    SANS_SERIF: ClassVar["KFont"]
    """无衬线字体，如：微软雅黑，思源宋体，思源黑体、阿里巴巴普惠体，鸿蒙黑体等"""
    WINGDINGS: ClassVar["KFont"]
    """符号字体"""
    WINGDINGS2: ClassVar["KFont"]
    """符号字体"""
    WINGDINGS3: ClassVar["KFont"]
    """符号字体"""

    def __init__(
        self,
        name: str,
        *,
        monospace: bool = False,
        serif: bool = False,
        sans_serif: bool = False,
        wingdings: bool = False,
    ):
        super().__init__()
        self.name: Final = name
        self.monospace: Final = monospace
        self.serif: Final = serif
        self.sans_serif: Final = sans_serif
        self.wingdings: Final = wingdings
        """表示word中使用的wingdings字体，unicode统一转化为0xf020-0xf0ff区域
        如果需要还原，需要指定使用wingdings字体，如果需要使用标准unicode（有些新的unicode，并不是所有字体都支持），
        目前需要使用：NotoSansSymbols2-Regular.ttf字体，还需要做一个从wingdings的unicode转化为标准的unicode，
        在生成docx，使用wingdings字体+私有unicode，在生成html，可以使用2者，指定字体即可
        """

    def wingdings2standard(self, text: str) -> str:
        from .wingdings import wingdings2standard

        return wingdings2standard(self.name, text)

    def standard2wingdings(self, text: str) -> str:
        from .wingdings import standard2wingdings

        return standard2wingdings(self.name, text)

    def normalize_wingdings(
        self,
        img: Any,
        bbox: Sequence[float],
        text: str,
        default: str = "□",
        force: bool = False,
    ) -> tuple[str, str]:
        from .wingdings import WingdingsRecognizer, wingdings2standard
        
        if text.isspace():
            w_text=text
        elif not force and 0xF020 <= ord(text) <= 0xF0FF:
            # 认为是准确的？
            w_text = text
        else:
            w_text = WingdingsRecognizer.get(self.name).recognize(img, [bbox])[0]
        if w_text:
            return w_text, wingdings2standard(self.name, w_text)
        else:
            return (text, text)

    @classmethod
    def get(cls, name: str, monospace: bool = False, serif: bool = False) -> "KFont":
        name = re.sub(r"[\s]", "", name.lower())
        if "wingdings2" in name:
            return cls.WINGDINGS2
        elif "wingdings3" in name:
            return cls.WINGDINGS3
        elif "wingdings" in name:
            return cls.WINGDINGS
        elif serif:
            return cls.SERIF
        elif monospace:
            return cls.MONOSPACE
        else:
            return cls.SANS_SERIF


# 对于ocr，无法获得字体，使用这个表示
# 对于pdf，可以获得字体，但是意义不大，因为字体名字是随意的，而且字形也不知道具体显示什么
# 字体的size也没有意义，所以，简单的使用一个字体就可以
# 如果需要再复杂一点，就可以多3个，monospaced（等宽），serif（衬线字体），san-serif（非衬线）
KFont.OCR = KFont("ocr", monospace=True)
# KFont.PDF=KFont('pdf')
KFont.MONOSPACE = KFont("monospace", monospace=True)
KFont.SERIF = KFont("serif", serif=True)
KFont.SANS_SERIF = KFont("sans-serif", sans_serif=True)
KFont.WINGDINGS = KFont("wingdings", wingdings=True)
KFont.WINGDINGS2 = KFont("wingdings2", wingdings=True)
KFont.WINGDINGS3 = KFont("wingdings3", wingdings=True)


class KChar(KObject):
    type = "char"

    def __init__(
        self,
        page: KPage,
        quad: Quad,
        *,
        source: CharSource = CharSource.UNKNOWN,
        bold: bool = False,
        italic: bool = False,
        underline: bool = False,
        strikeout: bool = False,
        text: str,
        font: KFont = KFont.MONOSPACE,
        color: KColor = KColor.BLACK,
        subtype: str | None = None,
        raw_text: str | None = None,
        wingdings_text: str | None = None,
    ):
        super().__init__(page, quad)
        # self.page:Final=page
        # self.quad:Final=quad
        # self.bbox:Final=quad.bbox

        self.text: Final = text
        self.font: Final = font
        self.color: Final = color

        self.bold: Final = bold
        """粗体"""
        self.italic: Final = italic
        """斜体"""
        self.underline = underline
        """下划线"""
        self.strikeout = strikeout
        """删除线"""

        # self.subtype:str|None=None
        self.source: CharSource = source
        """来自pdf还是ocr还是llm"""
        self.seqno: int = -1
        """如果来自pdf的，可以获得书写序号，多个字符可以为同一个seqno，表示在字体等相同的连续指令中"""

        self.raw_text = raw_text
        self.wingdings_text: str | None = wingdings_text
        """如果是wingdings的字符，可以获得pua区域的文本，方便在生成docx的时候直接使用"""
        self.subtype = subtype

        self.footnote_ref:KFootnoteRef|None=None
        """如果该字符后面有一个上标引用了一个脚注"""

    def alike(self, obj: "KChar") -> bool:
        c1 = self
        c2 = obj
        return (
            c1.bold == c2.bold
            and c1.italic == c2.italic
            and c1.strikeout == c2.strikeout
            and c1.underline == c2.underline
            and c1.color == c2.color
            and c1.font == c2.font
            and c1.source == c2.source
            and c1.subtype==c2.subtype
            and abs(c1.bbox.height - c2.bbox.height) <= 2
        )
    
    def is_superscript(self)->bool:
        """表示为上标字符"""
        return self.subtype=='superscript'
    
    def is_subscript(self)->bool:
        """表示为下标字符"""
        return self.subtype=='subscript'

    @cached_property
    def index(self)->int:
        """获得书写顺序，仅仅对来自pdf字符有效，其他的都返回-1"""
        try:
            return self.page.pdf_chars.index(self)
        except ValueError:
            return -1

    
    @cached_property
    def min_bbox(self) -> BBox:
        if self.text in "》】）｝］？；。：，！、":
            return self.bbox.adjust(x1=self.bbox.cx)
        elif self.text in "《【（｛［":
            return self.bbox.adjust(x0=self.bbox.cx)
        else:
            return self.bbox

    def markdown(self) -> str:
        return _md_escape(self.text)

    def jsonify(self):
        data = {"bbox": self.bbox.jsonify(), "text": self.text}
        if self.bold:
            data["bold"] = True
        if self.italic:
            data["italic"] = True
        if self.underline:
            data["underline"] = True
        if self.strikeout:
            data["strikeout"] = True
        # 默认为黑色
        if not self.color.is_black():
            data["color"] = self.color.jsonify()

        data["font"] = self.font.name
        # if self.wingdings_text:
        # data['wingdings']=self.wingdings_text
        return data

    def is_valid(self) -> bool:
        """判断是否为有效的字符"""
        # 65533就是无效的unicode
        # BMP PUA0xE000 – 0xF8FF
        # 补充PUA-A0xF0000 – 0xFFFFF
        # 补充PUA-B0x100000 – 0x10FFFF

        if self.font.wingdings:
            # TODO 通常表示列表符号，通过ocr也很难识别，因为不一定有对应的标准字符，而且显示方式各异
            # 所以仍然认为是有效的字符，或者在pdf解析的过程中，就把这些字符给去掉了
            return True
        codepoint = ord(self.text)
        if codepoint == 0xFFFD:
            return False
        elif (
            0xE000 <= codepoint <= 0xF8FF
        ):  # or 0xf0000<=codepoint<=0xffffd or 0x100000<=codepoint<=0x10ffffd:
            # 使用了私有域
            # U+F0000 ~ U+FFFFD
            # U+100000 ~ U+10FFFD
            return False
        else:
            return True




class KSpan(KObject):
    """表示一串属性相同的字符"""

    type = "span"

    def __init__(
        self,
        page: KPage,
        quad: Quad | BBox,
        *,
        chars: Sequence[KChar],
        score: float = 1,
    ):
        assert len(chars) > 0
        super().__init__(page, quad)
        self.chars: Final[Sequence[KChar]] = tuple(chars)
        self.score: float = 1

    @cached_property
    def text(self) -> str:
        return "".join(c.text for c in self.chars)

    @property
    def bold(self) -> bool:
        return self.chars[0].bold

    @property
    def italic(self) -> bool:
        return self.chars[0].italic

    @property
    def underline(self) -> bool:
        return self.chars[0].underline

    @property
    def strikeout(self) -> bool:
        return self.chars[0].strikeout

    @property
    def font(self) -> KFont:
        return self.chars[0].font

    @property
    def color(self) -> KColor:
        return self.chars[0].color


class KTextline(KObject):
    """文本行，支持行内公式，行内图片等"""

    type = "textline"

    def __init__(self, page: KPage, quad: Quad, *, objects: Sequence[KObject]):
        assert len(objects) > 0
        super().__init__(page, quad)
        self.objects: Final[Sequence[KObject]] = tuple(objects)

    @cached_property
    def chars(self) -> Sequence[KChar]:
        return tuple(c for c in self.objects if isinstance(c, KChar))

    @cached_property
    def figures(self) -> Sequence["KFigure"]:
        return tuple(c for c in self.objects if isinstance(c, KFigure))

    @cached_property
    def formulas(self) -> Sequence["KFormula"]:
        return tuple(c for c in self.objects if isinstance(c, KFormula))

    @cached_property
    def text(self) -> str:
        return "".join(c.text for c in self.chars)

    def split(self) -> list[KObject]:
        """把相邻的属性相同的字符串合并为一个span，方便渲染"""

        def join(group: list[KObject], obj: KObject) -> bool:
            if not (isinstance(obj, KChar) and isinstance(group[-1], KChar)):
                return False
            c1 = group[-1]
            c2 = obj
            if -2 <= c2.bbox.x0 - c1.bbox.x1 <= 3 and c1.alike(c2):
                group.append(c2)
                return True
            else:
                return False

        def is_span(group: Sequence[KObject]) -> TypeGuard[Sequence[KChar]]:
            return isinstance(group[0], KChar)

        groups: list[list[KObject]] = []
        group: list[KObject] = [self.objects[0]]
        groups.append(group)
        for obj in self.objects[1:]:
            if not join(group, obj):
                group = [obj]
                groups.append(group)

        new_objects: list[KObject] = []
        for group in groups:
            if is_span(group):
                span = KSpan(self.page, BBox.join2(group), chars=group)
                new_objects.append(span)
            else:
                new_objects.extend(group)

        return new_objects

    @override
    def markdown(self) -> str:
        return self.render_markdown(self.objects)

    def __add__(self, other: Self):
        quad = Quad.join([self.quad, other.quad])
        return KTextline(self.page, quad, objects=[*self.objects, *other.objects])

    def set_underline(self, bbox: BBox, *, dx: float = 2):
        """设置指定范围内的字符串有下划线"""
        found = False
        for char in self.chars:
            b = char.min_bbox
            if b.x0 >= bbox.x0 - dx and b.x1 <= bbox.x1 + dx:
                char.underline = True
                found = True
            elif found:
                break

    def set_strikeout(self, bbox: BBox, *, dx: float = 2):
        """设置指定范围内的字符串有删除线"""
        found = False
        for char in self.chars:
            b = char.min_bbox
            if b.x0 >= bbox.x0 - dx and b.x1 <= bbox.x1 + dx:
                char.strikeout = True
                found = True
            elif found:
                break

    def jsonify(self) -> Any:
        return {
            "bbox": self.bbox.jsonify(),
            "text": self.text,
            "objects": [obj.jsonify() for obj in self.objects],
        }

    @classmethod
    def render_markdown(cls, objects: Sequence[KObject], use_style: bool = True) -> str:
        def alike(c1: KChar, c2: KChar) -> bool:
            return (
                c1.bold == c2.bold
                and c1.italic == c2.italic
                and c1.underline == c2.underline
                and c1.strikeout == c2.strikeout
            )

        def split_groups(objs: Sequence[KObject]):
            groups: list[list[KObject]] = []
            group = [objs[0]]
            groups.append(group)
            for obj in objs[1:]:
                if (
                    isinstance(obj, KChar)
                    and isinstance(group[-1], KChar)
                    and alike(group[-1], obj)
                ):
                    group.append(obj)
                else:
                    group = [obj]
                    groups.append(group)
            return groups

        def is_punctuation(s: str) -> bool:
            from memect.base import strs
            import string

            a, b = strs.to_bq(s)
            return a in string.punctuation

        buf: list[str] = []
        if use_style:
            for group in split_groups(objects):
                if isinstance(group[0], KChar):
                    s = "".join(c.text for c in cast(Sequence[KChar], group))
                    if not s.strip():
                        buf.append(s)
                    else:
                        # 前后的空格无法显示粗体，所以就先去掉空格
                        m = re.search(r"^(\s*)(.*?)(\s*)$", s)
                        if m:
                            # 去掉前后空格
                            prefix = m.group(1)
                            suffix = m.group(3)
                            s = m.group(2)
                        else:
                            prefix = ""
                            suffix = ""

                        buf.append(prefix)
                        has_style = False
                        c = group[0]
                        if c.underline:
                            buf.append("<u>")
                            has_style = True
                        if c.strikeout:
                            buf.append("~~")
                            has_style = True
                        if c.italic:
                            buf.append("*")
                            has_style = True
                        if c.bold:
                            buf.append("**")
                            has_style = True

                        buf.append(_md_escape(s))
                        if c.italic:
                            buf.append("*")
                        if c.bold:
                            buf.append("**")
                        if c.strikeout:
                            buf.append("~~")
                        if c.underline:
                            buf.append("</u>")

                        buf.append(suffix)
                        if (
                            has_style
                            and len(s) > 0
                            and buf[-1] != " "
                            and is_punctuation(s[-1])
                        ):
                            # 如果最后为标点，需要添加一个空格才能够表示为加粗
                            # 如：**abc:** => 不会被渲染为粗体，后面需要添加一个空格
                            buf.append(" ")
                else:
                    for obj in group:
                        buf.append(obj.markdown())
        else:
            for obj in objects:
                buf.append(obj.markdown())
        return "".join(buf)

    @classmethod
    def parse(cls, objects: Sequence[KObject], *, strip: bool = True) -> list[Self]:
        if not objects:
            return []

        def split_groups(objects: Sequence[KObject]) -> list[Group[KObject]]:
            """为了更好的支持下标/上标字符串，先局部分组再分行"""
            objects = sorted(objects, key=lambda obj: obj.bbox.x0)
            groups: list[Group[KObject]] = []
            while objects:
                group = split_group(objects)
                groups.append(group)
            return groups

        def split_group(objects: list[KObject]) -> Group[KObject]:
            group: Group[KObject] = Group()
            group.append(objects.pop(0))
            i = 0
            dx = 3
            while i < len(objects):
                obj1 = group[-1]
                obj2 = objects[i]
                if obj2.bbox.x0 - obj1.bbox.x1 > dx:
                    # [obj1]-dx-[obj2]
                    break
                if (
                    obj2.bbox.x0 > obj1.bbox.x0
                    and -10 <= obj2.bbox.x0 - obj1.bbox.x1
                    and abs(obj1.bbox.y1 - obj2.bbox.y1) <= 2
                    and abs(obj1.bbox.y0 - obj2.bbox.y0) <= 2
                ):
                    # [obj1][obj2]，可能重叠一点点，特别是半角符号
                    group.append(obj2)
                    del objects[i]
                else:
                    i += 1
            group.invalidate()
            return group

        def parse_line(groups: list[Group[KObject]]) -> Group[KObject]:
            # 有行内公式，图片等
            # 还有行间公式，图片等
            # 还有上下标

            # 还需要考虑特殊的情况，如：
            # ---line1---
            #             --line3-- 在旁边且居中对齐，这是对象识别的区域不够细
            # ---line2---
            line: Group[Group[KObject]] = Group()
            line.append(groups.pop(0))
            line.invalidate()
            i = 0
            while i < len(groups):
                # 使用这个可能会导致误差累计，使用高度最大的一个
                # b1 = line.bbox
                b1 = max(line, key=lambda g: g.bbox.height).bbox
                b2 = groups[i].bbox
                # 表示有n个单位重叠
                d = min(b2.height // 2, b1.height // 2)
                # 至少需要重叠4个单位，如果是上下标，如何处理？
                d = max(d, 4)
                if b1.over("y", b2, d=d):  # and abs(b1.height-b2.height)<=6:
                    # [b1][b2]
                    # 如果为两行重叠的，分开，如：
                    # [------line1----]
                    #                  [----line2---]
                    # 同时删除该对象
                    line.append(groups.pop(i))
                    line.invalidate()
                # 已经按y1排序，不需要
                elif b2.y1 <= b1.y0:
                    # [b1]
                    # [b2] =>接下来的都会更低，不需要再继续
                    break
                else:
                    i += 1

            # TODO 如果需要如下情况，也就是line3跨了line1，line2，这是对象识别的错误，不应该包含line3的
            # --line1--
            #            --line3--
            # --line2--

            # local/cases/test/ocr-4-页面由多个图片组成.pdf 第1页
            # 设置为False，返回:[line1+line3,line2]
            # 设置为True，返回：[line1,line2+line3]
            # 如果外面use_column=True，返回[line1,line2,line3]
            handle_overlap_lines = False
            if handle_overlap_lines and groups:
                line.sort(key=lambda g: g.bbox.y0)
                while len(line) > 1:
                    if groups[0].bbox.y1 - line[0].bbox.y0 >= 5:
                        # 如果和下一列有重叠，把这个重叠的去掉？
                        groups.append(line.pop(0))
                        groups.sort(key=lambda group: group.bbox.y1, reverse=True)
                    else:
                        break

            # 简单的排序了，如果需要支持更完美，同时有上下标字符串的，需要先排序上标
            new_line: Group[KObject] = Group()
            sort_method = 2
            if sort_method == 1:

                def cmp(g1: Group[KObject], g2: Group[KObject]) -> int:
                    if g1.bbox.y0 - g2.bbox.y1 >= -1 and g1.bbox.over(
                        "x", g2.bbox, d=4
                    ):
                        return -1
                    elif g1.bbox.y1 - g2.bbox.y0 <= -1 and g1.bbox.over(
                        "x", g2.bbox, d=4
                    ):
                        return 1
                    else:
                        return int(g1.bbox.x0 - g2.bbox.x0)

                # TODO 如果group的顺序已经错位了，如：
                # [--------g1------]
                #    [g2]    => 属于中间的，那么排序就不正确的
                # line.sort(key=lambda obj:obj.bbox.x0)
                lists.sort(line, cmp=cmp)
                for g in line:
                    new_line.extend(g)
            else:
                for g in line:
                    new_line.extend(g)
                new_line.sort(key=lambda obj: obj.bbox.x0)

            new_line.invalidate()
            # print('=======>line',[(c.text,c.bbox) for c in new_line if isinstance(c,Char)])
            # 可能还需要补充连续的下标字符串，如：
            # ABC[123]D => 123表示下标字符串
            # ABC   D => 目前解析了D
            return new_line

        def split_columns(
            groups: list[Group[KObject]], max_gap_width: float = 8
        ) -> list[Group[Group[KObject]]]:
            if not groups:
                return []
            columns: list[Group[Any]] = []
            groups.sort(key=lambda g: g.bbox.x0)
            columns.append(Group())
            for group in groups:
                if columns[-1] and group.bbox.x0 - columns[-1].bbox.x1 > max_gap_width:
                    columns.append(Group())
                    columns[-1].append(group)
                else:
                    columns[-1].append(group)

                columns[-1].invalidate()

            # 再检查是否真的需要分开，如果列之间没有跨行重叠的
            if len(columns) == 1:
                return columns

            return columns

        def strip_line(line: Group[KObject]):
            while line:
                obj = line[0]
                if isinstance(obj, KChar) and obj.text.isspace():
                    del line[0]
                else:
                    break
            while line:
                obj = line[-1]
                if isinstance(obj, KChar) and obj.text.isspace():
                    del line[-1]
                else:
                    break

        groups = split_groups(objects)
        use_columns = False
        if use_columns:
            columns = split_columns(groups)
        else:
            columns = [groups]

        lines: list[Group[KObject]] = []
        for column in columns:
            column.sort(key=lambda group: group.bbox.y1, reverse=True)
            while column:
                lines.append(parse_line(column))

        if strip:
            i = 0
            while i < len(lines):
                line = lines[i]
                strip_line(line)
                if not line:
                    del lines[i]
                else:
                    i += 1
        page = objects[0].page
        return [
            cls(
                page,
                line[0].quad if len(line) == 1 else line.bbox.to_quad(),
                objects=line,
            )
            for line in lines
        ]


class KText(KObject):
    """文本块，知道每一个字符的具体坐标，阅读顺序从上到下，从左到右。
    如果将来需要支持从右到左阅读的，或者垂直书写的，从左到右，从右到左的，这些都是需要专门的处理，
    不在这里处理。
    """

    type = "text"

    def __init__(
        self,
        page: KPage,
        quad: Quad | BBox,
        *,
        lines: Sequence[KTextline] | None = None,
        text: str | None = None,
        md_text: str | None = None,
    ):
        super().__init__(page, quad)

        if lines and text is not None:
            # 设置了lines，表示有坐标的方式
            raise ValueError("lines和text只能够指定一个，现在2个都设置了")

        if text is not None and md_text is None:
            # 如果指定了文本但是没有原始markdown，就马上转义一个
            md_text = _md_escape(text)

        self.lines: Final[Sequence[KTextline]] = tuple(lines or [])
        self._raw_text: Final = text
        """直接的文本，因为没有单个字符的坐标"""
        self._md_text: Final = md_text
        """原始的markdown"""

    @cached_property
    def text(self) -> str:
        if self._raw_text is not None:
            return self._raw_text
        else:
            return "".join(line.text for line in self.lines)

    @cached_property
    def text2(self) -> str:
        """去掉了空格，全角变成了半角，合适正则表达式使用"""
        return NText.get(self.text, mode="q2b", space="remove").text

    @cached_property
    def chars(self) -> Sequence[KChar]:
        return tuple(lists.join(line.chars for line in self.lines))

    @cached_property
    def objects(self) -> Sequence[KObject]:
        return tuple(lists.join(line.objects for line in self.lines))

    @cached_property
    def figures(self) -> Sequence["KFigure"]:
        return tuple(lists.join(line.figures for line in self.lines))

    @cached_property
    def formulas(self) -> Sequence["KFormula"]:
        return tuple(lists.join(line.formulas for line in self.lines))

    @override
    def markdown(self) -> str:
        """需要按原文换行吗？"""
        if self._md_text is not None:
            return self._md_text
        else:
            # 对于markdown，可以直接输出为一段长文本即可，不需要添加换行符
            keep_lr = False
            buf: list[str] = []
            if keep_lr:
                for tl in self.lines:
                    buf.append(tl.markdown())
                return "\n".join(buf)
            else:
                objs: list[KObject] = []

                for i, tl in enumerate(self.lines):
                    # TODO 如果是英文的，可能在换行后，需要添加一个空格，如：
                    # hello
                    # world  => hello world
                    # if padding_space(i,self.lines):
                    # objs.append(KText(self.page,None,text=' '))
                    # pass
                    objs.extend(tl.objects)
                return KTextline.render_markdown(objs)

    def __add__(self, other: Self) -> Self:
        quad = Quad.join([self.quad, other.quad])
        if len(self.lines) > 0 and len(other.lines) > 0:
            # 如果都是有坐标的
            lines = [*self.lines, *other.lines]
            return self.__class__(self.page, quad, lines=lines)
        else:
            # 有一个没有坐标，就变成没有坐标的
            text = self.text + other.text
            md_text = self.markdown() + other.markdown()
            return self.__class__(self.page, quad, text=text, md_text=md_text)

    def jsonify(self):
        return {
            "type": "text",
            "bbox": self.bbox.jsonify(),
            "text": self.text,
            "lines": [line.jsonify() for line in self.lines],
        }

    @classmethod
    def join(cls, tbs: Sequence[Self]) -> Self:
        assert len(tbs) > 0
        quad = Quad.join([tb.quad for tb in tbs])
        page = tbs[0].page
        return cls(page, quad, lines=[line for tb in tbs for line in tb.lines])

    @classmethod
    def from_objects(
        cls, objects: Sequence[KObject], *, strip: bool = True
    ) -> Self | None:
        assert len(objects) > 0
        quad = Quad.join([c.quad for c in objects])
        page = objects[0].page
        # 可能存在空白的行，然后被去掉了，这个时候就没有KTextbox了
        lines = KTextline.parse(objects, strip=strip)
        if len(lines) == 0:
            return None
        else:
            return cls(page, quad, lines=lines)

    @classmethod
    def from_markdown(cls, page: KPage, bbox: BBox | Quad, text: str) -> Self:
        """根据markdown构造"""
        text = cls.md_unescape(text)
        return cls(page, bbox, text=text, md_text=text)

    @classmethod
    def md_escape(cls, text: str) -> str:
        return _md_escape(text)

    @classmethod
    def md_unescape(cls, text: str) -> str:
        """将 markdown 文本还原为纯文本，行内公式保留，还是$xx$"""
        # TODO 将来复杂的情况，如：可以为代码，html，或者公式，这些如何处理？
        # ```code```
        # <table></table>
        # $$xxx$$

        # 1. 移除标题标记 (# ## ### 等)
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

        # 2. 移除粗体 (**text** 或 __text__)
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"__(.+?)__", r"\1", text)

        # 3. 移除斜体 (*text* 或 _text_)
        text = re.sub(r"\*(.+?)\*", r"\1", text)
        text = re.sub(r"_(.+?)_", r"\1", text)

        # 4. 移除删除线 (~~text~~)
        text = re.sub(r"~~(.+?)~~", r"\1", text)

        # 5. 移除行内代码 (`code`)
        text = re.sub(r"`(.+?)`", r"\1", text)

        # 6. 移除链接 [text](url) -> text
        text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)

        # 7. 移除图片 ![alt](url) -> alt
        text = re.sub(r"!\[(.+?)\]\(.+?\)", r"\1", text)

        # 8. 移除转义符 (\* \# 等)
        text = re.sub(r"\\([`*_+\-!{}#.\\])", r"\1", text)

        # 9. 行内公式 "$xxx$" or "\(xxx\)"
        # 如何转换为文本？把公式扔了？或者不做改变？
        # 有些上标或者下标也是使用行内公式表示

        return text


class KFigure(KObject):
    type: str = "figure"

    def __init__(self, page: KPage, quad: Quad|BBox, *, filename: str):
        super().__init__(page, quad)
        self.filename: Final = filename
        """如：images/1.png，相对doc.md"""

    @cached_property
    def fullpath(self) -> Path:
        return self.doc.out_dir / self.filename

    def markdown(self) -> str:
        name = _md_escape(self.fullpath.name)
        return f"![{name}](./images/{name})"

    def jsonify(self):
        return {
            "type": "figure",
            "bbox": self.bbox.jsonify(),
            "filename": self.filename,
        }


class TableIntent(StrEnum):
    DATA=auto()
    LAYOUT=auto()

class ChartLayout:
    def __init__(self,title:bool=False,body:bool=False,source:bool=False):
        super().__init__()
        self.title=title
        self.body=body
        self.source=source

    def is_ok(self)->bool:
        return self.title and self.body and self.source
    def is_title(self)->bool:
        return self.title and not self.body and not self.source
    def is_source(self)->bool:
        return self.source and not self.body and not self.title
    
    def no_source(self)->bool:
        return self.body and not self.source
    
    def no_title(self)->bool:
        return self.body and not self.title

class KTable(KObject):
    type: str = "table"

    def __init__(self, page: KPage, quad: Quad | BBox, *, cells: Sequence["KCell"],subtype:str|None=None):
        super().__init__(page, quad)
        assert len(cells)>0
        self.row_num = max(c.row_index+c.row_span for c in cells)
        self.col_num = max(c.col_index+c.col_span for c in cells)

        self._validate(cells)
        for cell in cells:
            cell.table = self
        


        self.cells: Sequence[KCell] = tuple(cells)
        self.filename: str = ""
        """对应的截图的文件名，在llm且只需要获得markdown下，不一定需要截图"""
        self.grid: list[list[KCell]] = []
        self.subtype = subtype or 'wbk'
        """默认都是无边框表格，除非是直接使用线解析的"""

        # 跨页表格合并需要的属性
        # main=(header,body)
        self.header: Self | None = None
        """在跨页合并的时候，可以分离表头和表体"""
        self.body: Self | None = None
        """在跨页合并的时候，可以分离表头和表体"""
        self.main: Self | None = None
        """如果是header/body表格，可以获得其的主表格"""

        self.merged: bool = False
        """True表示跨页/跨栏合并了"""
        self.merge_info: Any = None
        """可以记录合并后的一些信息"""

        self.working_state: Any = None
        """合并或者其他操作可以设置临时状态"""

        self.grid = self._create_grid()

        #self.intent:TableIntent = TableIntent.DATA
        #"""表示该表格的用途，如：layout"""

        self.row_colors:list[KColor]=[]
        self.col_colors:list[KColor]=[]

        self.chart_layout:ChartLayout|None=None
        """表示为图表布局，且获得对应的信息，方便跨页表格合并"""

    def _create_grid(self):
        # TODO 如果需要快速的访问，建立一个n*m的grid
        grid: list[list[KCell]] = []
        for i in range(self.row_num):
            row = [None] * self.col_num
            grid.append(row) # type: ignore

        for cell in self.cells:
            for i in range(cell.row_index, cell.row_index + cell.row_span):
                for j in range(cell.col_index, cell.col_index + cell.col_span):
                    grid[i][j] = cell
        return grid
    
    def is_layout(self)->bool:
        """表示表格的意图是布局"""
        return self.subtype=='layout'

    def is_wbk(self)->bool:
        return self.subtype=='wbk'
    
    def is_ybk(self)->bool:
        return self.subtype=='ybk'
    
    @cached_property
    def fullpath(self) -> Path:
        """截图的完整路径"""
        # 必须设置才能够调用
        assert self.filename
        return self.doc.out_dir / self.filename

    def get_row(self, index: int) -> list["KCell"]:
        if index < 0:
            index += self.row_num
        row: list[KCell] = []
        for cell in self.cells:
            if cell.row_index <= index < cell.row_index + cell.row_span:
                row.append(cell)
        return row

    def get_column(self, index: int) -> list["KCell"]:
        if index < 0:
            index += self.col_num
        column: list[KCell] = []
        for cell in self.cells:
            if cell.col_index <= index < cell.col_index + cell.col_span:
                column.append(cell)
        return column

    def __getitem__(self, key: tuple[int, int]) -> "KCell":
        # 因为页面的单元格比较少，如：self.row_num*self.col_num的表格，如果需要遍历所有单元格，且都是访问最后一个，就需要执行
        # self.row_num*self.col_num*len(self.cells)次，太慢了
        row_index, col_index = key
        if True:
            return self.grid[row_index][col_index]
        else:
            for cell in self.cells:
                if (
                    cell.row_index <= row_index < cell.row_index + cell.row_span
                    and cell.col_index <= col_index < cell.col_index + cell.col_span
                ):
                    return cell
            raise ValueError(f"错误的的行列坐标:{key}")

    def set_body(self, row_index: int | None):
        """设置(0,row_index)为header，(row_index,end)为body"""
        if row_index is None:
            if self.header is not None:
                for cell in self.header.cells:
                    cell.subtype = None
                    if cell.original_cell is not None:
                        cell.original_cell.subtype = None
            self.header = None
            self.body = None
        else:
            self.header, self.body = self.split(row_index)
            if self.header is not None:
                # 同时可以标记这些为表头，这样在生成html的时候，可以标记为跨页表头，知道这部分内容不参与合并
                # 当然，第一个表格的表头还是参与合并的
                for cell in self.header.cells:
                    cell.subtype = "header"
                    if cell.original_cell is not None:
                        cell.original_cell.subtype = "header"

    def split(self, row_index: int) -> tuple[Self | None, Self | None]:
        """在指定的行分开"""
        header_cells: list[KCell] = []
        body_cells: list[KCell] = []
        for cell in self.cells:
            if cell.row_index < row_index:
                # 出现错误表示识别的header错误了
                assert cell.row_index + cell.row_span <= row_index
                header_cells.append(cell)
            else:
                body_cells.append(cell)

        if not body_cells:
            return (self, None)
        elif not header_cells:
            return (None, self)
        else:
            header = self._from_cells(header_cells)
            header.main = self
            body = self._from_cells(body_cells)
            body.main = self
            return (header, body)

    def select(self, start: int, end: int, *, strict: bool = False) -> Self:
        """
        选择指定范围的行，返回一个新的表格，
        start: 开始行
        end： 结束行（不包括）

        strict：True表示严格到end结束，False表示如果跨行超过end，自动调整，返回对齐的
        """
        cells: list[KCell] = []
        for cell in self.cells:
            if strict:
                # 表示严格到end结束，必须对齐
                assert cell.row_index + cell.row_span <= end
                if cell.row_index >= start:
                    cells.append(cell)
            else:
                if start <= cell.row_index < end:
                    # 如果溢出了，自动调整end
                    end = max(end, cell.row_index + cell.row_span)
                    cells.append(cell)
        
        return self._from_cells(cells)


    def _get_lines(self,cells:Sequence["KCell"]) -> tuple[list["KLine"],list["KLine"]]:
        """根据当前cells的bbox，构造水平线和垂直线（合并共线重叠段）。"""
        horiz: list[tuple[float, float, float]] = []  # (y, x0, x1)
        vert: list[tuple[float, float, float]] = []  # (x, y0, y1)
        for cell in cells:
            x0, y0, x1, y1 = cell.bbox.x0, cell.bbox.y0, cell.bbox.x1, cell.bbox.y1
            horiz.append((y0, x0, x1))
            horiz.append((y1, x0, x1))
            vert.append((x0, y0, y1))
            vert.append((x1, y0, y1))

        def _merge(
            segs: list[tuple[float, float, float]],
        ) -> list[tuple[float, float, float]]:
            if not segs:
                return []
            segs = sorted(segs, key=lambda s: (round(s[0], 2), s[1]))
            merged: list[tuple[float, float, float]] = []
            for coord, a, b in segs:
                if (
                    merged
                    and abs(merged[-1][0] - coord) < 0.5
                    and a <= merged[-1][2] + 0.5
                ):
                    pc, pa, pb = merged[-1]
                    merged[-1] = (pc, pa, max(pb, b))
                else:
                    merged.append((coord, a, b))
            return merged

        h_lines: list[KLine] = []
        v_lines: list[KLine] =[]
        for y, x0, x1 in _merge(horiz):
            h_lines.append(KLine(self.page, BBox(x0, y, x1, y)))
        for x, y0, y1 in _merge(vert):
            v_lines.append(KLine(self.page, BBox(x, y0, x, y1)))
        return h_lines,v_lines
    
    def get_lines(self)->tuple[list["KLine"],list["KLine"]]:
        return self._get_lines(self.cells)
    
    def get_lines2(self)->list["KLine"]:
        h_lines,v_lines=self.get_lines()
        return h_lines+v_lines
    

    def strip(self,top:bool=True,bottom:bool=True,left:bool=False,right:bool=False,all:bool=False)->Self:
        """去掉上下空白的行或者左右空白的列"""

        def is_blank(cells:Sequence[KCell])->bool:
            for c in cells:
                if len(c.objects)!=0:
                    return False
            return True
        
        def is_flat_row(cells:Sequence[KCell],row_index:int)->bool:
            for c in cells:
                if c.row_index==row_index and c.row_span==1:
                    pass
                else:
                    return False
            return True
        
        def is_flat_column(cells:Sequence[KCell],col_index:int)->bool:
            for c in cells:
                if c.col_index==col_index and c.col_span==1:
                    pass
                else:
                    return False
            return True
        
        if all:
            top=True
            bottom=True
            left=True
            right=True

        start_row_index=0
        end_row_index = self.row_num
        start_col_index=0
        end_col_index=self.col_num
        if top:
            #去掉顶部空白的行
            for i in range(self.row_num):
                row = self.get_row(i)
                #不能够存在跨行的，可以存在跨列，且所有单元格都为空
                if is_blank(row) and is_flat_row(row,i):
                    start_row_index=i+1
                else:
                    break
        if bottom:
            for i in range(self.row_num-1,-1,-1):
                row = self.get_row(i)
                if is_blank(row) and is_flat_row(row,i):
                    end_row_index=i
                else:
                    break

        if left:
            for i in range(self.col_num):
                col = self.get_column(i)
                if is_blank(col) and is_flat_column(col,i):
                    start_col_index=i+1
                else:
                    break

        if right:
            for i in range(self.col_num-1,-1,-1):
                col = self.get_column(i)
                if is_blank(col) and is_flat_column(col,i):
                    end_col_index=i
                else:
                    break
        
        n1=end_row_index-start_row_index
        n2=end_col_index-start_col_index
        if n1==self.row_num and n2==self.col_num or n1==0 or n2==0:
            #如果没有改变或者变成一行/一列都没有，就不改变
            return self
        
        
        new_cells:list[KCell]=[]
        for cell in self.cells:
            if start_row_index<=cell.row_index<end_row_index and start_col_index<=cell.col_index<end_col_index:
                new_cells.append(cell)
        if len(new_cells)==0:
            new_cells=[
                KCell(self.page,self.bbox,row_index=0,col_index=0)
            ]
        return self._from_cells(new_cells,keep_original_cell=False)
        

    def get_stripped_size(self)->tuple[int,int]:
        """去掉空白行列后的size"""
        def is_blank(cells:Sequence[KCell])->bool:
            for c in cells:
                if len(c.objects)!=0:
                    return False
            return True
        
        def is_flat_row(cells:Sequence[KCell],row_index:int)->bool:
            for c in cells:
                if c.row_index==row_index and c.row_span==1:
                    pass
                else:
                    return False
            return True
        
        def is_flat_column(cells:Sequence[KCell],col_index:int)->bool:
            for c in cells:
                if c.col_index==col_index and c.col_span==1:
                    pass
                else:
                    return False
            return True
        
        row_num=self.row_num
        col_num=self.col_num
        for i in range(self.row_num):
            row = self.get_row(i)
            if is_blank(row) and is_flat_row(row,i):
                row_num-=1
        for i in range(self.col_num):
            col = self.get_column(i)
            if is_blank(col) and is_flat_column(col,i):
                col_num-=1
        return (row_num,col_num)

    def _from_cells(self,cells:Sequence["KCell"],*,keep_original_cell:bool=True)->Self:
        """根据选择的部分单元格构造一个新的表格，重新计算单元格"""
        h_lines,v_lines = self._get_lines(cells)
        grid = Grid([line.bbox for line in h_lines+v_lines])
        assert len(cells)==len(grid.cells)
        new_cells:list[KCell]=[]
        for c1,c2 in zip(cells,grid.cells):
            cell = c1.copy(row_index=c2.row_index,col_index=c2.col_index,row_span=c2.row_span,col_span=c2.col_span)
            if not keep_original_cell:
                cell.original_cell=None
            new_cells.append(cell)
        return self.__class__(self.page,grid.bbox,cells=new_cells,subtype=self.subtype)


    def jsonify(self) -> Any:
        data = {
            "type": "table",
            "bbox": self.bbox.jsonify(),
            "row_num": self.row_num,
            "col_num": self.col_num,
            "cells": [c.jsonify() for c in self.cells],
        }
        #ybk|wbk|layout
        data['subtype']=self.subtype
        return data

    def markdown(self) -> str:
        return self.html()

    def html(self) -> str:
        # 没有使用这个就是返回最基本的table的结构，不需要style
        # table.html()
        from html import escape

        def render_objects(objs: Sequence[KObject], allow_chars: bool = False) -> str:
            buf: list[str] = []
            for obj in objs:
                if isinstance(obj, KText):
                    # TODO 如果全部是文字，如果包含有图片
                    if obj.lines:
                        for tl in obj.lines:
                            buf.append("<div>")
                            buf.append(render_objects(tl.objects, allow_chars=True))
                            buf.append("</div>")
                    else:
                        buf.append(escape(obj.text))
                elif isinstance(obj, KFigure):
                    buf.append(f'<img src="{obj.filename}">')
                elif isinstance(obj, KFormula):
                    buf.append(f'<img src="{obj.filename}">')
                elif isinstance(obj, KTable):
                    buf.append(obj.html())
                elif isinstance(obj, KBlock):
                    # KBlock，表示一组对象
                    buf.append(render_objects(obj.objects))
                elif isinstance(obj, KChar) and allow_chars:
                    buf.append(escape(obj.text))
                else:
                    raise ValueError(f"不支持的对象:{obj}")
            return "".join(buf)

        buf: list[str] = []
        buf.append("<table>")
        if True:
            # 如果存在错位的跨列，如果该列没有内容，会导致width=0，显示上看不出来错位
            # 所以这里简单的设置一个宽度
            buf.append("<colgroup>")
            for i in range(self.col_num):
                buf.append('<col colspan=1 style="min-width:20px;"></col>')
            buf.append("</colgroup>")
        i = -1
        tr: list[str] = []
        for cell in self.cells:
            if cell.row_index != i:
                if tr:
                    tr.append("</tr>")
                    buf.extend(tr)
                    tr.clear()
                i = cell.row_index
                tr.append("<tr>")

            if cell.col_span == 1 and cell.row_span == 1:
                # 多数没有，省点
                tr.append("<td>")
            else:
                tr.append(f'<td colspan="{cell.col_span}" rowspan="{cell.row_span}">')
            # tr.append(self._render_block(cell.body,use_html=True))
            if cell.objects:
                tr.append(render_objects(cell.objects))
            else:
                tr.append(escape(cell.text))
            tr.append("</td>")

        if tr:
            tr.append("</tr>")
            buf.extend(tr)
        buf.append("</table>")
        return "".join(buf)

    def fill_objects(self, objs: list[KObject | VObject]):
        """已经创建表格结构，把对象填充到单元格，会消耗使用的对象，目的是为了更好的测试"""

        def remove_spaces(objs: Sequence[KObject]) -> list[KObject]:
            new_objs: list[KObject] = []
            for obj in objs:
                if isinstance(obj, KChar) and obj.text.isspace():
                    pass
                else:
                    new_objs.append(obj)
            return new_objs

        # >=3.13才能够使用TypeIs
        def is_chars(objs: Sequence[Any]) -> TypeGuard[Sequence[KChar]]:
            return len(objs) > 0 and all(isinstance(obj, KChar) for obj in objs)

        def is_figures(objs: Sequence[Any]) -> TypeGuard[Sequence[KFigure]]:
            return len(objs) > 0 and all(isinstance(obj, KFigure) for obj in objs)

        def is_vobjects(objs: Sequence[Any]) -> TypeGuard[Sequence[VObject]]:
            return len(objs) > 0 and all(isinstance(obj, VObject) for obj in objs)

        for cell in self.cells:
            if not objs:
                break
            assert cell.bbox is not None
            # TODO 单元格也可以存在复杂的布局，如果是这样，又需要进行一个单元格的版面分析
            # 目前仅仅支持简单的格式，要么为纯文本，要么为图片
            # 如果是文本和图片混合，如
            # --text1----
            # --figure1--
            # --figure2--
            # --text2----
            cell_objs = cell.bbox.get(objs, ratio=0.7, remove=True)
            if not cell_objs:
                continue

            new_cell_objs: list[KObject] = []
            for obj in cell_objs:
                if isinstance(obj, KPDFFigure | VObject):
                    # 都使用图片即可，如果是vobject的，可以再考虑公式等？
                    obj = obj.make_figure()
                new_cell_objs.append(obj)

            # 空格先暂时去掉，混合在图片中，没有意义
            valid_objs = remove_spaces(new_cell_objs)
            if is_chars(new_cell_objs):
                # 全部都是字符
                tb = KText.from_objects(new_cell_objs)
                if tb is not None:
                    cell.objects.append(tb)
                    # cell.text = tb.text
            elif is_figures(valid_objs):
                # 都是图片
                for obj in valid_objs:
                    cell.objects.append(obj)
            else:
                # 图文并茂，简单的从上到下分行即可
                groups: list[tuple[str, list[KObject]]] = []
                for line in Sorter.get_lines(new_cell_objs):
                    # 先去掉前后的空格字符
                    valid_line = remove_spaces(line)
                    if is_figures(valid_line):
                        groups.append(("figure", list(valid_line)))
                    else:
                        if not groups or groups[-1][0] != "char":
                            groups.append(("char", list(line)))
                        else:
                            groups[-1][1].extend(line)

                for group in groups:
                    if group[0] == "figure":
                        cell.objects.extend(group[1])
                    else:
                        tb = KText.from_objects(group[1])
                        if tb is not None:
                            cell.objects.append(tb)
                            # cell.text += tb.text

        if len(objs) > 0:
            # 有对象剩余？
            pass

    def adjust(self,*,x0:float|None=None,x1:float|None=None):
        """表示调整一下cell的bbox，以便对齐，如果是来自有边框解析，不需要调用这个方法。这个方法合适构造了逻辑表格然后为了美观，调整一下"""

        ref_x0:Final=x0
        ref_x1:Final=x1

        def adjust_y():
            y1 = self.bbox.y1
            for i in range(self.row_num):
                # 获得行
                row = self.get_row(i)
                if i + 1 < self.row_num:
                    # [--row1--]
                    # [--row2--]
                    row2 = self.get_row(i + 1)
                    # 需要的是y0
                    a = BBox.join2(
                        [
                            cell
                            for cell in row
                            if cell.row_index + cell.row_span == i + 1
                        ]
                    )
                    # 需要的是y1
                    b = BBox.join2([cell for cell in row2 if cell.row_index == i + 1])
                    # 取中间
                    y0 = (a.y0 + b.y1) / 2
                else:
                    y0 = self.bbox.y0

                for cell in row:
                    # 设置y1
                    if cell.row_index == i:
                        assert cell.bbox is not None
                        cell.bbox = cell.bbox.adjust(y1=y1)

                    if cell.row_index + cell.row_span == i + 1:
                        assert cell.bbox is not None
                        cell.bbox = cell.bbox.adjust(y0=y0)

                y1 = y0

        def adjust_x():
            x0 = self.bbox.x0
            if ref_x0 is not None:
                x0 = min(x0,ref_x0)
            for i in range(self.col_num):
                #
                column = self.get_column(i)
                if i + 1 < self.col_num:
                    # col1|col2
                    column2 = self.get_column(i + 1)
                    # a需要的是x1
                    a = BBox.join2(
                        [
                            cell.bbox
                            for cell in column
                            if cell.col_index + cell.col_span == i + 1
                        ]
                    )
                    # b需要的是x0
                    b = BBox.join2(
                        [cell.bbox for cell in column2 if cell.col_index == i + 1]
                    )
                    # 取中间
                    x1 = (a.x1 + b.x0) / 2
                else:
                    x1 = self.bbox.x1
                    if ref_x1 is not None:
                        x1=max(x1,ref_x1)

                for cell in column:
                    # 设置x
                    if cell.col_index == i:
                        assert cell.bbox is not None
                        cell.bbox = cell.bbox.adjust(x0=x0)

                    if cell.col_index + cell.col_span == i + 1:
                        assert cell.bbox is not None
                        cell.bbox = cell.bbox.adjust(x1=x1)

                x0 = x1

        adjust_y()
        adjust_x()

        if x0 is not None or x1 is not None:
            bbox = BBox.join2(self.cells)
            self.bbox=bbox

    def align(self,*,x_axis:Sequence[float]|None=None,y_axis:Sequence[float]|None=None,force:bool=False)->bool:
        """根据新的坐标调整单元格的位置，返回False表示无法调整
        x_axis:[x0,x1,x2,...] 
        y_axis:[y0,y1,y2,...]
        force: True表示强制设置，False表示如果新的单元格区域小于内容，就不调整

        返回True表示调整了，返回False表示没有调整
        """
        if x_axis is not None:
            assert len(x_axis)==self.col_num+1
        
        if y_axis is not None:
            assert len(y_axis)==self.row_num+1

        new_bboxes:list[BBox]=[]
        for cell in self.cells:
            x0=x_axis[cell.col_index] if x_axis else None
            x1=x_axis[cell.col_index+cell.col_span] if x_axis else None
            y0=y_axis[cell.row_index] if y_axis else None
            y1=y_axis[cell.row_index+cell.row_span] if y_axis else None
            b = cell.bbox.adjust(x0=x0,x1=x1,y0=y0,y1=y1)
            cb = cell.content_bbox
            if force or cb is None or b.get([cb],ratio=0.8):
                #表示还是满足的，记录下来
                new_bboxes.append(b)
            else:
                break
        
        if len(new_bboxes)!=len(self.cells):
            return False
        
        #TODO cell.bbox不允许修改
        for cell,bbox in zip(self.cells,new_bboxes):
            cell.bbox = bbox   
        #TODO 这个也不允许修改
        self.bbox=BBox.join(new_bboxes)
        return True


    def _validate(self, cells: Sequence["KCell"]):
        row_num = self.row_num
        col_num = self.col_num
        for cell in cells:
            if (
                cell.col_index < 0
                or cell.col_index + cell.col_span > col_num
                or cell.row_index < 0
                or cell.row_index + cell.row_span > row_num
            ):
                raise ValueError(
                    f"第{self.page.number}页表格结构错误,table=({row_num},{col_num})，cell=({cell.row_index},{cell.col_index},{cell.row_span},{cell.col_span})"
                )
        row_indexes: set[int] = set()
        col_indexes: set[int] = set()
        for cell in cells:
            row_indexes.add(cell.row_index)
            col_indexes.add(cell.col_index)
        #if len(row_indexes) != row_num or len(col_indexes) != col_num:
        if row_indexes!=set(range(row_num)) or col_indexes!=set(range(col_num)):
            a = set(range(row_num)).difference(row_indexes)
            b = set(range(col_num)).difference(col_indexes)
            raise ValueError(
                f"第{self.page.number}页表格结构错误，缺少了行={a},缺少列={b}"
            )
        
        #重叠了
        grid:list[list[Any]]=[ [None]*col_num for _ in range(row_num)]
        for cell in cells:
            for i in range(cell.row_index,cell.row_index+cell.row_span):
                for j in range(cell.col_index,cell.col_index+cell.col_span):
                    old_cell=grid[i][j]
                    if old_cell is not None:
                        raise ValueError(f'第{self.page.number}页表格结构错误，单元格重叠,cell1=({old_cell.row_index},{old_cell.col_index},{old_cell.row_span},{old_cell.col_span}),cell2=({cell.row_index},{cell.col_index},{cell.row_span},{cell.col_span})')
                    grid[i][j]=cell

    @classmethod
    def create_table(
        cls, page: KPage, quad: Quad | BBox, data: Mapping[str, Any]
    ) -> "KTable":
        from .table_builder import Builder

        # 计算表格的结构
        bbox = quad.bbox if isinstance(quad, Quad) else quad
        x_axis, y_axis = Builder().build(bbox, data)
        # row_num = data["row_num"]
        # col_num = data["col_num"]

        cells: list[KCell] = []
        for cell in data["cells"]:
            row_index: int = cell["row_index"]
            col_index: int = cell["col_index"]
            row_span: int = cell["row_span"]
            col_span: int = cell["col_span"]
            x0 = x_axis[col_index]
            x1 = x_axis[col_index + col_span]
            y0 = y_axis[row_index]
            y1 = y_axis[row_index + row_span]
            cell_bbox = BBox(x0, y0, x1, y1)
            text_bbox = BBox(x0, y0, x1, y1)
            ktext = KText(page, text_bbox, text=cell["text"])
            kcell = KCell(
                page,
                cell_bbox,
                row_index=row_index,
                col_index=col_index,
                row_span=row_span,
                col_span=col_span,
                objects=[ktext],
            )
            cells.append(kcell)

        return KTable(page, bbox, cells=cells)

    @classmethod
    def from_text(cls, page: KPage, quad: Quad | BBox, text: str) -> "KTable":
        """根据html或者otsl构造"""
        table = cls.create_table(page, quad, cls.parse_text(text))
        table.raw_text = text
        return table

    @classmethod
    def from_data(
        cls, page: KPage, quad: Quad | BBox, data: Mapping[str, Any]
    ) -> "KTable":
        return cls.create_table(page, quad, data)

    @classmethod
    def from_grid(cls, page: KPage, grid: Grid) -> "KTable":
        #grid.draw(size=(int(page.width),int(page.height)),show=True).show()
        cells: list[KCell] = []
        for cell in grid.cells:
            kcell = KCell(
                page,
                cell.bbox.to_quad(),
                row_index=cell.row_index,
                col_index=cell.col_index,
                row_span=cell.row_span,
                col_span=cell.col_span,
            )
            kcell.objects.extend(cell.objects)
            cells.append(kcell)
        return KTable(page, grid.bbox, cells=cells)

    @classmethod
    def from_lines(
        cls,
        page: KPage,
        lines: Sequence["KLine|BBox|Sequence[float]"],
        objects: list[KObject | VObject] | None = None,
    ) -> "KTable":
        """
        objects: 会删除使用过的对象，目的是为了方便测试
        """

        def to_line(line: Any) -> Any:
            if isinstance(line, KLine):
                return line.bbox
            else:
                return line

        grid = Grid([to_line(line) for line in lines])
        table = cls.from_grid(page, grid)
        if objects:
            table.fill_objects(objects)
        return table

    @classmethod
    def parse_text(cls, text: str) -> dict[str, Any]:
        text = text.strip()
        if text.startswith("<table"):
            return cls.parse_html(text)
        else:
            return cls.parse_otsl(text)

    @classmethod
    def parse_html(cls, html: str) -> dict[str, Any]:
        """
        解析 HTML 表格为二维列表，支持 colspan / rowspan。

        Args:
            html: 包含 <table> 的 HTML 字符串

        Returns:
            二维列表，每个元素为单元格文本
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if table is None:
            return {"row_num": 0, "col_num": 0, "cells": []}

        def get_int(a: Any, name: str, default: int = 1) -> int:
            return int(a.get(name, default))

        # 先确定表格实际行列数
        rows = table.find_all("tr")
        n_rows = len(rows)
        n_cols = 0

        for row in rows:
            cols = sum(get_int(td, "colspan", 1) for td in row.find_all(["td", "th"]))
            n_cols = max(n_cols, cols)

        # 用 None 初始化网格，处理 rowspan/colspan 占位
        grid: list[list[Any | None]] = [[None] * n_cols for _ in range(n_rows)]

        cells: list[Any] = []
        for r_idx, row in enumerate(rows):
            c_idx = 0
            for td in row.find_all(["td", "th"]):
                # 跳过已被 rowspan 占用的格子
                while c_idx < n_cols and grid[r_idx][c_idx] is not None:
                    c_idx += 1

                colspan = get_int(td, "colspan", 1)
                rowspan = get_int(td, "rowspan")
                text = td.get_text(strip=True)
                cell = {
                    "row_index": r_idx,
                    "col_index": c_idx,
                    "row_span": rowspan,
                    "col_span": colspan,
                    "text": text,
                }
                cells.append(cell)
                # 填充 rowspan × colspan 范围
                for dr in range(rowspan):
                    for dc in range(colspan):
                        if r_idx + dr < n_rows and c_idx + dc < n_cols:
                            grid[r_idx + dr][c_idx + dc] = cell

                c_idx += colspan

        return {"row_num": len(rows), "col_num": n_cols, "cells": cells}

    @classmethod
    def parse_otsl(cls, text: str) -> dict[str, Any]:
        # 当前的代码来自百度
        # https://github.com/PaddlePaddle/PaddleX/blob/v3.4.2/paddlex/inference/pipelines/paddleocr_vl/uilts.py
        # 用ai写了一个，但是还没有测试过
        from .otsl import otsl_parse

        text = text.strip()
        result: dict[str, Any] = {"row_num": 0, "col_num": 0, "cells": []}
        if not text:
            return result

        data = otsl_parse(text)
        for cell in data["cells"]:
            result["cells"].append(
                {
                    "row_index": cell[0],
                    "col_index": cell[1],
                    "row_span": cell[2],
                    "col_span": cell[3],
                    "text": cell[4],
                }
            )

        result["row_num"] = data["row_num"]
        result["col_num"] = data["col_num"]
        return result


class KCell:
    """有些情况下不需要获得cell的坐标，所以bbox=(0,0,0,0)"""

    # type: str = "cell"

    # 后续设置
    table: KTable

    def __init__(
        self,
        page: KPage,
        quad: Quad | BBox,
        *,
        row_index: int,
        col_index: int,
        row_span: int = 1,
        col_span: int = 1,
        objects: Sequence[KObject] | None = None,
        original_cell:Self|None=None
    ):
        # super().__init__(page,quad)
        self.page: Final = page
        # 如果是来自大模型的结果，目前单元格不一定有bbox，或者也不需要
        # 另外一个是为了支持逻辑表格，也就是需要理解为表格，但是并没有严格行列对齐的
        if isinstance(quad, BBox):
            bbox = quad
            quad = bbox.to_quad()
        else:
            bbox=quad.bbox
        self.quad: Final = quad
        self.bbox: Final = bbox
        # self.text: str = ''
        self.row_index = row_index
        self.col_index = col_index
        self.row_span = row_span
        self.col_span = col_span

        self.subtype: str | None = None

        # 后续设置
        # self.table:KTable

        self.working_state: Any = None
        self.original_cell:Self|None=original_cell
        """在复制的时候，可以设置来自哪个单元格"""

        self.merged:bool|None=None
        """True表示在跨页/跨栏的时候被合并"""

        self.color:KColor|None=None
        """设置或者获得单元格的背景颜色"""
        self.font_color:KColor|None=None
        """来自ocr的解析，可以通过这里获得字体颜色"""

        self.objects: Final[list[KObject]] = []
        if objects:
            self.objects.extend(objects)

    @property
    def content_bbox(self) -> BBox | None:
        return BBox.join([obj.content_bbox for obj in self.objects], strict=False)

    @property
    def text(self) -> str:
        buf: list[str] = []
        for obj in self.objects:
            if isinstance(obj, KText):
                buf.append(obj.text)
        return "".join(buf)

    def copy(self,*,row_index:int|None=None,col_index:int|None=None,col_span:int|None=None,row_span:int|None=None)->Self:
        if row_index is None:
            row_index = self.row_index
        if col_index is None:
            col_index = self.col_index
        if col_span is None:
            col_span = self.col_span
        if row_span is None:
            row_span = self.row_span
        cell = self.__class__(self.page,self.bbox,row_index=row_index,col_index=col_index,row_span=row_span,col_span=col_span,objects=self.objects,original_cell=self)
        #TODO 需要复制吗？或者original_cell.color?
        cell.color = self.color
        cell.font_color = self.font_color
        return cell

    def jsonify(self) -> Any:
        # 有些并不需要输出准确的bbox
        obj: dict[str, Any] = {
            "row_index": self.row_index,
            "col_index": self.col_index,
            "row_span": self.row_span,
            "col_span": self.col_span,
            "text": self.text,
        }
        if self.bbox is not None:
            obj["bbox"] = self.bbox.jsonify()
        if self.objects:
            obj["objects"] = [obj.jsonify() for obj in self.objects]
        
        if self.color and not self.color.is_white():
            obj['bg_color']=self.color.jsonify()
        else:
            #如果为白色，就不需要输出了
            pass
        
        return obj


class KFormula(KObject):
    type: str = "formula"

    def __init__(
        self,
        page: KPage,
        quad: Quad|BBox,
        *,
        inline: bool = False,
        latex: str = "",
        filename: str = "",
    ):
        super().__init__(page, quad)
        self.latex = latex
        self.inline = inline
        self.filename = filename

    @cached_property
    def fullpath(self) -> Path:
        return self.doc.out_dir / self.filename

    def markdown(self) -> str:
        if self.latex:
            if self.inline:
                # $xx$    markdown用
                # \(xxx\) 标准
                return f"${self.latex}$"
            else:
                # $$xx$$ markdown用
                # \[xx\] 标准
                return f"$${self.latex}$$"
                # return rf'\[{self.latex}\]'
        else:
            name = _md_escape(self.fullpath.name)
            return f"![{name}](./images/{name})"

    def jsonify(self):
        data = {
            "type": "formula",
            "bbox": self.bbox.jsonify(),
            "filename": self.filename,
            "latex": self.latex,
        }
        return data

    # \(xx\) =>xx  标准的行内公式
    # \[xx\] => xx 标准的行间公式
    # $xx$   => markdown的行内
    # $$xx$$ => markdown的行内

    _latex_pattern: Final = re.compile(
        r"\$\$(.*?)\$\$|\$(.*?)\$|\\\[(.*?)\\\]|\\\((.*?)\\\)", re.DOTALL
    )
    # \[\[xxx\]\] => 这个会出现？
    # _latex_pattern = re.compile(r'\$\$(.*?)\$\$|\$(.*?)\$|\\\[\\\[(.*?)\\\]\\\]|\\\[(.*?)\\\]|\\\((.*?)\\\)', re.DOTALL)

    @classmethod
    def normalize(cls, text: str) -> str:
        """去掉前后的修饰符"""
        m = cls._latex_pattern.fullmatch(text.strip())
        if m:
            for g in m.groups():
                if g is not None:
                    return g.strip()
            raise ValueError(f"正则表达式错误，不应该执行到这里:{text}")
        return text


class KLine(KObject):
    type = "line"

    def __init__(
        self, page: KPage, bbox: BBox, *, color: KColor = KColor.BLACK, width: float = 1
    ):
        super().__init__(page, bbox)
        self.color = color
        self.width = width

    @property
    def length(self) -> float:
        """线的长度"""
        if self.bbox.is_h():
            return self.bbox.width
        else:
            return self.bbox.height

    def is_h(self) -> bool:
        return self.bbox.is_h()

    def is_v(self) -> bool:
        return self.bbox.is_v()

    @property
    def content_bbox(self) -> BBox:
        """线作为矩形，包括了线的宽度"""
        if self.bbox.is_h():
            return self.bbox.expand(dy=self.width)
        else:
            return self.bbox.expand(dx=self.width)

    @classmethod
    def split(cls, lines: Sequence[Self]) -> tuple[list[Self], list[Self]]:
        h_lines: list[Self] = []
        v_lines: list[Self] = []
        for line in lines:
            if line.is_h():
                h_lines.append(line)
            else:
                v_lines.append(line)
        return h_lines, v_lines


class KRect(KObject):
    type = "rect"

    def __init__(self, page: KPage, bbox: BBox, *, color: KColor = KColor.WHITE):
        super().__init__(page, bbox)
        self.color = color
        """填充颜色"""

        # 为边框，目前不打算支持
        self.stroke_width: float | None = None
        self.stroke_color: KColor | None = None


class KOther(KObject):
    def __init__(self, page: KPage, quad: Quad, type: str):
        super().__init__(page, quad)
        self.type = type


class KBlock(KObject):
    """表示一个局部的内容块，把一些关联或者没有关联的，影响阅读顺序的内容放在一起"""

    type = "block"

    def __init__(
        self,
        page: KPage,
        quad: Quad | BBox,
        *,
        objects: Sequence[KObject] | None = None,
    ):
        super().__init__(page, quad)
        self.objects: Final[list[KObject]] = []
        if objects:
            self.objects.extend(objects)

    @property
    def content_bbox(self) -> BBox | None:
        return (
            BBox.join([obj.content_bbox for obj in self.objects], strict=False)
            if self.objects
            else None
        )


    def expand(self)->Iterator[KObject]:
        """展开"""
        for obj in self.objects:
            if isinstance(obj,KBlock):
                yield from obj.expand()
            else:
                yield obj
class KPageHeader(KObject):
    type = "pageheader"

    def __init__(self, page: KPage, quad: Quad):
        super().__init__(page, quad)
        self.objects: Final[list[KObject]] = []

    @property
    def content_bbox(self) -> BBox | None:
        return BBox.join([obj.content_bbox for obj in self.objects],strict=False)


class KPageFooter(KObject):
    type = "pagefooter"

    def __init__(self, page: KPage, quad: Quad):
        super().__init__(page, quad)
        self.objects: Final[list[KObject]] = []

    @property
    def content_bbox(self) -> BBox | None:
        return BBox.join([obj.content_bbox for obj in self.objects],strict=False)



class KPageFootnote(KObject):
    type = "pagefootnote"

    def __init__(self, page: KPage, quad: Quad):
        super().__init__(page, quad)
        self.objects: Final[list[KObject]] = []

    @property
    def content_bbox(self) -> BBox | None:
        return BBox.join([obj.content_bbox for obj in self.objects],strict=False)

    pass

class KFootnote:
    def __init__(self,id:str,objects:Sequence[KObject]):
        super().__init__()
        self.id:Final=id

        #如果需要先跨页合并对象也可以使用XObject，目前就不合并了，因为最终在word中，输出多个对象即可
        #而且多数情况都只是输出文本
        self.objects:Final[Sequence[KObject]]=tuple(objects)
    
    @property
    def text(self)->str:
        #TODO 在生成word需要去掉开头的序号，因为会自动生成
        return ''.join( obj.text for obj in self.objects if isinstance(obj,KText))

class KFootnoteRef:
    def __init__(self,chars:Sequence[KChar]):
        super().__init__()
        assert len(chars)>0
        self.chars:Final= chars
        self.no:Final =''.join(c.text for c in chars)
        """来自原文的脚注序号，如：1"""
        self.footnote:KFootnote|None=None
        """该引用对应的脚注对象"""
        


class KPDFFigure(KObject):
    """表示pdf上的一个图片，没有直接使用KFigure，是这个需要存储更多的信息，而且也不一定就需要获得该图片的原始文件"""

    type = "pdffigure"

    def __init__(self, page: KPage, quad: Quad, *, transparent: bool = False):
        super().__init__(page, quad)
        self.transparent = transparent

    def make_figure(self) -> KFigure:
        # TODO 可以使用截图，也可以直接使用原图，但是需要补充flip+旋转等信息
        figure = self.page.make_figure(self.quad, add=False)
        assert figure is not None
        return figure


class Group[T](list[T]):
    _bbox: BBox | None = None

    @property
    def bbox(self) -> BBox:
        assert len(self) > 0
        if self._bbox is None:
            self._bbox = BBox.join([obj.bbox for obj in self])
        return self._bbox

    def invalidate(self):
        self._bbox = None


class KDest:
    def __init__(self, page_number: int, point: Point):
        super().__init__()
        self.page_number = page_number
        self.point = point
        """页面的坐标"""


class PDFNode:
    """表示pdf的outline的一个节点"""

    def __init__(self, parent: "PDFNode|None" = None):
        super().__init__()
        self.parent = parent
        self.children: list[PDFNode] = []
        self.level = 0
        self.title: str = ""
        self.page_number: int = -1
        """页面，1位第一页，-1表示没有"""
        self.point: tuple[float, float] | None = None
        """页面坐标，原点为左下角"""
        self.type = "title"
        # self.target:Any={}

    @property
    def size(self) -> int:
        return len(self.children)

    def add(self, node: "PDFNode"):
        node.parent = self
        self.children.append(node)

    def get_last_node(self, level: int) -> "PDFNode":
        if level == 0:
            return self.get_root()
        elif level > self.level:
            # 往下查找
            node = self
            while level > node.level:
                node = node.children[-1]
            return node
        else:
            # 往上查找
            return self.get_root().get_last_node(level)

    def get_root(self) -> "PDFNode":
        if self.parent is None:
            return self
        else:
            return self.parent.get_root()

    def detach(self):
        if self.parent is not None:
            self.parent.children.remove(self)

    def stringify(self) -> str:
        buf: list[str] = []
        indent = "    " * self.level
        buf.append(
            f"{indent}[{self.level}]({self.page_number},{self.point}){self.title}\n"
        )
        for child in self.children:
            buf.append(child.stringify())
        return "".join(buf)


class PDFLink:
    def __init__(self, bbox: BBox, page_number: int, point: Point):
        super().__init__()
        self.bbox: Final = bbox
        """表示在页面上的位置"""
        self.page_number: Final = page_number
        """表示点击后跳转到哪个页面"""
        self.point: Final = point
        """表示点击后跳转到页面的那个位置"""
