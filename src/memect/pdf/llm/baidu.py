import base64
import json
import logging
from pathlib import Path
import re
from typing import Any, Final, Mapping, Sequence
import weakref

import httpx
from pydantic import Field

from memect.base.bbox import BBox
from memect.base.debug import XDebugger
from memect.base.utils import MyBaseModel
from memect.pdf.base import KDocument, KPage, KTable, KText


class BaiduArgs(MyBaseModel):
    name: str = "baidu"
    model: dict[str, Any] = Field(default_factory=dict)


class Baidu:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")

    def __init__(self, args: BaiduArgs | Mapping[str, Any] | None = None):
        super().__init__()
        args = BaiduArgs.create(args)
        self._name: Final = args.name
        self._base_url: Final = args.model["base_url"]
        # self._model:Final= create_model(args.model)
        self._llm_key = f"cache/{self._name}"

    def close(self):
        pass

    def parse(self, doc: KDocument):
        doc.all_as_images()

        def encode_image(image_path: Path) -> Any:
            ext = image_path.suffix.lower()
            mime = (
                "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext.lstrip('.')}"
            )
            with open(image_path, "rb") as f:
                data = base64.b64encode(f.read()).decode("utf-8")
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{data}"},
            }

        def build_content(prompt: str, image_paths: Sequence[Path]) -> list[Any]:
            return [{"type": "text", "text": prompt}] + [
                encode_image(path) for path in image_paths
            ]

        def encode_payload(obj:Any)->bytes:
            try:
                import orjson
                return orjson.dumps(obj)
            except ImportError:
                return json.dumps(obj,ensure_ascii=False).encode('utf-8')
        def generate(
            prompt: str, image_paths: Sequence[Path], image_mode: str, ngram_window: int
        ) -> str:
            # from sglang.srt.sampling.custom_logit_processor import DeepseekOCRNoRepeatNGramLogitProcessor
            # DeepseekOCRNoRepeatNGramLogitProcessor.to_str()
            processor: Final = '{"callable": "80049559000000000000008c2a73676c616e672e7372742e73616d706c696e672e637573746f6d5f6c6f6769745f70726f636573736f72948c26446565707365656b4f43524e6f5265706561744e4772616d4c6f67697450726f636573736f729493942e"}'
            payload = {
                "model": "Unlimited-OCR",
                "messages": [
                    {"role": "user", "content": build_content(prompt, image_paths)}
                ],
                "temperature": 0,
                # 这几个参数，如果是使用vllm，需要通过extra_body中传递
                # 现在使用的是sglang的服务器
                "skip_special_tokens": False,
                "images_config": {"image_mode": image_mode},
                "custom_logit_processor": processor,  # DeepseekOCRNoRepeatNGramLogitProcessor.to_str(),
                "custom_params": {
                    "ngram_size": 35,
                    "window_size": ngram_window,
                },
                "stream": True,
            }

        

            chunks: list[str] = []
            with httpx.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                headers={"Content-Type": "application/json"},
                #content=json.dumps(payload, ensure_ascii=False),
                #如果后台支持gzip，可以gzip，然后Content-Encoding:gzip
                content=encode_payload(payload),
                #每个页面10秒+60+上传时间？
                timeout=len(pages)*10+60+100,
            ) as res:
                res.raise_for_status()
                for line in res.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[len("data: ") :]
                    if data == "[DONE]":
                        break
                    event = json.loads(data)
                    delta = event["choices"][0].get("delta", {}).get("content", "")
                    if delta:
                        #print(delta, end="", flush=True)
                        chunks.append(delta)
            #print()
            return "".join(chunks)
        
        #但是不能够一次性太多页面，因为模型的返回还是受限于max token的，目前就是32K，所以一次性10个页面即可
        batch = False
        batch_size=10
        pages: list[KPage] = []
        for page in doc.working_pages:
            if doc.is_dev() and doc.has_file(f"{self._llm_key}/{page.number}.txt"):
                page.cache[self._llm_key] = doc.read_text(
                    f"{self._llm_key}/{page.number}.txt"
                )
            else:
                pages.append(page)
        page_texts: list[str] = []
        if len(pages) > 0:
            if batch:
                for i in range(0,len(pages),batch_size):
                    # 多个页面一起解析
                    prompt = "Multi page parsing."
                    # <PAGE>xxx
                    # <PAGE>xxx
                    text = generate(
                        prompt,
                        [page.file for page in pages[i:i+batch_size]],
                        image_mode="base",
                        ngram_window=1024,
                    )
                    buf: list[str]|None=None
                    for line in text.splitlines(keepends=True):
                        if line.startswith("<PAGE>"):
                            if buf is not None:
                                page_texts.append("".join(buf))
                            # 每一页的开始
                            buf=[]
                            buf.append(line[len("<PAGE>") :])
                        elif buf is not None:
                            buf.append(line)
                        else:
                            #返回的内容不符合格式
                            pass

                    if buf is not None:
                        page_texts.append("".join(buf))

            else:
                # 单页解析，多线程并行？
                prompt = "document parsing."
                for page in pages:
                    text = generate(
                        prompt, [page.file], image_mode="gundam", ngram_window=128
                    )
                    page_texts.append(text)
                    #print(text)

        if len(page_texts) != len(pages):
            raise RuntimeError(f"需要解析:{len(pages)}页，仅仅返回{len(page_texts)}页")

        for page, text in zip(pages, page_texts):
            if doc.is_dev():
                doc.write(f"{self._llm_key}/{page.number}.txt", text)
            page.cache[self._llm_key] = text

        for page in doc.working_pages:
            self._parse_page(page)

    def _parse_page(self, page: KPage):
        debugger: Final = self._debugger.bind(page=page.number)
        # 必须存在，如果不存在，不应该执行到这里
        text: str = page.cache[self._llm_key]
        # 获得后就可以释放了
        del page.cache[self._llm_key]
        if debugger.allow("info"):
            with debugger.group("llm"):
                print(text)

        def parse_bbox(s: str) -> BBox | None:
            # 解析是归一化的，需要转换为相对当前图片的
            try:
                # 模型遇到需要旋转的页面，一样可以正常识别，返回的bbox是相对输入的页面，所以溯源显示是正确的
                # 只是如果能够告知该页面需要旋转
                # 这里返回的bbox都是相对输入的图片(应用了page.rotation后的)，而page.bbox已经为应用page.rotation的
                # 所以不需要做任何特别的处理
                width = int(page.width)
                height = int(page.height)
                # bboxes: list[BBox] = []
                obj = json.loads(s)
                # obj = ast.literal_eval(s)

                x0, y0, x1, y1 = obj
                x0 = int(x0 / 999 * width)
                x1 = int(x1 / 999 * width)
                y0 = int(y0 / 999 * height)
                y1 = int(y1 / 999 * height)
                # 原点从左上角转换为左下角
                b = BBox(x0, height - y1, x1, height - y0)
                x_bbox = page.bbox.intersect(b)
                if b.is_valid() and x_bbox is not None and x_bbox.is_valid():
                    # 确保在页面范围内
                    return x_bbox
                else:
                    self._logger.warning(
                        "获得错误的bbox,page=%s,text=%s,bbox=%s",
                        page.number,
                        obj,
                        (x0, y0, x1, y1),
                    )
                return None
            except Exception:
                # 使用warning就可以了？
                self._logger.warning(
                    "解析bbox失败,page=%s,text=%s", page.number, s, exc_info=True
                )
                return None

        def parse(text: str):
            """
            <|det|>aside_text [21, 15, 50, 91]<|/det|>股票研究
            <|det|>aside_text [22, 180, 52, 339]<|/det|>新股研究
            TO
            <|det|>aside_text [21, 470, 52, 584]<|/det|>证券研究报告
            <|det|>image [86, 198, 115, 216]<|/det|>
            """

            # (\s*\[\s*\[\s*[0-9]+\s*,\s*[0-9]+\s*,\s*[0-9]+\s*,\s*[0-9]+\s*\]
            pattern = re.compile(r"(<\|det\|>(?P<type>.+?)[\s]+(?P<bbox>.+)<\|/det\|>)")
            buf: list[str] = []
            type_: str | None = None
            bbox: BBox | None = None
            for line in text.splitlines():
                m = pattern.match(line)
                if m:
                    if len(buf) > 0 and type_ and bbox:
                        # TODO 需要添加一个换行吗？
                        parse_object(type_, bbox, "".join(buf))
                    type_ = m.group("type")
                    bbox = parse_bbox(m.group("bbox"))

                    buf.clear()
                    buf.append(line[m.end() :])
                else:
                    buf.append(line)

            if len(buf) > 0 and type_ and bbox:
                parse_object(type_, bbox, "".join(buf))

        def parse_object(type_: str, bbox: BBox, text: str):
            text = text.strip()
            if debugger.allow("info"):
                debugger.print(f"type={type_},bbox={bbox}")
            if type_ in ("image", "chart"):
                # 有些BBox可能小了一点，如：没有边界，可以稍微大一点
                page.make_figure(bbox, add=True)
            elif type_ == "table":
                # 获得的是html，然后可以解析为cells
                table_bbox = bbox
                table: KTable | None = None
                try:
                    table = KTable.from_text(page, table_bbox, text)
                except Exception:
                    self._logger.exception("解析表格出现异常")
                if table is None or table.row_num == 0 or table.col_num == 0:
                    # 如果无法生成表格，返回markdown？使用图片表示
                    page.make_figure(table_bbox, add=True)
                else:
                    page.objects.append(table)
            elif type_ in ("title", "text", "image_caption", "image_footnote"):
                page.objects.append(KText.from_markdown(page, bbox, text))
            else:
                # page_number: 页码
                # page_footnote: 脚注
                # header: 页眉
                # footer: 页脚
                # aside_text:
                pass

        parse(text)

        if debugger.allow("draw"):
            tables: list[Any] = []
            i = 0
            for obj in page.objects:
                if isinstance(obj, KTable):
                    tables.append((f"table_{i}", obj.cells))
                    i += 1

            page.draw(("objects", page.objects), *tables, dir=f"debug/{self._name}")
