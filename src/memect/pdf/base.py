import json
import logging
import math
import random
import re
import threading
import uuid
import weakref
from collections.abc import Sequence
from enum import StrEnum, auto
from functools import cached_property
from pathlib import Path
from typing import (
    Annotated,
    Any,
    ClassVar,
    Final,
    Mapping,
    NotRequired,
    Self,
    TextIO,
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
from memect.base.bbox import BBox, Quad
from memect.base.matrix import Matrix
from memect.base.utils import AutoCleaner, MyBaseModel, safe_write


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


class PageType(StrEnum):
    PDF = auto()
    """表示有pdf解析获得的字符，可以包含部分来自ocr的"""
    IMAGE = auto()
    """表示完全作为图片解析，也就是所有字符都来自ocr"""
    UNKNOWN = auto()


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
    LLM = auto()
    """使用LLM解析"""


class ParseMode(StrEnum):
    PAGE = auto()
    """按页解析即可"""
    TREE = auto()
    """按章节树解析"""
    PPT = auto()
    """按PPT解析，也就是没有页眉页脚注的内容"""


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

    mode: ParseMode = ParseMode.TREE
    table: TableMode = TableMode.AUTO
    formula: bool = True
    """True表示需要解析公式，需要部署对应的模型,False只是截图即可"""

    remove_watermark: bool = False
    """True表示清除水印"""

    use_llm: Annotated[bool, Field(description="")] = False
    """如果为True且backend=default，表示使用llm，目前为deepseek，之前是给pdf2skills用"""
    backend: Backend = Backend.DEFAULT
    """deepseek,paddle,glm,default"""

    ocr: OCRMode = OCRMode.AUTO


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

    FIGURE = auto()
    CHART = auto()
    TABLE = auto()
    SEAL = auto()
    FORMULA = auto()
    INLINE_FORMULA = auto()

    HEADER = auto()
    FOOTER = auto()
    FOOTNOTE = auto()


class VObject:
    def __init__(self, type: str, quad: Quad, score: float = 1, raw_type: str = ""):
        super().__init__()
        self.type: Final = VObjectType(type)
        self.bbox: Final = quad.bbox
        self.quad: Final = quad
        self.score: Final = score
        self.raw_type: Final = raw_type
        self.cache: Final[dict[str, Any]] = {}
        self.debug: Final[dict[str, Any]] = {}
        self.ocr_chars: Final[list[KChar]] = []
        """该对象区域试验ocr识别的字符串"""
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
        return self.type == VObjectType.HEADER

    def is_footer(self) -> bool:
        return self.type == VObjectType.FOOTER

    def is_footnote(self) -> bool:
        return self.type == VObjectType.FOOTNOTE


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

    def __del__(self):
        self._logger.debug("gc %s", self)

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
        from ..x2x.zip import Archiver

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
        data = {"pages": []}
        for page in self.pages:
            data["pages"].append(page.jsonify())
        return data

    def markdown(self) -> str:
        buf: list[str] = []
        for page in self.working_pages:
            buf.append(page.markdown())
        return "\n\n".join(buf)

class KDocumentFactory:
    def __init__(self,file:str|Path,params:Any,out_dir:Path|None=None):
        super().__init__()
        self.file:Final=file
        self.params:Final=params
        self.out_dir:Final=out_dir
    
    def __call__(self)->KDocument:
        return KDocument(self.file,params=self.params,out_dir=self.out_dir)
    
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
    return re.sub(r"[`*_+\-!{}#.\\]", lambda m: rf"\{m.group()}", text)


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
        self.pdf_paths: Final[list[Any]] = []
        """页面上所有的原始的路径"""

        # self.ocr_spans:Final[list[KSpan]]=[]
        # """ocr识别的字符串，目的是用来去掉和pdf识别的且重叠的"""

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
        self.footnote: Final = KPageFootnote(
            self, self.bbox.adjust(y0=int(height * 0.1), y1=int(height * 0.2)).to_quad()
        )

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

    def make_figure(self, quad: Quad, *, add: bool = False) -> "KFigure|None":
        img = self.crop(quad)
        if img is None:
            return None

        filename = self.next_figure_name()
        figure = KFigure(self, quad, filename=filename)
        figure.fullpath.parent.mkdir(parents=True, exist_ok=True)
        img.save(figure.fullpath)
        if add:
            self.objects.append(figure)
        return figure

    def make_formula(
        self, quad: Quad, *, add: bool = False, inline: bool = False, latex: str = ""
    ):
        figure = self.make_figure(quad)
        if figure is None:
            return None
        formula = KFormula(
            self, quad, inline=inline, latex=latex, filename=figure.filename
        )
        if add:
            self.objects.append(formula)
        return formula

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

        vobjects = list(vobjects)
        # 可以删除太小的对象，目前仅仅删除为0
        lists.remove2(vobjects, lambda i, vobjs: vobjs[i].bbox.area <= 0)

        # 现在处理区域重叠的问题，不管使用哪种算法，都只能够处理某些情况，所以就简化了
        # 1.找到重叠的区域
        # 2.面积排序，大的留下，小的去掉
        # 3.相互重叠的情况就不考虑了
        vobjects.sort(key=lambda vobj: vobj.bbox.area, reverse=True)
        i = 0
        while i < len(vobjects):
            vobj = vobjects[i]
            for vobj2 in vobjects[i + 1 :]:
                inter = vobj.bbox.intersect(vobj2.bbox)
                if inter is None or inter.area == 0:
                    continue

                overlap_ratio = inter.area / vobj2.bbox.area
                if overlap_ratio >= min_overlap_ratio:
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
                    continue
                # 如果仅仅部分区域重叠，合并为一个？
            i += 1
        return vobjects

    def draw(
        self,
        *columns: Any,
        file: str | Path | None = None,
        dir: str | Path | None = None,
        use_bbox: bool = False,
        show_type: bool = True,
        index: int | None = None,
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

        def draw_objects(objects: Sequence[Any], font: Any):
            image = self.image.copy()
            overlay_image = PIL.Image.new("RGBA", image.size, (0, 0, 0, 0))

            draw = PIL.ImageDraw.Draw(image)
            overlay_draw = PIL.ImageDraw.Draw(overlay_image)

            # 把相对页面的坐标转换为相对图片的，原点从左下角到左上角，然后再缩放
            m = Matrix.lb_to_lt(self.size, image.size)
            for obj in objects:
                # TODO 为页面坐标，还需要转换为相对图片的，因为可能缩放了，但是旋转方向等是一致的
                quad, type_ = get_quad_and_type(obj)
                quad = quad.transform(m)
                bbox = quad.bbox
                x0, y0, x1, y1 = bbox
                if isinstance(obj, KLine):
                    draw.line((x0, y0, x1, y1), fill=(255, 255, 0) if obj.is_h() else (0,0,255), width=math.ceil(obj.width))
                    #draw.rectangle(obj.rect_bbox.transform(m),fill=(255,255,0))
                elif isinstance(obj, KRect):
                    #这个是覆盖原图
                    #draw.rectangle((x0, y0, x1, y1), fill=(255, 0, 0, 20))
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

                    if type_ and show_type:
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
        for title, obj in columns:
            if obj is None:
                image = self.image
            elif isinstance(obj, PIL.Image.Image):
                image = obj
            else:
                image = draw_objects(obj, type_font)

            images.append((title, image))

        title_font = get_font(30)
        gap_width = 2
        title_height = 40
        new_width = sum(img[1].width for img in images) + gap_width * (len(columns) - 1)
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

    def jsonify(self) -> Any:
        data: dict[str, Any] = {
            "number": self.number,
            "bbox": self.bbox.jsonify(),
            "width": self.width,
            "height": self.height,
            "objects": [],
        }
        for obj in self.objects:
            data["objects"].append(obj.jsonify())
        return data

    def markdown(self) -> str:
        buf: list[str] = []
        for obj in self.objects:
            if isinstance(obj, KTable):
                # 后续也可以使用obj.markdown()
                buf.append(obj.html())
            else:
                buf.append(obj.markdown())
        return "\n\n".join(buf)


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
        return None

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

        if not force and 0xF020 <= ord(text) <= 0xF0FF:
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
        self.underline: Final = underline
        """下划线"""
        self.strikeout: Final = strikeout
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

    def markdown(self) -> str:
        return _md_escape(self.text)

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


class KText(KObject):
    """表示纯文本"""

    type = "text"

    def __init__(self, page: KPage, quad: Quad, *, text: str):
        super().__init__(page, quad)
        self.text: Final = text

    def markdown(self) -> str:
        return _md_escape(self.text)


class KMarkdown(KObject):
    """表示markdown的文本"""

    type = "markdown"

    def __init__(self, page: KPage, quad: Quad, text: str):
        super().__init__(page, quad)
        self.text = text
        """markdown"""

    def jsonify(self) -> Any:
        return {"type": self.type, "bbox": self.bbox.jsonify(), "text": self.text}

    def markdown(self) -> str:
        return self.text

    def plaintext(self) -> str:
        """纯文本"""
        return self.unescape(self.text)

    @classmethod
    def escape(cls, text: str) -> str:
        return _md_escape(text)

    @classmethod
    def unescape(cls, text: str) -> str:
        """将 markdown 文本还原为纯文本"""
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

        return text


class KSpan(KObject):
    """表示一串属性相同的字符"""

    type = "span"

    def __init__(
        self, page: KPage, quad: Quad, *, chars: Sequence[KChar], score: float = 1
    ):
        assert len(chars) > 0
        super().__init__(page, quad)
        self.chars: Final[Sequence[KChar]] = tuple(chars)
        self.score: float = 1

    @cached_property
    def text(self) -> str:
        return "".join(c.text for c in self.chars)


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

    def to_textbox(self) -> "KTextbox":
        return KTextbox(self.page, self.quad, lines=[self])

    def __add__(self, other: Self):
        quad = Quad.join([self.quad, other.quad])
        return KTextline(self.page, quad, objects=[*self.objects, *other.objects])

    @classmethod
    def parse(cls, objects: Sequence[KObject]) -> list[Self]:
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

        page = objects[0].page
        return [
            cls(
                page,
                line[0].quad if len(line) == 1 else line.bbox.to_quad(),
                objects=line,
            )
            for line in lines
        ]

    @classmethod
    def parse2(cls, objects: Sequence[KObject]) -> list[Self]:
        if not objects:
            return []

        def parse_line(objects: list[KObject]) -> Group[KObject]:
            # 有行内公式，图片等
            # 还有行间公式，图片等
            # 还有上下标
            line: Group[KObject] = Group()
            line.append(objects.pop(0))
            line.invalidate()
            i = 0
            while i < len(objects):
                b1 = line.bbox
                b2 = objects[i].bbox
                # 表示有4个单位重叠
                d = 4
                if b1.over("y", b2, d=d) and abs(b1.height - b2.height) <= 5:
                    # [b1][b2]
                    # 如果为两行重叠的，分开，如：
                    # [------line1----]
                    #                  [----line2---]
                    # 同时删除该对象
                    line.append(objects.pop(i))
                    line.invalidate()
                # 已经按y1排序，不需要
                elif b2.y1 <= b1.y0:
                    # [b1]
                    # [b2] =>接下来的都会更低，不需要再继续
                    break
                else:
                    i += 1

            line.sort(key=lambda obj: obj.bbox.x0)
            # print('=======>line',[(c.text,c.bbox) for c in line if isinstance(c,Char)])
            # 可能还需要补充连续的下标字符串，如：
            # ABC[123]D => 123表示下标字符串
            # ABC   D => 目前解析了D

            return line

        def is_subscript(i: int, objs: Sequence[KObject]) -> bool:
            if isinstance(objs[i], KChar):
                return objs[i].subtype == "subscript"
            else:
                return False

        def is_superscript(i: int, objs: Sequence[KObject]) -> bool:
            if isinstance(objs[i], KChar):
                return objs[i].subtype == "superscript"
            else:
                return False

        def fill_script(script: KChar, line: Group[KObject]) -> bool:
            b1 = line.bbox
            b2 = script.bbox
            # 先快速判断
            if script.subtype == "subscript":
                # 如果为下标
                if b1.y0 <= b2.y1 <= b1.cy:
                    for i, char in enumerate(line):
                        if char.bbox.x1 - 2 <= b2.x0 <= char.bbox.x1 + 2:
                            line.insert(i + 1, script)
                            line.invalidate()
                            return True

                return False
            else:
                # 为上标
                # [---line---]
                if b1.cy <= b2.y0 <= b1.y1:
                    for i, char in enumerate(line):
                        if char.bbox.x1 - 2 <= b2.x0 <= char.bbox.x1 + 2:
                            line.insert(i + 1, script)
                            line.invalidate()
                            return True

                return False

        def fill_scripts(
            is_subscript: bool, scripts: list[KChar], lines: list[Group[KObject]]
        ):
            if not scripts:
                return

            # 都是按从上到下排序过的
            # 考虑到上下标的字符实际上并不多，所以就采用最简单的实现方式了
            scripts.sort(key=lambda char: char.bbox.x0)
            for script in scripts:
                for line in lines:
                    if fill_script(script, line):
                        break

        # TODO 可以先把上下标字符去掉，再排序，然后再把上下标字符插入到指定位置
        objects = sorted(objects, key=lambda obj: obj.bbox.y1, reverse=True)
        subscripts = cast(list[KChar], lists.remove2(objects, is_subscript))
        superscripts = cast(list[KChar], lists.remove2(objects, is_superscript))
        lines: list[Group[KObject]] = []
        while objects:
            lines.append(parse_line(objects))

        fill_scripts(False, superscripts, lines)
        fill_scripts(True, subscripts, lines)

        page = objects[0].page
        return [
            cls(
                page,
                line[0].quad if len(line) == 1 else line.bbox.to_quad(),
                objects=line,
            )
            for line in lines
        ]


class KTextbox(KObject):
    """文本块，知道每一个字符的具体坐标，阅读顺序从上到下，从左到右。
    如果将来需要支持从右到左阅读的，或者垂直书写的，从左到右，从右到左的，这些都是需要专门的处理，
    不在这里处理。
    """

    type = "textbox"

    def __init__(self, page: KPage, quad: Quad, *, lines: Sequence[KTextline]):
        super().__init__(page, quad)
        assert len(lines) > 0
        self.lines: Final[Sequence[KTextline]] = tuple(lines)

    @cached_property
    def text(self) -> str:
        return "".join(line.text for line in self.lines)

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
    def formula(self) -> Sequence["KFormula"]:
        return tuple(lists.join(line.formulas for line in self.lines))

    @override
    def markdown(self) -> str:
        """需要按原文换行吗？"""
        return _md_escape(self.text)

    def __add__(self, other: Self) -> Self:
        lines = [*self.lines, *other.lines]
        quad = Quad.join([self.quad, other.quad])
        return self.__class__(self.page, quad, lines=lines)

    @classmethod
    def join(cls, tbs: Sequence[Self]) -> Self:
        assert len(tbs) > 0
        quad = Quad.join([tb.quad for tb in tbs])
        page = tbs[0].page
        return cls(page, quad, lines=[line for tb in tbs for line in tb.lines])

    @classmethod
    def from_objects(cls, objects: Sequence[KObject]) -> Self:
        assert len(objects) > 0
        quad = Quad.join([c.quad for c in objects])
        page = objects[0].page
        lines = KTextline.parse(objects)
        return cls(page, quad, lines=lines)


class KFigure(KObject):
    type: str = "figure"

    def __init__(self, page: KPage, quad: Quad, *, filename: str):
        super().__init__(page, quad)
        self.filename: Final = filename
        """如：images/1.png，相对doc.md"""

    @cached_property
    def fullpath(self) -> Path:
        return self.doc.out_dir / self.filename

    def markdown(self) -> str:
        name = self.fullpath.name
        return f"![{_md_escape(name)}](./images/{_md_escape(name)})"


class KTable(KObject):
    type: str = "table"

    def __init__(self, page: KPage, quad: Quad, *, row_num: int = 0, col_num: int = 0):
        super().__init__(page, quad)
        self.row_num = row_num
        self.col_num = col_num
        self.cells: list[KCell] = []
        self.filename: str = ""
        """对应的截图的文件名，在llm且只需要获得markdown下，不一定需要截图"""
        self.grid: list[list[KCell]] = []

    @cached_property
    def fullpath(self) -> Path:
        """截图的完整路径"""
        # 必须设置才能够调用
        assert self.filename
        return self.doc.out_dir / self.filename

    def __getitem__(self, key: tuple[int, int]) -> "KCell":
        row_index, col_index = key
        for cell in self.cells:
            if (
                cell.row_index <= row_index < cell.row_index + cell.row_span
                and cell.col_index <= col_index < cell.col_index + cell.col_span
            ):
                return cell
        raise ValueError(f"错误的的行列坐标:{key}")

    def jsonify(self) -> Any:
        data = {
            "type": self.type,
            "bbox": self.bbox.jsonify(),
            "row_num": self.row_num,
            "col_num": self.col_num,
            "cells": [c.jsonify() for c in self.cells],
        }
        return data

    def markdown(self) -> str:
        return super().markdown()

    def html(self) -> str:
        # 没有使用这个就是返回最基本的table的结构，不需要style
        # table.html()
        from html import escape

        buf: list[str] = []
        buf.append("<table>")
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
            tr.append(escape(cell.text))
            tr.append("</td>")

        if tr:
            tr.append("</tr>")
            buf.extend(tr)
        buf.append("</table>")
        return "".join(buf)

    def rich_html(
        self, fp: str | Path | TextIO | None = None, full: bool = True
    ) -> str:
        buf: list[str] = []
        if full:
            buf.append("<html><head></head><body>")
        buf.append('<table style="border: 1px solid;border-collapse: collapse;">')
        # 如果需要显示复杂的表格，如下设置可以让跨列跨行的显示更加明确
        buf.append("<colgroup>")
        for i in range(self.col_num):
            buf.append('<col colspan=1 style="width:100px;"></col>')
        buf.append("</colgroup>")
        buf.append("<tbody>")

        i = -1
        tr: list[str] = []
        for cell in self.cells:
            if cell.row_index != i:
                if tr:
                    tr.append("</tr>")
                    buf.extend(tr)
                    tr.clear()
                i = cell.row_index
                # 如果使用css，并不需要输出style
                tr.append('<tr style="border:1px solid;">')

            if cell.merged == True:
                # 表示来自2个表格合并
                bgcolor = "background-color:blue;"
            elif cell.merged == False:
                # 上下相邻的单元格，没有合并
                bgcolor = "background-color:gray;"
            elif cell.subtype == "header":
                # 重复的表头不存在merged=True或者False的情况
                bgcolor = "background-color:yellow;"
            else:
                bgcolor = ""
            tr.append(
                f'<td colspan="{cell.col_span}" rowspan="{cell.row_span}" style="border:1px solid;{bgcolor}">'
            )
            # tr.append(html.escape(cell.body.text()))
            for obj in cell.body.objects:
                if isinstance(obj, Text):
                    tr.append(html.escape(obj.text))
                elif isinstance(obj, Figure):
                    tr.append(f'<img src="images/{obj.filename}">')

            tr.append("</td>")

        if tr:
            tr.append("</tr>")
            buf.extend(tr)

        buf.append("</tbody>")

        buf.append("</table>")
        if full:
            buf.append("</body></html>")
        s = "".join(buf)
        if isinstance(fp, (str, Path)):
            Path(fp).write_text(s, encoding="utf-8")
        elif isinstance(fp, TextIO):
            fp.write(s)
        else:
            pass
        return s

    def fill_html(self, html: str):
        self.llm_text = html
        self.raw_text = html
        self.fill(self.parse_html(html))

    def fill_otsl(self, text: str):
        self.llm_text = text
        self.raw_text = text
        self.fill(self.parse_otsl(text))

    def fill(self, data: Mapping[str, Any]):
        self.row_num = data["row_num"]
        self.col_num = data["col_num"]
        self.cells.clear()
        for cell in data["cells"]:
            kcell = KCell(
                self.page,
                None,
                row_index=cell["row_index"],
                col_index=cell["col_index"],
                row_span=cell["row_span"],
                col_span=cell["col_span"],
                text=cell["text"],
            )
            self.cells.append(kcell)

    @classmethod
    def from_text(cls, page: KPage, quad: Quad, text: str) -> "KTable":
        """根据html或者otsl构造"""
        table = KTable(page, quad)
        table.fill(cls.parse_text(text))
        table.llm_text = text
        return table

    @classmethod
    def from_data(cls, page: KPage, quad: Quad, data: Mapping[str, Any]) -> "KTable":
        table = KTable(page, quad)
        table.fill(data)
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
                # kcell = KCell(ktable.page,None,row_index=r_idx,col_index=c_idx,col_span=colspan,row_span=rowspan,text=text)
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

    def __init__(
        self,
        page: KPage,
        quad: Quad | None,
        *,
        row_index: int,
        col_index: int,
        row_span: int = 1,
        col_span: int = 1,
        text: str = "",
    ):
        # super().__init__(page,quad)
        self.page: Final = page
        # 如果是来自大模型的结果，目前单元格不一定有bbox，或者也不需要
        # 另外一个是为了支持逻辑表格，也就是需要理解为表格，但是并没有严格行列对齐的
        self.quad: Final = quad
        self.bbox: Final = quad.bbox if quad else None
        self.text: str = text
        self.row_index = row_index
        self.col_index = col_index
        self.row_span = row_span
        self.col_span = col_span

        self.working_state: Any = None

        self.objects: Final[list[KObject]] = []

    @property
    def content_bbox(self) -> BBox | None:
        return BBox.join([obj.content_bbox for obj in self.objects], strict=False)

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
        return obj


class KFormula(KObject):
    type: str = "formula"

    def __init__(
        self,
        page: KPage,
        quad: Quad,
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
            name = self.fullpath.name
            return f"![{_md_escape(name)}](./images/{_md_escape(name)})"

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
    def length(self)->float:
        """线的长度"""
        if self.bbox.is_h():
            return self.bbox.width
        else:
            return self.bbox.height
        
    def is_h(self)->bool:
        return self.bbox.is_h()
    
    def is_v(self)->bool:
        return self.bbox.is_v()
    
    @property
    def content_bbox(self)->BBox:
        """线作为矩形，包括了线的宽度"""
        if self.bbox.is_h():
            return self.bbox.expand(dy=self.width)
        else:
            return self.bbox.expand(dx=self.width)


class KRect(KObject):
    type = "rect"
    def __init__(self,page:KPage,bbox:BBox,*,color:KColor=KColor.WHITE):
        super().__init__(page,bbox)
        self.color = color
        """填充颜色"""

        #为边框，目前不打算支持
        self.stroke_width:float|None=None
        self.stroke_color:KColor|None=None



class KOther(KObject):
    def __init__(self, page: KPage, quad: Quad, type: str):
        super().__init__(page, quad)
        self.type = type


class KBlock(KObject):
    type = "block"

    def __init__(self, page: KPage, quad: Quad):
        super().__init__(page, quad)
        self.objects: Final[list[KObject]] = []

    @property
    def content_bbox(self) -> BBox | None:
        return BBox.join([obj.bbox for obj in self.objects]) if self.objects else None


class KPageHeader(KObject):
    type = "pageheader"

    def __init__(self, page: KPage, quad: Quad):
        super().__init__(page, quad)
        self.objects: Final[list[KObject]] = []

    def content_bbox(self) -> BBox | None:
        return BBox.join([obj.bbox for obj in self.objects]) if self.objects else None


class KPageFooter(KObject):
    type = "pagefooter"

    def __init__(self, page: KPage, quad: Quad):
        super().__init__(page, quad)
        self.objects: Final[list[KObject]] = []

    def content_bbox(self) -> BBox | None:
        return BBox.join([obj.bbox for obj in self.objects]) if self.objects else None


class KPageFootnote(KObject):
    type = "pagefootnote"

    def __init__(self, page: KPage, quad: Quad):
        super().__init__(page, quad)
        self.objects: Final[list[KObject]] = []

    def content_bbox(self) -> BBox | None:
        return BBox.join([obj.bbox for obj in self.objects]) if self.objects else None

    pass


class KPDFFigure(KObject):
    """表示pdf上的一个图片，没有直接使用KFigure，是这个需要存储更多的信息，而且也不一定就需要获得该图片的原始文件"""

    type = "pdffigure"

    def __init__(self, page: KPage, quad: Quad, *, transparent: bool = False):
        super().__init__(page, quad)
        self.transparent = transparent


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
