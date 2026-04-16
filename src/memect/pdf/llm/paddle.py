
import logging
from collections import Counter
from typing import Any, Final, Iterator, Mapping

from pydantic import Field

from memect.base import images
from memect.base.debug import XDebugger
from memect.base.utils import MyBaseModel
from memect.pdf.base import KDocument, KFormula, KMarkdown, KPage, KTable, VObject
from memect.pdf.model import ModelExecutor
from .llm import Model, ModelArgs, Task

v2 = [
    "abstract",
    "algorithm",
    "aside_text",
    "chart",
    "content",
    "display_formula",
    "doc_title",
    "figure_title",
    "footer",
    "footer_image",
    "footnote",
    "formula_number",
    "header",
    "header_image",
    "image",
    "inline_formula",
    "number",
    "paragraph_title",
    "reference",
    "reference_content",
    "seal",
    "table",
    "text",
    "vertical_text",
    "vision_footnote",
]
v3 = [
    "abstract",
    "algorithm",
    "aside_text",
    "chart",
    "content",
    "display_formula",
    "doc_title",
    "figure_title",
    "footer",
    "footer_image",
    "footnote",
    "formula_number",
    "header",
    "header_image",
    "image",
    "inline_formula",
    "number",
    "paragraph_title",
    "reference",
    "reference_content",
    "seal",
    "table",
    "text",
    "vertical_text",
    "vision_footnote",
]

def create_model_args(args: Mapping[str, Any] | ModelArgs) -> ModelArgs:
    default = ModelArgs(
        name="paddleocr",
        base_url="http://127.0.0.1:4001/v1",
        api_key="",
        #model="paddleocr-vl-1.5"
        model="paddleocr-vl",
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
    return Model(PaddleTask, create_model_args(args))


class PaddleArgs(MyBaseModel):
    name:str='paddle'
    layout:str='layout'
    """表示使用哪个版面分析模型"""
    model: ModelArgs | Mapping[str, Any] = Field(default_factory=create_model_args)


class PaddleTask(Task):
    prompts = {
        "ocr": "OCR:",
        "table": "Table Recognition:",
        "formula": "Formula Recognition:",
        "chart": "Chart Recognition:",
        #1.5
        "seal":"Seal Recognition:"
    }

    def build_messages(self) -> Iterator[tuple[Any, Any]]:
        page = self.args.page
        assert page is not None
        # image = images.open(self.file,exif=self.args.exif,rotation=self.args.rotation)
        # 可以获得layout的数据了
        for i, vobj in enumerate(page.vobjects):
            # 截图，然后根据类型获得提示词
            prompt = ""
            #这里使用raw_type更好
            type = vobj.raw_type
            if type in ('table',):
                prompt = self.prompts["table"]
            elif type in ('display_formula','inline_formula'):
                prompt = self.prompts['formula']
            elif type in ('chart',):
                #prompt = self.prompts['chart']
                pass
            elif type in ('image','seal'):
                pass
            elif type in ('header','footer','header_image','footer_image','footnote','number','aside_text'):
                #这些都不需要了
                pass
            else:
                prompt = self.prompts['ocr']

            if prompt:
                # 保存llm使用的图片
                image = page.crop(vobj.quad)
                if image:
                    llm_image, url = images.to_url(image, max_size=self.max_image_size, format=self.image_format)
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




class Paddle:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")

    def __init__(self, args: PaddleArgs | Mapping[str, Any] | None = None):
        super().__init__()
        args = PaddleArgs.create(args)
        self.name:Final = args.name
        self._llm_model: Final = create_model(args.model)
        self._layout_model:Final = ModelExecutor.get(args.layout)

        self._llm_key:Final=f'cache/{self.name}/llm'
        self._layout_key:Final=f'cache/{self.name}/layout'

    def parse(self, doc: KDocument):
        doc.all_as_images()
        #第一步，版面分析，获得解析结果
        self._parse_layout(doc)
        #第二步，切割页面对象调用llm识别获得结果
        self._llm_model.parse(self._llm_key,doc)

        #根据两步的结果进行处理
        buf: list[str] = []
        for page in doc.working_pages:
            text = self._parse_page(page)
            buf.append(text)
        
    def _parse_layout(self,doc:KDocument):
        debugger = self._debugger.bind()
        name=self._layout_key
        self._layout_model.parse(doc,name)
        for page in doc.working_pages:
            page.load_layout(page.cache.pop(name))
            
    def _parse_page(self, page: KPage) -> str:
        doc: Final = page.doc
        debugger: Final = self._debugger.bind(page=page.number)
        # 必须存在，如果不存在，不应该执行到这里
        result:dict[str,str] = page.cache.pop(self._llm_key)
        if debugger.allow("info"):
            with debugger.group("json"):
                print(result)
        

        def parse_table(vobj:VObject,text:str,raw_text:str):
            table = KTable(page,vobj.quad)
            table.fill_otsl(text)
            if table.row_num>0 and table.col_num>0:
                table.raw_text=raw_text
                table.vobject=vobj
                page.objects.append(table)

        def parse_figure(vobj:VObject):
            figure=page.make_figure(vobj.quad,add=True)
            if figure:
                figure.vobject=vobj

        def parse_formula(vobj:VObject,text:str,raw_text:str):
            #TODO 也可以搞一个图片
            figure = page.make_figure(vobj.quad)
            if figure is not None:
                #如果能够截图
                #TODO 需要对文本做处理吗？
                inline = vobj.type=='inline_formula'
                formula = KFormula(page,vobj.quad,inline=inline,latex=KFormula.normalize(text),filename=figure.filename)
                formula.llm_text=text
                formula.raw_text=raw_text
                formula.vobject=vobj
                page.objects.append(formula)
            else:
                return None
        
        def parse_text(vobj:VObject,text:str,raw_text:str):
            #根据vobj.raw_type设计markdown的level？
            md = KMarkdown(page,vobj.quad,text=text)
            md.raw_text=raw_text
            md.llm_text=text
            md.vobject=vobj
            page.objects.append(md)


        for i,vobj in enumerate(page.vobjects):
            #分数值太低的去掉？
            if vobj.score<0.4:
                continue

            raw_text = result.get(str(i)) or ''
            
            if debugger.allow('info'):
                with debugger.group('llm'):
                    debugger.print(i,vobj.type)
                    print(raw_text)

            text = self._clean_text(vobj,raw_text)
            #使用paddle自身的类型
            type=vobj.raw_type
            if type in ('image','chart'):
                parse_figure(vobj)
            elif text:
                #如果有text，表示被识别了
                if type=='table':
                    parse_table(vobj,text,raw_text)
                elif type in ('display_formula','inline_formula'):
                    #如果是图片的
                    parse_formula(vobj,text,raw_text)
                else:
                    #解析为文本
                    parse_text(vobj,text,raw_text)
            else:
                pass
        
        if debugger.allow("draw"):
            page.draw(('raw_vobjects',page.raw_vobjects),('vobjects',page.vobjects),('objects',page.objects),dir=f"debug/{self.name}")

        return page.markdown()
    
    def _clean_text(self,vobj:VObject,result_str:str)->str:
        #https://github.com/PaddlePaddle/PaddleX/blob/release/3.4/paddlex/inference/pipelines/paddleocr_vl/pipeline.py
        block_label = vobj.raw_type
        min_count = 5000 if block_label == "table" else 50
        result_str = truncate_repetitive_content(
            result_str, min_count=min_count
        )
        if ("\\(" in result_str and "\\)" in result_str) or (
            "\\[" in result_str and "\\]" in result_str
        ):
            result_str = result_str.replace("$", "")

            result_str = (
                result_str.replace("\\(", " $ ")
                .replace("\\)", " $")
                .replace("\\[\\[", "\\[")
                .replace("\\]\\]", "\\]")
                .replace("\\[", " $$ ")
                .replace("\\]", " $$ ")
            )
            if block_label == "formula_number":
                result_str = result_str.replace("$", "")
        return result_str


#========
def find_shortest_repeating_substring(s: str) ->str|None:
    """
    Find the shortest substring that repeats to form the entire string.

    Args:
        s (str): Input string.

    Returns:
        str or None: Shortest repeating substring, or None if not found.
    """
    n = len(s)
    for i in range(1, n // 2 + 1):
        if n % i == 0:
            substring = s[:i]
            if substring * (n // i) == s:
                return substring
    return None


def find_repeating_suffix(
    s: str, min_len: int = 8, min_repeats: int = 5
) -> tuple[str, str, int]|None:
    """
    Detect if string ends with a repeating phrase.

    Args:
        s (str): Input string.
        min_len (int): Minimum length of unit.
        min_repeats (int): Minimum repeat count.

    Returns:
        Tuple[str, str, int] or None: (prefix, unit, count) if found, else None.
    """
    for i in range(len(s) // (min_repeats), min_len - 1, -1):
        unit = s[-i:]
        if s.endswith(unit * min_repeats):
            count = 0
            temp_s = s
            while temp_s.endswith(unit):
                temp_s = temp_s[:-i]
                count += 1
            start_index = len(s) - (count * i)
            return s[:start_index], unit, count
    return None

def truncate_repetitive_content(
    content: str,
    line_threshold: int = 10,
    char_threshold: int = 10,
    min_len: int = 10,
    min_count: int = 3000,
) -> str:
    """
    Detect and truncate character-level, phrase-level, or line-level repetition in content.

    Args:
        content (str): Input text.
        line_threshold (int): Min lines for line-level truncation.
        char_threshold (int): Min repeats for char-level truncation.
        min_len (int): Min length for char-level check.

    Returns:
        Union[str, str]: (truncated_content, info_string)
    """
    if len(content) < min_count:
        return content

    stripped_content = content.strip()
    if not stripped_content:
        return content

    # Priority 1: Phrase-level suffix repetition in long single lines.
    if "\n" not in stripped_content and len(stripped_content) > 100:
        suffix_match = find_repeating_suffix(stripped_content, min_len=8, min_repeats=5)
        if suffix_match:
            prefix, repeating_unit, count = suffix_match
            if len(repeating_unit) * count > len(stripped_content) * 0.5:
                return prefix

    # Priority 2: Full-string character-level repetition (e.g., 'ababab')
    if "\n" not in stripped_content and len(stripped_content) > min_len:
        repeating_unit = find_shortest_repeating_substring(stripped_content)
        if repeating_unit:
            count = len(stripped_content) // len(repeating_unit)
            if count >= char_threshold:
                return repeating_unit

    # Priority 3: Line-level repetition (e.g., same line repeated many times)
    lines = [line.strip() for line in content.split("\n") if line.strip()]
    if not lines:
        return content
    total_lines = len(lines)
    if total_lines < line_threshold:
        return content
    line_counts = Counter(lines)
    most_common_line, count = line_counts.most_common(1)[0]
    if count >= line_threshold and (count / total_lines) >= 0.8:
        return most_common_line

    return content