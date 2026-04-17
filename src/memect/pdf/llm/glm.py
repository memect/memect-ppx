import logging
import re
from collections import Counter
from typing import Any, Final, Iterator, Mapping
import weakref

from pydantic import Field

from memect.base import images
from memect.base.debug import XDebugger
from memect.base.utils import MyBaseModel
from memect.pdf.base import KDocument, KFormula, KMarkdown, KPage, KTable, VObject
from memect.pdf.model import ModelManager

from .llm import Model, ModelArgs, Task


def create_model_args(args: Mapping[str, Any] | ModelArgs) -> ModelArgs:
    default = ModelArgs(
        name="glmocr",
        base_url="http://127.0.0.1:4002/v1",
        api_key="",
        model="glmocr",
        max_tokens=16384-1024,
        temperature=0.0,
        prompt="",
        extra_body={},
        max_image_size=(2000, 2000),
        image_format="png",
    )
    # exclude_none=True??
    if isinstance(args, ModelArgs):
        return default.model_copy(update=args.model_dump())
    else:
        a = default.model_dump()
        a.update(args)
        return ModelArgs.model_validate(a)


def create_model(args: Mapping[str, Any] | ModelArgs) -> Model:
    return Model(GLMTask, create_model_args(args))


class GLMArgs(MyBaseModel):
    name: str = "glm"
    layout: str = "layout"
    """表示使用哪个版面分析模型"""
    model: ModelArgs | Mapping[str, Any] = Field(default_factory=create_model_args)


class GLMTask(Task):
    prompts = {
        "text": "Text Recognition:",
        "formula": "Formula Recognition:",
        "table": "Table Recognition:",
    }

    def build_messages(self) -> Iterator[tuple[Any, Any]]:
        page = self.args.page
        assert page is not None
        # image = images.open(self.file,exif=self.args.exif,rotation=self.args.rotation)
        # 可以获得layout的数据了
        for i, vobj in enumerate(page.vobjects):
            # 截图，然后根据类型获得提示词
            prompt = ""
            # 这里使用raw_type更好
            type = vobj.raw_type
            if type in ("table",):
                prompt = self.prompts["table"]
            elif type in ("display_formula", "inline_formula"):
                prompt = self.prompts["formula"]
            elif type in ("chart",):
                # prompt = self.prompts['chart']
                pass
            elif type in ("image", "seal"):
                pass
            elif type in (
                "header",
                "footer",
                "header_image",
                "footer_image",
                "footnote",
                "number",
                "aside_text",
            ):
                # 这些都不需要了
                pass
            else:
                prompt = self.prompts["text"]

            if prompt:
                # 保存llm使用的图片
                image = page.crop(vobj.quad)
                if image:
                    llm_image, url = images.to_url(
                        image, max_size=self.max_image_size, format=self.image_format
                    )
                    self.images.append((image, llm_image))
                    yield (
                        str(i),
                        [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "image_url", "image_url": {"url": url}},
                                    {"type": "text", "text": prompt},
                                ],
                            }
                        ],
                    )


class GLM:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")

    def __init__(self,manager:ModelManager,args: GLMArgs | Mapping[str, Any] | None = None):
        super().__init__()
        args = GLMArgs.create(args)
        self.name: Final = args.name
        self._llm_model: Final = create_model(args.model)
        self._layout_model: Final = manager.get(args.layout)

        self._llm_key: Final = f"cache/{self.name}/llm"
        self._layout_key: Final = f"cache/{self.name}/layout"

        self._finalizer = weakref.finalize(self,self._close,self._llm_model)
    
    @classmethod
    def _close(cls,model:Model):
        model.close()
    
    def close(self):
        if self._finalizer.alive:
            self._finalizer()
            
    def parse(self, doc: KDocument):
        doc.all_as_images()
        # 第一步，版面分析，获得解析结果
        self._parse_layout(doc)
        # 第二步，切割页面对象调用llm识别获得结果
        self._llm_model.parse(self._llm_key, doc)

        # 根据两步的结果进行处理
        buf: list[str] = []
        for page in doc.pages:
            if page.skipped:
                continue

            text = self._parse_page(page)
            buf.append(text)

    def _parse_layout(self, doc: KDocument):
        debugger = self._debugger.bind()
        name = self._layout_key
        self._layout_model.parse(doc,name)
        for page in doc.working_pages:
            page.load_layout(page.cache.pop(name))
            

    def _parse_page(self, page: KPage) -> str:
        doc: Final = page.doc
        debugger: Final = self._debugger.bind(page=page.number)
        # 必须存在，如果不存在，不应该执行到这里
        result: dict[str, str] = page.cache.pop(self._llm_key)
        if debugger.allow("info"):
            with debugger.group("json"):
                print(result)

        def parse_table(vobj: VObject, text: str,raw_text:str):
            table = KTable(page,vobj.quad)
            table.fill_html(text)
            if table.row_num>0 and table.col_num>0:
                table.raw_text=raw_text
                table.vobject=vobj
                page.objects.append(table)

        def parse_figure(vobj: VObject):
            figure=page.make_figure(vobj.quad, add=True)
            if figure:
                figure.vobject=vobj
        



        def parse_formula(vobj: VObject, text: str,raw_text:str):
            # TODO 也可以搞一个图片
            figure = page.make_figure(vobj.quad)
            if figure is not None:
                # 如果能够截图
                # TODO 需要对文本做处理吗？
                #$$xxx$$ => 转换为xxx ??
                #[[xxx]] => 转换为xxx ??
                inline = vobj.type == "inline_formula"
                formula = KFormula(
                        page,
                        vobj.quad,
                        inline=inline,
                        latex=KFormula.normalize(text),
                        filename=figure.filename,
                    )
                formula.llm_text=text
                formula.raw_text=raw_text
                formula.vobject = vobj
                page.objects.append(
                    formula
                )
            else:
                return None

        def parse_text(vobj: VObject, text: str,raw_text:str):
            # 根据vobj.raw_type设计markdown的level？
            md = KMarkdown(page, vobj.quad, text=text)
            md.llm_text=text
            md.raw_text=text
            md.vobject=vobj
            page.objects.append(md)

        for i, vobj in enumerate(page.vobjects):
            # 分数值太低的去掉？
            if vobj.score < 0.4:
                continue

            raw_text = result.get(str(i)) or ''
            if debugger.allow('info'):
                with debugger.group('llm'):
                    debugger.print(i,vobj.type)
                    print(raw_text)
                debugger.console.print()

            #返回的还是markdown或者html(table)
            text = self._format_text(vobj,raw_text)
            # 使用layout自身的类型
            type = vobj.raw_type
            if type in ("image", "chart"):
                parse_figure(vobj)
            elif text:
                # 如果有text，表示被识别了
                if type == "table":
                    parse_table(vobj, text,raw_text)
                elif type in ("display_formula", "inline_formula"):
                    # 如果是图片的
                    parse_formula(vobj, text,raw_text)
                else:
                    # 解析为文本
                    parse_text(vobj, text,raw_text)
            else:
                pass

        if debugger.allow("draw"):
            page.draw(('raw_vobjects',page.raw_vobjects),('vobjects',page.vobjects),('objects',page.objects),dir=f"debug/{self.name}")

        return page.markdown()
    
    def _clean_text(self,text:str|None)->str:
        """对模型返回的text进行一些清洗"""
        return TextCleaner().clean(text)

    def _format_text(self,vobj:VObject,content:str):
        native_label = vobj.raw_type
        label = vobj.type
        content = self._clean_text(content)
        # Title formatting
        if native_label == "doc_title":
            # Remove existing # symbols at the beginning
            content = re.sub(r"^#+\s*", "", content)
            content = "# " + content
        elif native_label == "paragraph_title":
            # Remove existing - or # symbols at the beginning
            if content.startswith("- ") or content.startswith("* "):
                content = content[2:].lstrip()
            content = re.sub(r"^#+\s*", "", content)
            content = "## " + content.lstrip()

        # Formula formatting
        if label == "formula":
            if content.startswith("$$") and content.endswith("$$"):
                content = content[2:-2].strip()
                content = "$$\n" + content + "\n$$"
            elif content.startswith("\\[") and content.endswith("\\]"):
                content = content[2:-2].strip()
                content = "$$\n" + content + "\n$$"
            elif content.startswith("\\(") and content.endswith("\\)"):
                content = content[2:-2].strip()
                content = "$$\n" + content + "\n$$"
            else:
                content = "$$\n" + content + "\n$$"

        # Text formatting
        if label == "text":
            # Bullet points
            if (
                content.startswith("·")
                or content.startswith("•")
                or content.startswith("* ")
            ):
                content = "- " + content[1:].lstrip()

            # Allow multiple digits for numbers, single letter for alphabetic
            match = re.match(r"^(\(|\（)(\d+|[A-Za-z])(\)|\）)(.*)$", content)
            if match:
                _, symbol, _, rest = match.groups()
                content = f"({symbol}) {rest.lstrip()}"

            # Allow multiple digits for numbers, single letter for alphabetic
            match = re.match(r"^(\d+|[A-Za-z])(\.|\)|\）)(.*)$", content)
            if match:
                symbol, sep, rest = match.groups()
                sep = ")" if sep == "）" else sep
                content = f"{symbol}{sep} {rest.lstrip()}"

            # Replace single newlines with double newlines
            content = re.sub(r"(?<!\n)\n(?!\n)", "\n\n", content)

        return content

    def _parse_table(self,ktable:KTable,html:str):
        ktable.fill_html(html)




class TextCleaner:
    """基于 GLM-OCR 仓库的文本清洗类，用于处理模型返回结果的清洗。"""

    def __init__(self):
        self._min_unit_len = 10
        self._min_repeats = 10
        self._line_threshold = 10

    def clean(self, content: str | None) -> str:
        """对模型返回的文本进行清洗。"""
        if content is None:
            return ""

        # 移除前后的字面 \t
        content = re.sub(r"^(\\t)+", "", content).lstrip()
        content = re.sub(r"(\\t)+$", "", content).rstrip()

        # 移除重复标点
        content = re.sub(r"(\.)\1{2,}", r"\1\1\1", content)
        content = re.sub(r"(·)\1{2,}", r"\1\1\1", content)
        content = re.sub(r"(_)\1{2,}", r"\1\1\1", content)
        content = re.sub(r"(\\_)\1{2,}", r"\1\1\1", content)

        # 移除重复内容（针对长内容）
        if len(content) >= 2048:
            content = self._clean_repeated_content(content)

        return content.strip()

    def _clean_repeated_content(
        self,
        content: str,
        min_len: int | None = None,
        min_repeats: int | None = None,
        line_threshold: int | None = None,
    ) -> str:
        """移除重复内容（连续和行级）。"""
        if min_len is None:
            min_len = self._min_unit_len
        if min_repeats is None:
            min_repeats = self._min_repeats
        if line_threshold is None:
            line_threshold = self._line_threshold

        stripped_content = content.strip()
        if not stripped_content:
            return content

        # 1. 连续重复检测（支持多行模式）
        if len(stripped_content) > min_len * min_repeats:
            result = self._find_consecutive_repeat(
                stripped_content, min_unit_len=min_len, min_repeats=min_repeats
            )
            if result is not None:
                return result

        # 2. 行级重复检测
        lines = [line.strip() for line in content.split("\n") if line.strip()]
        total_lines = len(lines)
        if total_lines >= line_threshold and lines:
            common, count = Counter(lines).most_common(1)[0]
            if count >= line_threshold and (count / total_lines) >= 0.8:
                for i, line in enumerate(lines):
                    if line == common:
                        consecutive = sum(
                            1
                            for j in range(i, min(i + 3, len(lines)))
                            if lines[j] == common
                        )
                        if consecutive >= 3:
                            original_lines = content.split("\n")
                            non_empty_count = 0
                            for idx, orig_line in enumerate(original_lines):
                                if orig_line.strip():
                                    non_empty_count += 1
                                    if non_empty_count == i + 1:
                                        return "\n".join(original_lines[: idx + 1])
                            break
        return content

    def _find_consecutive_repeat(
        self, s: str, min_unit_len: int = 10, min_repeats: int = 10
    ) -> str | None:
        """查找并移除连续重复模式。"""
        n = len(s)
        if n < min_unit_len * min_repeats:
            return None

        # 动态计算 max_unit_len
        max_unit_len = n // min_repeats
        if max_unit_len < min_unit_len:
            return None

        # 使用 DOTALL 模式匹配换行符
        pattern = re.compile(
            r"(.{"
            + str(min_unit_len)
            + ","
            + str(max_unit_len)
            + r"}?)\1{"
            + str(min_repeats - 1)
            + ",}",
            re.DOTALL,
        )
        match = pattern.search(s)
        if match:
            return s[: match.start()] + match.group(1)
        return None


